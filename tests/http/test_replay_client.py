import pytest

from jobsniffer.http.exceptions import FixtureNotFoundError
from jobsniffer.http.fixtures import RecordedExchange, append_fixture
from jobsniffer.http.replay_client import ReplayClient
from jobsniffer.http.curl_client import CurlCffiClient


@pytest.fixture
def fixture_path(tmp_path):
    path = tmp_path / "site.jsonl"
    append_fixture(
        path,
        RecordedExchange.from_bytes(
            method="GET",
            url="https://example.com/jobs?q=engineer",
            status_code=200,
            content=b'{"jobs": ["a"]}',
            headers={"content-type": "application/json"},
        ),
    )
    append_fixture(
        path,
        RecordedExchange.from_bytes(
            method="POST",
            url="https://example.com/graphql",
            status_code=200,
            content=b'{"data": {"n": 1}}',
            request_body_signature="sig-1",
        ),
    )
    append_fixture(
        path,
        RecordedExchange.from_bytes(
            method="POST",
            url="https://example.com/graphql",
            status_code=200,
            content=b'{"data": {"n": 2}}',
            request_body_signature="sig-2",
        ),
    )
    return path


def test_replay_matches_exact_url(fixture_path):
    client = ReplayClient(fixture_path)
    response = client.get("https://example.com/jobs?q=engineer")
    assert response.status_code == 200
    assert response.ok is True
    assert response.json() == {"jobs": ["a"]}


def test_replay_merges_params_into_url_before_matching(fixture_path):
    client = ReplayClient(fixture_path)
    response = client.get("https://example.com/jobs", params={"q": "engineer"})
    assert response.json() == {"jobs": ["a"]}


def test_replay_disambiguates_same_url_by_body_signature(fixture_path, monkeypatch):
    import jobsniffer.http.replay_client as replay_module

    monkeypatch.setattr(
        replay_module, "compute_body_signature", lambda **_: "sig-2"
    )
    client = ReplayClient(fixture_path)
    response = client.post("https://example.com/graphql", json={"query": "whatever"})
    assert response.json() == {"data": {"n": 2}}


def test_replay_missing_fixture_raises_with_method_and_url(fixture_path):
    client = ReplayClient(fixture_path)
    with pytest.raises(FixtureNotFoundError) as exc_info:
        client.get("https://example.com/nonexistent")
    assert exc_info.value.method == "GET"
    assert exc_info.value.url == "https://example.com/nonexistent"


def test_record_mode_appends_and_serves_from_memory(tmp_path):
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b'{"ok": true}'

    class FakeRecorder:
        def __init__(self):
            self.calls = []
            self.closed = False

        def request(self, method, url, **kwargs):
            self.calls.append((method, url))
            return FakeResponse()

        def close(self):
            self.closed = True

    path = tmp_path / "recorded.jsonl"
    fake_recorder = FakeRecorder()
    client = ReplayClient(path, mode="record", recorder=fake_recorder)

    response = client.get("https://example.com/live")
    assert response.json() == {"ok": True}
    assert fake_recorder.calls == [("GET", "https://example.com/live")]

    # Persisted to disk, and immediately servable from the same client's
    # in-memory cache without hitting the recorder again.
    replay_client = ReplayClient(path)
    replayed = replay_client.get("https://example.com/live")
    assert replayed.json() == {"ok": True}

    client.close()
    assert fake_recorder.closed is True


def test_record_mode_without_explicit_recorder_creates_a_real_curl_client(tmp_path):
    path = tmp_path / "recorded.jsonl"
    client = ReplayClient(path, mode="record")
    assert isinstance(client._recorder, CurlCffiClient)
