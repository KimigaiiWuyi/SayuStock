"""模拟盘 PIL 渲染（账户视图 / 持仓简图 / 排行）。"""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from gsuid_core.utils.fonts.fonts import core_font as ss_font
from gsuid_core.utils.image.convert import convert_img

from . import db
from ..utils.image import get_footer
from ..utils.market import get_market, is_market_error, board_rows_to_items

# 与「我的自选」共用纹理（涨跌条 / 标题情绪图）
_MY_STOCK_TEX = Path(__file__).resolve().parent.parent / "stock_info" / "texture2d"
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
async def draw_account_view(
    group_id: str,
    bot_id: str,
) -> bytes:
    acc = await db.PaperAccountRepo.get(group_id, bot_id)
    positions = await db.PaperPositionRepo.list_by_account(group_id, bot_id)
    recent_trades = await db.PaperTradeRepo.list_by_account(group_id, bot_id, limit=5)

    W, H = 900, 1200
    img = _new_canvas(W, H)

    # 标题
    _draw_text(img, (40, 30), "【早柚 模拟盘 · 账户视图】", color=(255, 200, 100), size=30)
    _draw_text(img, (40, 80), f"群 {group_id}  ·  {bot_id}", color=(180, 180, 180), size=18)

    if not acc:
        _draw_text(img, (40, 140), "❌ 该群尚未开户，发送「模拟盘初始化」开户", color=(255, 100, 100), size=22)
        return await convert_img(img)

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
# 1b) 持仓简图（仿「我的自选」条卡 + 持仓浮盈）
# ============================================================
@dataclass(frozen=True)
class HoldingBarRow:
    """单只持仓条数据（简化快照用，不含流水）。"""

    code: str
    name: str
    qty: int
    avg_cost: float
    current_price: float
    day_change_pct: float | None
    unrealized_pnl: float
    unrealized_pnl_pct: float
    market_value: float


def _title_num_for_avg(avg_p: float) -> str:
    for thr, num in _DIFF_MAP.items():
        if avg_p >= thr:
            return num
    return "11"


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    """单行文本超出 max_width 时用省略号截断。"""
    if not text:
        return text
    bbox = draw.textbbox((0, 0), text, font=font)
    if bbox[2] - bbox[0] <= max_width:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid] + ell
        bb = draw.textbbox((0, 0), candidate, font=font)
        if bb[2] - bb[0] <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ell if lo else ell


def _draw_holding_bar(row: HoldingBarRow) -> Image.Image:
    """仿 draw_bar_from_quote：右侧今日涨跌，中间持仓收益率。"""
    day = row.day_change_pct
    if day is None:
        # 无日涨跌时用持仓盈亏着色
        paint = row.unrealized_pnl_pct
    else:
        paint = day

    if paint > 0:
        bar = Image.open(_MY_STOCK_TEX / "myup.png")
        sub_color = (213, 102, 102)
    elif paint == 0:
        bar = Image.open(_MY_STOCK_TEX / "myeq.png")
        sub_color = (240, 240, 240)
    else:
        bar = Image.open(_MY_STOCK_TEX / "mydown.png")
        sub_color = (175, 231, 170)

    b_title = (row.name or row.code).split(" (")[0]
    # 副标题可用宽度约到中间「持仓%」列（x≈520）
    s_title = (
        f"({row.code}) {row.qty}股  成本 {row.avg_cost:.2f}  现价 {row.current_price:.2f}  市值 {row.market_value:,.0f}"
    )
    if day is None:
        day_label = "今日 —"
    else:
        day_label = f"+{day:.2f}%" if day >= 0 else f"{day:.2f}%"
    hold_label = f"持{row.unrealized_pnl_pct:+.2f}%"

    draw = ImageDraw.Draw(bar)
    title_font = ss_font(32)
    sub_font = ss_font(18)
    draw.text((82, 40), _fit_text(draw, b_title, title_font, 420), (255, 255, 255), title_font, "lm")
    draw.text((82, 75), _fit_text(draw, s_title, sub_font, 430), sub_color, sub_font, "lm")
    # 中间：持仓收益（相对成本）；右侧：今日涨跌（相对昨收）
    draw.text((580, 55), hold_label, (240, 240, 240), ss_font(26), "mm")
    draw.text((758, 55), day_label, (255, 255, 255), ss_font(26), "mm")
    return bar


# 持仓简图垂直布局（对齐「我的自选」：bar5 高 90，条卡高 110）
_HS_INDEX_Y = 308
_HS_SUMMARY_Y = 448
_HS_SUMMARY_H = 100
_HS_BAR5_GAP = 8  # 摘要 → 「我的持仓」banner
_HS_BAR5_H = 90
_HS_ROW_H = 110
_HS_FOOTER_PAD = 60


