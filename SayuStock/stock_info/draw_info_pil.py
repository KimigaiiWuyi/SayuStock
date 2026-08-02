"""兼容层：旧 PIL 路径已合并到 draw_my_info（语义 Quote）。"""

from .draw_my_info import DIFF_MAP, TEXT_PATH, draw_my_stock_img, draw_bar_from_quote

__all__ = ["DIFF_MAP", "TEXT_PATH", "draw_bar_from_quote", "draw_my_stock_img"]
