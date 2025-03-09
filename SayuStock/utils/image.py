from pathlib import Path

from PIL import Image

TEXT_PATH = Path(__file__).parent / 'texture2d'


def get_footer():
    return Image.open(TEXT_PATH / 'footer.png')


def get_ICON():
    return Image.open(Path(__file__).parents[2] / 'ICON.png')
