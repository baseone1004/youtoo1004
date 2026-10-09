import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import 대본선택 as app
import production_library as library


class LanguageStorageTests(unittest.TestCase):
    def test_new_scripts_are_saved_in_separate_language_folders(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with patch.object(app, "backup_work_text"):
            td=temp.name
            root=Path(td)
            cwd=os.getcwd()
            os.chdir(root)
            self.addCleanup(os.chdir,cwd)
            ai=Mock(name="ai"); ai.name="mock"; ai.model="mock"; ai.cost_text.return_value="mock"
            with patch.object(app,"대본_폴더",str(root/"대본")), patch.object(app,"AI",return_value=ai), \
                 patch.object(app.대본생성,"load_cfg",return_value={"대본_글자수":7000}), \
                 patch.object(app,"read_guideline",return_value="mock"), patch.object(app,"find_issues",return_value=[]), \
                 patch.object(app.대본생성,"generate",return_value=("[대본]\n본문.","본문.")):
                paths=[]
                for code in ("ko","ja"):
                    with patch.object(app.채널_프로필,"language_code",return_value=code):
                        r=app.make_person_script(app.Job("script"),{"title":"같은 제목", "optimize":False,"mark_used":False})
                        paths.append(Path(r["file"]))
                self.assertEqual(paths[0].parent.name,"한국어")
                self.assertEqual(paths[1].parent.name,"일본어")
                self.assertEqual(len(app.script_files()),2)
                self.assertEqual({x["language"] for x in app.script_files()},{"ko","ja"})

    def test_nested_work_backup_recovery_and_upload_cleanup_preserve_other_language(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for language in ("한국어","일본어"):
                folder=root/"대본"/language
                folder.mkdir(parents=True)
                (folder/"작업.txt").write_text("본문",encoding="utf-8")
                (folder/"작업_자료").mkdir()
                (folder/"작업_자료"/"나레이션.srt").write_text("자막",encoding="utf-8")
            library.snapshot(root)
            self.assertEqual(len(library.library(root)),2)
            jp=root/"대본"/"일본어"/"작업.txt"
            jp.unlink()
            self.assertTrue(any(x["id"]=="일본어/작업.txt" and x["recoverable"] for x in library.library(root)))
            self.assertEqual(library.restore_missing(root,"일본어/작업.txt")["count"],1)
            library.upload_cleanup(root,"일본어/작업.txt",execute=True)
            self.assertTrue((root/"대본"/"한국어"/"작업.txt").exists())
            self.assertFalse(jp.exists())

    def test_language_mismatch_blocks_regeneration(self):
        with tempfile.TemporaryDirectory() as td, patch.object(app,"대본_폴더",td):
            jp=Path(td)/"일본어"/"作業.txt"
            ko=Path(td)/"한국어"/"작업.txt"
            with patch.object(app.채널_프로필,"language_code",return_value="ko"):
                with self.assertRaisesRegex(ValueError,"일본어"):
                    app.ensure_script_language(jp)
                app.ensure_script_language(ko)
            with patch.object(app.채널_프로필,"language_code",return_value="ja"):
                with self.assertRaisesRegex(ValueError,"한국어"):
                    app.ensure_script_language(ko)
