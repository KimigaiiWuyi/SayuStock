"""数据来源标签：provider id → 展示名与结果盖章。"""

from __future__ import annotations

from SayuStock.utils.market.enums import AssetClass, KlinePeriod
from SayuStock.utils.market.models import Quote, SymbolRef, KlineSeries
from SayuStock.utils.market.display import PROVIDER_DISPLAY, source_label, stamp_provider


def _sym(secid: str = "1.600519") -> SymbolRef:
    return SymbolRef(
        code=secid.split(".")[-1],
        name="贵州茅台",
        asset_class=AssetClass.EQUITY,
        exchange="SSE",
        provider_symbol=secid,
    )


def _quote() -> Quote:
    return Quote(
        symbol=_sym(),
        price=1.0,
        open=None,
        high=None,
        low=None,
        prev_close=None,
        change_pct=None,
        change_amount=None,
        volume=None,
        amount=None,
        turnover_rate=None,
        pe=None,
        pb=None,
        market_cap=None,
        float_market_cap=None,
        industry=None,
        limit_up=None,
        limit_down=None,
        as_of=None,
    )


def test_source_label() -> None:
    assert source_label("eastmoney") == "东方财富"
    assert source_label("tencent") == "腾讯财经"
    assert source_label("sina") == "新浪财经"
    assert source_label("tiantian") == "天天基金"
    assert source_label("okx") == "OKX"
    # 多源去重拼接
    assert source_label("eastmoney", "tencent") == "东方财富、腾讯财经"
    assert source_label("sina", "sina") == "新浪财经"
    # 无来源信息回退历史口径；未知 id 原样展示
    assert source_label(None, "") == "东方财富"
    assert source_label("unknown_src") == "unknown_src"


def test_stamp_provider() -> None:
    q = stamp_provider(_quote(), "tencent")
    assert isinstance(q, Quote) and q.provider == "tencent"
    # 已有值不覆盖
    q2 = stamp_provider(q, "sina")
    assert q2.provider == "tencent"
    # 非模型对象原样返回
    assert stamp_provider("eastmoney:quote", "eastmoney") == "eastmoney:quote"
    # K 线序列也可盖章
    kl = stamp_provider(KlineSeries(symbol=_sym(), period=KlinePeriod.D1, bars=(), adjusted=False), "sina")
    assert kl.provider == "sina"


def test_provider_display_covers_all_sources() -> None:
    for pid in ("eastmoney", "tencent", "sina", "tiantian", "okx", "vix"):
        assert pid in PROVIDER_DISPLAY
