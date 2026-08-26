# 六、模拟盘 papertrade

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · **上一章**：[五](./05-ai-tools-and-agents.md) · **下一章**：[七、配置/数据库/缓存](./07-config-database-cache.md)

用户文档见 `docs/papertrade.md` 与 `stock_papertrade/PAPERTRADE_GUIDE.md`。  
本章是**开发者视角**。

## 6.1 设计哲学

- **观察型**：用户建盘 / 换策略 / 启停 + 只读查询；**不能**干预 AI 的单笔买卖  
- **命名账户**：`SayuPaperAccount.name` 唯一，`account_id` 是所有子表的分区键；
  **盘不绑群**——群只作为播报订阅方存在（`SayuPaperBroadcastTarget`）  
- **真源 SQLModel**：账户/持仓/流水/决策/快照/候选池/订阅表；**禁止** `record_*` 平行账本  
- **建账唯一入口**：trigger `send_init_command` / `send_create_account`（幂等）；
  agent 不直接写半截状态  

## 6.2 模块地图

```
stock_papertrade/
├── __init__.py          # ai_alias / KB / gate 注册 + import 子模块
├── sv.py                # sv_papertrade (pm=3) / sv_papertrade_admin (pm=0)
├── commands.py          # 用户命令（建盘/改名/删除/启停/策略/订阅/查询）
├── admin.py             # 清盘 + master 压测（dry-run 用独立临时盘）
├── permissions.py
├── account_scope.py     # 盘名归一/校验 + 读写两条账户解析路径 + 写授权
├── broadcast.py         # 按 account_id 扇出到订阅群（组装可投递 Event）
├── cross_group.py       # 跨盘排行 / 按盘名或群反查
├── db.py                # 仓储封装（含 PaperBroadcastRepo）
├── quote_service.py     # 报价（语义 parse）
├── trade_executor.py    # 下单执行
├── matcher.py           # 撮合
├── strategy.py          # 评分 / 风控参数（**仅测试与 gate 复用，不在生产决策路径**）
├── strategies/          # 策略包：注册表 + 每策略工具清单 / 硬闸 / Kanban 流程
├── indicators.py        # 模拟盘侧指标封装
├── candidate_pool.py    # 候选池
├── trading_calendar.py  # A 股交易日/时段
├── proactive.py         # 主动播报等
├── render.py            # 账户/收益图
├── ai_tools.py          # @ai_tools 读写
└── PAPERTRADE_GUIDE.md  # 注入人格的操作指南
```

## 6.3 数据表（`utils/database/papertrade_models.py`）

导入副作用：`utils/database/models.py` 会 import papertrade 表 + WebConsole Admin。

| 表（类） | 用途 |
|----------|------|
| `SayuPaperAccount` | **一行一个命名盘**：`name`（唯一）、`strategy_id` / `strategy_params`、现金、模式、enabled、Kanban root id… |
| `SayuPaperPosition` | 持仓（`account_id`） |
| `SayuPaperTrade` | 成交流水（`account_id`） |
| `SayuPaperDecision` | 决策日志 / reasoning（`account_id`） |
| `SayuPaperSnapshot` | 日净值（`account_id`） |
| `SayuPaperAgentPool` | 候选池（`account_id`） |
| `SayuPaperWatchlist` | 观察列表（`account_id`） |
| `SayuPaperBroadcastTarget` | 播报订阅：`account_id` × `group_id`，**必须含 `ws_bot_id` / `bot_self_id`** |

子表里保留的 `group_id` / `bot_id` **只是建盘原群的排障字段**，不参与查询路由。

`ws_bot_id` / `bot_self_id` 不是可选项：`emit_proactive_message` 靠它们找到连接实例并拼
`session_id`，缺了会投错群或直接投不出去。

WebConsole：`SayuPaper*Admin` 注册到管理后台。

### 迁移（`utils/database/papertrade_migration.py`）

