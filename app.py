from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from market_terminal.analysis_engine import analyze_trade
from market_terminal.ai_analysis import AiAnalysisService, AiJobManager, AlertEvaluator, AnalysisInputBuilder
from market_terminal.ai_models import AiAnalysisRequest, ApplyAlertsRequest, FeatureLayoutUpdate
from market_terminal.config import (
    ASSET_ROOT,
    ROOT,
    STOCK_BY_SYMBOL,
    STOCKS,
    Settings,
    SECRET_STORE_ERROR,
    VALIDATION_STORE,
    load_settings,
    mark_setup_completed,
    provider_secret_status,
    provider_secret_verified,
    save_runtime_settings,
    save_secret_settings,
    stored_secret_values,
)
from market_terminal.connection_tests import test_all
from market_terminal.database import Database
from market_terminal.feature_engine import compute_technical_features, merge_external_features
from market_terminal.feature_providers import (
    CsvUploadProvider,
    EcosProvider,
    ManualJsonProvider,
    OpenDartProvider,
    FeatureRequest,
    ProviderHub,
    StatusProvider,
    UnavailableProvider,
)
from market_terminal.feature_registry import FeatureRegistry
from market_terminal.news_pipeline import NewsPipelineService
from market_terminal.providers import MarketProvider
from market_terminal.trader_features import (
    build_semi_auto_playbook,
    compute_trader_features,
    market_watch_cards,
)
from market_terminal.trading import entry_cost, exit_result, validate_quantity
from market_terminal.secret_store import secret_fingerprint

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("kr-terminal")
KST = ZoneInfo("Asia/Seoul")
settings: Settings = load_settings()
db = Database(ROOT / "data" / "terminal.sqlite3")
provider = MarketProvider(settings)
chart_cache: dict[tuple[str, str, str], tuple[float, pd.DataFrame, str, str]] = {}
chart_cache_lock = threading.RLock()
chart_fetch_lock = threading.Lock()
snapshot_cache: dict[str,dict[str,Any]] = {}
feature_registry = FeatureRegistry(ASSET_ROOT / "config")
analysis_input_builder = AnalysisInputBuilder(feature_registry)
ai_service = AiAnalysisService(settings, db, feature_registry)
ai_jobs = AiJobManager(ai_service)
alert_evaluator = AlertEvaluator(db)
news_service = NewsPipelineService(settings, db, ROOT, ASSET_ROOT)
provider_reconfigure_lock = asyncio.Lock()
news_background_task: asyncio.Task[Any] | None = None
auto_news_analysis_clusters: dict[str,str] = {}


def build_feature_provider_hub() -> ProviderHub:
    external_path = Path(settings.external_feature_file)
    if not external_path.is_absolute():
        external_path = ROOT / external_path
    dart_key = settings.open_dart_api_key if provider_secret_verified("open_dart", "OPEN_DART_API_KEY") else ""
    ecos_key = settings.ecos_api_key if provider_secret_verified("ecos", "ECOS_API_KEY") else ""
    # Low-to-high precedence: official API, user CSV, explicit Manual JSON.
    return ProviderHub([
        StatusProvider("public_market", "NO_KEY_REAL_DATA", "네이버 공개 시세·호가·차트·수급·지수 연결"),
        StatusProvider("public_macro", "NO_KEY_PERIODIC", "FRED 공개 금리/신용·Yahoo FX/글로벌·ECOS sample 연결"),
        StatusProvider("public_news", "NO_KEY_NEAR_REALTIME", "네이버 금융 종목뉴스·DART 공식 RSS 연결"),
        OpenDartProvider(dart_key, ROOT / "data" / "dart_corp_codes.xml"),
        EcosProvider(ecos_key),
        CsvUploadProvider(ROOT / "data" / "import"),
        ManualJsonProvider(external_path),
    ])


feature_provider_hub = build_feature_provider_hub()


class SettingsUpdate(BaseModel):
    seed_money: float | None = Field(default=None,gt=0)
    data_mode: str | None = None
    fee_rate: float | None = Field(default=None,ge=0,le=.1)
    tax_rate: float | None = Field(default=None,ge=0,le=.1)
    slippage_rate: float | None = Field(default=None,ge=0,le=.1)
    refresh_ms: int | None = Field(default=None,ge=250,le=10000)
    news_min_materiality: int | None = Field(default=None,ge=0,le=100)
    news_min_data_quality: int | None = Field(default=None,ge=0,le=100)


class CredentialSetup(BaseModel):
    kis_mode: str = Field(default="real",pattern="^(real|paper)$")
    kis_app_key: str | None = Field(default=None,max_length=512)
    kis_app_secret: str | None = Field(default=None,max_length=1024)
    openai_api_key: str | None = Field(default=None,max_length=1024)
    open_dart_api_key: str | None = Field(default=None,max_length=512)
    ecos_api_key: str | None = Field(default=None,max_length=512)
    naver_client_id: str | None = Field(default=None,max_length=512)
    naver_client_secret: str | None = Field(default=None,max_length=1024)
    activate_kis: bool = False


class SetupComplete(BaseModel):
    choice: Literal["public", "later"]


class OpenTradeRequest(BaseModel):
    symbol: str
    entry_time: str
    entry_price: float = Field(gt=0)
    quantity: int = Field(gt=0)
    mode: str = "virtual"
    side: Literal["LONG", "SHORT"] = "LONG"
    entry_features: dict[str,Any] = Field(default_factory=dict)


class CloseTradeRequest(BaseModel):
    exit_time: str
    exit_price: float = Field(gt=0)
    quantity: int | None = Field(default=None,gt=0)
    exit_features: dict[str,Any] = Field(default_factory=dict)


class HistoricalTradeRequest(BaseModel):
    symbol: str
    entry_time: str
    entry_price: float = Field(gt=0)
    exit_time: str
    exit_price: float = Field(gt=0)
    quantity: int = Field(gt=0)
    side: Literal["LONG", "SHORT"] = "LONG"
    entry_features: dict[str,Any] = Field(default_factory=dict)
    exit_features: dict[str,Any] = Field(default_factory=dict)


