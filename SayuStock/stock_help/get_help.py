import json
from typing import Dict
from pathlib import Path

import aiofiles
from PIL import Image

from gsuid_core.sv import get_plugin_available_prefix
from gsuid_core.help.model import PluginHelp
from gsuid_core.help.draw_new_plugin_help import get_new_help

from ..version import SayuStock_version
from ..utils.image import get_footer

ICON = Path(__file__).parent.parent.parent / "ICON.png"
HELP_DATA = Path(__file__).parent / "help.json"


async def get_help_data() -> Dict[str, PluginHelp]:
    async with aiofiles.open(HELP_DATA, "rb") as file:
        return json.loads(await file.read())


async def get_help():
    return await get_new_help(
        plugin_name="SayuStock",
        plugin_info={f"v{SayuStock_version}": ""},
        plugin_icon=Image.open(ICON),
        plugin_help=await get_help_data(),
        plugin_prefix=get_plugin_available_prefix("SayuStock"),
        help_mode="dark",
        banner_sub_text="一图速览 A 股行情",
        footer=get_footer(),
        enable_cache=True,
    )
