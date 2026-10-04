import unittest
import 공통_api as api

class SupportedProviderTest(unittest.TestCase):
    def test_removed_provider_cannot_be_used_as_fallback(self):
        self.assertNotIn("claude", api.PROVIDERS)
        self.assertEqual(api.fallback_candidates({"API_키_claude": "old", "API_키_gemini": "new"}, "deepseek"), ["gemini"])

    def test_legacy_selection_cannot_send_old_key_to_new_provider(self):
        source = {"AI": "claude", "API_키": "old", "모델": "old-model", "인월드_API_키": "voice"}
        updated = api.normalize_provider_config(source)
        self.assertEqual(updated["AI"], "deepseek-web")
        self.assertEqual(updated["API_키"], "")
        self.assertEqual(updated["모델"], "")
        self.assertEqual(updated["인월드_API_키"], "voice")
        self.assertEqual(source["AI"], "claude")
