"""盘后选股：用选定的策略生成明天的交易计划。

建议每个交易日 17:30 之后运行（龙虎榜约 17:00 后公布）：
    python update_data.py      # 先增量更新数据
    python daily_pick.py                         # 用 config.yaml 的 active_strategy
    python daily_pick.py --strategy ma_pullback  # 指定 strategies/ 下的策略

输出 plans/plan_明天日期.csv（含该策略自己的买入价、止盈止损、持有天数），live_trade.py 次日读取执行。
"""
import argparse

import pandas as pd

from quant import boards, sentiment
from quant import strategy_loader as SL
from quant.config import ROOT, load_config
from quant.context import Context


def next_trade_day(d: pd.Timestamp) -> pd.Timestamp:
    """简单处理：跳过周末。法定节假日请自行留意（节前最后一天的计划，节后执行即可）。"""
    n = d + pd.Timedelta(days=1)
    while n.weekday() >= 5:
        n += pd.Timedelta(days=1)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="按某个历史交易日选股（复盘用），默认最新")
    ap.add_argument("--strategy", help="strategies/ 下的策略名，默认 config.yaml 的 active_strategy")
    args = ap.parse_args()

    cfg = load_config()
    key = args.strategy or cfg.get("active_strategy", "mainline_leader_dip")
    mod = SL.load(key)
    scfg = SL.strategy_cfg(cfg, mod)
    st = scfg["strategy"]
    start = (pd.Timestamp(args.date or pd.Timestamp.today()) - pd.Timedelta(days=500)).strftime("%Y-%m-%d")
    ctx = Context(cfg, start=start, end=args.date)
    T = ctx.T

    s = ctx.senti.loc[T]
    ratio = sentiment.position_ratio(s["score"], scfg["sentiment"])
    print(f"\n=== {T.date()} 盘后 · 策略：{getattr(mod, 'NAME', key)} ===")
    print(f"情绪分 {s['score']:.0f} | 涨停 {s['zt_count']:.0f} 跌停 {s['dt_count']:.0f} "
          f"炸板率 {s['zb_rate']:.0%} 最高板 {s['max_streak']:.0f} 昨涨停溢价 {s['premium']:+.2%} "
          f"| ST涨停 {s['st_zt_count']:.0f} ST跌停 {s['st_dt_count']:.0f}")
    print(f"建议仓位：{ratio:.0%}" + ("（情绪差，明天不开新仓）" if ratio == 0 else ""))
    ml = ctx.board["mainline"].loc[T]
    top = ctx.board["board_win"].loc[T][ml].sort_values(ascending=False)
    print(f"主线板块（近{cfg['strategy']['board_window']}日涨停数）：" +
          ("、".join(f"{b}({int(n)})" for b, n in top.items()) or "无"))

    if SL.mode(mod) == "rebalance":
        return rebalance_plan(ctx, mod, key, T)

    sig = SL.run_signals(ctx, mod)
    today = sig[sig["date"] == T].copy()
    if today.empty:
        print("今日无符合条件的标的")
        return
    today["close"] = ctx.ind["close"].loc[T, today["code"]].values
    today["ret"] = ctx.ret.loc[T, today["code"]].values
    today["name"] = today["code"].map(ctx.names)
    today["boards"] = [boards.stock_boards(ctx.members, ml, c) for c in today["code"]]
    today["buy_price"] = (today["close"] * (1 + st["buy_offset"])).round(2)
    today["max_open"] = (today["close"] * (1 + st["max_gap_up"])).round(2)
    today["position_ratio"] = ratio
    for k in ("take_profit", "stop_loss", "max_hold_days"):
        today[k] = st[k]
    today["strategy"] = key
    cols = ["code", "name", "boards", "close", "ret", "score", "buy_price", "max_open", "position_ratio",
            "take_profit", "stop_loss", "max_hold_days", "strategy"]
    out = today[cols].head(cfg["backtest"]["max_positions"] * 3)

    show = out[["code", "name", "boards", "close", "ret", "score", "buy_price", "max_open"]].copy()
    show["ret"] = show["ret"].map("{:+.2%}".format)
    show["score"] = show["score"].round(3)
    print("\n候选（按得分排序）：")
    print(show.to_string(index=False))

    plan_dir = ROOT / "plans"
    plan_dir.mkdir(exist_ok=True)
    nd = next_trade_day(T)
    path = plan_dir / f"plan_{nd:%Y%m%d}.csv"
    out.assign(signal_date=T.date(), trade_date=nd.date()).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n计划已保存：{path}")
    print(f"执行规则：开盘价 > max_open 放弃；挂 buy_price 限价单；"
          f"止盈 {st['take_profit']:+.0%} / 止损 {st['stop_loss']:+.0%} / 最多持有 {st['max_hold_days']} 天")


def rebalance_plan(ctx, mod, key, T):
    """调仓类策略：输出最新目标持仓；T 日是调仓日时列出需要卖出/买入的品种。
    结果存为 plans/rebalance_次日.csv（live_trade.py 不会自动执行，请手动按清单调仓）。"""
    w = SL.run_weights(ctx, mod)
    u = SL.universe(ctx, mod)
    if w.empty:
        print("还没有任何调仓决策")
        return
    last = w.index[-1]
    cur = w.loc[last][w.loc[last] > 0]
    prev = w.loc[w.index[-2]][w.loc[w.index[-2]] > 0] if len(w) > 1 else pd.Series(dtype=float)
    is_today = last == T
    print(f"\n最近一次调仓决策：{last.date()}" + ("（就是今天，明天开盘执行）" if is_today else f"（今天 {T.date()} 不是调仓日）"))
    rows = []
    for c, wt in cur.items():
        px = u.ind["close"][c].loc[:T].dropna()
        rows.append(dict(code=c, name=ctx.names.get(c, c), weight=round(wt, 4), close=px.iloc[-1] if len(px) else None,
                         action="新买入" if c not in prev.index else "继续持有"))
    for c in prev.index.difference(cur.index):
        rows.append(dict(code=c, name=ctx.names.get(c, c), weight=0, close=None, action="卖出"))
    df = pd.DataFrame(rows)
    print("\n目标持仓" + ("（明天执行）：" if is_today else "（以下「新买入/卖出」是上次调仓时的变动，今天无需操作）："))
    print(df.to_string(index=False))
    if is_today:
        plan_dir = ROOT / "plans"
        plan_dir.mkdir(exist_ok=True)
        path = plan_dir / f"rebalance_{next_trade_day(T):%Y%m%d}.csv"
        df.assign(strategy=key, decision_date=last.date()).to_csv(path, index=False, encoding="utf-8-sig")
        print(f"\n调仓清单已保存：{path}\n执行方式：明天开盘先卖出「卖出」项，再按权重买入「新买入」项（100股整数倍）")


if __name__ == "__main__":
    main()
