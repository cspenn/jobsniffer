"""jobsniffer.http.curl_client

Production HTTP client. Uses curl_cffi rather than httpx (this org's default
per references/python-best-practices.md Sec. 7.4-adjacent guidance) because
impersonation is a hard requirement here, not a preference: curl_cffi
replicates a real browser's TLS/JA3 fingerprint at the libcurl layer, which
httpx (an ordinary Python TLS stack) cannot do. Every site jobsniffer targets
fingerprints inbound connections to distinguish real browsers from bots --
matching a Chrome-shaped signature is the entire reason this dependency
exists (see docs/2026-07-27-jobsniffer-modernization-plan.md, Phase 1).
"""

from __future__ import annotations

from typing import Any, Self, cast

import stamina
import structlog
from curl_cffi import requests as curl_requests
from curl_cffi.const import CurlOpt
from curl_cffi.requests.exceptions import RequestException
from curl_cffi.requests.session import HttpMethod

from jobsniffer.http.proxy import ProxyRotator

log = structlog.get_logger(__name__)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class CurlCffiClient:
    """HttpClient backed by curl_cffi, impersonating a real Chrome build.

    Retries transient failures (connection errors, 429/5xx) with jittered
    exponential backoff via stamina; a request that exhausts retries raises
    the underlying curl_cffi exception rather than returning a fabricated
    response (P8 -- explicit failure propagation).
    """

    def __init__(
        self,
        *,
        impersonate: str = "chrome",
        proxies: list[str] | str | None = None,
        ca_cert: str | None = None,
        verify: bool = True,
        timeout: float = 30.0,
        max_attempts: int = 3,
        retry_timeout: float = 60.0,
    ) -> None:
        self._proxy_rotator = ProxyRotator(proxies)
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._retry_timeout = retry_timeout

        session_kwargs: dict[str, Any] = {"impersonate": impersonate, "verify": verify}
        if ca_cert:
            session_kwargs["curl_options"] = {CurlOpt.CAINFO: ca_cert}
        self._session = curl_requests.Session(**session_kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> curl_requests.Response:
        kwargs.setdefault("timeout", self._timeout)
        proxy = self._proxy_rotator.next()
        if proxy is not None:
            kwargs.setdefault("proxies", proxy)

        for attempt in stamina.retry_context(
            on=RequestException,
            attempts=self._max_attempts,
            timeout=self._retry_timeout,
            wait_initial=0.5,
            wait_max=15.0,
            wait_jitter=1.0,
            wait_exp_base=2.0,
        ):
            with attempt:
                # Our own HttpClient protocol deliberately types `method` as
                # `str` (it's not curl_cffi-specific); narrow only at the
                # boundary where curl_cffi's stricter Literal is required.
                response = self._session.request(
                    cast(HttpMethod, method), url, **kwargs
                )
                if response.status_code in _RETRYABLE_STATUS:
                    log.warning(
                        "http.retryable_status",
                        method=method,
                        url=url,
                        status_code=response.status_code,
                    )
                    response.raise_for_status()
                return response

        # stamina.retry_context always either returns from within `with attempt`
        # or raises once attempts/timeout are exhausted; this line is defensive,
        # not a real code path (P6: no silent fallthrough).
        raise HttpClientUnreachableError(method, url)  # pragma: no cover

    def get(self, url: str, **kwargs: Any) -> curl_requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> curl_requests.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class HttpClientUnreachableError(RuntimeError):
    """Raised only if stamina's retry loop exits without returning or
    raising -- a stamina contract violation, not an expected runtime state."""

    def __init__(self, method: str, url: str) -> None:
        super().__init__(
            f"stamina.retry_context exited without a result for {method} {url}"
        )
