from msgspec import to_builtins
from gsuid_core.models import MessageReceive
from gsuid_core.segment import (
    MessageSegment,
)
import httpx


async def http_test():
    msg = to_builtins(
        MessageReceive(
            content=[
                MessageSegment.text('个股 601919'),
            ]
        )
    )

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            'http://127.0.0.1:8765/api/send_msg',
            json=msg,
        )
        print(response.text)
        print(response.status_code)

if __name__ == "__main__":
    import asyncio
    asyncio.run(http_test())
