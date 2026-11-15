# 七、配置 / 数据库 / 缓存 / 资源路径

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · **上一章**：[六](./06-papertrade.md) · **下一章**：[八、测试与质量门](./08-testing-and-quality.md)

## 7.1 资源路径

```python
# utils/resource_path.py
MAIN_PATH = get_res_path() / "SayuStock"
CONFIG_PATH = MAIN_PATH / "config.json"
DATA_PATH = MAIN_PATH / "data"   # 自动 mkdir
```

`get_res_path()` 来自 GsCore `data_store`，通常在部署数据目录下，**不要**写死盘符。

## 7.2 `STOCK_CONFIG`

```python
# stock_config/stock_config.py
STOCK_CONFIG = StringConfig("SayuStock", CONFIG_PATH, CONFIG_DEFAULT)
```

默认项见 `config_default.py`：

> 改这里的默认值只影响**新装 / 新增键**：Core 加载配置时对已存在的键**保留用户 `data`**、
> 只刷新 title/desc/options（`utils/plugins_config/gs_config.py` 的 `reconcile_config`，
> 注释原文「同类型: 刷新代码侧元数据, 保留用户 data」）。所以改默认值不需要迁移逻辑，
> 但也别指望老实例自动跟上。

| 键 | 含义 | 默认 |
|----|------|------|
| ~~`papertrade_multi_group`~~ | **已废弃**（多盘制后无意义），运行时不再读 | False |
| ~~`papertrade_broadcast_group`~~ | **已废弃**，只在 v2 迁移时读一次转成 `SayuPaperBroadcastTarget` | `""` |
| `mapcloud_viewport` | 云图 CSS 画布宽度 | 2500 |
| `mapcloud_scale` | 云图放大倍数 | 2 |
| `mapcloud_refresh_minutes` | 图/数据缓存 TTL | 3 |
| `stock_cache_retention_days` | 每日清理保留天数 | 7 |
| `eastmoney_cookie` | 东财 Cookie | 内置字符串 |
| `market_api_chain` | 全局行情源链（`GsListStrConfig` 字符串列表，列表顺序即优先级）；**默认值已预填推荐链** `["东方财富","腾讯财经","新浪财经"]`（清空 = 内置链 东财→腾讯→新浪→同花顺）。**链外源自动排链尾兜底**（不是禁用），因此任何配置下 14 接口都有源可用；失败/不支持自动顺延；网页控制台改完热生效 | `["东方财富", "腾讯财经", "新浪财经"]` |
| `market_api_chain_quote` / `_kline` / `_board` / `_market` / `_exclusive` | 五域源链覆盖（盘口/分时、K线、板块/排行/菜单、大盘统计/资金、东财独占）；**默认值同样预填推荐链**（①②＝东财→腾讯→新浪；③④＝东财→新浪；⑤＝东财），清空 = 跟随全局链；`_IFACE_GROUPS` 把 14 接口映射到域，`resolve` 不配。每键 `options` 只列**真正实现了该组接口的源**（④⑤ 组的依据见覆盖面矩阵 §13.9），某组只有一个可选值 = 该组是它独占的 | ①②＝`["东方财富", "腾讯财经", "新浪财经"]`；③④＝`["东方财富", "新浪财经"]`；⑤＝`["东方财富"]` |
| `ths_api_key` | 同花顺金融数据 API（扶摇 fuyao.aicubes.cn）的 X-api-key | 内置公共 Key |
| `holdings_analysis_unlimited_users` | 持仓分析免每日限额的 `user_id` 列表；网页控制台改完热生效 | `[]` |
| `news_source_order` | 财经快讯源顺序。失败或空列表才试下一个；没写进列表的已知源排在后面兜底。可选 `eastmoney` / `wallstreetcn` / `sina` / `jin10` | `["eastmoney", "wallstreetcn", "sina", "jin10"]` |
| `news_push_hourly_groups` | 财经快讯「小时汇总」群列表（类别2，每小时整点合并推送） | `[]` |
| `news_push_trading_session_groups` | 财经快讯「交易时段汇总」群列表（类别3，每日 08/12/16/22 点合并推送：隔夜/午间/收盘/晚间） | `[]` |
| `news_push_daily_groups` | 财经快讯「每日汇总」群列表（类别4，每日 08:00 合并推送） | `[]` |

