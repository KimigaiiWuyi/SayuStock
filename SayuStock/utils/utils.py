import json
from typing import List, Optional
from datetime import datetime, timedelta

from gsuid_core.logger import logger

from .resource_path import DATA_PATH, HISTORY_PATH


def convert_list(input_list: List[str]) -> List[str]:
    result = []
    for item in input_list:
        if '.' not in item and result:  # 当前项不含点且结果列表不为空
            result[-1] += '_' + item  # 合并到前一项
        else:
            result.append(item)  # 正常添加项
    input_list = result
    return input_list


def get_file(
    market: str,
    suffix: str,
    sector: Optional[str] = None,
    sp: Optional[str] = None,
):
    a = f'{market}_{sector}_{sp}_data'
    a = a[:254]
    return DATA_PATH / f"{a}.{suffix}"


def get_adjusted_date():
    now = datetime.now()
    target_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    # 判断当前时间是否在当天的9:30之前
    if now < target_time:
        adjusted_date = now.replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=1)
    else:
        adjusted_date = now
    return adjusted_date


def calculate_difference(data: List[str]):
    # 获取今天的日期
    today = get_adjusted_date()

    date_dict = {}
    for item in data:
        item_part = item.split(',')
        date_day = datetime.strptime(item_part[0], "%Y-%m-%d %H:%M")
        if date_day.day not in date_dict:
            date_dict[date_day.day] = []
        date_dict[date_day.day].append(float(item_part[6]))

    for _ in range(4):
        if today.day not in date_dict:
            today = today - timedelta(days=1)
        else:
            break
    else:
        return 0

    logger.info(f"[SayuStock]今天交易日: {today}")
    all_today_data = sum(date_dict[today.day])
    all_today_len = len(date_dict[today.day])
    del date_dict[today.day]

    all_yestoday_data = sum(list(date_dict.values())[0][:all_today_len])
    return all_today_data - all_yestoday_data


def number_to_chinese(num: float):
    """
    将大数字转换为保留两位小数的汉字形式
    :param num: 输入的浮点数
    :return: 转换后的汉字字符串
    """
    if num < 0:
        return "不支持负数"

    # 定义单位
    units = ["", "万", "亿", "万亿"]
    unit_index = 0

    # 将数字缩小到合适的单位
    while num >= 10000 and unit_index < len(units) - 1:
        num /= 10000
        unit_index += 1

    # 保留两位小数
    num_rounded = round(num, 2)

    # 转换为字符串并去掉末尾的".00"（如果存在）
    result = f"{num_rounded}{units[unit_index]}"
    if result.endswith(".00"):
        result = result[:-3] + units[unit_index]

    return result


def save_history(num: float):
    date_str = get_adjusted_date().strftime("%Y-%-m-%-d")
    data = {date_str: num}

    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, "r", encoding="utf-8") as file:
            existing_data = json.load(file)
    else:
        existing_data = {}

    # 更新数据
    existing_data.update(data)

    # 保存更新后的数据
    with open(HISTORY_PATH, "w", encoding="utf-8") as file:
        json.dump(existing_data, file, ensure_ascii=False, indent=4)


def get_history() -> float:
    # 如果文件不存在，直接返回0
    if not HISTORY_PATH.exists():
        return 0

    # 读取JSON文件
    with open(HISTORY_PATH, "r", encoding="utf-8") as file:
        data = json.load(file)

    # 获取当前时间并调整日期
    current_date = get_adjusted_date()

    # 回溯查找前一天的num数据
    while True:
        # 计算前一天的日期
        current_date -= timedelta(days=1)
        date_str = current_date.strftime("%Y-%-m-%-d")

        # 如果日期不在数据中，返回0
        if date_str not in data:
            return 0

        # 如果找到非零数据，返回该数据
        if data[date_str] != 0:
            return data[date_str]

        # 如果数据为0，继续回溯
        continue
