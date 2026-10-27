import os
import unittest
from unittest.mock import patch

import 대본선택 as app


class AccountChangeSafetyTests(unittest.TestCase):
    def test_paused_queue_blocks_account_switch_before_profile_change(self):
        with patch.dict(os.environ, {"YOUTOO_ACCOUNT": ""}), \
             patch.object(app, "STATE", {"job": None}), \
             patch.object(app, "queue_snapshot", return_value={"status": "paused"}), \
             patch.object(app, "aip") as aip:
            with self.assertRaisesRegex(ValueError, "일시정지.*대기 중인 영상"):
                app.select_saved_account({"id": "japanese", "slot": "person"})
            aip.assert_not_called()

    def test_running_queue_blocks_account_management(self):
        with patch.object(app, "queue_snapshot", return_value={"status": "running"}):
            with self.assertRaisesRegex(ValueError, "대기열이 진행 중"):
                app.require_queue_idle_for_account_change()

    def test_idle_queue_allows_account_management(self):
        for status in ("idle", "done", "cancelled"):
            with self.subTest(status=status), patch.object(app, "queue_snapshot", return_value={"status": status}):
                app.require_queue_idle_for_account_change()


if __name__ == "__main__":
    unittest.main()
