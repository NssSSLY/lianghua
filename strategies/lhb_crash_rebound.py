"""龙虎榜：连续三日跌幅偏离 + 当日跌停，次日开盘买，持有 10 天（超跌反弹）。

这是在 2023-11 ~ 2025-04 的龙虎榜数据里，约 2900 组两两条件中样本内表现最好的一组（每笔平均 +10%），
但在 2022-06 ~ 2023-10 和 2025-05 以后都是亏的。留在这里作为「样本内挑出来的规则」的反面例子。
"""
NAME = "龙虎榜超跌反弹（样本内最优）"
DESCRIPTION = ("龙虎榜上榜原因含「连续三个交易日跌幅偏离」且当天跌停，次日开盘买，持有10天收盘卖。"
               "样本内（2023-11~2025-04）每笔平均+10%，前后两段都亏，是过拟合的例子，不要实盘")
BUILTIN = True
USE_SENTIMENT = False
MAX_POSITIONS = 2
EXEC = dict(buy_offset=0.5, max_gap_up=0.5, take_profit=10.0, stop_loss=-0.99, max_hold_days=10,
            hold_if_limit_up=False, skip_limit_down_open=False)


def generate(ctx):
    lhb = ctx.lhb
    if lhb.empty:
        return None
    hit = lhb[lhb["reason"].astype(str).str.contains("连续三个交易日") & lhb["reason"].astype(str).str.contains("跌幅")]
    hit = hit.drop_duplicates(["date", "code"])
    dt = ctx.ind["is_dt"]
    mask = dt & False
    for d, c in zip(hit["date"], hit["code"]):
        if d in mask.index and c in mask.columns:
            mask.at[d, c] = True
    mask &= dt
    return ctx.signals(mask, score=-ctx.ret)
