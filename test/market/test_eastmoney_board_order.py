"""东财 board 与 rank_list 的排序方向(po)契约测试。

规则验证：
1. 东财 HTTP clist API: po=1 为降序(从大到小，高值在前，即涨幅榜/成交额最大)；
   po=0 为升序(从小到大，低值在前，即跌幅榜/小额)。
2. Provider.board(sort_asc=True) 必须映射到 po=0 (跌幅/升序)；
   Provider.board(sort_asc=False) 必须映射到 po=1 (涨幅/降序)。
3. Provider.rank_list(high_first=True) 必须映射到 po=1；
   Provider.rank_list(high_first=False) 必须映射到 po=0。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from SayuStock.utils.market.enums import RankBy, BoardKind
from SayuStock.utils.market.models import BoardRow, BoardSnapshot
from SayuStock.utils.market.adapters.eastmoney.provider import EastMoneyMarketData


def test_eastmoney_board_sort_asc_mapping() -> None:
    """验证 board 的 sort_asc 正确映射到东财 po 参数。"""
    provider = EastMoneyMarketData()
    recorded_calls: list[dict[str, object]] = []

    async def _fake_get_market_list(market: str, is_loop: bool = False, po: int = 1, pz: int = 20):
        recorded_calls.append({"market": market, "is_loop": is_loop, "po": po, "pz": pz})
        return {
            "data": {
                "diff": [
                    {
                        "f12": "BK0437",
                        "f14": "电子器件",
                        "f2": 100.0,
                        "f3": 2.5,
                        "f6": 1e9,
                        "f128": "测试股",
                        "f136": 2.5,
                    }
                ]
            }
        }

    with patch(
        "SayuStock.utils.market.adapters.eastmoney.provider.EASTMONEY_REQUESTER.get_market_list",
        side_effect=_fake_get_market_list,
    ):
        # 1. sort_asc=True (升序/跌幅榜) -> 必须是 po=0
        snap_asc = asyncio.run(provider.board(BoardKind.INDUSTRY, limit=20, sort_asc=True))
        assert not isinstance(snap_asc, str)
        assert recorded_calls[-1]["po"] == 0

        # 2. sort_asc=False (降序/涨幅榜) -> 必须是 po=1
        snap_desc = asyncio.run(provider.board(BoardKind.INDUSTRY, limit=20, sort_asc=False))
        assert not isinstance(snap_desc, str)
        assert recorded_calls[-1]["po"] == 1


def test_eastmoney_rank_list_high_first_mapping() -> None:
    """验证 rank_list 的 high_first 正确映射到东财 po 参数。"""
    provider = EastMoneyMarketData()
    recorded_params: list[list[tuple[str, str]]] = []

    async def _fake_stock_request(url: str, method: str, params: list[tuple[str, str]]):
        recorded_params.append(params)
        return {
            "data": {
                "diff": [
                    {
                        "f12": "600519",
                        "f14": "贵州茅台",
                        "f2": 1800.0,
                        "f3": 1.5,
                        "f6": 5e9,
                        "f8": 0.5,
                        "f100": "白酒",
                    }
                ]
            }
        }

    with patch(
        "SayuStock.utils.market.adapters.eastmoney.provider.EASTMONEY_REQUESTER.stock_request",
        side_effect=_fake_stock_request,
    ):
        # high_first=True (高值在前) -> 必须是 po=1
        asyncio.run(provider.rank_list(RankBy.AMOUNT, limit=20, high_first=True))
        param_dict_high = dict(recorded_params[-1])
        assert param_dict_high.get("po") == "1"

        # high_first=False (低值在前) -> 必须是 po=0
        asyncio.run(provider.rank_list(RankBy.AMOUNT, limit=20, high_first=False))
        param_dict_low = dict(recorded_params[-1])
        assert param_dict_low.get("po") == "0"


def test_draw_info_requests_fall_as_asc_and_rise_as_desc(tmp_path: Path) -> None:
    """验证大盘概览绘图时，领跌按 sort_asc=True 拉取，领涨按 sort_asc=False 拉取。"""
    from SayuStock.stock_info import draw_info

    board_calls: list[tuple[str, bool]] = []

    class _MockMarket:
        async def intraday(self, secid: str, ndays: int = 1):
            from SayuStock.utils.market.errors import unsupported

            return unsupported("skip", provider="mock")

        async def quote(self, secid: str):
            from SayuStock.utils.market.enums import AssetClass
            from SayuStock.utils.market.models import Quote, SymbolRef

            return Quote(
                symbol=SymbolRef(
                    code=secid,
                    name=secid,
                    asset_class=AssetClass.INDEX,
                    exchange="",
                    provider_symbol=secid,
                ),
                price=3000.0,
                open=3000.0,
                high=3000.0,
                low=3000.0,
                prev_close=2980.0,
                change_pct=0.67,
                change_amount=20.0,
                volume=None,
                amount=None,
                turnover_rate=None,
                pe=None,
                pb=None,
                market_cap=None,
                float_market_cap=None,
                industry=None,
                limit_up=None,
                limit_down=None,
                as_of=None,
                provider="mock",
            )

        async def board(self, kind: str, *, limit: int = 20, sort_asc: bool = False):
            board_calls.append((kind, sort_asc))
            change = -3.0 if sort_asc else 5.0
            row = BoardRow(
                code="BK0001",
                name=f"{kind}_{'跌' if sort_asc else '涨'}",
                price=100.0,
                change_pct=change,
                amount=1e8,
                market_cap=None,
                industry=None,
                lead_name="个股",
                lead_change_pct=change,
            )
            return BoardSnapshot(kind=BoardKind.INDUSTRY, title=kind, rows=(row,), provider="mock")

        async def breadth(self):
            from SayuStock.utils.market.models import BreadthBar, BreadthBucket

            return BreadthBar(buckets=(BreadthBucket(label=">7%", count=10),))

        async def market_turnover(self):
            from datetime import datetime

            from SayuStock.utils.market.models import MarketTurnover

            return MarketTurnover(amount=1e12, prev_amount=9e11, last_trade_date=datetime.now(), provider="mock")

    async def _mock_render(*args, **kwargs):
        return b"fake_png"

    from PIL import Image

    fake_img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    preview = tmp_path / "preview_output" / "market_overview.png"

    with (
        patch("SayuStock.stock_info.draw_info.get_market", return_value=_MockMarket()),
        patch("SayuStock.stock_info.draw_info.get_image_from_em", return_value=fake_img),
        patch("SayuStock.stock_info.draw_info.render_html", side_effect=_mock_render),
        patch("SayuStock.stock_info.draw_info.ai_return") as mock_ai,
        patch.object(draw_info, "PREVIEW_FILE", preview),
    ):
        res = asyncio.run(draw_info.draw_info_img())
        assert res == b"fake_png"
        assert not preview.exists()

        # 检查 4 次 board 调用：行业/概念的跌幅榜必须是 sort_asc=True，涨幅榜必须是 sort_asc=False
        assert ("行业板块", True) in board_calls
        assert ("行业板块", False) in board_calls
        assert ("概念板块", True) in board_calls
        assert ("概念板块", False) in board_calls

        # 检查 ai_return 文本中领涨板块 change_pct 是正的，领跌板块是负的
        ai_text = mock_ai.call_args[0][0]
        assert "【领涨行业板块】\n  行业板块_涨: +5.0%" in ai_text
        assert "【领跌行业板块】\n  行业板块_跌: -3.0%" in ai_text

        assert asyncio.run(draw_info.draw_info_img(is_save=True)) == b"fake_png"
        assert preview.read_bytes() == b"fake_png"
        with patch.object(draw_info, "render_html", side_effect=draw_info.RenderError("test failure")):
            assert asyncio.run(draw_info.draw_info_img(is_save=True)) == "大盘概览出图失败"
        assert preview.read_bytes() == b"fake_png"
