from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from market_terminal.feature_providers import (
    FeatureBatch,
    FeatureDataProvider,
    FeatureRequest,
    ProviderHub,
    ProviderStatus,
)
from market_terminal.secret_store import DpapiSecretStore, ProviderValidationStore, secret_fingerprint


ROOT = Path(__file__).resolve().parents[1]


class _Provider(FeatureDataProvider):
    def __init__(self, provider_id: str, features: dict, *, fail: bool = False):
        self.provider_id = provider_id
        self.features = features
        self.fail = fail

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus(self.provider_id, True, "NORMAL", "test")

    async def fetch(self, request: FeatureRequest) -> FeatureBatch:
        if self.fail:
            raise RuntimeError("provider failed")
        return FeatureBatch(self.provider_id, self.features, datetime.now(timezone.utc).isoformat())

    def supported_features(self) -> set[str]:
        return set(self.features)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI contract")
def test_dpapi_round_trip_never_writes_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "secrets.dpapi"
    store = DpapiSecretStore(path)
    store.save({"OPENAI_API_KEY": "sk-test-plain-marker"})
    assert b"sk-test-plain-marker" not in path.read_bytes()
    assert store.load()["OPENAI_API_KEY"] == "sk-test-plain-marker"


def test_validation_is_bound_to_current_secret_fingerprint(tmp_path: Path) -> None:
    store = ProviderValidationStore(tmp_path / "validation.json")
    first = secret_fingerprint("key-one")
    second = secret_fingerprint("key-two")
    store.record("openai", first, "NORMAL", "ok")
    assert store.is_verified("openai", first)
    assert not store.is_verified("openai", second)
    assert "fingerprint" not in store.status("openai", first)


def test_provider_merge_preserves_value_and_precedence() -> None:
    hub = ProviderHub([
        _Provider("public", {"price": {"value": 100, "source": "public"}, "keep": {"value": 7}}),
        _Provider("official", {"price": {"value": 101, "source": "official"}, "keep": {"value": None}}),
        _Provider("broken", {}, fail=True),
    ])
    request = FeatureRequest("005930", {"price", "keep"}, datetime.now(timezone.utc).isoformat())
    result = hub.fetch_blocking(request)
    assert result["price"]["value"] == 101
    assert result["price"]["source"] == "official"
    assert result["keep"]["value"] == 7
    assert any(row["provider_id"] == "broken" and row["errors"] for row in hub.last_diagnostics)


def test_blocking_provider_bridge_is_safe_inside_event_loop() -> None:
    hub = ProviderHub([_Provider("one", {"feature": {"value": 1}})])
    request = FeatureRequest("005930", {"feature"}, datetime.now(timezone.utc).isoformat())

    async def run() -> dict:
        return hub.fetch_blocking(request)

    assert asyncio.run(run())["feature"]["value"] == 1


def test_first_run_api_wizard_contract_and_no_plaintext_env_keys() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for element_id in (
        "api-setup-skip", "api-setup-save", "api-setup-test", "api-setup-clear",
        "api-setup-later", "api-setup-reconfigure", "setup-validation",
    ):
        assert f'id="{element_id}"' in html
    assert "/api/setup/complete" in js
    assert "/api/setup/test" in js
    assert "Windows 현재 사용자 전용 DPAPI" in html
    for secret_name in (
        "OPENAI_API_KEY=", "KIS_APP_KEY=", "KIS_APP_SECRET=", "OPEN_DART_API_KEY=",
        "ECOS_API_KEY=", "NAVER_CLIENT_ID=", "NAVER_CLIENT_SECRET=",
    ):
        assert secret_name not in env_example

