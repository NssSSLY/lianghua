"""布林下轨反弹。"""
from quant import ta

NAME = "布林下轨回升"
DESCRIPTION = "昨日收盘跌破布林下轨，今日收回下轨上方且收阳；次日开盘买入，目标中轨附近（+6%）"
EXEC = dict(buy_offset=0.03, max_gap_up=0.02, take_profit=0.06, stop_loss=-0.05, max_hold_days=5)
USE_SENTIMENT = False
BUILTIN = True


def generate(ctx):
    c = ctx.close
    mid, up, dn = ta.boll(c)
    cond = (c.shift(1) < dn.shift(1)) & (c > dn) & (ctx.ret > 0) & (ctx.amount > 1e8)
    score = (mid - c) / c                              # 离中轨越远（空间越大）越优先
    return ctx.signals(cond, score)
