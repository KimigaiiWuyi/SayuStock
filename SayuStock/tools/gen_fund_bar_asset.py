"""从 ``myup.png`` 色相偏移出基金持仓页专用的金色底图。

「我的自选」的红条素材（``myup/mydown/myeq``）是给自选页的：整行染色，
涨红跌绿。基金持仓页要自己的皮肤——行底统一走金色，涨跌交给文字颜色，
不然两页长得一模一样。

做法只转**色相**（饱和度 / 明度 / alpha 全不动），所以新素材的
渐变、左侧色条、右侧药丸和圆角跟原素材逐像素对齐，结构不会走形。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

TEXTURE_DIR = Path(__file__).resolve().parent.parent / "stock_info" / "texture2d"
SRC = TEXTURE_DIR / "myup.png"
DST = TEXTURE_DIR / "myup_gold.png"

# 红(≈359°) → 金(≈43°)：+44°。PIL 的 HSV 模式下 H 是 0-255 对应 0-360°。
HUE_SHIFT = round(44.0 / 360.0 * 255)

# 查表法：point() 的 callable 重载有歧义（lambda 会被推成 ImagePointTransform），
# 给 256 项的 LUT 就没有类型歧义，也比逐像素回调快。
HUE_LUT = [(x + HUE_SHIFT) % 256 for x in range(256)]


def build_gold(src: Path = SRC, dst: Path = DST) -> None:
    img = Image.open(src).convert("RGBA")
    alpha = img.getchannel("A")
    h, s, v = img.convert("HSV").split()
    gold = Image.merge("HSV", (h.point(HUE_LUT), s, v)).convert("RGB")
    gold.putalpha(alpha)
    gold.save(dst)
    print(f"{src.name} → {dst.name}  (hue +{HUE_SHIFT / 255 * 360:.0f}°)")


if __name__ == "__main__":
    build_gold()
