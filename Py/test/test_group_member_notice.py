import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PY_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("wechat_bridge_group_notice", PY_DIR / "main.py")
wechat_bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wechat_bridge
SPEC.loader.exec_module(wechat_bridge)


class GroupMemberNoticeTests(unittest.TestCase):
    def setUp(self):
        self.original_cache = wechat_bridge._GROUP_MEMBER_CACHE
        self.original_cache_file = wechat_bridge._GROUP_MEMBER_CACHE_FILE
        self.temp_dir = tempfile.TemporaryDirectory()
        wechat_bridge._GROUP_MEMBER_CACHE = {}
        wechat_bridge._GROUP_MEMBER_CACHE_FILE = str(Path(self.temp_dir.name) / "group_members.json")

    def tearDown(self):
        wechat_bridge._GROUP_MEMBER_CACHE = self.original_cache
        wechat_bridge._GROUP_MEMBER_CACHE_FILE = self.original_cache_file
        self.temp_dir.cleanup()

    def test_full_snapshot_builds_identity_baseline(self):
        room = "53288922794@chatroom"
        target = "wxid_target"
        cached_room, count = wechat_bridge._cache_group_members(
            {
                "room_wxid": room,
                "member_list": [
                    {"wxid": target, "nickname": "一崝", "display_name": "一崝（姜玉杰的迷弟）"},
                    {"wxid": "wxid_other", "nickname": "其他人", "display_name": ""},
                ],
            }
        )

        self.assertEqual(cached_room, room)
        self.assertEqual(count, 2)
        self.assertTrue(wechat_bridge._has_member_identity(wechat_bridge._GROUP_MEMBER_CACHE[room][target]))
        current = wechat_bridge._merge_member_update(
            wechat_bridge._GROUP_MEMBER_CACHE[room][target],
            {"wxid": target, "nickname": "一崝", "display_name": "一崝（姜玉杰的迷弟）"},
        )
        self.assertEqual(
            wechat_bridge._member_identity(current),
            wechat_bridge._member_identity(wechat_bridge._GROUP_MEMBER_CACHE[room][target]),
        )

    def test_snapshot_without_avatar_preserves_resolved_avatar(self):
        room = "room@chatroom"
        target = "wxid_target"
        avatar = "https://wx.qlogo.cn/mmhead/example/0"
        wechat_bridge._GROUP_MEMBER_CACHE[room] = {
            target: {
                "nickname": "旧名称",
                "display_name": "旧群昵称",
                "avatar": avatar,
                "remark": "",
            },
            "wxid_departed": {"nickname": "已离群", "display_name": "", "avatar": "old", "remark": ""},
        }

        wechat_bridge._cache_group_members(
            {
                "room_wxid": room,
                "member_list": [
                    {"wxid": target, "nickname": "新名称", "display_name": "新群昵称"},
                ],
            }
        )

        self.assertEqual(wechat_bridge._GROUP_MEMBER_CACHE[room][target]["avatar"], avatar)
        self.assertNotIn("wxid_departed", wechat_bridge._GROUP_MEMBER_CACHE[room])

    def test_avatar_field_aliases_are_normalized(self):
        self.assertEqual(
            wechat_bridge._member_avatar({"head_img_url": "https://example.test/avatar.jpg"}),
            "https://example.test/avatar.jpg",
        )

    def test_empty_placeholder_is_not_a_change_baseline(self):
        self.assertFalse(wechat_bridge._has_member_identity({}))
        self.assertFalse(
            wechat_bridge._has_member_identity(
                {"nickname": "", "display_name": "", "avatar": "https://example.test/avatar.jpg"}
            )
        )

    def test_full_snapshot_detects_existing_member_display_name_change(self):
        previous = {
            "wxid_target": {
                "nickname": "账号名称",
                "display_name": "旧群昵称",
                "avatar": "https://example.test/old.jpg",
            },
            "wxid_same": {"nickname": "没改名", "display_name": ""},
        }
        current = {
            "wxid_target": {
                "nickname": "账号名称",
                "display_name": "新群昵称",
                "avatar": "https://example.test/new.jpg",
            },
            "wxid_same": {"nickname": "没改名", "display_name": ""},
            "wxid_new": {"nickname": "刚入群", "display_name": ""},
        }

        changes = wechat_bridge._group_member_identity_changes(previous, current)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0][0], "wxid_target")
        self.assertEqual(changes[0][1]["display_name"], "旧群昵称")
        self.assertEqual(changes[0][2]["display_name"], "新群昵称")

    def test_full_snapshot_ignores_avatar_only_change_and_new_member(self):
        previous = {
            "wxid_target": {
                "nickname": "账号名称",
                "display_name": "群昵称",
                "avatar": "https://example.test/old.jpg",
            }
        }
        current = {
            "wxid_target": {
                "nickname": "账号名称",
                "display_name": "群昵称",
                "avatar": "https://example.test/new.jpg",
            },
            "wxid_new": {"nickname": "刚入群", "display_name": ""},
        }

        self.assertEqual(
            wechat_bridge._group_member_identity_changes(previous, current),
            [],
        )

    def test_nickname_change_is_detected_when_display_name_is_empty(self):
        self.assertTrue(
            wechat_bridge._member_identity_changed(
                {"nickname": "旧名称", "display_name": ""},
                {"nickname": "新名称", "display_name": ""},
            )
        )

    def test_11200_full_snapshot_queues_changed_existing_member(self):
        room = "53288922794@chatroom"
        wechat_bridge._GROUP_MEMBER_CACHE[room] = {
            "wxid_target": {"nickname": "账号名称", "display_name": "旧群昵称", "avatar": ""},
            "wxid_same": {"nickname": "没改名", "display_name": "", "avatar": ""},
        }
        handler = object.__new__(wechat_bridge.WeChatServiceHandler)
        queued = []
        handler._queue_group_member_update_notice = lambda **kwargs: queued.append(kwargs) or True
        fake_ws = mock.Mock()
        fake_ws._bot_wxid = "wxid_bot"

        with mock.patch.object(
            wechat_bridge,
            "get_astrbot_ws_client",
            return_value=fake_ws,
        ):
            handler.on_receive(
                "test-client",
                11200,
                {
                    "data": {
                        "room_wxid": room,
                        "nickname": "测试群",
                        "member_list": [
                            {
                                "wxid": "wxid_target",
                                "nickname": "账号名称",
                                "display_name": "新群昵称",
                            },
                            {
                                "wxid": "wxid_same",
                                "nickname": "没改名",
                                "display_name": "",
                            },
                            {
                                "wxid": "wxid_new",
                                "nickname": "刚入群",
                                "display_name": "",
                            },
                        ],
                    }
                },
            )

        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["wxid"], "wxid_target")
        self.assertEqual(queued[0]["old_entry"]["display_name"], "旧群昵称")
        self.assertEqual(queued[0]["new_entry"]["display_name"], "新群昵称")
        self.assertEqual(queued[0]["source"], "11200 full snapshot diff")


if __name__ == "__main__":
    unittest.main()
