"""新浪原始响应 → 领域模型（供应商字段仅本文件解析）。"""

from __future__ import annotations

from typing import Mapping, Sequence
from datetime import date, datetime

from .client import PROVIDER
from ...enums import RankBy, BoardKind, KlinePeriod
from ...errors import MarketError, empty_error, parse_error, unsupported
from ...models import (
    RANKING_CAVEAT,
    Bar,
    Quote,
    RankRow,
    BoardRow,
    SymbolRef,
    BoardExtras,
    KlineSeries,
    RankSnapshot,
    BoardSnapshot,
    IntradayPoint,
    IntradaySeries,
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


def sina_symbol_from_secid(secid: str) -> str | None:
    """东财 secid → 新浪盘口符号：1.600519→sh600519，105.QQQ→gb_qqq，100.SPX→gb_inx。"""
    if "." not in secid:
        return None
    prefix, code = secid.split(".", 1)
    if prefix == "1":
        return f"sh{code}"
    if prefix == "0":
        return f"sz{code}"
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
) -> BoardSnapshot | MarketError:
    """newSinaHy 变量表 → 行业板块 BoardSnapshot。

    行列序：节点,名称,家数,均价,涨跌额,涨跌幅,成交量,成交额,
    领涨代码,领涨涨跌幅,领涨价,?,领涨名。
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
    rows.sort(key=lambda r: r.change_pct if r.change_pct is not None else 0.0, reverse=True)
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
