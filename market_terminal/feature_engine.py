from __future__ import annotations

import math
from typing import Any
import numpy as np
import pandas as pd


def fv(key: str, label: str, value: Any, unit: str = "", direction: str = "neutral",
       source: str = "계산", quality: str = "LIVE") -> dict[str, Any]:
    return {"key":key,"label":label,"value":value,"unit":unit,"direction":direction,"source":source,"quality":quality}


def direction(value: float | None, pos: float = 0, neg: float = 0) -> str:
    if value is None: return "neutral"
    if value > pos: return "positive"
    if value < neg: return "negative"
    return "neutral"


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100/(1+rs)).fillna(50)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-prev).abs(), (df["low"]-prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean().bfill()


def streak(returns: pd.Series) -> int:
    if returns.empty: return 0
    sign = int(np.sign(returns.iloc[-1]))
    if sign == 0: return 0
    count = 0
    for value in reversed(returns.tolist()):
        if int(np.sign(value)) == sign: count += 1
        else: break
    return count * sign


def compute_technical_features(frame: pd.DataFrame, market_returns: pd.Series | None = None,
                               shares_outstanding: float | None = None) -> tuple[dict[str,list[dict[str,Any]]],dict[str,Any]]:
    required = {"time","open","high","low","close","volume"}
    if frame.empty or not required.issubset(frame.columns): return {}, {}
    df = frame.copy().sort_values("time").reset_index(drop=True)
    for col in ("open","high","low","close","volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    if len(df) < 5: return {}, {}
    close, volume = df["close"], df["volume"]
    returns = close.pct_change().replace([np.inf,-np.inf],np.nan).fillna(0)
    logret = np.log(close/close.shift(1)).replace([np.inf,-np.inf],np.nan).fillna(0)
    sma5, sma20, sma60 = close.rolling(5,1).mean(), close.rolling(20,1).mean(), close.rolling(60,1).mean()
    ema9, ema21 = close.ewm(span=9,adjust=False).mean(), close.ewm(span=21,adjust=False).mean()
    rsi14, atr14 = rsi(close), atr(df)
    vmean, vstd = volume.rolling(20,1).mean(), volume.rolling(20,2).std().replace(0,np.nan)
    vz = ((volume-vmean)/vstd).fillna(0)
    value = close*volume
    valmean, valstd = value.rolling(20,1).mean(), value.rolling(20,2).std().replace(0,np.nan)
    valuez = ((value-valmean)/valstd).fillna(0)
    amihud = (returns.abs()/value.replace(0,np.nan)).rolling(20,5).mean()*1e9
    idx, px = len(df)-1, float(close.iloc[-1])
    ret = {n: float(close.pct_change(n).iloc[-1])*100 if len(df)>n else 0 for n in (1,3,5,10,20,60)}
    high20, low20 = df["high"].tail(20).max(), df["low"].tail(20).min()
    peak = close.tail(60).cummax(); mdd = float((close.tail(60)/peak-1).min()*100)
    full_range = max(float(df["high"].iloc[-1]-df["low"].iloc[-1]),1e-9)
    body = float(close.iloc[-1]-df["open"].iloc[-1])
    upper = float(df["high"].iloc[-1]-max(close.iloc[-1],df["open"].iloc[-1]))/full_range*100
    lower = float(min(close.iloc[-1],df["open"].iloc[-1])-df["low"].iloc[-1])/full_range*100
    gap = (float(df["open"].iloc[-1])/float(close.iloc[-2])-1)*100 if len(df)>1 else 0
    realized = float(logret.tail(20).std()*math.sqrt(252*390)*100)
    downside = float(logret.where(logret<0,0).tail(20).std()*math.sqrt(252*390)*100)
    skew, kurt = float(logret.tail(60).skew()), float(logret.tail(60).kurt())
    turnover = float(volume.iloc[-1]/shares_outstanding*100) if shares_outstanding else None
    beta = corr = idio = None
    if market_returns is not None:
        aligned = pd.concat([returns.tail(60).rename("a"),market_returns.tail(60).rename("m")],axis=1).dropna()
        if len(aligned)>=10 and aligned["m"].var()>0:
            beta = float(aligned.cov().iloc[0,1]/aligned["m"].var()); corr=float(aligned.corr().iloc[0,1])
            idio=float((aligned["a"]-beta*aligned["m"]).std()*math.sqrt(252*390)*100)
    t = pd.Timestamp(df["time"].iloc[-1])
    groups = {
        "가격·추세":[
            fv("ret_1","1분 수익률",round(ret[1],3),"%",direction(ret[1],.02,-.02)),
            fv("ret_3","3분 수익률",round(ret[3],3),"%",direction(ret[3],.04,-.04)),
            fv("ret_5","5분 수익률",round(ret[5],3),"%",direction(ret[5],.05,-.05)),
            fv("ret_20","20분 수익률",round(ret[20],3),"%",direction(ret[20],.1,-.1)),
            fv("sma20_gap","SMA20 이격",round((px/sma20.iloc[-1]-1)*100,3),"%",direction(px/sma20.iloc[-1]-1,.001,-.001)),
            fv("ema_gap","EMA9-21",round((ema9.iloc[-1]/ema21.iloc[-1]-1)*100,3),"%",direction(ema9.iloc[-1]/ema21.iloc[-1]-1,.0005,-.0005)),
            fv("rsi14","RSI(14)",round(float(rsi14.iloc[-1]),2),"","negative" if rsi14.iloc[-1]>75 else "positive" if rsi14.iloc[-1]<30 else "neutral"),
            fv("atr14_pct","ATR(14)",round(float(atr14.iloc[-1]/px*100),3),"%","warning" if atr14.iloc[-1]/px>.01 else "neutral"),
            fv("high_distance","20봉 고점 거리",round((px/high20-1)*100,3),"%","negative" if px/high20-1<-.02 else "neutral"),
            fv("low_distance","20봉 저점 거리",round((px/low20-1)*100,3),"%","positive" if px/low20-1>.02 else "neutral"),
            fv("max_drawdown","60봉 최대낙폭",round(mdd,3),"%","negative")],
        "연속성·캔들":[
            fv("streak","연속 상승/하락",streak(returns),"봉",direction(streak(returns))),
            fv("up_ratio","20봉 상승 비율",round(float((returns.tail(20)>0).mean()*100),1),"%",direction(float((returns.tail(20)>0).mean())-.5,.08,-.08)),
            fv("gap","시가 갭",round(gap,3),"%",direction(gap,.2,-.2)),
            fv("range","현재봉 장중범위",round(full_range/px*100,3),"%","warning" if full_range/px>.015 else "neutral"),
            fv("upper_wick","윗꼬리 비율",round(upper,1),"%","negative" if upper>55 else "neutral"),
            fv("lower_wick","아랫꼬리 비율",round(lower,1),"%","positive" if lower>55 else "neutral"),
            fv("candle_body","캔들 몸통",round(body/px*100,3),"%",direction(body))],
        "변동성·꼬리":[
            fv("realized_vol","실현변동성",round(realized,2),"%","warning" if realized>50 else "neutral"),
            fv("downside_vol","하방변동성",round(downside,2),"%","negative" if downside>realized*.7 else "neutral"),
            fv("skew","왜도",round(skew,3),"","negative" if skew<-.7 else "positive" if skew>.7 else "neutral"),
            fv("kurtosis","첨도",round(kurt,3),"","warning" if kurt>3 else "neutral"),
            fv("idio_vol","고유변동성",None if idio is None else round(idio,2),"%","warning" if idio and idio>35 else "neutral",quality="시장데이터 필요" if idio is None else "LIVE")],
        "거래량·유동성":[
            fv("volume_ratio","거래량/20봉 평균",round(float(volume.iloc[-1]/max(vmean.iloc[-1],1)),2),"배"),
            fv("volume_z","거래량 z-score",round(float(vz.iloc[-1]),2),"","warning" if abs(vz.iloc[-1])>2 else "neutral"),
            fv("value_z","거래대금 z-score",round(float(valuez.iloc[-1]),2),"","warning" if abs(valuez.iloc[-1])>2 else "neutral"),
            fv("trade_value","현재봉 거래대금",round(float(value.iloc[-1])),"원"),
            fv("turnover","회전율",None if turnover is None else round(turnover,5),"%",quality="종목정보 필요" if turnover is None else "LIVE"),
            fv("amihud","Amihud",round(float(amihud.iloc[-1]) if pd.notna(amihud.iloc[-1]) else 0,5),"")],
        "민감도·상호작용":[
            fv("global_beta","해외시장 rolling beta",None if beta is None else round(beta,3),"",quality="시장데이터 필요" if beta is None else "LIVE"),
            fv("global_corr","해외시장 correlation",None if corr is None else round(corr,3),"",quality="시장데이터 필요" if corr is None else "LIVE"),
            fv("crash_sensitivity","급락×민감도",None if beta is None else round(beta*min(ret[20],0),3),"",quality="시장데이터 필요" if beta is None else "LIVE")],
        "횡단면·달력":[
            fv("cross_section_pct","시장 내 백분위",None,"%",quality="전종목 데이터 필요"),
            fv("weekday","요일",t.strftime("%a")),fv("month","월",int(t.month),"월"),
            fv("quarter","분기",int((t.month-1)//3+1),"Q"),
            fv("month_end","월말 여부","예" if t.is_month_end else "아니오"),
            fv("quarter_end","분기말 여부","예" if t.is_quarter_end else "아니오")]
    }
    flat = {item["key"]:item["value"] for items in groups.values() for item in items}
    flat.update({"price":px,"time":t.isoformat(),"sma5":float(sma5.iloc[-1]),"sma20":float(sma20.iloc[-1]),
                 "sma60":float(sma60.iloc[-1]),"ema9":float(ema9.iloc[-1]),"ema21":float(ema21.iloc[-1])})
    return groups, flat


EXTERNAL_GROUPS: dict[str,list[tuple[str,str,str]]] = {
    "외국인·기관·개인":[("foreign_net","외국인 순매수","원"),("institution_net","기관 순매수","원"),("individual_net","개인 순매수","원"),("foreign_streak","외국인 연속 수급","일"),("institution_streak","기관 연속 수급","일"),("flow_to_value","거래대금 대비 수급","%")],
    "외국인+기관 결합":[("joint_buy","동시매수",""),("joint_sell","동시매도",""),("price_flow_divergence","가격-수급 괴리",""),("flow_acceleration","수급가속도","원/일")],
    "공매도":[("short_ratio","공매도 거래비중","%"),("short_balance_to_cap","공매도 잔고/시총","%"),("short_cumulative","공매도 누적량","주"),("price_short_divergence","가격-잔고 괴리","")],
    "시장 breadth":[("decline_ratio","하락종목 비율","%"),("crash_ratio","급락종목 비율","%"),("top50_contagion","상위50 전염","%")],
    "업종·주요주 전염":[("sector_crash_ratio","업종 급락비율","%"),("samsung_contagion","삼성전자 급락","%"),("hynix_contagion","SK하이닉스 급락","%"),("hyundai_contagion","현대차 급락","%")],
    "글로벌 증시":[("sp500","S&P500","%"),("dow","다우","%"),("nasdaq","NASDAQ","%"),("europe","유럽","%"),("nikkei","일본","%"),("china","중국","%"),("hangseng","홍콩","%"),("india","인도","%"),("vix","VIX","")],
    "코인·원자재":[("btc","BTC","%"),("eth","ETH","%"),("gold","금","%"),("oil","유가","%"),("copper","구리","%"),("natural_gas_price","천연가스",""),("dollar_index","달러 인덱스","")],
    "부동산":[("vnq","VNQ","%"),("xlre","XLRE","%"),("iyr","IYR","%"),("xhb","XHB","%"),("itb","ITB","%"),("krx_construction","KRX 건설","%"),("krx_reit","KRX 리츠","%"),("ecos_house","ECOS 주택가격","%")],
    "금리·환율·신용":[("us2y","미국 2Y","%"),("us10y","미국 10Y","%"),("yield_curve","미 장단기차","%p"),("fed_rate","연준금리","%"),("bok_rate","한국 기준금리","%"),("hy_spread","HY 스프레드","%p"),("usdkrw","USD/KRW",""),("vix","VIX","")],
    "정책 이벤트":[("fomc_distance","FOMC까지","거래일"),("bok_distance","한국은행 결정까지","거래일"),("policy_window","1·3·5·10일 이벤트 창","")],
    "뉴스":[("news_count","기업·시장 기사량","건"),("negative_news","부정어 점수",""),("risk_words","위험어 점수",""),("source_diversity","출처 다양성","곳"),("news_burst","기사량 burst","z"),("gdelt_count","GDELT 뉴스량","건")],
    "DART":[("dart_rights_issue","유상증자",""),("dart_capital_reduction","감자",""),("dart_cb_bw","CB/BW",""),("dart_major_holder","최대주주 변경",""),("dart_embezzlement","횡령",""),("dart_default","부도·회생",""),("dart_audit","감사의견",""),("dart_lawsuit","소송","")],
    "재무":[("debt_ratio","부채비율","%"),("current_ratio","유동비율","%"),("cash_assets","현금/자산","%"),("borrow_assets","차입금/자산","%"),("operating_margin","영업마진","%"),("operating_cashflow","영업현금흐름","원"),("rate_debt_interaction","금리×부채","")]
}


def merge_external_features(external: dict[str,Any], source_mode: str) -> tuple[dict[str,list[dict[str,Any]]],dict[str,Any]]:
    groups, flat = {}, {}
    for group, defs in EXTERNAL_GROUPS.items():
        items=[]
        for key,label,unit in defs:
            raw=external.get(key); value=raw.get("value") if isinstance(raw,dict) else raw
            source=raw.get("source","외부 피드") if isinstance(raw,dict) else "외부 피드"
            quality=raw.get("quality",source_mode) if isinstance(raw,dict) else source_mode
            direct=raw.get("direction") if isinstance(raw,dict) else None
            if value is None: quality="미연결"
            if direct is None:
                try: direct=direction(float(value))
                except (TypeError,ValueError): direct="neutral"
            item=fv(key,label,value,unit,direct,source,quality)
            if isinstance(raw,dict):
                for metadata_key in ("metadata","observed_at","collected_at","freshness","reliability","source_type"):
                    if metadata_key in raw:item[metadata_key]=raw[metadata_key]
            items.append(item); flat[key]=value
        groups[group]=items
    return groups, flat
