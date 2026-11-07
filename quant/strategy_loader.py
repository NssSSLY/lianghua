"""策略插件加载：strategies/ 目录下每个 .py 文件就是一个策略（以 _ 开头的文件忽略）。

策略文件格式（见 strategies/_template.py）：
    NAME = "策略名"
    DESCRIPTION = "一句话说明"
    EXEC = dict(buy_offset=-0.02, max_gap_up=0.03, take_profit=0.08, stop_loss=-0.05, max_hold_days=3)
    USE_SENTIMENT = True        # 是否用情绪分控制仓位（低于阈值不开仓）
    def generate(ctx):          # 返回 DataFrame[date, code, score]，T 日收盘后的候选，T+1 执行
        ...
"""
import ast
import copy
import importlib.util
import traceback
from pathlib import Path

import pandas as pd

from . import backtest, portfolio
from .config import ROOT

STRATEGY_DIR = ROOT / "strategies"
EXEC_KEYS = ("buy_offset", "max_gap_up", "take_profit", "stop_loss", "max_hold_days",
             "hold_if_limit_up", "skip_limit_down_open")


def validate_source(src: str) -> str | None:
    """只做静态检查：语法正确、定义了 generate(ctx)。返回错误信息或 None。"""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return f"语法错误：第 {e.lineno} 行 {e.msg}"
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("generate", "rebalance")]
    if not fns:
        return "没有找到 generate(ctx)（信号模式）或 rebalance(ctx)（调仓模式）函数"
    return None


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"strategy_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def list_strategies() -> list[dict]:
    out = []
    for p in sorted(STRATEGY_DIR.glob("*.py")):
        if p.name.startswith("_"):
            continue
        info = {"key": p.stem, "file": p.name, "name": p.stem, "description": "", "exec": {}, "error": None}
        try:
            mod = _load(p)
            info.update(name=getattr(mod, "NAME", p.stem), description=getattr(mod, "DESCRIPTION", ""),
                        exec=getattr(mod, "EXEC", {}), use_sentiment=getattr(mod, "USE_SENTIMENT", True),
                        builtin=getattr(mod, "BUILTIN", False), mode=mode(mod),
                        universe=getattr(mod, "UNIVERSE", "stock"))
            fn = "rebalance" if mode(mod) == "rebalance" else "generate"
            if not callable(getattr(mod, fn, None)):
                info["error"] = f"缺少 {fn}(ctx)"
        except Exception as e:
            info["error"] = f"{type(e).__name__}: {e}"
        out.append(info)
    return out


def load(key: str):
    p = STRATEGY_DIR / f"{key}.py"
    if not p.exists():
        raise FileNotFoundError(f"策略 {key} 不存在")
    return _load(p)


def strategy_cfg(cfg: dict, mod) -> dict:
    """把策略的 EXEC / USE_SENTIMENT / MAX_POSITIONS 合并进配置副本。"""
    c = copy.deepcopy(cfg)
    for k, v in getattr(mod, "EXEC", {}).items():
        if k in EXEC_KEYS:
            c["strategy"][k] = v
    if not getattr(mod, "USE_SENTIMENT", True):
        c["sentiment"] = {**c["sentiment"], "min_score": -1, "full_score": -1}
    if hasattr(mod, "MAX_POSITIONS"):
        c["backtest"]["max_positions"] = int(mod.MAX_POSITIONS)
    return c


def mode(mod) -> str:
    return getattr(mod, "MODE", "signal")


def universe(ctx, mod):
    return ctx.etf if getattr(mod, "UNIVERSE", "stock") == "etf" else ctx


def run_weights(ctx, mod) -> pd.DataFrame:
    """调仓模式：rebalance(ctx) 返回 index=决策日、columns=代码 的目标权重表。"""
    w = mod.rebalance(ctx)
    if w is None or len(w) == 0:
        return pd.DataFrame()
    w = pd.DataFrame(w).fillna(0)
    w.index = pd.to_datetime(w.index)
    w.columns = [str(c).zfill(6) for c in w.columns]
    s = w.sum(axis=1)
    if (s > 1.0001).any():
        raise ValueError(f"rebalance() 某些日期权重合计超过 1（最大 {s.max():.3f}）")
    return w.sort_index()


def _weights_to_rows(w: pd.DataFrame) -> pd.DataFrame:
    if w.empty:
        return pd.DataFrame(columns=["date", "code", "score"])
    st = w.stack()
    st = st[st > 0]
    df = pd.DataFrame({"date": st.index.get_level_values(0), "code": st.index.get_level_values(1), "score": st.values})
    return df.sort_values(["date", "score"], ascending=[True, False]).reset_index(drop=True)


def run_signals(ctx, mod) -> pd.DataFrame:
    if mode(mod) == "rebalance":  # 调仓模式：把目标持仓转成「信号」行（score=权重），供看板展示
        w = run_weights(ctx, mod)
        if w.empty:
            return pd.DataFrame(columns=["date", "code", "score"])
        return _weights_to_rows(w)
    sig = mod.generate(ctx)
    if sig is None or len(sig) == 0:
        return pd.DataFrame(columns=["date", "code", "score"])
    sig = pd.DataFrame(sig)
    missing = {"date", "code"} - set(sig.columns)
    if missing:
        raise ValueError(f"generate() 返回的表缺少列 {missing}")
    if "score" not in sig:
        sig["score"] = 0.0
    sig["date"] = pd.to_datetime(sig["date"])
    sig["code"] = sig["code"].astype(str).str.zfill(6)
    return sig.sort_values(["date", "score"], ascending=[True, False]).reset_index(drop=True)


ZERO_COST = {"commission": 0, "min_commission": 0, "stamp_tax": 0, "transfer_fee": 0, "slippage": 0}


def run_backtest(ctx, mod, cfg, zero_cost=False, cache=None) -> dict:
    """zero_cost=True：不计任何交易成本（对照用）。cache：传入上次的 signals/weights 避免重复计算。"""
    c = strategy_cfg(cfg, mod)
    if zero_cost:
        c["backtest"] = {**c["backtest"], **ZERO_COST}
    if mode(mod) == "rebalance":
        w = cache["weights"] if cache else run_weights(ctx, mod)
        costs = {**getattr(mod, "COSTS", {}), **(ZERO_COST if zero_cost else {})}
        res = portfolio.run(universe(ctx, mod).ind, w, c["backtest"], costs)
        res["weights"] = w
        res["signals"] = _weights_to_rows(w)
    else:
        sig = cache["signals"] if cache else run_signals(ctx, mod)
        res = backtest.run(ctx.ind, sig, ctx.senti, c)
        res["signals"] = sig
    res["cfg"] = c
    return res


def safe_list():
    try:
        return list_strategies()
    except Exception:
        traceback.print_exc()
        return []
