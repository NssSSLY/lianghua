"""短线情绪周期指标，作为策略总开关。

每日计算（只用当日收盘后已知的数据）：
- zt_count  涨停家数
- dt_count  跌停家数
- zb_rate   炸板率 = 炸板 / (涨停 + 炸板)
- max_streak 市场最高连板高度
- premium   昨日涨停股今日平均涨幅（接力溢价，最能反映短线赚钱效应）

情绪分 0~100（四项各 25 分）：
- 涨停家数在近 120 日中的分位
- 跌停家数分位（越少越好）
- 炸板率：15% 及以下满分，50% 及以上 0 分
- 昨日涨停溢价：-2% 为 0 分，+4% 及以上满分
"""
import numpy as np
import pandas as pd


def _rank_last(x):
    return (x <= x[-1]).mean()


def compute(ind: dict, window=120, st=()) -> pd.DataFrame:
    """st：ST 股票集合。情绪指标只统计非 ST，ST 的涨跌停家数单独列出（st_zt_count / st_dt_count）。"""
    cols = [c for c in ind["is_zt"].columns if c not in st]
    stc = [c for c in ind["is_zt"].columns if c in st]
    zt, dt, br = ind["is_zt"][cols], ind["is_dt"][cols], ind["broken"][cols]
    df = pd.DataFrame(index=zt.index)
    df["st_zt_count"] = ind["is_zt"][stc].sum(axis=1)
    df["st_dt_count"] = ind["is_dt"][stc].sum(axis=1)
    df["zt_count"] = zt.sum(axis=1)
    df["dt_count"] = dt.sum(axis=1)
    df["zb_rate"] = br.sum(axis=1) / (df["zt_count"] + br.sum(axis=1)).replace(0, np.nan)
    df["max_streak"] = ind["streak"][cols].max(axis=1)
    prev_zt = zt.shift(1, fill_value=False)
    df["premium"] = ind["ret"][cols].where(prev_zt).mean(axis=1)

    r = lambda s: s.rolling(window, min_periods=20).apply(_rank_last, raw=True)
    s_zt = 25 * r(df["zt_count"])
    s_dt = 25 * (1 - r(df["dt_count"]))
    s_zb = 25 * ((0.5 - df["zb_rate"]) / 0.35).clip(0, 1)
    s_pm = 25 * ((df["premium"] + 0.02) / 0.06).clip(0, 1)
    df["score"] = (s_zt + s_dt + s_zb.fillna(0) + s_pm.fillna(0)).round(1)
    return df


def position_ratio(score, cfg) -> float:
    """根据情绪分决定仓位：低于 min_score 空仓，高于 full_score 满仓，中间半仓。"""
    if pd.isna(score) or score < cfg["min_score"]:
        return 0.0
    return 1.0 if score >= cfg["full_score"] else 0.5
