import io
from typing import Dict, List, Union

import aiohttp
import pandas as pd
from gsuid_core.logger import logger

from .utils import ErroText

URL = 'https://1.optbbs.com/d/csv/d/{}.csv'


async def get_vix_data(vix_name: str):
    url = URL.format(vix_name)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                content_text = await response.text(encoding='utf-8-sig')
                sio = io.StringIO(content_text)
                df = pd.read_csv(sio)
        except aiohttp.ClientError as e:
            logger.error(f"请求 URL 失败: {e}")
            return ErroText['notStock']
        except pd.errors.ParserError as e:
            logger.error(f"解析 CSV 失败: {e}")
            return ErroText['notStock']

    if df.empty:
        return ErroText['notStock']

    if len(df.columns) < 2:
        logger.error("CSV 数据列数不足，无法获取第二列数据。")
        return ErroText['notStock']

    # 获取第二列的列名
    price_col_name = df.columns[1]

    # --- Modified Data Cleaning Steps ---
    # If the second column is null but the third column has data, fill the second with the third.
    if len(df.columns) > 2:
        third_col_name = df.columns[2]
        # Condition: second column is null AND third column is not null
        condition = df[price_col_name].isnull() & df[third_col_name].notnull()
        # For rows matching the condition, copy data from the third to the second column
        df.loc[condition, price_col_name] = df.loc[condition, third_col_name]

    # 1. Now, drop rows where the price column is still empty
    df.dropna(subset=[price_col_name], inplace=True)

    # 2. Ensure time format is correct and sort by time
    df['Time'] = pd.to_datetime(df['Time'], format='%H:%M:%S')
    df.sort_values(by='Time', inplace=True)

    # 3. Fill remaining nulls (now only in 'Pre', 'max', 'min' columns)
    df.fillna(0, inplace=True)

    stock_data: List[Dict[str, Union[str, float, int]]] = []

    for _, row in df.iterrows():
        try:
            stock_data.append(
                {
                    'datetime': row['Time'].strftime('%H:%M'),
                    'price': float(str(row[price_col_name]).strip()),
                    'open': float(str(row['Pre']).strip()),
                    'high': float(str(row['max']).strip()),
                    'low': float(str(row['min']).strip()),
                    'amount': 0,
                    'money': 0.0,
                    'avg_price': 0.0,
                }
            )
        except (ValueError, KeyError) as e:
            logger.error(f"处理行数据时出错: {e}, 行数据: {row.to_dict()}")
            continue

    return stock_data
