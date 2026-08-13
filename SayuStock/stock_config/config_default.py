from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsIntConfig,
    GsStrConfig,
    GsBoolConfig,
)

CONFIG_DEFAULT: Dict[str, GSC] = {
    # 保留键：迁移读旧播报群；运行时不再引用。
    "papertrade_multi_group": GsBoolConfig(
        "多群模拟盘（已废弃）",
        "已失效：模拟盘改为命名账户，同一个群可开多个盘，任意群都能查任意盘。请用「模拟盘创建 <盘名>」",
        False,
    ),
    "papertrade_broadcast_group": GsStrConfig(
        "模拟盘播报群号（已废弃）",
        "已失效：升级时会自动转成一条播报订阅。之后请用「模拟盘推送添加 <盘名>」/「模拟盘推送删除 <盘名>」维护",
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
