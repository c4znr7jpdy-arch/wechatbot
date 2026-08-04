"""短剧资源站搜索、标题匹配与播放地址解析。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit

import aiohttp


logger = logging.getLogger("jiang_short_drama")


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class Episode:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    source: str
    episodes: tuple[Episode, ...]
    cover_url: str = ""
    year: str = ""
    actor: str = ""
    score: int = 0
    duration_seconds: float = 0.0
    is_full_version: bool = False


# 来源取自用户提供的「短百影视」UserScript。只启用已验证能返回 JSON 的
# HTTPS 源；单源故障不会影响其他源。
DEFAULT_SOURCES: tuple[Source, ...] = (
    Source("暴风资源", "https://bfzyapi.com/api.php/provide/vod/"),
    Source("非凡资源", "https://cj.ffzyapi.com/api.php/provide/vod/"),
    Source("量子资源", "https://cj.lziapi.com/api.php/provide/vod/"),
    Source("红牛资源", "https://www.hongniuzy2.com/api.php/provide/vod/from/hnm3u8/"),
    Source("极速资源", "https://jszyapi.com/api.php/provide/vod"),
    Source("百度云资源", "https://api.apibdzy.com/api.php/provide/vod/"),
    Source("金鹰资源", "https://jyzyapi.com/provide/vod/from/jinyingm3u8/at/json"),
    Source("飘零资源", "https://p2100.net/api.php/provide/vod/"),
)

_COMMAND_RE = re.compile(r"^短剧[ \t]+(?P<query>\S(?:.*\S)?)$")
_EPISODE_REQUEST_RE = re.compile(
    r"^(?P<title>.+?)[ \t]+第(?P<number>[1-9]\d{0,2})集$"
)
_EPISODE_RANGE_RE = re.compile(
    r"第?\s*(?P<start>\d+)\s*(?:[-~～—至到]\s*(?P<end>\d+)\s*)?集"
)
EXACT_TITLE_SCORE = 10_000
DEFAULT_MIN_FUZZY_SCORE = 3_000
_KNOWN_TITLE_HINTS: dict[str, tuple[str, ...]] = {
    "全球杀机": ("全球杀戮",),
    "腊肉": ("致命的腊肉", "腊肉风云"),
}
_NOISE_RE = re.compile(
    r"(?:全集|高清|超清|蓝光|修复版|国语|中字|中文字幕|完整版|无删减|"
    r"4k|8k|bd|hd|web-?dl|webrip|bluray)$",
    re.IGNORECASE,
)
_BRACKET_NOISE_RE = re.compile(
    r"[（(【\[].*?(?:全集|高清|超清|4k|8k|国语|中字|完整版).*?[）)】\]]",
    re.IGNORECASE,
)


def extract_query(text: str) -> str | None:
    """严格解析“短剧 + ASCII 空白 + 剧名”，不接受命令前缀或全角空格。"""
    match = _COMMAND_RE.fullmatch((text or "").rstrip())
    if not match:
        return None
    return match.group("query")


def parse_episode_request(query: str) -> tuple[str, int | None]:
    """解析可选的“剧名 第N集”；普通剧名原样返回。"""
    match = _EPISODE_REQUEST_RE.fullmatch((query or "").strip())
    if not match:
        return (query or "").strip(), None
    return match.group("title").rstrip(), int(match.group("number"))


def select_episode(result: SearchResult, requested: int | None) -> Episode | None:
    """按单集编号或“第1-20集”分段范围选择播放线路。"""
    if not result.episodes:
        return None
    if requested is None:
        return result.episodes[0]

    for episode in result.episodes:
        match = _EPISODE_RANGE_RE.search(episode.name)
        if not match:
            continue
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        if start <= requested <= end:
            return episode

    if 1 <= requested <= len(result.episodes):
        return result.episodes[requested - 1]
    return None


def normalize_title(value: str) -> str:
    """生成仅用于匹配的标题，不改变最终展示标题。"""
    text = unicodedata.normalize("NFKC", value or "").strip().lower()
    text = _BRACKET_NOISE_RE.sub("", text)
    while True:
        cleaned = _NOISE_RE.sub("", text).strip()
        if cleaned == text:
            break
        text = cleaned
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def title_score(query: str, candidate: str) -> int:
    """标题越接近输入分数越高；短标题的泛匹配会受到明显惩罚。"""
    wanted = normalize_title(query)
    actual = normalize_title(candidate)
    if not wanted or not actual:
        return 0
    if wanted == actual:
        return 10_000
    if actual.startswith(wanted):
        return 7_000 - max(0, len(actual) - len(wanted)) * 20
    if wanted.startswith(actual):
        return 5_500 - max(0, len(wanted) - len(actual)) * 30
    if wanted in actual:
        return 4_000 - max(0, len(actual) - len(wanted)) * 25
    if actual in wanted:
        return 3_000 - max(0, len(wanted) - len(actual)) * 35

    # 容忍一个错字，例如“全球杀机”与“全球杀戮：……”。只比较等长窗口，
    # 避免长标题的公共字过多导致误判。
    if len(wanted) >= 3 and len(actual) >= len(wanted):
        window_size = len(wanted)
        best_ratio = max(
            SequenceMatcher(
                None,
                wanted,
                actual[index : index + window_size],
                autojunk=False,
            ).ratio()
            for index in range(len(actual) - window_size + 1)
        )
        if best_ratio >= 0.75:
            return int(3_000 + best_ratio * 4_000)

    overlap = len(set(wanted) & set(actual))
    ratio = overlap / max(len(set(wanted)), 1)
    return int(ratio * 1_000) if ratio >= 0.7 else 0


def known_title_hints(query: str) -> tuple[str, ...]:
    return _KNOWN_TITLE_HINTS.get(normalize_title(query), ())


def fuzzy_search_terms(query: str) -> tuple[str, ...]:
    """生成安全的二次检索词；短词不盲目拆成单字。"""
    hints = known_title_hints(query)
    if hints:
        return hints

    text = (query or "").strip()
    if len(text) < 4:
        return ()
    terms: list[str] = []
    for value in (text[:-1].strip(), text[1:].strip()):
        if len(value) >= 2 and value != text and value not in terms:
            terms.append(value)
    return tuple(terms)


def candidate_pool(
    candidates: Iterable[SearchResult],
    *,
    min_fuzzy_score: int = DEFAULT_MIN_FUZZY_SCORE,
) -> tuple[list[SearchResult], bool]:
    """精准候选存在时隔离模糊结果；否则启用达到阈值的模糊候选。"""
    available = list(candidates)
    exact = [item for item in available if item.score >= EXACT_TITLE_SCORE]
    if exact:
        return exact, True
    fuzzy = [item for item in available if item.score >= min_fuzzy_score]
    return fuzzy, False


def playlist_duration_seconds(content: str) -> float:
    """累计媒体播放清单中的 EXTINF 时长。"""
    return sum(
        float(value)
        for value in re.findall(r"(?m)^#EXTINF:([0-9]+(?:\.[0-9]+)?)", content or "")
    )


def master_playlist_variant(content: str) -> str | None:
    """读取 master playlist 的第一条媒体变体相对地址。"""
    waiting_for_uri = False
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            waiting_for_uri = True
            continue
        if waiting_for_uri and line and not line.startswith("#"):
            return line
    return None


def _decode_json(raw: bytes) -> dict[str, Any]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            data = json.loads(raw.decode(encoding))
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = exc
    if last_error:
        raise last_error
    return {}


def parse_episodes(play_url: Any) -> tuple[Episode, ...]:
    """从苹果 CMS 的 vod_play_url 中选择集数最多的一条 HLS 线路。"""
    if not isinstance(play_url, str) or not play_url.strip():
        return ()

    best: list[Episode] = []
    for route in play_url.split("$$$"):
        episodes: list[Episode] = []
        seen: set[str] = set()
        for index, item in enumerate(route.split("#"), start=1):
            item = item.strip()
            if not item:
                continue
            if "$" in item:
                name, url = item.split("$", 1)
            else:
                name, url = f"第{index}集", item
            url = url.strip()
            if not re.match(r"^https?://", url, flags=re.IGNORECASE):
                continue
            if ".m3u8" not in url.lower() or url in seen:
                continue
            seen.add(url)
            episodes.append(Episode(name=name.strip() or f"第{index}集", url=url))
        if len(episodes) > len(best):
            best = episodes
    return tuple(best)


def best_item(query: str, items: Iterable[Any], source_name: str) -> SearchResult | None:
    candidates: list[SearchResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("vod_name") or "").strip()
        score = title_score(query, title)
        if score <= 0:
            continue
        episodes = parse_episodes(item.get("vod_play_url"))
        if not episodes:
            continue
        candidates.append(
            SearchResult(
                title=title,
                source=source_name,
                episodes=episodes,
                cover_url=str(item.get("vod_pic") or "").strip(),
                year=str(item.get("vod_year") or "").strip(),
                actor=str(item.get("vod_actor") or "").strip(),
                score=score,
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda result: (result.score, len(result.episodes)))


class ShortDramaSearcher:
    def __init__(
        self,
        *,
        sources: tuple[Source, ...] = DEFAULT_SOURCES,
        timeout_seconds: float = 10,
        max_concurrent: int = 4,
        cache_ttl_seconds: int = 6 * 3600,
        full_version_min_minutes: float = 30,
        min_fuzzy_score: int = DEFAULT_MIN_FUZZY_SCORE,
    ) -> None:
        self.sources = sources
        self.timeout_seconds = max(2.0, float(timeout_seconds))
        self.max_concurrent = max(1, min(int(max_concurrent), 8))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.full_version_min_seconds = max(
            5 * 60,
            float(full_version_min_minutes) * 60,
        )
        self.min_fuzzy_score = max(1, min(int(min_fuzzy_score), 9_999))
        self._cache: dict[str, tuple[float, SearchResult | None]] = {}

    async def search(
        self,
        query: str,
        *,
        prefer_full: bool = True,
    ) -> SearchResult | None:
        cache_key = f"{normalize_title(query)}:{'full' if prefer_full else 'episodes'}"
        cached = self._cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        timeout = aiohttp.ClientTimeout(
            total=self.timeout_seconds,
            connect=min(5.0, self.timeout_seconds),
        )
        semaphore = asyncio.Semaphore(self.max_concurrent)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/137.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
        }
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers=headers,
            trust_env=False,
        ) as session:
            results = await asyncio.gather(
                *(
                    self._search_source(session, semaphore, source, query)
                    for source in self.sources
                )
            )
            available = [result for result in results if result is not None]
            pool, exact_match = candidate_pool(
                available,
                min_fuzzy_score=self.min_fuzzy_score,
            )
            if not exact_match:
                secondary_terms = fuzzy_search_terms(query)
                if secondary_terms:
                    secondary_results = await asyncio.gather(
                        *(
                            self._search_source(
                                session,
                                semaphore,
                                source,
                                term,
                                match_query=query,
                            )
                            for term in secondary_terms
                            for source in self.sources
                        )
                    )
                    combined = self._dedupe_results(
                        available
                        + [
                            result
                            for result in secondary_results
                            if result is not None
                        ]
                    )
                    pool, exact_match = candidate_pool(
                        combined,
                        min_fuzzy_score=self.min_fuzzy_score,
                    )
            best = await self._choose_best_result(
                session,
                semaphore,
                pool,
                prefer_full=prefer_full,
            )

        if best:
            logger.info(
                "[SHORT_DRAMA] selected title=%r source=%s match=%s episodes=%s "
                "duration=%.1fmin full=%s",
                best.title,
                best.source,
                "exact" if exact_match else "fuzzy",
                len(best.episodes),
                best.duration_seconds / 60,
                best.is_full_version,
            )
        now = time.monotonic()
        self._cache = {
            key: value
            for key, value in self._cache.items()
            if value[0] > now
        }
        if len(self._cache) >= 100:
            oldest_key = min(self._cache, key=lambda key: self._cache[key][0])
            self._cache.pop(oldest_key, None)
        self._cache[cache_key] = (
            now + self.cache_ttl_seconds,
            best,
        )
        return best

    @staticmethod
    def _dedupe_results(results: Iterable[SearchResult]) -> list[SearchResult]:
        unique: dict[tuple[str, str, str], SearchResult] = {}
        for result in results:
            key = (
                result.source,
                normalize_title(result.title),
                result.episodes[0].url,
            )
            current = unique.get(key)
            if current is None or result.score > current.score:
                unique[key] = result
        return list(unique.values())

    async def _choose_best_result(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
        pool: list[SearchResult],
        *,
        prefer_full: bool,
    ) -> SearchResult | None:
        if not pool:
            return None

        if not prefer_full:
            return max(
                pool,
                key=lambda result: (
                    result.score,
                    len(result.episodes),
                    result.duration_seconds,
                ),
            )

        single_streams = [result for result in pool if len(result.episodes) == 1]
        probed = await asyncio.gather(
            *(
                self._probe_result_duration(session, semaphore, result)
                for result in single_streams
            )
        )
        probed_by_key = {
            (result.source, result.episodes[0].url): result for result in probed
        }
        measured_pool = [
            probed_by_key.get(
                (result.source, result.episodes[0].url),
                result,
            )
            for result in pool
        ]
        full_versions = [
            result
            for result in measured_pool
            if len(result.episodes) == 1
            and result.duration_seconds >= self.full_version_min_seconds
        ]
        if full_versions:
            longest = max(
                full_versions,
                key=lambda result: (result.duration_seconds, result.score),
            )
            return replace(longest, is_full_version=True)

        # 没有满足时长阈值的完整版才回退分集；同分时优先集数较完整的源。
        return max(
            measured_pool,
            key=lambda result: (
                result.score,
                len(result.episodes),
                result.duration_seconds,
            ),
        )

    async def _probe_result_duration(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
        result: SearchResult,
    ) -> SearchResult:
        seconds = await self._hls_duration(
            session,
            semaphore,
            result.episodes[0].url,
        )
        return replace(result, duration_seconds=seconds)

    async def _hls_duration(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
        url: str,
        *,
        depth: int = 0,
    ) -> float:
        if depth > 2 or not self._is_public_http_url(url):
            return 0.0
        try:
            async with semaphore:
                async with session.get(url, allow_redirects=True) as response:
                    if response.status not in {200, 206}:
                        return 0.0
                    raw = await response.read()
                    if len(raw) > 5_000_000:
                        return 0.0
            content = raw.decode("utf-8-sig", errors="replace")
            seconds = playlist_duration_seconds(content)
            if seconds > 0:
                return seconds
            variant = master_playlist_variant(content)
            if not variant:
                return 0.0
            return await self._hls_duration(
                session,
                semaphore,
                urljoin(url, variant),
                depth=depth + 1,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return 0.0

    @staticmethod
    def _is_public_http_url(url: str) -> bool:
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return False
            if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
                return False
            try:
                address = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                return True
            return not (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_reserved
                or address.is_multicast
            )
        except ValueError:
            return False

    async def _search_source(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
        source: Source,
        query: str,
        *,
        match_query: str | None = None,
    ) -> SearchResult | None:
        try:
            async with semaphore:
                async with session.get(
                    source.url,
                    params={"ac": "detail", "wd": query},
                    allow_redirects=True,
                ) as response:
                    if response.status != 200:
                        logger.info(
                            "[SHORT_DRAMA] source=%s status=%s",
                            source.name,
                            response.status,
                        )
                        return None
                    raw = await response.read()
                    if len(raw) > 5_000_000:
                        logger.info(
                            "[SHORT_DRAMA] source=%s response too large: %s bytes",
                            source.name,
                            len(raw),
                        )
                        return None
            payload = _decode_json(raw)
            return best_item(
                match_query or query,
                payload.get("list") or (),
                source.name,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
            logger.info(
                "[SHORT_DRAMA] source=%s unavailable: %s: %s",
                source.name,
                type(exc).__name__,
                exc,
            )
            return None
