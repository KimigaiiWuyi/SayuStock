"""SayuStock 模拟盘数据库表。

8 张表全部继承 BaseIDModel（只 id 主键）。**账本主键是 ``account_id``**——
模拟盘是"命名的盘"，不是"群的财产"，同一个盘可以推送到任意多个群。
WebConsole admin 一次性挂到"SayuStock 模拟盘"菜单分组下。

迁移说明：本文件末尾通过 ``exec_list.extend`` 把 ALTER/CREATE INDEX 挂到
``on_core_start_before`` 阶段的 ``trans_adapter`` 内执行；既有库会自动补齐。
``trans_adapter`` 对每条 SQL 都 try/except pass，**失败是静默的**，所以数据
回填（``papertrade_migration.py``）必须自己探测列是否真的加上了。
"""

from typing import Optional
from datetime import date, datetime

from sqlmodel import Field
from sqlalchemy import UniqueConstraint

from gsuid_core.webconsole.mount_app import PageSchema, GsAdminModel, site
from gsuid_core.utils.database.startup import exec_list
from gsuid_core.utils.database.base_models import BaseIDModel

# 默认盘名：迁移时最早建的那个账户会被命名成它；工具/命令未指定盘名时也解析到它。
DEFAULT_ACCOUNT_NAME: str = "默认模拟盘"
# 默认策略 id（与 stock_papertrade.strategies 注册表对齐）
DEFAULT_STRATEGY_ID: str = "multi_factor"


# ============================================================
# 1) 账户表
# ============================================================
class SayuPaperAccount(BaseIDModel, table=True):
    """模拟盘账户（一个"命名的盘"，与群解耦）

    ``name`` 全库唯一：
    - 新库由 ``create_all`` 自动挂上 ``ux_sayupaperaccount_name``；
    - 老库通过文件末尾的 ``exec_list`` 跑 CREATE UNIQUE INDEX 兜底补齐。

    ``group_id`` / ``bot_id`` 保留但语义降级为**创建原群 / 心跳 bot**，
    不再是账本主键；播报目标由 ``SayuPaperBroadcastTarget`` 决定。
    """

    __table_args__ = (
        UniqueConstraint("name", name="ux_sayupaperaccount_name"),
        {"extend_existing": True},
    )

    name: str = Field(default=DEFAULT_ACCOUNT_NAME, title="盘名（全库唯一）", index=True)
    strategy_id: str = Field(default=DEFAULT_STRATEGY_ID, title="策略 id", index=True)
    strategy_params: str = Field(default="{}", title="策略参数 JSON")
    group_id: str = Field(title="创建原群", index=True)
    bot_id: str = Field(title="平台", index=True)
    cash: float = Field(default=1_000_000.0, title="现金余额")
    initial_cash: float = Field(default=1_000_000.0, title="期初本金")
    principal: float = Field(default=1_000_000.0, title="当前本金（=初始+已实现盈亏）")
    mode: str = Field(default="balanced", title="模式（balanced/aggressive/conservative）")
    frequency_minutes: int = Field(default=30, title="心跳频率(分钟)")
    enabled: int = Field(default=1, title="开关 0/1", index=True)
    kanban_init_root_id: Optional[str] = Field(default=None, title="init Kanban 根任务 ID")
    kanban_period_root_id: Optional[str] = Field(default=None, title="周期 Kanban 根任务 ID")
    initialized_by: Optional[str] = Field(default=None, title="初始化人 user_id")
    # 多账户迁移幂等标记：1 = 本行已跑过 v2 迁移，重启不再改名（用户改过的名字要保住）
    schema_migrated_v2: int = Field(default=0, title="多账户迁移标记 0/1")
    created_at: Optional[datetime] = Field(default=None, title="创建时间")
    started_at: Optional[datetime] = Field(default=None, title="首次交易时间")
    last_decided_at: Optional[datetime] = Field(default=None, title="上次决策时间")


