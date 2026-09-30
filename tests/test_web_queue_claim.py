# -*- coding: utf-8 -*-
import time
import unittest
from unittest.mock import patch

import 웹큐


class WebQueueClaimTest(unittest.TestCase):
    def setUp(self) -> None:
        with 웹큐._event:
            웹큐._jobs.clear()

    def tearDown(self) -> None:
        with 웹큐._event:
            웹큐._jobs.clear()

    def test_only_current_lease_can_heartbeat_or_finish(self) -> None:
        jid = 웹큐.submit("request")
        first = 웹큐.next_job(0)
        self.assertEqual(first["id"], jid)
        self.assertTrue(웹큐.heartbeat(jid, "working", first["claim"]))
        self.assertFalse(웹큐.finish(jid, "late", claim="wrong-claim"))

        with 웹큐._event:
            expired = time.time() - 웹큐.LEASE_SEC - 1
            웹큐._jobs[jid]["beat"] = expired
            웹큐._jobs[jid]["taken_at"] = expired
        second = 웹큐.next_job(0)
        self.assertNotEqual(first["claim"], second["claim"])
        self.assertFalse(웹큐.heartbeat(jid, "stale", first["claim"]))
        self.assertFalse(웹큐.finish(jid, "stale result", claim=first["claim"]))
        self.assertTrue(웹큐.finish(jid, "current result", claim=second["claim"]))
        self.assertEqual(웹큐._jobs[jid]["result"], "current result")

    def test_legacy_extension_is_allowed_only_on_first_lease(self) -> None:
        jid = 웹큐.submit("request")
        웹큐.next_job(0)
        self.assertTrue(웹큐.heartbeat(jid))
        with 웹큐._event:
            expired = time.time() - 웹큐.LEASE_SEC - 1
            웹큐._jobs[jid]["beat"] = expired
            웹큐._jobs[jid]["taken_at"] = expired
        웹큐.next_job(0)
        self.assertFalse(웹큐.heartbeat(jid))
        self.assertFalse(웹큐.finish(jid, "stale legacy result"))


if __name__ == "__main__":
    unittest.main()