- 加列走框架 `exec_list`（`trans_adapter`，priority `-80`），**失败是静默的**——
  所以迁移脚本必须自己 `PRAGMA table_info` 复核列在不在，不能假设加成功了
- 数据回填挂 `@on_core_start_before(priority=-70)`，排在建表(`-90`)/加列(`-80`)之后
- SQLite 的表级 `UNIQUE (group_id, bot_id)` 是 `sqlite_autoindex_*`，**DROP INDEX 删不掉**，
  只能读 DDL → 正则剥掉约束 → 建临时表 → 搬数据 → 换名（整个过程一个事务）
- 全流程幂等；孤儿子表行跳过并告警
- 启动时再跑 ``heal_orphan_ledger``：删幽灵仓；若现金−流水重算现金≈Σrealized_pnl，判定为旧卖出公式把盈亏加进了现金并更正；回写净值；补空的播报路由。master 也可发 ``模拟盘对账``。

## 6.4 用户命令

| 命令 | 说明 | 权限 |
|------|------|------|
| 模拟盘初始化 [资金] | 建/自愈默认盘 + 挂 Kanban/cron | 群主/管理 |
| 模拟盘创建 &lt;盘名&gt; [策略id] [资金] | 新建命名盘 | 群主/管理 |
| 模拟盘改名 / 删除 / 停用 / 启用 | 盘生命周期 | 群主/管理 |
| 模拟盘策略列表 / 模拟盘策略切换 &lt;盘名&gt; &lt;策略id&gt; | 策略管理（切换会重建心跳树） | 列表任何人 / 切换管理 |
| 模拟盘推送添加｜删除｜列表 | 当前群订阅/退订某盘的成交播报 | 增删管理 / 列表任何人 |
| 模拟盘列表 / 查看 &lt;盘名&gt; / 持仓 &lt;盘名&gt; / 收益 &lt;盘名&gt; / 记录 &lt;盘名&gt; | 只读查询 | 任何人 |
| 模拟盘排行 / 模拟盘查询 &lt;盘名&gt; | 跨盘排行 / 单盘明细 | 管理 |
| 模拟盘清盘 &lt;盘名&gt; / 模拟盘对账 / 模拟盘模拟测试 | 运维 | master（pm=0） |

权限 helpers：`permissions.user_pm_level` / `check_admin`。

针对某个盘的用户命令**必须带盘名**（`_require_named_account`），无参只回用法，
不回落到「默认模拟盘」。列表 / 排行 / 初始化默认盘除外。

⚠️ **`on_prefix` 不匹配"只有关键词、没有参数"的消息**（`_check_prefix` 显式排除了
fullmatch）。带参命令若也想支持裸发（给用法提示），必须**同时**叠一个 `on_fullmatch`；
`to_ai` 只挂在其中一个上，否则会注册两个同名 AI 工具。

⚠️ 插件级 `force_prefix=["a", "股票"]`：所有命令实际要发 `a模拟盘列表`。端侧联调脚本
（`test/_manual_ws_client.py`）不带前缀会静默掉进 AI 闲聊兜底，看起来像"命令没注册"。

## 6.5 周期心跳与日历

- `trading_calendar.py`：`is_a_share_trading_day` / `is_trading_time` / `trading_day_summary`  
- `__init__.py` 注册 **recurring gate**：非交易日不进 decision/pool/snapshot  
- 决策节奏约 30 分钟（交易时段内）；快照约 15:35；月报复盘约每月 1 日（以代码/配置为准）

## 6.6 决策与撮合流水线

```
候选池 → 行情(quote_service / Port)
      → 指标(indicators)
      → 财报/新闻(可选工具)
      → strategy 评分 (-1~+1) + 风控门
      → matcher / trade_executor
      → db **同一 session** 写流水+现金+持仓（禁止 LLM 并行 upsert 改股数）
      → 系统侧成交冒泡（非 agent 闲聊；流水失败则不播报、不建仓）
```

