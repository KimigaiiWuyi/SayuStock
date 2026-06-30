"""AI 模拟盘策略（评分 + 决策树 + 风控矩阵）。

公开 API：
- :func:`score_stock`       单只股票评分（-1.0 ~ +1.0）
- :func:`decide_action`     决策树：基于 score + 持仓 + 模式 → 买/卖/持
- :func:`apply_risk_check`  风控检查（单日交易次数 / 回撤熔断等）
- :data:`MODE_RULES`         三种风控模式的规则矩阵
- :data:`MODE_THRESHOLDS`    各模式对应的 score 门槛
"""

from typing import Any, Dict, List, Optional
from dataclasses import field, dataclass

# ============================================================
# 风控矩阵
# ============================================================
MODE_RULES: Dict[str, Dict[str, float]] = {
    "balanced": {
        "max_pos_pct": 0.25,
        "max_daily_trades": 6,
        "stop_loss": -0.08,
        "max_drawdown": -0.20,
        "min_cash_pct": 0.05,
        "max_holdings": 8,
        "reentry_per_day": 1,
    },
    "aggressive": {
        "max_pos_pct": 0.40,
        "max_daily_trades": 12,
        "stop_loss": -0.12,
        "max_drawdown": -0.30,
        "min_cash_pct": 0.00,
        "max_holdings": 12,
        "reentry_per_day": 2,
    },
    "conservative": {
        "max_pos_pct": 0.15,
        "max_daily_trades": 3,
        "stop_loss": -0.05,
        "max_drawdown": -0.12,
        "min_cash_pct": 0.15,
        "max_holdings": 5,
        "reentry_per_day": 1,
    },
}

# 各模式对应的 score 门槛
MODE_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "balanced": {"strong_buy": 0.30, "try_buy": 0.10, "weak_signal": 0.05, "strong_sell": -0.30},
    "aggressive": {"strong_buy": 0.25, "try_buy": 0.08, "weak_signal": 0.05, "strong_sell": -0.30},
    "conservative": {"strong_buy": 0.35, "try_buy": 0.15, "weak_signal": 0.05, "strong_sell": -0.30},
}


# ============================================================
# 数据类
# ============================================================
@dataclass(slots=True)
class TechSignals:
    """技术指标信号"""

    macd_dif: Optional[float] = None
    macd_dea: Optional[float] = None
    macd_bar: Optional[float] = None
    macd_golden_cross_in_3d: bool = False
    macd_death_cross_in_3d: bool = False
    macd_bar_positive_and_dif_above_dea: bool = False
    macd_bar_negative_and_dif_below_dea: bool = False
    rsi6: Optional[float] = None
    rsi12: Optional[float] = None
    rsi24: Optional[float] = None
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    ma_bull_alignment: bool = False
    ma_bear_alignment: bool = False
    close_above_ma20: bool = False
    close_below_ma20: bool = False
    cmf20: Optional[float] = None
    volume_ratio: Optional[float] = None
    turnover_pct: Optional[float] = None
    bias: Optional[float] = None
    atr_pct: Optional[float] = None


@dataclass(slots=True)
class FundSignals:
    """基本面信号"""

    roe: Optional[float] = None
    revenue_yoy: Optional[float] = None
    profit_yoy: Optional[float] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None
    debt_ratio: Optional[float] = None
    pe_ttm: Optional[float] = None
    industry_pe_median: Optional[float] = None


@dataclass(slots=True)
class NewsSignals:
    """舆情信号"""

    positive_count: int = 0
    negative_count: int = 0
    has_forecast_up: bool = False
    has_reduction_or_negative: bool = False
    items: List[Any] = field(default_factory=list)


@dataclass(slots=True)
class StockContext:
    """单只股票的当前市场上下文"""

    code: str
    name: str
    current_price: float


@dataclass(slots=True)
class PositionContext:
    """当前持仓上下文"""

    qty: int = 0
    avg_cost: float = 0.0


@dataclass(slots=True)
class AccountContext:
    """账户上下文"""

    cash: float
    total_equity: float
    position_value: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    holdings_count: int = 0
    daily_trade_count: int = 0
    reentry_count_today: Dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class Decision:
    """决策结果"""

    action: str  # "buy" / "sell" / "hold"
    qty: int
    reason: str
    score: float = 0.0
    detail_reasons: List[str] = field(default_factory=list)
    blocked_by: str = ""