# ============================================================
# 2) 持仓表
# ============================================================
class SayuPaperPosition(BaseIDModel, table=True):
    """模拟盘持仓（每盘每股票最多一行）"""

    __table_args__ = {"extend_existing": True}

    account_id: int = Field(default=0, title="模拟盘账户 ID", index=True)
    group_id: str = Field(default="", title="创建原群（仅排障）", index=True)
    bot_id: str = Field(default="", title="平台（仅排障）", index=True)
    stock_code: str = Field(title="股票代码", index=True)
    stock_name: str = Field(default="", title="名称")
    secid: str = Field(default="", title="东财 secid")
    qty: int = Field(default=0, title="持仓股数（100 整手）")
    avg_cost: float = Field(default=0.0, title="加权平均成本")
    # 2026-07-01 新增：报价缓存。决策代理 / refresh tool 拿到的最新价
    # 直接落库，让 ``papertrade_position_list`` / ``papertrade_account_query``
    # 即使在不开盘 / API 暂时不可达时也能算出持仓市值和浮盈。
    # - 老库通过文件末尾 ``exec_list`` 的 ALTER TABLE 加列（trans_adapter 兜底）
    # - 旧数据全为 None；首次刷新前用 ``avg_cost`` 兜底显示（``quote_source='cost'``）
    last_quote_price: Optional[float] = Field(default=None, title="最新报价缓存")
    last_quote_at: Optional[datetime] = Field(default=None, title="报价时间戳")
    opened_at: Optional[datetime] = Field(default=None, title="首次建仓时间")
    updated_at: Optional[datetime] = Field(default=None, title="更新时间")


# ============================================================
# 3) 交易流水表（append-only）
# ============================================================
class SayuPaperTrade(BaseIDModel, table=True):
    """模拟盘交易流水（append-only；不可改、不可删）"""

    __table_args__ = {"extend_existing": True}

    account_id: int = Field(default=0, title="模拟盘账户 ID", index=True)
    group_id: str = Field(default="", title="创建原群（仅排障）", index=True)
    bot_id: str = Field(default="", title="平台（仅排障）", index=True)
    stock_code: str = Field(title="股票代码", index=True)
    stock_name: str = Field(default="", title="名称")
    secid: str = Field(default="", title="东财 secid")
    side: str = Field(title="方向 buy/sell")
    price: float = Field(title="成交价")
    qty: int = Field(title="成交股数")
    amount: float = Field(title="成交金额 = price*qty")
    fee: float = Field(default=0.0, title="手续费（佣金+印花税）")
    realized_pnl: float = Field(default=0.0, title="已实现盈亏（仅 sell）")
    reason: str = Field(default="", title="AI 决策理由")
    snapshot: str = Field(default="", title="决策时指标快照 JSON")
    decided_at: datetime = Field(default_factory=datetime.now, title="决策时间", index=True)
    executed_at: datetime = Field(default_factory=datetime.now, title="成交时间")
    decision_id: Optional[int] = Field(default=None, title="关联决策日志 ID")
    mode: str = Field(default="balanced", title="下单时风控模式")


# ============================================================
# 4) 决策日志表（append-only）
# ============================================================
class SayuPaperDecision(BaseIDModel, table=True):
    """模拟盘决策日志（每次心跳每个标的写一条；action=hold 也写）"""

    __table_args__ = {"extend_existing": True}

    account_id: int = Field(default=0, title="模拟盘账户 ID", index=True)
    group_id: str = Field(default="", title="创建原群（仅排障）", index=True)
    bot_id: str = Field(default="", title="平台（仅排障）", index=True)
    action: str = Field(title="buy/sell/hold", index=True)
    stock_code: Optional[str] = Field(default=None, title="股票代码", index=True)
    stock_name: Optional[str] = Field(default=None, title="名称")
    score: float = Field(default=0.0, title="策略评分 -1.0~1.0")
    reason: str = Field(default="", title="完整 reasoning（AI 原始输出）")
    indicators: str = Field(default="", title="指标快照 JSON")
    trade_id: Optional[int] = Field(default=None, title="实际执行则关联 Trade.id")
    blocked_by: str = Field(default="", title="风控拦截原因")
    created_at: datetime = Field(default_factory=datetime.now, title="决策时间", index=True)


# ============================================================
# 5) 每日净值快照表（append-only）
# ============================================================
class SayuPaperSnapshot(BaseIDModel, table=True):
    """模拟盘每日净值快照（15:30 收盘后写）"""

    __table_args__ = {"extend_existing": True}

    account_id: int = Field(default=0, title="模拟盘账户 ID", index=True)
    group_id: str = Field(default="", title="创建原群（仅排障）", index=True)
    bot_id: str = Field(default="", title="平台（仅排障）", index=True)
    trade_date: date = Field(title="交易日", index=True)
    cash: float = Field(title="当日现金")
    position_value: float = Field(title="当日持仓市值")
    total_equity: float = Field(title="当日总资产 = cash + position_value")
    day_pnl: float = Field(default=0.0, title="当日盈亏")
    day_pnl_pct: float = Field(default=0.0, title="当日收益率 %")
    total_pnl: float = Field(default=0.0, title="累计盈亏（相对 initial_cash）")
    total_pnl_pct: float = Field(default=0.0, title="累计收益率 %")
    created_at: datetime = Field(default_factory=datetime.now, title="写入时间")


