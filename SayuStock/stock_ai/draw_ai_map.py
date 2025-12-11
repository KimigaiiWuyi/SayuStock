import sys
import asyncio
from typing import Dict, Union, Optional, cast
from pathlib import Path
from contextlib import contextmanager

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from tqdm import trange

from gsuid_core.bot import Bot
from gsuid_core.logger import logger

from ..utils.image import render_image_by_pw
from ..utils.constant import ErroText
from ..utils.load_data import get_full_security_code
from ..utils.stock.utils import async_file_cache
from ..utils.stock.request import get_gg
from ..utils.stock.request_utils import get_code_id

NOW_QUEUE = []


@contextmanager
def temp_sys_path(path: str):
    """临时添加 sys.path，退出时恢复"""
    old_path = list(sys.path)
    sys.path.insert(0, path)
    try:
        yield
    finally:
        sys.path[:] = old_path


base_dir = Path(__file__).parent
kronos_dir = base_dir.parent / "Kronos"

# 临时添加 Kronos 路径进行导入
with temp_sys_path(str(kronos_dir)):
    from ..Kronos.model import Kronos, KronosPredictor, KronosTokenizer


def fill_kline_by_kronos(raw_data: Dict) -> Optional[pd.DataFrame]:
    """将 Kronos 返回的 kline 数据转换为标准 DataFrame 格式。
    返回 None 或 pd.DataFrame（列：timestamps, open, high, low, close, volume, amount）
    """

    if not raw_data.get("data") or not raw_data["data"].get("klines"):
        return None

    # 原始头（对应每个逗号分隔字段）
    headers = [
        "date",
        "open",
        "close",
        "high",
        "low",
        "volume",
        "amount",
        "amplitude",
        "chg_percent",
        "chg_amount",
        "turnover_rate",
    ]

    # 解析数据（每行都是逗号分隔）
    rows = [line.split(",") for line in raw_data["data"]["klines"]]
    df = pd.DataFrame(rows, columns=headers)  # type: ignore

    # 强制转换数值列
    numeric_cols = ["open", "close", "high", "low", "volume", "amount"]
    # 如果某些列可能缺失，建议先过滤存在的列：
    existing_numeric_cols = [c for c in numeric_cols if c in df.columns]
    df[existing_numeric_cols] = df[existing_numeric_cols].astype(float)

    # 转换日期
    df["date"] = pd.to_datetime(df["date"])

    # 统一命名
    df = df.rename(columns={"date": "timestamps"})

    # 要返回的列顺序（保证这些列在 df 中存在）
    final_cols = [
        "timestamps",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    ]
    final_cols = [c for c in final_cols if c in df.columns]

    final_df = df.loc[:, final_cols].copy()
    final_df = cast(pd.DataFrame, final_df)

    return final_df


async def draw_ai_kline_with_forecast(market: str, bot: Bot):
    logger.info(f"[SayuStock] get_single_fig_data code: {market}")

    sec_id_data = await get_code_id(market)
    if sec_id_data is None:
        return ErroText["notStock"]

    sec_id = get_full_security_code(sec_id_data[0])
    if sec_id is None:
        return ErroText["notStock"]

    if sec_id in NOW_QUEUE:
        return "当前股票已在预测队列中，请稍后..."

    if NOW_QUEUE:
        return f"当前队列中还有{len(NOW_QUEUE)}只股票在预测中，请稍后提交..."

    await bot.send("[SayuStock] 模型预测中，预计将会持续3分钟，请稍后...")
    NOW_QUEUE.append(sec_id)
    try:
        fig = await _draw_ai_kline_with_forecast(sec_id)
    except Exception as e:
        logger.error(f"[SayuStock] 模型预测出现错误: {e}")
        return f"模型预测出现错误: {e}"
    finally:
        NOW_QUEUE.remove(sec_id)

    if isinstance(fig, str):
        return fig
    elif isinstance(fig, Path):
        return await render_image_by_pw(fig, 4000, 2000, 0)
    else:
        return "出现了未知错误。"


