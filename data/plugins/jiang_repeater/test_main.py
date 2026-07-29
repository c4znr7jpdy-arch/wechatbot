import asyncio
import unittest
from unittest import mock

import main as repeater


class FakeComponent:
    def __init__(self, component_type: str) -> None:
        self.type = component_type


def identity_prompt(wxid: str, nickname: str, text: str, newline: str = "\n") -> str:
    return (
        f"[系统身份提示：当前发言者 wxid={wxid}，昵称/群名：{nickname}。"
        f"当前发言者不是祈。]{newline}{text}"
    )


class FakeEvent:
    def __init__(
        self,
        *,
        text: str,
        uid: str,
        gid: str = "1@chatroom",
        self_id: str = "wxid_bot",
        component_types: tuple[str, ...] = ("Plain",),
        directed: bool = False,
    ) -> None:
        self.text = text
        self.uid = uid
        self.gid = gid
        self.self_id = self_id
        self.components = [FakeComponent(kind) for kind in component_types]
        self.is_at_or_wake_command = directed

    def get_message_str(self):
        return self.text

    def get_messages(self):
        return self.components

    def get_group_id(self):
        return self.gid

    def get_sender_id(self):
        return self.uid

    def get_self_id(self):
        return self.self_id

    def plain_result(self, text):
        return text


async def collect(plugin, event):
    return [item async for item in plugin.on_group_message(event)]


class RepeaterTests(unittest.TestCase):
    def setUp(self):
        repeater._group_history.clear()
        repeater._cooldown.clear()
        self.plugin = repeater.Main(None)

    def test_strips_identity_prompt_with_both_newline_styles(self):
        self.assertEqual(
            repeater._strip_system_identity_prompt(
                identity_prompt("wxid_a", "甲", "建强加油")
            ),
            "建强加油",
        )
        self.assertEqual(
            repeater._strip_system_identity_prompt(
                identity_prompt("wxid_a", "甲", "建强加油", "\r\n")
            ),
            "建强加油",
        )

    def test_two_users_same_text_trigger_repeat(self):
        first = FakeEvent(
            text=identity_prompt("wxid_a", "甲", "建强加油"), uid="wxid_a"
        )
        second = FakeEvent(
            text=identity_prompt("wxid_b", "乙", "建强加油"), uid="wxid_b"
        )

        self.assertEqual(asyncio.run(collect(self.plugin, first)), [])
        self.assertEqual(asyncio.run(collect(self.plugin, second)), ["建强加油"])

    def test_same_user_does_not_trigger(self):
        first = FakeEvent(text=identity_prompt("wxid_a", "甲", "复读"), uid="wxid_a")
        second = FakeEvent(text=identity_prompt("wxid_a", "甲", "复读"), uid="wxid_a")

        asyncio.run(collect(self.plugin, first))
        self.assertEqual(asyncio.run(collect(self.plugin, second)), [])

    def test_different_text_does_not_trigger(self):
        first = FakeEvent(text=identity_prompt("wxid_a", "甲", "第一句"), uid="wxid_a")
        second = FakeEvent(text=identity_prompt("wxid_b", "乙", "第二句"), uid="wxid_b")

        asyncio.run(collect(self.plugin, first))
        self.assertEqual(asyncio.run(collect(self.plugin, second)), [])

    def test_at_bot_requests_do_not_trigger_repeat(self):
        first = FakeEvent(
            text=identity_prompt("wxid_a", "甲", "@姜小妹 看看批"),
            uid="wxid_a",
            component_types=("At", "Plain"),
            directed=True,
        )
        second = FakeEvent(
            text=identity_prompt("wxid_b", "乙", "@姜小妹 看看批"),
            uid="wxid_b",
            component_types=("At", "Plain"),
            directed=True,
        )

        self.assertEqual(asyncio.run(collect(self.plugin, first)), [])
        self.assertEqual(asyncio.run(collect(self.plugin, second)), [])
        self.assertNotIn(first.gid, repeater._group_history)

    def test_quote_metadata_from_music_cards_does_not_trigger_repeat(self):
        card_text = (
            '{"qt": "text", "qs": "", "qsw": "", "qtxt": ""}\n'
            "[引用  的消息:「」]\n"
            "中文Dj激情喊麦 流行伤感MC骚麦派对 (DJ版)"
        )
        first = FakeEvent(text=card_text, uid="wxid_music_bot_a")
        second = FakeEvent(text=card_text, uid="wxid_music_bot_b")

        self.assertEqual(asyncio.run(collect(self.plugin, first)), [])
        self.assertEqual(asyncio.run(collect(self.plugin, second)), [])
        self.assertNotIn(first.gid, repeater._group_history)

    def test_commands_do_not_trigger_repeat(self):
        for command in ("/帮助", "\\天气 北京", "#状态", "点歌 骚麦"):
            with self.subTest(command=command):
                repeater._group_history.clear()
                first = FakeEvent(text=command, uid="wxid_a")
                second = FakeEvent(text=command, uid="wxid_b")
                self.assertEqual(asyncio.run(collect(self.plugin, first)), [])
                self.assertEqual(asyncio.run(collect(self.plugin, second)), [])

    def test_self_message_does_not_participate(self):
        own_message = FakeEvent(text="复读", uid="wxid_bot")
        user_message = FakeEvent(text="复读", uid="wxid_a")

        self.assertEqual(asyncio.run(collect(self.plugin, own_message)), [])
        self.assertEqual(asyncio.run(collect(self.plugin, user_message)), [])

    def test_non_repeatable_message_breaks_the_chain(self):
        first = FakeEvent(text="建强加油", uid="wxid_a")
        interrupted = FakeEvent(
            text="@姜小妹 帮我看看",
            uid="wxid_c",
            component_types=("At", "Plain"),
            directed=True,
        )
        second = FakeEvent(text="建强加油", uid="wxid_b")

        asyncio.run(collect(self.plugin, first))
        asyncio.run(collect(self.plugin, interrupted))
        self.assertEqual(asyncio.run(collect(self.plugin, second)), [])

    def test_messages_outside_repeat_window_do_not_trigger(self):
        first = FakeEvent(text="建强加油", uid="wxid_a")
        second = FakeEvent(text="建强加油", uid="wxid_b")

        with mock.patch.object(repeater, "time", side_effect=[100.0, 161.0]):
            self.assertEqual(asyncio.run(collect(self.plugin, first)), [])
            self.assertEqual(asyncio.run(collect(self.plugin, second)), [])


if __name__ == "__main__":
    unittest.main()
