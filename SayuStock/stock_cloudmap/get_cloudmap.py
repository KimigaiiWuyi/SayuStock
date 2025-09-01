import math
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Union, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from gsuid_core.logger import logger
from plotly.subplots import make_subplots
from playwright.async_api import async_playwright
from gsuid_core.utils.image.convert import convert_img

from .utils import fill_kline
from .get_compare import to_compare_fig
from ..utils.stock.utils import get_file
from ..utils.time_range import get_trading_minutes
from ..stock_config.stock_config import STOCK_CONFIG
from ..utils.constant import ErroText, bk_dict, market_dict
from ..utils.stock.request import get_gg, get_vix, get_hotmap, get_mtdata
from ..utils.utils import get_vix_name, int_to_percentage, number_to_chinese

view_port: int = STOCK_CONFIG.get_config('mapcloud_viewport').data
scale: int = STOCK_CONFIG.get_config('mapcloud_scale').data


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

    # price_history_pd['price'] = price_history_pd['price'].fillna(None)

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
    market: str,
    sector: Optional[str] = None,
    layer: int = 2,
):
    '''
    layer = 2 是按照F100分类，大盘云图

    layer = 1 就全部都在一起，概念云图
    '''
    all_stocks = []
    for item in raw_data.get('data', {}).get('diff', []):
        if (
            item.get('f20') == '-'
            or item.get('f100') == '-'
            or item.get('f3') == '-'
        ):
            continue

        category_name = item['f100']
        if item['f14'].startswith(('ST', '*ST')):
            category_name = 'ST'

        all_stocks.append(
            {
                'category': category_name,
                'name': item['f14'],
                'value': item['f20'],
                'diff_val': item['f3'],
                'code': item['f12'],
            }
        )

    if not all_stocks:
        return ErroText['notData']

    grouped_by_category = defaultdict(list)
    for stock in all_stocks:
        grouped_by_category[stock['category']].append(stock)

    final_stock_list = []

    if market == '大盘云图' or market == '概念云图':
        categories_to_process = list(grouped_by_category.keys())
    elif sector in grouped_by_category:
        categories_to_process = [sector]
    else:
        for i in grouped_by_category.keys():
            if sector in i:
                categories_to_process = [i]
                break
        else:
            return ErroText['notData']

    for cat_name in categories_to_process:
        stock_items = grouped_by_category[cat_name]
        num_items = len(stock_items)  # 获取当前行业的股票总数
        if layer == 1:
            fit = 1
            num_to_extract = num_items
        else:
            if num_items <= 40:
                fit = 0.5  # 总数40以内，计划显示50%
            elif num_items <= 100:
                fit = 0.4  # 40到100之间，计划显示40%
            else:
                fit = 0.3  # 超过100，计划显示30%

            ideal_count = math.ceil(num_items * fit)
            clamped_count = max(5, min(ideal_count, 20))
            num_to_extract = min(clamped_count, num_items)

        sorted_stocks = sorted(
            stock_items, key=lambda x: x['value'], reverse=True
        )
        subset_data = sorted_stocks[:num_to_extract]

        final_stock_list.extend(subset_data)

    if not final_stock_list:
        return ErroText['notData']

    # 步骤 4, 5, 6: 创建DataFrame并返回指定格式 (此部分不变)
    df = pd.DataFrame(final_stock_list)
    df = df.sort_values(by='value', ascending=False)

    category = ('<b>' + df['category'] + '</b>').tolist()
    stock_name = df['name'].tolist()
    values = df['value'].tolist()
    diff = df['diff_val'].tolist()
    custom_info = (
        df['diff_val']
        .apply(lambda d: f"+{d}%" if d >= 0 else f"{d}%")
        .tolist()
    )

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
    logger.info(f"[SayuStock] market: {market} sector: {sector}")

    if sector != 'single-stock':
        if market in market_dict and 'b:' in market_dict[market]:
            sector = market
        elif market in bk_dict:
            sector = market

    # 如果是个股错误
    if sector == 'single-stock' and not market:
        return ErroText['notMarket']

    if not market:
        market = '沪深A'

    logger.info("[SayuStock] 开始获取数据...")

    # 对比个股 数据
    if market == '大盘云图':
        raw_data = await get_hotmap()
        # raw_data = await get_mtdata('沪深A', True, 1, 100)
    elif market == '行业云图':
        raw_data = await get_hotmap()
    elif market == '概念云图':
        if sector:
            raw_data = await get_mtdata(sector, True, 1, 100)
        else:
            raw_data = '概念云图需要后跟概念类型, 例如： 概念云图 华为欧拉'
    elif sector and sector.startswith('single-stock-kline'):
        raw_data = await get_gg(
            market,
            sector,
            start_time,
            end_time,
        )
    elif sector == 'compare-stock':
        markets = market.split(' ')
        raw_datas: List[Dict] = []
        for m in markets:
            if m == 'A500':
                m = 'A500ETF'
            raw_data = await get_gg(
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
    elif sector == 'single-stock':
        m = get_vix_name(market)
        if m is None:
            raw_data = await get_gg(
                market, 'single-stock', start_time, end_time
            )
        else:
            raw_data = await get_vix(m)
    else:
        raw_data = await get_mtdata(market)

    if isinstance(raw_data, str):
        return raw_data

    file = get_file(market, 'html', sector, _sp_str)
    if file.exists():
        minutes = STOCK_CONFIG.get_config('mapcloud_refresh_minutes').data
        file_mod_time = datetime.fromtimestamp(file.stat().st_mtime)
        if datetime.now() - file_mod_time < timedelta(minutes=minutes):
            logger.info(
                f"[SayuStock] html文件在{minutes}分钟内，直接返回文件数据。"
            )
            return file

    # 个股
    if sector == 'single-stock':
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
            market,
            sector,
            2 if sector == '大盘云图' else 1,
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
        elif sector == 'single-stock':
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
