"""新浪原始响应 → 领域模型（供应商字段仅本文件解析）。"""

from __future__ import annotations

from typing import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from datetime import date, datetime

from .._base import BJ_CODE_PREFIXES
from .client import PROVIDER
from ...enums import RankBy, BoardKind, KlinePeriod
from ...errors import MarketError, empty_error, parse_error, unsupported
from ...models import (
    BREADTH_BANDS,
    RANKING_CAVEAT,
    Bar,
    Quote,
    RankRow,
    BoardRow,
    SymbolRef,
    BreadthBar,
    BoardExtras,
    KlineSeries,
    RankSnapshot,
    BoardSnapshot,
    BreadthBucket,
    IntradayPoint,
    IntradaySeries,
    MarketTurnover,
)

try:  # 美股分钟时间戳为美东时间，转北京时间对齐东财口径；缺 tzdata 时退化为原样
    from zoneinfo import ZoneInfo

    _ET_TZ = ZoneInfo("America/New_York")
    _BJ_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001 - Windows 无 tzdata 包等场景
    _ET_TZ = None
    _BJ_TZ = None

# 东财 100.* 美股指数 → 新浪符号。注意东财 NDX 实为纳斯达克综合（查 IXIC/
# 纳斯达克综合指数 均指向 100.NDX），对应新浪 .ixic，而非纳指100 .ndx。
US_INDEX_HQ: dict[str, str] = {
    "SPX": "gb_inx",
    "DJIA": "gb_dji",
    "NDX": "gb_ixic",
}
US_INDEX_MINK: dict[str, str] = {
    "SPX": ".inx",
    "DJIA": ".dji",
    "NDX": ".ixic",
}


# 东财专有市场里，新浪只有盘口、且符号与 secid 不同名的品种。
# K 线接口不认这些符号。118 是沪金99；122.XAU 是伦敦金，两只不要混。
_SINA_QUOTE_ONLY: dict[str, str] = {
    "118.AU9999": "gds_AU9999",
    "220.TLM": "nf_TL0",
    "100.HSI": "rt_hkHSI",
    "100.N225": "b_NKY",
    "100.FTSE": "b_UKX",
    "100.FCHI": "b_CAC",
    "100.GDAXI": "b_DAX",
    "122.XAU": "hf_XAU",
    "122.XAG": "hf_XAG",
    "102.CL00Y": "hf_CL",
    "109.LCPT": "hf_CAD",
    "113.rbm": "nf_RB0",
    "114.mm": "nf_M0",
    "114.jmm": "nf_JM0",
    "114.lhm": "nf_LH0",
    "133.USDCNH": "fx_susdcnh",
    "119.USDCHF": "fx_susdchf",
    "119.USDJPY": "fx_susdjpy",
    "100.UDI": "DINIW",
}
# 商品连续：名称在首列。三十债 nf_TL0 不在这里。
_SINA_DOMESTIC_NF = frozenset({"nf_RB0", "nf_M0", "nf_JM0", "nf_LH0"})


def sina_quote_only(secid: str) -> bool:
    """该 secid 在新浪只有盘口，没有分时/K 线。"""
    return secid in _SINA_QUOTE_ONLY


def sina_domestic_nf(sina_sym: str) -> bool:
    """商品连续（螺纹/豆粕/焦煤/生猪）。列序与三十债不同。"""
    return sina_sym in _SINA_DOMESTIC_NF


def sina_symbol_from_secid(secid: str) -> str | None:
    """东财 secid → 新浪盘口符号：1.600519→sh600519，0.920000→bj920000。

    北交所 secid 与深市同为 ``0.`` 前缀，但新浪行情中心用 ``bj`` 符号
    （``sz920000`` 返回空串）；不区分会把北交所股票误报成「不存在」。
    东财 ``2.`` 是中证指数市场。2026-10-09 检索和盘口都没有 932000，
    拼 ``sh`` 只会拿到空行，所以直接不支持。
    """
    if secid in _SINA_QUOTE_ONLY:
        return _SINA_QUOTE_ONLY[secid]
    if "." not in secid:
        return None
    prefix, code = secid.split(".", 1)
    if prefix == "1":
        return f"sh{code}"
    if prefix == "2":
        return None
    if prefix == "0":
        return f"bj{code}" if code.startswith(BJ_CODE_PREFIXES) else f"sz{code}"
    if prefix in ("105", "106", "107", "153"):
        # 美股符号必须小写（gb_QQQ 返回空）
        return f"gb_{code.lower()}"
    if prefix == "100":
        return US_INDEX_HQ.get(code.upper())
    return None


def sina_us_mink_symbol_from_secid(secid: str) -> str | None:
    """东财 secid → 新浪美股分钟K/日K符号：股票=裸代码（QQQ），指数=.inx 等。

    US_MinKService.getDailyK/getMinK 的 symbol 参数：股票直接传代码，
    指数需带前导点（.inx）；与 hq 盘口符号（gb_*）不同。
    """
    if "." not in secid:
        return None
    prefix, code = secid.split(".", 1)
    if prefix in ("105", "106", "107", "153"):
        return code
    if prefix == "100":
        return US_INDEX_MINK.get(code.upper())
    return None


