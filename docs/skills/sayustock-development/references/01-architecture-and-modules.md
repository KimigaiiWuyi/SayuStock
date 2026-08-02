# 一、架构与模块全景

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · **下一章**：[二、插件布局与命令层](./02-plugin-layout-and-commands.md)

本章建立 SayuStock 的心智模型：它是什么、目录怎么切、一条「个股 / 云图」请求从触发到
出图 / 给 AI 文字要经过哪些层。

## 1.1 SayuStock 是什么

SayuStock（早柚股票）是 **GsCore 业务插件**，提供：

- **行情与出图**：大盘/行业/概念云图、个股分时、多股分时、日K/周K/…、个股对比
- **自选与概览**：我的自选、大盘概览、估值对比（PB/PE/PS）、基金持仓等
- **AI 集成**：`to_ai` 触发器桥接、`@ai_tools` 工具、`stock_agent` 等能力代理、Kronos 预测
- **模拟盘 papertrade**：群级 AI 操盘账户、周期决策、撮合、SQLModel 落库、Kanban 心跳

它**不是**独立 Bot：消息由平台适配器 → GsCore → 本插件的 `SV` 触发器处理。

## 1.2 插件根目录

```
plugins/SayuStock/
├── SayuStock/                 # 可 import 的 Python 包（业务代码几乎全在这里）
│   ├── __init__.py            # Plugins(name="SayuStock", force_prefix=["a","股票"])
│   ├── version.py
│   ├── stock_*/               # 功能子包（命令 / AI / 模拟盘…）
│   ├── utils/                 # 共享：market / render / indicators / eastmoney / db
│   ├── Kronos/                # vendored 预测模型（pyright exclude）
│   └── tools/                 # 离线脚本（如 gen_A.py）
├── test/                      # pytest（从插件根或 monorepo 根跑）
├── doc/ / docs/               # 专题文档
├── docs/skills/…              # 本开发 SKILL
├── pyproject.toml             # poetry/pdm + pyright/basedpyright
├── ruff.toml
└── README.md
```

## 1.3 `SayuStock/` 功能子包一览

| 子包 | 职责 |
|------|------|
| `stock_cloudmap/` | **大盘/行业/概念云图**（plotly + playwright 截图）；定时清缓存 |
| `stock_stockinfo/` | **个股分时 / K 线 / 对比 / mpl 云图入口**（matplotlib + mplchart） |
| `stock_info/` | 大盘概览、我的自选列表图、基金/期货等 PIL 卡片 |
| `stock_user/` | 添加/删除自选（`SsBind`） |
| `stock_sina/` | 市盈/市净/股息等估值对比图 |
| `stock_analysis/` | 技术分析、股票卡片、自动选股、组合体检 |
| `stock_ai_func/` | 面向 Agent 的 `@ai_tools`（新闻、VIX、涨跌幅、云图工具…） |
| `stock_ai/` | Kronos 模型预测出图 |
| `stock_agent/` | 注册 `stock_agent` 与模拟盘相关 AgentNode |
| `stock_papertrade/` | 模拟盘命令、DB、撮合、策略、周期 gate |
| `stock_news/` | 雪球等新闻订阅（群） |
| `stock_help/` | 帮助图 + `register_help` |
| `stock_status/` | 状态相关（若启用） |
| `stock_config/` | `STOCK_CONFIG` / `CONFIG_DEFAULT` |
| `utils/` | **横切能力**（见下） |

`SayuStock/__init__.py` 显式 import：`stock_agent`、`stock_analysis`、`stock_papertrade`（保证
能力代理 / 分析 / 模拟盘在加载时注册）。其余子包靠 GsCore 的目录插件加载 import 各自
`__init__.py`。

## 1.4 `utils/` 横切层（最重要）

