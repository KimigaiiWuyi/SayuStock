import sys
import asyncio
from pathlib import Path
from contextlib import contextmanager
from typing import Dict, Optional, cast

import pandas as pd
import plotly.graph_objects as go
from gsuid_core.logger import logger

from ..utils.constant import ErroText
from ..utils.stock.request import get_gg
from ..utils.image import render_image_by_pw
from ..utils.stock.utils import async_file_cache
from ..utils.stock.request_utils import get_code_id
from ..utils.load_data import get_full_security_code


@contextmanager
def temp_sys_path(path: str):
    """临时添加 sys.path，退出时恢复"""
    old_path = list(sys.path)
    sys.path.insert(0, path)
    try:
        yield
    finally:
        sys.path[:] = old_path


base_dir = Path(__file__).parent  # 当前 stock_ai 目录
kronos_dir = base_dir.parent / 'Kronos'

# 临时添加 Kronos 路径进行导入
with temp_sys_path(str(kronos_dir)):
    from ..Kronos.model import Kronos, KronosPredictor, KronosTokenizer


def fill_kline_by_kronos(raw_data: Dict) -> Optional[pd.DataFrame]:
    """将 Kronos 返回的 kline 数据转换为标准 DataFrame 格式。
    返回 None 或 pd.DataFrame（列：timestamps, open, high, low, close, volume, amount）
    """

    if not raw_data.get('data') or not raw_data['data'].get('klines'):
        return None

    # 原始头（对应每个逗号分隔字段）
    headers = [
        'date',
        'open',
        'close',
        'high',
        'low',
        'volume',
        'amount',
        'amplitude',
        'chg_percent',
        'chg_amount',
        'turnover_rate',
    ]

    # 解析数据（每行都是逗号分隔）
    rows = [line.split(',') for line in raw_data['data']['klines']]
    df = pd.DataFrame(rows, columns=headers)  # type: ignore

    # 强制转换数值列
    numeric_cols = ['open', 'close', 'high', 'low', 'volume', 'amount']
    # 如果某些列可能缺失，建议先过滤存在的列：
    existing_numeric_cols = [c for c in numeric_cols if c in df.columns]
    df[existing_numeric_cols] = df[existing_numeric_cols].astype(float)

    # 转换日期
    df['date'] = pd.to_datetime(df['date'])

    # 统一命名
    df = df.rename(columns={'date': 'timestamps'})

    # 要返回的列顺序（保证这些列在 df 中存在）
    final_cols = [
        'timestamps',
        'open',
        'high',
        'low',
        'close',
        'volume',
        'amount',
    ]
    final_cols = [c for c in final_cols if c in df.columns]

    final_df = df.loc[:, final_cols].copy()
    final_df = cast(pd.DataFrame, final_df)

    return final_df


async def draw_ai_kline_with_forecast(market: str):
    logger.info(f'[SayuStock] get_single_fig_data code: {market}')

    sec_id_data = await get_code_id(market)
    if sec_id_data is None:
        return ErroText['notStock']

    sec_id = get_full_security_code(sec_id_data[0])
    if sec_id is None:
        return ErroText['notStock']

    fig = await _draw_ai_kline_with_forecast(sec_id)

    if isinstance(fig, str):
        return fig
    elif isinstance(fig, Path):
        return await render_image_by_pw(fig, 4000, 2000, 0)
    else:
        return '出现了未知错误。'


@async_file_cache(
    market='{sec_id}',
    sector='single-stock-ai',
    suffix='html',
    minutes=150,
)
async def _draw_ai_kline_with_forecast(sec_id: str):
    raw_data = await get_gg(sec_id, 'single-stock-kline-101')
    if isinstance(raw_data, str):
        return raw_data

    df = fill_kline_by_kronos(raw_data)
    if df is None or df.empty:
        return "无有效K线数据"

    fig = await asyncio.to_thread(gdf, df, raw_data)
    return fig


