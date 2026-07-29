import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
PY_DIR = PROJECT_DIR / "Py"
SPEC = importlib.util.spec_from_file_location(
    "wechat_bridge_meme_emoji",
    PY_DIR / "main.py",
)
wechat_bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wechat_bridge
SPEC.loader.exec_module(wechat_bridge)

from astrbot.core.message.components import Image
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
    _is_meme_manager_image,
)


class MemeEmojiSendTests(unittest.TestCase):
    def test_bridge_recognizes_old_and_v4_meme_paths(self):
        checker = wechat_bridge.AstrBotWsClient._is_meme_manager_image_path

        self.assertTrue(
            checker(
                r"E:\Project\data\plugin_data\meme_manager\memes\happy\old.jpg"
            )
        )
        self.assertTrue(
            checker(
                r"E:\Project\data\plugin_data\meme_manager"
                r"\packs\legacy-migrated\memes\happy\new.jpg"
            )
        )
        self.assertFalse(
            checker(r"E:\Project\data\plugin_data\jiang_image\generated\image.jpg")
        )

    def test_aiocqhttp_preserves_v4_meme_path_and_marks_emoji(self):
        image = Image.fromFileSystem(
            PROJECT_DIR
            / "data"
            / "plugin_data"
            / "meme_manager"
            / "packs"
            / "legacy-migrated"
            / "memes"
            / "happy"
            / "example.jpg"
        )

        self.assertTrue(_is_meme_manager_image(image))
        payload = asyncio.run(AiocqhttpMessageEvent._from_segment_to_dict(image))

        self.assertEqual(payload["type"], "image")
        self.assertEqual(payload["data"]["sub_type"], "emoji")
        self.assertTrue(payload["data"]["file"].startswith("file:///"))

        client = wechat_bridge.AstrBotWsClient()
        text, image_path, sub_type = client._extract_text_and_image([payload])
        self.assertEqual(text, "")
        self.assertTrue(
            image_path.endswith(
                r"meme_manager\packs\legacy-migrated\memes\happy\example.jpg"
            )
        )
        self.assertTrue(client._is_emoji_sub_type(sub_type))

    def test_aiocqhttp_does_not_mark_other_plugin_images(self):
        image = Image.fromFileSystem(
            PROJECT_DIR
            / "data"
            / "plugin_data"
            / "jiang_image"
            / "generated"
            / "example.jpg"
        )

        self.assertFalse(_is_meme_manager_image(image))


if __name__ == "__main__":
    unittest.main()
