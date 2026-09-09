from __future__ import annotations

import csv
import html
import io
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import pandas as pd
import requests


logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


class PublicMarketClient:
    """No-key, read-only market data adapter.

    Naver Finance web feeds are used for Korean quotes, order book, charts,
    investor flow, member flow, indexes, breadth and program trading.  They
    are not an exchange-grade contractual API, so every value carries an
    explicit source/latency label.  Public Yahoo and FRED endpoints only fill
    global/macro context; they never replace a Korean quote.
    """

    NAVER_MOBILE = "https://m.stock.naver.com/api"
    NAVER_POLLING = "https://polling.finance.naver.com/api/realtime/domestic"
    NAVER_CHART = "https://fchart.stock.naver.com/sise.nhn"
    NAVER_DESKTOP = "https://finance.naver.com"
    YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
    ECOS_SAMPLE = "https://ecos.bok.or.kr/api/KeyStatisticList/sample/json/kr/1/10"

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Referer": "https://finance.naver.com/",
            "Accept": "application/json,text/plain,*/*",
        })
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()
        self._last_volume: dict[str, tuple[str, int]] = {}

    @staticmethod
    def number(value: Any) -> float | None:
        if value in (None, "", "null", "-"):
            return None
        try:
            return float(str(value).replace(",", "").replace("%", "").replace("+", "").strip())
        except (TypeError, ValueError):
            return None

    @classmethod
    def korean_won(cls, value: Any) -> float | None:
        """Parse Naver's compact Korean monetary notation (e.g. 12조 6,388억)."""
        text = str(value or "").replace(" ", "")
        if not text or text in {"-", "N/A"}:
            return None
        total = 0.0
        matched = False
        for number, unit in re.findall(r"([\d,.]+)([조억만])", text):
            parsed = cls.number(number)
            if parsed is None:
                continue
            total += parsed * {"조": 1_000_000_000_000, "억": 100_000_000, "만": 10_000}[unit]
            matched = True
        return total if matched else cls.number(text)

    def _cached(self, key: str, ttl: float, loader: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < ttl:
                return cached[1]
        try:
            value = loader()
        except Exception:
            with self._lock:
                stale = self._cache.get(key)
            if stale:
                logger.warning("공개 데이터 갱신 실패, 마지막 정상값 사용: %s", key)
                return stale[1]
            raise
        with self._lock:
            self._cache[key] = (now, value)
        return value

    def _get_json(self, url: str, *, timeout: float = 12.0) -> Any:
        response = self.session.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def _quote_row(self, symbol: str) -> dict[str, Any]:
        payload = self._cached(
            f"quote:{symbol}", 6.5,
            lambda: self._get_json(f"{self.NAVER_POLLING}/stock/{symbol}"),
        )
        rows = payload.get("datas", []) if isinstance(payload, dict) else []
        if not rows:
            raise RuntimeError("네이버 공개 현재가 응답이 비어 있습니다.")
        return dict(rows[0])

    def orderbook(self, symbol: str) -> dict[str, Any]:
        payload = self._cached(
            f"orderbook:{symbol}", 6.5,
            lambda: self._get_json(f"{self.NAVER_MOBILE}/stock/{symbol}/askingPrice"),
        )
        asks = [
            {"price": self.number(row.get("price")) or 0, "qty": self.number(row.get("count")) or 0}
            for row in payload.get("sellInfo", [])
            if self.number(row.get("price"))
        ]
        bids = [
            {"price": self.number(row.get("price")) or 0, "qty": self.number(row.get("count")) or 0}
            for row in payload.get("buyInfos", [])
            if self.number(row.get("price"))
        ]
        # The endpoint lists asks from far to near; the terminal expects best first.
        asks.sort(key=lambda row: row["price"])
        bids.sort(key=lambda row: row["price"], reverse=True)
        return {
            "asks": asks,
            "bids": bids,
            "source": "네이버 금융 공개 5단계 호가",
            "quality": "NEAR_REALTIME / 약 7초 폴링",
        }

    def current_price(self, symbol: str) -> dict[str, Any]:
        row = self._quote_row(symbol)
        price = self.number(row.get("closePriceRaw") or row.get("closePrice"))
        if not price or price <= 0:
            raise RuntimeError("네이버 공개 현재가가 유효하지 않습니다.")
        change = self.number(row.get("compareToPreviousClosePriceRaw") or row.get("compareToPreviousClosePrice")) or 0.0
        change_rate = self.number(row.get("fluctuationsRatioRaw") or row.get("fluctuationsRatio")) or 0.0
        accumulated = int(self.number(row.get("accumulatedTradingVolumeRaw") or row.get("accumulatedTradingVolume")) or 0)
        traded_at = str(row.get("localTradedAt") or datetime.now(KST).isoformat())
        session_day = traded_at[:10]
        previous = self._last_volume.get(symbol)
        volume_delta = 0 if not previous or previous[0] != session_day else max(0, accumulated - previous[1])
        self._last_volume[symbol] = (session_day, accumulated)
        market_status = str(row.get("marketStatus") or "UNKNOWN").upper()
        quality = "NEAR_REALTIME / 약 7초 폴링" if market_status == "OPEN" else "CLOSED / 정규장 마감값"
        tick = {
            "symbol": symbol,
            "time": traded_at,
            "price": price,
            "change": change,
            "change_rate": change_rate,
            "volume": volume_delta,
            "acc_volume": accumulated,
            "market_status": market_status,
            "source": "네이버 금융 공개시세",
            "quality": quality,
        }
        try:
            book = self.orderbook(symbol)
            tick["orderbook"] = book
            if book.get("asks"):
                tick["ask"] = book["asks"][0]["price"]
            if book.get("bids"):
                tick["bid"] = book["bids"][0]["price"]
        except Exception as exc:
            logger.debug("네이버 공개 호가 조회 실패 %s: %s", symbol, exc)
        return tick

    def _chart_xml(self, symbol: str, timeframe: str, count: int) -> ElementTree.Element:
        def load() -> bytes:
            response = self.session.get(
                self.NAVER_CHART,
                params={"symbol": symbol, "timeframe": timeframe, "count": count, "requestType": 0},
                timeout=18,
            )
            response.raise_for_status()
            return response.content

        ttl = 18.0 if timeframe == "minute" else 180.0
        content = self._cached(f"chart:{symbol}:{timeframe}:{count}", ttl, load)
        # Windows' bundled expat can reject a multibyte encoding declared in
        # a bytes document.  Decode exactly as declared, then parse Unicode.
        text = content.decode("euc-kr", errors="replace") if isinstance(content, bytes) else str(content)
        text = re.sub(r"^\s*<\?xml[^?]*\?>", "", text, count=1)
        return ElementTree.fromstring(text)

    def intraday_bars(self, symbol: str, days: int = 5) -> pd.DataFrame:
        root = self._chart_xml(symbol, "minute", 3200)
        raw_rows: list[tuple[str, float, float, float, float, int]] = []
        previous_close: dict[str, float] = {}
        previous_cumulative: dict[str, int] = {}
        for node in root.findall(".//item"):
            parts = str(node.attrib.get("data") or "").split("|")
            if len(parts) < 6 or len(parts[0]) < 12:
                continue
            stamp, day = parts[0], parts[0][:8]
            close = self.number(parts[4])
            cumulative = int(self.number(parts[5]) or 0)
            if close is None or close <= 0:
                continue
            derived_open = previous_close.get(day, close)
            open_ = self.number(parts[1]) or derived_open
            high = self.number(parts[2]) or max(open_, close)
            low = self.number(parts[3]) or min(open_, close)
            prior_volume = previous_cumulative.get(day, 0)
            volume = max(0, cumulative - prior_volume)
            previous_close[day] = close
            previous_cumulative[day] = cumulative
            raw_rows.append((stamp, open_, high, low, close, volume))
        if not raw_rows:
            return pd.DataFrame()
        dates = list(dict.fromkeys(row[0][:8] for row in raw_rows))[-max(1, days):]
        wanted = set(dates)
        records = [{
            "time": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[8:10]}:{stamp[10:12]}:00+09:00",
            "open": float(open_), "high": float(high), "low": float(low), "close": float(close),
            "volume": int(volume),
        } for stamp, open_, high, low, close, volume in raw_rows if stamp[:8] in wanted]
        return pd.DataFrame(records).drop_duplicates("time").sort_values("time").reset_index(drop=True)

    def historical_bars(self, symbol: str, days: int = 66) -> pd.DataFrame:
        root = self._chart_xml(symbol, "day", max(100, days + 10))
        records: list[dict[str, Any]] = []
        for node in root.findall(".//item"):
            parts = str(node.attrib.get("data") or "").split("|")
            if len(parts) < 6 or len(parts[0]) != 8:
                continue
            values = [self.number(value) for value in parts[1:6]]
            if any(value is None for value in values):
                continue
            date = parts[0]
            records.append({
                "time": f"{date[:4]}-{date[4:6]}-{date[6:8]}T15:30:00+09:00",
                "open": float(values[0]), "high": float(values[1]), "low": float(values[2]),
                "close": float(values[3]), "volume": int(values[4]),
            })
        return pd.DataFrame(records).drop_duplicates("time").sort_values("time").tail(days).reset_index(drop=True) if records else pd.DataFrame()

    def integration(self, symbol: str) -> dict[str, Any]:
        return dict(self._cached(
            f"integration:{symbol}", 20.0,
            lambda: self._get_json(f"{self.NAVER_MOBILE}/stock/{symbol}/integration"),
        ))

    @staticmethod
    def _market_value(
        value: Any,
        source: str,
        quality: str,
        direction: str | None = None,
        *,
        is_proxy: bool = False,
        proxy_symbol: str | None = None,
        frequency: str | None = None,
        delay_possible: bool | None = None,
    ) -> dict[str, Any]:
        collected_at = datetime.now(KST).isoformat()
        return {
            "value": value,
            "source": source,
            "quality": quality,
            "direction": direction,
            "observed_at": collected_at,
            "collected_at": collected_at,
            "freshness": "FRESH",
            "metadata": {
                "is_proxy": is_proxy,
                "proxy_symbol": proxy_symbol,
                "source_timestamp": collected_at,
                "frequency": frequency,
                "delay_possible": delay_possible,
            },
        }

    def investor_features(self, symbol: str, price: float) -> dict[str, Any]:
        payload = self.integration(symbol)
        rows = list(payload.get("dealTrendInfos") or [])
        if not rows:
            return {}
        latest = rows[0]
        foreign_qty = self.number(latest.get("foreignerPureBuyQuant"))
        institution_qty = self.number(latest.get("organPureBuyQuant"))
        individual_qty = self.number(latest.get("individualPureBuyQuant"))
        quality = "당일 누적/일별 갱신 · 수량×현재가 원화 환산"
        source = "네이버 금융 투자자별 매매동향"
        output: dict[str, Any] = {}
        totals = {str(row.get("code") or ""): row.get("value") for row in payload.get("totalInfos") or []}
        trading_value = self.korean_won(totals.get("accumulatedTradingValue"))
        market_value = self.korean_won(totals.get("marketValue"))
        if trading_value is not None and market_value and market_value > 0:
            turnover = round(trading_value / market_value * 100, 4)
            output["turnover"] = self._market_value(
                turnover, "Naver public accumulated trading value / market cap", "DAILY CUMULATIVE",
                "positive" if turnover > 0 else "neutral",
            )

        def add(feature_id: str, quantity: float | None) -> float | None:
            if quantity is None or price <= 0:
                return None
            won = round(quantity * price)
            output[feature_id] = self._market_value(
                won, source, quality, "positive" if won > 0 else "negative" if won < 0 else "neutral"
            )
            return float(won)

        foreign = add("foreign_net", foreign_qty)
        institution = add("institution_net", institution_qty)
        individual = add("individual_net", individual_qty)

        def streak(field: str) -> int | None:
            signs: list[int] = []
            for row in rows:
                value = self.number(row.get(field))
                if value is None or value == 0:
                    break
                signs.append(1 if value > 0 else -1)
            if not signs:
                return None
            count = 0
            for sign in signs:
                if sign != signs[0]:
                    break
                count += 1
            return count * signs[0]

        foreign_streak = streak("foreignerPureBuyQuant")
        institution_streak = streak("organPureBuyQuant")
        if foreign_streak is not None:
            output["foreign_streak"] = self._market_value(foreign_streak, source, "최근 5거래일", "positive" if foreign_streak > 0 else "negative")
        if institution_streak is not None:
            output["institution_streak"] = self._market_value(institution_streak, source, "최근 5거래일", "positive" if institution_streak > 0 else "negative")
        if foreign is not None and institution is not None:
            combined = foreign + institution
            output["joint_buy"] = self._market_value("예" if foreign > 0 and institution > 0 else "아니오", source, quality, "positive" if foreign > 0 and institution > 0 else "neutral")
            output["joint_sell"] = self._market_value("예" if foreign < 0 and institution < 0 else "아니오", source, quality, "negative" if foreign < 0 and institution < 0 else "neutral")
            volume = self.number(latest.get("accumulatedTradingVolume"))
            traded_value = volume * price if volume and price else None
            if traded_value:
                output["flow_to_value"] = self._market_value(round(combined / traded_value * 100, 4), source, quality)
            if len(rows) > 1:
                prior_combined_qty = (self.number(rows[1].get("foreignerPureBuyQuant")) or 0) + (self.number(rows[1].get("organPureBuyQuant")) or 0)
                acceleration = round(combined - prior_combined_qty * price)
                output["flow_acceleration"] = self._market_value(acceleration, source, "최근 거래일 대비 원화 증감", "positive" if acceleration > 0 else "negative")
        if foreign is not None and len(rows) > 1:
            prior_foreign = (self.number(rows[1].get("foreignerPureBuyQuant")) or 0) * price
            acceleration = round(foreign - prior_foreign)
            output["foreign_flow_acceleration"] = self._market_value(acceleration, source, "최근 거래일 대비 원화 증감", "positive" if acceleration > 0 else "negative")
        return output

    @staticmethod
    def _strip_tags(fragment: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()

    def members(self, symbol: str) -> list[dict[str, Any]]:
        def load() -> list[dict[str, Any]]:
            response = self.session.get(f"{self.NAVER_DESKTOP}/item/main.naver", params={"code": symbol}, timeout=15)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "euc-kr"
            match = re.search(r'<table[^>]+summary="[^"]*거래원 정보[^"]*"[^>]*>(.*?)</table>', response.text, re.I | re.S)
            if not match:
                return []
            tbody = re.search(r"<tbody[^>]*>(.*?)</tbody>", match.group(1), re.I | re.S)
            if not tbody:
                return []
            merged: dict[str, dict[str, Any]] = {}
            for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody.group(1), re.I | re.S):
                cells = [self._strip_tags(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.I | re.S)]
                cells = [cell for cell in cells if cell]
                if len(cells) < 4:
                    continue
                sell_name, sell_qty_raw, buy_name, buy_qty_raw = cells[:4]
                sell_qty = self.number(sell_qty_raw) or 0
                buy_qty = self.number(buy_qty_raw) or 0
                if sell_name:
                    item = merged.setdefault(sell_name, {"name": sell_name, "buy": 0.0, "sell": 0.0, "net": 0.0, "quality": "20분 지연/일별 상위 창구"})
                    item["sell"] += sell_qty
                if buy_name:
                    item = merged.setdefault(buy_name, {"name": buy_name, "buy": 0.0, "sell": 0.0, "net": 0.0, "quality": "20분 지연/일별 상위 창구"})
                    item["buy"] += buy_qty
            for item in merged.values():
                item["net"] = item["buy"] - item["sell"]
            return sorted(merged.values(), key=lambda item: abs(item["net"]), reverse=True)

        return list(self._cached(f"members:{symbol}", 60.0, load))

    def _index_payload(self, code: str) -> tuple[dict[str, Any], dict[str, Any]]:
        quote = self._cached(
            f"index-quote:{code}", 30.0,
            lambda: self._get_json(f"{self.NAVER_POLLING}/index/{code}"),
        )
        rows = quote.get("datas", []) if isinstance(quote, dict) else []
        row = dict(rows[0]) if rows else {}
        integration = dict(self._cached(
            f"index-integration:{code}", 60.0,
            lambda: self._get_json(f"{self.NAVER_MOBILE}/index/{code}/integration"),
        ))
        return row, integration

    def _yahoo_value(self, ticker: str) -> tuple[float | None, float | None]:
        payload = self._get_json(f"{self.YAHOO_CHART}/{ticker}?range=5d&interval=5m", timeout=10)
        results = payload.get("chart", {}).get("result") or []
        if not results:
            return None, None
        meta = results[0].get("meta", {})
        price = self.number(meta.get("regularMarketPrice"))
        previous = self.number(meta.get("previousClose") or meta.get("chartPreviousClose"))
        change = (price / previous - 1) * 100 if price and previous else None
        return price, change

    def _fred_value(self, series_id: str) -> float | None:
        response = self.session.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv",
            params={"id": series_id}, timeout=12,
            headers={"User-Agent": self.session.headers["User-Agent"]},
        )
        response.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(response.text)))
        for row in reversed(rows):
            value = self.number(row.get(series_id))
            if value is not None:
                return value
        return None

    def _bok_rate(self) -> float | None:
        payload = self._get_json(self.ECOS_SAMPLE, timeout=12)
        rows = payload.get("KeyStatisticList", {}).get("row", []) if isinstance(payload, dict) else []
        for row in rows:
            if "기준금리" in str(row.get("KEYSTAT_NAME") or ""):
                return self.number(row.get("DATA_VALUE"))
        return None

    def market_context(self) -> dict[str, Any]:
        return dict(self._cached("public-market-context", 45.0, self._build_market_context))

    def _build_market_context(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        index_defs = {
            "KOSPI": ("kospi_index", "kospi_change", "kospi_breadth"),
            "KOSDAQ": ("kosdaq_index", "kosdaq_change", "kosdaq_breadth"),
            "KPI200": ("kospi200_index", "kospi200_change", None),
        }
        for code, (price_key, change_key, breadth_key) in index_defs.items():
            try:
                row, integration = self._index_payload(code)
                price = self.number(row.get("closePriceRaw") or row.get("closePrice"))
                change = self.number(row.get("fluctuationsRatioRaw") or row.get("fluctuationsRatio"))
                quality = "NEAR_REALTIME / 약 70초 폴링" if str(row.get("marketStatus")).upper() == "OPEN" else "CLOSED / 마감값"
                if price is not None:
                    result[price_key] = self._market_value(price, "네이버 금융 공개 지수", quality)
                if change is not None:
                    result[change_key] = self._market_value(change, "네이버 금융 공개 지수", quality, "positive" if change > 0 else "negative" if change < 0 else "neutral")
                counts = integration.get("upDownStockInfo") or {}
                up = (self.number(counts.get("upperCount")) or 0) + (self.number(counts.get("riseCount")) or 0)
                down = (self.number(counts.get("lowerCount")) or 0) + (self.number(counts.get("fallCount")) or 0)
                flat = self.number(counts.get("steadyCount")) or 0
                if breadth_key and up + down + flat > 0:
                    breadth = round(up / (up + down + flat) * 100, 2)
                    result[breadth_key] = self._market_value(breadth, "네이버 금융 상승·하락 종목수 계산", quality, "positive" if breadth >= 55 else "negative" if breadth <= 45 else "neutral")
                if code == "KOSPI":
                    program = integration.get("programTrendInfo") or {}
                    for feature_id, field in {
                        "program_arbitrage_net": "indexDifferenceReal",
                        "program_non_arbitrage_net": "indexBiDifferenceReal",
                        "program_trading": "indexTotalReal",
                    }.items():
                        value = self.number(program.get(field))
                        if value is not None:
                            won = round(value * 100_000_000)
                            result[feature_id] = self._market_value(won, "네이버 금융 프로그램 매매(억원→원 환산)", "당일 누적", "positive" if won > 0 else "negative" if won < 0 else "neutral")
            except Exception as exc:
                logger.warning("네이버 공개 지수 조회 실패 %s: %s", code, exc)

        yahoo_defs = {
            "sp500": "^GSPC", "dow": "^DJI", "nasdaq": "^IXIC", "vix": "^VIX",
            "europe": "^STOXX50E", "nikkei": "^N225", "china": "000001.SS",
            "hangseng": "^HSI", "india": "^BSESN", "dollar_index": "DX-Y.NYB",
            "btc": "BTC-USD", "eth": "ETH-USD", "gold": "GC=F",
            "oil": "CL=F", "copper": "HG=F", "natural_gas_price": "NG=F", "vnq": "VNQ",
            "xlre": "XLRE", "iyr": "IYR", "xhb": "XHB", "itb": "ITB",
            "usdkrw": "KRW=X",
            "phlx_semiconductor_return": "^SOX", "soxx_return": "SOXX", "smh_return": "SMH",
            "nvidia_return": "NVDA", "micron_return": "MU", "tsmc_adr_return": "TSM",
            "taiwan_weighted_return": "^TWII", "tesla_return": "TSLA",
            "battery_etf_return": "LIT", "xbi_return": "XBI",
            "global_defense_etf": "ITA", "global_defense_companies_return": "PPA",
            "global_nuclear_etf": "URA",
        }
        price_features = {"usdkrw", "vix", "natural_gas_price", "dollar_index"}
        proxy_features = {
            "soxx_return", "smh_return", "nvidia_return", "micron_return", "tsmc_adr_return",
            "tesla_return", "battery_etf_return", "xbi_return", "global_defense_etf",
            "global_defense_companies_return", "global_nuclear_etf",
        }
        fred_defs = {"us2y": "DGS2", "us10y": "DGS10", "fed_rate": "DFF", "hy_spread": "BAMLH0A0HYM2"}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures: dict[Any, tuple[str, str, str]] = {}
            for feature_id, ticker in yahoo_defs.items():
                futures[pool.submit(self._yahoo_value, ticker)] = ("yahoo", feature_id, ticker)
            for feature_id, series_id in fred_defs.items():
                futures[pool.submit(self._fred_value, series_id)] = ("fred", feature_id, series_id)
            futures[pool.submit(self._bok_rate)] = ("ecos", "bok_rate", "KeyStatisticList/sample")
            for future in as_completed(futures):
                provider, feature_id, provider_symbol = futures[future]
                try:
                    value = future.result()
                except Exception as exc:
                    logger.debug("공개 거시 피드 실패 %s/%s: %s", provider, feature_id, exc)
                    continue
                if provider == "yahoo":
                    price, change = value
                    if feature_id == "usdkrw":
                        if price is not None:
                            result[feature_id] = self._market_value(round(price, 4), "Yahoo 공개 FX", "NEAR_REALTIME / 지연 가능", proxy_symbol=provider_symbol, frequency="5분", delay_possible=True)
                        if change is not None:
                            result["usdkrw_change"] = self._market_value(round(change, 3), "Yahoo 공개 FX", "NEAR_REALTIME / 지연 가능", "positive" if change > 0 else "negative" if change < 0 else "neutral", proxy_symbol=provider_symbol, frequency="5분", delay_possible=True)
                    elif feature_id == "vix":
                        if price is not None:
                            result[feature_id] = self._market_value(round(price, 3), "Yahoo 공개 시장데이터", "지연 가능", "negative" if price >= 25 else "neutral", proxy_symbol=provider_symbol, frequency="5분", delay_possible=True)
                    elif feature_id in price_features:
                        if price is not None:
                            result[feature_id] = self._market_value(round(price, 4), "Yahoo 공개 시장데이터", "지연 가능", proxy_symbol=provider_symbol, frequency="5분", delay_possible=True)
                    elif change is not None:
                        result[feature_id] = self._market_value(round(change, 3), "Yahoo 공개 시장데이터", "지연 가능", "positive" if change > 0 else "negative" if change < 0 else "neutral", is_proxy=feature_id in proxy_features, proxy_symbol=provider_symbol, frequency="5분", delay_possible=True)
                elif value is not None:
                    source = "FRED 공개 통계" if provider == "fred" else "한국은행 ECOS sample 무키 지표"
                    quality = "일별/공식 공개" if provider == "fred" else "공식 sample / 호출제한"
                    result[feature_id] = self._market_value(round(float(value), 4), source, quality, proxy_symbol=provider_symbol, frequency="일별" if provider == "fred" else "ECOS 표기주기", delay_possible=True)

        us2y = result.get("us2y", {}).get("value")
        us10y = result.get("us10y", {}).get("value")
        if isinstance(us2y, (int, float)) and isinstance(us10y, (int, float)):
            curve = round(us10y - us2y, 4)
            result["yield_curve"] = self._market_value(curve, "FRED 10Y-2Y 계산", "일별/공식 공개", "positive" if curve >= 0 else "negative")
        breadth = result.get("kospi_breadth", {}).get("value")
        if isinstance(breadth, (int, float)):
            result["decline_ratio"] = self._market_value(round(100 - breadth, 2), "네이버 금융 시장폭 계산", "당일", "negative" if breadth < 45 else "neutral")
        now = datetime.now(KST)
        is_night = now.hour >= 18 or now.hour < 6
        is_day = (now.hour == 8 and now.minute >= 45) or 9 <= now.hour < 15 or (now.hour == 15 and now.minute <= 45)
        session = "야간" if is_night else "주간" if is_day else "장외"
        result["krx_derivatives_session"] = self._market_value(session, "KRX 세션 시각 규칙", "시각 규칙", "neutral")
        return result
