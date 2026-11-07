"""题材龙头首阴低吸（同花顺涨停原因，按当时数据）。"""
from quant import strategy

NAME = "题材龙头首阴低吸（同花顺）"
DESCRIPTION = ("与「主线龙头首阴低吸」相同的买卖规则，但主线改用同花顺当天写下的涨停原因识别（无前视偏差）："
               "近3日涨停原因里出现最多的题材标签为主线，选其中最高连板的龙头。同花顺数据约一年，回测只覆盖最近一年")
EXEC = dict(buy_offset=-0.02, max_gap_up=0.03, take_profit=0.08, stop_loss=-0.05, max_hold_days=3)
USE_SENTIMENT = True
BUILTIN = True


def generate(ctx):
    tm = ctx.theme_masks
    board = {"leader": tm["leader"], "in_mainline": tm["in_theme"]}
    p = {**ctx.cfg["strategy"], "use_board": True, "leader_only": True}
    return strategy.generate(ctx.ind, p, ctx.codes, ctx.lhb[["date", "code", "net_buy"]], board)
