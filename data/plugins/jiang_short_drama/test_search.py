from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from .main import (
    Main,
    _compact_play_card_text,
    _compact_variants_card_text,
    _first_recommendation_cover,
    _media_help_text,
)
from .search import (
    CollectionPage,
    Episode,
    SearchResult,
    best_item,
    candidate_pool,
    extract_media_query,
    extract_query,
    extract_sports_query,
    fuzzy_search_terms,
    is_exact_sports_replay_result,
    is_media_category,
    is_media_help_command,
    is_recommendation_command,
    is_selectable_variant,
    is_sports_category,
    is_sports_replay_item,
    known_title_hints,
    master_playlist_variant,
    matching_items,
    normalize_title,
    parse_episodes,
    parse_episode_request,
    parse_sports_collection_query,
    parse_variant_request,
    playlist_duration_seconds,
    recommendation_candidates,
    select_episode,
    title_score,
    variant_identity,
)
from .player_server import (
    _COLLECTION_JAVASCRIPT,
    _PLAYER_JAVASCRIPT,
    _collection_html,
    _player_html,
    _recommendation_html,
    _variant_html,
    CollectionStore,
    EpisodePlaylistStore,
    ShortDramaPlayerServer,
    VariantRecord,
    VariantStore,
    validate_public_base_url,
)
from .watch_url import build_watch_url
from .schedule_store import (
    RecommendationScheduleStore,
    ScheduleConfigError,
    task_cron_expression,
    validate_task,
)


