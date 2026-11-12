"""Image assets used by the help and status messages."""

from pathlib import Path

from PIL import Image

TEXT_PATH = Path(__file__).parent / "texture2d"


def get_footer() -> Image.Image:
    return Image.open(TEXT_PATH / "footer.png")


def get_ICON() -> Image.Image:
    return Image.open(Path(__file__).parents[2] / "ICON.png")
