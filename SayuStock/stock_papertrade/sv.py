"""所有 SV 实例集中处（命令注册统一切入口）。

设计：
- ``sv_papertrade`` (pm=3, area="GROUP"): 业务命令——master / 群主 / 管理员 可用。
- ``sv_papertrade_admin`` (pm=0, area="GROUP"): master-only 工具——仅 master 可触发。

pm=0 + area="GROUP" 在 ``gsuid_core/handler.py`` 框架层做双重门槛：
- ``user_pm > sv.pm`` 拒 → 仅 master 可触发（pm=0 SV 等价 master-only）。
- ``event.user_type != "group"`` 拒 → 私聊天然屏蔽。

新增 SV 时只在这里加实例 + 在 ``commands.py`` / ``admin.py`` 里装饰
``@sv_xxx.on_fullmatch(...)`` / ``@sv_xxx.on_prefix(...)``，
``__init__.py`` 仅做 re-export + 子模块 import 触发 decorator。
"""

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false

from gsuid_core.sv import SV

sv_papertrade: SV = SV("AI模拟盘", pm=3, area="GROUP")

sv_papertrade_admin: SV = SV("AI模拟盘·管理", pm=0, area="GROUP")
