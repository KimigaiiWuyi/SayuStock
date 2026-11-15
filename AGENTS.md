# AGENTS.md

> 给编码 Agent 的仓库说明；人类文档见 [README.md](./README.md)，业务细节按需读 [`.agents/skills/sayustock-development/SKILL.md`](.agents/skills/sayustock-development/SKILL.md)（勿一次塞入全部 `references/`）。**源码是唯一事实源**。

## Overview & Layout

**GsCore 股票业务插件**（Python `==3.12.*`，版本以 `SayuStock/version.py` 为准）：`Plugins(name="SayuStock", force_prefix=["a", "股票"], allow_empty_prefix=True)`。

- 运行时配置与数据：`{get_res_path()}/SayuStock/config.json` 与 `data/`（不进 git）。
- 核心架构：命令 → `data` 服务 → `get_market()` → `adapters/**` → 领域模型 → `render_data` / `render_text` → `render.render_html`。
- `SayuStock/__init__.py` 显式导入：`stock_agent`、`stock_macro`、`stock_analysis`、`stock_papertrade`、`stock_holdings_analysis`。

| 目录/子包 | 职责 |
|-----------|------|
| `stock_cloudmap/` / `stock_stockinfo/` / `stock_info/` | 云图、分时/K 线/对比、大盘/自选/基金持仓/全天候（Jinja + Core Takumi） |
| `render/` | 共享模板、调色板、SVG 几何及 Core `render_html_to_bytes` 适配 |
| `stock_user/` / `stock_sina/` / `stock_analysis/` / `stock_holdings_analysis/` | 自选（`SsBind`）、估值、技术选股、持仓分析 |
| `stock_ai_func/` / `stock_ai/` / `stock_agent/` / `stock_macro/` | `@ai_tools`、Kronos 预测、`stock_agent` 研究代理、宏观事件表与三档提示词 |
| `stock_papertrade/` | 模拟盘撮合、风控、候选池、策略与自愈 |
| `stock_news/` / `_help/` / `_config/` / `_status/` | 快讯订阅（默认东财→华尔街见闻→新浪→金十）、帮助、配置、状态 |
| `utils/` | `market/`（`MarketDataPort`）、`news/`（`NewsPort`）、`indicators.py`、`render_data.py`、`warn_box.py` |
| `Kronos/` | vendored 模型代码（ruff / pyright 均排除，勿当业务代码修改） |

## Setup & CI Gates

在本插件根目录执行（解释器优先宿主 Core `.venv`，CI 四门全部阻塞合并）：

```sh
uv run ruff check SayuStock test
uv run ruff format --check SayuStock test
uv run pytest test -q
basedpyright --pythonpath <Core venv>/python
```

- 配置：`pyproject.toml`（`testpaths = ["test"]`、`pythonpath = [".", "test"]`）、`ruff.toml`（120 列）、`pyrightconfig.json`（勿写死本机 `venvPath`）。
- 图片统一调用 Core 公开 `render_html_to_bytes`，不另建浏览器或线程渲染链；Playwright 仅用于雪球 Cookie。

## Code Style & Constraints

同步遵循 Core 根 [`AGENTS.md`](../../../AGENTS.md) §1–§4 与 §1.9（`try/except` 仅限不可信外部 JSON 与 `_ai_return_*`；禁 `cast` / `Any` / `type: ignore` / `getattr`·`dict.get` 兜底；`#` 注释 ≤2 行且每行 ≤88 字；函数全标注；`@with_session` + `col()`、无 `__tablename__`；全异步），并叠加本插件红线：

- **行情与快讯**：业务侧只读 `get_market()` 与 `get_news_port()` 模型；禁读东财 `f*`，禁依赖 `utils/market/compat.py`（仅测试）；Port 错误是 `str`，禁传给 `build_*_render_data`；禁止 `web_search` 替代实时价。
- **有图必有文字与显式告警**：`ai_return` / `_emit_ai_text` 必须在缓存命中 `return` 之前；HTML 页取数失败须经 `utils/warn_box.py` 标出并写入 `ai_return`。
- **单源与注册**：指标单源 `utils/indicators.py`；渲染计算单源 `utils/render_data.py`；宏观提示词单源 `stock_macro/prompts.py`；模拟盘单源写 SQLModel（禁第二套账本）；新 `@sv` / `@ai_tools` 须在包 `__init__.py` 显式导入且 docstring 紧贴 `def`。
- **代理边界**：`stock_agent` 仅做研究、严禁下单；宏观事件表全局一张；决策代理 prompt/工具为注册期常量，`prompt_block` 为建树快照。

## Testing & 单元测试添加标准

- **规模上限**：`test/` 总用例严格控制在 **≤ 160 条**，按业务域收敛在现有核心单测文件中（指标、行情路由/解析、文字/分时对齐、渲染运行与实图证据、功能特性、模拟盘核心/策略迁徙）。
- **一次性验证脚本即测即删**：排查 bug、验证单次修复或临时冒烟写的 `test_*.py` / 探针脚本，**测试通过后必须删除，严禁提交入库**。
- **只守核心契约，合并大于新建**：禁止为单个修复单或微调新建独立 `test_*.py`；确有长期回归价值的契约，将关键断言合并进对应的现有核心单测文件，并同步保持精简。
- **环境与门禁约束**：
  - `test/conftest.py` 须同时兼容扁平（无 Core）与嵌套布局，且不触发 `Plugins` 初始化链。
  - `test/test_indicators.py`、`test/test_papertrade_indicators.py`、`test/test_render_text.py` 须保持无 Core 可独立运行（对应 CI `indicators` 任务）。
  - 图表/模板改动须在 `SAYUSTOCK_REQUIRE_RENDER_RUNTIME=1` 下向 `SAYUSTOCK_RENDER_EVIDENCE_DIR` 输出真实 PNG（不以旧渲染器像素完全一致为标准，JPEG 沿用 Core `PicQuality`）。
  - 价列标注用 `Sequence[float | None]`（规避 `list` 不变性报错）；禁把真人 Cookie 写入 fixture；清理测后 `DATA_PATH` 产物。

## Critical Pitfalls

1. **字段与数据口径**：`f45` 是最低价、`f170` 才是涨跌幅 %；对比图默认 `D1_YEAR`（365 天）；名称含 `(板块)` 须展开成分股；行业云图用 `chinese_stocks` 申万三级成分+大盘热图（禁翻页拉 BK 行情）。
2. **缓存与文字**：图片使用独立版本缓存键、不覆盖行情 JSON；热缓存命中仍须先走 `ai_return`。
3. **模拟盘账本与自愈**：
   - 交易日由上证 `1.000001` 分时/日 K 自证；`holiday_heal` 中 `text()` 返回的 datetime 列是字符串，解析失败须跳过并告警（**绝不能兜底 `now()`**）。
   - 持仓与流水必须**双向对账**（补缺仓 + 删幽灵仓），遍历范围须含**只有持仓、零流水**的盘，且逻辑必须排在 `if not dirty` 早退之前。
   - `get_quote` 返回 `None`（如限流 `-400016`）时 `record_trade` 必须**就地 `return` 拒绝入库**并配短 `QUOTE_FAIL_TTL`（兼顾 None 收窄）。
   - `async_maker()` 退出 `with` 不自动提交：所有落库路径确认显式 `commit()`，核验持久化须另开独立连接复查。

## Security

- `eastmoney_cookie` 仅存运行时配置；公网部署 Core 须配 `WS_TOKEN` / WebConsole；模拟盘非实盘。
