"""MACD 零轴上方金叉 + 放量。"""
from quant import ta

NAME = "MACD零上金叉放量"
DESCRIPTION = "DIF 在零轴上方上穿 DEA，当日放量（>5日均量1.5倍）收阳；次日开盘买入，持有至多10天"
EXEC = dict(buy_offset=0.03, max_gap_up=0.03, take_profit=0.10, stop_loss=-0.06, max_hold_days=10)
USE_SENTIMENT = False
BUILTIN = True


def generate(ctx):
    c, v = ctx.close, ctx.volume
    dif, dea, _ = ta.macd(c)
    cond = ta.cross(dif, dea) & (dea > 0) & (v > ta.ma(v, 5).shift(1) * 1.5) & (ctx.ret > 0) \
        & ~ctx.ind["is_zt"] & (ctx.amount > 1e8)
    score = v / ta.ma(v, 5).shift(1)                   # 放量越明显越优先
    return ctx.signals(cond, score)