# ============================================================
# 6) 群友关注列表（公开可查）
# ============================================================
class SayuPaperWatchlist(BaseIDModel, table=True):
    """群友关注列表（@机器人 模拟盘自选 可查）

    归属于**盘**而非群：候选池把它当作"永不淘汰的保护集"
    （``candidate_pool._from_watchlist``），跟着盘走才能让不同策略的盘
    拥有各自的保护集。副作用：同一个群的关注在不同盘之间互不可见。
    """

    __table_args__ = {"extend_existing": True}

    account_id: int = Field(default=0, title="模拟盘账户 ID", index=True)
    group_id: str = Field(default="", title="添加时所在群", index=True)
    bot_id: str = Field(default="", title="平台", index=True)
    user_id: str = Field(title="添加者 user_id", index=True)
    stock_code: str = Field(title="股票代码", index=True)
    stock_name: str = Field(default="", title="名称")
    secid: str = Field(default="", title="东财 secid")
    note: str = Field(default="", title="备注")
    created_at: datetime = Field(default_factory=datetime.now, title="添加时间")


# ============================================================
# 7) AI 内部决策池（私有，不对外暴露）
# ============================================================
class SayuPaperAgentPool(BaseIDModel, table=True):
    """AI 内部关注池（每心跳后维护；带 expires_at 自动过期）"""

    __table_args__ = {"extend_existing": True}

    account_id: int = Field(default=0, title="模拟盘账户 ID", index=True)
    group_id: str = Field(default="", title="创建原群（仅排障）", index=True)
    bot_id: str = Field(default="", title="平台（仅排障）", index=True)
    stock_code: str = Field(title="股票代码", index=True)
    stock_name: str = Field(default="", title="名称")
    secid: str = Field(default="", title="东财 secid")
    reason: str = Field(default="", title="加入池的原因")
    added_by: str = Field(default="ai", title="ai / user")
    priority: int = Field(default=0, title="优先级 0~10")
    expires_at: Optional[datetime] = Field(default=None, title="过期时间")
    created_at: datetime = Field(default_factory=datetime.now, title="加入时间")


# ============================================================
# 8) 播报目标表（一个盘 → 任意多个群）
# ============================================================
class SayuPaperBroadcastTarget(BaseIDModel, table=True):
    """模拟盘播报目标（成交冒泡推到哪些群）。

    ``ws_bot_id`` / ``bot_self_id`` 不是冗余：``emit_proactive_message`` 靠
    ``Event.WS_BOT_ID`` 在 ``gss.active_bot`` 里选连接，选不到就
    ``next(iter(...))`` 随便挑一条——多适配器部署下会把 A 平台的播报从 B 平台
    发出去，消息静默丢失。``Event.session_id`` 也是
    ``{WS_BOT_ID}:{bot_id}:{bot_self_id}:group:{group_id}``，缺字段会拼出对不上
    的伪会话，让心跳去重与 AI 会话历史都记错账。

    这三个字段在「模拟盘推送添加」命令执行时从当前 ``ev`` 直接抄——命令就是在
    目标群里发的，此刻的 ev 正是"这个群收得到消息"的权威来源。迁移种子没有 ev
    可抄，留空串退化到兜底连接，属可接受降级。
    """

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "bot_id",
            "group_id",
            name="ux_sayupaperbroadcast_acc_bot_group",
        ),
        {"extend_existing": True},
    )

    account_id: int = Field(title="模拟盘账户 ID", index=True)
    bot_id: str = Field(default="", title="发送用 bot（适配器名）", index=True)
    bot_self_id: str = Field(default="", title="发送用 bot 自身账号")
    ws_bot_id: str = Field(default="", title="WS 连接 ID")
    group_id: str = Field(default="", title="目标群号", index=True)
    enabled: int = Field(default=1, title="开关 0/1", index=True)
    created_by: Optional[str] = Field(default=None, title="添加者 user_id")
    created_at: datetime = Field(default_factory=datetime.now, title="添加时间")


# ============================================================
# WebConsole 注册（一次性挂到 "SayuStock 模拟盘" 菜单分组）
# ============================================================
@site.register_admin
class SayuPaperAccountAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·账户",
        icon="fa fa-bullhorn",
    )
    model = SayuPaperAccount