@async_file_cache(
    market="{sec_id}",
    sector="single-stock-ai",
    suffix="html",
    minutes=150,
)
async def _draw_ai_kline_with_forecast(sec_id: str):
    raw_data = await get_gg(sec_id, "single-stock-kline-30")
    if isinstance(raw_data, str):
        return raw_data

    df = fill_kline_by_kronos(raw_data)
    if df is None or df.empty:
        return "无有效K线数据"

    fig = await asyncio.to_thread(gdf, df, raw_data)
    return fig


def generate_trading_times(
    start: pd.Timestamp,
    periods: int,
    freq: Union[str, pd.Timedelta] = "1H",
    *,
    # 可选：交易日的时间段（小时小数），默认简单时段 9:30-15:00（含午盘）
    trading_intervals: list[tuple[float, float]] = [(9.5, 11.5), (13.0, 15.0)],
    skip_weekends: bool = True,
) -> pd.DatetimeIndex:
    """
    从 start 开始，按步长 freq 生成 `periods` 个时间戳，但只保留交易时段（按 trading_intervals）
    支持 freq 为 str（如 "1H", "30min"）或 pd.Timedelta。
    如果传入 freq 为 NaT 或无效，会抛出 ValueError。
    """
    # 规范化 freq 为 Timedelta
    if isinstance(freq, str):
        try:
            freq_td = pd.Timedelta(freq)
        except Exception as e:
            raise ValueError(f"无法解析字符串 freq={freq!r}: {e}") from e
    else:
        freq_td = freq

    # 检查 NaT / 非 Timedelta
    if pd.isna(freq_td):
        raise ValueError("freq 是 NaT 或无效，请传入有效的 str 或 pd.Timedelta（例如 '1H' 或 pd.Timedelta(hours=1)）")
    if not isinstance(freq_td, pd.Timedelta):
        # 例如用户传入 numpy.timedelta64 等
        try:
            freq_td = pd.Timedelta(freq_td)
        except Exception as e:
            raise ValueError(f"无法转换 freq 为 Timedelta: {e}") from e

    times = []
    curr = pd.to_datetime(start)
    # 安全上限，避免死循环（例如 freq 非交易时间粒度太小）
    max_iters = periods * 1000 + 10000

    iters = 0
    while len(times) < periods and iters < max_iters:
        iters += 1
        curr = curr + freq_td

        # 跳过周末
        if skip_weekends and curr.weekday() >= 5:
            continue

        # 检查是否在任一交易区间内（小时小数）
        t_float = curr.hour + curr.minute / 60.0 + curr.second / 3600.0
        in_trade = any(start_h <= t_float <= end_h for (start_h, end_h) in trading_intervals)
        if not in_trade:
            continue

        times.append(curr)

    if len(times) < periods:
        raise RuntimeError(
            f"无法生成足够的交易时间戳 (requested {periods}, got {len(times)}). "
            "请检查 freq、trading_intervals 是否合理，或增大 max_iters。"
        )

    return pd.DatetimeIndex(times)


