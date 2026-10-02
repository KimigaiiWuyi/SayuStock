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
2. 在 `provider_registry._PROVIDER_FACTORIES` / `PROVIDER_LABELS` / `_PROVIDER_RANKS`
   注册供应商，并在 `stock_config/config_default.py` 加 `market_api_priority_<id>` 整数配置；
   能力不全无需特殊处理——`unsupported` 会自动回落默认源。
3. **禁止**在 feature 模块解析供应商原始字段。

## 行情API 数据源优先级（后台设置「行情API」）

- 网页控制台 → 插件配置 →「行情API」→ 每源一个**优先级数字**（`market_api_priority_<id>`，
  0-100，数字越大越先尝试，0=禁用），所有行情接口共用。数字相同的源按各源系统内禀序号
  （`_PROVIDER_RANKS`：东财9/腾讯8/新浪7/同花顺6，大者先）裁决；出厂默认 40/30/20/10。
- 路由实现：`utils/market/provider_registry.py` 的 `ConfigurableEquityMarket`
  （equity 槽位包装）。配置每次调用时读取，网页控制台改完**立即热生效**。
- 取数语义（尽可能交付）：按优先级逐一尝试，成功即返回；源不支持该接口（`unsupported`）
  跳过；网络/解析/空数据错误顺延下一个源；`not_found` 短路返回（解析层共用）。
  全部失败才报错，报优先级最高的真实错误。
- 东财被显式禁用（=0）后**不再自动补链尾**：云图/北向/估值/财报等东财独占接口随之
  无兜底；全部禁用时保底东财防整体瘫痪。旧版 `market_api_priority` 链串配置在装配时
  一次性迁移为每源数字（`migrate_legacy_priority_config`，幂等）。
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