@site.register_admin
class SayuPaperPositionAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·持仓",
        icon="fa fa-bullhorn",
    )
    model = SayuPaperPosition


@site.register_admin
class SayuPaperTradeAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·交易流水",
        icon="fa fa-bullhorn",
    )
    model = SayuPaperTrade


@site.register_admin
class SayuPaperDecisionAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·决策日志",
        icon="fa fa-bullhorn",
    )
    model = SayuPaperDecision


@site.register_admin
class SayuPaperSnapshotAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·净值快照",
        icon="fa fa-bullhorn",
    )
    model = SayuPaperSnapshot


@site.register_admin
class SayuPaperWatchlistAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·群友关注",
        icon="fa fa-bullhorn",
    )
    model = SayuPaperWatchlist


@site.register_admin
class SayuPaperAgentPoolAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·内部池",
        icon="fa fa-bullhorn",
    )
    model = SayuPaperAgentPool


@site.register_admin
class SayuPaperBroadcastTargetAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·推送目标",
        icon="fa fa-bullhorn",
    )
    model = SayuPaperBroadcastTarget


# ============================================================
# 迁移 SQL（在 on_core_start_before 阶段的 trans_adapter 内执行）
# 全部幂等：trans_adapter 对每条都 try/except pass，所以 SQLite/MySQL/PG
# 任一不识别的 ALTER 报错都不会阻塞启动。
# ============================================================
exec_list.extend(
    [
        # 持仓报价缓存列。不要在这里 DELETE 同群多行或重建 (group_id, bot_id)
        # 唯一索引：多盘后同 origin 群会有多行，每启动跑一次会把第二盘删掉。
        "ALTER TABLE sayupaperposition ADD COLUMN last_quote_price REAL;",
        "ALTER TABLE sayupaperposition ADD COLUMN last_quote_at DATETIME;",
        "ALTER TABLE sayupaperposition ADD COLUMN last_quote_price DOUBLE;",
        "ALTER TABLE sayupaperposition ADD COLUMN last_quote_at DATETIME;",
    ]
)

# ============================================================
# 2026-08-12 迁移：多账户（去群主键）
#
# 顺序说明（不能乱）：
#   1. 先给账户表加 name / strategy_id / strategy_params / schema_migrated_v2；
#   2. 再给 6 张子表加 account_id；
#   3. 唯一索引 ux_sayupaperaccount_name **不在这里建** —— 必须等
#      papertrade_migration 把重名先修掉，否则老库有多行同为默认值
#      '默认模拟盘' 时索引建不上（静默失败），后续创建第二个盘就查不出重名。
#      建索引的动作放在 backfill 结尾（Python 侧，能看到失败）。
#   4. DROP 旧的 (group_id, bot_id) 唯一索引 —— 不删掉就没法在同一个群里
#      开第二个盘（本次改造的核心诉求）。同样在 backfill 里复验。
# ============================================================
_V2_CHILD_TABLES: tuple[str, ...] = (
    "sayupaperposition",
    "sayupapertrade",
    "sayupaperdecision",
    "sayupapersnapshot",
    "sayupaperwatchlist",
    "sayupaperagentpool",
)

exec_list.extend(
    [
        # ─── 账户表新列 ───
        f"ALTER TABLE sayupaperaccount ADD COLUMN name VARCHAR(64) DEFAULT '{DEFAULT_ACCOUNT_NAME}';",
        f"ALTER TABLE sayupaperaccount ADD COLUMN strategy_id VARCHAR(64) DEFAULT '{DEFAULT_STRATEGY_ID}';",
        "ALTER TABLE sayupaperaccount ADD COLUMN strategy_params TEXT DEFAULT '{}';",
        "ALTER TABLE sayupaperaccount ADD COLUMN schema_migrated_v2 INTEGER DEFAULT 0;",
        # ─── 旧唯一索引：三方言各试一次（DROP 失败由 backfill 复验并报错） ───
        "DROP INDEX IF EXISTS ux_sayupaperaccount_gid_bid;",
        "ALTER TABLE sayupaperaccount DROP INDEX ux_sayupaperaccount_gid_bid;",
    ]
    # ─── 6 张子表加 account_id ───
    + [f"ALTER TABLE {t} ADD COLUMN account_id INTEGER DEFAULT 0;" for t in _V2_CHILD_TABLES]
    + [f"CREATE INDEX IF NOT EXISTS ix_{t}_account_id ON {t} (account_id);" for t in _V2_CHILD_TABLES]
)
