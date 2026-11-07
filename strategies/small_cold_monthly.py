"""小盘冷门低价股（月度）。规律来自 2023-2026 年的因子统计，长历史验证未通过，只作对照，不建议实盘。"""
import numpy as np

from quant import factors as F

NAME = "小盘冷门低价（月）"
DESCRIPTION = ("小市值+低流动性+低于20日线+近期少涨停+低价，每月末选前5等权。"
               "注意：看板数据不含退市股且全程剔除当前ST，结果明显偏高；"
               "加入退市股和ST后，2017-2026年化约10%，换调仓日在-1%~15%之间，最大回撤超50%")
MODE = "rebalance"
UNIVERSE = "stock"
BUILTIN = True

TOP_N = 5
FREQ = "M"


def rebalance(ctx):
    ind = ctx.ind
    ok = (ind["bars"] >= 120) & (ctx.amount.rolling(20).mean() >= 3e7) & (ind["close"] >= 3) \
        & (ind["float_mv"] >= 1.5e9) & ~ind["suspended"] & ~ind["is_zt"]
    ok.loc[:, ~ok.columns.isin(ctx.codes)] = False
    sc = F.composite({
        "size": -np.log(ind["float_mv"]),
        "illiq": F.illiquidity(ctx.ret, ctx.amount, 20),
        "cold": -(ctx.close / ctx.close.rolling(20).mean() - 1),
        "no_zt": -ind["is_zt"].rolling(20).sum(),
        "low_px": -ind["close"],
    })
    return ctx.top_n_weights(sc, TOP_N, ctx.rebalance_dates(FREQ), eligible=ok)
