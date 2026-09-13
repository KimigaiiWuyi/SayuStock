from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsDivider,
    GsIntConfig,
    GsStrConfig,
    GsBoolConfig,
    GsListStrConfig,
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
    "macro_event_heartbeat": GsBoolConfig(
        "宏观事件定时复核",
        "每天 08:40 / 12:40 / 20:40 让 AI 联网复核宏观重大事件表（关税 / 地缘 / 央行 / 油价），"
        "供模拟盘与持仓分析读取。关闭后仍可手动发「宏观事件刷新」。",
        True,
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
    "holdings_analysis_unlimited_users": GsListStrConfig(
        "持仓分析免限额用户",
        "这些 user_id 不受「每日 1 次」限制；网页控制台改完立即生效，无需重启",
        [],
        options=[],
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
    "kronos_divider": GsDivider(
        "AI模型预测",
        "Kronos AI预测的运行配置；网页控制台修改后立即生效，无需重启",
        "AI模型预测（Kronos）",
    ),
    "kronos_device": GsStrConfig(
        "AI预测运行设备",
        "cpu=用CPU预测（默认，无需显卡）；cuda:0/cuda:1=用第1/2块NVIDIA显卡预测。"
        "⚠️ 选择GPU前请先确认服务器已安装CUDA版PyTorch、显卡驱动正常且显存充足，"
        "否则预测会自动回退到CPU并在日志中告警",
        "cpu",
        options=["cpu", "cuda:0", "cuda:1"],
    ),
    "kronos_tokenizer": GsStrConfig(
        "AI预测Tokenizer",
        "Kronos分词器。Kronos-Tokenizer-base=默认，512上下文，官方搭配small/base模型；"
        "Kronos-Tokenizer-2k=2048上下文，官方搭配Kronos-mini。"
        "⚠️ 切换前请先确认与所选模型匹配，并确认服务器配置足以流畅运行",
        "NeoQuasar/Kronos-Tokenizer-base",
        options=["NeoQuasar/Kronos-Tokenizer-base", "NeoQuasar/Kronos-Tokenizer-2k"],
    ),
    "kronos_model": GsStrConfig(
        "AI预测模型",
        "Kronos-mini=默认，4.1M参数，CPU即可流畅运行；"
        "Kronos-small=24.7M参数，建议GPU；Kronos-base=102.3M参数，需GPU且显存充足。"
        "⚠️ 选择高配置模型前，请先确认服务器具有能流畅运行该模型的配置（内存/显存），"
        "否则预测会非常慢，甚至因资源不足失败",
        "NeoQuasar/Kronos-mini",
        options=["NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-small", "NeoQuasar/Kronos-base"],
    ),
}
