"""
天气预报模块 — /xx天气 命令，使用接口盒子 (apihz) 天气接口
中国气象局数据，支持 1-7 天预报 + 实况 + 预警
"""
import random
from datetime import datetime, timedelta

import httpx

API_ID = "10012789"
API_KEY = "8eb14a1232332764a246f46b062c715f"

# apihz 负载均衡节点
_API_HOSTS = [
    "https://cn.apihz.cn",
    "http://81.69.163.176",
    "http://101.35.2.25",
    "http://124.222.204.22",
    "http://101.34.207.105",
    "http://43.142.65.209",
    "http://81.68.85.14",
    "http://124.222.2.141",
    "http://49.235.116.180",
    "http://129.211.70.28",
    "http://124.220.236.177",
]


_WEATHER_EMOJI = {
    "晴": "☀️", "多云": "⛅", "阴": "☁️",
    "小雨": "🌦", "中雨": "🌧", "大雨": "🌧", "暴雨": "🌧",
    "雷阵雨": "⛈", "阵雨": "🌦", "雨": "🌧",
    "小雪": "🌨", "中雪": "❄️", "大雪": "❄️", "暴雪": "❄️", "雪": "🌨",
    "雾": "🌫", "霾": "🌫", "沙尘暴": "🌪",
}


def _weather_icon(text: str) -> str:
    for key, emoji in _WEATHER_EMOJI.items():
        if key in text:
            return emoji
    return "🌤"


def _compact_weather(w1: str, w2: str) -> str:
    if not w1:
        return w2 or ""
    if not w2 or w1 == w2:
        return w1
    return f"{w1}→{w2}"


_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _date_label(offset: int) -> str:
    dt = datetime.now() + timedelta(days=offset)
    weekday = _WEEKDAYS[dt.weekday()]
    return f"{dt.month}月{dt.day}日（{weekday}）"


def _extract_alarm_brief(title: str) -> str:
    """从预警标题中提取简短描述"""
    import re
    m = re.search(r"发布(.+)$", title)
    return m.group(1) if m else title


async def fetch_weather(place: str, sheng: str = "", days: int = 7) -> dict:
    """获取天气预报

    Args:
        place: 地点（市级名称）
        sheng: 省份名称（可选）
        days: 预报天数 1-7
    """
    timeout = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)
    last_err = None
    for host in _API_HOSTS:
        try:
            url = f"{host}/api/tianqi/tqyb.php"
            params = {
                "id": API_ID,
                "key": API_KEY,
                "place": place,
                "day": str(days),
                "hourtype": "0",
                "suntimetype": "1",
            }
            if sheng:
                params["sheng"] = sheng
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if data.get("code") != 200:
                    continue
                return data
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ConnectError) as e:
            last_err = e
            continue
    raise RuntimeError(f"所有 apihz 节点均不可用: {last_err}")


ALAPI_TOKEN = "bvw7kwtpteetrhtdkqggdrnohoewj7"


async def fetch_weather_alapi(city: str) -> dict | None:
    """从 alapi v3 获取当前天气 + 逐小时预报"""
    timeout = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)
    try:
        url = "https://v3.alapi.cn/api/tianqi"
        params = {"token": ALAPI_TOKEN, "city": city, "hour": "1"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("success"):
                return data.get("data")
    except Exception:
        pass
    return None


def format_weather(data: dict) -> str:
    """格式化天气预报为表格文本"""
    if data.get("code") != 200:
        return "没查到这个地方的天气，试试只说城市名？比如「青岛天气」「成都天气」\n省级地名查不了哦，得说具体的市"

    sheng = data.get("sheng", "")
    shi = data.get("shi", "")
    name = data.get("name", "")
    location = f"{sheng}{shi}" if sheng and shi else name

    today = datetime.now()
    end_day = today + timedelta(days=6)
    lines = [f"**{location}天气预报（{today.month}月{today.day}日 - {end_day.month}月{end_day.day}日）**"]
    lines.append("")

    # ── 实况 ──
    nowinfo = data.get("nowinfo")
    if nowinfo:
        temp = nowinfo.get("temperature", "")
        feelst = nowinfo.get("feelst", "")
        humidity = nowinfo.get("humidity", "")
        wind_dir = nowinfo.get("windDirection", "")
        wind_scale = nowinfo.get("windScale", "")
        parts = []
        if temp:
            parts.append(f"📍 实况：{temp}°")
        if feelst:
            parts.append(f"  体感 {feelst}°")
        if humidity:
            parts.append(f"  💧{humidity}%")
        if wind_dir or wind_scale:
            parts.append(f"  🌬{wind_dir}{wind_scale}")
        if parts:
            lines.append("".join(parts))
            lines.append("")

    # ── 预警 ──
    alarms = data.get("alarm") or []
    if alarms:
        for alarm in alarms:
            title = alarm.get("title", "")
            if title:
                lines.append(f"⚠️ {_extract_alarm_brief(title)}")
        lines.append("")

    # ── 表头 ──
    lines.append("| 日期 | 天气 |")
    lines.append("|------|------|")

    # ── 逐天 ──
    rain_count = 0
    for i in range(1, 8):
        if i == 1:
            w1 = data.get("weather1", "")
            w2 = data.get("weather2", "")
            t1 = data.get("wd1", "")
            t2 = data.get("wd2", "")
        else:
            dd = data.get(f"weatherday{i}")
            if not dd:
                continue
            w1 = dd.get("weather1", "")
            w2 = dd.get("weather2", "")
            t1 = dd.get("wd1", "")
            t2 = dd.get("wd2", "")

        weather_desc = _compact_weather(w1, w2)
        icon = _weather_icon(weather_desc)
        date_str = _date_label(i - 1)
        temp_range = f" {t2}~{t1}°" if t1 and t2 else ""
        lines.append(f"| {date_str} | {icon} {weather_desc}{temp_range} |")

        if any(r in weather_desc for r in ("雨", "雷")):
            rain_count += 1

    # ── 尾部建议 ──
    lines.append("")
    if rain_count >= 3:
        lines.append("最近几天阴雨天居多，出门记得带伞 ☂️")
    elif rain_count >= 1:
        lines.append("近几天有雨，出行注意备伞 ☂️")
    else:
        lines.append("近几天天气不错，适合出行 🌈")

    # ── 随机小 tips ──
    tips_pool = []
    if nowinfo:
        # 基于湿度的提示
        humidity_val = nowinfo.get("humidity", 0)
        try:
            h = int(humidity_val) if humidity_val else 0
        except (ValueError, TypeError):
            h = 0
        if h >= 80:
            tips_pool.append("湿度较高，注意防潮")
        elif h < 30:
            tips_pool.append("空气干燥，记得补水")
    if rain_count == 0:
        tips_pool.append("紫外线较强，注意防晒")
    if tips_pool:
        lines.append("")
        lines.append(f"💡 {random.choice(tips_pool)}")

    return "\n".join(lines)
