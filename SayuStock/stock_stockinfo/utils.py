from typing import Dict

import pandas as pd


def fill_kline(raw_data: Dict):
    headers = [
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

    kline_dict = {header: [] for header in headers}

    # 填充字典
    if not raw_data["data"]["klines"]:
        return None

    for line in raw_data["data"]["klines"]:
        values = line.split(",")
        for header, value in zip(headers, values):
            kline_dict[header].append(value)
    df = pd.DataFrame(kline_dict)

    # 将收盘价转换为float类型
    numeric_cols = [
        "开盘",
        "收盘",
        "最高",
        "最低",
        "成交量",
        "成交额",
        "振幅",
        "涨跌幅",
        "涨跌额",
        "换手率",  # 包含所有需要数值转换的列
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")  # 使用coerce处理无法转换的值

    # 在计算均线前，确保关键列没有NaN，否则均线也会是NaN
    df = df.dropna(subset=["开盘", "收盘", "成交量"]).reset_index(drop=True)

    # 计算5日和10日移动平均线
    df["5日均线"] = df["收盘"].rolling(window=5).mean()
    df["10日均线"] = df["收盘"].rolling(window=10).mean()
    df["换手率"] = df["换手率"].astype(float) / 100

    df["归一化"] = (df["收盘"] / df["收盘"].iloc[0]) - 1

    return df