# ============================================================
# 1) 单只股票评分
# ============================================================
def score_stock(
    tech: TechSignals,
    fund: FundSignals,
    news: NewsSignals,
) -> tuple[float, List[str]]:
    """返回 (score, reasons)"""
    score = 0.0
    reasons: List[str] = []

    # ===== 技术面 40% =====
    if tech.macd_golden_cross_in_3d:
        score += 0.20
        reasons.append("MACD 金叉(3日内)")
    if tech.macd_death_cross_in_3d:
        score -= 0.20
        reasons.append("MACD 死叉(3日内)")
    if tech.macd_bar_positive_and_dif_above_dea:
        score += 0.05
        reasons.append("MACD 柱状转正")
    if tech.macd_bar_negative_and_dif_below_dea:
        score -= 0.05
        reasons.append("MACD 柱状转负")

    if tech.rsi6 is not None and 25 <= tech.rsi6 <= 35:
        score += 0.10
        reasons.append(f"RSI6={tech.rsi6:.0f} 超卖区")
    if tech.rsi6 is not None and 65 <= tech.rsi6 <= 75:
        score -= 0.10
        reasons.append(f"RSI6={tech.rsi6:.0f} 超买区")

    if tech.ma_bull_alignment:
        score += 0.10
        reasons.append("均线多头排列")
    if tech.ma_bear_alignment:
        score -= 0.10
        reasons.append("均线空头排列")
    if tech.close_above_ma20:
        score += 0.05
        reasons.append("站上 MA20")
    if tech.close_below_ma20:
        score -= 0.05
        reasons.append("跌破 MA20")

    if tech.cmf20 is not None and tech.cmf20 > 0.10:
        score += 0.10
        reasons.append(f"CMF20={tech.cmf20:.3f} 资金强流入")
    if tech.cmf20 is not None and tech.cmf20 < -0.10:
        score -= 0.10
        reasons.append(f"CMF20={tech.cmf20:.3f} 资金强流出")
    if tech.volume_ratio is not None and tech.volume_ratio > 2.0:
        score += 0.05
        reasons.append(f"量比={tech.volume_ratio:.1f} 放量")
    if tech.volume_ratio is not None and tech.volume_ratio < 0.5:
        score -= 0.05
        reasons.append(f"量比={tech.volume_ratio:.1f} 缩量")

    if tech.turnover_pct is not None and 0.5 <= tech.turnover_pct <= 5.0:
        score += 0.05
        reasons.append(f"换手率={tech.turnover_pct:.2f}% 健康")
    if tech.turnover_pct is not None and tech.turnover_pct > 15.0:
        score -= 0.05
        reasons.append(f"换手率={tech.turnover_pct:.2f}% 异常高")

    # ===== 基本面 30% =====
    if fund.roe is not None and fund.roe > 0.15:
        score += 0.10
        reasons.append(f"ROE={fund.roe:.1%}")
    if fund.roe is not None and fund.roe < 0.05:
        score -= 0.10
        reasons.append(f"ROE={fund.roe:.1%} 过低")
    if fund.revenue_yoy is not None and fund.revenue_yoy > 0.20:
        score += 0.08
        reasons.append(f"营收同比+{fund.revenue_yoy:.1%}")
    if fund.revenue_yoy is not None and fund.revenue_yoy < 0:
        score -= 0.08
        reasons.append(f"营收同比{fund.revenue_yoy:.1%}")
    if fund.profit_yoy is not None and fund.profit_yoy > 0.30:
        score += 0.07
        reasons.append(f"净利同比+{fund.profit_yoy:.1%}")
    if fund.profit_yoy is not None and fund.profit_yoy < -0.20:
        score -= 0.07
        reasons.append(f"净利同比{fund.profit_yoy:.1%}")
    if fund.gross_margin is not None and fund.gross_margin > 0.40:
        score += 0.05
        reasons.append(f"毛利率={fund.gross_margin:.1%}")
    if fund.debt_ratio is not None and fund.debt_ratio > 0.70:
        score -= 0.10
        reasons.append(f"负债率={fund.debt_ratio:.1%} 偏高")
    if fund.pe_ttm is not None and fund.industry_pe_median:
        if fund.pe_ttm < fund.industry_pe_median * 0.7:
            score += 0.10
            reasons.append(f"PE={fund.pe_ttm:.1f} < 行业中位×0.7 低估")
        if fund.pe_ttm > fund.industry_pe_median * 1.5:
            score -= 0.10
            reasons.append(f"PE={fund.pe_ttm:.1f} > 行业中位×1.5 高估")

    # ===== 舆情 15% =====
    if news.positive_count >= 3 and news.positive_count > news.negative_count * 2:
        score += 0.15
        reasons.append(f"近期正面新闻 {news.positive_count} 条")
    if news.negative_count >= 3 and news.negative_count > news.positive_count * 2:
        score -= 0.15
        reasons.append(f"近期负面新闻 {news.negative_count} 条")
    if news.has_forecast_up:
        score += 0.05
        reasons.append("业绩预增公告")
    if news.has_reduction_or_negative:
        score -= 0.05
        reasons.append("减持/利空公告")

    # ===== 波动率调整 ±15% =====
    if tech.atr_pct is not None and tech.atr_pct > 0.05:
        score *= 0.7
        reasons.append(f"日波幅={tech.atr_pct:.2%} 高波动降权×0.7")
    if tech.atr_pct is not None and tech.atr_pct < 0.015:
        score *= 1.1
        reasons.append(f"日波幅={tech.atr_pct:.2%} 低波动加全×1.1")

    score = max(-1.0, min(1.0, score))
    return score, reasons