三个推送分级列表由 `stock_config.py` 的 `SayuStockConfig`（`StringConfig` 子类）覆写 `set_config`：
保存任一列表后检查群号跨列表重叠并 `logger.warning` 告警（重叠群按 小时 > 交易时段 > 每日 优先遮蔽，
重叠集合不变不重复告警；模块导入时兜底检查一次，覆盖手改 config.json 的场景）。

读取：

```python
minutes = int(STOCK_CONFIG.get_config("mapcloud_refresh_minutes").data)
```

新增配置：在 `CONFIG_DEFAULT` 加 `Gs*Config`，消费侧「每次用时读」即可热更新（与 GsCore 插件配置习惯一致）。

请求鉴权仅开放 `eastmoney_cookie` / `eastmoney_user_agent`、
`xueqiu_cookie` / `xueqiu_user_agent` 与原有 `ths_api_key`，每次请求读取，保存后生效。
东财 Cookie 留空不发送；雪球手动 Cookie 优先，留空自动获取；UA 留空使用内置 Desktop UA。
公有渠道直接使用 `utils/constant.py` 的 Desktop / Mobile UA 常量，不生成 WebUI 配置项。
Referer、Accept、语言由各接口保留；东财 `sec-ch-ua` 根据 UA 的 Chrome 主版本自动生成。
金十客户端号与版本、天天基金 deviceid 留在各自请求模块内部，不作为用户配置。

## 7.3 数据库

### 自选 `SsBind`

- 继承 `Bind`，`table=True`  
- WebConsole：`SsPushAdmin`  
- 列表用 `_` 拼接；`convert_list` 处理无 `.` 的拼接段  

### 模拟盘表

见 [§06](./06-papertrade.md)；全部 SQLModel，Admin 一并注册。

### 约定

- 不写死 `__tablename__`（与 Core 习惯一致，除非表已存在特殊名）  
- 异步方法用 Core 的 session 装饰器约定  
- Schema 变更需考虑已有用户库升级  

## 7.4 文件缓存

### 行情 / 图缓存

- 路径：`DATA_PATH` 下 JSON/PNG/HTML  
- 行情键生成：`utils/stock/utils.get_file`、`async_file_cache` 装饰器
- 图片：`utils/stock/render_cache.py` 使用独立版本命名空间、同键串行和原子写入；不覆盖行情 JSON
- TTL：`mapcloud_refresh_minutes` 比对 mtime  
- 行业云图成分代码：`industry-members_{BKxxxx}_*.json`，TTL **30 天**（`INDUSTRY_MEMBER_CACHE_MINUTES`）  
- 清理：每日 00:20 删超过 `stock_cache_retention_days` 的文件  

### 装饰器注意

`@async_file_cache` 序列化结果多为 JSON 友好结构。  
**不要**把 frozen dataclass 直接丢进需要 JSON dump 的缓存（除非装饰器已支持）；  
领域模型取数缓存在 adapter/requester 层更合适。

### Kronos

预测 PNG 缓存 150 分钟，键区分 tokenizer、模型和设备；AI 文字在图片缓存判断前发送。
同键请求串行，模型推理沿用原耗时契约；绘图交给 Core 公开接口，不新增超时预算。

## 7.5 常量 `utils/constant.py`

- `ErroText`：用户可见错误短句字典（`notData` / `notStock` / `notOpen`…）  
- `market_dict` / `bk_dict`：市场/板块别名 → 东财 fs  
- `VIX_LIST`：VIX 别名映射  
- 改错误文案时保持键稳定，避免调用方 KeyError  

## 7.6 证券主数据

- `utils/chinese_stocks.json`：A 股代码 → 名称 / 申万一/二/三级
- 生成脚本：`SayuStock/utils/update_stocks.py`（东财 `clist` 沪深京A + `sidemenu` flag 分级成分）
- 刷新：在 **Core 仓库根**用 Core 解释器跑（插件自己的 `.venv` 没有 fastapi）::

  `uv run python gsuid_core/plugins/SayuStock/SayuStock/utils/update_stocks.py --diff`  
- `get_code_id`（`stock/request_utils.py`）：名称/代码 → secid  

解析失败时 Port 返回 `not_found` / 业务 `ErroText["notStock"]`。

## 7.7 依赖安装

Python 依赖以 `pyproject.toml` 的 `[project].dependencies` 为准，由 Core 自动安装依赖功能管理。
图片复用 Core Python 环境里的 `pytakumi`，不另装绘图运行时。
Playwright 只用于雪球自动 Cookie，使用该功能时才需要安装浏览器。
依赖检查和回退见 [rendering.md](../../../../docs/rendering.md)。
