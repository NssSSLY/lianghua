"""ETF 动量轮动（周度）。"""
import numpy as np

from quant.etf import CASH_ETF
from quant.factors import momentum_score

NAME = "ETF动量轮动（周）"
DESCRIPTION = "37只宽基/行业/跨境/商品/债券ETF，按25日「年化回归斜率×R²」打分，每周五选前2等权持有；全部为负时持有货币ETF避险。ETF免印花税、换手低"
MODE = "rebalance"
UNIVERSE = "etf"
COSTS = dict(stamp_tax=0, slippage=0.0005)   # ETF 卖出免印花税；流动性好，滑点按 0.05%
BUILTIN = True

N_DAYS = 25      # 动量窗口
TOP_N = 2        # 持有只数
FREQ = "W"       # 'W' 每周 / 'M' 每月 / 整数 N 每 N 个交易日



def rebalance(ctx):
    u = ctx.etf
    score = momentum_score(u.close, N_DAYS)
    liquid = (u.amount.rolling(20).mean() > 3e7) & (u.ind["bars"] >= N_DAYS + 5)
    score = score.where(liquid)
    score[CASH_ETF] = np.nan                         # 货币 ETF 不参与排名，只用来避险
    dates = ctx.rebalance_dates(FREQ, universe=u)
    w = ctx.top_n_weights(score, TOP_N, dates)
    best = score.loc[w.index].max(axis=1)
    risk_off = best <= 0                             # 没有任何品种在涨：全部换成货币 ETF
    w.loc[risk_off, :] = 0
    if CASH_ETF in w.columns:
        w.loc[risk_off, CASH_ETF] = 1.0
    return w
