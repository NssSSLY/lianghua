"""盘中封板监控：只提示、只记录，不下单。

用法：
    python board_watch.py               # 交易日 09:15 前后启动，15:01 自动结束；看板「盘中提示」也可以启停
    python board_watch.py --interval 5  # 轮询间隔（秒），默认 10
"""
import argparse
import os
import time
from datetime import datetime

from quant import board_watch as W


def phase(now: str) -> str:
    if now < "09:25":
        return "wait"
    if "11:31" <= now < "13:00":
        return "lunch"
    if now >= "15:01":
        return "closed"
    return "live"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=10)
    args = ap.parse_args()

    W.WATCH_DIR.mkdir(parents=True, exist_ok=True)
    W.STOP_FILE.unlink(missing_ok=True)
    codes = W.watch_codes()
    today = datetime.now().strftime("%Y%m%d")
    w = W.Watcher(W.prev_boards(), W.read_events(today))
    base = dict(alive=True, pid=os.getpid(), day=today, n_codes=len(codes), interval=args.interval)
    print(f"监控 {len(codes)} 只，已恢复今天 {len(w.seen)} 只的状态", flush=True)
    message = "已退出"
    while True:
        if W.STOP_FILE.exists():
            W.STOP_FILE.unlink(missing_ok=True)
            message = "已手动停止"
            break
        now = datetime.now().strftime("%H:%M:%S")
        ph = phase(now)
        if ph == "closed":
            message = "今天已收盘"
            break
        if ph != "live":
            W.write_status(**base, phase=ph, message="等待开盘" if ph == "wait" else "午间休市")
            time.sleep(1)
            continue
        t0 = time.time()
        quotes = W.fetch_quotes(codes)
        stamp = next((q["stamp"] for q in quotes.values() if q["stamp"]), "")
        if not quotes:
            W.write_status(**base, phase="error", message="行情取不到，稍后重试")
        elif stamp[:8] != today:
            message = "今天不是交易日"
            break
        else:
            ev = w.update(quotes, now)
            W.append_events(today, ev)
            for e in ev:
                tag = " ★候选" if e["pick"] else ""
                print(f"{now} {e['event']} {e['code']} {e['name']} 第{e['boards']}板 封单{e['seal_pct']}%{tag}", flush=True)
            W.write_status(**base, phase="live", message="监控中", last=now, n_quotes=len(quotes),
                           cycle=round(time.time() - t0, 1))
        time.sleep(max(0.5, args.interval - (time.time() - t0)))
    W.write_status(**base | {"alive": False}, phase="off", message=message)
    print(message, flush=True)


if __name__ == "__main__":
    main()
