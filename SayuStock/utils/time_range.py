import re
import datetime
import zoneinfo
from enum import Enum, auto
from typing import Dict, List, Tuple, Optional


class Market(Enum):
    """定义市场类型的枚举"""

    A_SHARE = auto()
    HK_STOCK = auto()
    US_STOCK = auto()
    US_FUTURE = auto()  # 美国期货（如 nq、es）
    CN_FUTURE_DAY = auto()  # 中国日盘期货（通用）
    CN_FUTURE_NIGHT = auto()  # 中国夜盘期货（通用，如金属、能源）
    SG_FUTURE = auto()  # 新加坡期货（如A50）
    BOND = auto()  # 中国债
    US_BOND = auto()  # 美债收益率：电子盘近 23×5，非美股 9:30–16:00
    UNKNOWN = auto()
    COMMODITY = auto()  # 商品期货
    SPOT = auto()  # 现货
    TLM = auto()  # TLM
    COMMODITY_SPOT = auto()  # 商品现货
    CRYPTO = auto()  # 加密货币
    FX = auto()  # 外汇 24×5（周日美东 17:00 开，周五美东 17:00 收）
    # —— 东方财富 PREFIX 100 开头的全球指数（按各自主市场时段）——
    # 备注：美股指数（道琼/纳指/标普）复用 US_STOCK；恒生指数复用 HK_STOCK
    KR_STOCK = auto()  # 韩股个股（东财 PREFIX 177，如 177.005930 三星电子）
    KR_INDEX = auto()  # 韩国交易所指数（KOSPI/KOSPI200）
    JP_INDEX = auto()  # 日本交易所指数（日经225 等）
    CA_INDEX = auto()  # 加拿大 S&P/TSX
    LATAM_INDEX = auto()  # 拉美指数（巴西/墨西哥/俄罗斯等）
    EU_INDEX = auto()  # 欧洲指数（SXXP/SX5E/FTSE/CAC/DAX）


_BJT = zoneinfo.ZoneInfo("Asia/Shanghai")
_NY = zoneinfo.ZoneInfo("America/New_York")


def now_bjt() -> datetime.datetime:
    """当前北京时间墙钟（朴素 datetime）。不跟服务器本地时区走。"""
    return datetime.datetime.now(_BJT).replace(tzinfo=None)


def as_naive_bjt(now: Optional[datetime.datetime] = None) -> datetime.datetime:
    """规范成朴素 BJT：None 取当前北京时间；带 tzinfo 则转到上海。"""
    if now is None:
        return now_bjt()
    if now.tzinfo is None:
        return now
    return now.astimezone(_BJT).replace(tzinfo=None)


def is_us_daylight_saving(now_bjt: Optional[datetime.datetime] = None) -> bool:
    """美国是否夏令时。决定美股 BJT 开盘 21:30 还是 22:30。

    传入 now_bjt（朴素时间视为北京时间）时按该时刻判断，避免进程跨冬夏令仍用启动时 DST。
    """
    aware = as_naive_bjt(now_bjt).replace(tzinfo=_BJT)
    return aware.astimezone(_NY).dst() != datetime.timedelta(0)


# 不随夏令时变化的时段（北京时间）。跨天如 21:00→02:30 由生成函数处理。
_FIXED_SESSIONS: Dict[Market, List[Tuple[str, str]]] = {
    Market.A_SHARE: [
        ("09:30", "11:30"),
        ("13:00", "15:00"),
    ],
    Market.HK_STOCK: [
        ("09:30", "12:00"),
        ("13:00", "16:00"),  # 联交所持续交易 16:00 收，不是 15:30
    ],
    Market.CN_FUTURE_DAY: [
        ("09:00", "10:15"),
        ("10:30", "11:30"),
        ("13:30", "15:00"),
    ],
    Market.CN_FUTURE_NIGHT: [
        ("21:00", "02:30"),
    ],
    Market.SG_FUTURE: [
        ("09:00", "16:30"),
        ("17:00", "05:15"),
    ],
    Market.BOND: [
        ("09:30", "11:30"),
        ("13:00", "15:00"),
    ],
    Market.SPOT: [
        ("09:00", "15:30"),
        ("20:00", "02:30"),
    ],
    Market.TLM: [
        ("09:30", "11:30"),
        ("13:00", "15:15"),
    ],
    Market.CRYPTO: [
        ("00:00", "23:59"),
    ],
    Market.KR_STOCK: [
        ("08:00", "14:30"),
    ],
    Market.KR_INDEX: [
        ("08:00", "14:30"),
    ],
    # 东证 09:00-11:30 / 12:30-15:30 JST（2024-11 起后场收到 15:30）
    Market.JP_INDEX: [
        ("08:00", "10:30"),
        ("11:30", "14:30"),
    ],
}


