import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)


PROJECT_DIR = Path(__file__).resolve().parents[2]


class AstrBotWeChatCompatTests(unittest.TestCase):
    def test_command_wake_prefixes_keep_slash_and_backslash(self):
        config = json.loads(
            (PROJECT_DIR / "data" / "cmd_config.json").read_text(
                encoding="utf-8-sig"
            )
        )

        self.assertIn("/", config["wake_prefix"])
        self.assertIn("\\", config["wake_prefix"])

    def test_group_send_keeps_wechat_chatroom_id_as_string(self):
        bot = AsyncMock()

        asyncio.run(
            AiocqhttpMessageEvent._dispatch_send(
                bot=bot,
                event=None,
                is_group=True,
                session_id="51632940287@chatroom",
                messages=[{"type": "text", "data": {"text": "ok"}}],
            )
        )

        bot.send_group_msg.assert_awaited_once_with(
            group_id="51632940287@chatroom",
            message=[{"type": "text", "data": {"text": "ok"}}],
        )

    def test_private_send_keeps_wechat_wxid_as_string(self):
        bot = AsyncMock()

        asyncio.run(
            AiocqhttpMessageEvent._dispatch_send(
                bot=bot,
                event=None,
                is_group=False,
                session_id="wxid_example",
                messages=[{"type": "text", "data": {"text": "ok"}}],
            )
        )

        bot.send_private_msg.assert_awaited_once_with(
            user_id="wxid_example",
            message=[{"type": "text", "data": {"text": "ok"}}],
        )


if __name__ == "__main__":
    unittest.main()
