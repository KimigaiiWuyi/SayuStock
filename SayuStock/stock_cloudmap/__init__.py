import re
import datetime

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.aps import scheduler
from gsuid_core.logger import logger

from .utils import VIX_LIST
from ..utils.utils import convert_list
from .get_cloudmap import render_image
from ..utils.database.models import SsBind
from ..utils.resource_path import DATA_PATH, GN_BK_PATH

sv_stock_cloudmap = SV("大盘云图")
sv_stock_compare = SV("对比个股", priority=3)

MS_MAP = {
    'k线': '100',
    '日线': '101',
    '日k': '101',
    '周k': '102',
    '周线': '102',
    '月k': '103',
    '月线': '103',
    '季k': '104',
    '季线': '104',
    '半年k': '105',
    '半年线': '105',
    '年k': '106',
    '年线': '106',
}


# 每日零点二十删除全部缓存数据
@scheduler.scheduled_job('cron', hour=0, minute=20)
async def delete_all_data():
    logger.info("[SayuStock] 开始执行[删除全部缓存数据]")
    for i in DATA_PATH.iterdir():
        if i.is_file():
            i.unlink()
    if GN_BK_PATH.exists():
        GN_BK_PATH.unlink()

    logger.success("[SayuStock] [删除全部缓存数据] 执行完成！")


@sv_stock_cloudmap.on_command(("大盘云图"))
async def send_cloudmap_img(bot: Bot, ev: Event):
    logger.info("开始执行[大盘云图]")
    im = await render_image(ev.text.strip())
    await bot.send(im)


@sv_stock_cloudmap.on_command(("板块云图", "行业云图", "行业板块"))
async def send_typemap_img(bot: Bot, ev: Event):
    logger.info("开始执行[板块云图]")
    im = await render_image('沪深A', ev.text.strip())
    await bot.send(im)


@sv_stock_cloudmap.on_command(("概念云图", "概念板块云图", "概念板块"))
async def send_gn_img(bot: Bot, ev: Event):
    logger.info("开始执行[概念云图]")
    im = await render_image(ev.text.strip(), ev.text.strip())
    await bot.send(im)


@sv_stock_cloudmap.on_command(("个股"))
async def send_stock_img(bot: Bot, ev: Event):
    logger.info("开始执行[个股数据]")
    content = ev.text.strip().lower()
    for g in MS_MAP:
        if content.startswith(g):
            content = content.replace(g, '')
            kline_code = MS_MAP[g]
            for vix in VIX_LIST:
                if vix in content:
                    return await bot.send(
                        f'[VIX] 仅支持使用 个股 300vix 方式调用, 暂时无法查看日K等数据'
                    )
            im = await render_image(
                content,
                f'single-stock-kline-{kline_code}',
            )
            break
    else:
        im = await render_image(
            content.replace('分时', ''),
            'single-stock',
        )
    await bot.send(im)


@sv_stock_compare.on_command(("对比个股", "个股对比"), block=True)
async def send_compare_img(bot: Bot, ev: Event):
    logger.info("开始执行[对比个股]")
    txt = (
        ev.text.strip()
        .replace('个股', '')
        .replace('，', ',')
        .replace(',', ' ')
        .replace('  ', ' ')
        .strip()
    )

    if '最近一年' in txt or '近一年' in txt or '过去一年' in txt:
        txt = (
            txt.replace('最近一年', '')
            .replace('近一年', '')
            .replace('过去一年', '')
            .strip()
        )
        start_time = datetime.datetime.now() - datetime.timedelta(days=365)
        end_time = datetime.datetime.now()
    elif '最近一月' in txt or '近一月' in txt or '过去一月' in txt:
        txt = (
            txt.replace('最近一月', '')
            .replace('近一月', '')
            .replace('过去一月', '')
            .strip()
        )
        start_time = datetime.datetime.now() - datetime.timedelta(days=30)
        end_time = datetime.datetime.now()
    elif '年初至今' in txt or '今年以来' in txt or '今年' in txt:
        txt = (
            txt.replace('年初至今', '')
            .replace('今年以来', '')
            .replace('今年', '')
            .strip()
        )
        start_time = datetime.datetime(datetime.datetime.now().year, 1, 1)
        end_time = datetime.datetime.now()
    else:
        p = r'(\d{4}[./]\d{1,2}[./]\d{1,2})(?:[~-](\d{4}[./]\d{1,2}[./]\d{1,2}))?'  # noqa: E501
        match = re.search(p, txt)
        start_time = end_time = None

        if match:
            try:
                start_str, end_str = match.groups()
                # 转换为datetime对象
                start_time = datetime.datetime.strptime(
                    re.sub(r'[./]', '-', start_str), "%Y-%m-%d"
                )
                end_time = (
                    datetime.datetime.strptime(
                        re.sub(r'[./]', '-', end_str), "%Y-%m-%d"
                    )
                    if end_str
                    else datetime.datetime.now()
                )
                # 移除原始文本中的日期部分
                txt = re.sub(p, '', txt).strip()
            except ValueError:
                await bot.send(
                    "日期格式错误，请使用正确的日期格式如 2024.12.05 或 2024/12/5"
                )
                return

    if not txt.strip():
        user_id = ev.at if ev.at else ev.user_id
        uid = await SsBind.get_uid_list_by_game(user_id, ev.bot_id)

        if not uid:
            return await bot.send(
                '您还未添加自选呢~或者后跟具体股票代码, 例如：\n 个股对比 年初至今 中证白酒 中证2000'
            )

        uid = convert_list(uid)
        if len(uid) > 12:
            uid = uid[:12]
            await bot.send(
                '你添加的股票代码过多, 暂时只会对比前12支股票噢~请稍等结果...'
            )
        txt = ' '.join(uid)

    logger.debug(f"[SayuStock] [对比个股] 生成的文本: {txt}")
    im = await render_image(
        txt,
        'compare-stock',
        start_time,
        end_time,
    )
    await bot.send(im)
