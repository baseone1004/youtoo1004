# -*- coding: utf-8 -*-
"""편집프로그램 연결 패치의 핵심 안전장치 회귀 테스트."""
import py_compile
import tempfile
import unittest
from pathlib import Path

from 편집프로그램_렌더_보호 import apply


class RenderGuardPatchTest(unittest.TestCase):
    def make_editor(self, root: Path) -> None:
        core = root / "core"
        core.mkdir()
        (core / "jobs.py").write_text(
            "import threading\nimport uuid\n\n"
            "class Job:\n"
            "    def __init__(self, id, result=None):\n"
            "        self.id = id\n"
            "        self.result = result or {}\n"
            "        self.status = 'pending'\n\n"
            "class JobStore:\n"
            "    def __init__(self):\n"
            "        self._lock = threading.Lock()\n"
            "        self._jobs = {}\n\n"
            "    def get(self, job_id: str) -> Job | None:\n"
            "        return self._jobs.get(job_id)\n",
            encoding="utf-8",
        )
        (root / "app.py").write_text(
            "from pathlib import Path\n\n"
            "def submit(jobs, req, prep, do_mp4):\n"
            "    job = jobs.create()\n"
            "    job.result = {\"warnings\": prep[\"warnings\"]}\n"
            "    return job\n",
            encoding="utf-8",
        )
        (core / "renderer.py").write_text(
            "import random\n"
            "import shutil\n"
            "import tempfile\n"
            "from pathlib import Path\n\n"
            "def render(job, opts):\n"
            "    tmp = Path(tempfile.mkdtemp(prefix=\"aip_\"))\n"
            "    try:\n"
            "        fps, total_duration = 30, 1.0\n"
            "        args = [\"-r\", str(fps), \"-movflags\", \"+faststart\", \"-t\", f\"{total_duration:.4f}\", opts.output]\n"
            "        Path(opts.output).parent.mkdir(parents=True, exist_ok=True)\n"
            "        job.progress = 1.0\n"
            "        job.stage = \"완료\"\n"
            "    finally:\n"
            "        shutil.rmtree(tmp, ignore_errors=True)\n",
            encoding="utf-8",
        )

    def test_patch_is_idempotent_and_result_compiles(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            editor = Path(td)
            self.make_editor(editor)

            self.assertTrue(apply(editor))
            first = {p: p.read_text(encoding="utf-8") for p in editor.rglob("*.py")}
            self.assertFalse(apply(editor))
            second = {p: p.read_text(encoding="utf-8") for p in editor.rglob("*.py")}
            self.assertEqual(first, second)

            for path in editor.rglob("*.py"):
                py_compile.compile(str(path), doraise=True)

            jobs_source = (editor / "core" / "jobs.py").read_text(encoding="utf-8")
            renderer_source = (editor / "core" / "renderer.py").read_text(encoding="utf-8")
            self.assertIn("def create_for_output", jobs_source)
            self.assertIn("os.replace(partial, target)", renderer_source)
            self.assertIn("partial.unlink(missing_ok=True)", renderer_source)
            self.assertNotIn("f\"{total_duration:.4f}\", opts.output]", renderer_source)


if __name__ == "__main__":
    unittest.main()
