"""jobsniffer.indeed.search

Step 1 of Indeed's two-step flow: GET the HTML search results page and
parse job identity + snippet + estimated salary out of it via
jobsniffer.indeed.parse. See jobsniffer.indeed.parse's module docstring
for why this alone is insufficient (truncated description, estimated
salary) and jobsniffer.indeed.detail for step 2.
"""

from __future__ import annotations

from jobsniffer.http.protocol import HttpClient
from jobsniffer.indeed.parse import parse_search_results


def build_search_params(
    *,
    search_term: str | None,
    location: str | None,
    distance: int | None,
    hours_old: int | None,
    start: int = 0,
) -> dict:
    params: dict = {"q": search_term, "l": location, "start": start}
    if distance is not None:
        params["radius"] = distance
    if hours_old is not None:
        # Indeed's HTML search accepts fromage in whole days.
        params["fromage"] = max(hours_old // 24, 1)
    return {k: v for k, v in params.items() if v is not None}


def fetch_search_page(
    session: HttpClient,
    *,
    base_url: str,
    search_term: str | None,
    location: str | None,
    distance: int | None,
    hours_old: int | None,
    start: int = 0,
    timeout: float = 15.0,
) -> list[dict]:
    """Fetches one page of Indeed search results and returns the raw
    per-job dicts (see jobsniffer.indeed.parse.parse_search_results).

    Returns [] on a non-2xx/3xx response rather than raising -- a single
    blocked/CAPTCHA'd page shouldn't crash the whole scrape; the caller
    decides whether to fall back to the GraphQL path.
    """
    params = build_search_params(
        search_term=search_term,
        location=location,
        distance=distance,
        hours_old=hours_old,
        start=start,
    )
    response = session.get(f"{base_url}/jobs", params=params, timeout=timeout)
    if not response.ok:
        return []
    return parse_search_results(response.text)
