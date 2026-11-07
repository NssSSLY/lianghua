"""符合 A 股规则的日频回测引擎。

已处理：
- T+1：买入当天不能卖
- 涨跌停：一字跌停卖不出；跌停开盘可选择不接；停牌不能交易
- 成交假设：限价低吸单只有盘中最低价 <= 挂单价才成交；开盘就低于挂单价则按开盘价成交
- 同一天止盈止损都触及：保守按止损处理（日线无法知道先后）
- 交易成本：佣金（最低5元）、印花税（卖出）、过户费、单边滑点
- 除权除息：持仓用复权价计值
- 手数：100 股整数倍
"""
import math

import numpy as np
import pandas as pd

from .sentiment import position_ratio


class Costs:
    def __init__(self, bt):
        self.rate, self.min_c = bt["commission"], bt["min_commission"]
        self.tax, self.tf, self.slip = bt["stamp_tax"], bt["transfer_fee"], bt["slippage"]

    def buy_fee(self, amt):
        return max(amt * self.rate, self.min_c) + amt * self.tf

    def sell_fee(self, amt):
        return max(amt * self.rate, self.min_c) + amt * self.tf + amt * self.tax


def run(ind: dict, signals: pd.DataFrame, senti: pd.DataFrame, cfg: dict) -> dict:
    bt, st = cfg["backtest"], cfg["strategy"]
    costs = Costs(bt)
    dates = ind["close"].loc[bt["start"]:bt["end"]].index
    all_dates = ind["close"].index
    A = {k: ind["adj"][k] for k in ("open", "high", "low", "close")}
    raw_o, raw_c = ind["open"], ind["close"]
    is_zt, one_word, dt_price = ind["is_zt"], ind["one_word"], ind["dt_price"]
    sig_by_date = {d: g for d, g in signals.groupby("date")} if len(signals) else {}

    cash = float(bt["init_cash"])
    positions = []  # dict(code, shares, cost_raw, entry_adj, entry_i, buy_amt)
    trades, equity = [], []
    last_adj = {}

    def mtm(i, code):
        v = A["close"].iat[i, A["close"].columns.get_loc(code)]
        if not np.isnan(v):
            last_adj[code] = v
        return last_adj.get(code)

    pos_idx = {d: all_dates.get_loc(d) for d in dates}
    for d in dates:
        i = pos_idx[d]
        # ---------------- 1. 卖出
        keep = []
        for p in positions:
            j = A["close"].columns.get_loc(p["code"])
            o, h, l, c = (A[k].iat[i, j] for k in ("open", "high", "low", "close"))
            held = i - p["entry_i"]
            if held < 1 or np.isnan(o):  # T+1 或停牌
                keep.append(p)
                continue
            if one_word.iat[i, j] and raw_c.iat[i, j] <= dt_price.iat[i, j] + 1e-6:  # 一字跌停卖不出
                keep.append(p)
                continue
            tp = p["entry_adj"] * (1 + st["take_profit"])
            sl = p["entry_adj"] * (1 + st["stop_loss"])
            px, reason = None, None
            if o <= sl:
                px, reason = o, "止损(开盘)"
            elif o >= tp:
                px, reason = o, "止盈(开盘)"
            elif l <= sl:
                px, reason = sl, "止损"
            elif h >= tp:
                px, reason = tp, "止盈"
            elif held >= st["max_hold_days"] and not (st.get("hold_if_limit_up") and is_zt.iat[i, j]):
                px, reason = c, "到期"
            if px is None:
                keep.append(p)
                continue
            px *= 1 - costs.slip
            amt = p["shares"] * p["cost_raw"] * px / p["entry_adj"]
            fee = costs.sell_fee(amt)
            cash += amt - fee
            pnl = amt - fee - p["buy_amt"]
            trades.append(dict(code=p["code"], buy_date=all_dates[p["entry_i"]], sell_date=d,
                               buy_px=round(p["cost_raw"], 3), shares=p["shares"],
                               ret=pnl / p["buy_amt"], pnl=pnl, hold=held, reason=reason))
        positions = keep

        # ---------------- 2. 买入（昨天收盘后产生的信号）
        prev = all_dates[i - 1] if i > 0 else None
        free = bt["max_positions"] - len(positions)
        if prev in sig_by_date and free > 0:
            ratio = position_ratio(senti.at[prev, "score"], cfg["sentiment"])
            equity_prev = equity[-1]["equity"] if equity else cash
            slot = equity_prev * ratio / bt["max_positions"]
            held_codes = {p["code"] for p in positions}
            cand = sig_by_date[prev]
            cand = cand[~cand["code"].isin(held_codes)].head(free)
            for _, s in cand.iterrows() if slot > 0 else []:
                j = A["close"].columns.get_loc(s["code"])
                o, l = A["open"].iat[i, j], A["low"].iat[i, j]
                ref = A["close"].iat[i - 1, j]
                if np.isnan(o) or np.isnan(ref):
                    continue
                if o / ref - 1 > st["max_gap_up"]:
                    continue
                if st.get("skip_limit_down_open") and raw_o.iat[i, j] <= dt_price.iat[i, j] + 1e-6:
                    continue
                limit = ref * (1 + st["buy_offset"])
                if o <= limit:
                    px = o
                elif l <= limit:
                    px = limit
                else:
                    continue  # 没跌到挂单价，未成交
                k = raw_c.iat[i, j] / A["close"].iat[i, j]  # 复权价 -> 当日不复权价
                raw_px = px * k * (1 + costs.slip)
                budget = min(slot, cash)
                shares = math.floor(budget / (raw_px * (1 + costs.rate + costs.tf)) / 100) * 100
                if shares <= 0:
                    continue
                amt = shares * raw_px
                fee = costs.buy_fee(amt)
                if amt + fee > cash:
                    shares -= 100
                    if shares <= 0:
                        continue
                    amt = shares * raw_px
                    fee = costs.buy_fee(amt)
                cash -= amt + fee
                positions.append(dict(code=s["code"], shares=shares, cost_raw=raw_px,
                                      entry_adj=px * (1 + costs.slip), entry_i=i, buy_amt=amt + fee))

        # ---------------- 3. 收盘市值
        mv = 0.0
        for p in positions:
            a = mtm(i, p["code"])
            mv += p["shares"] * p["cost_raw"] * (a / p["entry_adj"] if a else 1)
        equity.append(dict(date=d, equity=cash + mv, cash=cash, n_pos=len(positions)))

    eq = pd.DataFrame(equity).set_index("date")
    tr = pd.DataFrame(trades)
    return dict(equity=eq, trades=tr, metrics=metrics(eq, tr, bt["init_cash"]))


