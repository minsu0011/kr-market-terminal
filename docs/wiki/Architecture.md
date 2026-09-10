# 서비스 조립 구조

`app.py`가 Settings, Database, MarketProvider, FeatureRegistry, AnalysisInputBuilder, AiAnalysisService·AiJobManager, AlertEvaluator와 NewsPipelineService를 조립한다. FastAPI HTTP 라우트와 WebSocket은 이 서비스의 결과를 UI에 전달한다.

market data와 뉴스 수집은 외부 입출력이다. feature engine은 관측값을 계산하고 registry는 정의·가용성을 관리한다. AI 분석은 그 뒤의 별도 작업이며 SQLite는 분석·경보·가상 거래의 상태를 보관한다.

[market_terminal](../../market_terminal)에서 provider, news_pipeline, feature_engine, feature_registry, ai_analysis, database, trading을 순서대로 볼 수 있다. cache와 lock은 공유 요청·상태 충돌을 다루는 장치이지 데이터 신선도를 자동 보증하지는 않는다.
