from msgspec import to_builtins
from gsuid_core.models import MessageReceive
from gsuid_core.segment import (
    MessageSegment,
)
from gsuid_core.logger import logger
import httpx
import time
import base64


async def http_test():
    msg = to_builtins(
        MessageReceive(
            content=[
                MessageSegment.text('个股 601919'),
                # MessageSegment.text('大盘云图'),
            ]
        )
    )

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            'http://127.0.0.1:8765/api/send_msg',
            json=msg,
        )

        timestamp = int(time.time())
        res = response.json()
        image_datas = res['data']['content']
        for image_info in image_datas:
            if image_info['type'] == 'image':
                logger.info(image_info['type'])
                image_data = image_info['data']
                if image_data.startswith('base64://'):
                    image_data = image_data[len('base64://'):]
                image_dataBytes = base64.b64decode(image_data)
                with open(f"{timestamp}.jpg", "wb") as f:
                    f.write(image_dataBytes)
        print(response.status_code)

if __name__ == "__main__":
    import asyncio
    asyncio.run(http_test())
