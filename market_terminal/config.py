from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .secret_store import DpapiSecretStore, ProviderValidationStore, secret_fingerprint

ASSET_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ASSET_ROOT
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"
SECRETS_FILE = DATA_DIR / "secrets.dpapi"
VALIDATION_FILE = DATA_DIR / "provider_validation.json"
SECRET_ENV_KEYS = {
    "KIS_APP_KEY",
    "KIS_APP_SECRET",
    "OPENAI_API_KEY",
    "OPEN_DART_API_KEY",
    "ECOS_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
}
SETUP_ENV_KEYS = set(SECRET_ENV_KEYS) | {"KIS_MODE"}
SECRET_STORE = DpapiSecretStore(SECRETS_FILE)
VALIDATION_STORE = ProviderValidationStore(VALIDATION_FILE)
SECRET_STORE_ERROR = ""
_DPAPI_SECRETS: dict[str, str] = {}


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def _scrub_legacy_env(path: Path) -> None:
    """Remove historical plaintext API keys while preserving normal options."""
    if not path.exists():
        return
    kept: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        key = raw.split("=", 1)[0].strip() if stripped and "=" in raw and not stripped.startswith("#") else ""
        if key not in SECRET_ENV_KEYS:
            kept.append(raw)
    temp = path.with_suffix(".env.scrub.tmp")
    temp.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    os.replace(temp, path)


_legacy_env_path = ROOT / ".env"
_legacy_env = _read_dotenv(_legacy_env_path)
for _key, _value in _legacy_env.items():
    if _key not in SECRET_ENV_KEYS:
        if _key in SETUP_ENV_KEYS or _key == "DATA_MODE":
            os.environ[_key] = _value
        else:
            os.environ.setdefault(_key, _value)
try:
    _legacy_secrets = {key: value for key, value in _legacy_env.items() if key in SECRET_ENV_KEYS and value}
    if _legacy_secrets:
        SECRET_STORE.update(_legacy_secrets)
        _scrub_legacy_env(_legacy_env_path)
    _DPAPI_SECRETS = SECRET_STORE.load()
    for _key, _value in _DPAPI_SECRETS.items():
        if _key in SECRET_ENV_KEYS:
            os.environ[_key] = _value
except Exception as _secret_exc:
    SECRET_STORE_ERROR = f"{type(_secret_exc).__name__}: DPAPI 저장소를 읽지 못했습니다. API를 다시 설정하십시오."


