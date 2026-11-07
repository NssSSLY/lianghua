"""行情看板用的市场数据：大盘指数、资金流、板块排行与板块指数。

数据源与回退：
- 大盘指数日线：新浪（stock_zh_index_daily）
- 板块排行/板块资金流：同花顺（stock_fund_flow_concept / industry），稳定
- 板块指数K线：同花顺（stock_board_concept_index_ths / industry_index_ths）
- 个股资金流（主力/超大单/大单/中单/小单，近约120个交易日）：东方财富 HTTP 接口
  东财会按访问频率限流，失败后熔断 10 分钟，期间直接返回 None，页面提示「暂不可用」
- 同花顺个股资金流（只有流入/流出/净额，没有大小单拆分）：每日由 archive_pools.py 归档积累
所有结果缓存到 data/market/，同一交易日内不重复请求。
"""
import re
import time
from pathlib import Path

import pandas as pd
import requests

from . import data  # noqa: F401  (设置国内站点直连)
import akshare as ak

INDEXES = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
           "sh000688": "科创50", "sh000852": "中证1000"}


# ---------------------------------------------------------------- 工具
def cn_num(x):
    """'1.69亿' / '-817.49万' / '20.01%' / 3.2 -> float（元 / 百分数原值）"""
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "")
    if s in ("", "-", "--", "nan", "None"):
        return float("nan")
    m = re.match(r"^(-?[\d.]+)\s*(亿|万|%)?$", s)
    if not m:
        return float("nan")
    v = float(m.group(1))
    return v * {"亿": 1e8, "万": 1e4}.get(m.group(2), 1)


def _cache(data_dir, name) -> Path:
    p = Path(data_dir) / "market" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _fresh(p: Path, seconds) -> bool:
    return p.exists() and time.time() - p.stat().st_mtime < seconds


# ---------------------------------------------------------------- 大盘指数
def index_daily(data_dir, symbol="sh000001", days=500) -> pd.DataFrame:
    p = _cache(data_dir, f"index_{symbol}.csv")
    if not _fresh(p, 1800):
        try:
            df = ak.stock_zh_index_daily(symbol=symbol)
            df.to_csv(p, index=False)
        except Exception:
            if not p.exists():
                raise
    df = pd.read_csv(p, parse_dates=["date"])
    return df.tail(days).reset_index(drop=True)


# ---------------------------------------------------------------- 同花顺 板块/个股资金流
BOARD_PERIODS = ["即时", "3日排行", "5日排行", "10日排行", "20日排行"]


def ths_board_flow(data_dir, kind="concept", period="即时") -> pd.DataFrame:
    """板块资金流排行。返回 name/index/pct/inflow/outflow/net（元）/count/leader/leader_pct"""
    p = _cache(data_dir, f"ths_{kind}_{period}.csv")
    if not _fresh(p, 300):
        fn = ak.stock_fund_flow_concept if kind == "concept" else ak.stock_fund_flow_industry
        raw = fn(symbol=period)
        df = pd.DataFrame({
            "name": raw["行业"],
            "index": raw["行业指数"].map(cn_num),
            "pct": raw.get("行业-涨跌幅", raw.get("阶段涨跌幅")).map(cn_num),
            "inflow": raw["流入资金"].map(cn_num) * 1e8,   # 同花顺板块资金单位是亿
            "outflow": raw["流出资金"].map(cn_num) * 1e8,
            "net": raw["净额"].map(cn_num) * 1e8,
            "count": raw["公司家数"],
            "leader": raw.get("领涨股", ""),
            "leader_pct": raw["领涨股-涨跌幅"].map(cn_num) if "领涨股-涨跌幅" in raw else float("nan"),
        })
        df.to_csv(p, index=False, encoding="utf-8-sig")
    df = pd.read_csv(p)
    if kind == "concept":  # 去掉指数型/泛概念（不代表题材）
        df = df[~df["name"].map(lambda n: any(x in n for x in NOT_THEME))]
    return df


NOT_THEME = ("同花顺", "证金", "高股息", "超级品牌", "融资融券", "沪股通", "深股通", "MSCI", "标普", "富时",
             "QFII", "社保", "基金重仓", "预增", "预减", "预盈", "ST板块", "参股", "专精特新", "中特估", "破净",
             "央企国企改革", "地方国资改革", "三季报", "中报", "年报", "一季报")


def ths_stock_flow(period="即时") -> pd.DataFrame:
    raw = ak.stock_fund_flow_individual(symbol=period)
    out = pd.DataFrame({"code": raw["股票代码"].astype(str).str.zfill(6), "name": raw["股票简称"],
                        "price": raw["最新价"].map(cn_num)})
    if "净额" in raw:
        out["pct"] = raw["涨跌幅"].map(cn_num)
        out["turnover"] = raw["换手率"].map(cn_num)
        out["inflow"] = raw["流入资金"].map(cn_num)
        out["outflow"] = raw["流出资金"].map(cn_num)
        out["net"] = raw["净额"].map(cn_num)
        out["amount"] = raw["成交额"].map(cn_num)
    else:
        out["pct"] = raw["阶段涨跌幅"].map(cn_num)
        out["net"] = raw["资金流入净额"].map(cn_num)
    return out


