import json
import random
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Union, Optional

import aiohttp
import aiofiles
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from gsuid_core.logger import logger
from plotly.subplots import make_subplots
from playwright.async_api import async_playwright
from gsuid_core.utils.image.convert import convert_img

from .utils import fill_kline
from ..utils.request import get_code_id
from .get_compare import to_compare_fig
from ..utils.resource_path import GN_BK_PATH
from ..utils.time_range import get_trading_minutes
from ..stock_config.stock_config import STOCK_CONFIG
from ..utils.utils import get_file, number_to_chinese
from ..utils.load_data import mdata, get_full_security_code
from ..utils.constant import (
    SP_STOCK,
    STOCK_SECTOR,
    SINGLE_LINE_FIELDS1,
    SINGLE_LINE_FIELDS2,
    SINGLE_STOCK_FIELDS,
    bk_dict,
    market_dict,
    request_header,
    trade_detail_dict,
)

view_port: int = STOCK_CONFIG.get_config('mapcloud_viewport').data
scale: int = STOCK_CONFIG.get_config('mapcloud_scale').data
minutes: int = STOCK_CONFIG.get_config('mapcloud_refresh_minutes').data

GK_DATA = {}

ErroText = {
    'typemap': '❌未找到对应板块, 请重新输入\n📄例如: \n大盘云图沪深A\n大盘云图创业板 \n等等...',
    'notData': '❌不存在该板块或市场, 暂无数据...',
    'notStock': '❌不存在该股票，暂无数据...',
    'notOpen': '❌该股票未开盘，暂无数据...',
}


async def load_data_from_file(file: Path):
    async with aiofiles.open(file, 'r', encoding='UTF-8') as f:
        data = json.loads(await f.read())
    data['file_name'] = file.name
    return data


async def load_bk_data():
    global GK_DATA
    _GK_DATA = {}
    if GN_BK_PATH.exists():
        _GK_DATA = await load_data_from_file(GN_BK_PATH)
    for i in _GK_DATA:
        GK_DATA[i.upper()] = _GK_DATA[i]
    return GK_DATA


# 获取个股折线数据
async def get_single_fig_data(secid: str):
    params = []
    url = "https://push2.eastmoney.com/api/qt/stock/trends2/get"
    fields1 = ",".join(SINGLE_LINE_FIELDS1)
    fields2 = ",".join(SINGLE_LINE_FIELDS2)
    params.append(('fields1', fields1))
    params.append(('fields2', fields2))
    params.append(('secid', secid))
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            headers=request_header,
            params=params,
        ) as response:
            resp = await response.json()
    # 处理获取个股数据错误
    if resp['data'] is None:
        return ErroText['notStock']
    stock_line_data: list[str] = resp['data']['trends']
    stock_data: list[Dict[str, Union[str, float, int]]] = []
    for item in stock_line_data:
        # 原始数据格式
        # "2024-12-31 14:05,15.63,15.62,15.63,15.61,3300,5154770.00,15.672"
        parts = item.split(',')
        # 原始时间格式为'2024-12-31 14:05'
        datetime = parts[0].split(' ') if len(parts[0]) > 0 else ['', '']
        stock_data.append(
            {
                'datetime': datetime[1],
                'price': float(parts[1]),
                'open': float(parts[2]),
                'high': float(parts[3]),
                'low': float(parts[4]),
                'amount': int(parts[5]),
                'money': float(parts[6]),
                'avg_price': float(parts[7]),
            }
        )
    return stock_data


async def req(url: str, params: List[tuple]):
    async with aiohttp.ClientSession() as session:
        logger.debug(f'[SayuStock] 请求参数: URL: {url}')
        logger.debug(f'[SayuStock] 请求参数: params: {params}')
        async with session.get(
            url,
            headers=request_header,
            params=params,
        ) as response:
            text = await response.text()
            logger.debug(text)
            resp = json.loads(text)
            return resp


