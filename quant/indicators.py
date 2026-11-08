"""基础指标：真实昨收、涨跌停价、连板数、复权价格。全部基于宽表（index=日期, columns=代码）。"""
import numpy as np
import pandas as pd

from .data import market_of

GEM_20PCT_START = pd.Timestamp("2020-08-24")  # 创业板注册制改革，涨跌幅改为20%（含创业板ST）
# 沪深主板风险警示（ST）股票涨跌幅由 5% 调整为 10% 的生效日。
# 由本地数据核实：2025 年上半年一直是 ST 的 18 只主板股，17 只在 2026-07-06/07 首次出现 >5.5% 的单日涨跌幅
MAIN_ST_10PCT_START = pd.Timestamp("2026-07-06")


def limit_ratio(panel, st=()) -> pd.DataFrame:
    """每只股票每天的涨跌幅限制。
    st 是「当前」ST 股票集合：历史上何时戴帽无法追溯，按全程 ST 处理（会误判戴帽前的涨停）。"""
    close = panel["close"]
    mk = pd.Series({c: market_of(c) for c in close.columns})
    r = pd.DataFrame(0.10, index=close.index, columns=close.columns)
    r.loc[:, mk[mk == "star"].index] = 0.20
    gem = mk[mk == "gem"].index
    r.loc[r.index >= GEM_20PCT_START, gem] = 0.20
    st_main = [c for c in close.columns if c in st and mk[c] == "main"]
    st_gem_old = [c for c in close.columns if c in st and mk[c] == "gem"]
    r.loc[r.index < MAIN_ST_10PCT_START, st_main] = 0.05
    r.loc[r.index < GEM_20PCT_START, st_gem_old] = 0.05
    return r


def round_price(x):
    """交易所价格四舍五入到分。"""
    return np.floor(x * 100 + 0.5 + 1e-7) / 100


def build(panel, st=()) -> dict:
    """在面板上计算所有基础指标，返回 dict（含原始字段）。st：ST 股票代码集合（涨跌停比例不同）。"""
    o, h, l, c = panel["open"], panel["high"], panel["low"], panel["close"]
    f = panel["factor"]
    vol = panel["volume"]
    suspended = vol.isna() | (vol <= 0)
    o, h, l, c = (x.where(~suspended) for x in (o, h, l, c))

    # 真实昨收：平时=前一交易日收盘；除权日用复权因子还原
    # 数据源的前复权是「减去分红」式的，因子会随股价每天同向变动（甚至算出 ≤0），不是除权。
    # 按「减去分红」的模型，股价涨 r 时因子会跟着涨约 (1/f - 1)·r；超出这部分 0.5% 以上才算除权（分红/送转），
    # 这样股价上涨当天的分红也能识别。另外股价大涨而因子大幅下降是合股
    c_prev = c.ffill().shift(1)
    f_ff = f.where(f > 0).ffill().bfill()
    f_ratio = f_ff.shift(1) / f_ff
    f_rise = 1 / f_ratio - 1
    raw_ret = c / c_prev - 1
    noise = (1 / f_ff.shift(1) - 1).clip(lower=0) * raw_ret.clip(lower=0)
    exright = ((f_rise - 1.5 * noise > 0.005)) | ((f_rise < -0.2) & (raw_ret > 0.25))
    preclose = c_prev.where(~exright, c_prev * f_ratio)

    lim = limit_ratio(panel, st)
    zt_price = round_price(preclose * (1 + lim))
    dt_price = round_price(preclose * (1 - lim))
    # 新股上市前 5 个交易日不设涨跌幅（数据起点之前就上市的不受影响）
    listed_in_data = c.notna().idxmax() > c.index[0]
    new_ipo = (c.notna().cumsum() <= 5) & listed_in_data
    is_zt = (c >= zt_price - 1e-6) & c.notna() & ~new_ipo
    is_dt = (c <= dt_price + 1e-6) & c.notna() & ~new_ipo
    touch_zt = (h >= zt_price - 1e-6) & c.notna()
    broken = touch_zt & ~is_zt  # 炸板
    one_word = (h == l) & c.notna()

    # 无"毛刺"的复权价：用 收盘/真实昨收 的链式累乘，避免四舍五入导致的复权因子抖动
    ret = (c / preclose - 1).where(c.notna())
    adj_c = (1 + ret.fillna(0)).cumprod().where(c.notna())
    k = adj_c / c  # 当日 复权价/不复权价
    adj = {"open": o * k, "high": h * k, "low": l * k, "close": adj_c}

    # 连板数
    cs = is_zt.cumsum()
    streak = cs - cs.where(~is_zt).ffill().fillna(0)

    return dict(
        open=o, high=h, low=l, close=c, volume=vol.where(~suspended), amount=panel["amount"],
        float_share=panel["float_share"], preclose=preclose, ret=ret, lim=lim,
        zt_price=zt_price, dt_price=dt_price, is_zt=is_zt, is_dt=is_dt, broken=broken,
        one_word=one_word, streak=streak, suspended=suspended, adj=adj, k=k,
        float_mv=c * panel["float_share"], bars=c.notna().cumsum(),
    )