def supplemental_provider_features(symbol: str, observed_at: str) -> dict[str, dict[str, Any]]:
    feature_ids = set(feature_registry.definitions)
    for source in feature_provider_hub.providers:
        try:
            feature_ids.update(source.supported_features())
        except Exception:
            continue
    try:
        return feature_provider_hub.fetch_blocking(FeatureRequest(
            symbol=symbol, feature_ids=feature_ids, as_of=observed_at,
        ))
    except Exception as exc:
        logger.warning("보강 공급자 병합 실패(기본 시세 계속): %s", type(exc).__name__)
        return {}


def merge_nonempty(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for feature_id, raw in overlay.items():
        value = raw.get("value") if isinstance(raw, dict) else raw
        if value is None or value == "":
            continue
        if isinstance(value, float) and pd.isna(value):
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        merged[feature_id] = raw
    return merged


def fill_missing_group_features(groups: dict[str, list[dict[str, Any]]], flat: dict[str, Any], external: dict[str, Any]) -> None:
    """Let an actual provider value replace only an empty calculated placeholder."""
    for items in groups.values():
        for item in items:
            feature_id = str(item.get("key") or item.get("feature_id") or "")
            if not feature_id or item.get("value") is not None:
                continue
            raw = external.get(feature_id)
            value = raw.get("value") if isinstance(raw, dict) else raw
            if value is None:
                continue
            item["value"] = value
            if isinstance(raw, dict):
                for key in ("source", "quality", "direction", "observed_at", "metadata"):
                    if raw.get(key) is not None:
                        item[key] = raw[key]
            flat[feature_id] = value


async def evaluate_news_symbol(symbol:str)->dict[str,Any]:
    frame=await asyncio.to_thread(provider.current_frame,symbol)
    if frame.empty:
        frame,_,_=await asyncio.to_thread(provider.get_bars,symbol)
    _,technical=compute_technical_features(frame)
    observed_at=datetime.now(KST).isoformat()
    external,members=await asyncio.to_thread(provider.external_features,symbol,frame)
    supplemental=await asyncio.to_thread(supplemental_provider_features,symbol,observed_at)
    external=merge_nonempty(external,supplemental)
    _,external_flat=merge_external_features(external,"SIMULATED" if provider.mode=="demo" else "LIVE")
    return news_service.evaluate(
        symbol,frame,{**technical,**external_flat},members,provider.mode,observed_at
    )


async def queue_news_gpt_analysis(symbol:str,overview:dict[str,Any])->str|None:
    clusters=overview.get("clusters") or []
    if not clusters or not settings.openai_analysis_enabled:
        return None
    if not settings.openai_fixture_mode and (
        not settings.openai_api_key or not provider_secret_verified("openai", "OPENAI_API_KEY")
    ):
        return None
    latest=clusters[0]
    verification=(latest.get("verification") or {}).get("status")
    materiality=int((latest.get("materiality") or {}).get("score") or 0)
    if verification in {"UNVERIFIED","SINGLE_SOURCE"} and materiality < 40:
        return None
    # Re-analyze only when event evidence changes.
    verification_info=latest.get("verification") or {}
    analysis_key=json.dumps({
        "cluster_id":latest.get("cluster_id"),
        "verification":verification,
        "independent_sources":verification_info.get("independent_source_count"),
        "primary_sources":verification_info.get("primary_source_count"),
        "syndicated_copies":verification_info.get("syndicated_copy_count"),
        "materiality":materiality,
        "sentiment":(latest.get("sentiment") or {}).get("sentiment"),
        "event_type":latest.get("event_type"),
    },sort_keys=True,ensure_ascii=False)
    if auto_news_analysis_clusters.get(symbol)==analysis_key:
        return None
    frame=await asyncio.to_thread(provider.current_frame,symbol)
    if frame.empty:
        frame,_,_=await asyncio.to_thread(provider.get_bars,symbol)
    daily,_,_=await asyncio.to_thread(provider.get_chart,symbol,"3mo")
    tick=await asyncio.to_thread(provider.get_tick,symbol)
    snap=await asyncio.to_thread(build_snapshot,symbol,frame,tick)
    trades_payload=await asyncio.to_thread(db.list_trades,symbol,None,300)
    portfolio_payload=await asyncio.to_thread(db.portfolio_summary,settings.seed_money)
    sentiment=(latest.get("sentiment") or {}).get("sentiment") or "NEUTRAL"
    question=(
        f"[NAVER_NEWS_AUTO] {latest.get('canonical_title','')} 사건을 현재 시장·가격·거래량·"
        f"외국인·기관·회원사 피처와 함께 분석하라. 검증 상태 {verification}, 방향 {sentiment}. "
        "독립 원출처와 사건 클러스터만 검증 근거로 사용하고 재배포 기사 수는 근거로 쓰지 마라. "
        "LONG 또는 SHORT 진입 검토 피처 기준선, 원화 진입 범위, 손절·익절 가격과 퍼센트를 "
        "조건부로 제시하되 실주문과 자동주문은 만들지 마라."
    )
    analysis_input=analysis_input_builder.build(
        analysis_type="SYMBOL",
        symbol=symbol,
        question=question,
        frame=frame,
        daily_frame=daily,
        snapshot=snap,
        tick=tick,
        trades=trades_payload,
        portfolio=portfolio_payload,
    )
    auto_news_analysis_clusters[symbol]=analysis_key
    return ai_jobs.start(analysis_input)


async def news_refresh_loop()->None:
    while True:
        try:
            await news_service.refresh([item["symbol"] for item in STOCKS])
            affected=sorted({symbol for cluster in news_service.clusters for symbol in cluster.get("symbols",[])})
            for symbol in affected:
                overview=await evaluate_news_symbol(symbol)
                await queue_news_gpt_analysis(symbol,overview)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("뉴스 갱신 실패(시세·차트는 계속): %s",type(exc).__name__)
        await asyncio.sleep(max(settings.news_refresh_seconds,30))


@asynccontextmanager
async def lifespan(_: FastAPI):
    global news_background_task
    await provider.startup()
    news_background_task=asyncio.create_task(news_refresh_loop())
    yield
    if news_background_task:
        news_background_task.cancel()
        try:await news_background_task
        except asyncio.CancelledError:pass
    await provider.shutdown()


app = FastAPI(title="국장 단타 분석 터미널",version="2.1.0",lifespan=lifespan)
app.mount("/static",StaticFiles(directory=ASSET_ROOT/"static"),name="static")


def bars_to_records(frame: pd.DataFrame)->list[dict[str,Any]]:
    if frame.empty:return []
    records=frame.tail(2500).to_dict(orient="records")
    for row in records:
        row["time"]=str(row["time"])
        for key in ("open","high","low","close"):row[key]=float(row[key])
        row["volume"]=int(float(row["volume"]))
    return records


def chart_bar_resolution(frame: pd.DataFrame) -> str:
    """Distinguish actual minute bars from daily bars; range labels alone cannot."""
    if frame.empty or "time" not in frame:
        return "unknown"
    times = pd.to_datetime(frame["time"], errors="coerce")
    dates = times.dropna().dt.date
    return "intraday" if dates.duplicated().any() else "daily"


def get_chart_data(symbol: str, chart_range: str) -> tuple[pd.DataFrame, str, str]:
    """Short-lived cache prevents range switches from repeatedly blocking on KIS."""
    key = (provider.mode, symbol, chart_range)
    ttl = {"1d": 2.0, "1w": 20.0, "1mo": 120.0, "3mo": 300.0}[chart_range]
    now = time.monotonic()
    with chart_cache_lock:
        cached = chart_cache.get(key)
        if cached and now - cached[0] < ttl:
            return cached[1].copy(), cached[2], cached[3]
    # Do not issue duplicate long KIS history requests when browser actions overlap.
    with chart_fetch_lock:
        now = time.monotonic()
        with chart_cache_lock:
            cached = chart_cache.get(key)
            if cached and now - cached[0] < ttl:
                return cached[1].copy(), cached[2], cached[3]
        frame, source, quality = provider.get_chart(symbol, chart_range)
        with chart_cache_lock:
            chart_cache[key] = (time.monotonic(), frame.copy(), source, quality)
        return frame, source, quality


def build_snapshot(symbol:str,frame:pd.DataFrame|None=None,tick:dict[str,Any]|None=None)->dict[str,Any]:
    if frame is None or frame.empty:frame=provider.current_frame(symbol)
    if frame.empty:frame,_,_=provider.get_bars(symbol)
    if tick is None:tick=provider.get_tick(symbol)
    synced_frame=provider.current_frame(symbol)
    if not synced_frame.empty:frame=synced_frame
    observed_at=datetime.now(KST).isoformat()
    tg,tf=compute_technical_features(frame); external,members=provider.external_features(symbol,frame)
    external=merge_nonempty(external,supplemental_provider_features(symbol,observed_at))
    fill_missing_group_features(tg,tf,external)
    eg,ef=merge_external_features(external,"SIMULATED" if provider.mode=="demo" else "LIVE")
    symbol_profile=feature_registry.profile(symbol)
    trader_context,peer_rows=provider.trader_context(symbol,symbol_profile)
    trader_groups,trader_flat,trader_promoted,attention_keywords=compute_trader_features(
        frame,tick,trader_context,peer_rows,provider.mode
    )
    if members:
        member_items=[]
        for idx,row in enumerate(members[:12],start=1):
            net=float(row.get("net") or 0)
            member_source="KIS 실시간회원사" if provider.mode=="kis" else "네이버 금융 회원사 상위 창구" if provider.mode=="public" else "DEMO 생성"
            member_items.append({"key":f"member_net_{idx}","label":f"{row.get('name','회원사')} 순매수","value":round(net,0),"unit":"주","direction":"positive" if net>0 else "negative" if net<0 else "neutral","source":member_source,"quality":row.get("quality","SIMULATED" if provider.mode=="demo" else "LIVE")})
        eg["특정 회원사·창구 유입"] = member_items
        top=max(members,key=lambda x:float(x.get("net") or 0)); bottom=min(members,key=lambda x:float(x.get("net") or 0))
        ef.update({"top_member_name":top.get("name"),"top_member_net":float(top.get("net") or 0),"exit_member_name":bottom.get("name"),"exit_member_net":float(bottom.get("net") or 0)})
    news_overview=news_service.evaluate(symbol,frame,{**tf,**ef},members,provider.mode,observed_at)
    news_groups,news_flat=news_service.feature_groups(news_overview)
    flat={**tf,**ef,**trader_flat,**news_flat}
    combined_external={**external,**trader_context,**news_overview.get("feature_values",{})}
    combined_groups,flat,standardized_features=feature_registry.enrich(
        symbol,{**trader_groups,**tg,**news_groups,**eg},flat,combined_external,provider.mode,observed_at
    )
    active_alerts=db.list_ai_alerts(symbol,active_only=True)
    triggered_alerts=alert_evaluator.evaluate(symbol,flat)
    latest_ai_rows=db.list_ai_analyses(symbol,1)
    latest_ai=(latest_ai_rows[0].get("response_json") if latest_ai_rows else None)
    snap={"symbol":symbol,"symbol_name":STOCK_BY_SYMBOL.get(symbol,{}).get("name",symbol),
        "symbol_profile":symbol_profile,
        "time":observed_at,"mode":provider.mode,"groups":combined_groups,"features":standardized_features,
        "flat":flat,"tick":tick,"members":members,"peer_context":peer_rows,
        "active_alerts":active_alerts,"triggered_alerts":triggered_alerts,
        "latest_ai_analysis":latest_ai,
        "market_promoted_features":trader_promoted,
        "market_watch":market_watch_cards(flat),"attention_keywords":attention_keywords,
        "news_promoted_features":news_overview.get("promoted_features",[]),
        "news_context":{
            "clusters":[{
                "cluster_id":item["cluster_id"],"title":item["canonical_title"],
                "published_at":item["first_published_at"],"verification":item["verification"],
                "materiality":item["materiality"],"sentiment":item["sentiment"],
                "evaluation":item.get("evaluation",{}),
            } for item in news_overview.get("clusters",[])[:8]],
            "candidates":news_overview.get("candidates",[])[:8],
            "independent_sources_not_article_count":True,
            "institution_is_not_member_flow":True,
            "statistics_available":False,"automatic_order":False,
        },
        "provider_diagnostics":feature_provider_hub.last_diagnostics,
        "data_notice":(
            "KIS 실시간/추정 데이터입니다. 현재가·호가는 실시간이며, 외국인·기관은 장중 지정시각 가집계입니다."
            if provider.mode=="kis" else
            "간편 무키 실데이터 모드: 네이버 공개 시세·호가·분봉·일봉·투자자/회원사 흐름과 공개 거시 피드를 사용합니다. 값마다 7초·20분·일별 지연을 구분하며, 미제공 값은 생성하지 않습니다."
            if provider.mode=="public" else
            "DEMO 모드: 현재가·호가·수급은 시뮬레이션이며 실제 시장 가격이 아닙니다."
        )}
    snapshot_cache[symbol]=snap;db.store_snapshot(symbol,snap["time"],flat);return snap


def setup_status_payload() -> dict[str, Any]:
    secrets=stored_secret_values()
    def keyed(provider_id:str,keys:tuple[str,...],purpose:str,features:list[str])->dict[str,Any]:
        status=provider_secret_status(provider_id,*keys)
        configured=all(bool(secrets.get(key)) for key in keys)
        if not configured:
            status={"state":"NOT_CONFIGURED","message":"미설정","last_validation_at":None,"last_success_at":None}
        return {
            "configured":configured,
            "connected":configured and status.get("state")=="NORMAL",
            "state":status.get("state"),"message":status.get("message"),
            "last_validation_at":status.get("last_validation_at"),
            "last_success_at":status.get("last_success_at"),
            "purpose":purpose,"fills_features":features,
        }
    openai=keyed("openai",("OPENAI_API_KEY",),f"GPT 질문·조건부 피처 분석 ({settings.openai_model})",["GPT 시장·종목 조건분석","트리거 기준선","거래 피처 복기","뉴스 GPT 자동분석"])
    kis=keyed("kis",("KIS_APP_KEY","KIS_APP_SECRET","KIS_MODE"),"선택 고급 공급원",["거래소급 체결·호가","KOSPI200 선물","야간선물","정밀 회원사·장중 수급"])
    dart=keyed("open_dart",("OPEN_DART_API_KEY",),"구조화 공시·재무 보강",["공시 유형별 건수","매출·이익·현금흐름","부채·자본·ROA·ROE"])
    ecos=keyed("ecos",("ECOS_API_KEY",),"한국은행 구조화 통계 보강",["기준금리","원/달러 공식 통계","주택가격 등 ECOS 제공 지표"])
    naver=keyed("naver_news",("NAVER_CLIENT_ID","NAVER_CLIENT_SECRET"),"뉴스 검색 범위 보강",["네이버 뉴스 검색 결과","종목뉴스 사건 클러스터 추가 원출처"])
    return {
        "needs_setup":not settings.setup_completed or settings.settings_version<3 or bool(SECRET_STORE_ERROR),
        "setup_completed":settings.setup_completed,"setup_choice":settings.setup_choice,
        "setup_reason":SECRET_STORE_ERROR or ("최초 설정 또는 v3 보안 저장 마이그레이션 필요" if not settings.setup_completed or settings.settings_version<3 else "완료"),
        "data_mode": provider.mode,
        "kis_mode": settings.kis_mode,
        "credential_storage":"Windows DPAPI 현재 사용자 암호화 / data/secrets.dpapi",
        "secrets_returned": False,
        "providers": {
            "public_market": {
                "configured": True,
                "connected": True,
                "implementation": "CONNECTED_NO_KEY",
                "purpose": "네이버 공개 현재가·5단계 호가·1분/일봉·투자자/회원사·시장폭·프로그램 + 공개 글로벌/거시",
            },
            "kis":{**kis,"implementation":"CONNECTED" if kis["connected"] else "API_KEY_REQUIRED"},
            "openai":{**openai,"implementation":"CONNECTED" if openai["connected"] else "API_KEY_REQUIRED"},
            "open_dart":{**dart,"public_connected":True,"implementation":"CONNECTED_KEYED" if dart["connected"] else "NO_KEY_RSS_ONLY"},
            "naver_news":{**naver,"public_connected":True,"implementation":"CONNECTED_KEYED" if naver["connected"] else "NO_KEY_FINANCE_NEWS"},
            "ecos":{**ecos,"public_connected":True,"implementation":"CONNECTED_KEYED" if ecos["connected"] else "NO_KEY_SAMPLE_ONLY"},
        },
    }


@app.get("/")
def index()->FileResponse:return FileResponse(ASSET_ROOT/"static"/"index.html")


@app.get("/api/setup/status")
def setup_status()->dict[str,Any]:
    return setup_status_payload()


PROVIDER_CREDENTIAL_KEYS={
    "openai":("OPENAI_API_KEY",),
    "open_dart":("OPEN_DART_API_KEY",),
    "ecos":("ECOS_API_KEY",),
    "naver_news":("NAVER_CLIENT_ID","NAVER_CLIENT_SECRET"),
    "kis":("KIS_APP_KEY","KIS_APP_SECRET","KIS_MODE"),
}


def _local_setup_request(request:Request)->None:
    client_host=request.client.host if request.client else ""
    if client_host not in {"127.0.0.1","::1","localhost"}:
        raise HTTPException(403,"API 설정은 이 PC의 로컬 화면에서만 가능합니다.")


def _credential_values(payload:CredentialSetup|None=None)->dict[str,str]:
    values=stored_secret_values()
    if payload:
        supplied={
            "KIS_MODE":payload.kis_mode,"KIS_APP_KEY":payload.kis_app_key,
            "KIS_APP_SECRET":payload.kis_app_secret,"OPENAI_API_KEY":payload.openai_api_key,
            "OPEN_DART_API_KEY":payload.open_dart_api_key,"ECOS_API_KEY":payload.ecos_api_key,
            "NAVER_CLIENT_ID":payload.naver_client_id,"NAVER_CLIENT_SECRET":payload.naver_client_secret,
        }
        for key,value in supplied.items():
            if value and str(value).strip():values[key]=str(value).strip()
    values["KIS_MODE"]=str(values.get("KIS_MODE") or settings.kis_mode or "real")
    return values


def _record_validation(values:dict[str,str],results:dict[str,dict[str,Any]])->None:
    for provider_id,keys in PROVIDER_CREDENTIAL_KEYS.items():
        fingerprint=secret_fingerprint(*(values.get(key,"") for key in keys))
        if not fingerprint:continue
        result=results[provider_id]
        VALIDATION_STORE.record(provider_id,fingerprint,result["state"],result["message"])


def _apply_credentials(values:dict[str,str])->None:
    mapping={
        "KIS_MODE":"kis_mode","KIS_APP_KEY":"kis_app_key","KIS_APP_SECRET":"kis_app_secret",
        "OPENAI_API_KEY":"openai_api_key","OPEN_DART_API_KEY":"open_dart_api_key",
        "ECOS_API_KEY":"ecos_api_key","NAVER_CLIENT_ID":"naver_client_id",
        "NAVER_CLIENT_SECRET":"naver_client_secret",
    }
    for env_key,attribute in mapping.items():
        setattr(settings,attribute,values.get(env_key,""))


async def _reconfigure_after_setup(activate_kis:bool)->None:
    global provider,feature_provider_hub
    if activate_kis and provider_secret_verified("kis","KIS_APP_KEY","KIS_APP_SECRET","KIS_MODE"):
        settings.data_mode="kis"
    else:
        settings.data_mode="public"
    save_runtime_settings(settings,{"data_mode":settings.data_mode,"kis_mode":settings.kis_mode})
    async with provider_reconfigure_lock:
        new_provider=MarketProvider(settings)
        await provider.shutdown()
        provider=new_provider
        with chart_cache_lock:
            chart_cache.clear()
        await provider.startup()
        feature_provider_hub=build_feature_provider_hub()
        news_service.configure_providers()


@app.post("/api/setup/test")
async def test_credentials(request:Request,payload:CredentialSetup)->dict[str,Any]:
    _local_setup_request(request)
    values=_credential_values(payload)
    results=await asyncio.to_thread(test_all,values,values["KIS_MODE"])
    return {"ok":True,"results":results,"secrets_returned":False}


@app.post("/api/setup/complete")
async def complete_setup(request:Request,payload:SetupComplete)->dict[str,Any]:
    _local_setup_request(request)
    settings.data_mode="public"
    mark_setup_completed(settings,payload.choice)
    await _reconfigure_after_setup(False)
    return setup_status_payload()


@app.post("/api/setup/credentials")
async def save_credentials(request:Request,payload:CredentialSetup)->dict[str,Any]:
    _local_setup_request(request)
    if request.headers.get("x-setup-intent")!="save-local-api-keys":
        raise HTTPException(403,"API 키 저장 요청 확인 헤더가 없습니다.")

    fields={
        "KIS_MODE":payload.kis_mode,
        "KIS_APP_KEY":payload.kis_app_key,
        "KIS_APP_SECRET":payload.kis_app_secret,
        "OPENAI_API_KEY":payload.openai_api_key,
        "OPEN_DART_API_KEY":payload.open_dart_api_key,
        "ECOS_API_KEY":payload.ecos_api_key,
        "NAVER_CLIENT_ID":payload.naver_client_id,
        "NAVER_CLIENT_SECRET":payload.naver_client_secret,
    }
    updates={key:value.strip() for key,value in fields.items() if key!="KIS_MODE" and value and value.strip()}
    save_secret_settings(updates)
    os.environ["KIS_MODE"]=payload.kis_mode
    values=_credential_values(payload)
    results=await asyncio.to_thread(test_all,values,values["KIS_MODE"])
    _record_validation(values,results)
    _apply_credentials(values)
    mark_setup_completed(settings,"api_saved")
    # A first-run save with both KIS credentials is an explicit request to use
    # them.  Do not leave the terminal in public mode merely because the UI
    # checkbox was missed or a stale browser asset sent ``false``.
    activate_kis=payload.activate_kis or bool(
        (payload.kis_app_key or "").strip() and (payload.kis_app_secret or "").strip()
    )
    await _reconfigure_after_setup(activate_kis)

    result=setup_status_payload()
    kis_result=results["kis"]
    result.update({
        "ok":True,"saved":sorted(updates),"validation":results,
        "kis_validation":{"attempted":kis_result["state"]!="NOT_CONFIGURED","ok":kis_result["ok"],"message":kis_result["message"]},
    })
    return result


@app.get("/api/bootstrap")
async def bootstrap(symbol:str=Query(default=settings.default_symbol),chart_range:str=Query(default="1d",pattern="^(1d|1w|1mo|3mo)$"))->dict[str,Any]:
    if symbol not in STOCK_BY_SYMBOL:raise HTTPException(404,"지원 종목이 아닙니다.")
    try:await news_service.refresh([item["symbol"] for item in STOCKS])
    except Exception as exc:logger.warning("부트스트랩 뉴스 갱신 실패(계속 진행): %s",type(exc).__name__)
    frame,source,quality=get_chart_data(symbol,chart_range)
    feature_frame=provider.current_frame(symbol)
    snap=build_snapshot(symbol,feature_frame if not feature_frame.empty else frame)
    return {"stocks":STOCKS,"settings":{"data_mode":provider.mode,"seed_money":settings.seed_money,
        "fee_rate":settings.fee_rate,"tax_rate":settings.tax_rate,"slippage_rate":settings.slippage_rate,
        "round_trip_cost_rate":settings.round_trip_cost_rate,"refresh_ms":settings.refresh_ms,
        "news_min_materiality":settings.news_min_materiality,"news_min_data_quality":settings.news_min_data_quality,
        "kis_configured":settings.kis_configured},
        "selected":STOCK_BY_SYMBOL[symbol],"bars":bars_to_records(frame),"source":source,"quality":quality,"chart_range":chart_range,"bar_resolution":chart_bar_resolution(frame),
        "snapshot":snap,"trades":db.list_trades(),"portfolio":db.portfolio_summary(settings.seed_money),
        "ai":{"model":settings.openai_model,"enabled":settings.openai_analysis_enabled,
              "key_configured":bool(settings.openai_api_key),
              "key_verified":provider_secret_verified("openai", "OPENAI_API_KEY"),
              "fixture_mode":settings.openai_fixture_mode,
              "statistical_engine_available":False},
        "feature_layout":db.get_feature_layout(symbol),"news":news_service.overview(symbol)}


@app.get("/api/news/overview")
async def news_overview(symbol:str=Query(default=settings.default_symbol))->dict[str,Any]:
    if symbol not in STOCK_BY_SYMBOL:raise HTTPException(404,"지원 종목이 아닙니다.")
    try:
        await news_service.refresh([item["symbol"] for item in STOCKS])
        overview=await evaluate_news_symbol(symbol)
        await queue_news_gpt_analysis(symbol,overview)
        return overview
    except Exception as exc:
        logger.warning("뉴스 개요 갱신 실패: %s",type(exc).__name__)
        return {**news_service.overview(symbol),"refresh_error":type(exc).__name__}


@app.post("/api/news/refresh")
async def refresh_news(symbol:str=Query(default=settings.default_symbol))->dict[str,Any]:
    if symbol not in STOCK_BY_SYMBOL:raise HTTPException(404,"지원 종목이 아닙니다.")
    try:
        refresh_result=await news_service.refresh([item["symbol"] for item in STOCKS],force=True)
        overview=await evaluate_news_symbol(symbol)
        job_id=await queue_news_gpt_analysis(symbol,overview)
        return {"ok":True,"refresh":refresh_result,"overview":overview,"ai_job_id":job_id}
    except Exception as exc:
        logger.warning("수동 뉴스 갱신 실패: %s",type(exc).__name__)
        raise HTTPException(503,"뉴스 공급자 갱신 실패. 시세·차트·가상거래는 계속 사용할 수 있습니다.") from exc


@app.get("/api/chart/{symbol}")
def chart(symbol:str,chart_range:str=Query(default="1d",pattern="^(1d|1w|1mo|3mo)$"))->dict[str,Any]:
    if symbol not in STOCK_BY_SYMBOL:raise HTTPException(404,"지원 종목이 아닙니다.")
    frame,source,quality=get_chart_data(symbol,chart_range)
    return {"symbol":symbol,"chart_range":chart_range,"bars":bars_to_records(frame),"source":source,"quality":quality,"bar_resolution":chart_bar_resolution(frame)}


@app.get("/api/snapshot/{symbol}")
def snapshot(symbol:str)->dict[str,Any]:
    if symbol not in STOCK_BY_SYMBOL:raise HTTPException(404,"지원 종목이 아닙니다.")
    return build_snapshot(symbol)


@app.get("/api/ai/status")
async def ai_status()->dict[str,Any]:
    return {
        "enabled":settings.openai_analysis_enabled,
        "key_configured":bool(settings.openai_api_key),
        "key_verified":provider_secret_verified("openai", "OPENAI_API_KEY"),
        "fixture_mode":settings.openai_fixture_mode,
        "model":settings.openai_model,
        "model_locked":True,
        "reasoning_effort":"max",
        "stream":settings.openai_stream,
        "store_responses":settings.openai_store_responses,
        "statistical_engine":{"available":False,"reason":"통계·백테스트 엔진 미연결"},
        "providers":await feature_provider_hub.health(),
    }


@app.post("/api/ai/analyze")
async def start_ai_analysis(payload:AiAnalysisRequest)->dict[str,Any]:
    if payload.symbol not in STOCK_BY_SYMBOL:raise HTTPException(404,"지원 종목이 아닙니다.")
    symbol=payload.symbol
    try:
        frame=await asyncio.to_thread(provider.current_frame,symbol)
        if frame.empty:
            frame,_,_=await asyncio.to_thread(provider.get_bars,symbol)
        daily,_,_=await asyncio.to_thread(provider.get_chart,symbol,"3mo")
        tick=await asyncio.to_thread(provider.get_tick,symbol)
        snap=await asyncio.to_thread(build_snapshot,symbol,frame,tick)
        trades_payload=await asyncio.to_thread(db.list_trades,symbol,None,300)
        portfolio_payload=await asyncio.to_thread(db.portfolio_summary,settings.seed_money)
        analysis_input=analysis_input_builder.build(
            analysis_type=payload.analysis_type,
            symbol=symbol,
            question=payload.question,
            frame=frame,
            daily_frame=daily,
            snapshot=snap,
            tick=tick,
            trades=trades_payload,
            portfolio=portfolio_payload,
            trade_id=payload.trade_id,
        )
        return {"job_id":ai_jobs.start(analysis_input),"status":"QUEUED","model":settings.openai_model}
    except Exception as exc:
        logger.warning("GPT 분석 입력 구성 실패: %s",exc)
        raise HTTPException(500,f"분석 입력 구성 실패: {type(exc).__name__}") from exc


@app.get("/api/ai/jobs/{job_id}")
def get_ai_job(job_id:str)->dict[str,Any]:
    job=ai_jobs.get(job_id)
    if not job:raise HTTPException(404,"분석 작업을 찾을 수 없습니다.")
    return job


@app.delete("/api/ai/jobs/{job_id}")
def cancel_ai_job(job_id:str)->dict[str,Any]:
    if not ai_jobs.cancel(job_id):raise HTTPException(409,"이미 완료되었거나 존재하지 않는 분석입니다.")
    return {"ok":True,"status":"CANCELLING"}


@app.get("/api/ai/analyses")
def list_ai_analyses(symbol:str|None=None,limit:int=Query(default=30,ge=1,le=100))->list[dict[str,Any]]:
    rows=db.list_ai_analyses(symbol,limit)
    return [{
        "id":row["id"],"request_type":row["request_type"],"symbol":row["symbol"],
        "model":row["model"],"requested_at":row["requested_at"],"completed_at":row["completed_at"],
        "expires_at":row["expires_at"],"market_regime":row["market_regime"],
        "overall_action":row["overall_action"],"confidence":row["confidence"],
        "data_quality_score":row["data_quality_score"],"human_summary":row["human_summary"],
        "is_saved":row["is_saved"],"error":row["error"],
    } for row in rows]


@app.get("/api/ai/analyses/{analysis_id}")
def get_ai_analysis(analysis_id:str)->dict[str,Any]:
    row=db.get_ai_analysis(analysis_id)
    if not row:raise HTTPException(404,"분석 기록을 찾을 수 없습니다.")
    return {"record":{
        "id":row["id"],"request_type":row["request_type"],"symbol":row["symbol"],
        "model":row["model"],"requested_at":row["requested_at"],"completed_at":row["completed_at"],
        "expires_at":row["expires_at"],"is_saved":row["is_saved"],
    },"analysis":row["response_json"]}


@app.post("/api/ai/playbook/{analysis_id}")
def create_semi_auto_playbook(analysis_id:str)->dict[str,Any]:
    row=db.get_ai_analysis(analysis_id)
    if not row or not row.get("response_json"):
        raise HTTPException(404,"분석 기록을 찾을 수 없습니다.")
    symbol=row["symbol"]
    snap=build_snapshot(symbol)
    return build_semi_auto_playbook(row,snap,db.portfolio_summary(settings.seed_money))


@app.post("/api/ai/analyses/{analysis_id}/save")
def save_ai_analysis(analysis_id:str)->dict[str,Any]:
    if not db.mark_ai_analysis_saved(analysis_id):raise HTTPException(404,"분석 기록을 찾을 수 없습니다.")
    return {"ok":True,"analysis_id":analysis_id}


@app.get("/api/ai/feature-layout/{symbol}")
def get_feature_layout(symbol:str)->dict[str,Any]:
    if symbol not in STOCK_BY_SYMBOL:raise HTTPException(404,"지원 종목이 아닙니다.")
    return db.get_feature_layout(symbol)


@app.post("/api/ai/feature-layout")
def save_feature_layout(payload:FeatureLayoutUpdate)->dict[str,Any]:
    if payload.symbol not in STOCK_BY_SYMBOL:raise HTTPException(404,"지원 종목이 아닙니다.")
    valid=feature_registry.valid_ids(snapshot_cache.get(payload.symbol,{}).get("features",[]))
    cleaned=payload.model_dump()
    for key in ("pinned","excluded","ai_order"):
        cleaned[key]=[feature_id for feature_id in dict.fromkeys(cleaned[key]) if feature_id in valid]
    return db.save_feature_layout(cleaned)


@app.get("/api/ai/alerts/{symbol}")
def get_ai_alerts(symbol:str)->list[dict[str,Any]]:
    if symbol not in STOCK_BY_SYMBOL:raise HTTPException(404,"지원 종목이 아닙니다.")
    db.expire_ai_alerts(datetime.now(ZoneInfo("UTC")).isoformat())
    return db.list_ai_alerts(symbol)


@app.post("/api/ai/alerts/apply")
def apply_ai_alerts(payload:ApplyAlertsRequest)->dict[str,Any]:
    try:
        alerts=alert_evaluator.apply_from_analysis(payload.analysis_id,payload.symbol)
        return {"ok":True,"alerts":alerts}
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc


@app.delete("/api/ai/alerts/{symbol}")
def clear_ai_alerts(symbol:str)->dict[str,Any]:
    if symbol not in STOCK_BY_SYMBOL:raise HTTPException(404,"지원 종목이 아닙니다.")
    return {"ok":True,"cleared":db.clear_ai_alerts(symbol)}


@app.get("/api/trades")
def trades(symbol:str|None=None,status:str|None=None)->list[dict[str,Any]]:return db.list_trades(symbol=symbol,status=status)


@app.get("/api/portfolio")
def portfolio()->dict[str,Any]:return db.portfolio_summary(settings.seed_money)


@app.post("/api/settings")
def update_settings(payload:SettingsUpdate)->dict[str,Any]:
    changes={k:v for k,v in payload.model_dump().items() if v is not None}
    if "data_mode" in changes:
        requested=str(changes["data_mode"]).lower()
        if requested=="kis" and not settings.kis_configured:raise HTTPException(400,"KIS_APP_KEY와 KIS_APP_SECRET을 .env에 먼저 입력해야 합니다.")
        if requested not in {"public","demo","kis"}:raise HTTPException(400,"data_mode는 public, demo 또는 kis만 가능합니다.")
    save_runtime_settings(settings,changes)
    return {"ok":True,"settings":changes,"restart_required":"data_mode" in changes}


@app.post("/api/trades/open")
def open_trade(payload:OpenTradeRequest)->dict[str,Any]:
    if payload.symbol not in STOCK_BY_SYMBOL:raise HTTPException(404,"지원 종목이 아닙니다.")
    summary=db.portfolio_summary(settings.seed_money); costs=entry_cost(payload.entry_price,payload.quantity,settings)
    try:validate_quantity(payload.quantity,costs["total"],summary["cash"])
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    features=payload.entry_features or snapshot_cache.get(payload.symbol,{}).get("flat",{})
    trade=db.create_trade({"symbol":payload.symbol,"symbol_name":STOCK_BY_SYMBOL[payload.symbol]["name"],"mode":payload.mode,
        "side":payload.side,
        "entry_time":payload.entry_time,"entry_price":payload.entry_price,"entry_quantity":payload.quantity,
        "entry_amount":costs["gross"],"entry_fee":costs["fee"],"entry_features":features})
    return {"trade":trade,"portfolio":db.portfolio_summary(settings.seed_money)}


@app.post("/api/trades/{trade_id}/close")
def close_trade(trade_id:int,payload:CloseTradeRequest)->dict[str,Any]:
    trade=db.get_trade(trade_id)
    if not trade:raise HTTPException(404,"거래를 찾을 수 없습니다.")
    if trade["status"]!="OPEN":raise HTTPException(400,"이미 종료된 거래입니다.")
    if pd.Timestamp(payload.exit_time)<=pd.Timestamp(trade["entry_time"]):raise HTTPException(400,"청산 시점은 진입 시점보다 뒤여야 합니다.")
    qty=payload.quantity or int(trade["entry_quantity"])
    if qty!=int(trade["entry_quantity"]):raise HTTPException(400,"현재 버전은 전량 청산만 지원합니다.")
    features=payload.exit_features or snapshot_cache.get(trade["symbol"],{}).get("flat",{})
    result=exit_result(float(trade["entry_price"]),payload.exit_price,qty,settings,float(trade["entry_fee"]),trade.get("entry_features") or {},features,trade.get("side") or "LONG")
    closed=db.close_trade(trade_id,{"exit_time":payload.exit_time,"exit_price":payload.exit_price,"exit_quantity":qty,"exit_features":features,**result})
    return {"trade":closed,"analysis":result["analysis"],"portfolio":db.portfolio_summary(settings.seed_money)}


@app.post("/api/trades/historical")
def historical_trade(payload:HistoricalTradeRequest)->dict[str,Any]:
    if payload.symbol not in STOCK_BY_SYMBOL:raise HTTPException(404,"지원 종목이 아닙니다.")
    if pd.Timestamp(payload.exit_time)<=pd.Timestamp(payload.entry_time):raise HTTPException(400,"매도 시점은 매수 시점보다 뒤여야 합니다.")
    costs=entry_cost(payload.entry_price,payload.quantity,settings)
    try:validate_quantity(payload.quantity,costs["total"],max(db.portfolio_summary(settings.seed_money)["cash"],settings.seed_money))
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    result=exit_result(payload.entry_price,payload.exit_price,payload.quantity,settings,costs["fee"],payload.entry_features,payload.exit_features,payload.side)
    trade=db.create_historical_trade({"symbol":payload.symbol,"symbol_name":STOCK_BY_SYMBOL[payload.symbol]["name"],
        "side":payload.side,
        "entry_time":payload.entry_time,"entry_price":payload.entry_price,"entry_quantity":payload.quantity,
        "entry_amount":costs["gross"],"entry_fee":costs["fee"],"entry_features":payload.entry_features,
        "exit_time":payload.exit_time,"exit_price":payload.exit_price,"exit_quantity":payload.quantity,
        "exit_features":payload.exit_features,**result})
    return {"trade":trade,"analysis":result["analysis"],"portfolio":db.portfolio_summary(settings.seed_money)}


@app.get("/api/trades/{trade_id}/analysis")
def trade_analysis(trade_id:int)->dict[str,Any]:
    trade=db.get_trade(trade_id)
    if not trade:raise HTTPException(404,"거래를 찾을 수 없습니다.")
    if trade["status"]=="CLOSED":
        result=analyze_trade(trade.get("entry_features") or {},trade.get("exit_features") or {},float(trade.get("net_pnl") or 0),float(trade.get("return_pct") or 0),trade.get("side") or "LONG")
        result["side"]=trade.get("side") or "LONG"
        return result
    current=snapshot_cache.get(trade["symbol"],{}).get("flat",{});price=float(current.get("price") or trade["entry_price"])
    result=exit_result(float(trade["entry_price"]),price,int(trade["entry_quantity"]),settings,float(trade["entry_fee"]),trade.get("entry_features") or {},current,trade.get("side") or "LONG")
    result["analysis"]["result"]="진행 중";return result["analysis"]


@app.delete("/api/trades/{trade_id}")
def delete_trade(trade_id:int)->dict[str,Any]:return {"ok":db.delete_trade(trade_id),"portfolio":db.portfolio_summary(settings.seed_money)}


@app.websocket("/ws/market/{symbol}")
async def market_ws(websocket:WebSocket,symbol:str)->None:
    await websocket.accept()
    if symbol not in STOCK_BY_SYMBOL:
        await websocket.send_json({"error":"지원 종목이 아닙니다."});await websocket.close(code=1008);return
    counter=0
    try:
        while True:
            tick=await asyncio.to_thread(provider.get_tick,symbol);payload={"type":"tick","tick":tick};counter+=1
            # The tick itself is streamed at REFRESH_MS; recompute expensive
            # derived features every five seconds so all panels stay in sync
            # without over-polling KIS REST endpoints.
            if counter%max(1,int(5000/max(settings.refresh_ms,250)))==0:
                frame=provider.current_frame(symbol)
                if not frame.empty:payload["snapshot"]=await asyncio.to_thread(build_snapshot,symbol,frame,tick)
            await websocket.send_text(json.dumps(payload,ensure_ascii=False));await asyncio.sleep(max(settings.refresh_ms,250)/1000)
    except WebSocketDisconnect:return
    except Exception as exc:
        logger.exception("브라우저 WebSocket 오류: %s",exc)
        try:await websocket.send_json({"error":str(exc)})
        except Exception:pass


if __name__=="__main__":uvicorn.run("app:app",host="127.0.0.1",port=8767,reload=False,log_level="info")
