"""数据下载与本地缓存。

数据源：
- 日线：新浪（akshare.stock_zh_a_daily），失败时回退腾讯（stock_zh_a_hist_tx）
  同时下载不复权和前复权两份，存 raw 价格 + 复权因子 factor = 前复权close / 不复权close
  - 涨跌停判断用不复权价格（交易所按不复权价计算涨停价）
  - 收益计算用 raw * factor（正确处理除权除息）
- 龙虎榜：东方财富（stock_lhb_detail_em），按月缓存
- 实时涨停/跌停/炸板池：东方财富（仅用于盘后复核，回测不依赖）
"""
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

# 国内行情站点直连，不走系统代理（代理经常导致东财/新浪接口断开）
_DIRECT = ["sina.com.cn", "sinajs.cn", "eastmoney.com", "qq.com", "gtimg.cn", "baostock.com"]
for _k in ("NO_PROXY", "no_proxy"):
    _old = os.environ.get(_k, "")
    os.environ[_k] = ",".join([x for x in [_old] + _DIRECT if x])

import akshare as ak  # noqa: E402  (必须在设置 NO_PROXY 之后导入)

FIELDS = ["open", "high", "low", "close", "volume", "amount", "float_share", "factor"]


# ---------------------------------------------------------------- 股票池
def market_of(code: str) -> str:
    if code.startswith(("60", "000", "001", "002", "003")):
        return "main"
    if code.startswith(("300", "301")):
        return "gem"
    if code.startswith(("688", "689")):
        return "star"
    return "bj"  # 北交所等


def to_symbol(code: str) -> str:
    return ("sh" if code.startswith("6") else "sz") + code


def load_stock_list(data_dir, refresh=False) -> pd.DataFrame:
    """全部A股代码+名称，带 market/is_st 标记。名称是当前名称，历史ST无法追溯。"""
    path = Path(data_dir) / "stock_list.csv"
    if path.exists() and not refresh and time.time() - path.stat().st_mtime < 86400 * 3:
        df = pd.read_csv(path, dtype={"code": str})
    else:
        df = ak.stock_info_a_code_name()[["code", "name"]]
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False, encoding="utf-8-sig")
    df["market"] = df["code"].map(market_of)
    df["is_st"] = df["name"].str.contains("ST|退", regex=True)
    return df


def tradable_codes(stock_list: pd.DataFrame, markets) -> list:
    """策略可交易池：剔除 ST。"""
    m = stock_list["market"].isin(markets) & ~stock_list["is_st"]
    return stock_list.loc[m, "code"].tolist()


def download_codes(stock_list: pd.DataFrame, markets, include_st=True) -> list:
    """需要下载/展示的股票：可交易池 + （可选）同板块的 ST 股。"""
    m = stock_list["market"].isin(markets)
    if not include_st:
        m &= ~stock_list["is_st"]
    return stock_list.loc[m, "code"].tolist()


def st_codes(stock_list: pd.DataFrame) -> set:
    return set(stock_list.loc[stock_list["is_st"], "code"])


