"""六个源链配置的出货默认值：填的推荐链必须与「留空」时代的实际链等价。

`data` 从 `[]` 改成推荐链之后，网页控制台一打开就能看见推荐顺序；但等价性得由测试守住
—— 链外源本来就会按内禀次序补到链尾，所以「填推荐链」与「留空」在每个域**真正实现了
接口的源**上必须给出同一个先后。改 `config_default.py` 的默认值、或改某个键的 `options`
（`options` 同时是「哪些源真的实现了该域接口」的名单），这里都会红。
"""

from __future__ import annotations

from collections.abc import Callable

from SayuStock.utils.market import provider_registry as pr
from SayuStock.stock_config.config_default import CONFIG_DEFAULT
from gsuid_core.utils.plugins_config.models import GsListStrConfig

_GROUPS = tuple(pr._GROUP_CHAIN_CONFIG_KEYS)


def _shipped_reader() -> Callable[[str, object], object]:
    """config_reader 形状：读 CONFIG_DEFAULT 的 `data`，空值回退 fallback（同生产语义）。"""

    def reader(key: str, fallback: object) -> object:
        item = CONFIG_DEFAULT.get(key)
        if isinstance(item, GsListStrConfig) and item.data:
            return item.data
        return fallback

    return reader


def test_shipped_defaults_become_the_recommended_chains() -> None:
    """填进 `data` 的推荐链 = 每个域实际生效的调用链。"""
    reader = _shipped_reader()
    assert pr.build_priority_chain(reader) == ["eastmoney", "tencent", "sina", "ths"]
    assert pr.build_priority_chain(reader, "quote") == ["eastmoney", "tencent", "sina", "ths"]
    assert pr.build_priority_chain(reader, "kline") == ["eastmoney", "tencent", "sina", "ths"]
    # ③④ 组只有东财/新浪实现了接口：推荐链把它们排前两位，腾讯/同花顺照旧由链尾兜底补入
    # （对这两组它们是 unsupported，跳过即可，不影响实际取数顺序）。
    assert pr.build_priority_chain(reader, "board") == ["eastmoney", "sina", "tencent", "ths"]
    assert pr.build_priority_chain(reader, "market") == ["eastmoney", "sina", "tencent", "ths"]
    assert pr.build_priority_chain(reader, "exclusive") == ["eastmoney", "tencent", "sina", "ths"]


def test_filled_defaults_match_the_old_empty_defaults_on_capable_sources() -> None:
    """「填推荐链」与旧的「留空」等价：只看每域真正实现了接口的源，先后不许变。"""
    shipped = _shipped_reader()
    cleared: dict[str, object] = {key: [] for key in pr._GROUP_CHAIN_CONFIG_KEYS.values()}
    cleared[pr.CHAIN_CONFIG_KEY] = []

    def cleared_reader(key: str, fallback: object) -> object:
        return cleared.get(key, fallback)

    for group in _GROUPS:
        item = CONFIG_DEFAULT[pr._GROUP_CHAIN_CONFIG_KEYS[group]]
        assert isinstance(item, GsListStrConfig), group
        capable = set(pr.parse_priority_chain(item.options))
        new_chain = [p for p in pr.build_priority_chain(shipped, group) if p in capable]
        old_chain = [p for p in pr.build_priority_chain(cleared_reader, group) if p in capable]
        assert new_chain == old_chain, group


def test_shipped_defaults_are_selectable_in_options() -> None:
    """`data` 里填的每一项都必须在该键的 `options` 里，否则控制台里根本选不出来。"""
    for key, item in CONFIG_DEFAULT.items():
        if not key.startswith("market_api_chain"):
            continue
        assert isinstance(item, GsListStrConfig), key
        assert item.data, key
        assert set(item.data) <= set(item.options), key
