import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import 채널_연동


class ChannelIntegrationAccountsTest(unittest.TestCase):
    def test_person_topics_union_korean_and_japanese_but_exclude_story_channel(self):
        cfg = {
            "유튜브_선택_person": "ko",
            "유튜브_선택_mindam": "story",
            "내_채널": "https://www.youtube.com/@ko",
            "민담_채널": "https://www.youtube.com/@story",
            "유튜브_계정": [
                {"id": "ko", "name": "한국심리", "url": "https://www.youtube.com/@ko", "language": "ko"},
                {"id": "ja", "name": "日本心理", "url": "https://www.youtube.com/@ja", "language": "ja"},
                {"id": "story", "name": "설루온", "url": "https://www.youtube.com/@story", "language": "ko"},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "channel_titles.json"
            cache.write_text(json.dumps({
                "account:ko": {"url": cfg["유튜브_계정"][0]["url"], "titles": ["한국 심리 주제"]},
                "account:ja": {"url": cfg["유튜브_계정"][1]["url"], "titles": ["日本の心理テーマ"]},
                "account:story": {"url": cfg["유튜브_계정"][2]["url"], "titles": ["조선 야담"]},
            }, ensure_ascii=False), encoding="utf-8")
            with patch.object(채널_연동, "CACHE", str(cache)):
                self.assertEqual(채널_연동.titles(cfg, "person"), ["한국 심리 주제", "日本の心理テーマ"])
                self.assertEqual(채널_연동.titles(cfg, "mindam"), ["조선 야담"])


if __name__ == "__main__":
    unittest.main()
