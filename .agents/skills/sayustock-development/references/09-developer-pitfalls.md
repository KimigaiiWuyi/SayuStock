# 九、已知坑与开发注意事项

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · **上一章**：[八](./08-testing-and-quality.md) · **下一章**：[十、CI/CD](./10-cicd-and-dev-workflow.md)

**这一章是别人替你踩过的坑。改插件前过一遍能省大量返工。**

## 9.1 绝对红线：行情与字段

### 🔴 业务代码禁止读东财 `f*` / 依赖 compat

- **正确**：`get_market()` → `Quote` / `IntradaySeries` / `KlineSeries` / `BoardSnapshot`  
- **错误**：`raw["data"]["f43"]`、`board_to_em_dict` 再给下游  
- `utils/market/compat.py` **仅测试对照**；`__init__.py` 已不导出  

历史坑：`f45` 是**最低价**不是涨跌幅；`f170` 才是涨跌幅 %。

### 🔴 供应商解析只允许在 adapter 内

feature（`stock_stockinfo` / `stock_ai_func` / …）import `parse_quote_payload` 做业务分支 = 架构回退。  
需要新字段：在 adapter 模型上加语义属性，再在上层读。

### 🔴 不要复活「过渡层」编码

`get_gg` / `get_mtdata` 已返回模型。若有人再改回 EM dict「兼容旧图」，会破坏
`render_data` 类型契约与测试。

## 9.2 有图必有文字（S-1）

部分模型**看不到图**，`ai_return` 的文字是唯一输入。

| 错误 | 后果 |
|------|------|
| 文字写在 `@async_file_cache` 装饰的函数体内 | 热缓存时函数不执行 → AI 零输入 |
| 文字写在 `if file.exists() and fresh: return file` **之后** | 同上 |
| 图上有 MA/MACD，文字只有 OHLC | AI 瞎猜指标 |

**正确**：在 `render_image_file` / `render_html` 里，**取数成功后、缓存判断前** `_emit_ai_text`。  
锁测：`test_ai_text_delivery.py`。

## 9.3 指标口径不要漂（S-2）

- 图与 AI 必须共用 `utils/indicators.py`  
- 勿用 mplchart 默认 MACD/RSI/BOLL 当主读数（柱高度、BOLL 中轨定义不同）  
- 改 `compute_indicators` 必跑 `test_render_text` 一致性用例  

## 9.4 渲染分叉（S-3）

历史上 `stock_stockinfo` 与 `stock_cloudmap` 各拷一份 `render_data` / `fill_kline`，
修复只进一份。现已收敛到 `utils/render_data.py`。

**禁止**再复制一份「临时改法」到子包；只 re-export。

## 9.5 分时跨天时间轴（S-4）

美期 `103.*`、夜盘等：源数据可能只有 `HH:MM`。

- **禁止**按品种中文名特判  
- **禁止**把点按时钟贴到「次日会话模板」导致幽灵日  
- 用 `time_range` + `_resolve_trend_absolute_datetimes` 的绝对时间路径  
- 锁测：`test_intraday_align.py`  

## 9.6 插件加载只扫 `__init__.py`（S-5）

新增 `commands.py` / `ai_tools.py` 后必须在包 `__init__.py` **显式 import**，否则：

- 命令「没反应」  
- AI 工具「注册表里没有」  

`stock_papertrade/__init__.py` 注释已写明，照做。

## 9.7 AI 工具 docstring 位置（S-6）

```python
@ai_tools()
async def foo(...):
    """正确：紧贴 def。"""
    logger.info("...")

@ai_tools()
async def bar(...):
    logger.info("...")
    """错误：这只是表达式，__doc__ 为 None，永远召不回。"""
```

## 9.8 模拟盘双账本（S-7） / 流水与持仓分裂

2026-07 事故：研究代理用 `record_*` / `state_set` 记账，SQLModel 另一套，主人格查持仓读错。

2026-08-25 事故：决策代理把 `papertrade_trade_insert` 与 `papertrade_position_upsert`
**并行**调用。止损闸拒绝流水（JSON 用了 `stop_pct` 且尾部写坏），upsert 仍建仓 →
持仓有中际旭创、流水没有、现金未扣、群不播报、收盘净值虚高约 8.5 万。

