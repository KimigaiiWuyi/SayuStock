from gsuid_core.subscribe import gs_subscribe
from gsuid_core.status.plugin_status import register_status

from ..utils.image import get_ICON
from ..utils.database.models import SsBind
from ..stock_news.__init__ import TASK_NAME


async def get_subscribe_num():
    datas = await gs_subscribe.get_subscribe(TASK_NAME)
    return len(datas) if datas else 0


async def get_add_num():
    datas = await SsBind.get_all_data()
    return len(datas) if datas else 0


register_status(
    get_ICON(),
    'SayuStock',
    {
        '启用订阅': get_subscribe_num,
        '自选账户': get_add_num,
    },
)