class CommandMatchTests(unittest.TestCase):
    def test_recommendation_cover_uses_first_available_item(self):
        first = SearchResult(
            title="第一部",
            source="测试源",
            episodes=(Episode("正片", "https://cdn.example/first.m3u8"),),
        )
        second = SearchResult(
            title="第二部",
            source="测试源",
            cover_url="https://img.example/second.jpg",
            episodes=(Episode("正片", "https://cdn.example/second.m3u8"),),
        )
        self.assertEqual(
            _first_recommendation_cover((first, second)),
            second.cover_url,
        )

    def test_recommendation_cover_prefers_first_item_when_present(self):
        first = SearchResult(
            title="第一部",
            source="测试源",
            cover_url="https://img.example/first.webp",
            episodes=(Episode("正片", "https://cdn.example/first.m3u8"),),
        )
        second = SearchResult(
            title="第二部",
            source="测试源",
            cover_url="https://img.example/second.jpg",
            episodes=(Episode("正片", "https://cdn.example/second.m3u8"),),
        )
        self.assertEqual(
            _first_recommendation_cover((first, second)),
            first.cover_url,
        )

    def test_recommendation_cover_skips_blank_values(self):
        first = SearchResult(
            title="第一部",
            source="测试源",
            cover_url="   ",
            episodes=(Episode("正片", "https://cdn.example/first.m3u8"),),
        )
        second = SearchResult(
            title="第二部",
            source="测试源",
            cover_url="https://img.example/second.jpg",
            episodes=(Episode("正片", "https://cdn.example/second.m3u8"),),
        )
        self.assertEqual(
            _first_recommendation_cover((first, second)),
            "https://img.example/second.jpg",
        )

    def test_recommendation_cover_falls_back_to_default_when_none_present(self):
        first = SearchResult(
            title="第一部",
            source="测试源",
            episodes=(Episode("正片", "https://cdn.example/first.m3u8"),),
        )
        second = SearchResult(
            title="第二部",
            source="测试源",
            episodes=(Episode("正片", "https://cdn.example/second.m3u8"),),
        )
        self.assertEqual(
            _first_recommendation_cover((first, second)),
            "https://duanjubaike.cn/favicon.ico",
        )

    def test_requires_ascii_space_and_full_match(self):
        self.assertEqual(extract_query("短剧 闪婚"), "闪婚")
        self.assertEqual(extract_query("短剧   闪婚后   "), "闪婚后")
        self.assertIsNone(extract_query("短剧闪婚"))
        self.assertIsNone(extract_query("短剧　闪婚"))
        self.assertIsNone(extract_query("/短剧 闪婚"))
        self.assertIsNone(extract_query(" 短剧 闪婚"))

    def test_media_commands_require_ascii_space_and_return_type(self):
        self.assertEqual(extract_media_query("短剧 闪婚"), ("短剧", "闪婚"))
        self.assertEqual(
            extract_media_query("电视剧   三国演义   "),
            ("电视剧", "三国演义"),
        )
        self.assertEqual(extract_media_query("电影 红楼梦"), ("电影", "红楼梦"))
        self.assertEqual(extract_media_query("动漫 海贼王"), ("动漫", "海贼王"))
        self.assertEqual(extract_media_query("综艺 奔跑吧"), ("综艺", "奔跑吧"))
        self.assertEqual(extract_media_query("剧 三国演义"), ("剧", "三国演义"))
        self.assertIsNone(extract_media_query("电视剧"))
        self.assertIsNone(extract_media_query("电影流浪地球"))
        self.assertIsNone(extract_media_query("动漫　海贼王"))
        self.assertIsNone(extract_media_query("/综艺 奔跑吧"))

    def test_media_help_requires_exact_command(self):
        self.assertTrue(is_media_help_command("剧"))
        self.assertFalse(is_media_help_command("剧 "))
        self.assertFalse(is_media_help_command("/剧"))
        help_text = _media_help_text()
        for command in ("短剧", "电视剧", "电影", "动漫", "综艺", "体育", "剧 名称"):
            self.assertIn(command, help_text)
        for section in ("🔍 分类搜索", "🏟️ 体育回放", "✨ 最新推荐", "⚙️ 选集与版本"):
            self.assertIn(section, help_text)
        self.assertIn("最新12部", help_text)

    def test_sports_command_requires_query_and_ascii_space(self):
        self.assertEqual(extract_sports_query("体育 斯诺克"), "斯诺克")
        self.assertEqual(
            extract_sports_query("体育   CBA2023   "),
            "CBA2023",
        )
        self.assertIsNone(extract_sports_query("体育"))
        self.assertIsNone(extract_sports_query("体育 "))
        self.assertIsNone(extract_sports_query("体育足球"))
        self.assertIsNone(extract_sports_query("体育　足球"))
        self.assertIsNone(extract_sports_query("/体育 足球"))

    def test_recommendation_command_requires_exact_full_match(self):
        self.assertTrue(is_recommendation_command("短剧推荐"))
        self.assertFalse(is_recommendation_command("短剧推荐 "))
        self.assertFalse(is_recommendation_command(" 短剧推荐"))
        self.assertFalse(is_recommendation_command("/短剧推荐"))
        self.assertFalse(is_recommendation_command("短剧 推荐"))

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

    def test_variant_request_requires_separate_version_suffix(self):
        self.assertEqual(parse_variant_request("三国演义 版本"), ("三国演义", None))
        self.assertEqual(parse_variant_request("三国演义 版本2"), ("三国演义", 2))
        self.assertEqual(parse_variant_request("三国演义 版本 3"), ("三国演义", 3))
        self.assertIsNone(parse_variant_request("三国演义版本2"))
        self.assertIsNone(parse_variant_request("三国演义 版本0"))

    def test_sports_collection_query_accepts_league_and_year(self):
        self.assertEqual(parse_sports_collection_query("CBA2023"), ("CBA", "2023"))
        self.assertEqual(parse_sports_collection_query("nba 2024"), ("NBA", "2024"))
        self.assertEqual(parse_sports_collection_query("英超2025"), ("英超", "2025"))
        self.assertEqual(parse_sports_collection_query("CBA"), ("CBA", None))
        self.assertEqual(parse_sports_collection_query("斯诺克"), ("斯诺克", None))
        self.assertEqual(parse_sports_collection_query("足球2024"), ("足球", "2024"))
        self.assertIsNone(parse_sports_collection_query("CBA20235"))

    def test_sports_collection_filters_same_name_movies(self):
        self.assertTrue(
            is_sports_replay_item(
                "斯诺克",
                None,
                {"vod_name": "2024斯诺克大师赛", "type_name": "台球"},
            )
        )
        self.assertFalse(
            is_sports_replay_item(
                "斯诺克",
                None,
                {"vod_name": "我爱斯诺克", "type_name": "剧情片"},
            )
        )
        self.assertTrue(
            is_sports_replay_item(
                "足球",
                "2024",
                {"vod_name": "足球热身赛 A队vsB队20240811", "type_name": "足球"},
            )
        )
        self.assertFalse(
            is_sports_replay_item(
                "足球",
                None,
                {"vod_name": "足球爸爸", "type_name": "喜剧片"},
            )
        )

    def test_sports_category_excludes_non_sports_media(self):
        self.assertTrue(is_sports_category("足球"))
        self.assertTrue(is_sports_category("综合体育"))
        self.assertTrue(is_sports_category("斯诺克/台球"))
        self.assertFalse(is_sports_category("剧情片"))
        self.assertFalse(is_sports_category("国产动漫"))

    def test_exact_sports_replay_requires_exact_title_and_sports_category(self):
        result = SearchResult(
            title="2025斯诺克世锦赛第一轮A选手VS B选手",
            source="测试源",
            episodes=(Episode("正片", "https://example.com/replay.m3u8"),),
            category="台球",
        )
        self.assertTrue(
            is_exact_sports_replay_result(
                "2025斯诺克世锦赛第一轮A选手VS B选手",
                result,
            )
        )
        self.assertFalse(
            is_exact_sports_replay_result("2025斯诺克世锦赛", result)
        )
        self.assertFalse(
            is_exact_sports_replay_result(
                result.title,
                SearchResult(
                    title=result.title,
                    source="测试源",
                    episodes=result.episodes,
                    category="剧情片",
                ),
            )
        )


