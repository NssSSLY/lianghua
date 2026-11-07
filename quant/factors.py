"""因子库：对全市场宽表（index=日期, columns=代码）计算，策略脚本直接调用。

截面打分惯例：分数越大越好。用 rank_pct 把不同量纲的因子转成 0~1 的截面分位再加权合成。
"""
import numpy as np
import pandas as pd


def rank_pct(f: pd.DataFrame) -> pd.DataFrame:
    """每天在截面上排分位（0~1，越大越好），NaN 保持 NaN。"""
    return f.rank(axis=1, pct=True)


def composite(factors: dict, weights: dict | None = None) -> pd.DataFrame:
    """多个因子按截面分位加权平均。factors={名: 宽表(越大越好)}，weights={名: 权重}（缺省等权）。"""
    weights = weights or {k: 1.0 for k in factors}
    total = sum(weights.values())
    out = None
    for k, f in factors.items():
        part = rank_pct(f) * weights.get(k, 0) / total
        out = part if out is None else out.add(part, fill_value=np.nan)
    return out


# ---------------------------------------------------------------- 动量 / 趋势
def momentum_score(close: pd.DataFrame, n: int) -> pd.DataFrame:
    """对 log(价格) 在窗口内做线性回归：(exp(斜率×250) - 1) × R²。斜率=涨得快，R²=涨得稳。"""
    y = np.log(close)
    t = pd.DataFrame(np.arange(len(y), dtype=float)[:, None].repeat(y.shape[1], 1), index=y.index, columns=y.columns)
    t = t.where(y.notna())
    m = lambda x: x.rolling(n, min_periods=n).mean()
    cov = m(t * y) - m(t) * m(y)
    var_t = m(t * t) - m(t) ** 2
    var_y = m(y * y) - m(y) ** 2
    slope = cov / var_t
    r2 = (cov ** 2) / (var_t * var_y)
    return (np.exp(slope * 250) - 1) * r2


def ret_n(close, n):
    return close / close.shift(n) - 1


# ---------------------------------------------------------------- A 股常见量价因子（越大越好的方向已处理）
def reversal(close, n=20):
    """短期反转：近 n 日跌得多的得分高。"""
    return -ret_n(close, n)


def low_volatility(ret, n=20):
    """低波动：日收益标准差越小得分越高。"""
    return -ret.rolling(n, min_periods=n).std()


def low_turnover(volume, float_share, n=20):
    """低换手：n 日平均换手率越低得分越高（冷门、拥挤度低）。"""
    return -(volume / float_share).rolling(n, min_periods=n).mean()


def small_size(float_mv):
    """小市值：流通市值越小得分越高（对数）。"""
    return -np.log(float_mv)


def illiquidity(ret, amount, n=20):
    """Amihud 非流动性：|收益|/成交额 越大得分越高。"""
    return (ret.abs() / amount).rolling(n, min_periods=n).mean()
