"""全天候加密货币栏：一排 4 格、别名表不能重复贴同一币。"""

from __future__ import annotations

from SayuStock.utils.constant import crypto
from SayuStock.utils.market.display import DisplayItem, pick_display_items

# 旧展示键：CRYPTO_MAP 前若干个别名。子串匹配会把 BTC 贴三次。
_ALIAS_KEYS = ["BTC USD", "ETH USD", "BTCUSD", "BTC", "SOL", "XRP", "ETH", "DOGE"]


def _item(name: str) -> DisplayItem:
    return DisplayItem(name=name, price=1.0, change_pct=0.0)


def test_all_weather_crypto_is_four_unique_slots() -> None:
    assert list(crypto) == ["BTC", "ETH", "SOL", "XRP"]
    assert len(set(crypto)) == 4


def test_crypto_map_aliases_do_not_duplicate_btc() -> None:
    pool = [_item("BTC"), _item("ETH"), _item("SOL"), _item("XRP")]
    naive: list[str] = []
    for key in _ALIAS_KEYS:
        for item in pool:
            if item.name != key and key not in item.name and item.name not in key:
                continue
            naive.append(item.name)
            break
    assert naive[:4] == ["BTC", "ETH", "BTC", "BTC"]
    assert len(naive) == 7

    picked = [i.name for i in pick_display_items(pool, crypto)]
    assert picked == ["BTC", "ETH", "SOL", "XRP"]

    guarded = [i.name for i in pick_display_items(pool, _ALIAS_KEYS)]
    assert guarded == ["BTC", "ETH", "SOL", "XRP"]
