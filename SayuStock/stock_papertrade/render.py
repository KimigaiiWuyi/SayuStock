"""AI 模拟盘 PIL 渲染（账户视图 / 排行）。"""

from PIL import Image, ImageDraw, ImageFont

from gsuid_core.utils.fonts.fonts import core_font as ss_font
from gsuid_core.utils.image.convert import convert_img

from . import db
from ..utils.image import get_footer


# ============================================================
# 工具
# ============================================================
def _font(size: int = 22) -> ImageFont.FreeTypeFont:
    try:
        return ss_font(size)
    except Exception:
        return ImageFont.load_default()


def _new_canvas(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), (24, 24, 30))
    return img


def _draw_text(
    img: Image.Image,
    xy: tuple,
    text: str,
    color=(240, 240, 240),
    size: int = 22,
    anchor: str = "lt",
):
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
# 工具
# ============================================================
def _font(size: int = 22) -> ImageFont.FreeTypeFont:
    try:
        return ss_font(size)
    except Exception:
        return ImageFont.load_default()


def _hex(c: tuple) -> str:
    return "#{:02x}{:02x}{:02x}".format(*c)


def _new_canvas(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), (24, 24, 30))
    return img


def _draw_text(
    img: Image.Image,
    xy: tuple,
    text: str,
    color=(240, 240, 240),
    size: int = 22,
    anchor: str = "lt",
):
    draw = ImageDraw.Draw(img)
    draw.text(xy, text, fill=color, font=_font(size), anchor=anchor)


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
    snap = await db.PaperSnapshotRepo.latest(group_id, bot_id)

    W, H = 900, 1200
    img = _new_canvas(W, H)
    draw = ImageDraw.Draw(img)

    # 标题
    _draw_text(img, (40, 30), "【早柚 AI 模拟盘 · 账户视图】", color=(255, 200, 100), size=30)
    _draw_text(img, (40, 80), f"群 {group_id}  ·  {bot_id}", color=(180, 180, 180), size=18)

    if not acc:
        _draw_text(img, (40, 140), "❌ 该群尚未开户，发送「AI操盘初始化」开户", color=(255, 100, 100), size=22)
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
                f"{p.stock_name or p.stock_code} ({p.stock_code})  ×{p.qty}股  均价 {p.avg_cost:.2f}  市值 {value:,.0f}",
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
# 2) 排行
# ============================================================
async def draw_leaderboard() -> bytes:
    snaps = await db.PaperSnapshotRepo.list_latest_all_groups(limit=20)

    W, H = 900, 100 + 60 * (len(snaps) + 1)
    img = _new_canvas(W, H)
    draw = ImageDraw.Draw(img)

    _draw_text(img, (40, 30), "【早柚 AI 模拟盘 · 跨群收益排行 TOP 20】", color=(255, 200, 100), size=28)
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
