"""低频多因子选股（月度，更低换手）。"""
from quant import factors as F

NAME = "多因子选股（月）"
DESCRIPTION = "同周版：反转+低波动+低换手+小市值，改为每月末选前8等权；交易次数更少，对比调仓频率与成本"
MODE = "rebalance"
UNIVERSE = "stock"
BUILTIN = True

TOP_N = 8
FREQ = "M"
WEIGHTS = {"reversal": 1, "low_vol": 1, "low_turnover": 1, "small_size": 1}


def rebalance(ctx):
    ind = ctx.ind
    ok = (ind["bars"] >= 120) & (ctx.amount.rolling(20).mean() >= 3e7) & (ind["close"] >= 3) \
        & (ind["float_mv"] >= 1.5e9) & ~ind["suspended"] & ~ind["is_zt"]
    ok.loc[:, ~ok.columns.isin(ctx.codes)] = False
    sc = F.composite({
        "reversal": F.reversal(ctx.close, 20),
        "low_vol": F.low_volatility(ctx.ret, 20),
        "low_turnover": F.low_turnover(ctx.volume, ind["float_share"], 20),
        "small_size": F.small_size(ind["float_mv"]),
    }, WEIGHTS)
    return ctx.top_n_weights(sc, TOP_N, ctx.rebalance_dates(FREQ), eligible=ok)
