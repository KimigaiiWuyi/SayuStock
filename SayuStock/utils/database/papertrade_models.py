"""SayuStock 模拟盘数据库表。

7 张表全部继承 BaseIDModel（只 id 主键），按 (group_id, bot_id, ...) 分区
实现多群数据隔离。WebConsole admin 一次性挂到"SayuStock 模拟盘"菜单分组下。

迁移说明：本文件末尾通过 ``exec_list.extend`` 把 ALTER/CREATE INDEX 挂到
``on_core_start_before`` 阶段的 ``trans_adapter`` 内执行；既有库会自动补齐。
"""

from typing import Optional
from datetime import date, datetime

from sqlmodel import Field
from sqlalchemy import UniqueConstraint

from gsuid_core.webconsole.mount_app import PageSchema, GsAdminModel, site
from gsuid_core.utils.database.startup import exec_list
from gsuid_core.utils.database.base_models import BaseIDModel


# ============================================================
# 1) 账户表
# ============================================================
class SayuPaperAccount(BaseIDModel, table=True):
    """模拟盘账户（每群每 bot 一份）

    ``(group_id, bot_id)`` 复合唯一约束：
    - 新库由 ``create_all`` 自动挂上 ``ux_sayupaperaccount_gid_bid``；
    - 老库通过文件末尾的 ``exec_list`` 跑 CREATE UNIQUE INDEX 兜底补齐。
    """

    __table_args__ = (
        UniqueConstraint("group_id", "bot_id", name="ux_sayupaperaccount_gid_bid"),
        {"extend_existing": True},
    )

    group_id: str = Field(title="群号", index=True)
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
    created_at: Optional[datetime] = Field(default=None, title="创建时间")
    started_at: Optional[datetime] = Field(default=None, title="首次交易时间")
    last_decided_at: Optional[datetime] = Field(default=None, title="上次决策时间")


# ============================================================
# 2) 持仓表
# ============================================================
class SayuPaperPosition(BaseIDModel, table=True):
    """模拟盘持仓（每群每 bot 每股票最多一行）"""

    __table_args__ = {"extend_existing": True}

    group_id: str = Field(title="群号", index=True)
    bot_id: str = Field(title="平台", index=True)
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

    group_id: str = Field(title="群号", index=True)
    bot_id: str = Field(title="平台", index=True)
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

    group_id: str = Field(title="群号", index=True)
    bot_id: str = Field(title="平台", index=True)
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

    group_id: str = Field(title="群号", index=True)
    bot_id: str = Field(title="平台", index=True)
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
    """群友关注列表（@机器人 模拟盘自选 可查）"""

    __table_args__ = {"extend_existing": True}

    group_id: str = Field(title="群号", index=True)
    bot_id: str = Field(title="平台", index=True)
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

    group_id: str = Field(title="群号", index=True)
    bot_id: str = Field(title="平台", index=True)
    stock_code: str = Field(title="股票代码", index=True)
    stock_name: str = Field(default="", title="名称")
    secid: str = Field(default="", title="东财 secid")
    reason: str = Field(default="", title="加入池的原因")
    added_by: str = Field(default="ai", title="ai / user")
    priority: int = Field(default=0, title="优先级 0~10")
    expires_at: Optional[datetime] = Field(default=None, title="过期时间")
    created_at: datetime = Field(default_factory=datetime.now, title="加入时间")


# ============================================================
# WebConsole 注册（一次性挂到 "SayuStock 模拟盘" 菜单分组）
# ============================================================
@site.register_admin
class SayuPaperAccountAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·账户",
        icon="fa fa-bullhorn",
    )  # type: ignore
    model = SayuPaperAccount


@site.register_admin
class SayuPaperPositionAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·持仓",
        icon="fa fa-bullhorn",
    )  # type: ignore
    model = SayuPaperPosition


@site.register_admin
class SayuPaperTradeAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·交易流水",
        icon="fa fa-bullhorn",
    )  # type: ignore
    model = SayuPaperTrade


@site.register_admin
class SayuPaperDecisionAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·决策日志",
        icon="fa fa-bullhorn",
    )  # type: ignore
    model = SayuPaperDecision


@site.register_admin
class SayuPaperSnapshotAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·净值快照",
        icon="fa fa-bullhorn",
    )  # type: ignore
    model = SayuPaperSnapshot


@site.register_admin
class SayuPaperWatchlistAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·群友关注",
        icon="fa fa-bullhorn",
    )  # type: ignore
    model = SayuPaperWatchlist


@site.register_admin
class SayuPaperAgentPoolAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·内部池",
        icon="fa fa-bullhorn",
    )  # type: ignore
    model = SayuPaperAgentPool


# ============================================================
# 迁移 SQL（在 on_core_start_before 阶段的 trans_adapter 内执行）
# 全部幂等：trans_adapter 对每条都 try/except pass，所以 SQLite/MySQL/PG
# 任一不识别的 ALTER 报错都不会阻塞启动。
# ============================================================
exec_list.extend(
    [
        # 兼容已建过 sayupaperaccount 但未挂上 UniqueConstraint 的部署：
        # 先清掉 (group_id, bot_id) 重复行，保留每个分区的最小 id；再补建唯一索引。
        # SQLite / MySQL / PG 通用语法，subquery alias 在 SQLite 旧版可能需要别名，
        # 用 INNER JOIN 形式兜底。
        'DELETE FROM sayupaperaccount '
        'WHERE id NOT IN ('
        '  SELECT MIN(id) FROM sayupaperaccount GROUP BY group_id, bot_id'
        ');',
        # CREATE UNIQUE INDEX 三方言通用
        'CREATE UNIQUE INDEX IF NOT EXISTS ux_sayupaperaccount_gid_bid '
        'ON sayupaperaccount (group_id, bot_id);',
        # MySQL 没有 IF NOT EXISTS 的 INDEX 语法，用 try 兜底
        'ALTER TABLE sayupaperaccount '
        'ADD UNIQUE INDEX ux_sayupaperaccount_gid_bid (group_id, bot_id);',
        # PostgreSQL
        'CREATE UNIQUE INDEX IF NOT EXISTS ux_sayupaperaccount_gid_bid '
        'ON sayupaperaccount (group_id, bot_id);',
        # ─── 2026-07-01 迁移：SayuPaperPosition 加 last_quote_price + last_quote_at ───
        # SQLite / PostgreSQL 老版本不支持 ADD COLUMN IF NOT EXISTS；trans_adapter 已经
        # 用 try/except pass 兜底（重复执行 → 第二次失败无害），所以直接 ADD COLUMN 即可。
        'ALTER TABLE sayupaperposition ADD COLUMN last_quote_price REAL;',
        'ALTER TABLE sayupaperposition ADD COLUMN last_quote_at DATETIME;',
        # MySQL 同义（DATETIME 直接用，REAL → DOUBLE 也能存，但保留 REAL 跨方言一致）
        'ALTER TABLE sayupaperposition ADD COLUMN last_quote_price DOUBLE;',
        'ALTER TABLE sayupaperposition ADD COLUMN last_quote_at DATETIME;',
    ]
)