class CardTextTests(unittest.TestCase):
    def test_episode_card_text_is_compact(self):
        episodes = tuple(
            Episode(f"第{index}集", f"https://example.com/{index}.m3u8")
            for index in range(1, 6)
        )
        current = Episode("第1-20集", "https://example.com/current.m3u8")
        result = SearchResult(
            title="重回末日前",
            source="红牛资源",
            episodes=episodes,
            year="2024",
            category="短剧",
        )
        self.assertEqual(
            _compact_play_card_text(result, current),
            ("重回末日前｜第1-20集", "2024 · 短剧 · 共5集 · 点击播放"),
        )

    def test_full_version_card_text_keeps_duration(self):
        episode = Episode("正片", "https://example.com/full.m3u8")
        result = SearchResult(
            title="测试完整版",
            source="量子资源",
            episodes=(episode,),
            year="2025",
            category="短剧",
            duration_seconds=9_000,
            is_full_version=True,
        )
        self.assertEqual(
            _compact_play_card_text(result, episode),
            ("测试完整版｜完整版", "2025 · 短剧 · 约150分钟 · 点击播放"),
        )

    def test_variants_card_text_deduplicates_and_sorts_years(self):
        episode = Episode("正片", "https://example.com/play.m3u8")
        variants = tuple(
            SearchResult(
                title=f"红楼梦版本{index}",
                source="测试源",
                episodes=(episode,),
                year=year,
                category=category,
            )
            for index, (year, category) in enumerate(
                (
                    ("2010", "国产剧"),
                    ("1987", "国产剧"),
                    ("2018", "剧情片"),
                    ("2018", "伦理片"),
                    ("1987", "电视剧"),
                    ("", "动画片"),
                ),
                start=1,
            )
        )
        self.assertEqual(
            _compact_variants_card_text("红楼梦", variants),
            ("红楼梦｜6个版本", "1987、2010、2018 · 点击选择版本"),
        )


