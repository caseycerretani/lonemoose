"""Shared fixtures.

Every test runs offline. Connector tests use :class:`FakeClient`, which
serves canned responses keyed by a substring of the request URL, so the
suite is fast, deterministic, and never depends on a county web server
being up.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from dcpermits.httpclient import HttpError


class FakeClient:
    """Stand-in for :class:`dcpermits.httpclient.PoliteClient`."""

    def __init__(self, routes: Optional[Dict[str, Any]] = None):
        # Maps a URL substring -> response payload, or an Exception to raise.
        self.routes: Dict[str, Any] = routes or {}
        self.calls: List[str] = []

    def add(self, url_fragment: str, payload: Any) -> "FakeClient":
        self.routes[url_fragment] = payload
        return self

    def _resolve(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        full = url
        if params:
            # Keep the query visible so tests can assert on what was sent.
            parts = "&".join(f"{k}={v}" for k, v in params.items())
            full = f"{url}?{parts}"
        self.calls.append(full)

        # Longest matching fragment wins, so a specific route can override
        # a general one.
        matches = sorted(
            (frag for frag in self.routes if frag in full), key=len, reverse=True
        )
        if not matches:
            raise HttpError(f"no fake route for {full}", status=404)
        payload = self.routes[matches[0]]
        if isinstance(payload, Exception):
            raise payload
        return payload

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None,
                 headers: Optional[Dict[str, str]] = None) -> Any:
        return self._resolve(url, params)

    def get_text(self, url: str, params: Optional[Dict[str, Any]] = None) -> str:
        payload = self._resolve(url, params)
        return payload if isinstance(payload, str) else json.dumps(payload)

    def get(self, url: str, params: Optional[Dict[str, Any]] = None,
            headers: Optional[Dict[str, str]] = None) -> bytes:
        return self.get_text(url, params).encode("utf-8")

    def last_query(self) -> str:
        return self.calls[-1] if self.calls else ""

    def queries_containing(self, needle: str) -> List[str]:
        return [c for c in self.calls if needle in c]


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "permits.db"


@pytest.fixture
def tmp_registry(tmp_path):
    return tmp_path / "sources.json"
