from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsIntConfig,
    GsStrConfig,
    GsBoolConfig,
)

CONFIG_DEFAULT: Dict[str, GSC] = {
    "papertrade_multi_group": GsBoolConfig(
        "多群模拟盘",
        "开启后每个群各开各的模拟盘（旧行为）。默认关闭 = 全服共用一个盘："
        "任意群都能查询同一账户，且只有第一个开盘的群能开",
        False,
    ),
    "papertrade_broadcast_group": GsStrConfig(
        "模拟盘播报群号",
        "共用模式下生效（即多群模拟盘关闭时）。填了就把成交播报推到该群；留空则推到开盘的那个原群",
        "",
    ),
    "mapcloud_viewport": GsIntConfig(
        "大盘云图分辨率",
        "截图的大盘云图分辨率",
        2500,
        options=[1000, 1500, 2000, 2500, 3000],
    ),
    "mapcloud_scale": GsIntConfig(
        "大盘云图分辨放大倍数",
        "大盘云图分辨放大倍数",
        2,
        options=[1, 2, 3],
    ),
    "mapcloud_refresh_minutes": GsIntConfig(
        "大盘云图刷新时间(分钟)",
        "隔多久之后才会重新请求新数据",
        3,
        options=[1, 2, 3, 4, 5, 10, 30, 60],
    ),
    "stock_cache_retention_days": GsIntConfig(
        "股票缓存保留天数",
        "每日定时任务只会清理超过该天数的缓存文件，不再每天清空缓存目录",
        7,
        options=[1, 3, 7, 15, 30],
    ),
    "eastmoney_cookie": GsStrConfig(
        "东财Cookie",
        "东财Cookie",
        "qgqp_b_id=659a53f35cc91d08833fd26098e9ce34; st_nvi=DXIDHc92MckKhvIssg8zda85c;"
        " nid=0ff5d2da99cd123247ff24b723a17e3c; "
        "nid_create_time=1762029542554; gvi=VIzYcS_d6R9H3UQkE2C7078a4; gvi_create_time=1762029542554; "
        "websitepoptg_api_time=1762781584093; fullscreengg=1; fullscreengg2=1",
        options=[
            "qgqp_b_id=659a53f35cc91d08833fd26098e9ce34; st_nvi=DXIDHc92MckKhvIssg8zda85c;"
            " nid=0ff5d2da99cd123247ff24b723a17e3c; "
            "nid_create_time=1762029542554; gvi=VIzYcS_d6R9H3UQkE2C7078a4; gvi_create_time=1762029542554; "
            "websitepoptg_api_time=1762781584093; fullscreengg=1; fullscreengg2=1"
        ],
    ),
}
