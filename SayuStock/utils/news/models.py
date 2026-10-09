"""供应商无关的快讯模型。id 只在同一 source 内可比。"""

from __future__ import annotations

from dataclasses import dataclass

# 汇总推送要盖住漏推的一整日，和雪球缓存的保留窗口对齐
NEWS_RETENTION_MS = 48 * 60 * 60 * 1000

SOURCE_LABELS: dict[str, str] = {
    "eastmoney": "东财7x24",
    "wallstreetcn": "华尔街见闻",
    "sina": "新浪7x24",
    "jin10": "金十快讯",
}


@dataclass(frozen=True, slots=True)
class NewsItem:
    source: str
    id: str
    text: str
    published_ms: int
    important: bool
    title: str = ""
    url: str = ""

    def watermark(self) -> str:
        return f"{self.source}:{self.id}"


@dataclass(frozen=True, slots=True)
class NewsFeed:
    """items 按发布时间从新到旧。"""

    source: str
    items: tuple[NewsItem, ...]


def source_label(source: str) -> str:
    if source in SOURCE_LABELS:
        return SOURCE_LABELS[source]
    return source


def split_watermark(raw: str | None) -> tuple[str, str]:
    """返回 (source, id)。没有冒号的旧雪球纯数字水位线 source 为空。"""
    text = (raw or "").strip()
    if ":" not in text:
        return "", text
    source, nid = text.split(":", 1)
    return source, nid


def should_rebase(raw: str | None, source: str) -> bool:
    saved, _nid = split_watermark(raw)
    return saved != source


def id_newer(candidate: str, watermark_id: str) -> bool:
    if candidate.isdigit() and watermark_id.isdigit():
        return int(candidate) > int(watermark_id)
    return candidate > watermark_id
