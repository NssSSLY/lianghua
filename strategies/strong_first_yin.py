"""强势股首阴低吸（不加主线过滤，作对照组）。"""
from quant import strategy

NAME = "强势股首阴低吸（无主线）"
DESCRIPTION = "近10日≥2连板的强势股，首次收阴缩量回调时次日挂-2%低吸；与主线版对比看板块过滤的作用"
EXEC = dict(buy_offset=-0.02, max_gap_up=0.03, take_profit=0.08, stop_loss=-0.05, max_hold_days=3)
USE_SENTIMENT = True
BUILTIN = True


def generate(ctx):
    p = {**ctx.cfg["strategy"], "use_board": False}
    return strategy.generate(ctx.ind, p, ctx.codes, ctx.lhb[["date", "code", "net_buy"]], None)
