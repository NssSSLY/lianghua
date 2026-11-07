"""定期调仓回测引擎（ETF 轮动、多因子选股这类「持有一篮子、定期换仓」的策略）。

规则：
- weights：index=决策日（T 日收盘后算出），columns=代码，值=目标权重（合计 ≤1，其余为现金）
- T+1 开盘执行：先卖出不在目标里的持仓，再买入新进入目标的品种
- 仍在目标里的老持仓不动（不做权重再平衡），减少换手和成本
- 卖不出（停牌 / 跌停开盘）顺延到下一交易日继续卖；买不进（停牌 / 涨停开盘）放弃
- 成本：佣金（最低5元）+ 过户费 + 印花税（股票卖出收，ETF 免）+ 单边滑点；100 股整数倍
- 复权：持仓按复权价计值，正确处理分红除权
"""
import math

import numpy as np
import pandas as pd

from .backtest import metrics


def run(ind: dict, weights: pd.DataFrame, bt: dict, costs: dict | None = None) -> dict:
    c = {"commission": bt["commission"], "min_commission": bt["min_commission"], "stamp_tax": bt["stamp_tax"],
         "transfer_fee": bt["transfer_fee"], "slippage": bt["slippage"], **(costs or {})}

    def fee(amt, sell):
        return max(amt * c["commission"], c["min_commission"]) + amt * c["transfer_fee"] + (amt * c["stamp_tax"] if sell else 0)

    A = ind["adj"]
    raw_o, k = ind["open"], ind["k"]
    zt, dt = ind["zt_price"], ind["dt_price"]
    all_dates = A["close"].index
    dates = all_dates[(all_dates >= pd.Timestamp(bt["start"])) & (all_dates <= pd.Timestamp(bt["end"]))]
    cols = {cd: i for i, cd in enumerate(A["close"].columns)}
    W = weights.reindex(columns=[x for x in weights.columns if x in cols]).fillna(0)
    decision = {d: row[row > 0] for d, row in W.iterrows()}

    cash = float(bt["init_cash"])
    pos = {}            # code -> dict(shares, cost_raw, entry_adj, entry_date, buy_amt)
    pending_sell = set()
    trades, equity = [], []
    last_adj = {}

    for d in dates:
        i = all_dates.get_loc(d)
        prev = all_dates[i - 1] if i > 0 else None
        target = decision.get(prev)
        if target is not None:
            pending_sell |= {x for x in pos if x not in target.index}

        # ---- 卖出
        for cd in list(pending_sell):
            if cd not in pos:
                pending_sell.discard(cd)
                continue
            j = cols[cd]
            o = A["open"].iat[i, j]
            if np.isnan(o) or raw_o.iat[i, j] <= dt.iat[i, j] + 1e-6:
                continue  # 停牌或跌停开盘，明天再卖
            p = pos.pop(cd)
            px = o * (1 - c["slippage"])
            amt = p["shares"] * p["cost_raw"] * px / p["entry_adj"]
            f = fee(amt, True)
            cash += amt - f
            pnl = amt - f - p["buy_amt"]
            trades.append(dict(code=cd, buy_date=p["entry_date"], sell_date=d, buy_px=round(p["cost_raw"], 3),
                               shares=p["shares"], ret=pnl / p["buy_amt"], pnl=pnl,
                               hold=all_dates.get_loc(d) - all_dates.get_loc(p["entry_date"]), reason="调仓"))
            pending_sell.discard(cd)

        # ---- 买入
        if target is not None and len(target):
            eq_prev = equity[-1]["equity"] if equity else cash
            new = [cd for cd in target.index if cd not in pos]
            for cd in sorted(new, key=lambda x: -target[x]):
                j = cols[cd]
                o = A["open"].iat[i, j]
                if np.isnan(o) or raw_o.iat[i, j] >= zt.iat[i, j] - 1e-6:
                    continue  # 停牌或涨停开盘，买不进
                px = o * (1 + c["slippage"])
                raw_px = px * k.iat[i, j]
                budget = min(eq_prev * target[cd], cash)
                shares = math.floor(budget / (raw_px * (1 + c["commission"] + c["transfer_fee"])) / 100) * 100
                while shares > 0 and shares * raw_px + fee(shares * raw_px, False) > cash:
                    shares -= 100
                if shares <= 0:
                    continue
                amt = shares * raw_px
                f = fee(amt, False)
                cash -= amt + f
                pos[cd] = dict(shares=shares, cost_raw=raw_px, entry_adj=px, entry_date=d, buy_amt=amt + f)

        # ---- 收盘估值
        mv = 0.0
        for cd, p in pos.items():
            v = A["close"].iat[i, cols[cd]]
            if not np.isnan(v):
                last_adj[cd] = v
            a = last_adj.get(cd, p["entry_adj"])
            mv += p["shares"] * p["cost_raw"] * a / p["entry_adj"]
        equity.append(dict(date=d, equity=cash + mv, cash=cash, n_pos=len(pos)))

    eq = pd.DataFrame(equity).set_index("date")
    tr = pd.DataFrame(trades, columns=["code", "buy_date", "sell_date", "buy_px", "shares", "ret", "pnl", "hold", "reason"])
    return dict(equity=eq, trades=tr, metrics=metrics(eq, tr, bt["init_cash"]))


def rebalance_dates(dates: pd.DatetimeIndex, freq="W") -> pd.DatetimeIndex:
    """调仓决策日：'W' 每周最后一个交易日，'M' 每月最后一个交易日，整数 N = 每 N 个交易日。"""
    s = pd.Series(dates, index=dates)
    if isinstance(freq, int):
        return dates[::-1][::freq][::-1]
    key = s.dt.to_period("W-FRI" if freq == "W" else "M")
    out = pd.DatetimeIndex(s.groupby(key).max().values)
    # 数据最后一天所在的周/月还没结束时，它不是真正的调仓日（否则实盘会每天都"调仓"）
    last = dates[-1]
    period_end = (last + pd.offsets.Week(weekday=4)) if freq == "W" and last.weekday() != 4 else \
        (last + pd.offsets.BMonthEnd(0)) if freq == "M" else last
    if len(out) and out[-1] == last and period_end.normalize() > last.normalize():
        out = out[:-1]
    return out
