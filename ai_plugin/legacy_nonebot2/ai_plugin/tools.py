"""
工具注册表 — AI 可调用的能力定义与执行调度
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from nonebot import logger


@dataclass
class ToolResult:
    """工具执行结果"""
    text: str = ""
    media: list = field(default_factory=list)  # 本地文件路径
    media_type: str = ""  # "image" | "video" | ""


# ── 工具定义 ──────────────────────────────────────────

TOOLS = {
    "weather": {
        "desc": "查询城市天气预报（3天）",
        "params": "城市名",
        "example": "CALL:weather:绵阳",
    },
    "news": {
        "desc": "获取今日热点新闻（抖音热榜Top20）",
        "params": "无",
        "example": "CALL:news",
    },
    "image": {
        "desc": "文生图，根据描述生成图片",
        "params": "图片描述,比例(可选如16:9)",
        "example": "CALL:image:一只猫,16:9",
    },
    "image_edit": {
        "desc": "图生图，基于用户引用的原图进行编辑修改",
        "params": "编辑描述",
        "example": "CALL:image_edit:改成赛博朋克风格",
    },
    "video": {
        "desc": "文生视频，根据描述生成短视频",
        "params": "视频描述",
        "example": "CALL:video:一只猫在草地上奔跑",
    },
    "search": {
        "desc": "联网搜索最新信息",
        "params": "搜索关键词",
        "example": "CALL:search:今天的股市行情",
    },
    "schedule_add": {
        "desc": "添加定时任务",
        "params": "类型(新闻/天气),时间(如8:00),城市(天气必填)",
        "example": "CALL:schedule_add:新闻,8:00",
    },
    "schedule_del": {
        "desc": "删除定时任务",
        "params": "类型(新闻/天气)",
        "example": "CALL:schedule_del:新闻",
    },
    "schedule_list": {
        "desc": "查看当前聊天的定时任务列表",
        "params": "无",
        "example": "CALL:schedule_list",
    },
    "epic": {
        "desc": "获取 Epic Games 本周免费游戏",
        "params": "无",
        "example": "CALL:epic",
    },
    "oilprice": {
        "desc": "查询今日油价（省份）",
        "params": "省份名",
        "example": "CALL:oilprice:四川",
    },
}

# 生成系统提示词片段
TOOL_PROMPT = """
【你可以调用的工具】
当你需要调用工具时，在回复中使用以下格式（单独一行）：
[CALL:工具名:参数]

可用工具：
- weather:城市名 → 查询3天天气预报
- news → 获取今日热点新闻
- image:描述,比例 → 文生图（比例可选，如16:9、9:16、1:1）
- image_edit:描述 → 图生图（需用户引用了图片）
- video:描述 → 生成短视频
- search:关键词 → 联网搜索最新信息
- schedule_add:类型,时间,城市 → 添加定时任务（类型：新闻/天气/epic/油价，天气需加城市，油价需加省份）
- schedule_del:类型 → 删除定时任务（类型：新闻/天气/epic/油价）
- schedule_list → 查看当前聊天的定时任务
- epic → 获取 Epic Games 本周免费游戏
- oilprice:省份名 → 查询今日油价（如四川、北京）

示例：
用户问"绵阳天气怎么样"，你回复：我帮你查一下~ [CALL:weather:绵阳]
用户问"最近有什么热点"，你回复：看看今天有啥~ [CALL:news]
用户说"帮我画一只猫"，你回复：好的~ [CALL:image:一只猫]
用户引用图片说"改成动漫风"，你回复：没问题~ [CALL:image_edit:动漫风格]
用户说"每天8点给我推新闻"，你回复：好的，已设置~ [CALL:schedule_add:新闻,8:00]
用户说"关掉定时新闻"，你回复：已取消~ [CALL:schedule_del:新闻]
用户说"看看定时任务"，你回复：[CALL:schedule_list]
用户问"Epic有什么免费游戏"，你回复：看看本周有啥~ [CALL:epic]
用户问"四川油价多少"，你回复：帮你查一下~ [CALL:oilprice:四川]
用户说"每天8点推油价"，你回复：好的~ [CALL:schedule_add:油价,8:00,四川]

