"""青岛官方潮汐数据访问、解析与格式化。"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Generic, TypeVar
from zoneinfo import ZoneInfo

import aiohttp


TZ = ZoneInfo("Asia/Shanghai")
HOURLY_URL = "http://www.qdmf.org.cn/Ajax/GetTideData.ashx"
BEACH_URL = "http://www.qdmf.org.cn/Ajax/QD24hTide.ashx"
SOURCE_NAME = "自然资源部北海预报减灾中心"

BEACH_ALIASES = {
    "一浴": "第一海水浴场",
    "第一浴场": "第一海水浴场",
    "第一海水浴场": "第一海水浴场",
    "二浴": "第二海水浴场",
    "第二浴场": "第二海水浴场",
    "第二海水浴场": "第二海水浴场",
    "三浴": "第三海水浴场",
    "第三浴场": "第三海水浴场",
    "第三海水浴场": "第三海水浴场",
    "六浴": "第六海水浴场",
    "第六浴场": "第六海水浴场",
    "第六海水浴场": "第六海水浴场",
    "石老人": "石老人海水浴场",
    "石老人浴场": "石老人海水浴场",
    "石老人海水浴场": "石老人海水浴场",
    "金沙滩": "金沙滩海水浴场（青岛）",
    "金沙滩浴场": "金沙滩海水浴场（青岛）",
    "金沙滩海水浴场": "金沙滩海水浴场（青岛）",
    "仰口": "仰口海水浴场",
    "仰口浴场": "仰口海水浴场",
    "仰口海水浴场": "仰口海水浴场",
    "银沙滩": "银沙滩海水浴场",
    "银沙滩浴场": "银沙滩海水浴场",
    "银沙滩海水浴场": "银沙滩海水浴场",
    "灵山湾": "灵山湾海水浴场",
    "灵山湾浴场": "灵山湾海水浴场",
    "灵山湾海水浴场": "灵山湾海水浴场",
}

_DATE_WORDS = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2}
_TIDE_WORDS = ("涨潮退潮", "涨退潮", "潮汐", "涨潮", "退潮")
_HOURLY_ITEM_RE = re.compile(
    r'\{"TIDETIME":\'(?P<hour>\d+)\',"TIDEHEIGHT":\'(?P<height>-?\d+)\','
    r'"TIDEDATE":\'(?P<day>[^\']+)\'\}'
)


@dataclass(frozen=True, slots=True)
class TideCommand:
    location: str | None
    date_offset: int | None


@dataclass(frozen=True, slots=True)
class TidePoint:
    at: datetime
    height_cm: int


@dataclass(frozen=True, slots=True)
class TideExtreme:
    kind: str
    at: datetime
    height_cm: int


@dataclass(frozen=True, slots=True)
class BeachForecast:
    location: str
    forecast_date: date
    high_tides: tuple[tuple[str, int], ...]
    low_tides: tuple[tuple[str, int], ...]


def _strip_system_identity_prefix(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("[系统身份提示：") and "]\n" in cleaned:
        cleaned = cleaned.split("]\n", 1)[1].lstrip()
    return cleaned


def parse_command(
    text: str, *, allow_wake_normalized: bool = False
) -> TideCommand | None:
    """识别潮汐命令；非潮汐消息返回 None，地点错误则抛出 ValueError。"""
    cleaned = _strip_system_identity_prefix(text)
    if cleaned.startswith(("/", "\\")):
        cleaned = cleaned[1:].strip()
    elif not allow_wake_normalized:
        return None
    compact = re.sub(r"\s+", "", cleaned)

    # 只接管明确的潮汐命令，不拦截“赶海要等退潮吗”等普通 AI 问句。
    command_probe = compact
    if command_probe.startswith("青岛"):
        command_probe = command_probe[2:]
    for word in _DATE_WORDS:
        if command_probe.startswith(word):
            command_probe = command_probe[len(word) :]
            break
    if command_probe.startswith("青岛"):
        command_probe = command_probe[2:]
    if not any(command_probe.startswith(word) for word in _TIDE_WORDS):
        return None

    date_offsets = {
        offset for word, offset in _DATE_WORDS.items() if word in compact
    }
    if len(date_offsets) > 1:
        raise ValueError("日期只能选择今天、明天或后天中的一个")
    date_offset = next(iter(date_offsets), None)
    for word in _DATE_WORDS:
        compact = compact.replace(word, "")

    if compact.startswith("青岛"):
        compact = compact[2:]
    tide_word = next(word for word in _TIDE_WORDS if word in compact)
    compact = compact.replace(tide_word, "", 1)
    compact = compact.removesuffix("时间").strip()

    if compact in {"", "青岛", "青岛沿岸", "沿岸"}:
        return TideCommand(location=None, date_offset=date_offset)
    location = BEACH_ALIASES.get(compact)
    if not location:
        supported = "一浴、二浴、三浴、六浴、石老人、金沙滩、仰口、银沙滩、灵山湾"
        raise ValueError(f"暂不支持“{compact}”，可查询：{supported}")
    return TideCommand(location=location, date_offset=date_offset)


def parse_hourly_payload(payload: str) -> list[TidePoint]:
    """解析官网返回的非标准 JSON（字段值使用单引号）。"""
    points: list[TidePoint] = []
    for match in _HOURLY_ITEM_RE.finditer(payload):
        base = datetime.strptime(match.group("day"), "%Y/%m/%d %H:%M:%S")
        at = base.replace(hour=int(match.group("hour")), tzinfo=TZ)
        points.append(TidePoint(at=at, height_cm=int(match.group("height"))))
    points.sort(key=lambda item: item.at)
    if len(points) < 24:
        raise ValueError("官方逐小时潮汐数据不完整")
    return points


def _parse_hhmm(value: object) -> str | None:
    digits = str(value or "").strip()
    if not re.fullmatch(r"\d{4}", digits):
        return None
    hour, minute = int(digits[:2]), int(digits[2:])
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_beach_payload(payload: str) -> list[BeachForecast]:
    parsed = json.loads(payload)
    rows = parsed.get("rows") if isinstance(parsed, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("官方浴场潮汐数据为空")

    forecasts: list[BeachForecast] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            forecast_date = datetime.strptime(
                str(row["FORECASTDATE"]), "%Y-%m-%d-%H"
            ).date()
        except (KeyError, TypeError, ValueError):
            continue

        highs = []
        lows = []
        for time_key, height_key in (
            ("FIRSTHIGHTIME", "FIRSTHIGHLEVEL"),
            ("SECONDHIGHTIME", "SECONDHEIGHTLEVEL"),
        ):
            tide_time = _parse_hhmm(row.get(time_key))
            if tide_time:
                highs.append((tide_time, int(row[height_key])))
        for time_key, height_key in (
            ("FIRSTLOWTIME", "FIRSTLOWLEVEL"),
            ("SECONDLOWTIME", "SECONDLOWLEVEL"),
        ):
            tide_time = _parse_hhmm(row.get(time_key))
            if tide_time:
                lows.append((tide_time, int(row[height_key])))
        if highs or lows:
            forecasts.append(
                BeachForecast(
                    location=str(row.get("SEABEACH") or "未知浴场"),
                    forecast_date=forecast_date,
                    high_tides=tuple(highs),
                    low_tides=tuple(lows),
                )
            )
    if not forecasts:
        raise ValueError("官方浴场潮汐数据格式异常")
    return forecasts


def calculate_extremes(points: list[TidePoint], target: date) -> list[TideExtreme]:
    """用相邻三个整点作抛物线插值，估算极值发生的分钟。"""
    extremes: list[TideExtreme] = []
    for index in range(1, len(points) - 1):
        previous, current, following = points[index - 1 : index + 2]
        if current.at.date() != target:
            continue
        is_high = current.height_cm > previous.height_cm and current.height_cm > following.height_cm
        is_low = current.height_cm < previous.height_cm and current.height_cm < following.height_cm
        if not (is_high or is_low):
            continue

        y_prev, y_now, y_next = previous.height_cm, current.height_cm, following.height_cm
        denominator = y_prev - 2 * y_now + y_next
        offset = 0.0 if denominator == 0 else 0.5 * (y_prev - y_next) / denominator
        offset = max(-1.0, min(1.0, offset))
        a = (y_prev - 2 * y_now + y_next) / 2
        b = (y_next - y_prev) / 2
        height = round(a * offset * offset + b * offset + y_now)
        at = current.at + timedelta(minutes=round(offset * 60))
        extremes.append(
            TideExtreme(kind="high" if is_high else "low", at=at, height_cm=height)
        )
    return extremes


def _format_extreme_list(extremes: list[TideExtreme], kind: str) -> str:
    selected = [item for item in extremes if item.kind == kind]
    if not selected:
        return "暂无"
    return "；".join(f"{item.at:%H:%M}（{item.height_cm}cm）" for item in selected)


def _current_trend(points: list[TidePoint], now: datetime) -> str | None:
    before = [point for point in points if point.at <= now]
    after = [point for point in points if point.at > now]
    if not before or not after:
        return None
    return "涨潮中" if after[0].height_cm > before[-1].height_cm else "退潮中"


def format_hourly_forecast(
    points: list[TidePoint], target: date, now: datetime, *, stale: bool = False
) -> str:
    day_points = [point for point in points if point.at.date() == target]
    if not day_points:
        first, last = points[0].at.date(), points[-1].at.date()
        raise ValueError(f"官方当前只提供 {first:%m月%d日} 至 {last:%m月%d日} 的预报")
    extremes = calculate_extremes(points, target)
    lines = [f"青岛沿岸潮汐｜{target:%m月%d日}"]
    if target == now.date():
        trend = _current_trend(points, now)
        upcoming = next((item for item in extremes if item.at > now), None)
        if trend:
            current = f"当前：{trend}"
            if upcoming:
                label = "高潮" if upcoming.kind == "high" else "低潮"
                current += f"，下一次{label}约 {upcoming.at:%H:%M}"
            lines.append(current)
    lines.append(f"高潮：{_format_extreme_list(extremes, 'high')}")
    lines.append(f"低潮：{_format_extreme_list(extremes, 'low')}")
    lines.append(f"数据：{SOURCE_NAME}（逐小时预报，潮时为插值估算）")
    if stale:
        lines.append("提示：官网暂时不可用，以上为最近一次缓存数据")
    return "\n".join(lines)


def format_beach_forecast(forecast: BeachForecast, *, stale: bool = False) -> str:
    def join(items: tuple[tuple[str, int], ...]) -> str:
        return "；".join(f"{tide_time}（{height}cm）" for tide_time, height in items) or "暂无"

    display_name = forecast.location.replace("（青岛）", "")
    lines = [
        f"{display_name}潮汐｜{forecast.forecast_date:%m月%d日}",
        f"高潮：{join(forecast.high_tides)}",
        f"低潮：{join(forecast.low_tides)}",
        f"数据：{SOURCE_NAME}（官方浴场预报）",
    ]
    if stale:
        lines.append("提示：官网暂时不可用，以上为最近一次缓存数据")
    return "\n".join(lines)


T = TypeVar("T")


@dataclass(slots=True)
class _CacheEntry(Generic[T]):
    value: T
    fetched_at: float


class TideClient:
    """带十分钟缓存和六小时故障缓存的官网只读客户端。"""

    def __init__(self, cache_ttl: int = 600, stale_ttl: int = 21600) -> None:
        self.cache_ttl = cache_ttl
        self.stale_ttl = stale_ttl
        self._hourly: _CacheEntry[list[TidePoint]] | None = None
        self._beaches: _CacheEntry[list[BeachForecast]] | None = None
        self._lock = asyncio.Lock()

    async def _download(self, url: str) -> str:
        timeout = aiohttp.ClientTimeout(total=12, connect=5)
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; JiangTide/1.0)",
            "Referer": "http://www.qdmf.org.cn/",
        }
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                return await response.text(encoding="utf-8")

    async def _get_cached(self, attr: str, url: str, parser):
        now = time.monotonic()
        entry = getattr(self, attr)
        if entry and now - entry.fetched_at <= self.cache_ttl:
            return entry.value, False
        async with self._lock:
            now = time.monotonic()
            entry = getattr(self, attr)
            if entry and now - entry.fetched_at <= self.cache_ttl:
                return entry.value, False
            try:
                value = parser(await self._download(url))
            except Exception:
                if entry and now - entry.fetched_at <= self.stale_ttl:
                    return entry.value, True
                raise
            setattr(self, attr, _CacheEntry(value=value, fetched_at=now))
            return value, False

    async def fetch_hourly(self) -> tuple[list[TidePoint], bool]:
        return await self._get_cached("_hourly", HOURLY_URL, parse_hourly_payload)

    async def fetch_beaches(self) -> tuple[list[BeachForecast], bool]:
        return await self._get_cached("_beaches", BEACH_URL, parse_beach_payload)
