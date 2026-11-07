"""低吸策略：强势股（近期连板龙头/热点前排）首阴回调低吸。

逻辑：
1. 候选池：近 lookback_days 内出现过 >= min_boards 连板，说明有资金关注、有辨识度
2. T 日收盘后筛选：
   - 距最后一个涨停 <= max_days_after_board 天，且之后只有 max_down_days 根阴线（首阴）
   - T 日收跌但未跌停，并且缩量（抛压不重）
   - 从近期高点回撤在 [pullback_min, pullback_max] 区间，且收盘守住 N 日均线
   - 流通市值、成交额满足条件；剔除 ST、北交所、上市不足 60 个交易日的次新股
3. T+1 日：不追高开（高开 > max_gap_up 放弃），挂 T日收盘*(1+buy_offset) 的限价单低吸
4. 卖出：止盈 / 止损 / 持有到期（见 backtest.py）
5. 可选主线过滤（use_board）：T 日属于主线概念板块，且是板块内连板最高的龙头（见 boards.py）

所有条件只用 T 日收盘后已知的数据，不含未来函数。
"""
import numpy as np
import pandas as pd


def generate(ind: dict, cfg: dict, tradable: list, lhb: pd.DataFrame = None, board: dict = None) -> pd.DataFrame:
    """返回信号表：每行 = T 日收盘后选出的一只候选股（次日低吸）。
    board 为 boards.compute() 的结果；cfg.use_board 时只保留主线板块（及其龙头）。"""
    p = cfg
    c, adj_c, adj_h = ind["close"], ind["adj"]["close"], ind["adj"]["high"]
    ret, vol, streak, is_zt = ind["ret"], ind["volume"], ind["streak"], ind["is_zt"]
    lb = p["lookback_days"]

    max_streak = streak.rolling(lb, min_periods=1).max()
    hh = adj_h.rolling(lb, min_periods=1).max()
    pullback = 1 - adj_c / hh

    # 距最后一个涨停的天数、之后的阴线数
    idx = pd.DataFrame(np.arange(len(c))[:, None].repeat(c.shape[1], 1), index=c.index, columns=c.columns)
    last_zt_idx = idx.where(is_zt).ffill()
    days_after = idx - last_zt_idx
    down = (ret < 0).astype(int)
    cum_down = down.cumsum()
    down_since = cum_down - cum_down.where(is_zt).ffill()

    ma = adj_c.rolling(p["ma_support"]).mean()

    cond = (
        (max_streak >= p["min_boards"])
        & (days_after >= 1) & (days_after <= p["max_days_after_board"])
        & (down_since >= 1) & (down_since <= p["max_down_days"])
        & (ret <= p["max_signal_pct"]) & (ret >= p["min_signal_pct"])
        & ~ind["is_dt"]
        & (vol <= vol.shift(1) * p["vol_shrink"])
        & (pullback >= p["pullback_min"]) & (pullback <= p["pullback_max"])
        & (adj_c >= ma)
        & (ind["float_mv"] <= p["max_float_mv"])
        & (ind["amount"] >= p["min_amount"])
        & (ind["bars"] >= 60)
    )
    cond = cond.reindex(columns=c.columns, fill_value=False)
    cond.loc[:, ~cond.columns.isin(tradable)] = False
    if p.get("use_board") and board is not None:
        key = "leader" if p.get("leader_only") else "in_mainline"
        cond &= board[key].reindex(index=c.index, columns=c.columns, fill_value=False)

    sig = cond.stack()
    sig = sig[sig].index.to_frame(index=False)
    sig.columns = ["date", "code"]
    if sig.empty:
        return sig

    def pick(df):
        return df.stack().reindex(pd.MultiIndex.from_frame(sig[["date", "code"]])).values

    sig["close"] = pick(c)
    sig["ret"] = pick(ret)
    sig["max_streak"] = pick(max_streak)
    sig["pullback"] = pick(pullback)
    sig["float_mv"] = pick(ind["float_mv"])
    sig["amount"] = pick(ind["amount"])

    # 龙虎榜：T 日上榜且净买入为正加分，净卖出减分（龙虎榜 T 日晚间公布，T+1 可用）
    sig["lhb_net"] = np.nan
    if p.get("use_lhb") and lhb is not None and not lhb.empty:
        sig = sig.merge(lhb.rename(columns={"net_buy": "lhb_net_"}), on=["date", "code"], how="left")
        sig["lhb_net"] = sig.pop("lhb_net_")
    lhb_bonus = np.sign(sig["lhb_net"].fillna(0)) * 5

    # 打分：连板高度（辨识度）优先，回撤越浅越强
    sig["score"] = sig["max_streak"] * 10 - sig["pullback"] * 50 + lhb_bonus
    return sig.sort_values(["date", "score"], ascending=[True, False]).reset_index(drop=True)
