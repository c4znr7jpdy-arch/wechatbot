"""
消息路由模块 — 从 __init__.py 提取的消息处理逻辑
"""
import os
import re
import asyncio
import hashlib
import random
import time as _time_mod
from pathlib import Path

import httpx
from nonebot import Bot, logger
from nonebot.adapters.onebot.v12 import MessageEvent, PrivateMessageEvent, MessageSegment

from .config import Config
from .handler import AIHandler
from .tools import ToolResult
from .hermes_client import get_hermes_client
from .message_buffer import get_buffer, BufferedMessage
from .utils import clean_at
from .video_generator import detect_video_intent, get_video_generator
from .image_generator import detect_image_intent, detect_image_edit_intent, get_image_generator, switch_image_model, get_current_image_model
from .image_editor import get_image_editor, get_current_editor_model, switch_editor_model
from .mcp_tools import detect_search_intent, get_search_client, get_vlm_client
from .douyin import extract_url, extract_bilibili_uid, parse_url, format_result, fetch_user_dynamics, format_user_dynamics
from .kfc import detect_kfc_intent, fetch_kfc_text
from .news import fetch_hot_news, format_news
from .weather import fetch_weather, format_weather
from .epic import fetch_epic_free, format_epic_free
from .oilprice import fetch_oilprice, format_oilprice
from . import runtime
from .tts import tts_to_silk, send_voice_via_cdn, set_voice, get_current_voice, VOICE_OPTIONS
from .plugin_manager import is_enabled, enable_plugin, disable_plugin, list_plugins, find_plugin_key


def _detect_news_intent(text: str) -> bool:
    """检测新闻/热点意图"""
    return bool(re.match(r"^\s*(新闻|热点|热榜|今日热点|抖音热榜|news)\s*$", text, re.IGNORECASE))


def _detect_epic_intent(text: str) -> bool:
    """检测 Epic 喜加一意图"""
    return bool(re.match(r"^\s*(epic|喜加一|epic喜加一|免费游戏)\s*$", text, re.IGNORECASE))


def _detect_weather_intent(text: str) -> str | None:
    """检测天气意图，返回城市名；无意图返回 None"""
    text = re.sub(r"^@?姜小妹[\s ]+", "", text.strip())
    m = re.match(r"^\s*(.+?)(?:天气|天气预报)\s*$", text)
    if m:
        city = m.group(1).strip()
        if city:
            # 去除常见口语化前缀（长前缀在前，避免部分匹配残留）
            city = re.sub(
                r"^(帮我查一下|帮我看看|我想看看|给我看看|帮我看|来一下|查一下|告诉我|帮我查|查查|看看|查)\s*",
                "", city,
            )
            # 去除尾部 "的"
            city = city.rstrip("的").strip()
            # 去除省级前缀，只保留城市名（如 "山东青岛" → "青岛"）
            # 注意：直辖市（北京/上海/天津/重庆）本身就是市级，不剥离
            city = re.sub(
                r"^(河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|内蒙古|广西|西藏|宁夏|新疆|香港|澳门)",
                "", city,
            )
            if city:
                return city
    return None


def _detect_oilprice_intent(text: str) -> str | None:
    """检测油价意图，返回省份名；无意图返回 None"""
    m = re.match(r"^\s*(.+?)(?:油价|汽油价格)\s*$", text)
    if m:
        province = m.group(1).strip()
        if province:
            return province
    if re.match(r"^\s*油价\s*$", text):
        return ""
    return None


def _parse_aspect(prompt: str) -> tuple[str, str]:
    """从 prompt 中提取比例，返回 (aspect_ratio, cleaned_prompt)"""
    patterns = [
        (r"比例\s*16[：:]9", "16:9"),
        (r"比例\s*9[：:]16", "9:16"),
        (r"比例\s*4[：:]3", "4:3"),
        (r"比例\s*3[：:]4", "3:4"),
        (r"比例\s*3[：:]2", "3:2"),
        (r"比例\s*2[：:]3", "2:3"),
        (r"比例\s*1[：:]1", "1:1"),
        (r"16[：:]9", "16:9"),
        (r"9[：:]16", "9:16"),
        (r"横屏", "16:9"),
        (r"竖屏", "9:16"),
    ]
    for pat, aspect in patterns:
        m = re.search(pat, prompt)
        if m:
            cleaned = prompt[:m.start()] + prompt[m.end():]
            cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip("，,。.")
            return aspect, cleaned
    return "1:1", prompt


async def _handle_video(bot: Bot, prompt: str, target_id: str, is_group: bool) -> bool:
    vg = get_video_generator()
    if not vg:
        await _send(bot, target_id, is_group, "视频生成功能暂不可用，请检查 MiniMax API Key 配置。")
        return True
    try:
        await _send(bot, target_id, is_group, f"正在为你生成视频，通常需要 1-5 分钟...\n提示词: {prompt}")
        video_path = await vg.generate(prompt)
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=MessageSegment("video", {"file_id": video_path}),
        )
        logger.info(f"[VIDEO] 视频已发送: {video_path}")
    except Exception as e:
        logger.exception(f"[VIDEO] 生成失败: {e}")
        await _send(bot, target_id, is_group, f"视频生成失败: {str(e)[:200]}")
    return True


async def _handle_image(bot: Bot, prompt: str, target_id: str, is_group: bool) -> bool:
    ig = get_image_generator()
    if not ig:
        await _send(bot, target_id, is_group, "图片生成功能暂不可用，请检查 MiniMax API Key 配置。")
        return True
    try:
        aspect, clean_prompt = _parse_aspect(prompt)
        model_name = get_current_image_model()
        await _send(bot, target_id, is_group, f"正在用 {model_name} 生成图片...\n比例: {aspect}\n提示词: {clean_prompt}")
        paths = await ig.generate(clean_prompt, n=1, aspect_ratio=aspect)
        for path in paths:
            await bot.send_message(
                detail_type="group" if is_group else "private",
                user_id=target_id if not is_group else None,
                group_id=target_id if is_group else None,
                message=MessageSegment("image", {"file_id": path}),
            )
        logger.info(f"[IMAGE] 已发送 {len(paths)} 张图片")
    except httpx.TimeoutException:
        logger.exception("[IMAGE] 生成超时")
        await _send(bot, target_id, is_group, "图片生成超时（超过2分钟），请简化提示词后重试")
    except Exception as e:
        logger.exception(f"[IMAGE] 生成失败: {e}")
        err_msg = str(e)
        if "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
            hint = "图片生成超时（超过2分钟），请简化提示词后重试"
        elif "404" in err_msg:
            hint = "图片生成服务暂不可用（404），请稍后重试"
        elif "502" in err_msg:
            hint = "图片生成服务暂时繁忙（502），请稍后重试"
        elif "balance" in err_msg.lower() or "insufficient" in err_msg.lower():
            hint = "图片生成失败：API 余额不足"
        elif "sensitive" in err_msg.lower() or "content" in err_msg.lower():
            hint = "图片生成失败：提示词包含敏感内容，请修改后重试"
        else:
            hint = f"图片生成失败: {err_msg[:200]}"
        await _send(bot, target_id, is_group, hint)
    return True


