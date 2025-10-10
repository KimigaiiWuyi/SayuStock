from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.logger import logger

from ..utils.utils import get_vix_name
from ..utils.database.models import SsBind
from ..utils.stock.request_utils import get_code_id

sv_user_info = SV('股票用户信息', priority=1)

HINT1 = '''[SayuStock]
你需要在命令后面加入你自选的股票！
例如: 添加自选 600000
你可以在命令后面加入多个股票，用空格隔开
例如: 添加自选 600000 光纤传媒
'''

HINT2 = '''[SayuStock]
你需要在命令后面加入你要删除的股票！
例如: 删除自选 600000
你可以在命令后面加入多个股票，用空格隔开
例如: 删除自选 600000 光纤传媒
'''


@sv_user_info.on_command(
    ('添加自选', '添加个股', '添加股票', '添加持仓', '加入自选'), block=True
)
async def bind_uid(bot: Bot, ev: Event):
    qid = ev.user_id
    uid = ev.text.strip()
    logger.info(f'[SayuStock] 开始执行自选绑定, qid={qid}, uid={uid}')

    if not uid:
        return await bot.send(HINT1)

    u = uid.split(' ')
    add_dict = {}
    if not u:
        return await bot.send(HINT1)

    for _u in u:
        _u = _u.strip()
        if not _u:
            continue

        vix_name = get_vix_name(_u)
        if vix_name is None:
            code_id = await get_code_id(_u)
        else:
            code_id = f'VIX.{vix_name}', vix_name

        if not code_id:
            return await bot.send(f'❎[SayuStock] 股票[{_u}]不存在!')
        add_dict[f'{code_id[1]}({code_id[0]})'] = code_id[0]

    send_m = '\n'.join(add_dict.keys())
    resp = await bot.receive_resp(
        f'是否确认将下列股票添加自选?\n{send_m}\n请输入是或否。',
    )
    if resp is not None:
        if resp.text == '是':
            for _u in add_dict:
                await SsBind.insert_uid(
                    qid,
                    ev.bot_id,
                    add_dict[_u],
                    ev.group_id,
                    is_digit=False,
                )
        else:
            return await bot.send('已取消!')

    return await bot.send(
        '✅[SayuStock] 添加自选成功!\n可发送[我的自选]查看或发送[删除自选]清除！'
    )


@sv_user_info.on_command(
    (
        '删除自选',
        '删除个股',
        '删除股票',
        '移除自选',
        '删除持仓',
    ),
    block=True,
)
async def delete_uid(bot: Bot, ev: Event):
    qid = ev.user_id
    uid = ev.text.strip()
    logger.info(f'[SayuStock] 开始执行自选解绑, qid={qid}, uid={uid}')

    if not uid:
        return await bot.send(HINT2)

    now_uid = await SsBind.get_uid_list_by_game(qid, ev.bot_id)
    if not now_uid:
        return await bot.send('您还未添加自选呢~请输入 添加自选 查看帮助!')

    u = uid.split(' ')
    add_dict = {}
    for _u in u:
        _u = _u.strip()
        if not _u:
            continue

        vix_name = get_vix_name(_u)
        if vix_name is None:
            code_id = await get_code_id(_u)
        else:
            code_id = f'VIX.{vix_name}', vix_name

        if not code_id:
            return await bot.send(f'❎[SayuStock] 股票[{_u}]不存在!')

        _name = f'{code_id[1]}({code_id[0]})'
        add_dict[_name] = code_id[0]

        if code_id[0] not in now_uid:
            return await bot.send(
                f'❎[SayuStock] 股票[{_name}]不在您的自选中!'
            )

    _d = '\n'.join(add_dict.keys())
    resp = await bot.receive_resp(
        f"是否确认将下列股票删除自选?\n{_d}\n请输入是或否。"
    )
    if resp is not None:
        if resp.text == '是':
            for _u in add_dict:
                await SsBind.delete_uid(qid, ev.bot_id, add_dict[_u])
        else:
            return await bot.send('已取消!')

    await bot.send(
        '✅[SayuStock] 删除自选成功!\n可发送[我的自选]查看或发送[添加自选]清除！'
    )
