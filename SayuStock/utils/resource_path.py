from gsuid_core.data_store import get_res_path

MAIN_PATH = get_res_path() / "SayuStock"

# 配置文件
CONFIG_PATH = MAIN_PATH / "config.json"

DATA_PATH = MAIN_PATH / "data"

for i in [DATA_PATH]:
    if not i.exists():
        i.mkdir(parents=True)


def _register_backup_hints() -> None:
    # 宿主 Core 无该模块（旧版本）时跳过，不报错
    try:
        from gsuid_core.utils.backup.hints import register_backup_path
    except ImportError:
        return
    register_backup_path(
        "SayuStock",
        CONFIG_PATH,
        "config",
        "插件配置（含数据源、模拟盘与雪球配置）",
        default_selected=True,
    )
    register_backup_path(
        "SayuStock",
        DATA_PATH,
        "regenerable",
        "行情渲染缓存与临时归档，可重新生成",
    )


_register_backup_hints()
