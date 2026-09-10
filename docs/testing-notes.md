# 테스트와 실행 범위

저장소 루트에서 의존성을 준비한 뒤 다음 범위를 확인할 수 있다.

```powershell
python -m pytest tests/test_news_pipeline.py tests/test_ai_analysis.py -q
```

외부 원천 전체 수집이나 금융 성과 재현을 의미하지 않는다.

실시간 API 권한·지연과 외부 모델 응답에 의존한다. 분석 설명은 관측 사실이나 검증된 투자 판단을 대신하지 않는다. 로컬 가상매매와 실제 체결·주문 운영을 구분한다.

테스트 실행과 전체 원천 수집·학습은 별개다. 연구 결과는 [README](../README.md)의 당시 기록과 입력 조건을 함께 읽는다.