async def _handle_image_edit(bot: Bot, prompt: str, image_path: str | None,
                             target_id: str, is_group: bool) -> bool:
    editor = get_image_editor()
    if not editor:
        await _send(bot, target_id, is_group, "图生图功能暂不可用，请检查 API Key 配置。")
        return True
    if not image_path:
        await _send(bot, target_id, is_group, "未能下载引用的图片，请确认图片可访问后重试。")
        return True
    try:
        model_name = get_current_editor_model()
        await _send(bot, target_id, is_group, f"正在用 {model_name} 处理图片...\n提示词: {prompt}")
        paths = await editor.edit(image_path, prompt, model=model_name)
        for path in paths:
            await bot.send_message(
                detail_type="group" if is_group else "private",
                user_id=target_id if not is_group else None,
                group_id=target_id if is_group else None,
                message=MessageSegment("image", {"file_id": path}),
            )
        logger.info(f"[IMAGE EDIT] 已发送 {len(paths)} 张编辑后图片")
    except httpx.TimeoutException:
        logger.exception("[IMAGE EDIT] 超时")
        await _send(bot, target_id, is_group, "图片处理超时，请简化提示词后重试")
    except Exception as e:
        logger.exception(f"[IMAGE EDIT] 失败: {e}")
        await _send(bot, target_id, is_group, f"图片处理失败: {str(e)[:300]}")
    return True


async def _handle_image_understand(bot: Bot, text: str, image_path: str,
                                   target_id: str, is_group: bool) -> bool:
    """处理图片理解请求 — 引用图片 + 问图中内容（MiniMax VLM API）"""
    if not image_path or not os.path.exists(image_path):
        await _send(bot, target_id, is_group, "未能获取引用的图片，请确认图片可访问后重试。")
        return True
    vlm = get_vlm_client()
    if not vlm:
        await _send(bot, target_id, is_group, "图片理解功能暂不可用，请检查 MiniMax API Key 配置。")
        return True
    try:
        await _send(bot, target_id, is_group, "正在分析图片...")
        prompt = text.strip() if text.strip() else "请描述这张图片的内容"
        reply = await vlm.understand(image_path, prompt)
        await _send(bot, target_id, is_group, reply)
        logger.info(f"[VLM] 图片理解完成: {image_path}")
    except Exception as e:
        logger.exception(f"[VLM] 图片理解失败: {e}")
        await _send(bot, target_id, is_group, f"图片分析失败: {str(e)[:200]}")
    return True


async def _handle_bilibili_space(bot: Bot, uid: str, target_id: str, is_group: bool) -> bool:
    try:
        items = await fetch_user_dynamics(uid)
        msg = format_user_dynamics(items)
        await _send(bot, target_id, is_group, msg)
        logger.info(f"[DOUYIN] B站用户 {uid} 动态已获取 ({len(items)}条)")
    except Exception as e:
        logger.exception(f"[DOUYIN] B站用户查询失败: {uid}: {e}")
        await _send(bot, target_id, is_group, f"获取B站用户动态失败: {e}")
    return True


async def _handle_kfc(bot: Bot, target_id: str, is_group: bool) -> bool:
    try:
        text = await fetch_kfc_text()
        await _send(bot, target_id, is_group, text)
        logger.info("[KFC] 疯狂星期四文案已发送")
    except Exception as e:
        logger.exception(f"[KFC] 获取文案失败: {e}")
        await _send(bot, target_id, is_group, "V我50！")
    return True


async def _handle_news(bot: Bot, target_id: str, is_group: bool) -> bool:
    try:
        items = await fetch_hot_news(20)
        msg = format_news(items)
        await _send(bot, target_id, is_group, msg)
        logger.info(f"[NEWS] 热点新闻已发送 ({len(items)}条)")
    except Exception as e:
        logger.exception(f"[NEWS] 获取新闻失败: {e}")
        await _send(bot, target_id, is_group, f"获取新闻失败: {e}")
    return True


async def _handle_weather(bot: Bot, city: str, target_id: str, is_group: bool) -> bool:
    try:
        data = await fetch_weather(city)
        msg = format_weather(data)
        await _send(bot, target_id, is_group, msg)
        logger.info(f"[WEATHER] 天气预报已发送: {city}")
    except Exception as e:
        logger.exception(f"[WEATHER] 获取天气失败: {e}")
        await _send(bot, target_id, is_group, f"获取天气失败: {e}")
    return True


async def _handle_epic(bot: Bot, target_id: str, is_group: bool) -> bool:
    try:
        games = await fetch_epic_free()
        msg = format_epic_free(games)
        await _send(bot, target_id, is_group, msg)
        logger.info(f"[EPIC] Epic 喜加一已发送 ({len(games)}个)")
    except Exception as e:
        logger.exception(f"[EPIC] 获取 Epic 免费游戏失败: {e}")
        await _send(bot, target_id, is_group, f"获取 Epic 免费游戏失败: {e}")
    return True


async def _handle_oilprice(bot: Bot, province: str, target_id: str, is_group: bool) -> bool:
    if not province:
        await _send(bot, target_id, is_group, "请指定省份，如: 四川油价、北京油价")
        return True
    try:
        data = await fetch_oilprice(province)
        msg = format_oilprice(data)
        await _send(bot, target_id, is_group, msg)
        logger.info(f"[OILPRICE] 油价已发送: {province}")
    except Exception as e:
        logger.exception(f"[OILPRICE] 获取油价失败: {e}")
        await _send(bot, target_id, is_group, f"获取油价失败: {e}")
    return True


