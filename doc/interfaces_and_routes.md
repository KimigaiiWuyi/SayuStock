# SayuStock 全部接口和路由文档

> 本文档完整梳理了 SayuStock 项目的所有用户命令路由、内部数据接口、AI Tools 接口及定时任务。
> 项目基于 `gsuid_core` 框架开发，作为聊天机器人插件运行。

---

## 一、用户命令路由（Bot Commands）

所有命令通过 `gsuid_core.sv.SV` 注册，支持前缀匹配、完全匹配和命令匹配。

### 1. 大盘与概览模块 (`stock_info`)

| 命令 | 匹配方式 | 处理函数 | 说明 |
|------|---------|---------|------|
| `大盘概览` / `大盘概况` | `on_fullmatch` | `send_stock_info` | 生成大盘概览图片，包含主要指数、涨跌分布、领涨领跌板块等 |
| `我的自选` / `我的持仓` / `我的股票` | `on_fullmatch` | `send_my_stock` | 查询用户已添加的自选股行情 |
| `全天候` / `全天候板块` | `on_fullmatch` | `send_future_stock` | 生成全天候策略板块图 |
| `基金持仓` / `持仓分布` | `on_command` | `send_fund_info` | 查询指定基金的持仓分布，需后跟基金代码 |

### 2. 用户自选管理模块 (`stock_user`)

| 命令 | 匹配方式 | 处理函数 | 说明 |
|------|---------|---------|------|
| `添加自选` / `添加个股` / `添加股票` / `添加持仓` / `加入自选` | `on_command` | `bind_uid` | 添加股票到用户自选列表，支持多个股票用空格分隔 |
| `删除自选` / `删除个股` / `删除股票` / `移除自选` / `删除持仓` | `on_command` | `delete_uid` | 从用户自选列表删除股票 |

**交互流程：**
- 用户发送命令 + 股票代码/名称
- Bot 查询股票代码有效性
- Bot 发送确认消息，等待用户回复"是"或"否"
- 根据用户确认执行绑定/解绑操作

### 3. 云图与个股模块 (`stock_cloudmap`)

| 命令 | 匹配方式 | 处理函数 | 说明 |
|------|---------|---------|------|
| `大盘云图` | `on_command` | `send_cloudmap_img` | 生成大盘云图（Treemap），后跟市场名称如`沪深A`、`创业板`等 |
| `板块云图` / `行业云图` / `行业板块` | `on_command` | `send_typemap_img` | 生成行业板块云图 |
| `概念云图` / `概念板块云图` / `概念板块` | `on_command` | `send_gn_img` | 生成概念板块云图，需后跟概念名称 |
| `我的个股` | `on_fullmatch` | `send_my_stock_img` | 生成用户自选股的个股对比图 |
| `个股` | `on_command` | `send_stock_img` | 生成单只股票的分时图或K线图，支持K线周期后缀 |
| `对比个股` / `个股对比` | `on_command` | `send_compare_img` | 对比多只股票的走势，支持时间范围筛选 |

**个股K线周期支持：**
- 分时（默认）: `个股 600000`
- 5分钟K: `个股 5k 600000`
- 15分钟K: `个股 15k 600000`
- 30分钟K: `个股 30k 600000`
- 60分钟K: `个股 60k 600000`
- 日线: `个股 日线 600000` / `日k` / `k线`
- 周线: `个股 周线 600000` / `周k`
- 月线: `个股 月线 600000` / `月k`
- 季线: `个股 季线 600000` / `季k`
- 半年线: `个股 半年线 600000` / `半年k`
- 年线: `个股 年线 600000` / `年k`

**对比个股时间范围支持：**
- `最近一年` / `近一年` / `过去一年`
- `最近一月` / `近一月` / `过去一月`
- `年初至今` / `今年以来` / `今年`
- 自定义日期: `2024.12.05~2025.01.01` 或 `2024/12/5-2025/1/1`

### 4. 新闻订阅模块 (`stock_news`)

| 命令 | 匹配方式 | 处理函数 | 说明 |
|------|---------|---------|------|
| `订阅雪球新闻` / `订阅雪球热点` | `on_fullmatch` | `send_add_subscribe_info` | 订阅雪球7x24小时新闻推送 |
| `取消订阅雪球新闻` / `取消订阅雪球热点` | `on_fullmatch` | `send_delete_subscribe_info` | 取消新闻订阅 |

### 5. 市盈市净对比模块 (`stock_sina`)

| 命令 | 匹配方式 | 处理函数 | 说明 |
|------|---------|---------|------|
| `市盈率对比` | `on_prefix` | `send_stock_PE_info` | 对比多只股票市盈率，需后跟股票代码（空格分隔） |
| `市净率对比` | `on_prefix` | `send_stock_PB_info` | 对比多只股票市净率，需后跟股票代码（空格分隔） |

### 6. AI 模型预测模块 (`stock_ai`)

| 命令 | 匹配方式 | 处理函数 | 说明 |
|------|---------|---------|------|
| `模型预测` / `ai预测` / `AI预测` / `趋势预测` | `on_prefix` | `send_stock_kronos` | 使用 Kronos 模型对股票进行趋势预测，生成含回测与未来预测的K线图 |

