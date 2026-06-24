"""
基础命令插件 — 新闻、天气、Epic、KFC、油价、B站动态、帮助
直接复用 ai_plugin 下的现有模块，通过 sys.path 引入
"""
import sys
import os
import re
import logging
from pathlib import Path

# 加载 ai_plugin/.env.prod（ALapi token 等配置）
try:
    from dotenv import load_dotenv
    _env_file = str(Path(__file__).resolve().parent.parent.parent.parent / "ai_plugin" / ".env.prod")
    load_dotenv(_env_file, override=False)
except ImportError:
    pass

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter

logger = logging.getLogger("jiang_commands")

# NoneBot 桩（ai_plugin 依赖 nonebot）
_PLUGINS_DIR = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)
import _nonebot_stubs
_nonebot_stubs.setup()

# 将 ai_plugin 父目录加入 sys.path，复用现有模块
_AI_PLUGIN_PARENT = str(Path(__file__).resolve().parent.parent.parent.parent / "ai_plugin")
if _AI_PLUGIN_PARENT not in sys.path:
    sys.path.insert(0, _AI_PLUGIN_PARENT)

import ai_plugin.news as _news_mod
import ai_plugin.weather as _weather_mod
import ai_plugin.epic as _epic_mod
import ai_plugin.kfc as _kfc_mod
import ai_plugin.oilprice as _oilprice_mod
import ai_plugin.bilibili_dynamic as _bili_mod
import ai_plugin.help_card as _help_mod

fetch_hot_news = _news_mod.fetch_hot_news
format_news = _news_mod.format_news
fetch_weather = _weather_mod.fetch_weather
format_weather = _weather_mod.format_weather
fetch_epic_free = _epic_mod.fetch_epic_free
format_epic_free = _epic_mod.format_epic_free
fetch_kfc_text = _kfc_mod.fetch_kfc_text
fetch_oilprice = _oilprice_mod.fetch_oilprice
format_oilprice = _oilprice_mod.format_oilprice
fetch_dynamics = _bili_mod.fetch_dynamics
generate_help_card = _help_mod.generate_help_card

# 预生成帮助卡片路径
_HELP_IMG = str(Path(__file__).parent / "help_card.png")

# 帮助数据（与原 ai_plugin/__init__.py 一致）
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


def _ensure_help_image():
    """确保帮助卡片图片存在"""
    if not os.path.exists(_HELP_IMG):
        try:
            data = generate_help_card(HELP_DATA, bot_name="姜小妹")
            with open(_HELP_IMG, "wb") as f:
                f.write(data)
            logger.info("已生成帮助卡片图片")
        except Exception as e:
            logger.warning(f"生成帮助卡片失败: {e}")


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        _ensure_help_image()

    # ── /新闻 ──────────────────────────────────────────
    @filter.command("新闻")
    async def news(self, event: AstrMessageEvent):
        """今日热点新闻"""
        try:
            items = await fetch_hot_news(20)
            msg = format_news(items)
            yield event.plain_result(msg)
        except Exception as e:
            logger.exception(f"/新闻 失败: {e}")
            yield event.plain_result(f"获取新闻失败: {e}")

    # ── /xx天气 ─────────────────────────────────────────
    @filter.regex(r"^\s*(.+?)天气\s*$")
    async def weather(self, event: AstrMessageEvent):
        """城市天气预报 — 图片卡片"""
        text = event.get_message_str().strip()
        m = re.match(r"^\s*(.+?)天气\s*$", text)
        if not m:
            return
        place = m.group(1).strip()
        logger.info(f"[WEATHER] 原始 place: {place!r}")
        place = re.sub(r"^@.*?\s+", "", place).strip()
        if not place:
            yield event.plain_result("请指定城市名称，如: /绵阳天气")
            return
        try:
            from weather import fetch_weather_alapi, fetch_weather
            from weather_image import render_weather_image
            alapi_data = await fetch_weather_alapi(place)
            if not alapi_data:
                # alapi 失败，降级到纯文本
                data = await fetch_weather(place)
                msg = format_weather(data)
                yield event.plain_result(msg)
                return
            apihz_data = None
            try:
                apihz_data = await fetch_weather(place)
            except Exception:
                pass
            img_path = await render_weather_image(alapi_data, apihz_data)
            yield event.image_result(img_path)
        except Exception as e:
            logger.exception(f"/{place}天气 失败: {e}")
            yield event.plain_result(f"获取天气失败: {e}")

    # ── /epic ──────────────────────────────────────────
    @filter.command("epic")
    async def epic(self, event: AstrMessageEvent):
        """Epic 喜加一"""
        try:
            games = await fetch_epic_free()
            msg = format_epic_free(games)
            yield event.plain_result(msg)
        except Exception as e:
            logger.exception(f"/epic 失败: {e}")
            yield event.plain_result(f"获取 Epic 免费游戏失败: {e}")

    # ── /kfc ───────────────────────────────────────────
    @filter.command("kfc")
    async def kfc(self, event: AstrMessageEvent):
        """KFC 疯狂星期四文案"""
        try:
            text = await fetch_kfc_text()
            yield event.plain_result(text)
        except Exception as e:
            logger.exception(f"/kfc 失败: {e}")
            yield event.plain_result("V我50！")

    # ── /油价xx ────────────────────────────────────────
    @filter.regex(r"^\s*油价\s*(.+?)\s*$")
    async def oilprice(self, event: AstrMessageEvent):
        """今日油价"""
        text = event.get_message_str().strip()
        m = re.match(r"^\s*油价\s*(.+?)\s*$", text)
        if not m:
            return
        province = m.group(1).strip()
        if not province:
            yield event.plain_result("请指定省份，如: \\油价四川 或 \\油价 北京")
            return
        try:
            data = await fetch_oilprice(province)
            msg = format_oilprice(data)
            yield event.plain_result(msg)
        except Exception as e:
            logger.exception(f"/油价 失败: {e}")
            yield event.plain_result(f"获取油价失败: {e}")

    # ── /燕云 ──────────────────────────────────────────
    @filter.command("燕云")
    async def yanyun(self, event: AstrMessageEvent):
        """燕云十六声官方动态"""
        try:
            items = await fetch_dynamics("1567141152", count=3)
            if not items:
                yield event.plain_result("暂无燕云动态")
                return
            lines = ["燕云十六声最新动态："]
            for i, item in enumerate(items, 1):
                title = item.get("title") or item.get("text", "")[:50]
                author = item.get("author", "")
                ts = item.get("timestamp", "")
                if title:
                    lines.append(f"{i}. {title}")
                if author:
                    lines.append(f"   —— {author}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.exception(f"/燕云 失败: {e}")
            yield event.plain_result(f"获取燕云动态失败: {e}")

    # ── /帮助 ──────────────────────────────────────────
    @filter.command("帮助")
    async def help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        if os.path.exists(_HELP_IMG):
            yield event.image_result(_HELP_IMG)
        else:
            # 兜底：文本帮助
            lines = ["姜小妹命令指南："]
            for sect, items in HELP_DATA.items():
                lines.append(f"\n【{sect}】")
                for cmd, desc in items:
                    lines.append(f"  {cmd} — {desc}")
            yield event.plain_result("\n".join(lines))
