"""
天气图片卡片渲染模块 — HTML + Playwright 渲染毛玻璃风格天气卡片
"""
from __future__ import annotations

import base64
import hashlib
import html as html_lib
import os
import re
import tempfile
from datetime import datetime, timedelta

from weather_svg import get_svg_icon

# ── 常量 ──────────────────────────────────────────────
_HTML_PATH = os.path.join(os.path.dirname(__file__), "weather_card.html")


def _load_html_template() -> str:
    with open(_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _icon_img_tag(weather_code: str, size: int = 64) -> str:
    """生成 SVG 天气图标，作为 data URI 嵌入 HTML"""
    svg = get_svg_icon(weather_code, size)
    if not svg:
        return ""
    b64 = base64.b64encode(svg.encode("utf-8")).decode()
    return f'<img src="data:image/svg+xml;base64,{b64}" width="{size}" height="{size}" />'


# ── 温度条颜色 ───────────────────────────────────────
def _bar_gradient(low: int, high: int, t_min: int, t_range: int) -> str:
    avg = (low + high) / 2
    t = (avg - t_min) / t_range if t_range > 0 else 0.5
    t = max(0.0, min(1.0, t))
    if t < 0.33:
        r = int(80 + (120 - 80) * t * 3)
        g = int(170 + (200 - 170) * t * 3)
        b = int(255 - 40 * t * 3)
    elif t < 0.66:
        p = (t - 0.33) * 3
        r = int(120 + (255 - 120) * p)
        g = int(200 - 20 * p)
        b = int(215 - 115 * p)
    else:
        p = (t - 0.66) * 3
        r = 255
        g = int(180 - 70 * p)
        b = int(100 - 60 * p)
    return f"linear-gradient(90deg, rgb({r},{g},{b}), rgb({min(255,r+25)},{max(0,g-15)},{max(0,b-30)}))"


# ── 图标映射 ──────────────────────────────────────────
def _get_icon_name(weather_code: str) -> str:
    code = weather_code.lower() if weather_code else ""
    if "lei" in code:
        return "thunder"
    elif "zhenyu" in code:
        return "light_rain"
    elif "xiaoyu" in code:
        return "light_rain"
    elif "yu" in code:
        return "rain"
    elif "xue" in code:
        return "snow"
    elif "wu" in code or "mai" in code:
        return "fog"
    elif "qing" in code:
        return "sun"
    elif "duoyun" in code:
        return "partly_cloudy"
    elif "yin" in code:
        return "overcast"
    else:
        return "cloud"


def _weather_theme(weather_code: str, weather_text: str) -> str:
    code = (weather_code or "").lower()
    text = weather_text or ""

    if any(k in code for k in ("lei", "thunder")) or "雷" in text:
        return "thunder"
    if any(k in code for k in ("xue", "snow")) or "雪" in text:
        return "snow"
    if (
        ("yu" in code and "duoyun" not in code)
        or any(k in code for k in ("rain", "shower"))
        or "雨" in text
    ):
        return "rain"
    if any(k in code for k in ("wu", "mai", "fog", "haze")) or any(k in text for k in ("雾", "霾", "沙尘")):
        return "fog"
    if any(k in code for k in ("yin", "overcast")) or "阴" in text:
        return "overcast"
    if any(k in code for k in ("duoyun", "partly")) or "多云" in text:
        return "partly"
    if any(k in code for k in ("qing", "sun", "clear")) or "晴" in text:
        return "sunny"
    return "partly"


def _clean_temp(value: object) -> str:
    text = str(value or "").replace("℃", "").replace("°", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return match.group(0) if match else text


def _format_sunrise(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "--"
    match = re.search(r"(\d{1,2}:\d{2})", text)
    if match:
        return match.group(1)
    return text


def _sunrise_from_entry(entry: object) -> str:
    if not isinstance(entry, dict):
        return ""
    suntimes = entry.get("suntimes") or {}
    if isinstance(suntimes, dict):
        sunrise = str(suntimes.get("sunrise") or "").strip()
        if sunrise:
            return sunrise
    return str(entry.get("sunrise") or "").strip()


def _daily_sunrise_text(apihz_data: dict, day_index: int, day_data: dict | None = None) -> str:
    sunrise = _sunrise_from_entry(day_data or {})
    if sunrise:
        return sunrise

    suntimes = apihz_data.get("suntimes") or []
    if isinstance(suntimes, list) and 0 <= day_index - 1 < len(suntimes):
        return _sunrise_from_entry(suntimes[day_index - 1])
    if isinstance(suntimes, dict):
        return _sunrise_from_entry(suntimes)

    return _sunrise_from_entry(apihz_data if day_index == 1 else {})


def _date_range_label(start: datetime, days: int = 7) -> str:
    end = start + timedelta(days=days - 1)
    if start.month == end.month:
        return f"{start.month}月{start.day}日-{end.day}日"
    return f"{start.month}月{start.day}日-{end.month}月{end.day}日"


def _extract_alarm_brief(title: str) -> str:
    match = re.search(r"发布(.+)$", title or "")
    brief = match.group(1) if match else (title or "")
    brief = re.sub(r"\s+", " ", brief).strip()
    warning_match = re.search(r"(.+?预警)", brief)
    if warning_match:
        return warning_match.group(1)
    return brief[:8] if len(brief) > 8 else brief


def _alert_payload(apihz_data: dict | None) -> tuple[str, str]:
    alarms = []
    if apihz_data and apihz_data.get("code") == 200:
        alarms = apihz_data.get("alarm") or []
    if not alarms:
        return "none", "暂无预警"

    title = str((alarms[0] or {}).get("title", "")).strip()
    text = _extract_alarm_brief(title) or "Weather Alert"
    severity = "active"
    if "红色" in title or "红" in title:
        severity = "red"
    elif "橙色" in title or "橙" in title:
        severity = "orange"
    elif "黄色" in title or "黄" in title:
        severity = "yellow"
    elif "蓝色" in title or "蓝" in title:
        severity = "blue"
    return severity, text


# ── 构建 HTML ────────────────────────────────────────
def _build_html(alapi_data: dict, apihz_data: dict | None) -> str:
    html = _load_html_template()

    city = alapi_data.get("city", "")
    weather = alapi_data.get("weather", "")
    weather_code = alapi_data.get("weather_code", "duoyun")
    live_temp = _clean_temp(alapi_data.get("temp", ""))
    if apihz_data and apihz_data.get("code") == 200:
        nowinfo = apihz_data.get("nowinfo") or {}
        live_temp = _clean_temp(nowinfo.get("temperature") or live_temp)
    alert_severity, alert_text = _alert_payload(apihz_data)

    today = datetime.now()
    subtitle = f"{city} · {_date_range_label(today)}"

    # 7天预报
    daily_days = _build_daily_days(apihz_data, alapi_data)
    while len(daily_days) < 7:
        daily_days.append({
            "weather": weather,
            "weather_code": weather_code,
            "high": alapi_data.get("temp", "0"),
            "low": alapi_data.get("temp", "0"),
            "sunrise": "",
        })
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    daily_columns = []
    for i, d in enumerate(daily_days[:7]):
        dt = today + timedelta(days=i)
        day_label = weekday_names[dt.weekday()]
        d_weather = d.get("weather", "")
        d_code = d.get("weather_code", "duoyun")
        d_theme = _weather_theme(d_code, d_weather)
        high = _clean_temp(d.get("high", ""))
        low = _clean_temp(d.get("low", ""))
        sunrise = _format_sunrise(d.get("sunrise"))
        d_icon = _icon_img_tag(d_code, 112)
        weather_title = html_lib.escape(d_weather)
        daily_columns.append(f'''<div class="forecast-col weather-{d_theme}" title="{weather_title}">
        <div class="day">{day_label}</div>
        <div class="date">{dt.day}</div>
        <div class="icon-wrap">{d_icon}</div>
        <div class="temps">
          <div class="high">{high}°</div>
          <div class="low">{low}°</div>
        </div>
        <div class="sunrise">
          <div class="sunrise-mark"><span></span></div>
          <div class="sunrise-label">日出<br>{html_lib.escape(sunrise)}</div>
        </div>
      </div>''')

    html = html.replace("{{SUBTITLE}}", html_lib.escape(subtitle))
    html = html.replace("{{LIVE_TEMP}}", html_lib.escape(live_temp or "--"))
    html = html.replace("{{LIVE_WEATHER}}", html_lib.escape(weather or "实时天气"))
    html = html.replace("{{ALERT_SEVERITY}}", html_lib.escape(alert_severity))
    html = html.replace("{{ALERT_TEXT}}", html_lib.escape(alert_text))
    html = html.replace("{{DAILY_COLUMNS}}", "\n      ".join(daily_columns))

    return html


# ── Playwright 渲染 ──────────────────────────────────
async def _render_to_png(html: str, output_path: str) -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=2)
        await page.set_content(html, wait_until="networkidle")
        await page.evaluate("document.fonts && document.fonts.ready")
        await page.wait_for_timeout(800)
        height = await page.evaluate("document.body.scrollHeight")
        await page.set_viewport_size({"width": 1280, "height": height})
        await page.screenshot(path=output_path, full_page=True)
        await browser.close()


# ── 缓存 ──────────────────────────────────────────────
_cache: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 600


def _get_cache(city: str) -> str | None:
    import time
    if city in _cache:
        ts, path = _cache[city]
        if time.time() - ts < _CACHE_TTL and os.path.exists(path):
            return path
    return None


def _set_cache(city: str, path: str) -> None:
    import time
    _cache[city] = (time.time(), path)


# ── 公开接口 ──────────────────────────────────────────
async def render_weather_image(alapi_data: dict, apihz_data: dict | None = None) -> str:
    city = alapi_data.get("city", "")
    cache_key = "|".join(
        str(alapi_data.get(k, ""))
        for k in ("city", "weather", "weather_code", "temp")
    )
    if apihz_data:
        nowinfo = apihz_data.get("nowinfo") or {}
        alarms = apihz_data.get("alarm") or []
        cache_key += "|" + str(nowinfo.get("temperature", ""))
        cache_key += "|" + "|".join(str((a or {}).get("title", "")) for a in alarms[:2])
        cache_key += "|" + datetime.now().strftime("%Y-%m-%d")
        cache_key += "|" + "|".join(
            _daily_sunrise_text(
                apihz_data,
                i,
                apihz_data if i == 1 else apihz_data.get(f"weatherday{i}") or {},
            )
            for i in range(1, 8)
        )
    cached = _get_cache(cache_key)
    if cached:
        return cached

    html = _build_html(alapi_data, apihz_data)

    tmp_dir = tempfile.gettempdir()
    file_key = hashlib.md5(cache_key.encode("utf-8")).hexdigest()[:12]
    path = os.path.join(tmp_dir, f"weather_{file_key}.png")
    await _render_to_png(html, path)

    _set_cache(cache_key, path)
    return path


def _build_daily_days(apihz_data: dict | None, alapi_data: dict) -> list[dict]:
    days = []
    if apihz_data and apihz_data.get("code") == 200:
        for i in range(1, 8):
            if i == 1:
                day = {
                    "weather": apihz_data.get("weather1", ""),
                    "weather_code": _map_apihz_code(apihz_data.get("weather1img", "")),
                    "high": apihz_data.get("wd1", "0"),
                    "low": apihz_data.get("wd2", "0"),
                    "sunrise": _daily_sunrise_text(apihz_data, i, apihz_data),
                }
            else:
                dd = apihz_data.get(f"weatherday{i}")
                if not dd:
                    continue
                day = {
                    "weather": dd.get("weather1", ""),
                    "weather_code": _map_apihz_code(dd.get("weather1img", "")),
                    "high": dd.get("wd1", "0"),
                    "low": dd.get("wd2", "0"),
                    "sunrise": _daily_sunrise_text(apihz_data, i, dd),
                }
            days.append(day)
    else:
        from collections import defaultdict
        hourly = alapi_data.get("hour", [])
        grouped: dict[str, list] = defaultdict(list)
        for h in hourly:
            date_key = h.get("time", "")[:10]
            if date_key:
                grouped[date_key].append(h)
        for i, (date_key, hrs) in enumerate(sorted(grouped.items())[:7]):
            temps = [h.get("temp", 0) for h in hrs]
            wea = hrs[0].get("wea", "") if hrs else ""
            wea_code = hrs[0].get("wea_code", "duoyun") if hrs else "duoyun"
            days.append({
                "weather": wea,
                "weather_code": wea_code,
                "high": str(max(temps)) if temps else "0",
                "low": str(min(temps)) if temps else "0",
                "sunrise": "",
            })
    return days


def _map_apihz_code(img_url: str) -> str:
    name = img_url.rsplit("/", 1)[-1].rsplit(".", 1)[0] if img_url else ""
    mapping = {
        "qing": "qing", "duoyun": "duoyun", "yin": "yin",
        "yu": "yu", "lei": "lei", "xue": "xue",
        "xiaoyu": "xiaoyu", "zhongyu": "yu", "dayu": "yu",
        "zhenyu": "zhenyu", "lei_zhengyu": "lei",
        "wu": "wu", "mai": "mai", "shachenbao": "wu",
    }
    return mapping.get(name, "duoyun")
