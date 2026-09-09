from __future__ import annotations

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import requests


USER_AGENT = "KR-DayTrading-Terminal/3.0 (local Windows desktop; read-only)"


def _result(state: str, message: str) -> dict[str, Any]:
    return {"state": state, "ok": state == "NORMAL", "message": message}


def _http_error(response: requests.Response) -> dict[str, Any]:
    status = response.status_code
    if status == 401:
        return _result("AUTH_FAILED", "인증 실패(401)")
    if status == 403:
        return _result("PERMISSION_DENIED", "권한 부족(403)")
    if status == 429:
        return _result("RATE_LIMITED", "호출 제한(429)")
    if 500 <= status:
        return _result("SERVER_ERROR", f"공급자 서버 오류({status})")
    return _result("RESPONSE_CHANGED", f"예상하지 못한 HTTP 응답({status})")


def _run(test: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return test()
    except requests.ConnectTimeout:
        return _result("TIMEOUT", "연결 시간 초과")
    except requests.ReadTimeout:
        return _result("TIMEOUT", "응답 시간 초과")
    except requests.ConnectionError:
        return _result("NETWORK_FAILED", "DNS 또는 네트워크 연결 실패")
    except (ValueError, KeyError, TypeError):
        return _result("RESPONSE_CHANGED", "응답 형식이 예상과 다릅니다.")
    except requests.RequestException:
        return _result("NETWORK_FAILED", "네트워크 요청 실패")
    except Exception as exc:
        return _result("SERVER_ERROR", f"연결 검사 실패({type(exc).__name__})")


def test_openai(api_key: str, model: str = "gpt-5.6-sol") -> dict[str, Any]:
    if not api_key:
        return _result("NOT_CONFIGURED", "미설정")

    def check() -> dict[str, Any]:
        response = requests.get(
            f"https://api.openai.com/v1/models/{model}",
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
            timeout=(7, 15),
        )
        if response.status_code != 200:
            return _http_error(response)
        payload = response.json()
        if not payload.get("id"):
            return _result("RESPONSE_CHANGED", "OpenAI 모델 응답 형식 변경")
        return _result("NORMAL", f"OpenAI 인증·{model} 접근 정상")

    return _run(check)


def test_open_dart(api_key: str) -> dict[str, Any]:
    if not api_key:
        return _result("NOT_CONFIGURED", "미설정")

    def check() -> dict[str, Any]:
        today = datetime.now().strftime("%Y%m%d")
        response = requests.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={"crtfc_key": api_key, "bgn_de": today, "end_de": today, "page_count": 1},
            headers={"User-Agent": USER_AGENT}, timeout=(7, 15),
        )
        if response.status_code != 200:
            return _http_error(response)
        payload = response.json()
        status = str(payload.get("status") or "")
        if status in {"000", "013"}:
            return _result("NORMAL", "OpenDART 인증 정상")
        mapping = {
            "010": ("AUTH_FAILED", "등록되지 않은 OpenDART 키"),
            "011": ("AUTH_FAILED", "사용 중지된 OpenDART 키"),
            "012": ("PERMISSION_DENIED", "OpenDART 접근 IP 제한"),
            "020": ("RATE_LIMITED", "OpenDART 호출 제한 초과"),
            "800": ("SERVER_ERROR", "OpenDART 점검 중"),
        }
        state, message = mapping.get(status, ("RESPONSE_CHANGED", f"OpenDART 응답 코드 {status or '없음'}"))
        return _result(state, message)

    return _run(check)


def test_ecos(api_key: str) -> dict[str, Any]:
    if not api_key:
        return _result("NOT_CONFIGURED", "미설정")

    def check() -> dict[str, Any]:
        response = requests.get(
            f"https://ecos.bok.or.kr/api/KeyStatisticList/{api_key}/json/kr/1/1",
            headers={"User-Agent": USER_AGENT}, timeout=(7, 15),
        )
        if response.status_code != 200:
            return _http_error(response)
        payload = response.json()
        if payload.get("RESULT"):
            code = str(payload["RESULT"].get("CODE") or "")
            if code in {"INFO-200", "ERROR-300"}:
                return _result("AUTH_FAILED", "ECOS 인증키 오류")
            if code == "INFO-100":
                return _result("RATE_LIMITED", "ECOS 호출 제한")
            return _result("SERVER_ERROR", "ECOS 오류 응답")
        if "KeyStatisticList" not in payload:
            return _result("RESPONSE_CHANGED", "ECOS 응답 형식 변경")
        return _result("NORMAL", "ECOS 인증 정상")

    return _run(check)


def test_naver(client_id: str, client_secret: str) -> dict[str, Any]:
    if not client_id or not client_secret:
        return _result("NOT_CONFIGURED", "Client ID와 Secret이 모두 필요합니다.")

    def check() -> dict[str, Any]:
        response = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={"query": "삼성전자", "display": 1, "sort": "date"},
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
                "User-Agent": USER_AGENT,
            },
            timeout=(7, 15),
        )
        if response.status_code != 200:
            return _http_error(response)
        payload = response.json()
        if not isinstance(payload.get("items"), list):
            return _result("RESPONSE_CHANGED", "네이버 검색 응답 형식 변경")
        return _result("NORMAL", "네이버 뉴스 검색 API 인증 정상")

    return _run(check)


def test_kis(app_key: str, app_secret: str, mode: str) -> dict[str, Any]:
    if not app_key or not app_secret:
        return _result("NOT_CONFIGURED", "APP Key와 APP Secret이 모두 필요합니다.")

    def check() -> dict[str, Any]:
        base = "https://openapivts.koreainvestment.com:29443" if mode == "paper" else "https://openapi.koreainvestment.com:9443"
        response = requests.post(
            f"{base}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret},
            headers={"User-Agent": USER_AGENT}, timeout=(7, 20),
        )
        if response.status_code != 200:
            return _http_error(response)
        payload = response.json()
        if not payload.get("access_token"):
            return _result("AUTH_FAILED", "KIS 토큰을 받지 못했습니다.")
        return _result("NORMAL", "KIS 인증 정상")

    return _run(check)


def test_all(values: dict[str, str], kis_mode: str = "real") -> dict[str, dict[str, Any]]:
    jobs = {
        "openai": (test_openai, (values.get("OPENAI_API_KEY", ""),)),
        "open_dart": (test_open_dart, (values.get("OPEN_DART_API_KEY", ""),)),
        "ecos": (test_ecos, (values.get("ECOS_API_KEY", ""),)),
        "naver_news": (test_naver, (values.get("NAVER_CLIENT_ID", ""), values.get("NAVER_CLIENT_SECRET", ""))),
        "kis": (test_kis, (values.get("KIS_APP_KEY", ""), values.get("KIS_APP_SECRET", ""), kis_mode)),
    }
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {provider_id: pool.submit(function, *args) for provider_id, (function, args) in jobs.items()}
        return {provider_id: future.result() for provider_id, future in futures.items()}
