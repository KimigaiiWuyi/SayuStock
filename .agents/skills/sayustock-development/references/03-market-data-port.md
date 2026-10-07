# 三、行情数据层 MarketDataPort

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · **上一章**：[二](./02-plugin-layout-and-commands.md) · **下一章**：[四、渲染管线](./04-render-pipeline.md)

**本章是 SayuStock 最硬的不变量：业务代码不读供应商原始字段。**

## 3.1 为什么要有 Port

历史上业务直接吃东财 JSON（`f43` 现价、`f45` 最低价……），导致：

- 字段语义极易搞反（`f45` 是最低价不是涨跌幅）
- OKX / VIX 要再写一套解析
- 绘图与 AI 工具各自 parse，行为分叉

现在：**HTTP 与字段映射关在 adapter；上层只见领域模型。**

## 3.2 业务侧标准用法

```python
from SayuStock.utils.market import get_market, is_market_error, KlinePeriod

port = get_market()

q = await port.quote("茅台")
if is_market_error(q):
    return q.message  # 用户可读错误

series = await port.intraday("600519")
five = await port.intraday("600519", ndays=5)  # 五日分时，东财走 push2his
kl = await port.kline("600519", KlinePeriod.D1)
snap = await port.hotmap()
board = await port.board("沪深A", limit=100, sort_asc=False)
menu = await port.sector_menu("concept")  # 或 "industry"
fin = await port.financial_snapshot("600519")
```

- **错误**：`MarketError`，用 `is_market_error(x)` 判断，展示 `x.message`
- **成功**：对应 dataclass（多为 `frozen` + `slots`）

## 3.3 领域模型（`utils/market/models/`）

| 类型 | 含义 | 常见字段 |
|------|------|----------|
| `SymbolRef` | 标的身份 | `code`, `name`, `asset_class`, `exchange`, `provider_symbol` |
| `Quote` | 快照 | `price`, `open/high/low`, `prev_close`, `change_pct`（百分数如 1.23）, `amount`, `turnover_rate`, … |
| `IntradayPoint` / `IntradaySeries` | 分时 | `points`、可选 `quote`、`ndays`（1=当日，5=五日） |
| `Bar` / `KlineSeries` | K 线 | `period: KlinePeriod`, `bars`, `adjusted` |
| `BoardRow` / `BoardSnapshot` | 列表/云图 | `kind`, `title`, `rows`（含 `change_pct`, `market_cap`, `industry`…） |
| `BoardExtras` | 扩展列 | pe / turnover / lead_code… |
| `ValueSeries` | 估值序列 | PE/PB/DY |
| `FinancialSnapshot` | 财报摘要 | |
| `BreadthBar` / `MarketTurnover` / `NorthboundFlow` | 市场宽度/成交额/北向 | |

转 DataFrame：`kline_to_df` / `kline_to_cn_df` / `board_to_df`（`convert/dataframe.py`）。

列表展示：`display.from_quote` / `from_board_row` / `board_rows_to_items`。

**数据来源归属（provider 字段）**：`Quote` / `KlineSeries` / `IntradaySeries` /
`BoardSnapshot` / `RankSnapshot` / `ValueSeries` 均带 `provider: str | None`——
equity 链在 `ConfigurableEquityMarket._dispatch` 命中时盖章（`display.stamp_provider`）；
okx/vix/tiantian 槽位在 `CompositeMarketData` 路由时盖章。图表左下角「数据来源」
标签用 `display.source_label(*providers)`（id→中文名，多源去重拼接，None 回退
「东方财富」）；**禁止再硬编码「数据来源：东方财富」**。

## 3.4 `MarketDataPort` 方法清单

定义在 `utils/market/port.py`：

| 方法 | 返回 |
|------|------|
| `resolve(query)` | `SymbolRef \| None` |
| `quote` / `quotes` | `Quote \| MarketError` |
| `intraday(query, *, ndays=1)` | `IntradaySeries \| MarketError`；`ndays=5` 为五日分时 |
| `kline(query, period, *, start, end)` | `KlineSeries \| MarketError` |
| `board(kind \| str, *, sector, limit, sort_asc)` | `BoardSnapshot \| MarketError` |
| `hotmap` | `BoardSnapshot \| MarketError` |
| `sector_menu("industry"\|"concept")` | `dict[str,str] \| MarketError` |
| `breadth` / `market_turnover` / `northbound` | 对应模型 |
| `valuation_series` / `financial_snapshot` | 对应模型 |

`board` 的 `kind` 可为 `BoardKind` 或市场别名字符串（如 `"沪深A"`、板块代码）；adapter 内
`_market_key` 映射到东财 `fs` / 列表接口。

## 3.5 `KlinePeriod`（`enums.py`）

与业务 sector 后缀对齐：

| 枚举 | value | 含义 |
|------|-------|------|
| `M5` / `M15` / `M30` / `M60` | 5/15/30/60 | 分钟 K |
| `D1_RECENT` | 100 | 短窗日 K（旧 k线） |
| `D1` | 101 | 日 K |
| `W1` / `MON1` / `Q1` / `H1` / `Y1` | 102–106 | 周/月/季/半年/年 |
| `D1_YEAR` | 111 | 对比图等用的日 K 窗口 |

