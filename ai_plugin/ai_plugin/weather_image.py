"""
天气图片卡片渲染模块 — HTML + Playwright 渲染毛玻璃风格天气卡片
"""
from __future__ import annotations

import base64
import os
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


# ── 构建 HTML ────────────────────────────────────────
def _build_html(alapi_data: dict, apihz_data: dict | None) -> str:
    html = _load_html_template()

    city = alapi_data.get("city", "")
    weather = alapi_data.get("weather", "")
    temp = str(alapi_data.get("temp", ""))
    feels = temp
    if apihz_data and apihz_data.get("code") == 200:
        nowinfo = apihz_data.get("nowinfo") or {}
        feels = str(nowinfo.get("feelst", temp))
    weather_code = alapi_data.get("weather_code", "duoyun")
    rain = alapi_data.get("rain", "0")
    humidity = alapi_data.get("humidity", "").replace("%", "")
    wind_speed = alapi_data.get("wind_speed", "").replace("km/h", "")
    update = alapi_data.get("update_time", "")[-8:][:5]

    today = datetime.now()
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    date_str = f"{today.month}月{today.day}日 {weekdays[today.weekday()]}"

    icon_main = _icon_img_tag(weather_code, 120)

    # 小时预报
    hours = alapi_data.get("hour", [])[:7]
    now_hour = datetime.now().hour
    hourly_items = []
    for i, h in enumerate(hours):
        time_str = h.get("time", "")
        try:
            hour = int(time_str[11:13])
        except (ValueError, IndexError):
            hour = now_hour + i
        label = "现在" if i == 0 else f"{hour}:00"
        h_temp = str(h.get("temp", ""))
        h_code = h.get("wea_code", "duoyun")
        h_icon = _icon_img_tag(h_code, 28)
        active = ' class="active"' if i == 0 else ""
        hourly_items.append(f'''<div class="hour-item{active}">
        <div class="hour-time">{label}</div>
        <div class="hour-icon">{h_icon}</div>
        <div class="hour-temp">{h_temp}°</div>
      </div>''')

    # 7天预报
    daily_days = _build_daily_days(apihz_data, alapi_data)
    all_temps = []
    for d in daily_days[:7]:
        try:
            all_temps.append(int(d.get("low", 20)))
            all_temps.append(int(d.get("high", 30)))
        except (ValueError, TypeError):
            pass
    t_min = min(all_temps) if all_temps else 15
    t_max = max(all_temps) if all_temps else 35
    t_range = t_max - t_min if t_max != t_min else 1

    weekdays_list = ["今天", "明天"]
    for i in range(2, 7):
        dt = datetime.now() + timedelta(days=i)
        weekdays_list.append(["周一", "周二", "周三", "周四", "周五", "周六", "周日"][dt.weekday()])

    daily_items = []
    for i, d in enumerate(daily_days[:7]):
        day_label = weekdays_list[i] if i < len(weekdays_list) else f"第{i+1}天"
        d_weather = d.get("weather", "")
        d_code = d.get("weather_code", "duoyun")
        try:
            low = int(d.get("low", 0))
            high = int(d.get("high", 0))
        except (ValueError, TypeError):
            low, high = 0, 0
        d_icon = _icon_img_tag(d_code, 22)
        fl = int((low - t_min) / t_range * 90)
        fw = max(8, int((high - low) / t_range * 90))
        bar_style = f"left:{fl}px;width:{fw}px;background:{_bar_gradient(low, high, t_min, t_range)}"
        daily_items.append(f'''<div class="daily-item">
        <div class="daily-day">{day_label}</div>
        <div class="daily-icon">{d_icon}</div>
        <div class="daily-desc">{d_weather}</div>
        <div class="daily-temp-bar">
          <div class="daily-temp-low">{low}°</div>
          <div class="daily-bar-track"><div class="daily-bar-fill" style="{bar_style}"></div></div>
          <div class="daily-temp-high">{high}°</div>
        </div>
      </div>''')

    html = html.replace("{{CITY}}", city)
    html = html.replace("{{UPDATE_TIME}}", update)
    html = html.replace("{{DATE_STR}}", date_str)
    html = html.replace("{{WEATHER}}", weather)
    html = html.replace("{{TEMP}}", temp)
    html = html.replace("{{ICON_MAIN}}", icon_main)
    html = html.replace("{{FEELS}}", feels)
    html = html.replace("{{RAIN}}", rain)
    html = html.replace("{{WIND}}", f"{wind_speed}km/h")
    html = html.replace("{{HUMIDITY}}", humidity)
    html = html.replace("{{HOURLY_ITEMS}}", "\n      ".join(hourly_items))
    html = html.replace("{{DAILY_ITEMS}}", "\n    ".join(daily_items))

    return html


# ── Playwright 渲染 ──────────────────────────────────
async def _render_to_png(html: str, output_path: str) -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 480, "height": 900}, device_scale_factor=2)
        await page.set_content(html, wait_until="networkidle")
        await page.wait_for_timeout(800)
        height = await page.evaluate("document.body.scrollHeight")
        await page.set_viewport_size({"width": 480, "height": height})
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
    cached = _get_cache(city)
    if cached:
        return cached

    html = _build_html(alapi_data, apihz_data)

    tmp_dir = tempfile.gettempdir()
    path = os.path.join(tmp_dir, f"weather_{city}.png")
    await _render_to_png(html, path)

    _set_cache(city, path)
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