def _et_to_bj(dt: datetime) -> datetime:
    """美东 naive 时间 → 北京 naive 时间（自动处理夏令时，EDT+12/EST+13）。"""
    if _ET_TZ is None or _BJ_TZ is None:
        return dt
    return dt.replace(tzinfo=_ET_TZ).astimezone(_BJ_TZ).replace(tzinfo=None)


def _f(parts: Sequence[str], idx: int) -> float | None:
    if idx >= len(parts):
        return None
    raw = parts[idx].strip()
    if not raw or raw == "-":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _s(parts: Sequence[str], idx: int) -> str | None:
    if idx >= len(parts):
        return None
    text = parts[idx].strip()
    return text or None


def parse_hq_line(line: str, *, symbol: SymbolRef) -> Quote | MarketError:
    """hq.sinajs.cn CSV → Quote。列序：名称,开,昨收,现价,高,低,买,卖,量(股),额(元),...,日期,时间。"""
    parts = line.split(",")
    if len(parts) < 32:
        return parse_error("新浪盘口字段不足", provider=PROVIDER)
    name = _s(parts, 0) or symbol.name
    open_px = _f(parts, 1)
    prev_close = _f(parts, 2)
    price = _f(parts, 3)
    if price is None or price == 0.0:
        price = prev_close or open_px
    if price is None:
        return parse_error("新浪盘口缺少现价", provider=PROVIDER)
    if price <= 0:
        # 新浪对已转板/退市代码返回全零占位行（并附日期与状态位）。若当成成功，
        # 会用一个 0 价 Quote 阻断优先级链顺延，故按空数据报错交给下一个源。
        return empty_error("新浪盘口无有效报价（疑似转板/退市占位）", provider=PROVIDER)
    high = _f(parts, 4)
    low = _f(parts, 5)
    volume = _f(parts, 8)
    amount = _f(parts, 9)
    change_pct = None
    if prev_close:
        change_pct = round((price - prev_close) / prev_close * 100, 3)
    date_raw = _s(parts, 30)
    time_raw = _s(parts, 31)
    as_of = None
    if date_raw and time_raw:
        try:
            as_of = datetime.strptime(f"{date_raw} {time_raw}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            as_of = None
    return Quote(
        symbol=SymbolRef(
            code=symbol.code,
            name=name,
            asset_class=symbol.asset_class,
            exchange=symbol.exchange,
            provider_symbol=symbol.provider_symbol,
            sec_type=symbol.sec_type,
        ),
        price=price,
        open=open_px,
        high=high,
        low=low,
        prev_close=prev_close,
        change_pct=change_pct,
        change_amount=None,
        volume=volume,
        amount=amount,
        turnover_rate=None,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=as_of,
    )


def _clock(date_raw: str | None, time_raw: str | None) -> datetime | None:
    if not date_raw or not time_raw:
        return None
    date_text = date_raw.strip().replace("/", "-")
    time_text = time_raw.strip()
    if len(time_text) == 6 and time_text.isdigit():
        time_text = f"{time_text[0:2]}:{time_text[2:4]}:{time_text[4:6]}"
    elif len(time_text) == 5 and time_text[2] == ":":
        time_text = f"{time_text}:00"
    try:
        return datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _quote_from_ohlc(
    symbol: SymbolRef,
    *,
    name: str,
    price: float,
    open_px: float | None,
    high: float | None,
    low: float | None,
    prev_close: float | None,
    volume: float | None,
    as_of: datetime | None,
) -> Quote:
    change_amount = None
    change_pct = None
    if prev_close:
        change_amount = price - prev_close
        change_pct = round(change_amount / prev_close * 100, 3)
    return Quote(
        symbol=SymbolRef(
            code=symbol.code,
            name=name,
            asset_class=symbol.asset_class,
            exchange=symbol.exchange,
            provider_symbol=symbol.provider_symbol,
            sec_type=symbol.sec_type,
        ),
        price=price,
        open=open_px,
        high=high,
        low=low,
        prev_close=prev_close,
        change_pct=change_pct,
        change_amount=change_amount,
        volume=volume,
        amount=None,
        turnover_rate=None,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=as_of,
    )


def parse_hq_line_sge(line: str, *, symbol: SymbolRef) -> Quote | MarketError:
    """上金所 gds_ 盘口。列序 2026-10-09 与东财 AU9999 对齐。

    0 现价, 4 最高, 5 最低, 6 时间, 7 昨收, 8 今开, 9 成交量, 12 日期, 13 名称。
    """
    parts = line.split(",")
    if len(parts) < 14:
        return parse_error("新浪黄金盘口字段不足", provider=PROVIDER)
    price = _f(parts, 0)
    prev_close = _f(parts, 7)
    open_px = _f(parts, 8)
    if price is None or price <= 0:
        price = prev_close or open_px
    if price is None or price <= 0:
        return empty_error("新浪黄金盘口无有效报价", provider=PROVIDER)
    return _quote_from_ohlc(
        symbol,
        name=_s(parts, 13) or symbol.name,
        price=price,
        open_px=open_px,
        high=_f(parts, 4),
        low=_f(parts, 5),
        prev_close=prev_close,
        volume=_f(parts, 9),
        as_of=_clock(_s(parts, 12), _s(parts, 6)),
    )


def parse_hq_line_cffex(line: str, *, symbol: SymbolRef) -> Quote | MarketError:
    """中金所 nf_ 连续合约。列序 2026-10-09 与东财三十债主连对齐。

    0 开, 1 高, 2 低, 3 现价, 4 成交量, 13 昨结, 36 日期, 37 时间, 49 名称。
    成交额单位对不上，不填 amount。商品连续（名称在首列）不要走这里。
    """
    parts = line.split(",")
    if len(parts) < 50:
        return parse_error("新浪国债期货盘口字段不足", provider=PROVIDER)
    price = _f(parts, 3)
    prev_close = _f(parts, 13)
    open_px = _f(parts, 0)
    if price is None or price <= 0:
        price = prev_close or open_px
    if price is None or price <= 0:
        return empty_error("新浪国债期货盘口无有效报价", provider=PROVIDER)
    return _quote_from_ohlc(
        symbol,
        name=_s(parts, 49) or symbol.name,
        price=price,
        open_px=open_px,
        high=_f(parts, 1),
        low=_f(parts, 2),
        prev_close=prev_close,
        volume=_f(parts, 4),
        as_of=_clock(_s(parts, 36), _s(parts, 37)),
    )


def parse_hq_line_hk(line: str, *, symbol: SymbolRef) -> Quote | MarketError:
    """港股指数 rt_hk/hk。2026-10-09 rt_hkHSI。

    1 名称, 2 开, 3 昨收, 4 高, 5 低, 6 现价, 17 日期, 18 时间。
    成交额单位对不上恒指全日成交，不填 amount。
    """
    parts = line.split(",")
    if len(parts) < 19:
        return parse_error("新浪港股指数盘口字段不足", provider=PROVIDER)
    price = _f(parts, 6)
    prev_close = _f(parts, 3)
    open_px = _f(parts, 2)
    if price is None or price <= 0:
        price = prev_close or open_px
    if price is None or price <= 0:
        return empty_error("新浪港股指数盘口无有效报价", provider=PROVIDER)
    return _quote_from_ohlc(
        symbol,
        name=_s(parts, 1) or symbol.name,
        price=price,
        open_px=open_px,
        high=_f(parts, 4),
        low=_f(parts, 5),
        prev_close=prev_close,
        volume=None,
        as_of=_clock(_s(parts, 17), _s(parts, 18)),
    )


def parse_hq_line_world(line: str, *, symbol: SymbolRef) -> Quote | MarketError:
    """b_ 全球指数。2026-10-09 b_NKY / b_UKX。

    0 名称, 1 现价, 6 行情日期, 7 时间, 8 开, 9 昨收, 10 高, 11 低。
    第 4、5 列可能是过期标签，日期以第 6 列为准。
    """
    parts = line.split(",")
    if len(parts) < 12:
        return parse_error("新浪全球指数盘口字段不足", provider=PROVIDER)
    price = _f(parts, 1)
    prev_close = _f(parts, 9)
    open_px = _f(parts, 8)
    if price is None or price <= 0:
        price = prev_close or open_px
    if price is None or price <= 0:
        return empty_error("新浪全球指数盘口无有效报价", provider=PROVIDER)
    return _quote_from_ohlc(
        symbol,
        name=_s(parts, 0) or symbol.name,
        price=price,
        open_px=open_px,
        high=_f(parts, 10),
        low=_f(parts, 11),
        prev_close=prev_close,
        volume=None,
        as_of=_clock(_s(parts, 6), _s(parts, 7)),
    )


def parse_hq_line_fx(line: str, *, symbol: SymbolRef) -> Quote | MarketError:
    """外汇与美元指数。2026-10-09 fx_susdcnh / DINIW。

    0 时间, 3 昨收, 6 高, 7 低, 8 现价, 9 名称，日期在最后一列。
    """
    parts = line.split(",")
    if len(parts) < 10:
        return parse_error("新浪外汇盘口字段不足", provider=PROVIDER)
    price = _f(parts, 8) or _f(parts, 1)
    prev_close = _f(parts, 3)
    if price is None or price <= 0:
        price = prev_close
    if price is None or price <= 0:
        return empty_error("新浪外汇盘口无有效报价", provider=PROVIDER)
    date_raw = _s(parts, len(parts) - 1)
    if date_raw is None or ("-" not in date_raw and "/" not in date_raw):
        date_raw = None
    return _quote_from_ohlc(
        symbol,
        name=_s(parts, 9) or symbol.name,
        price=price,
        open_px=None,
        high=_f(parts, 6),
        low=_f(parts, 7),
        prev_close=prev_close,
        volume=None,
        as_of=_clock(date_raw, _s(parts, 0)),
    )


def parse_hq_line_commodity(line: str, *, symbol: SymbolRef) -> Quote | MarketError:
    """内盘商品连续。2026-10-09 nf_RB0。名称在首列，不要走三十债解析。

    0 名称, 1 时间 HHMMSS, 2 开, 3 高, 4 低, 8 现价, 10 昨结, 14 成交量, 17 日期。
    """
    parts = line.split(",")
    if len(parts) < 18 or _f(parts, 0) is not None:
        return parse_error("新浪商品连续盘口字段不足", provider=PROVIDER)
    price = _f(parts, 8)
    prev_close = _f(parts, 10)
    if prev_close is None or prev_close <= 0:
        prev_close = _f(parts, 5)
    open_px = _f(parts, 2)
    if price is None or price <= 0:
        price = prev_close or open_px
    if price is None or price <= 0:
        return empty_error("新浪商品连续盘口无有效报价", provider=PROVIDER)
    return _quote_from_ohlc(
        symbol,
        name=_s(parts, 0) or symbol.name,
        price=price,
        open_px=open_px,
        high=_f(parts, 3),
        low=_f(parts, 4),
        prev_close=prev_close,
        volume=_f(parts, 14),
        as_of=_clock(_s(parts, 17), _s(parts, 1)),
    )


def parse_hq_line_us(line: str, *, symbol: SymbolRef) -> Quote | MarketError:
    """hq.sinajs.cn 美股 gb_ CSV → Quote。

    列序（与 A 股完全不同）：0 名称, 1 现价, 2 涨跌%, 4 涨跌, 5 今开,
    6 最高, 7 最低, 8 52周高, 9 52周低, 10 量(股), 12 总市值(美元),
    19 股本, 24/25 美东时间串, 26 昨收, 30 成交额(美元)。
    量额单位与东财美股口径一致（股/美元），不做换算。
    """
    parts = line.split(",")
    if len(parts) < 27:
        return parse_error("新浪美股盘口字段不足", provider=PROVIDER)
    name = _s(parts, 0) or symbol.name
    price = _f(parts, 1)
    prev_close = _f(parts, 26)
    open_px = _f(parts, 5)
    if price is None or price == 0.0:
        price = prev_close or open_px
    if price is None:
        return parse_error("新浪美股盘口缺少现价", provider=PROVIDER)
    change_pct = None
    if prev_close:
        # 与 A 股口径一致按昨收计算；新浪美股涨跌%字段对部分标的（如 OTC）不维护
        change_pct = round((price - prev_close) / prev_close * 100, 3)
    if change_pct is None:
        change_pct = _f(parts, 2)
    return Quote(
        symbol=SymbolRef(
            code=symbol.code,
            name=name,
            asset_class=symbol.asset_class,
            exchange=symbol.exchange,
            provider_symbol=symbol.provider_symbol,
            sec_type=symbol.sec_type,
        ),
        price=price,
        open=open_px,
        high=_f(parts, 6),
        low=_f(parts, 7),
        prev_close=prev_close,
        change_pct=change_pct,
        change_amount=_f(parts, 4),
        volume=_f(parts, 10),
        amount=_f(parts, 30),
        turnover_rate=None,
        pe=None,
        pb=None,
        market_cap=_f(parts, 12),
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        # 东财主源美股 Quote 也不带 as_of；新浪美股时间为美东串，保持一致置空
        as_of=None,
    )


def _parse_ts(raw: str) -> datetime | None:
    text = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_kline_rows(
    rows: object,
    *,
    symbol: SymbolRef,
    period: KlinePeriod,
) -> KlineSeries | MarketError:
    if not isinstance(rows, list) or not rows:
        return empty_error("新浪K线为空", provider=PROVIDER)
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ts = _parse_ts(str(row.get("day", "")))
        if ts is None:
            continue
        try:
            open_px = float(row["open"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            volume = float(row["volume"])
        except (KeyError, TypeError, ValueError):
            continue
        amount = None
        if "amount" in row:
            try:
                amount = float(row["amount"])
            except (TypeError, ValueError):
                amount = None
        bars.append(
            Bar(
                ts=ts,
                open=open_px,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=amount,
                amplitude=None,
                change_pct=None,
                change_amount=None,
                turnover_rate=None,
            )
        )
    if not bars:
        return empty_error("新浪K线解析后为空", provider=PROVIDER)
    return KlineSeries(symbol=symbol, period=period, bars=tuple(bars), adjusted=False)


def parse_us_daily_rows(
    rows: object,
    *,
    symbol: SymbolRef,
    period: KlinePeriod,
    limit: int,
    start: date | None = None,
    end: date | None = None,
) -> KlineSeries | MarketError:
    """新浪美股 getDailyK（d/o/h/l/c/v/a 全量历史）→ KlineSeries。

    接口只返回全量历史，按 start/end 过滤后取尾部 limit 根。
    """
    if not isinstance(rows, list) or not rows:
        return empty_error("新浪美股日K为空", provider=PROVIDER)
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ts = _parse_ts(str(row.get("d", "")))
        if ts is None:
            continue
        try:
            open_px = float(row["o"])
            high = float(row["h"])
            low = float(row["l"])
            close = float(row["c"])
            volume = float(row["v"])
        except (KeyError, TypeError, ValueError):
            continue
        amount = None
        if "a" in row:
            try:
                amount = float(row["a"])
            except (TypeError, ValueError):
                amount = None
        bars.append(
            Bar(
                ts=ts,
                open=open_px,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=amount,
                amplitude=None,
                change_pct=None,
                change_amount=None,
                turnover_rate=None,
            )
        )
    if not bars:
        return empty_error("新浪美股日K解析后为空", provider=PROVIDER)
    if start is not None:
        bars = [b for b in bars if b.ts.date() >= start]
    if end is not None:
        bars = [b for b in bars if b.ts.date() <= end]
    if not bars:
        return empty_error("新浪美股日K过滤后为空", provider=PROVIDER)
    return KlineSeries(symbol=symbol, period=period, bars=tuple(bars[-limit:]), adjusted=False)


def _us_mink_bars(rows: object) -> list[Bar] | MarketError:
    """getMinK 行（d/o/h/l/c/v/a，美东时间，逐 bar 量额）→ Bar（北京时间）。"""
    if not isinstance(rows, list) or not rows:
        return empty_error("新浪美股分钟K为空", provider=PROVIDER)
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ts = _parse_ts(str(row.get("d", "")))
        if ts is None:
            continue
        try:
            open_px = float(row["o"])
            high = float(row["h"])
            low = float(row["l"])
            close = float(row["c"])
            volume = float(row["v"])
        except (KeyError, TypeError, ValueError):
            continue
        amount = None
        if "a" in row:
            try:
                amount = float(row["a"])
            except (TypeError, ValueError):
                amount = None
        bars.append(
            Bar(
                ts=_et_to_bj(ts),
                open=open_px,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=amount,
                amplitude=None,
                change_pct=None,
                change_amount=None,
                turnover_rate=None,
            )
        )
    if not bars:
        return empty_error("新浪美股分钟K解析后为空", provider=PROVIDER)
    return bars


def parse_us_mink_rows(
    rows: object,
    *,
    symbol: SymbolRef,
    period: KlinePeriod,
    limit: int,
    start: date | None = None,
    end: date | None = None,
) -> KlineSeries | MarketError:
    """getMinK 分钟K（type=5/15/30/60，最多 1023 根，约 3~40 个交易日）→ KlineSeries。"""
    bars = _us_mink_bars(rows)
    if isinstance(bars, MarketError):
        return bars
    if start is not None:
        bars = [b for b in bars if b.ts.date() >= start]
    if end is not None:
        bars = [b for b in bars if b.ts.date() <= end]
    if not bars:
        return empty_error("新浪美股分钟K过滤后为空", provider=PROVIDER)
    return KlineSeries(symbol=symbol, period=period, bars=tuple(bars[-limit:]), adjusted=False)


# type=1 分时新鲜度守卫：新浪对部分标的（OTC、美股指数）的 1 分钟数据停更于
# 2020 年，返回的仍是旧数据；超期视为该源无此数据（unsupported 回落东财）
_US_INTRADAY_MAX_AGE_DAYS = 10


def parse_us_mink_intraday(
    rows: object,
    *,
    symbol: SymbolRef,
    quote: Quote | None,
    today: date,
) -> IntradaySeries | MarketError:
    """getMinK type=1（1 分钟线，末时刻标注）→ 当日（最近一个交易日）IntradaySeries。

    时间戳美东 → 北京；量额为逐 bar 值，均价按累计额/累计量推导。
    """
    if not isinstance(rows, list) or not rows:
        return unsupported("新浪该美股标的无分时数据", provider=PROVIDER)
    parsed: list[tuple[datetime, Bar]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ts = _parse_ts(str(row.get("d", "")))
        if ts is None:
            continue
        try:
            bar = Bar(
                ts=ts,
                open=float(row["o"]),
                high=float(row["h"]),
                low=float(row["l"]),
                close=float(row["c"]),
                volume=float(row["v"]),
                amount=float(row["a"]) if row.get("a") not in (None, "") else None,
                amplitude=None,
                change_pct=None,
                change_amount=None,
                turnover_rate=None,
            )
        except (KeyError, TypeError, ValueError):
            continue
        parsed.append((ts, bar))
    if not parsed:
        return unsupported("新浪该美股标的无分时数据", provider=PROVIDER)
    last_day = parsed[-1][0].date()
    if (today - last_day).days > _US_INTRADAY_MAX_AGE_DAYS:
        # 数据停更（如 OTC/指数停在 2020 年），不是当日分时
        return unsupported(f"新浪该美股标的无近期分时数据（末点 {last_day}）", provider=PROVIDER)
    session = [bar for ts, bar in parsed if ts.date() == last_day]
    points: list[IntradayPoint] = []
    open_px: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    cum_vol = 0.0
    cum_amount = 0.0
    for bar in session:
        price = bar.close
        if price <= 0:
            continue
        ts_bj = _et_to_bj(bar.ts)
        if open_px is None:
            open_px = bar.open or price
        day_high = bar.high if day_high is None else max(day_high, bar.high)
        day_low = bar.low if day_low is None else min(day_low, bar.low)
        cum_vol += bar.volume or 0.0
        cum_amount += bar.amount or 0.0
        points.append(
            IntradayPoint(
                ts=ts_bj,
                price=price,
                open=open_px,
                high=day_high,
                low=day_low,
                volume=bar.volume or 0.0,
                amount=bar.amount or 0.0,
                # 指数（.ixic 等）无成交额（a=0），均价回退到当前价
                avg_price=cum_amount / cum_vol if cum_vol > 0 and cum_amount > 0 else price,
            )
        )
    if not points:
        return empty_error("新浪美股分时解析后为空", provider=PROVIDER)
    return IntradaySeries(symbol=symbol, points=tuple(points), quote=quote, ndays=1)


def parse_minline_rows(
    rows: object,
    *,
    symbol: SymbolRef,
    quote: Quote | None,
    trade_date: str,
) -> IntradaySeries | MarketError:
    """新浪分时 m/v/p/avg_p → IntradaySeries；v 为分钟成交量(股)。"""
    if not isinstance(rows, list) or not rows:
        return empty_error("新浪分时为空", provider=PROVIDER)
    points: list[IntradayPoint] = []
    open_px = quote.open if quote is not None and quote.open else None
    day_high = None
    day_low = None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        m = str(row.get("m", "")).strip()
        if not m:
            continue
        try:
            ts = datetime.strptime(f"{trade_date} {m}", "%Y-%m-%d %H:%M:%S")
            price = float(row["p"])
            volume = float(row.get("v") or 0.0)
            avg_price = float(row.get("avg_p") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if price <= 0:
            continue
        if open_px is None:
            open_px = price
        day_high = price if day_high is None else max(day_high, price)
        day_low = price if day_low is None else min(day_low, price)
        points.append(
            IntradayPoint(
                ts=ts,
                price=price,
                open=open_px,
                high=day_high,
                low=day_low,
                volume=volume,
                amount=volume * price,
                avg_price=avg_price if avg_price > 0 else price,
            )
        )
    if not points:
        return empty_error("新浪分时解析后为空", provider=PROVIDER)
    return IntradaySeries(symbol=symbol, points=tuple(points), quote=quote, ndays=1)


def parse_node_row(row: Mapping[str, object]) -> BoardRow | None:
    """行情中心行 → BoardRow；amount 元、mktcap/nmc 万元 → 元。"""
    code = row.get("code")
    if not isinstance(code, str) or not code.strip():
        return None

    def _num(key: str) -> float | None:
        raw = row.get(key)
        if not isinstance(raw, (int, float, str)) or isinstance(raw, bool):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _txt(key: str) -> str | None:
        raw = row.get(key)
        return raw.strip() if isinstance(raw, str) and raw.strip() else None

    market_cap = _num("mktcap")
    float_cap = _num("nmc")
    extras = BoardExtras(
        pe=_num("per"),
        turnover_rate=_num("turnoverratio"),
        float_market_cap=float_cap * 10000.0 if float_cap is not None else None,
    )
    has_extra = any(v is not None for v in (extras.pe, extras.turnover_rate, extras.float_market_cap))
    return BoardRow(
        code=code.strip(),
        name=_txt("name") or code.strip(),
        price=_num("trade"),
        change_pct=_num("changepercent"),
        amount=_num("amount"),
        market_cap=market_cap * 10000.0 if market_cap is not None else None,
        industry=None,
        lead_name=None,
        lead_change_pct=None,
        fall_name=None,
        fall_change_pct=None,
        extras=extras if has_extra else None,
    )


def parse_node_board(
    rows: object,
    *,
    kind: BoardKind,
    title: str,
    limit: int | None = None,
) -> BoardSnapshot | MarketError:
    if not isinstance(rows, list) or not rows:
        return empty_error("新浪列表为空", provider=PROVIDER)
    out: list[BoardRow] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        parsed = parse_node_row(row)
        if parsed is None:
            continue
        out.append(parsed)
        if limit is not None and len(out) >= limit:
            break
    if not out:
        return empty_error("新浪列表解析后为空", provider=PROVIDER)
    return BoardSnapshot(kind=kind, title=title, rows=tuple(out))


def parse_industry_summary(
    payload: object,
    *,
    kind: BoardKind,
    title: str,
    sort_asc: bool = False,
    limit: int | None = None,
) -> BoardSnapshot | MarketError:
    """newSinaHy 变量表 → 行业板块 BoardSnapshot。

    行列序：节点,名称,家数,均价,涨跌额,涨跌幅,成交量,成交额,
    领涨代码,领涨涨跌幅,领涨价,?,领涨名。
    sort_asc / limit 由调用方决定，领跌榜不能再和领涨榜共用降序前 N 名。
    """
    if not isinstance(payload, Mapping) or not payload:
        return empty_error("新浪行业板块为空", provider=PROVIDER)
    rows: list[BoardRow] = []
    for node, raw in payload.items():
        if not isinstance(raw, str):
            continue
        parts = raw.split(",")
        if len(parts) < 10:
            continue
        try:
            price = float(parts[3])
            change_pct = float(parts[5])
            amount = float(parts[7])
            lead_change = float(parts[9])
        except (TypeError, ValueError):
            continue
        lead_name = parts[12].strip() if len(parts) > 12 else None
        rows.append(
            BoardRow(
                code=node,
                name=parts[1].strip(),
                price=price,
                change_pct=change_pct,
                amount=amount,
                market_cap=None,
                industry=None,
                lead_name=lead_name or None,
                lead_change_pct=lead_change,
                fall_name=None,
                fall_change_pct=None,
                extras=None,
            )
        )
    if not rows:
        return empty_error("新浪行业板块解析后为空", provider=PROVIDER)
    rows.sort(key=lambda r: r.change_pct if r.change_pct is not None else 0.0, reverse=not sort_asc)
    if limit is not None:
        rows = rows[: max(0, limit)]
    return BoardSnapshot(kind=kind, title=title, rows=tuple(rows))


# RankBy → 行情中心 sort 参数；不支持资金流/ROE/利润同比
SINA_RANK_SORT: dict[RankBy, str] = {
    RankBy.TURNOVER: "turnoverratio",
    RankBy.AMOUNT: "amount",
    RankBy.VOLUME: "volume",
}

_SINA_RANK_LABEL: dict[RankBy, str] = {
    RankBy.TURNOVER: "换手率",
    RankBy.AMOUNT: "成交额",
    RankBy.VOLUME: "成交量",
}


def parse_rank_rows(
    rows: object,
    *,
    rank_by: RankBy,
    high_first: bool,
    limit: int,
) -> RankSnapshot | MarketError:
    if not isinstance(rows, list) or not rows:
        return empty_error("新浪排行为空", provider=PROVIDER)
    if rank_by not in SINA_RANK_SORT:
        return parse_error(f"新浪不支持排行 {rank_by.value}", provider=PROVIDER)
    metric_key = SINA_RANK_SORT[rank_by]
    out: list[RankRow] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        board_row = parse_node_row(row)
        if board_row is None:
            continue
        metric = _row_float(row, metric_key)
        out.append(
            RankRow(
                rank=len(out) + 1,
                code=board_row.code,
                name=board_row.name,
                price=board_row.price,
                change_pct=board_row.change_pct,
                metric=metric,
                metric_label=_SINA_RANK_LABEL[rank_by],
                turnover_pct=_row_float(row, "turnoverratio"),
                amount=board_row.amount,
                volume=_row_float(row, "volume"),
                sector=None,
            )
        )
        if len(out) >= limit:
            break
    if not out:
        return empty_error("新浪排行解析后为空", provider=PROVIDER)
    return RankSnapshot(
        rank_by=rank_by.value,
        rank_by_label=_SINA_RANK_LABEL[rank_by],
        unit_hint="元" if rank_by == RankBy.AMOUNT else ("%" if rank_by == RankBy.TURNOVER else "股"),
        high_first=high_first,
        caveat=RANKING_CAVEAT,
        rows=tuple(out),
    )


def _row_float(row: Mapping[str, object], key: str) -> float | None:
    raw = row.get(key)
    if not isinstance(raw, (int, float, str)) or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def industry_menu(payload: object) -> dict[str, str] | MarketError:
    """newSinaHy → {行业名: 节点代码}，供 sector_menu("industry")。"""
    if not isinstance(payload, Mapping) or not payload:
        return empty_error("新浪行业板块为空", provider=PROVIDER)
    menu: dict[str, str] = {}
    for node, raw in payload.items():
        if not isinstance(raw, str):
            continue
        parts = raw.split(",")
        if len(parts) < 2 or not parts[1].strip():
            continue
        menu[parts[1].strip()] = node
    if not menu:
        return empty_error("新浪行业菜单为空", provider=PROVIDER)
    return menu


def node_for_industry(menu: Mapping[str, str], sector: str) -> str | None:
    """行业名/节点代码 → 行情中心 node。"""
    text = sector.strip()
    if text in menu:
        return menu[text]
    for name, node in menu.items():
        if name == text or node == text:
            return node
    return None


# 资金流排行 sort 参数：新浪按净流入/净流出绝对值排序，asc 决定方向
MONEY_FLOW_RANK_BY = (RankBy.MAIN_INFLOW, RankBy.MAIN_OUTFLOW)
_MONEY_FLOW_LABEL: dict[RankBy, str] = {
    RankBy.MAIN_INFLOW: "主力净流入",
    RankBy.MAIN_OUTFLOW: "主力净流出",
}


def parse_money_flow_rank(
    rows: object,
    *,
    rank_by: RankBy,
    high_first: bool,
    limit: int,
) -> RankSnapshot | MarketError:
    """MoneyFlow.ssl_bkzj_ssggzj 行 → RankSnapshot。

    该接口的 `changeratio` 是**小数比例**（如 -0.0000852594 表示 -0.0085%），
    与行情中心 `changepercent` 的百分数口径不同，此处统一 ×100 对齐内部模型。
    """
    if rank_by not in MONEY_FLOW_RANK_BY:
        return parse_error(f"新浪不支持资金流排行 {rank_by.value}", provider=PROVIDER)
    if not isinstance(rows, list) or not rows:
        return empty_error("新浪资金流排行为空", provider=PROVIDER)
    out: list[RankRow] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        raw_name = row.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else symbol.strip()
        ratio = _row_float(row, "changeratio")
        out.append(
            RankRow(
                rank=len(out) + 1,
                code=symbol.strip(),
                name=name,
                price=_row_float(row, "trade"),
                change_pct=round(ratio * 100, 3) if ratio is not None else None,
                metric=_row_float(row, "netamount"),
                metric_label=_MONEY_FLOW_LABEL[rank_by],
                turnover_pct=None,
                amount=_row_float(row, "amount"),
                volume=_row_float(row, "turnover"),
                sector=None,
            )
        )
        if len(out) >= limit:
            break
    if not out:
        return empty_error("新浪资金流排行解析后为空", provider=PROVIDER)
    return RankSnapshot(
        rank_by=rank_by.value,
        rank_by_label=_MONEY_FLOW_LABEL[rank_by],
        unit_hint="元",
        high_first=high_first,
        caveat=RANKING_CAVEAT,
        rows=tuple(out),
    )


# 名义涨跌停阈值（%）：科创/创业 20、北交所 30、主板 10。
# 实测（2026-10-08 收盘，比对腾讯盘口「涨停价/跌停价」字段）：主板 ST/*ST 同样 ±10%，
# 名称里的 ST 不改变涨跌幅限制，故本函数不接收名称。
_LIMIT_TOLERANCE = 0.95


def _limit_threshold_pct(code: str) -> float:
    """按板块判定名义涨跌停幅度。"""
    if code.startswith(("688", "689")) or code[:3] in ("300", "301"):
        return 20.0
    if code.startswith(BJ_CODE_PREFIXES):
        return 30.0
    return 10.0


def _limit_price(prev_close: float, nominal_pct: float, *, up: bool) -> float:
    """昨收 × (1±阈值) → 涨跌停价：交易所口径四舍五入到分（Decimal 半进位）。"""
    ratio = Decimal(1) + Decimal(str(nominal_pct)) / Decimal(100) * (1 if up else -1)
    return float((Decimal(str(prev_close)) * ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_breadth_rows(rows: object) -> BreadthBar | MarketError:
    """全 A 行（行情中心 hs_a）→ 涨跌分布，口径与东财 updowndistribution 一致。

    分档用「涨停 → 5~10 → … → 平 → … → 跌停」；`5~10` 实际覆盖「5% 到涨停」
    （东财把主板/双创/北交所的涨停带合并进同一档）。
    首尾两档按「收盘价 == 涨停价/跌停价」逐分比对：涨幅超阈值但未封板（新股首日、
    冲高回落）不计入涨停；昨收缺失时回退到名义阈值 × 容差。停牌（现价或成交量为 0）
    不计入任何档位（东财/同花顺同样把停牌单列，不进「平」档）。
    """
    if not isinstance(rows, list) or not rows:
        return empty_error("新浪全A列表为空", provider=PROVIDER)
    counts: dict[str, int] = {label: 0 for label in BREADTH_BANDS}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        pct = _row_float(row, "changepercent")
        if pct is None:
            continue
        close = _row_float(row, "trade")
        prev_close = _row_float(row, "settlement")
        volume = _row_float(row, "volume")
        if (close is not None and close <= 0) or (volume is not None and volume <= 0):
            continue
        code = row.get("code")
        code_text = code.strip() if isinstance(code, str) else ""
        nominal = _limit_threshold_pct(code_text)
        if close is not None and close > 0 and prev_close is not None and prev_close > 0:
            counts[_sealed_band_label(pct, close, prev_close, nominal)] += 1
        else:
            limit = nominal * _LIMIT_TOLERANCE
            if pct >= limit:
                counts["涨停"] += 1
            elif pct <= -limit:
                counts["跌停"] += 1
            else:
                counts[_band_label(pct)] += 1
    if not any(counts.values()):
        return empty_error("新浪全A涨跌分布解析后全零", provider=PROVIDER)
    return BreadthBar(buckets=tuple(BreadthBucket(label=k, count=counts[k]) for k in BREADTH_BANDS))


def _sealed_band_label(pct: float, close: float, prev_close: float, nominal_pct: float) -> str:
    """收盘价与涨停价/跌停价逐分比对 → 首尾档；否则按幅度落中间档。"""
    if abs(close - _limit_price(prev_close, nominal_pct, up=True)) < 1e-4:
        return "涨停"
    if abs(close - _limit_price(prev_close, nominal_pct, up=False)) < 1e-4:
        return "跌停"
    return _band_label(pct)


def _band_label(pct: float) -> str:
    """涨跌幅 → 中间档位标签（不含首尾涨停/跌停档）。"""
    mag = abs(pct)
    if pct > 0:
        for lo, label in ((5, "5~10"), (3, "3~5"), (2, "2~3"), (1, "1~2")):
            if mag >= lo:
                return label
        return "0~1"
    if pct < 0:
        for lo, label in ((5, "-5~-10"), (3, "-3~-5"), (2, "-2~-3"), (1, "-1~-2")):
            if mag >= lo:
                return label
        return "0~-1"
    return "平"


def parse_turnover_quotes(quotes: Sequence[Quote]) -> MarketTurnover | MarketError:
    """沪 + 深指数盘口 → 两市成交额。

    单市场源拿不到昨成交额，`prev_amount` 返回 None 而非 0 填充（渲染层需判空）。
    """
    total = 0.0
    last: datetime | None = None
    for q in quotes:
        if q.amount is not None:
            total += q.amount
        if q.as_of is not None and (last is None or q.as_of > last):
            last = q.as_of
    if total <= 0:
        return empty_error("新浪两市成交额解析为 0", provider=PROVIDER)
    return MarketTurnover(prev_amount=None, amount=total, last_trade_date=last)
