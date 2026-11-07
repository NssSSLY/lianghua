"""策略上下文：一次加载，所有策略共用。策略脚本的 generate(ctx) 拿到的就是它。

常用属性（都是宽表：index=交易日, columns=股票代码；价格均为前复权口径，适合算指标）：
    ctx.open / high / low / close / volume / amount / ret(当日涨跌幅, 小数)
    ctx.ind            基础指标 dict：is_zt / is_dt / broken(炸板) / streak(连板数) / one_word / float_mv / bars ...
    ctx.senti          每日情绪 DataFrame：score / zt_count / dt_count / zb_rate / max_streak / premium
    ctx.lhb            龙虎榜（code/date/net_buy/buy/sell/reason/explain）
    ctx.lhb_jg         龙虎榜机构统计（code/date/jg_buyers/jg_sellers/jg_net/...）
    ctx.board          主线板块：mainline(日期×板块) / in_mainline / leader(日期×股票)
    ctx.codes          可交易股票（已剔除 ST、北交所）
    ctx.names          代码 -> 名称
    ctx.cfg            config.yaml
    ctx.etf            ETF 池数据（.close/.open/.amount/.ind ...，同上）
    ctx.ths_limitup    同花顺涨停池长表（date/code/reason 涨停原因/boards 板数/type/first_time ...）
    ctx.themes         按当时涨停原因识别的题材：mainline {日期: [主线标签]}、strength（日期×标签）
    ctx.theme_masks    in_theme / leader（日期×股票）宽表
方法：
    ctx.rebalance_dates(freq, universe)   调仓决策日（'W' 每周 / 'M' 每月 / N 每N日）
    ctx.top_n_weights(score, n, dates, eligible)  决策日按得分取前 n 等权
    ctx.signals(mask, score)   把布尔宽表转成信号表（自动限定可交易股票、上市满60日、非停牌）
    ctx.lhb_wide(col)          龙虎榜某列转成宽表（未上榜为 NaN）
    ctx.board_state(**params)  用自定义参数重新计算主线板块
"""
from functools import cached_property

import numpy as np
import pandas as pd

from . import boards, data, etf, indicators, portfolio, sentiment, ths


class Universe:
    """另一组品种（如 ETF）的宽表数据，属性与 Context 相同：open/high/low/close/volume/amount/ret/ind。"""

    def __init__(self, ind):
        self.ind = ind
        self.dates = ind["close"].index

    open = property(lambda s: s.ind["adj"]["open"])
    high = property(lambda s: s.ind["adj"]["high"])
    low = property(lambda s: s.ind["adj"]["low"])
    close = property(lambda s: s.ind["adj"]["close"])
    volume = property(lambda s: s.ind["volume"])
    amount = property(lambda s: s.ind["amount"])
    ret = property(lambda s: s.ind["ret"])


