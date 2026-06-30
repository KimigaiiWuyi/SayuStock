"""权限校验 helpers（纯函数，不持有 SV 引用）。

公开 API：
- ``user_pm_level(ev)``: ``ev.user_pm`` 的严格 int 化
  （private chat → 0；bool/str/None/其他 → 6；int 直返）。
- ``check_admin(ev)``: 群主 / 管理员 / master 全过（``pm <= 1``）。

历史：原本有第三个 helper ``is_master_pm0`` 在 ``send_dry_run`` 里做运行时
master 检查；该命令迁到 ``sv_papertrade_admin`` (pm=0) 之后 helper 已无用，
整段连同 ``send_dry_run`` 内的运行时检查一并废弃，依赖框架层做硬门槛。
"""

from gsuid_core.models import Event


def user_pm_level(ev: Event) -> int:
    """``ev.user_pm`` 的严格整型化；统一返回 0/1/2/.../6。

    Returns:
        0=master, 1=群主 / 管理员, >=2=普通成员。
        私聊（``ev.group_id`` 为空）按当前约定返回 0（视为最大权限）。
    """
    if not ev.group_id:
        return 0
    pm = ev.user_pm
    if isinstance(pm, bool):
        # bool 是 int 的子类，先排除避免 True / False 被当作 1 / 0
        return 6
    if isinstance(pm, int):
        return pm
    if isinstance(pm, str) and pm.isdigit():
        return int(pm)
    return 6


async def check_admin(ev: Event) -> bool:
    """群主 / 管理员 / master 全过（``pm <= 1``）。

    不使用 try/except 兜底 ``ev.user_pm`` 类型——见 user_pm_level 的 isinstance 守卫。
    """
    return user_pm_level(ev) <= 1
