from typing import List, Union


def convert_list(input_list: List[str]) -> List[str]:
    result = []
    for item in input_list:
        if '.' not in item and result:  # 当前项不含点且结果列表不为空
            result[-1] += '_' + item  # 合并到前一项
        else:
            result.append(item)  # 正常添加项
    input_list = result
    return input_list


def int_to_percentage(value: Union[int, str, float]) -> str:
    if isinstance(value, str):
        return '-%'
    sign = '+' if value >= 0 else ''
    return f"{sign}{value:.2f}%"


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
