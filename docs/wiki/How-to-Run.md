# 로컬 웹 실행

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

`http://127.0.0.1:8000`에 접속한다. 시작 시 `data/terminal.sqlite3`가 생성될 수 있다. 설정에 따라 background provider·뉴스 호출이 발생하므로 계정 권한과 API 비용을 먼저 확인한다.

[데이터 준비](../../data/README.md)를 따라 로컬 설정을 넣는다. 기본 문서의 localhost 실행을 외부 공개 배포나 실거래 서비스로 간주하지 않는다. secret store와 가상매매 모듈을 제거해 단순화하지 않는다.
