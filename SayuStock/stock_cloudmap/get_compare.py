from typing import Dict, List

import plotly.graph_objects as go
from plotly.colors import qualitative

from .utils import fill_kline


async def to_compare_fig(raw_datas: List[Dict]):
    data = []
    colors = qualitative.Plotly * (len(raw_datas) // 10 + 1)
    for i, raw_data in enumerate(raw_datas):
        df = fill_kline(raw_data)
        data.append(
            go.Scatter(
                x=df['日期'],
                y=df['归一化'],
                mode='lines',
                line=dict(color=colors[i], width=4),
                yaxis='y2',
                name=f'{raw_data["data"]["name"]}',
            ),
        )

    fig = go.Figure(data=data)

    fig.update_layout(
        title=dict(
            text='对比图',
            font=dict(size=60),
            x=0.5,
            xanchor='center',
        ),
        xaxis=dict(
            title_font=dict(size=40),  # X轴标题字体大小
            tickfont=dict(size=40),  # X轴刻度标签字体大小
        ),
        yaxis=dict(
            title_font=dict(size=40),  # Y轴标题字体大小
            tickfont=dict(size=40),  # Y轴刻度标签字体大小
            title='归一化收盘价',
        ),
        legend=dict(
            title=dict(
                font=dict(
                    size=60,
                )
            )
        ),  # 设置图例标题的大小
        font=dict(size=60),  # 设置整个图表的字体大小
    )

    fig.update_xaxes(tickformat='%Y.%m')

    return fig
