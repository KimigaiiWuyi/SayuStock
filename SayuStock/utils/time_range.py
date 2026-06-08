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
    BOND = auto()
    UNKNOWN = auto()
    COMMODITY = auto()  # 商品期货
    SPOT = auto()  # 现货
    TLM = auto()  # TLM
    COMMODITY_SPOT = auto()  # 商品现货
    CRYPTO = auto()  # 加密货币
    # —— 东方财富 PREFIX 100 开头的全球指数（按各自主市场时段）——
    # 备注：美股指数（道琼/纳指/标普）复用 US_STOCK；恒生指数复用 HK_STOCK
    KR_INDEX = auto()  # 韩国交易所指数（KOSPI/KOSPI200）
    JP_INDEX = auto()  # 日本交易所指数（日经225 等）
    CA_INDEX = auto()  # 加拿大 S&P/TSX
    LATAM_INDEX = auto()  # 拉美指数（巴西/墨西哥/俄罗斯等）
    EU_INDEX = auto()  # 欧洲指数（SX5E/FTSE/CAC/DAX）


def is_us_daylight_saving() -> bool:
    """
    判断当前是否为美国夏令时，返回True或False。
    这决定了美股在北京时间的开盘是21:30还是22:30。
    """
    us_tz = zoneinfo.ZoneInfo("America/New_York")
    now_in_us = datetime.datetime.now(us_tz)
    return now_in_us.dst() != datetime.timedelta(0)


# 交易时间段配置（北京时间, UTC+8）
# 格式：[(开始时间, 结束时间), (开始时间, 结束时间), ...]
# 注意：对于跨天时段，如 '21:00' 到 '02:30'，我们的生成函数会自动处理。
MARKET_SESSIONS: Dict[Market, List[Tuple[str, str]]] = {
    Market.A_SHARE: [
        ("09:30", "11:30"),
        ("13:00", "15:00"),
    ],
    Market.HK_STOCK: [
        ("09:30", "12:00"),
        ("13:00", "16:00"),
    ],
    # 动态判断美股的交易时间
    Market.US_STOCK: [("21:30", "04:00") if is_us_daylight_saving() else ("22:30", "05:00")],
    Market.CN_FUTURE_DAY: [  # 大部分日盘品种
        ("09:00", "10:15"),
        ("10:30", "11:30"),
        ("13:30", "15:00"),
    ],
    Market.CN_FUTURE_NIGHT: [  # 黑色系、有色金属等夜盘
        ("21:00", "02:30"),
        # 注意：不同期货品种的夜盘时间有差异，这里仅为通用示例
        # 实际应用中可能需要根据具体合约代码（如 'rb', 'ag'）进一步细化
    ],
    Market.US_FUTURE: [  # 美国期货如 nq（纳斯达克）、es（标普500）等
        ("18:00", "17:00"),
    ],
    Market.SG_FUTURE: [  # 例如富时中国A50指数期货 (CN)
        ("09:00", "16:30"),
        ("17:00", "05:15"),
    ],
    Market.BOND: [  # 国债交易时间与A股基本一致
        ("09:30", "11:30"),
        ("13:00", "15:00"),
    ],
    Market.COMMODITY: [  # 商品期货
        ("06:00", "05:00") if is_us_daylight_saving() else ("07:00", "06:00"),
    ],
    Market.SPOT: [  # 现货
        ("09:00", "15:30"),
        ("20:00", "02:30"),
    ],
    Market.TLM: [  # TLM
        ("09:30", "11:30"),
        ("13:00", "15:15"),
    ],
    Market.COMMODITY_SPOT: [  # 商品现货
        ("06:00", "05:15") if is_us_daylight_saving() else ("07:00", "06:15"),
    ],
    Market.CRYPTO: [  # 加密货币
        ("00:00", "23:59"),
    ],
    # —— 全球指数（东方财富 PREFIX 100）：按各自主市场本土开收盘 —— #
    # 韩国（KST = UTC+9，无夏令时；BJT 比 KST 晚 1 小时 = UTC+8）
    # KOSPI / KOSPI200 实际：09:00-15:30 KST = 08:00-14:30 BJT
    Market.KR_INDEX: [
        ("08:00", "14:30"),
    ],
    # 日本（JST = UTC+9，无夏令时；BJT 比 JST 晚 1 小时 = UTC+8）
    # 日经 225 等：09:00-15:00 JST（午休 11:30-12:30）= 08:00-14:00 BJT
    # 这里取 08:00-14:00 作为参考时段
    Market.JP_INDEX: [
        ("08:00", "14:00"),
    ],
    # 恒生指数与港股交易时段一致，复用 HK_STOCK
    # 美国指数（道琼/纳指/标普）与美股时段一致，复用 US_STOCK
    # 加拿大 S&P/TSX（多伦多，ET 同美股）：09:30-16:00 ET = 22:30/23:30-05:00/06:00 BJT
    Market.CA_INDEX: [("22:30", "05:00") if is_us_daylight_saving() else ("23:30", "06:00")],
    # 拉美（巴西/墨西哥）：使用美股时间作为兜底
    Market.LATAM_INDEX: [("21:30", "04:00") if is_us_daylight_saving() else ("22:30", "05:00")],
    # 欧洲指数（伦敦/巴黎/法兰克福）：冬令时 16:00-00:30 BJT；夏令时 15:00-23:30 BJT
    # 这里参考 US_STOCK 的夏冬令判断做偏移
    Market.EU_INDEX: [("15:00", "23:30") if is_us_daylight_saving() else ("16:00", "00:30")],
}


