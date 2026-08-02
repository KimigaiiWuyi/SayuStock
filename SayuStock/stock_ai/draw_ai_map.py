"""Kronos AI 预测出图。

惰性加载 Kronos（依赖 torch / HF），避免插件 import 期拖垮无 torch 的环境。
出图结果经 ``@async_file_cache`` 写成 HTML；``ai_return`` 必须在缓存函数外调用。
"""

from __future__ import annotations

import sys
import asyncio
from typing import TYPE_CHECKING, Any, Union, cast
from pathlib import Path
from contextlib import contextmanager
from collections.abc import Iterator

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from tqdm import trange
from numpy.typing import NDArray

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..utils.image import render_image_by_pw
from ..utils.market import KlinePeriod, get_market, is_market_error
from ..utils.constant import ErroText
from ..utils.load_data import get_full_security_code
from ..utils.stock.utils import async_file_cache
from ..utils.market.models import KlineSeries
from ..utils.stock.request_utils import get_code_id
from ..utils.market.convert.dataframe import kline_to_df

if TYPE_CHECKING:
    from plotly.graph_objs import Figure as PlotlyFigure
else:
    PlotlyFigure = go.Figure

NOW_QUEUE: list[str] = []

base_dir = Path(__file__).parent
kronos_dir = base_dir.parent / "Kronos"


@contextmanager
def temp_sys_path(path: str) -> Iterator[None]:
    """临时添加 sys.path，退出时恢复。"""
    old_path = list(sys.path)
    sys.path.insert(0, path)
    try:
        yield
    finally:
        sys.path[:] = old_path


def fill_kline_by_kronos(series: KlineSeries) -> pd.DataFrame | None:
    """将 KlineSeries 转为 Kronos 用 DataFrame（timestamps 为 datetime）。"""
    en = kline_to_df(series)
    if en.empty:
        return None
    df = en.rename(columns={"date": "timestamps", "chg_pct": "chg_percent"})
    df["timestamps"] = pd.to_datetime(df["timestamps"], errors="coerce")
    final_cols = [
        "timestamps",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    ]
    keep = [c for c in final_cols if c in df.columns]
    out = df.loc[:, keep].copy()
    out = out.dropna(subset=["timestamps"])
    if out.empty:
        return None
    return out


async def draw_ai_kline_with_forecast(market: str, bot: Bot) -> str | bytes:
    logger.info(f"[SayuStock] get_single_fig_data code: {market}")

    sec_id_data = await get_code_id(market)
    if sec_id_data is None:
        return ErroText["notStock"]

    sec_id = get_full_security_code(sec_id_data[0])
    if not sec_id:
        return ErroText["notStock"]

    if sec_id in NOW_QUEUE:
        return "当前股票已在预测队列中，请稍后..."

    if NOW_QUEUE:
        return f"当前队列中还有{len(NOW_QUEUE)}只股票在预测中，请稍后提交..."

    series = await get_market().kline(sec_id, KlinePeriod.M30)
    if is_market_error(series):
        return series.message

    df = fill_kline_by_kronos(series)
    if df is None or df.empty:
        return "无有效K线数据"

    # 文字必须发在缓存**之外**：部分模型看不到图，ai_return 的文字是它唯一的输入，
    # 而 _draw_ai_kline_with_forecast 挂着 @async_file_cache(minutes=150) —— 命中缓存
    # 时装饰器直接返回文件、整个函数体都不执行。文字若留在里面，2.5 小时内问第二次
    # 同一只票，AI 就一个字都收不到。
    _ai_return_kronos_data(series, df)

    await bot.send("[SayuStock] 模型预测中，预计将会持续3分钟，请稍后...")
    NOW_QUEUE.append(sec_id)
    try:
        fig_or_path = await _draw_ai_kline_with_forecast(sec_id, df, series)
    except Exception as e:
        logger.error(f"[SayuStock] 模型预测出现错误: {e}")
        return f"模型预测出现错误: {e}"
    finally:
        if sec_id in NOW_QUEUE:
            NOW_QUEUE.remove(sec_id)

    if isinstance(fig_or_path, str):
        return fig_or_path
    if isinstance(fig_or_path, Path):
        return await render_image_by_pw(fig_or_path, 4000, 2000, 0)
    # 正常路径下 @async_file_cache 会把 Figure 落成 Path；此处仅兜底
    if isinstance(fig_or_path, go.Figure):
        from ..utils.stock.utils import get_file

        html_path_obj = get_file(sec_id, "html", "single-stock-ai", None)
        html_path = html_path_obj if isinstance(html_path_obj, Path) else Path(str(html_path_obj))
        fig_or_path.write_html(html_path)
        return await render_image_by_pw(html_path, 4000, 2000, 0)
    return "出现了未知错误。"


