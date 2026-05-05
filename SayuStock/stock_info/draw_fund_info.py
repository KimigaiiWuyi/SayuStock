from typing import Union

from PIL import Image, ImageDraw

from gsuid_core.logger import logger
from gsuid_core.utils.fonts.fonts import core_font as ss_font
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..utils.image import get_footer
from .draw_my_info import DIFF_MAP, TEXT_PATH, draw_bar
from ..utils.stock.request import get_gg
from ..utils.stock.request_utils import get_code_id, get_fund_pos_list


async def draw_fund_info(fcode: Union[str, int]):
    _code = await get_code_id(str(fcode))
    if _code is None:
        return "基金代码有误，请检查后重新输入~"

    dcode = _code[0].split(".")[1]
    fund_data = await get_fund_pos_list(dcode)
    if fund_data is None or not fund_data["Datas"]:
        return "获取基金持仓数据失败，请稍后再试~"

    img = Image.new(
        "RGBA",
        (900, 400 + 60 + len(fund_data["Datas"]) * 110),
        (7, 9, 27),
    )
    img_draw = ImageDraw.Draw(img)
    img_draw.text(
        (450, 355),
        f"{_code[1]}({_code[0]})持仓信息",
        (255, 255, 255),
        ss_font(36),
        "mm",
    )

    all_p = 0.0
    for index, d in enumerate(fund_data["Datas"]):
        share_code: str = d["ShareCode"]
        data = await get_gg(
            share_code,
            "single-stock",
        )
        percent = f"{d['ShareProportion']}%"
        if isinstance(data, str):
            continue
        bar = draw_bar(data, _code[0], percent=percent)
        if isinstance(data["data"]["f170"], str):
            all_p += 0
        else:
            all_p += data["data"]["f170"]
        img.paste(bar, (0, 400 + index * 110), bar)

    avg_p = all_p / len(fund_data["Datas"])
    for i in DIFF_MAP:
        if avg_p >= i:
            title_num = DIFF_MAP[i]
            break
    else:
        title_num = 11

    title = Image.open(TEXT_PATH / f"title{title_num}.png")
    img.paste(
        title,
        (25, -31),
        title,
    )

    footer = get_footer()
    img.paste(
        footer,
        (25, img.size[1] - 55),
        footer,
    )

    res = await convert_img(img)

    # AI 注入：提取基金持仓文本数据
    _ai_return_fund_info(_code, fund_data, all_p)

    return res


def _ai_return_fund_info(code_info, fund_data, all_p):
    """从基金持仓数据中提取文本信息，通过 ai_return 返回给 AI 分析"""
    try:
        fund_name = code_info[1]
        fund_code = code_info[0]
        holdings = fund_data.get("Datas", [])
        avg_p = all_p / len(holdings) if holdings else 0

        result = f"【{fund_name}({fund_code}) 持仓信息】\n"
        result += f"持仓数量: {len(holdings)} 只，平均涨跌幅: {avg_p:+.2f}%\n\n"
        result += "重仓股:\n"
        for d in holdings[:10]:
            name = d.get("ShareName", "N/A")
            code = d.get("ShareCode", "N/A")
            proportion = d.get("ShareProportion", "N/A")
            result += f"  {name}({code}): 占比 {proportion}%\n"

        ai_return(result)
    except Exception as e:
        logger.warning(f"[SayuStock] ai_return 基金持仓数据提取失败: {e}")