def _parse_em_code(code: str) -> Market:
    """
    解析东方财富代码，返回其所属的市场枚举类型。
    """
    if "crypto" in code:
        return Market.CRYPTO

    code = code.split("_")[0]

    if not isinstance(code, str) or not code:
        return Market.UNKNOWN

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
        if prefix in ["101", "102", "171"]:
            return Market.COMMODITY
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
            # 加拿大
            if mc in {"TSX", "TSXCOMP"}:
                return Market.CA_INDEX
            # 拉美
            if mc in {"BVSP", "MXX", "RTS", "MERVAL", "IPSA"}:
                return Market.LATAM_INDEX
            # 欧洲
            if mc in {"SX5E", "FTSE", "FCHI", "GDAXI", "CAC40", "DAX30", "STOXX50E"}:
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
    market = _parse_em_code(code) if code else Market.A_SHARE

    # 如果市场未知，默认返回A股时间
    if market == Market.UNKNOWN:
        market = Market.A_SHARE

    sessions = MARKET_SESSIONS.get(market, MARKET_SESSIONS[Market.A_SHARE])

    return [item.strftime("%Y-%m-%d %H:%M") for item in _generate_datetime_array(sessions)]


def get_trading_datetimes_bjt(
    code: Optional[str] = None,
    now_bjt: Optional[datetime.datetime] = None,
) -> List[datetime.datetime]:
    """
    返回某个市场在本 BJT 日 (00:00 起) 的全部交易分钟 datetime 列表。

    与 `get_trading_datetimes` 的差别：
    - 该函数以**今天 BJT 00:00** 作为基准日，跨天时段会进位到次日；
    - 同一调用中传入不同市场时，各市场的交易时间在 X 轴上能按 BJT 小时正确拼接
      （例如美股夏令时 21:30-04:00 会接在 21:30-04:00 BJT 的右端）；
    - 适合多市场对比场景（multi-stock / compare-stock）。
    """
    if now_bjt is None:
        now_bjt = datetime.datetime.now()
    market = _parse_em_code(code) if code else Market.A_SHARE
    if market == Market.UNKNOWN:
        market = Market.A_SHARE
    sessions = MARKET_SESSIONS.get(market, MARKET_SESSIONS[Market.A_SHARE])
    return _generate_datetime_array_with_base(sessions, now_bjt.date())


def is_market_active_now(
    code: Optional[str] = None,
    now_bjt: Optional[datetime.datetime] = None,
) -> bool:
    """
    判断某市场在给定 BJT 时刻是否处于交易时段内。

    适用于多市场对比场景：当某市场当前不在交易时段（如日间的美股、夜间的 A 股）
    时，该市场该日**暂未开盘**，其分时数据在 X 轴上会被置空。
    """
    if now_bjt is None:
        now_bjt = datetime.datetime.now()
    market = _parse_em_code(code) if code else Market.A_SHARE
    if market == Market.UNKNOWN:
        market = Market.A_SHARE
    sessions = MARKET_SESSIONS.get(market, MARKET_SESSIONS[Market.A_SHARE])

    # 为兼容动态调用的夏冬令，重复计算一次获取实足配置
    if market == Market.US_STOCK and not sessions[0][0] == ("21:30" if is_us_daylight_saving() else "22:30"):
        sessions = MARKET_SESSIONS[market]

    current_time = now_bjt.time()
    for start_str, end_str in sessions:
        start = datetime.datetime.strptime(start_str, "%H:%M").time()
        end = datetime.datetime.strptime(end_str, "%H:%M").time()
        if start <= end:
            if start <= current_time <= end:
                return True
        else:
            # 跨天：now 落在 start~24:00 或 00:00~end 都视为活跃
            if current_time >= start or current_time <= end:
                return True
    return False


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