async def _handle_douyin(bot: Bot, url: str, target_id: str, is_group: bool) -> bool:
    try:
        data = await parse_url(url)
        msg = format_result(data)
        media_type = data.get("type", "video")
        dl_dir = (Path(__file__).parent / ".." / "data" / "douyin").resolve()
        dl_dir.mkdir(parents=True, exist_ok=True)

        if media_type == "image":
            image_urls = data.get("image_data", {}).get("no_watermark_image_list", [])
            if image_urls:
                total = len(image_urls)
                # ≤5 张直接发到聊天，>5 张发到文件传输助手（避免刷屏）
                send_to = target_id if total <= 5 else "filehelper"
                is_filehelper = (send_to == "filehelper")

                for i, img_url in enumerate(image_urls):
                    try:
                        path = await _download_image(img_url, dl_dir)
                        await bot.send_message(
                            detail_type="private",
                            user_id=send_to,
                            group_id=None,
                            message=MessageSegment("image", {"file_id": path}),
                        )
                        logger.info(f"[DOUYIN] 图片已发送 [{i+1}/{total}] -> {send_to}")
                    except Exception as e:
                        logger.warning(f"[DOUYIN] 图片下载失败 [{i+1}]: {e}")

                if is_filehelper:
                    await _send(bot, target_id, is_group, f"图集共{total}张，已发送至文件传输助手，请手动转发到群聊")
                await _send(bot, target_id, is_group, msg)
            else:
                await _send(bot, target_id, is_group, msg)
        elif media_type == "video":
            video_data = data.get("video_data", {})
            video_url = video_data.get("nwm_video_url_HQ") or video_data.get("nwm_video_url") or ""
            sent = False
            if video_url:
                try:
                    path = await _download_video(video_url, dl_dir)
                    file_size = os.path.getsize(path)
                    if file_size > 25 * 1024 * 1024:
                        await _send(bot, target_id, is_group, f"视频过大({file_size/1024/1024:.1f}MB)，请点击链接下载:\n{video_url}")
                    else:
                        await bot.send_message(
                            detail_type="group" if is_group else "private",
                            user_id=target_id if not is_group else None,
                            group_id=target_id if is_group else None,
                            message=MessageSegment("video", {"file_id": path}),
                        )
                        sent = True
                        logger.info(f"[DOUYIN] 视频已发送: {path} ({file_size/1024:.0f}KB)")
                except Exception as e:
                    logger.warning(f"[DOUYIN] 视频下载/发送失败: {e}")
            if not sent:
                await _send(bot, target_id, is_group, msg)
            else:
                # 视频已发出，补充文字信息（不含下载链接）
                stats_text = _format_douyin_text_info(data)
                if stats_text:
                    await _send(bot, target_id, is_group, stats_text)
        logger.info(f"[DOUYIN] 已解析: {url}")
    except Exception as e:
        logger.exception(f"[DOUYIN] 解析失败: {url}: {e}")
        await _send(bot, target_id, is_group, f"视频解析失败: {e}")
    return True


async def _download_image(url: str, dl_dir: Path) -> str:
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    out_path = str(dl_dir / f"dy_{url_hash}.jpg")
    if os.path.exists(out_path):
        return os.path.abspath(out_path)

    from PIL import Image
    import io

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        if img.mode in ("RGBA", "P", "LA", "PA"):
            img = img.convert("RGB")
        img.save(out_path, "JPEG", quality=90)
        return os.path.abspath(out_path)


async def _download_video(url: str, dl_dir: Path) -> str:
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    path = str(dl_dir / f"dy_{url_hash}.mp4")
    if os.path.exists(path):
        return os.path.abspath(path)
    headers = {
        "Referer": "https://www.douyin.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    douyin_cookie = os.getenv("DOUYIN_COOKIE", "")
    if douyin_cookie:
        headers["Cookie"] = douyin_cookie
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True, headers=headers) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=8192):
                    f.write(chunk)
    return os.path.abspath(path)


def _format_douyin_text_info(data: dict) -> str:
    """视频已发送时的紧凑文字信息"""

    def _fnum(n: int) -> str:
        if n >= 10000:
            return f"{n / 10000:.1f}万"
        return str(n)

    platform_names = {"douyin": "抖音", "tiktok": "TikTok", "bilibili": "B站"}
    platform = data.get("platform", "unknown")
    desc = data.get("desc", "") or ""
    if len(desc) > 80:
        desc = desc[:80] + "..."
    author = data.get("author", {})
    if platform == "bilibili":
        author_name = author.get("name", "未知")
    else:
        author_name = author.get("nickname", "未知")
    stats = data.get("statistics", {})
    parts = [f"{platform_names.get(platform, platform)} · {author_name}"]
    if desc:
        parts.append(desc)
    stat_parts = []
    if platform == "bilibili":
        v = stats.get("view"); l = stats.get("like")
        if v: stat_parts.append(f"播放{_fnum(v)}")
        if l: stat_parts.append(f"赞{_fnum(l)}")
    elif platform == "tiktok":
        p = stats.get("play_count"); d = stats.get("digg_count")
        if p: stat_parts.append(f"播放{_fnum(p)}")
        if d: stat_parts.append(f"赞{_fnum(d)}")
    else:
        d = stats.get("digg_count")
        if d: stat_parts.append(f"赞{_fnum(d)}")
    if stat_parts:
        parts.append(" · ".join(stat_parts))
    return "\n".join(parts)


def _is_search_intent(text: str) -> bool:
    """判断是否为联网搜索/科普/查询意图，这类请求绕过 Hermes 直接走本地搜索"""
    return detect_search_intent(text) is not None or _is_knowledge_request(text)