async def _get_data(
    resp: Dict,
    url: str,
    params: List[tuple],
    stop_event: asyncio.Event,
):
    if stop_event.is_set():
        return None
    await asyncio.sleep(random.uniform(0.4, 0.9))
    resp2 = await req(url, params)
    if resp2['data']:
        resp['data']['diff'].extend(resp2['data']['diff'])
        if len(resp2['data']['diff']) < 100:
            stop_event.set()
    else:
        stop_event.set()


async def get_data(
    market: str = '沪深A',
    sector: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> Union[Dict, str]:
    market = market.upper()
    if not market:
        market = '沪深A'

    file = get_file(market, 'json')

    is_loop = False
    params = [
        ('pz', '200'),
        ('po', '1'),
        ('np', '1'),
        ('fltt', '2'),
        ('invt', '2'),
        ('fid', 'f3'),
        ('pn', '1'),
    ]

    if market == 'A500':
        market = 'A500ETF'

    if market in SP_STOCK:
        fields = 'f58,f57,f107,f43,f59,f169,f170,f152'
        url = 'https://push2.eastmoney.com/api/qt/stock/get'
        params.append(('secid', SP_STOCK[market]))
    elif sector == STOCK_SECTOR:
        # 个股
        fields = ",".join(SINGLE_STOCK_FIELDS)
        url = 'https://push2.eastmoney.com/api/qt/stock/get'
        logger.info(f'[SayuStock] get_single_fig_data code: {market}')
        secid = await get_code_id(market)
        if secid is None:
            return ErroText['notStock']
        logger.info(f'[SayuStock] get_single_fig_data secid: {secid}')
        secid = get_full_security_code(secid[0])
        file = get_file(secid, 'json', sector)
        params.append(('secid', secid))
    elif sector and sector.startswith('single-stock-kline'):
        url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
        secid = await get_code_id(market)
        if secid is None:
            return ErroText['notStock']
        logger.info(f'[SayuStock] get_single_fig_data secid: {secid}')
        secid = get_full_security_code(secid[0])
        now = datetime.now()
        kline_code = sector.split('-')[-1]
        if kline_code == '100':
            kline_code = 101
            out_day = 50
        elif kline_code == '101':
            out_day = 230
        elif kline_code == '102':
            out_day = 365
        elif kline_code == '103':
            out_day = 520
        elif kline_code == '104':
            out_day = 580
        elif kline_code == '105':
            out_day = 1300
        elif kline_code == '111':
            kline_code = 101
            if start_time:
                if end_time is None:
                    end_time = now
            out_day = 720
        else:
            out_day = 1600

        st_f = start_time.strftime('%Y%m%d') if start_time else ''
        et_f = end_time.strftime('%Y%m%d') if end_time else ''
        file = get_file(
            secid,
            'json',
            sector,
            f"{st_f}-{et_f}",
        )

        params = [
            ('fields1', 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13'),
            ('fields2', 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'),
            ('rtntype', '6'),
            ('klt', kline_code),
            ('fqt', '1'),
            (
                'secid',
                secid,
            ),
        ]

        if start_time and end_time:
            params.append(('beg', start_time.strftime('%Y%m%d')))
            params.append(('end', end_time.strftime('%Y%m%d')))
        else:
            params.append(
                ('beg', (now - timedelta(days=out_day)).strftime("%Y%m%d"))
            )
            params.append(('end', now.strftime("%Y%m%d")))
    else:
        # 大盘云图
        url = 'http://push2.eastmoney.com/api/qt/clist/get'
        if market in market_dict:
            fs = market_dict[market]
        else:
            # 概念云图
            if not GK_DATA:
                await load_bk_data()

            if market in GK_DATA:
                fs = GK_DATA[market]
            else:
                for i in GK_DATA:
                    if market in i:
                        fs = GK_DATA[i]
                        break
                else:
                    return ErroText['typemap']

        fields = ",".join(trade_detail_dict.keys())
        params.append(('fs', fs))
        is_loop = True

    if (
        sector and not sector.startswith('single-stock-kline')
    ) or sector is None:
        params.append(('fields', fields))

    # 检查当前目录下是否有符合条件的文件
    if file.exists():
        # 检查文件的修改时间是否在一分钟以内
        file_mod_time = datetime.fromtimestamp(file.stat().st_mtime)
        if datetime.now() - file_mod_time < timedelta(minutes=minutes):
            logger.info(
                f"[SayuStock] json文件在{minutes}分钟内，直接返回文件数据。"
            )
            return await load_data_from_file(file)

    logger.info("[SayuStock] 开始请求数据...")
    resp = await req(url, params)

    # 这里是在反复请求处理云图数据
    if is_loop and resp['data'] and len(resp['data']['diff']) >= 100:
        stop_event = asyncio.Event()
        pn = 2
        TASK = []
        params.remove(('pn', '1'))
        params.remove(('pz', '200'))
        params.append(('pz', str(len(resp['data']['diff']))))

        while not stop_event.is_set():
            for _ in range(10):
                _params = params.copy()
                _params.append(('pn', str(pn)))
                TASK.append(_get_data(resp, url, _params, stop_event))
                pn += 1
            await asyncio.gather(*TASK)
            TASK.clear()

        await asyncio.gather(*TASK)

    logger.info("[SayuStock] 数据获取完成...")

    # 处理获取个股数据错误
    if sector == STOCK_SECTOR and resp['data'] is None:
        return ErroText['notStock']

    # 处理个股折线数据
    secid = next((value for key, value in params if key == 'secid'), None)
    if sector == STOCK_SECTOR and secid:
        trends = await get_single_fig_data(secid)
        if isinstance(trends, str):
            return resp
        resp['trends'] = trends

    resp['file_name'] = file.name

    # 写入文件
    logger.info("[SayuStock] 开始写入文件...")
    async with aiofiles.open(file, 'w', encoding='UTF-8') as f:
        await f.write(json.dumps(resp, ensure_ascii=False, indent=4))

    if market == '概念板块':
        sresult = {}
        for a in resp['data']['diff']:
            sresult[a['f14']] = f"b:{a['f12']}+f:!50"
        async with aiofiles.open(GN_BK_PATH, 'w', encoding='UTF-8') as f:
            await f.write(json.dumps(sresult, ensure_ascii=False, indent=4))

        if not GK_DATA:
            await load_bk_data()

    return resp


def int_to_percentage(value: Union[int, str, float]) -> str:
    if isinstance(value, str):
        return '-%'
    sign = '+' if value >= 0 else ''
    return f"{sign}{value:.2f}%"


async def to_single_fig_kline(
    raw_data: Dict,
    sp: Optional[str] = None,
):
    df = fill_kline(raw_data)
    if df is None:
        return ErroText['notData']

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=df['日期'],
                open=df['开盘'],
                high=df['最高'],
                low=df['最低'],
                close=df['收盘'],
                increasing_line_color='red',
                decreasing_line_color='green',
                name='K线',
            ),
            go.Scatter(
                x=df['日期'],
                y=df['换手率'],
                mode='lines',
                line=dict(color='purple', width=4),
                yaxis='y2',
                name='换手率',
            ),
            # 添加5日均线
            go.Scatter(
                x=df['日期'],
                y=df['5日均线'],
                mode='lines',
                line=dict(color='orange', width=3),
                name='5日均线',
            ),
            # 添加10日均线
            go.Scatter(
                x=df['日期'],
                y=df['10日均线'],
                mode='lines',
                line=dict(color='blue', width=3),
                name='10日均线',
            ),
        ]
    )

    fig.update_layout(xaxis_rangeslider_visible=False)

    df['is_max'] = (
        df['换手率'] == df['换手率'].rolling(window=3, center=True).max()
    )
    max_turnovers = df[df['is_max'] & (df['换手率'] > 0)]

    # 添加所有最高点标记
    for _, row in max_turnovers.iterrows():
        fig.add_trace(
            go.Scatter(
                x=[row['日期']],
                y=[row['换手率']],
                mode='markers+text',
                text=[f'{row["换手率"] * 100:.2f}%'],
                textposition='top center',
                marker=dict(size=10, color='red'),
                showlegend=False,
                yaxis='y2',
            )
        )

    fig.update_layout(
        title=dict(
            text=raw_data['data']['name'],
            font=dict(size=80),
            y=0.98,
            x=0.5,
            xanchor='center',
            yanchor='top',
        ),
        xaxis=dict(
            title_font=dict(size=40),  # X轴标题字体大小
            tickfont=dict(size=40),  # X轴刻度标签字体大小
        ),
        yaxis=dict(
            title_font=dict(size=40),  # Y轴标题字体大小
            tickfont=dict(size=40),  # Y轴刻度标签字体大小
            title='价格',
        ),
        yaxis2=dict(
            title_font=dict(size=40),  # Y轴标题字体大小
            tickfont=dict(size=40),  # Y轴刻度标签字体大小
            title='换手率',
            overlaying='y',
            side='right',
            tickformat=".0%",
        ),
        legend=dict(
            title=dict(
                font=dict(
                    size=40,
                )
            )
        ),  # 设置图例标题的大小
        font=dict(size=40),  # 设置整个图表的字体大小
    )

    fig.update_xaxes(tickformat='%Y.%m')
    # fig.update_layout(width=10000)
    return fig


