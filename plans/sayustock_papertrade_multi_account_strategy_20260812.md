# SayuStock 模拟盘改造方案：多盘 · 多策略 · DB 多群推送

| 项 | 内容 |
|----|------|
| 状态 | 方案待确认 / 未开工 |
| 日期 | 2026-08-12 |
| 文档路径 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\plans\sayustock_papertrade_multi_account_strategy_20260812.md` |
| 插件根目录 | `F:\gsuid_core\gsuid_core\plugins\SayuStock` |
| 业务代码根 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock` |
| 框架根目录 | `F:\gsuid_core` |
| 范围 | papertrade 全链路：模型 / Repo / scope / 命令 / ai_tools / agent / Kanban / 播报 / 策略 / 测试 / 文档 |
| 规范（框架） | `F:\gsuid_core\docs\skills\gscore-plugin-development\` |
| 规范（插件） | `F:\gsuid_core\gsuid_core\plugins\SayuStock\docs\skills\sayustock-development\` |
| LLM 红线 | `F:\gsuid_core\docs\LLM.md` |

> **路径约定**：本文档内凡涉及仓库文件/目录，一律写 **Windows 绝对路径**（与当前工作区一致）。引用章节用相对锚点（`#...`）仅用于本文内跳转。

---

## 目录

1. [文档目的与读者](#1-文档目的与读者)
2. [需求原文归纳](#2-需求原文归纳)
3. [历史演进与为何变成今天这样](#3-历史演进与为何变成今天这样)
4. [现状全景（改造基线）](#4-现状全景改造基线)
5. [目标、非目标与成功标准](#5-目标非目标与成功标准)
6. [领域模型重写](#6-领域模型重写)
7. [数据库设计与迁移（极细）](#7-数据库设计与迁移极细)
8. [作用域解析层重写](#8-作用域解析层重写)
9. [策略框架与两套预设](#9-策略框架与两套预设)
10. [推送与播报](#10-推送与播报)
11. [命令与产品交互](#11-命令与产品交互)
12. [AI 工具清单与改造点](#12-ai-工具清单与改造点)
13. [Agent / Kanban / 心跳](#13-agent--kanban--心跳)
14. [模块级改动地图（文件绝对路径）](#14-模块级改动地图文件绝对路径)
15. [实施顺序与依赖](#15-实施顺序与依赖)
16. [测试矩阵与质量门](#16-测试矩阵与质量门)
17. [风险、默认决策、踩坑与反模式](#17-风险默认决策踩坑与反模式)
18. [验收标准 DoD](#18-验收标准-dod)
19. [附录 A：绝对路径总表](#19-附录-a绝对路径总表)
20. [附录 B：关键代码行为摘录](#20-附录-b关键代码行为摘录)
21. [附录 C：变更记录](#21-附录-c变更记录)

---

## 1. 文档目的与读者

### 1.1 目的

把「多模拟盘 + 命名 + 多策略 + 推送入库多群」从一次口头需求，固化为**可实施、可评审、可验收**的工程方案。实现阶段应以此文档为单一事实来源（SSOT）；若实现偏离，先改文档再改代码。

### 1.2 读者

| 角色 | 关注章节 |
|------|----------|
| 产品/需求方 | §2、§5、§11、§18 |
| 实现者 | §4、§6–§15、§17、§19–§20 |
| Reviewer | §7 迁移、§12–§13 鉴权、§16–§17 红线 |
| 运维/部署 | §7.4 迁移、§10 推送、§17 默认决策 A/I |

### 1.3 不在本文展开的内容

- GsCore 通用插件脚手架（见 `F:\gsuid_core\docs\skills\gscore-plugin-development\SKILL.md`）
- Kronos / 大盘云图 / 个股 K 线等非 papertrade 子系统
- 真·证券交易通道

---

## 2. 需求原文归纳

用户提出的改造诉求（意译并结构化，保留原意）：

| # | 诉求 | 工程解读 |
|---|------|----------|
| R1 | 一个群也可以创建不同的模拟盘 | 解除「一群至多一盘 / 全服一盘」的硬限制 |
| R2 | 模拟盘不再有「群」的概念，但需要有多个 | 业务主键从群变为**命名账户实体**；群只作入口/推送目标 |
| R3 | 不同策略可以去模拟 | 每盘绑定 `strategy_id`；策略可插拔 |
| R4 | 创建的不同模拟盘可以起名字，方便搜索查询 | `name` 唯一 + list/模糊查 |
| R5 | 顾及历史数据；历史全群范围只有一个盘；旧的命名为「默认模拟盘」 | 迁移策略：最早账户 → 默认模拟盘；不静默合并多行资金 |
| R6 | 拟定不同预设策略；旧的是多「面」评估买卖点 | 保留 multi_factor |
| R7 | 全新策略：底部放量买入、顶部放量卖出 | 新增 volume_extremum + 指标/工具 |
| R8 | 可能需要新 ai_tools；整体框架结构变化 | 工具签名、agent、Kanban scope 全面 account 化 |
| R9 | 每日播报应带模拟盘名字以便分辨 | 成交/结构化/复盘/出图统一带 name |
| R10 | 推送不拘泥于 config，写入数据库，支持多群列表遍历 | BroadcastTarget 表 + for 循环 |
| R11 | 遵守 gscore 插件规范（尤其数据库与代码规范） | §5 DB / §17 红线 |
| R12 | 写完自测；ruff / pyright / LLM.md / CI/CD 全绿 | §16 |
| R13 | 先方案后代码 | 本文；未开工 |

---

## 3. 历史演进与为何变成今天这样

理解「为什么现在是 `(group_id, bot_id)` + shared_mode」有助于避免改错方向。

### 3.1 第一阶段：一群一盘

最初模型把模拟盘当成**群资产**：每个 `(group_id, bot_id)` 一行账户，子表全部用同样的二元组分区。这与「在哪个群初始化就在哪个群看」的直觉一致。

**残留证据**：

- 表唯一约束：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\utils\database\papertrade_models.py` 中 `UniqueConstraint("group_id", "bot_id", name="ux_sayupaperaccount_gid_bid")`
- 命令 `send_query_group`、排行 `cross_group.py` 仍按「群」查盘
- 用户文档仍写「每群每 bot」类表述：`F:\gsuid_core\gsuid_core\plugins\SayuStock\docs\papertrade.md`

### 3.2 第二阶段：默认全服共用一盘（当前默认）

产品上修正为：模拟盘是 **AI 自己经营的账户**，不是某群的财产。于是引入：

- 配置 `papertrade_multi_group`（默认 `False` = 共用）
- `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\account_scope.py` 中 `home_account_key()`：钉死**库内最早创建**的账户，且**故意不随配置漂移**（防止改一次配置就指向空分区，现金/持仓「消失」）
- 共用模式下任意群可查同一盘；**禁止第二群再 init**（防两棵 Kanban 决策树并发写同一账本把持仓写坏——见 `commands.send_init_command` 注释）
- 播报改向：`papertrade_broadcast_group` 单群字符串

配置定义位置：

`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_config\config_default.py`

### 3.3 运行中踩过的坑（改造时必须保留的「教训」）

这些不是考古，而是**改造后仍要满足的不变量**：

| 时间/主题 | 问题 | 现修复位置 | 改造后注意 |
|-----------|------|------------|------------|
| 2026-07-01 | 只建了 DB 账户没挂 Kanban，开盘 AI 不决策 | `commands.send_init_command` 6 步 | 每盘仍必须挂自己的 init+period 树 |
| 2026-07-01 | period ROOT 误带 recurring_trigger 导致子任务永不 arm | `commands._setup_papertrade_kanban_trees` 注释 | 新盘建树复制同一约束 |
| 2026-07-01 | total_equity 只报 cash | account_query 含持仓市值 | 多盘各自 equity 独立算 |
| 2026-07-02 | 候选池「&lt;N 才刷」导致锚定不换股 | `candidate_pool` + decision prompt Phase 0 | 每盘每轮仍必 refresh |
| 2026-07-02 | 建账 agent 写死「0 持仓」进 artifact，主人格长期误报空仓 | `stock_agent` PAPERTRADE_SETUP_PROMPT | 确认文案禁写瞬时数字 |
| 2026-07-02 | 主人格工具召回不到 papertrade 读工具 | `ai_tools._PAPERTRADE_CTX_TAGS` | 多盘工具 docstring/covers 要带盘名语义 |
| 2026-07-06 | 死 Kanban 树仍挂在账户上，init 幂等误判「已开户」 | `send_init_command` need_rebuild | 按 account 检查 period root 状态 |
| 写入鉴权 | 仅靠 visible_when 不够，用户可委派 profile | `account_scope.deny_write_reason` | 改为按 account 的 kanban root 比对 |

### 3.4 为何第三阶段必须「去群主键」

共用模式解决了「一盘被群绑定」的一半问题，但：

1. **不能一群多盘**（无法同时跑「多维评分盘」和「放量盘」对照）；
2. **不能命名搜索**（只有 gid 或「唯一那个」）；
3. **策略写死一套**；
4. **推送仍是 config 单群**，多群旁观/多运营群无法配置化；
5. 多群模式与共用模式是 **配置开关二选一**，不是「多命名实体」。

用户 R1–R10 要求的是**命名实体 × 多策略 × 多推送目标**，必须进入第三阶段。

---

## 4. 现状全景（改造基线）

### 4.1 架构总览

```
用户命令 / AI 主人格
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ stock_papertrade                                          │
│  commands / admin  ──► account_scope.resolve_account_key  │
│  ai_tools          ──► (group_id, bot_id) ──► db.Repo     │
│  candidate_pool / strategy / matcher / quote_service      │
│  proactive / render                                       │
└───────────────────────────────────────────────────────────┘
        │                              │
        ▼                              ▼
 SQLModel 7 表                    Kanban (ai_core)
 papertrade_models.py             create_kanban_tree
        │                         period 子任务 cron
        ▼                              │
 WebConsole Admin                      ▼
                                  papertrade_*_agent
                                  (stock_agent 注册)
```

### 4.2 账户与分区（现状）

| 概念 | 实现 | 文件 |
|------|------|------|
| 账户唯一键 | `(group_id, bot_id)` | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\utils\database\papertrade_models.py` |
| 共用模式 | `home_account_key()` = 最早账户 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\account_scope.py` |
| 多群模式 | 会话 `ev.group_id + ev.bot_id` | 同上 |
| 子表分区 | 全部带 `group_id` + `bot_id` | 同 models 文件 |

### 4.3 数据表（现状 7 张）

定义文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\utils\database\papertrade_models.py`  
导入副作用：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\utils\database\models.py`（import papertrade 表以便 create_all / Admin）

| 类名 | 用途 | 关键字段（现状） |
|------|------|------------------|
| `SayuPaperAccount` | 账户 | cash, mode, frequency_minutes, enabled, kanban_*_root_id, group_id, bot_id |
| `SayuPaperPosition` | 持仓 | stock_code, qty, avg_cost, last_quote_price, last_quote_at |
| `SayuPaperTrade` | 成交流水 append-only | side, price, qty, fee, realized_pnl, reason, snapshot |
| `SayuPaperDecision` | 决策日志 append-only | action, score, reason, indicators, blocked_by |
| `SayuPaperSnapshot` | 日净值 | trade_date, total_equity, day_pnl, total_pnl |
| `SayuPaperWatchlist` | 群友关注（用户侧已弱化） | user_id, stock_code |
| `SayuPaperAgentPool` | AI 候选池 | expires_at, priority, added_by |

迁移片段（exec_list）同文件末尾：清理重复账户、唯一索引、position 报价列等。

### 4.4 Repo 层（现状）

文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\db.py`

| Repo | 代表方法 | 分区参数 |
|------|----------|----------|
| `PaperAccountRepo` | get, get_or_create, update, get_earliest, list_enabled, bind_kanban_*, reset_account | group_id, bot_id |
| `PaperPositionRepo` | get, list_by_account, upsert, bulk_set_quote | 同上 |
| `PaperTradeRepo` | insert, list_by_account, aggregate_pnl | 同上 |
| `PaperDecisionRepo` | insert, list_recent | 同上 |
| `PaperSnapshotRepo` | upsert, list_range | 同上 |
| `PaperAgentPoolRepo` / Watchlist | list / add / cleanup | 同上 |

`reset_account` 按 gid+bid **级联删除** 6 类子数据 + 账户本身——多盘后必须改为 `account_id`，否则误伤。

### 4.5 作用域层（现状 API）

文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\account_scope.py`

| 函数 | 职责 |
|------|------|
| `is_shared_mode()` | 读 config multi_group |
| `broadcast_group_override()` | 读 config 播报群 |
| `invalidate_home_cache()` | 清盘/新开后清缓存 |
| `home_account_key()` | 共用模式钉死键 |
| `resolve_account_key(ev)` | 会话 → (gid, bid) |
| `is_home_context(ev)` | 是否开户原群 |
| `scope_note_for_llm` / `not_opened_message` | 防 LLM 误报未开户 |
| `broadcast_event(ev)` | msgspec 替换 group_id |
| `grant_write()` / `deny_write_reason` | 写入 contextvar + Kanban 校验 |

### 4.6 用户命令（现状）

文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\commands.py`  
SV：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\sv.py`（`sv_papertrade` pm=3，`sv_papertrade_admin` pm=0）

| 处理函数 | 触发 | 权限 |
|----------|------|------|
| `send_init_command` | 模拟盘/AI操盘 初始化 | 管理 |
| `send_view` | 查看 | 任意 |
| `send_holdings` | 自选/持仓图 | 任意 |
| `send_pnl` | 收益 日/周/… | 任意 |
| `send_records` | 记录 | 任意 |
| `send_leaderboard` | 排行 | 管理 |
| `send_query_group` | 查询 &lt;group_id&gt; | 管理 |

Admin：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\admin.py`

| 函数 | 作用 |
|------|------|
| `send_clear_all` | 清盘（master） |
| `send_dry_run` | 压测多段决策 + 播报 |

### 4.7 初始化 6 步（现状，必须按盘复刻）

实现：`send_init_command` + `_setup_papertrade_kanban_trees`

1. `check_admin`
2. `PaperAccountRepo.get_or_create`
3. Kanban init 树（`papertrade_setup_agent`）
4. Kanban period 树（4 子任务：decision / snapshot / monthly_report / pool_refresh）
5. 子任务 arm 到 APScheduler（ROOT **不**带 recurring_trigger）
6. bind kanban root_id 回账户  
7.（附加）`_kick_after_kanban_ready`：kick init；开盘时 fire-and-forget 一次 decision

Kanban `scope_key` 现状：

- `papertrade_init_{group_id}_{bot_id}`
- `papertrade_period_{group_id}_{bot_id}`

period 子任务 cron（现状）：

| 子任务 | profile | cron |
|--------|---------|------|
| 决策 | `papertrade_decision_agent` | `0,30 9-11,13-15 * * 1-5` |
| 快照 | `papertrade_snapshot_agent` | `5 15 * * 1-5` |
| 月报 | `papertrade_reporter_agent` | `0 9 1 * *` |
| 池轮换 | `papertrade_pool_refresh_agent` | `15 10,14 * * 1-5` |

Recurring gate 注册：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\__init__.py` 中 `_register_recurring_gates`  
日历：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\trading_calendar.py`

### 4.8 AI 工具（现状清单）

文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\ai_tools.py`

文档头注释写明：**建账户 tool 已删除**，唯一入口是 trigger `send_init_command`。

**只读 common（主 persona 可用）**

| 工具 | 作用 |
|------|------|
| `papertrade_account_query` | 账户 + equity |
| `papertrade_position_list` | 持仓 enriched |
| `papertrade_holdings_image` | 持仓简图 |
| `papertrade_trade_list` | 流水 |
| `papertrade_decision_list` | 决策 |
| `papertrade_watchlist_list` | 关注列表 |
| `papertrade_agent_pool_list` | 候选池 |

**写工具（visible_when 仅 papertrade agent + `_deny_write`）**

| 工具 | 作用 |
|------|------|
| `papertrade_decision_insert` | 写决策；触发 pool 反馈 |
| `papertrade_trade_insert` | 写流水；**成功则 `_broadcast_fill`** |
| `papertrade_position_upsert` | 更新持仓 |
| `papertrade_candidate_refresh` | 轮换池 |
| `papertrade_match_order` | 撮合试算/执行辅助 |
| `papertrade_snapshot_write` | 收盘净值 |

**辅助**

| 工具 | 作用 |
|------|------|
| `stock_financials` | 财报 |
| `stock_indicators` | 多周期指标 |
| `stock_is_trading_day` | 交易日/时段守卫 |

`_resolve_scope`：共用模式忽略入参 group_id，钉死 home；多群模式用入参或 ev。

### 4.9 策略与撮合（现状）

| 模块 | 绝对路径 | 内容 |
|------|----------|------|
| 策略 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\strategy.py` | `score_stock` / `decide_action` / `apply_risk_check` / `MODE_RULES` / `MODE_THRESHOLDS` / `indicators_have_entry_stop` |
| 指标封装 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\indicators.py` | 对接通用指标，含 volume_ratio 等 |
| 撮合 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\matcher.py` | 整手、佣金、印花税、涨跌停 |
| 执行器 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\trade_executor.py` | 执行后端抽象 |
| 报价 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\quote_service.py` | 批量报价 |
| 候选池 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\candidate_pool.py` | 多源轮换、过热过滤、决策反馈改池 |

**评分权重（用户文档与代码一致）**：技术 40% / 基本面 30% / 舆情 15% / 波动调节 ±15%。  
量比 &gt; 2 目前只是 multi_factor 里 **+0.05 的一小项**，不是独立「底/顶放量」策略。

#### 4.9.1 ⚠️ 代码级核验：`strategy.py` 不在生产路径（实现前必读）

全库 ripgrep 结果（2026-08-12 核验）：

| 符号 | 生产调用点 | 测试调用点 |
|------|-----------|-----------|
| `score_stock` | **0** | `test_papertrade_strategy.py` |
| `decide_action` | **0** | 同上 |
| `apply_risk_check` | **0** | 同上 |
| `MODE_RULES` / `MODE_THRESHOLDS` | **0**（仅 `strategy.py` 内部自用） | 同上 |
| `indicators_have_entry_stop` | **2**：`ai_tools.py` 第 869 行（decision_insert）、第 1014 行（trade_insert） | 同上 |

即：**真实买卖决策 100% 由 `PAPERTRADE_DECISION_PROMPT` 驱动**。该 prompt 第 517 行原文写着「无独立 score_stock 工具可调；把分项写进 decision reason」——四维加权是 LLM 在 prompt 指导下"在脑中"完成的。`MODE_RULES` 那张风控矩阵同样以**散文形式**复写在 prompt 里（如 balanced 止损 −8%），运行时**没有任何代码**去读 `MODE_RULES[mode]`。

**对本方案的影响**：

1. §9.3「multi_factor 现有完整迁入」在语义上是**把参考实现挪个位置**，不是"把生产逻辑模块化"。
2. 若 `volume_extremum` 只实现 `score/decide`，线上行为**一点不会变**——DoD 第 5 条可以全绿而策略并未真正生效。
3. 因此必须先定义**策略生效点**（见新增 §9.0），再写策略代码。

#### 4.9.2 其它代码级核验结论

| 事实 | 证据 | 影响 |
|------|------|------|
| `db.py` 共 1332 行 / 42 个 Repo 方法，其中 **37 个**首参为 `(group_id, bot_id)` | `db.py` | Phase 0 工作量基线 |
| 生产侧 Repo/scope 调用点约 **123 处**，分布 9 个文件（`ai_tools` 34 / `admin` 29 / `commands` 29 / `render` 8 / `proactive` 7 / `candidate_pool` 5 / `cross_group` 5 / `trade_executor` 3 / `account_scope` 3） | ripgrep | §15 Phase 排序依据；先改 `db.py` 再改外层（§17.2-20） |
| `cross_group.py` **全文 151 行都是按群设计**，含两个私有 helper 直接 `WHERE group_id=?` | `cross_group.py` 第 107–134 行 | §14 里的「中」应按**重写**估工，不是改签名 |
| OHLCV 成交量/高低点**已可获取** | `MarketDataPort.kline()` → `Bar.volume/high/low`；`kline_to_df` 输出含 `volume` 列 | volume 策略**不需要新数据管道**，只需新增指标函数 |
| `utils/indicators.py` 已有 `volume_ratio(period=5)` / `support_resistance(period=20)` / `swing_points` | 同文件 | 可复用，但窗口是写死的 5 / 20，需要参数化到 N/M |
| 框架启动钩子顺序：`-100` import models → `-90` `create_all` → `-80` `trans_adapter`（跑 `exec_list`） | `gsuid_core/utils/database/startup.py` 第 173/186/191 行 | 迁移钩子 priority 必须 **> −80**（见 §7.4.5） |
| `trans_adapter` 对每条 `exec_list` SQL 是 `except: pass` | 同文件第 194–199 行 | **ALTER 失败是无声的**；backfill 必须自己探测列（见 §7.4.6） |
| `emit_proactive_message` 用 `event.WS_BOT_ID` 找连接，找不到就 `next(iter(gss.active_bot.values()))` | `gsuid_core/ai_core/proactive/emitter.py` 第 44–57 行 | BroadcastTarget 必须存 `WS_BOT_ID`（见 §7.3） |
| 群会话 `Event.session_id` = `{WS_BOT_ID}:{bot_id}:{bot_self_id}:group:{group_id}` | `gsuid_core/models.py` 第 122–135 行 | 缺字段会拼出对不上的假 session（见 §7.3 / §10.1） |

**风控 mode（与 strategy_id 正交，改造后保留）**：

| 规则 | balanced | aggressive | conservative |
|------|----------|------------|--------------|
| 单票仓位 | 25% | 40% | 15% |
| 日交易次数 | 6 | 12 | 3 |
| 止损 | -8% | -12% | -5% |
| 总回撤 | -20% | -30% | -12% |
| 现金缓冲 | 5% | 0% | 15% |
| 最大持仓数 | 8 | 12 | 5 |

### 4.10 播报路径（现状）

| 路径 | 行为 | 文件 |
|------|------|------|
| 成交冒泡 | trade_insert → `_broadcast_fill` → `broadcast_event` → `emit_proactive_message` 一行 🟢/🔴 | `ai_tools.py` |
| 结构化播报 | `build_papertrade_proactive_text`（dry_run / 历史 init 类） | `proactive.py` |
| Kanban persona 转译 | cron 后 `_persona_relay`（与结构化 builder 分离） | 框架侧 + agent 最终 `<<NO_BROADCAST>>` |
| 配置改向 | 仅单群 `papertrade_broadcast_group` | `account_scope.py` + `config_default.py` |

**不变量（保留）**：真成交由系统冒泡；agent 最终只 `<<NO_BROADCAST>>`；hold 不推群。

### 4.11 能力代理（现状）

注册：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_agent\__init__.py` 中 `register_papertrade_agents()`

| node_id | 用途 |
|---------|------|
| `papertrade_setup_agent` | 建账验证 / 触发 send_init_command |
| `papertrade_decision_agent` | 30 分钟决策 |
| `papertrade_pool_refresh_agent` | 独立池轮换 |
| `papertrade_snapshot_agent` | 收盘快照 |
| `papertrade_reporter_agent` | 复盘/持仓图等 |
| `papertrade_summary_agent` | Markdown 明细汇总 |

Prompt 中多处写死「全服共用一个模拟盘」——改造后必须改写。

### 4.12 知识库与别名

`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\__init__.py`：

- `ai_alias("papertrade", ...)`
- `ai_entity(KnowledgeBase id=sayustock_papertrade_guide)` ← 内容来自  
  `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\PAPERTRADE_GUIDE.md`

### 4.13 测试与 CI（现状）

测试目录：`F:\gsuid_core\gsuid_core\plugins\SayuStock\test\`

| 文件 | 覆盖 |
|------|------|
| `test_papertrade_account_scope.py` | scope / 播报改向 / 写鉴权 |
| `test_papertrade_strategy.py` | 评分决策风控 |
| `test_papertrade_matcher.py` | 撮合 |
| `test_papertrade_indicators.py` | 指标 |
| `test_papertrade_candidate_pool.py` | 候选池 |
| `test_papertrade_calendar.py` | 日历 |
| `test_papertrade_holdings_layout.py` | 持仓图布局 |

CI：`F:\gsuid_core\gsuid_core\plugins\SayuStock\.github\workflows\ci.yml`  
jobs：ruff → indicators 子集 → full pytest（checkout gsuid_core 层级）→ pyright（continue-on-error 现状，目标仍应本地/门禁尽量绿）

插件 pyright 配置：`F:\gsuid_core\gsuid_core\plugins\SayuStock\pyproject.toml`、`F:\gsuid_core\gsuid_core\plugins\SayuStock\pyrightconfig.json`  
ruff：`F:\gsuid_core\gsuid_core\plugins\SayuStock\ruff.toml`

### 4.14 文档与技能（现状）

| 文档 | 绝对路径 |
|------|----------|
| 用户文档 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\docs\papertrade.md` |
| 人格操作指南 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\PAPERTRADE_GUIDE.md` |
| 插件开发 skill 入口 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\docs\skills\sayustock-development\SKILL.md` |
| papertrade 开发章 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\docs\skills\sayustock-development\references\06-papertrade.md` |
| 架构章 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\docs\skills\sayustock-development\references\01-architecture-and-modules.md` |
| 框架 DB 规范 | `F:\gsuid_core\docs\skills\gscore-plugin-development\references\05-database.md` |
| 框架推送规范 | `F:\gsuid_core\docs\skills\gscore-plugin-development\references\06-scheduler-and-subscribe.md` |
| 框架 ai_tools | `F:\gsuid_core\docs\skills\gscore-plugin-development\references\11-ai-tools-decorator.md` |
| 框架代码红线 | `F:\gsuid_core\docs\skills\gscore-plugin-development\references\17-code-redlines.md` |

### 4.15 加载入口

| 文件 | 作用 |
|------|------|
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\__init__.py` / `__nest__.py` | 外层嵌套 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\__init__.py` | 显式 import stock_papertrade / stock_agent 等 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\__full__.py` | 框架约定空文件 |

---

## 5. 目标、非目标与成功标准

### 5.1 目标（对应 R1–R12）

见 §2 表格；实现上收敛为四条主线：

1. **命名多账户实体**（account_id + name）
2. **策略注册表**（multi_factor + volume_extremum）
3. **推送目标表** + 列表遍历 + 文案带盘名
4. **迁移历史 → 默认模拟盘** + 质量门全绿

### 5.2 非目标

- 用户手动买卖 / 用户改风控参数
- 完整回测引擎
- 一盘多 bot_id 并行心跳
- 本轮强制把所有推送改成 `gs_subscribe` 唯一通道（见 §10.3 关系说明）
- 盘级 ACL / 私有盘

### 5.3 成功标准摘要

见 §18 DoD。

---

## 6. 领域模型重写

### 6.1 三个正交概念

| 概念 | 旧 | 新 |
|------|----|----|
| 账户身份 | `(group_id, bot_id)` | **`account.id` + 唯一 `name`** |
| 执行宿主 | 与身份合一 | `bot_id` + `group_id`（origin 创建原群）仅服务 Kanban/Event |
| 播报目标 | config 单群 | `SayuPaperBroadcastTarget` 多行 |

### 6.2 ER 图

```
SayuPaperAccount
  id (PK)
  name UNIQUE
  strategy_id
  strategy_params (JSON text)
  mode, cash, initial_cash, principal, enabled, frequency_minutes
  bot_id, group_id (origin)
  kanban_init_root_id, kanban_period_root_id
  initialized_by, created_at, started_at, last_decided_at
        │
        ├──1:N── SayuPaperPosition      (account_id)
        ├──1:N── SayuPaperTrade
        ├──1:N── SayuPaperDecision
        ├──1:N── SayuPaperSnapshot
        ├──1:N── SayuPaperAgentPool
        ├──1:N── SayuPaperWatchlist
        └──1:N── SayuPaperBroadcastTarget
                   (account_id, bot_id, group_id) UNIQUE
```

### 6.3 命名规则（产品 + 校验）

| 规则 | 建议 |
|------|------|
| 长度 | 2～16 个 Unicode 字符 |
| 字符 | 中文、字母、数字、下划线；禁止空白/控制符 |
| 禁止 | 纯数字（避免与旧 group_id 查询歧义） |
| 唯一 | 全库唯一 |
| 默认名常量 | `"默认模拟盘"`（代码常量 `DEFAULT_ACCOUNT_NAME`） |
| 冲突 | 创建失败，提示已有盘 id/策略 |
| 搜索 | 精确 name；列表支持关键字包含匹配 |

**保留名与校验边界（实现时逐条写成 `validate_account_name()` 的分支）**

| # | 输入 | 结果 |
|---|------|------|
| N1 | `""` / 全空白 | 拒绝：「盘名不能为空」 |
| N2 | 长度 < 2 或 > 16（按 `len(str)` 即 Unicode 码点计） | 拒绝并回显当前长度 |
| N3 | 纯数字（`"123456"`） | 拒绝：与群号查询歧义 |
| N4 | 含空白 / 控制字符 / emoji | 拒绝；提示允许字符集 |
| N5 | 命中周期词（`日/周/月/季/年/ytd/总/全部/all/今日/今天/本周/本月/本季/本年/今年`） | 拒绝：会让 `模拟盘收益 <名> <周期>` 无法分词（§11.3） |
| N6 | 命中命令保留词（`初始化/查看/持仓/自选/收益/记录/排行/查询/清盘/创建/列表/推送/策略/切换/添加/删除`） | 拒绝：会与命令前缀解析冲突 |
| N7 | 与现有盘名重复（strip + 全角转半角后比较） | 拒绝，回显已存在盘的 id / 策略 |
| N8 | 与现有盘名互为前缀（`"放量"` vs `"放量盘"`） | **允许**；但查询侧精确匹配优先，包含匹配歧义时不猜（§12.1.1） |
| N9 | `默认模拟盘`（`DEFAULT_ACCOUNT_NAME`） | 仅迁移可自动占用；用户手动创建时若已存在则按 N7 拒绝 |
| N10 | 大小写不同的同名（`"VolPool"` / `"volpool"`） | 视为**不同**盘（SQLite 默认 `BINARY` 排序），但创建时给 warning 提示易混淆 |

> N5/N6 的清单必须与 `commands.py` 里真实注册的命令词表**同源**（提取成模块级常量并被两边 import），否则加新命令时会漏掉保留词。

### 6.4 权限与可见性（本轮）

- 创建 / 推送增删：群管理（`check_admin`）
- 查询任意盘名：全服可读
- 写账本：仅该盘 Kanban 或 `grant_write`
- 清盘：master admin，必须指定 name/id

---

## 7. 数据库设计与迁移（极细）

规范依据：`F:\gsuid_core\docs\skills\gscore-plugin-development\references\05-database.md`  
（`@with_session`、`exec_list`、`GsAdminModel`、`extend_existing`）

### 7.1 SayuPaperAccount 变更明细

文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\utils\database\papertrade_models.py`

| 字段 | 动作 | Python 类型 | SQL 迁移示例 | 说明 |
|------|------|-------------|--------------|------|
| `group_id` | 保留 | str | — | 语义改为创建原群；Field title 更新 |
| `bot_id` | 保留 | str | — | 心跳 bot |
| `name` | 新增 | str | `ALTER TABLE sayupaperaccount ADD COLUMN name TEXT DEFAULT '默认模拟盘'` | NOT NULL 业务层保证 |
| `strategy_id` | 新增 | str | `... DEFAULT 'multi_factor'` | 注册表 id |
| `strategy_params` | 新增 | str | `... DEFAULT '{}'` | JSON 文本 |
| Unique(gid,bid) | 删除 | — | drop index 多方言 try | 见迁移顺序 |
| Unique(name) | 新增 | — | `CREATE UNIQUE INDEX ... (name)` | |

`mode` 继续表示风控档 balanced/aggressive/conservative，**不要**与 strategy_id 混用同一字段。

### 7.2 子表 account_id

对以下表均增加 `account_id: int`（Field index=True）：

- `SayuPaperPosition`
- `SayuPaperTrade`
- `SayuPaperDecision`
- `SayuPaperSnapshot`
- `SayuPaperAgentPool`
- `SayuPaperWatchlist`

建议索引：

- `(account_id)`
- Position/Trade：`(account_id, stock_code)`
- Snapshot：`(account_id, trade_date)` 业务层幂等

**读写策略**：

- 读：只按 `account_id`
- 写：必写 `account_id`；过渡期可双写 `group_id/bot_id = account.origin` 便于人肉查库
- 下阶段再评估删 gid/bid 列

### 7.3 新表 SayuPaperBroadcastTarget

```text
表名: sayupaperbroadcasttarget（SQLModel 默认小写类名规则以现网为准）
字段:
  id              自增 PK (BaseIDModel)
  account_id      int, index, 标题=模拟盘账户ID
  bot_id          str, index, 发送用 bot（适配器名，如 onebot）
  bot_self_id     str, default ""，发送用 bot 的自身账号（多号同平台必需）
  ws_bot_id       str, default ""，WS 连接 ID（emit 侧据此选连接）
  group_id        str, index, 目标群
  enabled         int 0/1, default 1
  created_at      datetime optional
  created_by      str optional  操作者 user_id
约束:
  Unique(account_id, bot_id, group_id)
Admin:
  label=模拟盘·推送目标
```

**为什么必须存 `ws_bot_id` / `bot_self_id`（原方案缺失，会导致播报错投）**

`emit_proactive_message` 完全靠传入的 `Event` 决定投递（`gsuid_core/ai_core/proactive/emitter.py`）：

```python
# _resolve_active_bot(ev)
if ev.WS_BOT_ID and ev.WS_BOT_ID in gss.active_bot:
    return gss.active_bot[ev.WS_BOT_ID]
if gss.active_bot:
    return next(iter(gss.active_bot.values()))   # ← 随便挑一个连接
```

只存 `(bot_id, group_id)` 时，多适配器 / 一平台多号部署下会走到 `next(iter(...))` 这条兜底分支——**把 A 平台的播报从 B 平台的连接发出去**，目标群号在 B 平台不存在，消息静默丢失，且日志里看不出异常。

同时 `Event.session_id` 拼装式为 `{WS_BOT_ID}:{bot_id}:{bot_self_id}:group:{group_id}`（`gsuid_core/models.py`）。这三个字段任一缺失都会拼出一个**与真实会话对不上的伪 session**，导致：

- `emit_proactive_message` 内部的 `suppress_when_heartbeat_recent` 心跳去重按错 key 判断；
- 播报内容写进错误的 AI 会话历史，persona 后续回顾时张冠李戴。

**落库时机**：`模拟盘推送添加` 命令执行时，从当前 `ev` 直接抄 `ev.WS_BOT_ID / ev.bot_id / ev.bot_self_id`——命令是在**目标群**里发的，此刻的 `ev` 就是"这个群能收到消息的那条连接"的权威来源。迁移种子（§7.4.3）没有 `ev` 可抄，只能留空串，播报时退化到 `next(iter(...))` 兜底；这是**可接受的降级**，因为种子目标就是原账户所在群，且启动时框架通常只有一条连接。文档需向运维说明：迁移后建议在每个目标群重发一次 `模拟盘推送添加` 以补齐字段。

**边界条件**：

| 场景 | 期望行为 |
|------|---------|
| `enabled=0` 的目标 | 播报跳过，但列表命令仍展示（灰显）；不参与去重计数 |
| 同一 (account, bot, group) 重复添加 | Unique 约束命中 → Repo 层改为幂等更新 `enabled=1` + 刷新 ws/self id，不报错 |
| 目标群把 bot 踢了 | `emit_proactive_message` 返回 False；连续 N 次失败可选自动 `enabled=0`（本轮**不做**，只记 warning，避免误封） |
| 账户被清盘 | `reset_account` 必须同时删该 account 的 targets，否则孤儿行会在下次同 id 复用时误播 |
| 一个盘 0 个目标 | 合法状态（静默盘）；成交冒泡直接跳过，不报错、不 fallback 到 origin 群 |
| 目标数 > 20 | 播报改为串行 + 每条之间 `asyncio.sleep(0.2)`，避免适配器限流；失败不阻断后续目标 |

### 7.4 迁移算法（推荐实现位置）

**SQL 加列 / 建索引**：继续 `exec_list.extend([...])` 写在  
`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\utils\database\papertrade_models.py` 末尾  
（与现有 position 报价列迁移同一机制；trans_adapter 对失败语句 try/pass，故语句应幂等）。

**复杂 backfill**：单独 async 函数，挂在插件启动钩子（参考  
`F:\gsuid_core\docs\skills\gscore-plugin-development\references\07-lifecycle-hooks.md`），例如 `on_core_start_before` 或插件已有 startup 路径。  
原因：按 created_at 排序命名、子表 join 回填、推送种子，用 Python 更安全可测。

#### 7.4.1 伪代码：账户命名

```
accounts = SELECT * FROM sayupaperaccount ORDER BY created_at ASC NULLS LAST, id ASC
if not accounts: return
accounts[0].name = "默认模拟盘"
accounts[0].strategy_id = accounts[0].strategy_id or "multi_factor"
for a in accounts[1:]:
    if not a.name or a.name == "默认模拟盘":
        a.name = f"历史盘-{a.id}"
    if not a.strategy_id:
        a.strategy_id = "multi_factor"
# 若出现 name 冲突，后缀 -{id} 直至唯一
```

#### 7.4.2 伪代码：子表 account_id

```
for each child_table:
  UPDATE child SET account_id = (
    SELECT id FROM sayupaperaccount a
    WHERE a.group_id = child.group_id AND a.bot_id = child.bot_id
  )
  WHERE account_id = 0 OR account_id IS NULL
# 统计仍为 0 的行 → logger.error，人工处理（孤儿行）
```

#### 7.4.3 伪代码：推送种子

```
default = get_by_name("默认模拟盘")
if default:
  seed_groups = set()
  if config.papertrade_broadcast_group.strip():
    seed_groups.add(strip)
  if default.group_id:
    seed_groups.add(default.group_id)
  for g in seed_groups:
    INSERT BroadcastTarget(account_id=default.id, bot_id=default.bot_id, group_id=g)
    ON CONFLICT DO NOTHING
```

#### 7.4.4 索引顺序

1. 加列  
2. backfill name / strategy / account_id  
3. 保证 name 无重复  
4. `CREATE UNIQUE INDEX` on name  
5. 尝试 DROP 旧 `ux_sayupaperaccount_gid_bid`  
6. 子表 index on account_id  

迁移开关建议：账户表增加可空列 `schema_migrated_v2: int` 或独立 `SayuPaperMeta` 键，避免每次启动重复重命名「历史盘」。

#### 7.4.5 钩子优先级（必须钉死，否则迁移在空表上跑）

`gsuid_core/utils/database/startup.py` 里框架自己注册了三个 `@on_core_start_before`：

| priority | 函数 | 作用 |
|----------|------|------|
| `-100` | `import_module_list` | import 所有插件的 `*_models.py`（此时 `exec_list.extend` 才执行） |
| `-90` | `init_database` | `SQLModel.metadata.create_all`（新库按 models 建全表全列） |
| `-80` | `trans_adapter` | 逐条跑 `exec_list` 里的 ALTER / CREATE INDEX（老库补列） |

所以 Python backfill 必须注册为 `@on_core_start_before(priority=-70)`（或任何 > −80 的值）。写 `-90` 会在 `create_all` 之前跑 → 表还不存在；写 `-85` 会在 ALTER 之前跑 → `name` / `account_id` 列还不存在。

**注册位置**：新建 `SayuStock/utils/database/papertrade_migration.py`，由 `papertrade_models.py` 末尾 import 触发注册（models 在 `-100` 阶段已被 import，装饰器此刻生效，来得及）。

#### 7.4.6 `exec_list` 是无声失败的 —— backfill 必须自证前置条件

`trans_adapter` 的实现：

```python
for sql in exec_list:
    try:
        await session.execute(text(sql))
        await session.commit()
    except Exception:
        pass        # ← 任何 ALTER 失败都被吞掉
```

这个设计对"重复执行 ADD COLUMN"是必要的（第二次必然报 duplicate column），但也意味着**真正的失败（磁盘满 / 锁表 / 方言不支持）同样静默**。因此 backfill 函数开头必须自己探测：

```python
async def _has_column(session, table: str, column: str) -> bool:
    """跨方言列探测：先试 SELECT，报错即视为无此列。"""
    try:
        await session.execute(text(f"SELECT {column} FROM {table} LIMIT 1"))
        return True
    except Exception:
        return False
```

探测失败时：`logger.error` 明确写出「列 X 缺失，多账户迁移已跳过，模拟盘将继续以单盘模式运行」，然后 **return**，绝不继续跑 UPDATE（否则 SQL 报错 → 钩子抛异常 → 整个 core 启动被拖挂）。整个 backfill 外层再包一层 `try/except` + `logger.exception`，保证"迁移失败 ≠ 启动失败"。

#### 7.4.7 Kanban 树迁移（原方案完全没提，会产生双决策树）

现状：`commands._setup_papertrade_kanban_trees` 用 `scope_key=f"papertrade_period_{group_id}_{bot_id}"` 建树，root_id 回填到 `account.kanban_period_root_id`。新方案改成 `papertrade_period_{account_id}`。

**如果不处理**，升级后会发生：

1. 老树还挂在 APScheduler 上（`restore_armed_subtask_templates` 每次启动都会恢复），继续按老 `scope_key` 每 30 分钟跑决策；
2. 用户在新 UI 里对同一个账户重建了树 → 两棵树、两个决策代理，并发对同一账本 `position_upsert`（读-改-写）；
3. 交错写入把持仓算坏，且现金/持仓不自洽——这正是 §3.3 里已经踩过一次的坑。

**决策：不改老树的 `scope_key`**。理由：`scope_key` 只是 Kanban 的分组标签，不参与写鉴权（`deny_write_reason` 比的是 `root_task_id` == `account.kanban_*_root_id`），而 `account.kanban_period_root_id` 在迁移中原样保留 → 老树**天然继续对新 account_id 有写权限**。因此迁移只需：

```
# 迁移期什么都不做；新建的盘用新 scope_key，老盘沿用老 scope_key
assert account.kanban_period_root_id 原样保留
```

**唯一必须新增的守卫**：`send_init_command` 在为一个**已存在**的账户补挂树前，必须先检查 `account.kanban_period_root_id` 指向的树是否仍存活（现有代码已有此逻辑，见 `commands.py` 第 360–369 行），迁移后要**保持**，且新增的"按名字建盘"路径必须复用同一份幂等检查。

**新盘的 scope_key 规范**：

```
papertrade_init_{account_id}
papertrade_period_{account_id}
session_id: papertrade_period_acc{account_id}
```

不再带 group_id——盘不属于任何群。

#### 7.4.8 迁移边界条件全表

| # | 场景 | 期望行为 | 检测点 |
|---|------|---------|--------|
| M1 | 全新库（无任何账户） | backfill 直接 return；`create_all` 已按新 models 建好全部列 | `SELECT COUNT(*) == 0` |
| M2 | 单账户老库（最常见） | 命名为 `默认模拟盘`，`strategy_id='multi_factor'`，子表 account_id 全部回填，推送种子 = origin 群 (+ 配置的改向群) | 迁移后 `SELECT COUNT(*) FROM 子表 WHERE account_id IS NULL OR account_id=0` == 0 |
| M3 | 多账户老库（多群模式遗留） | 最早的叫 `默认模拟盘`，其余 `历史盘-{id}`；推送种子**只给默认盘**，其余盘 0 目标（静默） | 名称唯一性 |
| M4 | 已迁移过（重启） | `schema_migrated_v2 == 1` → 整段跳过；**不得**把用户改过的名字改回 `历史盘-{id}` | 幂等标记 |
| M5 | 孤儿子表行（account 已删但流水还在） | `account_id` 回填不到 → 保持 0；`logger.warning` 输出条数；读路径按 `account_id > 0` 过滤，永不展示 | 回填后统计 |
| M6 | 用户已手工建过叫「默认模拟盘」的行 | 命名冲突 → 后缀 `-{id}` 直到唯一（伪代码已覆盖），且**先解决冲突再建唯一索引**（顺序见 §7.4.4） | 建索引前 `GROUP BY name HAVING COUNT(*)>1` |
| M7 | `ALTER TABLE ... ADD COLUMN name` 在某方言失败 | 探测到列缺失 → 记 error 并 return，模拟盘退化为"只有一个盘可用"，但**不崩启动、不丢数据** | §7.4.6 探测 |
| M8 | 迁移中途进程被 kill | 下次启动重跑；每一步都是 `UPDATE ... WHERE account_id IS NULL OR account_id = 0` 形态，天然幂等 | 无需事务补偿 |
| M9 | `created_at` 全为 NULL（很老的库） | 排序退化为 `id ASC`（与 `get_earliest` 现有双排序一致），结果稳定 | `ORDER BY created_at ASC, id ASC` |
| M10 | 旧唯一索引 `ux_sayupaperaccount_gid_bid` 删不掉 | 不影响功能（同群同 bot 本来也不会建两个盘），只在"想在同一个群建第二个盘"时报错 → 必须删；DROP 失败要 `logger.error` **显式提示**，不能沿用 `exec_list` 的静默 pass | 单独探测：`INSERT` 试探或查 `sqlite_master` |

> M10 补充：`exec_list` 里的 DROP INDEX 三方言都要写（SQLite/PG：`DROP INDEX IF EXISTS ux_...`；MySQL：`ALTER TABLE sayupaperaccount DROP INDEX ux_...`），并在 backfill 里**复验**——这是唯一一个"静默失败会让核心新功能不可用"的迁移步骤。

### 7.5 Repo API 目标形态

文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\db.py`

```
PaperAccountRepo
  get_by_id(account_id) -> Optional[SayuPaperAccount]
  get_by_name(name) -> Optional[SayuPaperAccount]
  list_all() / list_enabled()
  search(keyword) -> list
  create(name, strategy_id, bot_id, origin_group_id, initial_cash, mode, initialized_by, strategy_params)
  update(account_id, **whitelist)  # whitelist 含 name? 建议独立 rename
  rename(account_id, new_name) -> 查重
  bind_kanban_init/period(account_id, root_id)
  touch_decided(account_id)
  reset_account(account_id) -> 删子表 + 可选 targets + 账户
  list_by_strategy(strategy_id)

PaperPositionRepo / Trade / Decision / Snapshot / AgentPool / Watchlist
  所有 list/get/upsert/delete 第一业务键 = account_id

PaperBroadcastRepo
  list_by_account(account_id, enabled_only=False)
  add(account_id, bot_id, group_id, created_by)
  remove(account_id, bot_id, group_id) 或 remove_by_id
  set_enabled(...)
```

过渡适配（短窗口）：

```
async def get_account_via_legacy(gid, bid):
    # 仅迁移调试用；正式路径禁止
```

### 7.6 WebConsole

每个模型继续 `@site.register_admin`；Account Admin 列展示 name/strategy_id；新增 BroadcastTarget Admin。

---

## 8. 作用域解析层重写

文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\account_scope.py`

### 8.1 删除或降级的 API

| 旧 API | 处理 |
|--------|------|
| `is_shared_mode` | 主路径删除；若保留则恒 False 或 deprecated warning |
| `home_account_key` / `_home_key_cache` | 删除；改为无全局 home 或仅 default name 查询缓存 |
| `broadcast_group_override` / `broadcast_event`（单群） | 删除；由 BroadcastService 替代 |
| `resolve_account_key(ev) -> (gid,bid)` | 删除或仅返回 origin 上下文，不再当账本键 |
| `is_home_context` | 改为「当前群是否在某盘的推送列表 / 是否 origin」按需 |

### 8.2 新 API 草案

```
DEFAULT_ACCOUNT_NAME = "默认模拟盘"

async def resolve_account(
    *,
    name: str | None = None,
    account_id: int | None = None,
    fallback_default: bool = True,
) -> SayuPaperAccount | None

def parse_account_ref_from_text(text: str) -> str | None
  # 命令行解析盘名

async def deny_write_reason(root_task_id: str, account: SayuPaperAccount) -> str
  # 比对 account.kanban_*_root_id；grant_write 放行

def scope_note_for_llm(account: SayuPaperAccount | None) -> str
  # 「命名模拟盘，非群资产；未指定则默认模拟盘」

def not_opened_message(*, name: str | None = None) -> str
  # 禁止「本群未开通」；改为「不存在名为 X 的模拟盘 / 尚未创建默认模拟盘」

@contextmanager
def grant_write():  # 保留
```

### 8.3 解析优先级

1. 显式 `account_id`
2. 显式 `name`（strip）
3. 若 `fallback_default`：查 `默认模拟盘`
4. 否则 None

工具层：`account_name: str = ""` 空则 default；显式传入则精确匹配。

### 8.4 配置废弃策略

文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_config\config_default.py`

- 保留键一版，帮助文案写「已废弃，请用模拟盘推送命令 / WebConsole」
- 运行时：`STOCK_CONFIG.get_config("papertrade_multi_group")` 若被读到打 warning
- 仅迁移种子读取 `papertrade_broadcast_group` 一次

---

## 9. 策略框架与两套预设

### 9.1 目录结构（目标）

```
F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\strategies\
  __init__.py          # STRATEGY_REGISTRY: dict[str, Strategy]
  base.py              # Protocol / ABC / StrategyContext dataclass
  multi_factor.py       # 从 strategy.py 迁入
  volume_extremum.py   # 新策略
F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\strategy.py
  # re-export MODE_RULES, score_stock(兼容), get_strategy(id)
```

### 9.0 ⚠️ 策略生效点（本节是 §9 的前提，必须先读）

§4.9.1 已证明：`strategy.py` 的 `score/decide/risk` 在生产路径上**一次都没被调用**。所以"新增一个策略类"本身**不会改变任何线上行为**。要让 `strategy_id` 真正生效，必须至少落地下列生效点之一；本方案采用 **A + B + C 三点齐上**（D 列为明确不做）。

| 生效点 | 机制 | 强度 | 本轮 |
|--------|------|------|------|
| **A. Prompt 注入** | `Strategy.decision_system_extra` 拼进 `PAPERTRADE_DECISION_PROMPT`，让 LLM 按该策略的语义决策 | 软（LLM 可能不遵守） | ✅ 做 |
| **B. 候选池偏好** | `papertrade_candidate_refresh` 按 `strategy.candidate_policy` 切换源权重（volume 策略提高跌幅榜/量能异动权重） | 中（改变 LLM 能看到的标的集合） | ✅ 做 |
| **C. 落库硬门** | `papertrade_trade_insert` / `decision_insert` 在写库前调 `strategy.validate_entry(...)`，不满足策略结构条件直接拒绝 | **硬**（代码强制，LLM 绕不过） | ✅ 做 |
| D. 完全程序化下单 | 用 `decide()` 的输出直接下单，LLM 只做解释 | 最硬 | ❌ 不做（改动过大、丧失事件面判断） |

**C 的具体形态**（这是让"策略"从文档变成事实的关键）：

现有 `papertrade_trade_insert` 已有一道硬门 —— buy 必须在 `snapshot` JSON 里带可解析的 `plan_stop_pct(<0)` 或 `plan_stop_price(>0)`（`ai_tools.py` 第 1009–1029 行）。新增的策略硬门与它**同层同形**：

```
strategy = get_strategy(account.strategy_id)
verdict = strategy.validate_entry(side=side, snapshot=snap_obj)
if verdict.rejected:
    return f"⚠️ 策略 {strategy.display_name} 拒绝本单：{verdict.reason}"
```

- `multi_factor.validate_entry` → 保持现状（只查 plan_stop），**零行为变更**，保证升级不破坏既有盘。
- `volume_extremum.validate_entry` → buy 时要求 snapshot 含 `rel_volume >= vol_ratio_min` 且 `close_percentile <= bottom_pct`；sell 时要求顶部放量或触发 plan_stop。缺字段即拒，并在错误信息里**明确告诉 LLM 该调哪个工具补齐**（`stock_indicators` 已返回这些值）——这条错误信息就是让 LLM 学会遵守策略的反馈回路。

**边界条件**：

| 场景 | 期望 |
|------|------|
| `strategy_id` 是未注册的字符串（人工改库/回滚） | `get_strategy` 返回 `multi_factor` 兜底 + `logger.warning`，**不抛异常**（决策心跳不能因此瘫痪） |
| `strategy_params` 是非法 JSON | 用 `default_params`，记 warning |
| `strategy_params` 有未知键 | 忽略未知键，不报错（前向兼容新版参数回滚到老版） |
| `strategy_params` 键类型不对（如 `vol_ma_n="20"`） | 显式 `isinstance` 校验失败 → 该键退回默认值 + warning；**禁止** `dict.get` 静默吞掉 |
| sell 被策略硬门拒绝，但持仓已触发 plan_stop | **止损优先**：`validate_entry` 对 sell 侧只在"非止损"路径生效；snapshot 里带 `stop_triggered=true` 时一律放行（否则策略会把风控锁死，这是真实亏钱的路径） |
| 账户在有持仓时切换策略 | 允许；已有持仓按**原入场计划**（trade.snapshot 里的 plan_stop）管理，新策略只约束新开仓。切换命令须在回执里明说这一点 |

### 9.2 Strategy 接口字段

| 成员 | 类型 | 说明 |
|------|------|------|
| `strategy_id` | str | 稳定 id，入库用 |
| `display_name` | str | 中文展示 |
| `description` | str | 列表/帮助 |
| `default_params` | TypedDict | 可 JSON 序列化 |
| `score(...)` | → (float, list[str]) | -1~1 与原因 |
| `decide(...)` | → action + 仓位提示 | |
| `risk_uses_global_mode` | bool | True 则套 MODE_RULES |
| `decision_system_extra` | str | 注入 agent prompt |
| `required_indicator_keys` | list[str] | |
| `candidate_policy` | str | 给 refresh/agent 的偏好说明 |
| `agent_profile_id` | str | Kanban 用哪个 decision profile |

### 9.3 multi_factor（现有完整迁入）

来源：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\strategy.py`

行为保持：

- TechSignals / FundSignals / NewsSignals
- MACD/RSI/均线/CMF/量比/换手 + 财务 + 舆情 + ATR 调节
- `decide_action` 与 mode 门槛
- `apply_risk_check`
- buy 必须 `indicators_have_entry_stop`

单测：`F:\gsuid_core\gsuid_core\plugins\SayuStock\test\test_papertrade_strategy.py` 应几乎不改断言语义。

### 9.4 volume_extremum（新）详细规格

#### 9.4.1 产品语义

- **买**：价格处于阶段性底部区域 **且** 出现放量 → 视为资金在低位承接，允许开仓/加仓（受 mode 风控）。
- **卖**：价格处于阶段性顶部区域 **且** 出现放量 → 视为高位派发，减仓/清仓；或触发 plan_stop / 风控止损。
- **不做**：高位放量追多、低位无量「抄底」、纯基本面故事无量价结构。

#### 9.4.2 默认参数（TypedDict，可进 strategy_params）

| 键 | 默认 | 含义 |
|----|------|------|
| `vol_ma_n` | 20 | 量能均线窗口 |
| `vol_ratio_min` | 2.0 | 相对放量阈值 |
| `lookback_m` | 60 | 底/顶区位回看天数 |
| `bottom_pct` | 0.15 | 收盘价分位 ≤ 此视为底部区（或 dist_to_low 规则二选一，实现时固定一种） |
| `top_pct` | 0.85 | 顶部区分位 |
| `dist_to_extreme_pct` | 0.03 | 或：距 M 日高/低 ≤ 3% |
| `require_bullish_close` | true | 底放量买要求收阳 |
| `fund_veto_debt_ratio` | 0.85 | 超过则禁止新开仓（软基本面 veto） |
| `news_veto_on_severe` | true | 严重利空 veto |

参数解析：**显式键 + isinstance 校验**，禁止业务 `dict.get` 静默默认（可在 registry 装载时 merge default_params 一次生成完整 TypedDict）。

#### 9.4.3 指标需求

扩展：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\indicators.py`  
及底层：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\utils\indicators.py`（若已有量能函数则复用）

| 指标 | 用途 |
|------|------|
| `volume_ma_n` / `rel_volume` | 放量 |
| `close_percentile_m` 或 `dist_to_m_low/high` | 区位 |
| 已有 `volume_ratio` | 可作盘中量比辅助，日线策略以日线相对量为主 |

#### 9.4.4 候选池策略差异

文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\candidate_pool.py`

| multi_factor | volume_extremum |
|--------------|-----------------|
| 蓝筹底仓 + 动量多源 | 提高「跌幅榜/低位活跃/量能异动」权重，降低纯高 ROE 蓝筹锚定 |
| 过滤涨停过热 | 同样过滤涨停；顶放量卖不依赖新开涨停票 |

`papertrade_candidate_refresh` 需能读当前 account.strategy_id 分支（或 refresh 入参 strategy_id）。

#### 9.4.5 与 LLM 决策的分工

- **确定性层**：score/decide 给出结构信号与门槛，供单测与 agent 参考。
- **LLM 层**：解释、事件 veto、在多只候选中排序；**不得**无结构强行满仓。
- 落库：buy 仍强制 plan_stop；trade_insert 仍走统一撮合。

---

## 10. 推送与播报

### 10.1 BroadcastService（建议新文件）

建议路径：  
`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\broadcast.py`

```
async def broadcast_to_account(
    account: SayuPaperAccount,
    message: str | list,  # 与 emit 兼容的消息类型
    *,
    reason: str,
    source: str = "tool",
) -> dict[str, bool]:  # group_id -> ok
    targets = await PaperBroadcastRepo.list_by_account(account.id, enabled_only=True)
    if not targets:
        logger.info("无推送目标，跳过")
        return {}
    for t in targets:
        # 构造 Event: bot_id=t.bot_id, group_id=t.group_id, user_type=group
        # emit_proactive_message(...)
        # 单条失败 debug/warning，continue
```

替换调用点：

| 位置 | 绝对路径 |
|------|----------|
| `_broadcast_fill` | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\ai_tools.py` |
| init kick 失败提示等 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\commands.py` |
| dry_run 推送 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\admin.py` |
| 结构化文本投递（若有） | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\proactive.py` |

### 10.2 文案模板（必须含盘名）

| 场景 | 模板 |
|------|------|
| 买入冒泡 | `🟢 [{name}] 买入 {股票}({code}) {qty} 股 @¥{price:.2f}` |
| 卖出冒泡 | `🔴 [{name}] 卖出 ...（±¥pnl）` |
| 结构化头 | `📈 模拟盘·操盘播报 · {name}（{strategy_display}）` |
| 账户图标题 | `{name} · {strategy_display}` |
| 月报标题 | `模拟盘月报 · {name}` |

`name` 取自账户，禁止只写 group_id。

### 10.3 与 gs_subscribe 的关系

规范：`F:\gsuid_core\docs\skills\gscore-plugin-development\references\06-scheduler-and-subscribe.md`  
要求主动推送优先订阅系统，禁止裸遍历 `gss.active_bot` 硬编码群号。

本方案选择：

| 机制 | 角色 |
|------|------|
| `SayuPaperBroadcastTarget` | **账户级推送配置权威**（运营：这盘推哪些群） |
| `emit_proactive_message` | 投递实现（沿用现网成交冒泡） |
| `gs_subscribe` | 二期可选：用户订阅「某盘日报」 |

禁止：在 APScheduler 回调里 `for bot in gss.active_bot` + 写死群号。

### 10.4 Kanban broadcast_targets

创建树时可传入当前 DB 列表，但 **运行时播报不信任该快照**；永远 `PaperBroadcastRepo.list_*`。

---

## 11. 命令与产品交互

文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\commands.py`  
权限：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\permissions.py`

### 11.1 命令表

| 命令文案 | 权限 | 行为细节 |
|----------|------|----------|
| `模拟盘创建 <名字> [策略] [资金]` | 管理 | 校验名/策略；create；本群加入 BroadcastTarget；挂 Kanban；可选 kick |
| `模拟盘初始化 [资金]` | 管理 | 兼容：对「默认模拟盘」幂等 create 或补树 |
| `模拟盘列表 [关键字]` | 任意 | id/name/strategy/equity/enabled |
| `模拟盘查看 [名字]` | 任意 | 默认默认模拟盘；出图带名 |
| `模拟盘自选/持仓 [名字]` | 任意 | |
| `模拟盘收益 [名字] 周期` | 任意 | 解析顺序：先周期关键字，再盘名 |
| `模拟盘记录 [名字]` | 任意 | |
| `模拟盘策略` | 任意 | 打印 registry |
| `模拟盘推送添加 <名字>` | 管理 | 当前群+bot 写入 target |
| `模拟盘推送移除 <名字>` | 管理 | |
| `模拟盘推送列表 <名字>` | 管理 | |
| `模拟盘排行` | 管理 | **跨盘** total_pnl_pct TOP N |
| `模拟盘查询 <名字\|旧gid>` | 管理 | 先 name；纯数字再 match origin group_id |
| `模拟盘清盘 <名字>` | master | 指定盘；禁止无参清全库 |
| `模拟盘压测 <名字>` | master | dry_run 绑定盘 |

别名元组保留 `AI操盘*`。

### 11.2 初始化兼容语义

旧：`模拟盘初始化` 在共用模式拒第二群。  
新：

- `初始化` = 确保「默认模拟盘」存在且心跳健康；
- 其它群执行 `初始化`：**不建第二默认盘**，可选择「把本群加入默认盘推送列表」并提示用 `模拟盘创建` 建新盘；
- 或：`初始化` 仅当默认盘不存在时创建，存在则补树 + 提示推送添加。

推荐文案：

```
✅ 默认模拟盘已就绪（策略=多维评分）
本群已加入推送列表。新建其它策略盘请用：模拟盘创建 放量盘 volume_extremum
```

### 11.3 解析盘名注意

`模拟盘收益 月` vs `模拟盘收益 放量盘 月`：

- 周期集合：日/周/月/季/年/ytd/总
- 若 token 命中周期，则盘名用默认；若两 token，盘名+周期

---

## 12. AI 工具清单与改造点

文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\ai_tools.py`

### 12.1 通用改造

每个工具：

1. 增加 `account_name: str = ""`（或 `account_id: int = 0`）
2. `_resolve_scope` → `_resolve_account(ctx, account_name, account_id)`
3. JSON 输出增加 `account_id`, `name`, `strategy_id`, `strategy_display`
4. 删除 `shared_mode` 字段或恒 false + 新 `scope_note`
5. covers/aliases 增加：默认模拟盘、多盘、放量、盘名

#### 12.1.1 ⚠️ 读工具与写工具的账户解析必须走**不同**优先级

原方案对所有工具统一用 `account_name` 入参，这在**写工具**上有一个会导致心跳停摆的风险：

心跳链路是 `Kanban 子任务 → run_capability_agent → 工具`，LLM 拿到的任务描述里带着盘名字符串。若它把盘名写错一个字（简繁、空格、"放量盘" vs "放量"），`resolve_account(name=...)` 会解析到**另一个盘或 None** →

- 解析到 None → 回落默认盘 → **把 A 盘的成交写进默认盘的账本**（最坏情况：静默数据污染）；
- 解析到别的盘 → `deny_write_reason` 比对 `root_task_id` 失败 → 拒绝写入 → agent 收到拒绝、重试、再拒绝，一轮心跳空转烧 token。

**因此写工具的解析优先级必须是**：

```
1. root_task_id → 反查 account（唯一权威）        ← 写工具只认这条
2. grant_write() 上下文 + 显式 account_id/name    ← init kick / dry_run 专用
3. 其它一律拒绝，返回明确理由
```

`root_task_id` → account 的反查：`PaperAccountRepo.get_by_kanban_root(root_task_id)`（`WHERE kanban_init_root_id = ? OR kanban_period_root_id = ?`）。它与 `deny_write_reason` 查的是同一份数据，因此**解析成功 ⇒ 鉴权必然通过**，两者天然一致，不会出现"解析到 A、鉴权按 B"的裂缝。

写工具签名上仍保留 `account_name` 入参，但语义降级为**一致性校验**：传了且与 root 反查结果不符时，返回

```
⚠️ 本次心跳属于模拟盘「{真实盘名}」，但你传入了「{传入值}」。
已按任务归属的盘执行；请在后续调用中省略 account_name。
```

——纠正而非拒绝，避免 LLM 拼错名字就让整轮心跳报废。

读工具则相反：`account_name` 是**唯一**输入（用户就是想查指定的盘），空则默认盘。读错盘不会污染数据，只会答错，且用户能立刻发现。

**边界条件**：

| 场景 | 读工具 | 写工具 |
|------|--------|--------|
| `account_name=""` | 默认盘；无默认盘 → `not_opened_message()` | root 反查 |
| 名字不存在 | 返回"不存在名为 X 的模拟盘，现有：A/B/C"（**带清单**，让 LLM 自纠） | 忽略入参，按 root 执行 + 提示 |
| 名字前后有空格/全角空格 | strip + 全角转半角后精确匹配，仍不中再做包含匹配 | 同左 |
| 多个盘名互为前缀（"放量" / "放量盘"） | 精确匹配优先；包含匹配命中多个 → 返回歧义提示 + 候选清单，**不猜** | 不适用 |
| `root_task_id` 为空（ad-hoc） | — | 必须有 `grant_write()`，否则拒 |
| root 反查不到账户（树被删/账户被清） | — | 拒绝 + 明确提示"该心跳树已与账户解绑，请重新初始化" |

### 12.2 工具增删改表

| 工具 | 动作 |
|------|------|
| `papertrade_account_query` | 改：按名；输出 name/strategy |
| `papertrade_position_list` 等 list | 改 |
| `papertrade_holdings_image` | 改：图含盘名 |
| `papertrade_trade_insert` | 改：broadcast 用 account |
| `papertrade_candidate_refresh` | 改：按策略分支 |
| `papertrade_snapshot_write` | 改 |
| `papertrade_account_list` | **新** common |
| `papertrade_strategy_list` | **新** common |
| `papertrade_broadcast_list` | **新**（管理可见或 agent 只读） |
| `papertrade_broadcast_add/remove` | **新** 写：严格鉴权 |
| `papertrade_volume_scan` | **新** common 或 default |
| `papertrade_account_create` | **仍不建议**对主 persona 开放；创建走 trigger。setup agent 继续 by_trigger 命令 |

### 12.3 TypedDict 更新示例

`_AccountView` 现状含 `group_id/shared_mode`（`ai_tools.py` 内）。目标：

```
account_id, name, strategy_id, strategy_display,
origin_group_id, bot_id, scope_note,
cash, initial_cash, principal, position_value, total_equity, ...
```

### 12.4 context_tags

保留 `_PAPERTRADE_CTX_TAGS`，可加 `"放量"`, `"默认模拟盘"` 等若框架按标签装配。

---

## 13. Agent / Kanban / 心跳

### 13.1 注册文件

`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_agent\__init__.py`

### 13.2 Decision profile 拆分（推荐）

| strategy_id | agent_profile | gate |
|-------------|---------------|------|
| `multi_factor` | `papertrade_decision_multi_factor`（可由现 decision 改名或并存） | `should_run_papertrade` |
| `volume_extremum` | `papertrade_decision_volume` | 同上 |

`register_recurring_gate` 对两个 profile 都注册。  
文件：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\__init__.py`

### 13.3 一盘一树

```
scope_key_init   = f"papertrade_init_{account_id}"
scope_key_period = f"papertrade_period_{account_id}"
goal = f"模拟盘[{name}|{strategy_id}] 周期托管"
session_id = f"papertrade_period_{account_id}"
```

子任务 description 用模板 format：

- multi_factor：保留现 Phase 0–6（技术/基本面/舆情）
- volume：Phase 4 改为量价结构 + volume_scan；财报仅 veto；候选策略说明替换

### 13.4 写鉴权

`deny_write_reason` 使用 **account 上存储的 root_id**，不再 `PaperAccountRepo.get(gid,bid)`。

多盘并发时：盘 A 的 task 不能写盘 B（root 比对失败）。

### 13.5 并发与限流

N 个 enabled 盘 × 同刻 cron → N 次 decision。

建议：

- quote/indicators 层已有缓存则共享
- 可选 `asyncio.Semaphore(3)` 包在 kick 入口（commands/admin 不强制）

**成本闸（必须实现，不是可选项）**

每个 decision tick 是一次完整的 capability agent 调用（多轮工具 + 长 prompt）。现状单盘每交易日 10 个 tick（`cron:0,30 9-11,13-15 * * 1-5`）+ 2 个池轮换 + 1 个快照 ≈ 13 次；N 个盘就是 13N 次。

| 闸 | 值 | 位置 |
|----|-----|------|
| `MAX_ENABLED_ACCOUNTS` | 5 | 创建 / 启用命令；超限直接拒绝并列出当前启用的盘，提示先停用 |
| 同刻并发上限 | `asyncio.Semaphore(2)` | 决策 kick 入口（避免 N 个盘同时打东财接口被限流） |
| 单盘 tick 频率可调 | `account.frequency_minutes` 已在表里，但**现状没被使用**（cron 写死 0,30） | 本轮不实现动态 cron，但创建多盘时应在回执里提示"所有盘共用 30 分钟节奏" |

盘的 `enabled=0` 必须真正阻断心跳：现状 `enabled` 字段只在展示层用，`recurring gate` 并不查它。→ 需要在 decision agent 的第一步（或 gate 里）加"账户 enabled=0 则立即 NO_BROADCAST 退出"，否则停用一个盘只是界面上变灰，token 照烧。

### 13.6 Prompt 必改句

删除/替换：

- 「默认全服共用一个模拟盘」
- 「group_id 是开户原群不是本群无盘」类解释 → 改为 name 语义
- setup agent：触发创建时带盘名参数（默认盘 vs 创建命令）

更新知识库源文件：  
`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\PAPERTRADE_GUIDE.md`

---

## 14. 模块级改动地图（文件绝对路径）

| 绝对路径 | 级别 | 改动摘要 |
|----------|------|----------|
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\utils\database\papertrade_models.py` | 高 | 字段/新表/Admin/exec_list |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\utils\database\models.py` | 低 | 确认 import 新表 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\db.py` | 高 | account_id Repo + BroadcastRepo |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\account_scope.py` | 高 | 重写 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\broadcast.py` | 高 | **新建** |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\strategies\` | 高 | **新建包** |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\strategy.py` | 中 | re-export |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\indicators.py` | 中 | 放量/区位 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\ai_tools.py` | 高 | 全工具 account 化 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\commands.py` | 高 | 创建/列表/推送/兼容 init |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\admin.py` | 中 | 清盘/压测指定盘 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\proactive.py` | 中 | 盘名文案；snapshot API 改 account_id |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\render.py` | 中 | 标题盘名；`build_holdings_snapshot_image(account_id)` |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\candidate_pool.py` | 中 | strategy 分支；(gid,bid)→account_id |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\cross_group.py` | 中 | 改跨盘排行/查询 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\__init__.py` | 中 | alias/gate/新 profile |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\PAPERTRADE_GUIDE.md` | 中 | 人格指南 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_agent\__init__.py` | 高 | prompt + register |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_config\config_default.py` | 低 | deprecated 文案 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_help\help.json` | 低 | 若有模拟盘帮助项 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\docs\papertrade.md` | 中 | 用户文档 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\docs\skills\sayustock-development\references\06-papertrade.md` | 中 | 开发文档 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\docs\skills\sayustock-development\SKILL.md` | 低 | 索引句 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\test\test_papertrade_*.py` | 高 | 扩展/重写桩 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\plans\sayustock_papertrade_multi_account_strategy_20260812.md` | — | 本文 |

撮合/日历/permissions 可少动：

- `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\matcher.py`
- `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\trading_calendar.py`
- `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\permissions.py`
- `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\trade_executor.py`
- `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\quote_service.py`

---

## 15. 实施顺序与依赖

```
Phase 0  模型+exec_list+迁移函数+Repo+BroadcastRepo
    │ 依赖：无
    ▼
Phase 1  account_scope + broadcast.py + ai_tools 读路径 + commands 查询/创建/推送
    │ 依赖：Phase 0
    ▼
Phase 2  Kanban 按 account_id 建树 + 写工具 + admin + 成交冒泡切 BroadcastService
    │ 依赖：Phase 1
    ▼
Phase 3  strategies 包 + multi_factor 迁入 + volume + candidate 分支 + decision profiles
    │ 依赖：Phase 2
    ▼
Phase 4  文档/KB/help + 删适配层 + 全量测试/ruff/pyright
```

**建议同一发布列车完成 Phase 0–3**，避免线上长期双主键。  
Phase 4 可同 PR。

依赖关系注意：

- 未完成迁移前不要部署只认 account_id 的代码到有旧库的环境
- 迁移函数必须先于任何命令写路径执行（startup 顺序）

---

## 16. 测试矩阵与质量门

### 16.1 单测矩阵

| 绝对路径 | 场景 |
|----------|------|
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\test\test_papertrade_account_scope.py` | resolve 按名/默认；deny_write 按 account；无 home 缓存 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\test\test_papertrade_strategy.py` | multi_factor 回归；volume 底放量买/顶放量卖/不满足 hold |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\test\test_papertrade_matcher.py` | 回归 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\test\test_papertrade_candidate_pool.py` | account_id；策略分支若有 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\test\test_papertrade_broadcast.py` | **新建**：多 target、空列表、文案含名 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\test\test_papertrade_migration_logic.py` | **新建**：命名默认盘、历史盘-id、backfill 映射 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\test\test_papertrade_calendar.py` | 回归 |
| `F:\gsuid_core\gsuid_core\plugins\SayuStock\test\test_papertrade_holdings_layout.py` | 若签名变则改 |

### 16.2 手工/联调清单

1. 旧库启动 → 仅「默认模拟盘」，持仓数与迁移前一致  
2. `模拟盘创建 放量盘 volume_extremum` → 两盘列表可见  
3. 两盘分别 decision（dry_run）→ 流水 account_id 不串  
4. 推送添加两群 → 一笔成交两群各一条带盘名冒泡  
5. 非交易日 gate 仍跳过  
6. 主人格问「默认模拟盘持仓」工具能召回  

### 16.3 质量门命令（在插件根执行）

工作目录：`F:\gsuid_core\gsuid_core\plugins\SayuStock`

```
ruff check SayuStock/ test/
ruff format --check SayuStock/ test/
pytest test/ -q
# pyright 按 pyproject / pyrightconfig
```

CI 定义：`F:\gsuid_core\gsuid_core\plugins\SayuStock\.github\workflows\ci.yml`

红线自查：

- `F:\gsuid_core\docs\skills\gscore-plugin-development\references\17-code-redlines.md`
- `F:\gsuid_core\docs\LLM.md`

---

## 17. 风险、默认决策、踩坑与反模式

### 17.1 默认决策（实现前可改，改则先更新本文）

| ID | 议题 | 默认 |
|----|------|------|
| A | 历史多行账户 | 最早→`默认模拟盘`；其余→`历史盘-{id}` |
| B | 查询未写名 | fallback `默认模拟盘` |
| C | 可见性 | 全服可读 |
| D | 同策略多盘 | 允许（名不同） |
| E | volume agent | 独立 profile |
| F | 推送权威 | BroadcastTarget 表 |
| G | group_id 列 | 不 rename |
| H | 创建挂树 | 是，对齐现 init |
| I | 旧 multi_group | warning + 多命名账户运行 |
| J | 第二群「初始化」 | 不建第二默认盘；提示创建/加推送 |
| K | account_create tool | 不对主 persona 开放 |

### 17.2 踩坑清单（扩展）

1. 改推送不能改 account_id；改名不能导致子表失联（子表只认 id）。  
2. 清盘必须 `invalidate` 一切 name/id 缓存。  
3. Unique name：先修数据再建索引。  
4. Kanban `scope_key` 必须含 `account_id`。  
5. 写鉴权 root 与 account 绑定防串盘。  
6. 双写 gid 必须等于 origin，避免排障幻觉。  
7. 成交冒泡系统路径保留；agent NO_BROADCAST。  
8. buy 强制 plan_stop（volume 不例外）。  
9. matcher 涨跌停/整手/费率统一，策略禁止旁路。  
10. volume 候选池勿只塞高价蓝筹。  
11. 迁移 orphan 子表行要打错误日志。  
12. 文档三处：papertrade.md / PAPERTRADE_GUIDE / skill §6。  
13. CI 依赖 `gsuid_core/plugins/SayuStock` 目录层级。  
14. 禁止裸 `gss.active_bot` 群发。  
15. 工具空 `account_name` 语义写进 docstring。  
16. period ROOT 禁止 recurring_trigger（历史坑）。  
17. setup 确认禁止写瞬时持仓数字（artifact 坑）。  
18. 共用模式「第二群 init」旧逻辑删除后，用 J 策略替代，**仍要防止两棵树写同一 account**（同一 account 只一棵 period 树）。  
19. `reset_account` 旧实现按 gid 删——多盘后按 id，测试覆盖「只删目标盘」。  
20. pyright：Repo 签名变更导致 call-site 爆炸，先改 db 再改外层。  
21. **`exec_list` 的失败是静默的**（`trans_adapter` 逐条 `except: pass`）。加列失败不会有任何日志，直到 backfill 里 `SELECT name` 报错才暴露。→ backfill 必须自己探测列（§7.4.6）。  
22. **迁移钩子 priority 必须 > −80**，否则跑在 `exec_list` 之前，看到的是没有新列的旧表（§7.4.5）。  
23. **`BroadcastTarget` 少存 `ws_bot_id` / `bot_self_id` 会静默错投**：`_resolve_active_bot` 找不到 WS_BOT_ID 就 `next(iter(gss.active_bot.values()))`，多适配器部署下从错误的连接发出去（§7.3）。  
24. **写工具不能信 LLM 传的 `account_name`**：拼错名字轻则整轮心跳被 `deny_write_reason` 拒到空转，重则把成交写进默认盘。写路径只认 `root_task_id` 反查（§12.1.1）。  
25. **老 Kanban 树的 `scope_key` 不要改**：`scope_key` 不参与鉴权，改了反而可能和新建树重复；`kanban_period_root_id` 原样保留即可让老树继续对新 account_id 生效（§7.4.7）。  
26. **`strategy.py` 的 score/decide 当前是死代码**（生产 0 调用）。只搬运不接生效点 = 策略功能形同虚设（§4.9.1 / §9.0）。  
27. **策略硬门不能锁死止损**：sell 侧的 `validate_entry` 必须对 `stop_triggered` 放行，否则风控被策略挡住（§9.0 边界表）。  
28. **`SayuPaperWatchlist` 归属要先定性**：它是"群友关注"，语义上属于**群**而非**盘**。本轮决策：跟随 account（加 `account_id`），因为它同时被候选池当作"保护集"使用（`candidate_pool._from_watchlist`），跟着盘走才能让不同策略的盘有不同保护集。副作用：同一个群的关注列表在不同盘里互相不可见——需在命令回执里说明。  
29. **LLM 成本随盘数线性增长**：N 个 enabled 盘 × 每日 10 个决策 tick = N×10 次 capability agent 调用。上线前必须给 `list_enabled` 加**总数上限**（建议 `MAX_ENABLED_ACCOUNTS = 5`，超出时创建命令直接拒绝并提示先停用），否则一次误操作建 20 个盘就是 20 倍账单。  
30. **`cross_group.py` 是重写不是改签名**：全文 151 行按群设计，含两个私有 helper 直接 `WHERE group_id=?`。排行语义要从"跨群排行"变成"跨盘排行"，`draw_leaderboard` 的表头「群号」列也要换成「盘名」。  

### 17.3 反模式

```
❌ (group_id, bot_id) 继续当多盘主键
❌ config 字符串维护多群推送
❌ record_* / 记忆工具记账
❌ 单 prompt 硬塞两种策略人格且不拆 profile
❌ 迁移 silent merge 多账户资金持仓
❌ 宽 try/except 吞迁移失败
❌ 无参清盘清掉所有盘
❌ 用网页摘要当 volume 策略的成交量真源
```

### 17.4 难度与可行性（摘要）

- **可行性高**：撮合/日历/Kanban/鉴权复用。  
- **难度中高**：横切 account 化 + 迁移正确性 + 全绿门禁。  
- **volume 策略**：工程中等，调参可迭代。  
详见对话中的难度评估；本文以实施规格为准。

---

## 18. 验收标准 DoD

1. 旧数据在「默认模拟盘」下持仓/流水/快照/决策可查，数量与迁移前一致（允许 name 字段新增）。  
2. 可创建第二盘（不同 strategy），数据隔离；排行按盘。  
3. 所有用户可见播报/图含 **盘名**。  
4. 推送目标仅 DB；≥2 群收到同一盘成交冒泡；增删命令即时生效。  
5. multi_factor 单测通过；volume 有独立用例。  
6. ruff / pytest / pyright（目标绿）/ §17+LLM.md 自查通过。  
7. 用户文档、PAPERTRADE_GUIDE、skill §6、config 废弃说明已更新。  
8. 无第二套账本；写路径仍 deny 非 Kanban。  

---

## 19. 附录 A：绝对路径总表

### 19.1 插件核心

| 说明 | 绝对路径 |
|------|----------|
| 插件根 | `F:\gsuid_core\gsuid_core\plugins\SayuStock` |
| 业务包 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock` |
| papertrade 包 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade` |
| 本方案 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\plans\sayustock_papertrade_multi_account_strategy_20260812.md` |
| 表模型 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\utils\database\papertrade_models.py` |
| DB 聚合 import | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\utils\database\models.py` |
| Repo | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\db.py` |
| Scope | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\account_scope.py` |
| AI tools | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\ai_tools.py` |
| 命令 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\commands.py` |
| Admin | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\admin.py` |
| 策略（现） | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\strategy.py` |
| 候选池 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\candidate_pool.py` |
| 播报 builder | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\proactive.py` |
| 渲染 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\render.py` |
| 跨群 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\cross_group.py` |
| 撮合 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\matcher.py` |
| 入口/KB/gate | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\__init__.py` |
| Agent 注册 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_agent\__init__.py` |
| 配置默认 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_config\config_default.py` |
| 配置实例 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_config\stock_config.py` |

### 19.2 测试与 CI

| 说明 | 绝对路径 |
|------|----------|
| 测试目录 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\test` |
| CI | `F:\gsuid_core\gsuid_core\plugins\SayuStock\.github\workflows\ci.yml` |
| pyproject | `F:\gsuid_core\gsuid_core\plugins\SayuStock\pyproject.toml` |
| ruff | `F:\gsuid_core\gsuid_core\plugins\SayuStock\ruff.toml` |

### 19.3 文档与规范

| 说明 | 绝对路径 |
|------|----------|
| 用户 papertrade | `F:\gsuid_core\gsuid_core\plugins\SayuStock\docs\papertrade.md` |
| 人格指南 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\PAPERTRADE_GUIDE.md` |
| 插件 skill | `F:\gsuid_core\gsuid_core\plugins\SayuStock\docs\skills\sayustock-development\SKILL.md` |
| 插件 papertrade 章 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\docs\skills\sayustock-development\references\06-papertrade.md` |
| 框架 skill 入口 | `F:\gsuid_core\docs\skills\gscore-plugin-development\SKILL.md` |
| 框架数据库 | `F:\gsuid_core\docs\skills\gscore-plugin-development\references\05-database.md` |
| 框架订阅 | `F:\gsuid_core\docs\skills\gscore-plugin-development\references\06-scheduler-and-subscribe.md` |
| 框架生命周期 | `F:\gsuid_core\docs\skills\gscore-plugin-development\references\07-lifecycle-hooks.md` |
| 框架 ai_tools | `F:\gsuid_core\docs\skills\gscore-plugin-development\references\11-ai-tools-decorator.md` |
| 框架红线 | `F:\gsuid_core\docs\skills\gscore-plugin-development\references\17-code-redlines.md` |
| LLM.md | `F:\gsuid_core\docs\LLM.md` |
| 框架根 | `F:\gsuid_core` |

### 19.4 目标新建路径

| 说明 | 绝对路径 |
|------|----------|
| 策略包 | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\strategies\` |
| multi_factor | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\strategies\multi_factor.py` |
| volume | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\strategies\volume_extremum.py` |
| base | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\strategies\base.py` |
| registry | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\strategies\__init__.py` |
| BroadcastService | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\broadcast.py` |
| 迁移逻辑模块（可选） | `F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\migration_v2.py` |

---

## 20. 附录 B：关键代码行为摘录

### 20.1 共用模式拒第二群 init（将被 J 策略替代）

位置：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\commands.py` 中 `send_init_command`  
原因注释：防止两棵决策树并发写同一账本。  
改造后：同一 `account_id` 仍只允许一棵 period 树；**不同 account_id** 才允许多树。

### 20.2 成交冒泡

位置：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\ai_tools.py` 中 `_broadcast_fill`  
现状依赖 `account_scope.broadcast_event` 单群改向。  
目标：`broadcast_to_account(account, line, reason=...)`。

### 20.3 账户键钉死最早

位置：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_papertrade\account_scope.py` 中 `home_account_key`  
与 `PaperAccountRepo.get_earliest`（`db.py`）。  
改造后删除「全局唯一盘」语义，但迁移时 **get_earliest 仍用于选出默认模拟盘**。

### 20.4 决策代理最终输出纪律

位置：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\stock_agent\__init__.py` 中 `PAPERTRADE_DECISION_PROMPT`  
`<<NO_BROADCAST>>`；成交仅系统冒泡。多盘后每条冒泡带盘名即可区分。

### 20.5 表唯一约束现状

位置：`F:\gsuid_core\gsuid_core\plugins\SayuStock\SayuStock\utils\database\papertrade_models.py`  
`UniqueConstraint("group_id", "bot_id", name="ux_sayupaperaccount_gid_bid")`  
→ 替换为 name 唯一。

---

## 21. 附录 C：变更记录

| 日期 | 说明 |
|------|------|
| 2026-08-12 | 初稿（后移入插件 plans） |
| 2026-08-12 | 修订：全部路径绝对化；大幅补充历史背景、现状全景、迁移伪代码、命令/工具/Agent 细节、踩坑与附录 |
| 2026-08-12 | **代码级复核补充**：新增 §4.9.1（`strategy.py` 生产 0 调用的实证）、§4.9.2（核验事实表）、§6.3 保留名边界 N1–N10、§7.3 `ws_bot_id`/`bot_self_id` 缺失会静默错投的论证 + 播报边界表、§7.4.5 钩子 priority、§7.4.6 `exec_list` 静默失败与列探测、§7.4.7 Kanban 树迁移决策、§7.4.8 迁移边界 M1–M10、§9.0 策略生效点 A/B/C、§12.1.1 读写工具解析优先级分离、§13.5 成本闸、§17.2 追加踩坑 21–30 |

---

**下一步**：确认 §17.1 默认决策 A–K 后，按 §15 Phase 0 开工。若推送必须 100% 改走 `gs_subscribe`，先修订 §10 再实现。
