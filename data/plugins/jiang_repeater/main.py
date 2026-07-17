"""
复读机插件 — 群内连续两条相同消息自动复读
规则：
1. 上两条消息内容一致时触发
2. 若其中一条是机器人发的，不触发
3. 复读后冷却 2 分钟
"""
from collections import defaultdict
from time import time
import logging

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import EventMessageType

logger = logging.getLogger("jiang_repeater")

# 每个群的最近消息: [(user_id, text, is_self, timestamp), ...]
_group_history: dict[str, list[tuple[str, str, bool, float]]] = defaultdict(list)

# 每个群的冷却时间戳
_cooldown: dict[str, float] = {}

COOLDOWN_SECONDS = 120


def _strip_system_identity_prompt(text: str) -> str:
    """移除消息管线注入的身份提示，只保留用户实际发送的内容。"""
    cleaned = str(text or "").strip()
    marker = "[系统身份提示："
    start = cleaned.find(marker)
    if start < 0:
        return cleaned

    # 当前身份提示以 ] 后换行结束；兼容 Windows 和 Unix 换行。
    end_match = None
    for terminator in ("]\r\n", "]\n"):
        end = cleaned.find(terminator, start)
        if end >= 0 and (end_match is None or end < end_match[0]):
            end_match = (end, len(terminator))
    if end_match is None:
        return cleaned

    end, terminator_length = end_match
    return (cleaned[:start] + cleaned[end + terminator_length :]).strip()


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听所有群消息，检测复读条件"""
        text = _strip_system_identity_prompt(event.get_message_str())
        gid = event.get_group_id()
        uid = event.get_sender_id()
        now = time()

        if not text or len(text) > 500:
            return

        # 检查冷却
        if now - _cooldown.get(gid, 0) < COOLDOWN_SECONDS:
            return

        history = _group_history[gid]

        # 保持最近 2 条
        is_self = False  # AstrBot 中消息来自用户，不是 bot
        history.append((uid, text, is_self, now))
        if len(history) > 2:
            history.pop(0)

        if len(history) < 2:
            return

        (uid1, text1, self1, _), (uid2, text2, self2, _) = history

        # 两条消息内容相同
        if text1 != text2:
            return

        # 其中一条是机器人发的，不触发
        if self1 or self2:
            return

        # 同一用户连续发相同内容，不触发
        if uid1 == uid2:
            return

        # 触发复读
        _cooldown[gid] = time()
        _group_history[gid].clear()
        logger.info("群 %s 触发复读，发送者 %s -> %s，内容=%r", gid, uid1, uid2, text)
        yield event.plain_result(text)
