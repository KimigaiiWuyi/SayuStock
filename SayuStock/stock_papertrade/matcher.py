"""AI 模拟盘撮合（A 股真实费率规则）。

- 价格：以 get_single_stock().data.f43（最新价）立即成交
- 数量：100 股整手；向下取整
- 费率：
  * 佣金：max(amount × 0.00025, 5)  （万 2.5，最低 5 元）
  * 印花税：sell 时 amount × 0.0005  （万 5，卖出单边）
"""

from dataclasses import dataclass

# ============================================================
# 费率常量
# ============================================================
COMMISSION_RATE = 0.00025  # 万 2.5
COMMISSION_MIN = 5.0  # 最低 5 元
STAMP_TAX_RATE = 0.0005  # 印花税万 5（卖出单边）
LOT_SIZE = 100  # A 股最小 100 股整手


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


def match_order(
    side: str,
    code: str,
    qty: int,
    price: float,
    cash_available: float,
    position_qty: int,
) -> MatchResult:
    """撮合一笔订单（A 股真实费率）。

    Args:
        side: "buy" / "sell"
        code: 股票代码
        qty: 请求股数（将自动取整到 100 整手）
        price: 成交价
        cash_available: 账户当前可用现金（buy 时校验）
        position_qty: 当前持仓股数（sell 时校验）

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
