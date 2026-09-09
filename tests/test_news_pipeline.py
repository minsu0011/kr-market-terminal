from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from market_terminal.config import Settings
from market_terminal.news_pipeline import (
    CandidateScoringEngine,
    CompanyEntityResolver,
    NewsDeduplicationEngine,
    NewsPipelineService,
    NewsVerificationEngine,
    RawNewsItem,
)


NOW = datetime.now(timezone.utc)


def article(
    article_id: str,
    publisher: str,
    title: str = "삼성전자 2조원 공급계약 체결",
    body: str | None = "삼성전자가 고객사와 2조원 공급계약을 체결했다. 매출 확대가 기대된다.",
    *,
    url: str | None = None,
    original_publisher: str | None = None,
    press_release: bool = False,
    disclosure: bool = False,
    retraction: bool = False,
) -> RawNewsItem:
    domain = publisher.lower().replace(" ", "") + ".example.com"
    return RawNewsItem.from_dict(
        {
            "article_id": article_id,
            "publisher": publisher,
            "publisher_domain": "opendart.fss.or.kr" if disclosure else domain,
            "original_publisher": original_publisher,
            "title": title,
            "summary": body,
            "body": body,
            "url": url or f"https://{domain}/{article_id}",
            "published_at": (NOW - timedelta(minutes=10)).isoformat(),
            "symbols_explicit": ["005930"],
            "company_names_explicit": ["삼성전자"],
            "is_press_release": press_release,
            "is_disclosure": disclosure,
            "is_retraction": retraction,
            "metadata": {"receipt_no": article_id} if disclosure else {},
        },
        "open_dart" if disclosure else "test_news",
    )


def clusters(items: list[RawNewsItem]) -> list[dict]:
    return NewsDeduplicationEngine(CompanyEntityResolver()).cluster(items)


def test_same_url_with_tracking_is_one_raw_article() -> None:
    first = article("a1", "매체A", url="https://news.example.com/a?utm_source=x")
    duplicate = article("a2", "매체A", url="https://news.example.com/a?utm_source=y")
    result = clusters([first, duplicate])
    assert len(result) == 1
    assert result[0]["raw_article_count"] == 1


def test_wire_reposts_count_as_one_independent_source() -> None:
    items = [
        article("w1", "매체A", original_publisher="연합뉴스"),
        article("w2", "매체B", original_publisher="연합뉴스"),
        article("w3", "매체C", original_publisher="연합뉴스"),
    ]
    result = clusters(items)[0]
    assert result["raw_article_count"] == 3
    assert result["independent_source_count"] == 1
    assert result["syndicated_copy_count"] == 2
    assert NewsVerificationEngine.verify(result)["status"] == "SINGLE_SOURCE"


def test_press_release_reposts_are_one_origin_not_three_confirmations() -> None:
    items = [
        article(
            "p1",
            "매체A",
            body="삼성전자는 신규 공급 계약 체결 보도자료를 배포했다. 계약 금액은 2조원이다.",
            press_release=True,
        ),
        article(
            "p2",
            "매체B",
            body="삼성전자가 신규 공급 계약을 체결했다는 보도자료를 냈다. 계약 금액은 2조원이다.",
            press_release=True,
        ),
        article(
            "p3",
            "매체C",
            body="삼성전자 신규 공급 계약 체결 보도자료. 계약 금액 2조원으로 알려졌다.",
            press_release=True,
        ),
    ]
    result = clusters(items)[0]
    assert result["raw_article_count"] == 3
    assert result["independent_source_count"] == 1
    assert result["syndicated_copy_count"] == 2
    assert result["verification_count_basis"] == "INDEPENDENT_ORIGINAL_SOURCES_AND_PRIMARY_SOURCES"
    assert NewsVerificationEngine.verify(result)["status"] == "SINGLE_SOURCE"


