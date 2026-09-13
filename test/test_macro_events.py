"""宏观定调：事件字段归一 / 到期判定 / 提示词拼装 / 知识库分片 / 技能目录。全部离线。"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from SayuStock.stock_macro import (  # noqa: E402
    repo as macro_repo,
    prompts as macro_prompts,
    knowledge as macro_kb,
)
from SayuStock.stock_macro.ai_tools import _to_view, format_macro_events_text  # noqa: E402
from SayuStock.utils.database.macro_models import (  # noqa: E402
    MACRO_STATUS_OPEN,
    MACRO_STATUS_SETTLED,
    SayuMacroEvent,
)


def _event(**overrides: object) -> SayuMacroEvent:
    base: dict[str, object] = {
        "slug": "us_china_tariff_2026",
        "title": "中美关税战（2026）",
        "category": "trade",
        "status": MACRO_STATUS_OPEN,
        "severity": 4,
        "direction": "risk_off",
        "result": "双方互加至 84%，谈判窗口未定",
        "stance": "未到顶",
        "check_interval_hours": 6,
        "created_at": datetime(2026, 9, 1, 9, 0),
        "updated_at": datetime(2026, 9, 1, 9, 0),
    }
    base.update(overrides)
    return SayuMacroEvent(**base)


# ─────────────────────────── 字段归一 ───────────────────────────


def test_slug_only_keeps_ascii_word_chars():
    assert macro_repo.normalize_slug("US-China Tariff 2026") == "us_china_tariff_2026"
    assert macro_repo.normalize_slug("  red__sea--shipping  ") == "red_sea_shipping"
    assert macro_repo.normalize_slug("中美关税战") == ""
    assert len(macro_repo.normalize_slug("a" * 200)) == 64


def test_enum_normalizers_accept_aliases_and_fallback():
    assert macro_repo.normalize_direction("利空") == "risk_off"
    assert macro_repo.normalize_direction("Risk-On") == "risk_on"
    assert macro_repo.normalize_direction("随便写") == "mixed"
    assert macro_repo.normalize_status("盖棺定论") == MACRO_STATUS_SETTLED
    assert macro_repo.normalize_status("ongoing") == MACRO_STATUS_OPEN
    assert macro_repo.normalize_status("???") == MACRO_STATUS_OPEN
    assert macro_repo.normalize_category("关税 升级") == "trade"
    assert macro_repo.normalize_category("美联储决议") == "monetary"
    assert macro_repo.normalize_category("unknown") == "other"


def test_severity_clamped_and_non_numeric_defaults():
    assert macro_repo.normalize_severity(9) == 5
    assert macro_repo.normalize_severity(-3) == 1
    assert macro_repo.normalize_severity("4") == 4
    assert macro_repo.normalize_severity("high") == 3
    assert macro_repo.normalize_severity(True) == 3


def test_input_from_raw_clips_and_normalizes():
    data = macro_repo.MacroEventInput.from_raw(
        slug="Red Sea 2026",
        title="红海航道受阻",
        category="地缘",
        status="进行中",
        severity="7",
        direction="避险",
        result="x" * 5000,
        check_interval_hours=999,
        created_by="",
    )
    assert data.slug == "red_sea_2026"
    assert data.category == "geopolitics"
    assert data.status == MACRO_STATUS_OPEN
    assert data.severity == 5
    assert data.direction == "risk_off"
    assert len(data.result) == 1200 and data.result.endswith("...")
    assert data.check_interval_hours is None  # 越界 → 「没说」，新建时落默认 6
    assert data.created_by == "ai"


def test_input_from_raw_blank_means_unchanged():
    """只改进展时 status / category / severity / direction 留空 → None（更新时保留原值）。"""
    data = macro_repo.MacroEventInput.from_raw(slug="x_y", title="X", result="新进展")
    assert data.category is None and data.status is None
    assert data.severity is None and data.direction is None
    assert data.status_or_open == MACRO_STATUS_OPEN


# ─────────────────────────── 到期 / 高危判定 ───────────────────────────


def test_due_for_check_respects_interval_and_settled():
    now = datetime(2026, 9, 12, 12, 0)
    never = _event(last_checked_at=None)
    fresh = _event(last_checked_at=now - timedelta(hours=1))
    stale = _event(last_checked_at=now - timedelta(hours=7))
    settled = _event(status=MACRO_STATUS_SETTLED, last_checked_at=now - timedelta(days=30))
    assert macro_repo.is_due_for_check(never, now) is True
    assert macro_repo.is_due_for_check(fresh, now) is False
    assert macro_repo.is_due_for_check(stale, now) is True
    assert macro_repo.is_due_for_check(settled, now) is False


def test_severe_risk_off_requires_open_and_level():
    assert macro_repo.is_severe_risk_off(_event()) is True
    assert macro_repo.is_severe_risk_off(_event(severity=3)) is False
    assert macro_repo.is_severe_risk_off(_event(direction="risk_on")) is False
    assert macro_repo.is_severe_risk_off(_event(status=MACRO_STATUS_SETTLED)) is False


def test_event_view_and_text_format():
    now = datetime(2026, 9, 12, 12, 0)
    row = _event(last_checked_at=now - timedelta(hours=8))
    view = _to_view(row, now)
    assert view["stale"] is True and view["slug"] == "us_china_tariff_2026"
    text = format_macro_events_text([row], now)
    assert "进行中" in text and "待复核" in text and "中美关税战" in text
    assert "为空" in format_macro_events_text([], now)


# ─────────────────────────── 档位归一 ───────────────────────────


def test_macro_regime_aliases():
    assert macro_prompts.normalize_macro_regime("进攻") == "进攻"
    assert macro_prompts.normalize_macro_regime("risk_off") == "防御"
    assert macro_prompts.normalize_macro_regime("Neutral") == "中性"
    assert macro_prompts.normalize_macro_regime("防御档") == "防御"
    assert macro_prompts.normalize_macro_regime("") == ""
    assert macro_prompts.normalize_macro_regime(42) == ""
    assert macro_prompts.normalize_macro_regime("瞎写") == ""


# ─────────────────────────── 提示词拼装 ───────────────────────────


def test_decision_prompt_embeds_macro_sections():
    from SayuStock.stock_agent import STOCK_AGENT_PROMPT, HOLDINGS_ANALYSIS_PROMPT, PAPERTRADE_DECISION_PROMPT

    for needle in ("宏观三问", "三档仓位", "macro_event_list", "macro_regime", "宏观速查", "政策顶"):
        assert needle in PAPERTRADE_DECISION_PROMPT, needle
    # 最终输出铁律仍在末尾（速查表不能把它挤掉）
    assert PAPERTRADE_DECISION_PROMPT.rstrip().endswith("多一个字都会被框架当成要播报的内容推给群。")
    assert "macro_event_list" in STOCK_AGENT_PROMPT and "宏观三问" in STOCK_AGENT_PROMPT
    assert "宏观定调" in HOLDINGS_ANALYSIS_PROMPT and "顺风" in HOLDINGS_ANALYSIS_PROMPT


def test_prompt_sections_are_plain_and_short_lines():
    """给低能力模型看的段落：每行不超过 100 字，避免超长句。"""
    for block in (macro_prompts.MACRO_QUICK_CHECK, macro_prompts.MACRO_POSITION_RULES, macro_prompts.MACRO_CHEATSHEET):
        for line in block.splitlines():
            assert len(line) <= 100, line


# ─────────────────────────── 知识库分片 / 技能目录 ───────────────────────────


def test_markdown_split_by_h2_and_skips_quotes():
    text = "# 标题\n\n> 引子不入库\n\n引言正文\n\n## 一\n\n内容一\n\n## 二\n\n内容二"
    sections = macro_kb.split_markdown_sections(text, doc_slug="demo", fallback_title="demo")
    assert [s.heading for s in sections] == ["", "一", "二"]
    assert sections[0].body == "引言正文"
    assert sections[1].entity_id == "sayustock_macro_demo_01"
    assert sections[1].title == "宏观定调 · 标题 · 一"


def test_long_section_is_repacked_by_paragraph():
    para = "段" * 400
    text = "# T\n\n## 长\n\n" + "\n\n".join([para, para, para])
    sections = macro_kb.split_markdown_sections(text, doc_slug="long", fallback_title="long")
    assert len(sections) >= 2
    assert sections[1].heading.startswith("长（续")
    assert all(len(s.body) <= 1000 for s in sections)


def test_reference_docs_build_entities_within_embedding_budget():
    entities = macro_kb.build_macro_knowledge_entities()
    assert len(entities) >= 20
    ids = [e["id"] for e in entities]
    assert len(ids) == len(set(ids))
    for e in entities:
        assert e["plugin"] == "SayuStock"
        assert e["content"].strip()
        assert len(e["content"]) <= 1100, e["title"]
        assert "宏观定调" in e["tags"]
    titles = " ".join(e["title"] for e in entities)
    for needle in ("PPI", "三种「相」", "养殖场", "反转点", "三问"):
        assert needle in titles, needle


def test_repo_upsert_roundtrip_on_temp_sqlite(tmp_path: Path):
    """临时 sqlite 上跑一遍：建表 → 登记 → 复核更新 → 盖棺定论 → 高危筛选。"""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import gsuid_core.utils.database.base_models as base_models

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/macro.db")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _main() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(SayuMacroEvent.metadata.create_all, tables=[SayuMacroEvent.__table__])
        original = base_models.async_maker
        base_models.async_maker = maker
        try:
            first = macro_repo.MacroEventInput.from_raw(
                slug="us_china_tariff_2026",
                title="中美关税战",
                category="trade",
                severity=4,
                direction="risk_off",
                summary="互加关税",
                result="双方互加至 84%",
            )
            row = await macro_repo.MacroEventRepo.upsert(first, touch_checked=False)
            assert row.id is not None and row.last_checked_at is None
            severe = await macro_repo.MacroEventRepo.list_open_severe()
            assert [e.slug for e in severe] == ["us_china_tariff_2026"]

            # 只改进展：severity / direction / category 没说 → 保留 4 / risk_off / trade
            progress = macro_repo.MacroEventInput.from_raw(
                slug="us_china_tariff_2026",
                title="中美关税战",
                result="双方仍在加码",
            )
            row1 = await macro_repo.MacroEventRepo.upsert(progress, touch_checked=True)
            assert row1.id == row.id and row1.last_checked_at is not None
            assert row1.severity == 4 and row1.direction == "risk_off" and row1.category == "trade"
            assert row1.check_interval_hours == 6
            assert [e.slug for e in await macro_repo.MacroEventRepo.list_open_severe()] == ["us_china_tariff_2026"]

            # 联网复核并明确降级：同 slug 更新，不新增行，高危筛选随之为空
            second = macro_repo.MacroEventInput.from_raw(
                slug="us_china_tariff_2026",
                title="中美关税战",
                severity=3,
                direction="mixed",
                result="双方回到谈判桌",
            )
            row2 = await macro_repo.MacroEventRepo.upsert(second, touch_checked=True)
            assert row2.id == row.id and row2.severity == 3
            assert row2.summary == "互加关税" and row2.result == "双方回到谈判桌"
            assert await macro_repo.MacroEventRepo.list_open_severe() == []

            # 盖棺定论：写 settled_at，list(open) 不再返回，refresh 永不到期
            third = macro_repo.MacroEventInput.from_raw(
                slug="us_china_tariff_2026",
                title="中美关税战",
                status="settled",
                result="协议签署",
            )
            row3 = await macro_repo.MacroEventRepo.upsert(third, touch_checked=True)
            assert row3.status == MACRO_STATUS_SETTLED and row3.settled_at is not None
            assert await macro_repo.MacroEventRepo.list_by_status(MACRO_STATUS_OPEN) == []
            assert len(await macro_repo.MacroEventRepo.list_by_status(None)) == 1
            assert macro_repo.is_due_for_check(row3) is False
            assert await macro_repo.MacroEventRepo.delete_by_slug("us_china_tariff_2026") is True
            assert await macro_repo.MacroEventRepo.get_by_slug("us_china_tariff_2026") is None
        finally:
            base_models.async_maker = original
            await engine.dispose()

    asyncio.run(_main())


def test_skill_dir_has_frontmatter_and_references():
    skill_md = macro_kb.MACRO_SKILL_DIR / "SKILL.md"
    assert skill_md.is_file()
    head = skill_md.read_text(encoding="utf-8").splitlines()[:4]
    assert head[0] == "---"
    assert head[1].startswith("name: macro-regime-analysis")
    assert head[2].startswith("description:")
    assert len(list(macro_kb.MACRO_REFERENCES_DIR.glob("*.md"))) == 5