class PlayUrlTests(unittest.TestCase):
    def test_matching_items_strictly_filter_media_categories(self):
        rows = [
            {
                "vod_name": "同名作品",
                "type_name": category,
                "vod_play_url": f"正片$https://example.com/{index}.m3u8",
            }
            for index, category in enumerate(
                ("短剧", "国产剧", "剧情片", "国产动漫", "大陆综艺"),
                start=1,
            )
        ]
        expected = {
            "短剧": "短剧",
            "电视剧": "国产剧",
            "电影": "剧情片",
            "动漫": "国产动漫",
            "综艺": "大陆综艺",
        }
        for media_type, category in expected.items():
            with self.subTest(media_type=media_type):
                results = matching_items(
                    "同名作品",
                    rows,
                    "测试源",
                    media_type,
                )
                self.assertEqual([item.category for item in results], [category])

    def test_media_category_rejects_missing_or_cross_type_categories(self):
        self.assertTrue(is_media_category("短剧", "短剧大全"))
        self.assertTrue(is_media_category("短剧", "AI漫剧"))
        self.assertFalse(is_media_category("短剧", "国产剧"))
        self.assertTrue(is_media_category("电视剧", "香港剧"))
        self.assertTrue(is_media_category("电视剧", "连续剧"))
        self.assertFalse(is_media_category("电视剧", "短剧"))
        self.assertTrue(is_media_category("电影", "动作片"))
        self.assertFalse(is_media_category("电影", "电影解说"))
        self.assertTrue(is_media_category("动漫", "日韩动漫"))
        self.assertTrue(is_media_category("动漫", "动画片"))
        self.assertTrue(is_media_category("综艺", "真人秀"))
        self.assertFalse(is_media_category("综艺", "国产剧"))
        self.assertFalse(is_media_category("电影", ""))

    def test_unfiltered_media_search_keeps_all_categories(self):
        rows = [
            {
                "vod_name": "同名资源",
                "type_name": category,
                "vod_play_url": f"正片$https://example.com/{index}.m3u8",
            }
            for index, category in enumerate(
                ("短剧", "国产剧", "科幻片", "日本动漫", "大陆综艺", "其他"),
                start=1,
            )
        ]
        results = matching_items("同名资源", rows, "测试源", None)
        self.assertEqual(
            {item.category for item in results},
            {"短剧", "国产剧", "科幻片", "日本动漫", "大陆综艺", "其他"},
        )

    def test_recommendations_include_short_drama_descendants_with_hls(self):
        payload = {
            "class": [
                {"type_id": 58, "type_pid": 0, "type_name": "短剧大全"},
                {"type_id": 67, "type_pid": 58, "type_name": "现代言情"},
                {"type_id": 20, "type_pid": 0, "type_name": "电影片"},
            ],
            "list": [
                {
                    "vod_name": "今日短剧",
                    "type_id": 67,
                    "type_name": "现代言情",
                    "vod_time": "2026-08-05 10:00:00",
                    "vod_play_url": "第1集$https://example.com/1.m3u8",
                },
                {
                    "vod_name": "没有HLS的短剧",
                    "type_id": 58,
                    "type_name": "短剧大全",
                    "vod_time": "2026-08-05 10:01:00",
                    "vod_play_url": "正片$https://example.com/video.mp4",
                },
                {
                    "vod_name": "普通电影",
                    "type_id": 20,
                    "type_name": "电影片",
                    "vod_time": "2026-08-05 10:02:00",
                    "vod_play_url": "正片$https://example.com/movie.m3u8",
                },
                {
                    "vod_name": "AI漫剧",
                    "type_id": 88,
                    "type_name": "AI漫剧",
                    "vod_time": "2026-08-05 10:03:00",
                    "vod_play_url": "正片$https://example.com/comic.m3u8",
                },
            ],
        }
        candidates = recommendation_candidates(payload, "测试源")
        self.assertEqual(
            [item.title for item in candidates],
            ["今日短剧", "AI漫剧"],
        )
        self.assertEqual(candidates[0].sort_key, "20260805100000")
        self.assertEqual(candidates[0].result.category, "现代言情")
        self.assertEqual(len(candidates[0].result.episodes), 1)
        self.assertEqual(candidates[1].result.category, "AI漫剧")

    def test_recommendations_can_filter_another_media_category(self):
        payload = {
            "class": [
                {"type_id": 1, "type_pid": 0, "type_name": "短剧"},
                {"type_id": 2, "type_pid": 0, "type_name": "电影片"},
                {"type_id": 3, "type_pid": 0, "type_name": "足球"},
            ],
            "list": [
                {
                    "vod_name": "短剧作品",
                    "type_id": 1,
                    "type_name": "短剧",
                    "vod_play_url": "正片$https://example.com/short.m3u8",
                },
                {
                    "vod_name": "电影作品",
                    "type_id": 2,
                    "type_name": "动作片",
                    "vod_play_url": "正片$https://example.com/movie.m3u8",
                },
                {
                    "vod_name": "足球回放",
                    "type_id": 3,
                    "type_name": "足球",
                    "vod_play_url": "正片$https://example.com/sports.m3u8",
                },
            ],
        }
        candidates = recommendation_candidates(payload, "测试源", "电影")
        self.assertEqual([item.title for item in candidates], ["电影作品"])
        sports = recommendation_candidates(payload, "测试源", "体育")
        self.assertEqual([item.title for item in sports], ["足球回放"])

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

    def test_matching_items_keep_version_metadata(self):
        items = [
            {
                "vod_name": "三国演义",
                "vod_year": "1994",
                "type_name": "国产剧",
                "vod_actor": "唐国强,孙彦军",
                "vod_remarks": "84集全",
                "vod_play_url": "1$https://example.com/1994.m3u8",
            },
            {
                "vod_name": "三国演义",
                "vod_year": "2009",
                "type_name": "国产动漫",
                "vod_actor": "",
                "vod_remarks": "86集全",
                "vod_play_url": "1$https://example.com/cartoon.m3u8",
            },
        ]
        results = matching_items("三国演义", items, "测试源")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].year, "1994")
        self.assertEqual(results[0].category, "国产剧")
        self.assertEqual(results[0].remarks, "84集全")
        self.assertNotEqual(
            variant_identity(results[0]),
            variant_identity(results[1]),
        )

    def test_variant_identity_deduplicates_same_version_across_sources(self):
        first = SearchResult(
            title="三国演义",
            source="甲源",
            episodes=(Episode("第1集", "https://a.example/1.m3u8"),),
            year="1994",
            actor="唐国强, 孙彦军",
            category="国产剧",
        )
        second = SearchResult(
            title="三国演义",
            source="乙源",
            episodes=(Episode("第1集", "https://b.example/1.m3u8"),),
            year="1994",
            actor="唐国强、孙彦军",
            category="国产剧",
        )
        self.assertEqual(variant_identity(first), variant_identity(second))

    def test_variant_identity_normalizes_animation_labels(self):
        first = SearchResult(
            title="三国演义",
            source="甲源",
            episodes=(Episode("第1集", "https://a.example/1.m3u8"),),
            year="2017",
            actor="杨默,图特哈蒙",
            category="中国动漫",
        )
        second = SearchResult(
            title="三国演义动画版",
            source="乙源",
            episodes=(Episode("第1集", "https://b.example/1.m3u8"),),
            year="2017",
            actor="杨默,张震",
            category="国产动画",
        )
        self.assertEqual(variant_identity(first), variant_identity(second))

    def test_variant_list_filters_commentary_not_the_main_video(self):
        commentary = SearchResult(
            title="三国演义1994[电影解说]",
            source="测试源",
            episodes=(Episode("正片", "https://a.example/comment.m3u8"),),
            category="电影解说",
        )
        series = SearchResult(
            title="三国演义",
            source="测试源",
            episodes=(Episode("第1集", "https://a.example/series.m3u8"),),
            category="国产剧",
        )
        self.assertFalse(is_selectable_variant(commentary))
        self.assertTrue(is_selectable_variant(series))

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


