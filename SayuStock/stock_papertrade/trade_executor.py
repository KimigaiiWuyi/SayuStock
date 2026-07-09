# pyright: reportMissingImports=false
"""交易执行抽象层（TradeExecutor）。

把 agent 的"撮合 / 写成交流水 / 改持仓"三步交易操作收敛成一个**可替换的类**：

- ``PaperTradeExecutor`` —— 当前模拟盘实现（撮合器 + SQLModel 落库 + 现金维护）。
- ``LiveTradeExecutor``  —— 实盘执行桩；接入券商 / 柜台交易 API 后实现同名三方法即可。

**未来从模拟盘切实盘**：实现一个新的 ``TradeExecutor`` 子类（或补全
``LiveTradeExecutor``），把默认后端切到 ``live``（``set_default_backend("live")``
或 ``get_executor("live")``）即可——三个 ``papertrade_*`` ai_tools 的签名、返回契约
以及决策代理提示词**都不用改**，因为它们只跟本抽象层打交道。

三个方法与 agent 三步操作一一对应：
  ``match``           → 定价 / 校验（返回 :class:`MatchResult`）
  ``record_trade``    → 写成交流水 + 维护现金（返回 :class:`RecordResult`）
  ``update_position`` → 落持仓（返回 position id）
"""

import datetime as _dt
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from gsuid_core.logger import logger as _gslogger

from . import db
from .matcher import MatchResult
from .matcher import match_order as _match_order
from .quote_service import quote_service
from .trading_calendar import trading_day_summary, should_run_papertrade


# ============================================================
# 结果契约
# ============================================================
@dataclass(slots=True)
class RecordResult:
    """``record_trade`` 结果。message 为面向 LLM 的中文说明（成功/失败原因）。"""

    ok: bool
    trade_id: int = 0
    cash_delta: float = 0.0
    message: str = ""


def _reject_match(side: str, code: str, qty: int, reason: str) -> MatchResult:
    """构造一个 ok=False 的撮合结果（价格/费用全 0）。"""
    return MatchResult(
        ok=False,
        side=side,
        code=code,
        requested_qty=qty,
        actual_qty=0,
        price=0.0,
        amount=0.0,
        commission=0.0,
        stamp_tax=0.0,
        fee_total=0.0,
        reason=reason,
    )


# ============================================================
# 抽象接口
# ============================================================
class TradeExecutor(ABC):
    """交易执行抽象接口。换后端（模拟盘 ↔ 实盘）时实现同名三方法即可。"""

    backend: str = "base"

    @abstractmethod
    async def match(
        self,
        *,
        side: str,
        stock_code: str,
        qty: int,
        price: float = 0.0,
        cash_available: float = 0.0,
        position_qty: int = 0,
    ) -> MatchResult:
        """撮合定价 + 涨跌停 / 现金 / 持仓 / 交易时段校验，返回 MatchResult。"""
        ...

    @abstractmethod
    async def record_trade(
        self,
        *,
        group_id: str,
        bot_id: str,
        stock_code: str,
        stock_name: str,
        secid: str,
        side: str,
        price: float,
        qty: int,
        amount: float,
        fee: float,
        realized_pnl: float = 0.0,
        reason: str = "",
        snapshot: str = "",
        decision_id: int = 0,
        mode: str = "balanced",
    ) -> RecordResult:
        """写成交流水并维护账户现金（原子）。"""
        ...

    @abstractmethod
    async def update_position(
        self,
        *,
        group_id: str,
        bot_id: str,
        stock_code: str,
        stock_name: str,
        secid: str,
        qty: int,
        avg_cost: float,
        last_quote_price: float = 0.0,
    ) -> int:
        """落持仓（qty=0 时删除记录），返回 position id（无则 0）。"""
        ...


# ============================================================
# 模拟盘实现（现有逻辑）
# ============================================================
class PaperTradeExecutor(TradeExecutor):
    """模拟盘执行器：实时行情定价 + A 股费率/涨跌停/T+1 + SQLModel 落库。"""

    backend = "paper"

    async def match(
        self,
        *,
        side: str,
        stock_code: str,
        qty: int,
        price: float = 0.0,
        cash_available: float = 0.0,
        position_qty: int = 0,
    ) -> MatchResult:
        # ── 交易时段守卫：非交易日 / 非交易时段一律拒单 ──
        if not should_run_papertrade():
            _, _, desc = trading_day_summary()
            return _reject_match(
                side, stock_code, qty,
                f"非交易时段拒绝撮合（{desc}）——真实市场此刻无法成交，请改 hold 等开盘",
            )

        # ── 拉实时行情：成交价 + 昨收 + 涨跌幅 + 名称（涨跌停 / ST 拦截用） ──
        live_price: Optional[float] = None
        last_close: Optional[float] = None
        change_pct: Optional[float] = None
        name: Optional[str] = None
        try:
            # secid 格式：沪市(6开头) → "1.xxxxxx"；深市/北交所 → "0.xxxxxx"
            secid = f"1.{stock_code}" if stock_code.startswith("6") else f"0.{stock_code}"
            entry = await quote_service.get_quote_detail(secid)
            if entry is not None:
                live_price = entry.price
                last_close = entry.last_close
                change_pct = entry.change_pct
                name = entry.name  # f57 名称，含 ST/*ST 前缀，供撮合层判风险警示股
        except Exception as e:
            _gslogger.debug(f"[SayuStock][PaperTrade] match_quote_fetch failed stock={stock_code}: {e}")

        if live_price is None or live_price <= 0:
            return _reject_match(
                side, stock_code, qty,
                f"实时行情不可达（{stock_code}），拒绝撮合——不允许按参考价/旧价成交，请稍后重试或改 hold",
            )

        # ── 参考价偏差提示：LLM 传的旧价与实时价差过大时写进 reason 提醒 ──
        deviation_note: str = ""
        if price and price > 0:
            dev_pct: float = (live_price - price) / price * 100.0
            if abs(dev_pct) >= 0.5:
                deviation_note = (
                    f"按实时价 {live_price:.2f} 成交（参考价 {price:.2f} 已过时，偏差 {dev_pct:+.2f}%）"
                )

        res = _match_order(
            side=side,
            code=stock_code,
            qty=qty,
            price=live_price,
            cash_available=cash_available,
            position_qty=position_qty,
            last_close=last_close,
            change_pct=change_pct,
            name=name,
        )
        if res.ok and deviation_note:
            res.reason = deviation_note if not res.reason else f"{res.reason}；{deviation_note}"
        return res

    async def record_trade(
        self,
        *,
        group_id: str,
        bot_id: str,
        stock_code: str,
        stock_name: str,
        secid: str,
        side: str,
        price: float,
        qty: int,
        amount: float,
        fee: float,
        realized_pnl: float = 0.0,
        reason: str = "",
        snapshot: str = "",
        decision_id: int = 0,
        mode: str = "balanced",
    ) -> RecordResult:
        # ── 实时价偏差校验：拦截"按候选池入池旧价成交"的失真流水 ──
        try:
            _secid = f"1.{stock_code}" if stock_code.startswith("6") else f"0.{stock_code}"
            _live: Optional[float] = await quote_service.get_quote(_secid)
        except Exception:
            _live = None
        if _live is not None and _live > 0 and price > 0:
            _dev_pct: float = abs(price - _live) / _live * 100.0
            if _dev_pct > 3.0:
                return RecordResult(
                    ok=False,
                    message=(
                        f"⚠️ trade_insert 拒绝：传入 price={price:.2f} 与实时行情 {_live:.2f} "
                        f"偏差 {_dev_pct:.1f}%（>3%），疑似使用了入池时的旧价。"
                        f"请重新调 papertrade_match_order 并使用其返回的实时成交价。"
                    ),
                )

        # ── A 股 T+1 拦截 ──
        if side == "sell":
            # 用东八区当天日期（系统时钟如果漂移到 UTC，sell 拦截可能误判）
            try:
                from zoneinfo import ZoneInfo

                today_cn = _dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()
            except Exception:
                today_cn = _dt.date.today()
            try:
                locked_qty: int = await db.PaperTradeRepo.locked_qty_today(group_id, bot_id, stock_code, today=today_cn)
            except Exception:
                locked_qty = 0  # 防御：DB 异常不要阻塞 sell，让撮合层兜底
            if locked_qty > 0:
                return RecordResult(
                    ok=False,
                    message=(
                        f"⚠️ A 股 T+1 拦截：{stock_code} {stock_name or ''}今天已买入 "
                        f"{locked_qty} 股，按 A 股结算规则需留仓到下一交易日开盘前才可卖；"
                        f"请改 hold，或换一只非今天买入的标的卖。"
                    ),
                )

        try:
            t = await db.PaperTradeRepo.append_with_cash_update(
                group_id,
                bot_id,
                stock_code=stock_code,
                stock_name=stock_name,
                secid=secid,
                side=side,
                price=price,
                qty=qty,
                amount=amount,
                fee=fee,
                realized_pnl=realized_pnl,
                reason=reason,
                snapshot=snapshot,
                decision_id=decision_id if decision_id > 0 else None,
                mode=mode,
            )
        except (ValueError, RuntimeError) as e:
            # side 非法 / 账户不存在——不写库，明示错误给 LLM
            return RecordResult(ok=False, message=f"⚠️ trade_insert 失败: {e}")

        cash_delta: float = -(amount + fee) if side == "buy" else (amount - fee + realized_pnl)
        if side == "buy":
            formula: str = "buy: cash -= amount+fee"
        else:
            formula = "sell: cash += amount-fee+realized_pnl, principal += realized_pnl"
        return RecordResult(
            ok=True,
            trade_id=t.id,
            cash_delta=cash_delta,
            message=f"ok trade_id={t.id}  cash_delta={cash_delta:+,.2f}  ({formula})",
        )

    async def update_position(
        self,
        *,
        group_id: str,
        bot_id: str,
        stock_code: str,
        stock_name: str,
        secid: str,
        qty: int,
        avg_cost: float,
        last_quote_price: float = 0.0,
    ) -> int:
        p = await db.PaperPositionRepo.upsert(
            group_id,
            bot_id,
            stock_code=stock_code,
            stock_name=stock_name,
            secid=secid,
            qty=qty,
            avg_cost=avg_cost,
            last_quote_price=last_quote_price if last_quote_price > 0 else None,
            last_quote_at=_dt.datetime.now() if last_quote_price and last_quote_price > 0 else None,
        )
        return p.id if p else 0


