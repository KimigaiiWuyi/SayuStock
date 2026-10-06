# MarketDataPort 接口 × 数据源覆盖矩阵

> **基线**：PR #19（`multi-source-support`，head `c6e0c90`）合并前的 `pr19` ref。本文档所有行号均取自该 ref。
>
> **用途**：回答「插件一共有多少个行情接口、每个接口哪些源能供数、能否按接口单独换源」。
> 配套阅读 [`doc/market_data_port.md`](./market_data_port.md)（契约与扩展规范）。

---

## 1. 口径说明

| 符号 | 含义 |
|---|---|
| ✅ | 该源实现此接口，可正常供数 |
| ⚠️ | **条件性支持**：仅部分周期 / 部分维度 / 部分市场 / 特定参数下可用，其余返回 `unsupported` |
| ❌ | 未实现，继承 `PartialMarketData` 返回 `unsupported`，由 `ConfigurableEquityMarket` **自动顺延**到下一个源 |

判定依据是**类内是否覆写了该方法**，不是「文件里出现过这个词」。所有 ✅/⚠️ 均已逐个打开方法体确认，不是按记忆或文档描述填的。

---

## 2. 接口清单：共 **14 个**

定义位置：`SayuStock/utils/market/port.py` → `class MarketDataPort(Protocol)`

| # | 方法 | 返回类型 |
|---|---|---|
| 1 | `resolve` | `SymbolRef \| None` |
| 2 | `quote` | `Quote \| MarketError` |
| 3 | `quotes` | `list[Quote \| MarketError]` |
| 4 | `intraday` | `IntradaySeries \| MarketError` |
| 5 | `kline` | `KlineSeries \| MarketError` |
| 6 | `board` | `BoardSnapshot \| MarketError` |
| 7 | `rank_list` | `RankSnapshot \| MarketError` |
| 8 | `hotmap` | `BoardSnapshot \| MarketError` |
| 9 | `sector_menu` | `dict[str, str] \| MarketError` |
| 10 | `breadth` | `BreadthBar \| MarketError` |
| 11 | `market_turnover` | `MarketTurnover \| MarketError` |
| 12 | `northbound` | `NorthboundFlow \| MarketError` |
| 13 | `valuation_series` | `ValueSeries \| MarketError` |
| 14 | `financial_snapshot` | `FinancialSnapshot \| MarketError` |

**没有第 15 个。** 已核对 `port.py` 全文、五个非 equity 适配器、以及 `CompositeMarketData`（14 个转发方法，与协议一一对应）。业务层的「基金净值」「美股」「VIX」等不是独立接口，是 `CompositeMarketData` 在这 14 个之上做的**路由分流**（`_route` / `_kline_port`）。

### 相关但不属于契约的两个维度枚举

- `KlinePeriod`（`enums.py`）：**12 个** —— `M5/M15/M30/M60/D1/W1/MON1/Q1/H1/Y1/D1_RECENT/D1_YEAR`
- `RankBy`（`enums.py`）：**7 个** —— `MAIN_INFLOW/MAIN_OUTFLOW/TURNOVER/ROE/AMOUNT/VOLUME/PROFIT_YOY`

这两个枚举是 `kline` / `rank_list` 的**参数空间**，各源覆盖差异极大，见 §5、§6。

---

## 3. 覆盖矩阵总表

| # | 接口 | 东财 | 腾讯 | 新浪 | 同花顺 | 备注 |
|---|---|:--:|:--:|:--:|:--:|---|
| 1 | `resolve` | ✅ | ✅ | ✅ | ✅ | 四源共用东财 searchapi 解析层 |
| 2 | `quote` | ✅ | ✅ | ✅ | ✅ | 港股仅东财 |
| 3 | `quotes` | ✅ | ✅ | ✅ | ✅ | 批量盘口 |
| 4 | `intraday` | ✅ | ✅ | ⚠️ | ❌ | 新浪仅 `ndays=1`；同花顺未开放 |
| 5 | `kline` | ✅ | ⚠️ | ⚠️ | ⚠️ | 周期/复权差异极大，见 §5 |
| 6 | `board` | ✅ | ❌ | ⚠️ | ❌ | 新浪无概念板块 |
| 7 | `rank_list` | ✅ | ❌ | ⚠️ | ❌ | 新浪仅 3/7 维度 |
| 8 | `hotmap` | ✅ | ❌ | ❌ | ❌ | **东财独占** |
| 9 | `sector_menu` | ✅ | ❌ | ⚠️ | ❌ | 新浪仅 industry |
| 10 | `breadth` | ✅ | ❌ | ❌ | ❌ | **东财独占** |
| 11 | `market_turnover` | ✅ | ❌ | ❌ | ❌ | **东财独占** |
| 12 | `northbound` | ✅ | ❌ | ❌ | ❌ | **东财独占** |
| 13 | `valuation_series` | ✅ | ❌ | ❌ | ❌ | **东财独占** |
| 14 | `financial_snapshot` | ✅ | ❌ | ❌ | ❌ | **东财独占** |
| | **覆盖数** | **14/14** | **5/14** | **8/14** | **4/14** | |