def gdf(df: pd.DataFrame, raw_data: Dict):
    tokenizer = KronosTokenizer.from_pretrained(
        "NeoQuasar/Kronos-Tokenizer-base"
    )
    model = Kronos.from_pretrained("NeoQuasar/Kronos-base")
    predictor = KronosPredictor(
        model, tokenizer, device="cpu", max_context=512
    )

    total_len = len(df)
    if total_len == 0:
        return ErroText['notData']

    max_lookback = 400
    lookback = min(max_lookback, total_len - 1)
    pred_len = 30

    x_df = df.iloc[:lookback, :][
        ['open', 'high', 'low', 'close', 'volume', 'amount']
    ].reset_index(drop=True)
    x_timestamp_ser = df.iloc[:lookback]['timestamps'].reset_index(drop=True)

    timestamps = df['timestamps']
    if len(timestamps) >= 2:
        freq = timestamps.iloc[-1] - timestamps.iloc[-2]
    else:
        freq = pd.Timedelta(days=1)

    last_timestamp = timestamps.iloc[-1]
    y_timestamp_ser = pd.date_range(
        start=last_timestamp + freq, periods=pred_len, freq=freq
    )
    y_timestamp_ser = pd.Series(y_timestamp_ser)

    if x_df.shape[0] != len(x_timestamp_ser):
        msg = f"长度不匹配: x_df rows={x_df.shape[0]} vs x_timestamp={len(x_timestamp_ser)}"
        logger.error(msg)
        raise RuntimeError(msg)

    if len(y_timestamp_ser) != pred_len:
        msg = f"预测时间戳长度不等于 pred_len: len(y_timestamp)={len(y_timestamp_ser)} vs pred_len={pred_len}"
        logger.warning(msg + "，将以实际长度为准并调整 pred_len。")
        pred_len = len(y_timestamp_ser)

    try:
        pred_df = predictor.predict(
            df=x_df,
            x_timestamp=x_timestamp_ser,
            y_timestamp=y_timestamp_ser,
            pred_len=pred_len,
            T=1.0,
            top_p=0.9,
            sample_count=1,
        )

        # Kronos返回的DataFrame通常以y_timestamp为index，需要显式恢复为列
        if pred_df.index.name is None or pred_df.index.name != 'timestamps':
            pred_df = pred_df.copy()
            pred_df['timestamps'] = y_timestamp_ser.values
        else:
            pred_df = pred_df.reset_index()

    except Exception as e:
        logger.exception(
            "模型预测失败。x_df.shape=%s, x_stamp.shape=%s, y_stamp.shape=%s, pred_len=%s",
            x_df.shape,
            getattr(x_timestamp_ser, 'shape', None),
            getattr(y_timestamp_ser, 'shape', None),
            pred_len,
        )
        raise

    pred_df['is_forecast'] = True
    df['is_forecast'] = False

    merged_df = pd.concat([df, pred_df], ignore_index=True)

    fig = go.Figure()

    hist_df = merged_df[~merged_df['is_forecast']]
    fig.add_trace(
        go.Candlestick(
            x=hist_df['timestamps'],
            open=hist_df['open'],
            high=hist_df['high'],
            low=hist_df['low'],
            close=hist_df['close'],
            increasing_line_color='red',
            decreasing_line_color='green',
            name='历史K线',
        )
    )

    forecast_df = merged_df[merged_df['is_forecast']]
    if not forecast_df.empty:
        fig.add_trace(
            go.Candlestick(
                x=forecast_df['timestamps'],
                open=forecast_df['open'],
                high=forecast_df['high'],
                low=forecast_df['low'],
                close=forecast_df['close'],
                increasing_line_color='orange',
                decreasing_line_color='blue',
                name='预测K线',
            )
        )

    fig.update_layout(
        title=dict(
            text=f"{raw_data['data'].get('name', 'K线预测')}",
            font=dict(size=40),
            y=0.95,
            x=0.5,
            xanchor='center',
            yanchor='top',
        ),
        xaxis=dict(
            title="时间",
            title_font=dict(size=20),
            tickfont=dict(size=18),
        ),
        yaxis=dict(
            title="价格",
            title_font=dict(size=20),
            tickfont=dict(size=18),
        ),
        xaxis_rangeslider_visible=False,
        legend=dict(
            font=dict(size=18),
            orientation='h',
            yanchor='bottom',
            y=1.02,
            xanchor='right',
            x=1,
        ),
        template="plotly_white",
    )

    fig.update_xaxes(tickformat='%Y-%m-%d')
    return fig