class EpisodePlayerTests(unittest.TestCase):
    def setUp(self):
        self.result = SearchResult(
            title="测试短剧",
            source="测试源",
            episodes=(
                Episode("第01集", "https://cdn.example/1.m3u8"),
                Episode("第02集", "https://cdn.example/2.m3u8"),
            ),
        )

    def test_public_base_url_validation(self):
        self.assertEqual(
            validate_public_base_url("https://player.example/root/"),
            "https://player.example/root",
        )
        self.assertEqual(validate_public_base_url("javascript:alert(1)"), "")
        self.assertEqual(validate_public_base_url("https://example.com/?x=1"), "")

    def test_playlist_token_expires(self):
        store = EpisodePlaylistStore(ttl_seconds=60)
        token = store.put(self.result, now=100)
        self.assertEqual(store.get(token, now=159).title, "测试短剧")
        self.assertIsNone(store.get(token, now=160))

    def test_variant_token_expires(self):
        store = VariantStore(ttl_seconds=60)
        token = store.put("测试短剧", (self.result,), now=100)
        self.assertEqual(store.get(token, now=159).query_title, "测试短剧")
        self.assertIsNone(store.get(token, now=160))

    def test_collection_token_keeps_first_page_and_expires(self):
        page = CollectionPage(
            query="CBA2023",
            page=1,
            items=(self.result,),
            has_more=True,
        )
        store = CollectionStore(ttl_seconds=60)
        token = store.put("CBA2023", page, now=100)
        self.assertEqual(store.get(token, now=159).pages[1], page)
        self.assertIsNone(store.get(token, now=160))

    def test_player_url_selects_requested_episode_without_embedding_streams(self):
        server = ShortDramaPlayerServer(
            public_base_url="https://player.example",
        )
        server.started = True
        url = server.create_watch_url(self.result, self.result.episodes[1])
        self.assertIsNotNone(url)
        self.assertTrue(url.startswith("https://player.example/short-drama/watch/"))
        self.assertTrue(url.endswith("?ep=2"))
        self.assertNotIn("m3u8", url)

    def test_player_url_can_force_explicit_episode(self):
        server = ShortDramaPlayerServer(
            public_base_url="https://player.example",
        )
        server.started = True
        url = server.create_watch_url(
            self.result,
            self.result.episodes[1],
            force_episode=True,
        )
        self.assertTrue(url.endswith("?ep=2&force=1"))

    def test_single_stream_keeps_existing_player(self):
        server = ShortDramaPlayerServer(
            public_base_url="https://player.example",
        )
        server.started = True
        single = SearchResult(
            title=self.result.title,
            source=self.result.source,
            episodes=(self.result.episodes[0],),
        )
        self.assertIsNone(server.create_watch_url(single, single.episodes[0]))

    def test_variants_url_uses_token_without_embedding_streams(self):
        server = ShortDramaPlayerServer(
            public_base_url="https://player.example",
        )
        server.started = True
        other = SearchResult(
            title="测试短剧动画版",
            source="动画源",
            episodes=(Episode("第01集", "https://cdn.example/a.m3u8"),),
            category="国产动漫",
        )
        url = server.create_variants_url("测试短剧", (self.result, other))
        self.assertIsNotNone(url)
        self.assertTrue(
            url.startswith("https://player.example/short-drama/variants/")
        )
        self.assertNotIn("m3u8", url)

    def test_collection_url_uses_token_without_embedding_streams(self):
        server = ShortDramaPlayerServer(
            public_base_url="https://player.example",
        )
        server.started = True
        page = CollectionPage(
            query="CBA2023",
            page=1,
            items=(self.result,),
            has_more=True,
        )
        url = server.create_collection_url("CBA2023", page)
        self.assertIsNotNone(url)
        self.assertTrue(
            url.startswith("https://player.example/short-drama/collection/")
        )
        self.assertNotIn("m3u8", url)

    def test_recommendations_url_uses_token_without_embedding_streams(self):
        server = ShortDramaPlayerServer(
            public_base_url="https://player.example",
        )
        server.started = True
        url = server.create_recommendations_url((self.result,))
        self.assertIsNotNone(url)
        self.assertTrue(
            url.startswith("https://player.example/short-drama/recommendations/")
        )
        self.assertNotIn("m3u8", url)

    def test_card_cover_url_hides_first_cover_behind_jpeg_token(self):
        server = ShortDramaPlayerServer(
            public_base_url="https://player.example",
        )
        server.started = True
        source_url = "https://img.example/first.webp"
        url = server.create_card_cover_url(source_url)
        self.assertIsNotNone(url)
        self.assertTrue(url.startswith("https://player.example/short-drama/cover/"))
        self.assertTrue(url.endswith(".jpg"))
        self.assertNotIn(source_url, url)
        token = url.rsplit("/", 1)[-1].removesuffix(".jpg")
        self.assertEqual(server.cover_store.get(token).source_url, source_url)

    def test_variant_page_shows_covers_metadata_and_choice_links(self):
        live_action = SearchResult(
            title="三国演义",
            source="电视剧源",
            episodes=(Episode("第01集", "https://cdn.example/tv.m3u8"),),
            cover_url="https://img.example/tv.jpg",
            year="1994",
            actor="唐国强,鲍国安",
            category="国产剧",
        )
        animation = SearchResult(
            title="三国演义动画版",
            source="动画源",
            episodes=(Episode("第01集", "https://cdn.example/anime.m3u8"),),
            cover_url="https://img.example/anime.jpg",
            year="2017",
            actor="杨默,图特哈蒙",
            category="国产动漫",
        )
        record = VariantRecord(
            query_title="三国演义",
            variants=(live_action, animation),
            expires_at=999,
        )
        html = _variant_html(record, "safe-token")
        self.assertIn("选择《三国演义》的版本", html)
        self.assertIn("https://img.example/tv.jpg", html)
        self.assertIn("1994年", html)
        self.assertIn("唐国强、鲍国安", html)
        self.assertIn("../choose/safe-token/2", html)

    def test_recommendation_page_shows_covers_and_play_links(self):
        recommended = SearchResult(
            title="今日短剧",
            source="量子资源",
            episodes=(Episode("第01集", "https://cdn.example/1.m3u8"),),
            cover_url="https://img.example/recommend.jpg",
            year="2026",
            category="短剧",
        )
        record = VariantRecord(
            query_title="最新短剧推荐",
            variants=(recommended,),
            expires_at=999,
        )
        html = _recommendation_html(record, "recommend-token")
        self.assertIn("最新短剧推荐", html)
        self.assertIn("共 1 部", html)
        self.assertIn("https://img.example/recommend.jpg", html)
        self.assertIn("../choose/recommend-token/1", html)
        self.assertNotIn("m3u8", html)

    def test_collection_page_uses_lazy_external_script(self):
        html = _collection_html("CBA2023")
        self.assertIn("CBA2023 比赛回放", html)
        self.assertIn('id="list"', html)
        self.assertIn('loading = \'lazy\'', _COLLECTION_JAVASCRIPT)
        self.assertIn("IntersectionObserver", _COLLECTION_JAVASCRIPT)
        self.assertIn("collection-api", _COLLECTION_JAVASCRIPT)

    def test_mobile_player_uses_custom_episode_sheet(self):
        html = _player_html("测试短剧")
        self.assertNotIn("<select", html)
        self.assertIn('id="episodeSheet"', html)
        self.assertIn('id="sheetClose"', html)
        self.assertIn('id="inlineEpisodeGrid"', html)
        self.assertIn('id="episodeCount"', html)
        self.assertIn('aria-label="选择剧集">选集</button>', html)
        self.assertIn('id="currentEpisode"', html)
        self.assertIn("repeat(5, minmax(0,1fr))", html)
        self.assertIn("inlineEpisodeGrid.appendChild", _PLAYER_JAVASCRIPT)
        self.assertIn("inlineEpisodes.scrollIntoView", _PLAYER_JAVASCRIPT)
        self.assertIn('aria-label="上一集"', html)
        self.assertIn('aria-label="下一集"', html)

    def test_quality_and_portrait_adaptation_are_present(self):
        html = _player_html("测试短剧")
        self.assertIn('id="qualityButton"', html)
        self.assertIn('id="videoShell"', html)
        self.assertIn("hls.levels", _PLAYER_JAVASCRIPT)
        self.assertIn("video.videoWidth / video.videoHeight", _PLAYER_JAVASCRIPT)

    def test_player_supports_hold_for_temporary_double_speed(self):
        html = _player_html("测试短剧")
        self.assertIn('id="holdSpeedHint"', html)
        self.assertIn('hidden>2×</div>', html)
        self.assertNotIn("2× 倍速播放", html)
        self.assertIn("video.addEventListener('pointerdown', startHoldSpeed)", _PLAYER_JAVASCRIPT)
        self.assertIn("video.playbackRate = 2", _PLAYER_JAVASCRIPT)
        self.assertIn("video.playbackRate = holdSpeedOriginalRate", _PLAYER_JAVASCRIPT)
        self.assertIn("nativeControlsHeight", _PLAYER_JAVASCRIPT)
        self.assertIn("350", _PLAYER_JAVASCRIPT)
        self.assertIn("showHoldSpeedHint", _PLAYER_JAVASCRIPT)
        self.assertIn("1200", _PLAYER_JAVASCRIPT)

    def test_player_stage_centers_in_mobile_visual_viewport(self):
        html = _player_html("测试短剧")
        self.assertIn('class="watch-stage"', html)
        self.assertIn("justify-content: center", html)
        self.assertIn("--player-viewport-height", html)
        self.assertNotIn(".watch-stage {{ display: block", html)
        self.assertIn("window.visualViewport?.height", _PLAYER_JAVASCRIPT)
        self.assertIn(
            "window.visualViewport?.addEventListener('resize', updatePlayerViewportHeight)",
            _PLAYER_JAVASCRIPT,
        )

    def test_player_persists_history_across_playlist_tokens(self):
        self.assertIn("stableMediaId(body)", _PLAYER_JAVASCRIPT)
        self.assertIn("body.title, body.source, body.cover", _PLAYER_JAVASCRIPT)
        self.assertIn("short-drama:history:${mediaStorageId}", _PLAYER_JAVASCRIPT)
        self.assertIn("migrateLegacyHistory", _PLAYER_JAVASCRIPT)
        self.assertIn("pagehide", _PLAYER_JAVASCRIPT)
        self.assertIn("visibilitychange", _PLAYER_JAVASCRIPT)
        self.assertIn("video.addEventListener('seeked', saveCurrentProgress)", _PLAYER_JAVASCRIPT)
        self.assertIn("storageRemove(progressKey(current))", _PLAYER_JAVASCRIPT)

    def test_player_prefers_saved_episode_unless_url_forces_one(self):
        self.assertIn("searchParams.get('force') === '1'", _PLAYER_JAVASCRIPT)
        self.assertIn("forced && hasRequested", _PLAYER_JAVASCRIPT)
        self.assertIn("hasSaved ? saved", _PLAYER_JAVASCRIPT)

    def test_fullscreen_uses_player_container_and_keeps_episode_controls(self):
        html = _player_html("测试短剧")
        self.assertIn('controlslist="nofullscreen nodownload noremoteplayback"', html)
        self.assertIn('id="fullscreenButton"', html)
        self.assertIn('class="top-right-actions"', html)
        self.assertNotIn("bottom: 54px", html)
        shell_start = html.index('id="videoShell"')
        episode_sheet = html.index('id="episodeSheet"')
        below_player = html.index('class="below-player"')
        self.assertLess(shell_start, episode_sheet)
        self.assertLess(episode_sheet, below_player)
        self.assertIn("videoShell.requestFullscreen", _PLAYER_JAVASCRIPT)
        self.assertIn("videoShell.classList.contains('pseudo-fullscreen')", _PLAYER_JAVASCRIPT)


class CollectionPlayerAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_lazy_pages_drop_titles_already_shown(self):
        first_item = SearchResult(
            title="CBA A队VS B队 20231231",
            source="甲源",
            episodes=(Episode("正片", "https://a.example/1.m3u8"),),
        )
        next_item = SearchResult(
            title="CBA C队VS D队 20231230",
            source="乙源",
            episodes=(Episode("正片", "https://b.example/1.m3u8"),),
        )
        first_page = CollectionPage(
            query="CBA2023",
            page=1,
            items=(first_item,),
            has_more=True,
        )
        server = ShortDramaPlayerServer(public_base_url="https://player.example")
        token = server.collection_store.put("CBA2023", first_page)
        record = server.collection_store.get(token)

        async def loader(_query: str, page: int) -> CollectionPage:
            return CollectionPage(
                query="CBA2023",
                page=page,
                items=(first_item, next_item),
                has_more=False,
            )

        server.collection_loader = loader
        loaded = await server._load_collection_page(record, 2)
        self.assertEqual([item.title for item in loaded.items], [next_item.title])

    async def test_scheduled_recommendation_sends_share_to_exact_umo(self):
        result = SearchResult(
            title="测试短剧",
            source="测试源",
            cover_url="https://example.com/cover.jpg",
            episodes=(Episode("正片", "https://example.com/play.m3u8"),),
        )

        class FakeSearcher:
            async def recommend_results(self, limit, *, media_type):
                self.request = (limit, media_type)
                return (result,)

        class FakePlayer:
            def create_recommendations_url(self, recommendations, heading):
                self.request = (recommendations, heading)
                return "https://player.example/recommendations/test"

            def create_card_cover_url(self, source_url):
                self.cover_request = source_url
                return "https://player.example/cover/first.jpg"

        class FakeContext:
            async def send_message(self, session, chain):
                self.request = (session, chain)
                return True

        plugin = object.__new__(Main)
        plugin.searcher = FakeSearcher()
        plugin.player_server = FakePlayer()
        plugin.context = FakeContext()
        response = await plugin._send_scheduled_recommendations(
            {
                "id": "abcdef123456",
                "session": "aiocqhttp:GroupMessage:51632940287@chatroom",
                "media_type": "短剧",
                "limit": 12,
            }
        )
        self.assertTrue(response["sent"])
        self.assertEqual(plugin.searcher.request, (12, "短剧"))
        session, chain = plugin.context.request
        self.assertEqual(
            session,
            "aiocqhttp:GroupMessage:51632940287@chatroom",
        )
        segment = chain.chain[0]
        self.assertEqual(segment.type.value, "Share")
        self.assertIn("最新短剧推荐", segment.title)
        self.assertEqual(plugin.player_server.cover_request, result.cover_url)
        self.assertEqual(
            segment.image,
            "https://player.example/cover/first.jpg",
        )