def test_two_independently_written_sources_verify_event() -> None:
    items = [
        article("i1", "매체A", body="삼성전자는 신규 고객과 2조원 공급계약을 체결했다. 회사가 공식 확인했다."),
        article("i2", "매체B", body="삼성전자의 2조원 공급계약이 확정됐다. 생산 물량과 매출 증가가 예상된다."),
    ]
    result = clusters(items)[0]
    assert result["independent_source_count"] == 2
    assert NewsVerificationEngine.verify(result)["status"] == "VERIFIED_MULTI_SOURCE"


def test_primary_disclosure_is_verified_without_media_count_inflation() -> None:
    result = clusters([article("202607290001", "OpenDART", disclosure=True)])[0]
    verification = NewsVerificationEngine.verify(result)
    assert verification["primary_source_count"] == 1
    assert verification["independent_source_count"] == 0
    assert verification["status"] == "VERIFIED_PRIMARY"


def test_different_contract_amounts_are_separate_events_when_titles_differ() -> None:
    items = [
        article("c1", "매체A", title="삼성전자 A사와 2조원 반도체 공급계약", body="삼성전자가 A사와 2조원 반도체 공급계약을 체결했다."),
        article("c2", "매체B", title="삼성전자 B사와 5천억원 장비 공급계약", body="삼성전자가 B사와 5000억원 장비 공급계약을 체결했다."),
    ]
    assert len(clusters(items)) == 2


def test_conflicting_amounts_and_retraction_block_verification() -> None:
    conflict = clusters([
        article("x1", "매체A", title="삼성전자 공급계약 체결", body="삼성전자 공급계약 금액은 2조원이다."),
        article("x2", "매체B", title="삼성전자 공급계약 체결", body="삼성전자 공급계약 금액은 3조원이다."),
    ])[0]
    assert NewsVerificationEngine.verify(conflict)["status"] == "CONFLICTED"

    retracted = clusters([
        article("r1", "매체A"),
        article("r2", "매체A", retraction=True),
    ])[0]
    assert NewsVerificationEngine.verify(retracted)["status"] == "RETRACTED"


def score_candidate(*, price: bool | None, volume: bool | None, institution: bool | None, risk_flags: list[str] | None = None) -> dict:
    cluster = {"first_published_at": (NOW - timedelta(minutes=10)).isoformat()}
    verification = {"status": "VERIFIED_PRIMARY"}
    materiality = {"score": 85}
    sentiment = {"sentiment": "POSITIVE"}
    reaction = {"price_confirmation": price, "volume_confirmation": volume, "return_after_news_5m": 1.5}
    flow = {
        "institution_confirmation": institution,
        "foreign_confirmation": None,
        "member_confirmation": None,
        "market_risk_flags": risk_flags or [],
    }
    return CandidateScoringEngine.score(
        cluster, verification, materiality, sentiment, reaction, flow, 90, Settings()
    )


def test_buy_review_requires_two_independent_confirmation_groups() -> None:
    accepted = score_candidate(price=True, volume=False, institution=True)
    rejected = score_candidate(price=True, volume=False, institution=None)
    assert accepted["eligible"] is True
    assert accepted["action"] in {"BUY_REVIEW", "STRONG_BUY_REVIEW"}
    assert rejected["eligible"] is False
    assert rejected["action"] in {"WATCH", "WAIT"}
    assert accepted["score_label"] == "현재 뉴스·수급·가격 확인 점수"
    assert accepted["statistics_available"] is False
    assert accepted["automatic_order"] is False


def test_market_risk_state_blocks_candidate() -> None:
    result = score_candidate(price=True, volume=True, institution=True, risk_flags=["거래정지"])
    assert result["eligible"] is False
    assert any("거래정지" in reason for reason in result["blocking_reasons"])


def test_reaction_engine_never_reads_future_horizon() -> None:
    base = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    frame = pd.DataFrame([
        {"time": (base + timedelta(minutes=i)).isoformat(), "open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i, "volume": 100}
        for i in range(6)
    ])
    event = (base + timedelta(minutes=3)).isoformat()
    result = NewsPipelineService._reaction(frame, event)
    assert result["return_after_news_1m"] is not None
    assert result["return_after_news_3m"] is None
    assert result["return_after_news_5m"] is None
    assert result["future_data_used"] is False