# 获取个股图形
async def to_single_fig(
    raw_data: Dict,
    sp: Optional[str] = None,
):
    logger.info('[SayuStock] 开始获取图形...')
    raw = raw_data['data']
    gained: float = raw['f170']
    price_histroy = raw_data['trends']
    stock_name = raw['f58']
    new_price = raw['f43']
    custom_info = int_to_percentage(gained)
    turnover_rate = raw['f168']
    total_amount = (
        number_to_chinese(raw['f48']) if isinstance(raw['f48'], float) else 0
    )

    '''
    result = {
        'MARKET_CAP': raw['f116'],  # 总市值
        'NEW_PRICE': new_price,  # 最新价
        'STOCK_NAME': stock_name,  # 名称
        'GAINED': gained,  # 涨幅
        'CUSTOM_INFO': custom_info,
        'PRICE_HISTORY': price_histroy,
        'TURNOVER_RATE': turnover_rate,
    }
    '''

    '''
    if not gained:
        return ErroText['notData']
    '''

    code_id = raw_data.get('file_name')
    if code_id:
        code_id = code_id.split('_')[0]
    # 遍历TIME_RANGE如果存在没有数据的时间则插入空数据
    full_data = []
    existing_times = set(item['datetime'] for item in price_histroy)
    ARRAY = get_trading_minutes(code_id)
    for time in ARRAY:
        if time in existing_times:
            full_data.append(
                next(
                    item for item in price_histroy if item['datetime'] == time
                )
            )
        else:
            full_data.append(
                {
                    'datetime': time,
                    'price': None,
                    'open': None,
                    'high': None,
                    'low': None,
                    'amount': None,
                    'money': None,
                    'avg_price': None,
                }
            )
    price_histroy = full_data

    price_history_pd = pd.DataFrame(
        {
            'datetime': [item['datetime'] for item in full_data],
            'price': [item['price'] for item in full_data],
            'money': [item['money'] for item in full_data],  # 新增 money 列
        }
    )

    price_history_pd['price'] = price_history_pd['price'].ffill()

    # 设置最大波动率
    open_price = raw['f60']
    max_price = price_history_pd['price'].max()
    min_price = price_history_pd['price'].min()
    max_fluctuation = max(
        (max_price - open_price) / open_price,
        (open_price - min_price) / open_price,
    )
    y_axis_max_price = open_price * (1 + max_fluctuation + 0.01)
    y_axis_min_price = open_price * (1 - max_fluctuation - 0.01)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,  # 共享X轴
        vertical_spacing=0.05,  # 子图间的垂直间距
        row_heights=[0.7, 0.3],  # 第一行（价格）占70%高度，第二行（量能）占30%
    )

    # 1. 添加价格折线图到第一行
    fig.add_trace(
        go.Scatter(
            x=price_history_pd['datetime'],
            y=price_history_pd['price'],
            mode='lines',
            name='Price',
            line=dict(width=3, color='white'),
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    # 2. 为量能柱状图生成颜色
    bar_colors = []
    prices = price_history_pd['price']

    if prices[0] is None:
        return ErroText['notOpen']

    for i in range(len(prices)):
        if i == 0:
            # 第一个数据点，可以与开盘价比较
            bar_colors.append('red' if prices[i] > open_price else 'green')
        else:
            # 与前一个数据点比较
            if prices[i] > prices[i - 1]:
                bar_colors.append('red')
            elif prices[i] < prices[i - 1]:
                bar_colors.append('green')
            else:
                bar_colors.append('grey')  # 如果价格不变，使用灰色

    # 3. 添加量能柱状图到第二行
    fig.add_trace(
        go.Bar(
            x=price_history_pd['datetime'],
            y=price_history_pd['money'],
            name='Volume',
            marker_color=bar_colors,  # 应用动态颜色
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    # --- 将原有的 Shape 添加到第一个子图中 ---
    fig.add_hrect(
        y0=open_price,
        y1=y_axis_max_price,
        fillcolor="red",
        opacity=0.2,
        layer="below",
        line_width=0,
    )

    # 绘制绿色区域 (开盘价之下)
    fig.add_hrect(
        y0=y_axis_min_price,
        y1=open_price,
        fillcolor="green",
        opacity=0.2,
        layer="below",
        line_width=0,
    )

    # 使用 add_hline 绘制横跨整个图表宽度的水平线
    fig.add_hline(
        y=open_price,
        line=dict(color="yellow", width=2, dash="dashdot"),
    )

    # 计算Y轴刻度
    tick_values = []
    tick_texts = []

    max_range_percent = max_fluctuation * 100
    if max_range_percent > 15:
        step = 2
    elif max_range_percent > 30:
        step = 5
    else:
        step = 1

    for i in range(
        int(-(max_fluctuation + 0.01) * 100),
        int((max_fluctuation + 0.01) * 100) + 1,
    ):
        if i % step == 0:
            price = open_price * (1 + i / 100)
            if y_axis_min_price <= price <= y_axis_max_price:
                tick_values.append(price)
                tick_texts.append(f'{i}%')

    title_str1 = f"{stock_name}  最新价：{new_price}"
    title_str = f"【{title_str1}】 开盘价：{open_price} 涨跌幅：{custom_info} 换手率 {turnover_rate}% 成交额 {total_amount}"

    # --- 更新整体布局和坐标轴 ---
    fig.update_layout(
        title=dict(
            text=title_str,
            font=dict(size=35),
            y=0.99,
            x=0.5,
            xanchor='center',
            yanchor='top',
        ),
        margin=dict(t=80, l=50, r=50, b=50),
        paper_bgcolor="black",
        plot_bgcolor="black",
        font=dict(color="white"),
        # 隐藏所有图例
        showlegend=False,
        # 移除X轴的滑块
        # xaxis_rangeslider_visible=False,
    )

    # 更新Y轴 (价格)
    fig.update_yaxes(
        title_text='价格',
        range=[y_axis_min_price, y_axis_max_price],
        showgrid=True,
        gridcolor='rgba(255,255,255,0.2)',
        tickvals=tick_values,
        ticktext=tick_texts,
        title_font=dict(size=30),
        tickfont=dict(size=26),
        row=1,
        col=1,
    )

    # 更新Y轴 (量能)
    fig.update_yaxes(
        title_text='量能',
        showgrid=False,
        title_font=dict(size=30),
        tickfont=dict(size=26),
        row=2,
        col=1,
    )

    # 更新X轴 (隐藏顶部的X轴刻度，只显示底部的)
    fig.update_xaxes(
        # showticklabels=False,
        # showgrid=False,
        dtick=60,
        row=1,
        col=1,
        title_font=dict(size=30),
        tickfont=dict(size=26),
    )
    fig.update_xaxes(
        title_text='时间',
        showgrid=False,
        dtick=15,  # 每15分钟一个刻度
        title_font=dict(size=30),
        tickfont=dict(size=26),
        row=2,
        col=1,
    )
    return fig


async def to_fig(
    raw_data: Dict,
    sector: Optional[str] = None,
    sp: Optional[str] = None,
    layer: int = 2,
):
    result = {}

    for i in raw_data['data']['diff']:
        if i['f14'].startswith(('ST', '*ST')):
            i['f100'] = 'ST'

        if layer == 1:
            i['f100'] = sector

        if i['f20'] != '-' and i['f100'] != '-' and i['f3'] != '-':
            # stock = {'市值': i['f20'], '股票名称': i['f14']}
            if i['f100'] not in result:
                result[i['f100']] = {
                    '总市值': i['f20'],
                    '个股': [i],
                    'name': [i['f14']],
                }
            else:
                if i['f14'] not in result[i['f100']]['name']:
                    result[i['f100']]['总市值'] += i['f20']
                    result[i['f100']]['个股'].append(i)
                    result[i['f100']]['name'].append(i['f14'])

    if sector is None:
        fit = 0.2
    elif sector and layer == 1 and len(result[sector]) > 100:
        scale = 1 - (len(result[sector]) - 100) * 0.7 / 500
        fit = max(0.3, min(scale, 1))
    else:
        fit = 1

    if fit != 1:
        for r in result:
            stock_item = result[r]['个股']
            sorted_stock = sorted(
                stock_item, key=lambda x: x['f20'], reverse=True
            )
            num_items = len(sorted_stock)
            num_to_extract = int(num_items * fit)
            subset_data = sorted_stock[:num_to_extract]
            result[r]['个股'] = subset_data

    sorted_result = dict(
        sorted(
            result.items(),
            key=lambda item: item[1]['总市值'],
            reverse=True,
        )
    )

    category = []
    stock_name = []
    values = []
    diff = []
    custom_info = []

    for r in sorted_result:
        if sector and sector not in r:
            continue
        for s in sorted_result[r]['个股']:
            if sp and s['f12'] not in sp:
                continue
            category.append(f'<b>{r}</b>')
            stock_name.append(s['f14'])
            values.append(s['f20'])
            _d: float = s['f3']
            diff.append(_d)
            d_str = '+' + str(_d) if _d >= 0 else str(_d)
            custom_info.append(f"{d_str}%")

    if not diff:
        return ErroText['notData']

    data = {
        "Category": category,
        "StockName": stock_name,
        "Values": values,
        "Diff": diff,
        "CustomInfo": custom_info,
    }

    df = pd.DataFrame(data)

    df = df.sort_values(by='Values', ascending=False, inplace=False)

    # 生成 Treemap
    fig = px.treemap(
        df,
        path=["Category", "StockName"],
        values="Values",  # 定义块的大小
        color="Diff",  # 根据数值上色
        color_continuous_scale=[
            [0, 'rgba(0, 255, 0, 1)'],  # 绿色，透明度1
            [0.5, 'rgba(61, 61, 59, 1)'],
            # [0.4, 'rgba(0, 255, 0, 1)'],
            # [0.6, 'rgba(255, 0, 0, 1)'],
            [1, 'rgba(255, 0, 0, 1)'],  # 红色，透明度1
        ],  # 渐变颜色
        color_continuous_midpoint=0,
        range_color=[-10, 10],  # 设置数值范围
        custom_data=["CustomInfo"],
        branchvalues="total",
    )

    # 控制显示内容
    fig.update_traces(
        marker=dict(
            cmin=-10,  # 设置最小值
            cmax=10,  # 设置最大值
        ),
        marker_pad=dict(
            l=5,
            r=5,
            b=5,
            t=60,
        ),
        textfont=dict(
            color="white",
        ),
        textfont_family='MiSans',
        textfont_weight=350,
        texttemplate="%{label}<br>%{customdata[0]}",
        # textinfo="label+text",
        textfont_size=50,  # 设置字体大小
        textposition="middle center",
    )

    fig.update_layout(
        # uniformtext=dict(minsize=30, mode='hide'),
        margin=dict(t=0, b=0, l=0, r=0),
        paper_bgcolor="black",
        plot_bgcolor="black",
        font=dict(color="white"),
        coloraxis_showscale=False,
    )
    return fig


async def render_html(
    market: str = '沪深A',
    sector: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> Union[str, Path]:
    _sp_str = None
    sp = None
    logger.info(f"[SayuStock] market: {market} sector: {sector}")

    if market == '沪深300':
        market = 'hs300'
    elif market == '1000':
        market = '中证1000'
    elif market == '中证2000':
        market = '2000'

    if sector != STOCK_SECTOR:
        if market in market_dict and 'b:' in market_dict[market]:
            sector = market
        elif market in bk_dict:
            sector = market

    if market in mdata:
        _sp_str = market
        sp = mdata[market]
        logger.info(f"[SayuStock] 触发SP数据{_sp_str}: {len(sp)}...")
        market = '沪深A'

    # 如果是个股错误
    if sector == STOCK_SECTOR and not market:
        return ErroText['notMarket']

    if not market:
        market = '沪深A'

    logger.info("[SayuStock] 开始获取数据...")

    # 对比个股 数据
    if sector == 'compare-stock':
        markets = market.split(' ')
        raw_datas: List[Dict] = []
        for m in markets:
            if m == 'A500':
                m = 'A500ETF'
            raw_data = await get_data(
                m,
                'single-stock-kline-111',
                start_time,
                end_time,
            )
            if isinstance(raw_data, str):
                return raw_data
            raw_datas.append(raw_data)

        st_f = start_time.strftime('%Y%m%d') if start_time else ''
        et_f = end_time.strftime('%Y%m%d') if end_time else ''
        _sp_str = f'compare-stock-{st_f}-{et_f}'
    # 其他数据
    else:
        raw_data = await get_data(market, sector)
        if raw_data is None:
            return '数据处理失败, 请检查后台...'
        elif isinstance(raw_data, str):
            return raw_data

    file = get_file(market, 'html', sector, _sp_str)
    # 检查当前目录下是否有符合条件的文件
    if file.exists():
        # 检查文件的修改时间是否在一分钟以内
        file_mod_time = datetime.fromtimestamp(file.stat().st_mtime)
        if datetime.now() - file_mod_time < timedelta(minutes=minutes):
            logger.info(
                f"[SayuStock] html文件在{minutes}分钟内，直接返回文件数据。"
            )
            return file

    # 个股
    if sector == STOCK_SECTOR:
        fig = await to_single_fig(raw_data)
    # 个股对比
    elif sector == 'compare-stock':
        fig = await to_compare_fig(raw_datas)
    # 个股 日k 年k
    elif sector and sector.startswith('single-stock-kline'):
        fig = await to_single_fig_kline(raw_data)
    # 大盘云图
    else:
        fig = await to_fig(
            raw_data,
            sector,
            sp,
            2 if market != sector else 1,
        )
    if isinstance(fig, str):
        return fig

    # fig.show()
    fig.write_html(file)
    return file


async def render_image(
    market: str = '沪深A',
    sector: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
):
    html_path = await render_html(
        market,
        sector,
        start_time,
        end_time,
    )

    if isinstance(html_path, str):
        return html_path

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        if (
            sector
            and sector.startswith('single-stock-kline')
            or sector == 'compare-stock'
        ):
            viewport = {
                "width": 4600,
                "height": 3000,
            }
            _scale = 1
        elif sector == STOCK_SECTOR:
            viewport = {
                "width": 4000,
                "height": 3000,
            }
            _scale = 1
        else:
            viewport = {
                "width": view_port,
                "height": view_port,
            }
            _scale = scale

        context = await browser.new_context(
            viewport=viewport,  # type: ignore
            device_scale_factor=_scale,
        )
        page = await context.new_page()
        await page.goto(html_path.absolute().as_uri())
        await page.wait_for_selector(".plot-container")
        png_bytes = await page.screenshot(type='png')
        await browser.close()
        return await convert_img(png_bytes)
        return await convert_img(png_bytes)
