"""模拟盘账户、持仓简图和收益排行的 Takumi 出图入口。"""

from __future__ import annotations

import asyncio
from math import isfinite
from datetime import datetime
from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from gsuid_core.logger import logger
from gsuid_core.ai_core.trigger_bridge import ai_return

from . import db
from ..render import RenderError, render_html
from ..utils.market import DisplayItem, get_market, is_market_error
from ..utils.warn_box import Failure
from ..utils.sparkline import sparkline_from_series
from ..render.extras_ui import Tone, Metric, direction
from ..render.templates import render_template
from ..utils.index_quotes import quote_index_items
from ..utils.market.display import market_error_text
from ..utils.paper_holdings_html import (
    SPARK_H,
    SPARK_W,
    PAPER_HOLDINGS_WIDTH,
    PriceBasis,
    HoldingBarRow,
    format_holding_price,
    build_paper_holdings_html,
)
from ..utils.database.papertrade_models import (
    SayuPaperTrade,
    SayuPaperAccount,
    SayuPaperPosition,
    SayuPaperSnapshot,
)

PAPER_WIDTH = 900


@dataclass(frozen=True, slots=True)
class _AccountPosition:
    name: str
    code: str
    quantity: str
    price: str
    value: str


@dataclass(frozen=True, slots=True)
class _AccountTrade:
    name: str
    side: str
    tone: Tone
    quantity: str
    price: str
    fee: str


@dataclass(frozen=True, slots=True)
class _AccountView:
    name: str
    subtitle: str
    enabled: bool
    equity: str
    pnl: str
    pnl_pct: str
    pnl_tone: Tone
    metrics: tuple[Metric, ...]
    positions: tuple[_AccountPosition, ...]
    trades: tuple[_AccountTrade, ...]


@dataclass(frozen=True, slots=True)
class _RankRow:
    name: str
    trade_date: str
    equity: str
    pnl: str
    pnl_tone: Tone
    return_pct: str
    return_tone: Tone


def _account_trade(trade: SayuPaperTrade) -> _AccountTrade:
    buy = trade.side == "buy"
    return _AccountTrade(
        name=trade.stock_name or trade.stock_code,
        side="买入" if buy else "卖出",
        tone="up" if buy else "down",
        quantity=f"{trade.qty:,}",
        price=f"{trade.price:.2f}",
        fee=f"{trade.fee:.2f}",
    )


def _account_view(
    account: SayuPaperAccount, positions: Sequence[SayuPaperPosition], recent_trades: Sequence[SayuPaperTrade]
) -> _AccountView:
    value = sum(position.qty * position.avg_cost for position in positions)
    equity = account.cash + value
    pnl = equity - account.initial_cash
    pnl_pct = pnl / account.initial_cash if account.initial_cash else 0.0
    share = value / equity if equity else 0.0
    return _AccountView(
        name=account.name,
        subtitle=f"策略 {account.strategy_id} · 风控 {account.mode} · 心跳每 {account.frequency_minutes} 分钟",
        enabled=bool(account.enabled),
        equity=f"{equity:,.0f}",
        pnl=f"{pnl:+,.0f}",
        pnl_pct=f"{pnl_pct:+.2%}",
        pnl_tone=direction(pnl),
        metrics=(
            Metric("期初资金", f"{account.initial_cash:,.0f}"),
            Metric("当前现金", f"{account.cash:,.0f}"),
            Metric("持仓成本", f"{value:,.0f}", note=f"仓位 {share:.1%}"),
        ),
        positions=tuple(
            _AccountPosition(
                position.stock_name or position.stock_code,
                position.stock_code,
                f"{position.qty:,}",
                f"{position.avg_cost:.2f}",
                f"{position.qty * position.avg_cost:,.0f}",
            )
            for position in positions
        ),
        trades=tuple(_account_trade(trade) for trade in recent_trades[:5]),
    )


def _account_html(view: _AccountView) -> str:
    return render_template(
        "paper_account.html",
        width=PAPER_WIDTH,
        account_name=view.name,
        subtitle=view.subtitle,
        enabled=view.enabled,
        equity=view.equity,
        pnl=view.pnl,
        pnl_pct=view.pnl_pct,
        pnl_tone=view.pnl_tone,
        metrics_values=view.metrics,
        positions=view.positions,
        trades=view.trades,
        footer="SayuStock · 模拟盘账户账本",
    )


def _ai_return_page(text: str, label: str) -> None:
    try:
        ai_return(text)
    except (TypeError, ValueError) as error:
        logger.warning(f"[SayuStock][{label}] ai_return 失败: {error}")


