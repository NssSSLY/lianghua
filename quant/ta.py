"""技术指标（同花顺/通达信口径）。

所有函数既可传单只股票的 Series，也可传宽表 DataFrame（index=日期, columns=代码，按列独立计算），
策略脚本里可以直接对全市场宽表调用，例如 ta.rsi(ctx.close, 6)。

SMA(X, N, M) 是国内软件的「移动平均」：Y = (M*X + (N-M)*Y') / N，等价于 alpha=M/N 的指数平滑。
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- 基础
def ma(x, n):
    return x.rolling(n, min_periods=n).mean()


def ema(x, n):
    return x.ewm(span=n, adjust=False).mean()


def sma(x, n, m=1):
    return x.ewm(alpha=m / n, adjust=False).mean()


def hhv(x, n):
    return x.rolling(n, min_periods=1).max()


def llv(x, n):
    return x.rolling(n, min_periods=1).min()


def ref(x, n=1):
    return x.shift(n)


def cross(a, b):
    """a 上穿 b（今天 a>b，昨天 a<=b）。b 可以是曲线，也可以是数值（如 ta.cross(rsi, 30)）。"""
    b_prev = b.shift(1) if hasattr(b, "shift") else b
    return (a > b) & (a.shift(1) <= b_prev)


# ---------------------------------------------------------------- 趋势
def macd(close, fast=12, slow=26, signal=9):
    """返回 (DIF, DEA, MACD柱)；柱 = 2*(DIF-DEA)，与国内软件一致。"""
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    return dif, dea, 2 * (dif - dea)


def boll(close, n=20, k=2):
    """返回 (中轨, 上轨, 下轨)。"""
    mid = ma(close, n)
    std = close.rolling(n, min_periods=n).std(ddof=0)
    return mid, mid + k * std, mid - k * std


def dmi(high, low, close, n=14, m=6):
    """返回 (PDI, MDI, ADX, ADXR)。"""
    tr = true_range(high, low, close)
    hd, ld = high - high.shift(1), low.shift(1) - low
    dmp = hd.where((hd > 0) & (hd > ld), 0)
    dmm = ld.where((ld > 0) & (ld > hd), 0)
    trs = tr.rolling(n).sum()
    pdi = dmp.rolling(n).sum() * 100 / trs
    mdi = dmm.rolling(n).sum() * 100 / trs
    adx = ma((mdi - pdi).abs() / (mdi + pdi) * 100, m)
    return pdi, mdi, adx, (adx + adx.shift(m)) / 2


def trix(close, n=12, m=9):
    tr = ema(ema(ema(close, n), n), n)
    t = (tr - tr.shift(1)) / tr.shift(1) * 100
    return t, ma(t, m)


def dma(close, short=10, long=50, m=10):
    d = ma(close, short) - ma(close, long)
    return d, ma(d, m)


# ---------------------------------------------------------------- 摆动
def rsi(close, n=6):
    diff = close.diff()
    up = sma(diff.clip(lower=0), n)
    dn = sma(diff.abs(), n)
    return up / dn * 100


def kdj(high, low, close, n=9, m1=3, m2=3):
    """返回 (K, D, J)。"""
    l, h = llv(low, n), hhv(high, n)
    rsv = (close - l) / (h - l).replace(0, np.nan) * 100
    k = sma(rsv.fillna(50), m1)
    d = sma(k, m2)
    return k, d, 3 * k - 2 * d


def wr(high, low, close, n=10):
    """威廉指标（同花顺口径：0~100，越大越超卖）。"""
    h, l = hhv(high, n), llv(low, n)
    return (h - close) / (h - l).replace(0, np.nan) * 100


def cci(high, low, close, n=14):
    tp = (high + low + close) / 3
    avedev = tp.rolling(n).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma(tp, n)) / (0.015 * avedev)


def bias(close, n=6):
    m = ma(close, n)
    return (close - m) / m * 100


def roc(close, n=12, m=6):
    r = (close - close.shift(n)) / close.shift(n) * 100
    return r, ma(r, m)


def psy(close, n=12, m=6):
    p = (close > close.shift(1)).astype(float).rolling(n).sum() / n * 100
    return p, ma(p, m)


def mtm(close, n=12, m=6):
    t = close - close.shift(n)
    return t, ma(t, m)


# ---------------------------------------------------------------- 量能 / 波动
def obv(close, volume):
    sign = np.sign(close.diff()).fillna(0)
    return (sign * volume).cumsum()


def vr(close, volume, n=26):
    up = volume.where(close > close.shift(1), 0).rolling(n).sum()
    dn = volume.where(close < close.shift(1), 0).rolling(n).sum()
    eq = volume.where(close == close.shift(1), 0).rolling(n).sum()
    return (up + eq / 2) / (dn + eq / 2).replace(0, np.nan) * 100


def true_range(high, low, close):
    pc = close.shift(1)
    return np.maximum(np.maximum(high - low, (high - pc).abs()), (low - pc).abs())


def atr(high, low, close, n=14):
    return ma(true_range(high, low, close), n)


def vol_ratio(volume, n=5):
    """量比：今日量 / 前 n 日均量。"""
    return volume / volume.shift(1).rolling(n).mean()


# ---------------------------------------------------------------- 单只股票全套（看板用）
def all_for_stock(o, h, l, c, v) -> dict:
    """返回 {指标组: {线名: Series}}，供个股看板展示。"""
    dif, dea, hist = macd(c)
    k, d, j = kdj(h, l, c)
    mid, up, dn = boll(c)
    pdi, mdi, adx, adxr = dmi(h, l, c)
    tr, trma = trix(c)
    dd, ddma = dma(c)
    r, rma = roc(c)
    p, pma = psy(c)
    mt, mtma = mtm(c)
    return {
        "MA": {"MA5": ma(c, 5), "MA10": ma(c, 10), "MA20": ma(c, 20), "MA60": ma(c, 60)},
        "EMA": {"EMA12": ema(c, 12), "EMA26": ema(c, 26), "EMA50": ema(c, 50)},
        "BOLL": {"MID": mid, "UPPER": up, "LOWER": dn},
        "MACD": {"DIF": dif, "DEA": dea, "MACD": hist},
        "KDJ": {"K": k, "D": d, "J": j},
        "RSI": {"RSI6": rsi(c, 6), "RSI12": rsi(c, 12), "RSI24": rsi(c, 24)},
        "WR": {"WR10": wr(h, l, c, 10), "WR6": wr(h, l, c, 6)},
        "CCI": {"CCI": cci(h, l, c)},
        "BIAS": {"BIAS6": bias(c, 6), "BIAS12": bias(c, 12), "BIAS24": bias(c, 24)},
        "DMI": {"PDI": pdi, "MDI": mdi, "ADX": adx, "ADXR": adxr},
        "OBV": {"OBV": obv(c, v)},
        "VR": {"VR": vr(c, v)},
        "ATR": {"ATR": atr(h, l, c)},
        "TRIX": {"TRIX": tr, "MATRIX": trma},
        "DMA": {"DMA": dd, "AMA": ddma},
        "ROC": {"ROC": r, "MAROC": rma},
        "PSY": {"PSY": p, "MAPSY": pma},
        "MTM": {"MTM": mt, "MAMTM": mtma},
    }
