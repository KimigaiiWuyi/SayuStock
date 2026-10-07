import json
import inspect
import functools
from typing import Any, List, Tuple, TypeVar, Callable, Optional, Coroutine, ParamSpec, cast
from pathlib import Path
from datetime import date, datetime, timedelta

import aiofiles
from PIL import Image
from plotly.graph_objects import Figure

from gsuid_core.logger import logger

from ..resource_path import DATA_PATH
from ...stock_config.stock_config import STOCK_CONFIG

_P = ParamSpec("_P")
_R = TypeVar("_R")


def async_file_cache(
    **get_file_args: Any,
) -> Callable[[Callable[_P, Coroutine[Any, Any, _R]]], Callable[_P, Coroutine[Any, Any, _R]]]:
    """
    一个异步函数装饰器，用于缓存函数结果到文件。

    通过在装饰器参数中使用 f-string 格式的占位符，可以动态地根据
    被装饰函数的参数来生成文件名。

    示例:
        @async_file_cache(market='vix_market', sector='{vix_name}', suffix='json')
        async def get_vix(vix_name: str):
            ...

    当调用 `get_vix(vix_name='VIX_9D')` 时, 装饰器会使用
    `sector='VIX_9D'` 来调用 `get_file`。
    """

    def decorator(
        func: Callable[_P, Coroutine[Any, Any, _R]],
    ) -> Callable[_P, Coroutine[Any, Any, _R]]:
        @functools.wraps(func)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            # 1. 解析函数参数，为文件名生成做准备
            try:
                sig = inspect.signature(func)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()
                # 获取所有参数的字典
                func_args_dict = bound_args.arguments
            except TypeError as e:
                logger.warning(f"🏷️ [SayuStock] 参数绑定失败: {e}。将跳过缓存。")
                return await func(*args, **kwargs)

            # 2. 根据函数参数动态生成 get_file 的参数
            minutes = 0
            resolved_get_file_args = {}
            for key, value in get_file_args.items():
                if key == "minutes":
                    minutes = int(value)
                    continue

                if isinstance(value, str):
                    # 格式化字符串，将 {arg_name} 替换为实际参数值
                    try:
                        resolved_get_file_args[key] = value.format(**func_args_dict)
                    except KeyError as e:
                        raise ValueError(
                            f"装饰器参数 '{key}=\"{value}\"' 中的占位符 {e} 在函数 {func.__name__} 的参数中未找到。"
                        ) from e
                else:
                    resolved_get_file_args[key] = value

            # 3. 获取文件路径
            file_path = get_file(**resolved_get_file_args)
            logger.info(f"🔍️ [SayuStock] 检查缓存文件: {file_path}")

            if file_path.exists():
                try:
                    # 检查文件的修改时间是否在一分钟以内
                    cache_minutes = minutes
                    if cache_minutes == 0:
                        cache_minutes = int(STOCK_CONFIG.get_config("mapcloud_refresh_minutes").data)

                    file_mod_time = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if datetime.now() - file_mod_time < timedelta(minutes=cache_minutes):
                        logger.info(f"[SayuStock] 缓存文件在{cache_minutes}分钟内，直接返回文件数据。")

                        if file_path.suffix in {".html", ".png", ".jpg", ".jpeg", ".webp"}:
                            return cast(_R, file_path)

                        async with aiofiles.open(file_path, mode="r", encoding="utf-8") as f:
                            logger.success(f"✅ [SayuStock] 缓存命中！正在从 {file_path} 读取...")
                            content = await f.read()
                            return cast(_R, json.loads(content))

                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(f"🚨 [SayuStock] 读取或解析缓存文件失败: {e}。将重新执行函数。")

            # 5. 如果文件不存在，执行原函数
            logger.info(f"🚧 [SayuStock] 缓存未命中。正在执行函数 {func.__name__}...")
            result = await func(*args, **kwargs)
            if isinstance(result, (int, str)):
                return result

            if isinstance(result, Figure):
                result.write_html(str(file_path))
                return cast(_R, file_path)

            if isinstance(result, Image.Image):
                result.save(file_path)
                return cast(_R, file_path)

            if isinstance(result, (bytes, bytearray)) and file_path.suffix.lower() in {
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
            }:
                file_path.write_bytes(bytes(result))
                return cast(_R, file_path)

            if isinstance(result, dict):
                result["file_name"] = file_path.name

            # 6. 将结果异步写入文件
            try:
                serialized_result = json.dumps(result, indent=4, ensure_ascii=False)
                async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
                    await f.write(serialized_result)
                    logger.success(f"✅ [SayuStock] 结果已成功缓存至 {file_path}")
            except (TypeError, IOError) as e:
                logger.warning(f"🚨 [SayuStock] 缓存结果失败: {e}")

            return result

        return wrapper

    return decorator


def get_file(
    market: str,
    suffix: str,
    sector: Optional[str] = None,
    sp: Optional[str] = None,
    **_: Any,
) -> Path:
    a = f"{market}_{sector}_{sp}_data"
    a = a[:254]
    return DATA_PATH / f"{a}.{suffix}"


def get_adjusted_date() -> datetime:
    now = datetime.now()
    target_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    # 判断当前时间是否在当天的9:30之前
    if now < target_time:
        adjusted_date = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    else:
        adjusted_date = now
    return adjusted_date


def calculate_difference(data: List[str]) -> Tuple[float, float, Optional[datetime]]:
    """trends2（ndays≥2）→ (最近交易日成交额, 与前一交易日同时刻之差, 该交易日)。

    日期一律按**完整日期**比较，不再用「几号」：
    ① 按 ``.day`` 比较时，跨月会把 30 号当成「今天之后」；
    ② 原来「最多回退 4 天」的探法在长假（连休 >4 天）探不到任何交易日，
       直接回 (0, 0, None)，下游把它当成网络失败而降级换源——休市日的大盘概览
       于是悄悄改用另一个源、日期口径也不同。这里改成取数据里**最近的那个
       交易日**，休市日照样由东财自己给出「上一交易日成交额 + 实际日期」。
    """
    today = get_adjusted_date().date()

    amounts: dict[date, list[float]] = {}
    for item in data:
        item_part = item.split(",")
        stamp = datetime.strptime(item_part[0], "%Y-%m-%d %H:%M")
        amounts.setdefault(stamp.date(), []).append(float(item_part[6]))

    days = sorted(amounts)
    if len(days) < 2:
        # 只有一个交易日（甚至没有）时给不出「放量/缩量」，按拿不到处理
        return 0.0, 0.0, None

    target = today if today in amounts else days[-1]
    index = days.index(target)
    if index == 0:
        return 0.0, 0.0, None

    series = amounts[target]
    today_amount = sum(series)
    # 与前一交易日「同一时刻」的量相比，避免早盘拿全天量比
    prev_amount = sum(amounts[days[index - 1]][: len(series)])
    logger.info(f"[SayuStock]今天交易日: {target}")
    is_trading_day = target == today
    actual_date = None if is_trading_day else datetime(target.year, target.month, target.day)
    return today_amount, today_amount - prev_amount, actual_date
