from pathlib import Path

from jobsniffer.http.fixtures import load_fixtures
from jobsniffer.indeed.detail import fetch_job_detail

FIXTURES = Path(__file__).parent.parent / "fixtures" / "indeed.jsonl"


class FakeResponse:
    def __init__(self, *, ok=True, text=""):
        self.ok = ok
        self.text = text

    def json(self):
        import json

        return json.loads(self.text)


class FakeSession:
    def __init__(self, response: FakeResponse):
        self._response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


def test_fetch_job_detail_sends_expected_url_and_params():
    session = FakeSession(FakeResponse(ok=True, text='{"body": {}}'))
    fetch_job_detail(session, base_url="https://www.indeed.com", job_key="abc123")
    url, kwargs = session.calls[0]
    assert url == "https://www.indeed.com/viewjob"
    assert kwargs["params"] == {"jk": "abc123", "from": "vjs"}


def test_fetch_job_detail_returns_none_on_non_ok_response():
    session = FakeSession(FakeResponse(ok=False, text=""))
    assert fetch_job_detail(session, base_url="https://x", job_key="k") is None


def test_fetch_job_detail_returns_none_on_malformed_json():
    session = FakeSession(FakeResponse(ok=True, text="not json"))
    assert fetch_job_detail(session, base_url="https://x", job_key="k") is None


def test_fetch_job_detail_returns_none_when_body_is_not_a_dict():
    session = FakeSession(FakeResponse(ok=True, text='{"body": "unexpected string"}'))
    assert fetch_job_detail(session, base_url="https://x", job_key="k") is None


def test_fetch_job_detail_returns_the_real_recorded_known_job_body():
    """Verifies against the same known ground truth as test_parse.py:
    jk=20d6afbf6595234e -> title "Website Developer & Digital Marketing",
    salaryMin/Max 60000/65000."""
    exchanges = load_fixtures(FIXTURES)
    detail_exchange = next(e for e in exchanges if "jk=20d6afbf6595234e" in e.url)
    session = FakeSession(
        FakeResponse(ok=True, text=detail_exchange.content.decode("utf-8"))
    )

    body = fetch_job_detail(
        session, base_url="https://www.indeed.com", job_key="20d6afbf6595234e"
    )

    assert body is not None
    assert body["jobTitle"] == "Website Developer & Digital Marketing"
    assert body["salaryInfoModel"]["salaryMin"] == 60000
    assert body["salaryInfoModel"]["salaryMax"] == 65000