注意：
- 工具调用标记必须独占一行
- 一次回复可以调用多个工具
- 不需要工具时正常聊天即可
- image_edit 需要用户引用了图片才能使用
- 定时任务自动绑定当前聊天（群或私聊）
"""


def parse_tool_calls(text: str) -> list[tuple[str, str]]:
    """从 AI 回复中解析工具调用标记，返回 [(name, args), ...]"""
    pattern = r"\[CALL:([a-z_]+)(?::([^\]]*))?\]"
    results = []
    for m in re.finditer(pattern, text):
        name = m.group(1)
        args = (m.group(2) or "").strip()
        if name in TOOLS:
            results.append((name, args))
    return results


def strip_tool_calls(text: str) -> str:
    """移除回复中的工具调用标记，保留其余文本"""
    return re.sub(r"\s*\[CALL:[a-z_]+(?::[^\]]*)?\]\s*", "", text).strip()


# ── 工具执行 ──────────────────────────────────────────


async def _exec_weather(args: str, context: dict) -> ToolResult:
    from .weather import fetch_weather, format_weather
    city = args.strip()
    if not city:
        return ToolResult(text="请告诉我城市名称~")
    try:
        data = await fetch_weather(city)
        return ToolResult(text=format_weather(data))
    except Exception as e:
        logger.exception(f"[TOOL] weather 失败: {e}")
        return ToolResult(text=f"天气查询失败: {e}")


async def _exec_news(args: str, context: dict) -> ToolResult:
    from .news import fetch_hot_news, format_news
    try:
        items = await fetch_hot_news(20)
        return ToolResult(text=format_news(items))
    except Exception as e:
        logger.exception(f"[TOOL] news 失败: {e}")
        return ToolResult(text=f"获取新闻失败: {e}")


async def _exec_image(args: str, context: dict) -> ToolResult:
    from .image_generator import get_image_generator, get_current_image_model
    from .router import _parse_aspect

    gen = get_image_generator()
    if not gen:
        return ToolResult(text="图片生成功能暂不可用")

    aspect, prompt = _parse_aspect(args)
    if not prompt:
        return ToolResult(text="请描述你想生成的图片~")

    model_name = get_current_image_model()
    try:
        paths = await gen.generate(prompt, n=1, aspect_ratio=aspect)
        return ToolResult(
            text=f"用 {model_name} 生成完毕 ({aspect})",
            media=paths,
            media_type="image",
        )
    except Exception as e:
        logger.exception(f"[TOOL] image 失败: {e}")
        return ToolResult(text=f"图片生成失败: {e}")


async def _exec_image_edit(args: str, context: dict) -> ToolResult:
    from .image_editor import get_image_editor, get_current_editor_model

    editor = get_image_editor()
    if not editor:
        return ToolResult(text="图生图功能暂不可用")

    image_path = context.get("quoted_image_path")
    if not image_path:
        return ToolResult(text="请先引用一张图片，再告诉我怎么修改~")

    prompt = args.strip()
    if not prompt:
        return ToolResult(text="请描述你想怎么修改这张图片~")

    model_name = get_current_editor_model()
    try:
        paths = await editor.edit(image_path, prompt, model=model_name)
        return ToolResult(
            text=f"用 {model_name} 编辑完毕",
            media=paths,
            media_type="image",
        )
    except Exception as e:
        logger.exception(f"[TOOL] image_edit 失败: {e}")
        return ToolResult(text=f"图片编辑失败: {e}")


async def _exec_video(args: str, context: dict) -> ToolResult:
    from .video_generator import get_video_generator

    vg = get_video_generator()
    if not vg:
        return ToolResult(text="视频生成功能暂不可用")

    prompt = args.strip()
    if not prompt:
        return ToolResult(text="请描述你想生成的视频内容~")

    try:
        path = await vg.generate(prompt)
        return ToolResult(
            text="视频生成完毕",
            media=[path],
            media_type="video",
        )
    except Exception as e:
        logger.exception(f"[TOOL] video 失败: {e}")
        return ToolResult(text=f"视频生成失败: {e}")


async def _exec_search(args: str, context: dict) -> ToolResult:
    from .mcp_tools import get_search_client

    sc = get_search_client()
    if not sc:
        return ToolResult(text="联网搜索功能暂不可用")

    query = args.strip()
    if not query:
        return ToolResult(text="请告诉我你想搜什么~")

    try:
        results = await sc.search(query)
        ctx = sc.format_context(results)
        return ToolResult(text=ctx)
    except Exception as e:
        logger.exception(f"[TOOL] search 失败: {e}")
        return ToolResult(text=f"搜索失败: {e}")


async def _exec_schedule_add(args: str, context: dict) -> ToolResult:
    from .scheduler_tasks import add_task
    from . import runtime

    target_id = context.get("target_id")
    detail_type = context.get("detail_type", "private")
    if not target_id:
        return ToolResult(text="无法获取当前聊天信息")

    parts = [p.strip() for p in args.split(",")]
    if not parts:
        return ToolResult(text="请指定类型和时间，如: 新闻,8:00")

    task_type_raw = parts[0]
    if task_type_raw in ("新闻", "热点"):
        task_type = "news"
    elif task_type_raw == "天气":
        task_type = "weather"
    elif task_type_raw in ("epic", "喜加一", "免费游戏"):
        task_type = "epic"
    elif task_type_raw == "油价":
        task_type = "oilprice"
    else:
        return ToolResult(text=f"不支持的类型: {task_type_raw}，可选: 新闻、天气、epic、油价")

    # 解析时间
    time_str = parts[1] if len(parts) > 1 else "8:00"
    time_match = re.match(r"(\d{1,2})[:\：](\d{2})", time_str)
    if not time_match:
        return ToolResult(text="时间格式错误，请用 8:00 格式")
    hour = max(0, min(int(time_match.group(1)), 23))
    minute = max(0, min(int(time_match.group(2)), 59))

    city = parts[2] if len(parts) > 2 else ""
    if task_type == "weather" and not city:
        return ToolResult(text="天气任务需要指定城市，如: 天气,8:00,绵阳")
    if task_type == "oilprice" and not city:
        return ToolResult(text="油价任务需要指定省份，如: 油价,8:00,四川")

    try:
        msg = add_task(target_id, detail_type, task_type, hour, minute, city)
        return ToolResult(text=f"✅ {msg}")
    except Exception as e:
        logger.exception(f"[TOOL] schedule_add 失败: {e}")
        return ToolResult(text=f"添加定时任务失败: {e}")


async def _exec_schedule_del(args: str, context: dict) -> ToolResult:
    from .scheduler_tasks import delete_task

    target_id = context.get("target_id")
    if not target_id:
        return ToolResult(text="无法获取当前聊天信息")

    task_type_raw = args.strip()
    if task_type_raw in ("新闻", "热点"):
        task_type = "news"
    elif task_type_raw == "天气":
        task_type = "weather"
    elif task_type_raw in ("epic", "喜加一", "免费游戏"):
        task_type = "epic"
    elif task_type_raw == "油价":
        task_type = "oilprice"
    else:
        return ToolResult(text=f"不支持的类型: {task_type_raw}，可选: 新闻、天气、epic、油价")

    try:
        if delete_task(target_id, task_type):
            return ToolResult(text="✅ 定时任务已删除")
        return ToolResult(text="未找到该类型的定时任务")
    except Exception as e:
        logger.exception(f"[TOOL] schedule_del 失败: {e}")
        return ToolResult(text=f"删除定时任务失败: {e}")


async def _exec_schedule_list(args: str, context: dict) -> ToolResult:
    from .scheduler_tasks import list_tasks

    target_id = context.get("target_id")
    if not target_id:
        return ToolResult(text="无法获取当前聊天信息")

    try:
        tasks = list_tasks(target_id)
        if not tasks:
            return ToolResult(text="当前聊天暂无定时任务")

        type_label = {"news": "热点新闻", "weather": "天气预报", "epic": "Epic喜加一"}
        lines = ["📋 定时任务列表:"]
        for t in tasks:
            label = type_label.get(t["type"], t["type"])
            city = t.get("extra", {}).get("city", "")
            time_str = f"{t['hour']:02d}:{t['minute']:02d}"
            suffix = f" ({city})" if city else ""
            lines.append(f"  • {label}{suffix}  每天 {time_str}")
        return ToolResult(text="\n".join(lines))
    except Exception as e:
        logger.exception(f"[TOOL] schedule_list 失败: {e}")
        return ToolResult(text=f"查看定时任务失败: {e}")


async def _exec_epic(args: str, context: dict) -> ToolResult:
    from .epic import fetch_epic_free, format_epic_free
    try:
        games = await fetch_epic_free()
        return ToolResult(text=format_epic_free(games))
    except Exception as e:
        logger.exception(f"[TOOL] epic 失败: {e}")
        return ToolResult(text=f"获取 Epic 免费游戏失败: {e}")


async def _exec_oilprice(args: str, context: dict) -> ToolResult:
    from .oilprice import fetch_oilprice, format_oilprice
    province = args.strip()
    if not province:
        return ToolResult(text="请告诉我省份名称，如: 四川、北京")
    try:
        data = await fetch_oilprice(province)
        return ToolResult(text=format_oilprice(data))
    except Exception as e:
        logger.exception(f"[TOOL] oilprice 失败: {e}")
        return ToolResult(text=f"获取油价失败: {e}")


_EXECUTORS = {
    "weather": _exec_weather,
    "news": _exec_news,
    "image": _exec_image,
    "image_edit": _exec_image_edit,
    "video": _exec_video,
    "search": _exec_search,
    "schedule_add": _exec_schedule_add,
    "schedule_del": _exec_schedule_del,
    "schedule_list": _exec_schedule_list,
    "epic": _exec_epic,
    "oilprice": _exec_oilprice,
}


async def execute_tool(name: str, args: str, context: dict = None) -> ToolResult:
    """执行指定工具，返回结果"""
    executor = _EXECUTORS.get(name)
    if not executor:
        return ToolResult(text=f"未知工具: {name}")
    return await executor(args, context or {})