```
utils/
├── market/                 # ★ 行情抽象层（Port + adapters + models）
│   ├── port.py             # MarketDataPort Protocol
│   ├── facade.py           # build_default_market()
│   ├── registry.py         # get_market() / set_market()
│   ├── models/             # Quote / IntradaySeries / KlineSeries / BoardSnapshot …
│   ├── enums.py            # KlinePeriod / BoardKind / AssetClass / ValueKind
│   ├── errors.py           # MarketError / is_market_error
│   ├── convert/dataframe.py
│   ├── display.py          # DisplayItem（列表卡片）
│   ├── adapters/
│   │   ├── composite.py    # equity / crypto / vix 路由
│   │   ├── eastmoney/      # 主源：HTTP + parse_*
│   │   ├── okx/            # 加密货币
│   │   └── vix/            # VIX
│   └── compat.py           # 仅测试/调试；业务禁止依赖
├── render_data.py          # ★ 渲染计算唯一真相源（吃领域模型）
├── render_text.py          # ★ 图 → 文字（给看不见图的 AI）
├── indicators.py           # ★ 技术指标唯一真相源
├── kline.py                # KlineSeries → DataFrame 等辅助
├── eastmoney.py            # EastMoneyRequester 传输层（adapter 内部用）
├── eastmoney_finance.py    # 财报快照
├── get_OKX.py              # OKX 辅助（走 Port）
├── stock/request.py        # 薄封装 get_gg/get_vix/get_mtdata（返回模型）
├── stock/request_utils.py  # get_code_id 等
├── stock/get_vix.py        # VIX 原始序列
├── database/models.py      # SsBind + 导入 papertrade 表
├── database/papertrade_models.py
├── constant.py             # ErroText / market_dict / VIX_LIST …
├── time_range.py           # 交易时段、分时轴
├── image.py                # playwright 截图等
├── load_data.py            # 证券代码表
└── resource_path.py        # MAIN_PATH / DATA_PATH / CONFIG_PATH
```

## 1.5 请求 → 出图 → AI 文字（总链路）

以「个股 茅台」或「个股 日k 茅台」为例：

```
用户消息
  │  前缀 a/股票 + 触发器「个股」
  ▼
stock_stockinfo/__init__.py::send_stock_img
  │  解析 MS_MAP 周期 → sector = single-stock | single-stock-kline-101 …
  ▼
stock_stockinfo/get_cloudmap.py → render_mpl.render_image_file
  │
  ├─ CLOUDMAP_DATA_SERVICE.fetch(market, sector, …)
  │     └─ get_market().intraday / .kline / .board / .hotmap
  │           └─ CompositeMarketData 路由 → eastmoney | okx | vix adapter
  │                 └─ 返回 IntradaySeries | KlineSeries | BoardSnapshot | 错误 str
  │
  ├─ _emit_ai_text(...)          # ★ 必须在缓存命中 return 之前
  │     └─ render_text.*_text(领域模型) → ai_return(文字)
  │
  ├─ get_file(...).exists() + mapcloud_refresh_minutes 缓存？
  │     └─ 命中则直接 return 已有 PNG
  │
  └─ to_single_fig / to_single_fig_kline / to_multi_fig / to_compare_fig / to_fig
        └─ build_*_render_data(模型)  →  chart_*.py 画图 → 写 PNG
```

云图命令（「大盘云图」等）走 `stock_cloudmap/`：**plotly 写 HTML** + playwright 截图；
数据同样经 Port → `BoardSnapshot`，`build_cloudmap_render_data` + `render_text.cloudmap_text`。

## 1.6 分层原则（写代码时的默认取向）

| 层 | 允许 | 禁止 |
|----|------|------|
| 命令 / SV | 解析用户文本、调 service、`bot.send` | 解析 `f*`、拼东财 URL |
| data 服务 | 调 `get_market()`，聚合 `CloudMapDataResult` | 编码 EM dict |
| render_data / render_text | 吃领域模型，出 DataFrame / 字符串 | 读供应商原始字段 |
| chart / plotly | 只吃 RenderData / DataFrame | 再请求行情 |
| market adapter | HTTP + 解析供应商 JSON → 模型 | 被 feature 直接 import 解析函数做业务 |

## 1.7 依赖与运行环境

- **宿主**：GsCore（`gsuid_core`）+ 平台适配器
- **主要第三方**：`pandas`、`plotly`、`playwright`、`mplchart`、matplotlib
- **Python**：`pyproject.toml` 声明 `==3.12.*`（以当前环境为准）；pyright 排除 `Kronos/`
- **Playwright**：未 `playwright install` 时云图截图会卡住

## 1.8 版本与入口

- 包版本见 `SayuStock/version.py` / `pyproject.toml`（可能不一致，以发布流程为准）
- 插件注册：`Plugins(name="SayuStock", force_prefix=["a", "股票"], allow_empty_prefix=True)`
