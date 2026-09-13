"""模拟盘渲染：账户视图 / 排行走 PIL；持仓简图走 HTML。"""

from __future__ import annotations

import asyncio
import datetime as _dt

from PIL import Image, ImageDraw, ImageFont

from gsuid_core.logger import logger
from gsuid_core.utils.fonts.fonts import core_font as ss_font
from gsuid_core.utils.html_render import render_html_to_bytes
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from . import db
from ..utils.image import get_footer
from ..utils.market import DisplayItem, get_market, is_market_error, board_rows_to_items
from ..utils.sparkline import sparkline_from_series
from ..utils.paper_holdings_html import (
    SPARK_H,
    SPARK_W,
    HoldingBarRow,
    build_paper_holdings_html,
    paper_holdings_canvas_size,
)
from ..utils.database.papertrade_models import SayuPaperPosition

_DIFF_MAP: dict[float, str] = {
    3.3: "1",
    2.7: "2",
    2: "3",
    1: "4",
    0: "5",
    -0.5: "6",
    -1.3: "7",
    -2.1: "8",
    -3.1: "9",
    -4: "10",
}


# ============================================================
# 工具
# ============================================================
def _font(size: int = 22) -> ImageFont.FreeTypeFont:
    return ss_font(size)


def _new_canvas(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), (24, 24, 30))
    return img


def _draw_text(
    img: Image.Image,
    xy: tuple,
    text: str,
    color: str | tuple[int, ...] = (240, 240, 240),
    size: int = 22,
    anchor: str = "lt",
) -> None:
    draw = ImageDraw.Draw(img)
    draw.text(xy, text, fill=color, font=_font(size), anchor=anchor)


def _paste_footer(img: Image.Image) -> Image.Image:
    """把底部 footer 贴到 img 底部。返回新图。"""
    try:
        footer = get_footer()
    except Exception:
        return img
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    if footer.mode != "RGBA":
        footer = footer.convert("RGBA")
    new_h = img.size[1] + footer.size[1]
    new_img = Image.new("RGBA", (img.size[0], new_h), (24, 24, 30, 255))
    new_img.paste(img, (0, 0))
    new_img.paste(footer, (0, img.size[1]), footer)
    return new_img


# ============================================================
# 1) 账户视图
# ============================================================
async def draw_account_view(account_id: int) -> bytes:
    acc = await db.PaperAccountRepo.get_by_id(account_id)
    positions = await db.PaperPositionRepo.list_by_account(account_id)
    recent_trades = await db.PaperTradeRepo.list_by_account(account_id, limit=5)

    W, H = 900, 1200
    img = _new_canvas(W, H)

    # 标题
    _draw_text(img, (40, 30), "【早柚 模拟盘 · 账户视图】", color=(255, 200, 100), size=30)
    if not acc:
        _draw_text(img, (40, 140), "❌ 该模拟盘不存在，发送「模拟盘列表」查看现有的盘", color=(255, 100, 100), size=22)
        return await convert_img(img)
    _draw_text(
        img,
        (40, 80),
        f"{acc.name}  ·  策略 {acc.strategy_id}",
        color=(180, 180, 180),
        size=18,
    )

    y = 130
    # 账户信息
    _draw_text(img, (40, y), "═══ 账户信息 ═══", color=(100, 200, 255), size=22)
    y += 40
    info_lines = [
        f"初始资金: {acc.initial_cash:,.0f}",
        f"当前现金: {acc.cash:,.0f}",
        f"风控模式: {acc.mode}",
        f"心跳频率: {acc.frequency_minutes} 分钟",
        f"状态: {'🟢 开启' if acc.enabled else '🔴 关闭'}",
    ]
    for line in info_lines:
        _draw_text(img, (60, y), line, color=(220, 220, 220), size=20)
        y += 30
    y += 20

    # 持仓
    position_value = 0.0
    _draw_text(img, (40, y), "═══ 当前持仓 ═══", color=(100, 200, 255), size=22)
    y += 40
    if not positions:
        _draw_text(img, (60, y), "（暂无持仓）", color=(150, 150, 150), size=20)
        y += 30
    else:
        for p in positions:
            value = p.qty * p.avg_cost
            position_value += value
            _draw_text(
                img,
                (60, y),
                f"{p.stock_name or p.stock_code} ({p.stock_code})  "
                f"×{p.qty}股  均价 {p.avg_cost:.2f}  市值 {value:,.0f}",
                color=(200, 220, 255),
                size=20,
            )
            y += 30
    y += 20

    # 总资产
    total_equity = acc.cash + position_value
    total_pnl = total_equity - acc.initial_cash
    total_pnl_pct = total_pnl / acc.initial_cash * 100 if acc.initial_cash else 0
    pnl_color = (100, 255, 120) if total_pnl >= 0 else (255, 120, 120)
    _draw_text(
        img,
        (40, y),
        f"总资产: {total_equity:,.0f}  (现金 {acc.cash:,.0f} + 持仓 {position_value:,.0f})",
        color=(255, 220, 100),
        size=24,
    )
    y += 40
    _draw_text(
        img,
        (40, y),
        f"累计盈亏: {total_pnl:+,.0f}  ({total_pnl_pct:+.2f}%)",
        color=pnl_color,
        size=26,
    )
    y += 50

    # 最近交易
    _draw_text(img, (40, y), "═══ 最近 5 笔交易 ═══", color=(100, 200, 255), size=22)
    y += 40
    if not recent_trades:
        _draw_text(img, (60, y), "（暂无交易）", color=(150, 150, 150), size=20)
        y += 30
    else:
        for t in recent_trades:
            side_color = (255, 120, 120) if t.side == "buy" else (100, 255, 120)
            side_label = "买入" if t.side == "buy" else "卖出"
            _draw_text(
                img,
                (60, y),
                f"{side_label} {t.stock_name or t.stock_code}  {t.qty}股 @ {t.price:.2f}  费 {t.fee:.2f}",
                color=side_color,
                size=18,
            )
            y += 26

    img = _paste_footer(img)
    return await convert_img(img)


