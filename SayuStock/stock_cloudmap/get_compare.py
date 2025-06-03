from typing import Dict, List

import plotly.graph_objects as go
from plotly.colors import qualitative

from .utils import fill_kline


async def to_compare_fig(raw_datas: List[Dict]):
    data = []
    colors = qualitative.Plotly * (len(raw_datas) // 10 + 1)
    for i, raw_data in enumerate(raw_datas):
        df = fill_kline(raw_data)
        if df is None:
            continue

        trace_name = f'{raw_data.get("data", {}).get("name", f"Trace {i}")}'
        legend_group_name = f'legend_group_{trace_name}_{i}'

        data.append(
            go.Scatter(
                x=df['日期'],
                y=df['归一化'],
                mode='lines',
                line=dict(color=colors[i], width=4),
                # yaxis='y2',
                name=trace_name,
                legendgroup=legend_group_name,
                showlegend=False,
            )
        )

        data.append(
            go.Scatter(
                x=[None],
                y=[None],
                mode='markers',
                marker=dict(
                    color=colors[i],
                    symbol='square',
                    size=200,
                ),
                # yaxis='y2',
                name=trace_name,
                legendgroup=legend_group_name,
                showlegend=True,
            )
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
            title='',
        ),
        legend=dict(
            itemsizing='trace',
            title=dict(
                font=dict(
                    size=80,
                )
            ),
            font=dict(size=60),
        ),  # 设置图例标题的大小
        font=dict(size=60),  # 设置整个图表的字体大小
    )

    fig.update_xaxes(tickformat='%Y.%m')
    fig.update_yaxes(tickformat='.0%')

    return fig
