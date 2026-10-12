import pytest
import requests


@pytest.fixture(autouse=True)
def block_external_http(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "demo")
    monkeypatch.setenv("NEWS_FIXTURE_MODE", "true")
    monkeypatch.setenv("OPENAI_ANALYSIS_ENABLED", "false")

    def blocked(*args, **kwargs):
        raise AssertionError("External HTTP is disabled in the test suite.")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