def _holdings_layout(n_rows: int) -> tuple[int, int, int]:
    """返回 (canvas_h, bar5_y, rows_y0)。"""
    bar5_y = _HS_SUMMARY_Y + _HS_SUMMARY_H + _HS_BAR5_GAP
    rows_y0 = bar5_y + _HS_BAR5_H + 8
    body_h = rows_y0 + max(n_rows, 1) * _HS_ROW_H + _HS_FOOTER_PAD
    return body_h, bar5_y, rows_y0


async def draw_holdings_snapshot(
    *,
    group_id: str,
    bot_id: str,
    cash: float,
    initial_cash: float,
    holdings: list[HoldingBarRow],
) -> bytes:
    """简化版模拟盘持仓图（风格对齐「我的自选」，**不含**交易记录）。

    顶部：宽基指数 + 账户摘要（现金/总资产/浮盈）。
    列表：每只持仓条同时展示 **今日涨跌** 与 **持仓收益率**。
    """
    n = len(holdings)
    body_h, bar5_y, rows_y0 = _holdings_layout(n)
    img = Image.new("RGBA", (900, body_h), (7, 9, 27))

    # —— 宽基指数（与我的自选一致，失败则跳过）——
    market = get_market()
    zs_snap = await market.board("主要指数", limit=100, sort_asc=False)
    if not is_market_error(zs_snap):
        zs_items = board_rows_to_items(zs_snap.rows)
        zyzs = ["上证指数", "深证成指", "中证A500", "中证2000"]
        ni = 0
        for zs_name in zyzs:
            for item in zs_items:
                if zs_name != item.name.split("(")[0].strip() and zs_name not in item.name:
                    continue
                diff = float(item.change_pct)
                zs_img = Image.new("RGBA", (200, 140))
                zs_draw = ImageDraw.Draw(zs_img)
                if diff >= 0:
                    zsc, zsc2 = (140, 18, 22, 55), (206, 34, 30)
                else:
                    zsc, zsc2 = (59, 140, 18, 55), (36, 206, 30)
                zs_draw.rounded_rectangle((15, 13, 185, 127), 0, zsc)
                zs_draw.text((100, 99), zs_name, (255, 255, 255), ss_font(24), "mm")
                zs_draw.text((100, 38), f"{item.price}", zsc2, ss_font(30), "mm")
                zs_draw.text(
                    (100, 70),
                    f"{'+' if diff >= 0 else ''}{diff}%",
                    zsc2,
                    ss_font(30),
                    "mm",
                )
                img.paste(zs_img, (50 + 200 * ni, _HS_INDEX_Y), zs_img)
                ni += 1
                break

    # —— 账户摘要条（两行指标，避免单行撑破 850 宽）——
    pos_value = sum(h.market_value for h in holdings)
    total_equity = cash + pos_value
    total_unreal = sum(h.unrealized_pnl for h in holdings)
    total_unreal_pct = (total_unreal / pos_value * 100) if pos_value else 0.0
    total_pnl = total_equity - initial_cash
    total_pnl_pct = (total_pnl / initial_cash * 100) if initial_cash else 0.0
    day_vals = [h.day_change_pct for h in holdings if h.day_change_pct is not None]
    avg_day = sum(day_vals) / len(day_vals) if day_vals else 0.0

    summary = Image.new("RGBA", (850, _HS_SUMMARY_H), (20, 24, 48, 220))
    sd = ImageDraw.Draw(summary)
    sd.rounded_rectangle((0, 0, 849, _HS_SUMMARY_H - 1), 8, (20, 24, 48, 220))
    head_font = ss_font(22)
    line_font = ss_font(17)
    # 左右各留 20px
    max_text_w = 810
    head = _fit_text(sd, f"模拟盘持仓简图 · 群 {group_id}", head_font, max_text_w)
    line1 = _fit_text(
        sd,
        f"总资产 {total_equity:,.0f}  ·  现金 {cash:,.0f}  ·  持仓市值 {pos_value:,.0f}",
        line_font,
        max_text_w,
    )
    line2 = _fit_text(
        sd,
        f"浮盈 {total_unreal:+,.0f}({total_unreal_pct:+.2f}%)  ·  "
        f"累计 {total_pnl:+,.0f}({total_pnl_pct:+.2f}%)  ·  "
        f"持仓 {n} 只",
        line_font,
        max_text_w,
    )
    sd.text((20, 18), head, (255, 210, 120), head_font, "lm")
    sd.text((20, 50), line1, (220, 220, 230), line_font, "lm")
    sd.text((20, 76), line2, (200, 210, 220), line_font, "lm")
    img.paste(summary, (25, _HS_SUMMARY_Y), summary)

    # —— 标题情绪图（按持仓等权日均涨跌）——
    title_num = _title_num_for_avg(avg_day)
    title_path = _MY_STOCK_TEX / f"title{title_num}.png"
    if title_path.is_file():
        title = Image.open(title_path)
        img.paste(title, (25, -31), title)

    # —— 「我的持仓」banner：必须在摘要下方，持仓条再在其下 ——
    bar5_path = _MY_STOCK_TEX / "bar5.png"
    if bar5_path.is_file():
        bar5 = Image.open(bar5_path)
        img.paste(bar5, (25, bar5_y), bar5)

    # —— 持仓条 ——
    if not holdings:
        empty = Image.new("RGBA", (850, 90), (30, 30, 40, 200))
        ed = ImageDraw.Draw(empty)
        ed.text((425, 45), "（当前无持仓）", (160, 160, 170), ss_font(28), "mm")
        img.paste(empty, (25, rows_y0), empty)
    else:
        for i, row in enumerate(holdings):
            bar = _draw_holding_bar(row)
            img.paste(bar, (0, rows_y0 + i * _HS_ROW_H), bar)

    footer = get_footer()
    img.paste(footer, (25, img.size[1] - 55), footer)
    return await convert_img(img)


