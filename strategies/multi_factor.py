"""低频多因子选股（周度）。"""
from quant import factors as F

NAME = "多因子选股（周）"
DESCRIPTION = "非ST、流动性达标的个股，按 短期反转+低波动+低换手+小市值 四因子截面分位等权合成打分，每周五选前6等权持有，仍在前列的不换"
MODE = "rebalance"
UNIVERSE = "stock"
BUILTIN = True

TOP_N = 6
FREQ = "W"
WEIGHTS = {"reversal": 1, "low_vol": 1, "low_turnover": 1, "small_size": 1}   # 改权重即可做因子组合实验


def eligible(ctx):
    """股票池过滤：可交易、上市满120日、20日均成交额≥3000万、股价≥3元、流通市值≥15亿（避开退市风险的微盘）、
    当天没停牌、没涨停（涨停次日大概率买不进）。"""
    ind = ctx.ind
    ok = (ind["bars"] >= 120) & (ctx.amount.rolling(20).mean() >= 3e7) & (ind["close"] >= 3) \
        & (ind["float_mv"] >= 1.5e9) & ~ind["suspended"] & ~ind["is_zt"]
    ok.loc[:, ~ok.columns.isin(ctx.codes)] = False
    return ok


def score(ctx):
    ind = ctx.ind
    return F.composite({
        "reversal": F.reversal(ctx.close, 20),
        "low_vol": F.low_volatility(ctx.ret, 20),
        "low_turnover": F.low_turnover(ctx.volume, ind["float_share"], 20),
        "small_size": F.small_size(ind["float_mv"]),
    }, WEIGHTS)


def rebalance(ctx):
    dates = ctx.rebalance_dates(FREQ)
    return ctx.top_n_weights(score(ctx), TOP_N, dates, eligible=eligible(ctx))
