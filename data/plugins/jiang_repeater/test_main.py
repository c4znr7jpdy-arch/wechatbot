import asyncio
import unittest

import main as repeater


def identity_prompt(wxid: str, nickname: str, text: str, newline: str = "\n") -> str:
    return (
        f"[系统身份提示：当前发言者 wxid={wxid}，昵称/群名：{nickname}。"
        f"当前发言者不是祈。]{newline}{text}"
    )


class FakeEvent:
    def __init__(self, *, text: str, uid: str, gid: str = "1@chatroom") -> None:
        self.text = text
        self.uid = uid
        self.gid = gid

    def get_message_str(self):
        return self.text

    def get_group_id(self):
        return self.gid

    def get_sender_id(self):
        return self.uid

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


if __name__ == "__main__":
    unittest.main()

