"""随机选股基准：策略要跑赢它，才说明选股逻辑有价值（而不只是交易成本 + 市场涨跌）。"""
import numpy as np
import pandas as pd

NAME = "随机选股基准"
DESCRIPTION = "每天从成交额>1亿的股票里随机挑选，次日开盘买入，持有3天（止盈+8%/止损-5%）。用来衡量其他策略是否真有选股能力"
EXEC = dict(buy_offset=0.03, max_gap_up=0.03, take_profit=0.08, stop_loss=-0.05, max_hold_days=3)
USE_SENTIMENT = False
BUILTIN = True


def generate(ctx):
    rng = np.random.default_rng(42)  # 固定随机种子，结果可复现
    liquid = ctx.amount > 1e8
    score = pd.DataFrame(rng.random(liquid.shape), index=liquid.index, columns=liquid.columns)
    # 每天只保留随机分最高的 5 只，避免信号表过大
    top = score.where(liquid).rank(axis=1, ascending=False) <= 5
    return ctx.signals(top, score)
