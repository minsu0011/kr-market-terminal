# 실행·증거 범위

## 이전 실행 기록

- 구문 검사: PASS, Python 27개 파일.
- 선택한 실행: `pytest tests/test_news_pipeline.py tests/test_ai_analysis.py -q`
- 기록 상태: `PASS`, exit code 0.
- 금융 모델 성과 전체 재현: 수행하지 않음.

위 기록은 선택한 함수 또는 인터페이스의 실행 결과다. 외부 원천 전체 수집이나 금융 성과 재현을 의미하지 않는다.

## 문서 변경 확인

2026-09-11 문서 개정은 README와 상세 설명에 한정했다. 코드·모델·설정·보존 결과는 변경 전 해시와 대조하고, 문서 링크·금지된 게시 메타 표현·비밀정보 패턴·Git diff를 확인한다. 상세 감사 결과와 실행 로그는 로컬 publication/documentation 보고서에 보존한다.

## 증거 표시

`REPRODUCED`는 실제 재실행한 해당 범위, `RECEIPT_VERIFIED`는 보존 결과의 연결·동일성 확인, `SOURCE_REPORTED`는 당시 기록, `UNKNOWN`은 근거 미확인을 뜻한다. README에서는 보존된 결과 파일 또는 당시 실험 기록이라는 표현을 사용한다. 상태 label만으로 성과를 승격하지 않는다.

같은 dataset/version·sample ID·split·target·metric·전처리·horizon을 모두 확인하지 않은 실험 간에는 직접적인 metric delta를 계산하지 않는다.

[기존 검증 기록](validation.md) · [프로젝트 설명](../README.md)
