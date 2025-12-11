from typing import Dict

from gsuid_core.utils.plugins_config.models import GSC, GsIntConfig, GsStrConfig

CONFIG_DEFAULT: Dict[str, GSC] = {
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
