"""策略模板 —— 复制本文件，改名（不要以 _ 开头），就会出现在看板「策略对比」里。

规则：
1. generate(ctx) 只能用 T 日收盘时已知的数据（不要 shift(-1)，否则就是未来函数）
2. 返回 DataFrame，至少有 date、code 两列；score 列越大越优先（每天只买前 N 只，N=空余仓位）
3. 买卖由回测引擎统一执行（A股规则：T+1、涨跌停、手续费、最低5元佣金……），参数在 EXEC 里：
   - buy_offset   T+1 挂单价 = T日收盘 × (1 + buy_offset)；
                  负数=低吸（盘中跌到才成交），正数（如 +0.03）≈ 开盘直接买
   - max_gap_up   T+1 高开超过这个幅度就放弃
   - take_profit / stop_loss  盘中触及即卖（同一天都触及按止损算）
   - max_hold_days            最长持有交易日，到期收盘卖
4. USE_SENTIMENT=True 时，情绪分低于 config.yaml 的 sentiment.min_score 当天不开仓

ctx 的常用属性见 quant/context.py 顶部说明；技术指标见 quant/ta.py，因子见 quant/factors.py（都可直接对全市场宽表计算）。

—— 另一种写法：定期调仓（持有一篮子，每周/每月换仓），参考 strategies/etf_momentum.py、multi_factor.py ——
    MODE = "rebalance"
    UNIVERSE = "stock"            # 或 "etf"（用 ctx.etf.close 等）
    COSTS = dict(stamp_tax=0)     # 可选：覆盖成本（ETF 免印花税）
    def rebalance(ctx):           # 返回 index=决策日、columns=代码、值=目标权重 的表（每行合计 ≤1）
        dates = ctx.rebalance_dates("W")                      # 每周最后一个交易日
        return ctx.top_n_weights(score, 5, dates, eligible)   # 得分前 5 等权
"""
from quant import ta

NAME = "我的策略"
DESCRIPTION = "示例：MA20 上方、RSI6 从 30 以下回升时次日开盘买入"
EXEC = dict(buy_offset=0.03, max_gap_up=0.03, take_profit=0.06, stop_loss=-0.05, max_hold_days=5)
USE_SENTIMENT = False


def generate(ctx):
    c = ctx.close
    r = ta.rsi(c, 6)
    cond = (c > ta.ma(c, 20)) & ta.cross(r, 30) & (ctx.amount > 1e8)
    return ctx.signals(cond, score=-r)        # RSI 越低越优先
