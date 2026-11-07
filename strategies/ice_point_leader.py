"""情绪冰点逆势龙头。"""
NAME = "冰点逆势涨停"
DESCRIPTION = "情绪分 <25（冰点）的当天，仍然封住涨停的最高连板股；次日开盘买入（高开>5%放弃），博情绪修复"
EXEC = dict(buy_offset=0.05, max_gap_up=0.05, take_profit=0.10, stop_loss=-0.06, max_hold_days=2)
USE_SENTIMENT = False
BUILTIN = True


def generate(ctx):
    ice = ctx.senti["score"] < 25
    ice_wide = ctx.ind["is_zt"].mul(ice.reindex(ctx.dates).fillna(False).astype(bool), axis=0)
    cond = ctx.ind["is_zt"] & ice_wide & ~ctx.ind["one_word"]
    return ctx.signals(cond, score=ctx.ind["streak"])
