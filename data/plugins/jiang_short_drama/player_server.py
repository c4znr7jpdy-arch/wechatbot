"""Token-based multi-episode HLS player served by the AstrBot plugin."""

from __future__ import annotations

import logging
import re
import secrets
import time
from dataclasses import dataclass
from html import escape
from typing import Awaitable, Callable
from urllib.parse import quote, urlencode, urlsplit

from aiohttp import web

from .search import CollectionPage, Episode, SearchResult, normalize_title


logger = logging.getLogger("jiang_short_drama")
_ROUTE_PREFIX = "/short-drama"


@dataclass(frozen=True, slots=True)
class PlaylistRecord:
    title: str
    source: str
    cover_url: str
    episodes: tuple[Episode, ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class VariantRecord:
    query_title: str
    variants: tuple[SearchResult, ...]
    expires_at: float


@dataclass(slots=True)
class CollectionRecord:
    query_title: str
    pages: dict[int, CollectionPage]
    expires_at: float


class EpisodePlaylistStore:
    """Keep short-lived playlists behind unguessable tokens."""

    def __init__(self, ttl_seconds: int = 21_600, max_entries: int = 200) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_entries = max(10, int(max_entries))
        self._items: dict[str, PlaylistRecord] = {}

    def put(self, result: SearchResult, now: float | None = None) -> str:
        current = time.monotonic() if now is None else now
        self._purge(current)
        while len(self._items) >= self.max_entries:
            oldest = min(self._items, key=lambda key: self._items[key].expires_at)
            self._items.pop(oldest, None)

        token = secrets.token_urlsafe(12)
        self._items[token] = PlaylistRecord(
            title=result.title,
            source=result.source,
            cover_url=result.cover_url,
            episodes=result.episodes,
            expires_at=current + self.ttl_seconds,
        )
        return token

    def get(self, token: str, now: float | None = None) -> PlaylistRecord | None:
        current = time.monotonic() if now is None else now
        record = self._items.get(token)
        if record is None:
            return None
        if record.expires_at <= current:
            self._items.pop(token, None)
            return None
        return record

    def _purge(self, now: float) -> None:
        expired = [key for key, value in self._items.items() if value.expires_at <= now]
        for key in expired:
            self._items.pop(key, None)


class VariantStore:
    """Keep short-lived version choices behind unguessable tokens."""

    def __init__(self, ttl_seconds: int = 21_600, max_entries: int = 200) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_entries = max(10, int(max_entries))
        self._items: dict[str, VariantRecord] = {}

    def put(
        self,
        query_title: str,
        variants: tuple[SearchResult, ...],
        now: float | None = None,
    ) -> str:
        current = time.monotonic() if now is None else now
        self._purge(current)
        while len(self._items) >= self.max_entries:
            oldest = min(self._items, key=lambda key: self._items[key].expires_at)
            self._items.pop(oldest, None)
        token = secrets.token_urlsafe(12)
        self._items[token] = VariantRecord(
            query_title=query_title,
            variants=variants,
            expires_at=current + self.ttl_seconds,
        )
        return token

    def get(self, token: str, now: float | None = None) -> VariantRecord | None:
        current = time.monotonic() if now is None else now
        record = self._items.get(token)
        if record is None:
            return None
        if record.expires_at <= current:
            self._items.pop(token, None)
            return None
        return record

    def _purge(self, now: float) -> None:
        expired = [key for key, value in self._items.items() if value.expires_at <= now]
        for key in expired:
            self._items.pop(key, None)


class CollectionStore:
    """Keep lazy-loaded replay collections behind unguessable tokens."""

    def __init__(self, ttl_seconds: int = 21_600, max_entries: int = 200) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_entries = max(10, int(max_entries))
        self._items: dict[str, CollectionRecord] = {}

    def put(
        self,
        query_title: str,
        first_page: CollectionPage,
        now: float | None = None,
    ) -> str:
        current = time.monotonic() if now is None else now
        self._purge(current)
        while len(self._items) >= self.max_entries:
            oldest = min(self._items, key=lambda key: self._items[key].expires_at)
            self._items.pop(oldest, None)
        token = secrets.token_urlsafe(12)
        self._items[token] = CollectionRecord(
            query_title=query_title,
            pages={first_page.page: first_page},
            expires_at=current + self.ttl_seconds,
        )
        return token

    def get(self, token: str, now: float | None = None) -> CollectionRecord | None:
        current = time.monotonic() if now is None else now
        record = self._items.get(token)
        if record is None:
            return None
        if record.expires_at <= current:
            self._items.pop(token, None)
            return None
        return record

    def _purge(self, now: float) -> None:
        expired = [key for key, value in self._items.items() if value.expires_at <= now]
        for key in expired:
            self._items.pop(key, None)


def validate_public_base_url(value: object) -> str:
    """Return a usable public origin/base path or an empty string."""
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.query or parsed.fragment:
        return ""
    return text


class ShortDramaPlayerServer:
    def __init__(
        self,
        *,
        bind_host: str = "127.0.0.1",
        port: int = 6197,
        public_base_url: object = "",
        token_ttl_seconds: int = 21_600,
        max_playlists: int = 200,
    ) -> None:
        self.bind_host = str(bind_host or "127.0.0.1").strip()
        self.port = max(1, min(int(port), 65_535))
        self.public_base_url = validate_public_base_url(public_base_url)
        self.store = EpisodePlaylistStore(token_ttl_seconds, max_playlists)
        self.variant_store = VariantStore(token_ttl_seconds, max_playlists)
        self.collection_store = CollectionStore(token_ttl_seconds, max_playlists)
        self.collection_loader: (
            Callable[[str, int], Awaitable[CollectionPage]] | None
        ) = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self.started = False

    @property
    def configured(self) -> bool:
        return bool(self.public_base_url)

    async def start(self) -> None:
        if self.started or not self.configured:
            return
        app = web.Application()
        app.router.add_get(f"{_ROUTE_PREFIX}/health", self._handle_health)
        app.router.add_get(f"{_ROUTE_PREFIX}/watch/{{token}}", self._handle_watch)
        app.router.add_get(f"{_ROUTE_PREFIX}/api/{{token}}", self._handle_api)
        app.router.add_get(
            f"{_ROUTE_PREFIX}/variants/{{token}}",
            self._handle_variants,
        )
        app.router.add_get(
            f"{_ROUTE_PREFIX}/choose/{{token}}/{{index}}",
            self._handle_choose,
        )
        app.router.add_get(
            f"{_ROUTE_PREFIX}/recommendations/{{token}}",
            self._handle_recommendations,
        )
        app.router.add_get(
            f"{_ROUTE_PREFIX}/collection/{{token}}",
            self._handle_collection,
        )
        app.router.add_get(
            f"{_ROUTE_PREFIX}/collection-api/{{token}}",
            self._handle_collection_api,
        )
        app.router.add_get(
            f"{_ROUTE_PREFIX}/collection-choose/{{token}}/{{page}}/{{index}}",
            self._handle_collection_choose,
        )
        app.router.add_get(
            f"{_ROUTE_PREFIX}/collection.js",
            self._handle_collection_javascript,
        )
        app.router.add_get(f"{_ROUTE_PREFIX}/player.js", self._handle_javascript)

        self._runner = web.AppRunner(app, access_log=None)
        try:
            await self._runner.setup()
            self._site = web.TCPSite(
                self._runner,
                host=self.bind_host,
                port=self.port,
            )
            await self._site.start()
        except Exception as exc:
            logger.error(
                "[SHORT_DRAMA] episode player failed to listen on %s:%s: %s: %s",
                self.bind_host,
                self.port,
                type(exc).__name__,
                exc,
            )
            await self.stop()
            return

        self.started = True
        logger.info(
            "[SHORT_DRAMA] episode player listening on %s:%s, public base=%s",
            self.bind_host,
            self.port,
            self.public_base_url,
        )

    async def stop(self) -> None:
        self.started = False
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def create_watch_url(
        self,
        result: SearchResult,
        selected: Episode,
    ) -> str | None:
        if not self.started or not self.public_base_url or len(result.episodes) <= 1:
            return None
        try:
            selected_index = result.episodes.index(selected)
        except ValueError:
            selected_index = 0
        token = self.store.put(result)
        query = urlencode({"ep": selected_index + 1})
        return (
            f"{self.public_base_url}{_ROUTE_PREFIX}/watch/{token}?{query}"
        )

    def create_variants_url(
        self,
        query_title: str,
        variants: tuple[SearchResult, ...],
    ) -> str | None:
        if (
            not self.started
            or not self.public_base_url
            or len(variants) < 2
        ):
            return None
        token = self.variant_store.put(query_title, variants)
        return f"{self.public_base_url}{_ROUTE_PREFIX}/variants/{token}"

    def create_collection_url(
        self,
        query_title: str,
        first_page: CollectionPage,
    ) -> str | None:
        if (
            not self.started
            or not self.public_base_url
            or not first_page.items
        ):
            return None
        token = self.collection_store.put(query_title, first_page)
        return f"{self.public_base_url}{_ROUTE_PREFIX}/collection/{token}"

    def create_recommendations_url(
        self,
        recommendations: tuple[SearchResult, ...],
    ) -> str | None:
        if not self.started or not self.public_base_url or not recommendations:
            return None
        token = self.variant_store.put("最新短剧推荐", recommendations)
        return f"{self.public_base_url}{_ROUTE_PREFIX}/recommendations/{token}"

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"ok": True, "plugin": "jiang_short_drama", "player": True},
            headers={"Cache-Control": "no-store"},
        )

    async def _handle_watch(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        record = self.store.get(token)
        if record is None:
            raise web.HTTPNotFound(text="播放链接已失效，请回到微信重新搜索")
        html = _player_html(record.title)
        return web.Response(
            text=html,
            content_type="text/html",
            charset="utf-8",
            headers=_page_headers(),
        )

    async def _handle_api(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        record = self.store.get(token)
        if record is None:
            return web.json_response(
                {"ok": False, "error": "播放链接已失效，请回到微信重新搜索"},
                status=404,
                headers={"Cache-Control": "no-store"},
            )
        return web.json_response(
            {
                "ok": True,
                "title": record.title,
                "source": record.source,
                "cover": record.cover_url,
                "episodes": [
                    {"name": episode.name, "url": episode.url}
                    for episode in record.episodes
                ],
            },
            headers={"Cache-Control": "private, no-store"},
        )

    async def _handle_variants(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        record = self.variant_store.get(token)
        if record is None:
            raise web.HTTPNotFound(text="版本选择链接已失效，请回到微信重新搜索")
        return web.Response(
            text=_variant_html(record, token),
            content_type="text/html",
            charset="utf-8",
            headers=_page_headers(),
        )

    async def _handle_choose(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        record = self.variant_store.get(token)
        if record is None:
            raise web.HTTPNotFound(text="版本选择链接已失效，请回到微信重新搜索")
        try:
            index = int(request.match_info["index"]) - 1
        except ValueError:
            raise web.HTTPNotFound(text="无效的版本编号")
        if index < 0 or index >= len(record.variants):
            raise web.HTTPNotFound(text="无效的版本编号")
        result = record.variants[index]
        playlist_token = self.store.put(result)
        destination = (
            f"{self.public_base_url}{_ROUTE_PREFIX}/watch/{playlist_token}?ep=1"
        )
        raise web.HTTPFound(location=destination)

    async def _handle_recommendations(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        record = self.variant_store.get(token)
        if record is None:
            raise web.HTTPNotFound(text="推荐链接已失效，请回到微信重新获取")
        return web.Response(
            text=_recommendation_html(record, token),
            content_type="text/html",
            charset="utf-8",
            headers=_page_headers(),
        )

    async def _load_collection_page(
        self,
        record: CollectionRecord,
        page: int,
    ) -> CollectionPage | None:
        cached = record.pages.get(page)
        if cached is not None:
            return cached
        if self.collection_loader is None:
            return None
        loaded = await self.collection_loader(record.query_title, page)
        seen = {
            normalize_title(item.title)
            for existing_page, existing in record.pages.items()
            if existing_page != page
            for item in existing.items
        }
        if seen:
            loaded = CollectionPage(
                query=loaded.query,
                page=loaded.page,
                items=tuple(
                    item
                    for item in loaded.items
                    if normalize_title(item.title) not in seen
                ),
                has_more=loaded.has_more,
            )
        record.pages[page] = loaded
        return loaded

    async def _handle_collection(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        record = self.collection_store.get(token)
        if record is None:
            raise web.HTTPNotFound(text="赛事回放链接已失效，请回到微信重新搜索")
        return web.Response(
            text=_collection_html(record.query_title),
            content_type="text/html",
            charset="utf-8",
            headers=_page_headers(),
        )

    async def _handle_collection_api(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        record = self.collection_store.get(token)
        if record is None:
            return web.json_response(
                {"ok": False, "error": "赛事回放链接已失效，请重新搜索"},
                status=404,
                headers={"Cache-Control": "no-store"},
            )
        try:
            page = max(1, int(request.query.get("page", "1")))
        except ValueError:
            page = 1
        collection_page = await self._load_collection_page(record, page)
        if collection_page is None:
            return web.json_response(
                {"ok": False, "error": "暂时无法加载更多回放"},
                status=503,
                headers={"Cache-Control": "no-store"},
            )
        return web.json_response(
            {
                "ok": True,
                "query": record.query_title,
                "page": page,
                "has_more": collection_page.has_more,
                "items": [
                    {
                        "title": item.title,
                        "source": item.source,
                        "cover": item.cover_url,
                        "category": item.category,
                        "remarks": item.remarks,
                        "episodes": len(item.episodes),
                        "choose": (
                            f"../collection-choose/{quote(token, safe='')}/"
                            f"{page}/{index}"
                        ),
                    }
                    for index, item in enumerate(collection_page.items, start=1)
                ],
            },
            headers={"Cache-Control": "private, no-store"},
        )

    async def _handle_collection_choose(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        record = self.collection_store.get(token)
        if record is None:
            raise web.HTTPNotFound(text="赛事回放链接已失效，请回到微信重新搜索")
        try:
            page = max(1, int(request.match_info["page"]))
            index = int(request.match_info["index"]) - 1
        except ValueError:
            raise web.HTTPNotFound(text="无效的回放编号")
        collection_page = await self._load_collection_page(record, page)
        if (
            collection_page is None
            or index < 0
            or index >= len(collection_page.items)
        ):
            raise web.HTTPNotFound(text="无效的回放编号")
        result = collection_page.items[index]
        playlist_token = self.store.put(result)
        destination = (
            f"{self.public_base_url}{_ROUTE_PREFIX}/watch/{playlist_token}?ep=1"
        )
        raise web.HTTPFound(location=destination)

    async def _handle_collection_javascript(
        self,
        request: web.Request,
    ) -> web.Response:
        return web.Response(
            text=_COLLECTION_JAVASCRIPT,
            content_type="application/javascript",
            charset="utf-8",
            headers={"Cache-Control": "public, max-age=300"},
        )

    async def _handle_javascript(self, request: web.Request) -> web.Response:
        return web.Response(
            text=_PLAYER_JAVASCRIPT,
            content_type="application/javascript",
            charset="utf-8",
            headers={"Cache-Control": "public, max-age=300"},
        )


def _page_headers() -> dict[str, str]:
    return {
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: http: https:; "
            "media-src 'self' blob: http: https:; "
            "connect-src 'self' blob: http: https:"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
    }


def _variant_html(record: VariantRecord, token: str) -> str:
    safe_query = escape(record.query_title or "短剧")
    safe_token = quote(token, safe="")
    cards: list[str] = []
    for index, result in enumerate(record.variants, start=1):
        title = escape(result.title or record.query_title)
        category = escape(result.category or "类型未知")
        year = escape(f"{result.year}年" if result.year else "年份未知")
        source = escape(result.source or "未知来源")
        actors = [
            value.strip()
            for value in re.split(r"[,，、/]+", result.actor)
            if value.strip()
        ][:2]
        actor_text = escape("、".join(actors)) if actors else "演员信息暂无"
        cover = str(result.cover_url or "").strip()
        parsed_cover = urlsplit(cover)
        cover_html = ""
        if parsed_cover.scheme in {"http", "https"} and parsed_cover.netloc:
            cover_html = (
                f'<img src="{escape(cover, quote=True)}" alt="{title}封面" '
                'loading="lazy" referrerpolicy="no-referrer">'
            )
        cards.append(
            f"""
      <a class="version-card" href="../choose/{safe_token}/{index}">
        <div class="cover"><span>{escape((result.title or "剧")[0])}</span>{cover_html}</div>
        <div class="info">
          <div class="title"><span class="number">{index}</span><strong>{title}</strong></div>
          <div class="tags"><span>{year}</span><span>{category}</span><span>{len(result.episodes)}集</span></div>
          <div class="actor">{actor_text}</div>
          <div class="source">{source}</div>
          <div class="play">播放此版本 <span>›</span></div>
        </div>
      </a>"""
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>选择《{safe_query}》的版本</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui,-apple-system,"Microsoft YaHei",sans-serif; --accent:#20c5a5; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-height:100vh; background:#0b0c0f; color:#f4f6f8; }}
    main {{ width:min(100%,920px); margin:0 auto; padding:20px 14px max(28px,env(safe-area-inset-bottom)); }}
    header {{ margin:0 2px 18px; }}
    h1 {{ margin:0 0 7px; font-size:clamp(22px,5.8vw,32px); line-height:1.3; }}
    header p {{ margin:0; color:#9298a2; font-size:14px; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }}
    .version-card {{ display:grid; grid-template-columns:minmax(92px,38%) 1fr; min-height:164px; overflow:hidden; border:1px solid #292d34; border-radius:15px; color:inherit; background:#17191e; box-shadow:0 8px 24px rgba(0,0,0,.2); text-decoration:none; }}
    .cover {{ position:relative; min-height:164px; overflow:hidden; display:flex; align-items:center; justify-content:center; color:#68717d; background:linear-gradient(145deg,#232a33,#111419); font-size:42px; font-weight:750; }}
    .cover img {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
    .info {{ display:flex; min-width:0; flex-direction:column; padding:12px 12px 10px; }}
    .title {{ display:flex; align-items:flex-start; gap:7px; min-width:0; }}
    .title strong {{ display:-webkit-box; overflow:hidden; font-size:16px; line-height:1.38; -webkit-box-orient:vertical; -webkit-line-clamp:2; }}
    .number {{ flex:0 0 auto; min-width:22px; height:22px; padding:0 5px; border-radius:7px; color:#061813; background:var(--accent); font-size:13px; font-weight:750; line-height:22px; text-align:center; }}
    .tags {{ display:flex; flex-wrap:wrap; gap:5px; margin-top:9px; }}
    .tags span {{ padding:3px 6px; border:1px solid #343943; border-radius:6px; color:#c8cdd4; background:#202329; font-size:11px; }}
    .actor,.source {{ overflow:hidden; margin-top:7px; color:#9399a3; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }}
    .source {{ margin-top:3px; color:#6f7681; }}
    .play {{ display:flex; align-items:center; justify-content:space-between; margin-top:auto; padding-top:8px; color:var(--accent); font-size:13px; font-weight:650; }}
    .play span {{ font-size:23px; line-height:14px; }}
    .tip {{ margin:16px 2px 0; color:#717883; font-size:12px; text-align:center; }}
    @media (max-width:620px) {{
      main {{ padding:16px 10px max(22px,env(safe-area-inset-bottom)); }}
      header {{ margin-bottom:14px; }}
      .grid {{ grid-template-columns:1fr; gap:10px; }}
      .version-card {{ grid-template-columns:118px 1fr; min-height:168px; border-radius:13px; }}
      .cover {{ min-height:168px; }}
    }}
    @media (hover:hover) {{ .version-card:hover {{ border-color:rgba(32,197,165,.7); transform:translateY(-1px); }} }}
  </style>
</head>
<body>
  <main>
    <header><h1>选择《{safe_query}》的版本</h1><p>共 {len(record.variants)} 个可播放版本，点击封面或卡片即可观看</p></header>
    <section class="grid">{''.join(cards)}</section>
    <p class="tip">版本信息来自资源站；如封面或年份有误，请根据演员和集数判断。</p>
  </main>
</body>
</html>"""


def _recommendation_html(record: VariantRecord, token: str) -> str:
    safe_token = quote(token, safe="")
    cards: list[str] = []
    for index, result in enumerate(record.variants, start=1):
        title = escape(result.title or "短剧")
        category = escape(result.category or "短剧")
        year = escape(result.year) if result.year else ""
        source = escape(result.source or "未知来源")
        cover = str(result.cover_url or "").strip()
        parsed_cover = urlsplit(cover)
        cover_html = ""
        if parsed_cover.scheme in {"http", "https"} and parsed_cover.netloc:
            cover_html = (
                f'<img src="{escape(cover, quote=True)}" alt="{title}封面" '
                'loading="lazy" referrerpolicy="no-referrer">'
            )
        tags = "".join(
            f"<span>{value}</span>"
            for value in (
                year,
                category,
                f"{len(result.episodes)}集" if len(result.episodes) > 1 else "完整版",
            )
            if value
        )
        cards.append(
            f"""
      <a class="recommend-card" href="../choose/{safe_token}/{index}">
        <div class="cover"><span>{escape((result.title or "剧")[0])}</span>{cover_html}<b>{index}</b></div>
        <div class="info">
          <strong>{title}</strong>
          <div class="tags">{tags}</div>
          <div class="source">{source}</div>
          <div class="play">点击播放 <span>›</span></div>
        </div>
      </a>"""
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>最新短剧推荐</title>
  <style>
    :root {{ color-scheme:dark; font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif; --accent:#20c5a5; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-height:100vh; background:#0b0c0f; color:#f4f6f8; }}
    main {{ width:min(100%,920px); margin:0 auto; padding:18px 12px max(28px,env(safe-area-inset-bottom)); }}
    header {{ margin:0 2px 16px; }}
    h1 {{ margin:0 0 6px; font-size:clamp(24px,6vw,34px); line-height:1.25; }}
    header p {{ margin:0; color:#9298a2; font-size:14px; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }}
    .recommend-card {{ display:grid; grid-template-columns:112px minmax(0,1fr); min-height:158px; overflow:hidden; border:1px solid #292d34; border-radius:15px; color:inherit; background:#17191e; text-decoration:none; box-shadow:0 8px 24px rgba(0,0,0,.18); }}
    .cover {{ position:relative; min-height:158px; overflow:hidden; display:flex; align-items:center; justify-content:center; color:#68717d; background:linear-gradient(145deg,#232a33,#111419); font-size:40px; font-weight:750; }}
    .cover img {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
    .cover b {{ position:absolute; top:8px; left:8px; min-width:27px; height:27px; padding:0 7px; border-radius:9px; color:#061813; background:var(--accent); font-size:13px; line-height:27px; text-align:center; box-shadow:0 3px 10px rgba(0,0,0,.25); }}
    .info {{ display:flex; min-width:0; flex-direction:column; padding:13px 12px 11px; }}
    .info strong {{ display:-webkit-box; overflow:hidden; font-size:17px; line-height:1.4; -webkit-box-orient:vertical; -webkit-line-clamp:2; }}
    .tags {{ display:flex; flex-wrap:wrap; gap:5px; margin-top:10px; }}
    .tags span {{ padding:3px 7px; border:1px solid #343943; border-radius:7px; color:#c8cdd4; background:#202329; font-size:11px; }}
    .source {{ overflow:hidden; margin-top:8px; color:#777f8a; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }}
    .play {{ display:flex; align-items:center; justify-content:space-between; margin-top:auto; padding-top:9px; color:var(--accent); font-size:13px; font-weight:700; }}
    .play span {{ font-size:23px; line-height:14px; }}
    .tip {{ margin:16px 2px 0; color:#717883; font-size:12px; text-align:center; }}
    @media (max-width:680px) {{
      main {{ padding:16px 10px max(24px,env(safe-area-inset-bottom)); }}
      .grid {{ grid-template-columns:1fr; gap:10px; }}
      .recommend-card {{ grid-template-columns:108px minmax(0,1fr); min-height:154px; border-radius:13px; }}
      .cover {{ min-height:154px; }}
    }}
    @media (hover:hover) {{ .recommend-card:hover {{ border-color:rgba(32,197,165,.7); transform:translateY(-1px); }} }}
  </style>
</head>
<body>
  <main>
    <header><h1>最新短剧推荐</h1><p>共 {len(record.variants)} 部，按资源站更新时间排序</p></header>
    <section class="grid">{''.join(cards)}</section>
    <p class="tip">点击任一短剧即可进入播放器；推荐结果会随资源站更新。</p>
  </main>
</body>
</html>"""


def _collection_html(query_title: str) -> str:
    safe_query = escape(query_title or "赛事回放")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>{safe_query} 比赛回放</title>
  <style>
    :root {{ color-scheme:dark; font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif; --accent:#20c5a5; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-height:100vh; background:#0b0c0f; color:#f4f6f8; }}
    main {{ width:min(100%,900px); margin:0 auto; padding:18px 12px max(28px,env(safe-area-inset-bottom)); }}
    header {{ position:sticky; z-index:4; top:0; margin:-18px -12px 12px; padding:16px 14px 12px; border-bottom:1px solid rgba(255,255,255,.07); background:rgba(11,12,15,.94); backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px); }}
    h1 {{ margin:0 0 5px; font-size:clamp(21px,5.6vw,30px); }}
    header p {{ margin:0; color:#9298a2; font-size:13px; }}
    #list {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }}
    .replay {{ display:grid; grid-template-columns:96px 1fr; min-height:116px; overflow:hidden; border:1px solid #292d34; border-radius:13px; color:inherit; background:#17191e; text-decoration:none; }}
    .cover {{ position:relative; display:flex; align-items:center; justify-content:center; overflow:hidden; color:#66707c; background:linear-gradient(145deg,#25303a,#111419); font-size:28px; font-weight:800; }}
    .cover img {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
    .info {{ display:flex; min-width:0; flex-direction:column; padding:10px 10px 8px; }}
    .title {{ display:-webkit-box; overflow:hidden; font-size:14px; font-weight:650; line-height:1.42; -webkit-box-orient:vertical; -webkit-line-clamp:3; }}
    .meta {{ display:flex; flex-wrap:wrap; gap:5px; margin-top:7px; }}
    .meta span {{ padding:2px 5px; border:1px solid #343943; border-radius:5px; color:#afb5bd; background:#202329; font-size:10px; }}
    .source {{ overflow:hidden; margin-top:auto; padding-top:5px; color:#707782; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }}
    .footer {{ display:flex; flex-direction:column; align-items:center; gap:9px; padding:18px 0 4px; }}
    #status {{ min-height:18px; color:#858c96; font-size:12px; }}
    #more {{ min-width:150px; min-height:40px; padding:0 18px; border:1px solid #343a43; border-radius:20px; color:#e8ebef; background:#20242a; font:inherit; font-size:13px; }}
    #more:disabled {{ opacity:.55; }}
    #more[hidden] {{ display:none; }}
    @media (max-width:620px) {{
      #list {{ grid-template-columns:1fr; gap:8px; }}
      .replay {{ grid-template-columns:104px 1fr; min-height:122px; }}
      .title {{ font-size:14px; -webkit-line-clamp:3; }}
    }}
    @media (hover:hover) {{ .replay:hover {{ border-color:rgba(32,197,165,.7); }} #more:hover:not(:disabled) {{ border-color:var(--accent); }} }}
  </style>
</head>
<body>
  <main>
    <header><h1>{safe_query} 比赛回放</h1><p id="summary">正在载入最新回放…</p></header>
    <section id="list" aria-live="polite"></section>
    <div class="footer"><div id="sentinel"></div><div id="status"></div><button id="more" type="button" disabled>加载更多</button></div>
  </main>
  <script src="../collection.js?v=2.1.0"></script>
</body>
</html>"""


_COLLECTION_JAVASCRIPT = r"""
(() => {
  'use strict';
  const list = document.querySelector('#list');
  const summary = document.querySelector('#summary');
  const status = document.querySelector('#status');
  const more = document.querySelector('#more');
  const sentinel = document.querySelector('#sentinel');
  const apiUrl = new URL(location.href);
  apiUrl.pathname = apiUrl.pathname.replace('/collection/', '/collection-api/');
  apiUrl.search = '';
  let nextPage = 1;
  let loading = false;
  let hasMore = true;
  let total = 0;
  let autoAllowed = false;

  function safeCover(value) {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (_error) { return ''; }
  }

  function replayCard(item) {
    const link = document.createElement('a');
    link.className = 'replay';
    link.href = item.choose;
    const cover = document.createElement('div');
    cover.className = 'cover';
    cover.textContent = '回放';
    const coverUrl = safeCover(item.cover);
    if (coverUrl) {
      const image = document.createElement('img');
      image.src = coverUrl;
      image.alt = `${item.title}封面`;
      image.loading = 'lazy';
      image.referrerPolicy = 'no-referrer';
      cover.appendChild(image);
    }
    const info = document.createElement('div');
    info.className = 'info';
    const title = document.createElement('div');
    title.className = 'title';
    title.textContent = item.title;
    const meta = document.createElement('div');
    meta.className = 'meta';
    [item.category, item.remarks, item.episodes > 1 ? `${item.episodes}段` : '正片']
      .filter(Boolean)
      .forEach((value) => {
        const tag = document.createElement('span');
        tag.textContent = value;
        meta.appendChild(tag);
      });
    const source = document.createElement('div');
    source.className = 'source';
    source.textContent = item.source;
    info.append(title, meta, source);
    link.append(cover, info);
    return link;
  }

  async function loadNext(triggeredByScroll = false) {
    if (loading || !hasMore) return;
    loading = true;
    autoAllowed = false;
    more.disabled = true;
    more.textContent = '正在加载…';
    status.textContent = `正在读取第 ${nextPage} 页`;
    try {
      const url = new URL(apiUrl);
      url.searchParams.set('page', String(nextPage));
      const response = await fetch(url, { cache: 'no-store', credentials: 'omit' });
      const body = await response.json();
      if (!response.ok || !body.ok) throw new Error(body.error || '加载失败');
      body.items.forEach((item) => list.appendChild(replayCard(item)));
      total += body.items.length;
      hasMore = Boolean(body.has_more);
      nextPage += 1;
      summary.textContent = `已加载 ${total} 场，向下浏览可继续加载`;
      status.textContent = body.items.length
        ? ''
        : (hasMore ? '本页没有匹配回放，可继续加载下一页' : '已加载全部回放');
      autoAllowed = body.items.length > 0 && hasMore;
      more.hidden = !hasMore;
      more.disabled = !hasMore;
      more.textContent = hasMore ? '加载更多' : '已加载全部';
    } catch (error) {
      status.textContent = error.message || '加载失败，请重试';
      more.disabled = false;
      more.textContent = '重试';
      if (triggeredByScroll) autoAllowed = false;
    } finally {
      loading = false;
    }
  }

  more.addEventListener('click', () => loadNext(false));
  const observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting) && autoAllowed) loadNext(true);
  }, { rootMargin: '500px 0px' });
  observer.observe(sentinel);
  loadNext(false);
})();
""".strip()


def _player_html(title: str) -> str:
    safe_title = escape(title or "短剧播放器")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui,-apple-system,"Microsoft YaHei",sans-serif; --accent: #19b89b; }}
    * {{ box-sizing: border-box; }}
    html, body {{ min-height: 100%; }}
    body {{ margin: 0; background: #0b0c0f; color: #f3f5f7; }}
    body.sheet-open {{ overflow: hidden; }}
    button {{ font: inherit; }}
    main {{ width: min(100%, 980px); margin: 0 auto; padding: 12px 12px max(22px, env(safe-area-inset-bottom)); }}
    .heading {{ margin: 1px 2px 10px; }}
    h1 {{ display: -webkit-box; overflow: hidden; margin: 0 0 3px; font-size: clamp(17px, 4.4vw, 21px); font-weight: 650; line-height: 1.35; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }}
    #meta {{ color: #8f949d; font-size: 13px; }}
    .video-shell {{ position: relative; width: 100%; aspect-ratio: 16/9; margin: 0 auto; overflow: hidden; border-radius: 12px; background: #000; box-shadow: 0 8px 30px rgba(0,0,0,.28); transition: width .18s ease, aspect-ratio .18s ease; }}
    .video-shell:fullscreen, .video-shell:-webkit-full-screen, .video-shell.pseudo-fullscreen {{ width: 100vw !important; height: 100vh !important; height: 100dvh !important; max-width: none !important; aspect-ratio: auto !important; border-radius: 0; }}
    .video-shell.pseudo-fullscreen {{ position: fixed; z-index: 100; inset: 0; margin: 0; }}
    video {{ position: absolute; inset: 0; display: block; width: 100%; height: 100%; background: #000; object-fit: contain; }}
    .video-actions {{ position: absolute; inset: 0; z-index: 2; pointer-events: none; }}
    .icon-button, .pill-button {{ display: inline-flex; align-items: center; justify-content: center; border: 1px solid rgba(255,255,255,.16); color: #fff; background: rgba(10,12,15,.66); box-shadow: 0 2px 10px rgba(0,0,0,.22); backdrop-filter: blur(7px); -webkit-backdrop-filter: blur(7px); pointer-events: auto; }}
    .icon-button {{ position: absolute; top: 50%; width: 36px; height: 36px; padding: 0; border-radius: 50%; transform: translateY(-50%); }}
    .icon-button svg {{ width: 20px; height: 20px; fill: currentColor; }}
    #previous {{ left: 8px; }}
    #next {{ right: 8px; }}
    .icon-button:disabled {{ visibility: hidden; }}
    .pill-button {{ position: absolute; top: 8px; min-height: 31px; padding: 0 10px; border-radius: 16px; font-size: 12px; line-height: 1; }}
    #episodeOpen {{ left: 8px; min-width: 52px; }}
    #currentEpisode {{ position: absolute; z-index: 3; top: 8px; left: 50%; max-width: 46%; min-height: 31px; overflow: hidden; padding: 0 10px; color: #fff; font-size: 13px; line-height: 31px; text-overflow: ellipsis; text-shadow: 0 1px 4px #000; white-space: nowrap; transform: translateX(-50%); pointer-events: none; }}
    #qualityButton {{ right: 8px; min-width: 52px; }}
    #qualityButton:disabled {{ opacity: .72; }}
    #fullscreenButton {{ position: absolute; top: auto; right: 9px; bottom: 54px; z-index: 3; width: 34px; height: 34px; padding: 7px; border-radius: 8px; }}
    #fullscreenButton svg {{ width: 100%; height: 100%; fill: currentColor; }}
    #fullscreenButton .exit-fullscreen-icon {{ display: none; }}
    .video-shell.is-fullscreen #fullscreenButton .enter-fullscreen-icon {{ display: none; }}
    .video-shell.is-fullscreen #fullscreenButton .exit-fullscreen-icon {{ display: block; }}
    .quality-menu {{ position: absolute; z-index: 4; top: 44px; right: 8px; display: grid; grid-template-columns: repeat(2,minmax(68px,1fr)); gap: 3px; min-width: 156px; max-height: calc(100% - 52px); overflow-y: auto; padding: 5px; border: 1px solid #343840; border-radius: 10px; background: rgba(22,24,29,.96); box-shadow: 0 10px 30px rgba(0,0,0,.4); pointer-events: auto; }}
    .quality-menu[hidden] {{ display: none; }}
    .quality-menu button {{ display: block; width: 100%; min-height: 35px; padding: 0 8px; border: 0; border-radius: 7px; color: #d9dde3; background: transparent; text-align: center; }}
    .quality-menu button.active {{ color: #071512; background: var(--accent); font-weight: 650; }}
    .below-player {{ display: flex; align-items: center; gap: 10px; min-height: 38px; padding: 8px 2px 0; }}
    .switch-label {{ display: inline-flex; flex: 0 0 auto; align-items: center; gap: 7px; color: #aeb3bc; font-size: 13px; cursor: pointer; }}
    .switch-label input {{ position: absolute; width: 1px; height: 1px; opacity: 0; }}
    .switch {{ position: relative; width: 32px; height: 18px; border-radius: 10px; background: #383c44; transition: background .16s; }}
    .switch::after {{ content: ""; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%; background: #fff; transition: transform .16s; }}
    .switch-label input:checked + .switch {{ background: var(--accent); }}
    .switch-label input:checked + .switch::after {{ transform: translateX(14px); }}
    #status {{ flex: 1 1 auto; min-width: 0; margin: 0; color: #e4ae59; font-size: 12px; text-align: center; }}
    #retry {{ flex: 0 0 auto; min-height: 28px; padding: 0 9px; border: 1px solid #3a3e46; border-radius: 7px; color: #dce0e6; background: #202329; font-size: 12px; }}
    #retry[hidden] {{ display: none; }}
    #direct {{ flex: 0 0 auto; color: #8ca9ce; font-size: 12px; text-decoration: none; }}
    .inline-episodes {{ margin: 14px 0 2px; }}
    .inline-episodes-header {{ display: flex; align-items: baseline; justify-content: space-between; padding: 0 2px 10px; }}
    .inline-episodes-title {{ font-size: 18px; font-weight: 650; }}
    #episodeCount {{ color: #8f949d; font-size: 13px; }}
    .inline-episode-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(68px, 1fr)); gap: 8px; width: 100%; overflow: visible; }}
    .inline-episode-grid .episode-item {{ min-height: 48px; }}
    .sheet {{ position: fixed; z-index: 20; inset: 0; display: flex; align-items: flex-end; justify-content: center; padding-top: 48px; }}
    .sheet[hidden] {{ display: none; }}
    .sheet-backdrop {{ position: absolute; inset: 0; width: 100%; border: 0; background: rgba(0,0,0,.58); }}
    .sheet-panel {{ position: relative; width: min(100%, 720px); max-height: min(68vh, 620px); max-height: min(68svh, 620px); overflow: hidden; border: 1px solid #30333a; border-bottom: 0; border-radius: 16px 16px 0 0; background: #17191e; box-shadow: 0 -12px 38px rgba(0,0,0,.36); }}
    .sheet-header {{ display: flex; align-items: center; justify-content: space-between; min-height: 52px; padding: 0 14px; border-bottom: 1px solid #292c32; }}
    .sheet-title {{ font-size: 16px; font-weight: 650; }}
    .sheet-close {{ width: 34px; height: 34px; padding: 0; border: 0; border-radius: 50%; color: #bfc4cc; background: transparent; font-size: 25px; line-height: 1; }}
    .episode-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(72px,1fr)); gap: 8px; max-height: calc(min(68vh, 620px) - 53px); max-height: calc(min(68svh, 620px) - 53px); overflow-y: auto; padding: 12px 12px max(18px, env(safe-area-inset-bottom)); overscroll-behavior: contain; }}
    .episode-item {{ min-height: 40px; padding: 0 8px; border: 1px solid #343840; border-radius: 9px; color: #d7dbe1; background: #22252b; font-size: 13px; }}
    .episode-item.active {{ border-color: var(--accent); color: #071512; background: var(--accent); font-weight: 650; }}
    .episode-item:disabled {{ opacity: .45; }}
    @media (max-width: 560px) {{
      main {{ padding: 9px 9px max(18px, env(safe-area-inset-bottom)); }}
      .heading {{ margin: 0 3px 8px; }}
      .video-shell {{ border-radius: 10px; }}
      .icon-button {{ width: 34px; height: 34px; }}
      .below-player {{ gap: 7px; }}
      #status {{ text-align: left; }}
      .episode-grid {{ grid-template-columns: repeat(4, minmax(0,1fr)); }}
      .inline-episodes {{ margin-top: 12px; }}
      .inline-episode-grid {{ grid-template-columns: repeat(5, minmax(0,1fr)); gap: 7px; }}
      .inline-episode-grid .episode-item {{ min-height: 46px; padding: 0 3px; border-radius: 8px; }}
    }}
    @media (hover: hover) {{ .icon-button:hover, .pill-button:hover, .episode-item:hover {{ border-color: rgba(25,184,155,.8); }} }}
  </style>
</head>
<body>
  <main>
    <header class="heading">
      <h1 id="title">{safe_title}</h1>
      <div id="meta">正在载入剧集…</div>
    </header>
    <div id="videoShell" class="video-shell">
      <video id="video" controls controlslist="nofullscreen nodownload noremoteplayback" disablepictureinpicture playsinline webkit-playsinline preload="metadata"></video>
      <div class="video-actions">
        <button id="episodeOpen" class="pill-button" type="button" aria-label="选择剧集">选集</button>
        <div id="currentEpisode" aria-live="polite">第01集</div>
        <button id="qualityButton" class="pill-button" type="button" aria-label="选择清晰度" disabled><span id="qualityLabel">原画</span></button>
        <div id="qualityMenu" class="quality-menu" role="menu" hidden></div>
        <button id="previous" class="icon-button" type="button" aria-label="上一集">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.4 7.4 10.8 12l4.6 4.6L14 18l-6-6 6-6 1.4 1.4Z"/></svg>
        </button>
        <button id="next" class="icon-button" type="button" aria-label="下一集">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8.6 16.6 4.6-4.6-4.6-4.6L10 6l6 6-6 6-1.4-1.4Z"/></svg>
        </button>
        <button id="fullscreenButton" class="pill-button" type="button" aria-label="全屏播放">
          <svg class="enter-fullscreen-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3H3v4h2V5h2V3Zm14 4V3h-4v2h2v2h2ZM5 17H3v4h4v-2H5v-2Zm16 0h-2v2h-2v2h4v-4Z"/></svg>
          <svg class="exit-fullscreen-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3H3v4h4V5H5V3Zm14 0v2h-2v2h4V3h-2ZM7 17H3v4h2v-2h2v-2Zm14 0h-4v2h2v2h2v-4Z"/></svg>
        </button>
      </div>
      <div id="episodeSheet" class="sheet" hidden aria-hidden="true">
        <button id="sheetBackdrop" class="sheet-backdrop" type="button" aria-label="关闭选集"></button>
        <section class="sheet-panel" role="dialog" aria-modal="true" aria-labelledby="sheetTitle">
          <header class="sheet-header"><span id="sheetTitle" class="sheet-title">选择剧集</span><button id="sheetClose" class="sheet-close" type="button" aria-label="关闭">×</button></header>
          <div id="episodeGrid" class="episode-grid"></div>
        </section>
      </div>
    </div>
    <div class="below-player">
      <label class="switch-label"><input id="autoNext" type="checkbox" checked><span class="switch"></span><span>自动连播</span></label>
      <p id="status"></p>
      <button id="retry" type="button" hidden>重试</button>
      <a id="direct" target="_blank" rel="noreferrer">线路</a>
    </div>
    <section id="inlineEpisodes" class="inline-episodes" aria-labelledby="inlineEpisodesTitle">
      <header class="inline-episodes-header">
        <span id="inlineEpisodesTitle" class="inline-episodes-title">选集</span>
        <span id="episodeCount"></span>
      </header>
      <div id="inlineEpisodeGrid" class="inline-episode-grid"></div>
    </section>
  </main>
  <script src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"></script>
  <script src="../player.js?v=2.1.0"></script>
</body>
</html>"""


_PLAYER_JAVASCRIPT = r"""
(() => {
  'use strict';
  const video = document.querySelector('#video');
  const videoShell = document.querySelector('#videoShell');
  const previous = document.querySelector('#previous');
  const next = document.querySelector('#next');
  const retry = document.querySelector('#retry');
  const autoNext = document.querySelector('#autoNext');
  const status = document.querySelector('#status');
  const direct = document.querySelector('#direct');
  const title = document.querySelector('#title');
  const meta = document.querySelector('#meta');
  const currentEpisode = document.querySelector('#currentEpisode');
  const episodeOpen = document.querySelector('#episodeOpen');
  const episodeSheet = document.querySelector('#episodeSheet');
  const episodeGrid = document.querySelector('#episodeGrid');
  const inlineEpisodes = document.querySelector('#inlineEpisodes');
  const inlineEpisodeGrid = document.querySelector('#inlineEpisodeGrid');
  const episodeCount = document.querySelector('#episodeCount');
  const sheetBackdrop = document.querySelector('#sheetBackdrop');
  const sheetClose = document.querySelector('#sheetClose');
  const qualityButton = document.querySelector('#qualityButton');
  const qualityLabel = document.querySelector('#qualityLabel');
  const qualityMenu = document.querySelector('#qualityMenu');
  const fullscreenButton = document.querySelector('#fullscreenButton');
  const token = location.pathname.split('/').filter(Boolean).pop() || 'unknown';
  const apiUrl = new URL(location.href);
  apiUrl.pathname = apiUrl.pathname.replace('/watch/', '/api/');
  apiUrl.search = '';
  apiUrl.hash = '';

  let data = null;
  let current = 0;
  let hls = null;
  let qualityPreference = localStorage.getItem('short-drama:quality') || 'auto';
  let lastProgressSecond = -1;

  const progressKey = (index) => `short-drama:${token}:${index}:time`;
  const episodeKey = `short-drama:${token}:episode`;

  const savedAutoNext = localStorage.getItem('short-drama:auto-next');
  autoNext.checked = savedAutoNext === null ? true : savedAutoNext === 'true';

  function show(message, canRetry = false) {
    status.textContent = message || '';
    retry.hidden = !canRetry;
  }

  function destroyHls() {
    if (hls) { hls.destroy(); hls = null; }
    qualityMenu.hidden = true;
    qualityMenu.textContent = '';
    qualityLabel.textContent = '原画';
    qualityButton.disabled = true;
    video.removeAttribute('src');
    video.load();
  }

  function updateButtons() {
    previous.disabled = current <= 0;
    next.disabled = !data || current >= data.episodes.length - 1;
    if (data) currentEpisode.textContent = data.episodes[current]?.name || `第${current + 1}集`;
    document.querySelectorAll('.episode-item').forEach((button) => {
      button.classList.toggle('active', Number(button.dataset.index) === current);
    });
  }

  function applyVideoAspect() {
    if (videoShell.classList.contains('is-fullscreen')) return;
    if (!video.videoWidth || !video.videoHeight) return;
    const ratio = video.videoWidth / video.videoHeight;
    const availableWidth = Math.min(980, Math.max(240, document.documentElement.clientWidth - 18));
    const maxHeight = Math.max(240, window.innerHeight * .72);
    videoShell.style.aspectRatio = `${video.videoWidth}/${video.videoHeight}`;
    videoShell.style.width = `${Math.min(availableWidth, maxHeight * ratio)}px`;
  }

  function activeFullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }

  function updateFullscreenState() {
    const active = activeFullscreenElement() === videoShell
      || videoShell.classList.contains('pseudo-fullscreen');
    videoShell.classList.toggle('is-fullscreen', active);
    fullscreenButton.setAttribute('aria-label', active ? '退出全屏' : '全屏播放');
    if (!active) applyVideoAspect();
  }

  async function togglePlayerFullscreen() {
    const active = activeFullscreenElement() === videoShell
      || videoShell.classList.contains('pseudo-fullscreen');
    if (active) {
      if (document.exitFullscreen && activeFullscreenElement()) await document.exitFullscreen();
      else if (document.webkitExitFullscreen && activeFullscreenElement()) document.webkitExitFullscreen();
      else videoShell.classList.remove('pseudo-fullscreen');
      updateFullscreenState();
      return;
    }

    try {
      if (videoShell.requestFullscreen) await videoShell.requestFullscreen({ navigationUI: 'hide' });
      else if (videoShell.webkitRequestFullscreen) videoShell.webkitRequestFullscreen();
      else videoShell.classList.add('pseudo-fullscreen');
    } catch (_error) {
      videoShell.classList.add('pseudo-fullscreen');
    }
    updateFullscreenState();
  }

  function closeEpisodeSheet() {
    episodeSheet.hidden = true;
    episodeSheet.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('sheet-open');
  }

  function openEpisodeSheet() {
    if (!data) return;
    qualityMenu.hidden = true;
    episodeSheet.hidden = false;
    episodeSheet.setAttribute('aria-hidden', 'false');
    document.body.classList.add('sheet-open');
    const active = episodeGrid.querySelector('.episode-item.active');
    if (active) active.scrollIntoView({ block: 'center' });
    sheetClose.focus();
  }

  function openEpisodeSelector() {
    const fullscreen = activeFullscreenElement() === videoShell
      || videoShell.classList.contains('pseudo-fullscreen');
    if (fullscreen) {
      openEpisodeSheet();
      return;
    }
    inlineEpisodes.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function episodeButtonLabel(episode, index) {
    const name = episode.name || `第${index + 1}集`;
    const match = /^\u7b2c\s*0*(\d+)\s*\u96c6$/.exec(name);
    return match ? String(Number(match[1])) : name;
  }

  function createEpisodeButton(episode, index, inSheet) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'episode-item';
    button.dataset.index = String(index);
    button.textContent = episodeButtonLabel(episode, index);
    button.setAttribute('aria-label', episode.name || `第${index + 1}集`);
    button.addEventListener('click', () => {
      if (inSheet) closeEpisodeSheet();
      loadEpisode(index, true);
      if (!inSheet) videoShell.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    return button;
  }

  function renderEpisodes() {
    episodeGrid.textContent = '';
    inlineEpisodeGrid.textContent = '';
    episodeCount.textContent = `共${data.episodes.length}集`;
    data.episodes.forEach((episode, index) => {
      episodeGrid.appendChild(createEpisodeButton(episode, index, true));
      inlineEpisodeGrid.appendChild(createEpisodeButton(episode, index, false));
    });
  }

  function levelName(level) {
    if (level?.height) return `${level.height}P`;
    if (level?.bitrate) return `${Math.round(level.bitrate / 1000)}K`;
    return '清晰度';
  }

  function updateQualityActive(forcedLevel = null) {
    const manualLevel = !hls?.autoLevelEnabled
      ? (hls.currentLevel >= 0 ? hls.currentLevel : forcedLevel)
      : null;
    qualityMenu.querySelectorAll('button').forEach((button) => {
      const autoActive = button.dataset.level === 'auto' && hls?.autoLevelEnabled;
      const levelActive = button.dataset.level !== 'auto'
        && !hls?.autoLevelEnabled
        && Number(button.dataset.level) === manualLevel;
      button.classList.toggle('active', Boolean(autoActive || levelActive));
    });
    if (!hls || hls.levels.length <= 1) qualityLabel.textContent = '原画';
    else if (hls.autoLevelEnabled) qualityLabel.textContent = '自动';
    else qualityLabel.textContent = levelName(hls.levels[manualLevel]);
  }

  function chooseQuality(value) {
    if (!hls) return;
    if (value === 'auto') {
      hls.currentLevel = -1;
      qualityPreference = 'auto';
    } else {
      const index = Number(value);
      const level = hls.levels[index];
      if (!level) return;
      hls.currentLevel = index;
      qualityPreference = level.height ? `height:${level.height}` : `bitrate:${level.bitrate}`;
    }
    localStorage.setItem('short-drama:quality', qualityPreference);
    qualityMenu.hidden = true;
    updateQualityActive(value === 'auto' ? null : Number(value));
  }

  function setupQualityMenu() {
    qualityMenu.textContent = '';
    const levels = hls?.levels || [];
    if (levels.length <= 1) {
      qualityButton.disabled = true;
      qualityLabel.textContent = '原画';
      return;
    }
    qualityButton.disabled = false;
    const entries = [
      { index: 'auto', label: '自动' },
      ...levels.map((level, index) => ({ index: String(index), label: levelName(level) })).reverse(),
    ];
    entries.forEach((entry) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.level = entry.index;
      button.textContent = entry.label;
      button.addEventListener('click', () => chooseQuality(entry.index));
      qualityMenu.appendChild(button);
    });

    let preferredLevel = null;
    if (qualityPreference.startsWith('height:')) {
      const wanted = Number(qualityPreference.split(':')[1]);
      const match = levels.findIndex((level) => level.height === wanted);
      hls.currentLevel = match >= 0 ? match : -1;
      preferredLevel = match >= 0 ? match : null;
    } else if (qualityPreference.startsWith('bitrate:')) {
      const wanted = Number(qualityPreference.split(':')[1]);
      const match = levels.findIndex((level) => level.bitrate === wanted);
      hls.currentLevel = match >= 0 ? match : -1;
      preferredLevel = match >= 0 ? match : null;
    } else {
      hls.currentLevel = -1;
    }
    updateQualityActive(preferredLevel);
  }

  function restoreTime(index) {
    const seconds = Number(localStorage.getItem(progressKey(index)) || 0);
    if (Number.isFinite(seconds) && seconds > 2) {
      const apply = () => {
        if (!Number.isFinite(video.duration) || seconds < video.duration - 10) {
          video.currentTime = seconds;
        }
      };
      video.addEventListener('loadedmetadata', apply, { once: true });
    }
  }

  function loadEpisode(index, shouldPlay = false) {
    if (!data || index < 0 || index >= data.episodes.length) return;
    destroyHls();
    current = index;
    const episode = data.episodes[index];
    direct.href = episode.url;
    direct.title = `${episode.name} 直链`;
    localStorage.setItem(episodeKey, String(index));
    const shareUrl = new URL(location.href);
    shareUrl.searchParams.set('ep', String(index + 1));
    history.replaceState(null, '', shareUrl);
    updateButtons();
    show(`正在载入 ${episode.name}…`);
    lastProgressSecond = -1;

    if (window.Hls && window.Hls.isSupported()) {
      hls = new window.Hls({ enableWorker: true });
      hls.loadSource(episode.url);
      hls.attachMedia(video);
      hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
        setupQualityMenu();
        show('');
        restoreTime(index);
        if (shouldPlay) video.play().catch(() => {});
      });
      hls.on(window.Hls.Events.LEVEL_SWITCHED, () => updateQualityActive());
      hls.on(window.Hls.Events.ERROR, (_event, info) => {
        if (!info.fatal) return;
        show('播放线路载入失败', true);
        if (info.type === window.Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
      });
      return;
    }

    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = episode.url;
      video.addEventListener('loadedmetadata', () => {
        qualityLabel.textContent = '原画';
        show('');
        restoreTime(index);
        if (shouldPlay) video.play().catch(() => {});
      }, { once: true });
      return;
    }
    show('当前浏览器不支持 HLS 播放', true);
  }

  fetch(apiUrl, { cache: 'no-store', credentials: 'omit' })
    .then((response) => response.json().then((body) => ({ response, body })))
    .then(({ response, body }) => {
      if (!response.ok || !body.ok) throw new Error(body.error || '剧集载入失败');
      data = body;
      title.textContent = body.title;
      meta.textContent = `${body.source} · 共${body.episodes.length}集`;
      document.title = body.title;
      renderEpisodes();
      const requested = Number(new URL(location.href).searchParams.get('ep')) - 1;
      const saved = Number(localStorage.getItem(episodeKey));
      const initial = Number.isInteger(requested) && requested >= 0
        ? requested
        : (Number.isInteger(saved) && saved >= 0 ? saved : 0);
      loadEpisode(Math.min(initial, body.episodes.length - 1));
    })
    .catch((error) => show(error.message || '剧集载入失败', true));

  previous.addEventListener('click', () => loadEpisode(current - 1, true));
  next.addEventListener('click', () => loadEpisode(current + 1, true));
  fullscreenButton.addEventListener('click', togglePlayerFullscreen);
  retry.addEventListener('click', () => loadEpisode(current, true));
  episodeOpen.addEventListener('click', openEpisodeSelector);
  sheetBackdrop.addEventListener('click', closeEpisodeSheet);
  sheetClose.addEventListener('click', closeEpisodeSheet);
  qualityButton.addEventListener('click', (event) => {
    event.stopPropagation();
    if (!qualityButton.disabled) qualityMenu.hidden = !qualityMenu.hidden;
  });
  document.addEventListener('click', (event) => {
    if (!qualityMenu.contains(event.target) && event.target !== qualityButton) qualityMenu.hidden = true;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeEpisodeSheet();
  });
  autoNext.addEventListener('change', () => {
    localStorage.setItem('short-drama:auto-next', String(autoNext.checked));
  });
  video.addEventListener('loadedmetadata', applyVideoAspect);
  window.addEventListener('resize', applyVideoAspect);
  document.addEventListener('fullscreenchange', updateFullscreenState);
  document.addEventListener('webkitfullscreenchange', updateFullscreenState);
  video.addEventListener('timeupdate', () => {
    const second = Math.floor(video.currentTime);
    if (second >= 0 && (lastProgressSecond < 0 || second - lastProgressSecond >= 5)) {
      lastProgressSecond = second;
      localStorage.setItem(progressKey(current), String(video.currentTime));
    }
  });
  video.addEventListener('ended', () => {
    localStorage.removeItem(progressKey(current));
    if (autoNext.checked && data && current < data.episodes.length - 1) {
      loadEpisode(current + 1, true);
    }
  });
})();
""".strip()
