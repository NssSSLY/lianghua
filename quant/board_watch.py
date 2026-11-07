"""盘中封板提示：只提示、只记录，不下单。

回测（README 第七轮）没有证明打板能赚钱，缺的是盘中数据：封板那一刻的封单、之后炸不炸板、次日开盘多少。
这个模块把这些数据记下来，积累 2~3 个月后再判断。

data/watch/ 下的文件：
    YYYYMMDD.csv     当天事件（封板 / 炸板 / 回封）
    outcomes.csv     每只票当天首次封板的结果（日线同步后结算）
    holdings.json    你手动标记的真实持仓
    status.json      监控进程状态
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from . import data
from .config import load_config

CFG = load_config()
WATCH_DIR = Path(os.environ.get("QUANT_WATCH_DIR") or Path(CFG["data_dir"]) / "watch")
STOP_FILE = WATCH_DIR / "stop"
STATUS_FILE = WATCH_DIR / "status.json"
HOLD_FILE = WATCH_DIR / "holdings.json"
OUT_FILE = WATCH_DIR / "outcomes.csv"

EVENT_COLS = ["time", "code", "name", "event", "price", "seal_amt", "seal_pct", "float_mv", "boards",
              "one_word", "pick", "book"]
# 待验证的候选规则：10 点前封板、不是一字、封单 ≥ 流通市值 1%。历史回测里只知道收盘时的封单，这里记录封板当时的
PICK_BEFORE = "10:00"
PICK_MIN_SEAL = 1.0
MAX_BOOK = 2             # 纸面持仓每天最多 2 只（你的实际仓位上限）
COST = 0.005             # 买卖合计成本，与回测一致
STOP_LOSS = -0.05
SEALED_FILL = 0.3        # 悲观假设：封住的票只有 3 成能排上队


# ---------------------------------------------------------------- 行情
def _sym(code):
    return ("sh" if code.startswith("6") else "sz") + code


def parse_quote(line: str):
    """腾讯行情一行 → dict。字段序号：9/10 买一价量（手），19/20 卖一价量，30 时间，47/48 涨跌停价，72 流通股本。"""
    if "=" not in line or '"' not in line:
        return None
    f = line.split('"')[1].split("~")
    if len(f) < 73:
        return None

    def num(i):
        try:
            return float(f[i])
        except ValueError:
            return float("nan")
    return dict(code=f[2], name=f[1], price=num(3), pre_close=num(4), open=num(5), low=num(34), high=num(33),
                bid1=num(9), bid1_vol=num(10) * 100, ask1=num(19), ask1_vol=num(20) * 100, stamp=f[30],
                zt=num(47), dt=num(48), float_share=num(72))


def fetch_quotes(codes, batch=60, workers=4) -> dict:
    """全市场约 4600 只，每批 60 只。单批失败不影响其他批，下一轮会再取。"""
    def one(chunk):
        try:
            r = requests.get("http://qt.gtimg.cn/q=" + ",".join(_sym(c) for c in chunk), timeout=5,
                             proxies={"http": None, "https": None})
            return [q for q in map(parse_quote, r.content.decode("gbk", "replace").split(";")) if q]
        except requests.RequestException:
            return []
    chunks = [codes[i:i + batch] for i in range(0, len(codes), batch)]
    with ThreadPoolExecutor(workers) as ex:
        return {q["code"]: q for rows in ex.map(one, chunks) for q in rows}


def is_sealed(q) -> bool:
    """封死涨停：现价=涨停价、卖一没有挂单、买一有挂单。"""
    return q["zt"] > 0 and abs(q["price"] - q["zt"]) < 0.005 and q["ask1_vol"] == 0 and q["bid1_vol"] > 0


# ---------------------------------------------------------------- 事件识别
class Watcher:
    """喂入一轮行情，返回新事件。不碰网络和文件，方便用模拟行情测试。"""

    def __init__(self, prev_boards=None, history=None):
        self.prev_boards = prev_boards or {}
        self.sealed, self.seen, self.n_book = {}, set(), 0
        for e in history or []:      # 盘中重启：恢复当天状态，不重复提示
            self.seen.add(e["code"])
            self.sealed[e["code"]] = e["event"] != "炸板"
            self.n_book += int(str(e.get("book")) == "True") if e["event"] == "封板" else 0

    def update(self, quotes: dict, now: str) -> list:
        events = []
        for code, q in quotes.items():
            sealed = is_sealed(q)
            was = self.sealed.get(code, False)
            if sealed and not was:
                events.append(self._seal(q, now, code in self.seen))
                self.seen.add(code)
            elif was and not sealed and q["price"] < q["zt"] - 0.005:
                events.append(self._event(q, now, "炸板"))
            else:
                continue
            self.sealed[code] = sealed
        return events

    def _event(self, q, now, kind):
        fmv = q["price"] * q["float_share"]
        amt = q["bid1_vol"] * q["price"] if kind != "炸板" else 0.0
        return dict(time=now, code=q["code"], name=q["name"], event=kind, price=q["price"], seal_amt=round(amt),
                    seal_pct=round(amt / fmv * 100, 3) if fmv > 0 else float("nan"), float_mv=round(fmv),
                    boards=self.prev_boards.get(q["code"], 0) + 1, one_word=False, pick=False, book=False)

    def _seal(self, q, now, again):
        e = self._event(q, now, "回封" if again else "封板")
        if again:
            return e
        e["one_word"] = abs(q["low"] - q["zt"]) < 0.005
        e["pick"] = (now[:5] < PICK_BEFORE and not e["one_word"] and e["seal_pct"] >= PICK_MIN_SEAL)
        if e["pick"] and self.n_book < MAX_BOOK:
            e["book"] = True
            self.n_book += 1
        return e


# ---------------------------------------------------------------- 文件
def day_file(day: str) -> Path:
    return WATCH_DIR / f"{day}.csv"


def read_events(day: str) -> list:
    p = day_file(day)
    if not p.exists() or p.stat().st_size == 0:
        return []
    return pd.read_csv(p, dtype={"code": str}, encoding="utf-8-sig").to_dict("records")


def append_events(day: str, events: list):
    if not events:
        return
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    p = day_file(day)
    pd.DataFrame(events, columns=EVENT_COLS).to_csv(p, mode="a", index=False, header=not p.exists(), encoding="utf-8-sig")


def write_status(**kw):
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({**kw, "updated": time.time()}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATUS_FILE)


def read_status() -> dict:
    try:
        s = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"running": False, "message": "未启动"}
    s["running"] = s.get("alive", False) and time.time() - s.get("updated", 0) < 90
    return s


def prev_boards(ddir=None) -> dict:
    """上一个归档日的连板数，用来算今天是第几板。"""
    root = Path(ddir or CFG["data_dir"]) / "archive"
    today = datetime.now().strftime("%Y%m%d")
    days = sorted((x for x in os.listdir(root) if x.isdigit() and x < today), reverse=True) if root.exists() else []
    for d in days:
        p = root / d / "zt.csv"
        if p.exists() and p.stat().st_size > 20:
            z = pd.read_csv(p, dtype={"代码": str}, encoding="utf-8-sig")
            if "连板数" in z:
                return {str(c).zfill(6): int(n) for c, n in zip(z["代码"], z["连板数"])}
    return {}


def watch_codes() -> list:
    return list(data.tradable_codes(data.load_stock_list(CFG["data_dir"]), CFG["markets"]))


# ---------------------------------------------------------------- 真实持仓与卖出提示
def read_holdings() -> list:
    try:
        return json.loads(HOLD_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def write_holdings(rows: list):
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    HOLD_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def sell_advice(buy_px: float, buy_day: str, q: dict, today: str, now: str) -> tuple:
    """按回测里的卖出规则给提示：买入次日开盘卖；封死涨停继续拿，一炸板就卖；跌破 -5% 止损。返回 (状态, 文案)。"""
    if buy_day >= today:
        return "wait", "今天买入，T+1 今天不能卖"
    if not q or not q["price"] > 0:
        return "wait", "没有行情（可能停牌）"
    if now < "09:25":
        return "wait", "等 09:25 集合竞价结果"
    if is_sealed(q):
        return "hold", "封死涨停，继续拿；一炸板就卖"
    ret = q["price"] / buy_px - 1
    if ret <= STOP_LOSS:
        return "sell", f"已跌 {ret:.1%}，跌破止损，卖出"
    return "sell", f"没有封涨停（{ret:+.1%}），按规则卖出"


def prompts(now_dt=None) -> dict:
    """真实持仓 + 今天之前最近一天的纸面持仓，对应的卖出提示。"""
    now_dt = now_dt or datetime.now()
    today, now = now_dt.strftime("%Y%m%d"), now_dt.strftime("%H:%M")
    days = sorted(p.stem for p in WATCH_DIR.glob("[0-9]*.csv") if p.stem < today) if WATCH_DIR.exists() else []
    book = [e for e in read_events(days[-1]) if str(e.get("book")) == "True"] if days else []
    hold = read_holdings()
    q = fetch_quotes([h["code"] for h in hold] + [str(e["code"]).zfill(6) for e in book])
    rows = []
    for h in hold:
        st, txt = sell_advice(h["buy_px"], h["buy_day"], q.get(h["code"]), today, now)
        rows.append(dict(kind="真实", code=h["code"], name=h.get("name") or h["code"], buy_px=h["buy_px"],
                         buy_day=h["buy_day"], price=(q.get(h["code"]) or {}).get("price"), state=st, text=txt))
    for e in book:
        code = str(e["code"]).zfill(6)
        st, txt = sell_advice(e["price"], days[-1], q.get(code), today, now)
        rows.append(dict(kind="纸面", code=code, name=e["name"], buy_px=e["price"], buy_day=days[-1],
                         price=(q.get(code) or {}).get("price"), state=st, text=txt))
    return dict(rows=rows)


# ---------------------------------------------------------------- 结算与统计
def _outcome(e: dict, day: str, ddir) -> dict | None:
    """日线同步到次日后才能结算；还没有次日数据返回 None。"""
    p = Path(ddir) / "daily" / f"{e['code']}.csv"
    if not p.exists():
        return None
    d = pd.read_csv(p, parse_dates=["date"]).set_index("date")
    t = pd.Timestamp(day)
    if t not in d.index:
        return None
    i = d.index.get_loc(t)
    if i + 1 >= len(d):
        return None
    a, b = d.iloc[i], d.iloc[i + 1]
    # 除权日：股价没涨而因子上升才算（规则同 indicators.build）
    raw = b["close"] / a["close"] - 1
    rise = b["factor"] / a["factor"] - 1
    m = b["factor"] / a["factor"] if rise > 0.005 and raw <= 0 else 1.0
    px = float(e["price"])
    return dict(day=day, code=e["code"], name=e["name"], time=e["time"], seal_pct=e["seal_pct"], boards=e["boards"],
                one_word=e["one_word"], pick=e["pick"], book=e["book"],
                sealed_close=bool(a["close"] >= px - 0.005),
                r_open=b["open"] * m / px - 1 - COST, r_close=b["close"] * m / px - 1 - COST)


def settle(ddir=None) -> pd.DataFrame:
    """把已有次日日线的首次封板事件写入 outcomes.csv（已结算的不重复算）。"""
    ddir = ddir or CFG["data_dir"]
    old = pd.read_csv(OUT_FILE, dtype={"code": str, "day": str}, encoding="utf-8-sig") if OUT_FILE.exists() else pd.DataFrame()
    done = set(zip(old["day"], old["code"])) if len(old) else set()
    new = []
    for p in sorted(WATCH_DIR.glob("[0-9]*.csv")) if WATCH_DIR.exists() else []:
        for e in read_events(p.stem):
            e["code"] = str(e["code"]).zfill(6)
            if e["event"] != "封板" or (p.stem, e["code"]) in done:
                continue
            o = _outcome(e, p.stem, ddir)
            if o:
                new.append(o)
    if not new:
        return old
    out = pd.concat([old, pd.DataFrame(new)], ignore_index=True)
    out.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")
    return out


def _bucket(name, g) -> dict:
    if not len(g):
        return dict(name=name, n=0)
    s, u = g[g["sealed_close"]], g[~g["sealed_close"]]
    w = SEALED_FILL * len(s) + len(u)
    return dict(name=name, n=len(g), sealed=len(s) / len(g) * 100, r_all=g["r_open"].mean() * 100,
                r_fill=(SEALED_FILL * s["r_open"].sum() + u["r_open"].sum()) / w * 100 if w else None,
                win=(g["r_open"] > 0).mean() * 100, r_close=g["r_close"].mean() * 100)


def stats(df: pd.DataFrame) -> dict:
    if df is None or not len(df):
        return dict(days=0, n=0, rows=[], verdict="还没有可结算的记录。结算需要次日的日线，同步数据后才会更新。")
    df = df.copy()
    for c in ("sealed_close", "one_word", "pick", "book"):
        df[c] = df[c].astype(str) == "True"
    can = df[~df["one_word"]]        # 一字板买不到，不计入收益
    pick, book = can[can["pick"]], can[can["book"]]
    rows = [_bucket("全部可买的封板", can), _bucket("候选规则", pick), _bucket("纸面持仓（每天前 2 只）", book)]
    for a, b, nm in (("09:25", "09:45", "09:45 前封板"), ("09:45", "10:00", "09:45-10:00"),
                     ("10:00", "11:30", "10:00-11:30"), ("11:30", "15:01", "午后封板")):
        rows.append(_bucket(nm, can[(can["time"] >= a) & (can["time"] < b)]))
    for lo, hi, nm in ((0, 0.5, "封单 <0.5%"), (0.5, 1, "封单 0.5-1%"), (1, 3, "封单 1-3%"), (3, 1e9, "封单 ≥3%")):
        rows.append(_bucket(nm, can[(can["seal_pct"] >= lo) & (can["seal_pct"] < hi)]))
    days = df["day"].nunique()
    r = rows[2]
    if days < 40 or r["n"] < 40:
        v = f"样本不足：已记录 {days} 个交易日、纸面持仓 {r['n']} 笔。至少 40 个交易日、40 笔后再看。"
    elif r["r_all"] > 0 and r["r_fill"] > 0:
        v = f"纸面持仓 {r['n']} 笔，全部成交和只成交 3 成两种假设下都为正。这仍是纸面结果，真实排队可能更差。"
    else:
        v = f"纸面持仓 {r['n']} 笔，至少一种成交假设下为负，这条规则目前不支持实盘。"
    return dict(days=days, n=len(df), rows=rows, verdict=v)
