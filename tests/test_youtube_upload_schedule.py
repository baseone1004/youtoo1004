import datetime as dt
import tempfile
import unittest
from pathlib import Path

import youtube_upload_schedule as schedule


class UploadScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = dt.datetime.now().replace(second=0, microsecond=0)

    def tearDown(self):
        self.temp.cleanup()

    def add(self, *, account="account-1", minutes=5, package="package-1", **kwargs):
        return schedule.add(
            self.root,
            package_id=package,
            package_name=package,
            folder=self.root / package,
            account_id=account,
            channel_name=account,
            scheduled_at=(self.now + dt.timedelta(minutes=minutes)).isoformat(timespec="minutes"),
            include_shorts=kwargs.get("include_shorts", True),
        )

    def test_reservations_are_private_and_limit_two_long_forms_per_channel_day(self):
        first = self.add(package="one")
        second = self.add(package="two", minutes=10)

        self.assertEqual(first["visibility"], "private")
        self.assertEqual(second["visibility"], "private")
        with self.assertRaisesRegex(ValueError, "하루 롱폼 2편"):
            self.add(package="three", minutes=15)

    def test_daily_limit_is_per_account_and_cancelled_reservation_does_not_consume_slot(self):
        first = self.add(package="one")
        self.add(package="two", minutes=10)
        schedule.cancel(self.root, first["id"])

        allowed = self.add(package="three", minutes=15)
        another_channel = self.add(account="account-2", package="four", minutes=20)
        self.assertEqual(allowed["status"], "pending")
        self.assertEqual(another_channel["account_id"], "account-2")

    def test_only_pending_reservations_can_be_cancelled(self):
        reserved = self.add()
        claimed = schedule.claim_due(
            self.root, now=self.now + dt.timedelta(minutes=6)
        )

        self.assertEqual(claimed["id"], reserved["id"])
        with self.assertRaisesRegex(ValueError, "취소할 수 없습니다"):
            schedule.cancel(self.root, reserved["id"])

    def test_interrupted_upload_requires_manual_review_and_is_not_reclaimed(self):
        self.add()
        schedule.claim_due(self.root, now=self.now + dt.timedelta(minutes=6))

        self.assertEqual(schedule.recover_interrupted(self.root), 1)
        row = schedule.list_items(self.root)[0]
        self.assertEqual(row["status"], "attention")
        self.assertIsNone(schedule.claim_due(self.root, now=self.now + dt.timedelta(days=1)))

    def test_duplicate_active_package_is_rejected_but_cancelled_package_can_be_rebooked(self):
        reserved = self.add()
        with self.assertRaisesRegex(ValueError, "이미 예약"):
            self.add(minutes=15)

        schedule.cancel(self.root, reserved["id"])
        replacement = self.add(minutes=15)
        self.assertNotEqual(replacement["id"], reserved["id"])


if __name__ == "__main__":
    unittest.main()
