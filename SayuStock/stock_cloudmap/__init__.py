import re
import datetime

from gsuid_core.sv import SV
from gsuid_core.aps import scheduler
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from ..utils.utils import convert_list, get_vix_name
from .get_cloudmap import render_image
from ..utils.resource_path import DATA_PATH
from ..utils.database.models import SsBind

sv_stock_cloudmap = SV("大盘云图")
sv_stock_compare = SV("对比个股", priority=3)

MS_MAP = {
    "5k": "5",
    "15k": "15",
    "30k": "30",
    "60k": "60",
    "k线": "100",
    "日线": "101",
    "日k": "101",
    "周k": "102",
    "周线": "102",
    "月k": "103",
    "月线": "103",
    "季k": "104",
    "季线": "104",
    "半年k": "105",
    "半年线": "105",
    "年k": "106",
    "年线": "106",
}


# 每日零点二十删除全部缓存数据
@scheduler.scheduled_job("cron", hour=0, minute=20)
async def delete_all_data():
    logger.info("[SayuStock] 开始执行[删除全部缓存数据]")
    for i in DATA_PATH.iterdir():
        if i.is_file():
            i.unlink()

    logger.success("[SayuStock] [删除全部缓存数据] 执行完成！")


@sv_stock_cloudmap.on_command(
    ("大盘云图"),
    to_ai="""查看A股大盘行业板块涨跌分布云图

    当用户询问大盘行情、今日市场整体表现、行业板块涨跌分布、大盘热力图、
    "帮我看看大盘"、"今天市场怎么样"、"行业板块涨跌"时调用。

    Args:
        text: 可选的板块筛选条件，留空显示全部行业板块的大盘云图。
              例如 "" 或 "医药" 或 "科技"
    """,
)
async def send_cloudmap_img(bot: Bot, ev: Event):
    logger.info("开始执行[大盘云图]")
    im = await render_image("大盘云图", ev.text.strip())
    await bot.send(im)


@sv_stock_cloudmap.on_command(
    ("板块云图", "行业云图", "行业板块"),
    to_ai="""查看行业板块涨跌分布云图

    当用户询问某个行业板块行情、行业板块云图、"帮我看看半导体板块"、
    "新能源板块怎么样"、"行业板块涨跌"时调用。

    Args:
        text: 行业板块名称，例如 "半导体"、"新能源"、"医药"、"白酒"
    """,
)
async def send_typemap_img(bot: Bot, ev: Event):
    logger.info("开始执行[板块云图]")
    im = await render_image("行业云图", ev.text.strip())
    await bot.send(im)


@sv_stock_cloudmap.on_command(
    ("概念云图", "概念板块云图", "概念板块"),
    to_ai="""查看概念板块涨跌分布云图

    当用户询问某个概念板块行情、概念板块云图、"帮我看看人工智能概念"、
    "华为欧拉概念怎么样"、"概念板块涨跌"时调用。
    注意：不指定概念名称时会提示需要后跟概念类型。

    Args:
        text: 概念板块名称，例如 "华为欧拉"、"人工智能"、"机器人"、"芯片"
    """,
)
async def send_gn_img(bot: Bot, ev: Event):
    logger.info("开始执行[概念云图]")
    im = await render_image("概念云图", ev.text.strip())
    await bot.send(im)


@sv_stock_cloudmap.on_fullmatch(
    ("我的个股"),
    to_ai="""查看自选股当日分时行情图

    当用户询问"我的股票"、"自选股今天怎么样"、"帮我看看我的持仓走势"、
    "我的个股表现"时调用。自动读取当前用户的自选股列表，最多显示5只。
    无需参数，留空即可。

    Args:
        text: 无需参数，留空即可
    """,
)
async def send_my_stock_img(bot: Bot, ev: Event):
    logger.info("开始执行[我的个股数据]")
    user_id = ev.at if ev.at else ev.user_id
    uid = await SsBind.get_uid_list_by_game(user_id, ev.bot_id)

    if not uid:
        return await bot.send("您还未添加自选呢~或者后跟具体股票代码, 例如：\n 个股 中证白酒 中证2000")

    uid = convert_list(uid)
    if len(uid) > 5:
        uid = uid[:5]
    txt = " ".join(uid)

    im = await render_image(
        txt,
        "single-stock",
    )
    await bot.send(im)


