"""宏观重大事件表（全库一张，不按模拟盘分区）。

关税战 / 地缘冲突 / 央行决议 / 油价冲击这类事件同时影响所有盘、所有持仓分析，
所以它不是账户子表：``SayuMacroEvent`` 只有一份，模拟盘决策代理、持仓分析代理、
研究代理都读同一张表。

生命周期：``status="open"``（进行中）→ 宏观心跳按 ``check_interval_hours`` 联网复核
并改写 ``result`` → 事态落定后 ``status="settled"``（盖棺定论，此后不再联网查）。

新表由框架 ``create_all``（``on_core_start_before`` priority -90）自动建，老库无需迁移。
"""

from typing import Optional
from datetime import datetime

from sqlmodel import Field
from sqlalchemy import UniqueConstraint

from gsuid_core.webconsole.mount_app import PageSchema, GsAdminModel, site
from gsuid_core.utils.database.base_models import BaseIDModel

MACRO_STATUS_OPEN: str = "open"
MACRO_STATUS_SETTLED: str = "settled"
MACRO_STATUSES: tuple[str, ...] = (MACRO_STATUS_OPEN, MACRO_STATUS_SETTLED)

# 分类只用于给 LLM / 用户归档，不进任何硬闸；未知分类归 other。
MACRO_CATEGORIES: tuple[str, ...] = (
    "trade",  # 关税 / 贸易战 / 制裁
    "geopolitics",  # 战争 / 地缘冲突 / 航道封锁
    "monetary",  # 央行决议 / 利率 / 流动性
    "commodity",  # 原油 / 黄金 / 大宗
    "policy",  # 国内产业与财政政策
    "macro_data",  # PPI / CPI / PMI / GDP 等数据拐点
    "other",
)

# 对 A 股风险偏好的方向：risk_off 收缩仓位，risk_on 允许进攻，mixed/neutral 按中性档。
MACRO_DIRECTIONS: tuple[str, ...] = ("risk_off", "risk_on", "mixed", "neutral")

MACRO_SEVERITY_MIN: int = 1
MACRO_SEVERITY_MAX: int = 5
# 达到该级别且 risk_off 的进行中事件，会让多因子买入硬闸拒绝「进攻档」
MACRO_SEVERE_LEVEL: int = 4
DEFAULT_CHECK_INTERVAL_HOURS: int = 6


class SayuMacroEvent(BaseIDModel, table=True):
    """宏观重大事件（全局一张表）。

    ``slug`` 全库唯一：LLM 反复写同一事件时用它幂等 upsert，避免同一场关税战
    在表里出现五条。
    """

    __table_args__ = (
        UniqueConstraint("slug", name="ux_sayumacroevent_slug"),
        {"extend_existing": True},
    )

    slug: str = Field(title="事件唯一键（小写字母/数字/下划线）", index=True)
    title: str = Field(title="事件标题")
    category: str = Field(default="other", title="分类", index=True)
    status: str = Field(default=MACRO_STATUS_OPEN, title="open=进行中 / settled=盖棺定论", index=True)
    severity: int = Field(default=3, title="严重度 1~5（4+ 视为高危）")
    direction: str = Field(default="mixed", title="对 A 股风险偏好：risk_off/risk_on/mixed/neutral")
    affected_sectors: str = Field(default="", title="受影响板块（逗号分隔）")
    summary: str = Field(default="", title="事件是什么（稳定描述）")
    result: str = Field(default="", title="最新进展 / 最终结论（每次心跳改写）")
    stance: str = Field(default="", title="对操作的含义（一句话）")
    source_urls: str = Field(default="", title="最近复核来源 URL（换行分隔）")
    check_interval_hours: int = Field(default=DEFAULT_CHECK_INTERVAL_HOURS, title="复核间隔（小时）")
    created_by: str = Field(default="ai", title="创建者：ai / user:<id> / seed")
    created_at: datetime = Field(default_factory=datetime.now, title="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, title="最近更新时间")
    last_checked_at: Optional[datetime] = Field(default=None, title="最近联网复核时间")
    settled_at: Optional[datetime] = Field(default=None, title="盖棺定论时间")


@site.register_admin
class SayuMacroEventAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="模拟盘·宏观事件",
        icon="fa fa-bullhorn",
    )
    model = SayuMacroEvent
