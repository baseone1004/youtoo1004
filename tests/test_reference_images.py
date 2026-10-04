import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import reference_images as ref

class ReferenceTest(unittest.TestCase):
    def test_upload_is_normalized_and_channel_saved(self):
        data = io.BytesIO()
        Image.new("RGB", (50, 30), "red").save(data, format="JPEG")
        url = "data:image/jpeg;base64," + base64.b64encode(data.getvalue()).decode()
        with tempfile.TemporaryDirectory() as td, patch.object(ref, "ROOT", Path(td)), patch.object(ref.채널_프로필, "save", return_value={}) as save:
            ref.save_reference("mindam", url)
            saved = save.call_args.args[1]["마스코트"]
            self.assertTrue(saved["레퍼런스_사용"])
            with Image.open(Path(td, saved["이미지"])) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (50, 30))

    def test_invalid_upload_cannot_change_profile(self):
        with patch.object(ref.채널_프로필, "save") as save:
            for slot, data in [("../outside", "data:image/png;base64,eA=="), ("person", "data:image/png;base64,eA=="), ("person", "data:text/plain;base64,eA=="), ("person", "x" * (ref.MAX_BYTES * 2))]:
                with self.assertRaises(ValueError):
                    ref.save_reference(slot, data)
            save.assert_not_called()

    def test_disabled_reference_uses_default_model(self):
        with patch.object(ref.채널_프로필, "get", return_value={"마스코트": {"이미지": "missing.png", "레퍼런스_사용": False}}):
            self.assertEqual(ref.reference_options("person"), {})

    def test_enabled_missing_reference_does_not_silently_fallback(self):
        with patch.object(ref.채널_프로필, "get", return_value={"마스코트": {"이미지": "missing.png", "레퍼런스_사용": True}}):
            with self.assertRaises(ValueError):
                ref.reference_options("person")
