"""HTTP client tests.

These cover the politeness and resilience guarantees, using a stubbed
``urlopen`` so nothing touches the network.
"""

from __future__ import annotations

import http.client
import io
import urllib.error

import pytest

from dcpermits.httpclient import HttpConfig, HttpError, PoliteClient


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, headers=None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def client(monkeypatch):
    # No robots lookups, no sleeping between requests.
    config = HttpConfig(respect_robots=False, per_host_delay=0.0, max_retries=3)
    instance = PoliteClient(config)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    return instance


def install(monkeypatch, handler):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None: handler(req))


def test_get_json_parses_a_response(client, monkeypatch):
    install(monkeypatch, lambda req: FakeResponse(b'{"ok": true}'))
    assert client.get_json("https://x.example.gov/a") == {"ok": True}


def test_non_json_body_raises_with_a_snippet(client, monkeypatch):
    install(monkeypatch, lambda req: FakeResponse(b"<!DOCTYPE html><html>"))
    with pytest.raises(HttpError, match="non-JSON"):
        client.get_json("https://x.example.gov/a")


def test_params_are_appended_and_encoded(client, monkeypatch):
    seen = {}

    def handler(req):
        seen["url"] = req.full_url
        return FakeResponse(b"[]")

    install(monkeypatch, handler)
    client.get_json("https://x.example.gov/a", params={"$where": "A = 'b'"})
    assert "%24where" in seen["url"]
    assert "?" in seen["url"]


def test_existing_query_string_is_preserved(client, monkeypatch):
    seen = {}

    def handler(req):
        seen["url"] = req.full_url
        return FakeResponse(b"[]")

    install(monkeypatch, handler)
    client.get_json("https://x.example.gov/a?f=json", params={"g": "1"})
    assert "f=json&g=1" in seen["url"]


def test_retries_transient_status_then_succeeds(client, monkeypatch):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
        return FakeResponse(b'{"ok": 1}')

    install(monkeypatch, handler)
    assert client.get_json("https://x.example.gov/a") == {"ok": 1}
    assert calls["n"] == 3


def test_does_not_retry_a_client_error(client, monkeypatch):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, None)

    install(monkeypatch, handler)
    with pytest.raises(HttpError) as excinfo:
        client.get("https://x.example.gov/a")
    assert excinfo.value.status == 404
    # Retrying a 404 just wastes the host's capacity.
    assert calls["n"] == 1


def test_remote_disconnected_is_retried(client, monkeypatch):
    """http.client raises this outside the urllib.error hierarchy.

    Overloaded government endpoints do it constantly; an unwrapped
    exception would escape the retry loop entirely.
    """
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 2:
            raise http.client.RemoteDisconnected("closed")
        return FakeResponse(b'{"ok": 1}')

    install(monkeypatch, handler)
    assert client.get_json("https://x.example.gov/a") == {"ok": 1}
    assert calls["n"] == 2


def test_circuit_breaker_stops_retrying_a_dead_host(client, monkeypatch):
    """A host refusing every connection must not consume the run.

    With a full retry ladder per request, a publisher exposing dozens of
    layers on one dead host burns the whole time budget for nothing.
    """
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        raise http.client.RemoteDisconnected("closed")

    install(monkeypatch, handler)

    # Each early request burns the full ladder (1 + 3 retries).
    for _ in range(2):
        with pytest.raises(HttpError):
            client.get("https://dead.example.gov/a")
    assert calls["n"] == 8
    assert client.is_tripped("dead.example.gov")

    # Once tripped, each further request costs a single attempt.
    before = calls["n"]
    with pytest.raises(HttpError, match="circuit open"):
        client.get("https://dead.example.gov/b")
    assert calls["n"] == before + 1


def test_circuit_breaker_is_per_host(client, monkeypatch):
    def handler(req):
        if "dead" in req.full_url:
            raise http.client.RemoteDisconnected("closed")
        return FakeResponse(b'{"ok": 1}')

    install(monkeypatch, handler)
    for _ in range(3):
        with pytest.raises(HttpError):
            client.get("https://dead.example.gov/a")

    assert client.is_tripped("dead.example.gov")
    assert not client.is_tripped("healthy.example.gov")
    assert client.get_json("https://healthy.example.gov/a") == {"ok": 1}


def test_success_clears_the_failure_streak(client, monkeypatch):
    state = {"fail": True}

    def handler(req):
        if state["fail"]:
            raise http.client.RemoteDisconnected("closed")
        return FakeResponse(b'{"ok": 1}')

    install(monkeypatch, handler)
    with pytest.raises(HttpError):
        client.get("https://x.example.gov/a")
    assert client._host_failures.get("x.example.gov", 0) > 0

    state["fail"] = False
    client.get("https://x.example.gov/a")
    assert client._host_failures.get("x.example.gov", 0) == 0


def test_http_status_errors_do_not_trip_the_breaker(client, monkeypatch):
    """A 404 means the host is reachable and answering."""
    def handler(req):
        raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, None)

    install(monkeypatch, handler)
    for _ in range(10):
        with pytest.raises(HttpError):
            client.get("https://x.example.gov/a")
    assert not client.is_tripped("x.example.gov")


def test_oversized_response_is_rejected(monkeypatch):
    config = HttpConfig(respect_robots=False, per_host_delay=0.0, max_bytes=10)
    client = PoliteClient(config)
    install(monkeypatch, lambda req: FakeResponse(b"x" * 100))
    with pytest.raises(HttpError, match="exceeded"):
        client.get("https://x.example.gov/a")


def test_user_agent_is_identifiable(client, monkeypatch):
    seen = {}

    def handler(req):
        seen["ua"] = req.get_header("User-agent")
        return FakeResponse(b"[]")

    install(monkeypatch, handler)
    client.get("https://x.example.gov/a")
    assert "dcpermits" in seen["ua"]
    # A contact URL lets an operator find out who is calling.
    assert "http" in seen["ua"]


def test_robots_disallow_blocks_the_request(monkeypatch):
    client = PoliteClient(HttpConfig(per_host_delay=0.0, respect_robots=True))

    class Blocked:
        def can_fetch(self, _ua, _url):
            return False

    client._robots["https://x.example.gov"] = Blocked()
    with pytest.raises(HttpError, match="robots"):
        client.get("https://x.example.gov/a")


def test_missing_robots_is_treated_as_allowed(monkeypatch):
    """No robots.txt means no stated rules, not a blanket disallow."""
    client = PoliteClient(HttpConfig(per_host_delay=0.0, respect_robots=True))
    client._robots["https://x.example.gov"] = None
    assert client.allowed("https://x.example.gov/a")


def test_retry_after_header_is_honoured_and_capped(client):
    assert client._backoff(0, "5") == 5.0
    # An absurd Retry-After must not stall the run for hours.
    assert client._backoff(0, "99999") == 60.0
    assert client._backoff(0, "garbage") >= 1.0


def test_backoff_grows_and_is_bounded(client):
    assert client._backoff(0, None) < client._backoff(5, None)
    assert client._backoff(20, None) <= 30.0 + 0.75
