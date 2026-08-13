"""端侧联调脚本：按 client.py 的协议连上 core，跑一串模拟盘命令并打印回包。

不属于测试套件（需要 core 在跑 + 真实 DB），只在人工验收时用：

    python test/_manual_ws_client.py "模拟盘列表" "模拟盘策略列表"

不带参数时跑内置的多账户回归剧本。
"""

import sys
import json
import time
import asyncio
from typing import Any, List

import websockets

WS_URL = f"ws://localhost:8765/ws/PaperTradeE2E{int(time.time()) % 100000}?token=abc111"
GROUP_ID = "666249732"
USER_ID = "444838888"
BOT_SELF_ID = "3399214199"
# core 的 get_user_pml 会把 user_pm<1 的上报值抬成 2（普通群员），而 check_admin 要 pm<=1，
# 所以要测管理命令必须自报 1（群主/superuser），报 0 反而会被抬成 2。
USER_PM = 1

# SayuStock 的插件级 force_prefix 是 ["a", "股票"]，且 allow_empty_prefix=False，
# 不带前缀的「模拟盘列表」不会命中任何触发器（会掉进 AI 闲聊兜底）。
PREFIX = "a"

DEFAULT_SCRIPT: List[str] = [
    # 只读：空库形态
    "模拟盘列表",
    "模拟盘策略列表",
    # 建盘 + 命名校验
    "模拟盘创建",
    "模拟盘创建 123456",
    "模拟盘创建 放量盘 volume_extremum 500000",
    "模拟盘列表",
    # 盘名解析（存在 / 不存在）
    "模拟盘查看 放量盘",
    "模拟盘查看 查无此盘",
    # 改名 / 策略 / 启停
    "模拟盘改名 放量盘 量能盘",
    "模拟盘策略切换 量能盘 multi_factor",
    "模拟盘停用 量能盘",
    "模拟盘启用 量能盘",
    # 播报订阅
    "模拟盘推送添加 量能盘",
    "模拟盘推送列表",
    "模拟盘推送删除 量能盘",
    # 账本查询（空账本也要能出话，不能抛异常）
    "模拟盘收益 量能盘 月",
    "模拟盘记录 量能盘",
    "模拟盘排行",
    # 清理
    "模拟盘删除 量能盘",
    "模拟盘列表",
]


def _payload(msg: str) -> bytes:
    return json.dumps(
        {
            "bot_id": "console",
            "bot_self_id": BOT_SELF_ID,
            "user_type": "group",
            "user_pm": USER_PM,
            "group_id": GROUP_ID,
            "user_id": USER_ID,
            "content": [{"type": "text", "data": msg}],
        },
        ensure_ascii=False,
    ).encode()


def _render(raw: Any) -> str:
    try:
        data = json.loads(raw)
    except Exception:
        return str(raw)[:2000]
    out: List[str] = []
    for seg in data.get("content") or []:
        if seg.get("type") == "text":
            out.append(str(seg.get("data", "")))
        else:
            out.append(f"<{seg.get('type')}>")
    return "\n".join(out) if out else json.dumps(data, ensure_ascii=False)[:2000]


async def main() -> None:
    script = sys.argv[1:] or DEFAULT_SCRIPT
    async with websockets.connect(WS_URL, max_size=2**25, open_timeout=30) as ws:
        for msg in script:
            line = msg if msg.startswith(PREFIX) else PREFIX + msg
            print(f"\n{'=' * 70}\n>>> {line}\n{'=' * 70}")
            await ws.send(_payload(line))
            # 一条命令可能回多包；连续 8s 没有新包就认为这条结束
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=8)
                except asyncio.TimeoutError:
                    break
                print(_render(raw))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
