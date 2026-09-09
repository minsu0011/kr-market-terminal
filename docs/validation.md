# 검증

과거 성과는 UNKNOWN이며 현재 게시본 검사 범위는 아래 결과를 따른다.

현재 source의 구조를 확인했으며 기존 기록은 SOURCE_REPORTED로 구분한다. REPRODUCED는 이번 게시본에서 실행한 검사에만, RECEIPT_VERIFIED는 원본 결과 파일과 해시 연결을 확인한 수치에만 사용한다. 전체 학습 성능은 UNKNOWN 또는 SOURCE_REPORTED다.

실시간 API 권한과 연결 상태에 의존한다. 외부 모델 해석은 투자 판단의 검증된 근거가 아니며 주문 연결은 별도 검토가 필요하다.

게시본 검사 결과는 `checks.json`에 기록한다. 의존성·데이터가 없어 실행하지 못한 항목은 통과로 세지 않는다.

## 이번 게시본 검사

- Python 구문: PASS
- 실행 범위: `pytest tests/test_news_pipeline.py tests/test_ai_analysis.py -q`
- 결과: PASS
- 세부 집계: passed=24
