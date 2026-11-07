"""均线多头回踩 MA10。"""
from quant import ta

NAME = "均线多头回踩MA10"
DESCRIPTION = "MA5>MA10>MA20>MA60 且 MA20 向上的趋势股，缩量回踩到 MA10 附近收阴；次日挂-1%低吸，持有至多5天"
EXEC = dict(buy_offset=-0.01, max_gap_up=0.02, take_profit=0.08, stop_loss=-0.05, max_hold_days=5)
USE_SENTIMENT = False
BUILTIN = True


def generate(ctx):
    c, v = ctx.close, ctx.volume
    m5, m10, m20, m60 = ta.ma(c, 5), ta.ma(c, 10), ta.ma(c, 20), ta.ma(c, 60)
    trend = (m5 > m10) & (m10 > m20) & (m20 > m60) & (m20 > m20.shift(5))
    near = (c <= m10 * 1.015) & (c >= m10 * 0.985)
    shrink = v < ta.ma(v, 5) * 0.8
    cond = trend & near & shrink & (ctx.ret <= 0) & (ctx.amount > 1e8)
    score = c / c.shift(20) - 1                       # 20 日动量越强越优先
    return ctx.signals(cond, score)
