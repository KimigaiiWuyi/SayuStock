"""持仓分析：标的解析 + 自然日配额（不打 LLM）。"""

from datetime import date, timedelta

from SayuStock.stock_holdings_analysis import parse as p, quota as q, service as s


def test_split_manual_and_cap():
    tokens = p.split_manual_symbols("600519 茅台,000858，300750 宁德")
    assert "600519" in tokens
    assert len(tokens) >= 3
    many = [f"c{i:06d}" for i in range(12)]
    capped, trunc = p.cap_symbols(many)
    assert len(capped) == 8
    assert trunc is True
    print("[OK] split/cap")


def test_manual_overrides_watchlist():
    r = p.build_symbol_list(manual_text="600519 000001", watchlist=["999999"] * 10)
    assert r is not None
    assert r.source == "manual"
    assert r.symbols == ["600519", "000001"]
    print("[OK] manual overrides watchlist")


def test_watchlist_truncate_warning():
    wl = [f"{i:06d}" for i in range(1, 12)]
    r = p.build_symbol_list(manual_text="", watchlist=wl)
    assert r is not None
    assert r.truncated
    assert len(r.symbols) == 8
    assert "前 8" in r.cap_warning
    print("[OK] watchlist truncate")


def test_empty_returns_none():
    assert p.build_symbol_list(manual_text="", watchlist=[]) is None
    print("[OK] empty")


def test_quota_natural_day():
    uid, bid = "test_ha_user", "test_ha_bot"
    day = date.today()
    q.clear_quota_for_test(uid, bid, day)
    assert q.is_quota_available(uid, bid, day) is True
    assert q.try_claim_quota(uid, bid, day) is True
    assert q.is_quota_available(uid, bid, day) is False
    assert q.try_claim_quota(uid, bid, day) is False
    nxt = day + timedelta(days=1)
    q.clear_quota_for_test(uid, bid, nxt)
    assert q.is_quota_available(uid, bid, nxt) is True
    q.clear_quota_for_test(uid, bid, day)
    print("[OK] quota natural day")


def test_quota_claim_release():
    uid, bid = "test_ha_claim", "test_ha_bot"
    day = date.today()
    q.clear_quota_for_test(uid, bid, day)
    assert q.try_claim_quota(uid, bid, day) is True
    q.release_quota(uid, bid, day)
    assert q.is_quota_available(uid, bid, day) is True
    assert q.try_claim_quota(uid, bid, day) is True
    q.clear_quota_for_test(uid, bid, day)
    print("[OK] quota claim/release")


def test_extract_res_handles():
    text = "已出图，句柄 `res_abc123def456` 请发送；另见 res_deadbeefcafe"
    hs = s.extract_res_handles(text)
    assert any(h.startswith("res_") for h in hs)
    assert len(hs) >= 1
    print(f"[OK] extract handles → {hs}")


def test_build_tasks_non_empty():
    t = s.build_analysis_task(["600519", "茅台"], user_id="u1", day=date.today())
    assert "600519" in t and "技术面" in t
    rt = s.build_render_task("# 标题\n内容")
    assert "句柄" in rt
    print("[OK] task builders")


def test_normalize_analysis_markdown_strips_no_broadcast():
    assert s.normalize_analysis_markdown("<<NO_BROADCAST>>") == ""
    assert s.normalize_analysis_markdown("  <<NO_BROADCAST>>  body  ") == "body"
    assert s.normalize_analysis_markdown("# 标题\n内容") == "# 标题\n内容"
    assert s.normalize_analysis_markdown(None) == ""
    print("[OK] normalize markdown")
