# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import 배포.빌드 as release_build
from 배포.빌드 import validate_release


class ReleaseValidationTest(unittest.TestCase):
    def make_release(self, root: Path, key="") -> Path:
        out = root / "release"
        app = out / "app"
        app.mkdir(parents=True)
        (app / "설정.json").write_text(json.dumps({"API_키": key, "텔레그램_봇_토큰": ""}), encoding="utf-8")
        (app / "main.py").write_text("value = 1\n", encoding="utf-8")
        return out

    def test_clean_release_gets_checksum_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = self.make_release(Path(td))
            validate_release(out)
            manifest = (out / "SHA256SUMS.txt").read_text(encoding="utf-8")
            self.assertIn("app/main.py", manifest)
            self.assertIn("app/설정.json", manifest)

    def test_release_with_secret_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = self.make_release(Path(td), "secret-key")
            with self.assertRaisesRegex(RuntimeError, "인증정보"):
                validate_release(out)

    def test_release_with_personal_work_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = self.make_release(Path(td))
            work = out / "app" / "대본"
            work.mkdir()
            (work / "개인대본.txt").write_text("개인 작업", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "개인 작업 파일"):
                validate_release(out)

    def make_build_source(self, root: Path) -> Path:
        source = root / "source"
        (source / "assets").mkdir(parents=True)
        (source / "지침" / "민담").mkdir(parents=True)
        (source / "레퍼런스").mkdir()
        (source / "README.md").write_text("manual", encoding="utf-8")
        (source / "main.py").write_text("value = 1\n", encoding="utf-8")
        (source / "설정.json").write_text('{"API_키":"private"}', encoding="utf-8")
        (source / "youtube_auth").mkdir()
        (source / "youtube_auth" / "token.json").write_text("private", encoding="utf-8")
        (source / "레퍼런스" / "japan.png").write_bytes(b"japan reference")
        (source / "레퍼런스" / "korea.png").write_bytes(b"korea reference")
        profile = {
            "person": {"이름": "마음등불심리노트", "마스코트": {"이미지": "레퍼런스/korea.png"}},
            "mindam": {"이름": "설루온", "마스코트": {"이미지": ""}},
        }
        brand = {"channels": [
            {"name": "心のしくみ便り", "reference": "레퍼런스/japan.png"},
            {"name": "마음등불심리노트", "reference": "레퍼런스/korea.png"},
        ]}
        (source / "채널_프로필.json").write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
        (source / "채널_브랜드_레퍼런스.json").write_text(json.dumps(brand, ensure_ascii=False), encoding="utf-8")
        return source

    def test_buyer_build_uses_generic_profile_and_omits_owner_assets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = self.make_build_source(root)
            app = root / "buyer" / "app"
            with patch.object(release_build, "ROOT", source), patch.object(release_build, "APP_DIRS", ["assets", "지침"]):
                release_build.build_app(app, editor=None)
            profile = json.loads((app / "채널_프로필.json").read_text(encoding="utf-8"))
            settings = json.loads((app / "설정.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["person"]["이름"], "내 채널")
            self.assertEqual(settings["화풍"], "애니")
            self.assertFalse((app / "채널_브랜드_레퍼런스.json").exists())
            self.assertFalse((app / "레퍼런스").exists())
            self.assertFalse((app / "youtube_auth").exists())
            self.assertEqual(settings["API_키"], "")
            validate_release(app.parent)

    def test_owner_build_keeps_locked_brands_but_not_credentials_or_media(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = self.make_build_source(root)
            app = root / "owner" / "app"
            with patch.object(release_build, "ROOT", source), patch.object(release_build, "APP_DIRS", ["assets", "지침"]):
                release_build.build_app(app, editor=None, owner_profile=True)
            profile = json.loads((app / "채널_프로필.json").read_text(encoding="utf-8"))
            settings = json.loads((app / "설정.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["person"]["이름"], "마음등불심리노트")
            self.assertEqual(settings["AI"], "codex")
            self.assertEqual(settings["후킹_장면수"], 5)
            self.assertTrue((app / "레퍼런스" / "japan.png").is_file())
            self.assertTrue((app / "레퍼런스" / "korea.png").is_file())
            self.assertFalse((app / "youtube_auth").exists())
            self.assertEqual(settings["API_키"], "")
            validate_release(app.parent)


if __name__ == "__main__":
    unittest.main()
