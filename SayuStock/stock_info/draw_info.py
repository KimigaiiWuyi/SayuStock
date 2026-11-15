import io
import base64
import asyncio
from pathlib import Path
from datetime import datetime
from tempfile import NamedTemporaryFile

import numpy as np
from PIL import Image, ImageOps

from gsuid_core.logger import logger
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..render import RenderError, render_html
from ..utils.utils import number_to_chinese
from ..utils.market import (
    BREADTH_BANDS,
    Quote,
    BreadthBar,
    DisplayItem,
    get_market,
    breadth_counts,
    breadth_up_down,
    is_market_error,
    board_rows_to_items,
)
from ..utils.sparkline import sparkline_from_series
from ..render.card_widgets import tone, mood_number
from ..utils.market.models import BoardSnapshot, BreadthBucket, MarketTurnover
from ..utils.market.display import source_label
from ..utils.stock.request_utils import get_image_from_em
from ..utils.market_overview_html import CSS_WIDTH, build_market_overview_html

PREVIEW_FILE = Path(__file__).resolve().parents[2] / "preview_output" / "market_overview.png"

# 主要指数按代码报价。行情中心「涨跌幅前 100」里没有上证/沪深300。
_OVERVIEW_INDEXES: tuple[tuple[str, str], ...] = (
    ("上证指数", "1.000001"),
    ("中证全指", "1.000985"),
    ("创业板指", "0.399006"),
    ("科创综指", "1.000680"),
    ("沪深300", "1.000300"),
    ("中证500", "1.000905"),
    ("中证1000", "1.000852"),
    ("中证2000", "2.932000"),
    ("中证A500", "1.000510"),
    ("北证50", "0.899050"),
)
_OVERVIEW_OPTIONAL: tuple[tuple[str, str], ...] = (
    ("黄金9999", "118.AU9999"),
    ("三十债主连", "220.TLM"),
)


def invert_colors(img: Image.Image) -> Image.Image:
    r, g, b, a = img.split()

    rgb = Image.merge("RGB", (r, g, b))
    inverted_rgb = ImageOps.invert(rgb)

    inverted_img = Image.composite(
        Image.merge("RGBA", (*inverted_rgb.split(), a)),
        img,
        a,
    )
    return inverted_img