> ⚠️ 注意最后一行：**14 个接口里有 6 个（`hotmap`/`breadth`/`market_turnover`/`northbound`/`valuation_series`/`financial_snapshot`）是东财独占、零兜底**。东财挂掉或被设为 0，这 6 个直接报错，不会「换个源接着出图」。

---

## 4. 逐接口详解（含东财实现位置）

> 东财列的行号 = `SayuStock/utils/market/adapters/eastmoney/provider.py`

### 1. `resolve` — 东财 `provider.py:150`

- **东财** `:150` — `EASTMONEY_REQUESTER.resolve_stock(query)`
- **腾讯** `tencent/provider.py:66`、**新浪** `sina/provider.py:94`、**同花顺** `ths/provider.py:54` — 三者均为一行 `return await resolve_em_symbol_safe(query)`

四源解析层**完全共用**东财 searchapi，因此 `provider_symbol` 恒为东财 secid（如 `1.600519`）。这是刻意的：`CompositeMarketData` 用 `150.*` 前缀判定场外基金走天天基金，换源不能破坏该约定。

### 2. `quote` / 3. `quotes` — 东财 `:163` / `:178`

- **腾讯** `tencent/provider.py:84` / `:98` — `qt.gtimg.cn`（GBK），含 PE/PB/市值/涨跌停
- **新浪** `sina/provider.py:112` / `:126` — `hq.sinajs.cn`（GBK），美股符号**必须小写** `gb_qqq`
- **同花顺** `ths/provider.py:72` / `:76` — A股/指数/场内基金快照

**市场覆盖**（依据三个符号映射函数）：

| 市场 | secid 前缀 | 东财 | 腾讯 | 新浪 | 同花顺 |
|---|---|:--:|:--:|:--:|:--:|
| 沪市 | `1.` | ✅ | ✅ | ✅ | ✅ |
| 深市/北交所 | `0.` | ✅ | ✅ | ✅ | ✅ |
| 港股 | `116.` | ✅ | ❌ | ❌ | ❌ |
| 美股 | `105/106/107/153` | ✅ | ✅ 盘口 | ✅ | ❌ |
| 美股指数 | `100.SPX/DJIA/NDX` | ✅ | ✅ | ✅ | ❌ |
| 场外基金 | `150.` | ❌（转天天基金槽） | ❌ | ❌ | ❌ |

港股在三个新源的符号映射里都返回 `None` → `_symbol_of` 发 `unsupported` → 顺延回东财。**港股实际上仍是东财独占。**

### 4. `intraday` — 东财 `:181`

- **腾讯** `tencent/provider.py:126` — `minute/query`，累计量额差分
- **新浪** `sina/provider.py:154` — 首行即 `if ndays > 1: return unsupported("新浪仅支持当日分时")`
- **同花顺** ❌ — 未覆写

⚠️ **五日分时（`ndays=5`）是东财独占。** 新浪一进来就拒。腾讯只给当日。

美股分时只有新浪有实质数据（`getMinK type=1`，逐 bar 量额；OTC/`.inx`/`.dji` 停更于 2020，由 parse 层 10 天新鲜度守卫拒绝后回落东财）。

### 5. `kline` — 东财 `:230`

- **腾讯** `tencent/provider.py:151` — `fqkline` 日/周/月（**前复权**）、`mkline` 分钟
- **新浪** `sina/provider.py:190` — `getKLineData`（**不复权**）
- **同花顺** `ths/provider.py:111` — 仅日线（**前复权**）

⚠️ 复权口径不一致，且 `KlineSeries.adjusted` 字段**全插件零消费**（`grep '\.adjusted' SayuStock/` 无命中），渲染层与指标层不知道源换了：

| 源 | 日/周/月 K | 分钟 K |
|---|---|---|
| 东财 | `adjusted=True`（前复权） | `adjusted=True` |
| 腾讯 | `adjusted=True`（前复权） | `adjusted=False` |
| 新浪 | `adjusted=False`（**不复权**） | `adjusted=False` |
| 同花顺 | `adjusted=True` | ❌ 无分钟 K |

