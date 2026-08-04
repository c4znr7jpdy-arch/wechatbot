"""构造不会把 m3u8 域名作为顶层页面打开的播放地址。"""

from __future__ import annotations

from urllib.parse import quote

from .search import Episode, SearchResult


DEFAULT_PLAYER_URL_TEMPLATE = "https://m3u8-player.cc/player?url={url}"


def build_watch_url(
    result: SearchResult,
    configured_template: object = "",
    episode: Episode | None = None,
) -> str:
    """将目标集地址包进 HLS 播放页；配置为 ``direct`` 时才返回直链。"""
    target = episode or result.episodes[0]
    configured = str(configured_template or "").strip()
    if configured.lower() == "direct":
        return target.url

    template = configured or DEFAULT_PLAYER_URL_TEMPLATE
    try:
        return template.format(
            url=quote(target.url, safe=""),
            title=quote(result.title, safe=""),
            episode=quote(target.name, safe=""),
        )
    except (KeyError, ValueError):
        return DEFAULT_PLAYER_URL_TEMPLATE.format(
            url=quote(target.url, safe="")
        )
