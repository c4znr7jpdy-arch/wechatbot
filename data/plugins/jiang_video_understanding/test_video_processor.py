from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGINS_DIR = Path(__file__).resolve().parent.parent
if str(PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGINS_DIR))

from jiang_video_understanding.video_processor import (  # noqa: E402
    extract_audio,
    extract_frames,
    frame_timestamps,
    probe_video,
)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 FFmpeg")
class VideoProcessorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp_dir.name)
        cls.video = cls.root / "sample.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x240:rate=15",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:sample_rate=16000",
                "-t",
                "2",
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                "-shortest",
                str(cls.video),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_uniform_timestamps_for_one_minute_video(self):
        timestamps = frame_timestamps(60, 10)
        self.assertEqual(len(timestamps), 10)
        self.assertEqual(timestamps[0], 0)
        self.assertEqual(timestamps[-1], 54)

    def test_probe_extract_frames_and_audio(self):
        async def exercise():
            info = await probe_video(self.video)
            frames, audio = await asyncio.gather(
                extract_frames(
                    self.video,
                    self.root / "frames",
                    duration=info.duration,
                    frame_count=4,
                    max_width=240,
                ),
                extract_audio(self.video, self.root / "audio.wav"),
            )
            return info, frames, audio

        info, frames, audio = asyncio.run(exercise())
        self.assertGreater(info.duration, 1.5)
        self.assertEqual((info.width, info.height), (320, 240))
        self.assertTrue(info.has_audio)
        self.assertGreaterEqual(len(frames), 2)
        self.assertTrue(all(frame.stat().st_size > 0 for frame in frames))
        self.assertGreater(audio.stat().st_size, 44)


if __name__ == "__main__":
    unittest.main()
