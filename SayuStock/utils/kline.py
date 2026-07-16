"""东财 K 线解析 —— 全插件唯一真相源。

两种输出形态，对应两批历史调用方：

- ``fill_kline``：中文列名（日期/开盘/收盘…）+ 均线 + 归一化，画图那条链路在用；
- ``klines_to_df`` / ``klines_to_df_mins``：英文列名（date/open/close…），
  指标计算与 AI 工具在用。

``stock_stockinfo/utils.py`` 与 ``stock_cloudmap/utils.py`` 曾各存一份逐字节
相同的 ``fill_kline``，改一处忘另一处就会两边行为分叉。现在都从这里 re-export。

指标数学见 ``SayuStock/utils/indicators.py``。
"""

from typing import Dict, Optional

import pandas as pd

from .indicators import ma, normalize_pct

KLINE_HEADERS = [
    "日期",
    "开盘",
    "收盘",
    "最高",
    "最低",
    "成交量",
    "成交额",
    "振幅",
    "涨跌幅",
    "涨跌额",
    "换手率",
]

# klines_to_df 的英文列名，与 KLINE_HEADERS 一一对应
KLINE_COLUMNS = [
    "date",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "amplitude",
    "chg_pct",
    "chg_amount",
    "turnover_rate",
]

_NUMERIC_COLS = KLINE_HEADERS[1:]


def fill_kline(raw_data: Dict) -> Optional[pd.DataFrame]:
    """把东财 K 线接口返回解析成 DataFrame，并补上均线与归一化列。

    ``归一化`` = 相对首日收盘的累计涨跌幅（小数），多标的对比时把不同价位的
    股票拉到同一起点 —— 注意它是**累计涨跌幅**而非价格，两点相减只是百分点
    之差，算区间涨跌请用 ``indicators.swing_stats``。
    """
    if not raw_data["data"]["klines"]:
        return None

    kline_dict: Dict[str, list] = {header: [] for header in KLINE_HEADERS}
    for line in raw_data["data"]["klines"]:
        values = line.split(",")
        for header, value in zip(KLINE_HEADERS, values):
            kline_dict[header].append(value)
    df = pd.DataFrame(kline_dict)

    for col in _NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")  # coerce 处理无法转换的值

    # 计算均线前先确保关键列没有 NaN，否则均线也会是 NaN
    df = df.dropna(subset=["开盘", "收盘", "成交量"]).reset_index(drop=True)

    df["5日均线"] = ma(df["收盘"], 5)
    df["10日均线"] = ma(df["收盘"], 10)
    df["换手率"] = df["换手率"].astype(float)
    df["归一化"] = normalize_pct(df["收盘"])

    return df


def klines_to_df(klines: list[str]) -> pd.DataFrame:
    """把东财日 K 字符串列表转 DataFrame（英文列名）。

    格式："YYYY-MM-DD,open,close,high,low,volume,amount,amplitude,chg_pct,chg_amount,turnover_rate"
    字段不足 11 列或存在无法解析的数值时整行丢弃。
    """
    rows: list[dict[str, float | str]] = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        try:
            row: dict[str, float | str] = {"date": parts[0]}
            for col, part in zip(KLINE_COLUMNS[1:], parts[1:]):
                row[col] = float(part)
            rows.append(row)
        except (ValueError, IndexError):
            continue
    return pd.DataFrame(rows)


def klines_to_df_mins(klines: list[str]) -> pd.DataFrame:
    """把东财分钟 K 字符串列表转 DataFrame（英文列名）。

    与日 K 的区别：第一列可能是 "YYYY-MM-DD HH:MM"（只取日期部分），
    且字段数普遍只有 6~7 列，缺的补 0。
    """
    rows: list[dict[str, float | str]] = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        try:
            row: dict[str, float | str] = {"date": parts[0].split(" ")[0]}
            for i, col in enumerate(KLINE_COLUMNS[1:], start=1):
                row[col] = float(parts[i]) if len(parts) > i else 0.0
            rows.append(row)
        except (ValueError, IndexError):
            continue
    return pd.DataFrame(rows)
