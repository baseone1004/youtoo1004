import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import 대본선택 as app


class OpenMediaTest(unittest.TestCase):
    def test_media_uses_default_player(self):
        with tempfile.TemporaryDirectory() as td, patch.object(app, "BASE", td), patch.object(app.os, "startfile") as launch, patch.object(app.subprocess, "Popen") as explorer:
            audio = Path(td, "나레이션.mp3"); audio.write_bytes(b"test")
            app.open_workspace_path(str(audio))
            launch.assert_called_once_with(str(audio))
            explorer.assert_not_called()

    def test_missing_file_reports_error(self):
        with tempfile.TemporaryDirectory() as td, patch.object(app, "BASE", td):
            with self.assertRaisesRegex(ValueError, "아직"):
                app.open_workspace_path(str(Path(td, "missing.mp3")))

    def test_player_failure_has_clear_message(self):
        with tempfile.TemporaryDirectory() as td, patch.object(app, "BASE", td), patch.object(app.os, "startfile", side_effect=OSError()):
            audio = Path(td, "audio.mp3"); audio.write_bytes(b"test")
            with self.assertRaisesRegex(ValueError, "기본 앱"):
                app.open_workspace_path(str(audio))

    def test_other_files_are_only_selected_and_outside_is_rejected(self):
        with tempfile.TemporaryDirectory() as td, patch.object(app, "BASE", td), patch.object(app.subprocess, "Popen") as explorer:
            file = Path(td, "unsafe.exe"); file.write_bytes(b"test")
            app.open_workspace_path(str(file))
            self.assertEqual(explorer.call_args.args[0][1], "/select,")
            with self.assertRaises(ValueError):
                app.open_workspace_path(str(Path(td).parent / "outside.mp3"))
