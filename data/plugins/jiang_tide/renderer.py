"""青岛潮汐图片卡片渲染。"""

from __future__ import annotations

import tempfile
import time
import uuid
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .tide import (
    SOURCE_NAME,
    BeachForecast,
    TideExtreme,
    TidePoint,
    calculate_extremes,
)


WIDTH = 840
CARD_MARGIN = 34
FONT_REGULAR = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD = Path(r"C:\Windows\Fonts\msyhbd.ttc")
WEEKDAYS = "一二三四五六日"


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold and FONT_BOLD.exists() else FONT_REGULAR
    return ImageFont.truetype(str(path), size)


def _output_path(output_dir: str | Path | None = None) -> Path:
    folder = Path(output_dir) if output_dir else Path(tempfile.gettempdir()) / "jiang_tide"
    folder.mkdir(parents=True, exist_ok=True)
    if output_dir is None:
        cutoff = time.time() - 24 * 60 * 60
        for old_file in folder.glob("tide_*.png"):
            try:
                if old_file.stat().st_mtime < cutoff:
                    old_file.unlink()
            except OSError:
                pass
    return folder / f"tide_{uuid.uuid4().hex}.png"


def _rounded(draw: ImageDraw.ImageDraw, box, radius: int, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _text(draw, xy, value: str, size: int, fill, *, bold: bool = False, anchor=None):
    draw.text(xy, value, font=_font(size, bold=bold), fill=fill, anchor=anchor)


def _wrap_text(draw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _draw_tide_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    subtitle: str,
    items: list[tuple[str, int]],
    *,
    accent: tuple[int, int, int],
    soft: tuple[int, int, int],
) -> None:
    left, top, right, bottom = box
    _rounded(draw, box, 24, fill=soft)
    draw.ellipse((left + 25, top + 27, left + 39, top + 41), fill=accent)
    _text(draw, (left + 52, top + 23), title, 27, "#16324A", bold=True)
    _text(draw, (right - 24, top + 28), subtitle, 16, "#6C8192", anchor="ra")

    if not items:
        _text(draw, ((left + right) // 2, top + 128), "暂无数据", 24, "#8A9AA7", anchor="mm")
        return

    row_top = top + 78
    row_height = 66
    for index, (tide_time, height) in enumerate(items[:3]):
        y = row_top + index * row_height
        if index:
            draw.line((left + 25, y - 9, right - 25, y - 9), fill="#DCE7ED", width=2)
        _text(draw, (left + 25, y), tide_time, 31, "#102A43", bold=True)
        _text(draw, (right - 25, y + 5), f"{height} cm", 22, accent, bold=True, anchor="ra")


def _current_status(
    points: list[TidePoint], extremes: list[TideExtreme], now: datetime, target: date
) -> tuple[str, str]:
    if target != now.date():
        return "潮汐预报", "查看当天高潮与低潮时刻"
    before = [point for point in points if point.at <= now]
    after = [point for point in points if point.at > now]
    trend = "潮汐预报"
    if before and after:
        trend = "涨潮中" if after[0].height_cm > before[-1].height_cm else "退潮中"
    upcoming = next((item for item in extremes if item.at > now), None)
    if not upcoming:
        return trend, "今日潮汐周期接近结束"
    label = "高潮" if upcoming.kind == "high" else "低潮"
    return trend, f"下一次{label}约 {upcoming.at:%H:%M}"


def _render_card(
    *,
    location: str,
    target: date,
    status: str,
    status_detail: str,
    highs: list[tuple[str, int]],
    lows: list[tuple[str, int]],
    source_note: str,
    stale: bool,
    extra_note: str | None,
    output_dir: str | Path | None,
) -> str:
    note_count = int(bool(stale)) + int(bool(extra_note))
    height = 620 + note_count * 70
    image = Image.new("RGB", (WIDTH, height), "#EAF3F7")
    draw = ImageDraw.Draw(image)

    # 外层阴影与主卡片
    _rounded(draw, (CARD_MARGIN + 7, CARD_MARGIN + 9, WIDTH - CARD_MARGIN + 7, height - CARD_MARGIN + 9), 34, "#C8DCE5")
    _rounded(draw, (CARD_MARGIN, CARD_MARGIN, WIDTH - CARD_MARGIN, height - CARD_MARGIN), 34, "#FFFFFF")

    # 顶部海蓝色区域
    header = (CARD_MARGIN, CARD_MARGIN, WIDTH - CARD_MARGIN, 238)
    _rounded(draw, header, 34, "#087E8B")
    draw.rectangle((CARD_MARGIN, 190, WIDTH - CARD_MARGIN, 238), fill="#087E8B")
    draw.ellipse((WIDTH - 230, -65, WIDTH + 70, 235), fill="#1399A4")
    draw.ellipse((WIDTH - 120, 80, WIDTH + 90, 290), fill="#3BB3B8")

    _text(draw, (72, 68), "青岛潮汐", 42, "#FFFFFF", bold=True)
    _text(draw, (72, 126), location, 25, "#D9FAFB")
    date_text = f"{target:%m月%d日}  周{WEEKDAYS[target.weekday()]}"
    _text(draw, (WIDTH - 72, 79), date_text, 23, "#FFFFFF", bold=True, anchor="ra")

    _rounded(draw, (72, 173, 226, 218), 22, "#FFFFFF")
    _text(draw, (149, 195), status, 21, "#087E8B", bold=True, anchor="mm")
    _text(draw, (248, 196), status_detail, 21, "#E7FFFF", anchor="lm")

    panel_top = 272
    panel_bottom = 496
    gap = 20
    panel_width = (WIDTH - 2 * 72 - gap) // 2
    _draw_tide_panel(
        draw,
        (72, panel_top, 72 + panel_width, panel_bottom),
        "高潮",
        "潮位峰值",
        highs,
        accent=(232, 106, 77),
        soft=(255, 242, 237),
    )
    _draw_tide_panel(
        draw,
        (72 + panel_width + gap, panel_top, WIDTH - 72, panel_bottom),
        "低潮",
        "潮位谷值",
        lows,
        accent=(41, 133, 196),
        soft=(237, 247, 255),
    )

    footer_y = 532
    draw.line((72, footer_y - 15, WIDTH - 72, footer_y - 15), fill="#E2EBF0", width=2)
    _text(draw, (72, footer_y), f"数据来源  {SOURCE_NAME}", 18, "#526A7A")
    _text(draw, (WIDTH - 72, footer_y), source_note, 17, "#78909C", anchor="ra")

    note_y = footer_y + 39
    note_font = _font(17)
    notes = []
    if stale:
        notes.append("官网暂时不可用，当前展示最近一次缓存数据")
    if extra_note:
        notes.append(extra_note)
    for note in notes:
        draw.ellipse((73, note_y + 8, 81, note_y + 16), fill="#E09F3E")
        wrapped = _wrap_text(draw, note, note_font, WIDTH - 180)
        for line_index, line in enumerate(wrapped[:2]):
            _text(draw, (94, note_y + line_index * 27), line, 17, "#7B5A20")
        note_y += 70

    path = _output_path(output_dir)
    image.save(path, format="PNG", optimize=True)
    return str(path)


def render_hourly_card(
    points: list[TidePoint],
    target: date,
    now: datetime,
    *,
    stale: bool = False,
    extra_note: str | None = None,
    output_dir: str | Path | None = None,
) -> str:
    day_points = [point for point in points if point.at.date() == target]
    if not day_points:
        first, last = points[0].at.date(), points[-1].at.date()
        raise ValueError(f"官方当前只提供 {first:%m月%d日} 至 {last:%m月%d日} 的预报")
    extremes = calculate_extremes(points, target)
    status, detail = _current_status(points, extremes, now, target)
    highs = [(f"{item.at:%H:%M}", item.height_cm) for item in extremes if item.kind == "high"]
    lows = [(f"{item.at:%H:%M}", item.height_cm) for item in extremes if item.kind == "low"]
    return _render_card(
        location="青岛沿岸",
        target=target,
        status=status,
        status_detail=detail,
        highs=highs,
        lows=lows,
        source_note="逐小时预报 · 潮时为插值估算",
        stale=stale,
        extra_note=extra_note,
        output_dir=output_dir,
    )


def render_beach_card(
    forecast: BeachForecast,
    *,
    stale: bool = False,
    output_dir: str | Path | None = None,
) -> str:
    return _render_card(
        location=forecast.location.replace("（青岛）", ""),
        target=forecast.forecast_date,
        status="官方潮时",
        status_detail="海水浴场精确预报",
        highs=list(forecast.high_tides),
        lows=list(forecast.low_tides),
        source_note="官方浴场预报",
        stale=stale,
        extra_note=None,
        output_dir=output_dir,
    )