def _dst_varying_sessions(dst: bool) -> Dict[Market, List[Tuple[str, str]]]:
    """随美国夏令时变化的 BJT 时段。"""
    us_eq = [("21:30", "04:00") if dst else ("22:30", "05:00")]
    # CME 日维护约 16:00–17:00 ET → 夏 06:00–次日 05:00 / 冬 07:00–次日 06:00
    us_fut = [("06:00", "05:00") if dst else ("07:00", "06:00")]
    return {
        Market.US_STOCK: us_eq,
        Market.LATAM_INDEX: us_eq,
        Market.US_FUTURE: us_fut,
        Market.COMMODITY: us_fut,
        # 美债现金/收益率电子盘接近 23×5，与 CME 国债期货同一维护窗口
        Market.US_BOND: us_fut,
        Market.COMMODITY_SPOT: [("06:00", "05:15") if dst else ("07:00", "06:15")],
        Market.FX: [("05:00", "04:59") if dst else ("06:00", "05:59")],
        Market.CA_INDEX: us_eq,
        Market.EU_INDEX: [("15:00", "23:30") if dst else ("16:00", "00:30")],
    }


def get_market_sessions(
    market: Market,
    now_bjt: Optional[datetime.datetime] = None,
) -> List[Tuple[str, str]]:
    """返回该市场在 now_bjt（默认当前）下的 BJT 交易时段。未知市场按 A 股。"""
    if market in _FIXED_SESSIONS:
        return _FIXED_SESSIONS[market]
    dyn = _dst_varying_sessions(is_us_daylight_saving(now_bjt))
    if market in dyn:
        return dyn[market]
    return _FIXED_SESSIONS[Market.A_SHARE]


def _market_of_code(code: Optional[str]) -> Market:
    market = _parse_em_code(code) if code else Market.A_SHARE
    if market == Market.UNKNOWN:
        return Market.A_SHARE
    return market


def get_sessions_for_code(
    code: Optional[str] = None,
    now_bjt: Optional[datetime.datetime] = None,
) -> List[Tuple[str, str]]:
    """按东财代码返回 BJT 交易时段。未知代码按 A 股。"""
    return get_market_sessions(_market_of_code(code), now_bjt)


# 兼容旧调用：启动时刻的 DST 快照。跨冬夏令或按历史日判断请用 get_market_sessions。
MARKET_SESSIONS: Dict[Market, List[Tuple[str, str]]] = {
    **_FIXED_SESSIONS,
    **_dst_varying_sessions(is_us_daylight_saving()),
}


