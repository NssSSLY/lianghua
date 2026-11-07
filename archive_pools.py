"""每日盘后归档东财涨停池/炸板池/跌停池/龙虎榜，积累没有前视偏差的题材数据。

东财这些池子只能回查最近 2~3 周，过期就没了，所以要每天存。
另外每周存一份新浪概念板块成分股快照，以后可以按「当时的成分股」做回测。

用法：
    python archive_pools.py                # 归档今天（15:30 前运行则归档上一交易日）
    python archive_pools.py --backfill 30  # 回补最近 30 个工作日中还能查到的
    python archive_pools.py --date 2026-10-23 --force

输出 data/archive/YYYYMMDD/：
    zt.csv      涨停池（连板数、首次/最后封板时间、封板资金、炸板次数、所属行业）
    zb.csv      炸板池
    dt.csv      跌停池
    prev.csv    昨日涨停股今日表现（接力溢价）
    strong.csv  强势股池（入选理由：60日新高/近期多次涨停等）
    lhb.csv     龙虎榜明细（上榜原因、净买额、解读）
    lhb_jg.csv  龙虎榜机构买卖统计
data/archive/boards/boards_YYYYMMDD.csv  每周一份概念/行业成分股快照
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd

from quant import data  # noqa: F401  (设置国内站点直连)
from quant import boards, market, ths
from quant.config import load_config
import akshare as ak

POOLS = {
    "zt": lambda d: ak.stock_zt_pool_em(date=d),
    "zb": lambda d: ak.stock_zt_pool_zbgc_em(date=d),
    "dt": lambda d: ak.stock_zt_pool_dtgc_em(date=d),
    "prev": lambda d: ak.stock_zt_pool_previous_em(date=d),
    "strong": lambda d: ak.stock_zt_pool_strong_em(date=d),
    "lhb": lambda d: ak.stock_lhb_detail_em(start_date=d, end_date=d),
    "lhb_jg": lambda d: ak.stock_lhb_jgmmtj_em(start_date=d, end_date=d),
    "lhb_seats": lambda d: market.em_lhb_seats(DATA_DIR, d),     # 营业部席位明细（游资/机构/北向）
}

# 只能取「当天」的快照（接口不支持指定日期），只在归档当天时抓取，回补时跳过
TODAY_ONLY = {
    "flow_ths": lambda d: market.ths_stock_flow("即时"),          # 同花顺个股资金流
    "flow_concept": lambda d: market.ths_board_flow(DATA_DIR, "concept", "即时"),
    "flow_industry": lambda d: market.ths_board_flow(DATA_DIR, "industry", "即时"),
    "flow_em": lambda d: market.em_all_stock_flow(),              # 东财 主力/大单/小单（可能被限流）
    "ths_hot_stock": lambda d: ths.hot_stocks("day"),             # 同花顺个股人气榜（24小时）
    "ths_hot_concept": lambda d: ths.hot_plates("concept"),       # 同花顺热门概念
    "ths_hot_industry": lambda d: ths.hot_plates("industry"),     # 同花顺热门行业
}
DATA_DIR = None


def default_date() -> pd.Timestamp:
    now = pd.Timestamp.now()
    if now.hour * 60 + now.minute < 15 * 60 + 30:
        now -= pd.Timedelta(days=1)
    while now.weekday() >= 5:
        now -= pd.Timedelta(days=1)
    return now.normalize()


def fetch(fn, d, retries=3):
    for i in range(retries):
        try:
            df = fn(d)
            return df if df is not None else pd.DataFrame()
        except TypeError:
            return pd.DataFrame()  # 龙虎榜在非交易日返回空，akshare 内部报 NoneType 错
        except ValueError as e:
            if "最近" in str(e):
                return pd.DataFrame()  # 超出可回查范围
            err = e
        except Exception as e:
            err = e
            time.sleep(2 + i * 3)
    # 炸板池/跌停池 超出可查范围时接口会抛异常，当作无数据
    print(f"    失败: {type(err).__name__}: {str(err)[:80]}")
    return None


def archive_day(root: Path, day: pd.Timestamp, force=False, today_only=False) -> dict:
    d = day.strftime("%Y%m%d")
    out = root / d
    got = {}
    sources = {**POOLS, **(TODAY_ONLY if today_only else {})}
    for name, fn in sources.items():
        p = out / f"{name}.csv"
        if p.exists() and not force:
            got[name] = "已存在"
            continue
        try:
            df = fetch(fn, d)
            if df is None:
                got[name] = "失败"
                continue
            if df.empty:
                got[name] = 0
                continue
            for col in ("代码", "code"):
                if col in df.columns:
                    df[col] = df[col].astype(str).str.zfill(6)
            if "date" in df.columns:          # 部分数据源（如营业部席位）自带 date 列
                df["date"] = day.strftime("%Y-%m-%d")
            else:
                df.insert(0, "date", day.strftime("%Y-%m-%d"))
            out.mkdir(parents=True, exist_ok=True)
            df.to_csv(p, index=False, encoding="utf-8-sig")
            got[name] = len(df)
        except Exception as e:  # 单个数据源出错不影响其他数据源和后续的日线更新
            print(f"    {name} 出错: {type(e).__name__}: {str(e)[:80]}")
            got[name] = "失败"
    return got


def snapshot_boards(data_dir, root: Path, today: pd.Timestamp):
    bdir = root / "boards"
    bdir.mkdir(parents=True, exist_ok=True)
    snaps = sorted(bdir.glob("boards_*.csv"))
    if snaps and today - pd.Timestamp(snaps[-1].stem.split("_")[1]) < pd.Timedelta(days=7):
        return None
    m = boards.download(data_dir, refresh=True)
    p = bdir / f"boards_{today:%Y%m%d}.csv"
    m.to_csv(p, index=False, encoding="utf-8-sig")
    return p, len(m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--backfill", type=int, default=0, help="回补最近 N 个工作日")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    ap.add_argument("--no-boards", action="store_true")
    ap.add_argument("--ths-backfill", type=int, default=0, help="回补最近 N 个工作日的同花顺涨停原因（约能回查一年，N≈260）")
    args = ap.parse_args()

    global DATA_DIR
    cfg = load_config()
    DATA_DIR = cfg["data_dir"]
    root = Path(cfg["data_dir"]) / "archive"
    end = pd.Timestamp(args.date) if args.date else default_date()
    days = pd.bdate_range(end=end, periods=max(args.backfill, 1))[::-1]
    # 资金流快照只代表「今天收盘后」：仅当归档日就是最近一个已收盘交易日时才抓
    snap_day = default_date() if pd.Timestamp.now().weekday() < 5 and pd.Timestamp.now().hour * 60 + pd.Timestamp.now().minute >= 15 * 60 + 30 else None

    if args.ths_backfill:
        # 同花顺涨停原因约能回查一年：只回补同花顺涨停池 + 连板天梯
        tdays = pd.bdate_range(end=end, periods=args.ths_backfill)[::-1]
        stats = {}
        for i, day in enumerate(tdays, 1):
            r = ths.archive_day(DATA_DIR, day, args.force)
            stats[r if not r.endswith("只") else "成功"] = stats.get(r if not r.endswith("只") else "成功", 0) + 1
            if i % 20 == 0 or i == len(tdays):
                print(f"  同花顺回补 {i}/{len(tdays)} {stats}", flush=True)
            if r != "已存在":
                time.sleep(0.3)
        return

    n_ok = 0
    for day in days:
        got = archive_day(root, day, args.force, today_only=(day == snap_day))
        try:
            got["ths_zt"] = ths.archive_day(DATA_DIR, day, args.force)  # 同花顺涨停原因 + 连板天梯
        except Exception as e:
            got["ths_zt"] = f"失败 {type(e).__name__}"
        print(f"{day.date()}  " + "  ".join(f"{k}:{v}" for k, v in got.items()), flush=True)
        # 涨停池为 0：节假日或已超出可回查范围
        if got.get("zt") == "已存在" or (isinstance(got.get("zt"), int) and got["zt"] > 0):
            n_ok += 1

    if not args.no_boards:
        try:
            r = snapshot_boards(cfg["data_dir"], root, pd.Timestamp.today().normalize())
        except Exception as e:
            r = None
            print(f"板块成分快照失败：{type(e).__name__}: {str(e)[:80]}")
        if r:
            print(f"板块成分快照：{r[0].name}，{r[1]} 条")

    print(f"完成：{n_ok}/{len(days)} 天有涨停池数据")
    if n_ok == 0 and not args.backfill:
        sys.exit(1)  # 让计划任务记录失败


if __name__ == "__main__":
    main()
