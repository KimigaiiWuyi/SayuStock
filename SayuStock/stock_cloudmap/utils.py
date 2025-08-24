from typing import Dict

import pandas as pd

VIX_LIST = {
    '300ETFVIX': 'vix300',
    '沪深300VIX': 'vix300',
    'HS300VIX': 'vix300',
    '300VIX': 'vix300',
    'VIX300': 'vix300',
    '300IV': 'vix300',
    'IV300': 'vix300',
    '50VIX': 'vix50',
    '50ETFVIX': 'vix50',
    '50IV': 'vix50',
    'VIX50': 'vix50',
    'IV50': 'vix50',
    '1000VIX': 'vixindex1000',
    'VIX1000': 'vixindex1000',
    'IV1000': 'vixindex1000',
    '1000ETFVIX': 'vixindex1000',
    '科创板VIX': 'vixkcb',
    '科创版VIX': 'vixkcb',
    'VIX科创版': 'vixkcb',
    'VIX科创板': 'vixkcb',
    'VIXKCB': 'vixkcb',
    'KCBVIX': 'vixkcb',
    '创业板VIX': 'vixcyb',
    'VIX创业板': 'vixcyb',
    '创业板IV': 'vixcyb',
    'IV创业板': 'vixcyb',
    '创业版VIX': 'vixcyb',
    'VIX创业版': 'vixcyb',
}

ErroText = {
    'typemap': '❌未找到对应板块, 请重新输入\n📄例如: \n大盘云图沪深A\n大盘云图创业板 \n等等...',
    'notData': '❌不存在该板块或市场, 暂无数据...',
    'notStock': '❌不存在该股票，暂无数据...',
    'notOpen': '❌该股票未开盘，暂无数据...',
}


def fill_kline(raw_data: Dict):
    headers = [
        '日期',
        '开盘',
        '收盘',
        '最高',
        '最低',
        '成交量',
        '成交额',
        '振幅',
        '涨跌幅',
        '涨跌额',
        '换手率',
    ]

    kline_dict = {header: [] for header in headers}

    # 填充字典
    if not raw_data['data']['klines']:
        return None

    for line in raw_data['data']['klines']:
        values = line.split(',')
        for header, value in zip(headers, values):
            kline_dict[header].append(value)
    df = pd.DataFrame(kline_dict)

    # 将收盘价转换为float类型
    df['收盘'] = df['收盘'].astype(float)

    # 计算5日和10日移动平均线
    df['5日均线'] = df['收盘'].rolling(window=5).mean()
    df['10日均线'] = df['收盘'].rolling(window=10).mean()
    df['换手率'] = df['换手率'].astype(float) / 100

    df['归一化'] = (df['收盘'] / df['收盘'].iloc[0]) - 1

    return df
