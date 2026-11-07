"""MiniQMT 交易封装（银河证券 QMT 极简模式）。

使用前提：
1. 找银河客户经理开通 QMT / MiniQMT 权限，并完成程序化交易报告
2. 启动 QMT 客户端，登录时勾选「极简模式」（即 MiniQMT），保持登录状态
3. 安装 xtquant：pip install xtquant，或把 QMT 安装目录 bin.x64\\Lib\\site-packages 下的 xtquant 拷到环境里
4. config.yaml 里 live.qmt_path 填 QMT 安装目录下的 userdata_mini 文件夹

dry_run=True 时只打印、不真实下单。
"""
import math


def to_qmt_code(code: str) -> str:
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


class Broker:
    def __init__(self, live_cfg: dict):
        self.cfg = live_cfg
        self.dry = live_cfg.get("dry_run", True)
        from xtquant import xtconstant, xtdata
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount
        self.xc, self.xtdata = xtconstant, xtdata
        self.trader = XtQuantTrader(live_cfg["qmt_path"], int(live_cfg["session_id"]))
        self.acc = StockAccount(str(live_cfg["account_id"]))
        self.trader.start()
        if self.trader.connect() != 0:
            raise RuntimeError("连接 MiniQMT 失败：确认 QMT 已用极简模式登录，qmt_path 指向 userdata_mini")
        if self.trader.subscribe(self.acc) != 0:
            raise RuntimeError("订阅账户失败：检查 account_id")
        print(f"已连接 MiniQMT，账户 {live_cfg['account_id']}，{'模拟(不下单)' if self.dry else '实盘'}")

    # ---------- 行情
    def ticks(self, codes):
        """返回 {6位代码: tick字典}，含 lastPrice/open/high/low/lastClose。"""
        q = self.xtdata.get_full_tick([to_qmt_code(c) for c in codes])
        return {k.split(".")[0]: v for k, v in q.items()}

    # ---------- 账户
    def cash(self) -> float:
        return self.trader.query_stock_asset(self.acc).cash

    def total_asset(self) -> float:
        return self.trader.query_stock_asset(self.acc).total_asset

    def positions(self) -> dict:
        """{6位代码: (总股数, 可卖股数, 成本价)}"""
        out = {}
        for p in self.trader.query_stock_positions(self.acc) or []:
            if p.volume > 0:
                out[p.stock_code.split(".")[0]] = (p.volume, p.can_use_volume, p.open_price)
        return out

    # ---------- 下单
    def buy(self, code, price, cash_amount, remark="lowbuy"):
        shares = math.floor(cash_amount / price / 100) * 100
        if shares <= 0:
            print(f"  [跳过] {code} 资金不足一手")
            return None
        return self._order(code, self.xc.STOCK_BUY, shares, price, remark)

    def sell(self, code, shares, price, remark="exit"):
        return self._order(code, self.xc.STOCK_SELL, shares, price, remark)

    def _order(self, code, side, shares, price, remark):
        price = round(price, 2)
        tag = "买入" if side == self.xc.STOCK_BUY else "卖出"
        print(f"  [{tag}] {code} {shares}股 @ {price:.2f}  ({remark})")
        if self.dry:
            return None
        oid = self.trader.order_stock(self.acc, to_qmt_code(code), side, int(shares),
                                      self.xc.FIX_PRICE, price, "lowbuy", remark)
        if oid == -1:
            print(f"  !! 下单失败 {code}")
        return oid

    def cancel_all_buys(self):
        for o in self.trader.query_stock_orders(self.acc, True) or []:
            if o.order_type == self.xc.STOCK_BUY:
                print(f"  [撤单] {o.stock_code} {o.order_volume}股")
                if not self.dry:
                    self.trader.cancel_order_stock(self.acc, o.order_id)
