"""趋势内 RSI 超卖反弹。"""
from quant import ta

NAME = "RSI超卖反弹（趋势内）"
DESCRIPTION = "收盘在 MA60 上方（中期趋势向上），RSI6 跌破 20；次日开盘买入，反弹 5% 止盈"
EXEC = dict(buy_offset=0.03, max_gap_up=0.02, take_profit=0.05, stop_loss=-0.05, max_hold_days=5)
USE_SENTIMENT = False
BUILTIN = True


def generate(ctx):
    c = ctx.close
    r6 = ta.rsi(c, 6)
    m60 = ta.ma(c, 60)
    cond = (c > m60) & (m60 > m60.shift(10)) & (r6 < 20) & (ctx.amount > 1e8)
    return ctx.signals(cond, score=-r6)