@dataclass(slots=True)
class Settings:
    settings_version: int = 3
    setup_completed: bool = False
    setup_choice: str = ""
    setup_completed_at: str = ""
    data_mode: str = "public"
    seed_money: float = 100_000_000.0
    fee_rate: float = 0.00015
    tax_rate: float = 0.0020
    slippage_rate: float = 0.00005
    default_symbol: str = "005930"
    refresh_ms: int = 1000
    kis_mode: str = "real"
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_enable_ws: bool = True
    external_feature_file: str = "data/external_features.json"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-sol"
    openai_analysis_enabled: bool = True
    openai_timeout_seconds: float = 180.0
    openai_max_retries: int = 2
    openai_stream: bool = True
    openai_store_responses: bool = False
    openai_fixture_mode: bool = False
    open_dart_api_key: str = ""
    ecos_api_key: str = ""
    naver_client_id: str = ""
    naver_client_secret: str = ""
    news_fixture_mode: bool = False
    news_refresh_seconds: int = 30
    news_min_materiality: int = 65
    news_min_data_quality: int = 60

    @property
    def round_trip_cost_rate(self) -> float:
        return self.fee_rate * 2 + self.tax_rate + self.slippage_rate * 2

    @property
    def kis_configured(self) -> bool:
        return bool(self.kis_app_key and self.kis_app_secret)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_settings() -> Settings:
    base = Settings(
        data_mode=os.getenv("DATA_MODE", "public").strip().lower(),
        seed_money=float(os.getenv("SEED_MONEY", "100000000")),
        fee_rate=float(os.getenv("FEE_RATE", "0.00015")),
        tax_rate=float(os.getenv("TAX_RATE", "0.0020")),
        slippage_rate=float(os.getenv("SLIPPAGE_RATE", "0.00005")),
        default_symbol=os.getenv("DEFAULT_SYMBOL", "005930"),
        refresh_ms=int(os.getenv("REFRESH_MS", "1000")),
        kis_mode=os.getenv("KIS_MODE", "real").strip().lower(),
        kis_app_key=_DPAPI_SECRETS.get("KIS_APP_KEY", ""),
        kis_app_secret=_DPAPI_SECRETS.get("KIS_APP_SECRET", ""),
        kis_enable_ws=_env_bool("KIS_ENABLE_WS", True),
        external_feature_file=os.getenv("EXTERNAL_FEATURE_FILE", "data/external_features.json"),
        openai_api_key=_DPAPI_SECRETS.get("OPENAI_API_KEY", ""),
        # The browser cannot select a cheaper model. Every request uses the
        # current frontier model explicitly required by the user.
        openai_model="gpt-5.6-sol",
        openai_analysis_enabled=_env_bool("OPENAI_ANALYSIS_ENABLED", True),
        openai_timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "180")),
        openai_max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "2")),
        openai_stream=_env_bool("OPENAI_STREAM", True),
        openai_store_responses=_env_bool("OPENAI_STORE_RESPONSES", False),
        openai_fixture_mode=_env_bool("OPENAI_FIXTURE_MODE", False),
        open_dart_api_key=_DPAPI_SECRETS.get("OPEN_DART_API_KEY", ""),
        ecos_api_key=_DPAPI_SECRETS.get("ECOS_API_KEY", ""),
        naver_client_id=_DPAPI_SECRETS.get("NAVER_CLIENT_ID", ""),
        naver_client_secret=_DPAPI_SECRETS.get("NAVER_CLIENT_SECRET", ""),
        news_fixture_mode=_env_bool("NEWS_FIXTURE_MODE", False),
        news_refresh_seconds=max(30, int(os.getenv("NEWS_REFRESH_SECONDS", "30"))),
        news_min_materiality=max(0, min(100, int(os.getenv("NEWS_MIN_MATERIALITY", "65")))),
        news_min_data_quality=max(0, min(100, int(os.getenv("NEWS_MIN_DATA_QUALITY", "60")))),
    )
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            saved_version = int(saved.get("settings_version", 0) or 0)
            legacy_settings = saved_version < 2
            for key, value in saved.items():
                if hasattr(base, key) and key not in {
                    "kis_app_key", "kis_app_secret", "openai_api_key",
                    "open_dart_api_key", "ecos_api_key",
                    "naver_client_id", "naver_client_secret",
                    "openai_model",
                }:
                    setattr(base, key, value)
            # v1 automatically stored DEMO when KIS was absent.  That was a
            # fallback, not an intentional user choice; migrate it once to
            # the new no-key real-data default.  v2 can still explicitly save
            # DEMO from the settings screen.
            if legacy_settings and base.data_mode == "demo" and not os.getenv("DATA_MODE", "").strip():
                base.data_mode = "public"
        except (OSError, ValueError, TypeError):
            pass
    # .env의 명시적 모드는 이전 UI 저장값보다 우선합니다.
    env_mode = os.getenv("DATA_MODE", "").strip().lower()
    if env_mode in {"public", "kis", "demo"}:
        base.data_mode = env_mode
    if base.data_mode == "kis" and (
        not base.kis_configured or not provider_secret_verified("kis", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_MODE")
    ):
        base.data_mode = "public"
    if base.data_mode not in {"public", "kis", "demo"}:
        base.data_mode = "public"
    return base


def save_runtime_settings(settings: Settings, changes: dict[str, Any]) -> Settings:
    allowed = {
        "data_mode", "seed_money", "fee_rate", "tax_rate", "slippage_rate",
        "default_symbol", "refresh_ms", "news_min_materiality", "news_min_data_quality",
        "kis_mode", "setup_completed", "setup_choice", "setup_completed_at", "settings_version",
    }
    for key, value in changes.items():
        if key in allowed:
            setattr(settings, key, value)
    safe = asdict(settings)
    for secret in (
        "kis_app_key", "kis_app_secret", "openai_api_key",
        "open_dart_api_key", "ecos_api_key",
        "naver_client_id", "naver_client_secret",
    ):
        safe.pop(secret, None)
    SETTINGS_FILE.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings


def save_secret_settings(changes: dict[str, str]) -> Path:
    """Persist API secrets with Windows current-user DPAPI encryption."""
    global _DPAPI_SECRETS
    updates = {
        key: str(value).strip()
        for key, value in changes.items()
        if key in SECRET_ENV_KEYS and str(value).strip()
    }
    if not updates:
        return SECRETS_FILE
    path = SECRET_STORE.update(updates)
    _DPAPI_SECRETS.update(updates)
    for key, value in updates.items():
        os.environ[key] = value
    return path


def stored_secret_values() -> dict[str, str]:
    try:
        stored = SECRET_STORE.load()
    except Exception:
        stored = {}
    values = {key: stored.get(key, "") for key in SECRET_ENV_KEYS}
    values["KIS_MODE"] = os.getenv("KIS_MODE", "")
    if not values.get("KIS_MODE"):
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) if SETTINGS_FILE.exists() else {}
            values["KIS_MODE"] = str(saved.get("kis_mode") or "real")
        except (OSError, ValueError, TypeError):
            values["KIS_MODE"] = "real"
    return values


