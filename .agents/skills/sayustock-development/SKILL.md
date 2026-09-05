---
name: sayustock-development
description: >
  当用户要求"维护/开发 SayuStock 插件"、"股票插件怎么加命令"、"行情数据从哪取"、
  "MarketDataPort 是什么"、"为什么不能读 f* 字段"、"分时/K线/云图怎么画"、
  "render_data / render_text 分工"、"AI 工具怎么注册"、"stock_agent 能力代理"、
  "模拟盘 papertrade 怎么改"、"自选股 SsBind"、"缓存路径 / STOCK_CONFIG"、
  "指标 utils/indicators 口径"、"加新数据源 OKX/VIX"、"有图必有文字 ai_return"、
  "改 SayuStock 要注意什么 / 有哪些坑"、"CI 怎么跑 / GitHub Actions 红了"、
  "pytest 找不到 SayuStock / gsuid_core"、"basedpyright venv"、"pre-commit 与 ruff"
  时触发此 SKILL。
  凡是改动 `gsuid_core/plugins/SayuStock` 业务插件（非 GsCore 框架核心）的任务
  都应优先读取此 SKILL。

  面向 **SayuStock（早柚股票）插件开发者与维护者**的系统级开发指南。
  与「GsCore 框架开发」「插件通用开发」SKILL 不同，本 SKILL 讲的是 **SayuStock
  自身的内部结构与设计约束**：目录与模块全景、SV 命令与触发器、MarketDataPort
  行情抽象与多源路由、渲染管线（data → render_data → chart / plotly → ai_return）、
  AI 工具与能力代理、模拟盘 papertrade、配置/数据库/缓存、测试与质量门、
  CI/CD 与本地开发流程，以及一份**已知坑与开发注意事项**清单。
---

# SayuStock 插件开发与维护指南（核心入口）

> 本 SKILL 面向 **SayuStock 插件本身的开发者 / 维护者**，描述插件结构、行情层、
> 出图链路、AI 集成与模拟盘，以及后续开发必须注意的约束与坑点。
> 目标：让不熟悉本仓库的人也能安全地改插件，不踩历史上踩过的坑。
>
> 内容按章节拆分为「主入口 + `references/` 子文档」。需要某专题细节时，顺着下表的相对
> 路径**按需** `Read` 对应文件，**不要**一次性把所有内容塞进上下文。**源码永远是唯一
> 事实源**，本 SKILL 是导航与设计意图说明；改动核心后请同步更新对应章节。

## 谁该读这个 SKILL（与其他文档的分工）

| 你的任务 | 该读的文档 |
|----------|-----------|
| **改 SayuStock 业务代码**（行情 / 出图 / 命令 / 模拟盘 / AI 工具） | **本 SKILL** |
| **修 CI / 写测试 / 提 PR 对齐 Actions** | 本 SKILL [八](./references/08-testing-and-quality.md) + [十](./references/10-cicd-and-dev-workflow.md) |
| 改 GsCore 框架核心（handler / ai_core / 启动 / 配置基类） | Core `.agents/skills/gscore-development` |
| 写一个全新的 GsCore 插件（通用模板） | Core `.agents/skills/gscore-plugin-development` |
| 查 AI Core 给插件暴露的 API | Core `.agents/skills/gscore-ai-core-api` |
| 用户向模拟盘操作说明 | 插件内 `docs/papertrade.md`、`SayuStock/stock_papertrade/PAPERTRADE_GUIDE.md` |
| 行情 Port 一页速查 | 插件内 `doc/market_data_port.md` |

## 文档目录索引

| 章节 | 主题 | 链接 |
|------|------|------|
| 一 | 架构与模块全景（目录树、包职责、请求→出图总链路） | [references/01-architecture-and-modules.md](./references/01-architecture-and-modules.md) |
| 二 | 插件布局与命令层（Plugins / SV、各子包命令、to_ai 桥接） | [references/02-plugin-layout-and-commands.md](./references/02-plugin-layout-and-commands.md) |
| 三 | 行情数据层 MarketDataPort（领域模型、适配器、路由、禁止 f*） | [references/03-market-data-port.md](./references/03-market-data-port.md) |
| 四 | 渲染管线（data 服务 → render_data → chart/plotly → render_text / ai_return） | [references/04-render-pipeline.md](./references/04-render-pipeline.md) |
| 五 | AI 工具与能力代理（@ai_tools、stock_agent、Kronos 预测） | [references/05-ai-tools-and-agents.md](./references/05-ai-tools-and-agents.md) |
| 六 | 模拟盘 papertrade（账户/持仓/心跳/撮合/SQLModel） | [references/06-papertrade.md](./references/06-papertrade.md) |
| 七 | 配置 / 数据库 / 缓存 / 资源路径 | [references/07-config-database-cache.md](./references/07-config-database-cache.md) |
| 八 | 测试与质量门（pytest / ruff / pyright、fixtures） | [references/08-testing-and-quality.md](./references/08-testing-and-quality.md) |
| 九 | 已知坑与开发注意事项（红线、不变量、历史事故） | [references/09-developer-pitfalls.md](./references/09-developer-pitfalls.md) |
| 十 | CI/CD 与本地开发流程（GitHub Actions、双布局、conftest、踩坑） | [references/10-cicd-and-dev-workflow.md](./references/10-cicd-and-dev-workflow.md) |

