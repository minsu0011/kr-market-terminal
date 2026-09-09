from __future__ import annotations

import threading

import pytest
import requests

from market_terminal.config import Settings
from market_terminal.providers import KISAuthBackoffError, KISClient


class _Response:
    def __init__(self, payload: dict, status: int = 200):
        self.payload = payload
        self.status_code = status

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error


def _client() -> KISClient:
    return KISClient(Settings(kis_app_key="key", kis_app_secret="secret", kis_mode="real"))


def test_kis_token_is_single_flight_for_concurrent_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    calls = 0
    lock = threading.Lock()

    def post(*args, **kwargs):
        nonlocal calls
        with lock:
            calls += 1
        return _Response({"access_token": "token", "expires_in": 3600})

    monkeypatch.setattr(client.session, "post", post)
    threads = [threading.Thread(target=client.token) for _ in range(6)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert calls == 1


def test_kis_token_failure_uses_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    calls = 0

    def post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _Response({}, 403)

    monkeypatch.setattr(client.session, "post", post)
    with pytest.raises(requests.HTTPError):
        client.token()
    with pytest.raises(KISAuthBackoffError):
        client.token()
    assert calls == 1
    assert client._token_retry_delay == 1.2


def test_kis_token_retry_stays_short_after_each_failure_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    responses = [_Response({}, 403), _Response({}, 403), _Response({"access_token": "token", "expires_in": 3600})]
    monkeypatch.setattr(client.session, "post", lambda *args, **kwargs: responses.pop(0))

    with pytest.raises(requests.HTTPError):
        client.token()
    assert client._token_retry_delay == 1.2
    client._token_failure_until = 0.0
    with pytest.raises(requests.HTTPError):
        client.token()
    assert client._token_retry_delay == 1.2
    client._token_failure_until = 0.0
    assert client.token() == "token"
    assert client._token_retry_delay == 1.2


def test_intraday_bars_preserves_first_page_after_later_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    responses = [{"rt_cd": "0", "output2": [
        {"stck_cntg_hour": "153000", "stck_oprc": "1", "stck_hgpr": "2", "stck_lwpr": "1", "stck_prpr": "2", "cntg_vol": "3"},
        {"stck_cntg_hour": "152900", "stck_oprc": "2", "stck_hgpr": "3", "stck_lwpr": "2", "stck_prpr": "3", "cntg_vol": "4"},
    ]}]

    def get(*args, **kwargs):
        if responses:
            return responses.pop()
        raise requests.HTTPError("temporary KIS failure")

    monkeypatch.setattr(client, "get", get)
    frame = client.intraday_bars("005930")
    assert len(frame) == 2
    assert list(frame["close"]) == [3.0, 2.0]


def test_kis_market_context_accepts_mini_kospi200_futures(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()

    def index_price(code: str) -> dict:
        values = {
            "0001": {"bstp_nmix_prpr": "2500", "bstp_nmix_prdy_ctrt": "0.2"},
            "1001": {"bstp_nmix_prpr": "800", "bstp_nmix_prdy_ctrt": "0.1"},
            "2001": {"bstp_nmix_prpr": "330", "bstp_nmix_prdy_ctrt": "0.3"},
        }
        return values[code]

    monkeypatch.setattr(client, "index_price", index_price)
    monkeypatch.setattr(client, "futures_board", lambda: [{
        "futs_shrn_iscd": "A05608", "hts_kor_isnm": "미니F 202608",
        "futs_prpr": "332.5", "futs_prdy_ctrt": "0.4",
        "hts_otst_stpl_qty": "100", "acml_vol": "200", "hts_rmnn_dynu": "10",
    }])
    context = client.market_context()
    assert context["kospi200_futures_price"]["value"] == 332.5
    assert context["futures_open_interest"]["value"] == 100
    assert context["futures_volume"]["value"] == 200
    assert context["futures_basis"]["value"] == 2.5
