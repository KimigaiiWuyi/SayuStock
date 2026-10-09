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
   没实现的方法，以及目录里根本没有的那只标的，返回 `unsupported`（或映射函数返回 None）。
   不要把别的 secid 拼成一个空代码。只拿到盘口的品种，分时和 K 线在发请求前就拒绝。
2. 在 `provider_registry._PROVIDER_FACTORIES` / `PROVIDER_LABELS` 注册供应商，并把 id 加进
   `_SYSTEM_ORDER`（链尾兜底次序）；无需新增配置项——五域源链对每个接口自动生效。
   能力不全无需特殊处理——`unsupported` 会自动落到链上后续源。
   若新源补齐了原先「只有一个源」的接口，记得把该源加进对应域配置的 `options`
   （域与配置键的对应关系见 `_IFACE_GROUPS` / `_GROUP_CHAIN_CONFIG_KEYS`；
   有测试保证每个域都有键，但 `options` 是手写的，见覆盖面矩阵 §13.9 的能力表）。
   展示名加进 `display.PROVIDER_DISPLAY`。
3. **禁止**在 feature 模块解析供应商原始字段。非沪深京品种用显式符号表，列序先拿一条
   实盘和东财对齐再写解析器。
4. 成功模型带 `provider`（文档里的 sourceBy，`str | None = None`，放字段末尾）。
   新模型加入 `display._STAMPABLE`，由路由盖章，已有值不覆盖。不要再加第二个来源字段。
   不走端口的直连在结果上写死自己的 id。`sector_menu` 仍是名字到代码的 dict，来源看旁边的 `board`。
5. 每张图用 `display.source_footer(*providers)` 把这次用到的源画在角落里。一图多块时全部传进去。
   禁止写死「数据来源：东方财富」。
6. 补 `test/market/`：符号映射、列序、盖章、图角文案。改覆盖面矩阵里被这次打脸的那几句。

品种备忘（2026-10-09）：中证2000 `2.932000` 腾讯/新浪/同花顺都没有，前缀 `2` 映射为 None。
黄金9999 新浪是 `gds_AU9999`（沪金99，仅盘口）。三十债主连新浪是 `nf_TL0`（仅盘口，成交额留空）。
全天候备用符号与东财 secid 不同名，只锁盘口。新浪：恒生 `rt_hkHSI`、日经 `b_NKY`、富时 `b_UKX`、
CAC `b_CAC`、DAX `b_DAX`、伦敦金/银 `hf_XAU`/`hf_XAG`（不是沪金99，也不是 `hf_GC`）、
NYMEX 原油 `hf_CL`、综合铜 `hf_CAD`、螺纹/豆粕/焦煤/生猪 `nf_RB0`/`nf_M0`/`nf_JM0`/`nf_LH0`、
离岸人民币/瑞郎/日元 `fx_susdcnh`/`fx_susdchf`/`fx_susdjpy`、美元指数 `DINIW`。
腾讯：恒生 `hkHSI`、伦敦金/银/原油/伦铜 `hf_*`、日元/瑞郎 `fxUSDJPY`/`fxUSDCHF`。
`100.KOSPI200`、`100.SXXP`、`171.*` 国债收益率没有新鲜盘口。国际市场列表仍只有东财；
列表失败时全天候按单只报价画其余格子。

## 行情API 数据源优先级（后台设置「行情API」）

- 网页控制台 → 插件配置 →「行情API」→ **六个源链列表**（`GsListStrConfig`）：
  全局 `market_api_chain` + 六域 `market_api_chain_{quote,kline,board,market,exclusive,ipo}`。
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

## IPO 日历（`ipo_calendar(market)`）

- 接口按市场拆三个 iface（`ipo_cn` / `ipo_hk` / `ipo_us`），都映射到 `ipo` 域
  （后台「⑥ IPO 日历源链」）；`market` 参数收 `IpoMarket` 或 `cn/hk/us/A股/港股/美股`。
- 可选供应商：`eastmoney`（三市场全覆盖：A股 datacenter 申购表含申购/中签/缴款/上市全流程，
  港美股 push2 clist 按上市日排序）＋ `nasdaq`（仅美股官方日历，含已申报/预期定价阶段、
  发行价与募资额；限流时返回错误顺延）。`nasdaq` 不在 `_SYSTEM_ORDER`，只有 IPO 域链
  显式配置才参与，默认链＝纯东财（满足「出厂默认＝清空回落」不变量，
  见 `test_market_config_defaults`）。
- `adapters/aastocks/` 是港股**增强源**（非独立供应商）：东财 provider 的 hk 分支内合并
  招股截止日/暗盘/上市价/超购倍数/首日表现，失败仅告警；push2 不可达时降级为港股兜底数据
  （不含 GEM/介绍上市，名称为繁体）。
- 领域模型 `IpoEvent`（`models/ipo.py`）：阶段不落库，按查看日 `stage_on()` 推导
  （申购→待上市→已上市；美股另有已申报）。业务入口 `stock_ipo/draw_ipo.py`，
  命令「IPO日历 / IPO / ipo」（可带市场筛选，如 `aIPO 美股`）。

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