**预测流程：**
- 获取股票30分钟K线数据
- 使用 Kronos 模型进行回测预测（验证模型准确性）
- 使用 Kronos 模型进行未来30个周期预测
- 生成 Plotly 图表（历史走势 + 回测区间 + 未来预测区间）
- 通过 Playwright 截图返回图片

---

## 二、AI Tools 接口 (`stock_ai_func`)

以下工具通过 `gsuid_core.ai_core.register.ai_tools` 注册，供 AI Agent 调用：

| 工具名 | 函数 | 参数 | 返回值 | 说明 |
|--------|------|------|--------|------|
| `get_stock_basic` | 获取股票基本信息 | `stock_code: str` | 股票名称、价格、涨跌、换手率文本 | 支持代码或名称查询 |
| `get_market_summary` | 获取大盘概览 | 无 | 主要指数行情列表 | 沪深主要指数实时数据 |
| `get_sector_leader` | 获取领涨板块 | `sector_type: str` | 领涨板块及领涨股文本 | 支持"行业板块"或"概念板块" |
| `get_fund_holdings` | 获取基金持仓 | `fund_code: str` | 基金前5大持仓文本 | 查询基金重仓股 |
| `get_latest_news` | 获取最新财经新闻 | `limit: int = 5` | 新闻列表文本 | 雪球7x24新闻 |
| `get_crypto_prices` | 获取加密货币价格 | 无 | 主流加密货币行情 | BTC、ETH等 |
| `get_vix_index` | 获取VIX波动率指数 | `vix_type: str = "300"` | VIX指数数据文本 | 支持300/50/1000/kcb/cyb |
| `search_stock` | 搜索股票代码 | `query: str` | 股票代码与名称 | 模糊搜索 |
| `get_my_watchlist` | 获取用户自选列表 | 无（从上下文获取用户） | 自选股票行情列表 | 需用户已绑定自选 |

---

## 三、内部数据请求接口

### 3.1 东方财富数据接口 (`utils/stock/request.py`)

| 函数名 | 说明 | 关键参数 | 缓存 |
|--------|------|---------|------|
| `get_hours_from_em()` | 获取沪深大盘成交额及与昨日对比 | 无 | 无 |
| `get_bar()` | 获取市场涨跌分布统计 | 无 | 无 |
| `get_menu(mode)` | 获取板块菜单列表 | `mode: int` (2=行业, 3=概念) | 按天缓存 |
| `get_vix(vix_name)` | 获取VIX波动率指数数据 | `vix_name: str` | 文件缓存 |
| `get_single_fig_data(secid)` | 获取个股分时走势数据 | `secid: str` | 无 |
| `get_gg(market, sector, ...)` | 个股综合数据入口 | `market`, `sector`, `start_time`, `end_time` | 根据sector自动路由 |
| `_get_gg(sec_id, sec_type)` | 获取个股实时行情+分时 | `sec_id`, `sec_type` | 文件缓存(150分钟) |
| `_get_gg_kline(...)` | 获取个股K线数据 | `sec_id`, `kline_code`, `start_time`, `end_time` | 文件缓存 |
| `get_mtdata(market, ...)` | 批量获取市场股票列表 | `market`, `is_loop`, `po`, `pz` | 文件缓存 |
| `get_hotmap()` | 获取大盘热力图数据 | 无 | 文件缓存 |
| `stock_request(...)` | 通用HTTP请求封装 | `url`, `method`, `params`, etc. | 无 |
| `get_dc_token()` | 通过Playwright获取东财Cookie | 无 | 全局变量缓存 |

### 3.2 雪球新闻接口 (`utils/request.py`)

| 函数名 | 说明 | 关键参数 |
|--------|------|---------|
| `get_token()` | 通过Playwright获取雪球Cookie | 无 |
| `clean_news()` | 清空新闻缓存 | 无 |
| `get_news_list(max_id)` | 获取单页新闻列表 | `max_id: int` |
| `get_news(max_id)` | 获取完整新闻（含缓存合并） | `max_id: int` |
| `stock_request(...)` | 通用HTTP请求封装 | `url`, `method`, `params`, etc. |

### 3.3 工具函数接口 (`utils/stock/request_utils.py`)

| 函数名 | 说明 | 关键参数 |
|--------|------|---------|
| `get_fund_pos_list(fcode)` | 获取基金持仓明细 | `fcode: str` |
| `get_code_id(code, priority)` | 股票代码查询与转换 | `code: str`, `priority: str` (h/us/a) |
| `get_image_from_em(name, size)` | 下载东财资金流向图 | `name: str`, `size: tuple` |

### 3.4 加密货币接口 (`utils/get_OKX.py`)

| 函数名 | 说明 |
|--------|------|
| `analyze_market_target(market)` | 分析市场目标类型（crypto/stock） |
| `get_all_crypto_price()` | 获取所有加密货币价格 |
| `get_crypto_trend_as_json(code)` | 获取加密货币分时数据 |
| `get_crypto_history_kline_as_json(...)` | 获取加密货币历史K线 |

---

## 四、数据库模型接口

