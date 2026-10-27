import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import youtube_upload


class YouTubeUploadTests(unittest.TestCase):
    def test_oauth_client_is_desktop_only_and_stored_protected(self):
        client = {"installed": {"client_id": "client-id", "client_secret": "secret",
                               "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                               "token_uri": "https://oauth2.googleapis.com/token"}}
        with tempfile.TemporaryDirectory() as td:
            youtube_upload.save_client(td, client)
            path = Path(td) / youtube_upload.CLIENT_FILE
            saved = path.read_bytes()
            self.assertTrue(saved.startswith((youtube_upload._DPAPI_PREFIX, youtube_upload._LOCAL_PREFIX)))
            self.assertEqual(youtube_upload._read_private_json(path), client)
        with self.assertRaisesRegex(ValueError, "데스크톱 앱"):
            youtube_upload.save_client(tempfile.gettempdir(), {"web": {}})

    def test_package_account_matches_channel_folder_and_story_slot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "업로드" / "心のしくみ便り" / "2026-10-27_心の話"
            package.mkdir(parents=True)
            (package / "제작원본.json").write_text(
                json.dumps({"script": "대본/일본어/心の話.txt"}), encoding="utf-8")
            accounts = [
                {"id": "ko", "name": "마음등불심리노트", "profile": {"업로드_폴더": "마음등불심리노트"}},
                {"id": "ja", "name": "心のしくみ便り", "language": "ja",
                 "profile": {"업로드_폴더": "心のしくみ便り"}},
            ]
            cfg = {"유튜브_선택_person": "ko"}
            account, slot = youtube_upload.account_for_package(root, package, accounts, cfg)
            self.assertEqual((account["id"], slot), ("ja", "person"))

            story = root / "업로드" / "설루온" / "2026-10-27_옛이야기"
            story.mkdir(parents=True)
            (story / "제작원본.json").write_text(
                json.dumps({"script": "대본/민담/옛이야기/final.txt"}), encoding="utf-8")
            story_accounts = [{"id": "story", "name": "설루온",
                               "profile": {"업로드_폴더": "설루온"}}]
            account, slot = youtube_upload.account_for_package(root, story, story_accounts,
                                                                {"유튜브_선택_mindam": "story"})
            self.assertEqual((account["id"], slot), ("story", "mindam"))

    def test_preflight_checks_package_without_google_network(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "업로드" / "channel" / "video"
            package.mkdir(parents=True)
            (package / "업로드정보.txt").write_text(
                "[제목]\n검토할 제목\n\n[설명]\n설명입니다.\n\n[태그]\n심리, 관계\n", encoding="utf-8")
            (package / "최종.mp4").write_bytes(b"x" * 1_100_000)
            account = {"id": "ko", "language": "ko"}
            with patch.object(youtube_upload, "_authorized", return_value=(object(), {"id": "UC1", "title": "내 채널"})):
                result = youtube_upload.preflight(root, package, account, "private", include_shorts=False)
            self.assertEqual(result, {"channel": "내 채널", "video_count": 1, "title": "검토할 제목"})

    def test_ambiguous_upload_requires_manual_confirmation_and_preserves_successes(self):
        with tempfile.TemporaryDirectory() as td:
            package = Path(td) / "업로드" / "channel" / "video"
            package.mkdir(parents=True)
            manifest = package / "youtube_uploads.json"
            manifest.write_text(json.dumps({"account_id": "ko", "items": {
                "uncertain": {"status": "uploading"},
                "done": {"status": "uploaded", "video_id": "abc"}}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Studio"):
                youtube_upload.reset_uncertain(td, package, "ko", False)
            self.assertEqual(youtube_upload.reset_uncertain(td, package, "ko", True)["cleared"], 1)
            result = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(result["items"]["uncertain"]["status"], "pending")
            self.assertEqual(result["items"]["done"]["status"], "uploaded")


if __name__ == "__main__":
    unittest.main()
