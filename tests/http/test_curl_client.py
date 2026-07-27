import pytest
from curl_cffi.const import CurlOpt
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError

from jobsniffer.http.curl_client import CurlCffiClient, HttpClientUnreachableError


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.reason = "OK" if self.ok else "Error"

    def raise_for_status(self):
        if not self.ok:
            from curl_cffi.requests.exceptions import HTTPError

            raise HTTPError(f"HTTP Error {self.status_code}", 0, self)


class FakeSession:
    """Stands in for curl_cffi.requests.Session so tests never touch the
    network. Records every call and can be scripted to fail N times before
    succeeding, to exercise CurlCffiClient's stamina retry wiring."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    def close(self):
        self.closed = True


def _client_with_fake_session(monkeypatch, responses, **client_kwargs):
    client = CurlCffiClient(max_attempts=5, retry_timeout=5, **client_kwargs)
    fake = FakeSession(responses)
    monkeypatch.setattr(client, "_session", fake)
    return client, fake


def test_get_delegates_to_session_with_default_timeout(monkeypatch):
    client, fake = _client_with_fake_session(monkeypatch, [200])
    response = client.get("https://example.com")
    assert response.status_code == 200
    method, url, kwargs = fake.calls[0]
    assert method == "GET"
    assert url == "https://example.com"
    assert kwargs["timeout"] == client._timeout


def test_post_uses_post_method(monkeypatch):
    client, fake = _client_with_fake_session(monkeypatch, [200])
    client.post("https://example.com", json={"a": 1})
    method, _url, kwargs = fake.calls[0]
    assert method == "POST"
    assert kwargs["json"] == {"a": 1}


def test_retries_on_429_then_succeeds(monkeypatch):
    client, fake = _client_with_fake_session(monkeypatch, [429, 429, 200])
    response = client.get("https://example.com")
    assert response.status_code == 200
    assert len(fake.calls) == 3


def test_retries_on_connection_error_then_succeeds(monkeypatch):
    client, fake = _client_with_fake_session(
        monkeypatch, [CurlConnectionError("boom"), 200]
    )
    response = client.get("https://example.com")
    assert response.status_code == 200
    assert len(fake.calls) == 2


def test_non_retryable_status_returned_immediately(monkeypatch):
    client, fake = _client_with_fake_session(monkeypatch, [404])
    response = client.get("https://example.com")
    assert response.status_code == 404
    assert len(fake.calls) == 1


def test_exhausting_retries_raises(monkeypatch):
    client = CurlCffiClient(max_attempts=3, retry_timeout=5)
    fake = FakeSession([500, 500, 500])
    monkeypatch.setattr(client, "_session", fake)
    with pytest.raises(Exception):
        client.get("https://example.com")
    assert len(fake.calls) == 3


def test_close_closes_underlying_session(monkeypatch):
    client, fake = _client_with_fake_session(monkeypatch, [200])
    client.close()
    assert fake.closed is True


def test_context_manager_closes_on_exit(monkeypatch):
    client = CurlCffiClient()
    fake = FakeSession([200])
    monkeypatch.setattr(client, "_session", fake)
    with client as ctx:
        assert ctx is client
    assert fake.closed is True


def test_proxy_is_applied_from_rotator(monkeypatch):
    client, fake = _client_with_fake_session(monkeypatch, [200], proxies="1.2.3.4:80")
    client.get("https://example.com")
    _method, _url, kwargs = fake.calls[0]
    assert kwargs["proxies"] == {"http": "http://1.2.3.4:80", "https": "http://1.2.3.4:80"}


def test_ca_cert_sets_curl_cainfo_option(monkeypatch):
    import jobsniffer.http.curl_client as curl_client_module

    captured_kwargs = {}

    class FakeCurlSession:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(
        curl_client_module.curl_requests, "Session", FakeCurlSession
    )
    CurlCffiClient(ca_cert="/etc/ssl/corp-ca.pem")
    assert captured_kwargs["curl_options"] == {CurlOpt.CAINFO: "/etc/ssl/corp-ca.pem"}


def test_no_ca_cert_omits_curl_options(monkeypatch):
    import jobsniffer.http.curl_client as curl_client_module

    captured_kwargs = {}

    class FakeCurlSession:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(
        curl_client_module.curl_requests, "Session", FakeCurlSession
    )
    CurlCffiClient()
    assert "curl_options" not in captured_kwargs


def test_http_client_unreachable_error_message_names_method_and_url():
    error = HttpClientUnreachableError("GET", "https://example.com/x")
    assert "GET" in str(error)
    assert "https://example.com/x" in str(error)
