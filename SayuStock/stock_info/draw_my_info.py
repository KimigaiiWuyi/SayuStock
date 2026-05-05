import asyncio
from typing import Dict, Optional
from pathlib import Path

from PIL import Image, ImageDraw

from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.utils.fonts.fonts import core_font as ss_font
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..utils.image import get_footer
from ..utils.utils import convert_list, number_to_chinese
from ..utils.stock.request import get_gg, get_vix, get_mtdata
from ..stock_info.draw_info import DIFF_MAP
from ..utils.database.models import SsBind

TEXT_PATH = Path(__file__).parent / "texture2d"


def draw_bar(
    data: Dict,
    u: str,
    percent: Optional[str] = None,
) -> Image.Image:
    mark_data: dict = data["data"]
    if isinstance(mark_data["f48"], str):
        e_money = mark_data["f48"]
    else:
        e_money = number_to_chinese(mark_data["f48"])
    hs = mark_data["f168"]
    if isinstance(mark_data["f170"], str):
        p = 0
    else:
        p = mark_data["f170"]

    now_price = mark_data["f43"]

    b_title = f"{mark_data['f58']}"
    s_title = f"({u}) 换: {hs}% 额: {e_money} 价: {now_price}"
    if p > 0:
        bar = Image.open(TEXT_PATH / "myup.png")
        p_color = (213, 102, 102)
    elif p == 0:
        bar = Image.open(TEXT_PATH / "myeq.png")
        p_color = (240, 240, 240)
    else:
        bar = Image.open(TEXT_PATH / "mydown.png")
        p_color = (175, 231, 170)
    bar_draw = ImageDraw.Draw(bar)
    bar_draw.text(
        (82, 40),
        b_title,
        (255, 255, 255),
        ss_font(32),
        "lm",
    )
    bar_draw.text(
        (82, 75),
        s_title,
        p_color,
        ss_font(20),
        "lm",
    )
    bar_draw.text(
        (758, 55),
        f"+{p}%" if p >= 0 else f"{p}%",
        (255, 255, 255),
        ss_font(28),
        "mm",
    )
    if percent is not None:
        bar_draw.text(
            (613, 55),
            percent,
            (240, 240, 240),
            ss_font(28),
            "mm",
        )
    return bar