风控参数（单票仓位、日交易次数、止损、回撤熔断、现金缓冲）在 `strategy.py` 按模式
（平衡/激进/保守）声明；**用户命令不应暴露改参入口**（产品设计）。

> ⚠️ **`strategy.py::score_stock` / `decide_action` 不在生产路径上**——线上决策由
> 决策代理的 LLM prompt 驱动，这两个函数只有测试和策略硬闸在调。加规则只改
> `strategy.py` 等于什么都没改。

## 6.7 策略（`strategies/`）

每个策略自己声明：

| 成员 | 作用 |
|------|------|
| `extra_tools` / `decision_tools()` | 研究工具 + 共用账本工具。决策代理按这份清单挂工具 |
| `agent_profile` | Kanban / recurring gate / 写工具白名单 |
| `kanban_decision_task()` | 建树时的决策子任务全文（含 `research_phases`） |
| `pool_preference` | 候选池来源权重 |
| `validate_entry` / `gate_buy` / `gate_sell` | 落库硬闸。量能顶底必须走 `volume_structure` 函数 |

`stock_agent.register_papertrade_agents` 和 `commands._setup_papertrade_kanban_trees`
**不要再手写**策略工具列表或 Phase 4。加第三套策略：实现子类 → 进 `_REGISTRY`。

内置：`multi_factor`（财报/榜单/新闻工具）/ `volume_extremum`（`papertrade_volume_scan` +
月K/日K函数，不挂财报榜单）。

## 6.8 账户解析与写授权（`account_scope.py`）

**读路径和写路径用的是两套优先级，别混用**：

- 读（`resolve_account`）：显式 id → 精确盘名 → 唯一模糊匹配 → 默认盘 / 唯一盘；
  模糊命中多个时返回 None 而不是猜
- 写（`resolve_account_for_write`）：**只认 `root_task_id` 反查出来的盘**。
  LLM 传的 `account_name` 只用于一致性校验；没有任务上下文就直接拒写。
  这样 LLM 报错盘名最多被纠正，不会把 A 盘的成交写到 B 盘
- `grant_write(account_id)` 是 ContextVar 授权闸（dry-run / 立即决策用），
  存的是 **account_id 而不是 bool**——否则 dry-run 期间的写会漏到用户真盘上

## 6.9 AI 工具边界

模拟盘工具 `capability_domain="AI模拟盘"`：

- 查询：`account_list` / `account_query` / `position_list` / `trade_list` / `decision_list`…，
  都接受可选 `account_name`  
- 写入：仅决策链路与 setup trigger 应调用的 insert/upsert，账户由 `root_task_id` 决定  
- **stock_agent 研究代理禁止**用通用记忆工具记账  

建账 agent 确认文案**不要**写死「当前 0 持仓 / 现金 100w」——会被 artifact 永久引用；
实时数字必须查 SQLModel。

## 6.10 改模拟盘 checklist

1. 表结构变更：`papertrade_models.py` 模型 + `exec_list` 加列 + `papertrade_migration.py`
   回填（并复核列真的加上了）。  
2. 业务读写只经 `db.py` 的 repository，一律传 `account_id`。  
3. 新策略：三个生效点都实现 + 进注册表 + 补 `test_papertrade_strategies.py`。  
4. 日历 gate 与交易时段逻辑单测（`test_papertrade_calendar.py`）。  
5. 撮合/策略单测（`test_papertrade_matcher.py` / `test_papertrade_strategies.py`）。  
6. 更新 `PAPERTRADE_GUIDE.md`（人格 RAG）与用户 `docs/papertrade.md`。  
7. 端侧联调：`uv run core` + `python test/_manual_ws_client.py`（记得带 `a` 前缀、
   `user_pm=1` 才过 `check_admin`）。  
8. 不引入第二套持久化。  
