# SayuStock AI 触发器改造文档

## 一、改造概述

本次改造将 SayuStock 插件的全部触发器（`@sv.on_xxx`）改造为支持 AI Tool Call 调用。改造后：

- **用户直接发命令**：行为完全不变，走原有逻辑
- **AI 调用**：AI 按照 `to_ai` docstring 构建合适的 `text` 参数，触发器在 AI 上下文中执行，`ai_return()` 收集的文本内容返回给 AI 决策

同时清理了 `stock_ai_func/ai_tools.py` 中与触发器功能重复的 AI 工具定义，避免双重注册。

---

## 二、改造涉及的文件

### 2.1 触发器层改造（添加 `to_ai` 参数）

| 文件 | 改造的触发器 | 状态 |
|------|------------|------|
| [`stock_cloudmap/__init__.py`](../SayuStock/stock_cloudmap/__init__.py) | 大盘云图、板块云图、概念云图、我的个股、个股、对比个股 | ✅ 已完成（此前已改造） |
| [`stock_info/__init__.py`](../SayuStock/stock_info/__init__.py) | 基金持仓、大盘概览、我的自选、全天候 | ✅ 本次改造 |
| [`stock_ai/__init__.py`](../SayuStock/stock_ai/__init__.py) | 模型预测 | ✅ 本次改造 |
| [`stock_news/__init__.py`](../SayuStock/stock_news/__init__.py) | 订阅雪球新闻、取消订阅雪球新闻 | ✅ 本次改造 |
| [`stock_sina/__init__.py`](../SayuStock/stock_sina/__init__.py) | 市盈率对比、市净率对比 | ✅ 本次改造 |
| [`stock_user/__init__.py`](../SayuStock/stock_user/__init__.py) | 添加自选、删除自选 | ⏭️ 跳过（多轮交互命令） |
| [`stock_status/__init__.py`](../SayuStock/stock_status/__init__.py) | 无触发器（状态注册） | ⏭️ 不适用 |
| [`stock_ai_func/__init__.py`](../SayuStock/stock_ai_func/__init__.py) | 无触发器（AI实体注册） | ⏭️ 不适用 |

### 2.2 数据层改造（注入 `ai_return()` 调用）

| 文件 | 注入的辅助函数 | 状态 |
|------|--------------|------|
| [`stock_cloudmap/get_cloudmap.py`](../SayuStock/stock_cloudmap/get_cloudmap.py) | `_ai_return_single_stock`、`_ai_return_kline`、`_ai_return_compare_stock`、`_ai_return_cloudmap` | ✅ 已完成（此前已改造） |
| [`stock_info/draw_info.py`](../SayuStock/stock_info/draw_info.py) | `_ai_return_market_overview` | ✅ 本次改造 |
| [`stock_info/draw_my_info.py`](../SayuStock/stock_info/draw_my_info.py) | `_ai_return_my_stock` | ✅ 本次改造 |
| [`stock_info/draw_future.py`](../SayuStock/stock_info/draw_future.py) | `_ai_return_all_weather` | ✅ 本次改造 |
| [`stock_info/draw_fund_info.py`](../SayuStock/stock_info/draw_fund_info.py) | `_ai_return_fund_info` | ✅ 本次改造 |
| [`stock_ai/draw_ai_map.py`](../SayuStock/stock_ai/draw_ai_map.py) | `_ai_return_kronos_data` | ✅ 本次改造 |

### 2.3 AI 工具清理

| 文件 | 变更 | 状态 |
|------|------|------|
| [`stock_ai_func/ai_tools.py`](../SayuStock/stock_ai_func/ai_tools.py) | 移除 10 个重复工具，保留 5 个独立工具 | ✅ 本次改造 |

---

## 三、触发器改造详情

### 3.1 stock_cloudmap 模块（此前已完成）

| 触发器 | 类型 | `to_ai` 功能描述 |
|--------|------|-----------------|
| 大盘云图 | `on_command` | 查看A股大盘行业板块涨跌分布云图 |
| 板块云图/行业云图/行业板块 | `on_command` | 查看行业板块涨跌分布云图 |
| 概念云图/概念板块云图/概念板块 | `on_command` | 查看概念板块涨跌分布云图 |
| 我的个股 | `on_fullmatch` | 查看用户自选股的分时行情图 |
| 个股 | `on_command` | 查询指定股票或ETF的K线图或分时图 |
| 对比个股/个股对比 | `on_command` | 对比多只股票/ETF的分时涨跌幅走势 |

### 3.2 stock_info 模块（本次改造）

