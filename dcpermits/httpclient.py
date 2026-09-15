"""Polite HTTP client.

A nationwide harvest touches thousands of government hosts, many of them
small and underfunded. This client is built so the agent stays a good
citizen by default:

* per-host rate limiting (not just a global sleep)
* robots.txt consulted once per host and cached
* bounded retries with exponential backoff + jitter, only for transient
  failures
* an honest, identifiable User-Agent with a contact URL
* hard caps on response size so one pathological endpoint cannot exhaust
  memory

Stdlib only, so the agent runs in a bare container with no install step.
"""

from __future__ import annotations

import gzip
import http.client
import json
import logging
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from typing import Any, Dict, Optional

log = logging.getLogger("dcpermits.http")

DEFAULT_UA = (
    "dcpermits/0.1 (public building-permit research agent; "
    "+https://github.com/caseycerretani/lonemoose)"
)

# Retrying a 404 or a 400 just wastes the host's capacity — only these are
# worth a second attempt.
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

MAX_BYTES = 64 * 1024 * 1024


class HttpError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


@dataclass
class HttpConfig:
    user_agent: str = DEFAULT_UA
    timeout: float = 45.0
    max_retries: int = 3
    # Minimum seconds between requests to the same host.
    per_host_delay: float = 1.0
    respect_robots: bool = True
    max_bytes: int = MAX_BYTES


class PoliteClient:
    """Rate-limited, robots-aware HTTP GET client."""

    def __init__(self, config: Optional[HttpConfig] = None):
        self.config = config or HttpConfig()
        self._last_request: Dict[str, float] = {}
        self._robots: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}

    # ---------------------------------------------------------------- robots

    def _robots_for(self, url: str) -> Optional[urllib.robotparser.RobotFileParser]:
        parts = urllib.parse.urlsplit(url)
        host_key = f"{parts.scheme}://{parts.netloc}"
        if host_key in self._robots:
            return self._robots[host_key]

        parser: Optional[urllib.robotparser.RobotFileParser] = None
        try:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(f"{host_key}/robots.txt")
            # read() swallows most errors itself but can still raise on
            # DNS/TLS problems.
            parser.read()
        except Exception as exc:  # pragma: no cover - network dependent
            log.debug("robots.txt unavailable for %s (%s); allowing", host_key, exc)
            # A missing or broken robots.txt is not a disallow. Treat it as
            # "no rules stated" rather than blocking the whole host.
            parser = None

        self._robots[host_key] = parser
        return parser

    def allowed(self, url: str) -> bool:
        if not self.config.respect_robots:
            return True
        parser = self._robots_for(url)
        if parser is None:
            return True
        try:
            return parser.can_fetch(self.config.user_agent, url)
        except Exception:  # pragma: no cover - defensive
            return True

    # ------------------------------------------------------------ throttling

    def _throttle(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).netloc
        last = self._last_request.get(host)
        if last is not None:
            wait = self.config.per_host_delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    # ---------------------------------------------------------------- fetch

    def get(self, url: str, params: Optional[Dict[str, Any]] = None,
            headers: Optional[Dict[str, str]] = None) -> bytes:
        """GET a URL, returning raw bytes.

        Raises :class:`HttpError` on non-transient failures or once retries
        are exhausted.
        """
        if params:
            sep = "&" if urllib.parse.urlsplit(url).query else "?"
            url = f"{url}{sep}{urllib.parse.urlencode(params, doseq=True)}"

        if not self.allowed(url):
            raise HttpError(f"blocked by robots.txt: {url}")

        request_headers = {
            "User-Agent": self.config.user_agent,
            "Accept-Encoding": "gzip",
            "Accept": "application/json, text/csv, */*",
        }
        if headers:
            request_headers.update(headers)

        last_error: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            self._throttle(url)
            req = urllib.request.Request(url, headers=request_headers)
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    body = resp.read(self.config.max_bytes + 1)
                    if len(body) > self.config.max_bytes:
                        raise HttpError(f"response exceeded {self.config.max_bytes} bytes: {url}")
                    if resp.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                    return body

            except urllib.error.HTTPError as exc:
                last_error = exc
                # Honour Retry-After when the server bothers to send it.
                if exc.code in RETRYABLE_STATUS and attempt < self.config.max_retries:
                    delay = self._backoff(attempt, exc.headers.get("Retry-After"))
                    log.debug("HTTP %s on %s; retrying in %.1fs", exc.code, url, delay)
                    time.sleep(delay)
                    continue
                raise HttpError(f"HTTP {exc.code} for {url}", status=exc.code) from exc

            # http.client raises RemoteDisconnected/BadStatusLine outside the
            # urllib.error hierarchy. Overloaded government endpoints do this
            # constantly, and an unwrapped exception would escape the retry
            # loop and take the whole source down for the run.
            except (urllib.error.URLError, socket.timeout, TimeoutError,
                    http.client.HTTPException, ConnectionError, OSError) as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    delay = self._backoff(attempt, None)
                    log.debug("transport error on %s (%s); retrying in %.1fs", url, exc, delay)
                    time.sleep(delay)
                    continue
                raise HttpError(f"transport error for {url}: {exc}") from exc

        raise HttpError(f"exhausted retries for {url}: {last_error}")

    def _backoff(self, attempt: int, retry_after: Optional[str]) -> float:
        if retry_after:
            try:
                # Cap it: some servers send absurd values.
                return min(float(retry_after), 60.0)
            except (TypeError, ValueError):
                pass
        # Jitter keeps a fleet of workers from re-hitting in lockstep.
        return min(2.0 ** attempt, 30.0) + random.uniform(0, 0.75)

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None,
                 headers: Optional[Dict[str, str]] = None) -> Any:
        body = self.get(url, params=params, headers=headers)
        try:
            return json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            snippet = body[:200].decode("utf-8", errors="replace")
            raise HttpError(f"non-JSON response from {url}: {snippet!r}") from exc

    def get_text(self, url: str, params: Optional[Dict[str, Any]] = None) -> str:
        return self.get(url, params=params).decode("utf-8", errors="replace")
