"""Synthetic fixtures for the upstream-only rendering pages."""

from __future__ import annotations

from datetime import date

import pandas as pd

from SayuStock.utils.market import IpoEvent, IpoMarket
from SayuStock.utils.render_data import IpoCalendarRenderData, build_ipo_calendar_render_data
from SayuStock.stock_analysis.card import TradeCardData
from SayuStock.stock_analysis.screener import ScreenerResult
from SayuStock.stock_analysis.portfolio import HoldingRow, PortfolioRiskReport
from SayuStock.stock_analysis.technical import TechnicalReport
from SayuStock.utils.database.papertrade_models import (
    SayuPaperTrade,
    SayuPaperAccount,
    SayuPaperPosition,
    SayuPaperSnapshot,
)

ANCHOR = date(2026, 9, 22)


def technical_report() -> TechnicalReport:
    return TechnicalReport(
        name="示例科技",
        code="600001",
        period_code="101",
        period_label="日K",
        last_close=82.36,
        score=72,
        trend="多头排列",
        momentum="偏强",
        volume="放量",
        position="区间上沿",
        signals=[
            "均线呈多头排列，收盘站上 MA20",
            "MACD 柱由负转正",
            "RSI6 高于 RSI12",
            "股价维持在 BOLL 中轨上方",
            "短期量能有所放大",
            "上方临近前高",
        ],
        risk_flags=["距压力位较近", "连续上涨后短线振幅扩大", "波动偏高", "量比回落", "参考位不等于实际止损"],
        levels={
            "support": 78.12,
            "resistance": 86.8,
            "ma20": 79.67,
            "ma60": None,
            "stop_ref": 75.2,
            "target_ref": 88.0,
        },
        summary="趋势与动量同步转强，但当前位置临近前高。观察回踩支撑时量能是否收敛，再评估走势能否延续。",
        source_ids=("tencent",),
    )


def trade_card(*, partial: bool = False) -> TradeCardData:
    return TradeCardData(
        name="示例科技",
        code="600001",
        secid="1.600001",
        price=82.36,
        pct=None if partial else 1.25,
        open_price=80.12,
        prev_close=81.34,
        high=83.02,
        low=80.02,
        amount=2.35e8,
        turnover=2.17,
        industry="半导体",
        industry_pct=0.83,
        technical=None if partial else technical_report(),
        finance={} if partial else {"roe": 12.85, "revenue_yoy": 18.2, "eps": 1.26, "report_date": "2026-06-30"},
        pe=36.7,
        pb=4.2,
        mv=238.72e8,
        source_ids=("tencent", "eastmoney"),
    )


def screener_result(*, empty: bool = False) -> ScreenerResult:
    data = pd.DataFrame(
        [
            {
                "code": f"{600001 + i:06d}",
                "name": name,
                "price": 12.24 + i * 3.25,
                "pct": pct,
                "mv_yi": 150.16 + i * 19.25,
                "pe": 12.34 + i if i != 4 else None,
                "turnover": 1.63 + i * 0.25,
                "vol_ratio": 1.31 + i * 0.09,
                "industry": industry,
            }
            for i, (name, pct, industry) in enumerate(
                (
                    ("示例半导体", 2.46, "半导体"),
                    ("示例精密制造", 1.95, "电子元件"),
                    ("示例光学科技", 0.0, "光学光电子"),
                    ("示例算力", -0.42, "计算机设备"),
                    ("示例新材料", None, "半导体"),
                    ("示例软件", 1.16, "软件开发"),
                    ("示例有色", 0.82, "有色金属"),
                    ("示例工业", -0.78, "专用设备"),
                )
            )
        ]
    )
    return ScreenerResult(
        query="市值100-300 换手>1",
        scope="沪深A股",
        total_pool=2000,
        matched=0 if empty else 42,
        shown=0 if empty else len(data),
        df=data.iloc[:0] if empty else data,
        filters_desc=["市值 100–300 亿", "换手率 > 1%"],
        source_ids=("eastmoney",),
    )


