# 출처와 이용 범위

## 라이브러리

Python, FastAPI, Uvicorn, Pydantic, pandas, NumPy, requests, websockets, PyYAML, OpenAI SDK를 사용합니다. 화면은 일반 JavaScript와 Plotly입니다. 라이브러리의 구현과 저작권은 각 프로젝트에 있습니다.

static/plotly.min.js는 Plotly.js 3.3.1의 배포 파일입니다. 원래 배너를 유지했고, 라이선스는 third_party/plotly-LICENSE.txt에 포함했습니다.

- Plotly: https://github.com/plotly/plotly.js/tree/v3.3.1
- 해당 배포판 라이선스: https://github.com/plotly/plotly.js/blob/v3.3.1/LICENSE

## 데이터와 외부 모델

시세·뉴스·공시·거시 원천은 네이버, 한국투자증권 KIS, 금융감독원 OpenDART, 한국은행 ECOS, Yahoo Finance, FRED입니다. 연결 코드가 있다는 것이 모든 데이터의 재배포 권한이나 현재 공급 성공을 뜻하지 않습니다. 저장소에 실제 공급 응답·DB·개인 키는 포함하지 않았습니다.

선택적인 외부 LLM은 전달한 지표와 뉴스에 대한 구조화된 설명을 생성합니다. 모델 자체를 구현하거나 학습한 프로젝트가 아니며, 모델 제공 여부·접근 권한·비용을 따로 확인해야 합니다.

examples의 시세·뉴스·분석 응답은 동작 확인용 합성 자료입니다. docs/images도 기존 DEMO 화면이며 금융 성과나 실제 시세의 증거가 아닙니다.

## 코드 이용

기존 라이선스·저작권 고지는 해당 범위에 그대로 적용됩니다. 별도 명시가 없는 코드에 포괄적인 재사용 허가를 추가하지 않습니다. 원천 데이터와 외부 서비스 응답은 각 제공자의 이용 조건을 따릅니다.
