from __future__ import annotations

import unittest

from .search import (
    Episode,
    SearchResult,
    best_item,
    candidate_pool,
    extract_query,
    fuzzy_search_terms,
    known_title_hints,
    master_playlist_variant,
    normalize_title,
    parse_episodes,
    parse_episode_request,
    playlist_duration_seconds,
    select_episode,
    title_score,
)
from .watch_url import build_watch_url


class CommandMatchTests(unittest.TestCase):
    def test_requires_ascii_space_and_full_match(self):
        self.assertEqual(extract_query("短剧 闪婚"), "闪婚")
        self.assertEqual(extract_query("短剧   闪婚后   "), "闪婚后")
        self.assertIsNone(extract_query("短剧闪婚"))
        self.assertIsNone(extract_query("短剧　闪婚"))
        self.assertIsNone(extract_query("/短剧 闪婚"))
        self.assertIsNone(extract_query(" 短剧 闪婚"))

    def test_normalize_removes_quality_suffix(self):
        self.assertEqual(normalize_title("闪婚（全集高清）"), "闪婚")

    def test_optional_episode_request(self):
        self.assertEqual(
            parse_episode_request("可否许我再少年第12集"),
            ("可否许我再少年第12集", None),
        )
        self.assertEqual(
            parse_episode_request("可否许我再少年 第12集"),
            ("可否许我再少年", 12),
        )


class PlayUrlTests(unittest.TestCase):
    def test_selects_longest_hls_route(self):
        value = (
            "正片$https://example.com/video.mp4$$$"
            "第1集$https://cdn.example/1.m3u8#第2集$https://cdn.example/2.m3u8"
        )
        episodes = parse_episodes(value)
        self.assertEqual([item.name for item in episodes], ["第1集", "第2集"])
        self.assertEqual(episodes[0].url, "https://cdn.example/1.m3u8")

    def test_exact_title_beats_fuzzy_title(self):
        items = [
            {
                "vod_name": "闪婚老公请就位",
                "vod_play_url": "1$https://example.com/a.m3u8",
            },
            {
                "vod_name": "闪婚",
                "vod_play_url": "1$https://example.com/b.m3u8",
            },
        ]
        result = best_item("闪婚", items, "测试源")
        self.assertIsNotNone(result)
        self.assertEqual(result.title, "闪婚")

    def test_exact_pool_excludes_longer_fuzzy_sequel(self):
        exact = SearchResult(
            title="可否许我再少年",
            source="分集源",
            episodes=(Episode("第1集", "https://example.com/1.m3u8"),),
            score=10_000,
        )
        fuzzy = SearchResult(
            title="可否许我再少年2第二季",
            source="完整版源",
            episodes=(Episode("全集", "https://example.com/full.m3u8"),),
            score=6_920,
            duration_seconds=10_000,
        )
        pool, is_exact = candidate_pool([fuzzy, exact])
        self.assertTrue(is_exact)
        self.assertEqual(pool, [exact])

    def test_fuzzy_pool_is_used_only_without_exact_result(self):
        fuzzy = SearchResult(
            title="闪婚老公第二季",
            source="测试源",
            episodes=(Episode("全集", "https://example.com/full.m3u8"),),
            score=6_900,
        )
        weak = SearchResult(
            title="完全无关",
            source="测试源2",
            episodes=(Episode("全集", "https://example.com/other.m3u8"),),
            score=800,
        )
        pool, is_exact = candidate_pool([weak, fuzzy])
        self.assertFalse(is_exact)
        self.assertEqual(pool, [fuzzy])

    def test_one_character_typo_matches_equal_length_title_window(self):
        score = title_score(
            "全球杀机",
            "全球杀戮：开局觉醒SSS级天赋动态漫",
        )
        self.assertGreaterEqual(score, 3_000)

    def test_fuzzy_terms_use_alias_or_safe_shortening(self):
        self.assertEqual(fuzzy_search_terms("全球杀机"), ("全球杀戮",))
        self.assertEqual(
            fuzzy_search_terms("未知短剧"),
            ("未知短", "知短剧"),
        )
        self.assertEqual(fuzzy_search_terms("短词"), ())

    def test_known_title_hints_for_unavailable_resource(self):
        self.assertEqual(
            known_title_hints("腊肉"),
            ("致命的腊肉", "腊肉风云"),
        )

    def test_playlist_duration_and_master_variant(self):
        media = "#EXTM3U\n#EXTINF:5.5,\na.ts\n#EXTINF:4.5,\nb.ts\n"
        master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=128\n720/index.m3u8\n"
        self.assertEqual(playlist_duration_seconds(media), 10.0)
        self.assertEqual(master_playlist_variant(master), "720/index.m3u8")

    def test_selects_numbered_or_grouped_episode(self):
        grouped = SearchResult(
            title="测试短剧",
            source="测试源",
            episodes=(
                Episode("第1-20集", "https://example.com/1-20.m3u8"),
                Episode("第21-40集", "https://example.com/21-40.m3u8"),
            ),
        )
        self.assertEqual(select_episode(grouped, 12), grouped.episodes[0])
        self.assertEqual(select_episode(grouped, 35), grouped.episodes[1])
        self.assertIsNone(select_episode(grouped, 41))


class WatchUrlTests(unittest.TestCase):
    def setUp(self):
        self.result = SearchResult(
            title="闪婚",
            source="测试源",
            episodes=(
                Episode(
                    name="第1集",
                    url="https://hn.example.com/play/demo/index.m3u8",
                ),
            ),
        )

    def test_blank_config_uses_wechat_compatible_player(self):
        url = build_watch_url(self.result, "")
        self.assertEqual(
            url,
            "https://m3u8-player.cc/player?url="
            "https%3A%2F%2Fhn.example.com%2Fplay%2Fdemo%2Findex.m3u8",
        )

    def test_direct_mode_is_explicit(self):
        self.assertEqual(
            build_watch_url(self.result, "direct"),
            self.result.episodes[0].url,
        )

    def test_custom_template_receives_encoded_values(self):
        url = build_watch_url(
            self.result,
            "https://player.example/watch?src={url}&title={title}",
        )
        self.assertIn("src=https%3A%2F%2Fhn.example.com", url)
        self.assertIn("title=%E9%97%AA%E5%A9%9A", url)

    def test_selected_episode_is_used_in_player_url(self):
        second = Episode(
            name="第2集",
            url="https://hn.example.com/play/demo/2.m3u8",
        )
        result = SearchResult(
            title=self.result.title,
            source=self.result.source,
            episodes=self.result.episodes + (second,),
        )
        url = build_watch_url(result, "", second)
        self.assertIn("%2F2.m3u8", url)
        self.assertNotIn("index.m3u8", url)


if __name__ == "__main__":
    unittest.main()
