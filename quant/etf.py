"""ETF 池与数据（腾讯不复权日线 + 基金公告的分红/拆分算出的复权因子）。

腾讯的 ETF 前复权价不可靠：它按「减去分红金额」的方式复权并四舍五入到 0.001，拆分后的低价段
会把日收益放大（如 512690 全程被算成 4.15 倍，累计净值只有 1.69 倍），所以这里不用它。

ETF 卖出免印花税，交易次数少，是小资金降低成本的主要手段。
池子覆盖：宽基 / 行业主题 / 跨境 / 商品 / 债券 / 货币（避险）。可以在 ETF_POOL 里增删。
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import akshare as ak
import pandas as pd

from . import data

ETF_POOL = {
    # 宽基
    "510300": "沪深300ETF", "510500": "中证500ETF", "512100": "中证1000ETF", "510050": "上证50ETF",
    "159915": "创业板ETF", "588000": "科创50ETF",
    # 行业 / 主题
    "512880": "证券ETF", "512800": "银行ETF", "512660": "军工ETF", "512480": "半导体ETF",
    "515030": "新能源车ETF", "515790": "光伏ETF", "512690": "酒ETF", "159928": "消费ETF",
    "512010": "医药ETF", "512170": "医疗ETF", "512400": "有色金属ETF", "515220": "煤炭ETF",
    "515880": "通信ETF", "159869": "游戏ETF", "515070": "人工智能ETF", "512200": "房地产ETF",
    "159611": "电力ETF", "512980": "传媒ETF", "159825": "农业ETF", "515050": "5GETF",
    # 跨境
    "513100": "纳指ETF", "513500": "标普500ETF", "159920": "恒生ETF", "513050": "中概互联网ETF",
    "513180": "恒生科技ETF", "513520": "日经ETF",
    # 商品 / 债券 / 货币
    "518880": "黄金ETF", "159985": "豆粕ETF", "511260": "十年国债ETF", "511010": "国债ETF",
    "511880": "银华日利(货币)",
}
CASH_ETF = "511880"  # 避险时持有的货币 ETF


def symbol(code: str) -> str:
    return ("sh" if code.startswith(("5", "6")) else "sz") + code


_V8_LOCK = threading.Lock()  # 这个 akshare 接口内部用 V8，多线程同时调用会直接崩溃


def _fund_info(code: str, indicator: str) -> pd.DataFrame:
    with _V8_LOCK:
        return ak.fund_open_fund_info_em(symbol=code, indicator=indicator)


def fetch_events(code: str) -> list:
    """基金公告的除权事件：[(日期, 'div', 每份分红元) / (日期, 'split', 拆分后份额倍数)]。"""
    out = []
    div = _fund_info(code, "分红送配详情")
    if len(div):
        for d, txt in zip(div.iloc[:, 2], div.iloc[:, 3]):  # 除息日、每10份分红
            out.append((pd.Timestamp(d), "div", float(re.search(r"([\d.]+)元", str(txt)).group(1)) / 10))
    sp = _fund_info(code, "拆分详情")
    if len(sp):
        for d, txt in zip(sp.iloc[:, 1], sp.iloc[:, 3]):    # 拆分折算日、拆分折算比例
            m = re.search(r"1:([\d.]+)", str(txt))
            if m:
                out.append((pd.Timestamp(d), "split", float(m.group(1))))
    return out


def event_factor(df: pd.DataFrame, events: list) -> pd.Series:
    """由除权事件算前复权因子（最新一天=1，事件之间不变）。拆分的实际生效日可能晚几天，取价格跳变最吻合的一天。"""
    close = df["close"].values
    dates = pd.DatetimeIndex(df["date"])
    f = pd.Series(1.0, index=range(len(df)))
    for d, kind, val in sorted(events):
        i = dates.searchsorted(d)
        if i <= 0 or i >= len(df):
            continue
        if kind == "div":
            f.iloc[:i] *= (close[i - 1] - val) / close[i - 1]
        else:
            js = range(max(i - 1, 1), min(i + 5, len(df)))
            j = min(js, key=lambda x: abs(close[x] / close[x - 1] * val - 1))
            f.iloc[:j] /= val
    return f


def update(data_dir, start, end, codes=None, refresh_names=True):
    """下载/更新 ETF 日线到 data/etf/。ETF 数据量小，每次整段重下（保证复权因子最新）。"""
    out = Path(data_dir) / "etf"
    out.mkdir(parents=True, exist_ok=True)
    codes = codes or list(ETF_POOL)

    def one(c):
        for i in range(3):
            try:
                df = data._fetch_tx(c, start, end, sym=symbol(c))
                if df is None or df.empty:
                    return c, "empty"
                if c == CASH_ETF:  # 货币 ETF 没有分红公告（按份额结转），价格约 100、取整误差很小，用腾讯因子
                    df["factor"] = df["factor"].ffill().fillna(1.0)
                else:
                    df["factor"] = event_factor(df, fetch_events(c)).values
                df.to_csv(out / f"{c}.csv", index=False)
                return c, len(df)
            except Exception as e:
                err = e
                time.sleep(1 + i)
        return c, f"失败 {type(err).__name__}"

    with ThreadPoolExecutor(4) as ex:
        res = dict(ex.map(one, codes))
    if refresh_names:  # 用腾讯实时行情核对名称
        from . import market
        q = market.tx_quote(codes)
        names = {c: q.get(c, {}).get("name") or ETF_POOL.get(c, c) for c in codes}
        pd.Series(names, name="name").rename_axis("code").to_csv(out / "_names.csv", encoding="utf-8-sig")
    return res


def names(data_dir) -> dict:
    p = Path(data_dir) / "etf" / "_names.csv"
    if p.exists():
        return pd.read_csv(p, dtype={"code": str}).set_index("code")["name"].to_dict()
    return dict(ETF_POOL)


def load_panel(data_dir, start=None, end=None) -> dict:
    d = Path(data_dir) / "etf"
    frames = []
    for p in d.glob("[0-9]*.csv"):
        df = pd.read_csv(p, parse_dates=["date"]).drop_duplicates("date", keep="last")
        df["code"] = p.stem
        frames.append(df)
    if not frames:
        raise RuntimeError("没有 ETF 数据，请先运行 python update_data.py --etf")
    long = pd.concat(frames, ignore_index=True)
    panel = {f: long.pivot(index="date", columns="code", values=f).astype(float) for f in data.FIELDS}
    sl = slice(pd.Timestamp(start) if start else None, pd.Timestamp(end) if end else None)
    return {k: v.loc[sl] for k, v in panel.items()}
