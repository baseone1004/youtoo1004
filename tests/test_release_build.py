# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path

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
            (out / "app" / "대본").mkdir()
            with self.assertRaisesRegex(RuntimeError, "개인 작업 파일"):
                validate_release(out)


if __name__ == "__main__":
    unittest.main()
