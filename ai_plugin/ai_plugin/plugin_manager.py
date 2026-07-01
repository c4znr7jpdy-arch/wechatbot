"""
插件热管理模块 — 管理员可通过命令动态启用/禁用功能模块
"""
import json
from pathlib import Path
from typing import Optional

from nonebot import logger

_STATE_FILE = Path(__file__).parent.parent / "data" / "plugin_state.json"

# 所有可管理的功能模块及其默认状态
PLUGIN_REGISTRY: dict[str, dict] = {
    "news": {"label": "热点新闻", "default": True},
    "weather": {"label": "天气预报", "default": True},
    "oilprice": {"label": "每日油价", "default": True},
    "epic": {"label": "Epic喜加一", "default": True},
    "kfc": {"label": "KFC文案", "default": True},
    "yanyun": {"label": "燕云动态", "default": True},
    "schedule": {"label": "定时任务", "default": True},
    "bili_sub": {"label": "B站订阅", "default": True},
    "corpus": {"label": "语料爬取", "default": True},
    "video": {"label": "视频生成", "default": True},
    "image": {"label": "图片生成", "default": True},
    "image_edit": {"label": "图片编辑", "default": True},
    "search": {"label": "联网搜索", "default": True},
    "tts": {"label": "语音合成", "default": True},
    "douyin": {"label": "抖音/B站解析", "default": True},
    "ai_chat": {"label": "AI聊天", "default": True},
    "mystool": {"label": "米游社工具", "default": True},
    "tarot": {"label": "塔罗牌", "default": True},
    "setu": {"label": "涩图", "default": True},
    "repeater": {"label": "复读机", "default": True},
    "rocom": {"label": "洛克王国", "default": True},
}

_state: dict[str, bool] = {}


def _load_state():
    global _state
    if _STATE_FILE.exists():
        try:
            _state = json.loads(_STATE_FILE.read_text("utf-8"))
        except Exception:
            _state = {}
    # 补全缺失的插件（新增插件自动启用）
    for key, info in PLUGIN_REGISTRY.items():
        if key not in _state:
            _state[key] = info["default"]


def _save_state():
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(_state, ensure_ascii=False, indent=2), "utf-8")


def is_enabled(plugin_key: str) -> bool:
    """检查指定功能是否启用"""
    if not _state:
        _load_state()
    return _state.get(plugin_key, True)


def enable_plugin(plugin_key: str) -> Optional[str]:
    """启用插件，返回确认消息"""
    if plugin_key not in PLUGIN_REGISTRY:
        return None
    if not _state:
        _load_state()
    _state[plugin_key] = True
    _save_state()
    label = PLUGIN_REGISTRY[plugin_key]["label"]
    logger.info(f"[PLUGIN] 启用: {plugin_key} ({label})")
    return f"已启用「{label}」"


def disable_plugin(plugin_key: str) -> Optional[str]:
    """禁用插件，返回确认消息"""
    if plugin_key not in PLUGIN_REGISTRY:
        return None
    if not _state:
        _load_state()
    _state[plugin_key] = False
    _save_state()
    label = PLUGIN_REGISTRY[plugin_key]["label"]
    logger.info(f"[PLUGIN] 禁用: {plugin_key} ({label})")
    return f"已禁用「{label}」"


def list_plugins() -> str:
    """列出所有插件及其状态"""
    if not _state:
        _load_state()
    lines = ["插件列表:"]
    for key, info in PLUGIN_REGISTRY.items():
        status = "ON" if _state.get(key, True) else "OFF"
        lines.append(f"  {status}  {info['label']} ({key})")
    lines.append("\n管理: #启用 <key> / #禁用 <key>")
    return "\n".join(lines)


def find_plugin_key(name: str) -> Optional[str]:
    """通过 key 或 label 模糊匹配插件"""
    name = name.strip().lower()
    # 精确匹配 key
    if name in PLUGIN_REGISTRY:
        return name
    # 精确匹配 label
    for key, info in PLUGIN_REGISTRY.items():
        if info["label"].lower() == name:
            return key
    # 模糊匹配
    for key, info in PLUGIN_REGISTRY.items():
        if name in key or name in info["label"].lower():
            return key
    return None


# 启动时加载状态
_load_state()
