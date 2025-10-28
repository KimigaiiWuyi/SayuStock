from pathlib import Path
from typing import Union

from PIL import Image
from playwright.async_api import async_playwright
from gsuid_core.utils.image.convert import convert_img

from ..stock_config.stock_config import STOCK_CONFIG

TEXT_PATH = Path(__file__).parent / 'texture2d'

view_port: int = STOCK_CONFIG.get_config('mapcloud_viewport').data
scale: int = STOCK_CONFIG.get_config('mapcloud_scale').data


def get_footer():
    return Image.open(TEXT_PATH / 'footer.png')


def get_ICON():
    return Image.open(Path(__file__).parents[2] / 'ICON.png')


async def render_image_by_pw(
    html_path: Path, w: int, h: int, _scale: int
) -> Union[str, bytes]:
    if isinstance(html_path, str):
        return html_path

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        if w == 0 or h == 0:
            w = view_port
            h = view_port
        if _scale == 0:
            _scale = scale

        context = await browser.new_context(
            viewport={
                "width": w,
                "height": h,
            },  # type: ignore
            device_scale_factor=_scale,
        )
        page = await context.new_page()
        await page.goto(html_path.absolute().as_uri())
        await page.wait_for_selector(".plot-container")
        png_bytes = await page.screenshot(type='png')
        await browser.close()
        return await convert_img(png_bytes)
