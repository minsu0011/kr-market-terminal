# KR Market Terminal

국내 주식 시세·뉴스·통계 정보를 한 화면에서 확인하는 터미널이다. LLM 뉴스 해석은 실제 애플리케이션 기능이며 모델 학습 이력과 구분한다.

## 구성

Python, numpy, pandas, requests, fastapi. 핵심 경로: `app.py`, `market_terminal`, `static`, `tests`.

provider → feature/news pipeline → analysis → 웹 UI·선택적 trading 연결

## 데이터와 모델

시세·뉴스 API, 계정 설정, 로컬 DB. API key와 뉴스 원문/거래 기록은 포함하지 않는다.

LightGBM, XGBoost

## 실험의 흐름

| 단계 | 변경 | 근거 등급 | 판단 |
| --- | --- | --- | --- |
| 1 | 기본 terminal | SOURCE_REPORTED | RESEARCH_ONLY |
| 2 | 뉴스 정합성 | SOURCE_REPORTED | RESEARCH_ONLY |
| 3 | AI analysis | SOURCE_REPORTED | RESEARCH_ONLY |
| 4 | provider resilience | SOURCE_REPORTED | CURRENT |

## 결과와 한계

실시간 API 권한과 연결 상태에 의존한다. 외부 모델 해석은 투자 판단의 검증된 근거가 아니며 주문 연결은 별도 검토가 필요하다.

연구 결과는 별도 표기가 없으면 SOURCE_REPORTED다. 이번 게시본에서 전체 데이터 수집·학습·수익률 재현을 수행했다는 뜻은 아니다. [검증](docs/validation.md)과 [모델 변화](docs/model-evolution.md)에 범위를 구분했다.

## 실행

```powershell
python -m pytest tests/test_news_pipeline.py -q
```

원천 데이터와 가중치는 포함하지 않는다. 위 명령은 의존성을 준비한 뒤 사용하는 진입점이며 실행 검증 결과는 검증 문서를 따른다. [데이터 준비](data/README.md), [출처](ATTRIBUTION.md).
