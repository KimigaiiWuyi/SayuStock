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
| 8 | `hotmap` | ✅ | ❌ | ❌ | ❌ | **东财独占**（见 §9.1 为何不补） |
| 9 | `sector_menu` | ✅ | ❌ | ✅ | ❌ | 新浪 industry + concept（已补） |
| 10 | `breadth` | ✅ | ❌ | ✅ | ❌ | 新浪已补（全 A 5571 只，约 70 页） |
| 11 | `market_turnover` | ✅ | ❌ | ✅ | ❌ | 新浪已补（上证+深证 1 次请求） |
| 12 | `northbound` | ✅ | ❌ | ❌ | ❌ | **东财独占**（无独立源） |
| 13 | `valuation_series` | ✅ | ❌ | ❌ | ❌ | **东财独占** |
| 14 | `financial_snapshot` | ✅ | ❌ | ❌ | ❌ | **东财独占** |
| | **覆盖数** | **14/14** | **5/14** | **10/14** | **4/14** | 新浪本轮 +2 |

> ⚠️ 注意最后一行：**14 个接口里仍有 4 个（`hotmap`/`northbound`/`valuation_series`/`financial_snapshot`）是东财独占、没有替代源**。东财侧不可用时这 4 个直接报错，不会「换个源接着出图」——注意这不是配置能改变的：源链无法禁用东财（见 §10.4），链尾兜底始终会把它排在链上。

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

