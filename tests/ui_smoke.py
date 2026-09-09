from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
TRADER_SCREENSHOT = ROOT / "artifacts" / "TRADER_FEATURES_SCREEN_3440x1440.png"
NEWS_SCREENSHOT = ROOT / "artifacts" / "NEWS_VERIFICATION_SCREEN_3440x1440.png"
BASE_URL = os.getenv("TERMINAL_TEST_URL", "http://127.0.0.1:8766")


def main() -> None:
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        )
        page = browser.new_page(viewport={"width": 3440, "height": 1440})
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
        if page.locator("#api-setup-modal").is_visible():
            page.click("#api-setup-skip")
        page.locator("#feature-groups .feature-row").first.wait_for(timeout=30_000)
        page.fill("#ai-question", "야간선물과 동종주 동반상승을 포함해 지금 주목할 피처를 알려줘.")
        page.click('[data-ai-type="SYMBOL"]')
        page.wait_for_timeout(5_000)
        state = page.locator("#ai-state").inner_text()
        status = page.locator("#ai-status").inner_text()
        if state in {"완료", "캐시"}:
            page.click("#ai-apply-order")
            page.click("#ai-apply-alerts")
            page.click("#ai-prepare-response")
            page.wait_for_timeout(900)
        metrics = page.evaluate("""() => ({
            width: innerWidth,
            docWidth: document.documentElement.scrollWidth,
            bodyWidth: document.body.scrollWidth,
            panel: document.querySelector('#ai-panel').getBoundingClientRect().toJSON(),
            features: document.querySelector('#feature-groups').getBoundingClientRect().toJSON(),
            answer: document.querySelector('#ai-answer').innerText.slice(0, 500),
            rows: document.querySelectorAll('.feature-row').length,
            watchCards: document.querySelectorAll('#market-watch-strip > div').length,
            watchText: document.querySelector('#market-watch-strip').innerText,
            featureText: document.querySelector('#feature-groups').innerText,
            playbook: document.querySelector('#semi-auto-playbook').innerText
        })""")
        TRADER_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(TRADER_SCREENSHOT), full_page=False)
        page.click('[data-tab="news"]')
        page.locator("#news-list .news-row").first.wait_for(timeout=30_000)
        news_metrics = page.evaluate("""() => ({
            clusters: document.querySelectorAll('#news-list .news-row').length,
            detail: document.querySelector('#news-detail').innerText,
            providers: document.querySelectorAll('#news-provider-line .provider-pill').length,
            candidateLabel: document.querySelector('#candidate-strip-items').innerText
        })""")
        page.screenshot(path=str(NEWS_SCREENSHOT), full_page=False)
        browser.close()
    assert state in {"완료", "캐시"}, (state, status)
    assert metrics["docWidth"] <= metrics["width"]
    assert metrics["bodyWidth"] <= metrics["width"]
    assert abs(metrics["panel"]["width"] - metrics["features"]["width"]) <= 2
    assert metrics["answer"]
    assert metrics["watchCards"] == 4
    assert "KOSPI" in metrics["watchText"] and "야간선물" in metrics["watchText"]
    assert "주요 시장·야간선물" in metrics["featureText"]
    assert "동종주·테마 동반 움직임" in metrics["featureText"]
    assert "자동주문 없음" in metrics["playbook"]
    assert news_metrics["clusters"] >= 3
    assert news_metrics["providers"] >= 3
    assert "뉴스 사실 검증" in news_metrics["detail"]
    assert "가격·거래량 반응" in news_metrics["detail"]
    assert "외국인·기관·회원사 수급 확인" in news_metrics["detail"]
    assert not errors, errors
    print(json.dumps({"state": state, "status": status, "metrics": metrics, "news": news_metrics, "errors": errors}, ensure_ascii=True))


if __name__ == "__main__":
    main()
