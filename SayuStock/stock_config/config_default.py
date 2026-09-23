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
    "market_api_divider": GsDivider(
        "行情API",
        "行情数据源优先级：列表顺序即优先级，前面的源失败或缺少该接口时自动顺延到下一个源；"
        "列表外的源自动排到链尾兜底，因此任何配置下每个行情接口都保有可用数据源。"
        "各分组只列出**真正提供该组接口**的源——某组只有一个可选值，就说明那组接口是它独占的，"
        "换源不会生效。网页控制台修改后立即生效，无需重启",
        "行情API（数据源优先级）",
    ),
    "market_api_chain": GsListStrConfig(
        "全局行情源链（默认）",
        "所有行情接口的默认优先级，未单独配置的分组都跟随它。"
        "默认已按推荐填好：东方财富 → 腾讯财经 → 新浪财经（同花顺由链尾兜底自动补入，无需手填；"
        "清空则回落同一条内置默认链）。"
        "每项填一个数据源：东方财富 / 腾讯财经 / 新浪财经 / 同花顺",
        ["东方财富", "腾讯财经", "新浪财经"],
        options=["东方财富", "腾讯财经", "新浪财经", "同花顺"],
    ),
    "market_api_chain_quote": GsListStrConfig(
        "① 盘口 / 分时源链",
        "作用于实时盘口与分时。四个源都能供盘口，"
        "分时只有 东方财富 / 腾讯财经 / 新浪财经（同花顺无分时），五日分时仅 东方财富。"
        "同花顺快照不返回证券名称，名称用随仓库分发的本地股票表补。"
        "默认已按推荐填好：东方财富 → 腾讯财经 → 新浪财经（清空则跟随全局链）",
        ["东方财富", "腾讯财经", "新浪财经"],
        options=["东方财富", "腾讯财经", "新浪财经", "同花顺"],
    ),
    "market_api_chain_kline": GsListStrConfig(
        "② K线源链",
        "作用于日/周/月/季/半年/年 K 线。四个源都支持，"
        "但新浪是不复权口径、腾讯与同花顺只有日级，复权敏感场景建议以 东方财富 / 腾讯财经 开头。"
        "默认已按推荐填好：东方财富 → 腾讯财经 → 新浪财经（清空则跟随全局链）",
        ["东方财富", "腾讯财经", "新浪财经"],
        options=["东方财富", "腾讯财经", "新浪财经", "同花顺"],
    ),
    "market_api_chain_board": GsListStrConfig(
        "③ 板块 / 排行 / 菜单源链",
        "作用于板块快照与成分股、资金流排行榜、行业与概念板块菜单。"
        "只有 东方财富 与 新浪财经 有这些能力，腾讯财经与同花顺没有。"
        "默认已按推荐填好：东方财富 → 新浪财经（清空则跟随全局链）",
        ["东方财富", "新浪财经"],
        options=["东方财富", "新浪财经"],
    ),
    "market_api_chain_market": GsListStrConfig(
        "④ 大盘统计 / 资金源链",
        "作用于涨跌家数分布与两市成交额。"
        "只有 东方财富 与 新浪财经 有这些能力（新浪为自算/指数盘口口径）。"
        "默认已按推荐填好：东方财富 → 新浪财经（清空则跟随全局链）",
        ["东方财富", "新浪财经"],
        options=["东方财富", "新浪财经"],
    ),
    "market_api_chain_exclusive": GsListStrConfig(
        "⑤ 东财独占：云图 / 北向 / 估值 / 财报",
        "大盘云图、北向资金、估值序列、财报快照这四类**只有东方财富提供**："
        "其他三个源会返回「不支持」并自动兜底到东方财富，所以这一组填别的也不会生效。"
        "单独列出来是为了让你一眼看出「其他家没有」，不必在这组上试错。",
        ["东方财富"],
        options=["东方财富"],
    ),
    "market_api_chain_ipo": GsListStrConfig(
        "⑥ IPO 日历源链",
        "作用于新股 IPO 日历（A股/港股/美股，T-2至T+7）。"
        "东方财富覆盖三个市场（A股含申购/中签/缴款/上市全流程，港股另有 AAStocks 增强与兜底）；"
        "纳斯达克只有美股官方日历（多出已申报/预期定价阶段、发行价与募资额），A股/港股会自动跳过，"
        "东财挂掉时可作美股备源——需要时把它加进链里即可。"
        "默认已按推荐填好：东方财富（清空则跟随全局链）",
        ["东方财富"],
        options=["东方财富", "纳斯达克"],
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
    "ths_api_key": GsStrConfig(
        "同花顺API密钥",
        "同花顺金融数据API（扶摇 fuyao.aicubes.cn）的 API Key，请求头 X-api-key 携带；"
        "默认内置公共 Key，失效可到该站「API Key 管理」页（/admin）用同花顺账号签发自己的",
        "sk-fuyao-ORe_1l_p-CogNpfJpfU90yzO7LjLkMOE",
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
        "⚠️ 切换高配置模型前，请先确认服务器具有能流畅运行该模型的配置（内存/显存），"
        "否则预测会非常慢，甚至因资源不足失败",
        "NeoQuasar/Kronos-mini",
        options=["NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-small", "NeoQuasar/Kronos-base"],
    ),
    "news_push_divider": GsDivider(
        "雪球新闻推送分级",
        "已订阅「订阅雪球新闻」的群按下面的列表分为四类推送模式；"
        "同一群号出现在多个列表时按 小时 > 交易时段 > 每日 优先；"
        "没有出现在任何列表里的订阅群保持默认的逐条实时推送。列表改动立即生效，无需重启",
        "雪球7x24新闻推送分级 (默认立即推送)",
    ),
    "news_push_hourly_groups": GsListStrConfig(
        "小时汇总推送群",
        "这些群每小时整点收到一条合并推送，内容为上一小时内的雪球7x24新闻",
        [],
        options=[],
    ),
    "news_push_trading_session_groups": GsListStrConfig(
        "交易时段汇总推送群",
        "这些群在每天 08:00 / 12:00 / 16:00 / 22:00 各收到一条合并推送"
        "（隔夜/午间/收盘/晚间汇总），内容为自上次推送以来累积的雪球7x24新闻",
        [],
        options=[],
    ),
    "news_push_daily_groups": GsListStrConfig(
        "每日汇总推送群",
        "这些群每天 08:00 收到一条合并推送，内容为自昨天以来累积的雪球7x24新闻",
        [],
        options=[],
    ),
}
