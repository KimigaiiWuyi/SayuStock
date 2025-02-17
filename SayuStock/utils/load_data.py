import json
from pathlib import Path
from decimal import Decimal

output_data = Path(__file__).parent / 'output.json'

with output_data.open('r', encoding='utf-8') as f:
    mdata = json.load(f)

SZMarket = "SA"
SHMarket = "HA"


def get_market(code: str) -> str:
    """
    根据证券代码的前缀判断其所属市场。

    Args:
        code (str): 证券代码

    Returns:
        str: 所属市场，"SA" 表示深证市场，"HA" 表示上证市场

    Raises:
        ValueError: 如果证券代码不支持
    """
    if code.startswith("60") or code.startswith("51") or code.startswith("68"):
        return SHMarket
    if code.startswith("00") or code.startswith("15") or code.startswith("30"):
        return SZMarket
    raise ValueError("暂未支持的证券代码")


def get_full_security_code(code: str) -> str:
    """
    获取完整的证券代码。
    上证基金代码以50、51、52开头，
    深证基金代码以15、16、18开头。

    Args:
        code (str): 证券代码

    Returns:
        str: 完整的证券代码，格式为 "1.代码" 或 "0.代码"

    Raises:
        ValueError: 如果证券代码不支持
    """
    if code.startswith("0.") or code.startswith("1."):
        return code
    if code.startswith("51") or code.startswith("60") or code.startswith("68"):
        return "1." + code
    if code.startswith("1") or code.startswith("0") or code.startswith("3"):
        return "0." + code
    raise ValueError("暂未支持的证券代码")


def get_price_magnification(code: str) -> float:
    """
    获取价格倍率。

    Args:
        code (str): 证券代码

    Returns:
        float: 价格倍率，ETF 为 1000.0，个股为 100.0
    """
    if code.startswith("5") or code.startswith("1"):
        return 1000.0
    return 100.0


def is_etf(code: str) -> bool:
    """
    判断是否为ETF。

    Args:
        code (str): 证券代码

    Returns:
        bool: 如果是ETF返回 True，否则返回 False
    """
    return code.startswith("5") or code.startswith("1")


def get_code_market(code: str) -> str:
    """
    获取股票所在的板块。

    Args:
        code (str): 证券代码

    Returns:
        str: 所在板块，"1" 表示上证，"0" 表示深证

    Raises:
        ValueError: 如果证券代码不支持
    """
    if code.startswith("5") or code.startswith("6"):
        return "1"
    if code.startswith("1") or code.startswith("0") or code.startswith("3"):
        return "0"
    raise ValueError("unsupport security code")


def get_nearest_hundredfold_int(num: float) -> int:
    """
    获取最接近的整百数。

    Args:
        num (float): 输入的浮点数

    Returns:
        int: 最接近的整百数
    """
    return int((Decimal(num).to_integral_value() // 100) * 100)
