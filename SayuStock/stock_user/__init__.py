from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.logger import logger

from ..utils.request import get_code_id
from ..utils.database.models import SsBind

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


@sv_user_info.on_command(('添加自选'), block=True)
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
        code_id = await get_code_id(_u)
        if not code_id:
            return await bot.send(f'❎[SayuStock] 股票[{_u}]不存在!')
        add_dict[f'{code_id[1]}({code_id[0]})'] = code_id[0]

    send_m = '\n'.join(add_dict.keys())
    resp = await bot.receive_resp(
        f'是否确认将下列股票添加自选?\n{send_m}\n请输入是或否。',
    )
    if resp is not None:
        if resp.text == '否':
            return await bot.send('已取消!')
        else:
            for _u in add_dict:
                await SsBind.insert_uid(
                    qid,
                    ev.bot_id,
                    add_dict[_u],
                    ev.group_id,
                    is_digit=False,
                )

    return await bot.send(
        '✅[SayuStock] 添加自选成功!\n可发送[我的自选]查看或发送[删除自选]清除！'
    )


@sv_user_info.on_command(('删除自选'), block=True)
async def delete_uid(bot: Bot, ev: Event):
    qid = ev.user_id
    uid = ev.text.strip()
    logger.info(f'[SayuStock] 开始执行自选解绑, qid={qid}, uid={uid}')

    if not uid:
        return await bot.send(HINT2)

    u = uid.split(' ')
    add_dict = {}
    for _u in u:
        code_id = await get_code_id(_u)
        if not code_id:
            return await bot.send(f'❎[SayuStock] 股票[{_u}]不存在!')
        add_dict[f'{code_id[1]}({code_id[0]})'] = code_id[0]

    _d = '\n'.join(add_dict.keys())
    resp = await bot.receive_resp(
        f"是否确认将下列股票删除自选?\n{_d}\n请输入是或否。"
    )
    if resp is not None:
        if resp.text == '否':
            return await bot.send('已取消!')
        else:
            for _u in add_dict:
                await SsBind.delete_uid(qid, ev.bot_id, add_dict[_u])

    await bot.send(
        '✅[SayuStock] 删除自选成功!\n可发送[我的自选]查看或发送[添加自选]清除！'
    )