# ============================================================
# 1b) 持仓简图（HTML，对齐「我的自选」+ 仓位摘要 / 图例 / 分时）
# ============================================================
def _title_num_for_avg(avg_p: float) -> str:
    for thr, num in _DIFF_MAP.items():
        if avg_p >= thr:
            return num
    return "11"


async def _load_index_items() -> list[DisplayItem]:
    market = get_market()
    zs_snap = await market.board("主要指数", limit=100, sort_asc=False)
    if is_market_error(zs_snap):
        return []
    zs_items = board_rows_to_items(zs_snap.rows)
    wanted = ["上证指数", "深证成指", "中证A500", "中证2000"]
    out: list[DisplayItem] = []
    for zs_name in wanted:
        for item in zs_items:
            if zs_name != item.name.split("(")[0].strip() and zs_name not in item.name:
                continue
            out.append(item)
            break
    return out


def _fallback_price(position: SayuPaperPosition) -> float:
    if position.last_quote_price is not None:
        return float(position.last_quote_price)
    return float(position.avg_cost or 0.0)


async def _fetch_holding_row(position: SayuPaperPosition) -> tuple[HoldingBarRow, str, float | None]:
    price = _fallback_price(position)
    day_chg: float | None = None
    spark = ""
    live: float | None = None
    query = position.secid or position.stock_code
    if query:
        series = await get_market().intraday(query)
        if not is_market_error(series):
            quote = series.quote
            if quote is not None:
                if quote.price > 0:
                    price = float(quote.price)
                    live = price
                if quote.change_pct is not None:
                    day_chg = float(quote.change_pct)
            svg = sparkline_from_series(series, width=SPARK_W, height=SPARK_H)
            if svg:
                spark = svg
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
    )
    return row, spark, live


def _ai_return_holdings(
    account_name: str,
    cash: float,
    initial_cash: float,
    holdings: list[HoldingBarRow],
) -> None:
    """有图必有文字：持仓语义先于出图交给 AI。"""
    try:
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
            day = "—" if row.day_change_pct is None else f"{row.day_change_pct:+.2f}%"
            lines.append(
                f"  {row.name}({row.code}) {row.qty}股 成本 {row.avg_cost:.2f} "
                f"现价 {row.current_price:.2f} 今{day} 持{row.unrealized_pnl_pct:+.2f}%"
            )
        ai_return("\n".join(lines))
    except (TypeError, ValueError) as e:
        logger.warning(f"[SayuStock] ai_return 模拟盘自选失败: {e}")


