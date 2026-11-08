"""业绩快报（东方财富「业绩报表」，按季度）与「业绩加速」观察名单。

第九轮统计（README）：用已公布的增速、ROE 选股都不显著，只有「业绩加速」（本期同比 − 上期同比）在 2016-2021
和 2022-2026 两段都显著，但每月超额只有约 0.3%，扣成本后组合基本不赚钱。所以这里只做观察名单，不做策略。

data/fund/yjbb_YYYYMMDD.csv 每个季度一个文件。
"""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd

COLS = {"股票代码": "code", "股票简称": "name", "所处行业": "industry", "最新公告日期": "ann_date",
        "营业总收入-同比增长": "rev_yoy", "净利润-同比增长": "np_yoy", "净资产收益率": "roe", "净利润-净利润": "np"}
DEADLINE = {"0331": "-04-30", "0630": "-08-31", "0930": "-10-31", "1231": "-04-30"}  # 法定披露截止日（年报在次年）


def quarters(n: int, today=None) -> list:
    """最近 n 个已结束的报告期（如 20260930），从旧到新。"""
    t = pd.Timestamp(today or pd.Timestamp.today())
    qs = pd.date_range(end=t, periods=n, freq="QE")
    return [q.strftime("%Y%m%d") for q in qs if q <= t]


def _deadline(q: str) -> pd.Timestamp:
    y = int(q[:4]) + (1 if q[4:] == "1231" else 0)
    return pd.Timestamp(f"{y}{DEADLINE[q[4:]]}")


def _fetch(q: str, timeout=180) -> pd.DataFrame:
    with ThreadPoolExecutor(1) as ex:
        return ex.submit(ak.stock_yjbb_em, date=q).result(timeout=timeout)


def update(data_dir, n=6, log=print) -> dict:
    """补齐最近 n 个报告期；披露截止日后 15 天内的报告期每次都重下（还有公司在陆续公布）。"""
    out = Path(data_dir) / "fund"
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    for q in quarters(n * 3)[-n:]:
        p = out / f"yjbb_{q}.csv"
        fresh = pd.Timestamp.today() > _deadline(q) + pd.Timedelta(days=15)
        if p.exists() and fresh:
            res[q] = "已有"
            continue
        try:
            d = _fetch(q)
            d.to_csv(p, index=False, encoding="utf-8-sig")
            res[q] = len(d)
        except (FutTimeout, Exception) as e:  # 单个报告期失败不影响其他
            res[q] = f"失败 {type(e).__name__}"
        log(f"  业绩 {q}: {res[q]}")
    return res


def load(data_dir, n=8) -> pd.DataFrame:
    rows = []
    for p in sorted((Path(data_dir) / "fund").glob("yjbb_*.csv"))[-n:]:
        d = pd.read_csv(p, dtype={"股票代码": str}, encoding="utf-8-sig").rename(columns=COLS)
        d["code"] = d["code"].str.zfill(6)
        d["q"] = p.stem[5:]
        rows.append(d[[c for c in COLS.values() if c in d] + ["q"]])
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def accel_list(data_dir, eligible, top=40) -> tuple[pd.DataFrame, str | None]:
    """每只股票最新一期 vs 上一期：营收、净利润同比都为正，按「营收加速 + 利润加速」的截面排名取前 top。
    eligible：可选的股票代码集合（非 ST、成交额、股价过滤在调用方做）。返回 (名单, 最新报告期)。"""
    F = load(data_dir)
    if F.empty:
        return pd.DataFrame(), None
    F = F.sort_values(["code", "q"])
    F["acc"] = F.groupby("code")["np_yoy"].diff()
    F["racc"] = F.groupby("code")["rev_yoy"].diff()
    F["prev_q"] = F.groupby("code")["q"].shift()
    last = F.drop_duplicates("code", keep="last")
    # 上一期必须是紧挨着的报告期，否则差值没有意义
    ok = last["prev_q"].notna() & last["code"].isin(eligible) & (last["np_yoy"] > 0) & (last["rev_yoy"] > 0)
    x = last[ok].dropna(subset=["acc", "racc"]).copy()
    if x.empty:
        return x, F["q"].max()
    x["score"] = (x["acc"].rank(pct=True) + x["racc"].rank(pct=True)) / 2
    x = x.sort_values("score", ascending=False).head(top)
    keep = ["code", "name", "industry", "q", "ann_date", "rev_yoy", "racc", "np_yoy", "acc", "roe", "score"]
    return x[keep].replace([np.inf, -np.inf], np.nan), F["q"].max()
