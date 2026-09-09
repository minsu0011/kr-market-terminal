from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import websockets

from .config import ROOT, Settings, STOCK_BY_SYMBOL, STOCKS
from .public_data import PublicMarketClient

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


class KISAuthBackoffError(RuntimeError):
    """A recent KIS authentication failure is being cooled down locally."""


BASE_PRICES = {
    "005930":82000,"000660":205000,"005380":245000,"000270":118000,
    "373220":390000,"207940":1030000,"068270":188000,"035420":228000,
    "035720":51000,"105560":89000,"055550":55000,"005490":335000,
    "012450":420000,"028260":168000,
}


def trading_minutes(day: datetime) -> pd.DatetimeIndex:
    return pd.date_range(day.replace(hour=9,minute=0,second=0,microsecond=0),
                         day.replace(hour=15,minute=30,second=0,microsecond=0),freq="1min",tz=KST)


def market_is_open(now: datetime) -> bool:
    """KRX regular-session guard used by the local simulator."""
    if now.weekday() >= 5:
        return False
    opening = now.replace(hour=9, minute=0, second=0, microsecond=0)
    closing = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return opening <= now <= closing


def krx_tick(price: float) -> int:
    """KOSPI price unit, used only for simulated prices."""
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


class DemoProvider:
    def __init__(self):
        self.frames: dict[str,pd.DataFrame] = {}
        self.locks: dict[str,threading.RLock] = defaultdict(threading.RLock)
        self.rngs: dict[str,random.Random] = {}

    @staticmethod
    def seed(symbol: str) -> int:
        return sum((i+1)*ord(ch) for i,ch in enumerate(symbol))+20260729

    def generate(self, symbol: str, days: int = 5) -> pd.DataFrame:
        seed=self.seed(symbol); rng=np.random.default_rng(seed); now=datetime.now(KST)
        sessions=[]; cursor=now.replace(hour=0,minute=0,second=0,microsecond=0)
        while len(sessions)<days:
            if cursor.weekday()<5:
                session=trading_minutes(cursor)
                if cursor.date()==now.date():
                    market_open=now.replace(hour=9,minute=0,second=0,microsecond=0)
                    market_close=now.replace(hour=15,minute=30,second=0,microsecond=0)
                    if now < market_open:
                        session=session[:0]
                    elif now < market_close:
                        cutoff=pd.Timestamp(now.replace(second=0,microsecond=0))
                        session=session[session<=cutoff]
                if len(session): sessions.append(session)
            cursor-=timedelta(days=1)
        # ``sessions`` is collected newest -> oldest. Build the final index
        # oldest -> newest so chart bars, latest price, and features refer to
        # the very same final candle.
        idx=sessions[-1]
        for part in sessions[-2::-1]:
            idx=idx.append(part)
        n=len(idx); base=float(BASE_PRICES.get(symbol,100000))
        wave=np.sin(np.linspace(0,17,n))*.00028+np.sin(np.linspace(0,51,n)+seed%7)*.00018
        returns=np.where(np.arange(n)<n*.45,.000015,-.000002)+wave+rng.normal(0,.00065,n)
        close=base*np.exp(np.cumsum(returns)); tick=krx_tick(base)
        close=np.round(close/tick)*tick; open_=np.r_[close[0],close[:-1]]
        spread=np.abs(rng.normal(base*.00055,base*.00025,n))
        high=np.round((np.maximum(open_,close)+spread)/tick)*tick
        low=np.round(np.maximum(np.minimum(open_,close)-spread,tick)/tick)*tick
        minute=np.tile(np.arange(391),len(sessions))[:n]; ushape=1.6-.8*np.sin(np.pi*minute/390)
        volume=np.maximum((rng.lognormal(9.5,.7,n)*ushape).astype(int),100)
        frame=pd.DataFrame({"time":idx.astype(str),"open":open_.astype(float),"high":high.astype(float),
                            "low":low.astype(float),"close":close.astype(float),"volume":volume.astype(int)})
        self.frames[symbol]=frame; self.rngs[symbol]=random.Random(seed+int(time.time()//3600)); return frame

    def get_bars(self,symbol:str,days:int=5)->pd.DataFrame:
        with self.locks[symbol]:
            if symbol not in self.frames: self.generate(symbol,days)
            return self.frames[symbol].copy()

    def chart_bars(self, symbol: str, chart_range: str) -> pd.DataFrame:
        """Return stable, range-specific demo bars without mutating live prices."""
        if chart_range in {"1d", "1w"}:
            frame = self.get_bars(symbol, 5)
            times = pd.to_datetime(frame["time"])
            if chart_range == "1d" and not frame.empty:
                return frame.loc[times.dt.date == times.iloc[-1].date()].reset_index(drop=True)
            return frame.reset_index(drop=True)

        periods = {"1mo": 22, "3mo": 66}.get(chart_range, 22)
        now = datetime.now(KST)
        dates = pd.bdate_range(end=now.date(), periods=periods)
        seed = self.seed(symbol) + periods * 97
        rng = np.random.default_rng(seed)
        base = float(BASE_PRICES.get(symbol, 100000))
        returns = rng.normal(0.00035, 0.017, len(dates))
        close = base * np.exp(np.cumsum(returns) - np.cumsum(returns)[-1])
        tick = krx_tick(base)
        close = np.maximum(tick, np.round(close / tick) * tick)
        open_ = np.round(np.r_[close[0] * (1 - returns[0]), close[:-1]] / tick) * tick
        spread = np.maximum(base * rng.uniform(0.004, 0.018, len(dates)), tick)
        high = np.round((np.maximum(open_, close) + spread) / tick) * tick
        low = np.maximum(tick, np.round((np.minimum(open_, close) - spread) / tick) * tick)
        volume = rng.integers(500_000, 12_000_000, len(dates))
        return pd.DataFrame({
            "time": [f"{day:%Y-%m-%d}T15:30:00+09:00" for day in dates],
            "open": open_.astype(float), "high": high.astype(float), "low": low.astype(float),
            "close": close.astype(float), "volume": volume.astype(int),
        })

    def tick(self,symbol:str)->dict[str,Any]:
        with self.locks[symbol]:
            if symbol not in self.frames: self.generate(symbol)
            frame=self.frames[symbol]; rng=self.rngs[symbol]; price=float(frame.iloc[-1]["close"])
            now=datetime.now(KST).replace(microsecond=0); minute=now.replace(second=0)
            prev=float(frame.iloc[-2]["close"]) if len(frame)>1 else price
            if not market_is_open(now):
                return {"symbol":symbol,"time":now.isoformat(),"price":price,"volume":0,"change":price-prev,
                        "change_rate":(price/prev-1)*100 if prev else 0,"source":"DEMO","quality":"SIMULATED / CLOSED"}

            momentum=float(frame["close"].tail(6).pct_change().mean() or 0)
            move=max(-.0015,min(.0015,rng.gauss(0,.00018)+momentum*.12))
            base=float(BASE_PRICES.get(symbol,100000)); new=price*(1+move)
            new=min(base*1.6,max(base*.6,new)); unit=krx_tick(new)
            new=round(new/unit)*unit
            vol=max(1,int(abs(rng.gauss(9000,5000)))); last_t=pd.Timestamp(frame.iloc[-1]["time"])
            if last_t.tzinfo is None: last_t=last_t.tz_localize(KST)
            if last_t.floor("min")==pd.Timestamp(minute):
                i=frame.index[-1]; frame.at[i,"high"]=max(float(frame.at[i,"high"]),new)
                frame.at[i,"low"]=min(float(frame.at[i,"low"]),new); frame.at[i,"close"]=new
                frame.at[i,"volume"]=int(frame.at[i,"volume"])+vol
            else:
                frame=pd.concat([frame,pd.DataFrame([{"time":minute.isoformat(),"open":price,"high":max(price,new),
                    "low":min(price,new),"close":new,"volume":vol}])],ignore_index=True).tail(2500)
            self.frames[symbol]=frame
            return {"symbol":symbol,"time":now.isoformat(),"price":new,"volume":vol,"change":new-prev,
                    "change_rate":(new/prev-1)*100 if prev else 0,"source":"DEMO","quality":"SIMULATED"}

    def external(self,symbol:str,frame:pd.DataFrame)->dict[str,Any]:
        seed=self.seed(symbol)+int(pd.Timestamp(frame.iloc[-1]["time"]).timestamp()//300); rng=random.Random(seed)
        ret=float(frame["close"].pct_change(5).iloc[-1]*100); foreign_eok=round(rng.gauss(18+ret*8,45),1)
        institution_eok=round(rng.gauss(8+ret*6,34),1); individual_eok=round(-(foreign_eok+institution_eok)+rng.gauss(0,5),1)
        foreign, institution, individual = (round(foreign_eok*100_000_000), round(institution_eok*100_000_000), round(individual_eok*100_000_000))
        decline=min(95,max(5,48-ret*16+rng.gauss(0,8))); vix=max(9,18+rng.gauss(0,3)); short=max(.1,3.8+rng.gauss(0,1.5)-ret*.3)
        def d(value:Any,direction:str|None=None,quality:str="SIMULATED")->dict[str,Any]:
            return {"value":value,"source":"DEMO 생성","quality":quality,"direction":direction}
        data={
            "foreign_net":d(foreign),"institution_net":d(institution),"individual_net":d(individual),
            "foreign_streak":d(rng.choice([-4,-3,-2,-1,1,2,3,4])),"institution_streak":d(rng.choice([-3,-2,-1,1,2,3])),
            "flow_to_value":d(round((foreign_eok+institution_eok)/max(abs(foreign_eok)+abs(institution_eok)+200,1)*100,2)),
            "joint_buy":d("예" if foreign_eok>0 and institution_eok>0 else "아니오","positive" if foreign_eok>0 and institution_eok>0 else "neutral"),
            "joint_sell":d("예" if foreign_eok<0 and institution_eok<0 else "아니오","negative" if foreign_eok<0 and institution_eok<0 else "neutral"),
            "price_flow_divergence":d(round(ret-(foreign_eok+institution_eok)/100,2)),"flow_acceleration":d(round(rng.gauss((foreign_eok+institution_eok)/8,8),2)),
            "short_ratio":d(round(short,2)),"short_balance_to_cap":d(round(max(.1,rng.gauss(1.5,.5)),2)),
            "short_cumulative":d(int(max(0,rng.gauss(1_200_000,300_000)))),"price_short_divergence":d(round(ret+short/10,2)),
            "decline_ratio":d(round(decline,1)),"crash_ratio":d(round(max(0,decline-55)/4,1)),"top50_contagion":d(round(max(0,decline-40),1)),
            "sector_crash_ratio":d(round(max(0,decline-48),1)),"samsung_contagion":d(round(rng.gauss(-.1,.8),2)),
            "hynix_contagion":d(round(rng.gauss(.1,1.2),2)),"hyundai_contagion":d(round(rng.gauss(0,.7),2)),
            "sp500":d(round(rng.gauss(.15,.7),2)),"nasdaq":d(round(rng.gauss(.2,.9),2)),"europe":d(round(rng.gauss(0,.6),2)),
            "nikkei":d(round(rng.gauss(.1,.8),2)),"china":d(round(rng.gauss(-.05,.8),2)),"hangseng":d(round(rng.gauss(0,1),2)),
            "india":d(round(rng.gauss(.1,.5),2)),"vix":d(round(vix,2),"negative" if vix>25 else "neutral"),
            "btc":d(round(rng.gauss(.2,1.8),2)),"eth":d(round(rng.gauss(.3,2.1),2)),"gold":d(round(rng.gauss(0,.5),2)),
            "oil":d(round(rng.gauss(0,1.1),2)),"copper":d(round(rng.gauss(0,.8),2)),"vnq":d(round(rng.gauss(0,.7),2)),
            "xlre":d(round(rng.gauss(0,.65),2)),"iyr":d(round(rng.gauss(0,.65),2)),"xhb":d(round(rng.gauss(0,.9),2)),
            "itb":d(round(rng.gauss(0,.9),2)),"krx_construction":d(round(rng.gauss(0,.8),2)),"krx_reit":d(round(rng.gauss(0,.5),2)),
            "ecos_house":d(round(rng.gauss(.03,.08),2),quality="MONTHLY SIMULATED"),"us2y":d(round(rng.gauss(4.1,.08),3)),
            "us10y":d(round(rng.gauss(4.3,.08),3)),"yield_curve":d(round(rng.gauss(.2,.08),3)),"fed_rate":d(4.25),"bok_rate":d(2.5),
            "hy_spread":d(round(rng.gauss(3.2,.2),2)),"usdkrw":d(round(rng.gauss(1370,7),1)),"fomc_distance":d(rng.randint(-10,10)),
            "bok_distance":d(rng.randint(-10,10)),"policy_window":d(rng.choice(["해당없음","D-1","D+1","D-3"])),
            "news_count":d(rng.randint(1,55)),"negative_news":d(round(rng.random()*.8,2)),"risk_words":d(round(rng.random()*.7,2)),
            "source_diversity":d(rng.randint(1,14)),"news_burst":d(round(rng.gauss(.4,1.2),2)),"gdelt_count":d(rng.randint(0,70)),
            "dart_rights_issue":d(0),"dart_capital_reduction":d(0),"dart_cb_bw":d(0),"dart_major_holder":d(0),
            "dart_embezzlement":d(0),"dart_default":d(0),"dart_audit":d(0),"dart_lawsuit":d(0),
            "debt_ratio":d(round(rng.uniform(35,180),1)),"current_ratio":d(round(rng.uniform(75,260),1)),
            "cash_assets":d(round(rng.uniform(5,35),1)),"borrow_assets":d(round(rng.uniform(3,38),1)),
            "operating_margin":d(round(rng.uniform(3,32),1)),"operating_cashflow":d(round(rng.uniform(-5000,30000)*100_000_000)),
            "rate_debt_interaction":d(round(rng.uniform(.1,1.5),2)),
        }
        return data

    def members(self,symbol:str)->list[dict[str,Any]]:
        rng=random.Random(self.seed(symbol)+int(time.time()//60)); names=["미래에셋","NH투자","한국투자","KB증권","삼성증권","신한투자","메릴린치","모건스탠리","JP모간","골드만삭스"]
        rows=[]
        for name in names:
            buy=max(0,int(rng.gauss(180000,90000))); sell=max(0,int(rng.gauss(170000,90000)))
            rows.append({"name":name,"buy":buy,"sell":sell,"net":buy-sell,"quality":"SIMULATED"})
        return sorted(rows,key=lambda x:abs(x["net"]),reverse=True)


class KISClient:
    REAL_REST="https://openapi.koreainvestment.com:9443"; PAPER_REST="https://openapivts.koreainvestment.com:29443"
    REAL_WS="ws://ops.koreainvestment.com:21000"; PAPER_WS="ws://ops.koreainvestment.com:31000"
    def __init__(self,settings:Settings):
        self.settings=settings; self.rest_base=self.REAL_REST if settings.kis_mode=="real" else self.PAPER_REST
        self.ws_url=self.REAL_WS if settings.kis_mode=="real" else self.PAPER_WS; self.session=requests.Session()
        self._token=None; self._token_expiry=0.; self._approval=None; self._last_call=0.; self.latest_ticks={}; self.latest_members={}; self.latest_quotes={}; self.quote_times={}; self.task=None; self.running=False
        # A single client is shared by HTTP routes and the websocket loop.  KIS
        # permits only a very small number of token issuances, so concurrent
        # first requests must share one token request and failures need a pause.
        self._request_lock=threading.RLock(); self._token_lock=threading.RLock(); self._approval_lock=threading.RLock()
        self._token_failure_until=0.; self._approval_failure_until=0.
        # Keep retrying promptly while preserving a small safety margin above
        # KIS's one-token-per-second limit.
        self._token_retry_delay=1.2; self._approval_retry_delay=1.2
        self._market_cache:dict[str,Any]={}; self._market_cache_time=0.0
        self._futures_open_interest:dict[str,float]={}
    def rate_limit(self, minimum: float = 0.12) -> None:
        """Serialize REST starts to leave headroom below KIS per-second limits."""
        with self._request_lock:
            elapsed=time.monotonic()-self._last_call
            if elapsed<minimum: time.sleep(minimum-elapsed)
            self._last_call=time.monotonic()
    def _schedule_auth_retry(self, kind: str) -> None:
        delay_attr=f"_{kind}_retry_delay"; until_attr=f"_{kind}_failure_until"
        delay=float(getattr(self,delay_attr))
        setattr(self,until_attr,time.monotonic()+delay)
        # Use the same short interval for every failure; no long initial wait.
        setattr(self,delay_attr,1.2)
    def _reset_auth_retry(self, kind: str) -> None:
        setattr(self,f"_{kind}_retry_delay",1.2)
    def token(self)->str:
        if self._token and time.time()<self._token_expiry-60: return self._token
        with self._token_lock:
            if self._token and time.time()<self._token_expiry-60: return self._token
            if time.monotonic()<self._token_failure_until:
                raise KISAuthBackoffError("KIS 토큰 재시도 대기 중")
            try:
                r=self.session.post(
                    f"{self.rest_base}/oauth2/tokenP",
                    json={"grant_type":"client_credentials","appkey":self.settings.kis_app_key,"appsecret":self.settings.kis_app_secret},
                    headers={"content-type":"application/json; charset=utf-8","User-Agent":"KR-DayTrading-Terminal/3.0"},
                    timeout=15,
                )
                r.raise_for_status(); p=r.json(); token=str(p.get("access_token") or "")
                if not token: raise RuntimeError("KIS 토큰 응답에 access_token이 없습니다.")
            except Exception:
                # Do not turn one gateway rejection into a token request every
                # websocket tick.  The public-data fallback remains available.
                self._schedule_auth_retry("token")
                raise
            self._token=token; self._token_expiry=time.time()+int(p.get("expires_in",86400)); self._reset_auth_retry("token"); return self._token
    def approval(self)->str:
        if self._approval: return self._approval
        with self._approval_lock:
            if self._approval: return self._approval
            if time.monotonic()<self._approval_failure_until:
                raise KISAuthBackoffError("KIS 웹소켓 승인 재시도 대기 중")
            try:
                r=self.session.post(
                    f"{self.rest_base}/oauth2/Approval",
                    json={"grant_type":"client_credentials","appkey":self.settings.kis_app_key,"secretkey":self.settings.kis_app_secret},
                    headers={"content-type":"application/json; charset=utf-8","User-Agent":"KR-DayTrading-Terminal/3.0"},
                    timeout=15,
                )
                r.raise_for_status(); approval=str(r.json().get("approval_key") or "")
                if not approval: raise RuntimeError("KIS 승인 응답에 approval_key가 없습니다.")
            except Exception:
                self._schedule_auth_retry("approval")
                raise
            self._approval=approval; self._reset_auth_retry("approval"); return self._approval
    def get(self,path:str,tr_id:str,params:dict[str,Any])->dict[str,Any]:
        headers={"content-type":"application/json; charset=utf-8","authorization":f"Bearer {self.token()}","appkey":self.settings.kis_app_key,"appsecret":self.settings.kis_app_secret,"tr_id":tr_id,"custtype":"P","User-Agent":"KR-DayTrading-Terminal/3.0"}
        for attempt in range(2):
            self.rate_limit()
            r=self.session.get(f"{self.rest_base}{path}",headers=headers,params=params,timeout=15)
            if attempt==0 and r.status_code in {429,500,502,503,504}:
                time.sleep(.4)
                continue
            r.raise_for_status(); break
        p=r.json()
        if str(p.get("rt_cd","0"))!="0": raise RuntimeError(f"KIS 오류 {p.get('msg_cd')}: {p.get('msg1')}")
        return p
    def current_price(self,symbol:str)->dict[str,Any]:
        out=self.get("/uapi/domestic-stock/v1/quotations/inquire-price","FHKST01010100",{"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":symbol}).get("output",{})
        return {"symbol":symbol,"time":datetime.now(KST).isoformat(),"price":float(out.get("stck_prpr",0) or 0),"change":float(out.get("prdy_vrss",0) or 0),"change_rate":float(out.get("prdy_ctrt",0) or 0),"volume":int(float(out.get("acml_vol",0) or 0)),"source":"KIS REST","quality":"SNAPSHOT"}
    def asking_price(self,symbol:str)->dict[str,Any]:
        p=self.get("/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn","FHKST01010200",{"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":symbol})
        out=p.get("output1",{}) or {}; asks=[]; bids=[]
        for i in range(1,11):
            try: ap=float(out.get(f"askp{i}",0) or 0); aq=float(out.get(f"askp_rsqn{i}",0) or 0)
            except (TypeError,ValueError): ap=aq=0
            try: bp=float(out.get(f"bidp{i}",0) or 0); bq=float(out.get(f"bidp_rsqn{i}",0) or 0)
            except (TypeError,ValueError): bp=bq=0
            if ap>0: asks.append({"price":ap,"qty":aq})
            if bp>0: bids.append({"price":bp,"qty":bq})
        return {"asks":asks,"bids":bids,"source":"KIS 호가","quality":"SNAPSHOT"}

    def intraday_bars(self,symbol:str)->pd.DataFrame:
        rows=[]; seen=set(); now=datetime.now(KST)
        market_open=now.replace(hour=9,minute=0,second=0,microsecond=0)
        market_close=now.replace(hour=15,minute=30,second=0,microsecond=0)
        query=(market_close if now>=market_close else max(now,market_open)).strftime("%H%M%S")
        for _ in range(14):
            try:
                p=self.get("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice","FHKST03010200",{"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":symbol,"FID_INPUT_HOUR_1":query,"FID_PW_DATA_INCU_YN":"Y","FID_ETC_CLS_CODE":""})
            except Exception as exc:
                # The endpoint is paged.  A late transient failure must not
                # discard already received minute bars and force a full fallback.
                if rows:
                    logger.warning("KIS 분봉 일부 수신 후 중단 %s: %s",symbol,type(exc).__name__)
                    break
                raise
            batch=p.get("output2",[]) or []
            if not batch: break
            oldest=None
            for row in batch:
                h=str(row.get("stck_cntg_hour",""))
                if not h or h in seen: continue
                seen.add(h); rows.append(row); oldest=h if oldest is None or h<oldest else oldest
            if not oldest or oldest<="090000": break
            query=(datetime.strptime(oldest,"%H%M%S")-timedelta(minutes=1)).strftime("%H%M%S")
        today=datetime.now(KST).strftime("%Y-%m-%d"); rec=[]
        for row in rows:
            h=str(row.get("stck_cntg_hour","000000")); rec.append({"time":f"{today}T{h[:2]}:{h[2:4]}:{h[4:6]}+09:00","open":float(row.get("stck_oprc",0) or 0),"high":float(row.get("stck_hgpr",0) or 0),"low":float(row.get("stck_lwpr",0) or 0),"close":float(row.get("stck_prpr",0) or 0),"volume":int(float(row.get("cntg_vol",0) or 0))})
        return pd.DataFrame(rec).drop_duplicates("time").sort_values("time").reset_index(drop=True) if rec else pd.DataFrame()
    def historical_bars(self,symbol:str,days:int)->pd.DataFrame:
        """KIS daily OHLCV for the Naver-style week/month/quarter selectors."""
        end=datetime.now(KST).date(); start=end-timedelta(days=max(days*2,14))
        p=self.get("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice","FHKST03010100",{
            "FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":symbol,
            "FID_INPUT_DATE_1":start.strftime("%Y%m%d"),"FID_INPUT_DATE_2":end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE":"D","FID_ORG_ADJ_PRC":"0",
        })
        rec=[]
        for row in p.get("output2",[]) or []:
            date=str(row.get("stck_bsop_date","")).strip()
            if len(date)!=8: continue
            try:
                open_=float(row.get("stck_oprc",0) or 0); high=float(row.get("stck_hgpr",0) or 0)
                low=float(row.get("stck_lwpr",0) or 0); close=float(row.get("stck_clpr",0) or 0)
                volume=int(float(row.get("acml_vol",0) or 0))
            except (TypeError,ValueError):
                continue
            if close<=0: continue
            rec.append({"time":f"{date[:4]}-{date[4:6]}-{date[6:]}T15:30:00+09:00","open":open_,"high":high,"low":low,"close":close,"volume":volume})
        return pd.DataFrame(rec).drop_duplicates("time").sort_values("time").tail(days).reset_index(drop=True) if rec else pd.DataFrame()
    @staticmethod
    def _numeric(row:dict[str,Any],key:str)->float|None:
        value=row.get(key)
        if value in (None,""):return None
        try:return float(str(value).replace(",",""))
        except (TypeError,ValueError):return None
    @staticmethod
    def _market_value(value:Any,source:str,quality:str,direction:str|None=None,**metadata:Any)->dict[str,Any]:
        return {"value":value,"source":source,"quality":quality,"direction":direction,"metadata":metadata}
    def index_price(self,index_code:str)->dict[str,Any]:
        return self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            "FHPUP02100000",
            {"FID_COND_MRKT_DIV_CODE":"U","FID_INPUT_ISCD":index_code},
        ).get("output",{}) or {}
    def futures_board(self)->list[dict[str,Any]]:
        payload=self.get(
            "/uapi/domestic-futureoption/v1/quotations/display-board-futures",
            "FHPIF05030200",
            {
                "FID_COND_MRKT_DIV_CODE":"F",
                "FID_COND_SCR_DIV_CODE":"20503",
                "FID_COND_MRKT_CLS_CODE":"MKI",
            },
        )
        output=payload.get("output",[]) or []
        return output if isinstance(output,list) else [output]
    def market_context(self)->dict[str,Any]:
        """Read-only KIS index/derivatives context, cached to protect rate limits."""
        now_mono=time.monotonic()
        if self._market_cache and now_mono-self._market_cache_time<10:
            return dict(self._market_cache)
        result:dict[str,Any]={}
        index_defs={
            "0001":("kospi_index","kospi_change","kospi_breadth","KOSPI"),
            "1001":("kosdaq_index","kosdaq_change","kosdaq_breadth","KOSDAQ"),
            "2001":("kospi200_index","kospi200_change",None,"KOSPI200"),
        }
        for code,(price_key,change_key,breadth_key,name) in index_defs.items():
            try:
                row=self.index_price(code)
                price=self._numeric(row,"bstp_nmix_prpr")
                change=self._numeric(row,"bstp_nmix_prdy_ctrt")
                if price is not None:
                    result[price_key]=self._market_value(price,"KIS 국내업종 현재지수","LIVE")
                if change is not None:
                    result[change_key]=self._market_value(change,"KIS 국내업종 현재지수","LIVE","positive" if change>0 else "negative" if change<0 else "neutral")
                if breadth_key:
                    up=self._numeric(row,"ascn_issu_cnt")
                    down=self._numeric(row,"down_issu_cnt")
                    flat=self._numeric(row,"stnr_issu_cnt") or 0
                    total=(up or 0)+(down or 0)+flat
                    if up is not None and down is not None and total>0:
                        breadth=round(up/total*100,1)
                        result[breadth_key]=self._market_value(breadth,f"KIS {name} 상승·하락 종목수 계산","LIVE","positive" if breadth>=55 else "negative" if breadth<=45 else "neutral")
            except Exception as exc:
                logger.warning("%s 지수 조회 실패: %s",name,exc)
        try:
            rows=self.futures_board()
            candidates=[]
            for row in rows:
                name=str(row.get("hts_kor_isnm") or "")
                code=str(row.get("futs_shrn_iscd") or "")
                # KIS returns Mini KOSPI200 futures with codes such as A05608
                # and names such as "미니F 202608".  The former implementation
                # only accepted the regular-contract naming convention, which
                # discarded every valid board row for this account.
                regular_contract="200" in name or code.startswith("101")
                mini_kospi200_contract="미니F" in name or code.startswith("A056")
                if (regular_contract or mini_kospi200_contract) and self._numeric(row,"futs_prpr"):
                    remaining=self._numeric(row,"hts_rmnn_dynu")
                    contract_rank=0 if regular_contract else 1
                    candidates.append((contract_rank,remaining if remaining is not None and remaining>=0 else 99999,row))
            if candidates:
                _,_,row=min(candidates,key=lambda item:(item[0],item[1]))
                code=str(row.get("futs_shrn_iscd") or "")
                contract_name=str(row.get("hts_kor_isnm") or "KOSPI200 선물")
                is_mini="미니F" in contract_name or code.startswith("A056")
                source="KIS 국내선물 전광판" + (" / 미니 KOSPI200 선물" if is_mini else " / KOSPI200 선물")
                mapping={
                    "kospi200_futures_price":("futs_prpr","pt"),
                    "kospi200_futures_change":("futs_prdy_ctrt","%"),
                    "futures_open_interest":("hts_otst_stpl_qty","계약"),
                    "futures_volume":("acml_vol","계약"),
                }
                for feature_id,(field,_) in mapping.items():
                    value=self._numeric(row,field)
                    if value is not None:
                        result[feature_id]=self._market_value(value,source,"LIVE","positive" if feature_id.endswith("change") and value>0 else "negative" if feature_id.endswith("change") and value<0 else "neutral",contract_code=code,contract_name=contract_name)
                future_price=self._numeric(row,"futs_prpr")
                spot=self._numeric((result.get("kospi200_index") or {}),"value")
                if future_price is not None and spot is not None:
                    basis=round(future_price-spot,3)
                    result["futures_basis"]=self._market_value(basis,"KIS 선물 현재가 - KOSPI200 현물 계산","LIVE / 계산","positive" if basis>0 else "negative" if basis<0 else "neutral",contract_code=code,contract_name=contract_name)
                direct_oi_change=self._numeric(row,"otst_stpl_qty_icdc")
                current_oi=self._numeric(row,"hts_otst_stpl_qty")
                if direct_oi_change is not None:
                    result["futures_open_interest_change"]=self._market_value(direct_oi_change,source,"LIVE","positive" if direct_oi_change>0 else "negative" if direct_oi_change<0 else "neutral",contract_code=code,contract_name=contract_name)
                elif current_oi is not None:
                    previous_oi=self._futures_open_interest.get(code)
                    if previous_oi is not None:
                        oi_change=round(current_oi-previous_oi)
                        result["futures_open_interest_change"]=self._market_value(oi_change,"KIS 미결제약정 직전 수신값 대비 계산","LIVE / 직전 수신 대비","positive" if oi_change>0 else "negative" if oi_change<0 else "neutral",contract_code=code,contract_name=contract_name)
                    self._futures_open_interest[code]=current_oi
        except Exception as exc:
            logger.warning("KOSPI200 선물 전광판 조회 실패: %s",exc)
        now=datetime.now(KST)
        is_night=now.hour>=18 or now.hour<6
        is_day=(now.hour==8 and now.minute>=45) or 9<=now.hour<15 or (now.hour==15 and now.minute<=45)
        session="야간" if is_night else "주간" if is_day else "장외"
        result["krx_derivatives_session"]=self._market_value(session,"KRX 세션 시각 규칙","LIVE" if session!="장외" else "장외","neutral")
        # The domestic board keeps the last daytime Mini KOSPI200 quote after
        # close.  It is not an Eurex/KRX night-session feed, so never relabel
        # that stale value as a live night-futures observation.
        self._market_cache=dict(result); self._market_cache_time=now_mono
        return result
    def investor_estimate(self,symbol:str)->dict[str,Any]:
        try:
            p=self.get("/uapi/domestic-stock/v1/quotations/investor-trend-estimate","HHPTJ04160200",{"MKSC_SHRN_ISCD":symbol}); rows=p.get("output2",[]) or []; return rows[0] if rows else {}
        except Exception as e: logger.warning("투자자 추정 실패: %s",e); return {}
    def member_trend(self,symbol:str)->list[dict[str,Any]]:
        try:
            p=self.get("/uapi/domestic-stock/v1/quotations/frgnmem-trade-trend","FHPST04320000",{"FID_COND_SCR_DIV_CODE":"20432","FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":symbol,"FID_INPUT_ISCD_2":"99999","FID_MRKT_CLS_CODE":"A","FID_VOL_CNT":"0"}); return p.get("output2",[]) or []
        except Exception as e: logger.warning("회원사 동향 실패: %s",e); return []
    async def start(self,symbols:list[str])->None:
        if self.running:return
        self.running=True; self.task=asyncio.create_task(self.feed_loop(symbols))
    async def stop(self)->None:
        self.running=False
        if self.task:
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass
    async def feed_loop(self,symbols:list[str])->None:
        while self.running:
            try:
                approval=await asyncio.to_thread(self.approval)
                async with websockets.connect(self.ws_url,ping_interval=None,close_timeout=5) as ws:
                    for symbol in symbols:
                        for tr_id in ("H0STCNT0", "H0STMBC0"):
                            await ws.send(json.dumps({"header":{"approval_key":approval,"custtype":"P","tr_type":"1","content-type":"utf-8"},"body":{"input":{"tr_id":tr_id,"tr_key":symbol}}}))
                            await asyncio.sleep(.06)
                    while self.running:
                        raw=await asyncio.wait_for(ws.recv(),timeout=45)
                        if raw.startswith("0|"):
                            parts=raw.split("|",3)
                            if len(parts)==4 and parts[1]=="H0STCNT0":
                                v=parts[3].split("^")
                                if len(v)>=15:
                                    h=v[1]; now=datetime.now(KST); ts=now.replace(hour=int(h[:2]),minute=int(h[2:4]),second=int(h[4:6]),microsecond=0)
                                    self.latest_ticks[v[0]]={"symbol":v[0],"time":ts.isoformat(),"price":float(v[2] or 0),"change":float(v[4] or 0),"change_rate":float(v[5] or 0),"ask":float(v[10] or 0),"bid":float(v[11] or 0),"volume":int(float(v[12] or 0)),"acc_volume":int(float(v[13] or 0)),"source":"KIS WebSocket","quality":"LIVE"}
                            elif len(parts)==4 and parts[1]=="H0STMBC0":
                                v=parts[3].split("^")
                                if len(v)>=21:
                                    symbol=v[0]; merged={}
                                    for i in range(5):
                                        sell_name=(v[1+i] or "").strip(); buy_name=(v[6+i] or "").strip()
                                        try: sell_qty=float(v[11+i] or 0)
                                        except ValueError: sell_qty=0.0
                                        try: buy_qty=float(v[16+i] or 0)
                                        except ValueError: buy_qty=0.0
                                        if sell_name:
                                            row=merged.setdefault(sell_name,{"name":sell_name,"buy":0.0,"sell":0.0,"net":0.0,"quality":"LIVE/KIS 회원사"}); row["sell"]+=sell_qty
                                        if buy_name:
                                            row=merged.setdefault(buy_name,{"name":buy_name,"buy":0.0,"sell":0.0,"net":0.0,"quality":"LIVE/KIS 회원사"}); row["buy"]+=buy_qty
                                    for row in merged.values(): row["net"]=row["buy"]-row["sell"]
                                    self.latest_members[symbol]=sorted(merged.values(),key=lambda x:abs(x["net"]),reverse=True)
                        elif raw.startswith("{") and json.loads(raw).get("header",{}).get("tr_id")=="PINGPONG": await ws.send(raw)
            except asyncio.CancelledError: raise
            except Exception as e: logger.exception("KIS WebSocket 재접속: %s",e); await asyncio.sleep(3)


class ExternalStore:
    def __init__(self,settings:Settings):
        path=Path(settings.external_feature_file)
        # In a portable build, user-updatable feeds must live beside the EXE,
        # not inside PyInstaller's temporary read-only extraction directory.
        self.path=path if path.is_absolute() else ROOT/path
    def load(self,symbol:str)->dict[str,Any]:
        if not self.path.exists(): return {}
        try:
            p=json.loads(self.path.read_text(encoding="utf-8")); out=dict(p.get("global",{})); out.update(p.get("symbols",{}).get(symbol,{})); return out
        except Exception:return {}


class MarketProvider:
    def __init__(self,settings:Settings):
        self.settings=settings; self.demo=DemoProvider(); self.public=PublicMarketClient(); self.kis=KISClient(settings) if settings.kis_configured else None; self.external_store=ExternalStore(settings); self.live_frames={}
        self.peer_cache:dict[str,tuple[float,dict[str,Any]]]={}
    @property
    def mode(self)->str:
        if self.settings.data_mode=="kis" and self.kis:return "kis"
        if self.settings.data_mode=="demo":return "demo"
        return "public"
    async def startup(self)->None:
        if self.mode=="kis" and self.kis and self.settings.kis_enable_ws: await self.kis.start([s["symbol"] for s in STOCKS])
    async def shutdown(self)->None:
        if self.kis: await self.kis.stop()
    def get_bars(self,symbol:str,days:int=5)->tuple[pd.DataFrame,str,str]:
        if self.mode=="kis" and self.kis:
            try:
                frame=self.kis.intraday_bars(symbol)
                if not frame.empty:self.live_frames[symbol]=frame;return frame,"KIS","LIVE/당일"
            except Exception as e:logger.exception("KIS 분봉 실패, 공개 시세 대체: %s",e)
        if self.mode!="demo":
            frame=self.public.intraday_bars(symbol,days)
            if not frame.empty:
                self.live_frames[symbol]=frame
                return frame,"네이버 금융 공개 분봉","실제 1분 종가·누적량 / OHLC 파생"
            raise RuntimeError("공개 분봉 응답이 비어 있습니다.")
        return self.demo.get_bars(symbol,days),"DEMO","SIMULATED"
    def get_chart(self,symbol:str,chart_range:str="1d")->tuple[pd.DataFrame,str,str]:
        """Serve one coherent series per selected time range."""
        chart_range=chart_range if chart_range in {"1d","1w","1mo","3mo"} else "1d"
        requested_days={"1d":1,"1w":5,"1mo":22,"3mo":66}[chart_range]
        if self.mode=="kis" and self.kis:
            try:
                if chart_range=="1d":
                    frame=self.kis.intraday_bars(symbol)
                    if not frame.empty:
                        self.live_frames[symbol]=frame
                        return frame,"KIS","LIVE / 1분"
                else:
                    frame=self.kis.historical_bars(symbol,requested_days)
                    if not frame.empty:
                        return frame,"KIS","LIVE / 일봉"
                raise RuntimeError("KIS returned no chart bars")
            except Exception as exc:
                logger.exception("KIS 차트 조회 실패 (%s), 공개 시세 대체", chart_range)
        if self.mode!="demo":
            if chart_range in {"1d","1w"}:
                frame=self.public.intraday_bars(symbol,1 if chart_range=="1d" else 5)
                if not frame.empty:
                    self.live_frames[symbol]=frame
                    source="네이버 금융 공개 분봉" if self.mode=="public" else "KIS 오류 → 네이버 공개 분봉"
                    return frame,source,"실제 1분 종가·누적량 / OHLC 파생"
            else:
                frame=self.public.historical_bars(symbol,requested_days)
                if not frame.empty:
                    source="네이버 금융 공개 일봉" if self.mode=="public" else "KIS 오류 → 네이버 공개 일봉"
                    return frame,source,"실제 OHLCV / 일봉"
            raise RuntimeError("공개 차트 응답이 비어 있습니다.")
        return self.demo.chart_bars(symbol,chart_range),"DEMO","SIMULATED"
    def get_tick(self,symbol:str)->dict[str,Any]:
        if self.mode=="kis" and self.kis:
            tick=self.kis.latest_ticks.get(symbol)
            if not tick:
                try:tick=self.kis.current_price(symbol)
                except Exception as e:
                    logger.warning("KIS 현재가 실패, 공개 시세 대체 %s: %s",symbol,e)
                    try:tick=self.public.current_price(symbol)
                    except Exception as public_exc:return {"symbol":symbol,"time":datetime.now(KST).isoformat(),"error":str(public_exc),"source":"KIS/공개 시세","quality":"ERROR"}
            now=time.monotonic()
            if now-self.kis.quote_times.get(symbol,0)>=1.0:
                try:
                    self.kis.latest_quotes[symbol]=self.kis.asking_price(symbol); self.kis.quote_times[symbol]=now
                except Exception as e: logger.debug("KIS 호가 조회 실패: %s",e)
            if symbol in self.kis.latest_quotes: tick={**tick,"orderbook":self.kis.latest_quotes[symbol]}
            self.update_live_frame(tick);return tick
        if self.mode=="public":
            try:
                tick=self.public.current_price(symbol);self.update_live_frame(tick);return tick
            except Exception as exc:
                return {"symbol":symbol,"time":datetime.now(KST).isoformat(),"error":str(exc),"source":"공개 시세","quality":"ERROR"}
        return self.demo.tick(symbol)
    def update_live_frame(self,tick:dict[str,Any])->None:
        symbol=tick["symbol"]; frame=self.live_frames.get(symbol); price=float(tick.get("price") or 0)
        if frame is None or frame.empty or price<=0:return
        ts=pd.Timestamp(tick["time"]).floor("min"); last=pd.Timestamp(frame.iloc[-1]["time"]).floor("min")
        if ts==last:
            i=frame.index[-1];frame.at[i,"high"]=max(float(frame.at[i,"high"]),price);frame.at[i,"low"]=min(float(frame.at[i,"low"]),price);frame.at[i,"close"]=price;frame.at[i,"volume"]=int(frame.at[i,"volume"])+int(tick.get("volume") or 0)
        elif ts>last:
            prev=float(frame.iloc[-1]["close"]);frame=pd.concat([frame,pd.DataFrame([{"time":ts.isoformat(),"open":prev,"high":max(prev,price),"low":min(prev,price),"close":price,"volume":int(tick.get("volume") or 0)}])],ignore_index=True).tail(2500)
        self.live_frames[symbol]=frame
    def current_frame(self,symbol:str)->pd.DataFrame:
        if self.mode=="demo":return self.demo.get_bars(symbol)
        frame=self.live_frames.get(symbol,pd.DataFrame()).copy()
        if not frame.empty:return frame
        try:
            frame=self.public.intraday_bars(symbol,5);self.live_frames[symbol]=frame;return frame.copy()
        except Exception:return pd.DataFrame()
    @staticmethod
    def _volume_ratio(frame:pd.DataFrame)->float|None:
        if frame is None or frame.empty or "volume" not in frame:return None
        volume=pd.to_numeric(frame["volume"],errors="coerce").dropna()
        if volume.empty:return None
        mean=float(volume.tail(20).mean() or 0)
        return float(volume.iloc[-1]/mean) if mean>0 else None
    def trader_context(self,symbol:str,profile:dict[str,Any])->tuple[dict[str,Any],list[dict[str,Any]]]:
        """Return market and domestic-peer facts without synthesizing KIS gaps."""
        context=self.external_store.load(symbol)
        peer_symbols=list(dict.fromkeys(str(value) for value in profile.get("peers_domestic",[]) if str(value)!=symbol))
        sector=STOCK_BY_SYMBOL.get(symbol,{}).get("sector")
        peer_symbols.extend(
            item["symbol"] for item in STOCKS
            if item["symbol"]!=symbol and item.get("sector")==sector and item["symbol"] not in peer_symbols
        )
        peer_rows:list[dict[str,Any]]=[]
        if self.mode=="demo":
            selected_frame=self.demo.get_bars(symbol)
            seed=self.demo.seed(symbol)+int(time.time()//300)
            rng=random.Random(seed)
            base_change=(
                (float(selected_frame.iloc[-1]["close"])/float(selected_frame.iloc[-2]["close"])-1)*100
                if len(selected_frame)>1 else 0.0
            )
            def simulated(value:Any,direction:str|None=None)->dict[str,Any]:
                return {"value":value,"source":"DEMO 시장 생성","quality":"SIMULATED","direction":direction}
            kospi=round(base_change*.35+rng.gauss(0,.35),3)
            kosdaq=round(base_change*.45+rng.gauss(0,.5),3)
            future=round(kospi+rng.gauss(0,.18),3)
            now=datetime.now(KST); is_night=now.hour>=18 or now.hour<6
            context.update({
                "kospi_index":simulated(round(2750+rng.gauss(0,15),2)),
                "kospi_change":simulated(kospi,"positive" if kospi>0 else "negative"),
                "kosdaq_index":simulated(round(900+rng.gauss(0,8),2)),
                "kosdaq_change":simulated(kosdaq,"positive" if kosdaq>0 else "negative"),
                "kospi200_index":simulated(round(370+rng.gauss(0,3),2)),
                "kospi200_change":simulated(kospi,"positive" if kospi>0 else "negative"),
                "kospi_breadth":simulated(round(max(5,min(95,50+kospi*18)),1)),
                "kosdaq_breadth":simulated(round(max(5,min(95,50+kosdaq*16)),1)),
                "kospi200_futures_price":simulated(round(371+rng.gauss(0,3),2)),
                "kospi200_futures_change":simulated(future,"positive" if future>0 else "negative"),
                "futures_basis":simulated(round(rng.gauss(.15,.35),3)),
                "futures_open_interest":simulated(rng.randint(160000,310000)),
                "futures_open_interest_change":simulated(rng.randint(-8000,8000)),
                "futures_volume":simulated(rng.randint(50000,260000)),
                "krx_derivatives_session":simulated("야간" if is_night else "주간/장외"),
            })
            if is_night:context["night_futures_change"]=simulated(future,"positive" if future>0 else "negative")
            for peer_symbol in peer_symbols:
                tick=self.demo.tick(peer_symbol); peer_frame=self.demo.get_bars(peer_symbol)
                peer_rows.append({**tick,"name":STOCK_BY_SYMBOL.get(peer_symbol,{}).get("name",peer_symbol),"volume_ratio":self._volume_ratio(peer_frame)})
            return context,peer_rows
        if self.mode=="kis" and self.kis:
            # KIS does not publish every market-context field (program flow,
            # FX and global prices).  Fill only blank user values from public
            # read-only feeds, then let KIS overwrite overlapping domestic data.
            try:
                for feature_id,raw in self.public.market_context().items():
                    current=context.get(feature_id)
                    current_value=current.get("value") if isinstance(current,dict) else current
                    if current_value is None:context[feature_id]=raw
            except Exception as exc:
                logger.debug("KIS public-market supplement failed: %s",type(exc).__name__)
            context.update(self.kis.market_context())
            for peer_symbol in peer_symbols[:6]:
                tick=self.kis.latest_ticks.get(peer_symbol)
                cached=self.peer_cache.get(peer_symbol)
                if not tick and cached and time.monotonic()-cached[0]<10:
                    tick=cached[1]
                if not tick:
                    try:
                        tick=self.kis.current_price(peer_symbol)
                        self.peer_cache[peer_symbol]=(time.monotonic(),tick)
                    except Exception as exc:
                        logger.debug("동종주 현재가 조회 실패 %s: %s",peer_symbol,exc)
                        continue
                peer_frame=self.live_frames.get(peer_symbol,pd.DataFrame())
                peer_rows.append({**tick,"name":STOCK_BY_SYMBOL.get(peer_symbol,{}).get("name",peer_symbol),"volume_ratio":self._volume_ratio(peer_frame)})
        elif self.mode=="public":
            context.update(self.public.market_context())
            for peer_symbol in peer_symbols[:6]:
                cached=self.peer_cache.get(peer_symbol)
                tick=cached[1] if cached and time.monotonic()-cached[0]<7 else None
                if not tick:
                    try:
                        tick=self.public.current_price(peer_symbol)
                        self.peer_cache[peer_symbol]=(time.monotonic(),tick)
                    except Exception as exc:
                        logger.debug("공개 동종주 현재가 조회 실패 %s: %s",peer_symbol,exc)
                        continue
                peer_frame=self.live_frames.get(peer_symbol,pd.DataFrame())
                peer_rows.append({**tick,"name":STOCK_BY_SYMBOL.get(peer_symbol,{}).get("name",peer_symbol),"volume_ratio":self._volume_ratio(peer_frame)})
        return context,peer_rows
    @staticmethod
    def find_num(row:dict[str,Any],keys:list[str])->float|None:
        for k in keys:
            if row.get(k) not in (None,""):
                try:return float(str(row[k]).replace(",",""))
                except ValueError:pass
        return None
    def external_features(self,symbol:str,frame:pd.DataFrame)->tuple[dict[str,Any],list[dict[str,Any]]]:
        if self.mode=="demo":return self.demo.external(symbol,frame),self.demo.members(symbol)
        ext=self.external_store.load(symbol); members=[]
        if self.mode=="kis" and self.kis:
            # KIS covers domestic execution data.  Public sources remain the
            # intended read-only supplier for global indices, commodities,
            # FX, FRED rates and Naver program-flow data; without this merge
            # all of those live fields appeared disconnected in KIS mode.
            try:
                for feature_id,raw in self.public.market_context().items():
                    value=raw.get("value") if isinstance(raw,dict) else raw
                    existing=ext.get(feature_id)
                    existing_value=existing.get("value") if isinstance(existing,dict) else existing
                    if value is not None and existing_value is None:
                        ext[feature_id]=raw
            except Exception as exc:
                logger.debug("KIS 모드 공개 거시 피드 병합 실패: %s",type(exc).__name__)
            row=self.kis.investor_estimate(symbol)
            foreign_qty=self.find_num(row,["frgn_fake_ntby_qty","frgn_ntby_qty","frgn_estm_ntby_qty"])
            inst_qty=self.find_num(row,["orgn_fake_ntby_qty","orgn_ntby_qty","orgn_estm_ntby_qty"])
            price=float(frame.iloc[-1]["close"]) if not frame.empty else float(self.kis.latest_ticks.get(symbol,{}).get("price") or 0)
            # Naver provides individual flow, streaks, acceleration and
            # turnover that the KIS intraday estimate does not include.  The
            # KIS foreign/institution values below remain authoritative.
            try:
                for feature_id,raw in self.public.investor_features(symbol,price).items():
                    current=ext.get(feature_id)
                    current_value=current.get("value") if isinstance(current,dict) else current
                    if current_value is None:ext[feature_id]=raw
            except Exception as exc:
                logger.debug("KIS public-investor supplement failed: %s",type(exc).__name__)
            foreign=(foreign_qty*price) if foreign_qty is not None and price>0 else None
            inst=(inst_qty*price) if inst_qty is not None and price>0 else None
            if foreign is not None:ext["foreign_net"]={"value":round(foreign),"source":"KIS 외국인 가집계 수량×현재가","quality":"장중 지정시각/원 환산","direction":"positive" if foreign>0 else "negative"}
            if inst is not None:ext["institution_net"]={"value":round(inst),"source":"KIS 기관 가집계 수량×현재가","quality":"장중 지정시각/원 환산","direction":"positive" if inst>0 else "negative"}
            if foreign is not None and inst is not None:
                ext["joint_buy"]={"value":"예" if foreign>0 and inst>0 else "아니오","source":"KIS 계산","quality":"장중 지정시각"};ext["joint_sell"]={"value":"예" if foreign<0 and inst<0 else "아니오","source":"KIS 계산","quality":"장중 지정시각"}
            members=list(self.kis.latest_members.get(symbol,[]))
        else:
            price=float(frame.iloc[-1]["close"]) if not frame.empty else 0.0
            try:ext.update(self.public.market_context())
            except Exception as exc:logger.debug("공개 거시 피드 실패: %s",exc)
            try:ext.update(self.public.investor_features(symbol,price))
            except Exception as exc:logger.warning("공개 투자자 수급 실패 %s: %s",symbol,exc)
            try:members=self.public.members(symbol)
            except Exception as exc:logger.warning("공개 회원사 흐름 실패 %s: %s",symbol,exc)
        return ext,sorted(members,key=lambda x:abs(x["net"]),reverse=True)
