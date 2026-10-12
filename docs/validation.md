# 검증 범위

2026-10-13의 검증은 소프트웨어 실행·배포 경로와 안전한 저장소 구성을 대상으로 했습니다.

- pytest 54개 통과: 외부 HTTP 차단, fixture·mock·임시 DB 사용
- 로컬 ASGI 테스트: HTML과 번들 Plotly, DEMO bootstrap, 분봉 응답, 잘못된 종목 처리
- Python 구문과 JavaScript 구문 검사
- 저장소 자산·문서 링크·빌드 입력 경로 확인
- 키·DB·실행파일·개인 경로·작업 전달 파일 제외 검사

CI 설정의 두 OS 실행 결과는 Actions 기록을 별도로 확인해야 합니다. 로컬 통과와 원격 통과를 같은 것으로 표기하지 않습니다.

이것은 금융 성과 검증이 아닙니다. 실제 공급 API, 뉴스 방향의 문맥 정확도, 과거 정보의 발표 가용성, 반복 tick 처리와 원시 시세의 완전성에는 [남은 문제](limitations.md)가 있습니다.

[테스트 상세](testing-notes.md) · [실행 방법](wiki/How-to-Run.md)
