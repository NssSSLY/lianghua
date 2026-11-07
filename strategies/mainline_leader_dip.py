"""主线龙头首阴低吸（config.yaml 中的默认策略）。"""
from quant import strategy

NAME = "主线龙头首阴低吸"
DESCRIPTION = "近10日≥2连板、属于当日主线概念且为板块最高板的龙头，首次收阴缩量回调时次日挂-2%低吸"
EXEC = dict(buy_offset=-0.02, max_gap_up=0.03, take_profit=0.08, stop_loss=-0.05, max_hold_days=3)
USE_SENTIMENT = True
BUILTIN = True


def generate(ctx):
    p = {**ctx.cfg["strategy"], "use_board": True}
    return strategy.generate(ctx.ind, p, ctx.codes, ctx.lhb[["date", "code", "net_buy"]], ctx.board)