@async_file_cache(
    market="{sec_id}",
    sector="single-stock-ai",
    suffix="html",
    minutes=150,
)
async def _draw_ai_kline_with_forecast(
    sec_id: str,
    df: pd.DataFrame,
    series: KlineSeries,
) -> str | Path | PlotlyFigure:
    """只负责出图（Kronos 预测约 3 分钟，故缓存 150 分钟）。

    **不要在这里调 ai_return**：命中缓存时装饰器直接返回文件、本函数根本不执行，
    文字会丢。发文字请留在未被缓存的 ``draw_ai_kline_with_forecast`` 里。
    ``df`` / ``series`` 由调用方传入以免重复取数；缓存键只取 ``{sec_id}``。
    """
    _ = sec_id
    return await asyncio.to_thread(gdf, df, series)


def _ai_return_kronos_data(series: KlineSeries, df: pd.DataFrame) -> None:
    """从 Kronos 预测输入中提取文字，通过 ai_return 交给 AI。"""
    _ = df
    try:
        name = series.symbol.name or "N/A"
        lines: list[str] = [
            f"【{name} AI预测基础数据】",
            "数据周期: 30分钟K线",
            f"数据条数: {len(series.bars)}",
        ]
        if series.bars:
            last = series.bars[-1]
            chg = last.change_pct if last.change_pct is not None else 0.0
            lines.append(
                f"最新K线: {last.ts} 开:{last.open} 收:{last.close} 高:{last.high} 低:{last.low} 涨跌幅:{chg}%"
            )
            lines.append("")
            lines.append("最近5条K线:")
            for bar in series.bars[-5:]:
                bchg = bar.change_pct if bar.change_pct is not None else 0.0
                lines.append(f"  {bar.ts} 收:{bar.close} ({bchg}%)")
        ai_return("\n".join(lines))
    except Exception as e:
        logger.warning(f"[SayuStock] ai_return Kronos数据提取失败: {e}")


def generate_trading_times(
    start: pd.Timestamp,
    periods: int,
    freq: Union[str, pd.Timedelta] = "1H",
    *,
    trading_intervals: list[tuple[float, float]] | None = None,
    skip_weekends: bool = True,
) -> pd.DatetimeIndex:
    """从 start 起按 freq 生成 ``periods`` 个交易时段内时间戳。"""
    intervals = trading_intervals if trading_intervals is not None else [(9.5, 11.5), (13.0, 15.0)]

    if isinstance(freq, str):
        try:
            freq_td = pd.Timedelta(freq)
        except (ValueError, TypeError) as e:
            raise ValueError(f"无法解析字符串 freq={freq!r}: {e}") from e
    else:
        freq_td = freq

    if pd.isna(freq_td):
        raise ValueError("freq 是 NaT 或无效，请传入有效的 str 或 pd.Timedelta")
    if not isinstance(freq_td, pd.Timedelta):
        try:
            freq_td = pd.Timedelta(freq_td)
        except (ValueError, TypeError) as e:
            raise ValueError(f"无法转换 freq 为 Timedelta: {e}") from e
    assert isinstance(freq_td, pd.Timedelta)

    times: list[pd.Timestamp] = []
    curr = pd.Timestamp(start)
    max_iters = periods * 1000 + 10000
    iters = 0
    while len(times) < periods and iters < max_iters:
        iters += 1
        curr = curr + freq_td
        if skip_weekends and int(curr.weekday()) >= 5:
            continue
        t_float = float(curr.hour) + float(curr.minute) / 60.0 + float(curr.second) / 3600.0
        in_trade = any(start_h <= t_float <= end_h for (start_h, end_h) in intervals)
        if not in_trade:
            continue
        if isinstance(curr, pd.Timestamp) and not pd.isna(curr):
            times.append(curr)

    if len(times) < periods:
        raise RuntimeError(
            f"无法生成足够的交易时间戳 (requested {periods}, got {len(times)}). "
            "请检查 freq、trading_intervals 是否合理，或增大 max_iters。"
        )
    return pd.DatetimeIndex(times)