def portfolio_report() -> PortfolioRiskReport:
    holdings = [
        HoldingRow(f"{600001 + i:06d}", f"示例标的{i + 1}", name, 0.125, 1.2, 12.5)
        for i, name in enumerate(
            ("半导体", "半导体", "半导体", "半导体", "软件开发", "软件开发", "电子元件", "食品饮料")
        )
    ]
    return PortfolioRiskReport(
        holdings=holdings,
        industry_weights={"半导体": 0.5, "软件开发": 0.25, "电子元件": 0.125, "食品饮料": 0.125},
        industry_pct={},
        hhi=0.34375,
        top1_name="半导体",
        top1_weight=0.5,
        top3_weight=0.875,
        effective_n=2.91,
        risk_level="集中",
        messages=[
            "持仓 8 只（等权），覆盖 4 个行业标签",
            "最大行业：半导体 50.0%；Top3 累计 87.5%",
            "注意：行业过度集中于「半导体」，同涨同跌风险偏高",
        ],
        source_ids=("tencent", "eastmoney"),
    )


def ipo_events() -> list[IpoEvent]:
    return [
        IpoEvent(
            market=IpoMarket.CN,
            code="301686",
            name="示例新材料",
            apply_date=date(2026, 9, 22),
            ballot_date=date(2026, 9, 23),
            pay_date=date(2026, 9, 24),
            listing_date=date(2026, 9, 28),
            board="创业板",
            issue_price=15.72,
        ),
        IpoEvent(
            market=IpoMarket.CN,
            code="688001",
            name="示例集成电路",
            apply_date=date(2026, 9, 16),
            ballot_date=date(2026, 9, 17),
            pay_date=date(2026, 9, 17),
            listing_date=date(2026, 9, 21),
            board="科创板",
            issue_price=42.8,
            first_day_change=31.15,
        ),
        IpoEvent(
            market=IpoMarket.HK,
            code="03228",
            name="示例電子科技",
            apply_end_date=date(2026, 9, 24),
            grey_market_date=date(2026, 9, 28),
            listing_date=date(2026, 9, 29),
            board="港交所主板",
            issue_price_text="39–44",
            currency="HKD",
        ),
        IpoEvent(
            market=IpoMarket.HK,
            code="06727",
            name="示例網絡服務",
            listing_date=date(2026, 9, 25),
            board="港交所主板",
            issue_price=49.0,
            currency="HKD",
            oversubscription=72.9,
        ),
        IpoEvent(
            market=IpoMarket.US,
            code="EXCO",
            name="Example Capital Opportunities Inc.",
            filed_date=date(2026, 9, 23),
            board="纳斯达克",
            currency="USD",
        ),
        IpoEvent(
            market=IpoMarket.US,
            code="EXTC",
            name="Example Technology and Manufacturing Corp.",
            listing_date=date(2026, 9, 25),
            board="纽交所",
            issue_price_text="12–14",
            raise_yi=0.76,
            currency="USD",
        ),
    ]


def ipo_data() -> IpoCalendarRenderData:
    return build_ipo_calendar_render_data(ipo_events(), anchor=ANCHOR)


def paper_account() -> SayuPaperAccount:
    return SayuPaperAccount(
        id=7,
        name="示例多因子盘",
        strategy_id="multi_factor",
        group_id="group_1",
        bot_id="test_bot",
        cash=638_075.0,
        initial_cash=1_000_000.0,
    )


def paper_positions() -> list[SayuPaperPosition]:
    return [
        SayuPaperPosition(
            account_id=7,
            stock_code=code,
            stock_name=name,
            qty=qty,
            avg_cost=cost,
            last_quote_price=last,
            plan_stop_price=cost * 0.95,
            plan_stop_pct=-0.05,
            plan_take_price=cost * 1.1,
            plan_thesis="示例入场计划",
        )
        for code, name, qty, cost, last in (
            ("600001", "示例科技", 2000, 82.25, 84.36),
            ("000002", "示例电气", 1800, 61.83, 58.04),
            ("512880", "示例证券ETF", 85_000, 0.987, None),
        )
    ]


def paper_trades() -> list[SayuPaperTrade]:
    return [
        SayuPaperTrade(
            account_id=7,
            stock_code=f"{600001 + i:06d}",
            stock_name=f"示例交易{i + 1}",
            side="buy" if i % 2 == 0 else "sell",
            qty=200 * (i + 1),
            price=31.23 + i,
            amount=(31.23 + i) * 200 * (i + 1),
            fee=5.0 + i,
        )
        for i in range(6)
    ]


def paper_snapshots() -> list[SayuPaperSnapshot]:
    return [
        SayuPaperSnapshot(
            account_id=i,
            trade_date=ANCHOR,
            cash=100_000.0,
            position_value=900_000.0 + pnl,
            total_equity=1_000_000.0 + pnl,
            total_pnl=pnl,
            total_pnl_pct=pnl / 10_000,
        )
        for i in range(1, 22)
        for pnl in [48_520.0 - i * 3980]
    ]
