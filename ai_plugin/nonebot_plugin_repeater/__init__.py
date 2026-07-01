"""
复读机插件 — 当群里上两条消息相同时自动复读
规则：
1. 上两条消息内容一致时触发
2. 若其中一条是机器人发的，不触发
3. 复读后冷却 2 分钟
"""
from collections import defaultdict
from time import time

from nonebot import on_message
from nonebot.adapters.onebot.v12 import Bot, GroupMessageEvent
from nonebot.rule import Rule

# 每个群的最近消息: [(user_id, text, is_self, timestamp), ...]
_group_history: dict[str, list[tuple[str, str, bool, float]]] = defaultdict(list)

# 每个群的冷却时间戳
_cooldown: dict[str, float] = {}

COOLDOWN_SECONDS = 120


def _repeater_rule() -> Rule:
    async def _check(bot: Bot, event: GroupMessageEvent) -> bool:
        gid = event.group_id
        text = event.get_plaintext().strip()
        uid = event.user_id
        is_self = (uid == event.self.user_id)
        now = time()

        if not text or len(text) > 500:
            return False

        # 检查冷却
        if now - _cooldown.get(gid, 0) < COOLDOWN_SECONDS:
            return False

        history = _group_history[gid]

        # 保持最近 2 条
        history.append((uid, text, is_self, now))
        if len(history) > 2:
            history.pop(0)

        if len(history) < 2:
            return False

        (uid1, text1, self1, _), (uid2, text2, self2, _) = history

        # 两条消息内容相同
        if text1 != text2:
            return False

        # 其中一条是机器人发的，不触发
        if self1 or self2:
            return False

        # 同一用户连续发相同内容，不触发
        if uid1 == uid2:
            return False

        return True

    return Rule(_check)


matcher = on_message(rule=_repeater_rule(), priority=1, block=False)


@matcher.handle()
async def handle(bot: Bot, event: GroupMessageEvent):
    gid = event.group_id
    text = event.get_plaintext().strip()

    _cooldown[gid] = time()
    _group_history[gid].clear()

    await matcher.finish(text)