def gdf(df: pd.DataFrame, raw_data: Dict):
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-mini")
    predictor = KronosPredictor(
        model,
        tokenizer,
        device="cpu",
        max_context=512,
    )

    total_len = len(df)
    if total_len == 0:
        return ErroText["notData"]

    sample_count = 5  # 预测采样次数
    max_lookback = 470  # 最大回看窗口

    timestamps = pd.to_datetime(df["timestamps"], unit="ms")
    df["timestamps"] = timestamps.sort_values()

    if len(timestamps) >= 2:
        inferred_freq = timestamps.iloc[-1] - timestamps.iloc[-2]
    else:
        inferred_freq = pd.Timedelta(days=1)

    if bool(pd.isna(inferred_freq)):
        inferred_freq = pd.Timedelta(days=1)
    elif not isinstance(inferred_freq, pd.Timedelta):
        inferred_freq = pd.Timedelta(inferred_freq)

    inferred_freq = cast(pd.Timedelta, inferred_freq)
    freq_minutes = inferred_freq.total_seconds() / 60.0

    logger.info(f"[SayuStock] 股票K线周期: {freq_minutes}")

    if freq_minutes >= 1440 - 1:
        freq_label = "1D"
    elif freq_minutes >= 60:
        freq_label = f"{int(freq_minutes / 60)}H"
    else:
        # 对于例如 30.0 会得到 "30min"
        freq_label = f"{int(freq_minutes)}min"

    # --- 根据周期调整预测长度 ---
    if "min" in freq_label:
        pred_len = 30
    elif "H" in freq_label:
        pred_len = 30
    else:
        pred_len = 30

    # 检查数据是否足够进行回测
    if total_len < pred_len + 20:  # 至少需要 pred_len 天用于回测，以及一些（如20天）数据用于预测
        return "数据量不足，无法进行回测和预测"

    # 定义回测开始的索引
    backtest_start_index = total_len - pred_len

    # ---------------------------------
    # --- 2. 准备和执行 "回测" (Prediction 1) ---
    # ---------------------------------
    logger.info("[SayuStock] 正在执行回测预测...")

    # 回测的输入数据：回测期之前的数据
    lookback_backtest = min(max_lookback, backtest_start_index - 1)
    backtest_input_start_index = backtest_start_index - lookback_backtest
    backtest_input_end_index = backtest_start_index

    x_backtest_df = df.iloc[backtest_input_start_index:backtest_input_end_index][
        ["open", "high", "low", "close", "volume", "amount"]
    ].reset_index(drop=True)

    x_backtest_ts = df.iloc[backtest_input_start_index:backtest_input_end_index]["timestamps"].reset_index(drop=True)

    # 回测的输出时间：即实际数据的最后 pred_len 天
    y_backtest_ts = df.iloc[backtest_start_index:]["timestamps"].reset_index(drop=True)

    preds_backtest = []
    for _ in trange(sample_count, desc="Predicting backtest samples"):
        pred_df = predictor.predict(
            df=x_backtest_df,
            x_timestamp=x_backtest_ts,
            y_timestamp=y_backtest_ts,
            pred_len=pred_len,
            T=1.0,
            top_p=0.95,
            sample_count=4,
        )
        if pred_df.index.name != "timestamps":
            pred_df = pred_df.reset_index()
        preds_backtest.append(pred_df["close"].values)

    preds_backtest = np.stack(preds_backtest)
    mean_backtest = preds_backtest.mean(axis=0)
    min_backtest = preds_backtest.min(axis=0)
    max_backtest = preds_backtest.max(axis=0)

    # ---------------------------------
    # --- 3. 准备和执行 "未来预测" (Prediction 2) ---
    # ---------------------------------
    logger.info("[SayuStock] 正在执行未来预测...")

    # 未来预测的输入数据：使用所有（或最后 lookback）的可用数据
    lookback_future = min(max_lookback, total_len - 1)
    future_input_start_index = total_len - lookback_future

    x_future_df = df.iloc[future_input_start_index:][["open", "high", "low", "close", "volume", "amount"]].reset_index(
        drop=True
    )
    x_future_ts = df.iloc[future_input_start_index:]["timestamps"].reset_index(drop=True)

    # 未来预测的输出时间
    timestamps = df["timestamps"]
    freq = timestamps.iloc[-1] - timestamps.iloc[-2] if len(timestamps) >= 2 else pd.Timedelta(days=1)
    last_timestamp = timestamps.iloc[-1]
    pred_times = generate_trading_times(last_timestamp, pred_len, freq)

    preds_future = []
    for _ in trange(sample_count, desc="Predicting future samples"):
        pred_df = predictor.predict(
            df=x_future_df,
            x_timestamp=x_future_ts,
            y_timestamp=pd.Series(pred_times),
            pred_len=pred_len,
            T=1.0,
            top_p=0.95,
            sample_count=4,
        )
        if pred_df.index.name != "timestamps":
            pred_df = pred_df.reset_index()
        preds_future.append(pred_df["close"].values)

    preds_future = np.stack(preds_future)
    mean_future = preds_future.mean(axis=0)
    min_future = preds_future.min(axis=0)
    max_future = preds_future.max(axis=0)

    # ---------------------------------
    # --- 4. 绘图 ---
    # ---------------------------------
    fig = go.Figure()

    # 历史数据 (包含回测区的实际数据)
    hist_t = df["timestamps"]
    hist_close = df["close"]
    fig.add_trace(
        go.Scatter(
            x=hist_t,
            y=hist_close,
            mode="lines",
            name="历史实际走势",
            line=dict(color="blue", width=2),
        )
    )

    # --- 回测区 ---
    backtest_t_plotting = df.iloc[backtest_start_index:]["timestamps"]

    # 回测预测均值 (绿色虚线)
    fig.add_trace(
        go.Scatter(
            x=backtest_t_plotting,
            y=mean_backtest,
            mode="lines",
            name="回测-预测均值",
            line=dict(color="green", width=2, dash="dot"),
        )
    )

    # 回测阴影范围
    fig.add_trace(
        go.Scatter(
            x=list(backtest_t_plotting) + list(backtest_t_plotting[::-1]),
            y=list(max_backtest) + list(min_backtest[::-1]),
            fill="toself",
            fillcolor="rgba(0,255,0,0.2)",
            line=dict(color="rgba(255,255,255,0)"),
            hoverinfo="skip",
            name="回测范围 (Min–Max)",
        )
    )

    # --- 未来预测区 ---

    # 为了让历史曲线和预测曲线“连接”上，我们把历史的最后一个点加入到预测曲线的开头
    connected_future_t = pd.concat([hist_t.iloc[-1:], pd.Series(pred_times)])
    connected_future_close = np.concatenate([[hist_close.iloc[-1]], mean_future])

    # 预测均值曲线 (连接的)
    fig.add_trace(
        go.Scatter(
            x=connected_future_t,
            y=connected_future_close,
            mode="lines",
            name="未来-预测均值",
            line=dict(color="orange", width=2),
        )
    )

    # 未来阴影范围
    fig.add_trace(
        go.Scatter(
            x=list(pred_times) + list(pred_times[::-1]),
            y=list(max_future) + list(min_future[::-1]),
            fill="toself",
            fillcolor="rgba(255,165,0,0.3)",
            line=dict(color="rgba(255,255,255,0)"),
            hoverinfo="skip",
            name="未来范围 (Min–Max)",
        )
    )

    # --- 5. 添加分割线和布局 ---

    # 分割线 1: 回测开始
    backtest_start_time = df["timestamps"].iloc[backtest_start_index]
    fig.add_shape(
        type="line",
        x0=backtest_start_time,
        x1=backtest_start_time,
        xref="x",
        y0=0,
        y1=1,
        yref="paper",
        line=dict(color="grey", dash="dash", width=2),
    )
    fig.add_annotation(
        x=backtest_start_time,
        y=1.02,
        xref="x",
        yref="paper",
        text="回测开始",
        showarrow=False,
        align="right",
        font=dict(color="grey"),
    )

    # 分割线 2: 预测开始
    future_start_time = pd.to_datetime(last_timestamp).to_pydatetime()
    fig.add_shape(
        type="line",
        x0=future_start_time,
        x1=future_start_time,
        xref="x",
        y0=0,
        y1=1,
        yref="paper",
        line=dict(color="red", dash="dash", width=2),
    )
    fig.add_annotation(
        x=future_start_time,
        y=1.02,
        xref="x",
        yref="paper",
        text="预测开始",
        showarrow=False,
        align="left",
        font=dict(color="red"),
    )

    # -----------------------------
    # 布局
    # -----------------------------
    fig.update_layout(
        title=dict(
            text=f"{raw_data['data'].get('name', 'Price Forecast')} (含回测与预测)",
            font=dict(size=24),
            x=0.5,
            xanchor="center",
        ),
        xaxis=dict(title="时间", title_font=dict(size=18)),
        yaxis=dict(title="价格", title_font=dict(size=18)),
        legend=dict(font=dict(size=14)),
        template="plotly_white",
    )
    fig.update_xaxes(
        rangeslider_visible=False,
        tickformat="%Y-%m-%d %H:%M",
        rangebreaks=[
            dict(bounds=["sat", "mon"]),  # 跳过周末
            dict(bounds=[15.0, 9.5], pattern="hour"),  # 跳过夜间
        ],
    )

    return fig
