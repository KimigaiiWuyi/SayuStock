# MarketDataPort 行情抽象层

业务代码应通过 `SayuStock.utils.market` 获取行情，而不是直接读东财 `f*` 字段，也**不要**再走 `compat` / 东财形 dict。

## 快速使用

```python
from SayuStock.utils.market import get_market, is_market_error, KlinePeriod

market = get_market()
q = await market.quote("茅台")
if is_market_error(q):
    return q.message
print(q.price, q.change_pct, q.symbol.code)

kl = await market.kline("600519", KlinePeriod.D1)
series = await market.intraday("600519")
five = await market.intraday("600519", ndays=5)  # 五日分时
snap = await market.hotmap()
```

## 领域模型

| 模型 | 用途 |
|------|------|
| `Quote` | 快照行情 |
| `IntradaySeries` | 分时（`ndays=1` 当日，`ndays=5` 五日） |
| `KlineSeries` | K 线 |
| `BoardSnapshot` | 板块/云图列表 |

## 渲染层

- `utils/render_data.py`：只接受上述模型
- `utils/render_text.py`：AI 文字版同样只接受模型
- `stock_stockinfo/data.py` / `stock_cloudmap/data.py`：`CloudMapDataResult` 内为模型或错误文本

## 扩展新数据源

1. 实现 `MarketDataPort`（可继承 `adapters._base.PartialMarketData` 只覆盖子集）。
2. 在 `provider_registry._PROVIDER_FACTORIES` / `PROVIDER_LABELS` 注册供应商，并把 id 加进
   `_SYSTEM_ORDER`（链尾兜底次序）；无需新增配置项——五域源链对每个接口自动生效。
   能力不全无需特殊处理——`unsupported` 会自动落到链上后续源。
   若新源补齐了原先「只有一个源」的接口，记得把该源加进对应域配置的 `options`
   （域与配置键的对应关系见 `_IFACE_GROUPS` / `_GROUP_CHAIN_CONFIG_KEYS`；
   有测试保证每个域都有键，但 `options` 是手写的，见覆盖面矩阵 §13.9 的能力表）。
3. **禁止**在 feature 模块解析供应商原始字段。

## 行情API 数据源优先级（后台设置「行情API」）

- 网页控制台 → 插件配置 →「行情API」→ **六个源链列表**（`GsListStrConfig`）：
  全局 `market_api_chain` + 五域 `market_api_chain_{quote,kline,board,market,exclusive}`。
  列表顺序即优先级；域链只作用于该域接口，清空回落全局链；全局链清空用内置默认链
  （东财→腾讯→新浪→同花顺）。
- **默认值已按推荐预填**（2026-10-08 起，见覆盖面矩阵 §16）：全局与①②＝东财→腾讯→新浪，
  ③④＝东财→新浪，⑤＝东财。预填值与「留空」的实际生效链等价，照默认用即可，想换顺序再改。
- 每个域键的 **`options` 只列该域真正实现了接口的源**（依据是覆盖面矩阵 §13.9 的逐源实测），
  所以「某组只有一个可选值」本身就等于「这组接口是它独占的」。`exclusive` 组
  （云图/北向/估值/财报）就是这么一组：`options=["东方财富"]`、默认值 `["东方财富"]`，
  填别的也不会生效（不支持的源会被跳过、链尾兜底仍是东财），它的作用是**说明**而非控制。
- **链外源排链尾兜底**（不是禁用）：列表里没写的源自动按系统内禀次序
  （`_SYSTEM_ORDER`：东财→腾讯→新浪→同花顺）追加到链尾。因此任何配置下四个源都在链上，
  东财独占接口（云图/北向/估值/财报/五日分时/概念板块）永远保有兜底尝试。
- 路由实现：`utils/market/provider_registry.py` 的 `ConfigurableEquityMarket`
  （equity 槽位包装）。`_IFACE_GROUPS` 把 14 个接口映射到 5 个域；`resolve` 不在域内、
  恒走全局链链头。配置每次调用时读取，网页控制台改完**立即热生效**。
- 取数语义（尽可能交付）：按链逐一尝试，成功即返回；源不支持该接口（`unsupported`）
  跳过；网络/解析/空数据错误顺延下一个源；`not_found` 短路返回（解析层共用）。
  全部失败才报错，报链上第一个真实错误。
  **空行/缺行不属于 `not_found`**：符号映射成功、只是这一行没数据时报 `empty`，照样顺延 ——
  否则东财失败后，腾讯一行空占位就把后面的源全挡住。
- 时间预算：每个源最多占 `SOURCE_TIMEOUT_S`（25s）。调用方可用
  `utils.market.chain_deadline(seconds)` 声明整链总预算，链内按「剩余预算 / 剩余源数」
  分片，但**不低于 `MIN_SOURCE_SLICE_S`（8s）**：只平摊的话 4 源各约 3s，低于东财首拨
  建连的冷启动实测 3.98s，一次正常取价会被切在成功之前。时间片同时不越出剩余总预算。
  只在外层包一个 `asyncio.wait_for` 是不够的：那取消的是**整个** `quote()`。
- 数据能否真正交付还取决于**唯一源的上游状态**：上游限流/停发（例如北向
  `kamt` 返回 `rc=102, data=null`）时任何配置都变不出数据——本配置系统保证的是
  「源被尝试且尽可能交付」，不是「绕过上游」。
- 可选供应商：`eastmoney`（全接口）、`sina`（盘口/分时/分钟日K/沪深A/指数/行业板块/
  换手成交额成交量排行/行业菜单）、`tencent`（盘口/分时/分钟K+前复权日周月K）、
  `ths`（同花顺扶摇 fuyao.aicubes.cn：A股票（含北交所）/指数/ETF 盘口+前复权日K，
  API Key 见「同花顺API密钥」配置；分时/分钟K的高频动向接口未开放外部接入）。
- `resolve` 的 `provider_symbol` 恒为东财 secid（`150.*` 判场外基金依赖此约定），不随源切换变化。

## OKX / VIX / 场外基金

- 加密货币经 `CompositeMarketData` 路由到 `OkxMarketData`
- VIX 路由到 `VixMarketData`
- 场外基金（东财 `150.*`，如 `720001`）路由到 `TiantianFundMarketData`，K 线为累计净值；Quote 用单位净值
- 业务侧统一 `get_market().intraday/kline/quote`

## 已移除

- 业务侧 `board_to_em_dict` / `kline_to_em_dict` / `quote_with_intraday_to_em` 公开导出
- `get_gg` / `get_vix` / `get_mtdata` 的 compat 编码（现返回领域模型）
- `render_data` 的 legacy dict 解析入口

`utils/market/compat.py` 仅供测试/调试对照，**新代码禁止依赖**。