def _ai_return_account(view: _AccountView) -> None:
    lines = [
        f"【模拟盘账户】{view.name} · {'运行中' if view.enabled else '已停用'}",
        view.subtitle,
        f"总资产 {view.equity}（持仓按成本计） 累计盈亏 {view.pnl}（{view.pnl_pct}）",
        " · ".join(f"{item.label} {item.value} {item.note}".strip() for item in view.metrics),
        f"当前持仓 {len(view.positions)} 只",
    ]
    lines.extend(
        f"{row.name}({row.code}) {row.quantity}股 均价 {row.price} 持仓成本 {row.value}" for row in view.positions
    )
    lines.append(f"最近交易 {len(view.trades)} 笔")
    lines.extend(f"{row.side} {row.name} {row.quantity}股 @ {row.price} 费 {row.fee}" for row in view.trades)
    _ai_return_page("\n".join(lines), "模拟盘查看")


async def _render_page(html: str, *, width: int, label: str) -> bytes | str:
    try:
        return await render_html(html, width=width, label=label)
    except RenderError as error:
        logger.exception(f"[SayuStock][{label}] 出图失败: {error}")
        return f"{label}出图失败"


async def draw_account_view(account_id: int) -> bytes | str:
    acc = await db.PaperAccountRepo.get_by_id(account_id)
    if not acc:
        return "❌ 该模拟盘不存在，发送「模拟盘列表」查看现有的盘"
    positions = await db.PaperPositionRepo.list_by_account(account_id)
    recent_trades = await db.PaperTradeRepo.list_by_account(account_id, limit=5)
    view = _account_view(acc, positions, recent_trades)
    _ai_return_account(view)
    return await _render_page(_account_html(view), width=PAPER_WIDTH, label="模拟盘账户视图")


async def _load_index_items() -> list[DisplayItem]:
    return await quote_index_items(["上证指数", "深证成指", "中证A500", "中证2000"])


def _fallback_price(position: SayuPaperPosition) -> tuple[float, PriceBasis]:
    if position.last_quote_price is not None:
        return float(position.last_quote_price), "上次报价"
    return float(position.avg_cost or 0.0), "成本估值"


async def _fetch_holding_row(position: SayuPaperPosition) -> tuple[HoldingBarRow, str, float | None]:
    price, price_basis = _fallback_price(position)
    day_chg: float | None = None
    spark = ""
    live: float | None = None
    source_id: str | None = None
    spark_provider: str | None = None
    quote_error = ""
    query = position.secid or position.stock_code
    if query:
        series = await get_market().intraday(query)
        if not is_market_error(series):
            quote = series.quote
            if quote is not None:
                if isfinite(quote.price) and quote.price > 0:
                    price = float(quote.price)
                    live = price
                    price_basis = "最新行情"
                if quote.change_pct is not None and isfinite(quote.change_pct):
                    day_chg = float(quote.change_pct)
                if live is not None or day_chg is not None:
                    source_id = quote.provider or series.provider
            spark = sparkline_from_series(series, width=SPARK_W, height=SPARK_H)
            if spark:
                spark_provider = series.provider
        else:
            quote_error = market_error_text(series)
    else:
        quote_error = "持仓缺少行情代码"
    if day_chg is None and not quote_error:
        quote_error = "数据源未返回今日涨跌幅"
    cost = float(position.avg_cost or 0.0)
    qty = int(position.qty)
    mv = price * qty
    unreal = (price - cost) * qty if cost else 0.0
    unreal_pct = (unreal / (cost * qty) * 100.0) if cost and qty else 0.0
    row = HoldingBarRow(
        code=position.stock_code,
        name=position.stock_name or position.stock_code,
        qty=qty,
        avg_cost=cost,
        current_price=round(price, 4),
        day_change_pct=day_chg,
        unrealized_pnl=round(unreal, 2),
        unrealized_pnl_pct=round(unreal_pct, 4),
        market_value=round(mv, 2),
        provider=source_id,
        quote_error=quote_error,
        price_basis=price_basis,
        spark_provider=spark_provider,
    )
    return row, spark, live