| 触发器 | 类型 | `to_ai` 功能描述 |
|--------|------|-----------------|
| 基金持仓/持仓分布 | `on_command` | 查询指定基金的持仓股票分布信息 |
| 大盘概览/大盘概况 | `on_fullmatch` | 查看A股大盘整体概览 |
| 我的自选/我的持仓/我的股票 | `on_fullmatch` | 查看用户自选股列表的当日行情概览 |
| 全天候/全天候板块 | `on_fullmatch` | 查看全天候策略板块数据 |

### 3.3 stock_ai 模块（本次改造）

| 触发器 | 类型 | `to_ai` 功能描述 |
|--------|------|-----------------|
| 模型预测/ai预测/AI预测/趋势预测 | `on_prefix` | 使用Kronos AI模型预测指定股票的未来价格走势 |

### 3.4 stock_news 模块（本次改造）

| 触发器 | 类型 | `to_ai` 功能描述 |
|--------|------|-----------------|
| 订阅雪球新闻/订阅雪球热点 | `on_fullmatch` | 订阅雪球7x24小时财经新闻推送 |
| 取消订阅雪球新闻/取消订阅雪球热点 | `on_fullmatch` | 取消订阅雪球财经新闻推送 |

### 3.5 stock_sina 模块（本次改造）

| 触发器 | 类型 | `to_ai` 功能描述 |
|--------|------|-----------------|
| 市盈率对比 | `on_prefix` | 对比多只股票的市盈率(PE)历史走势 |
| 市净率对比 | `on_prefix` | 对比多只股票的市净率(PB)历史走势 |

### 3.6 stock_user 模块（未改造）

| 触发器 | 类型 | 未改造原因 |
|--------|------|-----------|
| 添加自选/添加个股/添加股票/添加持仓/加入自选 | `on_command` | 使用 `bot.receive_resp` 多轮交互确认，AI 无法完成 |
| 删除自选/删除个股/删除股票/移除自选/删除持仓 | `on_command` | 使用 `bot.receive_resp` 多轮交互确认，AI 无法完成 |

---

## 四、数据层 `ai_return()` 注入详情

### 4.1 注入机制说明

`ai_return(text)` 的行为：
- **AI 调用时**：将文字收集起来，作为工具的返回值传回给 AI
- **用户直接触发时**：什么都不做，完全透明，不影响原有逻辑

**关键原则**：`await bot.send(text)` 纯文字内容本身就会被 MockBot 收集并返回给 AI，**不需要**先 `ai_return()` 再 `bot.send()`。
**只有在返回图片之前**，才需要用 `ai_return()` 提供文字摘要，因为图片会被 MockBot 拦截暂存，AI 看不到图片内容。

注入位置：在数据已经拿到、图片还没生成时调用，传递结构化的文本数据摘要。

### 4.2 各辅助函数说明

#### `_ai_return_market_overview`（大盘概览）
- **注入位置**：[`draw_info.py`](../SayuStock/stock_info/draw_info.py:248)
- **提取内容**：主要指数行情（上证、深证、创业板等12个指数）、涨跌分布统计、领涨/领跌行业板块各5个

#### `_ai_return_my_stock`（我的自选）
- **注入位置**：[`draw_my_info.py`](../SayuStock/stock_info/draw_my_info.py:241)
- **提取内容**：自选股数量、平均涨跌幅、自选代码列表

#### `_ai_return_all_weather`（全天候板块）
- **注入位置**：[`draw_future.py`](../SayuStock/stock_info/draw_future.py:113)
- **提取内容**：全球股市指数、大宗商品价格、国债收益率、加密货币行情

#### `_ai_return_fund_info`（基金持仓）
- **注入位置**：[`draw_fund_info.py`](../SayuStock/stock_info/draw_fund_info.py:80)
- **提取内容**：基金名称、持仓数量、平均涨跌幅、前10大重仓股及占比

#### `_ai_return_pepb_compare`（市盈率/市净率对比）
- **注入位置**：[`gen_image.py`](../SayuStock/stock_sina/gen_image.py:35)
- **提取内容**：对比标的名称列表、对比类型（PE/PB）

#### `_ai_return_kronos_data`（AI预测）
- **注入位置**：[`draw_ai_map.py`](../SayuStock/stock_ai/draw_ai_map.py:152)
- **提取内容**：股票名称、数据周期、数据条数、最新K线数据、最近5条K线趋势

#### 此前已注入的辅助函数（stock_cloudmap 模块）

