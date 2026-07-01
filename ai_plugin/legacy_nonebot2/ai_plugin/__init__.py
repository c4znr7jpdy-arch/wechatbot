"""
ai_plugin - NoneBot AI Chat Plugin
"""
import os
import sys
from pathlib import Path

lib_path = Path(__file__).parent.parent.parent / "lib"
if str(lib_path) not in sys.path:
    sys.path.insert(0, str(lib_path))

import nonebot
from nonebot import on_message, on_regex, require
from nonebot.adapters.onebot.v12 import MessageEvent
from nonebot.rule import Rule
from nonebot import logger

from .config import Config
from .handler import AIHandler
from .video_generator import init_video_generator
from .image_generator import init_image_generators, get_current_image_model
from .image_editor import init_image_editor
from .mcp_tools import init_search_client, init_vlm_client
from .router import register_handler
from .plugin_manager import is_enabled, enable_plugin, disable_plugin, list_plugins, find_plugin_key
from .mcp_history import start_mcp_server
from . import group_notice


import re as _re


def _is_prefixed_command(text: str) -> bool:
    """微信在@后可能插入 \\u2005 等空白，用正则匹配"""
    return bool(_re.match(r"^[\s -‏ - 　]*\\", text))


def not_command_rule() -> Rule:
    async def _not_command(event: MessageEvent) -> bool:
        return not _is_prefixed_command(event.get_plaintext().strip())
    return Rule(_not_command)


config = Config()
ai_handler = AIHandler(config)

if config.minimax_api_key:
    init_video_generator(
        api_key=config.minimax_api_key,
        base_url=config.minimax_api_base,
        model=os.getenv("MINIMAX_VIDEO_MODEL", "MiniMax-Hailuo-02"),
    )
    logger.info("[VIDEO] 视频生成器已初始化")
    init_image_generators(
        minimax_api_key=config.minimax_api_key,
        minimax_base_url=config.minimax_api_base,
        minimax_model=os.getenv("MINIMAX_IMAGE_MODEL", "image-01"),
        gpt_api_key=os.getenv("GPT_IMAGE_API_KEY", ""),
        gpt_base_url=os.getenv("GPT_IMAGE_BASE_URL", "http://freeapi.dgbmc.top"),
        gpt_model=os.getenv("GPT_IMAGE_MODEL", "gpt-image-2"),
    )
    logger.info(f"[IMAGE] 图片生成器已初始化 (当前: {get_current_image_model()})")
    init_search_client(
        api_key=config.minimax_api_key,
        base_url=config.minimax_api_base,
    )
    logger.info("[SEARCH] 联网搜索已就绪")
    init_vlm_client(
        api_key=config.minimax_api_key,
        base_url=config.minimax_api_base,
    )
    logger.info("[VLM] 图片理解已就绪")
    init_image_editor(
        gpt_api_key=os.getenv("GPT_IMAGE_API_KEY", ""),
        gpt_base_url=os.getenv("GPT_IMAGE_BASE_URL", "http://freeapi.dgbmc.top"),
        gpt_model=os.getenv("GPT_IMAGE_MODEL", "gpt-image-2"),
        minimax_api_key=config.minimax_api_key,
        minimax_base_url=config.minimax_api_base,
        minimax_model=os.getenv("MINIMAX_IMAGE_MODEL", "image-01"),
    )
    logger.info("[IMAGE EDITOR] 图生图编辑器已初始化")
else:
    logger.warning("[VIDEO] MINIMAX_API_KEY 未设置，视频/图片生成功能不可用")

ai_matcher = on_message(rule=not_command_rule())
register_handler(ai_matcher, config, ai_handler)

# 启动聊天记录 MCP Server（供 Hermes 调用）
start_mcp_server()

# 启动时预初始化 Hermes Client
from .hermes_client import get_hermes_client
get_hermes_client()

# /燕云 — 快速获取燕云十六声官方动态（分段发送：文本+图片）
from nonebot import on_command
from nonebot.adapters.onebot.v12 import MessageEvent as _ME, MessageSegment
from .bilibili_dynamic import fetch_dynamics, send_dynamics_list

yan_cmd = on_command("燕云", aliases={"燕云动态", "yanyun"})