def ths_board_index(data_dir, name, kind="concept", start="20250101") -> pd.DataFrame:
    safe = re.sub(r'[\\/:*?"<>|]', "_", name)
    p = _cache(data_dir, f"bkidx_{kind}_{safe}.csv")
    if not _fresh(p, 1800):
        fn = ak.stock_board_concept_index_ths if kind == "concept" else ak.stock_board_industry_index_ths
        end = pd.Timestamp.today().strftime("%Y%m%d")
        raw = fn(symbol=name, start_date=start, end_date=end)
        df = raw.rename(columns={"日期": "date", "开盘价": "open", "最高价": "high", "最低价": "low",
                                 "收盘价": "close", "成交量": "volume", "成交额": "amount"})
        df.to_csv(p, index=False)
    return pd.read_csv(p, parse_dates=["date"])


# ---------------------------------------------------------------- 腾讯实时行情（稳定、免登录）
def tx_quote(codes) -> dict:
    """{code: {name, price, pre_close, open, high, low, pct, volume(股), amount(元), time}}"""
    syms = ",".join(("sh" if c.startswith("6") else "sz") + c for c in codes)
    try:
        r = requests.get(f"http://qt.gtimg.cn/q={syms}", timeout=5, proxies={"http": None, "https": None})
    except Exception:
        return {}
    out = {}
    for line in r.content.decode("gbk", "replace").split(";"):
        if "=" not in line or '"' not in line:
            continue
        f = line.split('"')[1].split("~")
        if len(f) < 38:
            continue
        num = lambda i: float(f[i]) if f[i] not in ("", "-") else float("nan")
        out[f[2]] = dict(name=f[1], price=num(3), pre_close=num(4), open=num(5), high=num(33), low=num(34),
                         pct=num(32), volume=num(6) * 100, amount=num(37) * 1e4, time=f[30])
    return out


# ---------------------------------------------------------------- 数据源健康检查
SOURCES = [
    ("新浪日线", "历史日线（主力源）", "http://finance.sina.com.cn/realstock/company/sh600000/hisdata/klc_kl.js"),
    ("腾讯行情", "日线备用源、实时行情", "http://qt.gtimg.cn/q=sh600000"),
    ("东财K线", "板块K线/个股资金流历史(push2his)", "http://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600000&klt=101&fqt=1&lmt=1&end=20500101&fields1=f1&fields2=f51"),
    ("东财排行", "全市场资金流/板块排行(push2)", "http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&fs=m:90+t:3&fields=f12"),
    ("东财涨停池", "涨停/炸板/跌停池(push2ex)", "https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=1&sort=fbt:asc&date=20261023"),
    ("东财数据中心", "龙虎榜/机构/席位(datacenter)", "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=SECURITY_CODE&pageSize=1&pageNumber=1"),
    ("同花顺", "板块资金流/概念指数", "http://data.10jqka.com.cn/funds/gnzjl/"),
    ("上交所", "官方公告数据", "https://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_SJ_GPSJ_GPSJZM_TJSJ_L"),
    ("深交所", "官方证券列表", "https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1110&TABKEY=tab1&PAGENO=1"),
]


def check_sources() -> list:
    from concurrent.futures import ThreadPoolExecutor

    def one(item):
        name, use, url = item
        t = time.time()
        try:
            ref = "https://www.szse.cn/market/product/stock/list/index.html" if "szse" in url else "https://www.sse.com.cn/"
            r = requests.get(url, timeout=6, headers={**_EM.H, "Referer": ref, "Accept": "application/json, text/plain, */*"},
                             proxies={"http": None, "https": None})
            ok = r.status_code < 400 and len(r.content) > 20
            return dict(name=name, use=use, ok=ok, ms=int((time.time() - t) * 1000), msg=f"HTTP {r.status_code}")
        except Exception as e:
            return dict(name=name, use=use, ok=False, ms=int((time.time() - t) * 1000), msg=type(e).__name__)

    with ThreadPoolExecutor(len(SOURCES)) as ex:
        return list(ex.map(one, SOURCES))


# ---------------------------------------------------------------- 东方财富（HTTP，带熔断）
class _EM:
    H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Connection": "close"}
    down_until = 0.0

    def get(self, url, params, retries=2):
        if time.time() < self.down_until:
            return None
        for i in range(retries + 1):
            try:
                r = requests.get(url, params=params, headers=self.H, timeout=8,
                                 proxies={"http": None, "https": None})
                return r.json().get("data")
            except Exception:
                time.sleep(1 + i)
        self.down_until = time.time() + 600  # 连续失败：10 分钟内不再尝试
        return None

    @property
    def available(self):
        return time.time() >= self.down_until


EM = _EM()
_FF = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
FLOW_COLS = ["date", "main", "small", "mid", "large", "super",
             "main_pct", "small_pct", "mid_pct", "large_pct", "super_pct", "close", "pct"]