- 模拟盘只写 papertrade 表  
- `stock_agent` 不承接模拟盘执行  
- 建账确认文案不写死可变数字（artifact 永久化）  
- **成交只走 `trade_insert`**：同一 session 写流水+现金+持仓；`position_upsert` 只刷新报价  
- sell 现金是 `amount - fee`，**不要**再加 `realized_pnl`（否则盈亏记两遍）  

## 9.9 缓存污染与测试（S-8）

- 测试若写 `DATA_PATH`，用完 `unlink`  
- 真网 Cookie 不要写进 fixture 仓库  
- `mapcloud_refresh_minutes=3`：调试「图不更新」先查 mtime 缓存  

## 9.10 Playwright / 出图环境（S-9）

- 未 `playwright install` → 云图截图卡住  
- matplotlib 必须 `Agg`（`chart_base` 已设）；勿在其它入口改 interactive backend  
- 重 CPU 绘图放 `asyncio.to_thread`，勿堵事件循环  
- 新版 mplchart（无 `bgcolor`）：默认浅色；数据 pane 透明，底色在 `label=root`。`Chart(bgcolor=)` 由 `mplchart_compat` 转成暗色 `style`，出图前再刷 root。  
- 新版忽略 `color_scheme`：BOLL 颜色必须 `Bands(..., color=)`，不能再靠 scheme 键。  
- matplotlib 不要把 `MiSans VF` 放 `font.sans-serif` 第一位：VF 只注册 Regular，`fontweight` 不会换档。优先系统静态 `MiSans`（Thin–Heavy）。  

Now write a test and run a quick smoke if possible. pandas might be missing from venv still.

I'll write a test that:
1. Imports Chart from compat
2. Creates a tiny prices df
3. Checks root facecolor is dark

If mplchart/pandas missing, skip.

Also verify kline import order after adding _paint_chart_background - ruff isort.

And verify chart_kline has _paint_chart_background imported - I added it.

Check if kline Chart() now gets style from bgcolor via compat - yes automatically.

Write test/test_mplchart_background.py

## 9.11 板块 vs 个股（S-10）

用户输入「证券」可能是板块：

- quote 名称含 `(板块)` → 展开成分股 multi 分时  
- `_is_sector` 曾缺失导致 AttributeError；现以名称语义判断  
- 对比/展开 limit≈13，改数量需评估图密度与 API 压力  

**行业云图不要按 hotmap 的一级名过滤，也不要每次 `board(BK)` 翻页拉行情。**
东财 `sidemenu` 行业是申万三级扁平 BK（`水泥` flag=2，`水泥制造` flag=3）。
流程：菜单命中名称 → 成分代码（`chinese_stocks` 申万一/二/三级；缺表才 BK 名单 **30 天**缓存）→
与大盘 `hotmap()`（5 分钟缓存）按代码求交再出图。匹配逻辑在 `utils/sector_resolve.py`。
刷新主数据：在 Core 根执行 `uv run python gsuid_core/plugins/SayuStock/SayuStock/utils/update_stocks.py --diff`。  

## 9.12 错误返回类型（S-11）

Port / data 服务：`str` 表示错误，模型表示成功。

```python
if isinstance(raw_data, str):
    return raw_data
if not isinstance(raw_data, KlineSeries):
    return ErroText["notData"]
```

勿对错误字符串再 `build_*_render_data`。

## 9.13 东财传输层边界（S-12）

`EASTMONEY_REQUESTER` 仍存在：

- **允许**：adapter、`universe.fetch_clist` 类自定义 fs、底层 request  
- **禁止**：在命令里 `stock_request` 后直接读 `diff[i]["f3"]`  

新列表需求优先 `port.board` / 扩展 Port。

## 9.14 前缀与空命令（S-13）

- 插件前缀 `a` / `股票`  
- `个股` 无参数要友好提示  
- `to_ai` 与命令实际参数格式保持一致，否则 Agent 传错  

## 9.15 Kronos / 重依赖（S-14）

- torch / 权重仅预测路径惰性 import  
- 队列 `NOW_QUEUE` 防并发打爆  
- 单测必须 mock 模型与 playwright  

## 9.16 CI / 测试导入（S-15）

