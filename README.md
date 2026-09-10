# KR Market Terminal

국내 주식의 시장 데이터, 뉴스, 분석 feature와 가상매매 기록을 한 화면에서 살펴보는 로컬 웹 터미널이다. 여러 공급자의 값과 분석 설명을 연결하면서, **값이 없는 상태·오래된 값·실제 관측값을 구분해 보여주는 구조**를 만들었다. 단일 모델 정확도보다 데이터가 UI와 의사결정까지 이어지는 흐름에 초점을 뒀다.

## 전체 구조

시장·뉴스 provider → feature registry·계산 엔진 → 분석 입력 구성 → AI 분석 작업 → 경보·가상매매 → API·WebSocket·UI

- [app.py](app.py): FastAPI 라우트와 서비스 조립
- [market_terminal](market_terminal): provider, 뉴스, feature, 분석·매매 모듈
- [config](config): feature 정의와 설정
- [static](static): 웹 화면
- [tests](tests): 뉴스와 분석 경계 테스트

Python, FastAPI, Uvicorn, Pydantic, pandas·NumPy, SQLite, WebSocket, JavaScript를 사용한다. 외부 LLM API는 분석 설명을 만드는 선택적 구성 요소다.

## 데이터와 컴포넌트

MarketProvider가 시장 값을 공급하고 NewsPipelineService가 뉴스 이벤트를 다룬다. OpenDART·ECOS·CSV·수동 입력 등은 ProviderHub를 통해 feature 입력에 연결된다. 공급자 값이 없을 때 사용할 수 없음 상태를 명시한다.

FeatureRegistry는 feature의 의미와 사용 가능 여부를 관리하고, AnalysisInputBuilder는 분석에 넘길 입력을 구성한다. AiJobManager는 분석 작업을 관리하며 AlertEvaluator는 등록된 조건을 평가한다. SQLite에는 분석·경보·가상 거래 상태를 저장한다.

## 개발 과정

1. **차트와 시장 조회에서 시작했다.** 가격 한 종류만으로 판단하기 어려워 재무·거시·수동 입력을 붙일 수 있는 provider 경계를 뒀다.
2. **feature의 의미를 중앙에서 관리했다.** 각 화면과 분석기가 서로 다른 이름·단위로 같은 값을 해석하지 않도록 registry와 계산 엔진을 분리했다.
3. **뉴스 수집과 분석 호출을 나눴다.** 같은 뉴스가 반복해서 들어오는 문제를 다루고, 뉴스 상태와 분석 job 상태를 구분했다.
4. **설명 모델에 전달할 입력을 제한했다.** LLM이 없는 값을 만들어 채우는 대신 가용한 feature와 상태를 분석 입력에 반영하는 구조를 사용한다.
5. **외부 호출과 화면 갱신을 분리했다.** cache·lock·background task와 WebSocket을 사용해 데이터 요청, 분석, UI 전송의 책임을 나눴다.
6. **가상매매와 자격증명 저장을 별도 경계로 뒀다.** 수량·진입 비용·청산 결과를 계산하는 모듈과 Windows DPAPI 기반 secret store를 보존했다. 분석 설명을 실거래 체결 보장으로 해석하지 않는다.

## 모델의 역할과 결과

외부 LLM은 관측된 feature를 바탕으로 설명과 구조화된 분석을 만든다. market provider나 feature engine을 대신하는 가격 예측기가 아니다. 뉴스 경보·분석·가상매매를 연결한 소프트웨어 구현이 주된 결과이며 투자 수익이나 방향 정확도의 인증을 주장하지 않는다.

데이터 권한·지연·분봉 coverage, 외부 API 비용과 실패, 설명의 오류 가능성이 남아 있다. 외부 모델 자체가 아니라 데이터·작업 상태·API/UI 연결이 이 프로젝트에서 구현한 범위다.

## 실행

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

브라우저에서 `http://127.0.0.1:8000`으로 접속한다. 서비스 시작 시 로컬 DB가 생성될 수 있고 설정에 따라 provider 호출이 발생한다. 먼저 [데이터·설정 안내](data/README.md)를 확인한다. API key를 코드나 저장소에 넣지 않는다.

## 상세 문서

[서비스 안내](docs/wiki/Home.md) · [구조](docs/wiki/Architecture.md) · [개발 과정·병목](docs/wiki/Development-Journey.md) · [분석 모델의 경계](docs/wiki/Model-Evolution.md) · [확인 범위와 한계](docs/wiki/Validation-and-Results.md) · [출처](ATTRIBUTION.md)