async def _handle_search_reply(
    bot: Bot, text: str, target_id: str, is_group: bool,
    ai_handler: "AIHandler",
):
    """联网搜索 → AI 直接总结，单条消息，不走完整 AI 管线（无上下文/无工具/无称呼）"""
    sc = get_search_client()
    if not sc:
        return False
    query = detect_search_intent(text)
    if not query:
        query = re.sub(r"^(?:科普|介绍一下|讲一下|说一下|解释一下)\s*", "", text).strip()
        query = re.sub(r"(?:是什么|是啥|啥意思|什么意思|玩法|怎么玩)$", "", query).strip()
        if not query or len(query) < 2:
            query = text
    try:
        results = await sc.search(query)
        if not results:
            return False
        ctx = sc.format_context(results)
        logger.info(f"[SEARCH] {query} → {len(results)} 条结果")
    except Exception as e:
        logger.warning(f"[SEARCH] 搜索失败: {e}")
        return False

    summary_prompt = (
        f"用户问: {text}\n\n{ctx}\n\n"
        "根据搜索结果直接回答，100字以内，纯文字，不要称呼不要客套不要生成图片。"
    )
    try:
        reply = await ai_handler.active_client.chat(
            message=summary_prompt,
            conversation_history=[],
            system_prompt="你是一个简洁的信息总结助手。只输出纯文字回答，禁止调用任何工具，禁止输出CALL:开头的内容。",
        )
    except Exception as e:
        logger.warning(f"[SEARCH] AI 总结失败: {e}")
        return False
    if not reply:
        return False
    msg = reply.strip()
    if msg.startswith("CALL:"):
        msg = msg.split("\n", 1)[-1].strip() if "\n" in msg else ""
    if not msg:
        return False
    await _send(bot, target_id, is_group, msg)
    buf = get_buffer()
    buf.append(BufferedMessage(
        ts=int(_time_mod.time()),
        group_id=target_id if is_group else None,
        user_id="bot",
        nickname="姜小妹",
        content=msg[:200],
        is_bot=True,
    ))
    return True


def _extract_quote_info(event: MessageEvent) -> dict | None:
    text = event.get_plaintext()
    if not text or not text.startswith('{'):
        return None
    nl = text.find('\n')
    if nl <= 0:
        return None
    try:
        import json as _json_mod
        meta = _json_mod.loads(text[:nl])
    except Exception as e:
        logger.warning(f"[QUOTE] JSON 解析失败: {e} | first_line={text[:nl][:120]}")
        return None
    qt = meta.get("qt", "")
    if qt == "text":
        return {
            "quote_type": "text",
            "text": meta.get("qtxt", ""),
            "quote_sender": meta.get("qs", ""),
            "quote_sender_wxid": meta.get("qsw", ""),
        }
    elif qt == "img":
        import os as _os
        image_path = meta.get("qp", "") or None
        if image_path and not _os.path.exists(image_path):
            logger.warning(f"[QUOTE] 引用图片路径不存在: {image_path}")
        logger.info(f"[QUOTE] 解析到引用图片: {image_path}")
        return {
            "quote_type": "image",
            "image_path": image_path,
            "quote_sender": meta.get("qs", ""),
            "quote_sender_wxid": meta.get("qsw", ""),
        }
    return None


def _strip_quote_meta(text: str) -> str:
    if text.startswith('{'):
        nl = text.find('\n')
        if nl > 0:
            text = text[nl + 1:].strip()
    if text.startswith('[引用 ') or text.startswith('[转发 '):
        nl = text.find(']\n')
        if nl > 0:
            text = text[nl + 2:].strip()
    return text


async def _send(bot: Bot, target_id: str, is_group: bool, message: str):
    await bot.send_message(
        detail_type="group" if is_group else "private",
        user_id=target_id if not is_group else None,
        group_id=target_id if is_group else None,
        message=message,
    )


async def _send_ai_reply(bot: Bot, target_id: str, is_group: bool, reply, ai_matcher=None):
    """发送 AI 回复，支持 str 和 ToolResult（含媒体）"""
    reply_text = ""
    if isinstance(reply, ToolResult):
        for path in reply.media:
            try:
                await bot.send_message(
                    detail_type="group" if is_group else "private",
                    user_id=target_id if not is_group else None,
                    group_id=target_id if is_group else None,
                    message=MessageSegment(reply.media_type, {"file_id": path}),
                )
            except Exception as e:
                logger.warning(f"[TOOL] 发送媒体失败: {e}")
        if reply.text:
            await _send(bot, target_id, is_group, reply.text)
            reply_text = reply.text
    elif reply:
        await _send(bot, target_id, is_group, reply)
        reply_text = reply

    # 记录 bot 回复到缓冲
    if reply_text and is_group:
        import time as _time
        get_buffer().append(BufferedMessage(
            ts=int(_time.time()),
            group_id=target_id,
            user_id="bot",
            nickname="姜小妹",
            content=reply_text[:200],
            is_bot=True,
        ))


INTERJECTION_TIME_WINDOW = 600  # 10分钟：上下文时间窗口
INTERJECTION_MIN_MESSAGES = 3   # 最少需要3条上下文消息才触发

# 流式输出条数：普通聊天2-3条，科普6条
_KNOWLEDGE_PATTERN = re.compile(r"科普|介绍一下|是什么|是啥|啥意思|什么意思|解释一下|讲一下|说一下")


def _is_knowledge_request(text: str) -> bool:
    return bool(_KNOWLEDGE_PATTERN.search(text))


def _max_send_lines(text: str = "") -> int:
    if _is_knowledge_request(text):
        return 6
    return 2 if random.random() < 0.7 else 3


async def _stream_hermes_reply(
    bot: Bot, target_id: str, is_group: bool, hermes_stream, max_lines: int,
):
    """消费 Hermes 流式输出，最多发 max_lines 条，超出部分丢弃。
    科普模式(max_lines=6)时，合并过短的碎片行，确保每条消息有实质内容。
    """
    is_knowledge = (max_lines == 6)
    lines: list[str] = []
    async for line in hermes_stream:
        lines.append(line)

    if is_knowledge:
        merged = _merge_short_lines(lines, min_len=15, target_count=6)
        logger.debug(f"[HERMES] 科普模式: 原始{len(lines)}行 → 合并后{len(merged)}行")
    else:
        merged = lines

    sent = 0
    for line in merged:
        if sent >= max_lines:
            break
        if sent > 0:
            await asyncio.sleep(2)
        await _send(bot, target_id, is_group, line)
        buf = get_buffer()
        buf.append(BufferedMessage(
            ts=int(_time_mod.time()),
            group_id=target_id if is_group else None,
            user_id="bot",
            nickname="姜小妹",
            content=line[:200],
            is_bot=True,
        ))
        sent += 1


def _merge_short_lines(lines: list[str], min_len: int = 15, target_count: int = 6) -> list[str]:
    """将过短的碎片行合并，使每条消息至少 min_len 字符，目标 target_count 条"""
    if not lines:
        return lines
    if len(lines) <= target_count and all(len(l) >= min_len for l in lines):
        return lines
    merged: list[str] = []
    buf = ""
    for line in lines:
        if buf:
            buf += line
        else:
            buf = line
        if len(buf) >= min_len:
            merged.append(buf)
            buf = ""
    if buf:
        if merged:
            merged[-1] += buf
        else:
            merged.append(buf)
    return merged[:target_count] if len(merged) > target_count else merged


