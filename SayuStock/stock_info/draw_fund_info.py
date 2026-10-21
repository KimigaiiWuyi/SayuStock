import asyncio
from typing import Union

from gsuid_core.logger import logger
from gsuid_core.utils.html_render import render_html_to_bytes
from gsuid_core.ai_core.trigger_bridge import ai_return

from .draw_info import DIFF_MAP
from ..utils.market import MarketDataPort, get_market, is_market_error
from ..utils.warn_box import Failure
from ..utils.sparkline import sparkline_from_series
from ..utils.index_quotes import quote_index_items
from ..utils.market.display import market_error_text
from ..utils.fund_holdings_html import (
    SPARK_H,
    SPARK_W,
    FundHoldingRow,
    build_fund_holdings_html,
    fund_holdings_canvas_size,
)
from ..utils.stock.request_utils import get_code_id, get_fund_pos_list


def _weight_text(value: object) -> str:
    """持仓占比：东财给的是 "8.50" 这类文本，统一成两位小数的纯数字。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    text = str(value).strip()
    if text.endswith("%"):
        text = text[:-1].strip()
    return text


def _weight_sum(rows: list[FundHoldingRow]) -> float:
    total = 0.0
    for row in rows:
        try:
            total += float(row.weight)
        except ValueError:
            continue
    return total


def _share_label(row: dict[str, object]) -> str:
    code = str(row.get("ShareCode", "") or "").strip()
    name = str(row.get("ShareName", "") or "").strip()
    return f"{name}({code})" if name else code


async def _fetch_row(market: MarketDataPort, raw: dict[str, object]) -> tuple[FundHoldingRow, str, str]:
    """一只重仓股 → (行, 分时 SVG, 失败原因)。失败原因非空表示这条没拿全。"""
    share_code = str(raw.get("ShareCode", "") or "").strip()
    share_name = str(raw.get("ShareName", "") or "").strip()
    weight = _weight_text(raw.get("ShareProportion"))
    if not share_code:
        return (
            FundHoldingRow(
                code=share_code,
                name=share_name,
                weight=weight,
                failed=True,
                reason="基金持仓数据缺少股票代码",
            ),
            "",
            "基金持仓数据缺少股票代码",
        )

    series = await market.intraday(share_code)
    if not is_market_error(series):
        q = series.quote
        if q is None:
            return (
                FundHoldingRow(
                    code=share_code,
                    name=share_name,
                    weight=weight,
                    failed=True,
                    reason="数据源未返回行情",
                ),
                "",
                "数据源未返回行情",
            )
        svg = sparkline_from_series(series, width=SPARK_W, height=SPARK_H)
        return (
            FundHoldingRow(
                code=q.symbol.code or share_code,
                name=q.symbol.display_name or share_name or (q.symbol.name or ""),
                weight=weight,
                price=q.price,
                change_pct=q.change_pct,
                turnover_rate=q.turnover_rate,
                amount=q.amount,
                provider=q.provider or series.provider,
            ),
            svg,
            "",
        )

    # 分时拿不到（同花顺等源没有 intraday）→ 退回盘口报价，至少把价格和涨跌幅画出来
    reason = market_error_text(series)
    q = await market.quote(share_code)
    if not is_market_error(q):
        return (
            FundHoldingRow(
                code=q.symbol.code or share_code,
                name=q.symbol.display_name or share_name or (q.symbol.name or ""),
                weight=weight,
                price=q.price,
                change_pct=q.change_pct,
                turnover_rate=q.turnover_rate,
                amount=q.amount,
                provider=q.provider,
            ),
            "",
            f"{reason}（无分时）",
        )
    return (
        FundHoldingRow(
            code=share_code,
            name=share_name,
            weight=weight,
            failed=True,
            reason=reason,
        ),
        "",
        reason,
    )


async def _render_page(
    *,
    fund_name: str,
    fund_code: str,
    rows: list[FundHoldingRow],
    meta: str,
    title_num: str,
    sparks: dict[str, str] | None = None,
    sources: tuple[str | None, ...] = (),
) -> str | bytes:
    html = build_fund_holdings_html(
        fund_name=fund_name,
        fund_code=fund_code,
        rows=rows,
        index_items=await quote_index_items(["上证指数", "深证成指", "中证A500", "中证2000"]),
        title_num=title_num,
        sparklines=sparks,
        sources=sources,
        meta=meta,
    )
    width, height = fund_holdings_canvas_size(len(rows))
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
        logger.exception(f"[SayuStock] 基金持仓 HTML 出图失败: {e}")
        return "基金持仓出图失败"


async def draw_fund_info(fcode: Union[str, int]) -> str | bytes:
    _code = await get_code_id(str(fcode))
    if _code is None:
        return "基金代码有误，请检查后重新输入~"

    fund_secid = _code[0]
    fund_name = _code[1] or str(fcode)
    dcode = fund_secid.split(".")[1] if "." in fund_secid else fund_secid

    fund_data = await get_fund_pos_list(dcode)
    holdings_raw = fund_data.get("Datas") if fund_data is not None else None
    if not isinstance(holdings_raw, list) or not holdings_raw:
        return _fund_data_failure(fund_name, fund_secid, "东方财富基金持仓接口未返回数据")
    holdings: list[dict[str, object]] = [row for row in holdings_raw if isinstance(row, dict)]
    if not holdings:
        return _fund_data_failure(fund_name, fund_secid, "东方财富基金持仓接口未返回有效持仓")

    market = get_market()
    fetched = await _gather_rows(market, holdings)
    rows: list[FundHoldingRow] = []
    sparks: dict[str, str] = {}
    failures: list[Failure] = []
    sources: list[str | None] = []
    for row, svg, reason, raw in fetched:
        rows.append(row)
        if row.provider:
            sources.append(row.provider)
        if svg:
            sparks[row.code] = svg
            if raw.get("ShareCode"):
                sparks[str(raw["ShareCode"])] = svg
        if reason:
            failures.append(Failure(label=_share_label(raw), reason=reason))

    if failures:
        logger.warning(f"[SayuStock] 基金持仓 {fund_name} {len(failures)}/{len(holdings)} 只行情获取失败")

    ok = [row for row in rows if not row.failed]
    avg_p = 0.0
    for row in ok:
        avg_p += float(row.change_pct) if row.change_pct is not None else 0.0
    avg_p = avg_p / len(ok) if ok else 0.0
    title_num = "11"
    for i in DIFF_MAP:
        if avg_p >= i:
            title_num = DIFF_MAP[i]
            break

    meta = f"持仓 {len(rows)} 只 · 前十占比 {_weight_sum(rows):.2f}% · 平均 {avg_p:+.2f}%"
    _ai_return_fund_info(fund_name, fund_secid, rows, avg_p, failures)
    return await _render_page(
        fund_name=fund_name,
        fund_code=fund_secid,
        rows=rows,
        meta=meta,
        title_num=title_num,
        sparks=sparks,
        sources=tuple(sources),
    )


def _fund_data_failure(fund_name: str, fund_code: str, reason: str) -> str:
    """持仓接口挂了：没有持仓行可标「失败」，直接回一句明确的话（不是静默）。"""
    ai_return(f"【{fund_name}({fund_code}) 持仓信息】获取基金持仓数据失败：{reason}")
    return "获取基金持仓数据失败，请稍后再试~"


async def _gather_rows(
    market: MarketDataPort,
    holdings: list[dict[str, object]],
) -> list[tuple[FundHoldingRow, str, str, dict[str, object]]]:
    """并发取每只重仓股的行情；返回 (行, 分时, 失败原因, 原始持仓)。"""
    results = await asyncio.gather(*[_fetch_row(market, raw) for raw in holdings])
    return [(row, svg, reason, raw) for (row, svg, reason), raw in zip(results, holdings, strict=True)]


def _ai_return_fund_info(
    fund_name: str,
    fund_code: str,
    rows: list[FundHoldingRow],
    avg_p: float,
    failures: list[Failure] | None = None,
) -> None:
    """从基金持仓数据中提取文本信息，通过 ai_return 返回给 AI 分析"""
    try:
        result = f"【{fund_name}({fund_code}) 持仓信息】\n"
        result += f"持仓数量: {len(rows)} 只，平均涨跌幅: {avg_p:+.2f}%\n\n"
        if failures:
            detail = "、".join(f.label for f in failures)
            result += f"⚠ {len(failures)} 只行情获取失败：{detail}\n\n"
        result += "重仓股:\n"
        for row in rows[:10]:
            price = row.price if row.price is not None else "N/A"
            change = row.change_pct if row.change_pct is not None else "N/A"
            weight = row.weight if row.weight else "N/A"
            result += f"  {row.name}({row.code}): 占比 {weight}%  最新价 {price}  今日 {change}%\n"

        ai_return(result)
    except Exception as e:
        logger.warning(f"[SayuStock] ai_return 基金持仓数据提取失败: {e}")