@yan_cmd.handle()
async def handle_yanyun(bot, event: _ME):
    if not is_enabled("yanyun"):
        return
    is_group = hasattr(event, 'group_id')
    target_id = str(event.group_id) if is_group and hasattr(event, 'group_id') else event.user_id
    try:
        items = await fetch_dynamics("1567141152", count=3)
        await send_dynamics_list(bot, target_id, is_group, items, title="燕云十六声")
    except Exception as e:
        logger.exception(f"[BILI] /燕云 失败: {e}")
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=f"获取燕云动态失败: {e}",
        )

# /新闻 — 获取今日热点新闻
from .news import fetch_hot_news, format_news

news_cmd = on_command("新闻", aliases={"热点", "今日热点", "热榜", "抖音热榜", "news"})

@news_cmd.handle()
async def handle_news(bot, event: _ME):
    if not is_enabled("news"):
        return
    is_group = hasattr(event, 'group_id')
    target_id = str(event.group_id) if is_group and hasattr(event, 'group_id') else event.user_id
    try:
        items = await fetch_hot_news(20)
        msg = format_news(items)
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=msg,
        )
    except Exception as e:
        logger.exception(f"[NEWS] /新闻 失败: {e}")
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=f"获取新闻失败: {e}",
        )


# /xx天气 — 获取天气预报
from .weather import fetch_weather, format_weather

weather_re = on_regex(r"^\s*\\(?!定时)(.+?)天气\s*$")

@weather_re.handle()
async def handle_weather(bot, event: _ME):
    if not is_enabled("weather"):
        return
    import re
    text = event.get_plaintext().strip()
    m = re.match(r"^\s*\\(.+?)天气\s*$", text)
    if not m:
        await weather_re.finish()
    place = m.group(1).strip()
    if not place:
        await weather_re.finish("请指定城市名称，如: \\绵阳天气")

    is_group = hasattr(event, 'group_id')
    target_id = str(event.group_id) if is_group and hasattr(event, 'group_id') else event.user_id
    try:
        data = await fetch_weather(place)
        msg = format_weather(data)
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=msg,
        )
    except Exception as e:
        logger.exception(f"[WEATHER] /{place}天气 失败: {e}")
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=f"获取天气失败: {e}",
        )


# ── 定时任务热管理 ──────────────────────────────────────
from .scheduler_tasks import handle_schedule_command, load_all_tasks


# /epic — Epic 喜加一
from .epic import fetch_epic_free, format_epic_free

epic_cmd = on_command("epic", aliases={"喜加一", "epic喜加一", "免费游戏"})

@epic_cmd.handle()
async def handle_epic(bot, event: _ME):
    if not is_enabled("epic"):
        return
    is_group = hasattr(event, 'group_id')
    target_id = str(event.group_id) if is_group and hasattr(event, 'group_id') else event.user_id
    try:
        games = await fetch_epic_free()
        msg = format_epic_free(games)
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=msg,
        )
    except Exception as e:
        logger.exception(f"[EPIC] /epic 失败: {e}")
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=f"获取 Epic 免费游戏失败: {e}",
        )


# ── 定时任务热管理 ──────────────────────────────────────

# 启动时加载已保存的定时任务
load_all_tasks()

# 定时任务命令匹配器（/定时前缀，优先级高于 AI 聊天）
_schedule_matcher = on_command("定时", priority=10)


@_schedule_matcher.handle()
async def _handle_schedule(bot, event: _ME):
    if not is_enabled("schedule"):
        return
    sender_id = event.user_id
    if not config.is_admin(sender_id):
        await _schedule_matcher.finish("仅管理员可操作定时任务")

    text = event.get_plaintext().strip()
    reply = await handle_schedule_command(bot, event, text)
    if reply:
        await _schedule_matcher.finish(reply)


# B站动态订阅命令
_bili_sub_matcher = on_command("订阅动态", priority=10)
_bili_unsub_matcher = on_command("取消订阅动态", priority=10)
_bili_list_matcher = on_command("订阅列表", priority=10)