def provider_secret_status(provider_id: str, *keys: str) -> dict[str, Any]:
    values = stored_secret_values()
    fingerprint = secret_fingerprint(*(values.get(key, "") for key in keys))
    return VALIDATION_STORE.status(provider_id, fingerprint)


def provider_secret_verified(provider_id: str, *keys: str) -> bool:
    values = stored_secret_values()
    fingerprint = secret_fingerprint(*(values.get(key, "") for key in keys))
    return VALIDATION_STORE.is_verified(provider_id, fingerprint)


def mark_setup_completed(settings: Settings, choice: str) -> Settings:
    now = datetime.now().astimezone().isoformat()
    return save_runtime_settings(settings, {
        "settings_version": 3,
        "setup_completed": True,
        "setup_choice": choice,
        "setup_completed_at": now,
    })


STOCKS: list[dict[str, str]] = [
    {"symbol": "005930", "name": "삼성전자", "sector": "반도체"},
    {"symbol": "000660", "name": "SK하이닉스", "sector": "반도체"},
    {"symbol": "005380", "name": "현대차", "sector": "자동차"},
    {"symbol": "000270", "name": "기아", "sector": "자동차"},
    {"symbol": "373220", "name": "LG에너지솔루션", "sector": "2차전지"},
    {"symbol": "207940", "name": "삼성바이오로직스", "sector": "바이오"},
    {"symbol": "068270", "name": "셀트리온", "sector": "바이오"},
    {"symbol": "035420", "name": "NAVER", "sector": "인터넷"},
    {"symbol": "035720", "name": "카카오", "sector": "인터넷"},
    {"symbol": "105560", "name": "KB금융", "sector": "금융"},
    {"symbol": "055550", "name": "신한지주", "sector": "금융"},
    {"symbol": "005490", "name": "POSCO홀딩스", "sector": "철강·2차전지"},
    {"symbol": "012450", "name": "한화에어로스페이스", "sector": "방산"},
    {"symbol": "034020", "name": "두산에너빌리티", "sector": "전력·원전"},
    {"symbol": "028260", "name": "삼성물산", "sector": "지주"},
]
STOCK_BY_SYMBOL = {item["symbol"]: item for item in STOCKS}
