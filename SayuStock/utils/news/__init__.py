"""快讯统一入口。推送、模拟盘和 AI 工具都走 get_news_port()。

默认顺序：东财 → 华尔街见闻 → 新浪 → 金十。
控制台 news_source_order 可改顺序；前一个失败或为空才用下一个。
"""

from .port import NewsPort, NewsSource
from .errors import NewsError, is_news_error
from .models import (
    SOURCE_LABELS,
    NEWS_RETENTION_MS,
    NewsFeed,
    NewsItem,
    id_newer,
    source_label,
    should_rebase,
    split_watermark,
)
from .registry import get_news_port, set_news_port

__all__ = [
    "NEWS_RETENTION_MS",
    "SOURCE_LABELS",
    "NewsError",
    "NewsFeed",
    "NewsItem",
    "NewsPort",
    "NewsSource",
    "get_news_port",
    "id_newer",
    "is_news_error",
    "set_news_port",
    "should_rebase",
    "source_label",
    "split_watermark",
]
