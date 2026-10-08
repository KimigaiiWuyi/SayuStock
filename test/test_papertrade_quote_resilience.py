"""模拟盘风控韧性单测：东财限流时不能写入无法验证的成交价。

背景：东财对同一 IP 高频请求会回 ``-400016``，此时 ``quote_service`` 一律
返回 ``None``。两处必须扛住：

1. ``record_trade`` 的实时价偏差校验。早先实现是"拿不到价就跳过校验"，
   等于限流期间完全失守——LLM 不调 match_order 直接调 trade_insert 时，
   任意价格都能落库。
2. ``QuoteCacheEntry`` 的 TTL。失败条目若沿用 60s 成功 TTL，一次限流会把
   该票整整一分钟锁死，即便东财早已恢复也照样拒单。
"""

import sys
import asyncio
import importlib.util
from types import ModuleType
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_papertrade_quote_test"


def _ensure_pkg() -> None:
    if PKG_NAME in sys.modules:
        return
    pkg_spec = importlib.util.spec_from_file_location(
        PKG_NAME, PKG_ROOT / "__init__.py", submodule_search_locations=[str(PKG_ROOT)]
    )
    assert pkg_spec is not None
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__path__ = [str(PKG_ROOT)]
    sys.modules[PKG_NAME] = pkg
    sub_spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.stock_papertrade",
        PKG_ROOT / "stock_papertrade" / "__init__.py",
        submodule_search_locations=[str(PKG_ROOT / "stock_papertrade")],
    )
    assert sub_spec is not None
    sub = importlib.util.module_from_spec(sub_spec)
    sub.__path__ = [str(PKG_ROOT / "stock_papertrade")]
    sys.modules[f"{PKG_NAME}.stock_papertrade"] = sub


def _load(name: str, file_name: str) -> ModuleType:
    _ensure_pkg()
    spec = importlib.util.spec_from_file_location(f"{PKG_NAME}.stock_papertrade.{name}", PKG_ROOT / file_name)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


quote_service_mod = _load("quote_service", "stock_papertrade/quote_service.py")


def _run_record_trade(
    *,
    quote: float | None,
    price: float = 80.1,
    order: list[str] | None = None,
) -> tuple[object, object, list[dict]]:
    """桩掉行情 / DB，真跑一次 ``PaperTradeExecutor.record_trade``。

    ``quote=None`` 模拟东财限流（行情不可达）。返回 ``(RecordResult, db桩, 写库记录)``。
    """
    from unittest.mock import AsyncMock, patch

    executor_mod = _load("trade_executor", "stock_papertrade/trade_executor.py")
    db_mod = _load("db", "stock_papertrade/db.py")

    written: list[dict] = []

    async def fake_quote(secid: str) -> float | None:
        return quote

    if order is not None:
        order.append("reject-check")

    ex = executor_mod.PaperTradeExecutor()
    with patch.object(executor_mod, "db", db_mod), patch.object(executor_mod, "quote_service") as fake_svc:
        fake_svc.get_quote = AsyncMock(side_effect=fake_quote)
        db_mod.PaperTradeRepo.locked_qty_today = _locked_zero  # type: ignore[attr-defined]
        db_mod.PaperTradeRepo.append_with_cash_update = _capture_write(written, order)  # type: ignore[attr-defined]

        result = asyncio.run(
            ex.record_trade(
                account_id=1,
                stock_code="000333",
                stock_name="美的集团",
                secid="0.000333",
                side="buy",
                price=price,
                qty=500,
                amount=price * 500,
                fee=5.0,
                reason="test",
            )
        )
    return result, db_mod, written


class _FakeTrade:
    """最小 SayuPaperTrade 替身，只带 id。"""

    def __init__(self, tid: int) -> None:
        self.id = tid


async def _locked_zero(account_id: int, stock_code: str, today=None) -> int:
    return 0


def _capture_write(sink: list[dict], order: list[str] | None):
    async def append_with_cash_update(*args, **kwargs):
        sink.append(kwargs)
        if order is not None:
            order.append("write")
        return _FakeTrade(1), 500

    return append_with_cash_update


# ============================================================
# 缓存 TTL
# ============================================================


