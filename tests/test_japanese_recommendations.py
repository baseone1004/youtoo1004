import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import 채널_프로필 as profiles
import 채널_연동 as link
import 주제_추천 as topics
import 주제뽑기 as collector
import 유튜브_API as youtube
import youtube_accounts


class JapaneseRecommendationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        old = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, old)
        language = patch.object(profiles, "language_code", return_value="ja")
        language.start()
        self.addCleanup(language.stop)

    def test_japanese_collection_preserves_korean_files_and_writes_real_views(self):
        for name in ("계획.json", "후보.json", "벤치_히트.json", "추천_추가.json"):
            Path(name).write_text("[]", encoding="utf-8")
        videos = [dict(id=str(i), title="人間関係で心が疲れる理由", views=1000 * (i+1), duration=1500,
                       url=f"https://www.youtube.com/watch?v={i}") for i in range(8)]
        ch = dict(id="other", name="参考チャンネル", url="https://www.youtube.com/channel/other", videos=videos)
        with patch.object(collector, "load_config", return_value={"내_채널":""}), \
             patch.object(collector, "discover_channels", return_value=[("other", "参考", ch["url"], 1)]), \
             patch.object(collector, "fetch_channel", return_value=ch), \
             patch.object(collector, "build_recommendations") as korean:
            collector.main()
        korean.assert_not_called()
        for name in ("계획.json", "후보.json", "벤치_히트.json", "추천_추가.json"):
            self.assertEqual(Path(name).read_text(encoding="utf-8"), "[]")
        data = json.loads(Path("벤치_히트_일본.json").read_text(encoding="utf-8"))
        self.assertEqual(data["히트"][0]["views"], 8000)
        self.assertEqual(len(topics.load()["person"]), 6)

    def test_default_topics_exclude_used_titles_and_are_labelled(self):
        used = topics.JAPANESE_TOPICS[0][1]
        items = topics.seed_japanese([used])
        self.assertNotIn(used, [v["제목"] for v in items])
        self.assertTrue(all("AI 미사용" in v["한줄"] for v in items))
        self.assertTrue(all(not __import__('re').search('[가-힣]', v['제목']) for v in items))

    def test_empty_channel_is_connected_not_a_connection_failure(self):
        with patch.object(link, "cached", return_value=dict(url="channel", fetched="2026-10-10 10:00", name="日本", videos=[])):
            result = link.analysis({"내_채널":"channel"}, "person")
        self.assertTrue(result["connected"])
        self.assertIn("공개 영상이 아직 없어", result["reason"])

    def test_search_requests_japanese_results_and_actual_statistics(self):
        search = {"items":[{"id":{"videoId":"vid"},"snippet":{"title":"人間関係の悩み", "channelId":"cid", "channelTitle":"参考"}}]}
        stats = {"items":[{"id":"vid", "statistics":{"viewCount":"45678"}, "contentDetails":{"duration":"PT25M"}}]}
        with patch.object(youtube, "_get", side_effect=[search, stats]) as get:
            videos = youtube.search_videos("test", "人間関係")
        self.assertEqual(get.call_args_list[0].kwargs["relevanceLanguage"], "ja")
        self.assertEqual(get.call_args_list[0].kwargs["regionCode"], "JP")
        self.assertEqual(videos[0]["view_count"], 45678)
        self.assertEqual(videos[0]["duration"], 1500)

    def test_replacing_selected_key_survives_switching_channels(self):
        cfg = {"유튜브_선택_person":"jp", "유튜브_계정":[
            dict(id="jp", name="日本", url="jp", api_key="old"),
            dict(id="ko", name="마음", url="ko", api_key="korean")]}
        youtube_accounts.save_selected_api_key(cfg, "replacement")
        youtube_accounts.select_account(cfg, "ko", "person")
        self.assertEqual(cfg["유튜브_API_키"], "korean")
        youtube_accounts.select_account(cfg, "jp", "person")
        self.assertEqual(cfg["유튜브_API_키"], "replacement")

    def test_food_and_investing_are_not_psychology_recommendations(self):
        self.assertFalse(collector.관련있음("人生を取り戻す食事と投資"))
        self.assertTrue(collector.관련있음("人間関係で心が疲れる理由"))


if __name__ == "__main__":
    unittest.main()
