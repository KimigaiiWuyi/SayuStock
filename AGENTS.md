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
- 版本：`SayuStock/version.py`（当前 `0.9`）。`pyproject.toml` 的 `[project]` / poetry 版本可能不一致。Python `==3.12.*`。

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
    ├── __init__.py                     # Plugins + 显式 import 五包
    ├── __full__.py / version.py
    ├── stock_*/                        # 功能子包（下表）
    ├── skills/                         # 运行时 AI Skill（ai_skill 注册；非 .agents/skills 开发文档）
    ├── utils/                          # market / news / render_data / indicators / db
    ├── Kronos/                         # vendored，pyright exclude
    └── tools/gen_A.py
```

内层 `__init__.py` 显式 import：`stock_agent`、`stock_macro`、`stock_analysis`、`stock_papertrade`、`stock_holdings_analysis`。

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
| `stock_macro/` | 宏观事件表 `SayuMacroEvent` + 宏观三问/三档提示词 + 知识库/技能注册 + 定时联网复核 |
| `stock_papertrade/` | 模拟盘 SQLModel |
| `stock_news/` / `_help/` / `_config/` / `_status/` | 财经快讯订阅（任务名仍是「雪球新闻订阅」）、帮助、配置、状态 |
| `utils/market/` | MarketDataPort + eastmoney / okx / vix |
| `utils/news/` | NewsPort；默认东财 → 华尔街见闻 → 新浪 → 金十，失败或空列表顺延 |

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
basedpyright --pythonpath <Core venv>/python
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
- 快讯只走 `get_news_port()`；供应商字段只在 `utils/news/adapters/`。
- 模拟盘只写 SQLModel，禁止 `record_*` / `state_set` 第二套账本。
- 宏观提示词单源 `stock_macro/prompts.py`（决策 / 持仓 / 研究代理共用）；宏观事件表全局一张，不按盘分区。
- 决策代理 prompt / 工具清单是注册期常量（重启对所有老盘生效）；`prompt_block` 是建树快照（重建心跳树才更新）。
- 新 `@sv` / `@ai_tools` 必须在包 `__init__.py` 显式 import。
- `@ai_tools` docstring 紧贴 `def`。

## Testing

- 扁平（无 Core）与嵌套（`plugins/SayuStock`）都要能 collection。
- `test/conftest.py` 包壳：不要执行 `SayuStock/__init__.py` 的 Plugins 链。
- 改 `ai_return`：`test_ai_text_delivery.py`。分时：`test_intraday_align.py`。模拟盘：`test_papertrade_*.py`。
- 改 `chart_base` 的 compat 导入：`test_end_label_dodge.py` 用假包加载该文件，桩必须能解析每一个 from-import。
- CI 四门全挡合并：ruff、indicators、full pytest、**basedpyright**。本地类型检查：`basedpyright --pythonpath <Core venv>/python`。
- `list` 不变：`list[float]` 不能当 `list[float | None]` 传入；价列用 `Sequence[float | None]`。
- 禁止把真人东财 Cookie 写进 fixture。测完 `DATA_PATH` 要 `unlink`。
- `test_offline_card_render` 是本地出图冒烟：空 `DATA_PATH`（`resource_path` 会 mkdir）不算有缓存，CI 应 skip。

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
8. importlib 桩漏 `mplchart_compat` 导出名 → Full suite collection `ImportError`。
9. 价序列类型用 `Sequence`，不要让 `list[float]` 与 `list[float | None]` 互赋。
10. 离线出图测：空 `DATA_PATH` 不是缓存；要有 `*_single-stock*_data.json`。
11. 模拟盘交易日**自证**（上证 `1.000001` 分时/日 K），别再维护人工假期表——旧表只到 2026-02 就过期，长假照常撮合写脏流水；`holiday_heal` 会自动清。`text()` 查询回来的 datetime 列是**字符串**，解析失败要跳过并告警，**兜底 `now()` 会把整个账本当脏数据删光**。
12. 持仓要跟流水**双向**对账，且**不许挂在"有没有脏成交"的早退后面**。2026-10-01 美的集团就是反方向损坏：卖出播报了、钱少了、库无流水——旧代码让 LLM 直接 `position_upsert(qty=0)` 删掉持仓行，`trade_insert` 因持仓不足被拒，于是持仓没了、钱没进、流水没有。只有"流水有 → 补持仓"和"持仓有 → 删幽灵仓"都做，且遍历范围要含**只有持仓、零流水**的盘（只遍历 `by_acc` 会整类漏掉），才算自愈。
13. 东财限流（`-400016`）时 `get_quote` 返回 `None`，**不许当成"跳过校验"放行**。`record_trade` 的偏差校验早先是 `_live is not None and ...`——限流恰好落进"跳过"分支，等于风控期完全失守，任意价格都能落库。拿不到实时价必须**拒绝入库**。且失败缓存 TTL 要短（`QUOTE_FAIL_TTL`，别沿用 60s 成功 TTL），否则一次限流把该票锁死一分钟。注意：拒绝要**就地 return**，不能先把判断结果存进 flag 再在下一段用，否则 `_live` 丢 None 收窄，basedpyright 会报算术运算符类型错。
14. `async_maker()` 是**普通 AsyncSession，退出 `with` 不自动提交**（实测：不 commit 时 INSERT 离开会话后查不到）。`holiday_heal` 把持仓写入放在「无脏成交」的早退分支里，所以**每条 return 前都要确认该 commit 的 commit 了**，否则"重启即自动修复"只是看起来成立：日志写着已对账，库里什么都没有。验证这类"以为落库了"的改动必须**另开独立连接复查**——同一个 session 会命中未提交事务，骗过自己。
15. 出图验收别拿精确色扫成品：每条渲染路径最后都由 `convert_img` 统一转 **JPEG**（`img.convert("RGB")` + `quality=pic_quality`，全插件 8 个文件 24 处），有损量化会把 `(187,26,26)` 写成 `(186,26,26)`——在成品图上按精确色找柱会一个像素都搜不到，看着像「柱子全丢了」，其实是探针在自己的收尾步骤上假阴性。像素级验收要么在 `convert_img` **之前**接住画布，要么按容差匹配。

## Security notes

- `eastmoney_cookie` 只放运行时配置。
- 模拟盘不是实盘；`stock_agent` 禁止下单。
- 公网 Core：`WS_TOKEN` / WebConsole。