@sv_stock_cloudmap.on_command(
    ("个股"),
    to_ai='''查询股票/ETF的K线图或分时图

    当用户询问某只股票/ETF今天走势、分时图、日K、周K、月K时调用。
    例如"帮我看看证券ETF"、"贵州茅台日K"、"白酒ETF周K"。
    支持同时查询多只股票的分时图。

    Args:
        text: 查询内容，格式为 "[周期前缀] 股票名称或代码"
              - 无前缀：默认显示分时图，例如 "证券ETF"
              - "日k": 日K线，例如 "日k 证券ETF"
              - "周k": 周K线，例如 "周k 白酒ETF"
              - "月k"/"季k"/"年k": 对应周期K线
              - 多个标的以空格分隔，例如 "证券ETF 白酒ETF"
              - VIX指数：例如 "300vix"（仅支持分时，不支持K线）
    ''',
)
async def send_stock_img(bot: Bot, ev: Event):
    logger.info("开始执行[个股数据]")
    content = ev.text.strip().lower()
    if not content:
        return await bot.send("请后跟股票代码使用, 例如：个股 证券ETF")

    for g in MS_MAP:
        if content.startswith(g):
            content = content.replace(g, "")
            kline_code = MS_MAP[g]
            vix_name = get_vix_name(content)
            if vix_name:
                return await bot.send("[VIX] 仅支持使用 个股 300vix 方式调用, 暂时无法查看日K等数据")
            im = await render_image(
                content,
                f"single-stock-kline-{kline_code}",
            )
            break
    else:
        im = await render_image(
            content.replace("分时", "").strip(),
            "single-stock",
        )
    await bot.send(im)


@sv_stock_compare.on_command(
    ("对比个股", "个股对比"),
    block=True,
    to_ai="""对比多只股票/ETF的涨跌幅走势

    当用户想要对比几只股票走势、比较不同ETF表现、"帮我对比白酒和医药"、
    "证券ETF和沪深300谁涨得多"时调用。不指定标的则对比用户自选列表。

    Args:
        text: 查询内容，格式为 "[时间范围] 股票名称或代码1 股票名称或代码2 ..."
              - 时间范围可选："年初至今"、"最近一年"、"最近一月"
              - 也可指定具体日期如 "2024.01.01~2024.12.31"
              - 无时间范围默认显示当日分时对比
              - 多个标的以空格分隔，例如 "白酒ETF 医药ETF 证券ETF"
              - 不指定标的则对比用户自选列表
    """,
)
async def send_compare_img(bot: Bot, ev: Event):
    logger.info("开始执行[对比个股]")
    txt = ev.text.strip().replace("个股", "").replace("，", ",").replace(",", " ").replace("  ", " ").strip()

    if "最近一年" in txt or "近一年" in txt or "过去一年" in txt:
        txt = txt.replace("最近一年", "").replace("近一年", "").replace("过去一年", "").strip()
        start_time = datetime.datetime.now() - datetime.timedelta(days=365)
        end_time = datetime.datetime.now()
    elif "最近一月" in txt or "近一月" in txt or "过去一月" in txt:
        txt = txt.replace("最近一月", "").replace("近一月", "").replace("过去一月", "").strip()
        start_time = datetime.datetime.now() - datetime.timedelta(days=30)
        end_time = datetime.datetime.now()
    elif "年初至今" in txt or "今年以来" in txt or "今年" in txt:
        txt = txt.replace("年初至今", "").replace("今年以来", "").replace("今年", "").strip()
        start_time = datetime.datetime(datetime.datetime.now().year, 1, 1)
        end_time = datetime.datetime.now()
    else:
        p = r"(\d{4}[./]\d{1,2}[./]\d{1,2})(?:[~-](\d{4}[./]\d{1,2}[./]\d{1,2}))?"  # noqa: E501
        match = re.search(p, txt)
        start_time = end_time = None

        if match:
            try:
                start_str, end_str = match.groups()
                # 转换为datetime对象
                start_time = datetime.datetime.strptime(re.sub(r"[./]", "-", start_str), "%Y-%m-%d")
                end_time = (
                    datetime.datetime.strptime(re.sub(r"[./]", "-", end_str), "%Y-%m-%d")
                    if end_str
                    else datetime.datetime.now()
                )
                # 移除原始文本中的日期部分
                txt = re.sub(p, "", txt).strip()
            except ValueError:
                await bot.send("日期格式错误，请使用正确的日期格式如 2024.12.05 或 2024/12/5")
                return

    if not txt.strip():
        user_id = ev.at if ev.at else ev.user_id
        uid = await SsBind.get_uid_list_by_game(user_id, ev.bot_id)

        if not uid:
            return await bot.send("您还未添加自选呢~或者后跟具体股票代码, 例如：\n 个股对比 年初至今 中证白酒 中证2000")

        uid = convert_list(uid)
        if len(uid) > 12:
            uid = uid[:12]
            await bot.send("你添加的股票代码过多, 暂时只会对比前12支股票噢~请稍等结果...")
        txt = " ".join(uid)

    logger.debug(f"[SayuStock] [对比个股] 生成的文本: {txt}")
    im = await render_image(
        txt,
        "compare-stock",
        start_time,
        end_time,
    )
    await bot.send(im)
