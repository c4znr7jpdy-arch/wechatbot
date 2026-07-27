from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGINS_DIR = Path(__file__).resolve().parent.parent
if str(PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGINS_DIR))

from astrbot.api.message_components import At, Video  # noqa: E402
from jiang_video_understanding.main import (  # noqa: E402
    Main,
    _adaptive_frame_count,
    _clean_question,
    _confirmed_context_hint,
    _known_context_hint,
    _needs_semantic_fallback,
)
from jiang_video_understanding.video_processor import VideoInfo  # noqa: E402


class FakeEvent:
    def __init__(self, messages):
        self.messages = messages
        self.unified_msg_origin = "aiocqhttp:GroupMessage:room@chatroom"
        self.sent = []
        self.stopped = False

    def get_messages(self):
        return self.messages

    @staticmethod
    def get_group_id():
        return "room@chatroom"

    @staticmethod
    def get_self_id():
        return "wxid_bot"

    @staticmethod
    def get_sender_id():
        return "wxid_user"

    @staticmethod
    def get_message_str():
        return "@姜小妹 这个视频说的什么意思"

    def stop_event(self):
        self.stopped = True

    @staticmethod
    def plain_result(text):
        return text

    async def send(self, message):
        self.sent.append(message)


class VideoTriggerTests(unittest.TestCase):
    def setUp(self):
        self.plugin = object.__new__(Main)

    def test_quoted_video_at_with_short_analysis_request_triggers(self):
        event = FakeEvent(
            [
                At(qq="wxid_bot"),
                Video(file="file:///tmp/video.mp4"),
            ]
        )
        self.assertTrue(self.plugin._is_explicit_trigger(event, "@姜小妹 分析一下"))

    def test_quoted_video_at_with_natural_question_triggers(self):
        event = FakeEvent(
            [
                At(qq="wxid_bot"),
                Video(file="file:///tmp/video.mp4"),
            ]
        )
        self.assertTrue(
            self.plugin._is_explicit_trigger(
                event,
                "@姜小妹 这个视频说的什么意思",
            )
        )

    def test_plain_group_video_stays_silent(self):
        event = FakeEvent([Video(file="file:///tmp/video.mp4")])
        self.assertFalse(self.plugin._is_explicit_trigger(event, ""))

    def test_video_command_does_not_require_at(self):
        event = FakeEvent([])
        self.assertTrue(self.plugin._is_explicit_trigger(event, "/总结视频"))

    def test_empty_mention_requests_context_and_meme_in_plain_language(self):
        question = _clean_question(
            "[系统身份提示：测试]\n@姜小妹 "
        )
        self.assertIn("人物", question)
        self.assertIn("战队", question)
        self.assertIn("梗点", question)
        self.assertIn("自然口语", question)

    def test_short_video_keeps_enough_frames_for_identity_clues(self):
        self.assertEqual(_adaptive_frame_count(9.5, 10), 6)
        self.assertEqual(_adaptive_frame_count(60, 10), 8)
        self.assertEqual(_adaptive_frame_count(300, 10), 10)

    def test_generic_visual_description_uses_semantic_fallback(self):
        self.assertTrue(
            _needs_semantic_fallback(
                "客厅里有两名年轻男子，一个人在操作摇杆。"
            )
        )
        self.assertFalse(
            _needs_semantic_fallback(
                "语境：LOL电竞\n"
                "人物身份：Bin、Zeus\n"
                "视频类型：网友恶搞二创\n"
                "梗点：把赛场对手剪成CP\n"
                "判断把握：高"
            )
        )

    def test_bin_zeus_context_distinguishes_rivals_from_cp_edit(self):
        hint = _known_context_hint("人物身份：BLG Bin 和 Zeus")
        self.assertIn("竞争对手", hint)
        self.assertIn("被 Zeus 击败", hint)
        self.assertIn("CP", hint)
        self.assertIn("网友恶搞", hint)

    def test_default_semantic_fallback_is_mimo_25(self):
        plugin = object.__new__(Main)
        plugin.config = {}
        self.assertEqual(
            plugin._fallback_provider_id(),
            "openai_2/mimo-v2.5",
        )

    def test_confirmed_context_is_scoped_to_exact_video_digest(self):
        hint = _confirmed_context_hint(
            "ec18c9104fe104af7a27166e107a7fc6f5bdb2f58429b3e025167c845d0a56fe"
        )
        self.assertIn("Bin", hint)
        self.assertIn("Zeus", hint)
        self.assertIn("竞争对手", hint)
        self.assertEqual(_confirmed_context_hint("unknown-digest"), "")

    def test_grok_vision_failure_switches_remaining_pipeline_to_mimo(self):
        async def run():
            class Response:
                def __init__(self, text):
                    self.completion_text = text

            class FakeContext:
                def __init__(self):
                    self.provider_calls = []

                async def llm_generate(self, **kwargs):
                    provider_id = kwargs["chat_provider_id"]
                    self.provider_calls.append(provider_id)
                    if provider_id == "jyjapi/grok-4.5":
                        raise RuntimeError("grok unavailable")
                    if kwargs.get("image_urls"):
                        return Response(
                            "语境：LOL电竞\n"
                            "人物身份：Bin、Zeus\n"
                            "真实关系/背景：赛场对手\n"
                            "视频类型：网友恶搞二创\n"
                            "关键互动：被剪成恩爱画面\n"
                            "梗点：对手反差\n"
                            "判断把握：高"
                        )
                    return Response("这是网友把两位赛场对手剪成CP感的恶搞视频。")

            context = FakeContext()
            plugin = object.__new__(Main)
            plugin.context = context
            plugin.config = {}
            event = FakeEvent([])

            with mock.patch.object(
                plugin,
                "_provider_id",
                return_value="jyjapi/grok-4.5",
            ):
                analysis, preferred = await plugin._describe_frames(
                    event,
                    [Path("frame.jpg")],
                    9.5,
                )

            result = await plugin._synthesize(
                event,
                VideoInfo(
                    duration=9.5,
                    size_bytes=100,
                    width=720,
                    height=1280,
                    has_audio=True,
                ),
                "这个视频什么意思",
                analysis,
                "",
                preferred_provider_id=preferred,
            )

            self.assertEqual(preferred, "openai_2/mimo-v2.5")
            self.assertIn("恶搞视频", result)
            self.assertEqual(
                context.provider_calls,
                [
                    "jyjapi/grok-4.5",
                    "openai_2/mimo-v2.5",
                    "openai_2/mimo-v2.5",
                ],
            )

        asyncio.run(run())

    def test_status_send_does_not_stop_video_processing(self):
        async def run():
            with tempfile.TemporaryDirectory() as temp_dir:
                video_path = Path(temp_dir) / "video.mp4"
                video_path.write_bytes(b"video")
                video = Video(file=str(video_path))
                event = FakeEvent([At(qq="wxid_bot"), video])
                plugin = object.__new__(Main)
                plugin.config = {}
                plugin._pending = {}
                plugin._jobs = asyncio.Semaphore(1)
                plugin._understand_video = mock.AsyncMock(return_value="解析完成")

                with mock.patch.object(
                    Video,
                    "convert_to_file_path",
                    mock.AsyncMock(return_value=str(video_path)),
                ):
                    replies = [reply async for reply in plugin.handle_video(event)]

                self.assertTrue(event.stopped)
                self.assertEqual(
                    event.sent,
                    ["正在看这个视频，通常需要 10～30 秒。"],
                )
                self.assertEqual(replies, ["解析完成"])
                plugin._understand_video.assert_awaited_once()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