class RecommendationScheduleStoreTests(unittest.TestCase):
    def test_validates_group_umo_and_builds_weekly_cron(self):
        task = validate_task(
            {
                "name": "周末电影推荐",
                "session": "aiocqhttp:GroupMessage:51632940287@chatroom",
                "media_type": "电影",
                "hour": 20,
                "minute": 30,
                "days": ["sat", "sun"],
                "limit": 12,
                "enabled": True,
            }
        )
        self.assertEqual(task_cron_expression(task), "30 20 * * sat,sun")
        self.assertEqual(len(task["id"]), 12)

    def test_rejects_private_message_umo(self):
        with self.assertRaises(ScheduleConfigError):
            validate_task(
                {
                    "session": "aiocqhttp:FriendMessage:wxid_test",
                    "media_type": "短剧",
                }
            )

    def test_store_upserts_and_deletes_tasks(self):
        with TemporaryDirectory() as temp_dir:
            store = RecommendationScheduleStore(Path(temp_dir) / "tasks.json")
            saved = store.upsert(
                {
                    "name": "动漫推荐",
                    "session": "aiocqhttp:GroupMessage:51632940287@chatroom",
                    "media_type": "动漫",
                    "hour": 18,
                    "minute": 5,
                    "days": [],
                    "limit": 8,
                    "enabled": False,
                }
            )
            self.assertEqual(len(store.load()), 1)
            self.assertEqual(task_cron_expression(saved), "5 18 * * *")
            saved["enabled"] = True
            updated = store.upsert(saved)
            self.assertTrue(updated["enabled"])
            self.assertTrue(store.delete(saved["id"]))
            self.assertEqual(store.load(), [])


if __name__ == "__main__":
    unittest.main()
