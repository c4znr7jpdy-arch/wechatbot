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
from .player_server import ShortDramaPlayerServer
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
        self.player_server = ShortDramaPlayerServer(
            bind_host=str(
                self.config.get("episode_player_bind_host") or "127.0.0.1"
            ),
            port=_safe_int(
                self.config.get("episode_player_port"), 6197, 1, 65_535
            ),
            public_base_url=self.config.get("episode_player_public_base_url"),
            token_ttl_seconds=_safe_int(
                self.config.get("episode_player_token_ttl_minutes"),
                360,
                1,
                1440,
            )
            * 60,
            max_playlists=_safe_int(
                self.config.get("episode_player_max_playlists"),
                200,
                10,
                2000,
            ),
        )
        configured_base = self.config.get("episode_player_public_base_url")
        if configured_base and not self.player_server.configured:
            logger.warning(
                "[SHORT_DRAMA] episode_player_public_base_url 格式无效，"
                "分集卡片将使用单集播放器"
            )

    async def initialize(self) -> None:
        await self.player_server.start()

    async def terminate(self) -> None:
        await self.player_server.stop()

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

        watch_url, player_can_select = self._watch_url(result, episode)
        sent = await self._send_card(
            event,
            result,
            episode,
            watch_url,
            player_can_select,
        )
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

    def _watch_url(
        self,
        result: SearchResult,
        episode: Episode,
    ) -> tuple[str, bool]:
        playlist_url = self.player_server.create_watch_url(result, episode)
        if playlist_url:
            return playlist_url, True

        configured = self.config.get("player_url_template")
        watch_url = build_watch_url(result, configured, episode)
        if configured and str(configured).strip() not in {"direct", "DIRECT"}:
            try:
                str(configured).format(url="", title="", episode="")
            except (KeyError, ValueError):
                logger.warning(
                    "[SHORT_DRAMA] player_url_template 格式无效，使用默认 HLS 播放页"
                )
        return watch_url, False

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
        player_can_select: bool = False,
    ) -> bool:
        summary = Main._result_summary(result, episode)
        title_suffix = "完整版" if result.is_full_version else episode.name
        description = (
            f"{summary} · 点击播放"
            if result.is_full_version
            else (
                f"{summary} · 播放器内可选集、上一集、下一集"
                if player_can_select
                else f"{summary} · 可发送“短剧 {result.title} 第N集”选集"
            )
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