## 3.6 装配与路由

```
get_market()  →  registry 单例
build_default_market()  →  CompositeMarketData(
    equity=ConfigurableEquityMarket(),   # 后台「行情API」优先级链驱动
    crypto=OkxMarketData(),
    vix=VixMarketData(),
    fund=TiantianFundMarketData(),
)
```

**行情API 源链（`provider_registry.py`）**：equity 槽位是 `ConfigurableEquityMarket`
包装，**五个源链列表**（`GsListStrConfig`）驱动：全局 `market_api_chain` + 四域
`market_api_chain_{quote,kline,board,market}`（`_IFACE_GROUPS` 把 14 接口映射到域；
`resolve` 不在域内、恒走全局链链头）：

- 列表顺序即优先级；域链留空回落全局链，全局链留空用内置默认链（东财→腾讯→新浪→同花顺）。
- **链外源排链尾兜底**（不是禁用）：没写进列表的源自动按 `_SYSTEM_ORDER`
  （东财→腾讯→新浪→同花顺）追加到链尾——任何配置下四个源都在链上，东财独占接口
  （云图/北向/估值/财报/五日分时/概念板块）永远保有兜底尝试。
- 取数语义（尽可能交付）：按链逐一尝试，**成功即返回**；源 `unsupported`
  （不支持该接口，如腾讯无板块）**跳过**；网络/解析/空数据错误**顺延下一个源**；
  源内部**抛异常**（如东财瞬断时连接错误穿透）转 network 错误顺延，不炸链；
  `not_found` 短路返回（标的解析层共用，换源无意义）。全部失败才报错，
  报链上第一个真实错误。
- 解析层（`get_code_id` → 东财 searchapi）失败 ≠ 标的不存在：`get_code_id_strict`
  在网络/HTTP 失败时抛 `ResolveLayerError`（HTTP 200 无结果才是真不存在）；
  东财/腾讯/新浪适配器把它转 **network 错误顺延**，避免误报 not_found 短路
  （用户会对真实存在的股票看到「不存在该股票」）。存量 `get_code_id` 契约
  不变（失败仍返回 None）；端口 `resolve()` 用 `resolve_em_symbol_safe` 不抛。
- 边界：配置保证「源被尝试且尽可能交付」，不保证「唯一源上游限流/停发时仍有数据」
  （例：北向 `kamt` 返回 `rc=102, data=null` 属上游停发；东财 push2 域
  连接断开时 `stock_request` 返回自造码 `-400016`）。
- 配置**每次调用时读取**（`STOCK_CONFIG.get_config`），网页控制台改完立即热生效；
  供应商实例按 id 缓存。
- `resolve` 走链头（各源 resolve 语义一致：`provider_symbol` 恒为东财 secid，
  `150.*` 判场外基金依赖此约定）。
- 新源接入：`_PROVIDER_FACTORIES` 加工厂 + `_PROVIDER_ALIASES`/`PROVIDER_LABELS`
  加名称 + `_SYSTEM_ORDER` 定兜底次序（无需新增配置项）。

`CompositeMarketData._route(query)`：

1. `is_vix_query` → VIX adapter  
2. `is_crypto_query` → OKX adapter  
3. 场外基金（东财 QuoteID `150.*`，如 `720001`）→ 天天基金净值  
4. 否则 → equity（`ConfigurableEquityMarket` 按优先级链逐一尝试东财/新浪/腾讯）；若返回的序列
   仍是 `150.*` / `AssetClass.FUND`，Composite 再改走天天基金（名称无「混合」等关键字的安全网）  

`board` / `hotmap` / 菜单 / 北向等**固定走 equity**（加密/VIX/场外基金无板块云图语义）。
`个股 日k/周k` 命中场外基金时，data 层改走 `compare-stock`（净值增长率，不是蜡烛图）。

测试可 `set_market(fake_port)` 注入假实现。

## 3.7 Adapter 内部职责

### 东财 `adapters/eastmoney/`

| 文件 | 职责 |
|------|------|
| `provider.py` | 实现 Port：解析 secid、调 `EASTMONEY_REQUESTER`、调 parse |
| `parse_quote.py` | 盘口 → `Quote` |
| `parse_intraday.py` | trends → `IntradaySeries` |
| `parse_kline.py` | klines 字符串 → `KlineSeries` |
| `parse_board.py` | clist/hotmap → `BoardSnapshot` |
| `parse_value.py` / `parse_finance.py` | 估值/财报 |
| `map_fields.py` | 字段表常量（**仅 adapter 可见**） |
| `json_util.py` | 安全取值 helpers |

传输层 `utils/eastmoney.py` 的 `EASTMONEY_REQUESTER`：**允许 adapter 与少数特殊筛选用**；
feature 模块不应再直接 `stock_request` 然后读 `f*`。

