import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import japanese_description as description
import 대본선택 as app
import 채널_프로필 as profiles


class JapaneseDescriptionTest(unittest.TestCase):
    def test_japanese_footer_spacing_and_idempotence(self):
        value = description.format_description('親と話していて、気持ちがすれ違うことはありませんか。\n\n今回の動画では、親子の会話を考えます。\n#親子 #親子', '親子, 会話, 마음')
        self.assertEqual(description.format_description(value, '親子, 会話'), value)
        self.assertEqual(value.count(description.AI_NOTICE), 1)
        self.assertIn(description.DIVIDER + '\n\n\n\n#親子 #会話', value)
        self.assertNotIn('#마음', value)
        self.assertIn('コメント', value)

    def test_only_description_block_changes(self):
        source = '[제목]\n会話の距離\n[설명글]\n説明。\n[태그]\n会話, 心理\n[대본]\n台本です。'
        result = description.format_metadata(source)
        self.assertTrue(result.startswith('[제목]\n会話の距離\n[설명글]\n説明。'))
        self.assertTrue(result.endswith('[태그]\n会話, 心理\n[대본]\n台本です。'))
        self.assertEqual(description.format_metadata(result), result)

    def test_japanese_txt_and_korean_description_remain_separate(self):
        with tempfile.TemporaryDirectory() as td:
            path = app.write_upload_texts(td, dict(title='会話', description='親と話す。', tags='会話', language='ja'), separate=True)
            text = Path(path).read_text(encoding='utf-8')
            self.assertIn(description.AI_NOTICE, text)
            self.assertEqual(app.upload_description('한국 설명', '심리', 'ko'), '한국 설명\n\n\n\n#심리')

    def test_japanese_rules_not_applied_to_korean_channel(self):
        with patch.object(profiles, 'language_code', return_value='ja'):
            self.assertIn(description.RULES, profiles.language_instruction('person'))
        with patch.object(profiles, 'language_code', return_value='ko'):
            self.assertNotIn(description.RULES, profiles.language_instruction('person'))


if __name__ == '__main__':
    unittest.main()