async def build_holdings_snapshot_image(account_id: int) -> bytes | str:
    """按账户拉持仓 + 分时，渲染「模拟盘自选」HTML 简图。

    Returns:
        图片 bytes；盘不存在等业务错误返回 str。
    """
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
    writes: list[dict] = []
    now = _dt.datetime.now()
    for position, (row, spark, live) in zip(positions, fetched):
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
        except (OSError, RuntimeError, ValueError) as e:
            logger.debug(f"[SayuStock][PaperTrade] 持仓报价回写跳过: {e}")

    day_vals = [h.day_change_pct for h in rows if h.day_change_pct is not None]
    avg_day = sum(day_vals) / len(day_vals) if day_vals else 0.0
    _ai_return_holdings(acc.name, float(acc.cash), float(acc.initial_cash), rows)
    html = build_paper_holdings_html(
        account_name=acc.name,
        strategy_id=acc.strategy_id,
        enabled=int(acc.enabled or 0) == 1,
        cash=float(acc.cash),
        initial_cash=float(acc.initial_cash),
        holdings=rows,
        index_items=index_items,
        title_num=_title_num_for_avg(avg_day),
        sparklines=sparks,
    )
    width, height = paper_holdings_canvas_size(len(rows))
    try:
        return await render_html_to_bytes(
            html,
            max_width=float(width * 2),
            dpi=192.0,
            device_height=float(height * 2),
            default_font_size=15.0,
            allow_refit=False,
            image_format="png",
            lang="zh",
            root_max_width=float(width),
        )
    except (RuntimeError, OSError, ValueError) as e:
        logger.exception(f"[SayuStock] 模拟盘自选 HTML 出图失败: {e}")
        return "模拟盘自选出图失败"


# ============================================================
# 2) 排行
# ============================================================
async def draw_leaderboard() -> bytes:
    snaps = await db.PaperSnapshotRepo.list_latest_all_accounts(limit=20)
    # 快照里只有 account_id，盘名要另查；一次拉全表比逐行查库省往返
    accounts = {a.id: a for a in await db.PaperAccountRepo.list_all()}

    W, H = 900, 100 + 60 * (len(snaps) + 1)
    img = _new_canvas(W, H)

    _draw_text(img, (40, 30), "【早柚 模拟盘 · 各盘收益排行 TOP 20】", color=(255, 200, 100), size=28)
    y = 90

    if not snaps:
        _draw_text(img, (60, y), "（暂无排行数据）", color=(150, 150, 150), size=20)
        img = _paste_footer(img)
        return await convert_img(img)

    # 表头
    _draw_text(img, (40, y), "排名", color=(180, 180, 180), size=18)
    _draw_text(img, (100, y), "盘名", color=(180, 180, 180), size=18)
    _draw_text(img, (250, y), "总资产", color=(180, 180, 180), size=18)
    _draw_text(img, (400, y), "累计盈亏", color=(180, 180, 180), size=18)
    _draw_text(img, (600, y), "收益率", color=(180, 180, 180), size=18)
    y += 35

    for i, s in enumerate(snaps, 1):
        pnl_color = (100, 255, 120) if s.total_pnl >= 0 else (255, 120, 120)
        _draw_text(img, (40, y), f"#{i}", color=(220, 220, 220), size=20)
        acc_row = accounts.get(s.account_id)
        label = acc_row.name if acc_row is not None else f"#{s.account_id}"
        _draw_text(img, (100, y), label[:30], color=(220, 220, 220), size=20)
        _draw_text(img, (250, y), f"{s.total_equity:,.0f}", color=(220, 220, 220), size=20)
        _draw_text(img, (400, y), f"{s.total_pnl:+,.0f}", color=pnl_color, size=20)
        _draw_text(
            img,
            (600, y),
            f"{s.total_pnl_pct:+.2f}%",
            color=pnl_color,
            size=22,
        )
        y += 32

    img = _paste_footer(img)
    return await convert_img(img)
