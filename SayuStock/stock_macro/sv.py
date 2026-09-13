from gsuid_core.sv import SV

# 只读查看任何人可用；「刷新」在 commands 里再做管理员校验
sv_macro: SV = SV("宏观事件", pm=3)
