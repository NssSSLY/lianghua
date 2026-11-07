"""ETF 动量轮动（月度，更低换手）。"""
import numpy as np

from quant.etf import CASH_ETF
from quant.factors import momentum_score

NAME = "ETF动量轮动（月）"
DESCRIPTION = "同周版逻辑，改为60日动量、每月末选前3等权；换手更低，对比调仓频率的影响"
MODE = "rebalance"
UNIVERSE = "etf"
COSTS = dict(stamp_tax=0, slippage=0.0005)
BUILTIN = True

N_DAYS = 60
TOP_N = 3
FREQ = "M"


def rebalance(ctx):
    u = ctx.etf
    score = momentum_score(u.close, N_DAYS)
    score = score.where((u.amount.rolling(20).mean() > 3e7) & (u.ind["bars"] >= N_DAYS + 5))
    score[CASH_ETF] = np.nan
    w = ctx.top_n_weights(score, TOP_N, ctx.rebalance_dates(FREQ, universe=u))
    risk_off = score.loc[w.index].max(axis=1) <= 0
    w.loc[risk_off, :] = 0
    if CASH_ETF in w.columns:
        w.loc[risk_off, CASH_ETF] = 1.0
    return w