把新浪排到东财之前 → 除权除息前的历史价格与原来不同 → 均线/MACD/BOLL/量价结构整体位移，选股、AI 读数、模拟盘策略一起变，**界面上无任何提示**。

### 6. `board` — 东财 `:267`

- **新浪** `sina/provider.py:239` — 三条命中路径：行业板块汇总（`行业板块`/`行业`/`industry`）、`hs_a` 节点（沪深A/沪A/深A/创业板/科创板）、`hs_s` 节点（主要指数）；带 `sector` 时先经 `industry_menu` 映射 `new_xxxx` 再拉行业成分
- **腾讯 / 同花顺** ❌

⚠️ **概念板块是东财独占。** 新浪的 `else` 分支直接 `unsupported`。

### 7. `rank_list` — 东财 `:291`

- **新浪** `sina/provider.py:285` — `if key not in SINA_RANK_SORT: return unsupported(...)`
- **腾讯 / 同花顺** ❌

⚠️ 维度覆盖 3/7：

| RankBy | 东财 | 新浪 |
|---|:--:|:--:|
| `TURNOVER` 换手率 | ✅ | ✅ |
| `AMOUNT` 成交额 | ✅ | ✅ |
| `VOLUME` 成交量 | ✅ | ✅ |
| `MAIN_INFLOW` 主力流入 | ✅ | ❌ |
| `MAIN_OUTFLOW` 主力流出 | ✅ | ❌ |
| `ROE` 净资产收益率 | ✅ | ❌ |
| `PROFIT_YOY` 净利同比 | ✅ | ❌ |

新浪排前面时，**资金流排行和选股质量池（`candidate_pool.py:378` 用 `RankBy.ROE`）会整体降级**回东财，行为上无感但多一跳网络。

### 8. `hotmap` — 东财 `:325` ｜ **东财独占**

云图/大盘热力图。腾讯无板块概念、新浪 `newSinaHy` 只有行业汇总无 hotmap、同花顺未开放。**东财不可用即无图。**

### 9. `sector_menu` — 东财 `:331`

- **新浪** `sina/provider.py:302` — `if kind != "industry": return unsupported("新浪仅支持行业板块菜单")`

⚠️ `kind="concept"` 时新浪被拒，回落东财。

### 10–14. 东财独占五连

| 接口 | 东财位置 | 说明 |
|---|:--:|---|
| `breadth` | `:338` | 涨跌家数 |
| `market_turnover` | `:347` | 两市成交额 |
| `northbound` | `:353` | 北向资金 |
| `valuation_series` | `:371` | PE/PB/PS 序列（`stock_sina/` 消费） |
| `financial_snapshot` | `:387` | 财报快照（走 `eastmoney_finance.py`） |

这 5 个 + `hotmap` 共 6 个，**三个新源全部返回 `unsupported`，零兜底**。

---

## 5. `kline` 周期覆盖细表（12 个周期）

| KlinePeriod | 东财 | 腾讯 | 新浪 | 同花顺 |
|---|:--:|:--:|:--:|:--:|
| `M5` / `M15` / `M30` / `M60` | ✅ | ✅ | ✅ | ❌ |
| `D1` | ✅ | ✅ | ✅ | ✅ |
| `D1_RECENT` | ✅ | ✅ | ✅ | ✅ |
| `D1_YEAR` | ✅ | ✅ | ✅ | ✅ |
| `W1` 周 | ✅ | ✅ | ❌ | ❌ |
| `MON1` 月 | ✅ | ✅ | ❌ | ❌ |
| `Q1` 季 | ✅ | ❌ | ❌ | ❌ |
| `H1` 半年 | ✅ | ❌ | ❌ | ❌ |
| `Y1` 年 | ✅ | ❌ | ❌ | ❌ |

- 东财 `_PERIOD_DAYS`（`provider.py:44-57`）12 个周期全映射
- 腾讯 `_MINUTE_UNIT`（4 个分钟）+ `_DAILY_UNIT`（`day`/`week`/`month`）
- 新浪 `_PERIOD_SCALE`（7 个，**无周月季半年年**）
- 同花顺 `_DAILY_WINDOW_DAYS`（3 个日级，**仅日线**）

**季/半年/年 K 是东财独占。** 同花顺窗口上限：个股/指数 3650 天、场内基金 1825 天。

---

## 6. 路由与换源语义

### 三层结构