> **⚠️ 本节是改造前（2026-10-08 早）的现状记录，已被 §10 的方案 3 取代并就绪落地。**
> 保留作为问题推导过程：「管道已铺 90%」的结论仍然成立（`_dispatch` 第一个参数就是接口名）。
> 但本节末尾提到的 `_LEGACY_PRIORITY_CONFIG_KEY` 迁移遗留、`market_api_overrides` 单串
> 提案**都不在最终实现里** —— 旧 int 配置从未上线、无迁移需求；最终形态是 §10 的
> 五域 `GsListStrConfig` 列表链 + 链外源排链尾兜底。

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
market_api_overrides = "quote:腾讯→东财;intraday:东财→腾讯;northbound:东财"
```

> ⚠️ 上面只是**示例语法**，该方案未落地（最终落地的是 §10 的 6 个 `GsListStrConfig`
> 域链）。这个例子的 K 线部分原本写成 `kline:新浪→东财`，**不要照抄那种顺序**：
> 新浪是不复权口径，排在 K 线链头会静默换掉复权口径（§6 的 B 组，2026-10-08 已改掉
> 这个示例以免误读）。实际生效的默认链是 **东财 → 腾讯 → 新浪 → 同花顺**
> —— 用运行时配置实跑 `build_priority_chain` 核对过，新浪在第三位。

空值 = 全走全局，行为与现在完全一致（向后兼容）。

`parse_priority_chain` 已在 `provider_registry.py` 里（当前被 `_LEGACY_PRIORITY_CONFIG_KEY` 迁移路径引用，而该路径的旧键 `market_api_priority` 从未进过 `config_default.py`、实际不可达）—— 正好可以把这套解析器从「永不执行的迁移遗留」复用成「单接口覆盖的解析入口」。（**最终实现**：迁移路径整体删除，`parse_priority_chain` 改为解析字符串列表。）

另一个更符合用户心智的方案是按**能力组**给 2–3 个覆盖点（行情类 / 板块类 / 财务类），配置项更少，也更容易想明白「我要锁的是哪一块」。

---

## 8. 线上接口核查（2026-10-08 实测）

> 本节为针对 §3 矩阵的**外部核查**：哪些「东财独占」其实有等价源、哪些确实补不上。所有结论都带实测或可溯源证据，不靠文档描述推断。

### 8.1 意外发现：腾讯盘口列索引 —— **PR 是对的，网上流传的那篇博客是错的**

各源对 `qt.gtimg.cn` 的 `~` 分隔字段表说法冲突。实测裁决（`sh600000`，2026-10-08，共 88 字段）：

| 下标 | 实测值 | 正确含义 |
|---|---|---|
| 38 | 0.44 | 换手率 % |
| 39 | 6.16 | 市盈率 TTM |
| 40 | *(空)* | — |
| 41 | 9.49 | 最高（冗余，同 33） |
| 42 | 9.16 | 最低（冗余，同 34） |
| 43 | 3.59 | 振幅 % |
| 44 | 3157.39 | 流通市值（亿） |
| 45 | 3157.39 | 总市值（亿） |
| 46 | 0.42 | **市净率 PB** |
| 47 | 10.10 | **涨停价** |
| 48 | 8.26 | **跌停价** |

数值交叉校验（三条独立约束全部闭合）：

- 昨收 `9.18 × 1.1 = 10.098` → **[47] = 10.10** ✅
- 昨收 `9.18 × 0.9 = 8.262` → **[48] = 8.26** ✅
- 振幅 `(9.49 − 9.16) / 9.18 = 3.59%` → **[43] = 3.59** ✅
- 浦发银行为破净银行股 → **PB ≈ 0.42** 合理 ✅

**结论**：`tencent/parse.py::parse_qt_line` 使用的 `44/45/46/47/48` **完全正确**。

需要注意的是，网上一篇标称「结合 2026-06-10 实测数据」的博客（cnblogs.com/soarowl）把 47/48/49 标成「量比 / 市净率 / 每股净资产」，**该文表格标错了** —— 它贴出的原始字符串实际是 `46=0.42, 47=10.31, 48=8.43`，与经典表一致，是它自己的对照表错了位。

> 这条要记下来：腾讯字段表在网上至少有三套互相矛盾的版本（经典表 / 该博客 / 另一套把 43 当 PB 的）。**唯一可靠的判据是拉真实数据 + 用昨收算涨跌停、用最高最低算振幅做数值校验**，不能信任何一篇文章的表格。

### 8.2 新浪概念/申万行业板块 —— **实测可用，PR 判「不支持」是漏了**

PR 的 `SinaMarketData.board` 只认 `行业板块 / 行业 / industry` 和 `hs_a / hs_s` 节点，对概念板块走 `else: unsupported`。但新浪**有**独立的概念与行业板块汇总端点，实测（2026-10-08）：

```python
# 概念板块 —— 22602 字节，正常返回
http://money.finance.sina.com.cn/q/view/newFLJK.php?param=class
var S_Finance_bankuai_class = {"gn_hwqc":"gn_hwqc,华为汽车,97,23.956875,
  -0.28739583333333,-1.1854175170251,2069201242,31890354954,
  sz002454,10.000,5.610,0.510,松芝股份", ...}

# 申万行业 —— 11253 字节，正常返回
http://money.finance.sina.com.cn/q/view/newFLJK.php?param=industry
var S_Finance_bankuai_industry = {"hangye_ZA01":"hangye_ZA01,农业,16,9.828125,
  0.34875,3.6790400210984,772991490,5723859279,sh601118,10.017,6.370,0.580,海南橡胶", ...}

# 地域板块
http://money.finance.sina.com.cn/q/view/newFLJK.php?param=area
```

行内字段：**代码, 名称, 家数, 均价, 涨跌额, 涨跌幅, 总成交量(手), 总成交额(元), 领涨股代码, 领涨股涨跌幅, 领涨股现价, 领涨股涨跌额, 领涨股名称**。

注意行业代码是**申万三级**（`hangye_ZA01` / `hangye_ZL01` …），与插件现有 `newSinaHy` 的行业口径不同，换源时要注意板块 ID 映射。

配套还有新浪已有的 `Market_Center.getHQNodeData`（分页取成分股，PR 已在用）→ 概念板块**成分股**也能补齐。

新浪还有资金流排行（可补 `rank_list` 的资金流维度）：

```
# 行业级资金流排行
vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_bk
    ?page=1&num=20&sort=&asc=0&fenlei={0|1|2}      # 0=申万 1=概念 2=证监会
# 个股级资金流排行
.../json_v2.php/MoneyFlow.ssl_bkzj_ssggzj
```

### 8.3 同花顺扶摇 —— **主力资金明确未开放**

扶摇官方文档（fuyao.aicubes.cn/docs/api-reference/capital-flow）原文：

> 该能力**暂未开放外部接入**，相关数据能力已经接入同花顺AI客户端。

这印证了 PR 文档「分时/分钟 K 的高频动向接口未开放外部接入」的判断。**同花顺短期内补不出资金流/分时。**

可用的只有 `/api/a-share/prices/snapshot` 和 `/api/a-share/prices/historical`（即 PR 已实现的部分）。

### 8.4 北向资金 —— **没有独立源**

扫描下来，akshare 的 `stock_hsgt_flow_em` / `stock_hsgt_hist_em` / `stock_hsgt_hold_stock_em` 底层**全部是东财封装**。多个技术栈文档都明确标注这一点。

叠加 2024 年 8 月起交易所已取消北向资金实时披露、只保留收盘后总额的大环境，`northbound` 属于**真·无兜底**。

### 8.5 业界佐证：breadth / turnover 是可冗余的

某生产级行情服务（finscope）的数据源路由表（实操验证结论）：

| 数据 | 其路由 |
|---|---|
| A 股市场宽度（涨/跌/平家数、涨跌停家数、成交额） | 东方财富全 A → **新浪全 A** → 同业务日快照 |
| 实时行情 | 腾讯 → 新浪 → 东财 |
| 历史日 K（前复权） | 扶摇同花顺 API |
| 资金流（分钟/日级主力） | AkShare(日级) → 东财(分钟+日级) |
| 个股资料 | AkShare → 东财 |

**与本插件的判断一致**：盘口/K线/宽度可以多源冗余，**资金流/估值/财务是真·东财域**。

### 8.6 别家 breadth / 概览接口实测（2026-10-08，均纯 HTTP、无需浏览器）

| 源 | 接口 | 内容 | 实测 |
|---|---|---|---|
| 腾讯 | `proxy.finance.qq.com/cgi/cgi-bin/market/hs/index`（无参） | 两市成交额 + 涨/跌/平/涨跌停/停牌家数 + 11 段粗分档 + 涨跌比分钟线 | ✅ 一次请求；成交额 14379.89 亿与东财/新浪完全一致 |
| 同花顺·官方 | `dq.10jqka.com.cn/fuyao/up_down_distribution/distribution/v2/realtime` | up/down/flat/suspend + limit_up/limit_down + 11 段 table | ✅ 无鉴权；56/12/170 与东财完全一致 |
| 同花顺·网页 | `q.10jqka.com.cn/api.php?t=indexflash&type=all` | 10 段分布 + 涨跌停分时 + 大盘评级 | ❌ WAF 403：连真浏览器页面自身 XHR 都被拒（IP 级封锁，纯 HTTP 不可能） |
| 东财·轻量 | `push2.eastmoney.com/api/qt/ulist.np/get`（指数 secid，`fields=f104,f105,f106,f6`） | 涨/跌/平家数 + 两市成交额（单请求计数，非分布） | ✅ 他人项目生产在用，未集成 |

- 腾讯 `flat=181` 含停牌 11（官方口径为「平 170 + 停牌单列」）；`amount_change` = 当日 − 上一交易日，
  可反推昨日成交额（14379.89 − 287.92 = 14091.98 亿）。
- 腾讯/同花顺的分档都是 ±2/3/5/7/10 粗档，**填不满**本插件 13 档（0~1 / 1~2 / 2~3 无法拆分）→
  适合做「计数层兜底 + 交叉校验」，不适合直接作为 13 档分布源。
- 因此 breadth 的完整兜底链应表述为：**东财 13 档 → 新浪自算 13 档 → 腾讯/同花顺单请求计数**。

---

## 9. 可补充清单

按「补的性价比」排序。

| 接口 | 现状 | 能否补 | 怎么补 | 成本 |
|---|:--:|:--:|---|:--:|
| `sector_menu` (concept) | 新浪拒 | ✅ **能** | `newFLJK.php?param=class` | 极低 |
| `board` 概念板块 | 东财独占 | ✅ **能** | `param=class` 拿板块汇总 + `getHQNodeData` 拿成分 | 低 |
| `board` 申万三级 | 新浪仅 `newSinaHy` | ⚠️ 部分 | `param=industry` 口径更标准，但板块 ID 与现有不同，需映射 | 中 |
| `rank_list` 资金流维度 | 新浪仅 3/7 | ✅ **能** | `MoneyFlow.ssl_bkzj_ssggzj` / `_bk` | 中 |
| `breadth` 涨跌家数 | 东财独占 | ✅ **能** | `getHQNodeData` 全 A 分页 → 自行统计涨跌平 | 中 |
| `market_turnover` 成交额 | 东财独占 | ✅ **能** | 同上，成交额求和 | 中 |
| `hotmap` 云图 | 东财独占 | ⚠️ 部分 | 概念+行业板块汇总能拼出雏形，但缺东财的热力分档口径 | 中高 |
| `rank_list` ROE / 净利同比 | 东财独占 | ❌ 难 | 新浪无财务排行；需自建 F10 拉取+计算 | 高 |
| `valuation_series` | 东财独占 | ❌ 难 | 需逐日 PE/PB 历史序列，腾讯/新浪盘口只有**当前值** | 高 |
| `financial_snapshot` | 东财独占 | ❌ 难 | 腾讯 F10 / akshare 有原始财报，但字段口径与东财 `eastmoney_finance` 差异大，对齐成本高 | 高 |
| `northbound` | 东财独占 | ❌ **不能** | 无独立源，交易所已取消实时披露 | — |

**如果只做一件事**：补 `breadth` + `market_turnover` 的新浪兜底。这两个是大盘概览的高频入口，也是「东财挂了首页就空白」的直接原因，而新浪全 A 数据拿得到、成本可控。

**不建议碰的**：`valuation_series` / `financial_snapshot` —— 口径对齐的风险大于收益，不如保留东财独占并在文档里写清楚。

**更新（见 §8.6）**：`breadth` / `market_turnover` 除「新浪全 A 翻页自算」外，还有**单请求路径**：
腾讯 `market/hs/index`（成交额 + 计数一把可取）、同花顺官方 `fuyao/up_down_distribution`（无鉴权）。
两者分档粗，只适宜计数层兜底；13 档分布仍以 东财 / 新浪自算 为准。

---

## 10. 按能力域区分优先级的配置系统（方案 3，已落地）

> 本节是 §7 诊断的**解法与最终形态**。最终语义与最初草案不同：**源链只表达优先级，
> 不是禁用表达** —— 链外源自动排链尾兜底，以兑现「任何配置组合下 14 个接口都有源可用」
> 这条硬约束（东财独占接口不能被配置饿死）。实网矩阵见 §10.6。

### 10.1 核心判断：不按 14 个接口逐个配，按「有没有第二源」分 5 个域

14 接口 × 4 源 = **56 个输入框**，网页控制台没法用，且用户根本记不住「哪个是 `market_turnover`」。

真正需要区分的是**语义**不同源的地方，而不是接口名。按覆盖矩阵，14 个接口天然分成 **5 个域**：

| 域 | 覆盖接口 | 真正可选的源 | 分开的理由 |
|---|---|---|---|
| **A. 盘口/分时** | `quote` `quotes` `intraday` | 东财/腾讯/新浪/同花顺 | 高频低延迟，腾讯/新浪最快；`ndays>1`（五日分时）只有东财实现，同花顺连当日分时都没有 |
| **B. K 线** | `kline` | 东财/腾讯/新浪/同花顺 | **复权口径敏感** —— 新浪不复权，排到前面会静默毁掉历史指标 |
| **C. 板块/排行/菜单** | `board` `rank_list` `sector_menu` | 东财/新浪 | 腾讯同花顺没有板块能力；新浪排行只有 3 个维度 |
| **D. 大盘统计/资金** | `breadth` `market_turnover` | 东财/新浪 | 只有这两家能算全 A 涨跌分布与两市成交额 |
| **E. 东财独占** | `hotmap` `northbound` `valuation_series` `financial_snapshot` | 仅东财 | 另三个源全部返回 `unsupported`（逐源实测见 §12）；单列成键是为了让后台点开就看见「只有东财」 |

`resolve` 不配 —— 解析层各源共用，恒走全局链链头（`_IFACE_GROUPS` 里没有它）。

> 分域判据是「**该接口还有没有第二个源**」。只有一个源的接口单独并进 E 组：
> 把 `hotmap` 留在 C 组会让用户以为「把新浪排前面就能让云图走新浪」，那是必然失败的尝试。

### 10.2 配置形态：`GsListStrConfig`，6 个键覆盖 14 接口

每个域键的 `options` 只列**该域真正实现了接口的源**（依据是 §12 的逐源实测），
所以「某组只有一个可选值」本身就是「这组接口是它独占的」的说明：

| 配置键 | 标题 | `options` | 默认值 |
|---|---|---|---|
| `market_api_chain` | 全局行情源链（默认） | 东方财富 / 腾讯财经 / 新浪财经 / 同花顺 | `["东方财富", "腾讯财经", "新浪财经"]` |
| `market_api_chain_quote` | ① 盘口 / 分时源链 | 东方财富 / 腾讯财经 / 新浪财经 / 同花顺 | `["东方财富", "腾讯财经", "新浪财经"]` |
| `market_api_chain_kline` | ② K线源链 | 东方财富 / 腾讯财经 / 新浪财经 / 同花顺 | `["东方财富", "腾讯财经", "新浪财经"]` |
| `market_api_chain_board` | ③ 板块 / 排行 / 菜单源链 | 东方财富 / 新浪财经 | `["东方财富", "新浪财经"]` |
| `market_api_chain_market` | ④ 大盘统计 / 资金源链 | 东方财富 / 新浪财经 | `["东方财富", "新浪财经"]` |
| `market_api_chain_exclusive` | ⑤ 东财独占：云图 / 北向 / 估值 / 财报 | 东方财富 | `["东方财富"]` |

六个键的 `data` **都预填推荐链**（2026-10-08 起，见 §16）：控制台一打开就能看见推荐顺序，
不必先猜「留空会发生什么」。清空任一键仍然合法、回落语义见 §10.4 —— 之所以能放心预填，
是因为「填」与「留空」在**每域真正实现了接口的源**上等价（同花顺由链尾兜底自动补入，不用手写）。

> ⚠️ ② K 线**不要把新浪排到链头**：新浪是不复权口径，排前面会静默换掉复权口径
> （§6 的 B 组）。默认值就是 「东财 → 腾讯 → 新浪（→ 同花顺兜底）」，
> 新浪已在第三位，只有东财、腾讯都拿不到时才会落到它 —— 保持默认即可。
> ③④ 两组只有 东财/新浪 两个可选值，默认值同样把新浪排在后。

```python
"market_api_chain_exclusive": GsListStrConfig(
    "⑤ 东财独占：云图 / 北向 / 估值 / 财报",
    "大盘云图、北向资金、估值序列、财报快照这四类**只有东方财富提供**："
    "其他三个源会返回「不支持」并自动兜底到东方财富，所以这一组填别的也不会生效。"
    "单独列出来是为了让你一眼看出「其他家没有」，不必在这组上试错。",
    ["东方财富"],
    options=["东方财富"],
),
```

**为什么是列表不是字符串？** 链本质就是有序列表：`GsListStrConfig` 在网页控制台给出
「从选单挑源 + 排序」的原生控件，用户拖顺序即可。字符串形态要把 `→` `>` `，` `,` 及空白
当解析规则，等于把序列化细节推给用户（首版字符串草案在评审中被否）。

### 10.3 为什么不继续用旧 int

| | 旧 int（40/30/20/10） | 落地形态（有序列表） |
|---|---|---|
| 用户心智 | 要理解「数字×10 + 内禀序号」的平局裁决 | 列表顺序即优先级 |
| 加减源 | 要改数字，还要猜会不会触发平局 | 增删一个名字 |
| 平局歧义 | 内禀序号是实现细节泄漏到配置 | 不存在 |
| 控制台 UI | 4 个数字输入框 | 原生列表控件（带 options 选单） |

> 旧 4 个 int 键**从未上线**（没有进过任何发布版 `config_default.py`），因此**不做迁移**：
> 删除旧定义、换新键即可。运行时 `config.json` 里可能残留旧键（Core 对孤儿键保留），
> 新代码不读它们、无影响。

### 10.4 语义规则：源链只表达优先级，不是禁用表达

1. **三级回落**：域链（`market_api_chain_{quote,kline,board,market,exclusive}`）→ 全局链
   （`market_api_chain`）→ 内置默认链（东财→腾讯→新浪→同花顺）。域链留空用全局链；
   全局链也留空用内置默认链。六个键的默认值现在**都预填推荐链**（§16），
   回落只在用户手动清空之后才会走到。
2. **链外源自动排链尾兜底**：链走完后，`_SYSTEM_ORDER`（东财→腾讯→新浪→同花顺）里没出现
   在链上的源**自动追加到链尾**。因此任何配置（空列表 / 单源 / 乱写）下四个源都在链上 ——
   把全局链配成「只留腾讯」，东财只是排到最后，而不是消失。
3. 配置里写不认识的源名 → `logger.warning` 后忽略该项，其余项照常生效。
4. 别名：`东财`/`东方财富`/`eastmoney` 等价（`_PROVIDER_ALIASES`），列表项写中文名或裸 id 均可。
5. **没有「禁用」开关**：要少用某源就把别的源排前面；实际上无法让某源完全不被调用 ——
   这正是为了兑现「任意配置下 14 个接口都可用」这条硬约束。

### 10.5 实现

`_dispatch(iface, ...)` 的第一个参数本来就是接口名（§7「管道已铺 90%」），改造只动三处：

```python
# provider_registry.py
_IFACE_GROUPS: dict[str, str] = {          # 14 个接口 → 5 个域
    "quote": "quote", "quotes": "quote", "intraday": "quote",
    "kline": "kline",
    "board": "board", "rank_list": "board", "sector_menu": "board",
    "breadth": "market", "market_turnover": "market",
    "hotmap": "exclusive", "northbound": "exclusive",
    "valuation_series": "exclusive", "financial_snapshot": "exclusive",
}

def build_priority_chain(reader, group=None) -> list[str]:
    """域链 → 全局链 → 内置默认链；链外源按 _SYSTEM_ORDER 排链尾。"""

def _chain(self, group: str | None = None) -> list[tuple[str, MarketDataPort]]: ...

# _dispatch：chain = self._chain(_IFACE_GROUPS.get(iface))
```

业务层与 adapter **一行未改**；`config_default.py` 的 4 个 int 换成 5 个 `GsListStrConfig`；
`facade.py` 里旧的迁移调用一并删除。回归测试重写为 `test/market/test_provider_switch.py`。

### 10.6 实网配置矩阵验证（2026-10-08，8 档 × 16 调用 / 14 接口）

对每一种配置注入真读函数（`real` 档真读用户 `config.json`），连真实网络逐一调用 16 个入口
（覆盖 14 个接口，含 `quotes` 批量与 `ndays=5` 分时）：

| 调用 | real | empty | tx | sina | ths | bad | weak4 | mix |
|---|---|---|---|---|---|---|---|---|
| `resolve` | ok | ok | ok | ok | ok | ok | ok | ok |
| `quote` | tencent | eastmoney | tencent | sina | ths | eastmoney | sina | ths |
| `quotes` | tencent | eastmoney | tencent | sina | ths | eastmoney | sina | sina |
| `intraday(1d)` | tencent | tencent | tencent | sina | tencent | tencent | sina | sina |
| `intraday(5d)` | ⛔ 单源 | ⛔ 单源 | ⛔ 单源 | ⛔ 单源 | ⛔ 单源 | ⛔ 单源 | ⛔ 单源 | ⛔ 单源 |
| `kline(D1)` | eastmoney | eastmoney | tencent | sina | ths | eastmoney | ths | tencent |
| `kline(Y1)` | eastmoney | eastmoney | eastmoney | eastmoney | eastmoney | eastmoney | eastmoney | eastmoney |
| `board` | sina | sina | sina | sina | sina | sina | sina | sina |
| `rank_list` | sina | sina | sina | sina | sina | sina | sina | sina |
| `hotmap` | eastmoney | eastmoney | eastmoney | eastmoney | eastmoney | eastmoney | eastmoney | eastmoney |
| `sector_menu` | ok | ok | ok | ok | ok | ok | ok | ok |
| `breadth` | ok* | ok | ok | ok | ok | ok* | ok* | ok |
| `market_turnover` | ok* | ok | ok | ok | ok | ok* | ok | ok |
| `northbound` | ⛔ 单源 | ⛔ 单源 | ⛔ 单源 | ⛔ 单源 | ⛔ 单源 | ⛔ 单源 | ⛔ 单源 | ⛔ 单源 |
| `valuation_series` | ok* | eastmoney | eastmoney | eastmoney | eastmoney | eastmoney | eastmoney | eastmoney |
| `financial_snapshot` | ok | ok | ok | ok | ok | ok | ok | ok |

读表要点：

- **配置确实生效**：`tx` 档 `kline(D1)=tencent`、`sina` 档 `=sina`、`ths` 档 `=ths` —— 链头就是配的那个源。
- **链尾兜底确实生效**：`hotmap` / `kline(Y1)` / `financial_snapshot` 在**每一档**都由 eastmoney 服务 ——
  这些单源接口在 `tx`/`sina`/`ths` 档都没被配置饿死，而是顺延到链尾的东财。
- **`bad` 档（配置写成「乱写的源」）等价于默认链**：非法项告警忽略后回落内置默认链，
  所有接口照常可用（与 `empty` 档结果一致）。
- **`board` / `rank_list` 全档 = sina**：当时东财 clist 正在限流（下同），链顺延到新浪命中；
  这恰好演示了「东财抽风时板块/排行仍有数据」。
- `ok*` = 当日首跑该格曾出现瞬时错误（东财限流高峰），45s 间隔复跑后全部 ok；
  属环境瞬态、与配置无关，判读依据见 §12。
- 表中 provider 为实测值；另有几格在**两轮实测之间互换**过瞬时状态而不影响结论：
  `valuation_series` 在 `real` 档（首跑 `not_found` → 复跑 ok）与 `bad` 档（首跑 eastmoney
  → 复跑 `not_found`）各出现一次瞬态。该接口先跑 `resolve`（东财代码表、真发网络请求），
  抖动时返回 None 即判 `not_found` 并**短路整链**（既有设计，见 §12.4 末条）。两档的有效链
  完全一致（`bad` 的乱写项被忽略后回落默认链），且该接口直连 eastmoney adapter 实测 OK
  （§12.2）→ 属环境瞬态，不是配置问题。**该瞬态已顺带修复**（§12.4 末条）：现改为报
  `network` 顺延其他源，不再短路。
- `⛔ 单源 = intraday(5d) / northbound`：只有东财实现，其余三源明确 `unsupported`（§12 有逐源实测），
  因此**任何配置**下都只能落到东财；当时东财侧不可用（trends2 被限流 / 北向上游停发），
  不是配置缺陷。八档失败集合完全一致，这本身就是「链已经把能试的源都试过了」的证据。

---

## 11. 本轮落地结果（分支 `feat/sina-coverage`）

基线：`main`（3b41f2c）+ 合入 PR #19（c6e0c90）。改动集中在 `adapters/sina/`、`models/stats.py`、以及下文 §11.4 的四条消费方链路；新增 `test/market/test_sina_coverage.py`、`test/market/test_eastmoney_breadth.py`。

### 11.1 已补齐的接口（4 个，全部实网验证通过）

| 接口 | 实现 | 实测结果（2026-10-08） |
|---|---|---|
| `sector_menu("concept")` | `newFLJK.php?param=class` | **175 个概念板块**，样例 华为汽车/BC电池/华为海思 |
| `board("概念板块")` | 同上，`BoardKind.CONCEPT` | **175 行**，CXO概念 +4.65%、创新药 +3.62%（领涨康希诺 +20.005%） |
| `board(sector="gn_xxx")` | `getHQNodeData?node=gn_xxx` | 成分可取，华为汽车 → 松芝股份/东风科技/江淮汽车 |
| `rank_list(MAIN_INFLOW)` | `MoneyFlow.ssl_bkzj_ssggzj` | 净额降序，江淮汽车 +5.492% 净流入 11.96 亿 |
| `rank_list(MAIN_OUTFLOW)` | 同上，升序 | 净额升序，N力勤 +206.594% 净流出 21.31 亿 |
| `market_turnover` | 上证+深证指数盘口，**1 次请求** | **14379.89 亿**，交易日 2026-09-30 16:19 |
| `breadth` | 全 A 分页并发（`_BREADTH_CONCURRENCY=6`） | **6.4s**，涨停63 涨2503 平181 跌2803 跌停21 |

**关键校验**：`breadth` 五项合计 = **5571**，与 `Market_Center.getHQNodeStockCount?node=hs_a` 返回的 `"5571"` **精确一致** → 70 页翻页无遗漏、无重复。

`market_turnover` 的 14379.89 亿 = 手工探测的沪 6793.99 亿 + 深 7585.90 亿，两条独立路径吻合。

### 11.2 两个必须知道的模型适配

1. **`MarketTurnover.prev_amount: float` → `float | None`**
   新浪指数盘口只有当日成交额；指数日 K 也**不含 amount 字段**（只有成交量），实测确认。
   原字段全仓库**零消费方**（只有东财产出），放宽无兼容风险。
   渲染层如需「较昨缩量」判断，**必须判空**，不能拿 `None` 参与算术。

2. **资金流排行 `changeratio` 是小数比例，必须 ×100**
   该接口返回 `-0.0000852594` 表示 **-0.0085%**，与行情中心 `changepercent` 的百分数口径不同。
   `parse_money_flow_rank` 已统一 ×100 对齐内部模型。实测校准：江淮 +5.492%、兆易 -3.719%、N力勤 +206.594%。

### 11.3 `hotmap` 为什么没补（不是漏了，是补了会坏）

`build_cloudmap_render_data`（`utils/render_data.py:920`）第一行就是：

```python
if row.market_cap is None or row.change_pct is None or not row.name:
    continue          # market_cap 为 None 直接丢弃
```

云图是**个股级 treemap**（`value=market_cap`，`category=row.industry`），不是板块云图。两条路都走不通：

- **用板块汇总替代**：`newFLJK` 板块行只有家数/均价/涨跌幅/成交额/领涨股，**没有 market_cap** → 所有行被上面这行过滤 → 出一张空白图。
- **用全 A 个股替代**：`getHQNodeData` 有 `mktcap`，但**没有 industry 字段** → `row.industry` 全为 None → `category` 全塌成 `"-"` → treemap 退化成单一大类，等于没有分类。

补 `hotmap` 需要额外拉「股票 → 申万三级行业」映射表来补 industry，工作量与收益不成比例。**建议保持东财独占并在 UI 明确提示**，而不是塞一个语义不符的实现。

### 11.4 ⚠️ 最重要的发现：四处生产代码绕过优先级链（已全部改造）

补适配器只是把能力做进端口。但扫描消费方时发现，**真正在用这些数据的生产代码有四条链路直连东财、完全不经过 `get_market()`**：

| 位置 | 绕过的内容 |
|---|---|
| `stock_ai_func/ai_tools.py:87` | `get_bar()` 直连东财取涨跌家数 —— **不改造的话本轮补的 `breadth` 对该工具零效果** |
| `stock_info/draw_info.py:160,373` | `get_bar()` + `get_hours_from_em()` —— 大盘概览图在东财挂掉时仍出不来 |
| `stock_papertrade/quote_service.py::_fetch_one` | `EASTMONEY_REQUESTER` 直连 —— 模拟盘撮合价与涨跌停拦截锁死东财 |
| `stock_analysis/universe.py::fetch_clist` / `resolve_industry_fs` / `resolve_concept_fs` | `EASTMONEY_REQUESTER.stock_request` / `get_menu` —— 选股池数据源仍单一 |

也就是说：**即便四个源全部接通，这四条链路仍然只认东财。** 链式容错只在「已经走 `get_market()` 的」地方生效。

东财限流时四条链路的实测对照（2026-10-08，连续探测触发 `-400016` 期间）：

```
旧 clist 全A 3页: n=0  err=-400016        ← universe.py 改造前：直接空池
端口 board(A_SHARE, limit=100): n=100     ← 同一时刻，走链顺延到新浪，有数据
```

改造后：

| 位置 | 改造后 | 行为差异 |
|---|---|---|
| `ai_tools.py:87` | `market.breadth()` + `breadth_counts()` | LLM 看的分档从东财 10 档原始分布 → 统一 13 档语义分桶（`BREADTH_BANDS`） |
| `draw_info.py:160` | `market.breadth()` | 同上，口径与 AI 工具一致 |
| `draw_info.py:373` | `market.turnover()` | `prev_amount is None` 时不再谎报放量/缩量（新浪指数盘口没有昨额） |
| `quote_service._fetch_one` | `get_market().quote(secid)` | 东财限流时按链顺延腾讯/新浪；**拿不到价仍返回全 None**，撮合层据此拒单 |
| `universe.py::resolve_industry_fs` / `resolve_concept_fs` | `market.sector_menu()` + `match_sector_menu` | 概念菜单 175（新浪）→ 504（东财）；匹配落到申万一级更准（「医药」→ 医药生物 BK1216） |
| `universe.py::fetch_board_members` | `market.board(code, limit=None)` | 成分股不再被 10 页截断；与 `sector_resolve.fetch_named_board` 同一形状 |

**仍然保留一处东财直连**：`universe.py::fetch_a_share_universe`，理由见 §11.5 —— 那是端口的能力缺口，不是绕过。

改造 `fetch_board_members` 时踩到的两个边界，都实测过：

- **板块代码空间是各源私有的。** 传东财菜单给的 `BK1036` 给新浪 → `unsupported 新浪不支持列表 BK1036`；
  换成传中文名 `board(sector="半导体")` 也一样（新浪的 `newSinaHy` 只有 49 个一级行业，命名体系不同，`_sector_node` 认不出）。
  所以**东财限流期间行业/概念选股仍会整体失败**——`fetch_board_members` 走端口拿到的是「单一入口 + 统一形状」，
  **不是**「东财挂了也能出成分」这个能力。别把这两件事混为一谈。
- 代码与 `sector_menu` 同源这点仍然有价值：链切到新浪时，菜单就会给出 `gn_xxx` / `hangye_xxx`，成分跟着走新浪，配对不会错位。

### 11.5 `fetch_a_share_universe` 为什么保留直连（能力缺口，不是绕过）

这个函数要的是「沪深A **按总市值降序**的前 ~2000 只」。端口 `board()` 的两条走法都不成立（2026-10-08 实测）：

| 走法 | 实测 | 问题 |
|---|---|---|
| `board(BoardKind.A_SHARE, limit=2000)` | **只回 100 行** | clist 的 `pz` 上限就是 100，`limit` 填多大都被静默截断 |
| `board(BoardKind.A_SHARE, limit=None)` | 1600 行 / **49.4s**（首次未命中缓存） | `is_loop` 全市场翻 50+ 页、每批 `sleep(0.4~0.9)`；限流时中途断流，拿到的还不完整 |

而且 `board()` 走 `get_market_list` 的排序字段固定是 `f3`（涨跌幅），**不是 `f20`（总市值）**——旧路径特意用市值排序，避免选股池「严重偏涨」。机器人命令等不起 49 秒。

结论：这里保留 `fs` 表达式直连，函数 docstring 里写死了原因。**这是 `MarketDataPort` 的能力缺口**——端口目前没有「筛选表达式 + 指定排序字段 + 可控翻页上限」的列表能力；要补得新开一个接口（并给新浪/腾讯实现），超出本 PR 范围。

### 11.6 质量门状态

> **本节数字是 `1b1cb86` 时点的记录；分支最新状态见 §12.1**（真 pytest 已可全量开跑，
> `606 passed / 4 skipped`）。

| 门 | 结果 |
|---|---|
| `ruff check SayuStock test` | ✅ All checks passed |
| `ruff format --check` | ✅ 251 files already formatted |
| `basedpyright`（adapters / models / stock_analysis / 新测试） | ✅ 改动文件 **0 errors** |
| 单测（runner 脱离 pytest） | ✅ **42 passed / 0 failed** |
| 实网回归（重构后重跑） | ✅ 全部接口仍通 |

> ⚠️ 当时**本地 `pytest test` 跑不起来**：本机 `F:\gsuid_core\.venv` 缺 `pandas`，41 个模块 collection error
> （`git stash` 验证改动前同样 41 个 error，属环境问题而非本次回归）。
> 后续给本机 venv 补齐了插件声明依赖，该限制已解除 —— 见 §12.1。

### 11.7 命令级覆盖：5 条常用命令各源能不能单独撑起来

**接口覆盖 ≠ 命令可用**：一条命令往往同时要 `board` + `breadth` + `market_turnover` + `intraday`。
逐源实测（2026-10-08，标的 `1.600519`；东财此时处于 `-400016` 限流）：

| 命令 | 用到的端口调用 | 东财 | 腾讯 | 新浪 | 同花顺 |
|---|---|:--:|:--:|:--:|:--:|
| `大盘概览` | `board(主要指数/行业板块/概念板块)` + `quote(118.AU9999)` + `quote(220.TLM)` + `breadth` + `market_turnover` | ⚠️ 限流中部分项失败 | ❌ | ✅ 含黄金9999 / 三十债主连盘口 | ❌ |
| `我的自选` | `board(主要指数)` + `intraday` × N | ✅ | ❌ 无 board | ✅ | ❌ 无 intraday |
| `我的个股` | `intraday` × 5 | ✅ | ✅ | ✅ | ❌ |
| `个股xx` | `intraday` | ✅ | ✅ | ✅ | ❌ |
| `个股 五日xx` | `intraday(ndays=5)` | ✅ | ❌ | ❌ | ❌ |
| `个股 日k xx` | `kline(101)` | ✅ | ✅ | ✅ | ✅ |

**只有 `个股 日k xx` 是四源全通。** 几个必须知道的边界：

- **同花顺没有 `intraday`**（4/14 覆盖里就不含），所以 `我的自选`/`我的个股`/`个股xx` 在「只配同花顺」的链里直接不可用。
- **黄金 `118.AU9999` 与三十债 `220.TLM`（2026-10-09 复核）**：腾讯 qt/检索没有这两只；同花顺公开行情 URL 没有对应行。新浪有盘口：黄金是上金所沪金99 `gds_AU9999`（不是沪金期货 `nf_AU0`），三十债主连是 `nf_TL0`。两只都只有盘口，分时/K 线在发请求前 `unsupported`。期货成交额单位对不上，`amount` 留空。东财挂掉时大盘概览这两格改走新浪；新浪也失败才跳过。
- **中证2000 `2.932000`**：腾讯、新浪、同花顺公开接口都没有这只指数。前缀 `2` 的映射返回 None，不再拼 `sh932000`。不要改挂 ETF 或国证2000 `sz399303`。东财不可用时这张卡片不画。
- **五日分时只有东财**：`个股 五日贵州茅台` 在新浪/腾讯/同花顺都会落到「当日分时」以外的失败。
- 链式容错能掩盖一部分（腾讯缺 `board` 会顺延到东财/新浪），但**单源配置必须知道这些边界**。

### 11.8 两个只有「走端口之后」才会暴露的成交额问题（已修）

1. **字段错位。** `get_hours_from_em` 返回的是 `(今日成交额, 今日-昨日, 日期)`
   ——`calculate_difference` 的第一项是 `all_today_data`。EM adapter 却映射成
   `prev_amount=今日`、`amount=差值`。这条错位长期没暴露，因为 `draw_info` 改造前
   直接调底层函数按下标取用（`all_f6, f6diff = ...`），**`market_turnover` 是零消费方的死代码**。
   走端口后的症状：大盘概览的「成交额」显示成差值，放量/缩量算成「差值 − 今日成交额」。
2. **失败伪装成功。** trends2 两个市场都挂时 `get_hours_from_em` 只 warning 再 `continue`，
   返回 `(0, 0, None)`；adapter 原样上报成「成交额 0 亿」这个**成功结果**，于是请求链
   **不会顺延**。实测 `-400016` 期间大盘概览就是静默显示 0 亿。现在返回 `network` 错误，
   同一时刻实测顺延到新浪得到 **14380 亿**。

顺带对齐了 `last_trade_date` 的语义：东财「正常交易日回 `None`」，新浪「回数据所属
交易日（盘中即今天）」。`draw_info` 原来只判 `is not None` ⇒ **新浪供数时盘中会显示休市**。
改为看「数据是不是今天的」（天数按 `.date()` 相减；带时分相减会把同一天算成 -1 天）。

### 11.9 涨跌分布口径修复：三处误判，修复后与官方完全对齐（PR #19 head `1b1cb86`）

新浪自算 breadth 曾在首尾两档明显高估：涨停 63 / 跌停 21（官方 56 / 12）。逐项实测定位后修复：

1. **首尾档改按价格判定。** 从「涨跌幅 ≥ 名义阈值 × 0.95 容差」改为「收盘价 == 涨停价/跌停价」
   （昨收 × (1±阈值)，Decimal 四舍五入到分）。涨幅超阈值但未封板的不再计入涨停：
   新股首日 `N力勤` +206.6%、科创冲高回落 `南模生物` +19.87%（收盘 64.98 < 涨停价 65.05）。
2. **删除 ST 收窄到 5% 的规则。** 实测腾讯盘口「涨停价/跌停价」字段：主板 ST/*ST 同样是 ±10%
   （如 `*ST华幸` 跌停价 1.23 = 昨收 1.37 × 0.9）。原规则按 5% 判定，是 63/21 高估的来源之一。
3. **停牌剔除。** 现价或成交量为 0 的行不计入任何档位（含「平」）；官方把停牌单列（11 只）。
   此前新浪把停牌记进「平」：181 vs 官方 170。

东财侧同步修复：`updowndistribution` 的 key=`"4"` **就是平盘家数**（实测 170 == 同花顺 flat），
原实现按 0 填充导致东财路径「平」恒为 0。

修复后用 5571 行实网数据离线重放：**涨停 56 / 跌停 12 / 平 170** —— 与东财、同花顺、腾讯
三家官方口径完全一致（配套更新 3 条回归测试：封板判定 / 停牌剔除 / 东财平盘档）。

---

## 12. 收尾记录（2026-10-08）：质量门与「哪些失败与配置无关」

### 12.1 质量门

| 门 | 结果 |
|---|---|
| `ruff check SayuStock test` | ✅ All checks passed |
| `ruff format --check SayuStock test` | ✅ 全仓已格式化 |
| `basedpyright`（本次改动文件） | ✅ 0 errors |
| `pytest test -q`（真 venv，非桩） | ✅ **606 passed / 4 skipped**（185s） |

> 本机 `.venv` 补齐了插件声明的依赖（pandas / plotly / mplchart / holidays / matplotlib / pyarrow）
> 之后，真 pytest 可全量开跑 —— §11.6 里「本机 pytest 跑不起来（缺 pandas）」的环境限制已解除，
> 该节数字（42 passed）由本节取代。

### 12.2 单源接口逐源实测（证明「失败与配置无关」）

直接调四个 adapter（绕过源链），2026-10-08：

| 接口 | eastmoney | tencent | sina | ths |
|---|---|---|---|---|
| `intraday(ndays=5)` | ERR（trends2 限流中） | `unsupported` 仅支持当日 | `unsupported` 仅支持当日 | `unsupported` 未实现 |
| `northbound` | ERR（上游 `data:null`） | `unsupported` | `unsupported` | `unsupported` |
| `hotmap` | OK | `unsupported` | `unsupported` | `unsupported` |
| `valuation_series` | OK | `unsupported` | `unsupported` | `unsupported` |
| `financial_snapshot` | OK | `unsupported` | `unsupported` | `unsupported` |
| `sector_menu(concept)` | OK（504 个） | `unsupported` | **OK（175 个）** | `unsupported` |
| `rank_list(MAIN_INFLOW)` | ERR（clist 限流） | `unsupported` | **OK** | `unsupported` |

→ `intraday(ndays=5)` 与 `northbound` 是**当前源码下的单源接口**：其他三源明确 `unsupported`，
链再长也只能落到东财。所以它们失败无法通过配置规避（也无需规避——即使配置里删掉东财，
链尾兜底也会把它补回来）。

### 12.3 东财限流的判定证据（避免后人误判）

同一份配置同一天两次跑结果不同，逐项排查后确认为**本机对东财 push2 / push2his 的请求按量限流**：

- 失败形态是**连接被直接断开**（`ServerDisconnectedError`），不是 HTTP 错误码；插件在
  `push2` 与 `push2delay` 双域都断连后返回自造码 `-400016`（`utils/eastmoney.py:210-222`）。
  **`-400016` 不是东财服务端错误码**——日志/报错里看到它，直接按「东财侧连不上」理解。
- 与 Cookie 无关：交错 A/B（仅 UA / 内置 `DC_COOKIES` / 配置 Cookie+DC 三条件各 4 轮，3s 间隔）
  显示失败**按时序聚集**（同一窗口内头 1–2 次成功、其后全断），三条件无差异。
- 与客户端无关：aiohttp（插件同款）与 httpx 裸探同样受影响。
- 与配置无关：8 档配置的失败集合完全一致（§10.6），且 45s 间隔复跑后首跑的瞬时错误全部消失。

### 12.4 已知边界（不做假承诺）

- `intraday(ndays=5)`（五日分时）与 `northbound`（北向）在**东财侧不可用时无任何兜底**：
  前者是本机限流（会自愈），后者是上游停发（交易所已取消实时披露，见 §8.4，短期无解）。
- 板块代码空间各源私有：东财限流期间「行业/概念选股」仍会整体失败（§11.4 末），
  这不是配置能解决的问题。
- `fetch_a_share_universe` 保留东财直连（端口能力缺口，见 §11.5）。
- 个别瞬态：`resolve` 走东财代码表（网络），偶发网络抖动会让依赖 resolve 的接口返回
  `not_found` 并短路整链（既有设计：解析层各源共用、换源无意义）。
  **本次已顺带加固**：`valuation_series` 原先漏用 `_resolve_code` 的加固解析（`quote`/
  `intraday` 都用了），瞬断被误报成 `not_found`；现已改为瞬断报 `network` 顺延、
  真·查无此票才 `not_found`，并补两条回归测试（`test/market/test_resolve_layer.py`）。
- **同花顺没有证券名称（已修）**：快照接口无名称字段，适配器沿用解析层结果，而解析层对
  `secid` 形态输入（`1.600519`，模拟盘取价用的就是这种）原先本地短路返回**空名**，于是
  `Quote.symbol.name` 退化成代码 → `matcher._is_st(name)` 判不出 ST，主板 ST 涨跌停拦截
  会从 ±5% 退回 ±10%（模拟盘风控变宽）。修法：解析层 secid 分支改用**随仓库分发**的
  `chinese_stocks.json`（5909 条 A 股、含 315 条 ST）补名，**零网络成本**，并加市场前缀
  守卫——该表按 6 位代码索引，指数与个股会撞码（`1.000001` 上证指数 vs `000001` 平安银行），
  只有 secid 前缀与代码真实市场一致才补名。
  残留边界：表外代码（ETF / 新上市）仍无名；ST 新鲜度跟随 `chinese_stocks.json` 的重新
  生成节奏（维护脚本 `utils/update_stocks.py`）。东财/腾讯/新浪从各自报文带回真名，不受影响。
  实测：同花顺单源链取 `1.600340` → `*ST华幸` → 阈值 5.0%（修复前为 10%）。

---

## 13. 评审整改（2026-10-08）：8 条缺陷 + 配置按「有没有第二源」重切

起因是一次外部逐行评审，提出 8 条缺陷与一条配置诉求（「其他家没有的内容单独成键，
点开就能看出推荐里只有一个」）。逐条实测复现 → 修复 → 补回归，并借机跑出**全接口 ×
全源能力矩阵**（§13.9）作为配置 `options` 的收窄依据。

### 13.1 涨跌分布柱：13 档画出画布外 + 平盘被涂成上涨红（高）

加入「平」档后 `BREADTH_BANDS` 从 12 档变 13 档，而渲染端两处仍按 12 档写死：

| 现象 | 根因 | 修法 |
|---|---|---|
| 末柱右缘 873 > `div.png` 宽 850，家数标签一起被裁 | 柱间距硬编码 `dindex * 66`（12 档时 45 + 11×66 + 36 = 807 正好在内） | 末柱右缘固定 807、间距按档数自算（`breadth_bar_left`）。12 档仍逐像素等于原 66px，13 档及以上自动压缩 |
| 平盘柱被涂成上涨红 | 配色按「第 6 根柱」对半切（`dindex <= 5`），插入「平」后它落到第 7 根 | 方向改由 `models/stats.py` 的 `BREADTH_DIRECTION` 单点给出（+1 涨 / 0 平 / -1 跌），涨跌家数合计也改由它派生 |

两条**不换源也会发生**（东财正常供数同样越界/错色）。测试：`test/test_breadth_layout.py`
（7 条，含「12 档间距不回归」与「任意档数末柱在画布内」）。

### 13.2 主板拿不到名称时按普通主板放行（高）

§12.4 记录的「同花顺无名称」已用本地表补名，但**表外代码**（新上市 / ETF）仍无名，
而 `matcher._is_st("")` 返回 False → 主板阈值按 ±10% 走。容灾换源恰好是缺名最容易发生的
时候，所以这条不能靠「补名覆盖率」兜住，得在风控入口兜：**名称缺失直接拒单**。

- 实现：`matcher._missing_name_reason(code, name)`，主板（60/00）缺名 → `ok=False`，
  reason 写明「涨跌停风控不可用」；科创/创业（±20%）、北交所（±30%）阈值与 ST 无关，不拦。
- 只在**本来就要做涨跌停判定**时生效（没有昨收/涨跌幅时判定与名称无关）。
- 测试：`test/test_papertrade_matcher.py` 4 条，含「同名同价、有名字则正常成交」的对照。

### 13.3 空盘口行被当成 `not_found`，短路整条链（中）

`not_found` 是**短路**语义（解析层共用，换源无意义）。腾讯/新浪在「符号已映射成功、
返回行却是空占位」时也报 `not_found` —— 东财失败后，腾讯一行空占位就把新浪和同花顺一起
挡在门外。改为 `empty`（顺延语义），「没有这只票」仍由解析层负责。
测试：`test/market/test_empty_quote_falls_through.py`。

### 13.4 休市日悄悄改走新浪成交额（中）

`calculate_difference` 原来按「几号」比较日期、且**最多回退 4 天**：国庆连休 7 天时探不到
任何交易日 → `(0, 0, None)` → adapter 判为拉取失败 → 顺延到新浪（`prev_amount=None`、
日期口径也不同），而图上那个数**看不出是哪来的**。

- 改为按**完整日期**取数据里最近的交易日：休市日照样由东财给出「上一交易日成交额 +
  实际日期」，`prev_amount` 也在，放量/缩量不再丢。跨月（09-30 / 10-02）不再被 `.day` 比较搞错。
- `MarketTurnover` 增加 `provider` 字段并进 `_STAMPABLE`；概览图左下角补
  「数据来源：X | SayuStock」——成交额会在源之间顺延，数字必须能标出来源。
- 测试：`test/market/test_turnover_calendar.py` 7 条（长假 / 短假 / 交易日 / 同时刻对比 /
  跨月 / 单日 / 空数据）。

### 13.5 `.us/.h/.kr/.a`：两层独立缺陷，只修一层仍然错（中）

评审指出的是第一层，实测发现还有第二层，**两层是串联的**：

1. **`for ... else` 错配**：搜索有结果但无目标市场时，回的是「第一项的 QuoteID/名称 +
   循环变量（最后一项）的证券类型」——一份自相矛盾的三元组。改为返回 `None`，
   并把 `priority → 可接受 SecurityTypeName` 收成 `_MARKET_SEC_TYPES` 单表。
2. **候选拆分把后缀架空**：`_code_query_candidates("600519.us")` 会先抽出裸代码
   `600519`，以 `priority=None` 命中 A 股并**先返回**——`.us` 根本没生效。
   （名字式查询如 `三星电子.kr` 不受影响，所以此前没暴露。）
   修法：带显式市场后缀时只回整串候选。

测试：`test/market/test_symbol_display_and_suffix.py` 2 条（`600519.us` → None、
`600519.h` → 三项同源的港股、无后缀主路径不变）。

### 13.6 新浪概念菜单缺 `await`（中）

`sina/provider.py` 的 `_sector_node` 把 `fetch_fljk_summary("class")` 的**协程对象**放进
元组没 await，`isinstance(..., str)` 为假 → `industry_menu` 收到协程必然出错 →
概念名永远匹配不上。当前无调用方传 `sector=`，属潜伏缺陷，一并修掉。

### 13.7 时间预算：8 秒包住整条链，慢源一挂就没人接班（中）

`quote_service` 的 `asyncio.wait_for(..., 8s)` 取消的是**整个** `quote()`：东财快速失败时
8 秒内来得及试腾讯，东财**挂起**时预算被吃光、后面的源一个都不会开始 —— 而容灾恰恰只在
那种时候才需要生效。叠加 `stock_request` 的 `ClientTimeout(total=300)`（等于没有超时）。

- `eastmoney.stock_request` 300s → **10s × 最多两个域名**（push2 / push2delay），
  单次 `stock_request` 的最坏总时长仍是 20s，与其它源一致。
- `_dispatch` 加**每源封顶** `SOURCE_TIMEOUT_S = 25.0`（> 各源自身 20s，让源自己报错而
  不是被取消；> 实测最慢健康调用 7.29s）。
- 新增 `chain_deadline(seconds)`：调用方声明整链总预算后，链内按「剩余预算 / 剩余源数」
  分片，保证每个源都轮得到。模拟盘取价用它包住 `QUOTE_TIMEOUT_S`。
- 预算耗尽时**就地停链并回 `MarketError`**（不能让 `None` 漏给调用方）。
- 测试：`test/market/test_provider_switch.py` 新增 3 条（慢源顺延 / 分片轮转 / 预算耗尽）。

**上面两处第二轮被证伪，已改（见 §14.1 / §14.2）**：平摊到 4 源只有 3s，低于东财
冷启动实测 3.98s；而 20s 的单域名超时会吃掉链上 25s 的每源封顶，备用域名一次都轮不到。
`QUOTE_TIMEOUT_S` 的取值也改过两次：`8.0 → 12.0`（第一轮，`6c564cc`）→ **20.0**
（第三轮放宽，见 §15.1）。

### 13.8 K 线日期窗滤空时返回未过滤的原序列（中）

新浪/腾讯客户端过滤后若窗口内一根都没有，原实现 `return series` 把**未过滤**的整段返回 ——
调用方拿到的日期范围就是错的（且 `datalen` 只够最近 N 根，窗口更早时必然触发）。
改为 `empty` 让注册表顺延到能给这个窗口的源。

### 13.9 全接口 × 全源能力矩阵（`options` 收窄的依据）

对四个 adapter 逐一直接调用 18 个入口（绕过源链）：

| 接口 | eastmoney | tencent | sina | ths |
|---|---|---|---|---|
| `quote` / `quotes` | Y | Y | Y | Y |
| `intraday(1d)` | Y | Y | Y | **N** |
| `intraday(5d)` | **Y** | N | N | N |
| `kline(D1)` | Y | Y | Y | Y |
| `board`（行业 / 概念 / 沪深A / 主要指数） | Y | **N** | Y | **N** |
| `rank_list` | Y | **N** | Y | **N** |
| `sector_menu`（industry / concept） | Y | **N** | Y | **N** |
| `breadth` | Y | **N** | Y | **N** |
| `market_turnover` | Y | **N** | Y | **N** |
| `hotmap` | **Y** | N | N | N |
| `northbound` | **Y** | N | N | N |
| `valuation_series` | **Y** | N | N | N |
| `financial_snapshot` | **Y** | N | N | N |

（Y = 实现了该接口，N = 返回 `unsupported`。）注意**腾讯不支持板块/排行/菜单/涨跌家数/
成交额**，所以 D 组只有东财 + 新浪两家。

耗时实测（成功调用的最慢值）：`breadth` 7.29s（新浪全 A 扫描，最慢）、
`board(limit=None)` 0.99s、`quote` 0.85s、`kline` 0.48s、`hotmap` 0.47s、`market_turnover` 0.05s。

### 13.10 配置重切：按「有没有第二源」分 5 组

`market_api_chain_board` 里混着 `hotmap`（东财独占）会让用户以为「把新浪排前面云图就能走
新浪」；`market_api_chain_market` 同理混着北向/估值/财报。重切后：

| 组 | 接口 | options |
|---|---|---|
| ① quote | quote / quotes / intraday | 4 源 |
| ② kline | kline | 4 源 |
| ③ board | board / rank_list / sector_menu | 东方财富 / 新浪财经 |
| ④ market | breadth / market_turnover | 东方财富 / 新浪财经 |
| **⑤ exclusive** | hotmap / northbound / valuation_series / financial_snapshot | **仅东方财富**（默认值预填，即「推荐」） |

判据：**分域依据是「该接口还有没有第二个源」**。只有一个源的接口并进 ⑤，用户点开看到
候选里只有一个，就知道其他家没有。⑤ 的功能语义是「说明」而非「控制」——填别的也不会生效
（不支持的源会被跳过、链尾兜底仍是东财），配置描述里已如实写明。

同步新增测试：`test/market/test_provider_switch.py` 的
`test_every_routed_interface_group_has_a_config_key`（接口表新增域却没有配置键会直接红，
防止新接口静默回落全局链）与 `test_exclusive_group_owns_the_single_source_interfaces`。

### 13.11 评审提到但**不改**的两条（附判断）

- **「源链关不掉任何一个源」**：这是刻意的设计（§10.4 第 2/5 条）。链只表达优先级，
  链外源排链尾兜底，为的是兑现「任意配置下每个接口都有源」。真要禁用会与那条硬约束冲突。
- **「`resolve()` 只用链头，四个 equity 源的解析都打东财 searchapi，searchapi 挂了则一起失败」**：
  事实描述正确，但 searchapi 是唯一的代码↔secid 解析源（各源都用它把用户输入转成自己的符号），
  换不了源；`resolve` 因此不参与源链配置（`_IFACE_GROUPS` 里没有它）。
- 附带一条评审提到的取舍：**「排在前面的源口径错了，后面的源不会再试」** —— 这也是刻意的
  （成功即返回）。口径差异已写在各组配置描述里（如 K 线的新浪不复权）。

## 14. 第二轮评审整改（2026-10-08）

第二轮结论是「只差两处小改动，改完就可以合」。下面四条已落地并实网复现/验证；
第 1 条未收到（见 §14.5）。

### 14.1 链头只分到 3s，一次正常请求被切在成功之前（必改）

`_source_budget` 原先是 `min(SOURCE_TIMEOUT_S, 剩余预算 / 剩余源数)`。模拟盘取价
`QUOTE_TIMEOUT_S = 12.0` 摊到 4 源，链头只拿到 **3s**；而东财首拨要建连 + 握手，
**冷启动实测 3.98s** —— 分片方向没错，错在把「一次正常但偏慢的请求」和「挂起」一视同仁。

修法是给时间片加下限，同时保留「不越出剩余总预算」的夹取：

```python
share = max(remaining / sources_left, MIN_SOURCE_SLICE_S)  # MIN_SOURCE_SLICE_S = 8.0
return min(SOURCE_TIMEOUT_S, remaining, share)
```

8s ≈ 冷启动的两倍余量。三个夹取各自的职责写在 `_source_budget` 的 docstring 里：
不超每源封顶、不越剩余总预算、也不为了平摊给后面的源把当前这个切掉。

- 回归 `test_first_source_slice_covers_cold_start`：4 源 12s 下链头 ≥ 8s 且 > 3.98s、
  不越出剩余总预算、预算耗尽回 0。（断言带 1e-6 容差：`deadline - monotonic()` 是
  两个大浮点数相减，实测有 1e-10 量级舍入。）
- 既有的 `test_chain_deadline_reserves_time_for_the_rest_of_the_chain` 把下限
  monkeypatch 成 0.05s —— 否则为了等链头那 8s 的时间片，单测要空跑 8 秒。

### 14.2 东财超时直接抛异常，备用域名一次都轮不到（必改）

`stock_request` 里 push2 失败会在 `urls = [push2, push2delay]` 上再试一次，但
`except ServerDisconnectedError` / `except ClientConnectionError` 只覆盖连接类错误。
aiohttp 的 `ClientTimeout(total=)` 超时抛的是**裸 `asyncio.TimeoutError`**
（不是 `ServerTimeoutError`，因而不属于 `ClientConnectionError`），于是超时直接冲出
`stock_request`，备用域名一次都轮不到。

不是推测：把 `update_stocks.py` 全量重跑一次就当场复现 —— 一级板块拉到 29/31 时崩在
`aiohttp/helpers.py:759 raise asyncio.TimeoutError from exc_val`，整个刷新任务失败。

两处改动：

- 捕 `(ClientConnectionError, asyncio.TimeoutError)`，超时也走 `_update_preferred_domain`。
- 单域名超时 20s → **10s**：push2 / push2delay 各一次，单次 `stock_request` 最坏总时长
  仍是 20s。若仍是 20s，链上 `SOURCE_TIMEOUT_S = 25s` 的每源封顶会把备用域名那次重试
  掐掉（20 + 20 = 40 > 25）—— 而 push2 被限流时，push2delay 往往正是通的那个。

回归：新增 `test/market/test_eastmoney_domain_fallback.py` 3 条（超时后确实再打
push2delay / 两个域名都超时回 `-400016` 而不是抛异常 / 非 push2 地址没有备用域名）。

### 14.3 ST 拦截只修了一半：名称为空被替换成了代码（必改）

上一轮加的「主板缺名拒单」判的是 `if name`，但解析层拿不到名称时会把**代码回填进
name**：

- `adapters/eastmoney/parse_quote.py:94`：`name_raw = opt_str(data, f58) or code`
- `adapters/eastmoney/provider.py:211,267`、`adapters/_base.py:51`：`name=code_info[1] or secid…`

所以生产链路里 name 永远不是 `None`（单测只造了 `None`，那条路真机走不到）。代码不是
空串，「缺名就拒单」被整个绕过：本地表里的票（`*ST帅电` 等）能拦住，表外的
`609999` / `001381` 涨 6% 照样按主板 ±10% 成交。

修法：`matcher._name_is_placeholder()` 把「空 / 等于代码 / 带市场前缀的 secid 形态
（`1.609999`）」一并判为缺名，`_missing_name_reason` 改用它。

回归两端都钉住：

- `test/market/test_parse_quote.py::test_missing_name_becomes_code_placeholder_and_matcher_rejects_it`
  先用真 `parse_quote_payload` 证明解析层确实产出代码占位，再把该 name 喂给真 matcher 断言被拒
  —— 单测不再自造生产上不存在的输入。
- `test/test_papertrade_matcher.py` 覆盖 6 种占位形态（`609999` / `001381` / `1.609999` /
  `0.001381` / 空串 / 纯空白），外加反向对照：真名 +6% 放行、`*ST` +6% 仍被涨停拦截。

### 14.4 `chinese_stocks.json` 刷新（发版前必做）

原表 5909 条、生成于 2026-09-04。用仓库自带 `SayuStock/utils/update_stocks.py`
（东财 `clist` 全 A + 行业板块三级成分）重跑，耗时 77.6s：

| 项 | 结果 |
|----|------|
| 条数 | 5909 → **5921** |
| 新增 / 删除 | +12 / **-0** |
| 改名/跨行 | 58（含 `300527 ST应急→中船应急` 等 4 只摘帽、`600363→ST联光` 等 2 只戴帽） |
| ST 条数 | 327 → 325 |
| 行业覆盖 | 5579/5921 |

两点核对：

- **评审点名的 `001381` 这次进了表**（皇冠新材），`609999` 不是真代码（表中无此项），
  所以「表外主板代码」这条容灾路径仍然存在，§14.3 的占位判据才是兜底。
- `XD*` / `C*`（除息日、新股）这类**临时前缀名**新旧两版都有（旧 6 个、新 5 个），
  不是本次刷新引入的；它们不影响 `_is_st`（前缀不是 ST），暂按上游原样保留。

⚠️ 这次重跑顺带就是 §14.2 的端到端验证：修之前同一脚本必崩，修之后一次跑通。

### 14.5 未收到评审第 1 条

第二轮报告正文从「2.」开始，附件（完整报告 + 分布图）没有随消息到达，所以
「还没解决的有两件」里的**第 1 条无法核对**。已向作者索要；拿到后本条再定稿，
不排除还要再改一处。

## 15. 第三轮（2026-10-08，作者直接指示）

### 15.1 模拟盘取价总预算 12s → 20s

第二轮把时间片下限提到 8s 之后，12s 的总预算只够「链头 8s + 第二个源 4s」。
放宽到 **20.0s** 后：链头 8s、第二源 8s、第三源 4s，三个源都拿得到有意义的片。

为什么是 20：它等于各源自身的 HTTP 超时（东财拆成每域名 10s × 最多两域名，合计也是
20s），所以「源自己报错返回」和「链把它切掉」这两件事的时间尺度对齐；健康单源实测最慢
0.85s，正常取价根本走不到上限。同一个 `(secid, ts_window)` 的并发请求仍有 `_lock` 兜住，
批量取价走 `asyncio.gather` 并发，整批墙钟时间同样被这个上限罩住。

回归：`test/test_papertrade_quote_resilience.py::test_quote_budget_lets_chain_head_reach_the_floor`
把**出货值**和 `MIN_SOURCE_SLICE_S` 绑死 —— 谁把总预算调到下限以下，链头就又会退化成
「一个人吃掉整个预算」，这条直接红。

### 15.2 「K 线别把新浪放第一位」：查证结果与修法

作者问「K 线默认优先级是怎么样的？不应该先用东财吗」。用运行时配置实跑
`build_priority_chain` 核对，**五个域 + 全局的实际链都是 东财 → 腾讯 → 新浪 → 同花顺**，
新浪在第三位，默认值空列表就是这条内置链（§10.1）。

真正会误导人的是**文档**：§7 里那段讨论「单接口覆盖」的方案示例写着

```
market_api_overrides = "kline:新浪→东财;intraday:东财→腾讯;northbound:东财"
```

该方案从未落地（最终采纳的是 6 个域链键），但示例本身读起来就像「K 线默认新浪优先」。
已把示例里的 K 线部分换成不与默认链冲突的写法，并就地标注「示例语法、未落地、
不要照抄这个顺序」；§10.2 也补了一条：② K 线不要把新浪排链头，否则会静默换掉复权口径。

顺带说明：运行时 `config.json` 里还留着上一代设计的 4 个 `market_api_priority_*`
数字键（东财 40 / 腾讯 30 / 新浪 20 / 同花顺 10）。它们**已不被任何代码读取**
（第一轮就换成了字符串链），留着只是历史数据，不影响行为。

### 15.3 清掉写旧的「20 秒」注释

单域名超时从 20s 改成 10s 之后，有两处注释还在按旧值描述：

- `provider_registry.SOURCE_TIMEOUT_S` 上方原写「东财 `stock_request` 是全局
  `ClientTimeout(total=20)`」→ 改为「各源自身的 HTTP 超时是 20s（东财拆成每域名 10s、
  最多两个域名，合计也是 20s）」。
- `MIN_SOURCE_SLICE_S` 上方原写「在 4 源 12s 预算下只给到 3s」→ 改成不绑定具体预算值的
  说法，并同步 §13.7 里 `QUOTE_TIMEOUT_S` 的取值沿革。

`eastmoney.py` 里解释「为什么不是 20s 而是 10s × 2」的两段注释经复核是**当前决策的理由**，
不是旧值残留，保留。

### 15.4 名称表定期刷新

`chinese_stocks.json` 现在由一次性人工刷新（§14.4）。作者要求之后定期跑
`SayuStock/utils/update_stocks.py` —— 调度周期与执行方式待确认后另行登记，
刷新命令与判读口径（看「新增/删除/改名」而不是只看条数）见 §14.4。

## 16. 源链默认值：把推荐链填进 `data`（2026-10-08，作者直接指示）

原先六个链键里 5 个是 `data=[]`，「推荐顺序」只写在 `desc` 文案里，控制台打开是空列表。
作者要求把这些配置的默认值直接补上推荐链。

| 键 | 原默认 | 现默认 |
|---|---|---|
| `market_api_chain` / `_quote` / `_kline` | `[]` | `["东方财富", "腾讯财经", "新浪财经"]` |
| `market_api_chain_board` / `_market` | `[]` | `["东方财富", "新浪财经"]` |
| `market_api_chain_exclusive` | `["东方财富"]` | 不变（原本就预填） |

**等价性（为什么能放心预填）**：链外源本来就会按 `_SYSTEM_ORDER` 补到链尾，所以

- ①②组与全局：填的值就是内置默认链的前缀，同花顺照旧由链尾补入 → 链不变；
- ③④组：填入 `["东方财富", "新浪财经"]` 后链变成 `东财 → 新浪 → 腾讯 → 同花顺`
  （腾讯/同花顺对这两组是 `unsupported`，跳过）→ **实际取数顺序与原「留空 → 内置链」一致**。

回归测试 `test/test_market_config_defaults.py` 把两件事一起钉住：填完之后每个域的实际调用链，
以及「填 vs 留空」在每域**真正实现了接口的源**（`options` 就是这份名单）上的先后必须相同。
变异验证：把③组的默认顺序反转，两条断言都红（`assert ['sina', 'eastmoney'] == ['eastmoney', 'sina']`）。

**已知边界**：GsCore 加载配置时对已存在的键**保留用户 `data`**、只刷新 title/desc/options
（`gs_config.py` 的 `reconcile_config`：「同类型: 刷新代码侧元数据, 保留用户 data」）。
所以这次改的是「新装 / 新增键」的默认值，已经存过盘的实例保持原样 —— 该功能尚未发布，
无需迁移。
