# -*- coding: utf-8 -*-
"""[■ 중단] 하나로 충분하게: AI 응답을 기다리는 중에도 중단이 바로 먹고, 중단은 예비 AI 로 넘어가지 않는다."""
import threading
import time
import unittest
from unittest.mock import patch

import 공통_api
import 웹큐
import 대본선택 as app


class CancelDuringAITest(unittest.TestCase):
    def _web_ai(self):
        with patch.object(웹큐, "extension_alive", return_value=True):
            return 공통_api.AI({"AI": "deepseek-web", "API_키_gemini": "spare-key"})

    def test_cancel_while_waiting_for_web_reply(self):
        ai = self._web_ai()
        flag = {"stop": False}
        ai.cancel_check = lambda: flag["stop"]
        fallback_made = []
        with patch.object(공통_api.AI, "_make_fallback", side_effect=lambda *_a, **_k: fallback_made.append(1) or None), \
             patch.object(웹큐, "submit", return_value="jid"), patch.object(웹큐, "cancel"), \
             patch.object(웹큐, "wait", wraps=None) as w:
            def slow_wait(jid, timeout=1800, cancel_check=None):     # 답이 안 오는 상태 — 중단 요청만 본다
                for _ in range(200):
                    if cancel_check and cancel_check():
                        raise RuntimeError("취소됨")
                    time.sleep(0.01)
                return "늦은 답"
            w.side_effect = slow_wait
            threading.Timer(0.05, lambda: flag.update(stop=True)).start()
            t0 = time.time()
            with self.assertRaises(공통_api.Cancelled):
                ai.ask("지침", "요청")
        self.assertLess(time.time() - t0, 1.5)                        # 몇 초 안에 멈춘다
        self.assertEqual(fallback_made, [])                            # 중단은 예비 AI 로 넘어가지 않는다

    def test_module_hook_reads_current_job(self):
        job = app.Job("images")
        with patch.dict(app.STATE, {"job": job}):
            ai = self._web_ai()
            self.assertFalse(ai._cancelled())
            job.cancel_requested = True
            self.assertTrue(ai._cancelled())
            with self.assertRaises(공통_api.Cancelled):
                ai.ask("지침", "요청")                                   # 묻기 전에 이미 중단됨 → 바로 멈춘다
        self.assertTrue(job.to_dict()["cancel_requested"])


if __name__ == "__main__":
    unittest.main()
