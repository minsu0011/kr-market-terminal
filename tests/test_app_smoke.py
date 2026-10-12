import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from market_terminal.config import Settings
from market_terminal.database import Database
from market_terminal.news_pipeline import NewsPipelineService


ROOT = Path(__file__).resolve().parents[1]


def test_demo_news_uses_only_local_providers(tmp_path):
    service = NewsPipelineService(
        Settings(data_mode="demo", news_fixture_mode=True),
        Database(tmp_path / "news.sqlite3"),
        tmp_path,
        ROOT,
    )
    assert {p.provider_id for p in service.providers} == {"manual_news_json", "fixture_news"}
    result = asyncio.run(service.refresh(["005930"], force=True))
    assert result["article_count"] > 0


def test_demo_api_and_chart_assets():
    from app import app

    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "/static/plotly.min.js" in page.text
        plotly = client.get("/static/plotly.min.js")
        assert plotly.status_code == 200
        assert "plotly.js v3.3.1" in plotly.text[:150]
        response = client.get("/api/bootstrap?symbol=005930&chart_range=1d")
        assert response.status_code == 200
        data = response.json()
        assert data["settings"]["data_mode"] == "demo"
        assert data["bars"]
        assert data["snapshot"]["flat"]["price"] > 0
        assert all(bar["close"] > 0 for bar in data["bars"])
        assert client.get("/api/chart/000000").status_code == 404
