import json
import tempfile
import unittest
from datetime import date, datetime

from PIL import Image

from .renderer import render_hourly_card
from .tide import (
    TZ,
    calculate_extremes,
    format_hourly_forecast,
    parse_beach_payload,
    parse_command,
    parse_hourly_payload,
)


class TideTests(unittest.TestCase):
    def test_parse_commands(self):
        self.assertEqual(parse_command("/潮汐").location, None)
        self.assertEqual(parse_command("/潮汐 青岛").location, None)
        self.assertEqual(parse_command("/青岛涨潮退潮时间").location, None)
        self.assertEqual(parse_command("\\明天潮汐 石老人").date_offset, 1)
        self.assertEqual(parse_command("/潮汐 金沙滩 明天").location, "金沙滩海水浴场（青岛）")
        self.assertEqual(
            parse_command("潮汐 金沙滩", allow_wake_normalized=True).location,
            "金沙滩海水浴场（青岛）",
        )
        self.assertIsNone(parse_command("今天天气不错"))

    def test_normal_coastal_chat_does_not_trigger_command(self):
        chat = (
            "行啊！那猪岛赶海计划就这么定了？露营、烤肉、现赶现烤，"
            "等退潮时候摸点小海鲜，晚上围着篝火烤着吃。"
        )
        self.assertIsNone(parse_command(chat))
        self.assertIsNone(parse_command("/赶海要等退潮吗"))
        self.assertIsNone(parse_command("潮汐 金沙滩 明天"))

    def test_unknown_beach_has_friendly_error(self):
        with self.assertRaisesRegex(ValueError, "暂不支持"):
            parse_command("/潮汐 栈桥")

    def test_parse_nonstandard_hourly_payload_and_extremes(self):
        rows = []
        heights = [100, 80, 60, 50, 70, 110, 150, 120, 90, 70, 65, 80,
                   110, 150, 190, 170, 130, 90, 60, 75, 110, 140, 125, 105]
        for hour, height in enumerate(heights):
            rows.append(
                f'{{"TIDETIME":\'{hour}\',"TIDEHEIGHT":\'{height}\','
                f'"TIDEDATE":\'2026/7/16 0:00:00\'}}'
            )
        points = parse_hourly_payload('{"tide": [' + ",".join(rows) + "]}")
        extremes = calculate_extremes(points, date(2026, 7, 16))
        self.assertEqual(len(points), 24)
        self.assertTrue(any(item.kind == "high" for item in extremes))
        self.assertTrue(any(item.kind == "low" for item in extremes))
        text = format_hourly_forecast(
            points, date(2026, 7, 16), datetime(2026, 7, 16, 12, 30, tzinfo=TZ)
        )
        self.assertIn("当前：涨潮中", text)
        self.assertIn("潮时为插值估算", text)

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = render_hourly_card(
                points,
                date(2026, 7, 16),
                datetime(2026, 7, 16, 12, 30, tzinfo=TZ),
                output_dir=temp_dir,
            )
            with Image.open(image_path) as card:
                self.assertEqual(card.width, 840)
                self.assertGreaterEqual(card.height, 620)
                self.assertEqual(card.mode, "RGB")

    def test_parse_beach_payload(self):
        payload = json.dumps(
            {
                "rows": [
                    {
                        "FORECASTDATE": "2026-07-17-00",
                        "FIRSTHIGHTIME": "0633",
                        "FIRSTHIGHLEVEL": "468",
                        "SECONDHIGHTIME": "1818",
                        "SECONDHEIGHTLEVEL": "439",
                        "FIRSTLOWTIME": "0046",
                        "FIRSTLOWLEVEL": "33",
                        "SECONDLOWTIME": "1312",
                        "SECONDLOWLEVEL": "151",
                        "SEABEACH": "石老人海水浴场",
                    }
                ]
            },
            ensure_ascii=False,
        )
        forecast = parse_beach_payload(payload)[0]
        self.assertEqual(forecast.forecast_date, date(2026, 7, 17))
        self.assertEqual(forecast.high_tides[0], ("06:33", 468))
        self.assertEqual(forecast.low_tides[1], ("13:12", 151))


if __name__ == "__main__":
    unittest.main()