def test_failed_entry_uses_short_ttl():
    """失败条目必须比成功条目短命，否则一次限流锁死该票 60 秒。"""
    ok_entry = quote_service_mod.QuoteCacheEntry(secid="0.000333", price=80.1)
    bad_entry = quote_service_mod.QuoteCacheEntry(secid="0.000333", price=None)
    assert ok_entry.ttl == quote_service_mod.QUOTE_CACHE_TTL
    assert bad_entry.ttl == quote_service_mod.QUOTE_FAIL_TTL
    assert bad_entry.ttl < ok_entry.ttl, "失败 TTL 必须严格短于成功 TTL"
    print(f"[OK] TTL：成功 {ok_entry.ttl:.0f}s / 失败 {bad_entry.ttl:.0f}s")


def test_fail_ttl_is_short_enough_to_not_block_recovery():
    """失败 TTL 的上界：超过 30s 就又变成"一分钟哑巴"了。"""
    assert 0 < quote_service_mod.QUOTE_FAIL_TTL <= 30.0, quote_service_mod.QUOTE_FAIL_TTL
    print(f"[OK] 失败 TTL {quote_service_mod.QUOTE_FAIL_TTL}s ≤ 30s，不会拖死恢复后的取价")


def test_is_fresh_expires_failed_entry_quickly():
    """失败条目过期后必须重新取价（否则改 TTL 只是摆设）。"""
    now = 1_000_000.0
    bad = quote_service_mod.QuoteCacheEntry(secid="0.000333", price=None, fetched_at=now)
    assert bad.is_fresh(now + 1.0) is True
    # 越过失败 TTL 后必须判定为不新鲜，从而穿透去重新拉
    assert bad.is_fresh(now + quote_service_mod.QUOTE_FAIL_TTL + 0.1) is False
    ok = quote_service_mod.QuoteCacheEntry(secid="0.000333", price=80.1, fetched_at=now)
    assert ok.is_fresh(now + quote_service_mod.QUOTE_FAIL_TTL + 0.1) is True
    print("[OK] 失败条目按短 TTL 过期，成功条目不受影响")


def test_quote_service_respects_short_fail_ttl():
    """端到端：第一次限流失败后，短 TTL 内会重新尝试，而不是一直返回 None。"""
    svc = quote_service_mod.QuoteService()
    calls: list[str] = []

    async def fake_fetch(secid: str):
        calls.append(secid)
        # 第一次模拟限流失败，第二次恢复正常
        if len(calls) == 1:
            return (None, None, None, None)
        return (80.1, 79.0, 1.4, "美的集团")

    svc._fetch_one = fake_fetch  # type: ignore[method-assign]

    async def run() -> tuple[float | None, float | None]:
        first = await svc.get_quote("0.000333")
        # 立刻再取：仍在失败 TTL 内，必须复用缓存（不发第二次请求）
        cached = await svc.get_quote("0.000333")
        assert len(calls) == 1, f"失败 TTL 内不该重复请求，实际发了 {len(calls)} 次"
        # 把失败条目人为老化，越过 TTL 后应重新取价
        entry = svc._cache.get("0.000333")
        assert entry is not None
        entry.fetched_at -= quote_service_mod.QUOTE_FAIL_TTL + 1.0
        recovered = await svc.get_quote("0.000333")
        return first, (cached + recovered if cached and recovered else recovered)

    first, result = asyncio.run(run())
    assert first is None, "首次限流应当拿不到价"
    assert result == 80.1, f"越过失败 TTL 后应当恢复取价，实际 {result}"
    assert len(calls) == 2, f"应恰好重新拉取一次，实际 {len(calls)} 次"
    print("[OK] 限流后短 TTL 内复用缓存，越过 TTL 自动重试并恢复")


# ============================================================
# record_trade 风控闸
# ============================================================


