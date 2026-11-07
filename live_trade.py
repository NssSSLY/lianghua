"""盘中执行（MiniQMT）：按昨晚生成的计划低吸，并自动止盈止损。

每个交易日 9:15 前启动，运行到收盘自动退出：
    python live_trade.py
先在 config.yaml 里保持 live.dry_run: true 跑几天，确认逻辑无误后再改成 false。

流程：
- 9:25 集合竞价结束后：读取 plans/plan_今天.csv，高开超过上限的放弃，其余挂限价低吸单
- 盘中每 3 秒：检查策略持仓，触及止盈/止损就卖出
- 14:50：持有到期且没有涨停的卖出
- 14:56：撤掉没成交的买单
策略持仓记录在 plans/live_state.json（只管理本策略买入的股票，不碰你手动买的票）
"""
import json
import time
from datetime import datetime

import pandas as pd

from quant.broker_qmt import Broker
from quant.config import ROOT, load_config

STATE = ROOT / "plans" / "live_state.json"


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(s):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def hold_days(buy_date: str, today: pd.Timestamp) -> int:
    return len(pd.bdate_range(buy_date, today)) - 1  # 交易日近似（未扣节假日）


def hhmm():
    n = datetime.now()
    return n.hour * 100 + n.minute


def wait_until(t):
    while hhmm() < t:
        time.sleep(1)


def place_buys(br, plan, cfg, state):
    st, live = cfg["strategy"], cfg["live"]
    held = br.positions()
    free = cfg["backtest"]["max_positions"] - len([c for c in state if c in held])
    if free <= 0 or plan.empty:
        print("无空余仓位或无计划")
        return
    ratio = float(plan["position_ratio"].iloc[0])
    if ratio <= 0:
        print("情绪分过低，今天不开新仓")
        return
    total = br.total_asset()
    per = min(total * ratio / cfg["backtest"]["max_positions"], live["per_order_cash"], br.cash() / free)
    ticks = br.ticks(plan["code"].tolist())
    n = 0
    for _, r in plan.iterrows():
        if n >= free:
            break
        code = r["code"]
        if code in held:
            continue
        t = ticks.get(code)
        if not t or not t.get("open"):
            print(f"  [跳过] {code} 无开盘数据（停牌？）")
            continue
        op, last_close = t["open"], t["lastClose"]
        if op > r["max_open"]:
            print(f"  [放弃] {code} {r['name']} 高开 {op / last_close - 1:+.2%}，不追")
            continue
        limit_dn = round(last_close * (0.8 if code.startswith(("30", "68")) else 0.9), 2)
        if st.get("skip_limit_down_open") and op <= limit_dn + 1e-6:
            print(f"  [放弃] {code} 跌停开盘")
            continue
        price = min(r["buy_price"], op)  # 开盘已低于挂单价就按开盘价附近成交
        br.buy(code, price, per, remark=f"lowbuy {r['signal_date']}")
        n += 1


def record_fills(br, state, plan, today):
    rows = {r["code"]: r for _, r in plan.iterrows()} if len(plan) else {}
    for code, (vol, _, cost) in br.positions().items():
        if code in rows and code not in state:
            r = rows[code]
            state[code] = {"buy_date": today.strftime("%Y-%m-%d"), "cost": cost,
                           # 记下该策略自己的卖出参数（计划文件里有就用，没有就用 config.yaml）
                           **{k: float(r[k]) for k in ("take_profit", "stop_loss", "max_hold_days") if k in r}}
            print(f"  [成交] {code} {vol}股 成本 {cost:.2f}")
            save_state(state)


def check_exits(br, state, cfg, today, time_exit=False):
    st = cfg["strategy"]
    pos = br.positions()
    for code in list(state):
        if code not in pos:
            state.pop(code)  # 已清仓（含手动卖出）
            save_state(state)
            continue
        vol, can_sell, cost = pos[code]
        if can_sell <= 0 or state[code].get("selling"):
            continue  # T+1 或已挂卖单
        t = br.ticks([code]).get(code, {})
        last, pre = t.get("lastPrice"), t.get("lastClose")
        if not last:
            continue
        r = last / cost - 1
        p = {k: state[code].get(k, st[k]) for k in ("take_profit", "stop_loss", "max_hold_days")}
        reason = None
        if r >= p["take_profit"]:
            reason = "止盈"
        elif r <= p["stop_loss"]:
            reason = "止损"
        elif time_exit and hold_days(state[code]["buy_date"], today) >= p["max_hold_days"]:
            zt = round(pre * (1.2 if code.startswith(("30", "68")) else 1.1), 2)
            if not (st.get("hold_if_limit_up") and last >= zt - 1e-6):
                reason = "到期"
        if reason:
            # 卖出挂略低于现价的限价单保证成交，但不能低于跌停价（否则废单）
            dt = round(pre * (0.8 if code.startswith(("30", "68")) else 0.9), 2)
            br.sell(code, can_sell, max(last * 0.995, dt), remark=reason)
            state[code]["selling"] = reason
            if not br.dry:
                save_state(state)


def main():
    cfg = load_config()
    today = pd.Timestamp.today().normalize()
    plan_path = ROOT / "plans" / f"plan_{today:%Y%m%d}.csv"
    plan = pd.read_csv(plan_path, dtype={"code": str}) if plan_path.exists() else pd.DataFrame()
    print(f"{today.date()} 计划：{plan_path.name if len(plan) else '无'}，{len(plan)} 只候选")

    br = Broker(cfg["live"])
    state = load_state()
    for v in state.values():
        v.pop("selling", None)  # 昨天没成交的卖单今天重新判断

    wait_until(926)
    print("== 集合竞价结束，挂低吸单 ==")
    place_buys(br, plan, cfg, state)

    wait_until(930)
    print("== 盘中监控 ==")
    time_exit_done = cancel_done = False
    while hhmm() < 1457:
        if 1130 <= hhmm() < 1300:
            time.sleep(10)
            continue
        record_fills(br, state, plan, today)
        do_time_exit = hhmm() >= 1450 and not time_exit_done
        check_exits(br, state, cfg, today, time_exit=do_time_exit)
        time_exit_done |= do_time_exit
        if hhmm() >= 1456 and not cancel_done:
            br.cancel_all_buys()
            cancel_done = True
        time.sleep(3)
    record_fills(br, state, plan, today)
    print("收盘，退出。当前策略持仓：", load_state())


if __name__ == "__main__":
    main()
