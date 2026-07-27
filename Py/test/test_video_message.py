import asyncio
import html
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PY_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("wechat_bridge_video", PY_DIR / "main.py")
wechat_bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wechat_bridge
SPEC.loader.exec_module(wechat_bridge)


class VideoMessageTests(unittest.TestCase):
    def test_parse_video_cdn_info(self):
        raw = """<?xml version="1.0"?>
        <msg>
          <videomsg
            aeskey="normal-key"
            cdnvideourl="normal-file"
            cdnrawvideoaeskey="raw-key"
            cdnrawvideourl="raw-file"
            length="242567"
            rawlength="500000"
            playlength="61"
            md5="abc123" />
        </msg>
        """

        parsed = wechat_bridge.WeChatServiceHandler._parse_video_cdn_info(raw)

        self.assertEqual(parsed["aes_key"], "normal-key")
        self.assertEqual(parsed["file_id"], "normal-file")
        self.assertEqual(parsed["raw_aes_key"], "raw-key")
        self.assertEqual(parsed["raw_file_id"], "raw-file")
        self.assertEqual(parsed["length"], "242567")
        self.assertEqual(parsed["raw_length"], "500000")
        self.assertEqual(parsed["play_length"], "61")
        self.assertEqual(parsed["md5"], "abc123")

    def test_invalid_video_xml_returns_empty_mapping(self):
        parsed = wechat_bridge.WeChatServiceHandler._parse_video_cdn_info("<msg>")
        self.assertEqual(parsed, {})

    def test_parse_escaped_video_from_quoted_message(self):
        video_xml = (
            '<msg><videomsg aeskey="quoted-key" cdnvideourl="quoted-file" '
            'length="1234" playlength="60" /></msg>'
        )
        raw = (
            "<msg><appmsg><title>@姜小妹 分析视频</title><type>57</type>"
            "<refermsg><type>43</type><svrid>video-source-id</svrid>"
            f"<content>{html.escape(video_xml)}</content>"
            "<displayname>发送者</displayname><chatusr>wxid_sender</chatusr>"
            "</refermsg></appmsg></msg>"
        )
        handler = object.__new__(wechat_bridge.WeChatServiceHandler)

        quote = handler._parse_11061_xml(raw)
        parsed = handler._parse_video_cdn_info(quote["quote_content"])

        self.assertEqual(quote["quote_type"], 43)
        self.assertEqual(quote["quote_svrid"], "video-source-id")
        self.assertEqual(parsed["aes_key"], "quoted-key")
        self.assertEqual(parsed["file_id"], "quoted-file")
        self.assertEqual(parsed["play_length"], "60")

    def test_forward_quoted_video_keeps_at_video_and_question_together(self):
        handler = object.__new__(wechat_bridge.WeChatServiceHandler)
        captured = []

        class FakeWs:
            _bot_wxid = "wxid_bot"
            _loop = object()

            @staticmethod
            def is_connected():
                return True

            @staticmethod
            def _next_msg_id():
                return 99

            @staticmethod
            async def send_event(event):
                captured.append(event)

        class Completed:
            @staticmethod
            def result(timeout=None):
                return None

        def run_immediately(coro, loop):
            del loop
            asyncio.run(coro)
            return Completed()

        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "quoted.mp4"
            video.write_bytes(b"video")
            with (
                mock.patch.object(
                    wechat_bridge,
                    "get_astrbot_ws_client",
                    return_value=FakeWs(),
                ),
                mock.patch.object(
                    wechat_bridge.asyncio,
                    "run_coroutine_threadsafe",
                    side_effect=run_immediately,
                ),
            ):
                handler._forward_video_event(
                    {
                        "from_wxid": "wxid_sender",
                        "room_wxid": "room@chatroom",
                        "timestamp": 123,
                    },
                    {"length": "5", "play_length": "1"},
                    str(video),
                    source_msgid="original-video-id",
                    user_text="@姜小妹 分析视频",
                    mentioned_bot=True,
                    quoted=True,
                )

        self.assertEqual(len(captured), 1)
        event = captured[0]
        self.assertEqual([part["type"] for part in event["message"]], ["at", "video", "text"])
        self.assertEqual(event["message"][0]["data"]["qq"], "wxid_bot")
        self.assertIn("分析视频", event["message"][2]["data"]["text"])
        self.assertEqual(event["raw_video"]["msgid"], "original-video-id")
        self.assertTrue(event["raw_video"]["quoted"])


if __name__ == "__main__":
    unittest.main()
