"""策略对比：对 strategies/ 下所有策略（或指定的几个）回测，分样本内/样本外统计。

用法：
    python compare_strategies.py                     # 全部策略
    python compare_strategies.py --only ma_pullback rsi_oversold
    python compare_strategies.py --split 2025-01-01  # 样本内/外分界（默认 2025-01-01）

结果：results/compare/summary.json，以及每个策略的 equity.csv / trades.csv / signals.csv
"""
import argparse
import json
import time
import traceback

import numpy as np
import pandas as pd

from quant import strategy_loader as SL
from quant.config import ROOT, load_config
from quant.context import Context

OUT = ROOT / "results" / "compare"


def period_stats(eq: pd.Series, tr: pd.DataFrame, start, end) -> dict:
    e = eq.loc[start:end]
    if len(e) < 2:
        return {}
    prev = eq.loc[:pd.Timestamp(start) - pd.Timedelta(days=1)]
    base = prev.iloc[-1] if len(prev) else e.iloc[0]
    ret = e.iloc[-1] / base - 1
    years = len(e) / 244
    t = tr[(tr["sell_date"] >= pd.Timestamp(start)) & (tr["sell_date"] <= pd.Timestamp(end))] if len(tr) else tr
    r = e.pct_change().dropna()
    return {
        "ret": ret, "ann": (1 + ret) ** (1 / years) - 1 if years > 0 and ret > -1 else None,
        "mdd": (e / e.cummax() - 1).min(),
        "sharpe": r.mean() / r.std() * np.sqrt(244) if r.std() > 0 else 0,
        "trades": len(t), "win": (t["pnl"] > 0).mean() if len(t) else None,
        "avg_ret": t["ret"].mean() if len(t) else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--split", default="2025-01-01")
    args = ap.parse_args()

    cfg = load_config()
    bt = cfg["backtest"]
    print("加载数据 ...", flush=True)
    t0 = time.time()
    ctx = Context(cfg, end=bt["end"])
    print(f"  {len(ctx.all_codes)} 只股票，用时 {time.time() - t0:.0f}s", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    summary_p = OUT / "summary.json"
    summary = json.loads(summary_p.read_text(encoding="utf-8")) if summary_p.exists() else {}

    split = pd.Timestamp(args.split)
    for info in SL.list_strategies():
        key = info["key"]
        if args.only and key not in args.only:
            continue
        print(f"\n== {info['name']} ({key})", flush=True)
        if info["error"]:
            print(f"  跳过：{info['error']}")
            summary[key] = {**info, "status": "error", "msg": info["error"]}
            continue
        t1 = time.time()
        try:
            mod = SL.load(key)
            res = SL.run_backtest(ctx, mod, cfg)
        except Exception as e:
            traceback.print_exc()
            summary[key] = {**info, "status": "error", "msg": f"{type(e).__name__}: {e}"}
            continue
        # 零成本对照：同样的信号/持仓不计佣金、印花税、滑点，看选股本身有没有优势
        r0 = SL.run_backtest(ctx, mod, cfg, zero_cost=True,
                             cache={"signals": res["signals"], "weights": res.get("weights")})
        eq, tr, sig = res["equity"]["equity"], res["trades"], res["signals"]
        d = OUT / key
        d.mkdir(exist_ok=True)
        res["equity"].to_csv(d / "equity.csv", encoding="utf-8-sig")
        tr.to_csv(d / "trades.csv", index=False, encoding="utf-8-sig")
        sig[sig["date"] >= pd.Timestamp(bt["start"])].to_csv(d / "signals.csv", index=False, encoding="utf-8-sig")
        yearly = {}
        for y, g in eq.groupby(eq.index.year):
            prev = eq[eq.index.year < y]
            yearly[int(y)] = g.iloc[-1] / (prev.iloc[-1] if len(prev) else g.iloc[0]) - 1
        summary[key] = {
            **info, "status": "ok", "metrics": res["metrics"], "yearly": yearly,
            "zero_cost": {"total": r0["metrics"]["总收益率"], "trades": r0["metrics"]["交易次数"],
                          "avg_ret": float(r0["trades"]["ret"].mean()) if len(r0["trades"]) else None},
            "n_signals": int((sig["date"] >= pd.Timestamp(bt["start"])).sum()),
            "in_sample": period_stats(eq, tr, bt["start"], split - pd.Timedelta(days=1)),
            "out_sample": period_stats(eq, tr, split, bt["end"]),
            "split": args.split, "period": [bt["start"], bt["end"]],
            "updated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - t1, 1),
        }
        m = res["metrics"]
        print(f"  信号 {summary[key]['n_signals']}，交易 {m['交易次数']}，总收益 {m['总收益率']}，"
              f"最大回撤 {m['最大回撤']}，用时 {time.time() - t1:.0f}s", flush=True)
        summary_p.write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\n已保存 {summary_p}")


if __name__ == "__main__":
    main()