async def build_holdings_snapshot_image(group_id: str, bot_id: str) -> bytes | str:
    """按账户拉持仓 + 刷价，渲染「模拟盘自选」简图。

    Returns:
        图片 bytes；未开户等业务错误返回 str。
    """
    from .quote_service import quote_service

    acc = await db.PaperAccountRepo.get(group_id, bot_id)
    if not acc:
        from . import account_scope as _scope

        return _scope.not_opened_message(group_id, bot_id)

    positions = await db.PaperPositionRepo.list_by_account(group_id, bot_id)
    secids = [p.secid for p in positions if p.secid]
    details = await quote_service.get_details_batch(secids) if secids else {}

    # 顺带写回 DB 报价（与 ai_tools._get_enriched_positions 一致，失败忽略）
    import datetime as _dt

    now = _dt.datetime.now()
    writes: list[dict] = []
    for p in positions:
        if not p.secid:
            continue
        d = details.get(p.secid)
        if d is not None and d.price is not None and d.price > 0:
            writes.append({"stock_code": p.stock_code, "price": float(d.price), "at": now})
    if writes:
        try:
            await db.PaperPositionRepo.bulk_set_quote(writes, group_id, bot_id)
        except Exception:
            pass

    rows: list[HoldingBarRow] = []
    for p in positions:
        d = details.get(p.secid) if p.secid else None
        if d is not None and d.price is not None and d.price > 0:
            price = float(d.price)
        elif p.last_quote_price is not None:
            price = float(p.last_quote_price)
        else:
            price = float(p.avg_cost or 0.0)
        day_chg: float | None = None
        if d is not None and d.change_pct is not None:
            day_chg = float(d.change_pct)
        cost = float(p.avg_cost or 0.0)
        qty = int(p.qty)
        mv = price * qty
        unreal = (price - cost) * qty if cost else 0.0
        unreal_pct = (unreal / (cost * qty) * 100) if cost and qty else 0.0
        rows.append(
            HoldingBarRow(
                code=p.stock_code,
                name=p.stock_name or p.stock_code,
                qty=qty,
                avg_cost=cost,
                current_price=round(price, 4),
                day_change_pct=day_chg,
                unrealized_pnl=round(unreal, 2),
                unrealized_pnl_pct=round(unreal_pct, 4),
                market_value=round(mv, 2),
            )
        )

    return await draw_holdings_snapshot(
        group_id=group_id,
        bot_id=bot_id,
        cash=float(acc.cash),
        initial_cash=float(acc.initial_cash),
        holdings=rows,
    )


# ============================================================
# 2) 排行
# ============================================================
async def draw_leaderboard() -> bytes:
    snaps = await db.PaperSnapshotRepo.list_latest_all_groups(limit=20)

    W, H = 900, 100 + 60 * (len(snaps) + 1)
    img = _new_canvas(W, H)

    _draw_text(img, (40, 30), "【早柚 模拟盘 · 跨群收益排行 TOP 20】", color=(255, 200, 100), size=28)
    y = 90

    if not snaps:
        _draw_text(img, (60, y), "（暂无排行数据）", color=(150, 150, 150), size=20)
        img = _paste_footer(img)
        return await convert_img(img)

    # 表头
    _draw_text(img, (40, y), "排名", color=(180, 180, 180), size=18)
    _draw_text(img, (100, y), "群号", color=(180, 180, 180), size=18)
    _draw_text(img, (250, y), "总资产", color=(180, 180, 180), size=18)
    _draw_text(img, (400, y), "累计盈亏", color=(180, 180, 180), size=18)
    _draw_text(img, (600, y), "收益率", color=(180, 180, 180), size=18)
    y += 35

    for i, s in enumerate(snaps, 1):
        pnl_color = (100, 255, 120) if s.total_pnl >= 0 else (255, 120, 120)
        _draw_text(img, (40, y), f"#{i}", color=(220, 220, 220), size=20)
        _draw_text(img, (100, y), str(s.group_id)[:30], color=(220, 220, 220), size=20)
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
