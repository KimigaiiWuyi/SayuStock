# AGENTS.md

> 本文件遵循 [AGENTS.md](https://agents.md/)：给编码 Agent 的仓库说明（README for agents）。
> 人类用户说明见 [README.md](./README.md)。**源码是唯一事实源**。
>
> 行情 / 出图 / 模拟盘 / AI：按需读
> [`.agents/skills/sayustock-development/SKILL.md`](.agents/skills/sayustock-development/SKILL.md)，
> **不要**一次把所有 `references/` 塞进上下文。

本仓库是 **GsCore 业务插件**，独立 git。放到 `gsuid_core/plugins/SayuStock/` 安装。

## Project overview

行情出图、自选、技术/估值、AI 研究代理、模拟盘。

- `Plugins(name="SayuStock", force_prefix=["a", "股票"], allow_empty_prefix=True)`。
- 行情只走 `get_market()` 领域模型；业务代码禁止解析东财 `f*`。
- 有图必有文字：`ai_return` 必须在图片缓存判断**之前**。
- 版本：`SayuStock/version.py`（当前 `0.8`）。`pyproject.toml` 的 `[project]` / poetry 版本可能不一致。Python `==3.12.*`。

## Repository map

```
.
├── AGENTS.md / README.md / ICON.png
├── pyproject.toml / ruff.toml / pyrightconfig.json
├── __init__.py / __nest__.py
├── test/                               # pytest（不是 tests/）
├── doc/  docs/  examples/  plans/
├── .agents/skills/sayustock-development/
└── SayuStock/
    ├── __init__.py                     # Plugins + 显式 import 四包
    ├── __full__.py / version.py
    ├── stock_*/                        # 功能子包（下表）
    ├── utils/                          # market / render_data / indicators / db
    ├── Kronos/                         # vendored，pyright exclude
    └── tools/gen_A.py
```

内层 `__init__.py` 显式 import：`stock_agent`、`stock_analysis`、`stock_papertrade`、`stock_holdings_analysis`。

| 子包 | 职责 |
|------|------|
| `stock_cloudmap/` | 大盘/行业/概念云图（plotly + playwright） |
| `stock_stockinfo/` | 个股分时 / K 线 / 对比 / mpl |
| `stock_info/` | 大盘概览、自选列表、基金（PIL）；全天候 pytakumi |
| `stock_user/` | 自选增删（`SsBind`） |
| `stock_sina/` | PB/PE/PS |
| `stock_analysis/` | 技术分析、卡片、选股、组合 |
| `stock_holdings_analysis/` | 持仓分析 |
| `stock_ai_func/` | `@ai_tools` |
| `stock_ai/` | Kronos 预测出图 |
| `stock_agent/` | `stock_agent` AgentNode |
| `stock_papertrade/` | 模拟盘 SQLModel |
| `stock_news/` / `_help/` / `_config/` / `_status/` | 新闻、帮助、配置、状态 |
| `utils/market/` | MarketDataPort + eastmoney / okx / vix |

运行时：`{get_res_path()}/SayuStock/config.json` 与 `data/`。不进 git。

## Skills

| 任务 | 读 |
|------|-----|
| 本插件 | [sayustock-development](.agents/skills/sayustock-development/SKILL.md) |
| 代码红线 | Core 根 [`AGENTS.md`](../../../AGENTS.md) §1–§4、§1.9 |
| 行情速查 / 模拟盘用户说明 | [`doc/market_data_port.md`](./doc/market_data_port.md)、[`docs/papertrade.md`](./docs/papertrade.md) |

单独 clone 时打开宿主 Core 的 `AGENTS.md`。

## Setup commands

在**本插件目录**执行。解释器优先 Core 根 `.venv`。

```sh
uv run ruff check SayuStock test
uv run ruff format --check SayuStock test
uv run pytest test -q
```

- `testpaths = ["test"]`，`pythonpath = [".", "test"]`。
- `ruff.toml`：120 列，排除 `Kronos`、`pyproject.toml`。
- `pyrightconfig.json` **不要**写死本机 `venvPath`。
- 云图需要 `playwright install`。不要改 `Kronos/` 当业务代码。

## Code style

新代码与 Core 根 `AGENTS.md` **编号一致**，正反例以那份为准。

| 编号 | 要求 |
|------|------|
| §1.1 | 禁止 try-except 兜底。例外：不可信行情 JSON；`_ai_return_*` |
| §1.2–1.4 | 禁止 `cast` / 自身 `type: ignore` / `getattr`·`dict.get` 兜底 |
| §1.6 | `#` 最多两行、每行 ≤88 字 |
| §1.7 | 不改 Core `system_prompt` |
| §1.8 | 禁止 `Any` |
| §1.9 | 股票 / 模拟盘 / 研报词只出现在本插件 `covers` / `aliases` / 代理 prompt |
| §2 | 函数全标注；领域模型用 TypedDict / dataclass，禁止业务侧裸 EM dict |
| §3 | `SsBind` 与 papertrade 表：无 `__tablename__`，`@with_session`，`col()` |
| §4 | 全异步；重 CPU 绘图 `asyncio.to_thread`；matplotlib `Agg` |

行宽 120。本插件额外：

- 禁止读东财 `f*`；解析只在 `utils/market/adapters/**`。
- 禁止依赖 `utils/market/compat.py`（仅测试）。
- `ai_return` / `_emit_ai_text` 在缓存命中 return **之前**。
- 指标单源 `utils/indicators.py`；渲染计算单源 `utils/render_data.py`。
- 模拟盘只写 SQLModel，禁止 `record_*` / `state_set` 第二套账本。
- 新 `@sv` / `@ai_tools` 必须在包 `__init__.py` 显式 import。
- `@ai_tools` docstring 紧贴 `def`。

## Testing

- 扁平（无 Core）与嵌套（`plugins/SayuStock`）都要能 collection。
- `test/conftest.py` 包壳：不要执行 `SayuStock/__init__.py` 的 Plugins 链。
- 改 `ai_return`：`test_ai_text_delivery.py`。分时：`test_intraday_align.py`。模拟盘：`test_papertrade_*.py`。
- 禁止把真人东财 Cookie 写进 fixture。测完 `DATA_PATH` 要 `unlink`。

## 本仓库结构约定

- 取数：命令 → data 服务 → `get_market()` → adapter → 模型 → `render_data` / `render_text` → chart。
- Port 错误是 `str`，禁止再喂 `build_*_render_data`。
- `STOCK_CONFIG`；`papertrade_multi_group` / `papertrade_broadcast_group` 已废弃。
- `stock_agent` 做研究、不执行模拟盘。报价以插件工具为准，禁止用 `web_search` 代替实时价。

## 坑点

完整清单：技能 [§09](.agents/skills/sayustock-development/references/09-developer-pitfalls.md)。

1. `f45` 是最低价；`f170` 才是涨跌幅 %。
2. 热缓存导致 `ai_return` 没跑。
3. 兄弟模块没 import → 命令/工具未注册。
4. pytest rootdir 上浮到 Core → `No module named 'SayuStock'`。
5. 对比图默认 `D1_YEAR`（365 天）。
6. 名称含 `(板块)` 要展开成分股。
7. 行业云图：`chinese_stocks` 申万三级成分 + 大盘 hotmap；禁止每次翻页拉 BK 行情。

## Security notes

- `eastmoney_cookie` 只放运行时配置。
- 模拟盘不是实盘；`stock_agent` 禁止下单。
- 公网 Core：`WS_TOKEN` / WebConsole。
