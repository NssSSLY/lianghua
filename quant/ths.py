"""同花顺短线数据：涨停池（含涨停原因）、连板天梯、个股热榜、热门板块。

接口为同花顺网页数据接口（非官方），请低频访问、仅作个人研究。
- 涨停池 / 连板天梯：可按日期查询，约能回查一年 → 缓存 data/ths/limitup/ 与 data/ths/ladder/
- 热榜 / 热门板块：只有实时数据 → 每日归档到 data/archive/日期/ths_hot_*.csv

涨停原因 reason_type 形如「人形机器人+减速器+业绩增长」，是当天写下的题材标签，没有前视偏差，
用来做按当时数据的题材识别（见 themes()）。
"""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Referer": "https://data.10jqka.com.cn/"}
NOPROXY = {"http": None, "https": None}
POOL_FIELDS = "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004"


def _get(url, params, retries=3):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=H, timeout=10, proxies=NOPROXY)
            return r.json()
        except Exception:
            time.sleep(1 + i * 2)
    return None


def _ts(x):
    """同花顺返回的封板时间是 unix 秒，转成 HH:MM:SS（北京时间）。"""
    try:
        return pd.Timestamp(int(x), unit="s", tz="Asia/Shanghai").strftime("%H:%M:%S")
    except Exception:
        return None


def _boards(high_days: str):
    """'首板'→(1,1)；'5天3板'→(5,3)；'6天6板'→(6,6)。返回 (天数, 板数)。"""
    if not high_days:
        return (np.nan, np.nan)
    if high_days == "首板":
        return (1, 1)
    try:
        d, b = high_days.replace("板", "").split("天")
        return (int(d), int(b))
    except Exception:
        return (np.nan, np.nan)


# ---------------------------------------------------------------- 涨停池（含涨停原因）
def fetch_limitup(date) -> tuple[pd.DataFrame | None, dict | None]:
    d = pd.Timestamp(date).strftime("%Y%m%d")
    rows, stats, page = [], None, 1
    while True:
        j = _get("https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool",
                 dict(page=page, limit=200, field=POOL_FIELDS, filter="HS,GEM2STAR", order_field="330324",
                      order_type=0, date=d))
        if not j or j.get("data") is None:
            return (None, None) if page == 1 else (pd.DataFrame(rows), stats)
        data = j["data"]
        stats = {k: v for k, v in data.items() if k in ("limit_up_count", "limit_down_count")}
        for x in data.get("info") or []:
            days, boards = _boards(x.get("high_days"))
            rows.append(dict(
                code=x["code"], name=x["name"], reason=x.get("reason_type") or "", high_days=x.get("high_days"),
                days=days, boards=boards, type=x.get("limit_up_type"), open_num=x.get("open_num"),
                first_time=_ts(x.get("first_limit_up_time")), last_time=_ts(x.get("last_limit_up_time")),
                order_amount=x.get("order_amount"), order_volume=x.get("order_volume"),
                suc_rate=x.get("limit_up_suc_rate"), float_mv=x.get("currency_value"),
                pct=x.get("change_rate"), turnover=x.get("turnover_rate"), price=x.get("latest"),
                is_new=x.get("is_new"), is_again=x.get("is_again_limit")))
        if page >= (data.get("page") or {}).get("count", 1):
            break
        page += 1
        time.sleep(0.3)
    df = pd.DataFrame(rows, columns=["code", "name", "reason", "high_days", "days", "boards", "type", "open_num",
                                     "first_time", "last_time", "order_amount", "order_volume", "suc_rate",
                                     "float_mv", "pct", "turnover", "price", "is_new", "is_again"])
    df.insert(0, "date", pd.Timestamp(date).strftime("%Y-%m-%d"))
    return df, stats


def fetch_ladder(date) -> list | None:
    d = pd.Timestamp(date).strftime("%Y%m%d")
    j = _get("https://data.10jqka.com.cn/dataapi/limit_up/continuous_limit_up", dict(filter="HS,GEM2STAR", date=d))
    if not j or j.get("data") is None:
        return None
    return [{"height": g["height"], "stocks": [{"code": s["code"], "name": s["name"]} for s in g["code_list"]]}
            for g in j["data"]]


