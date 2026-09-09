from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


AnalysisType = Literal["MARKET", "SYMBOL", "POSITION"]
Direction = Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "CONFLICTED", "UNKNOWN"]
ThresholdBasis = Literal[
    "CURRENT_SESSION_DISTRIBUTION",
    "ROLLING_RECENT_DISTRIBUTION",
    "FIXED_DOMAIN_RULE",
    "USER_DEFINED",
    "AI_HEURISTIC",
    "STATISTICAL_ENGINE",
    "UNAVAILABLE",
]
Operator = Literal["GT", "GTE", "LT", "LTE", "BETWEEN", "NONE"]


class AiAnalysisRequest(StrictModel):
    analysis_type: AnalysisType = "SYMBOL"
    symbol: str = Field(pattern=r"^\d{6}$")
    # The browser embeds the complete feature inventory in the visible prompt so
    # the request remains self-contained instead of depending on screen state.
    question: str = Field(default="", max_length=50000)
    trade_id: int | None = Field(default=None, ge=1)


class Threshold(StrictModel):
    operator: Operator
    value1: float | None
    value2: float | None
    basis: ThresholdBasis
    confidence: float = Field(ge=0, le=1)
    explanation: str


class ThresholdSet(StrictModel):
    positive: Threshold
    caution: Threshold
    danger: Threshold
    invalidation: Threshold


class TopFeature(StrictModel):
    feature_id: str
    display_name: str
    group: str
    rank: int = Field(ge=1, le=12)
    importance: int = Field(ge=0, le=100)
    current_value: float | str | bool | None
    current_display: str
    direction_now: Direction
    reason: str
    supporting_data: list[str]
    thresholds: ThresholdSet


class RegimeState(StrictModel):
    label: str
    confidence: float = Field(ge=0, le=1)
    summary: str


class OverallAction(StrictModel):
    code: Literal[
        "STRONG_BUY_REVIEW",
        "BUY_REVIEW",
        "HOLD",
        "WAIT",
        "REDUCE_REVIEW",
        "SELL_REVIEW",
        "NO_TRADE",
    ]
    korean_label: str
    confidence: float = Field(ge=0, le=1)
    reason: str


class DataQuality(StrictModel):
    score: int = Field(ge=0, le=100)
    stale_sources: list[str]
    missing_sources: list[str]
    warnings: list[str]


class HistoricalStatistics(StrictModel):
    available: bool
    sample_count: int | None
    win_rate: float | None
    expected_return_5m: float | None
    expected_return_15m: float | None
    expected_return_30m: float | None
    average_net_return: float | None
    lower_decile_return: float | None
    message: str


class AlertProposal(StrictModel):
    feature_id: str
    operator: Operator
    threshold: float | None
    second_threshold: float | None
    severity: Literal["POSITIVE", "CAUTION", "DANGER", "INVALIDATION"]
    basis: ThresholdBasis
    explanation: str


class FeatureTradeTrigger(StrictModel):
    feature_id: str
    display_name: str
    current_display: str
    buy_operator: Operator
    buy_threshold: float | None
    buy_threshold2: float | None
    threshold_basis: ThresholdBasis
    required_move: str
    explanation: str


class ConditionalTradePlan(StrictModel):
    applicable: bool
    data_mode: str
    automatic_order: bool
    plan_label: str
    side: Literal["LONG", "SHORT", "NONE"]
    reference_price_krw: float | None
    entry_method: Literal[
        "NO_ENTRY",
        "LIMIT_PULLBACK",
        "LIMIT_BREAKOUT_CONFIRM",
        "MARKETABLE_LIMIT_REVIEW",
    ]
    entry_price_low_krw: float | None
    entry_price_high_krw: float | None
    entry_instruction: str
    stop_loss_price_krw: float | None
    stop_loss_pct: float | None
    take_profit_1_price_krw: float | None
    take_profit_1_pct: float | None
    take_profit_2_price_krw: float | None
    take_profit_2_pct: float | None
    trailing_stop_pct: float | None
    feature_triggers: list[FeatureTradeTrigger] = Field(max_length=8)
    no_trade_conditions: list[str]
    risk_notice: str


class TradeDebrief(StrictModel):
    applicable: bool
    trade_id: int | None
    outcome: Literal["WIN", "LOSS", "BREAKEVEN", "OPEN", "NOT_REQUESTED"]
    execution_summary: str
    what_worked: list[str]
    what_failed: list[str]
    entry_assessment: str
    exit_assessment: str
    next_time_checklist: list[str]


class AiAnalysisResponse(StrictModel):
    analysis_id: str
    created_at: str
    expires_at: str
    market_regime: RegimeState
    symbol_state: RegimeState
    overall_action: OverallAction
    data_quality: DataQuality
    historical_statistics: HistoricalStatistics
    top_features: list[TopFeature] = Field(max_length=12)
    buy_review_conditions: list[str]
    hold_conditions: list[str]
    caution_conditions: list[str]
    reduce_review_conditions: list[str]
    sell_review_conditions: list[str]
    invalidation_conditions: list[str]
    feature_order: list[str]
    alerts_to_register: list[AlertProposal]
    conditional_trade_plan: ConditionalTradePlan
    trade_debrief: TradeDebrief
    limitations: list[str]
    human_readable_summary: str


class FeatureLayoutUpdate(StrictModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    pinned: list[str] = Field(default_factory=list, max_length=300)
    excluded: list[str] = Field(default_factory=list, max_length=300)
    ai_order: list[str] = Field(default_factory=list, max_length=300)
    mode: Literal["default", "ai", "importance", "risk"] = "default"


class ApplyAlertsRequest(StrictModel):
    analysis_id: str
    symbol: str = Field(pattern=r"^\d{6}$")


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return the Pydantic schema in the strict form required by Responses."""
    schema = model.model_json_schema()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                if "properties" in node:
                    node["required"] = list(node["properties"])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema
