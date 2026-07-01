"""AI 模拟盘撮合（A 股真实费率规则）。

- 价格：以 get_single_stock().data.f43（最新价）立即成交
- 数量：100 股整手；向下取整
- 费率：
  * 佣金：max(amount × 0.00025, 5)  （万 2.5，最低 5 元）
  * 印花税：sell 时 amount × 0.0005  （万 5，卖出单边）
"""

from dataclasses import dataclass
from typing import Optional

# ============================================================
# 费率常量
# ============================================================
COMMISSION_RATE = 0.00025  # 万 2.5
COMMISSION_MIN = 5.0  # 最低 5 元
STAMP_TAX_RATE = 0.0005  # 印花税万 5（卖出单边）
LOT_SIZE = 100  # A 股最小 100 股整手

# A 股涨跌停阈值（%）：按股票代码前缀识别板块
# 688xxx 科创板 / 300xxx 301xxx 创业板 → ±20%
# 830xxx+ 920xxx 北交所 → ±30%
# 其余（沪主板 60xxxx / 深主板 00xxxx）→ ±10%
LIMIT_THRESHOLD_MAIN = 10.0  # 主板 ±10%
LIMIT_THRESHOLD_STAR_CHINEXT = 20.0  # 科创 / 创业 ±20%
LIMIT_THRESHOLD_BSE = 30.0  # 北交所 ±30%


@dataclass(slots=True)
class MatchResult:
    """撮合结果"""

    ok: bool
    side: str  # "buy" / "sell"
    code: str
    requested_qty: int
    actual_qty: int  # 整手化后的股数（< 100 时 ok=False）
    price: float
    amount: float  # 成交总额 = price * actual_qty
    commission: float
    stamp_tax: float
    fee_total: float
    reason: str = ""  # 当 ok=False 时的拒绝原因


def calc_fee(side: str, amount: float) -> tuple[float, float, float]:
    """计算单笔手续费。返回 (commission, stamp_tax, total)"""
    commission = max(amount * COMMISSION_RATE, COMMISSION_MIN)
    stamp_tax = amount * STAMP_TAX_RATE if side == "sell" else 0.0
    return commission, stamp_tax, commission + stamp_tax


def round_lot(qty: int) -> int:
    """把任意股数向下取整到 100 整手。"""
    if qty < LOT_SIZE:
        return 0
    return (qty // LOT_SIZE) * LOT_SIZE


def _limit_threshold_for_code(code: str) -> float:
    """按股票代码前缀确定涨跌停阈值（%）。

    688xxx / 689xxx → 科创板 ±20%
    300xxx / 301xxx → 创业板 ±20%
    830xxx ~ 87xxx / 920xxx → 北交所 ±30%
    其余 → 主板 ±10%
    """
    if not code or len(code) < 6:
        return LIMIT_THRESHOLD_MAIN
    prefix3 = code[:3]
    if code.startswith("688") or code.startswith("689"):
        return LIMIT_THRESHOLD_STAR_CHINEXT
    if prefix3 in ("300", "301"):
        return LIMIT_THRESHOLD_STAR_CHINEXT
    if code.startswith(("83", "87", "920")):
        return LIMIT_THRESHOLD_BSE
    return LIMIT_THRESHOLD_MAIN


def _is_at_limit(
    side: str,
    price: float,
    code: str,
    last_close: Optional[float],
    change_pct: Optional[float],
) -> tuple[bool, str]:
    """检测是否触及涨跌停。

    Args:
        side: "buy" / "sell"
        price: 当前成交价
        last_close: 昨收价
        change_pct: 涨跌幅（%，如 9.99）

    Returns:
        ``(blocked, reason)`` —— blocked=True 时 reason 描述具体板子
    """
    if last_close is None or last_close <= 0:
        # 拿不到昨收，只能降级用 change_pct 推算
        if change_pct is None:
            return False, ""
        threshold = 9.5  # 保守阈值（10% 板）或 19.5（20% 板）
        if side == "buy" and change_pct >= threshold:
            return True, f"逼近涨停 (涨跌幅={change_pct:.2f}% ≥ {threshold}%，无昨收兜底保守拦截)"
        if side == "sell" and change_pct <= -threshold:
            return True, f"逼近跌停 (涨跌幅={change_pct:.2f}% ≤ -{threshold}%，无昨收兜底保守拦截)"
        return False, ""

    threshold = _limit_threshold_for_code(code)
    # 用昨收价直接计算涨跌幅，比 push2 的 f45 更可靠
    actual_chg = (price - last_close) / last_close * 100.0
    if side == "buy" and actual_chg >= threshold - 0.05:
        board = (
            "科创板/创业板" if threshold == LIMIT_THRESHOLD_STAR_CHINEXT
            else "北交所" if threshold == LIMIT_THRESHOLD_BSE
            else "主板"
        )
        return True, f"涨停板买入拦截 ({board} 涨幅={actual_chg:.2f}% ≥ {threshold}%，接近或触及涨停)"
    if side == "sell" and actual_chg <= -threshold + 0.05:
        board = (
            "科创板/创业板" if threshold == LIMIT_THRESHOLD_STAR_CHINEXT
            else "北交所" if threshold == LIMIT_THRESHOLD_BSE
            else "主板"
        )
        return True, f"跌停板卖出拦截 ({board} 跌幅={actual_chg:.2f}% ≤ -{threshold}%，接近或触及跌停)"
    return False, ""


def match_order(
    side: str,
    code: str,
    qty: int,
    price: float,
    cash_available: float,
    position_qty: int,
    last_close: Optional[float] = None,
    change_pct: Optional[float] = None,
) -> MatchResult:
    """撮合一笔订单（A 股真实费率）。

    Args:
        side: "buy" / "sell"
        code: 股票代码（用于识别主板 / 科创 / 创业板 / 北交所，确定涨跌停阈值）
        qty: 请求股数（将自动取整到 100 整手）
        price: 成交价
        cash_available: 账户当前可用现金（buy 时校验）
        position_qty: 当前持仓股数（sell 时校验）
        last_close: 昨收价；非 None 时启用涨跌停板拦截
        change_pct: 涨跌幅（%，如 9.99）；仅当 last_close=None 时使用保守兜底

    Returns:
        MatchResult —— ok=False 时 reason 写明原因
    """
    if side not in ("buy", "sell"):
        return MatchResult(
            ok=False,
            side=side,
            code=code,
            requested_qty=qty,
            actual_qty=0,
            price=price,
            amount=0.0,
            commission=0.0,
            stamp_tax=0.0,
            fee_total=0.0,
            reason=f"非法方向: {side}",
        )
    if price <= 0:
        return MatchResult(
            ok=False,
            side=side,
            code=code,
            requested_qty=qty,
            actual_qty=0,
            price=price,
            amount=0.0,
            commission=0.0,
            stamp_tax=0.0,
            fee_total=0.0,
            reason="价格异常",
        )

    # ── 涨跌停板拦截 ──
    if last_close is not None or change_pct is not None:
        blocked, block_reason = _is_at_limit(side, price, code, last_close, change_pct)
        if blocked:
            return MatchResult(
                ok=False,
                side=side,
                code=code,
                requested_qty=qty,
                actual_qty=0,
                price=price,
                amount=0.0,
                commission=0.0,
                stamp_tax=0.0,
                fee_total=0.0,
                reason=block_reason,
            )

    actual_qty = round_lot(qty)
    if actual_qty == 0:
        return MatchResult(
            ok=False,
            side=side,
            code=code,
            requested_qty=qty,
            actual_qty=0,
            price=price,
            amount=0.0,
            commission=0.0,
            stamp_tax=0.0,
            fee_total=0.0,
            reason=f"qty<{LOT_SIZE} 不足一整手",
        )

    amount = actual_qty * price
    commission, stamp_tax, fee_total = calc_fee(side, amount)

    if side == "buy":
        need = amount + fee_total
        if cash_available < need:
            # 尝试降一档
            max_amount = (cash_available - COMMISSION_MIN) / (1 + COMMISSION_RATE)
            actual_qty = round_lot(int(max_amount / price))
            if actual_qty < LOT_SIZE:
                return MatchResult(
                    ok=False,
                    side=side,
                    code=code,
                    requested_qty=qty,
                    actual_qty=0,
                    price=price,
                    amount=0.0,
                    commission=0.0,
                    stamp_tax=0.0,
                    fee_total=0.0,
                    reason=f"现金不足 (需 {need:.2f}, 可用 {cash_available:.2f})",
                )
            amount = actual_qty * price
            commission, stamp_tax, fee_total = calc_fee(side, amount)
    else:  # sell
        if position_qty < actual_qty:
            # 按最大可卖量截断
            actual_qty = round_lot(position_qty)
            if actual_qty < LOT_SIZE:
                return MatchResult(
                    ok=False,
                    side=side,
                    code=code,
                    requested_qty=qty,
                    actual_qty=0,
                    price=price,
                    amount=0.0,
                    commission=0.0,
                    stamp_tax=0.0,
                    fee_total=0.0,
                    reason=f"持仓不足 (需 {qty}, 持仓 {position_qty})",
                )
            amount = actual_qty * price
            commission, stamp_tax, fee_total = calc_fee(side, amount)

    return MatchResult(
        ok=True,
        side=side,
        code=code,
        requested_qty=qty,
        actual_qty=actual_qty,
        price=price,
        amount=amount,
        commission=commission,
        stamp_tax=stamp_tax,
        fee_total=fee_total,
    )


def calc_realized_pnl(avg_cost: float, sell_qty: int, sell_price: float, fee: float) -> float:
    """卖出时计算已实现盈亏： (sell_price - avg_cost) * qty - fee"""
    return (sell_price - avg_cost) * sell_qty - fee


def calc_new_avg_cost(old_qty: int, old_avg_cost: float, buy_qty: int, buy_price: float, buy_fee: float) -> float:
    """加仓后新的加权平均成本。买费用计入成本。"""
    if old_qty + buy_qty == 0:
        return 0.0
    new_qty = old_qty + buy_qty
    old_value = old_qty * old_avg_cost
    new_value = buy_qty * buy_price + buy_fee
    return (old_value + new_value) / new_qty