def limitup_dir(data_dir) -> Path:
    p = Path(data_dir) / "ths" / "limitup"
    p.mkdir(parents=True, exist_ok=True)
    return p


def archive_day(data_dir, date, force=False) -> str:
    """把某天的同花顺涨停池 + 统计 + 连板天梯存到 data/ths/。返回状态文字。"""
    D = pd.Timestamp(date)
    now = pd.Timestamp.now()
    if D.normalize() >= now.normalize() and now.hour * 60 + now.minute < 15 * 60 + 30:
        return "未收盘"  # 盘中的涨停池不完整，不缓存
    f = limitup_dir(data_dir) / f"{D:%Y%m%d}.csv"
    lad = Path(data_dir) / "ths" / "ladder" / f"{D:%Y%m%d}.json"
    lad.parent.mkdir(parents=True, exist_ok=True)
    if f.exists() and lad.exists() and not force:
        return "已存在"
    df, stats = fetch_limitup(D)
    if df is None:
        return "无数据"
    df.to_csv(f, index=False, encoding="utf-8-sig")
    ladder = fetch_ladder(D) or []
    lad.write_text(json.dumps({"stats": stats, "ladder": ladder}, ensure_ascii=False), encoding="utf-8")
    return f"{len(df)}只"


def load_limitup(data_dir, start=None, end=None) -> pd.DataFrame:
    frames = []
    for p in sorted(limitup_dir(data_dir).glob("*.csv")):
        d = pd.Timestamp(p.stem)
        if (start and d < pd.Timestamp(start)) or (end and d > pd.Timestamp(end)):
            continue
        if p.stat().st_size < 10:
            continue
        df = pd.read_csv(p, dtype={"code": str})
        if len(df):
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["date", "code", "name", "reason", "boards"])
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    out["code"] = out["code"].str.zfill(6)
    out["reason"] = out["reason"].fillna("")
    return out


def load_ladder(data_dir, date) -> dict | None:
    p = Path(data_dir) / "ths" / "ladder" / f"{pd.Timestamp(date):%Y%m%d}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def archived_dates(data_dir) -> list:
    return sorted(p.stem for p in limitup_dir(data_dir).glob("*.csv"))


# ---------------------------------------------------------------- 热榜（实时）
def hot_stocks(kind="hour") -> pd.DataFrame | None:
    """个股人气榜前 100。kind：hour（1小时）/ day（24小时）。"""
    j = _get("https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock",
             dict(stock_type="a", type=kind, list_type="normal"))
    if not j or not j.get("data"):
        return None
    rows = [dict(rank=x["order"], code=x["code"], name=x["name"], pct=x.get("rise_and_fall"),
                 heat=float(x.get("rate") or 0), rank_chg=x.get("hot_rank_chg"),
                 concepts="、".join((x.get("tag") or {}).get("concept_tag") or []),
                 popularity=(x.get("tag") or {}).get("popularity_tag") or "") for x in j["data"]["stock_list"]]
    return pd.DataFrame(rows)


def hot_plates(kind="concept") -> pd.DataFrame | None:
    """热门板块前 20。kind：concept / industry。"""
    j = _get("https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/plate", dict(type=kind))
    if not j or not j.get("data"):
        return None
    rows = [dict(rank=x["order"], code=x["code"], name=x["name"], pct=x.get("rise_and_fall"),
                 heat=float(x.get("rate") or 0), rank_chg=x.get("hot_rank_chg"), tag=x.get("tag") or "",
                 hot_tag=x.get("hot_tag") or "", etf=x.get("etf_name") or "", etf_code=x.get("etf_product_id") or "",
                 etf_pct=x.get("etf_rise_and_fall")) for x in j["data"]["plate_list"]]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 题材识别（按当时数据）
def split_reason(reason: str) -> list:
    """'人形机器人+减速器+业绩增长' → ['人形机器人','减速器','业绩增长']（去掉空白和重复）。"""
    tags = [t.strip() for t in str(reason).replace("＋", "+").split("+")]
    return list(dict.fromkeys(t for t in tags if t and t.lower() != "nan"))