### 4.1 数据模型 (`utils/database/models.py`)

| 模型 | 说明 | 关键字段 |
|------|------|---------|
| `SsBind` | 用户自选股票绑定表 | `uid: str` (自选股票代码), `push: str` (推送开关) |

**SsBind 方法：**
- `delete_uid(user_id, bot_id, uid)` - 删除指定自选股票
- `get_uid_list_by_game(user_id, bot_id)` - 获取用户自选列表
- `insert_uid(...)` - 插入自选（继承自基类）
- `update_data(...)` - 更新数据（继承自基类）

### 4.2 WebConsole 管理

| 管理页面 | 模型 | 说明 |
|---------|------|------|
| `SsPushAdmin` | `SsBind` | 在WebConsole中管理用户自选股票 |

---

## 五、定时任务（Scheduler Jobs）

| 任务 | 触发时间 | 执行函数 | 说明 |
|------|---------|---------|------|
| 保存大盘数据 | `cron: 每天 23:00` | `save_data_sayustock` | 保存当天大盘概览数据 |
| 检查新闻订阅 | `cron: 每5分钟` | `send_subscribe_info` | 检查并推送雪球新闻给订阅用户 |
| 清空新闻缓存 | `cron: 每天 00:00` | `clean_news_data` | 清空NEWS全局缓存 |
| 删除全部缓存 | `cron: 每天 00:20` | `delete_all_data` | 删除DATA_PATH下所有缓存文件 |

---

## 六、配置项

### 6.1 插件配置 (`stock_config/config_default.py`)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `mapcloud_viewport` | int | 2500 | 大盘云图截图分辨率 |
| `mapcloud_scale` | int | 2 | 大盘云图截图放大倍数 |
| `mapcloud_refresh_minutes` | int | 3 | 大盘云图数据刷新间隔（分钟） |
| `eastmoney_cookie` | str | ... | 东方财富Cookie，用于数据请求 |

### 6.2 全局常量 (`utils/constant.py`)

| 常量 | 说明 |
|------|------|
| `chinese_stocks` | 中国股票基础信息字典（代码->名称/行业） |
| `PREFIX_DATA` | 股票代码前缀映射（0=深A, 1=沪深A, etc.） |
| `ErroText` | 错误提示文本字典 |
| `VIX_LIST` | VIX指数别名映射 |
| `market_dict` | 市场/板块代码映射字典 |
| `bk_dict` | 板块专用字典 |
| `code_id_dict` | 常见指数代码映射 |
| `i_code` | 国际市场指数代码 |
| `commodity` | 大宗商品代码 |
| `bond` | 债券代码 |
| `whsc` | 外汇代码 |
| `trade_detail_dict` | 东财字段映射（f12=代码, f14=名称, f3=涨幅, etc.） |
| `SINGLE_STOCK_FIELDS` | 个股详情请求字段列表 |

---

## 七、文件缓存策略

项目使用自定义装饰器 `@async_file_cache` 进行文件级缓存：

| 数据类型 | 缓存时长 | 文件格式 |
|---------|---------|---------|
| 个股实时行情 | 150分钟 | JSON |
| 个股K线数据 | 150分钟 | JSON |
| 市场批量数据 | 150分钟 | JSON |
| 大盘热力图 | 150分钟 | JSON |
| VIX数据 | 150分钟 | JSON |
| 生成的HTML图表 | 3分钟（可配置） | HTML |
| 东财图片 | 3分钟（可配置） | PNG |

---

## 八、外部依赖服务

| 服务 | 用途 | 相关文件 |
|------|------|---------|
| 东方财富 (eastmoney.com) | 股票行情、K线、板块数据、资金流向 | `utils/stock/request.py`, `utils/stock/request_utils.py` |
| 雪球 (xueqiu.com) | 7x24财经新闻 | `utils/request.py` |
| OKX | 加密货币行情 | `utils/get_OKX.py` |
| HuggingFace | Kronos AI模型下载 | `stock_ai/draw_ai_map.py` |
| Playwright | 网页截图、Cookie获取 | `utils/image.py`, `utils/stock/request.py`, `utils/request.py` |

---

## 九、路由索引速查

### 按功能分类

**行情查询：**
- `大盘概览` - 市场整体情况
- `个股 <代码>` - 单股分时/K线
- `我的个股` - 自选股行情
- `对比个股 <代码1> <代码2>` - 多股对比

**板块分析：**
- `大盘云图 [市场]` - 市场全景Treemap
- `板块云图` / `行业云图` - 行业板块Treemap
- `概念云图 <概念名>` - 概念板块Treemap

**数据工具：**
- `市盈率对比 <代码>` - PE对比
- `市净率对比 <代码>` - PB对比
- `基金持仓 <基金代码>` - 基金重仓

**AI功能：**
- `模型预测 <代码>` / `AI预测 <代码>` - Kronos趋势预测

**用户管理：**
- `添加自选 <代码>` - 添加自选股
- `删除自选 <代码>` - 删除自选股
- `我的自选` - 查看自选股

**订阅服务：**
- `订阅雪球新闻` - 订阅新闻推送
- `取消订阅雪球新闻` - 取消订阅
