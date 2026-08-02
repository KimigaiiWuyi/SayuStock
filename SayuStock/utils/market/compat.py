"""兼容期：领域模型 → 旧东财形 dict，仅 shim 使用。"""

from __future__ import annotations

from typing import Any

from .models import Quote, KlineSeries, BoardSnapshot, IntradaySeries


def quote_to_em_dict(quote: Quote, trends: list[dict[str, object]] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "f57": quote.symbol.code,
        "f58": quote.symbol.name,
        "f43": quote.price,
        "f46": quote.open,
        "f44": quote.high,
        "f45": quote.low,
        "f60": quote.prev_close,
        "f170": quote.change_pct,
        "f169": quote.change_amount,
        "f47": quote.volume,
        "f48": quote.amount,
        "f168": quote.turnover_rate,
        "f9": quote.pe,
        "f23": quote.pb,
        "f20": quote.market_cap,
        "f21": quote.float_market_cap,
        "f127": quote.industry,
        "f100": quote.industry,
        "f51": quote.limit_up,
        "f52": quote.limit_down,
    }
    out: dict[str, Any] = {"data": data}
    if trends is not None:
        out["trends"] = trends
    return out


def intraday_to_trend_dicts(series: IntradaySeries) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for p in series.points:
        rows.append(
            {
                "datetime": p.ts.strftime("%Y-%m-%d %H:%M"),
                "price": p.price,
                "open": p.open,
                "high": p.high,
                "low": p.low,
                "amount": int(p.volume),
                "money": p.amount,
                "avg_price": p.avg_price,
            }
        )
    return rows


def quote_with_intraday_to_em(series: IntradaySeries) -> dict[str, Any]:
    trends = intraday_to_trend_dicts(series)
    if series.quote is not None:
        return quote_to_em_dict(series.quote, trends)
    # 仅用 last 点合成最小 quote
    last = series.points[-1]
    first = series.points[0]
    prev = first.open if first.open else first.price
    q = Quote(
        symbol=series.symbol,
        price=last.price,
        open=first.open,
        high=max(p.high for p in series.points),
        low=min(p.low for p in series.points),
        prev_close=prev,
        change_pct=((last.price - prev) / prev * 100.0) if prev else None,
        change_amount=None,
        volume=None,
        amount=None,
        turnover_rate=None,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=last.ts,
    )
    return quote_to_em_dict(q, trends)


def kline_to_em_dict(series: KlineSeries) -> dict[str, Any]:
    lines: list[str] = []
    for b in series.bars:
        ts = b.ts.strftime("%Y-%m-%d %H:%M") if (b.ts.hour or b.ts.minute) else b.ts.strftime("%Y-%m-%d")
        parts = [
            ts,
            f"{b.open}",
            f"{b.close}",
            f"{b.high}",
            f"{b.low}",
            f"{b.volume}",
            f"{b.amount if b.amount is not None else 0}",
            f"{b.amplitude if b.amplitude is not None else 0}",
            f"{b.change_pct if b.change_pct is not None else 0}",
            f"{b.change_amount if b.change_amount is not None else 0}",
            f"{b.turnover_rate if b.turnover_rate is not None else 0}",
        ]
        lines.append(",".join(parts))
    return {
        "data": {
            "code": series.symbol.code,
            "name": series.symbol.name,
            "klines": lines,
        }
    }


def board_to_em_dict(snapshot: BoardSnapshot) -> dict[str, Any]:
    diff: list[dict[str, Any]] = []
    for r in snapshot.rows:
        row: dict[str, Any] = {
            "f12": r.code,
            "f14": r.name,
            "f2": r.price,
            "f3": r.change_pct,
            "f6": r.amount,
            "f20": r.market_cap,
            "f100": r.industry,
            "f128": r.lead_name,
            "f136": r.lead_change_pct,
        }
        if r.extras is not None:
            if r.extras.pe is not None:
                row["f9"] = r.extras.pe
            if r.extras.turnover_rate is not None:
                row["f8"] = r.extras.turnover_rate
            if r.extras.volume_ratio is not None:
                row["f10"] = r.extras.volume_ratio
            if r.extras.lead_code is not None:
                row["f140"] = r.extras.lead_code
            if r.extras.up_count is not None:
                row["f104"] = r.extras.up_count
            if r.extras.down_count is not None:
                row["f105"] = r.extras.down_count
        diff.append(row)
    return {"data": {"total": len(diff), "diff": diff}}
