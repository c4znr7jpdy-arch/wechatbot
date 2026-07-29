import os
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PY_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "wechat_bridge_quoted_image_cleanup",
    PY_DIR / "main.py",
)
wechat_bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wechat_bridge
SPEC.loader.exec_module(wechat_bridge)


class QuotedImageCleanupTests(unittest.TestCase):
    def test_only_removes_quoted_images_older_than_three_days(self):
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            expired = cache_dir / "quoted_expired.jpg"
            recent = cache_dir / "quoted_recent.jpg"
            unrelated = cache_dir / "keep.txt"

            expired.write_bytes(b"expired")
            recent.write_bytes(b"recent")
            unrelated.write_bytes(b"keep")

            old_mtime = (
                now - wechat_bridge.QUOTED_IMAGE_RETENTION_SECONDS - 1
            )
            recent_mtime = (
                now - wechat_bridge.QUOTED_IMAGE_RETENTION_SECONDS + 1
            )
            os.utime(expired, (old_mtime, old_mtime))
            os.utime(recent, (recent_mtime, recent_mtime))
            os.utime(unrelated, (old_mtime, old_mtime))

            removed, freed = wechat_bridge._cleanup_quoted_image_cache(
                cache_dir,
                now=now,
            )

            self.assertEqual(removed, 1)
            self.assertEqual(freed, len(b"expired"))
            self.assertFalse(expired.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(unrelated.exists())

    def test_missing_cache_directory_is_a_noop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing"

            self.assertEqual(
                wechat_bridge._cleanup_quoted_image_cache(missing),
                (0, 0),
            )


if __name__ == "__main__":
    unittest.main()