@_bili_sub_matcher.handle()
async def _handle_bili_sub(bot, event: _ME):
    if not is_enabled("bili_sub"):
        return
    sender_id = event.user_id
    if not config.is_admin(sender_id):
        await _bili_sub_matcher.finish("仅管理员可操作动态订阅")
    text = event.get_plaintext().strip()
    if not text.startswith("\\"):
        text = "\\订阅动态 " + text
    reply = await handle_schedule_command(bot, event, text)
    if reply:
        await _bili_sub_matcher.finish(reply)


@_bili_unsub_matcher.handle()
async def _handle_bili_unsub(bot, event: _ME):
    if not is_enabled("bili_sub"):
        return
    sender_id = event.user_id
    if not config.is_admin(sender_id):
        await _bili_unsub_matcher.finish("仅管理员可操作动态订阅")
    text = event.get_plaintext().strip()
    if not text.startswith("\\"):
        text = "\\取消订阅动态 " + text
    reply = await handle_schedule_command(bot, event, text)
    if reply:
        await _bili_unsub_matcher.finish(reply)


@_bili_list_matcher.handle()
async def _handle_bili_list(bot, event: _ME):
    if not is_enabled("bili_sub"):
        return
    text = "\\订阅列表"
    reply = await handle_schedule_command(bot, event, text)
    if reply:
        await _bili_list_matcher.finish(reply)


# /kfc — KFC疯狂星期四文案
from .kfc import fetch_kfc_text

kfc_cmd = on_command("kfc", aliases={"疯狂星期四", "肯德基"})

@kfc_cmd.handle()
async def handle_kfc(bot, event: _ME):
    if not is_enabled("kfc"):
        return
    is_group = hasattr(event, 'group_id')
    target_id = str(event.group_id) if is_group and hasattr(event, 'group_id') else event.user_id
    try:
        text = await fetch_kfc_text()
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=text,
        )
    except Exception as e:
        logger.exception(f"[KFC] /kfc 失败: {e}")
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message="V我50！",
        )


# /油价 — 获取今日油价
from .oilprice import fetch_oilprice, format_oilprice

oilprice_cmd = on_command("油价", aliases={"今日油价", "汽油价格"})

@oilprice_cmd.handle()
async def handle_oilprice(bot, event: _ME):
    if not is_enabled("oilprice"):
        return
    text = event.get_plaintext().strip()
    # 提取省份：/油价四川 或 /油价 四川
    import re as _oil_re
    m = _oil_re.match(r"^\s*\\?油价\s*(.+?)\s*$", text)
    province = m.group(1).strip() if m else ""
    if not province:
        # 尝试从 on_command 去掉前缀后的剩余文本获取
        province = text.lstrip("\\").replace("油价", "").replace("今日油价", "").replace("汽油价格", "").strip()
    if not province:
        await oilprice_cmd.finish("请指定省份，如: \\油价四川 或 \\油价 北京")

    is_group = hasattr(event, 'group_id')
    target_id = str(event.group_id) if is_group and hasattr(event, 'group_id') else event.user_id
    try:
        data = await fetch_oilprice(province)
        msg = format_oilprice(data)
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=msg,
        )
    except Exception as e:
        logger.exception(f"[OILPRICE] /油价 失败: {e}")
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=f"获取油价失败: {e}",
        )


# ── 语料库管理 ──────────────────────────────────────
from .style_corpus import crawl_comments, crawl_from_url_and_store

_corpus_cmd = on_command("爬评论", aliases={"crawl_comments"})

@_corpus_cmd.handle()
async def handle_corpus_crawl(bot, event: _ME):
    if not is_enabled("corpus"):
        return
    sender_id = event.user_id
    if not config.is_admin(sender_id):
        await _corpus_cmd.finish("仅管理员可操作")

    text = event.get_plaintext().strip()
    # 去掉命令前缀
    arg = _re.sub(r"^[\\/\s]*爬评论\s*", "", text).strip()
    if not arg:
        count = ai_handler.embedding_store.get_style_corpus_count()
        await _corpus_cmd.finish(f"当前语料库: {count} 条\n用法: \\爬评论 <抖音URL或视频ID> [数量]")

    # 解析参数: URL或aweme_id + 可选数量
    parts = arg.split()
    target = parts[0]
    num = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30

    is_group = hasattr(event, 'group_id')
    target_id = str(event.group_id) if is_group and hasattr(event, 'group_id') else sender_id

    try:
        if target.startswith("http"):
            stored = await crawl_from_url_and_store(ai_handler.embedding_store, target, num)
        else:
            comments = await crawl_comments(target, num)
            stored = await ai_handler.embedding_store.add_style_batch(comments) if comments else 0

        total = ai_handler.embedding_store.get_style_corpus_count()
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=f"爬取完成，新增 {stored} 条，语料库总计 {total} 条",
        )
    except Exception as e:
        logger.exception(f"[CORPUS] 爬评论失败: {e}")
        await bot.send_message(
            detail_type="group" if is_group else "private",
            user_id=target_id if not is_group else None,
            group_id=target_id if is_group else None,
            message=f"爬取失败: {e}",
        )