def metrics(eq: pd.DataFrame, tr: pd.DataFrame, init_cash) -> dict:
    e = eq["equity"]
    r = e.pct_change().fillna(0)
    years = max(len(e) / 244, 1e-9)
    dd = e / e.cummax() - 1
    m = {
        "初始资金": init_cash,
        "期末资金": round(e.iloc[-1], 2),
        "总收益率": f"{e.iloc[-1] / init_cash - 1:.2%}",
        "年化收益率": f"{(e.iloc[-1] / init_cash) ** (1 / years) - 1:.2%}",
        "最大回撤": f"{dd.min():.2%}",
        "夏普比率": round(r.mean() / r.std() * np.sqrt(244), 2) if r.std() > 0 else 0,
        "交易次数": len(tr),
    }
    if len(tr):
        win = tr[tr["pnl"] > 0]
        loss = tr[tr["pnl"] <= 0]
        m.update({
            "胜率": f"{len(win) / len(tr):.2%}",
            "平均盈利": f"{win['ret'].mean():.2%}" if len(win) else "-",
            "平均亏损": f"{loss['ret'].mean():.2%}" if len(loss) else "-",
            "盈亏比": round(win["ret"].mean() / -loss["ret"].mean(), 2) if len(win) and len(loss) else "-",
            "平均持有天数": round(tr["hold"].mean(), 2),
            "最大连续亏损笔数": int(max_consecutive(tr["pnl"] <= 0)),
        })
    return m


def max_consecutive(flags) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best