def _as_float_array(values: Any) -> NDArray[np.floating[Any]]:
    arr = np.asarray(values, dtype=float)
    return arr


def gdf(df: pd.DataFrame, series: KlineSeries) -> str | PlotlyFigure:
    """运行 Kronos 回测+未来预测并返回 plotly Figure（或错误文本）。"""
    # Kronos 是 git submodule 且顶层 import torch —— 惰性导入
    with temp_sys_path(str(kronos_dir)):
        from ..Kronos.model import Kronos, KronosPredictor, KronosTokenizer

    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-mini")
    predictor = KronosPredictor(
        model,
        tokenizer,
        device="cpu",
        max_context=512,
    )

    work = df.copy()
    total_len = len(work)
    if total_len == 0:
        return ErroText["notData"]

    sample_count = 5
    max_lookback = 470

    # kline_to_df 产出的是日期字符串/datetime，不是 epoch ms
    ts_series = pd.to_datetime(work["timestamps"], errors="coerce")
    work = work.assign(timestamps=ts_series)
    work = work.dropna(subset=["timestamps"]).sort_values("timestamps").reset_index(drop=True)
    total_len = len(work)
    if total_len == 0:
        return ErroText["notData"]

    timestamps = work["timestamps"]
    inferred_freq = pd.Timedelta(days=1)
    if len(timestamps) >= 2:
        diff0 = timestamps.iloc[-1] - timestamps.iloc[-2]
        if isinstance(diff0, pd.Timedelta) and not pd.isna(diff0):
            inferred_freq = pd.Timedelta(diff0)

    freq_minutes = float(inferred_freq.total_seconds()) / 60.0
    logger.info(f"[SayuStock] 股票K线周期: {freq_minutes}")

    if freq_minutes >= 1440 - 1:
        freq_label = "1D"
    elif freq_minutes >= 60:
        freq_label = f"{int(freq_minutes / 60)}H"
    else:
        freq_label = f"{int(freq_minutes)}min"
    logger.info(f"[SayuStock] 判定周期标签: {freq_label}")

    pred_len = 30
    if total_len < pred_len + 20:
        return "数据量不足，无法进行回测和预测"

    backtest_start_index = total_len - pred_len
    logger.info("[SayuStock] 正在执行回测预测...")

    lookback_backtest = min(max_lookback, backtest_start_index - 1)
    backtest_input_start_index = backtest_start_index - lookback_backtest
    backtest_input_end_index = backtest_start_index

    ohlcv_cols = ["open", "high", "low", "close", "volume", "amount"]
    x_backtest_df = work.iloc[backtest_input_start_index:backtest_input_end_index][ohlcv_cols].reset_index(drop=True)
    x_backtest_ts = work.iloc[backtest_input_start_index:backtest_input_end_index]["timestamps"].reset_index(drop=True)
    y_backtest_ts = work.iloc[backtest_start_index:]["timestamps"].reset_index(drop=True)

    preds_backtest_list: list[NDArray[np.floating[Any]]] = []
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
        preds_backtest_list.append(_as_float_array(pred_df["close"].to_numpy()))

    preds_backtest = np.stack(preds_backtest_list)
    mean_backtest = preds_backtest.mean(axis=0)
    min_backtest = preds_backtest.min(axis=0)
    max_backtest = preds_backtest.max(axis=0)

    logger.info("[SayuStock] 正在执行未来预测...")
    lookback_future = min(max_lookback, total_len - 1)
    future_input_start_index = total_len - lookback_future

    x_future_df = work.iloc[future_input_start_index:][ohlcv_cols].reset_index(drop=True)
    x_future_ts = work.iloc[future_input_start_index:]["timestamps"].reset_index(drop=True)

    timestamps = work["timestamps"]
    freq = pd.Timedelta(days=1)
    if len(timestamps) >= 2:
        diff = timestamps.iloc[-1] - timestamps.iloc[-2]
        if isinstance(diff, pd.Timedelta) and not pd.isna(diff):
            freq = pd.Timedelta(diff)

    last_raw = timestamps.iloc[-1]
    last_timestamp = pd.Timestamp(last_raw)
    if pd.isna(last_timestamp):
        return ErroText["notData"]
    # stubs 里 Timestamp(...) / Timedelta(...) 常带 NaTType 联合，运行时已排除 NaT
    pred_times = generate_trading_times(
        cast(pd.Timestamp, last_timestamp),
        pred_len,
        cast(pd.Timedelta, freq),
    )

    preds_future_list: list[NDArray[np.floating[Any]]] = []
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
        preds_future_list.append(_as_float_array(pred_df["close"].to_numpy()))

    preds_future = np.stack(preds_future_list)
    mean_future = preds_future.mean(axis=0)
    min_future = preds_future.min(axis=0)
    max_future = preds_future.max(axis=0)

    fig = go.Figure()
    hist_t = work["timestamps"]
    hist_close = work["close"]
    fig.add_trace(
        go.Scatter(
            x=hist_t,
            y=hist_close,
            mode="lines",
            name="历史实际走势",
            line={"color": "blue", "width": 2},
        )
    )

    backtest_t_plotting = work.iloc[backtest_start_index:]["timestamps"]
    fig.add_trace(
        go.Scatter(
            x=backtest_t_plotting,
            y=mean_backtest,
            mode="lines",
            name="回测-预测均值",
            line={"color": "green", "width": 2, "dash": "dot"},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=list(backtest_t_plotting) + list(backtest_t_plotting[::-1]),
            y=list(max_backtest) + list(min_backtest[::-1]),
            fill="toself",
            fillcolor="rgba(0,255,0,0.2)",
            line={"color": "rgba(255,255,255,0)"},
            hoverinfo="skip",
            name="回测范围 (Min–Max)",
        )
    )

    connected_future_t = pd.concat([hist_t.iloc[-1:], pd.Series(pred_times)])
    last_close = float(hist_close.iloc[-1])
    connected_future_close = np.concatenate([[last_close], mean_future])
    fig.add_trace(
        go.Scatter(
            x=connected_future_t,
            y=connected_future_close,
            mode="lines",
            name="未来-预测均值",
            line={"color": "orange", "width": 2},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=list(pred_times) + list(pred_times[::-1]),
            y=list(max_future) + list(min_future[::-1]),
            fill="toself",
            fillcolor="rgba(255,165,0,0.3)",
            line={"color": "rgba(255,255,255,0)"},
            hoverinfo="skip",
            name="未来范围 (Min–Max)",
        )
    )

    backtest_start_time = work["timestamps"].iloc[backtest_start_index]
    fig.add_shape(
        type="line",
        x0=backtest_start_time,
        x1=backtest_start_time,
        xref="x",
        y0=0,
        y1=1,
        yref="paper",
        line={"color": "grey", "dash": "dash", "width": 2},
    )
    fig.add_annotation(
        x=backtest_start_time,
        y=1.02,
        xref="x",
        yref="paper",
        text="回测开始",
        showarrow=False,
        align="right",
        font={"color": "grey"},
    )

    future_start_time = last_timestamp.to_pydatetime()
    fig.add_shape(
        type="line",
        x0=future_start_time,
        x1=future_start_time,
        xref="x",
        y0=0,
        y1=1,
        yref="paper",
        line={"color": "red", "dash": "dash", "width": 2},
    )
    fig.add_annotation(
        x=future_start_time,
        y=1.02,
        xref="x",
        yref="paper",
        text="预测开始",
        showarrow=False,
        align="left",
        font={"color": "red"},
    )

    title_name = series.symbol.name or "Price Forecast"
    fig.update_layout(
        title={
            "text": f"{title_name} (含回测与预测)",
            "font": {"size": 24},
            "x": 0.5,
            "xanchor": "center",
        },
        xaxis={"title": "时间", "title_font": {"size": 18}},
        yaxis={"title": "价格", "title_font": {"size": 18}},
        legend={"font": {"size": 14}},
        template="plotly_white",
    )
    fig.update_xaxes(
        rangeslider_visible=False,
        tickformat="%Y-%m-%d %H:%M",
        rangebreaks=[
            {"bounds": ["sat", "mon"]},
            {"bounds": [15.0, 9.5], "pattern": "hour"},
        ],
    )
    return fig