def test_record_trade_rejects_when_quote_unreachable():
    """**回归**：实时价拿不到时必须拒绝入库，不能放行任意价格。

    这是风控期的关键防线。旧实现是 ``_live is not None and _live > 0 and
    price > 0`` 才校验——限流时 ``_live`` 为 None，整段被跳过，价格无从校验。

    这里真跑一次 ``record_trade``（桩掉行情与写库），而不是去猜源码语法：
    断言必须锚在**对外行为**上，写成"检查 AST 里有某种 if 形状"的话，
    实现换个等价写法就假红了。
    """
    executor, db_mod, made = _run_record_trade(quote=None)
    assert made == [], f"行情不可达时不得写库，实际写了 {len(made)} 条"
    assert executor.ok is False, "行情不可达时必须返回 ok=False"
    assert "行情不可达" in executor.message, executor.message
    assert "限流" in executor.message, "拒绝文案应说明可能是限流"
    print(f"[OK] 行情不可达 → 拒绝入库（{executor.message[:40]}…）")


def test_record_trade_rejects_on_stale_price_when_quote_ok():
    """行情可达但传入价偏离过大时仍然拒绝（原有行为不能被改坏）。"""
    executor, db_mod, made = _run_record_trade(quote=80.1, price=200.0)
    assert made == [], f"偏差过大时不得写库，实际写了 {len(made)} 条"
    assert executor.ok is False
    assert "偏差" in executor.message, executor.message
    print("[OK] 行情可达但价格偏离 3%+ → 仍拒绝")


def test_record_trade_allows_fresh_price():
    """正常路径必须仍然放行——新闸不能把好交易也毙掉。"""
    executor, db_mod, made = _run_record_trade(quote=80.1, price=80.15)
    assert executor.ok is True, executor.message
    assert len(made) == 1, f"正常成交应写库，实际 {len(made)} 条"
    print("[OK] 行情可达且价格一致 → 正常放行并写库")


def test_record_trade_never_writes_when_quote_unreachable():
    """风控期不得触发任何写库调用（用桩记录真实调用，不看源码）。"""
    order: list[str] = []

    executor, db_mod, made = _run_record_trade(quote=None, order=order)
    assert "write" not in order, f"行情不可达时不得写库，实际调用序列 {order}"
    assert made == [], f"不应有落库记录，实际 {len(made)} 条"
    assert executor.ok is False
    print("[OK] 行情不可达 → 全程未触发写库")


def test_quote_unreachable_message_tells_llm_what_to_do():
    """拒绝文案要给出可执行的下一步，而不是一句干巴巴的失败。"""
    import inspect

    executor = _load("trade_executor", "stock_papertrade/trade_executor.py")
    src = inspect.getsource(executor.PaperTradeExecutor.record_trade)
    assert "papertrade_match_order" in src, "拒绝时应指引 LLM 重新走撮合"
    assert "限流" in src, "拒绝时应说明可能是限流"
    print("[OK] 拒绝文案含限流说明与重试指引")


def test_quote_budget_lets_chain_head_reach_the_floor():
    """取价总预算必须够链头吃到 MIN_SOURCE_SLICE_S。

    链上时间片 = min(每源封顶, 剩余总预算, max(剩余/剩余源数, 下限))，所以总预算
    一旦低于下限，链头又会退化成「一个人吃掉整个预算」——正是第二轮评审报的
    「东财冷启动实测 3.98s 被 3s 时间片切掉」。这里把出货值和下限绑死：谁把
    QUOTE_TIMEOUT_S 调小到下限以下，这条直接红。
    """
    from SayuStock.utils.market import provider_registry as pr

    budget = quote_service_mod.QUOTE_TIMEOUT_S
    assert budget >= pr.MIN_SOURCE_SLICE_S, budget
    with pr.chain_deadline(budget):
        assert pr._source_budget(4) >= pr.MIN_SOURCE_SLICE_S
    print(f"[OK] 取价总预算 {budget}s 够链头吃到每源下限 {pr.MIN_SOURCE_SLICE_S}s")


if __name__ == "__main__":
    test_failed_entry_uses_short_ttl()
    test_fail_ttl_is_short_enough_to_not_block_recovery()
    test_is_fresh_expires_failed_entry_quickly()
    test_quote_service_respects_short_fail_ttl()
    test_quote_budget_lets_chain_head_reach_the_floor()
    test_record_trade_rejects_when_quote_unreachable()
    test_record_trade_rejects_on_stale_price_when_quote_ok()
    test_record_trade_allows_fresh_price()
    test_record_trade_never_writes_when_quote_unreachable()
    test_quote_unreachable_message_tells_llm_what_to_do()
    print("\n[SUCCESS] 风控韧性全部测试通过！")
