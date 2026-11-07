"""本地行情与策略看板（Flask 后端）。

启动：start_dashboard.bat，或 .venv\\Scripts\\python dashboard\\app.py，然后打开 http://127.0.0.1:8686
只监听本机 127.0.0.1，不对局域网开放。
"""
import json
import math
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, Response, request, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant import backtest, board_watch, boards, data, market, sentiment, ta, ths  # noqa: E402
from quant import strategy_loader as SL  # noqa: E402
from quant.config import load_config  # noqa: E402
from quant.context import Context  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
app = Flask(__name__, static_folder=None)
CFG = load_config()
DDIR = CFG["data_dir"]


# ---------------------------------------------------------------- JSON
def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if math.isnan(o) or math.isinf(o) else round(float(o), 6)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.strftime("%Y-%m-%d")
    if o is pd.NaT:
        return None
    return o


def ok(obj, status=200):
    return Response(json.dumps(clean(obj), ensure_ascii=False), status=status, mimetype="application/json")


def records(df: pd.DataFrame):
    return clean(df.to_dict(orient="records"))


# ---------------------------------------------------------------- 市场状态缓存
class State:
    """由本地日线计算的指标/情绪/主线/信号，日线更新后自动重建。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.key = None
        self.error = None

    def _key(self):
        p = Path(DDIR) / "panel.pkl"
        d = Path(DDIR) / "daily"
        newest = max((e.stat().st_mtime for e in __import__("os").scandir(d)), default=0)
        return (p.stat().st_mtime if p.exists() else 0, newest)

    def get(self):
        with self.lock:
            k = self._key()
            if k != self.key:
                self._build()
                self.key = self._key()
            return self

    def _build(self):
        t = time.time()
        cfg = load_config()
        self.cfg = cfg
        start = (pd.Timestamp.today() - pd.Timedelta(days=760)).strftime("%Y-%m-%d")
        ctx = Context(cfg, start=start)
        self.ctx = ctx
        self.names, self.codes, self.st = ctx.names, ctx.codes, ctx.st
        self.ind, self.senti, self.T = ctx.ind, ctx.senti, ctx.T
        self.M, self.board = ctx.members, ctx.board
        T = self.T
        self.lhb = data.load_lhb(DDIR, (T - pd.Timedelta(days=60)).strftime("%Y-%m-%d"), T.strftime("%Y-%m-%d"))
        # 总览里的「策略候选」用当前选用的策略
        self.active = cfg.get("active_strategy", "mainline_leader_dip")
        try:
            mod = SL.load(self.active)
            self.active_name = getattr(mod, "NAME", self.active)
            self.active_exec = SL.strategy_cfg(cfg, mod)["strategy"]
            self.active_mode = SL.mode(mod)
            self.active_uni = SL.universe(ctx, mod)
            self.sig = SL.run_signals(ctx, mod)
        except Exception as e:
            print(f"[state] 策略 {self.active} 运行失败：{e}", flush=True)
            self.active_name, self.active_exec = self.active, cfg["strategy"]
            self.active_mode, self.active_uni = "signal", ctx
            self.sig = pd.DataFrame(columns=["date", "code", "score"])

        self.views = {self.active: {"key": self.active, "name": self.active_name, "mode": self.active_mode,
                                    "exec": self.active_exec, "uni": self.active_uni, "sig": self.sig}}
        self.view_lock = threading.Lock()

    def view(self, key):
        """任意策略的信号/目标持仓（按需计算并缓存到下次数据更新）。"""
        with self.view_lock:
            if key not in self.views:
                mod = SL.load(key)
                self.views[key] = {"key": key, "name": getattr(mod, "NAME", key), "mode": SL.mode(mod),
                                   "exec": SL.strategy_cfg(self.cfg, mod)["strategy"],
                                   "uni": SL.universe(self.ctx, mod), "sig": SL.run_signals(self.ctx, mod)}
            return self.views[key]

    def ind_of(self, code):
        """个股或 ETF 的指标数据（ETF 不在个股面板里）。"""
        if code in self.ind["close"].columns:
            return self.ind
        try:
            if code in self.ctx.etf.ind["close"].columns:
                return self.ctx.etf.ind
        except Exception:
            pass
        return None
        print(f"[state] 重建完成 {time.time() - t:.1f}s，最新交易日 {T.date()}", flush=True)


STATE = State()


# ---------------------------------------------------------------- 后台任务（按钮）
JOBS = {
    "sync": ("同步数据", [["archive_pools.py"], ["update_data.py"]]),
    "sync_tx": ("同步数据(备用源)", [["archive_pools.py"], ["update_data.py", "--source", "sina"]]),
    "archive": ("归档涨停池/资金流", [["archive_pools.py"]]),
    "pick": ("生成次日计划", [["daily_pick.py"]]),
    "backtest": ("运行回测", [["run_backtest.py"]]),
    "compare": ("策略对比回测", [["compare_strategies.py"]]),
}


def job_steps(name, args):
    """带参数的任务：compare?only=key 只回测一个策略。"""
    title, steps = JOBS[name]
    if name == "compare" and args.get("only"):
        key = args["only"]
        if not all(ch.isalnum() or ch == "_" for ch in key):
            raise ValueError("策略名只能包含字母、数字、下划线")
        return f"策略回测：{key}", [["compare_strategies.py", "--only", key]]
    return title, steps
JOB = {"name": None, "title": None, "state": "idle", "started": None, "finished": None, "log": []}
JOB_LOCK = threading.Lock()
LOG_DIR = ROOT / "logs"


def _run_job(title, steps):
    LOG_DIR.mkdir(exist_ok=True)
    logf = LOG_DIR / "dashboard_jobs.log"
    env = dict(__import__("os").environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    state = "done"
    with open(logf, "a", encoding="utf-8") as lf:
        lf.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} {title}\n")
        for step in steps:
            JOB["log"].append(f"$ python {' '.join(step)}")
            p = subprocess.Popen([sys.executable, "-u", *step], cwd=ROOT, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            buf = b""
            while True:
                ch = p.stdout.read(1)
                if not ch:
                    break
                if ch in (b"\n", b"\r"):
                    line = buf.decode("utf-8", "replace").strip()
                    buf = b""
                    if line and "it/s" not in line and "%|" not in line:
                        JOB["log"].append(line)
                        JOB["log"] = JOB["log"][-300:]
                        lf.write(line + "\n")
                else:
                    buf += ch
            if p.wait() != 0:
                state = "failed"
                more = step is not steps[-1]
                JOB["log"].append(f"!! 这一步失败，退出码 {p.returncode}" + ("，继续执行下一步" if more else ""))
                lf.write(f"!! 步骤失败 {step} 退出码 {p.returncode}\n")
    JOB.update(state=state, finished=datetime.now().strftime("%H:%M:%S"))


@app.post("/api/jobs/<name>")
def start_job(name):
    if name not in JOBS:
        return ok({"error": "未知任务"}, 404)
    try:
        title, steps = job_steps(name, request.args)
    except ValueError as e:
        return ok({"error": str(e)}, 400)
    with JOB_LOCK:
        if JOB["state"] == "running":
            return ok({"error": f"已有任务在运行：{JOB['title']}"}, 409)
        JOB.update(name=name, title=title, state="running",
                   started=datetime.now().strftime("%H:%M:%S"), finished=None, log=[])
        threading.Thread(target=_run_job, args=(title, steps), daemon=True).start()
    return ok(JOB)


@app.get("/api/jobs")
def job_status():
    return ok({**JOB, "log": JOB["log"][-120:], "available": {k: v[0] for k, v in JOBS.items()}})


# ---------------------------------------------------------------- 状态
@app.get("/api/status")
def status():
    arch = sorted(p.name for p in (Path(DDIR) / "archive").glob("[0-9]*") if (p / "zt.csv").exists())
    s = STATE.get()
    return ok({
        "last_date": s.T, "n_stocks": len(s.codes),
        "archive_days": len(arch), "archive_first": arch[0] if arch else None, "archive_last": arch[-1] if arch else None,
        "em_available": market.EM.available, "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })


# ---------------------------------------------------------------- 总览
@app.get("/api/overview")
def overview():
    s = STATE.get()
    T, ind = s.T, s.ind
    row = s.senti.loc[T]
    sen = s.senti.tail(250).copy()
    ret = ind["ret"]
    up = (ret > 0).sum(axis=1)
    down = (ret < 0).sum(axis=1)
    sen["up"], sen["down"] = up.reindex(sen.index), down.reindex(sen.index)

    # 主线板块
    ml = s.board["mainline"].loc[T]
    win = s.board["board_win"].loc[T]
    strength = (win / np.sqrt(s.M.sum(axis=0))).sort_values(ascending=False)
    mainline = []
    for b in strength.index[:12]:
        mem = s.M.index[s.M[b] > 0]
        zt_today = [c for c in mem if ind["is_zt"].at[T, c]]
        mainline.append({"board": b, "win": win[b], "today_zt": len(zt_today), "is_main": bool(ml[b]),
                         "size": int(len(mem)),
                         "zt_names": [s.names.get(c, c) for c in zt_today][:8]})
    # 主线板块近 40 日涨停数走势
    top = [m["board"] for m in mainline[:6]]
    bz = s.board["board_zt"][top].tail(40)

    # 连板梯队（含 ST，单独标记）
    stk = ind["streak"].loc[T]
    ladder = []
    for n in sorted(stk[stk >= 1].unique(), reverse=True):
        cs = stk[stk == n].index
        ladder.append({"streak": int(n), "count": len(cs),
                       "stocks": [{"code": c, "name": s.names.get(c, c), "st": c in s.st} for c in cs][:40]})

    return ok({
        "date": T,
        "senti": {**row.to_dict(), "position": sentiment.position_ratio(row["score"], s.cfg["sentiment"]),
                  "up": up.loc[T], "down": down.loc[T]},
        "senti_hist": {"dates": list(sen.index), **{c: list(sen[c]) for c in
                       ["score", "zt_count", "dt_count", "zb_rate", "max_streak", "premium", "up", "down",
                        "st_zt_count", "st_dt_count"]}},
        "mainline": mainline,
        "board_hist": {"dates": list(bz.index), "series": [{"name": b, "data": list(bz[b])} for b in top]},
        "ladder": ladder,
        "tabs": overview_tabs(s),
        "active": s.active,
    })


def overview_tabs(s):
    """总览「策略候选」卡片的切换标签：每日策略排第一，再加 config.yaml 的 overview_strategies。"""
    keys = [s.active] + [k for k in (s.cfg.get("overview_strategies") or []) if k != s.active]
    tabs = []
    for info in SL.list_strategies():
        if info["key"] in keys and not info["error"]:
            tabs.append({"key": info["key"], "name": info["name"], "mode": info["mode"],
                         "universe": info["universe"], "active": info["key"] == s.active})
    return sorted(tabs, key=lambda t: keys.index(t["key"]))


@app.get("/api/candidates")
def candidates():
    """某个策略在最新交易日的候选（信号类）或目标持仓（调仓类）。"""
    s = STATE.get()
    key = request.args.get("key") or s.active
    try:
        v = s.view(key)
    except Exception as e:
        return ok({"error": f"策略 {key} 运行失败：{e}"}, 500)
    T, ind = s.T, s.ind
    head = {"key": key, "name": v["name"], "mode": v["mode"], "active": key == s.active}
    if v["mode"] == "rebalance":
        return ok({"strategy": head, "rebalance": rebalance_view(s, v, T)})
    sig, st = v["sig"], v["exec"]
    ml = s.board["mainline"].loc[T]
    cand = sig[sig["date"] == T].copy()
    cand["name"] = cand["code"].map(s.names)
    cand["boards"] = [boards.stock_boards(s.M, ml, c) for c in cand["code"]]
    cand["close"] = ind["close"].loc[T, cand["code"]].values if len(cand) else []
    cand["ret"] = ind["ret"].loc[T, cand["code"]].values if len(cand) else []
    cand["max_streak"] = ind["streak"].loc[T, cand["code"]].values if len(cand) else []
    cand["buy_price"] = (cand["close"] * (1 + st["buy_offset"])).round(2)
    recent = sig[(sig["date"] > T - pd.Timedelta(days=60))].copy()
    recent["name"] = recent["code"].map(s.names)
    recent["close"] = [ind["close"].at[d, c] for d, c in zip(recent["date"], recent["code"])]
    return ok({
        "strategy": head,
        "candidates": records(cand[["code", "name", "boards", "close", "ret", "max_streak", "score", "buy_price"]].head(20)),
        "recent_signals": records(recent[["date", "code", "name", "close", "score"]].tail(20)),
        "params": {k: st[k] for k in ("take_profit", "stop_loss", "max_hold_days", "buy_offset", "max_gap_up")},
    })


def rebalance_view(s, v, T):
    """调仓类策略：最近一次决策的目标持仓、相对上次的变动、自决策日以来涨跌。"""
    sig = v["sig"]
    if sig.empty:
        return {"decision_date": None, "rows": []}
    dates = sorted(sig["date"].unique())
    last = dates[-1]
    cur = sig[sig["date"] == last]
    prev = set(sig[sig["date"] == dates[-2]]["code"]) if len(dates) > 1 else set()
    ind = v["uni"].ind
    rows = []
    for _, r in cur.iterrows():
        c = r["code"]
        px = ind["close"][c] if c in ind["close"].columns else None
        adj = ind["adj"]["close"][c] if px is not None else None
        since = (adj.loc[T] / adj.loc[last] - 1) if px is not None and T in adj.index and last in adj.index else None
        rows.append({"code": c, "name": s.names.get(c, c), "weight": r["score"], "close": px.loc[:T].dropna().iloc[-1] if px is not None else None,
                     "action": "新调入" if c not in prev else "继续持有", "since": since})
    for c in prev - set(cur["code"]):
        rows.append({"code": c, "name": s.names.get(c, c), "weight": 0, "close": None, "action": "调出", "since": None})
    return {"decision_date": last, "is_today": last == T, "rows": rows}


@app.get("/api/index/<sym>")
def index_k(sym):
    if sym not in market.INDEXES:
        return ok({"error": "未知指数"}, 404)
    df = market.index_daily(DDIR, sym, days=int(request.args.get("days", 250)))
    return ok({"name": market.INDEXES[sym], "rows": df[["date", "open", "close", "low", "high", "volume"]].values.tolist()})


@app.get("/api/market_flow")
def market_flow():
    df = market.em_market_flow(DDIR)
    if df is None:
        return ok({"available": False, "msg": "东方财富资金流接口暂时被限流，稍后再试"})
    return ok({"available": True, "rows": records(df.tail(120))})


# ---------------------------------------------------------------- 个股
@app.get("/api/search")
def search():
    q = request.args.get("q", "").strip()
    s = STATE.get()
    if not q:
        return ok([])
    res = [{"code": c, "name": n} for c, n in s.names.items() if q in c or q in str(n)]
    return ok(res[:20])


@app.get("/api/stock/<code>")
def stock(code):
    s = STATE.get()
    ind = s.ind_of(code)
    if ind is None:
        return ok({"error": "不在股票池/ETF池（北交所或未下载）"}, 404)
    days = int(request.args.get("days", 250))
    c = ind["close"][code]
    alld = c.dropna().index
    valid = alld[-days:]
    k = ind["k"][code]
    last_k = k.loc[valid[-1]]
    # 前复权：复权价 / 最新一天的(复权/不复权)比例
    o = (ind["adj"]["open"][code] / last_k).loc[alld]
    h = (ind["adj"]["high"][code] / last_k).loc[alld]
    l = (ind["adj"]["low"][code] / last_k).loc[alld]
    cl = (ind["adj"]["close"][code] / last_k).loc[alld]
    vol = ind["volume"][code].loc[alld]
    turn = vol / ind["float_share"][code] * 100
    kline = [[d, o[d], cl[d], l[d], h[d], vol[d], ind["amount"][code][d], ind["ret"][code][d] * 100, turn[d]]
             for d in valid]
    # 技术指标用全部可得历史计算（保证预热），再截取展示区间
    groups = ta.all_for_stock(o, h, l, cl, vol)
    ta_out = {g: {n: s_.loc[valid].round(4).tolist() for n, s_ in lines.items()} for g, lines in groups.items()}
    zt = [d for d in valid if ind["is_zt"].at[d, code]]
    full_lhb = data.load_lhb(DDIR, valid[0].strftime("%Y-%m-%d"), valid[-1].strftime("%Y-%m-%d"), detail=True)
    lhb = full_lhb[full_lhb["code"] == code]
    sigs = s.sig[s.sig["code"] == code]["date"].tolist()
    mem = s.M.columns[s.M.loc[code] > 0].tolist() if code in s.M.index else []
    return ok({
        "code": code, "name": s.names.get(code, code), "boards": mem, "is_st": code in s.st,
        "limit": ind["lim"][code].loc[valid[-1]],
        "float_mv": ind["float_mv"][code].loc[valid[-1]],
        "streak": ind["streak"][code].loc[valid[-1]],
        "kline": kline, "zt_dates": zt, "signals": sigs, "ta": ta_out,
        "lhb": records(lhb[["date", "net_buy", "buy", "sell", "reason", "explain"]]),
    })


@app.get("/api/lhb_seats")
def lhb_seats():
    """某只股票某天的龙虎榜营业部席位（买入前5 / 卖出前5）。"""
    code, date = request.args["code"], request.args["date"]
    df = market.em_lhb_seats(DDIR, date, code)
    if df is None:
        return ok({"error": "东财数据中心暂时连不上"}, 502)
    jg = data.load_lhb_jg(DDIR, date, date)
    jg = jg[(jg["code"] == code) & (jg["date"] == pd.Timestamp(date))] if len(jg) else jg
    return ok({"seats": records(df), "jg": records(jg) if len(jg) else []})


@app.get("/api/quote/<code>")
def quote(code):
    """腾讯实时行情（盘中刷新用）。"""
    q = market.tx_quote([code])
    return ok(q.get(code) or {"error": "无行情"})


@app.get("/api/stock_flow/<code>")
def stock_flow(code):
    """个股资金流：东财（大小单拆分，可能被限流）+ 同花顺归档（净额）。单独接口，避免拖慢K线加载。"""
    flow_em = market.em_stock_flow(DDIR, code)
    ths = data.load_archive(DDIR, "flow_ths")
    flow_ths = ths[ths["code"] == code][["date", "net", "inflow", "outflow"]] if len(ths) else pd.DataFrame()
    return ok({"flow_em": records(flow_em.tail(250)) if flow_em is not None else None,
               "flow_ths": records(flow_ths), "em_available": market.EM.available})


# ---------------------------------------------------------------- 板块
@app.get("/api/boards")
def board_rank():
    kind = request.args.get("kind", "concept")
    period = request.args.get("period", "即时")
    try:
        df = market.ths_board_flow(DDIR, kind, period)
    except Exception as e:
        return ok({"error": f"同花顺板块数据获取失败：{e}"}, 502)
    return ok(records(df))


@app.get("/api/board_index")
def board_index():
    name, kind = request.args["name"], request.args.get("kind", "concept")
    try:
        df = market.ths_board_index(DDIR, name, kind)
    except Exception as e:
        return ok({"error": f"板块指数获取失败：{e}"}, 502)
    df = df.tail(250)
    return ok({"name": name, "rows": df[["date", "open", "close", "low", "high", "volume"]].values.tolist()})


@app.get("/api/mainline_board")
def mainline_board():
    """新浪口径的板块（主线识别用）：成分股今日表现。"""
    s = STATE.get()
    b = request.args["name"]
    if b not in s.M.columns:
        return ok({"error": "未知板块"}, 404)
    T, ind = s.T, s.ind
    mem = s.M.index[s.M[b] > 0]
    df = pd.DataFrame({"code": mem, "name": [s.names.get(c, c) for c in mem],
                       "pct": ind["ret"].loc[T, mem].values * 100,
                       "streak": ind["streak"].loc[T, mem].values,
                       "amount": ind["amount"].loc[T, mem].values,
                       "is_zt": ind["is_zt"].loc[T, mem].values,
                       "leader": s.board["leader"].loc[T, mem].values})
    hist = s.board["board_zt"][b].tail(60)
    return ok({"board": b, "members": records(df.sort_values("pct", ascending=False)),
               "zt_hist": {"dates": list(hist.index), "data": list(hist.values)}})


# ---------------------------------------------------------------- 涨停复盘
@app.get("/api/limitup")
def limitup():
    s = STATE.get()
    arch_root = Path(DDIR) / "archive"
    arch_dates = sorted(p.name for p in arch_root.glob("[0-9]*") if (p / "zt.csv").exists())
    date = request.args.get("date") or (arch_dates[-1] if arch_dates else s.T.strftime("%Y%m%d"))
    D = pd.Timestamp(date)
    out = {"date": D, "dates": arch_dates, "source": None}
    p = arch_root / D.strftime("%Y%m%d")

    def rd(name):
        f = p / f"{name}.csv"
        return pd.read_csv(f, dtype={"代码": str}) if f.exists() else pd.DataFrame()

    zt = rd("zt")
    if len(zt):
        out["source"] = "东财涨停池（归档）"
        zt = zt.rename(columns={"代码": "code", "名称": "name", "涨跌幅": "pct", "最新价": "price",
                                "成交额": "amount", "流通市值": "float_mv", "换手率": "turnover",
                                "封板资金": "seal", "首次封板时间": "first_time", "最后封板时间": "last_time",
                                "炸板次数": "breaks", "涨停统计": "stat", "连板数": "streak", "所属行业": "industry"})
        zt["first_time"] = zt["first_time"].astype(str).str.zfill(6)
        zt["last_time"] = zt["last_time"].astype(str).str.zfill(6)
        cols = ["code", "name", "streak", "stat", "first_time", "last_time", "breaks", "seal",
                "amount", "turnover", "float_mv", "industry"]
        out["zt"] = records(zt[cols].sort_values(["streak", "first_time"], ascending=[False, True]))
        ind_cnt = zt.groupby("industry").size().sort_values(ascending=False).head(15)
        out["industry"] = [{"name": k, "value": int(v)} for k, v in ind_cnt.items()]
        zb, dt, prev = rd("zb"), rd("dt"), rd("prev")
        out["zb_count"], out["dt_count"] = len(zb), len(dt)
        out["zb"] = records(zb.rename(columns={"代码": "code", "名称": "name", "涨跌幅": "pct", "炸板次数": "breaks",
                                               "所属行业": "industry"})[["code", "name", "pct", "breaks", "industry"]]) if len(zb) else []
        out["dt"] = records(dt.rename(columns={"代码": "code", "名称": "name", "涨跌幅": "pct", "连续跌停": "days",
                                               "所属行业": "industry"})[["code", "name", "pct", "days", "industry"]]) if len(dt) else []
        if len(prev):
            out["prev_premium"] = prev["涨跌幅"].mean()
            out["prev_up_ratio"] = (prev["涨跌幅"] > 0).mean()
    elif D in s.ind["close"].index:
        out["source"] = "本地日线计算（无归档）"
        ind = s.ind
        cs = ind["is_zt"].columns[ind["is_zt"].loc[D]]
        df = pd.DataFrame({"code": cs, "name": [s.names.get(c, c) for c in cs],
                           "streak": ind["streak"].loc[D, cs].values,
                           "amount": ind["amount"].loc[D, cs].values,
                           "float_mv": ind["float_mv"].loc[D, cs].values})
        out["zt"] = records(df.sort_values("streak", ascending=False))
        out["industry"] = []
        out["zb_count"] = int(ind["broken"].loc[D].sum())
        out["dt_count"] = int(ind["is_dt"].loc[D].sum())
    else:
        out["zt"] = []
    return ok(out)


@app.get("/api/lianban")
def lianban():
    """连板股 × 龙虎榜：某天 ≥N 连板的股票，附当天龙虎榜（上榜原因/净买/机构）；以及 ST 涨跌停。
    基于本地日线计算，任何已下载的交易日都能查（不依赖东财归档）。"""
    s = STATE.get()
    ind = s.ind
    dates = ind["close"].index
    D = pd.Timestamp(request.args.get("date") or dates[-1])
    if D not in dates:
        D = dates[dates <= D][-1]
    n_min = int(request.args.get("min", 2))
    stk = ind["streak"].loc[D]
    cs = stk[stk >= n_min].index
    i = dates.get_loc(D)
    df = pd.DataFrame({
        "code": cs, "name": [s.names.get(c, c) for c in cs], "streak": stk[cs].values,
        "is_st": [c in s.st for c in cs], "pct": ind["ret"].loc[D, cs].values * 100,
        "close": ind["close"].loc[D, cs].values, "amount": ind["amount"].loc[D, cs].values,
        "float_mv": ind["float_mv"].loc[D, cs].values,
        "one_word": ind["one_word"].loc[D, cs].values,
        # 次日表现（复盘用，历史日期才有）
        "next_pct": (ind["ret"].iloc[i + 1][cs].values * 100) if i + 1 < len(dates) else np.nan,
    })
    ml = s.board["mainline"].loc[D] if D in s.board["mainline"].index else None
    df["boards"] = [boards.stock_boards(s.M, ml, c) if ml is not None else "" for c in cs]
    lhb = data.load_lhb(DDIR, D, D, detail=True)
    jg = data.load_lhb_jg(DDIR, D, D)
    if len(lhb):
        df = df.merge(lhb[lhb["date"] == D][["code", "net_buy", "buy", "sell", "reason", "explain"]], on="code", how="left")
    if len(jg):
        df = df.merge(jg[jg["date"] == D][["code", "jg_buyers", "jg_sellers", "jg_net"]], on="code", how="left")
    # 同花顺涨停原因（当天涨停的才有）
    lu = s.ctx.ths_limitup
    lu_d = lu[lu["date"] == D][["code", "reason", "high_days"]].rename(columns={"reason": "zt_reason"})
    df = df.merge(lu_d, on="code", how="left")
    df = df.sort_values(["streak", "amount"], ascending=[False, False])

    # 这些股票近 15 日的连板高度走势
    hist_d = dates[max(0, i - 14): i + 1]
    top = df.head(10)
    height = {"dates": list(hist_d), "series": [
        {"name": r["name"], "data": ind["streak"].loc[hist_d, r["code"]].tolist()} for _, r in top.iterrows()]}

    st_list = [c for c in ind["close"].columns if c in s.st]
    st_zt = [c for c in st_list if ind["is_zt"].at[D, c]]
    st_dt = [c for c in st_list if ind["is_dt"].at[D, c]]
    st_rows = lambda arr: [{"code": c, "name": s.names.get(c, c), "pct": ind["ret"].at[D, c] * 100,
                            "streak": ind["streak"].at[D, c], "amount": ind["amount"].at[D, c]} for c in arr]
    return ok({"date": D, "dates": list(dates[-120:]), "min": n_min, "rows": records(df), "height": height,
               "st_zt": st_rows(st_zt), "st_dt": st_rows(st_dt), "st_total": len(st_list),
               "lim_note": "主板ST涨跌幅 2026-07-06 起为10%（之前5%）；ST按当前名单判断"})


@app.get("/api/themes")
def themes_view():
    """同花顺涨停原因 → 当天涨停明细、题材强度排行、题材走势、连板天梯、涨跌停统计。"""
    s = STATE.get()
    dates = ths.archived_dates(DDIR)
    if not dates:
        return ok({"dates": [], "error": "还没有同花顺涨停数据，请运行 python archive_pools.py --ths-backfill 260"})
    D = pd.Timestamp(request.args.get("date") or dates[-1])
    lu = s.ctx.ths_limitup
    day = lu[lu["date"] == D].copy()
    th = s.ctx.themes
    p = s.cfg.get("themes", {})
    out = {"date": D, "dates": dates, "params": {k: p.get(k) for k in ("window", "top_k", "min_count")}}
    lad = ths.load_ladder(DDIR, D) or {}
    out["stats"] = lad.get("stats")
    out["ladder"] = lad.get("ladder") or []

    # 当天明细
    day["tags"] = day["reason"].map(ths.split_reason)
    day = day.sort_values(["boards", "first_time"], ascending=[False, True])
    out["pool"] = records(day[["code", "name", "reason", "tags", "high_days", "boards", "type", "first_time",
                               "last_time", "open_num", "order_amount", "suc_rate", "turnover", "float_mv", "pct"]])

    # 题材排行（截至 D 的近 window 日强度）
    ml = th["mainline"].get(D, []) if D in th["strength"].index else []
    rank = []
    if D in th["strength"].index:
        stg, dly = th["strength"].loc[D], th["daily"].loc[D]
        top = stg[stg > 0].sort_values(ascending=False).head(25)
        for tag, v in top.items():
            g = day[day["tags"].map(lambda ts: tag in ts)]
            rank.append({"tag": tag, "strength": v, "today": dly[tag], "is_main": tag in ml, "generic": ths.is_generic(tag),
                         "stocks": [{"code": r.code, "name": r.name, "boards": r.boards} for r in g.itertuples()][:12],
                         "leader": (g.sort_values("boards", ascending=False).iloc[0]["name"] if len(g) else None)})
        # 走势：当前强度前 8 个题材（非通用）近 30 个交易日每天涨停家数
        idx = th["daily"].index
        i = idx.get_loc(D)
        hist = th["daily"].iloc[max(0, i - 29): i + 1]
        keys = [r["tag"] for r in rank if not r["generic"]][:8]
        out["trend"] = {"dates": list(hist.index), "series": [{"name": k, "data": list(hist[k])} for k in keys]}
    out["rank"] = rank
    out["mainline"] = ml
    return ok(out)


_HOT_CACHE = {}


@app.get("/api/ths_hot")
def ths_hot():
    """同花顺热榜：kind=stock（个股人气，24小时）/ concept / industry。date 为归档日期，缺省=实时。"""
    kind = request.args.get("kind", "stock")
    date = request.args.get("date")
    arch_dates = sorted(p.parent.name for p in (Path(DDIR) / "archive").glob(f"[0-9]*/ths_hot_{kind}.csv"))
    if date:
        f = Path(DDIR) / "archive" / date / f"ths_hot_{kind}.csv"
        if not f.exists():
            return ok({"error": "该日期没有归档", "dates": arch_dates}, 404)
        df = pd.read_csv(f, dtype={"code": str})
        return ok({"rows": records(df), "source": f"归档 {date}", "dates": arch_dates})
    key = kind
    if key not in _HOT_CACHE or time.time() - _HOT_CACHE[key][0] > 60:
        df = ths.hot_stocks("day") if kind == "stock" else ths.hot_plates(kind)
        if df is None:
            return ok({"error": "同花顺热榜暂时获取失败", "dates": arch_dates}, 502)
        _HOT_CACHE[key] = (time.time(), df)
    return ok({"rows": records(_HOT_CACHE[key][1]), "source": "实时（每分钟刷新）", "dates": arch_dates})


@app.get("/api/sources")
def sources():
    return ok(market.check_sources())


# ---------------------------------------------------------------- 策略
@app.get("/api/strategies")
def strategies():
    summ_p = ROOT / "results" / "compare" / "summary.json"
    summ = json.loads(summ_p.read_text(encoding="utf-8")) if summ_p.exists() else {}
    items = []
    for info in SL.list_strategies():
        r = summ.get(info["key"], {})
        items.append({**info, "result": r if r.get("status") == "ok" else None,
                      "result_error": r.get("msg") if r.get("status") == "error" else None})
    cfg = load_config()
    pinned = set(cfg.get("overview_strategies") or [])
    for it in items:
        it["pinned"] = it["key"] in pinned
    return ok({"items": items, "active": cfg.get("active_strategy")})


@app.get("/api/strategy/<key>")
def strategy_detail(key):
    d = ROOT / "results" / "compare" / key
    if not (d / "equity.csv").exists():
        return ok({"error": "该策略还没有回测结果，点「回测」"}, 404)
    eq = pd.read_csv(d / "equity.csv", parse_dates=["date"]).set_index("date")["equity"]
    tr = pd.read_csv(d / "trades.csv", dtype={"code": str}) if (d / "trades.csv").exists() else pd.DataFrame()
    s = STATE.get()
    if len(tr):
        tr["name"] = tr["code"].map(s.names)
    return ok({"dates": list(eq.index), "equity": list(eq), "drawdown": list(eq / eq.cummax() - 1),
               "trades": records(tr.sort_values("sell_date", ascending=False).head(300)) if len(tr) else []})


@app.get("/api/strategy_equities")
def strategy_equities():
    """所有已回测策略的资金曲线（归一化），用于叠加对比。"""
    root = ROOT / "results" / "compare"
    out = []
    for d in sorted(root.glob("*/equity.csv")):
        eq = pd.read_csv(d, parse_dates=["date"]).set_index("date")["equity"]
        out.append({"key": d.parent.name, "dates": list(eq.index), "nav": list(eq / eq.iloc[0])})
    try:
        idx = market.index_daily(DDIR, "sh000852", days=2000).set_index("date")["close"]
        if out:
            dts = pd.DatetimeIndex(out[0]["dates"])
            idx = idx.reindex(dts, method="ffill")
            out.append({"key": "__index__", "name": "中证1000（基准）", "dates": list(dts), "nav": list(idx / idx.iloc[0])})
    except Exception:
        pass
    return ok(out)


@app.get("/api/strategy_source/<key>")
def strategy_source(key):
    p = SL.STRATEGY_DIR / f"{key}.py"
    if not p.exists() or not key.replace("_", "").isalnum():
        return ok({"error": "不存在"}, 404)
    return ok({"key": key, "source": p.read_text(encoding="utf-8")})


@app.get("/api/strategy_template")
def strategy_template():
    p = SL.STRATEGY_DIR / "_template.py"
    return Response(p.read_text(encoding="utf-8"), mimetype="text/x-python",
                    headers={"Content-Disposition": "attachment; filename=my_strategy.py"})


@app.post("/api/strategies/upload")
def strategy_upload():
    """上传策略脚本到 strategies/。只做静态检查；脚本会在回测时以本机权限执行，请只上传自己写的代码。"""
    f = request.files.get("file")
    if not f or not f.filename.endswith(".py"):
        return ok({"error": "请选择 .py 文件"}, 400)
    key = Path(f.filename).stem.strip()
    if not key or key.startswith("_") or not key.replace("_", "").isalnum() or not key.isascii():
        return ok({"error": "文件名只能用英文字母、数字、下划线，且不能以 _ 开头，例如 my_strategy.py"}, 400)
    src = f.read().decode("utf-8", "replace")
    err = SL.validate_source(src)
    if err:
        return ok({"error": err}, 400)
    dst = SL.STRATEGY_DIR / f"{key}.py"
    existed = dst.exists()
    if existed and request.args.get("overwrite") != "1":
        return ok({"error": f"已存在 {key}.py，如需覆盖请勾选覆盖", "exists": True}, 409)
    dst.write_text(src, encoding="utf-8")
    return ok({"key": key, "overwritten": existed})


@app.post("/api/strategies/pin")
def strategy_pin():
    """在总览中显示/隐藏某个策略：改写 config.yaml 的 overview_strategies 一行（保留注释）。"""
    import re
    body = request.get_json(silent=True) or {}
    key, on = body.get("key", ""), bool(body.get("on"))
    if not (SL.STRATEGY_DIR / f"{key}.py").exists():
        return ok({"error": "策略不存在"}, 404)
    cfg = load_config()
    cur = [k for k in (cfg.get("overview_strategies") or []) if k != key]
    if on:
        cur.append(key)
    p = ROOT / "config.yaml"
    txt = p.read_text(encoding="utf-8")
    line = f"overview_strategies: [{', '.join(cur)}]"
    new, n = re.subn(r"(?m)^overview_strategies:\s*\[[^\]]*\]", line, txt)
    if n == 0:
        new = re.sub(r"(?m)^(active_strategy:.*)$", lambda m: m.group(1) + "\n" + line, txt, count=1)
    p.write_text(new, encoding="utf-8")
    STATE.cfg["overview_strategies"] = cur  # 总览标签立即生效，不必重建数据
    return ok({"overview_strategies": cur})


@app.post("/api/strategies/active")
def strategy_active():
    """设置每日计划/实盘使用的策略：改写 config.yaml 的 active_strategy 一行（保留注释）。"""
    key = (request.get_json(silent=True) or {}).get("key", "")
    if not (SL.STRATEGY_DIR / f"{key}.py").exists():
        return ok({"error": "策略不存在"}, 404)
    p = ROOT / "config.yaml"
    txt = p.read_text(encoding="utf-8")
    import re
    new, n = re.subn(r"(?m)^active_strategy:\s*\S+", f"active_strategy: {key}", txt)
    if n == 0:
        new = f"active_strategy: {key}\n" + txt
    p.write_text(new, encoding="utf-8")
    STATE.key = None  # 下次请求时重建（候选换成新策略）
    return ok({"active": key})


# ---------------------------------------------------------------- 资金流排行（同花顺）
@app.get("/api/stock_flow_rank")
def stock_flow_rank():
    date = request.args.get("date")
    df = data.load_archive(DDIR, "flow_ths")
    if df.empty:
        return ok({"dates": [], "inflow": [], "outflow": []})
    dates = sorted(df["date"].dt.strftime("%Y%m%d").unique())
    D = pd.Timestamp(date) if date else pd.Timestamp(dates[-1])
    d = df[df["date"] == D]
    cols = ["code", "name", "price", "pct", "net", "inflow", "outflow", "amount"]
    return ok({"date": D, "dates": dates,
               "inflow": records(d.nlargest(30, "net")[cols]),
               "outflow": records(d.nsmallest(30, "net")[cols])})


# ---------------------------------------------------------------- 回测
# ---------------------------------------------------------------- 盘中提示（只提示、只记录，不下单）
_WATCH_REASON = {"t": 0, "day": None, "map": {}}


def _today_reasons(day) -> dict:
    """同花顺涨停原因，盘中每 60 秒最多取一次；取不到就留空。"""
    c = _WATCH_REASON
    if c["day"] != day or time.time() - c["t"] > 60:
        c.update(t=time.time(), day=day)
        try:
            df, _ = ths.fetch_limitup(day)
            if df is not None and len(df):
                c["map"] = dict(zip(df["code"].astype(str).str.zfill(6), df["reason"]))
        except Exception:
            pass
    return c["map"]


@app.get("/api/watch")
def watch_state():
    day = request.args.get("day") or datetime.now().strftime("%Y%m%d")
    events = board_watch.read_events(day)
    reasons = _today_reasons(day) if events else {}
    for e in events:
        e["code"] = str(e["code"]).zfill(6)
        e["reason"] = reasons.get(e["code"])
    out = board_watch.settle(DDIR)
    days = sorted(p.stem for p in board_watch.WATCH_DIR.glob("[0-9]*.csv")) if board_watch.WATCH_DIR.exists() else []
    return ok(dict(status=board_watch.read_status(), day=day, days=days, events=events,
                   prompts=board_watch.prompts()["rows"], stats=board_watch.stats(out)))


@app.post("/api/watch/start")
def watch_start():
    if board_watch.read_status().get("running"):
        return ok({"error": "监控已在运行"}, 409)
    LOG_DIR.mkdir(exist_ok=True)
    env = dict(__import__("os").environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    log = open(LOG_DIR / "board_watch.log", "a", encoding="utf-8")
    subprocess.Popen([sys.executable, "-u", "board_watch.py"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    return ok({"started": True})


@app.post("/api/watch/stop")
def watch_stop():
    board_watch.WATCH_DIR.mkdir(parents=True, exist_ok=True)
    board_watch.STOP_FILE.touch()
    return ok({"stopping": True})


@app.post("/api/watch/holding")
def watch_add_holding():
    b = request.get_json(force=True)
    code = str(b.get("code", "")).zfill(6)
    if not code.isdigit() or len(code) != 6:
        return ok({"error": "股票代码不对"}, 400)
    try:
        px = float(b["buy_px"])
    except (KeyError, TypeError, ValueError):
        return ok({"error": "买入价不对"}, 400)
    if px <= 0:
        return ok({"error": "买入价不对"}, 400)
    rows = board_watch.read_holdings()
    if len(rows) >= board_watch.MAX_BOOK:
        return ok({"error": f"最多记录 {board_watch.MAX_BOOK} 只持仓"}, 400)
    if any(h["code"] == code for h in rows):
        return ok({"error": "这只已经在持仓里"}, 400)
    rows.append(dict(code=code, name=b.get("name") or code, buy_px=px, buy_day=datetime.now().strftime("%Y%m%d")))
    board_watch.write_holdings(rows)
    return ok({"rows": rows})


@app.delete("/api/watch/holding/<code>")
def watch_del_holding(code):
    board_watch.write_holdings([h for h in board_watch.read_holdings() if h["code"] != code])
    return ok({"deleted": code})


@app.get("/api/backtest")
def bt():
    out_dir = ROOT / "results"
    eq_p, tr_p = out_dir / "equity.csv", out_dir / "trades.csv"
    if not eq_p.exists():
        return ok({"error": "还没有回测结果，请先点「运行回测」"}, 404)
    eq = pd.read_csv(eq_p, parse_dates=["date"]).set_index("date")
    tr = pd.read_csv(tr_p, dtype={"code": str}, parse_dates=["buy_date", "sell_date"]) if tr_p.exists() else pd.DataFrame()
    m = backtest.metrics(eq, tr, CFG["backtest"]["init_cash"])
    e = eq["equity"]
    dd = e / e.cummax() - 1
    yearly = [{"year": int(y), "ret": g.iloc[-1] / (e[e.index.year < y].iloc[-1] if (e.index.year < y).any() else e.iloc[0]) - 1,
               "mdd": (g / g.cummax() - 1).min()} for y, g in e.groupby(e.index.year)]
    s = STATE.get()
    if len(tr):
        tr["name"] = tr["code"].map(s.names)
    return ok({"metrics": m, "dates": list(eq.index), "equity": list(e), "drawdown": list(dd),
               "yearly": yearly, "trades": records(tr.sort_values("sell_date", ascending=False).head(300)) if len(tr) else [],
               "updated": datetime.fromtimestamp(eq_p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")})


# ---------------------------------------------------------------- 静态页面
@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html", max_age=0)


@app.get("/static/<path:p>")
def static_files(p):
    # 本地工具：每次都向服务器确认，改完前端刷新即可生效
    return send_from_directory(STATIC, p, max_age=0)


@app.errorhandler(Exception)
def on_error(e):
    import traceback
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    traceback.print_exc()
    return ok({"error": f"{type(e).__name__}: {e}"}, 500)


if __name__ == "__main__":
    threading.Thread(target=STATE.get, daemon=True).start()  # 启动时预热
    print("看板地址：http://127.0.0.1:8686", flush=True)
    app.run(host="127.0.0.1", port=8686, debug=False, threaded=True)
