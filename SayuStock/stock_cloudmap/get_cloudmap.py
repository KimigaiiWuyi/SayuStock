import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Union, Optional

import aiohttp
import aiofiles
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from gsuid_core.logger import logger
from playwright.async_api import async_playwright
from gsuid_core.utils.image.convert import convert_img

from ..utils.utils import get_file
from ..utils.request import get_code_id
from ..utils.resource_path import GN_BK_PATH
from ..stock_config.stock_config import STOCK_CONFIG
from ..utils.load_data import mdata, get_full_security_code
from ..utils.constant import (
    SP_STOCK,
    TIME_ARRAY,
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
}


async def load_data_from_file(file: Path):
    async with aiofiles.open(file, 'r', encoding='UTF-8') as f:
        return json.loads(await f.read())


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


async def get_data(
    market: str = '沪深A',
    sector: Optional[str] = None,
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

    if market in SP_STOCK:
        fields = 'f58,f57,f107,f43,f59,f169,f170,f152'
        url = 'https://push2.eastmoney.com/api/qt/stock/get'
        params.append(('secid', SP_STOCK[market]))
    elif sector == STOCK_SECTOR:
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
    else:
        url = 'http://push2.eastmoney.com/api/qt/clist/get'
        if market in market_dict:
            fs = market_dict[market]
        else:
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

    if is_loop and resp['data'] and len(resp['data']['diff']) >= 200:
        for pn in range(2, 1000):
            params.remove(('pn', str(pn - 1)))
            params.append(('pn', str(pn)))
            resp2 = await req(url, params)
            if resp2['data']:
                resp['data']['diff'].extend(resp2['data']['diff'])
            else:
                break
            if len(resp2['data']['diff']) < 200:
                break

    logger.info("[SayuStock] 数据获取完成...")

    # 处理获取个股数据错误
    if sector == STOCK_SECTOR and resp['data'] is None:
        return ErroText['notStock']

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

    # 处理个股折线数据
    secid = next((value for key, value in params if key == 'secid'), None)
    if sector == STOCK_SECTOR and secid:
        trends = await get_single_fig_data(secid)
        if isinstance(trends, str):
            return resp
        resp['trends'] = trends
    return resp


def int_to_percentage(value: int) -> str:
    sign = '+' if value >= 0 else ''
    return f"{sign}{value:.2f}%"


# 获取个股图形
async def to_single_fig(
    raw_data: Dict,
    sp: Optional[str] = None,
):
    logger.info('[SayuStock] 开始获取图形...')
    raw = raw_data['data']
    gained = raw['f170']
    price_histroy = raw_data['trends']
    stock_name = raw['f58']
    new_price = raw['f43']
    custom_info = int_to_percentage(gained)
    turnover_rate = raw['f168']

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

    if not gained:
        return ErroText['notData']

    # 遍历TIME_RANGE如果存在没有数据的时间则插入空数据
    full_data = []
    existing_times = set(item['datetime'] for item in price_histroy)
    for time in TIME_ARRAY:
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
        }
    )

    # 设置最大波动率
    open_price = price_history_pd['price'].iloc[0]
    max_price = price_history_pd['price'].max()
    min_price = price_history_pd['price'].min()
    max_fluctuation = max(
        (max_price - open_price) / open_price,
        (open_price - min_price) / open_price,
    )
    max_price = open_price * (1 + max_fluctuation + 0.01)
    min_price = open_price * (1 - max_fluctuation - 0.01)

    fig = px.line(
        price_histroy,
        x="datetime",
        y="price",
        # text='price',  # 数据点显示值
        line_shape='linear',  # 共有6种插值方式：
        # 'linear'、'spline'、'hv'、'vh'、'hvh'和'vhv'
    )

    fig = go.Figure(fig)
    fig.update_traces(line=dict(width=5, color='white'))  # 使用白色线条

    # 添加背景颜色和虚
    fig.add_shape(
        type="rect",
        x0=price_history_pd['datetime'].min(),
        x1=price_history_pd['datetime'].max(),
        y0=open_price,
        y1=max_price,
        fillcolor="red",
        opacity=0.2,
        layer="below",
        line_width=0,
    )

    fig.add_shape(
        type="rect",
        x0=price_history_pd['datetime'].min(),
        x1=price_history_pd['datetime'].max(),
        y0=min_price,
        y1=open_price,
        fillcolor="green",
        opacity=0.2,
        layer="below",
        line_width=0,
    )

    # 添加0轴线
    fig.add_shape(
        type="line",
        x0=price_history_pd['datetime'].min(),
        x1=price_history_pd['datetime'].max(),
        y0=open_price,
        y1=open_price,
        line=dict(
            color="yellow",
            width=3,
            dash="dashdot",
        ),
    )

    # 计算以open_price为基准每1%为单位到max_price和min_price
    tick_values = []
    tick_texts = []
    for i in range(
        int(-(max_fluctuation + 0.01) * 100),
        int((max_fluctuation + 0.01) * 100) + 1,
    ):
        if i % 1 == 0:
            price = open_price * (1 + i / 100)
            if min_price <= price <= max_price:
                tick_values.append(price)
                tick_texts.append(f'{i}%')

    title_str1 = f"{stock_name}  最新价：{new_price}"
    title_str = f"【{title_str1}】 开盘价：{open_price} 跌涨幅：{custom_info} 换手率 {turnover_rate}%"  # noqa:E501

    # fig.update_layout(
    #     yaxis=dict(
    #         title='价格',
    #         range=[min_price, max_price],
    #         showgrid=True,
    #         tickvals=tick_values,
    #         ticktext=tick_texts
    #     ),
    #     xaxis=dict(
    #         title='时间',
    #         showgrid=False,
    #         dtick=15,
    #     ),
    #     title=title_str,
    # )
    # 修改y轴，x轴，title文字字号

    fig.update_layout(
        yaxis=dict(
            title='价格',
            range=[min_price, max_price],
            showgrid=True,
            tickvals=tick_values,
            ticktext=tick_texts,
            title_font=dict(size=36),  # 修改y轴标题字号
            tickfont=dict(size=36),  # 修改y轴刻度字号
        ),
        xaxis=dict(
            title='时间',
            showgrid=False,
            dtick=15,
            title_font=dict(size=36),  # 修改x轴标题字号
            tickfont=dict(size=36),  # 修改x轴刻度字号
        ),
        title=dict(
            text=title_str,
            font=dict(size=50),  # 修改标题字号
            y=0.985,
            x=0.5,
            xanchor='center',
            yanchor='top',
        ),
        # width=4800,
        # height=4800,
        margin=dict(t=100, l=50, r=50, b=50),
    )

    # 修改背景颜色
    fig.update_layout(
        paper_bgcolor="black",
        plot_bgcolor="black",
        font=dict(color="white"),
        coloraxis_showscale=False,
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
            [0.49, 'rgba(0, 255, 0, 0.05)'],
            [0.51, 'rgba(255, 0, 0, 0.05)'],
            [1, 'rgba(255, 0, 0, 1)'],  # 红色，透明度1
        ],  # 渐变颜色
        range_color=[-10, 10],  # 设置数值范围
        custom_data=["CustomInfo"],
        branchvalues="total",
    )

    # 控制显示内容
    fig.update_traces(
        marker=dict(
            colorscale=[
                [0, 'rgba(10, 204, 49, 1)'],  # 绿色，透明度1
                [0.49, 'rgba(10, 204, 49, 0.05)'],
                [0.51, 'rgba(238, 55, 58, 0.05)'],
                [1, 'rgba(238, 55, 58, 1)'],  # 红色，透明度1
            ],
            cmin=-10,  # 设置最小值
            cmax=10,  # 设置最大值
            cornerradius=5,
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
) -> Union[str, Path]:
    _sp_str = None
    sp = None
    logger.info(f"[SayuStock] market: {market} sector: {sector}")

    if market == '沪深300':
        market = '300'
    elif market == '1000':
        market = '中证1000'
    elif market == '中证2000':
        market = '2000'

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

    if sector == STOCK_SECTOR:
        fig = await to_single_fig(raw_data)
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
):
    html_path = await render_html(market, sector)
    if isinstance(html_path, str):
        return html_path

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={
                "width": view_port,
                "height": view_port,
            },
            device_scale_factor=scale,
        )
        page = await context.new_page()
        await page.goto(html_path.absolute().as_uri())
        await page.wait_for_selector(".plot-container")
        png_bytes = await page.screenshot(type='png')
        await browser.close()
        return await convert_img(png_bytes)
