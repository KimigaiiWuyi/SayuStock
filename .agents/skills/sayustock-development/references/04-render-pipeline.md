# 四、渲染管线

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · [行情 Port](./03-market-data-port.md) · [测试](./08-testing-and-quality.md)

数据模型统一、计算单源、图与文字一致、缓存不丢 AI 文字。
安装、运行时路径和真实出图方法见 [`docs/rendering.md`](../../../../docs/rendering.md)。

## 4.1 所有业务图片共用 Jinja + Takumi

```text
命令 → 原数据服务 → get_market() 领域模型
                       ↓
             render_data / indicators
                 ↙             ↘
       render_text / ai_return  typed 页面结构 → Jinja HTML/SVG
                                                    ↓
                                      render.render_html → Node Takumi → PNG
```

`render/` 管理模板、调色板和几何；原 `stock_*/` 包继续负责命令、取数与返回合同。
`stock_stockinfo/render_mpl.py` 保留原导入路径，内部不再使用 matplotlib。
`stock_cloudmap/render_html` 仍可生成静态 HTML 文件，PNG 由同一模板渲染，不启动浏览器。
自选、基金、全天候、概览、估值、预测、模拟盘、IPO 和分析卡片也走共享入口。
Core 的帮助和 Markdown 渲染仍归 Core，不能把整个 Core UI 复制进插件。

模板不能接收东财裸 `f*` 字典。用领域模型或有类型的展示结构，展示层只安排位置、
精度、标签与颜色，不重算或替换业务指标。`render/charts.py` 只提供 SVG 几何。

## 4.2 保留取数与降级语义

`stock_stockinfo/data.py` 覆盖分时、五日分时、K 线、对比、多股、板块展开与云图；
`stock_cloudmap/data.py` 保留云图的数据入口。`raw_data` 为 `str` 时是业务错误，
直接返回，不传进 `build_*_render_data`。

| 请求 | 数据合同 |
|---|---|
| 大盘云图 | `hotmap`；带筛选时 `board(sector)` |
| 行业云图 | 申万三级成分 ∩ `hotmap()`，缺表才用原 BK 缓存 |
| 概念云图 | `sector_menu("concept")` + `board` |
| `single-stock-kline-*` | `kline` + 原 `KlinePeriod` |
| `compare-stock` | 多标的 `kline(D1_YEAR)`，默认约一年 |
| `single-stock` | `intraday`，名称含「(板块)」时展开成分股 |
| `single-stock-ndays-5` | `intraday(..., ndays=5)`，按交易日分隔，不能替换成日 K |

数据源显示使用 `display.source_footer(*providers)`，传真实结果的 `provider`。
没有来源标识的数据不能根据市场猜供应商。保留数据时间，生成时间不能冒充行情时间。
不新增取数请求来补装饰性 UI。

## 4.3 失败必须留在图和文字里

- 我的自选：保留 `intraday → quote` 降级，失败票逐个列在警告区；不得静默减少请求数量。
- 基金：失败重仓仍占原行并保留原权重，在涨跌幅格写失败、同一行写原因，不能删掉后重排。
- 模拟盘持仓：保留失败标的和现有文字兜底，不因换渲染器改变账户或订单数据。
- 多股图：缺失数据不挤乱标的对应关系，部分失败写进图和 AI 文字。

颜色有含义：红涨、绿跌、灰平盘；金色/黄色用于失败或警告，不给正常行统一染黄。
行情没有价格/涨跌幅时显示缺失，不能按零涨跌或零价格绘制。

## 4.4 自选页的布局合同

顶部表情包使用原图完整比例，横跨内容宽度；下方是一排四张等宽指数卡，不能移到右边。
四卡为上证指数、深证成指、中证 A500、中证 2000；缺失指数保留位置。
列表是否双列按已保存自选总数判断（18 只及以上），不能因本轮取数失败变成单列。
股票名、代码、最新价和涨跌幅必须可辨；ETF 涨跌额保留三位小数。

## 4.5 计算单源

`utils/render_data.py` 负责分时时间轴、补点、K 线表和云图投影。
`stock_*/render_data.py` 只 re-export，不复制一套计算。
`utils/indicators.py` 供图、AI 文字和模拟盘共用：

- MA5/10/20/60、BBI、KDJ、BOLL、CMF、RSI6/12/24 保持上游口径。
- MACD 柱为 `2 * (DIF - DEA)`，不能换成外部绘图库的西式默认。
- 分时按交易会话补齐；跨天锚在开盘日，不能把 HH:MM 强贴到另一日。
- 对比保留同一基准与跨市场对齐；90 根日 K 跨年必须显示足以辨年的日期。
- Kronos 仅替换展示，模型、输入、回测和预测计算不变。

## 4.6 先发文字，再读图片缓存

```text
result = await fetch(...)
if isinstance(result, str): return result
emit_ai_text(result)
return await cached_render(path, ttl, render_callback)
```

`utils/stock/render_cache.py` 管理版本隔离的图片/HTML 缓存，原行情 JSON 缓存不变。
图片必须完整有效才能原子写入；失败和取消不覆盖已有缓存。
普通图片沿用 `mapcloud_refresh_minutes`，Kronos 保留原 150 分钟缓存合同。
缓存锁等待有上限，模型推理与图片绘制的预算分开，不能拿 45 秒绘图预算截断合法预测。

`render_text` 仍覆盖分时、K 线、对比和云图；技术指标数值与图保持同源。
冷缓存、热缓存都要调用 `ai_return`；失败时已有可用文字不能被渲染异常抹掉。

## 4.7 修改后的验收

1. 跑受影响页面测试，使用 `Quote`、`IntradaySeries`、`KlineSeries` 等领域模型 fixture。
2. 跑指标、AI 文字、冷/热缓存及受影响的失败分支。
3. 开启 `SAYUSTOCK_REQUIRE_RENDER_RUNTIME=1` 实际生成 PNG，检查文字、精度、轴和失败状态。
4. 布局和样式变化检查真实图；单元测试或 HTML 非空不能替代视觉验收。
5. 跨页面迁移运行四个 CI 检查；不因缺 Node 或原生模块把强制出图变成 skip。
