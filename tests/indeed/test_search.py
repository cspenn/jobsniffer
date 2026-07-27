from pathlib import Path

from jobsniffer.http.fixtures import load_fixtures
from jobsniffer.indeed.search import build_search_params, fetch_search_page

FIXTURES = Path(__file__).parent.parent / "fixtures" / "indeed.jsonl"


class FakeResponse:
    def __init__(self, *, ok=True, text=""):
        self.ok = ok
        self.text = text


class FakeSession:
    """Matches the HttpClient protocol's shape without depending on
    ReplayClient's exact-URL matching -- fetch_search_page's URL/params
    construction is verified directly via recorded calls, while its
    response handling is exercised against the real recorded search page
    body, decoupling "did we build the right request" from "did we parse
    the response correctly" (the latter already has its own exhaustive
    tests in tests/indeed/test_parse.py)."""

    def __init__(self, response: FakeResponse):
        self._response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


def test_build_search_params_includes_required_fields():
    params = build_search_params(
        search_term="software engineer",
        location="Austin, TX",
        distance=25,
        hours_old=None,
    )
    assert params == {"q": "software engineer", "l": "Austin, TX", "start": 0, "radius": 25}


def test_build_search_params_converts_hours_old_to_whole_days():
    params = build_search_params(
        search_term="x", location=None, distance=None, hours_old=72
    )
    assert params["fromage"] == 3


def test_build_search_params_minimum_one_day_for_small_hours_old():
    params = build_search_params(
        search_term="x", location=None, distance=None, hours_old=5
    )
    assert params["fromage"] == 1


def test_build_search_params_omits_none_values():
    params = build_search_params(
        search_term="x", location=None, distance=None, hours_old=None
    )
    assert "l" not in params
    assert "radius" not in params
    assert "fromage" not in params


def test_fetch_search_page_sends_expected_url_and_params():
    session = FakeSession(FakeResponse(ok=True, text="<html></html>"))
    fetch_search_page(
        session,
        base_url="https://www.indeed.com",
        search_term="marketing",
        location="Boston, MA",
        distance=None,
        hours_old=None,
        start=10,
    )
    url, kwargs = session.calls[0]
    assert url == "https://www.indeed.com/jobs"
    assert kwargs["params"] == {"q": "marketing", "l": "Boston, MA", "start": 10}


def test_fetch_search_page_returns_empty_list_on_non_ok_response():
    session = FakeSession(FakeResponse(ok=False, text=""))
    results = fetch_search_page(
        session,
        base_url="https://www.indeed.com",
        search_term="x",
        location=None,
        distance=None,
        hours_old=None,
    )
    assert results == []


def test_fetch_search_page_parses_the_real_recorded_search_page():
    """Response handling against real, known ground truth: this must
    yield the same 40 results (and the known job key) that
    tests/indeed/test_parse.py verifies directly against parse_search_results."""
    exchanges = load_fixtures(FIXTURES)
    search_html = exchanges[0].content.decode("utf-8")
    session = FakeSession(FakeResponse(ok=True, text=search_html))

    results = fetch_search_page(
        session,
        base_url="https://www.indeed.com",
        search_term="marketing",
        location="Framingham, MA",
        distance=None,
        hours_old=None,
    )

    assert len(results) == 40
    assert "20d6afbf6595234e" in {job["jobkey"] for job in results}