async def draw_my_stock_img(ev: Event):
    user_id = ev.at if ev.at else ev.user_id
    uid = await SsBind.get_uid_list_by_game(user_id, ev.bot_id)

    if not uid:
        return "您还未添加自选呢~请输入 添加自选 查看帮助!"

    uid = convert_list(uid)
    data_zs = await get_mtdata("主要指数", pz=100)
    data_hy = await get_mtdata("行业板块")

    if isinstance(data_zs, str):
        return data_zs
    if isinstance(data_hy, str):
        return data_hy

    img = Image.new(
        "RGBA",
        (
            900 if len(uid) < 18 else 1800,
            (541 + len(uid) * 110 + 60 if len(uid) < 18 else 541 + (((len(uid) - 1) // 2) + 1) * 110 + 60),
        ),
        (7, 9, 27),
    )
    zyzs = (
        [
            "上证指数",
            "深证成指",
            "中证A500",
            "中证2000",
        ]
        if len(uid) < 18
        else [
            "上证指数",
            "深证成指",
            "创业板指",
            "上证50",
            "沪深300",
            "中证A500",
            "中证2000",
            "国债指数",
        ]
    )

    # 主要指数
    n = 0
    x0 = 50 if len(uid) < 18 else 100
    for zs_name in zyzs:
        for zs_diff in data_zs["data"]["diff"]:
            if zs_name != zs_diff["f14"]:
                continue
            diff = zs_diff["f3"]
            zs_img = Image.new("RGBA", (200, 140))
            zs_draw = ImageDraw.Draw(zs_img)
            if diff >= 0:
                zsc = (140, 18, 22, 55)
                zsc2 = (206, 34, 30)
            else:
                zsc = (59, 140, 18, 55)
                zsc2 = (36, 206, 30)

            zs_draw.rounded_rectangle((15, 13, 185, 127), 0, zsc)

            zs_draw.text(
                (100, 99),
                f"{zs_diff['f14']}",
                (255, 255, 255),
                ss_font(24),
                "mm",
            )

            zs_draw.text(
                (100, 38),
                f"{zs_diff['f2']}",
                zsc2,
                ss_font(30),
                "mm",
            )

            zs_draw.text(
                (100, 70),
                f"{'+' if diff >= 0 else ''}{diff}%",
                zsc2,
                ss_font(30),
                "mm",
            )
            img.paste(
                zs_img,
                (x0 + 200 * n, 308 + 140 * 0),
                zs_img,
            )
            n += 1

    all_p = 0
    stock_details = []  # 收集每只股票的详细数据
    TASK = []

    async def sg(img: Image.Image, index: int, u: str, alluid: int):
        nonlocal all_p

        if u.startswith("VIX."):
            data = await get_vix(u[4:])
        else:
            data = await get_gg(u, "single-stock")

        if isinstance(data, str):
            return data

        if isinstance(data["data"]["f170"], str):
            all_p += 0
        else:
            all_p += data["data"]["f170"]

        # 收集股票详细数据用于 ai_return
        d = data.get("data", {})
        stock_details.append(
            {
                "code": u,
                "name": d.get("f58", "N/A"),
                "price": d.get("f43", "N/A"),
                "change": d.get("f170", 0),
                "turnover": d.get("f168", "N/A"),
                "amount": d.get("f48", "N/A"),
            }
        )

        bar = draw_bar(data, u)

        if alluid >= 18 and index >= ((alluid - 1) // 2) + 1:
            x = 900
            y = 541 + (index - (((alluid - 1) // 2) + 1)) * 110
        else:
            x = 0
            y = 541 + index * 110

        img.paste(bar, (x, y), bar)

    for index, u in enumerate(uid):
        TASK.append(sg(img, index, u, len(uid)))
    await asyncio.gather(*TASK)

    avg_p = all_p / len(uid)
    for i in DIFF_MAP:
        if avg_p >= i:
            title_num = DIFF_MAP[i]
            break
    else:
        title_num = 11

    title = Image.open(TEXT_PATH / f"title{title_num}.png")
    img.paste(
        title,
        (25 + 450 if len(uid) >= 18 else 25, -31),
        title,
    )

    bar5 = Image.open(TEXT_PATH / "bar5.png")
    img.paste(
        bar5,
        (25 + 450 if len(uid) >= 18 else 25, 443),
        bar5,
    )

    footer = get_footer()
    img.paste(
        footer,
        (25 + 450 if len(uid) >= 18 else 25, img.size[1] - 55),
        footer,
    )

    res = await convert_img(img)

    # AI 注入：提取自选股行情文本数据
    _ai_return_my_stock(uid, all_p, stock_details)

    return res


def _ai_return_my_stock(uid_list, all_p, stock_details=None):
    """从自选股数据中提取文本信息，通过 ai_return 返回给 AI 分析"""
    try:
        avg_p = all_p / len(uid_list) if uid_list else 0
        result = f"【我的自选】共 {len(uid_list)} 只，平均涨跌幅: {avg_p:+.2f}%\n"

        if stock_details:
            # 按涨跌幅排序
            sorted_details = sorted(
                stock_details,
                key=lambda x: x.get("change", 0) if isinstance(x.get("change", 0), (int, float)) else 0,
                reverse=True,
            )
            result += "\n个股行情:\n"
            for s in sorted_details:
                change = s.get("change", 0)
                sign = "+" if isinstance(change, (int, float)) and change >= 0 else ""
                amount = s.get("amount", "N/A")
                if isinstance(amount, (int, float)):
                    from ..utils.utils import number_to_chinese

                    amount = number_to_chinese(amount)
                result += (
                    f"  {s['name']}({s['code']}): "
                    f"最新价 {s['price']}  {sign}{change}%  "
                    f"换手率 {s['turnover']}%  成交额 {amount}\n"
                )
        else:
            result += f"自选代码: {', '.join(uid_list[:10])}"

        ai_return(result)
    except Exception as e:
        logger.warning(f"[SayuStock] ai_return 自选股数据提取失败: {e}")
