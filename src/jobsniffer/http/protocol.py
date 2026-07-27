"""jobsniffer.http.protocol

Structural interfaces for jobsniffer's HTTP layer. These are the exact
subset of the requests-style API the scrapers actually call: get/post/
request, plus a response with status_code/ok/text/content/headers/json().

curl_cffi.requests.Response and .Session satisfy these Protocols already
(curl_cffi mirrors the `requests` surface closely), so CurlCffiClient can
delegate to curl_cffi directly instead of wrapping every attribute.
ReplayClient's RecordedResponse satisfies the same shape without inheriting
from anything curl_cffi-specific, which is what lets tests swap one
implementation for the other with no change to scraper code.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class HttpResponse(Protocol):
    status_code: int
    ok: bool
    text: str
    content: bytes
    url: str
    headers: Mapping[str, str]

    def json(self) -> Any: ...


@runtime_checkable
class HttpClient(Protocol):
    """Implementations: CurlCffiClient (production, TLS/JA3 impersonation)
    and ReplayClient (tests, replays or records HAR-derived fixtures)."""

    def get(self, url: str, **kwargs: Any) -> HttpResponse: ...

    def post(self, url: str, **kwargs: Any) -> HttpResponse: ...

    def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse: ...

    def close(self) -> None: ...
