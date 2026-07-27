"""jobsniffer.http.replay_client

Test-time HttpClient. Two modes:

- "replay" (default): serves recorded exchanges from a JSONL fixture file.
  Matching is (method, full URL with any `params` merged in, request body
  signature) -- see jobsniffer.http.fixtures.compute_body_signature. A
  request with no matching fixture raises FixtureNotFoundError; it never
  falls back to an empty/fabricated response (P8).
- "record": wraps a real CurlCffiClient, makes the live request, and appends
  the resulting exchange to the fixture file as a side effect. Used once,
  interactively, to seed fixtures for sites with no HAR ground truth
  (LinkedIn's guest API, the five unverified scrapers) -- see
  docs/2026-07-27-jobsniffer-modernization-plan.md, Phase 2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlparse, urlunparse

from jobsniffer.http.curl_client import CurlCffiClient
from jobsniffer.http.exceptions import FixtureNotFoundError
from jobsniffer.http.fixtures import (
    RecordedExchange,
    append_fixture,
    compute_body_signature,
    load_fixtures,
)


def _merge_url_params(url: str, params: dict[str, Any] | None) -> str:
    """Folds a `params=` kwarg into the URL's query string, the same way a
    real HTTP client would before it hits the wire -- so replay matching
    keys on the URL the server actually saw, not the caller's shorthand."""
    if not params:
        return url
    parsed = urlparse(url)
    extra = urlencode(params, doseq=True)
    query = f"{parsed.query}&{extra}" if parsed.query else extra
    return urlunparse(parsed._replace(query=query))


class RecordedResponse:
    """Satisfies jobsniffer.http.protocol.HttpResponse by wrapping a single
    RecordedExchange. Deliberately not a dataclass: `.json()` needs to be a
    method (matching curl_cffi's Response), not a field."""

    def __init__(self, exchange: RecordedExchange) -> None:
        self._exchange = exchange
        self.status_code = exchange.status_code
        self.ok = 200 <= exchange.status_code < 400
        self.headers = exchange.headers
        self.url = exchange.url
        self.content = exchange.content
        self.text = self.content.decode(exchange.encoding or "utf-8")

    def json(self) -> Any:
        import json as _json

        return _json.loads(self.text)


class ReplayClient:
    """HttpClient implementation for tests. See module docstring for modes."""

    def __init__(
        self,
        fixture_path: Path,
        *,
        mode: Literal["replay", "record"] = "replay",
        recorder: CurlCffiClient | None = None,
    ) -> None:
        self._fixture_path = fixture_path
        self._mode = mode
        self._recorder = recorder
        self._exchanges: list[RecordedExchange] = (
            load_fixtures(fixture_path) if mode == "replay" else []
        )

        if mode == "record" and recorder is None:
            self._recorder = CurlCffiClient()

    def request(self, method: str, url: str, **kwargs: Any) -> RecordedResponse:
        full_url = _merge_url_params(url, kwargs.get("params"))
        body_sig = compute_body_signature(
            json_body=kwargs.get("json"), data=kwargs.get("data")
        )

        if self._mode == "record":
            return self._record(method, full_url, body_sig, **kwargs)
        return self._replay(method, full_url, body_sig)

    def _replay(
        self, method: str, full_url: str, body_sig: str | None
    ) -> RecordedResponse:
        method_upper = method.upper()
        for exchange in self._exchanges:
            if (
                exchange.method == method_upper
                and exchange.url == full_url
                and exchange.request_body_signature == body_sig
            ):
                return RecordedResponse(exchange)
        raise FixtureNotFoundError(method_upper, full_url)

    def _record(
        self, method: str, full_url: str, body_sig: str | None, **kwargs: Any
    ) -> RecordedResponse:
        assert self._recorder is not None  # set in __init__ for mode="record"
        live_response = self._recorder.request(method, full_url, **kwargs)
        exchange = RecordedExchange.from_bytes(
            method=method,
            url=full_url,
            status_code=live_response.status_code,
            content=live_response.content,
            headers={k: v for k, v in live_response.headers.items() if v is not None},
            request_body_signature=body_sig,
        )
        append_fixture(self._fixture_path, exchange)
        self._exchanges.append(exchange)
        return RecordedResponse(exchange)

    def get(self, url: str, **kwargs: Any) -> RecordedResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> RecordedResponse:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        if self._recorder is not None:
            self._recorder.close()
