# 데이터 흐름

app.py가 공급자, 피처 계산, 뉴스 서비스, 분석 작업과 DB를 조립합니다.

| 단계 | 입력 → 결과 | 코드 |
|---|---|---|
| 시장 조회 | symbol/range → tick·OHLCV·호가·회원사 | providers.py, public_data.py |
| 재무·거시 보강 | 종목/피처 ID → 최신 값·출처 | feature_providers.py |
| 기술 계산 | OHLCV → 추세·거래량·변동성 | feature_engine.py, trader_features.py |
| 뉴스 | 기사 → 종목·사건·원출처·검토 상태 | news_pipeline.py |
| 스냅샷 | 값·상태 → 표준 피처/flat | feature_registry.py, app.py |
| 저장 | 스냅샷·뉴스·가상거래 → SQLite | database.py |
| 전달 | REST JSON / WebSocket → 화면 state | app.py, static/app.js |
| 차트 | 정렬된 OHLCV → 리샘플 → Plotly | static/app.js |

가격은 provider의 메모리 프레임에서 읽습니다. DB에 모든 봉을 저장했다가 차트로 조회하는 구조가 아닙니다. 외부 피처도 현재 종목의 최신 값 병합이며, 일반적인 과거 as-of 테이블은 없습니다.

데모에서는 합성 MarketProvider와 수동/fixture 뉴스만 사용합니다. public에서는 공개 원천을 조회하고, 검증된 보강 키가 있을 때 추가 공급자를 사용합니다. 외부 설명 API는 별도 작업으로 호출하며 시세 원천을 대신하지 않습니다.

[API·컴포넌트](wiki/Architecture.md) · [한계](limitations.md)