详细流程与对照表见 [十、CI/CD](./10-cicd-and-dev-workflow.md)。这里只记红线：

| 坑 | 表现 | 处理 |
|----|------|------|
| 单测执行 `SayuStock/__init__.py` | 轻量 CI：`No module named 'gsuid_core'` | `test/conftest.py` 包壳；勿在 indicators 测里硬 import 包初始化 |
| 无 `[tool.pytest.ini_options]` | Full suite：`No module named 'SayuStock'` | 保留 `pythonpath = [".", "test"]`，防止 rootdir 上浮到 Core |
| `pyrightconfig` 写死 `.venv` | basedpyright exit 3，「0 errors」仍失败 | 删除 `venvPath`/`venv` |
| 用官方 pyright | unknown diagnostic rule | 统一 **basedpyright** |
| 脚本 E402 | pre-commit / lint 红 | 路径补丁后的 import 加 `# noqa: E402` |
| 对比图默认窗口 | 用户觉得「只有一个月」 | 对比默认 `KlinePeriod.D1_YEAR`（365 天），勿改回 `D1_RECENT`（50 天） |
| 场外基金当 K 线 | `个股 日k 720001` 只有一根柱 | 东财 `150.*` 无真 K 线；走天天基金净值，命令层改 `compare-stock` |

## 9.17 改完自查清单

1. 新代码是否只通过 `get_market()` / 领域模型取数？  
2. 是否未 import `compat` / 未解析 `f*`？  
3. 出图路径是否在缓存前 `ai_return`？  
4. 指标是否走 `utils/indicators`？  
5. 新模块是否被 `__init__.py` import？  
6. `@ai_tools` docstring 是否紧贴 def？  
7. 模拟盘是否只写 SQLModel？  
8. ruff / **basedpyright** / 相关 pytest 是否绿？  
9. 新测试是否在**扁平（无 Core）与嵌套**下都能 collection？（至少本地跑一遍 CI indicators 三件套 + `pytest test/`）  
10. 是否更新了本 SKILL 对应章节（若改了不变量 / CI 约定）？  

## 9.18 历史问题速查

| ID | 主题 | 详见 |
|----|------|------|
| S-1 | 热缓存丢 AI 文字 | §9.2 / [§04](./04-render-pipeline.md) |
| S-2 | 指标口径与图不一致 | §9.3 |
| S-3 | render 双份分叉 | §9.4 |
| S-4 | 跨天分时甩日 | §9.5 |
| S-5 | 装饰器未 import | §9.6 |
| S-6 | 工具无 docstring | §9.7 / [§05](./05-ai-tools-and-agents.md) |
| S-7 | 模拟盘双账本 / 流水与持仓分裂 / artifact 脏数字 | §9.8 / [§06](./06-papertrade.md) |
| S-8 | 缓存与测试污染 | §9.9 |
| S-9 | playwright / Agg | §9.10 |
| S-10 | 板块展开 | §9.11 |
| S-11 | str 错误当数据 | §9.12 |
| S-12 | 业务直读 requester | §9.13 |
| S-15 | CI 导入 / pytest rootdir / pyright venv | §9.16 / [§10](./10-cicd-and-dev-workflow.md) |
| S-16 | 全天候开盘灯 | `is_market_active_now` 为 `[start, end)`，收盘整点灭灯；港股持续交易 **16:00** 收（不是 15:30）；日股 BJT 08:00-10:30 / 11:30-14:30；时钟必须 `now_bjt()`（Asia/Shanghai），禁止 `datetime.now()` |
| C-1…C-8 | CI 事故速查表 | [§10.8](./10-cicd-and-dev-workflow.md) |
| M-1 | 领域模型迁移完成 | [§03](./03-market-data-port.md) |

## 9.19 与 GsCore 坑的交叉

以下问题出在框架，但会表现为「股票插件怪」：

- 工具召不回：嵌入模型语言不匹配 / 族展开赢家通吃 → 见 gscore-development §12.22e  
- 私聊记忆 scope：`group_id or user_id` → §12.22f  
- 输出闸误杀 → §12.22  

排查「AI 调错工具 / 不调股票工具」时，先确认 Core 工具注册与嵌入配置，再查本插件 docstring。