def _parse_em_code(code: str) -> Market:
    """
    解析东方财富代码，返回其所属的市场枚举类型。
    """
    if "crypto" in code:
        return Market.CRYPTO

    code = code.split("_")[0]
    if code.lower().startswith("i:"):
        code = code[2:]

    if not isinstance(code, str) or not code:
        return Market.UNKNOWN

    up = code.upper()
    if up in {"BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "PEPE"} or "-USD" in up:
        return Market.CRYPTO
    if up in {"JP.BOND", "JP30Y", "JP10Y"}:
        return Market.JP_INDEX

    # 优先匹配期货代码（通常是字母+数字）
    # e.g., 'rb2510', 'ag2512', 'IF2508'
    if re.fullmatch(r"^[a-zA-Z]{1,2}\d{4}$", code):
        # 简化处理：假设字母开头的都是期货。可根据首字母进一步细分日夜盘。
        # 例如，'rb', 'ag', 'au', 'cu' 等通常有夜盘
        if code.lower().startswith(
            (
                "rb",
                "ag",
                "au",
                "cu",
                "zn",
                "al",
                "pb",
                "ni",
                "sn",
                "sc",
                "lu",
            )
        ):
            return Market.CN_FUTURE_NIGHT
        # 美国期货：nq（纳斯达克）、es（标普500）、ym（道琼斯）等
        if code.lower().startswith(
            (
                "nq",
                "es",
                "ym",
                "rty",
            )
        ):
            return Market.US_FUTURE
        return Market.CN_FUTURE_DAY

    # 新加坡A50指数期货
    if code.upper() == "CN":
        return Market.SG_FUTURE

    # 带市场前缀的代码 (e.g., '1.600519', '106.BABA', '100.KS11')
    if "." in code:
        prefix, main_code = code.split(".", 1)
        if prefix in ["118"]:
            return Market.SPOT
        if prefix in ["103"]:
            return Market.US_FUTURE
        if prefix in ["122"]:
            return Market.COMMODITY_SPOT
        if prefix in ["101", "102"]:
            return Market.COMMODITY
        if prefix == "171":
            mc = main_code.upper()
            if mc.startswith("US"):
                return Market.US_BOND
            if mc.startswith("JP"):
                return Market.JP_INDEX
            return Market.BOND
        if prefix in ["109", "113", "114", "115"]:
            return Market.CN_FUTURE_DAY
        if prefix in ["119", "133"]:
            return Market.FX
        if prefix in ["220"]:
            return Market.TLM
        if prefix in ["0", "1"]:
            # 进一步判断是股票还是债券
            if main_code.startswith(("01", "10", "11", "12")):
                return Market.BOND
            return Market.A_SHARE
        if prefix in ["105", "106", "107", "153"]:
            return Market.US_STOCK
        if prefix == "116":
            return Market.HK_STOCK
        # 韩股个股：177.005930 三星电子 等
        if prefix == "177":
            return Market.KR_STOCK
        # PREFIX 100 = 东方财富全球指数（按 main_code 区分到对应市场）
        if prefix == "100":
            mc = main_code.upper()
            # 韩国：KS11 (KOSPI)、KOSPI200
            if mc in {"KS11", "KOSPI", "KOSPI200"}:
                return Market.KR_INDEX
            # 日本：N225 (日经225) 等
            if mc in {"N225", "NSE100", "TOPIX", "TPX"}:
                return Market.JP_INDEX
            # 港股：HSI (恒生指数)、HSCEI (恒生中国企业指数)，复用港股时段
            if mc in {"HSI", "HSCEI", "HSTECH"}:
                return Market.HK_STOCK
            # 美国指数：DJIA / NDX / SPX / RUT / VIX，复用美股时段
            if mc in {"DJIA", "NDX", "SPX", "RUT", "VIX"}:
                return Market.US_STOCK
            if mc == "UDI":
                return Market.FX
            # 加拿大
            if mc in {"TSX", "TSXCOMP"}:
                return Market.CA_INDEX
            # 拉美
            if mc in {"BVSP", "MXX", "RTS", "MERVAL", "IPSA"}:
                return Market.LATAM_INDEX
            # 欧洲
            if mc in {"SXXP", "SX5E", "FTSE", "FCHI", "GDAXI", "CAC40", "DAX30", "STOXX50E", "STOXX600"}:
                return Market.EU_INDEX
            # 未识别的 100.* 指数默认按美股时段处理（可能是个别未列出代码）
            return Market.US_STOCK

    # 无前缀的纯数字代码
    if code.isdigit():
        if len(code) == 6:
            if code.startswith(("01", "10", "11", "12")):
                return Market.BOND
            # 默认A股
            return Market.A_SHARE
        if len(code) == 5 and code.startswith("0"):
            return Market.HK_STOCK

    return Market.UNKNOWN


def _generate_datetime_array(sessions: List[Tuple[str, str]]) -> List[datetime.datetime]:
    """
    一个健壮的函数，根据给定的时间段列表生成分钟级别的完整时间数组。
    能够正确处理跨天的时间段，并保持正确的时间顺序。
    """
    full_datetime_array: List[datetime.datetime] = []
    delta = datetime.timedelta(minutes=1)

    for start_str, end_str in sessions:
        try:
            # 使用一个固定的日期（如1900-01-01）来创建datetime对象，以便进行时间运算
            # 这样做可以在跨天交易中保留日期顺序，例如 21:30 -> 次日 04:00。
            start_dt = datetime.datetime.strptime(start_str, "%H:%M")
            end_dt = datetime.datetime.strptime(end_str, "%H:%M")

            # 如果结束时间小于等于开始时间，说明是跨天交易，将结束日期加一天
            if end_dt <= start_dt:
                end_dt += datetime.timedelta(days=1)

            current_dt = start_dt
            while current_dt <= end_dt:
                full_datetime_array.append(current_dt)
                current_dt += delta
        except ValueError:
            continue

    return list(dict.fromkeys(full_datetime_array))


def _generate_time_array(sessions: List[Tuple[str, str]]) -> List[str]:
    """
    根据给定的时间段列表生成分钟级别的 HH:MM 时间数组。
    跨天时段会按交易顺序生成，但只保留时间部分，供兼容旧调用使用。
    """
    return [item.strftime("%H:%M") for item in _generate_datetime_array(sessions)]


def _generate_datetime_array_with_base(
    sessions: List[Tuple[str, str]],
    base_day: datetime.date,
) -> List[datetime.datetime]:
    """
    以指定的自然日（日期）为基准，生成跨天不丢失日期顺序的分钟级 datetime 数组。

    与 `_generate_datetime_array` 区别：基准日期可调，用于多市场对比场景下把不同
    跨天时段都拼接到同一个 X 轴（今天 00:00 BJT 起到次日几点）。
    """
    full_datetime_array: List[datetime.datetime] = []
    delta = datetime.timedelta(minutes=1)
    base = datetime.datetime.combine(base_day, datetime.time(0, 0))

    for start_str, end_str in sessions:
        try:
            start_dt = datetime.datetime.strptime(start_str, "%H:%M").replace(
                year=base.year, month=base.month, day=base.day
            )
            end_dt = datetime.datetime.strptime(end_str, "%H:%M").replace(
                year=base.year, month=base.month, day=base.day
            )
            if end_dt <= start_dt:
                end_dt += datetime.timedelta(days=1)
            current_dt = start_dt
            while current_dt <= end_dt:
                full_datetime_array.append(current_dt)
                current_dt += delta
        except ValueError:
            continue

    return list(dict.fromkeys(full_datetime_array))


def get_trading_minutes(code: Optional[str] = None) -> List[str]:
    """
    根据给定的东方财富代码，计算其交易时间并返回分钟级别的时间范围数组。

    Args:
        code (Optional[str]): 东方财富的标准或内部代码。
            例如: '300059', '1.600519', '106.BABA', '116.00700', 'rb2510'。
            如果code为None或无法识别，将默认返回A股交易时间。

    Returns:
        List[str]: 一个包含所有交易分钟的字符串列表，格式为 'HH:MM'。
    """
    return [item[-5:] for item in get_trading_datetimes(code)]


def get_trading_datetimes(code: Optional[str] = None) -> List[str]:
    """
    根据给定的东方财富代码，计算其交易时间并返回分钟级别的完整时间范围数组。
    跨天时段会保留日期偏移，避免 00:00~05:00 被排序到 21:30 前面。

    Args:
        code (Optional[str]): 东方财富的标准或内部代码。
            例如: '300059', '1.600519', '106.BABA', '116.00700', 'rb2510'。
            如果code为None或无法识别，将默认返回A股交易时间。

    Returns:
        List[str]: 一个包含所有交易分钟的字符串列表，格式为 'YYYY-MM-DD HH:MM'。
    """
    market = _market_of_code(code)
    sessions = get_market_sessions(market)

    return [item.strftime("%Y-%m-%d %H:%M") for item in _generate_datetime_array(sessions)]


def get_session_anchor_date(
    code: Optional[str] = None,
    now_bjt: Optional[datetime.datetime] = None,
) -> datetime.date:
    """返回「当前/最近一个交易时段」在 BJT 下的起始自然日。

    跨天时段（如美股 21:30-04:00、美期 06:00-次日 05:00）在过了 0 点后，
    会话其实是**昨天**开盘的；若仍用 today 作 base，会把已发生的
    06:00-17:00 数据错误地映射到「次日」。
    """
    now_bjt = as_naive_bjt(now_bjt)
    market = _market_of_code(code)
    sessions = get_market_sessions(market, now_bjt)
    current_time = now_bjt.time()
    today = now_bjt.date()

    for start_str, end_str in sessions:
        start = datetime.datetime.strptime(start_str, "%H:%M").time()
        end = datetime.datetime.strptime(end_str, "%H:%M").time()
        if start <= end:
            # 同日时段：始终锚定今天
            continue
        # 跨天：start~24:00 属于开盘日 D；00:00~end 属于 D+1 但仍属会话 D
        if current_time <= end:
            return today - datetime.timedelta(days=1)
    return today


def get_trading_datetimes_bjt(
    code: Optional[str] = None,
    now_bjt: Optional[datetime.datetime] = None,
) -> List[datetime.datetime]:
    """
    返回某个市场「当前/最近交易时段」的全部交易分钟 datetime 列表（BJT）。

    与 `get_trading_datetimes` 的差别：
    - 以会话锚定日（见 ``get_session_anchor_date``）为基准，跨天时段会进位到次日；
    - 过了 0 点仍处在夜盘/美期会话时，基准是**昨天**，避免把已发生的
      上午/下午分时错贴到「次日」；
    - 同一调用中传入不同市场时，各市场的交易时间在 X 轴上能按 BJT 绝对时间正确拼接；
    - 适合多市场对比场景（multi-stock / compare-stock）。
    """
    now_bjt = as_naive_bjt(now_bjt)
    market = _market_of_code(code)
    sessions = get_market_sessions(market, now_bjt)
    base_day = get_session_anchor_date(code, now_bjt=now_bjt)
    return _generate_datetime_array_with_base(sessions, base_day)


def is_market_active_now(
    code: Optional[str] = None,
    now_bjt: Optional[datetime.datetime] = None,
) -> bool:
    """
    判断某市场在给定 BJT 时刻是否处于交易时段内。

    时段为左闭右开 [start, end)：官方收盘时刻（如 A 股 15:00、港股 16:00）已收盘。
    适用于多市场对比场景：当某市场当前不在交易时段（如日间的美股、夜间的 A 股）
    时，该市场该日**暂未开盘**，其分时数据在 X 轴上会被置空。
    """
    now_bjt = as_naive_bjt(now_bjt)
    market = _market_of_code(code)
    if market == Market.CRYPTO:
        return True
    from .market_holidays import is_market_holiday

    if is_market_holiday(market, now_bjt):
        return False
    sessions = get_market_sessions(market, now_bjt)
    current = now_bjt.time()
    weekday = now_bjt.weekday()
    if weekday == 6:
        return False
    if weekday == 5:
        return _in_overnight_tail(current, sessions) and _time_in_sessions(current, sessions, end_closed=False)
    return _time_in_sessions(current, sessions, end_closed=False)


def _time_in_sessions(
    current: datetime.time,
    sessions: List[Tuple[str, str]],
    *,
    end_closed: bool,
) -> bool:
    """end_closed=False 时为 [start, end)，收盘整点不算在交易。"""
    for start_str, end_str in sessions:
        start = datetime.datetime.strptime(start_str, "%H:%M").time()
        end = datetime.datetime.strptime(end_str, "%H:%M").time()
        if start <= end:
            hit = start <= current <= end if end_closed else start <= current < end
        elif end_closed:
            hit = current >= start or current <= end
        else:
            hit = current >= start or current < end
        if hit:
            return True
    return False


def _in_overnight_tail(current: datetime.time, sessions: List[Tuple[str, str]]) -> bool:
    for start_str, end_str in sessions:
        start = datetime.datetime.strptime(start_str, "%H:%M").time()
        end = datetime.datetime.strptime(end_str, "%H:%M").time()
        if start > end and current <= end:
            return True
    return False


def has_session_started_today(
    code: Optional[str] = None,
    now_bjt: Optional[datetime.datetime] = None,
) -> bool:
    """今日该市场是否已经开过盘（含盘中 / 已收盘）。

    还没到今日开盘、或周末休市时返回 False，此时行情仍是上一交易日。
    加密货币 7×24，周末不休。
    外汇 24×5：周日全天休，周六仅周五夜盘收到凌晨；节假日见 market_holidays。
    其余市场周日休市；周六仅周五夜盘跨到凌晨的时段仍算开盘。
    """
    now_bjt = as_naive_bjt(now_bjt)
    weekday = now_bjt.weekday()
    current = now_bjt.time()
    market = _market_of_code(code)
    if market == Market.CRYPTO:
        return True
    from .market_holidays import is_market_holiday

    if is_market_holiday(market, now_bjt):
        return False
    sessions = get_market_sessions(market, now_bjt)
    if weekday == 6:
        return False
    if weekday == 5:
        return _in_overnight_tail(current, sessions)
    if _time_in_sessions(current, sessions, end_closed=True):
        if _in_overnight_tail(current, sessions):
            return weekday in {1, 2, 3, 4, 5}
        return True
    starts = [datetime.datetime.strptime(item[0], "%H:%M").time() for item in sessions]
    if not starts:
        return True
    return current >= min(starts)


def is_within_trading_day_window(
    code: Optional[str] = None,
    now_bjt: Optional[datetime.datetime] = None,
) -> bool:
    """是否处于「当前会话日」的交易日窗口内（含午休等盘中休市）。

    与 ``is_market_active_now`` 的差别：
    - 午休（如 A 股 11:30–13:00）返回 True；
    - 用于分时图把 X 轴补齐到当日收盘，而不是只画到「此刻」。

    判断依据是 ``get_trading_datetimes_bjt`` 的首尾绝对时间，跨天会话同样适用。
    """
    now_bjt = as_naive_bjt(now_bjt)
    times = get_trading_datetimes_bjt(code, now_bjt=now_bjt)
    if not times:
        return False
    return times[0] <= now_bjt <= times[-1]


def parse_time_range(text: str) -> Tuple[Optional[datetime.datetime], Optional[datetime.datetime], str]:
    """从输入文本中解析时间范围。

    支持中文相对时间描述和具体日期格式。

    Args:
        text: 原始输入文本

    Returns:
        (start_time, end_time, cleaned_text)
        start_time/end_time 为 None 表示未指定时间范围

    Raises:
        ValueError: 日期格式错误时抛出，附带错误提示信息
    """
    if "最近一年" in text or "近一年" in text or "过去一年" in text:
        text = text.replace("最近一年", "").replace("近一年", "").replace("过去一年", "").strip()
        start_time = datetime.datetime.now() - datetime.timedelta(days=365)
        end_time = datetime.datetime.now()
        return start_time, end_time, text
    elif "最近一月" in text or "近一月" in text or "过去一月" in text:
        text = text.replace("最近一月", "").replace("近一月", "").replace("过去一月", "").strip()
        start_time = datetime.datetime.now() - datetime.timedelta(days=30)
        end_time = datetime.datetime.now()
        return start_time, end_time, text
    elif "年初至今" in text or "今年以来" in text or "今年" in text:
        text = text.replace("年初至今", "").replace("今年以来", "").replace("今年", "").strip()
        start_time = datetime.datetime(datetime.datetime.now().year, 1, 1)
        end_time = datetime.datetime.now()
        return start_time, end_time, text
    else:
        p = r"(\d{4}[./]\d{1,2}[./]\d{1,2})(?:[~-](\d{4}[./]\d{1,2}[./]\d{1,2}))?"
        match = re.search(p, text)
        if match:
            try:
                start_str, end_str = match.groups()
                start_time = datetime.datetime.strptime(re.sub(r"[./]", "-", start_str), "%Y-%m-%d")
                end_time = (
                    datetime.datetime.strptime(re.sub(r"[./]", "-", end_str), "%Y-%m-%d")
                    if end_str
                    else datetime.datetime.now()
                )
                text = re.sub(p, "", text).strip()
                return start_time, end_time, text
            except ValueError as e:
                raise ValueError("日期格式错误，请使用正确的日期格式如 2024.12.05 或 2024/12/5") from e

    return None, None, text