def _flow_image_uri(image: Image.Image) -> str:
    pixels = np.array(image.convert("RGBA"))
    pixels[np.all(pixels[..., :3] >= 200, axis=-1), 3] = 0
    if not np.any(pixels[..., 3]):
        return ""
    image = invert_colors(Image.fromarray(pixels))
    with io.BytesIO() as buffer:
        image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _save_preview(path: Path, image: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file = NamedTemporaryFile(dir=path.parent, prefix=".overview-", delete=False)
    temporary = Path(file.name)
    try:
        with file:
            file.write(image)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _overview_item(label: str, quote: Quote) -> DisplayItem:
    change = float(quote.change_pct) if quote.change_pct is not None else 0.0
    return DisplayItem(
        name=label,
        price=float(quote.price),
        change_pct=change,
        amount=quote.amount,
        code=quote.symbol.code,
        provider=quote.provider,
    )


def _empty_breadth() -> BreadthBar:
    buckets = tuple(BreadthBucket(label=label, count=0) for label in BREADTH_BANDS)
    return BreadthBar(buckets=buckets)


def _ai_return_market_overview(
    data_zs: list[DisplayItem],
    data_hy_rise: list[DisplayItem],
    data_hy_fall: list[DisplayItem],
    up_value: int,
    down_value: int,
    diff_bar: dict[str, int],
) -> None:
    """从大盘概览语义数据中提取文本，经 ai_return 给 AI。"""
    try:
        result = "【A股大盘概览】\n【主要指数】\n"
        for item in data_zs[:12]:
            result += f"  {item.name}: {item.price} ({'+' if item.change_pct >= 0 else ''}{item.change_pct}%)\n"
        result += f"\n【涨跌分布】上涨 {up_value} 家  下跌 {down_value} 家\n"
        for label, count in diff_bar.items():
            if count > 0:
                result += f"  {label}: {count}\n"
        result += "\n【领涨行业板块】\n"
        for hy in data_hy_rise[:5]:
            result += (
                f"  {hy.name}: {'+' if hy.change_pct >= 0 else ''}{hy.change_pct}% (领涨: {hy.lead_name or 'N/A'})\n"
            )
        result += "\n【领跌行业板块】\n"
        for hy in data_hy_fall[:5]:
            leader = hy.fall_name or hy.lead_name or "N/A"
            result += f"  {hy.name}: {'+' if hy.change_pct >= 0 else ''}{hy.change_pct}% (领跌: {leader})\n"
        ai_return(result)
    except (TypeError, ValueError, KeyError) as e:
        logger.warning(f"[SayuStock] ai_return 大盘概览数据提取失败: {e}")


async def draw_info_img(is_save: bool = False) -> str | bytes:
    market = get_market()
    index_queries = [secid for _, secid in (*_OVERVIEW_INDEXES, *_OVERVIEW_OPTIONAL)]
    labels = [label for label, _ in (*_OVERVIEW_INDEXES, *_OVERVIEW_OPTIONAL)]
    optional_names = {label for label, _ in _OVERVIEW_OPTIONAL}

    # 并发拉取数据
    intraday_tasks = [market.intraday(secid) for secid in index_queries]
    intraday_coro = asyncio.gather(*intraday_tasks, return_exceptions=True)
    boards_coro = asyncio.gather(
        market.board("行业板块", limit=20, sort_asc=True),
        market.board("行业板块", limit=20, sort_asc=False),
        market.board("概念板块", limit=20, sort_asc=True),
        market.board("概念板块", limit=20, sort_asc=False),
        market.breadth(),
        market.market_turnover(),
    )
    img_coro = get_image_from_em(size=(500, 274))

    intraday_results, boards_results, raw_em_img = await asyncio.gather(
        intraday_coro,
        boards_coro,
        img_coro,
    )
    board_hy_fall, board_hy_rise, board_gn_fall, board_gn_rise, bar_r, turnover = boards_results

    # 1. 检验板块结果
    for result in (board_hy_fall, board_hy_rise, board_gn_fall, board_gn_rise):
        if is_market_error(result):
            return result.message
    if not isinstance(board_hy_fall, BoardSnapshot) or not isinstance(board_hy_rise, BoardSnapshot):
        return "行业板块数据异常"
    if not isinstance(board_gn_fall, BoardSnapshot) or not isinstance(board_gn_rise, BoardSnapshot):
        return "概念板块数据异常"

    data_hy_fall = board_rows_to_items(board_hy_fall.rows)
    data_hy_rise = board_rows_to_items(board_hy_rise.rows)
    data_gn_fall = board_rows_to_items(board_gn_fall.rows)
    data_gn_rise = board_rows_to_items(board_gn_rise.rows)

    # 2. 处理主要指数与分时折线图
    data_zs_items: list[DisplayItem] = []
    sparks: dict[str, str] = {}
    index_providers: list[str | None] = []
    index_errors: list[str] = []

    for label, secid, res in zip(labels, index_queries, intraday_results, strict=True):
        if not isinstance(res, BaseException) and not is_market_error(res) and res.points:
            q = res.quote
            price = float(q.price) if q else 0.0
            change = float(q.change_pct) if q and q.change_pct is not None else 0.0
            amount = q.amount if q else None
            code = q.symbol.code if q else secid
            prov = res.provider or (q.provider if q else None)
            item = DisplayItem(name=label, price=price, change_pct=change, amount=amount, code=code, provider=prov)
            data_zs_items.append(item)
            index_providers.append(prov)
            svg = sparkline_from_series(res, width=175.0, height=50.0)
            if svg:
                sparks[label] = svg
                sparks[code] = svg
        else:
            # 分时异常时降级尝试获取 quote 单报价
            q = await market.quote(secid)
            if isinstance(q, Quote):
                item = _overview_item(label, q)
                data_zs_items.append(item)
                index_providers.append(q.provider)
            elif is_market_error(q):
                if label in optional_names:
                    logger.warning(f"[SayuStock] 大盘概览{label}报价跳过: {q.message}")
                else:
                    index_errors.append(f"{label}: {q.message}")

    if not any(item.name not in optional_names for item in data_zs_items):
        detail = "；".join(index_errors) if index_errors else "无报价"
        return f"主要指数报价失败：{detail}"

    # 3. 涨跌分布
    breadth_provider: str | None = None
    breadth_available = isinstance(bar_r, BreadthBar) and bool(bar_r.buckets)
    if is_market_error(bar_r):
        logger.warning(f"[SayuStock] 大盘概览涨跌分布跳过: {bar_r.message}")
        bar_r = _empty_breadth()
    elif isinstance(bar_r, BreadthBar):
        breadth_provider = bar_r.provider
    else:
        bar_r = _empty_breadth()

    diff_bar: dict[str, int] = dict(breadth_counts(bar_r))
    up_value, down_value = breadth_up_down(diff_bar)

    # 4. 指数涨跌
    qz_diff = 0.0
    for item in data_zs_items:
        if item.name == "中证全指":
            qz_diff = item.change_pct

    # 5. 触发 AI 文本（有图必有文字规范）
    _ai_return_market_overview(data_zs_items, data_hy_rise, data_hy_fall, up_value, down_value, diff_bar)

    # 6. 两市成交额
    turnover_provider: str | None = None
    trade_date = None
    turnover_delta = ""
    turnover_tone = "flat"
    if is_market_error(turnover):
        logger.warning(f"[SayuStock] 大盘概览成交额跳过: {turnover.message}")
        turnover_text = "暂缺"
    else:
        if not isinstance(turnover, MarketTurnover):
            return "两市成交额数据异常"
        turnover_provider = turnover.provider
        trade_date = turnover.last_trade_date
        turnover_text = number_to_chinese(turnover.amount)
        if turnover.prev_amount is not None:
            delta = turnover.amount - turnover.prev_amount
            turnover_tone = tone(delta)
            if delta != 0:
                turnover_delta = f"{'放量' if delta > 0 else '缩量'} {number_to_chinese(abs(delta))}"

    # 7. 主力资金图片处理（抠白底与反色）
    em_flow_uri = ""
    try:
        em_flow_uri = await asyncio.to_thread(_flow_image_uri, raw_em_img)
    except (OSError, ValueError) as e:
        logger.warning(f"[SayuStock] 主力净流入图片处理失败: {e}")

    # 9. 数据来源信息
    source_bits = [f"指数{source_label(*index_providers)}"]
    board_providers = (board_hy_fall.provider, board_hy_rise.provider, board_gn_fall.provider, board_gn_rise.provider)
    source_bits.append(f"板块{source_label(*board_providers)}")
    if breadth_provider:
        source_bits.append(f"涨跌{source_label(breadth_provider)}")
    if turnover_provider:
        source_bits.append(f"成交额{source_label(turnover_provider)}")
    if em_flow_uri:
        source_bits.append(f"资金{source_label('eastmoney')}")
    source_line = f"数据来源：{' '.join(source_bits)} | SayuStock"

    # 10. 构建 HTML
    html = build_market_overview_html(
        data_zs_items=data_zs_items,
        sparks=sparks,
        diff_bar=diff_bar,
        up_value=up_value,
        down_value=down_value,
        breadth_available=breadth_available,
        em_flow_uri=em_flow_uri,
        now=datetime.now(),
        trade_date=trade_date,
        turnover=turnover_text,
        turnover_delta=turnover_delta,
        turnover_tone=turnover_tone,
        data_hy_fall=data_hy_fall,
        data_hy_rise=data_hy_rise,
        data_gn_fall=data_gn_fall,
        data_gn_rise=data_gn_rise,
        source_line=source_line,
        title_num=mood_number(qz_diff),
    )

    try:
        img_bytes = await render_html(html, width=CSS_WIDTH, label="大盘概览")
    except RenderError as e:
        logger.warning(f"[SayuStock] 大盘概览出图失败: {e}")
        return "大盘概览出图失败"

    if is_save:
        try:
            await asyncio.to_thread(_save_preview, PREVIEW_FILE, img_bytes)
        except OSError as error:
            logger.warning(f"[SayuStock] 大盘概览保存失败: {error}")

    return img_bytes
