from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_korean_market_colors() -> None:
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert "--red:#ff4d5a" in css
    assert "--blue:#4388ff" in css
    assert "--orange:#ffad42" in css
    assert "--unknown:#68788a" in css


def test_ai_panel_is_inside_feature_tab_right_half() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    feature_start = html.index('id="tab-features"')
    panel_start = html.index('id="ai-panel"')
    feature_end = html.index('id="tab-positions"')
    assert feature_start < panel_start < feature_end
    assert "grid-template-columns:1fr 1fr" in css


def test_3440_layout_has_no_page_horizontal_scroll_rule() -> None:
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert ".pane{min-width:0" in css
    assert ".ai-panel{min-width:0" in css
    assert ".feature-ai-split" in css


def test_sort_pin_alert_controls_exist() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for element_id in ("feature-sort", "ai-apply-order", "ai-restore-order", "ai-apply-alerts", "ai-clear-alerts"):
        assert f'id="{element_id}"' in html
    assert "data-pin" in js
    assert "data-exclude" in js


def test_gpt_trigger_plan_and_virtual_only_controls_exist() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for element_id in (
        "ai-live-context",
        "ai-auto-prompt",
        "ai-send-prompt",
        "ai-virtual-auto",
        "institution-priority-strip",
        "trade-side",
        "summary-closed-count",
    ):
        assert f'id="{element_id}"' in html
    assert "conditional_trade_plan" in js
    assert "syncAnalysisControls" in js
    assert "virtual-auto-gpt" in js
    assert "analysis-gpt-debrief" in js
    assert "startTradeAiDebrief" in js
    assert "data-trade-ai" in js
    assert "저장된 진입→청산 핵심 피처" in js
    assert "[분석 대상 회사]" in js
    assert "[수집 피처 전체 목록]" in js
    assert "분석 기준시각" in js
    assert "KST (UTC+09:00)" in js
    assert "전체 피처다" in js
    assert "현재 화면 기준으로" not in js
    assert 'maxlength="50000"' in html
    assert "가상거래 전용 · 실주문 없음" in html


def test_naver_news_auto_analysis_and_cluster_counts_are_visible() -> None:
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "renderInstitutionPriority" in js
    assert "독립 원출처" in js
    assert "사건 클러스터" in js
    assert "원시 수집 ${fmt(v.article_count)}건은 감사용 참고값" in js
    queue = app[app.index("async def queue_news_gpt_analysis"):app.index("async def news_refresh_loop")]
    assert "independent_source_count" in queue
    assert "primary_source_count" in queue
    assert "latest.get('last_updated_at')" not in queue


def test_portable_launcher_is_pinned_to_8767_with_fresh_assets() -> None:
    launcher = (ROOT / "launcher.py").read_text(encoding="utf-8")
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    package_root = ROOT.parent
    run_bat = (package_root / "실행_프로그램.bat").read_text(encoding="utf-8")
    assert 'TERMINAL_PORT", "8767"' in launcher
    assert "public-gpt5-8767" in html
    assert "http://127.0.0.1:8767/" in run_bat
    assert "Google/Chrome/Application/chrome.exe" in launcher
    assert 'KR_DayTrading_Terminal.exe' in run_bat


def test_news_ui_separates_four_confirmation_pillars() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for element_id in ("tab-news", "tab-candidates", "tab-attention", "news-list", "news-detail"):
        assert f'id="{element_id}"' in html
    for label in (
        "1. 뉴스 사실 검증",
        "2. 뉴스 중요도와 방향",
        "3. 가격·거래량 반응",
        "4. 외국인·기관·회원사 수급 확인",
    ):
        assert label in js
    assert "현재 뉴스·수급·가격 확인 점수" in js
    assert "기관 투자자와 증권사 회원사 창구는 별개" in js


def test_news_candidate_ui_does_not_render_unavailable_statistics() -> None:
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    candidate_renderer = js[js.index("function renderCandidateCard"):js.index("function renderNewsDetail")]
    for forbidden in ("승률", "기대수익", "하위 손실", "표본 수"):
        assert forbidden not in candidate_renderer
    assert "통계·백테스트 수치 아님" in candidate_renderer


def test_news_features_are_promoted_but_user_pins_stay_first() -> None:
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    sorter = js[js.index("function featureSortScore"):js.index("function renderFeatures")]
    assert sorter.index("if(pin>=0)") < sorter.index("newsRank")
    assert "return-80000+newsRank" in sorter
