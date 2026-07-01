"""
B站用户动态获取与推送模块
使用 crawlers 库的 BilibiliWebCrawler（带 w_rid 签名）
"""
import os
import tempfile
from pathlib import Path
from datetime import datetime

import httpx
from nonebot import logger
from nonebot.adapters.onebot.v12 import MessageSegment

from .douyin.crawlers.bilibili.web.web_crawler import BilibiliWebCrawler


_crawler = BilibiliWebCrawler()
_IMG_MAX = 3

_SUMMARIZE_PROMPT = """你是一个B站动态摘要助手。把下面的动态列表改写为简洁有趣的中文摘要，要求：
- 保留每条的序号、类型标签、时间
- 每条内容精简到一句话（20-50字），突出核心信息
- 语气轻松自然，像朋友分享给你看的
- 不要加emoji，不要加多余的开头结尾
- 直接输出改写后的列表，格式和原文一致"""


async def _ai_summarize(raw_text: str) -> str:
    """用 MiniMax 润色动态摘要，失败则返回原文"""
    api_key = os.getenv("MINIMAX_API_KEY", "")
    if not api_key:
        return raw_text
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.minimaxi.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "MiniMax-M2.7-highspeed",
                    "messages": [
                        {"role": "system", "content": _SUMMARIZE_PROMPT},
                        {"role": "user", "content": raw_text},
                    ],
                    "stream": False,
                    "max_tokens": 600,
                    "temperature": 0.5,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                result = data["choices"][0]["message"]["content"].strip()
                if result:
                    return result
    except Exception as e:
        logger.debug(f"[BILI] AI润色失败，使用原文: {e}")
    return raw_text


async def fetch_dynamics(uid: str, count: int = 5) -> list[dict]:
    """通过 crawlers 库获取用户最新动态（带 w_rid 签名）"""
    resp = await _crawler.fetch_user_dynamic(uid=uid, offset="")
    if resp.get("code") != 0:
        raise RuntimeError(f"B站API错误: {resp.get('message', 'unknown')}")

    items = resp.get("data", {}).get("items", [])
    result = []
    for item in items:
        modules = item.get("modules", {})
        author_mod = modules.get("module_author", {})
        dynamic = modules.get("module_dynamic", {})
        major = dynamic.get("major") or {}
        desc = dynamic.get("desc") or {}
        dtype = item.get("type", "")

        entry = {
            "dynamic_id": item.get("id_str", ""),
            "timestamp": author_mod.get("pub_ts", 0),
            "author": author_mod.get("name", ""),
        }

        if dtype == "DYNAMIC_TYPE_AV":
            archive = major.get("archive", {})
            entry["type"] = "video"
            entry["title"] = archive.get("title", "")
            entry["bvid"] = archive.get("bvid", "")
            entry["description"] = archive.get("desc", "") or desc.get("text", "")
            entry["cover"] = archive.get("cover", "")
        elif dtype == "DYNAMIC_TYPE_DRAW":
            draw = major.get("draw", {})
            pics = draw.get("items", [])
            entry["type"] = "image"
            entry["text"] = desc.get("text", "")
            entry["pictures"] = [p.get("src", "") for p in pics if p.get("src")]
        elif dtype == "DYNAMIC_TYPE_WORD":
            entry["type"] = "text"
            entry["text"] = desc.get("text", "")
        elif dtype == "DYNAMIC_TYPE_ARTICLE":
            article = major.get("article", {})
            entry["type"] = "article"
            entry["title"] = article.get("title", "")
            entry["text"] = desc.get("text", "") or article.get("desc", "")
        else:
            continue

        result.append(entry)
        if len(result) >= count:
            break
    return result