```
业务层
  ↓
CompositeMarketData        adapters/composite.py   ← 槽位分流：crypto / vix / 场外基金 / equity
  ↓ equity
ConfigurableEquityMarket   provider_registry.py     ← 优先级链：东财→腾讯→新浪→同花顺
  ↓
EastMoney / Tencent / Sina / THS   ← 各自 parse 层
```

### `_dispatch` 的四档处理（`provider_registry.py`）

```
for pid in 链:
    源内部抛异常          → 兜成 network 错误，继续往下（不炸链）
    成功                 → 立刻返回，stamp provider id
    not_found            → ⚠ 短路直接返回，不再试下一个
    unsupported          → 静默跳过（不算失败）
    network/解析/空数据   → 记为 first_real_error，顺延下一个
全部走完 → 返回 first_real_error（报「优先级最高且真正出错」那个源）
```

`not_found` 短路是对的：解析层四源共用东财 searchapi，东财说不存在，换源再问也是同一答案。

「这源不支持」与「标的不存在」是分开的 —— 新源遇到港股/期货等不覆盖的标的走 `unsupported`（顺延），`not_found` 只在共享解析层真返回 `None` 时才发。**不会误报「不存在该股票」。**

### 两个不走链的例外

| 位置 | 行为 |
|---|---|
| `ConfigurableEquityMarket.resolve` | 只走**链头**，不逐个试（解析层本就共用） |
| `stock_papertrade/quote_service.py::_fetch_one` | **绕过 `get_market()` 直连 `EASTMONEY_REQUESTER`**，模拟盘撮合价与涨跌停拦截锁死东财 |

第二个是好坏参半：弱源不会降级模拟盘价格，但东财真挂了模拟盘撮合也停，链式容错覆盖不到。

### 非 equity 槽位（固定路由，完全不在链内）

| 槽位 | 适配器 | 实现方法 |
|---|---|---|
| crypto | `OkxMarketData` | `resolve` `quote` `intraday` `kline` |
| vix | `VixMarketData` | `resolve` `quote` `intraday` |
| fund | `TiantianFundMarketData` | `resolve` `quote` `kline` |

这些不参与优先级链，`CompositeMarketData._slot_provider`（`composite.py:52`）直接盖章 provider id。

---

## 7. 结论：目前**只能全量切换**，无法按接口单独换源

配置里只有 4 个 `market_api_priority_<id>` 整数键（东财 40 / 腾讯 30 / 新浪 20 / 同花顺 10），**一套全局链，14 个接口全部共用**，没有任何 interface 维度的配置项。

### 因此以下诉求当前都无法实现

- 「K线走新浪加速，分时必须东财」→ 做不到，整体切过去分时也变了
- 「实时报价用腾讯兜底防东财抽风，但 K线锁死东财保前复权口径」→ 做不到，而这正是复权问题的正解
- 「北向/估值/财报永远东财」→ 只能靠「其他源 `unsupported` 被自动跳过」被动兜住，**不能显式锁**。将来谁给同花顺补上估值接口，会被静默接走

### 改造点很小：管道已铺 90%

`ConfigurableEquityMarket._dispatch(iface, method, ...)` 的**第一个参数就是接口名**，12 个调用点全部传了（`self._dispatch("kline", "kline", ...)`、`self._dispatch("northbound", "northbound")`……），只是 `_chain()` 生成全局链时**根本没用它**。

```python
# 现状
def _chain(self) -> list[tuple[str, MarketDataPort]]:
    for pid in build_priority_chain(self._reader): ...

# 改造后
def _chain(self, iface: str) -> list[tuple[str, MarketDataPort]]:
    override = parse_priority_chain(self._reader(f"{PREFIX_IFACE}{iface}", ""))
    pids = override or build_priority_chain(self._reader)   # 空覆盖 → 回落全局
    ...
```

只需**新增 1 个配置项**，不要做 14×4=56 个输入框（网页控制台会炸）。建议形态：

```
market_api_overrides = "kline:新浪→东财;intraday:东财→腾讯;northbound:东财"
```

空值 = 全走全局，行为与现在完全一致（向后兼容）。

`parse_priority_chain` 已在 `provider_registry.py` 里（当前被 `_LEGACY_PRIORITY_CONFIG_KEY` 迁移路径引用，而该路径的旧键 `market_api_priority` 从未进过 `config_default.py`、实际不可达）—— 正好可以把这套解析器从「永不执行的迁移遗留」复用成「单接口覆盖的解析入口」。

另一个更符合用户心智的方案是按**能力组**给 2–3 个覆盖点（行情类 / 板块类 / 财务类），配置项更少，也更容易想明白「我要锁的是哪一块」。