def _parse_flow(klines):
    rows = [k.split(",")[:13] for k in klines]
    df = pd.DataFrame(rows, columns=FLOW_COLS)
    df["date"] = pd.to_datetime(df["date"])
    for c in FLOW_COLS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def em_stock_flow(data_dir, code) -> pd.DataFrame | None:
    """个股每日资金流（元）。本地缓存会不断合并新数据，所以历史会越积越长。"""
    p = _cache(data_dir, f"flow/{code}.csv")
    p.parent.mkdir(exist_ok=True)
    old = pd.read_csv(p, parse_dates=["date"]) if p.exists() else None
    if old is not None and _fresh(p, 3600 * 6):
        return old
    secid = ("1." if code.startswith("6") else "0.") + code
    d = EM.get("http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
               dict(lmt=0, klt=101, secid=secid, fields1="f1,f2,f3,f7", fields2=_FF))
    if not d or not d.get("klines"):
        return old
    new = _parse_flow(d["klines"])
    if old is not None:
        new = pd.concat([old[old["date"] < new["date"].min()], new])
    new.to_csv(p, index=False)
    return new


def em_market_flow(data_dir) -> pd.DataFrame | None:
    """沪深两市合计每日资金流。"""
    p = _cache(data_dir, "flow_market.csv")
    old = pd.read_csv(p, parse_dates=["date"]) if p.exists() else None
    if old is not None and _fresh(p, 1800):
        return old
    d = EM.get("http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
               dict(lmt=0, klt=101, secid="1.000001", secid2="0.399001", fields1="f1,f2,f3,f7", fields2=_FF))
    if not d or not d.get("klines"):
        return old
    new = _parse_flow(d["klines"])
    if old is not None:
        new = pd.concat([old[old["date"] < new["date"].min()], new])
    new.to_csv(p, index=False)
    return new


def em_lhb_seats(data_dir, date, code=None) -> pd.DataFrame | None:
    """龙虎榜营业部席位明细（东财数据中心，支持历史）。
    code=None 时返回当天全部上榜股票的席位。返回 code/date/side(买/卖)/rank/seat/buy/sell/net"""
    d = pd.Timestamp(date).strftime("%Y-%m-%d")
    key = f"seats/{d}_{code or 'all'}.csv"
    p = _cache(data_dir, key)
    p.parent.mkdir(exist_ok=True)
    if p.exists():
        return pd.read_csv(p, dtype={"code": str})
    flt = f"(TRADE_DATE='{d}')" + (f'(SECURITY_CODE="{code}")' if code else "")
    rows = []
    for side, rpt, sort in (("买", "RPT_BILLBOARD_DAILYDETAILSBUY", "BUY"), ("卖", "RPT_BILLBOARD_DAILYDETAILSSELL", "SELL")):
        page = 1
        while True:
            try:
                r = requests.get("https://datacenter-web.eastmoney.com/api/data/v1/get", timeout=10,
                                 headers=_EM.H, proxies={"http": None, "https": None},
                                 params=dict(reportName=rpt, columns="ALL", filter=flt, pageNumber=page, pageSize=500,
                                             sortColumns=sort, sortTypes=-1, source="WEB", client="WEB"))
                j = r.json()
            except Exception:
                return None
            res = j.get("result")
            if not res:
                break
            for i, x in enumerate(res["data"]):
                rows.append(dict(code=x["SECURITY_CODE"], date=d, side=side, seat=x["OPERATEDEPT_NAME"],
                                 buy=x.get("BUY"), sell=x.get("SELL"), net=x.get("NET"), reason=x.get("EXPLANATION")))
            if page >= res.get("pages", 1):
                break
            page += 1
    df = pd.DataFrame(rows, columns=["code", "date", "side", "seat", "buy", "sell", "net", "reason"])
    if len(df):
        df["rank"] = df.groupby(["code", "side", "reason"]).cumcount() + 1
        if pd.Timestamp(d) < pd.Timestamp.today().normalize():  # 当天的可能还没发布完整，不缓存
            df.to_csv(p, index=False, encoding="utf-8-sig")
    return df


def em_all_stock_flow() -> pd.DataFrame | None:
    """全市场个股当日资金流（主力/超大/大/中/小单净额），供每日归档。"""
    fields = "f12,f14,f2,f3,f6,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"
    rows, pn = [], 1
    while True:
        d = EM.get("http://push2.eastmoney.com/api/qt/clist/get",
                   dict(pn=pn, pz=500, po=1, np=1, fltt=2, invt=2, fid="f62",
                        fs="m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048", fields=fields))
        if not d:
            return None if not rows else pd.DataFrame(rows)
        rows += d["diff"]
        if len(rows) >= d["total"]:
            break
        pn += 1
        time.sleep(0.5)
    df = pd.DataFrame(rows).rename(columns={
        "f12": "code", "f14": "name", "f2": "price", "f3": "pct", "f6": "amount",
        "f62": "main", "f184": "main_pct", "f66": "super", "f69": "super_pct", "f72": "large",
        "f75": "large_pct", "f78": "mid", "f81": "mid_pct", "f84": "small", "f87": "small_pct"})
    return df
