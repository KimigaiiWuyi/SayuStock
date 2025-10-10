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
    CN_FUTURE_DAY = auto()  # 中国日盘期货（通用）
    CN_FUTURE_NIGHT = auto()  # 中国夜盘期货（通用，如金属、能源）
    SG_FUTURE = auto()  # 新加坡期货（如A50）
    BOND = auto()
    UNKNOWN = auto()
    COMMODITY = auto()  # 商品期货
    SPOT = auto()  # 现货


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
        ('09:30', '11:30'),
        ('13:00', '15:00'),
    ],
    Market.HK_STOCK: [
        ('09:30', '12:00'),
        ('13:00', '16:00'),
    ],
    # 动态判断美股的交易时间
    Market.US_STOCK: [
        ('21:30', '04:00') if is_us_daylight_saving() else ('22:30', '05:00')
    ],
    Market.CN_FUTURE_DAY: [  # 大部分日盘品种
        ('09:00', '10:15'),
        ('10:30', '11:30'),
        ('13:30', '15:00'),
    ],
    Market.CN_FUTURE_NIGHT: [  # 黑色系、有色金属等夜盘
        ('21:00', '02:30'),
        # 注意：不同期货品种的夜盘时间有差异，这里仅为通用示例
        # 实际应用中可能需要根据具体合约代码（如 'rb', 'ag'）进一步细化
    ],
    Market.SG_FUTURE: [  # 例如富时中国A50指数期货 (CN)
        ('09:00', '16:30'),
        ('17:00', '05:15'),
    ],
    Market.BOND: [  # 国债交易时间与A股基本一致
        ('09:30', '11:30'),
        ('13:00', '15:00'),
    ],
    Market.COMMODITY: [  # 商品期货
        ('06:00', '05:00') if is_us_daylight_saving() else ('07:00', '06:00'),
    ],
    Market.SPOT: [  # 现货
        ('09:00', '15:30'),
        ('20:00', '02:30'),
    ],
}


def _parse_em_code(code: str) -> Market:
    """
    解析东方财富代码，返回其所属的市场枚举类型。
    """
    if not isinstance(code, str) or not code:
        return Market.UNKNOWN

    # 优先匹配期货代码（通常是字母+数字）
    # e.g., 'rb2510', 'ag2512', 'IF2508'
    if re.fullmatch(r'^[a-zA-Z]{1,2}\d{4}$', code):
        # 简化处理：假设字母开头的都是期货。可根据首字母进一步细分日夜盘。
        # 例如，'rb', 'ag', 'au', 'cu' 等通常有夜盘
        if code.lower().startswith(
            ('rb', 'ag', 'au', 'cu', 'zn', 'al', 'pb', 'ni', 'sn', 'sc', 'lu')
        ):
            return Market.CN_FUTURE_NIGHT
        return Market.CN_FUTURE_DAY

    # 新加坡A50指数期货
    if code.upper() == 'CN':
        return Market.SG_FUTURE

    # 带市场前缀的代码 (e.g., '1.600519', '106.BABA')
    if '.' in code:
        prefix, main_code = code.split('.', 1)
        if prefix in ['118']:
            return Market.SPOT
        if prefix in ['101', '102']:
            return Market.COMMODITY
        if prefix in ['0', '1']:
            # 进一步判断是股票还是债券
            if main_code.startswith(('01', '10', '11', '12')):
                return Market.BOND
            return Market.A_SHARE
        if prefix in ['105', '106', '107', '153']:
            return Market.US_STOCK
        if prefix == '116':
            return Market.HK_STOCK

    # 无前缀的纯数字代码
    if code.isdigit():
        if len(code) == 6:
            if code.startswith(('01', '10', '11', '12')):
                return Market.BOND
            # 默认A股
            return Market.A_SHARE
        if len(code) == 5 and code.startswith('0'):
            return Market.HK_STOCK

    return Market.UNKNOWN


def _generate_time_array(sessions: List[Tuple[str, str]]) -> List[str]:
    """
    一个健壮的函数，根据给定的时间段列表生成分钟级别的时间数组。
    能够正确处理跨天的时间段，并保持正确的时间顺序。
    """
    full_time_array = []
    delta = datetime.timedelta(minutes=1)

    for start_str, end_str in sessions:
        try:
            # 使用一个固定的日期（如1900-01-01）来创建datetime对象，以便进行时间运算
            # 这样做可以避免日期变化带来的干扰
            start_dt = datetime.datetime.strptime(start_str, '%H:%M')
            end_dt = datetime.datetime.strptime(end_str, '%H:%M')

            # 如果结束时间小于等于开始时间，说明是跨天交易，将结束日期加一天
            if end_dt <= start_dt:
                end_dt += datetime.timedelta(days=1)

            current_dt = start_dt
            while current_dt <= end_dt:
                full_time_array.append(current_dt.strftime('%H:%M'))
                current_dt += delta
        except ValueError:
            continue

    return list(dict.fromkeys(full_time_array))


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
    market = _parse_em_code(code) if code else Market.A_SHARE

    # 如果市场未知，默认返回A股时间
    if market == Market.UNKNOWN:
        market = Market.A_SHARE

    sessions = MARKET_SESSIONS.get(market, MARKET_SESSIONS[Market.A_SHARE])

    return _generate_time_array(sessions)
