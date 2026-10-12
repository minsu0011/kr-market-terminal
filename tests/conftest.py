import pytest
import requests


@pytest.fixture(autouse=True)
def block_external_http(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("External HTTP is disabled in the test suite.")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
