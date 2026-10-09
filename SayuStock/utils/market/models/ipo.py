"""IPO 日历事件。"""

from __future__ import annotations

from datetime import date
from dataclasses import dataclass

from ..enums import IpoStage, IpoMarket


@dataclass(frozen=True, slots=True)
class IpoEvent:
    """一次 IPO 的供应商无关视图；阶段不落库，按查看日推导。

    申购语义按市场不同：A股为单日（``apply_date``）；港股为多日招股期，
    有数据时 ``apply_end_date`` 是招股截止日（开始日缺失，渲染层按惯例
    前推 3 天画出申购窗口）。
    """

    market: IpoMarket
    code: str
    name: str
    listing_date: date | None = None  # 上市日 / 预期定价·上市日
    apply_date: date | None = None  # 申购日（A股）/ 招股开始（有数据时）
    apply_end_date: date | None = None  # 申购截止（港股招股截止日）
    ballot_date: date | None = None  # 中签公布（A股）/ 公布售股结果
    pay_date: date | None = None  # 中签缴款日（A股）
    grey_market_date: date | None = None  # 暗盘日（港股）
    filed_date: date | None = None  # 美股申报日（纳斯达克源）
    issue_price: float | None = None
    issue_price_text: str | None = None  # 招股价区间文本，如 "39-44"
    raise_yi: float | None = None  # 募资额（亿，货币见 currency）
    currency: str = "CNY"
    first_day_change: float | None = None  # 已上市首日收盘涨跌幅 %
    oversubscription: float | None = None  # 公开发售超购倍数（港股）
    board: str | None = None  # 创业板 / 科创板 / 纳斯达克 …

    def stage_on(self, anchor: date) -> IpoStage:
        if self.listing_date is not None and self.listing_date <= anchor:
            return IpoStage.LISTED
        apply_end = self.apply_end_date or self.apply_date
        if apply_end is not None and apply_end >= anchor:
            # 申购日/招股截止日未过 = 申购阶段（含截止日当天）
            return IpoStage.APPLY
        if self.filed_date is not None and self.listing_date is None:
            return IpoStage.FILED
        return IpoStage.PENDING