# 不代表题材的通用原因（业绩、股权变动、摘帽等），不参与主线排名
GENERIC = ("业绩", "净利润", "扭亏", "预增", "增长", "摘帽", "重组", "收购", "股权", "回购", "增持", "分红",
           "高送转", "定增", "中标", "订单", "签订", "合同", "举牌", "复牌", "次新", "新股", "国企改革",
           "实控人", "控制权", "变更", "转让", "资产注入", "低价", "超跌", "反弹",
           "央企", "国资", "地方国企", "季报", "中报", "年报", "预盈")


def is_generic(tag: str) -> bool:
    return any(g in tag for g in GENERIC)


def tag_table(lu: pd.DataFrame) -> pd.DataFrame:
    """涨停池 → 长表 (date, code, tag, boards)。一只股票的每个标签一行。"""
    rows = []
    for r in lu.itertuples():
        for t in split_reason(r.reason):
            rows.append((r.date, r.code, t, r.boards))
    return pd.DataFrame(rows, columns=["date", "code", "tag", "boards"])


def themes(lu: pd.DataFrame, dates: pd.DatetimeIndex, window=3, top_k=5, min_count=4) -> dict:
    """按当天写下的涨停原因做题材识别，每天只用当天及之前的数据。
    返回：
      daily       (日期 × 标签) 当天涨停家数
      strength    (日期 × 标签) 近 window 日涨停家数（题材强度）
      mainline    {日期: [主线标签...]}：强度前 top_k、近 window 日 ≥min_count 家、当天仍有涨停、非通用原因
      stock_tags  {(日期, 代码): [标签...]}：该股当天涨停时的标签
    """
    tt = tag_table(lu)
    if tt.empty:
        return {"daily": pd.DataFrame(index=dates), "strength": pd.DataFrame(index=dates), "mainline": {}, "stock_tags": {}}
    daily = tt.groupby(["date", "tag"]).size().unstack(fill_value=0).reindex(dates, fill_value=0)
    strength = daily.rolling(window, min_periods=1).sum()
    themed = [t for t in daily.columns if not is_generic(t)]
    mainline = {}
    for d in dates:
        s = strength.loc[d, themed]
        s = s[(s >= min_count) & (daily.loc[d, themed] >= 1)].sort_values(ascending=False)
        mainline[d] = list(s.index[:top_k])
    stock_tags = {(r.date, r.code): split_reason(r.reason) for r in lu.itertuples()}
    return {"daily": daily, "strength": strength, "mainline": mainline, "stock_tags": stock_tags}


def theme_masks(th: dict, lu: pd.DataFrame, ind: dict, lookback=10) -> dict:
    """把题材识别结果转成和行情同形状的宽表，供策略使用：
      in_theme (日期 × 股票)：近 lookback 日内涨停过、且其涨停原因含当天的主线标签
      leader   (日期 × 股票)：在所属主线标签里，近 lookback 日最高连板数最高者
    只用每只股票「当时」涨停时写下的标签，无前视偏差。"""
    close = ind["close"]
    dates, cols = close.index, close.columns
    in_theme = pd.DataFrame(False, index=dates, columns=cols)
    leader = pd.DataFrame(False, index=dates, columns=cols)
    if lu.empty:
        return {"in_theme": in_theme, "leader": leader}
    streak = ind["streak"].rolling(lookback, min_periods=1).max()
    tt = tag_table(lu)
    tt = tt[tt["code"].isin(cols)]
    pos = {d: i for i, d in enumerate(dates)}
    tt = tt[tt["date"].isin(pos)]
    tt["i"] = tt["date"].map(pos)
    for d in dates:
        ml = th["mainline"].get(d) or []
        if not ml:
            continue
        i = pos[d]
        recent = tt[(tt["i"] <= i) & (tt["i"] > i - lookback) & tt["tag"].isin(ml)]
        if recent.empty:
            continue
        codes = recent["code"].unique()
        in_theme.loc[d, codes] = True
        for tag, g in recent.groupby("tag"):
            cs = g["code"].unique()
            st = streak.loc[d, cs]
            if st.max() >= 1:
                leader.loc[d, st[st >= st.max()].index] = True
    return {"in_theme": in_theme, "leader": leader}
