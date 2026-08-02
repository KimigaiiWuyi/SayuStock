# 六、模拟盘 papertrade

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · **上一章**：[五](./05-ai-tools-and-agents.md) · **下一章**：[七、配置/数据库/缓存](./07-config-database-cache.md)

用户文档见 `docs/papertrade.md` 与 `stock_papertrade/PAPERTRADE_GUIDE.md`。  
本章是**开发者视角**。

## 6.1 设计哲学

- **观察型**：用户初始化一次 + 只读查询；**不能**干预 AI 买卖规则  
- **群账户**：默认一群一盘；可配置全服共用（`papertrade_multi_group`）  
- **真源 SQLModel**：账户/持仓/流水/决策/快照/候选池表；**禁止** `record_*` 平行账本  
- **建账唯一入口**：trigger `send_init_command`（幂等）；agent 不直接写半截状态  

## 6.2 模块地图

```
stock_papertrade/
├── __init__.py          # ai_alias / KB / gate 注册 + import 子模块
├── sv.py                # sv_papertrade (pm=3) / sv_papertrade_admin (pm=0)
├── commands.py          # 用户命令
├── admin.py             # master 压测
├── permissions.py
├── account_scope.py     # 多群/共用 scope 解析
├── cross_group.py       # 排行 / 跨群查询
├── db.py                # 仓储封装
├── quote_service.py     # 报价（语义 parse）
├── trade_executor.py    # 下单执行
├── matcher.py           # 撮合
├── strategy.py          # 评分 / 风控参数
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
| `SayuPaperAccount` | 现金、模式、enabled、Kanban root id… |
| `SayuPaperPosition` | 持仓 |
| `SayuPaperTrade` | 成交流水 |
| `SayuPaperDecision` | 决策日志 / reasoning |
| `SayuPaperSnapshot` | 日净值 |
| `SayuPaperAgentPool` | 候选池 |
| `SayuPaperWatchlist` | 观察列表（若使用） |

WebConsole：`SayuPaper*Admin` 注册到管理后台。

## 6.4 用户命令（只读为主）

| 命令 | 说明 | 权限 |
|------|------|------|
| AI操盘初始化 [资金] | 开户 + 挂 Kanban/cron | 群主/管理 |
| AI操盘查看 | 账户+持仓图 | 任何人 |
| AI操盘收益 日/周/月… | 区间盈亏 | 任何人 |
| AI操盘记录 | 最近流水 | 任何人 |
| AI操盘排行 | 跨群 TOP | 管理 |
| AI操盘查询 &lt;group_id&gt; | 查他群 | 管理 |

权限 helpers：`permissions.user_pm_level` / `check_admin`。

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
      → db 写持仓/流水/决策
      → 系统侧成交冒泡（非 agent 闲聊）
```

风控参数（单票仓位、日交易次数、止损、回撤熔断、现金缓冲）在 `strategy.py` 按模式
（平衡/激进/保守）切换；**用户命令不应暴露改参入口**（产品设计）。

## 6.7 Scope 与多群

`account_scope.py` + 配置：

- `papertrade_multi_group=False`（默认）：全服共用一个盘；`papertrade_broadcast_group` 可指定播报群  
- `True`：每群独立账户  

改 scope 解析时同步：查询命令、心跳、播报、跨群排行。

## 6.8 AI 工具边界

模拟盘工具 `capability_domain="AI模拟盘"`：

- 查询：account / position / trade / decision list  
- 写入：仅决策链路与 setup trigger 应调用的 insert/upsert  
- **stock_agent 研究代理禁止**用通用记忆工具记账  

建账 agent 确认文案**不要**写死「当前 0 持仓 / 现金 100w」——会被 artifact 永久引用；
实时数字必须查 SQLModel。

## 6.9 改模拟盘 checklist

1. 表结构变更：模型 + 迁移/启动钩子（若 Core 有 schema 升级约定）。  
2. 业务读写只经 `db.py` 或清晰的 repository。  
3. 日历 gate 与交易时段逻辑单测（`test_papertrade_calendar.py`）。  
4. 撮合/策略单测（`test_papertrade_matcher.py` / `strategy`）。  
5. 更新 `PAPERTRADE_GUIDE.md` 与用户 `docs/papertrade.md`。  
6. 不引入第二套持久化。  