def _ai_return_holdings(
    account_name: str,
    cash: float,
    initial_cash: float,
    holdings: list[HoldingBarRow],
) -> None:
    pos_value = sum(h.market_value for h in holdings)
    equity = cash + pos_value
    unreal = sum(h.unrealized_pnl for h in holdings)
    unreal_pct = (unreal / pos_value * 100.0) if pos_value else 0.0
    pnl = equity - initial_cash
    pnl_pct = (pnl / initial_cash * 100.0) if initial_cash else 0.0
    lines = [
        f"【模拟盘自选】{account_name} 总资产 {equity:,.0f} 现金 {cash:,.0f} 持仓 {len(holdings)} 只",
        f"浮盈 {unreal:+.0f}({unreal_pct:+.2f}%) 累计 {pnl:+.0f}({pnl_pct:+.2f}%)",
    ]
    ranked = sorted(holdings, key=lambda h: h.unrealized_pnl_pct, reverse=True)
    for row in ranked:
        day = "未获取" if row.day_change_pct is None else f"{row.day_change_pct:+.2f}%"
        reason = f"（{row.quote_error}）" if row.quote_error else ""
        lines.append(
            f"  {row.name}({row.code}) {row.qty}股 成本 {format_holding_price(row.avg_cost)} "
            f"{row.price_basis} {format_holding_price(row.current_price)} "
            f"今{day}{reason} 持{row.unrealized_pnl_pct:+.2f}%"
        )
    _ai_return_page("\n".join(lines), "模拟盘自选")


async def build_holdings_snapshot_image(account_id: int) -> bytes | str:
    """按账户拉持仓 + 分时，返回图片 bytes 或业务错误文案。"""
    acc = await db.PaperAccountRepo.get_by_id(account_id)
    if not acc:
        from . import account_scope as _scope

        return _scope.not_opened_message()

    positions = await db.PaperPositionRepo.list_by_account(account_id)

    async def _all_rows() -> list[tuple[HoldingBarRow, str, float | None]]:
        if not positions:
            return []
        return list(await asyncio.gather(*[_fetch_holding_row(p) for p in positions]))

    index_items, fetched = await asyncio.gather(_load_index_items(), _all_rows())
    rows: list[HoldingBarRow] = []
    sparks: dict[str, str] = {}
    writes: list[dict[str, str | float | datetime]] = []
    now = datetime.now()
    for position, (row, spark, live) in zip(positions, fetched, strict=True):
        rows.append(row)
        if spark:
            sparks[row.code] = spark
            if position.secid:
                sparks[position.secid] = spark
        if live is not None:
            writes.append({"stock_code": position.stock_code, "price": live, "at": now})
    if writes:
        try:
            await db.PaperPositionRepo.bulk_set_quote(writes, account_id)
        except (OSError, RuntimeError, ValueError) as error:
            logger.debug(f"[SayuStock][PaperTrade] 持仓报价回写跳过: {error}")

    failures = [Failure(label=f"{h.name}({h.code})", reason=h.quote_error) for h in rows if h.quote_error]
    _ai_return_holdings(acc.name, float(acc.cash), float(acc.initial_cash), rows)
    html = build_paper_holdings_html(
        account_name=acc.name,
        strategy_id=acc.strategy_id,
        enabled=int(acc.enabled or 0) == 1,
        cash=float(acc.cash),
        initial_cash=float(acc.initial_cash),
        holdings=rows,
        index_items=index_items,
        sparklines=sparks,
        failures=failures,
        warn_title=f"{len(failures)}/{len(rows)} 只持仓今日行情获取失败",
    )
    return await _render_page(html, width=PAPER_HOLDINGS_WIDTH, label="模拟盘自选")


def _rank_rows(snapshots: Sequence[SayuPaperSnapshot], accounts: Mapping[int, str]) -> tuple[_RankRow, ...]:
    return tuple(
        _RankRow(
            name=accounts[snap.account_id] if snap.account_id in accounts else f"#{snap.account_id}",
            trade_date=snap.trade_date.isoformat(),
            equity=f"{snap.total_equity:,.0f}",
            pnl=f"{snap.total_pnl:+,.0f}",
            pnl_tone=direction(snap.total_pnl),
            return_pct=f"{snap.total_pnl_pct:+.2f}%",
            return_tone=direction(snap.total_pnl_pct),
        )
        for snap in snapshots[:20]
    )


async def draw_leaderboard() -> bytes | str:
    snaps = await db.PaperSnapshotRepo.list_latest_all_accounts(limit=20)
    accounts = {account.id: account.name for account in await db.PaperAccountRepo.list_all() if account.id is not None}
    rows = _rank_rows(snaps, accounts)
    lines = [
        f"{i}. {row.name}（{row.trade_date}） 总资产 {row.equity} 累计盈亏 {row.pnl} 收益率 {row.return_pct}"
        for i, row in enumerate(rows, 1)
    ]
    _ai_return_page("\n".join(["【模拟盘收益排行 TOP 20】", *(lines or ["暂无排行数据"])]), "模拟盘排行")
    html = render_template(
        "paper_leaderboard.html", width=PAPER_WIDTH, rows=rows, footer="SayuStock · 模拟盘每日净值快照"
    )
    return await _render_page(html, width=PAPER_WIDTH, label="模拟盘排行")