# /帮助 — 统一命令帮助（长图）
help_cmd = on_command("帮助", aliases={"help"})

HELP_DATA = {
    "常用命令": [
        ("\\新闻", "今日热点新闻"),
        ("\\城市天气", "如: \\绵阳天气"),
        ("\\油价省份", "如: \\油价四川"),
        ("\\epic", "Epic喜加一"),
        ("\\kfc", "疯狂星期四文案"),
        ("\\燕云", "燕云十六声动态"),
    ],
    "AI 联网搜索": [
        ("@姜小妹 科普/查一下/介绍一下", "自动联网搜索总结"),
        ("@姜小妹 提问", "自由提问，AI 对话回复"),
    ],
    "AI 创作（直接说就行）": [
        ("@姜小妹 帮我画xxx", "文生图"),
        ("@姜小妹 P图/改图/图生图", "编辑图片（需引用图片）"),
    ],
    "塔罗牌": [
        ("\\占卜", "抽四张塔罗牌占卜"),
        ("\\塔罗牌", "抽一张塔罗牌"),
    ],
    "洛克王国": [
        ("\\洛克", "查看洛克全部指令"),
        ("\\洛克档案 \\战绩 \\背包", "查询角色数据"),
        ("\\洛克阵容 \\查蛋 \\配种", "阵容与宠物"),
        ("\\洛克交换大厅 \\远行商人", "交易与商店"),
        ("\\订阅远行商人 \\家园菜园", "订阅通知"),
    ],
    "定时任务（管理员）": [
        ("\\定时新闻 8点", "每日推送热点"),
        ("\\定时天气 绵阳 8点", "每日天气"),
        ("\\定时kfc 周四 12点", "每周KFC文案"),
        ("\\定时列表 \\定时删除", "管理定时任务"),
    ],
    "B站订阅（管理员）": [
        ("\\订阅动态 UID", "订阅B站用户动态"),
        ("\\取消订阅动态 UID", "取消订阅"),
        ("\\订阅列表", "查看当前订阅"),
    ],
    "视频解析": [
        ("发送链接", "抖音/TikTok/B站链接自动解析"),
    ],
    "管理员指令（\\前缀）": [
        ("\\启用 \\禁用 插件名", "插件开关"),
        ("\\插件列表", "查看所有插件"),
        ("\\切换模型 \\图片模型", "切换AI后端"),
        ("\\语音 文字", "TTS语音合成"),
    ],
    "米游社": [
        ("\\米游社帮助", "查看米游社相关功能"),
    ],
}


_HELP_IMG_PATH = str(Path(__file__).parent / "help_card.png")

@help_cmd.handle()
async def handle_help(bot, event: _ME):
    is_group = hasattr(event, 'group_id')
    target_id = str(event.group_id) if is_group and hasattr(event, 'group_id') else event.user_id
    detail = "group" if is_group else "private"
    kwargs = {"group_id": target_id} if is_group else {"user_id": target_id}
    try:
        seg = MessageSegment("image", {"file_id": _HELP_IMG_PATH})
        await bot.send_message(detail_type=detail, message=seg, **kwargs)
    except Exception as e:
        logger.exception(f"[HELP] 发送帮助图片失败: {e}")
        await bot.send_message(detail_type=detail, message="帮助发送失败，请联系管理员", **kwargs)


__nonebot_plugin_name__ = "ai_plugin"
