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

    # 获取第二列的列名，通常是 QVIX 或其变体
    price_col_name = df.columns[1]

    # --- 新增的数据清洗步骤 ---
    # 1. 剔除 QVIX 列为空的行，inplace=True 表示直接在原 DataFrame 上修改
    df.dropna(subset=[price_col_name], inplace=True)

    # 2. 确保时间格式正确并按时间排序，这对于后续取第一行和最后一行数据很重要
    df['Time'] = pd.to_datetime(df['Time'], format='%H:%M:%S')
    df.sort_values(by='Time', inplace=True)

    # 3. 重新填充空值（现在只剩下'Pre','max','min'列的空值）
    df.fillna(0, inplace=True)

    stock_data: List[Dict[str, Union[str, float, int]]] = []

    for _, row in df.iterrows():
        try:
            stock_data.append(
                {
                    'datetime': row['Time'].strftime('%H:%M'),  # type: ignore
                    'price': float(str(row[price_col_name]).strip()),
                    'open': float(str(row['Pre']).strip()),
                    'high': float(str(row['max']).strip()),
                    'low': float(str(row['min']).strip()),
                    # 这些字段在 VIX CSV 数据中不存在，根据原始格式，我们用 0 填充
                    'amount': 0,
                    'money': 0.0,
                    'avg_price': 0.0,
                }
            )
        except (ValueError, KeyError) as e:
            logger.error(f"处理行数据时出错: {e}, 行数据: {row.to_dict()}")
            continue  # 跳过当前行，继续处理下一行

    # 4. 返回处理好的字典列表
    return stock_data
    return stock_data
