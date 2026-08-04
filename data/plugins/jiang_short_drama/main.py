"""AstrBot 短剧搜索与微信播放卡片插件。"""

from __future__ import annotations

import logging

from astrbot.api import AstrBotConfig, star
from astrbot.api.event import AstrMessageEvent, filter

from .search import (
    Episode,
    SearchResult,
    ShortDramaSearcher,
    extract_query,
    known_title_hints,
    parse_episode_request,
    select_episode,
)
from .watch_url import build_watch_url


logger = logging.getLogger("jiang_short_drama")
_DEFAULT_COVER = "https://duanjubaike.cn/favicon.ico"


def _strip_system_identity_prefix(text: str) -> str:
    value = text or ""
    if value.startswith("[系统身份提示：") and "]\n" in value:
        return value.split("]\n", 1)[1]
    return value


def _safe_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(float(value), maximum))
    except (TypeError, ValueError):
        return default


class Main(star.Star):
    def __init__(
        self,
        context: star.Context,
        config: AstrBotConfig | None = None,
    ) -> None:
        super().__init__(context)
        self.context = context
        self.config = config or {}
        self.searcher = ShortDramaSearcher(
            timeout_seconds=_safe_float(
                self.config.get("request_timeout_seconds"), 10, 2, 30
            ),
            max_concurrent=_safe_int(
                self.config.get("max_concurrent_sources"), 4, 1, 8
            ),
            cache_ttl_seconds=_safe_int(
                self.config.get("cache_ttl_minutes"), 360, 0, 1440
            )
            * 60,
            full_version_min_minutes=_safe_float(
                self.config.get("full_version_min_minutes"), 30, 5, 300
            ),
            min_fuzzy_score=_safe_int(
                self.config.get("min_fuzzy_score"), 3000, 1, 9999
            ),
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def short_drama(self, event: AstrMessageEvent):
        """严格匹配“短剧 剧名”，搜索资源并发送微信链接卡片。"""
        text = _strip_system_identity_prefix(event.get_message_str())
        query = extract_query(text)
        if query is None:
            return

        event.stop_event()
        title_query, requested_episode = parse_episode_request(query)
        if len(title_query) > 60:
            yield event.plain_result("剧名太长了，精简到 60 个字以内再搜")
            return

        logger.info(
            "[SHORT_DRAMA] search query=%r sender=%s group=%s",
            title_query,
            event.get_sender_id(),
            event.get_group_id() or "private",
        )
        result = await self.searcher.search(
            title_query,
            prefer_full=requested_episode is None,
        )
        if result is None:
            hints = known_title_hints(title_query)
            if hints:
                names = "、".join(f"《{name}》" for name in hints)
                yield event.plain_result(
                    f"已按可能剧名 {names} 做了模糊检索，"
                    "但当前资源站暂无可播放资源"
                )
            else:
                yield event.plain_result(
                    f"没搜到《{title_query}》，换个完整剧名再试试"
                )
            return

        episode = select_episode(result, requested_episode)
        if episode is None:
            yield event.plain_result(
                f"《{result.title}》当前找到 {len(result.episodes)} 个分集线路，"
                f"没有第 {requested_episode} 集"
            )
            return

        watch_url = self._watch_url(result, episode)
        sent = await self._send_card(event, result, episode, watch_url)
        if sent:
            logger.info(
                "[SHORT_DRAMA] card sent title=%r source=%s episodes=%s",
                result.title,
                result.source,
                len(result.episodes),
            )
            return

        # 仅在卡片 API 不可用时降级，正常情况不额外发送重复链接。
        yield event.plain_result(
            f"《{result.title}》\n"
            f"{self._result_summary(result, episode)}\n"
            f"点击观看 {episode.name}：{watch_url}"
        )

    def _watch_url(self, result: SearchResult, episode: Episode) -> str:
        configured = self.config.get("player_url_template")
        watch_url = build_watch_url(result, configured, episode)
        if configured and str(configured).strip() not in {"direct", "DIRECT"}:
            try:
                str(configured).format(url="", title="", episode="")
            except (KeyError, ValueError):
                logger.warning(
                    "[SHORT_DRAMA] player_url_template 格式无效，使用默认 HLS 播放页"
                )
        return watch_url

    @staticmethod
    def _result_summary(result: SearchResult, episode: Episode) -> str:
        if result.is_full_version:
            minutes = max(1, round(result.duration_seconds / 60))
            return f"{result.source} · 完整版 · 约{minutes}分钟"
        return (
            f"{result.source} · 共{len(result.episodes)}集 · 当前{episode.name}"
        )

    @staticmethod
    async def _send_card(
        event: AstrMessageEvent,
        result: SearchResult,
        episode: Episode,
        watch_url: str,
    ) -> bool:
        summary = Main._result_summary(result, episode)
        title_suffix = "完整版" if result.is_full_version else episode.name
        description = (
            f"{summary} · 点击播放"
            if result.is_full_version
            else f"{summary} · 可发送“短剧 {result.title} 第N集”选集"
        )
        message = [
            {
                "type": "wechat_link_card",
                "data": {
                    "title": f"短剧｜{result.title}｜{title_suffix}"[:48],
                    "desc": description[:80],
                    "url": watch_url,
                    "image_url": result.cover_url or _DEFAULT_COVER,
                },
            }
        ]
        payload = {"message": message}
        try:
            if event.get_group_id():
                payload["group_id"] = event.get_group_id()
                response = await event.bot.call_action("send_group_msg", **payload)
            else:
                payload["user_id"] = event.get_sender_id()
                response = await event.bot.call_action("send_private_msg", **payload)
            if isinstance(response, dict):
                return response.get("retcode", 0) == 0 and response.get(
                    "status", "ok"
                ) in {"ok", "success"}
            return True
        except Exception as exc:
            logger.warning(
                "[SHORT_DRAMA] link card send failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return False