# ============================================================
# 实盘执行桩（接入券商 API 时实现）
# ============================================================
class LiveTradeExecutor(TradeExecutor):
    """实盘执行器桩。

    接入真实券商 / 柜台交易通道时，在这里实现：
      - ``match``           → 询价 / 预下单校验（可用余额、可卖持仓、涨跌停）
      - ``record_trade``    → 真实报单 + 回报落库 + 资金同步
      - ``update_position`` → 以券商持仓回报为准更新本地持仓
    未实现前一律拒绝，避免"以为在实盘其实没成交"的脏状态。
    """

    backend = "live"

    _NOT_READY = "⚠️ 实盘交易通道尚未接入（LiveTradeExecutor 未实现），请勿下单；如需模拟请切回 paper 后端。"

    async def match(
        self,
        *,
        side: str,
        stock_code: str,
        qty: int,
        price: float = 0.0,
        cash_available: float = 0.0,
        position_qty: int = 0,
    ) -> MatchResult:
        return _reject_match(side, stock_code, qty, self._NOT_READY)

    async def record_trade(
        self,
        *,
        group_id: str,
        bot_id: str,
        stock_code: str,
        stock_name: str,
        secid: str,
        side: str,
        price: float,
        qty: int,
        amount: float,
        fee: float,
        realized_pnl: float = 0.0,
        reason: str = "",
        snapshot: str = "",
        decision_id: int = 0,
        mode: str = "balanced",
    ) -> RecordResult:
        return RecordResult(ok=False, message=self._NOT_READY)

    async def update_position(
        self,
        *,
        group_id: str,
        bot_id: str,
        stock_code: str,
        stock_name: str,
        secid: str,
        qty: int,
        avg_cost: float,
        last_quote_price: float = 0.0,
    ) -> int:
        raise NotImplementedError(self._NOT_READY)


# ============================================================
# 后端选择（默认模拟盘；未来切实盘的唯一开关点）
# ============================================================
_EXECUTORS: dict[str, TradeExecutor] = {
    "paper": PaperTradeExecutor(),
    "live": LiveTradeExecutor(),
}
_DEFAULT_BACKEND: str = "paper"


def set_default_backend(backend: str) -> None:
    """切换全局默认交易后端（"paper" / "live"）。接实盘后调用一次即可全局切换。"""
    global _DEFAULT_BACKEND
    key = str(backend).lower()
    if key not in _EXECUTORS:
        raise ValueError(f"未知交易后端: {backend!r}，可选 {sorted(_EXECUTORS)}")
    _DEFAULT_BACKEND = key


def get_executor(backend: Optional[str] = None) -> TradeExecutor:
    """拿交易执行器。backend 留空用全局默认（当前 "paper"）。"""
    key = str(backend).lower() if backend else _DEFAULT_BACKEND
    return _EXECUTORS.get(key, _EXECUTORS[_DEFAULT_BACKEND])