| 函数 | 提取内容 |
|------|---------|
| `_ai_return_single_stock` | 个股分时行情：名称、最新价、涨跌幅、开盘/最高/最低、换手率、成交额 |
| `_ai_return_kline` | K线数据：名称、周期、最近10条K线的日期/开/收/高/低/涨跌幅 |
| `_ai_return_compare_stock` | 对比数据：各股票名称、收盘价、涨跌幅 |
| `_ai_return_cloudmap` | 云图数据：领涨/领跌各5个板块、涨跌家数统计 |

---

## 五、ai_tools.py 清理详情

### 5.1 移除的工具（已被触发器覆盖）

| 工具函数 | 覆盖它的触发器 |
|---------|--------------|
| `get_stock_basic` | `个股` 触发器 + `_ai_return_single_stock` |
| `get_market_summary` | `大盘概览` 触发器 + `_ai_return_market_overview` |
| `get_sector_leader` | `板块云图`/`概念云图` 触发器 + `_ai_return_cloudmap` |
| `get_fund_holdings` | `基金持仓` 触发器 + `_ai_return_fund_info` |
| `get_my_watchlist` | `我的自选` 触发器 + `_ai_return_my_stock` |
| `get_stock_kline` | `个股` 触发器（日K/周K等） + `_ai_return_kline` |
| `get_commodity_prices` | `全天候` 触发器 + `_ai_return_all_weather` |
| `get_bond_prices` | `全天候` 触发器 + `_ai_return_all_weather` |
| `get_global_stock_indexes` | `全天候` 触发器 + `_ai_return_all_weather` |
| `get_all_weather_data` | `全天候` 触发器 + `_ai_return_all_weather` |

### 5.2 保留的工具（无触发器覆盖或提供更精确的独立能力）

| 工具函数 | 保留原因 |
|---------|---------|
| `get_latest_news` | 触发器是订阅操作，不覆盖读取新闻的功能 |
| `get_crypto_prices` | 专门查加密货币，比全天候触发器更精确 |
| `get_vix_index` | 专门查VIX指数，无对应触发器 |
| `search_stock` | 股票代码搜索，无对应触发器 |
| `get_stock_change_rate` | 提供精确日期范围涨跌幅计算，比触发器更灵活 |

---

## 六、未改造的模块说明

| 模块 | 原因 |
|------|------|
| `stock_user` | `添加自选`和`删除自选`使用 `bot.receive_resp` 进行多轮交互确认，当前 AI 机制不支持多轮会话 |
| `stock_status` | 仅注册插件状态信息，无用户触发器 |
| `stock_ai_func` | 仅注册 AI 实体（知识库），无用户触发器 |
| `SayuStock/__init__.py` | 仅注册插件元信息（名称、前缀），无用户触发器 |

---

## 七、改造质量检查

### 触发器层
- [x] 所有应改造的 `on_xxx` 装饰器都已加 `to_ai` 参数
- [x] `to_ai` 字符串的第一句话能让 AI 准确识别触发意图
- [x] `text` 参数格式说明清晰，有具体例子
- [x] `on_fullmatch` 无参数型已注明"无需参数，留空即可"
- [x] 多 keyword 的 tuple 形式语法正确

### 数据层
- [x] 已 `from gsuid_core.ai_core.trigger_bridge import ai_return`
- [x] 每类数据都有对应的 `_ai_return_xxx()` 辅助函数
- [x] 注入点在数据获取后、图片生成前
- [x] 辅助函数用 `try/except` 包裹，错误只 `logger.warning`
- [x] `ai_return` 的文本内容包含足够的关键信息

### 不破坏性检查
- [x] 原触发器函数体完全未修改
- [x] `ai_return()` 调用在辅助函数里，不在触发器函数里
- [x] 没有给触发器函数添加任何额外参数
- [x] 用户直接触发时行为完全不变

---

## 八、AI 调用示例

改造后，AI 可以通过以下方式调用触发器：

```
用户: "帮我看看贵州茅台今天怎么样"
AI → 调用 个股 触发器，text="贵州茅台"
→ 返回分时行情数据 + 生成图片

用户: "大盘今天涨跌如何"
AI → 调用 大盘概览 触发器
→ 返回主要指数、涨跌分布数据 + 生成图片

用户: "帮我预测一下证券ETF的走势"
AI → 调用 模型预测 触发器，text="证券ETF"
→ 返回K线基础数据 + 生成预测图

用户: "对比一下白酒ETF和医药ETF年初至今的表现"
AI → 调用 对比个股 触发器，text="年初至今 白酒ETF 医药ETF"
→ 返回对比数据 + 生成对比图
```