## 推荐阅读顺序（按需跳转）

1. **第一次接触插件**：先看 [一、架构全景](./references/01-architecture-and-modules.md)，再看 [三、MarketDataPort](./references/03-market-data-port.md)。
2. **加/改用户命令**：看 [二、命令层](./references/02-plugin-layout-and-commands.md)。
3. **改出图 / 分时 / K 线 / 云图**：看 [四、渲染管线](./references/04-render-pipeline.md)，并回看 [三](./references/03-market-data-port.md)。
4. **接 AI / 加工具 / 改能力代理**：看 [五](./references/05-ai-tools-and-agents.md)。
5. **改模拟盘**：看 [六](./references/06-papertrade.md)。
6. **动手前必读**：[九、已知坑](./references/09-developer-pitfalls.md)。
7. **跑测试 / 修 CI / 提 PR 前**：[八、测试](./references/08-testing-and-quality.md) + [十、CI/CD](./references/10-cicd-and-dev-workflow.md)。

## 关键概念速记

- **GsCore 插件，不是独立 Bot**：靠 Core 的 `Plugins` / `SV` 注册触发器；前缀默认 `a` / `股票`（见 `SayuStock/__init__.py`）。
- **行情只走 `get_market()`**：业务侧读 `Quote` / `IntradaySeries` / `KlineSeries` / `BoardSnapshot`，**禁止**解析东财 `f*` 或依赖 `compat` 编码。
- **供应商字段只在 adapter 内解析**：`utils/market/adapters/eastmoney/parse_*.py` 等是唯一合法解析点。
- **有图必有文字**：`ai_return(...)` 必须在**图片缓存判断之前**调用；部分模型看不到图，文字是唯一输入。
- **指标单源**：图表与 AI 读数共用 `utils/indicators.py`（通达信/东财口径，勿改用 mplchart 西方 MACD/RSI）。
- **渲染计算单源**：`utils/render_data.py`；`stock_stockinfo` 与 `stock_cloudmap` 只 re-export，不要再分叉拷贝。
- **模拟盘落库 SQLModel**：禁止用 `record_*` / `state_set` 拼第二套账本。
- **插件加载只 import 各包 `__init__.py`**：兄弟模块的 `@sv` / `@ai_tools` 必须在 `__init__.py` 里**显式 import** 才会生效。
- **测试双布局**：扁平（无 Core，CI 指标门）与嵌套（`…/plugins/SayuStock`，全量 CI）都要能过；依赖 `test/conftest.py` 包壳，勿让单测执行 `SayuStock/__init__.py` 的 Plugins 链。
- **CI 四门全挡合并**：lint（ruff）→ indicators（轻量）→ full pytest → basedpyright；细节见 [十](./references/10-cicd-and-dev-workflow.md)。

## 仓库路径约定

```
gsuid_core/plugins/SayuStock/          # 插件根（本仓库）
├── SayuStock/                         # 可导入包
├── test/                              # pytest（含 conftest 路径/包壳）
├── .github/workflows/ci.yml           # GitHub Actions
├── pyproject.toml                     # pytest / pyright 配置
├── pyrightconfig.json                 # basedpyright（勿写死本机 venv）
├── doc/ / docs/                       # 散落专题文档
└── .agents/skills/sayustock-development/ # 本 SKILL
```

运行时数据目录（Core 的 data_store）：

```
{get_res_path()}/SayuStock/
├── config.json    # STOCK_CONFIG
└── data/          # 行情/图缓存 JSON、PNG、HTML
```

## 关联文档

- 代码红线：GsCore 根 [`AGENTS.md`](../../../../../../AGENTS.md)（插件内见 [`AGENTS.md`](../../../AGENTS.md)）
- 行情速查：[`doc/market_data_port.md`](../../../doc/market_data_port.md)
- 模拟盘用户文档：[`docs/papertrade.md`](../../papertrade.md)
- 接口/路由旧索引：[`doc/interfaces_and_routes.md`](../../../doc/interfaces_and_routes.md)（可能滞后，以源码为准）
