"""下载/增量更新全市场日线和龙虎榜数据。

首次运行约 10~30 分钟（全市场约 4000+ 只），之后每天盘后增量更新几分钟。
用法：
    python update_data.py                 # 更新到今天
    python update_data.py --end 2026-10-23
    python update_data.py --codes 600000 000001   # 只更新指定股票（调试用）
"""
import argparse
import os
import time

import pandas as pd

from quant.config import load_config
from quant import data, etf


def default_end() -> str:
    """15:30 之前当天K线还没走完，只更新到前一天。"""
    now = pd.Timestamp.now()
    if now.hour * 60 + now.minute < 15 * 60 + 30:
        now -= pd.Timedelta(days=1)
    return now.strftime("%Y-%m-%d")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default=default_end())
    ap.add_argument("--codes", nargs="*")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-lhb", action="store_true")
    ap.add_argument("--no-etf", action="store_true")
    ap.add_argument("--source", choices=["em", "sina", "tx"], default="em",
                    help="优先数据源（失败自动回退其他源）：em=东方财富（默认，最快）/ sina / tx")
    args = ap.parse_args()
    os.environ["QUANT_SOURCE"] = args.source  # 子进程会继承

    cfg = load_config()
    ddir = cfg["data_dir"]
    sl = data.load_stock_list(ddir, refresh=True)
    codes = args.codes or data.download_codes(sl, cfg["markets"], cfg.get("include_st", True))
    print(f"更新日线 {len(codes)} 只，区间 {cfg['download_start']} ~ {args.end}")
    t = time.time()
    failed = data.update_all(codes, ddir, cfg["download_start"], args.end, args.workers)
    if failed:
        print(f"重试失败的 {len(failed)} 只 ...")
        failed = data.update_all(failed, ddir, cfg["download_start"], args.end, 1)
    print(f"日线完成，用时 {time.time() - t:.0f}s，仍失败 {len(failed)} 只: {failed[:20]}")

    if not args.no_etf and not args.codes:
        print("更新 ETF ...")
        r = etf.update(ddir, "2019-01-01", args.end)
        bad = {k: v for k, v in r.items() if not isinstance(v, int)}
        print(f"ETF {len(r) - len(bad)}/{len(r)} 只完成" + (f"，失败：{bad}" if bad else ""))

    if not args.no_lhb:
        print("更新龙虎榜 ...")
        lhb = data.load_lhb(ddir, cfg["download_start"], args.end)
        print(f"龙虎榜记录 {len(lhb)} 条")


if __name__ == "__main__":
    main()