class Context:
    def __init__(self, cfg, start=None, end=None):
        self.cfg = cfg
        self.ddir = cfg["data_dir"]
        sl = data.load_stock_list(self.ddir)
        self.stock_list = sl
        self.names = sl.set_index("code")["name"].to_dict()
        self.names.update(etf.names(self.ddir))
        self.st = data.st_codes(sl)
        self.codes = data.tradable_codes(sl, cfg["markets"])
        self.all_codes = data.download_codes(sl, cfg["markets"], cfg.get("include_st", True))
        self.start = start or cfg["download_start"]
        self.end = end
        panel = data.load_panel(self.ddir, self.all_codes, self.start, end)
        self.ind = indicators.build(panel, self.st)
        self.senti = sentiment.compute(self.ind, st=self.st)
        self.dates = self.ind["close"].index
        self.T = self.dates[-1]

    # ---------------- 价格（前复权）
    @property
    def open(self):
        return self.ind["adj"]["open"]

    @property
    def high(self):
        return self.ind["adj"]["high"]

    @property
    def low(self):
        return self.ind["adj"]["low"]

    @property
    def close(self):
        return self.ind["adj"]["close"]

    @property
    def volume(self):
        return self.ind["volume"]

    @property
    def amount(self):
        return self.ind["amount"]

    @property
    def ret(self):
        return self.ind["ret"]

    # ---------------- 龙虎榜 / 板块（按需加载）
    @cached_property
    def lhb(self):
        return data.load_lhb(self.ddir, self.start, self.end or self.T, detail=True)

    @cached_property
    def lhb_jg(self):
        return data.load_lhb_jg(self.ddir, self.start, self.end or self.T)

    def lhb_wide(self, col="net_buy", src="lhb"):
        df = self.lhb if src == "lhb" else self.lhb_jg
        if df.empty:
            return pd.DataFrame(np.nan, index=self.dates, columns=self.close.columns)
        w = df.pivot_table(index="date", columns="code", values=col, aggfunc="first")
        return w.reindex(index=self.dates, columns=self.close.columns)

    @cached_property
    def members(self):
        st = self.cfg["strategy"]
        return boards.membership(boards.download(self.ddir), self.codes, st.get("board_kinds", ["概念"]))

    @cached_property
    def board(self):
        return boards.compute(self.ind, self.members, self.cfg["strategy"])

    def board_state(self, **params):
        p = {**self.cfg["strategy"], **params}
        M = boards.membership(boards.download(self.ddir), self.codes, p.get("board_kinds", ["概念"]))
        return boards.compute(self.ind, M, p)

    # ---------------- 同花顺涨停原因 → 按当时数据的题材
    @cached_property
    def ths_limitup(self):
        """同花顺涨停池（含涨停原因 reason、几天几板 boards），约一年历史 + 每日归档。"""
        return ths.load_limitup(self.ddir, self.start, self.end)

    @cached_property
    def themes(self):
        """题材识别：daily / strength（日期×标签）、mainline {日期: [主线标签]}、stock_tags。参数见 config.yaml → themes。"""
        p = self.cfg.get("themes", {})
        return ths.themes(self.ths_limitup, self.dates, p.get("window", 3), p.get("top_k", 5), p.get("min_count", 4))

    @cached_property
    def theme_masks(self):
        """in_theme / leader（日期×股票）：属于当天主线题材 / 是该题材的最高连板龙头。"""
        return ths.theme_masks(self.themes, self.ths_limitup, self.ind, self.cfg["strategy"].get("lookback_days", 10))

    # ---------------- ETF / 调仓
    @cached_property
    def etf(self) -> Universe:
        """ETF 池（quant/etf.py 的 ETF_POOL）。ctx.etf.close 等用法同个股。"""
        panel = etf.load_panel(self.ddir, self.start, self.end)
        return Universe(indicators.build(panel))

    def rebalance_dates(self, freq="W", universe=None):
        """调仓决策日：'W' 每周最后交易日 / 'M' 每月最后交易日 / 整数 N 每 N 个交易日。"""
        return portfolio.rebalance_dates((universe or self).dates, freq)

    def top_n_weights(self, score: pd.DataFrame, n: int, dates, eligible: pd.DataFrame = None) -> pd.DataFrame:
        """在每个决策日按 score 取前 n 名等权（score 越大越好；NaN 不选）。返回权重表。"""
        sc = score.loc[score.index.intersection(dates)]
        if eligible is not None:
            sc = sc.where(eligible.reindex_like(sc).fillna(False).astype(bool))
        rank = sc.rank(axis=1, ascending=False, method="first")
        w = (rank <= n).astype(float)
        return w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)

    # ---------------- 信号
    def signals(self, mask: pd.DataFrame, score=None, min_bars=60) -> pd.DataFrame:
        """mask：布尔宽表（T 日收盘后成立的条件）。score：同形状的数值宽表或 None（越大越优先）。"""
        m = mask.reindex(index=self.dates, columns=self.close.columns).fillna(False).astype(bool)
        m &= ~self.ind["suspended"] & (self.ind["bars"] >= min_bars)
        m.loc[:, ~m.columns.isin(self.codes)] = False
        s = m.stack()
        sig = s[s].index.to_frame(index=False)
        sig.columns = ["date", "code"]
        if sig.empty:
            return pd.DataFrame(columns=["date", "code", "score"])
        if score is None:
            sig["score"] = 0.0
        else:
            sc = score.reindex(index=self.dates, columns=self.close.columns).stack()
            sig["score"] = sc.reindex(pd.MultiIndex.from_frame(sig[["date", "code"]])).values
        sig["close"] = self.ind["close"].stack().reindex(pd.MultiIndex.from_frame(sig[["date", "code"]])).values
        return sig.sort_values(["date", "score"], ascending=[True, False]).reset_index(drop=True)
