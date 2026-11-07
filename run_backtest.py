"""运行回测。

用法：
    python run_backtest.py                              # 按 config.yaml 回测
    python run_backtest.py --start 2025-01-01 --end 2026-10-23
    python run_backtest.py --set strategy.take_profit=0.06 sentiment.min_score=0
    python run_backtest.py --sweep                      # 参数敏感性测试

结果输出到 results/：equity.csv 资金曲线、trades.csv 成交明细、signals.csv 信号、
sentiment.csv 每日情绪、equity.png 资金曲线图。
"""
import argparse
import copy
import itertools
from pathlib import Path

import pandas as pd
import yaml

from quant import backtest, boards, data, strategy
from quant import strategy_loader as SL
from quant.config import ROOT, load_config
from quant.context import Context

OUT = ROOT / "results"


def prepare(cfg, with_ctx=False):
    print("读取行情 ...", flush=True)
    ctx = Context(cfg, end=cfg["backtest"]["end"])
    print(f"  {ctx.close.shape[1]} 只股票（含ST {len(ctx.st & set(ctx.close.columns))} 只，仅用于统计），{len(ctx.dates)} 个交易日")
    st = cfg["strategy"]
    lhb = ctx.lhb[["date", "code", "net_buy"]] if st.get("use_lhb") else None
    board = ctx.board if st.get("use_board") else None
    out = (ctx.ind, ctx.senti, lhb, ctx.codes, board)
    return (*out, ctx) if with_ctx else out


def build_board(cfg, ind):
    st = cfg["strategy"]
    if not st.get("use_board"):
        return None
    sl = data.load_stock_list(cfg["data_dir"])
    codes = [c for c in data.tradable_codes(sl, cfg["markets"]) if c in ind["close"].columns]
    M = boards.membership(boards.download(cfg["data_dir"]), codes, st.get("board_kinds", ["概念"]))
    return boards.compute(ind, M, st)


def apply_overrides(cfg, sets):
    for s in sets or []:
        key, val = s.split("=", 1)
        node = cfg
        *path, last = key.split(".")
        for k in path:
            node = node[k]
        node[last] = yaml.safe_load(val)
    return cfg


def yearly(eq):
    e = eq["equity"]
    y = e.groupby(e.index.year).agg(["first", "last"])
    prev_last = y["last"].shift(1).fillna(e.iloc[0])
    dd = e.groupby(e.index.year).apply(lambda s: (s / s.cummax() - 1).min())
    return pd.DataFrame({"收益": (y["last"] / prev_last - 1).map("{:.2%}".format),
                         "最大回撤": dd.map("{:.2%}".format)})


def plot(eq, senti, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    a1.plot(eq.index, eq["equity"], lw=1.2)
    a1.set_title("资金曲线")
    a1.grid(alpha=.3)
    s = senti["score"].reindex(eq.index)
    a2.fill_between(s.index, s, alpha=.4)
    a2.set_title("情绪分")
    a2.grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--set", nargs="*", help="覆盖参数，如 strategy.take_profit=0.06")
    ap.add_argument("--sweep", action="store_true", help="参数敏感性测试")
    ap.add_argument("--strategy", help="回测 strategies/ 下的某个策略（文件名，不含 .py）")
    args = ap.parse_args()

    cfg = apply_overrides(load_config(), args.set)
    if args.start:
        cfg["backtest"]["start"] = args.start
    if args.end:
        cfg["backtest"]["end"] = args.end
    ind, senti, lhb, codes, board, ctx = prepare(cfg, with_ctx=True)
    OUT.mkdir(exist_ok=True)

    if args.sweep:
        return sweep(cfg, ind, senti, lhb, codes, board)

    if args.strategy:
        res = SL.run_backtest(ctx, SL.load(args.strategy), cfg)
        sig = res["signals"]
    else:
        sig = strategy.generate(ind, cfg["strategy"], codes, lhb, board)
        res = backtest.run(ind, sig, senti, cfg)
    print("\n========== 回测结果 ==========")
    print(f"区间 {cfg['backtest']['start']} ~ {cfg['backtest']['end']}，信号 {len(sig)} 个")
    for k, v in res["metrics"].items():
        print(f"  {k:<10} {v}")
    print("\n分年度：")
    print(yearly(res["equity"]).to_string())
    if len(res["trades"]):
        print("\n卖出原因：")
        print(res["trades"].groupby("reason")["ret"].agg(["count", "mean"]).to_string())

    res["equity"].to_csv(OUT / "equity.csv", encoding="utf-8-sig")
    res["trades"].to_csv(OUT / "trades.csv", index=False, encoding="utf-8-sig")
    sig.to_csv(OUT / "signals.csv", index=False, encoding="utf-8-sig")
    senti.to_csv(OUT / "sentiment.csv", encoding="utf-8-sig")
    plot(res["equity"], senti, OUT / "equity.png")
    print(f"\n明细已保存到 {OUT}")


def sweep(cfg, ind, senti, lhb, codes, board):
    """参数稍微改动收益就大变，说明是过拟合。好的策略在参数邻域内表现平稳。"""
    grid = {
        "strategy.buy_offset": [-0.01, -0.02, -0.03],
        "strategy.take_profit": [0.06, 0.08, 0.10],
        "strategy.stop_loss": [-0.04, -0.05, -0.07],
        "sentiment.min_score": [0, 40],
    }
    rows = []
    sig = strategy.generate(ind, cfg["strategy"], codes, lhb, board)  # 以上参数只影响成交，不影响选股
    for combo in itertools.product(*grid.values()):
        c = copy.deepcopy(cfg)
        apply_overrides(c, [f"{k}={v}" for k, v in zip(grid, combo)])
        m = backtest.run(ind, sig, senti, c)["metrics"]
        rows.append({**dict(zip(grid, combo)), **{k: m.get(k) for k in ("年化收益率", "最大回撤", "夏普比率", "交易次数", "胜率")}})
        print(rows[-1], flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "sweep.csv", index=False, encoding="utf-8-sig")
    print(f"\n已保存 {OUT / 'sweep.csv'}")


if __name__ == "__main__":
    main()