async def download_image(url: str) -> str | None:
    """下载图片到临时文件，返回本地路径"""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={
                "Referer": "https://www.bilibili.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            resp.raise_for_status()
            suffix = ".jpg"
            if "png" in resp.headers.get("content-type", ""):
                suffix = ".png"
            elif "webp" in resp.headers.get("content-type", ""):
                suffix = ".webp"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="bili_")
            tmp.write(resp.content)
            tmp.close()
            return tmp.name
    except Exception as e:
        logger.warning(f"[BILI] 下载图片失败: {url} -> {e}")
        return None


def _ts_to_str(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        return ""


def _truncate(text: str, limit: int = 100) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _format_item(idx: int, item: dict) -> str:
    """格式化单条动态为文本行"""
    time_str = _ts_to_str(item.get("timestamp", 0))
    dtype = item["type"]
    type_tag = {"video": "视频", "image": "图文", "text": "动态", "article": "专栏"}.get(dtype, "动态")

    if dtype == "video":
        title = item.get("title", "")
        desc = item.get("description", "")
        content = title
        if desc and desc != title:
            content = f"{title} | {desc}"
        bvid = item.get("bvid", "")
        line = f"{idx}. [{type_tag}] {_truncate(content)}"
        if bvid:
            line += f"\n   bilibili.com/video/{bvid}"
    elif dtype == "article":
        title = item.get("title", "")
        text = item.get("text", "")
        content = title
        if text and text != title:
            content = f"{title} | {text}"
        line = f"{idx}. [{type_tag}] {_truncate(content)}"
    elif dtype == "image":
        text = item.get("text", "")
        pic_count = len(item.get("pictures", []))
        content = text or "(无文字)"
        line = f"{idx}. [{type_tag}] {_truncate(content)} (图×{pic_count})"
    else:
        text = item.get("text", "")
        line = f"{idx}. [{type_tag}] {_truncate(text or '(无内容)')}"

    return f"[{time_str}] {line}" if time_str else line


async def send_dynamics_list(bot, target_id: str, is_group: bool,
                             items: list[dict], title: str = ""):
    """将多条动态合并为一条消息发送（时间倒序，最新在前，AI润色）"""
    detail_type = "group" if is_group else "private"
    kwargs = {
        "detail_type": detail_type,
        "user_id": target_id if not is_group else None,
        "group_id": target_id if is_group else None,
    }

    if not items:
        await bot.send_message(**kwargs, message=f"{title} 暂无动态")
        return

    # 按时间倒序排列（最新在前）
    items_sorted = sorted(items, key=lambda x: int(x.get("timestamp", 0) or 0), reverse=True)

    header = f"📢 {title} 最近动态" if title else "📢 最近动态"
    lines = [header, ""]
    for idx, item in enumerate(items_sorted, 1):
        lines.append(_format_item(idx, item))

    raw_text = "\n".join(lines)
    final_text = await _ai_summarize(raw_text)
    await bot.send_message(**kwargs, message=final_text)


async def send_dynamic(bot, target_id: str, is_group: bool, item: dict):
    """推送单条新动态（用于定时轮询推送）"""
    detail_type = "group" if is_group else "private"
    kwargs = {
        "detail_type": detail_type,
        "user_id": target_id if not is_group else None,
        "group_id": target_id if is_group else None,
    }
    author = item.get("author", "")
    line = _format_item(1, item).lstrip("1. ")
    header = f"📢 {author} 发布了新动态" if author else "📢 新动态"
    msg = f"{header}\n\n{line}"

    await bot.send_message(**kwargs, message=msg)

    # 图文类型额外发图片（最多3张）
    if item.get("type") == "image":
        pictures = item.get("pictures", [])
        for pic_url in pictures[:_IMG_MAX]:
            local_path = await download_image(pic_url)
            if local_path:
                try:
                    await bot.send_message(
                        **kwargs,
                        message=MessageSegment("image", {"file_id": local_path}),
                    )
                except Exception as e:
                    logger.warning(f"[BILI] 发送图片失败: {e}")
                finally:
                    try:
                        Path(local_path).unlink(missing_ok=True)
                    except Exception:
                        pass
