# 테스트

~~~powershell
python -m pytest -q
~~~

2026-10-13 Windows / Python 3.13.9에서 54개 테스트를 실행해 모두 통과했습니다. FastAPI/Starlette TestClient의 httpx 사용에 대한 deprecation 경고 1건이 있었습니다.

기존 규칙 테스트에 데모 뉴스의 로컬 공급자 제한, HTML/Plotly 자산, bootstrap 분봉, 잘못된 종목의 404를 확인하는 테스트를 추가했습니다. requests의 외부 HTTP는 테스트 fixture에서 차단하고, 분석은 합성 응답 또는 mock으로 처리합니다.

검사한 항목:

- 뉴스 중복·원출처·후보 gate와 아직 없는 horizon 처리
- 구조화된 설명 응답·피처 ID·허위 통계·알림·가상거래 경계
- KIS 토큰 동시 요청·backoff·부분 페이지 보존
- Windows DPAPI·현재 키 fingerprint 검증
- LONG/SHORT 손익과 DB migration
- UI 구조·색상·포터블 실행 경로·로컬 API와 Plotly 제공

실제 API 인증, 공급된 시세의 정확도, 외부 모델의 제공 여부, 브라우저 렌더링·매매 성과, EXE 빌드 성공은 이 테스트로 입증하지 않습니다. CI는 Windows·Linux용으로 추가했으며 원격 실행 결과는 Actions에서 확인합니다.
