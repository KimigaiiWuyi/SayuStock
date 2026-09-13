"""把宏观技能的 ``references/*.md`` 按小节灌进框架知识库。

框架 ``sync_knowledge`` 对插件实体**不做分片**：一条 ``KnowledgeBase`` 只算一个向量，
本地小模型 512 token 之外的内容会被静默截断。所以这里先按 ``## `` 切小节，
一小节一条实体，标题带「文件标题 · 小节标题」保证独立召回时自描述。

同时把 ``skills/`` 目录经 ``ai_skill`` 注册为运行时 Skill，主人格 / 能力代理可
``list_skills`` → ``load_skill("macro-regime-analysis")`` 读到完整流程。
"""

from __future__ import annotations

import re
from typing import List
from pathlib import Path
from dataclasses import dataclass

from gsuid_core.logger import logger
from gsuid_core.ai_core.models import KnowledgeBase
from gsuid_core.ai_core.register import ai_skill, ai_entity

__all__ = [
    "SKILLS_ROOT",
    "MACRO_SKILL_DIR",
    "MACRO_REFERENCES_DIR",
    "KnowledgeSection",
    "split_markdown_sections",
    "build_macro_knowledge_entities",
    "register_macro_knowledge",
    "register_macro_skill",
]

SKILLS_ROOT: Path = Path(__file__).resolve().parent.parent / "skills"
MACRO_SKILL_DIR: Path = SKILLS_ROOT / "macro-regime-analysis"
MACRO_REFERENCES_DIR: Path = MACRO_SKILL_DIR / "references"

_ENTITY_NAME: str = "宏观定调"
_ID_PREFIX: str = "sayustock_macro"
_BASE_TAGS: tuple[str, ...] = ("宏观", "宏观定调", "流动性", "模拟盘", "持仓分析", "SayuStock")
# 超过这个长度的小节按段落再切一次，避免单条实体超出本地嵌入模型上限
_SECTION_SOFT_CAP: int = 900

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class KnowledgeSection:
    doc_slug: str
    doc_title: str
    heading: str
    body: str
    index: int

    @property
    def entity_id(self) -> str:
        return f"{_ID_PREFIX}_{self.doc_slug}_{self.index:02d}"

    @property
    def title(self) -> str:
        base = f"{_ENTITY_NAME} · {self.doc_title}"
        return f"{base} · {self.heading}" if self.heading else base


def _doc_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _doc_slug(path: Path) -> str:
    return _SLUG_RE.sub("_", path.stem.lower()).strip("_")


def _split_long_body(body: str) -> List[str]:
    """按空行分段贪心打包到软上限；单段超长不再细切（Markdown 表格保持完整）。"""
    if len(body) <= _SECTION_SOFT_CAP:
        return [body]
    pieces: List[str] = []
    cur = ""
    for para in body.split("\n\n"):
        para = para.strip("\n")
        if not para.strip():
            continue
        if cur and len(cur) + len(para) + 2 > _SECTION_SOFT_CAP:
            pieces.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        pieces.append(cur)
    return pieces


def split_markdown_sections(text: str, *, doc_slug: str, fallback_title: str) -> List[KnowledgeSection]:
    """按 ``## `` 切小节；H1 与首个 H2 之前的引言归入第一块（去掉 ``> `` 引用行）。"""
    title = _doc_title(text, fallback_title)
    lines = text.splitlines()
    blocks: List[tuple[str, List[str]]] = []
    heading = ""
    buf: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            continue
        if stripped.startswith("## "):
            if buf and any(item.strip() for item in buf):
                blocks.append((heading, buf))
            heading = stripped[3:].strip()
            buf = []
            continue
        if stripped.startswith("> "):
            continue
        buf.append(line)
    if buf and any(item.strip() for item in buf):
        blocks.append((heading, buf))

    sections: List[KnowledgeSection] = []
    for head, body_lines in blocks:
        body = "\n".join(body_lines).strip("\n")
        if not body.strip():
            continue
        parts = _split_long_body(body)
        for i, part in enumerate(parts):
            sub_heading = head if i == 0 or not head else f"{head}（续{i + 1}）"
            sections.append(
                KnowledgeSection(
                    doc_slug=doc_slug,
                    doc_title=title,
                    heading=sub_heading,
                    body=part.strip(),
                    index=len(sections),
                )
            )
    return sections


def build_macro_knowledge_entities(refs_dir: Path = MACRO_REFERENCES_DIR) -> List[KnowledgeBase]:
    """读全部 references/*.md → 一小节一条 ``KnowledgeBase``。目录不存在返回空列表。"""
    if not refs_dir.is_dir():
        return []
    entities: List[KnowledgeBase] = []
    for path in sorted(refs_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        slug = _doc_slug(path)
        for section in split_markdown_sections(text, doc_slug=slug, fallback_title=path.stem):
            tags = [*_BASE_TAGS, section.doc_title]
            if section.heading:
                tags.append(section.heading)
            entities.append(
                KnowledgeBase(
                    id=section.entity_id,
                    plugin="SayuStock",
                    title=section.title,
                    content=f"# {section.heading}\n\n{section.body}" if section.heading else section.body,
                    tags=tags,
                    entity=_ENTITY_NAME,
                )
            )
    return entities


def register_macro_knowledge() -> int:
    """模块导入时调用；返回注册条数。"""
    entities = build_macro_knowledge_entities()
    for entity in entities:
        ai_entity(entity)
    if entities:
        logger.info(f"[SayuStock][Macro] 宏观知识库已注册 {len(entities)} 条小节实体")
    else:
        logger.warning(f"[SayuStock][Macro] 未找到宏观 references：{MACRO_REFERENCES_DIR}")
    return len(entities)


def register_macro_skill() -> None:
    """把 ``SayuStock/skills`` 注册为运行时 Skill 目录（含 macro-regime-analysis）。"""
    if not (MACRO_SKILL_DIR / "SKILL.md").is_file():
        logger.warning(f"[SayuStock][Macro] 宏观 Skill 不存在：{MACRO_SKILL_DIR}")
        return
    ai_skill(SKILLS_ROOT, plugin="SayuStock")