async def _try_random_interjection(bot: Bot, group_id: str):
    """随机插话：从 buffer 取最近消息，过滤时间窗口，调用 Hermes 回复"""
    from .message_buffer import format_perception_context

    buf = get_buffer()
    recent = buf.get_recent(group_id, limit=20)
    if not recent:
        return

    # 过滤：从最旧开始剔除与最新消息时间差超过阈值的
    latest_ts = recent[-1].ts
    filtered = [m for m in recent if (latest_ts - m.ts) <= INTERJECTION_TIME_WINDOW]

    if len(filtered) < INTERJECTION_MIN_MESSAGES:
        return

    # 不回复 bot 自己的消息
    if filtered[-1].is_bot:
        return

    context_text = format_perception_context(filtered)
    prompt = (
        "你正在围观群聊，觉得有话想说。"
        "根据下面的聊天记录，自然地插一句话参与讨论。"
        "不要重复别人说过的，不要刻意总结，就像朋友之间随口接话。\n\n"
        f"{context_text}"
    )

    try:
        hermes = get_hermes_client()
        max_lines = _max_send_lines()
        await _stream_hermes_reply(
            bot, group_id, True,
            hermes.chat_stream(
                user_id="bot",
                text=prompt,
                chat_id=group_id,
                is_group=True,
            ),
            max_lines,
        )
        logger.info(f"[INTERJECTION] 随机插话成功 group={group_id}")
    except Exception as e:
        logger.debug(f"[INTERJECTION] 插话失败: {e}")


def _shorten_nickname(nickname: str, sex: int = 0) -> str:
    """缩短昵称：长昵称取末尾，加哥/姐称呼
    sex: 1=男 2=女 0=未知
    """
    suffix = "姐" if sex == 2 else "哥"

    if not nickname or len(nickname) <= 1:
        return nickname

    # 1-2个字：直接加称呼
    if len(nickname) <= 2:
        return nickname + suffix

    # 3个字以上：去掉数字/英文后缀，取最后2个字
    cleaned = re.sub(r"[0-9a-zA-Z_]+$", "", nickname).strip()
    if not cleaned:
        cleaned = nickname

    if len(cleaned) <= 2:
        return cleaned + suffix

    tail = cleaned[-2:]
    if tail[0] == tail[1]:
        return tail + suffix
    return tail + suffix


_sex_cache: dict[str, int] = {}  # "group_id:sender_id" → sex


async def _get_sender_sex(bot: Bot, group_id: str, sender_id: str) -> int:
    """从群成员列表获取性别，1=男 2=女 0=未知（带缓存）"""
    cache_key = f"{group_id}:{sender_id}"
    if cache_key in _sex_cache:
        return _sex_cache[cache_key]
    try:
        result = await bot.call_api("get_group_member_info", group_id=group_id, user_id=sender_id)
        if isinstance(result, dict):
            sex = result.get("sex", 0)
            _sex_cache[cache_key] = sex
            return sex
    except Exception as e:
        logger.debug(f"[NICKNAME] 获取群成员信息失败: {e}")
    return 0


def _is_forward_title(text: str) -> bool:
    """检查文本是否看起来像转发聊天记录的标题"""
    text = text.strip()
    if not text:
        return False
    markers = ["的聊天记录", "群聊的聊天记录"]
    return any(text == m or text.endswith(m) for m in markers)


def _extract_forward_from_xml(raw_msg: str) -> str | None:
    """从微信 raw_msg XML 中提取转发聊天记录的内容"""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw_msg)
    except ET.ParseError:
        return None

    appmsg = root.find(".//appmsg")
    if appmsg is None:
        return None

    title = (appmsg.findtext("title") or "").strip()
    desc = (appmsg.findtext("des") or "").strip()
    recorditem = appmsg.find(".//recorditem")
    if recorditem is None:
        return None

    lines = []
    dataitems = recorditem.find(".//dataitems")
    if dataitems is not None:
        for item in dataitems.findall("dataitem"):
            sender = (item.findtext("sourcename") or "").strip()
            content = (item.findtext("srcmsgcontent") or "").strip()
            if content:
                line = f"{sender}: {content}" if sender else content
                lines.append(line)

    if not lines and desc:
        lines.append(desc)

    if not lines:
        return None

    header = f"【转发聊天记录】" + (f" {title}" if title else "")
    return header + "\n" + "\n".join(lines)


def _extract_forward_content(event: MessageEvent) -> str | None:
    """从合并转发消息中提取实际聊天内容"""
    parts = []
    for seg in event.message:
        if seg.type != "forward":
            continue
        content = seg.data.get("content", [])
        if not content:
            fwd_id = seg.data.get("id", "")
            logger.info(f"[FORWARD] forward segment id={fwd_id}, no inline content")
            continue
        for node in content:
            sender = node.get("user_name", "") or node.get("user_id", "未知")
            node_texts = []
            for ns in node.get("content", []):
                ns_type = ns.get("type", "")
                if ns_type == "text":
                    t = ns.get("data", {}).get("text", "")
                    if t:
                        node_texts.append(t)
                elif ns_type == "image":
                    node_texts.append("[图片]")
                elif ns_type == "face":
                    node_texts.append("[表情]")
                elif ns_type == "video":
                    node_texts.append("[视频]")
                elif ns_type == "file":
                    node_texts.append("[文件]")
                elif ns_type == "audio":
                    node_texts.append("[语音]")
            if node_texts:
                line = "".join(node_texts)
                parts.append(f"{sender}: {line}")
    if not parts:
        return None
    return "【转发聊天记录】\n" + "\n".join(parts)


