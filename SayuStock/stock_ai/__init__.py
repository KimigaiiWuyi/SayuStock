from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .draw_ai_map import draw_ai_kline_with_forecast

sv_stock_kronos = SV("模型预测")


@sv_stock_kronos.on_prefix(
    ("模型预测", "ai预测", "AI预测", "趋势预测"),
    to_ai="""使用Kronos AI模型预测股票未来价格走势

    当用户询问某只股票未来走势、AI预测、趋势预测、"帮我预测一下茅台走势"、
    "证券ETF未来会涨吗"、"用AI分析一下这只股票"时调用。
    预测过程约需3分钟，会生成包含回测和预测的K线图。

    Args:
        text: 股票代码或名称，例如 "600000"、"贵州茅台"、"证券ETF"
    """,
)
async def send_stock_kronos(bot: Bot, ev: Event):
    logger.info("[SayuStock] 开始执行[模型预测]")
    im = await draw_ai_kline_with_forecast(ev.text.strip(), bot)
    await bot.send(im, at_sender=True)