### OKX / VIX / 天天基金 / 新浪 / 腾讯

- `okx/client.py` + `parse.py` + `provider.py`：candle / index-ticker → 模型  
- `vix/provider.py`：`get_vix_data` → `IntradaySeries`  
- `tiantian/client.py` + `parse.py` + `provider.py`：场外基金搜索 + `FundMNHisNetList` 累计净值 → `KlineSeries`（OHLC 均为净值，供对比图归一化）  
- `sina/client.py` + `parse.py` + `provider.py`：备用权益源。`hq.sinajs.cn` 盘口（GBK）、
  `getKLineData` 分钟/日K（**不复权**）、`getMinlineData` 当日分时、行情中心
  `getHQNodeData`（沪深A hs_a / 指数 hs_s / 行业成分）与 `newSinaHy` 行业板块汇总 →
  换手/成交额/成交量排行。无概念板块/资金流/ROE/周月K（unsupported 自动回落东财）。
  **美股**（secid 105/106/107/153 → `gb_` **小写**符号；100.SPX/DJIA/NDX 指数 →
  `gb_inx`/`gb_dji`/`gb_ixic`，**东财 NDX 实为纳指综合**）：盘口 + 日K
  `US_MinKService.getDailyK`（**裸代码** QQQ / 指数 `.inx`，全量历史按 start/end 过滤
  尾部截取）+ 分钟K `US_MinKService.getMinK?symbol=…&type=5/15/30/60`（qn 无效恒 3，
  **最多 1023 根**，时间戳**美东** → parse 层转北京时间，夏令时 zoneinfo 自动处理）+
  分时 `type=1`（末时刻标注 09:31-16:00，逐 bar 量额，取最近交易日；**新鲜度守卫**：
  末点超 10 天（OTC/.inx/.dji 停更于 2020，.ixic 反而新鲜）→ unsupported 回落东财）。
  量=股、额/市值=美元；指数 a=0 时均价回退当前价。
- `tencent/client.py` + `parse.py` + `provider.py`：备用权益源。`qt.gtimg.cn` 盘口（GBK，含
  PE/PB/市值/涨跌停）、`fqkline` **前复权**日/周/月K、`mkline` 分钟K、`minute/query` 当日分时
  （累计量额差分）。K 行列序为**开、收、高、低**（与直觉相反）。无板块/排行（unsupported 回落）。
  **美股**（secid 105/106/107/153 → `us` 符号；100.SPX/DJIA/NDX → `usINX`/`usDJI`/`usIXIC`，
  代码与东财不同名）：**仅盘口**（量=股、额=美元、PB 在 43 列、时间为美东、无涨跌停）；
  美股 K线（fqkline/usfqkline 只回首末两根）、分钟K（mkline param error）与分时
  （minute/query 仅末点）无有效数据，unsupported 回落东财。

## 3.8 薄封装 `utils/stock/request.py`

历史入口，**现已返回领域模型**（非 EM dict）：

| 函数 | 行为 |
|------|------|
| `get_gg(market, sector, start, end)` | `single-stock` / `single-stock-ndays-*` → intraday；`single-stock-kline-*` → kline |
| `get_vix(name)` | `port.intraday` |
| `get_mtdata` / `get_hotmap` | board / hotmap |

**新代码优先直接 `get_market()`**，避免再叠一层 sector 字符串约定。

`_get_gg` / `_get_gg_kline` 是底层 HTTP，仅适配器内部语义。

## 3.9 `compat.py` 的定位

`quote_to_em_dict` / `kline_to_em_dict` / `board_to_em_dict`：

- **不在** `utils/market/__init__.py` 公开导出  
- 仅 `test/market/*` 做 roundtrip 对照  
- **业务、render、AI 工具禁止 import**

## 3.10 扩展新数据源 checklist

1. 新建 `adapters/<name>/provider.py`，实现 `MarketDataPort`（可继承 `PartialMarketData` 只覆盖子集）。  
2. 所有供应商 JSON 解析写在该 adapter 内，输出标准模型。  
3. 在 `provider_registry.py` 的 `_PROVIDER_FACTORIES` / `_PROVIDER_ALIASES` / `PROVIDER_LABELS` /
   `_SYSTEM_ORDER` 注册（`_SYSTEM_ORDER` 决定它排在链外兜底的位置）；
   无需新增配置项——四域源链对每个接口自动生效
   （能力不全的接口无需特殊处理，链式取数会自动跳过 unsupported 并顺延）。  
4. 补 `test/market/` 解析与路由单测。  
5. 不改 feature 模块字段假设。

## 3.11 残留特例（知悉即可）

- `stock_analysis/universe.fetch_clist`：自定义 `fs` 选股分页仍调 `EASTMONEY_REQUESTER`，但经
  `parse_board_row` → `BoardSnapshot` → `board_to_df`，业务读语义列。  
- `get_bar` / `get_hours_from_em`：宽度/成交额辅助，东财原始结构部分仍在 `BreadthBar.raw`。  
- 未来应逐步收进 Port 方法，而不是在 feature 扩散。
