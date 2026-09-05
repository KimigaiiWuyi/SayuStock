"""离线缓存探测：空 DATA_PATH 不能当成有行情 JSON。"""

from __future__ import annotations

from pathlib import Path

from offline_cache import has_quote_cache


def test_empty_dir_is_not_quote_cache(tmp_path: Path) -> None:
    assert has_quote_cache(tmp_path) is False
    (tmp_path / "readme.txt").write_text("x", encoding="utf-8")
    assert has_quote_cache(tmp_path) is False


def test_missing_dir_is_not_quote_cache(tmp_path: Path) -> None:
    assert has_quote_cache(tmp_path / "nope") is False


def test_single_stock_json_marks_quote_cache(tmp_path: Path) -> None:
    (tmp_path / "122.XAU_single-stock_None_data.json").write_text("{}", encoding="utf-8")
    assert has_quote_cache(tmp_path) is True
