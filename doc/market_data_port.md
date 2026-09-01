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
snap = await market.hotmap()
```

## 领域模型

| 模型 | 用途 |
|------|------|
| `Quote` | 快照行情 |
| `IntradaySeries` | 分时 |
| `KlineSeries` | K 线 |
| `BoardSnapshot` | 板块/云图列表 |

## 渲染层

- `utils/render_data.py`：只接受上述模型
- `utils/render_text.py`：AI 文字版同样只接受模型
- `stock_stockinfo/data.py` / `stock_cloudmap/data.py`：`CloudMapDataResult` 内为模型或错误文本

## 扩展新数据源

1. 实现 `MarketDataPort`（可继承 `adapters._base.PartialMarketData` 只覆盖子集）。
2. 在 `facade.build_default_market` / `CompositeMarketData` 中注册路由。
3. **禁止**在 feature 模块解析供应商原始字段。

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
