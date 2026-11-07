"""概念/行业板块强度与主线识别。

数据：新浪概念板块（约 175 个）+ 新浪行业板块（约 49 个）的成分股，缓存于 data/boards.csv。
注意：成分股只能拿到「当前」名单，历史上某只股票何时被纳入概念无从得知。
个股常在炒作后才被归入热门概念，因此回测会偏乐观（前视偏差），实盘效果要打折看。

板块强度完全由本地日线按当时数据计算（point-in-time）：
- 板块涨停数：成分股当日涨停家数
- 主线板块：近 board_window 日涨停总数 / √成分股数 排名前 board_top_k，且涨停总数 >= board_min_zt，
  且 T 日仍有 >= board_need_today_zt 家涨停（主线还没退潮）
- 板块龙头：成分股中近 lookback_days 最高连板数最高者（并列都算）
"""
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import data  # noqa: F401  (设置直连代理)
import akshare as ak

# 新浪概念里的泛概念/指数成分类，不代表题材
EXCLUDE = ("融资融券", "深股通", "沪股通", "MSCI", "标普", "富时", "转融券", "QFII", "社保",
           "基金重仓", "预盈预增", "预亏预减", "ST板块", "次新股", "股权激励", "送转",
           "参股", "专精特新", "业绩", "重组", "央企", "国企", "地方", "破净", "高送转")


def download(data_dir, refresh=False) -> pd.DataFrame:
    path = Path(data_dir) / "boards.csv"
    if path.exists() and not refresh and time.time() - path.stat().st_mtime < 86400 * 7:
        return pd.read_csv(path, dtype={"code": str})
    rows = []
    for kind, ind in (("概念", "概念"), ("行业", "新浪行业")):
        spot = ak.stock_sector_spot(indicator=ind)
        for _, s in spot.iterrows():
            if any(x in s["板块"] for x in EXCLUDE):
                continue
            for i in range(3):
                try:
                    d = ak.stock_sector_detail(sector=s["label"])
                    break
                except Exception:
                    time.sleep(2)
            else:
                print(f"  板块 {s['板块']} 获取失败")
                continue
            for c in d["code"].astype(str).str.zfill(6):
                rows.append((kind, s["板块"], c))
            time.sleep(0.2)
    df = pd.DataFrame(rows, columns=["kind", "board", "code"]).drop_duplicates()
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return df


def membership(members: pd.DataFrame, codes, kinds=("概念",), min_size=5, max_size=300) -> pd.DataFrame:
    """成分矩阵 (股票 × 板块) 0/1，剔除过小/过大（泛概念）的板块。"""
    m = members[members["code"].isin(codes) & members["kind"].isin(kinds)]
    m = m[~m["board"].map(lambda b: any(x in b for x in EXCLUDE))]
    size = m.groupby("board")["code"].nunique()
    keep = size[(size >= min_size) & (size <= max_size)].index
    m = m[m["board"].isin(keep)]
    M = pd.crosstab(m["code"], m["board"]).clip(upper=1)
    return M.reindex(index=list(codes), fill_value=0)


def compute(ind: dict, M: pd.DataFrame, p: dict) -> dict:
    """返回 dict：
    board_zt     (日期 × 板块) 当日涨停家数
    mainline     (日期 × 板块) 是否主线
    in_mainline  (日期 × 股票) 是否属于某个主线板块
    leader       (日期 × 股票) 是否是某个主线板块的龙头
    """
    codes = M.index
    zt = ind["is_zt"].reindex(columns=codes, fill_value=False).astype(float)
    Mv = M.values.astype(float)
    board_zt = pd.DataFrame(zt.values @ Mv, index=zt.index, columns=M.columns)

    win = board_zt.rolling(p["board_window"], min_periods=1).sum()
    # 按 涨停数/√成分数 排名，避免大板块仅因成分多而上榜
    strength = win / np.sqrt(M.sum(axis=0).values)
    rank = strength.rank(axis=1, ascending=False, method="min")
    mainline = (rank <= p["board_top_k"]) & (win >= p["board_min_zt"]) \
        & (board_zt >= p["board_need_today_zt"])

    in_main = pd.DataFrame((mainline.values.astype(float) @ Mv.T) > 0, index=zt.index, columns=codes)

    ms = ind["streak"].reindex(columns=codes).rolling(p["lookback_days"], min_periods=1).max().fillna(0)
    msv = ms.values
    leader = np.zeros_like(msv, dtype=bool)
    mv = mainline.values
    for b in range(Mv.shape[1]):
        idx = np.flatnonzero(Mv[:, b])
        if not len(idx) or not mv[:, b].any():
            continue
        sub = msv[:, idx]
        bmax = sub.max(axis=1, keepdims=True)
        leader[:, idx] |= (sub >= bmax) & (bmax >= 1) & mv[:, b:b + 1]
    leader = pd.DataFrame(leader, index=zt.index, columns=codes)
    return dict(board_zt=board_zt, board_win=win, mainline=mainline, in_mainline=in_main, leader=leader)


def stock_boards(M: pd.DataFrame, mainline_row: pd.Series, code: str) -> str:
    """某只股票当天所属的主线板块名称（展示用）。"""
    if code not in M.index:
        return ""
    bs = M.columns[(M.loc[code].values > 0) & mainline_row.reindex(M.columns, fill_value=False).values]
    return ",".join(bs)
