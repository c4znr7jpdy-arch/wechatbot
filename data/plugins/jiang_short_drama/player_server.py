"""Token-based multi-episode HLS player served by the AstrBot plugin."""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from html import escape
from urllib.parse import urlencode, urlsplit

from aiohttp import web

from .search import Episode, SearchResult


logger = logging.getLogger("jiang_short_drama")
_ROUTE_PREFIX = "/short-drama"


@dataclass(frozen=True, slots=True)
class PlaylistRecord:
    title: str
    source: str
    cover_url: str
    episodes: tuple[Episode, ...]
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
  <script src="../player.js?v=1.2.4"></script>
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
