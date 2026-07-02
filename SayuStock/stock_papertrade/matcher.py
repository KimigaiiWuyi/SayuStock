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
# ST / *ST（风险警示）主板 → ±5%
LIMIT_THRESHOLD_MAIN = 10.0  # 主板 ±10%
LIMIT_THRESHOLD_STAR_CHINEXT = 20.0  # 科创 / 创业 ±20%
LIMIT_THRESHOLD_BSE = 30.0  # 北交所 ±30%
LIMIT_THRESHOLD_ST = 5.0  # ST / *ST 主板 ±5%

# 保险拦截系数：实际涨跌幅达到"本板涨停幅度 × 此系数"即视为触及涨跌停，
# 禁止 agent 买入（涨停）/ 卖出（跌停）。留 10% 缓冲是因为：
#   1. 低价股涨停价四舍五入后实际涨幅可能只有 +9.7%（不到名义 10%）；
#   2. 盘中封板前后有细微跳动。
# 于是：主板 9% / ST 4.5% / 科创创业 18% / 北交所 27% 就按涨跌停处理。
LIMIT_BLOCK_RATIO = 0.9


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


def _is_st(name: Optional[str]) -> bool:
    """判断是否为 ST / *ST / SST / S*ST（风险警示股，涨跌停 ±5%）。

    A 股风险警示标记恒为名称前缀（"ST" / "*ST" / "SST" / "S*ST" / "PT"），
    正常股票名不会以这些字母开头，故按前缀匹配不会误伤。
    """
    if not name:
        return False
    nm = name.upper().replace(" ", "").replace("　", "")
    return nm.startswith(("ST", "*ST", "SST", "S*ST", "PT"))


def _limit_threshold_for(code: str, name: Optional[str] = None) -> float:
    """按股票代码前缀 + ST 标记确定名义涨跌停阈值（%）。

    ST / *ST 主板 → ±5%
    688xxx / 689xxx 科创板、300xxx / 301xxx 创业板 → ±20%（含创业板 ST）
    830xxx ~ 87xxx / 920xxx 北交所 → ±30%
    其余主板 → ±10%
    """
    if not code or len(code) < 6:
        return LIMIT_THRESHOLD_MAIN
    if code.startswith(("688", "689")) or code[:3] in ("300", "301"):
        return LIMIT_THRESHOLD_STAR_CHINEXT
    if code.startswith(("83", "87", "920")):
        return LIMIT_THRESHOLD_BSE
    # 主板：ST 收窄到 ±5%
    if _is_st(name):
        return LIMIT_THRESHOLD_ST
    return LIMIT_THRESHOLD_MAIN


def _board_label(threshold: float, is_st: bool) -> str:
    if is_st:
        return "ST 主板"
    if threshold == LIMIT_THRESHOLD_STAR_CHINEXT:
        return "科创板/创业板"
    if threshold == LIMIT_THRESHOLD_BSE:
        return "北交所"
    return "主板"


def _is_at_limit(
    side: str,
    price: float,
    code: str,
    last_close: Optional[float],
    change_pct: Optional[float],
    name: Optional[str] = None,
) -> tuple[bool, str]:
    """检测是否触及（或逼近）涨跌停。

    为保险起见按"名义涨跌停 × LIMIT_BLOCK_RATIO"拦截：主板普通股 ±9%、ST ±4.5%、
    科创/创业 ±18%、北交所 ±27% 即视为涨跌停，禁止 buy（涨停）/ sell（跌停）。

    Args:
        side: "buy" / "sell"
        price: 当前成交价
        code: 股票代码（判板块）
        last_close: 昨收价（有则用它算实际涨跌幅，比 push2 f45 更可靠）
        change_pct: 涨跌幅（%，如 9.99）；last_close 缺失时降级用它
        name: 股票名称（判 ST）

    Returns:
        ``(blocked, reason)`` —— blocked=True 时 reason 描述具体板子
    """
    threshold = _limit_threshold_for(code, name)
    block_at = threshold * LIMIT_BLOCK_RATIO
    board = _board_label(threshold, _is_st(name) and threshold == LIMIT_THRESHOLD_ST)

    # 优先用昨收价直接算实际涨跌幅；拿不到就降级用 push2 的 change_pct
    if last_close is not None and last_close > 0:
        actual_chg = (price - last_close) / last_close * 100.0
        src = ""
    elif change_pct is not None:
        actual_chg = change_pct
        src = "（无昨收，用行情涨跌幅兜底）"
    else:
        return False, ""

    if side == "buy" and actual_chg >= block_at:
        return True, (
            f"涨停板买入拦截 ({board} 涨幅={actual_chg:.2f}% ≥ {block_at:.1f}%"
            f"（名义涨停 {threshold:.0f}%），接近或触及涨停){src}"
        )
    if side == "sell" and actual_chg <= -block_at:
        return True, (
            f"跌停板卖出拦截 ({board} 跌幅={actual_chg:.2f}% ≤ -{block_at:.1f}%"
            f"（名义跌停 {threshold:.0f}%），接近或触及跌停){src}"
        )
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
    name: Optional[str] = None,
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
        name: 股票名称；用于识别 ST / *ST（涨跌停 ±5%）

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
        blocked, block_reason = _is_at_limit(side, price, code, last_close, change_pct, name)
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
