"""jobsniffer.http.exceptions

Explicit exception types for the HTTP layer (P8: no silent exception
swallowing, no bare Exception raised for an expected failure mode).
"""

from __future__ import annotations


class HttpClientError(Exception):
    """Base class for all jobsniffer.http errors."""


class FixtureNotFoundError(HttpClientError):
    """Raised by ReplayClient in replay mode when no recorded exchange
    matches the requested (method, url).

    A missing fixture is a test-authoring bug, not a soft failure -- it must
    never be papered over with an empty/fabricated response.
    """

    def __init__(self, method: str, url: str) -> None:
        self.method = method
        self.url = url
        super().__init__(
            f"No recorded fixture for {method} {url}. Record one with "
            "ReplayClient(mode='record') or add it via "
            "src/scripts/har_to_fixtures.py."
        )


class FixtureFileError(HttpClientError):
    """Raised when a fixture file is missing, unreadable, or malformed."""
