"""AstrBot 短剧搜索与微信播放卡片插件。"""

from __future__ import annotations

import logging
import re

from astrbot.api import AstrBotConfig, star
from astrbot.api.event import AstrMessageEvent, filter

from .search import (
    CollectionPage,
    Episode,
    SearchResult,
    ShortDramaSearcher,
    extract_query,
    extract_sports_query,
    is_recommendation_command,
    known_title_hints,
    parse_episode_request,
    parse_sports_collection_query,
    parse_variant_request,
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


def _compact_play_card_text(
    result: SearchResult,
    episode: Episode,
) -> tuple[str, str]:
    """生成适合微信小卡片的精简标题和单行摘要。"""
    title_suffix = "完整版" if result.is_full_version else episode.name
    title_limit = 48
    title_prefix_limit = max(1, title_limit - len(title_suffix) - 1)
    title = f"{result.title[:title_prefix_limit]}｜{title_suffix}"
    details = [value for value in (result.year, result.category) if value]
    if result.is_full_version:
        minutes = max(1, round(result.duration_seconds / 60))
        details.append(f"约{minutes}分钟")
    else:
        details.append(f"共{len(result.episodes)}集")
    details.append("点击播放")
    return title, " · ".join(details)


def _compact_variants_card_text(
    query_title: str,
    variants: tuple[SearchResult, ...],
) -> tuple[str, str]:
    """生成精简的多版本卡片标题和年份预览。"""
    title_suffix = f"{len(variants)}个版本"
    title_limit = 48
    title_prefix_limit = max(1, title_limit - len(title_suffix) - 1)
    title = f"{query_title[:title_prefix_limit]}｜{title_suffix}"
    years: list[str] = []
    for result in variants:
        year = result.year.strip()
        if year and year not in years:
            years.append(year)
    if years:
        years.sort(
            key=lambda value: (
                not value.isdigit(),
                int(value) if value.isdigit() else value,
            )
        )
        preview = "、".join(years[:4])
        if len(years) > 4:
            preview += "等"
        description = f"{preview} · 点击选择版本"
    else:
        description = f"共{len(variants)}个可播放版本 · 点击选择"
    return title, description


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
        self.player_server.collection_loader = self.searcher.search_collection_page
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
        if is_recommendation_command(text):
            event.stop_event()
            logger.info(
                "[SHORT_DRAMA] recommendation request sender=%s group=%s",
                event.get_sender_id(),
                event.get_group_id() or "private",
            )
            titles = await self.searcher.recommend_titles(20)
            if not titles:
                yield event.plain_result("当前资源站暂时没有可推荐的短剧")
                return
            yield event.plain_result("\n".join(titles))
            return

        sports_query = extract_sports_query(text)
        query = extract_query(text)
        if query is None and sports_query is None:
            return

        event.stop_event()
        if sports_query is not None:
            if len(sports_query) > 120:
                yield event.plain_result("赛事名称太长了，精简到 120 个字以内再搜")
                return
            sports_collection = parse_sports_collection_query(sports_query)
            if sports_collection is None:
                result = await self.searcher.search_sports_replay(sports_query)
                if result is None:
                    yield event.plain_result(
                        f"没找到精准赛事《{sports_query}》；"
                        "可发送“体育 足球”“体育 斯诺克”等分类进入回放页"
                    )
                    return
                episode = select_episode(result, None)
                if episode is None:
                    yield event.plain_result("这场赛事当前没有可播放线路")
                    return
                watch_url, player_can_select = self._watch_url(result, episode)
                if await self._send_sports_card(
                    event,
                    result,
                    episode,
                    watch_url,
                    player_can_select,
                ):
                    logger.info(
                        "[SHORT_DRAMA] sports card sent title=%r source=%s",
                        result.title,
                        result.source,
                    )
                    return
                yield event.plain_result(
                    f"《{result.title}》\n"
                    f"{self._result_summary(result, episode)}\n"
                    f"点击观看：{watch_url}"
                )
                return

            first_page = await self.searcher.search_collection_page(
                sports_query,
                1,
            )
            if not first_page.items:
                yield event.plain_result(
                    f"没找到《{sports_query}》的比赛回放，"
                    "可换个体育关键词或加上年份"
                )
                return
            collection_url = self.player_server.create_collection_url(
                sports_query,
                first_page,
            )
            if collection_url and await self._send_collection_card(
                event,
                sports_query,
                first_page,
                collection_url,
            ):
                logger.info(
                    "[SHORT_DRAMA] collection card sent query=%r first_page=%s",
                    sports_query,
                    len(first_page.items),
                )
                return
            lines = [item.title for item in first_page.items[:20]]
            lines.append("仅展示前20场；配置公网播放器后可分页浏览全部回放")
            yield event.plain_result("\n".join(lines))
            return

        assert query is not None

        variant_request = parse_variant_request(query)
        if variant_request is not None:
            title_query, variant_number = variant_request
            if len(title_query) > 60:
                yield event.plain_result("剧名太长了，精简到 60 个字以内再搜")
                return
            variants = await self.searcher.search_variants(title_query, 6)
            if not variants:
                yield event.plain_result(
                    f"没找到《{title_query}》的其他可播放版本"
                )
                return
            if variant_number is None:
                variants_url = self.player_server.create_variants_url(
                    title_query,
                    variants,
                )
                if variants_url and await self._send_variants_card(
                    event,
                    title_query,
                    variants,
                    variants_url,
                ):
                    return
                lines = [f"《{title_query}》可选版本："]
                lines.extend(
                    self._variant_line(index, result)
                    for index, result in enumerate(variants, start=1)
                )
                lines.append(
                    f"发送“短剧 {title_query} 版本2”播放对应版本"
                )
                yield event.plain_result("\n".join(lines))
                return
            if variant_number > len(variants):
                yield event.plain_result(
                    f"《{title_query}》目前只有 {len(variants)} 个可选版本，"
                    f"请发送“短剧 {title_query} 版本”重新查看"
                )
                return

            result = variants[variant_number - 1]
            episode = select_episode(result, None)
            if episode is None:
                yield event.plain_result("这个版本当前没有可播放线路")
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
                    "[SHORT_DRAMA] variant card sent title=%r variant=%s "
                    "source=%s episodes=%s",
                    result.title,
                    variant_number,
                    result.source,
                    len(result.episodes),
                )
                return
            yield event.plain_result(
                f"《{result.title}》\n"
                f"{self._result_summary(result, episode)}\n"
                f"点击观看 {episode.name}：{watch_url}"
            )
            return

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

        if requested_episode is None:
            variants = self.searcher.cached_variants(title_query, 6)
            variants_url = self.player_server.create_variants_url(
                title_query,
                variants,
            )
            if variants_url and await self._send_variants_card(
                event,
                title_query,
                variants,
                variants_url,
            ):
                logger.info(
                    "[SHORT_DRAMA] variants card sent title=%r variants=%s",
                    title_query,
                    len(variants),
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
        metadata = " · ".join(
            value
            for value in (
                result.source,
                f"{result.year}年" if result.year else "",
                result.category,
            )
            if value
        )
        if result.is_full_version:
            minutes = max(1, round(result.duration_seconds / 60))
            return f"{metadata} · 完整版 · 约{minutes}分钟"
        return (
            f"{metadata} · 共{len(result.episodes)}集 · 当前{episode.name}"
        )

    @staticmethod
    def _variant_line(index: int, result: SearchResult) -> str:
        parts = [
            f"《{result.title}》",
            f"{result.year}年" if result.year else "年份未知",
            result.category or "类型未知",
            f"{len(result.episodes)}集",
        ]
        actors = [
            value.strip()
            for value in re.split(r"[,，、/]+", result.actor)
            if value.strip()
        ][:2]
        if actors:
            parts.append("、".join(actors))
        parts.append(result.source)
        return f"{index}. " + "｜".join(parts)

    @staticmethod
    async def _send_collection_card(
        event: AstrMessageEvent,
        query_title: str,
        first_page: CollectionPage,
        collection_url: str,
    ) -> bool:
        cover = next(
            (item.cover_url for item in first_page.items if item.cover_url),
            _DEFAULT_COVER,
        )
        description = (
            f"已找到首批 {len(first_page.items)} 场回放"
            " · 点击按页浏览，向下滚动继续加载"
        )
        message = [
            {
                "type": "wechat_link_card",
                "data": {
                    "title": f"赛事回放｜{query_title}"[:48],
                    "desc": description[:80],
                    "url": collection_url,
                    "image_url": cover,
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
                "[SHORT_DRAMA] collection card send failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return False

    @staticmethod
    async def _send_sports_card(
        event: AstrMessageEvent,
        result: SearchResult,
        episode: Episode,
        watch_url: str,
        player_can_select: bool = False,
    ) -> bool:
        summary = Main._result_summary(result, episode)
        description = f"{summary} · 点击播放"
        if player_can_select and len(result.episodes) > 1:
            description += " · 播放器内可选线路"
        message = [
            {
                "type": "wechat_link_card",
                "data": {
                    "title": f"赛事回放｜{result.title}"[:48],
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
                "[SHORT_DRAMA] sports card send failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return False

    @staticmethod
    async def _send_variants_card(
        event: AstrMessageEvent,
        query_title: str,
        variants: tuple[SearchResult, ...],
        variants_url: str,
    ) -> bool:
        title, description = _compact_variants_card_text(
            query_title,
            variants,
        )
        cover = next(
            (result.cover_url for result in variants if result.cover_url),
            _DEFAULT_COVER,
        )
        message = [
            {
                "type": "wechat_link_card",
                "data": {
                    "title": title,
                    "desc": description[:80],
                    "url": variants_url,
                    "image_url": cover,
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
                "[SHORT_DRAMA] variants card send failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return False

    @staticmethod
    async def _send_card(
        event: AstrMessageEvent,
        result: SearchResult,
        episode: Episode,
        watch_url: str,
        player_can_select: bool = False,
    ) -> bool:
        title, description = _compact_play_card_text(result, episode)
        message = [
            {
                "type": "wechat_link_card",
                "data": {
                    "title": title,
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
