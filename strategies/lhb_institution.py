"""龙虎榜机构净买跟随。"""
NAME = "龙虎榜机构净买跟随"
DESCRIPTION = "龙虎榜上 ≥2 家机构买入、机构净买额占成交额 >3%，且当日非一字板；次日开盘买入（高开>4%放弃）"
EXEC = dict(buy_offset=0.04, max_gap_up=0.04, take_profit=0.10, stop_loss=-0.06, max_hold_days=5)
USE_SENTIMENT = False
BUILTIN = True


def generate(ctx):
    buyers = ctx.lhb_wide("jg_buyers", src="jg")
    ratio = ctx.lhb_wide("jg_net_ratio", src="jg")
    cond = (buyers >= 2) & (ratio > 3) & ~ctx.ind["one_word"]
    return ctx.signals(cond, score=ratio)