# ============================================================
# 2) 决策树
# ============================================================
def decide_action(
    stock: StockContext,
    score: float,
    score_reasons: List[str],
    account: AccountContext,
    position: PositionContext,
    mode: str,
) -> Decision:
    """根据 score + 持仓 + 模式决定 buy/sell/hold。"""
    rules = MODE_RULES[mode]
    thresholds = MODE_THRESHOLDS[mode]
    pnl_pct = (
        (stock.current_price - position.avg_cost) / position.avg_cost
        if position.qty > 0 and position.avg_cost > 0
        else 0.0
    )

    # === 强制卖出（风控优先于一切） ===
    if position.qty > 0:
        if pnl_pct <= rules["stop_loss"]:
            return Decision(
                "sell",
                position.qty,
                f"⛔ 止损 (亏{pnl_pct:.1%} ≤ {rules['stop_loss']:.0%})",
                score=score,
                detail_reasons=score_reasons,
            )
        if account.total_pnl_pct <= rules["max_drawdown"]:
            return Decision(
                "sell",
                position.qty,
                f"⛔ 总回撤熔断 (累计{account.total_pnl_pct:.1%})",
                score=score,
                detail_reasons=score_reasons,
            )
        if pnl_pct >= 0.50:
            return Decision(
                "sell",
                position.qty,
                f"💰 止盈全平 (盈{pnl_pct:.1%})",
                score=score,
                detail_reasons=score_reasons,
            )
        if pnl_pct >= 0.30:
            return Decision(
                "sell",
                position.qty // 2,
                f"💰 止盈一半 (盈{pnl_pct:.1%})",
                score=score,
                detail_reasons=score_reasons,
            )

    # === 强卖信号 ===
    if score < thresholds["strong_sell"] and position.qty > 0:
        return Decision(
            "sell",
            position.qty,
            f"📉 强卖信号 (score={score:.2f})",
            score=score,
            detail_reasons=score_reasons,
        )

    # === 强买信号 ===
    if score > thresholds["strong_buy"]:
        target_value = account.total_equity * rules["max_pos_pct"]
        existing_value = position.qty * stock.current_price
        buy_value = max(0.0, target_value - existing_value)
        if buy_value < stock.current_price * 100:
            return Decision(
                "hold",
                0,
                "已接近目标仓位",
                score=score,
                detail_reasons=score_reasons,
            )
        available = account.cash - account.total_equity * rules["min_cash_pct"]
        buy_value = min(buy_value, available * 0.95)
        qty = int(buy_value / stock.current_price // 100 * 100) if stock.current_price > 0 else 0
        if qty < 100:
            return Decision(
                "hold",
                0,
                f"信号强但现金不够 (需{buy_value:.0f}, 可用{available:.0f})",
                score=score,
                detail_reasons=score_reasons,
            )
        return Decision(
            "buy",
            qty,
            f"📈 强买信号 (score={score:.2f})",
            score=score,
            detail_reasons=score_reasons,
        )

    # === 中等买入信号 ===
    if thresholds["try_buy"] < score <= thresholds["strong_buy"]:
        if position.qty == 0:
            target_value = account.total_equity * rules["max_pos_pct"] * 0.33
            available = account.cash - account.total_equity * rules["min_cash_pct"]
            buy_value = min(target_value, available * 0.95)
            qty = int(buy_value / stock.current_price // 100 * 100) if stock.current_price > 0 else 0
            if qty < 100:
                return Decision(
                    "hold",
                    0,
                    "信号中等但现金不够试探仓",
                    score=score,
                    detail_reasons=score_reasons,
                )
            return Decision(
                "buy",
                qty,
                f"🔍 试探仓 (score={score:.2f})",
                score=score,
                detail_reasons=score_reasons,
            )
        # 已持仓，加仓至 70% 目标
        target_value = account.total_equity * rules["max_pos_pct"] * 0.7
        existing_value = position.qty * stock.current_price
        if existing_value < target_value:
            buy_value = target_value - existing_value
            available = account.cash - account.total_equity * rules["min_cash_pct"]
            buy_value = min(buy_value, available * 0.95)
            qty = int(buy_value / stock.current_price // 100 * 100) if stock.current_price > 0 else 0
            if qty >= 100:
                return Decision(
                    "buy",
                    qty,
                    f"➕ 加仓 (score={score:.2f})",
                    score=score,
                    detail_reasons=score_reasons,
                )

    # === 弱信号 / 中性 / 弱空 → hold ===
    if thresholds["weak_signal"] < score <= thresholds["try_buy"]:
        return Decision(
            "hold",
            0,
            f"弱信号观望 (score={score:.2f})",
            score=score,
            detail_reasons=score_reasons,
        )
    if -thresholds["weak_signal"] <= score <= thresholds["weak_signal"]:
        return Decision(
            "hold",
            0,
            f"中性观望 (score={score:.2f})",
            score=score,
            detail_reasons=score_reasons,
        )
    if -thresholds["strong_sell"] <= score < -thresholds["weak_signal"]:
        if position.qty > 0 and pnl_pct < 0:
            return Decision(
                "hold",
                0,
                f"弱空但已亏，暂持 (score={score:.2f}, 亏{pnl_pct:.1%})",
                score=score,
                detail_reasons=score_reasons,
            )
        return Decision(
            "hold",
            0,
            f"弱空观望 (score={score:.2f})",
            score=score,
            detail_reasons=score_reasons,
        )

    return Decision(
        "hold",
        0,
        f"无明确行动 (score={score:.2f})",
        score=score,
        detail_reasons=score_reasons,
    )


# ============================================================
# 3) 风控检查（账户级别，决策前调用）
# ============================================================
def apply_risk_check(
    account: AccountContext,
    proposed: Decision,
    code: str,
    mode: str,
) -> Decision:
    """对提议的决策做账户级风控检查，返回可能调整后的决策。"""
    rules = MODE_RULES[mode]

    # 单日交易次数上限
    if proposed.action in ("buy", "sell") and account.daily_trade_count >= rules["max_daily_trades"]:
        return Decision(
            "hold",
            0,
            f"已达单日交易上限 ({account.daily_trade_count}/{rules['max_daily_trades']})",
            score=proposed.score,
            detail_reasons=proposed.detail_reasons,
            blocked_by="max_daily_trades",
        )

    # 最大持仓数（buy 时）
    if proposed.action == "buy" and account.holdings_count >= rules["max_holdings"]:
        return Decision(
            "hold",
            0,
            f"已达最大持仓数 ({account.holdings_count}/{rules['max_holdings']})",
            score=proposed.score,
            detail_reasons=proposed.detail_reasons,
            blocked_by="max_holdings",
        )

    # 同只 24h 加仓频次
    if proposed.action == "buy" and account.reentry_count_today.get(code, 0) >= rules["reentry_per_day"]:
        return Decision(
            "hold",
            0,
            f"今日已加仓{account.reentry_count_today[code]}次 ≥ {rules['reentry_per_day']}",
            score=proposed.score,
            detail_reasons=proposed.detail_reasons,
            blocked_by="reentry_limit",
        )

    return proposed


# ============================================================
# 4) 便捷：从 indicators dict 构造 TechSignals
# ============================================================
def tech_from_indicators(ind: Dict[str, Any]) -> TechSignals:
    """从 indicators.compute_indicators() 的返回值构造 TechSignals"""
    dif = ind.get("macd_dif")
    dea = ind.get("macd_dea")
    bar = ind.get("macd_bar")
    return TechSignals(
        macd_dif=dif,
        macd_dea=dea,
        macd_bar=bar,
        macd_golden_cross_in_3d=ind.get("macd_golden_cross_in_3d", False),
        macd_death_cross_in_3d=ind.get("macd_death_cross_in_3d", False),
        macd_bar_positive_and_dif_above_dea=(
            bar is not None and dif is not None and dea is not None and bar > 0 and dif > dea
        ),
        macd_bar_negative_and_dif_below_dea=(
            bar is not None and dif is not None and dea is not None and bar < 0 and dif < dea
        ),
        rsi6=ind.get("rsi6"),
        rsi12=ind.get("rsi12"),
        rsi24=ind.get("rsi24"),
        ma5=ind.get("ma5"),
        ma10=ind.get("ma10"),
        ma20=ind.get("ma20"),
        ma60=ind.get("ma60"),
        ma_bull_alignment=ind.get("ma_bull_alignment", False),
        ma_bear_alignment=ind.get("ma_bear_alignment", False),
        close_above_ma20=ind.get("close_above_ma20", False),
        close_below_ma20=ind.get("close_below_ma20", False),
        cmf20=ind.get("cmf20"),
        volume_ratio=ind.get("volume_ratio"),
        turnover_pct=ind.get("turnover_pct"),
        bias=ind.get("bias"),
        atr_pct=ind.get("atr_pct"),
    )
