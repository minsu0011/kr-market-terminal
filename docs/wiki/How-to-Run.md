# 실행

Windows / Python 3.11 이상을 기준으로 합니다.

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8767
~~~

http://127.0.0.1:8767 에 접속합니다. 예시는 합성 시세·뉴스를 사용하고 외부 분석은 꺼져 있습니다. 최초 설정 팝업을 닫으면 현재 데모 설정을 유지하며, 공개 데이터로 시작하는 버튼은 네트워크 수집 모드로 전환합니다.

실데이터는 .env의 DATA_MODE=public 또는 화면의 검증된 KIS 설정을 사용합니다. [로컬 데이터](../../data/README.md)를 먼저 확인하세요. API 키는 파일에 적거나 Git에 올리지 않습니다.

## 개발 테스트

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
~~~

## Windows 포터블 빌드

별도 빌드 환경에 PyInstaller를 설치한 뒤 BUILD_PORTABLE.bat를 실행합니다. 소스 설정과 예제만 묶으며 실제 DB·비밀키는 포함하지 않습니다. 출력은 dist에 생성되고 Git에서 제외합니다.

~~~powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\BUILD_PORTABLE.bat
~~~

이번 검증에서는 EXE를 새로 빌드하지 않았습니다. 일반 Python 실행·테스트와 빌드 설정의 경로를 검사했습니다.
