"""账户和排行榜视图以账本、快照为准，持仓估值不被实时行情改写。"""

from __future__ import annotations

import pytest
from extras_samples import paper_trades, paper_account, paper_positions, paper_snapshots

from SayuStock.render.templates import render_template
from SayuStock.stock_papertrade import render
from SayuStock.utils.database.papertrade_models import (
    SayuPaperTrade,
    SayuPaperAccount,
    SayuPaperPosition,
    SayuPaperSnapshot,
)


def test_empty_account_keeps_cash_and_explicit_empty_sections() -> None:
    html = render._account_html(render._account_view(paper_account(), [], []))
    assert "持仓按成本计" in html
    assert "638,075" in html
    assert "暂无持仓" in html and "暂无交易" in html


@pytest.mark.asyncio
async def test_account_view_keeps_cost_valuation_and_latest_five(monkeypatch: pytest.MonkeyPatch) -> None:
    positions = paper_positions()
    calls: list[str] = []
    summary: list[str] = []

    async def get_account(account_id: int) -> SayuPaperAccount:
        assert account_id == 7
        return paper_account()

    async def list_positions(account_id: int) -> list[SayuPaperPosition]:
        assert account_id == 7
        return positions

    async def list_trades(account_id: int, *, limit: int) -> list[SayuPaperTrade]:
        assert account_id == 7 and limit == 5
        return paper_trades()

    async def capture(html: str, *, width: int, label: str) -> bytes:
        assert calls == ["ai"]
        assert width == render.PAPER_WIDTH and label == "模拟盘账户视图"
        assert "997,764" in html and "164,500" in html
        assert "84.36" not in html
        assert "示例交易5" in html and "示例交易6" not in html
        calls.append("render")
        return b"png"

    def emit(text: str) -> None:
        calls.append("ai")
        summary.append(text)

    monkeypatch.setattr(render.db.PaperAccountRepo, "get_by_id", get_account)
    monkeypatch.setattr(render.db.PaperPositionRepo, "list_by_account", list_positions)
    monkeypatch.setattr(render.db.PaperTradeRepo, "list_by_account", list_trades)
    monkeypatch.setattr(render, "render_html", capture)
    monkeypatch.setattr(render, "ai_return", emit)
    assert await render.draw_account_view(7) == b"png"
    assert calls == ["ai", "render"]
    assert "997,764（持仓按成本计）" in summary[0]
    assert "示例交易5" in summary[0] and "示例交易6" not in summary[0]
    assert positions[0].plan_thesis == "示例入场计划"


@pytest.mark.asyncio
async def test_leaderboard_respects_latest_snapshot_order_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshots = paper_snapshots()
    snapshots[0], snapshots[1] = snapshots[1], snapshots[0]
    calls: list[str] = []

    async def list_snaps(*, limit: int) -> list[SayuPaperSnapshot]:
        assert limit == 20
        return snapshots

    async def list_accounts() -> list[SayuPaperAccount]:
        return []

    async def capture(html: str, *, width: int, label: str) -> bytes:
        assert width == render.PAPER_WIDTH and label == "模拟盘排行"
        assert html.index('class="ex-name">#2</div>') < html.index('class="ex-name">#1</div>')
        assert 'class="ex-name">#20</div>' in html
        assert 'class="ex-name">#21</div>' not in html
        calls.append("render")
        return b"png"

    def emit(text: str) -> None:
        assert "20. #20" in text
        calls.append("ai")

    monkeypatch.setattr(render.db.PaperSnapshotRepo, "list_latest_all_accounts", list_snaps)
    monkeypatch.setattr(render.db.PaperAccountRepo, "list_all", list_accounts)
    monkeypatch.setattr(render, "render_html", capture)
    monkeypatch.setattr(render, "ai_return", emit)
    assert await render.draw_leaderboard() == b"png"
    assert calls == ["ai", "render"]
    empty = render_template("paper_leaderboard.html", width=render.PAPER_WIDTH, rows=(), footer="SayuStock")
    assert "暂无排行数据" in empty
