# 데이터 준비

시세·뉴스 API, 계정 설정, 로컬 DB. API key와 뉴스 원문/거래 기록은 포함하지 않는다.

수집·전처리 진입점은 루트 README와 코드의 CLI를 참고한다. 원천을 내려받을 때 이용약관과 계정 권한을 먼저 확인하고, 실행 시각·source URL·버전·SHA-256을 별도 보존한다. 실험 기간은 source config와 freeze 계약을 따라야 하며 현재 종목으로 historical universe를 대체해 인증하지 않는다.

raw/provider 응답, 뉴스 원문, 계정·거래 데이터, weights는 저장소에 넣지 않는다. API 키는 환경변수나 로컬 비공개 설정으로 입력한다.
