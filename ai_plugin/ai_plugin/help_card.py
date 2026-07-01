"""帮助命令长图生成器 — 用 Pillow 绘制风格化帮助卡片"""
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "C:/Windows/Fonts/msyhbd.ttc"
FONT_PATH_NORMAL = "C:/Windows/Fonts/msyh.ttc"

# 色彩方案
BG_COLOR = (25, 25, 30)
CARD_BG = (35, 35, 42)
TITLE_COLOR = (255, 200, 80)
SECTION_BG = (48, 48, 58)
SECTION_TEXT = (140, 180, 255)
ITEM_KEY = (255, 220, 140)
ITEM_DESC = (200, 200, 210)
FOOTER_COLOR = (100, 100, 110)
DIVIDER = (55, 55, 65)
ACCENT = (90, 140, 255)

# 布局参数
CARD_W = 680
PAD_X = 32
PAD_Y = 24
SECT_PAD = 14
ITEM_H = 34
SECTION_GAP = 18
TITLE_H = 80


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH if bold else FONT_PATH_NORMAL, size, index=0)


def generate_help_card(data: dict[str, list[tuple[str, str]]], bot_name: str = "姜小妹") -> bytes:
    """生成帮助卡片图片。
    data: {section_title: [(command, description), ...]}
    返回 PNG bytes。
    """
    title_font = _font(30)
    section_font = _font(20)
    item_key_font = _font(16)
    item_desc_font = _font(15, bold=False)
    footer_font = _font(13, bold=False)

    # 预计算高度
    total_h = PAD_Y + TITLE_H + SECTION_GAP
    for sect, items in data.items():
        total_h += SECT_PAD * 2 + 30 + len(items) * ITEM_H + SECTION_GAP
    total_h += 30 + PAD_Y  # footer

    img = Image.new("RGB", (CARD_W, total_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # 卡片背景
    draw.rounded_rectangle(
        [8, 8, CARD_W - 8, total_h - 8],
        radius=18, fill=CARD_BG,
    )

    y = PAD_Y + 8

    # 标题
    draw.text((PAD_X + 10, y + 10), f"✦  {bot_name} 命令指南", font=title_font, fill=TITLE_COLOR)
    draw.line([(PAD_X, y + TITLE_H - 10), (CARD_W - PAD_X, y + TITLE_H - 10)], fill=DIVIDER, width=1)
    y += TITLE_H

    # 分类
    for sect_title, items in data.items():
        sect_h = SECT_PAD * 2 + 28 + len(items) * ITEM_H
        # 分类背景
        draw.rounded_rectangle(
            [PAD_X, y, CARD_W - PAD_X, y + sect_h],
            radius=12, fill=SECTION_BG,
        )
        # 左侧色条
        draw.rounded_rectangle(
            [PAD_X, y, PAD_X + 5, y + sect_h],
            radius=2, fill=ACCENT,
        )
        # 分类标题
        sy = y + SECT_PAD
        draw.text((PAD_X + 16, sy), sect_title, font=section_font, fill=SECTION_TEXT)
        sy += 32

        # 命令列表
        for key, desc in items:
            draw.text((PAD_X + 20, sy + 6), key, font=item_key_font, fill=ITEM_KEY)
            # 计算 key 宽度，desc 放在后面
            bbox = item_key_font.getbbox(key)
            key_w = bbox[2] - bbox[0] if bbox else 0
            draw.text((PAD_X + 24 + key_w + 8, sy + 7), f"— {desc}", font=item_desc_font, fill=ITEM_DESC)
            sy += ITEM_H

        y += sect_h + SECTION_GAP

    # 底部
    draw.text((PAD_X + 10, y), "发送 /帮助 查看此指南  |  具体功能可直接 @我", font=footer_font, fill=FOOTER_COLOR)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