def register_handler(ai_matcher, config: Config, ai_handler: AIHandler):
    """注册消息处理器到 matcher"""

    @ai_matcher.handle()
    async def handle_message(bot: Bot, event: MessageEvent):
        sid = event.self.user_id if hasattr(event, 'self') else ""
        if sid and sid.startswith("wxid_") and sid != runtime.bot_wxid:
            runtime.bot_wxid = sid

        sender_id = event.user_id
        bot_self_id = event.self.user_id or runtime.bot_wxid
        # 获取发送者昵称并缩短
        raw_nickname = getattr(getattr(event, 'sender', None), 'nickname', None) or sender_id
        sender_sex = 0
        if not isinstance(event, PrivateMessageEvent) and hasattr(event, 'group_id'):
            sender_sex = await _get_sender_sex(bot, str(event.group_id), sender_id)
        sender_nickname = _shorten_nickname(raw_nickname, sender_sex)
        if bot_self_id and sender_id == bot_self_id:
            await ai_matcher.finish()

        text = event.get_plaintext().strip()
        if not text:
            await ai_matcher.finish()

        # 被动感知：所有消息写入缓冲（群聊）
        is_group_msg = not isinstance(event, PrivateMessageEvent)
        if is_group_msg and hasattr(event, 'group_id'):
            import time as _time
            get_buffer().append(BufferedMessage(
                ts=int(_time.time()),
                group_id=str(event.group_id),
                user_id=sender_id,
                nickname=raw_nickname,
                content=text[:200],
                is_bot=False,
            ))

        quote_info = _extract_quote_info(event)
        quoted_text = None
        quoted_image_path = None
        if quote_info:
            if quote_info.get("quote_type") == "text":
                quoted_text = quote_info.get("text", "")
            elif quote_info.get("quote_type") == "image":
                quoted_image_path = quote_info.get("image_path") or None
            text = _strip_quote_meta(text)

        # 合并转发聊天记录 — 提取实际消息内容
        forward_text = _extract_forward_content(event)
        if not forward_text:
            raw_msg = getattr(event, "raw_msg", None) or getattr(event, "raw_message", None)
            if raw_msg:
                forward_text = _extract_forward_from_xml(raw_msg)
                if forward_text:
                    logger.info(f"[FORWARD] 从 raw_msg XML 提取转发内容 ({len(forward_text)} 字符)")
        if forward_text:
            if text and not _is_forward_title(text):
                text = f"{forward_text}\n\n[用户对转发的评论]: {text}"
            else:
                text = forward_text
            logger.info(f"[FORWARD] 提取转发内容 ({len(forward_text)} 字符)")
        elif _is_forward_title(text) and not (quoted_text and "【转发聊天记录】" in str(quoted_text)):
            seg_types = [s.type for s in event.message]
            seg_details = [(s.type, dict(s.data)) for s in event.message]
            try:
                event_extra = {k: str(v)[:200] for k, v in event.dict().items()
                               if k not in ("message", "self", "time", "post_type")}
            except Exception:
                event_extra = {}
            logger.warning(
                f"[FORWARD] 疑似转发但无forward segment\n"
                f"  text={text[:200]}\n"
                f"  seg_types={seg_types}\n"
                f"  seg_details={seg_details}\n"
                f"  event_extra={event_extra}"
            )

        for prefix in ["请用米游社App扫码", "⚠️", "🎉", "❌", "✅", "🔔", "📢", "米游社账户"]:
            if text.startswith(prefix):
                await ai_matcher.finish()

        is_private = isinstance(event, PrivateMessageEvent)
        is_admin = config.is_admin(sender_id)
        is_group = not is_private
        target_id = str(event.group_id) if is_group and hasattr(event, 'group_id') else sender_id

        if is_admin and text.startswith("\\"):
            if text.startswith("\\语音 ") or text == "\\语音":
                if not is_enabled("tts"):
                    await ai_matcher.finish("语音合成功能已禁用")
                    return
                arg = text[3:].strip()
                if not arg:
                    await ai_matcher.finish(
                        f"用法: \\语音 <文本>\n"
                        f"当前音色: {get_current_voice()}\n"
                        f"可选: {', '.join(VOICE_OPTIONS.keys())}\n"
                        f"切换: \\切换音色 <名称>"
                    )
                    return
                await ai_matcher.send("正在生成语音...")
                silk_path = await tts_to_silk(arg)
                if not silk_path:
                    await ai_matcher.finish("语音生成失败")
                    return
                result = await send_voice_via_cdn(bot, target_id, silk_path)
                if not result["ok"]:
                    await ai_matcher.finish(f"语音发送失败: {result['msg']}")
                    return
                await ai_matcher.finish()
                return
            if text.startswith("\\切换音色"):
                arg = text[5:].strip()
                if not arg:
                    await ai_matcher.finish(
                        f"当前音色: {get_current_voice()}\n"
                        f"可选: {', '.join(VOICE_OPTIONS.keys())}"
                    )
                    return
                name = set_voice(arg)
                if name:
                    await ai_matcher.finish(f"已切换音色: {name}")
                else:
                    await ai_matcher.finish(f"未知音色: {arg}\n可选: {', '.join(VOICE_OPTIONS.keys())}")
                return
            if text.startswith("\\测试语音"):
                arg = text[len("#测试语音"):].strip()
                if not arg:
                    await ai_matcher.finish("用法: \\测试语音 [filehelper] [msg_type] <silk/slik文件路径>")
                    return

                send_to = target_id
                path_arg = arg
                first, _, rest = path_arg.partition(" ")
                if first.lower() == "filehelper":
                    send_to = "filehelper"
                    path_arg = rest.strip()

                msg_type = None
                first, _, rest = path_arg.partition(" ")
                if first.isdigit():
                    msg_type = int(first)
                    path_arg = rest.strip()

                voice_path = path_arg.strip().strip('"').strip("'")
                if not voice_path:
                    await ai_matcher.finish("缺少 silk/slik 文件路径")
                    return
                voice_file = Path(voice_path).expanduser()
                if not voice_file.exists():
                    await ai_matcher.finish(f"语音文件不存在: {voice_path}")
                    return

                params = {"to_wxid": send_to, "file": str(voice_file.resolve())}
                if msg_type is not None:
                    params["msg_type"] = msg_type
                try:
                    result = await bot.call_api("send_voice", **params)
                    logger.info(f"[VOICE TEST] send_voice result: {result}")
                except Exception as e:
                    logger.exception(f"[VOICE TEST] send_voice failed: {e}")
                    await ai_matcher.finish(f"语音探测失败: {e}")
                    return
                await ai_matcher.finish(
                    f"已发送语音探测请求 -> {send_to}\n"
                    f"文件: {voice_file.resolve()}\n"
                    f"type: {msg_type if msg_type is not None else 'WECHAT_VOICE_SEND_TYPE/11044'}\n"
                    "请确认微信里是否出现语音气泡。"
                )
                return
            if text.startswith("\\测试原始语音xml"):
                arg = text[len("#测试原始语音xml"):].strip()
                send_to = target_id
                content = "__last_voice__"
                if arg:
                    first, _, rest = arg.partition(" ")
                    if first.lower() == "filehelper":
                        send_to = "filehelper"
                        content = rest.strip() or "__last_voice__"
                    else:
                        content = arg
                try:
                    result = await bot.call_api("send_raw_xml", to_wxid=send_to, content=content)
                    logger.info(f"[RAW XML TEST] send_raw_xml result: {result}")
                except Exception as e:
                    logger.exception(f"[RAW XML TEST] send_raw_xml failed: {e}")
                    await ai_matcher.finish(f"原始 XML 探测失败: {e}")
                    return
                await ai_matcher.finish(
                    f"已发送原始 XML 探测请求 -> {send_to}\n"
                    f"content: {'最近一条语音 raw_msg' if content == '__last_voice__' else content[:120]}\n"
                    "请确认微信里是否出现语音气泡或不支持消息。"
                )
                return
            if text.startswith("\\转发文件助手"):
                count_str = text[7:].strip()
                count = int(count_str) if count_str.isdigit() else 2
                count = max(1, min(count, 20))
                sent = False
                err_msg = None
                try:
                    result = await bot.call_api("get_filehelper_messages", count=count)
                    if isinstance(result, dict):
                        msgs = result.get("data", [])
                    elif isinstance(result, list):
                        msgs = result
                    else:
                        msgs = []
                    if msgs:
                        title = "文件助手聊天记录"
                        fwd_result = await bot.call_api("send_forward_record",
                            to_wxid=target_id,
                            title=title,
                            messages=msgs,
                            forward_type=11044
                        )
                        logger.info(f"[FILEHELPER] send_forward_record result: {fwd_result}")
                        fwd_ok = isinstance(fwd_result, dict) and fwd_result.get("status") == "ok"
                        if fwd_ok:
                            sent = True
                        else:
                            err_msg = f"发送转发卡片失败 (type=11044)，可能需要确认 showdoc 中正确的 type 值"
                except Exception as e:
                    logger.warning(f"[FILEHELPER] 转发失败: {e}")
                    err_msg = f"转发失败: {e}"
                if sent:
                    await ai_matcher.finish()
                else:
                    await ai_matcher.finish(err_msg or "文件助手暂无缓存消息")
                return
            if text.startswith("\\切换图片模型"):
                target = text[7:].strip()
                if target:
                    gen_result = switch_image_model(target)
                    edit_result = switch_editor_model(target)
                    await ai_matcher.finish(f"{gen_result}\n{edit_result}")
                else:
                    await ai_matcher.finish(
                        f"当前文生图: {get_current_image_model()} | 图生图: {get_current_editor_model()}\n可用: minimax, gpt"
                    )
                return
            if text == "\\插件列表" or text == "\\插件":
                await ai_matcher.finish(list_plugins())
                return
            if text.startswith("\\启用"):
                arg = text[3:].strip()
                if not arg:
                    await ai_matcher.finish("用法: \\启用 <插件名>\n查看列表: \\插件列表")
                    return
                key = find_plugin_key(arg)
                if not key:
                    await ai_matcher.finish(f"未找到插件: {arg}\n查看列表: \\插件列表")
                    return
                result = enable_plugin(key)
                await ai_matcher.finish(result)
                return
            if text.startswith("\\禁用"):
                arg = text[3:].strip()
                if not arg:
                    await ai_matcher.finish("用法: \\禁用 <插件名>\n查看列表: \\插件列表")
                    return
                key = find_plugin_key(arg)
                if not key:
                    await ai_matcher.finish(f"未找到插件: {arg}\n查看列表: \\插件列表")
                    return
                result = disable_plugin(key)
                await ai_matcher.finish(result)
                return
            edit_prompt = detect_image_edit_intent(text)
            if edit_prompt and quoted_image_path:
                await _handle_image_edit(bot, edit_prompt, quoted_image_path, target_id, is_group)
                await ai_matcher.finish()
                return
            cmd_reply = ai_handler.handle_admin_command(text)
            if cmd_reply:
                await ai_matcher.finish(cmd_reply)
            else:
                clean_text = text
                douyin_url = extract_url(clean_text)
                if douyin_url and is_enabled("douyin"):
                    await _handle_douyin(bot, douyin_url, target_id, is_group)
                    await ai_matcher.finish()
                    return
                bili_uid = extract_bilibili_uid(clean_text)
                if bili_uid and is_enabled("douyin"):
                    await _handle_bilibili_space(bot, bili_uid, target_id, is_group)
                    await ai_matcher.finish()
                    return
                image_prompt = detect_image_intent(clean_text)
                if image_prompt:
                    if quoted_image_path and is_enabled("image_edit"):
                        # 管理员：引用图片 + 生成意图 → 图生图
                        await _handle_image_edit(bot, image_prompt, quoted_image_path, target_id, is_group)
                    elif is_enabled("image"):
                        await _handle_image(bot, image_prompt, target_id, is_group)
                    await ai_matcher.finish()
                    return
                # 引用图片 + 非生成/编辑意图 = 图片理解
                if quoted_image_path:
                    await _handle_image_understand(
                        bot, clean_text, quoted_image_path, target_id, is_group,
                    )
                    await ai_matcher.finish()
                    return
                video_prompt = detect_video_intent(clean_text)
                if video_prompt and is_enabled("video"):
                    await _handle_video(bot, video_prompt, target_id, is_group)
                    await ai_matcher.finish()
                    return
                if detect_kfc_intent(clean_text) and is_enabled("kfc"):
                    await _handle_kfc(bot, target_id, is_group)
                    await ai_matcher.finish()
                    return
                if _detect_news_intent(clean_text) and is_enabled("news"):
                    await _handle_news(bot, target_id, is_group)
                    await ai_matcher.finish()
                    return
                weather_city = _detect_weather_intent(clean_text)
                if weather_city and is_enabled("weather"):
                    await _handle_weather(bot, weather_city, target_id, is_group)
                    await ai_matcher.finish()
                    return
                if _detect_epic_intent(clean_text) and is_enabled("epic"):
                    await _handle_epic(bot, target_id, is_group)
                    await ai_matcher.finish()
                    return
                oil_province = _detect_oilprice_intent(clean_text)
                if oil_province is not None and is_enabled("oilprice"):
                    await _handle_oilprice(bot, oil_province, target_id, is_group)
                    await ai_matcher.finish()
                    return
                if _is_search_intent(clean_text) and is_enabled("ai_chat"):
                    handled = await _handle_search_reply(
                        bot, clean_text, target_id, is_group, ai_handler,
                    )
                    if handled:
                        await ai_matcher.finish()
                        return
                if is_enabled("ai_chat"):
                    hermes = get_hermes_client()
                    max_lines = _max_send_lines(clean_text)
                    await _stream_hermes_reply(
                        bot, target_id, is_group,
                        hermes.chat_stream(
                            user_id=sender_id, text=clean_text,
                            chat_id=target_id, is_group=is_group,
                            quoted_text=quoted_text,
                            force_long=_is_knowledge_request(clean_text),
                        ),
                        max_lines,
                    )
                    await ai_matcher.finish()
            return

        # 抖音/TikTok/B站链接解析 — 无需 @ 也可触发
        douyin_url = extract_url(text)
        if douyin_url and is_enabled("douyin"):
            await _handle_douyin(bot, douyin_url, target_id, is_group)
            await ai_matcher.finish()
            return

        # B站用户空间 — 获取最近投稿
        bili_uid = extract_bilibili_uid(text)
        if bili_uid and is_enabled("douyin"):
            await _handle_bilibili_space(bot, bili_uid, target_id, is_group)
            await ai_matcher.finish()
            return

        if is_group:
            if not getattr(event, 'to_me', False):
                # 手动检查是否被@ — 适配器可能未正确设置 to_me
                mentioned = False
                if bot_self_id:
                    for seg in event.message:
                        if seg.type == "mention" and seg.data.get("user_id") == bot_self_id:
                            mentioned = True
                            break
                if not mentioned:
                    # 随机插话：1% 概率主动回复群消息
                    if is_enabled("ai_chat") and random.random() < 0.01:
                        await _try_random_interjection(bot, str(event.group_id))
                    await ai_matcher.finish()

        clean_text = clean_at(text)
        if not clean_text.strip():
            # @bot 但没有消息内容，拉上下文进行语境回复
            if is_group and is_enabled("ai_chat"):
                buf = get_buffer()
                recent = buf.get_recent(target_id, limit=15)
                # 过滤时间窗口
                if recent:
                    latest_ts = recent[-1].ts
                    recent = [m for m in recent if (latest_ts - m.ts) <= INTERJECTION_TIME_WINDOW]
                if recent and len(recent) >= 2:
                    from .message_buffer import format_perception_context
                    context_text = format_perception_context(recent)
                    prompt = (
                        "有人在群里@了你但没说具体内容，根据最近的聊天记录，"
                        "自然地接话或回应最新话题。\n\n"
                        f"{context_text}"
                    )
                    hermes = get_hermes_client()
                    max_lines = _max_send_lines()
                    await _stream_hermes_reply(
                        bot, target_id, True,
                        hermes.chat_stream(
                            user_id=sender_id, text=prompt,
                            chat_id=target_id, is_group=True,
                        ),
                        max_lines,
                    )
            await ai_matcher.finish()

        edit_prompt2 = detect_image_edit_intent(clean_text)
        image_prompt = detect_image_intent(clean_text)

        # 引用图片 + 生成请求 = 图生图
        if quoted_image_path and image_prompt and not edit_prompt2:
            edit_prompt2 = image_prompt
            logger.info(f"[IMG_EDIT] 引用图片 + 生成意图 → 自动转为图生图: prompt={edit_prompt2}")

        if edit_prompt2:
            logger.info(f"[IMG_EDIT] 图生图: prompt={edit_prompt2}, image={quoted_image_path}")
            if quoted_image_path and is_enabled("image_edit"):
                await _handle_image_edit(bot, edit_prompt2, quoted_image_path, target_id, is_group)
                await ai_matcher.finish()
                return
            else:
                logger.warning(f"[IMG_EDIT] 无引用图片, 无法执行图生图")

        if image_prompt and not quoted_image_path and is_enabled("image"):
            await _handle_image(bot, image_prompt, target_id, is_group)
            await ai_matcher.finish()
            return

        # 引用图片 + 非生成/编辑意图 = 图片理解（看图说话）
        if quoted_image_path:
            await _handle_image_understand(
                bot, clean_text, quoted_image_path, target_id, is_group,
            )
            await ai_matcher.finish()
            return

        video_prompt = detect_video_intent(clean_text)
        if video_prompt and is_enabled("video"):
            await _handle_video(bot, video_prompt, target_id, is_group)
            await ai_matcher.finish()
            return

        if detect_kfc_intent(clean_text) and is_enabled("kfc"):
            await _handle_kfc(bot, target_id, is_group)
            await ai_matcher.finish()
            return

        if _detect_news_intent(clean_text) and is_enabled("news"):
            await _handle_news(bot, target_id, is_group)
            await ai_matcher.finish()
            return

        weather_city = _detect_weather_intent(clean_text)
        if weather_city and is_enabled("weather"):
            await _handle_weather(bot, weather_city, target_id, is_group)
            await ai_matcher.finish()
            return

        if _detect_epic_intent(clean_text) and is_enabled("epic"):
            await _handle_epic(bot, target_id, is_group)
            await ai_matcher.finish()
            return

        oil_province = _detect_oilprice_intent(clean_text)
        if oil_province is not None and is_enabled("oilprice"):
            await _handle_oilprice(bot, oil_province, target_id, is_group)
            await ai_matcher.finish()
            return

        if _is_search_intent(clean_text) and is_enabled("ai_chat"):
            handled = await _handle_search_reply(
                bot, clean_text, target_id, is_group, ai_handler,
            )
            if handled:
                await ai_matcher.finish()
                return

        if is_enabled("ai_chat"):
            hermes = get_hermes_client()
            max_lines = _max_send_lines(clean_text)
            await _stream_hermes_reply(
                bot, target_id, is_group,
                hermes.chat_stream(
                    user_id=sender_id, text=clean_text,
                    chat_id=target_id, is_group=is_group,
                    quoted_text=quoted_text,
                    force_long=_is_knowledge_request(clean_text),
                ),
                max_lines,
            )
            await ai_matcher.finish()