# ---------------------------------------------------------------- 日线
def _fetch_sina(code, start, end):
    sym, s, e = to_symbol(code), start.replace("-", ""), end.replace("-", "")
    raw = ak.stock_zh_a_daily(symbol=sym, start_date=s, end_date=e, adjust="")
    if raw is None or raw.empty:
        return None
    qfq = ak.stock_zh_a_daily(symbol=sym, start_date=s, end_date=e, adjust="qfq")
    raw = raw.rename(columns={"outstanding_share": "float_share"})
    df = raw[["date", "open", "high", "low", "close", "volume", "amount", "float_share"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    q = qfq[["date", "close"]].rename(columns={"close": "qclose"})
    q["date"] = pd.to_datetime(q["date"])
    df = df.merge(q, on="date", how="left")
    df["factor"] = df["qclose"] / df["close"]
    return df.drop(columns="qclose")


def _fetch_tx(code, start, end, sym=None):
    sym, s, e = sym or to_symbol(code), start.replace("-", ""), end.replace("-", "")
    raw = ak.stock_zh_a_hist_tx(symbol=sym, start_date=s, end_date=e, adjust="")
    if raw is None or raw.empty:
        return None
    qfq = ak.stock_zh_a_hist_tx(symbol=sym, start_date=s, end_date=e, adjust="qfq")
    # 腾讯有时会在末尾重复追加当天的实时行，去重（保留最后一条）
    raw = raw.drop_duplicates("date", keep="last")
    qfq = qfq.drop_duplicates("date", keep="last")
    df = raw[["date", "open", "high", "low", "close", "volume", "amount", "turnover"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    df["float_share"] = df["volume"] / df["turnover"].replace(0, np.nan)
    df = df.drop(columns="turnover")
    q = qfq[["date", "close"]].rename(columns={"close": "qclose"})
    q["date"] = pd.to_datetime(q["date"])
    df = df.merge(q, on="date", how="left")
    df["factor"] = df["qclose"] / df["close"]
    return df.drop(columns="qclose")


_EM_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36", "Connection": "close"}


_EM_STATE = {"last": 0.0, "down_until": 0.0}
EM_MIN_INTERVAL = 0.5     # 每个进程两次东财请求的最小间隔（秒），太密会被断开
EM_COOLDOWN = 180         # 被断开后暂停访问东财的秒数


def _em_kline(code, start, end, fqt):
    import requests
    now = time.time()
    if now < _EM_STATE["down_until"]:
        raise ConnectionError("东财暂停访问中（刚被限流）")
    wait = _EM_STATE["last"] + EM_MIN_INTERVAL - now
    if wait > 0:
        time.sleep(wait)
    _EM_STATE["last"] = time.time()
    secid = ("1." if code.startswith(("5", "6")) else "0.") + code
    try:
        r = _em_get(requests, secid, start, end, fqt)
    except requests.exceptions.ConnectionError:
        _EM_STATE["down_until"] = time.time() + EM_COOLDOWN
        raise
    return _parse_em(r)


def _em_get(requests, secid, start, end, fqt):
    return requests.get("http://push2his.eastmoney.com/api/qt/stock/kline/get", timeout=8, headers=_EM_H,
                     proxies={"http": None, "https": None},
                     params=dict(secid=secid, klt=101, fqt=fqt, beg=start.replace("-", ""), end=end.replace("-", ""),
                                 fields1="f1,f2,f3,f4,f5,f6", fields2="f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"))


def _parse_em(r):
    d = r.json().get("data")
    if not d or not d.get("klines"):
        return None
    rows = [k.split(",") for k in d["klines"]]
    df = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "volume", "amount",
                                     "amp", "pct", "chg", "turnover"])
    for c in df.columns[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    return df


def _fetch_em(code, start, end):
    """东方财富 HTTP 日线（HTTPS 在部分网络被拦截，用 HTTP）：一次返回成交额与换手率，速度快。"""
    raw = _em_kline(code, start, end, 0)
    if raw is None:
        return None
    qfq = _em_kline(code, start, end, 1)
    df = raw[["date", "open", "high", "low", "close"]].copy()
    df["volume"] = raw["volume"] * 100                      # 手 → 股
    df["amount"] = raw["amount"]
    df["float_share"] = df["volume"] / (raw["turnover"] / 100).replace(0, np.nan)
    q = qfq[["date", "close"]].rename(columns={"close": "qclose"}) if qfq is not None else None
    df["factor"] = (df.merge(q, on="date", how="left")["qclose"].values / df["close"].values) if q is not None else 1.0
    return df


def _fetch_txd(code, start, end):
    """腾讯日线（直接请求 web.ifzq.gtimg.cn，不经过 akshare）。
    接口不含成交额：用 成交量 × (开+高+低+收)/4 估算（误差约 1~2%）；流通股本取实时行情里的当前值。"""
    import requests
    sym = to_symbol(code) if not code.startswith("5") else "sh" + code
    n = min(2000, len(pd.bdate_range(start, end)) + 10)
    P = {"http": None, "https": None}

    def get(adj):
        r = requests.get("http://web.ifzq.gtimg.cn/appstock/app/fqkline/get", timeout=8, proxies=P,
                         params={"param": f"{sym},day,{start},{end},{n},{adj}"})
        return r.json()["data"][sym]

    raw = get("")
    rows = raw.get("day") or raw.get("qfqday")
    if not rows:
        return None
    qt = (raw.get("qt") or {}).get(sym) or []
    float_share = float(qt[72]) if len(qt) > 72 and qt[72] not in ("", "0") else np.nan
    df = pd.DataFrame([r[:6] for r in rows], columns=["date", "open", "close", "high", "low", "vol"])
    for c in ["open", "close", "high", "low", "vol"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df["volume"] = df["vol"] * 100
    df["amount"] = df["volume"] * (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    df["float_share"] = float_share
    q = get("qfq").get("qfqday") or []
    qd = pd.DataFrame([r[:3] for r in q], columns=["date", "o", "qclose"])
    qd["date"] = pd.to_datetime(qd["date"])
    qd["qclose"] = pd.to_numeric(qd["qclose"], errors="coerce")
    df = df.merge(qd[["date", "qclose"]], on="date", how="left")
    df["factor"] = df["qclose"] / df["close"]
    return df[["date", "open", "high", "low", "close", "volume", "amount", "float_share", "factor"]]


SOURCES = {"em": _fetch_em, "txd": _fetch_txd, "sina": _fetch_sina, "tx": _fetch_tx}


def fetch_daily(code, start, end, retries=2):
    # 优先级：环境变量 QUANT_SOURCE 指定的源排第一，其余按 东财 → 腾讯直连 → 新浪 → 腾讯(akshare) 回退
    first = os.environ.get("QUANT_SOURCE", "em")
    order = [SOURCES[first]] + [f for k, f in SOURCES.items() if k != first] if first in SOURCES else list(SOURCES.values())
    for i in range(retries + 1):
        for fn in order:
            try:
                df = fn(code, start, end)
                if df is not None:
                    df["factor"] = df["factor"].ffill().fillna(1.0)
                    return df
                return None  # 接口正常但无数据（未上市/停牌）
            except Exception:
                continue
        time.sleep(1 + i * 2)
    raise RuntimeError(f"{code} 下载失败")


def update_one(code, data_dir, start, end):
    """增量更新单只股票缓存。若复权因子变了（期间有除权），整段重下。"""
    path = Path(data_dir) / "daily" / f"{code}.csv"
    if path.exists():
        old = pd.read_csv(path, parse_dates=["date"])
        if not old.empty:
            last = old["date"].max()
            if last >= pd.Timestamp(end):
                return "skip"
            recent = fetch_daily(code, (last - pd.Timedelta(days=10)).strftime("%Y-%m-%d"), end)
            if recent is None or recent.empty:
                return "skip"
            ov = old.merge(recent[["date", "factor"]], on="date", suffixes=("", "_new"))
            if len(ov) and np.allclose(ov["factor"], ov["factor_new"], rtol=5e-3):  # 容忍不同数据源复权价的四舍五入差
                new = pd.concat([old[old["date"] < recent["date"].min()], recent])
                new.to_csv(path, index=False)
                return "inc"
    df = fetch_daily(code, start, end)
    if df is None or df.empty:
        return "empty"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return "full"


def update_all(codes, data_dir, start, end, workers=4):
    # 新浪接口内部用 V8(mini_racer) 解码，多线程会崩溃，所以用多进程
    stats, failed = {}, []
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(update_one, c, data_dir, start, end): c for c in codes}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                r = f.result()
                stats[r] = stats.get(r, 0) + 1
            except Exception:
                failed.append(futs[f])
            if i % 200 == 0 or i == len(codes):
                print(f"  进度 {i}/{len(codes)} {stats} 失败{len(failed)}", flush=True)
    return failed


# ---------------------------------------------------------------- 面板数据
def load_panel(data_dir, codes, start=None, end=None) -> dict:
    """读取缓存，返回宽表字典 {字段: DataFrame(index=日期, columns=代码)}。
    合并结果缓存在 data/panel.pkl，日线文件有更新时自动重建。"""
    ddir = Path(data_dir) / "daily"
    cache = Path(data_dir) / "panel.pkl"
    newest = max((e.stat().st_mtime for e in os.scandir(ddir)), default=0) if ddir.exists() else 0
    if cache.exists() and cache.stat().st_mtime > newest:
        panel = pd.read_pickle(cache)
    else:
        # 缓存包含目录下全部股票，按需筛选，避免不同调用方的股票池互相覆盖
        panel = _build_panel(ddir, [p.stem for p in ddir.glob("*.csv")])
        pd.to_pickle(panel, cache)
    cols = [c for c in panel["close"].columns if c in set(codes)]
    sl = slice(pd.Timestamp(start) if start else None, pd.Timestamp(end) if end else None)
    return {k: v.loc[sl, cols] for k, v in panel.items()}


def _build_panel(ddir, codes) -> dict:
    frames = []
    for c in codes:
        p = ddir / f"{c}.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p, parse_dates=["date"])
        if df.empty:
            continue
        df["code"] = c
        frames.append(df)
    if not frames:
        raise RuntimeError("没有本地数据，请先运行 python update_data.py")
    long = pd.concat(frames, ignore_index=True)
    panel = {}
    for f in FIELDS:
        panel[f] = long.pivot(index="date", columns="code", values=f).astype("float64")
    # 停牌日 volume=0 或缺失：价格保持 NaN，交易时视为无法成交
    return panel


# ---------------------------------------------------------------- 龙虎榜
def _with_timeout(fn, seconds, *args):
    """akshare 部分接口没有超时，网络卡住会一直挂着；放到后台线程里等，超时就放弃（线程自行结束）。"""
    import threading
    box = {}

    def run():
        try:
            box["v"] = fn(*args)
        except Exception as e:
            box["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"超过 {seconds}s 未返回")
    if "e" in box:
        raise box["e"]
    return box.get("v")


def _monthly(data_dir, sub, start, end, fetch, need_cols):
    """按月缓存的通用读取：当月和缺列（旧格式）的文件会重新下载。"""
    ldir = Path(data_dir) / sub
    ldir.mkdir(parents=True, exist_ok=True)
    months = pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq="M")
    cur = pd.Timestamp.today().to_period("M")
    out = []
    for m in months:
        p = ldir / f"{m}.csv"
        df = pd.read_csv(p, dtype={"code": str}, parse_dates=["date"]) if p.exists() else None
        stale = m >= cur and (not p.exists() or time.time() - p.stat().st_mtime > 3600)  # 当月数据 1 小时内不重复下载
        if df is None or stale or not set(need_cols) <= set(df.columns):
            s = m.start_time.strftime("%Y%m%d")
            e = min(m.end_time, pd.Timestamp.today()).strftime("%Y%m%d")
            try:
                new = _with_timeout(fetch, 60, s, e)
            except Exception as ex:
                print(f"  {sub} {m} 获取失败: {type(ex).__name__} {str(ex)[:80]}")
                new = None
            if new is not None and len(new):
                df = new
                df.to_csv(p, index=False, encoding="utf-8-sig")
        if df is not None and len(df):
            out.append(df)
    if not out:
        return pd.DataFrame(columns=need_cols)
    res = pd.concat(out, ignore_index=True)
    res["code"] = res["code"].astype(str).str.zfill(6)
    res["date"] = pd.to_datetime(res["date"])
    return res


def _fetch_lhb(s, e):
    raw = ak.stock_lhb_detail_em(start_date=s, end_date=e)
    if raw is None or raw.empty:
        return None
    return raw.rename(columns={
        "代码": "code", "名称": "name", "上榜日": "date", "龙虎榜净买额": "net_buy", "龙虎榜买入额": "buy",
        "龙虎榜卖出额": "sell", "龙虎榜成交额": "lhb_amount", "市场总成交额": "amount", "上榜原因": "reason",
        "解读": "explain", "涨跌幅": "pct", "换手率": "turnover"})[
        ["code", "name", "date", "net_buy", "buy", "sell", "lhb_amount", "amount", "pct", "turnover", "reason", "explain"]]


def load_lhb(data_dir, start, end, detail=False) -> pd.DataFrame:
    """龙虎榜（按月缓存）。
    detail=False：每只股票每天一行 code/date/net_buy（同日多个上榜原因取净买额均值）
    detail=True：再带 name/buy/sell/amount/reason（多个原因用 ; 拼接）/explain"""
    lhb = _monthly(data_dir, "lhb", start, end, _fetch_lhb, ["code", "date", "net_buy", "reason"])
    if lhb.empty:
        return lhb
    g = lhb.groupby(["date", "code"], as_index=False)
    if not detail:
        return g["net_buy"].mean()
    agg = g.agg(name=("name", "first"), net_buy=("net_buy", "mean"), buy=("buy", "mean"), sell=("sell", "mean"),
                amount=("amount", "first"), pct=("pct", "first"), explain=("explain", "first"),
                reason=("reason", lambda s: "；".join(dict.fromkeys(map(str, s)))))
    return agg


def _fetch_lhb_jg(s, e):
    raw = ak.stock_lhb_jgmmtj_em(start_date=s, end_date=e)
    if raw is None or raw.empty:
        return None
    return raw.rename(columns={
        "代码": "code", "名称": "name", "上榜日期": "date", "买方机构数": "jg_buyers", "卖方机构数": "jg_sellers",
        "机构买入总额": "jg_buy", "机构卖出总额": "jg_sell", "机构买入净额": "jg_net", "市场总成交额": "amount",
        "机构净买额占总成交额比": "jg_net_ratio"})[
        ["code", "name", "date", "jg_buyers", "jg_sellers", "jg_buy", "jg_sell", "jg_net", "amount", "jg_net_ratio"]]


def load_lhb_jg(data_dir, start, end) -> pd.DataFrame:
    """龙虎榜机构买卖统计（按月缓存）：机构买入/卖出家数与金额、机构净买额。"""
    jg = _monthly(data_dir, "lhb_jg", start, end, _fetch_lhb_jg, ["code", "date", "jg_net"])
    return jg.drop_duplicates(["date", "code"]) if len(jg) else jg


# ---------------------------------------------------------------- 归档数据（archive_pools.py 每日保存）
def load_archive(data_dir, name="zt", start=None, end=None) -> pd.DataFrame:
    """读取归档的某类池子，合并成长表（含 date 列）。
    name: zt / zb / dt / prev / strong / lhb / lhb_jg"""
    root = Path(data_dir) / "archive"
    frames = []
    for p in sorted(root.glob(f"[0-9]*/{name}.csv")):
        d = pd.Timestamp(p.parent.name)
        if (start and d < pd.Timestamp(start)) or (end and d > pd.Timestamp(end)):
            continue
        frames.append(pd.read_csv(p, dtype={"代码": str, "code": str}, parse_dates=["date"]))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_board_snapshot(data_dir, day) -> pd.DataFrame:
    """取 day 当天或之前最近一份板块成分快照（按当时成分股回测，避免前视偏差）。"""
    snaps = sorted((Path(data_dir) / "archive" / "boards").glob("boards_*.csv"))
    snaps = [s for s in snaps if pd.Timestamp(s.stem.split("_")[1]) <= pd.Timestamp(day)]
    return pd.read_csv(snaps[-1], dtype={"code": str}) if snaps else pd.DataFrame()


# ---------------------------------------------------------------- 实时池（盘后复核用）
def today_pools(date: str) -> dict:
    d = date.replace("-", "")
    pools = {}
    for name, fn in [("zt", ak.stock_zt_pool_em), ("dt", ak.stock_zt_pool_dtgc_em),
                     ("zb", ak.stock_zt_pool_zbgc_em)]:
        pools[name] = pd.DataFrame()
        for i in range(3):
            try:
                pools[name] = fn(date=d)
                break
            except Exception:
                time.sleep(2)
    return pools
