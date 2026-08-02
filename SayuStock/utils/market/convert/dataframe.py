"""领域模型 → DataFrame（指标/图表复用）。"""

from __future__ import annotations

import pandas as pd

from ..models import Quote, KlineSeries, BoardSnapshot, IntradaySeries
from ...indicators import ma, normalize_pct


def kline_to_df(series: KlineSeries) -> pd.DataFrame:
    """英文列：date/open/high/low/close/volume/... 与 utils.kline.KLINE_COLUMNS 对齐。"""
    rows: list[dict[str, float | str]] = []
    for bar in series.bars:
        date_s = bar.ts.strftime("%Y-%m-%d %H:%M") if (bar.ts.hour or bar.ts.minute) else bar.ts.strftime("%Y-%m-%d")
        rows.append(
            {
                "date": date_s,
                "open": bar.open,
                "close": bar.close,
                "high": bar.high,
                "low": bar.low,
                "volume": bar.volume,
                "amount": bar.amount if bar.amount is not None else 0.0,
                "amplitude": bar.amplitude if bar.amplitude is not None else 0.0,
                "chg_pct": bar.change_pct if bar.change_pct is not None else 0.0,
                "chg_amount": bar.change_amount if bar.change_amount is not None else 0.0,
                "turnover_rate": bar.turnover_rate if bar.turnover_rate is not None else 0.0,
            }
        )
    return pd.DataFrame(rows)


def kline_to_cn_df(series: KlineSeries) -> pd.DataFrame:
    """中文列 + 均线/归一化，供 fill_kline 风格渲染复用。"""
    en = kline_to_df(series)
    if en.empty:
        return en
    df = pd.DataFrame(
        {
            "日期": pd.to_datetime(en["date"], errors="coerce"),
            "开盘": en["open"],
            "收盘": en["close"],
            "最高": en["high"],
            "最低": en["low"],
            "成交量": en["volume"],
            "成交额": en["amount"],
            "振幅": en["amplitude"],
            "涨跌幅": en["chg_pct"],
            "涨跌额": en["chg_amount"],
            "换手率": en["turnover_rate"],
        }
    )
    df = df.dropna(subset=["开盘", "收盘", "成交量"]).reset_index(drop=True)
    if df.empty:
        return df
    close = df["收盘"]
    assert isinstance(close, pd.Series), "收盘列存在重复标签"
    df["5日均线"] = ma(close, 5)
    df["10日均线"] = ma(close, 10)
    df["换手率"] = df["换手率"].astype(float)
    df["归一化"] = normalize_pct(close)
    return df


def board_to_df(snapshot: BoardSnapshot) -> pd.DataFrame:
    rows: list[dict[str, float | str | None]] = []
    for r in snapshot.rows:
        extras = r.extras
        rows.append(
            {
                "code": r.code,
                "name": r.name,
                "price": r.price,
                "pct": r.change_pct,
                "amount": r.amount,
                "mv": r.market_cap,
                "industry": r.industry or "未分类",
                "pe": extras.pe if extras is not None else None,
                "turnover": extras.turnover_rate if extras is not None else None,
                "vol_ratio": extras.volume_ratio if extras is not None else None,
                "mv_circ": extras.float_market_cap if extras is not None else None,
            }
        )
    return pd.DataFrame(rows)


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
                "amount": int(p.volume) if p.volume == int(p.volume) else p.volume,
                "money": p.amount,
                "avg_price": p.avg_price,
            }
        )
    return rows


def quote_fields(q: Quote) -> dict[str, float | str | None]:
    """业务侧语义字段字典（非 f*）。"""
    return {
        "code": q.symbol.code,
        "name": q.symbol.name,
        "price": q.price,
        "open": q.open,
        "high": q.high,
        "low": q.low,
        "prev_close": q.prev_close,
        "change_pct": q.change_pct,
        "amount": q.amount,
        "turnover_rate": q.turnover_rate,
        "pe": q.pe,
        "pb": q.pb,
        "market_cap": q.market_cap,
        "industry": q.industry,
        "secid": q.symbol.provider_symbol,
    }
