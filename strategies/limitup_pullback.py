"""首板回踩（N字反包预备）。"""
from quant import ta

NAME = "首板缩量回踩"
DESCRIPTION = "3~6日前出现首板（非连板），之后缩量回调但未跌破涨停当日开盘价，T日收阴；次日挂-1%低吸博反包"
EXEC = dict(buy_offset=-0.01, max_gap_up=0.02, take_profit=0.07, stop_loss=-0.04, max_hold_days=3)
USE_SENTIMENT = True
BUILTIN = True


def generate(ctx):
    ind, c, o, v = ctx.ind, ctx.close, ctx.open, ctx.volume
    first_board = ind["is_zt"] & (ind["streak"] == 1)
    hits = []
    for k in range(3, 7):
        fb = first_board.shift(k, fill_value=False)
        # 首板次日没继续涨停（不是连板股）
        no_follow = ~ind["is_zt"].shift(k - 1, fill_value=False)
        support = o.shift(k)                         # 涨停当日开盘价
        zt_vol = v.shift(k)
        cond = fb & no_follow & (c >= support) & (v < zt_vol * 0.6) & (ta.llv(c, k) >= support * 0.99)
        hits.append(cond)
    cond = hits[0] | hits[1] | hits[2] | hits[3]
    cond &= (ctx.ret < 0) & (ctx.ret > -0.06) & (ind["float_mv"] < 2e10) & (ctx.amount > 8e7)
    score = -(c / ta.hhv(c, 6) - 1).abs()             # 回撤越浅越优先
    return ctx.signals(cond, score)
