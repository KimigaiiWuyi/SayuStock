from gsuid_core.data_store import get_res_path

MAIN_PATH = get_res_path() / "SayuStock"

# 配置文件
CONFIG_PATH = MAIN_PATH / "config.json"
GN_BK_PATH = MAIN_PATH / "概念板块.json"
HISTORY_PATH = MAIN_PATH / "history.json"

DATA_PATH = MAIN_PATH / "data"

for i in [DATA_PATH]:
    if not i.exists():
        i.mkdir(parents=True)
