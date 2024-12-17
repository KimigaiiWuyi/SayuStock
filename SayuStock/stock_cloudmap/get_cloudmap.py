import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Union, Optional

import aiohttp
import aiofiles
import pandas as pd
import plotly.express as px
from gsuid_core.logger import logger
from playwright.async_api import async_playwright
from gsuid_core.utils.image.convert import convert_img

from ..utils.load_data import mdata
from ..stock_config.stock_config import STOCK_CONFIG
from ..utils.resource_path import DATA_PATH, GN_BK_PATH
from ..utils.constant import (
    SP_STOCK,
    bk_dict,
    market_dict,
    request_header,
    trade_detail_dict,
)

view_port: int = STOCK_CONFIG.get_config('mapcloud_viewport').data
scale: int = STOCK_CONFIG.get_config('mapcloud_scale').data
minutes: int = STOCK_CONFIG.get_config('mapcloud_refresh_minutes').data

GK_DATA = {}


async def load_data_from_file(file: Path):
    async with aiofiles.open(file, 'r', encoding='UTF-8') as f:
        return json.loads(await f.read())


async def load_bk_data():
    global GK_DATA
    if GN_BK_PATH.exists():
        GK_DATA = await load_data_from_file(GN_BK_PATH)
    return GK_DATA


def get_file(
    market: str,
    suffix: str,
    sector: Optional[str] = None,
    sp: Optional[str] = None,
):
    """生成以当前时间命名的文件名。"""
    current_time = datetime.now()
    a = f'{market}_{sector}_{sp}_data'
    return DATA_PATH / f"{a}_{current_time.strftime('%Y%m%d_%H%M')}.{suffix}"


async def get_data(market: str = '沪深A') -> Union[Dict, str]:
    market = market.upper()
    if not market:
        market = '沪深A'

    file = get_file(market, 'json')

    params = [
        ('pn', '1'),
        ('pz', '1000000'),
        ('po', '1'),
        ('np', '1'),
        ('fltt', '2'),
        ('invt', '2'),
        ('fid', 'f3'),
    ]

    if market in SP_STOCK:
        fields = 'f58,f57,f107,f43,f59,f169,f170,f152'
        url = 'https://push2.eastmoney.com/api/qt/stock/get'
        params.append(('secid', SP_STOCK[market]))
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
                return '❌未找到对应板块, 请重新输入\n📄例如: \n大盘云图沪深A\n大盘云图创业板 \n等等...'

        fields = ",".join(trade_detail_dict.keys())
        params.append(('fs', fs))
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

    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            headers=request_header,
            params=params,
        ) as response:
            resp = await response.json()

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
        return '❌不存在该板块或市场, 暂无数据...'

    data = {
        "Category": category,
        "StockName": stock_name,
        "Values": values,
        "Diff": diff,
        "CustomInfo": custom_info,
    }

    async with aiofiles.open('dd.json', 'w', encoding='UTF-8') as f:
        await f.write(json.dumps(data, ensure_ascii=False, indent=4))

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

    if not market:
        market = '沪深A'

    raw_data = await get_data(market)
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
