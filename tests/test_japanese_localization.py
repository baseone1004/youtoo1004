import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import 채널_프로필 as profiles
import 썸네일_합성 as thumbs
import youtube_accounts as accounts
import 주제뽑기 as topics

class LocalizationTest(unittest.TestCase):
    def test_guidelines_native_and_korean_unchanged(self):
        with patch.object(profiles,"language_code",return_value="ja"):
            text=profiles.localize_guideline("현대 한국 a Korean office worker", "person")
            self.assertIn("현대 일본",text)
            self.assertIn("a Japanese",text)
            self.assertIn("です・ます",profiles.language_instruction("person"))
            self.assertEqual(profiles.benchmark_file(),"벤치_히트_일본.json")
            self.assertTrue(topics.is_korean("人間関係で疲れる理由"))
            self.assertFalse(topics.is_korean("인간관계 피로"))
            self.assertTrue(topics.관련있음("本音を言わない人の心理"))
            self.assertTrue(any("心理" in q for q in profiles.benchmark_queries()))
        with patch.object(profiles,"language_code",return_value="ko"):
            self.assertEqual(profiles.localize_guideline("현대 한국", "person"),"현대 한국")
            self.assertEqual(profiles.benchmark_file(),"벤치_히트.json")
    def test_account_language_selection_and_validation(self):
        cfg={}
        accounts.save_account(cfg,{"name":"Japan","url":"https://www.youtube.com/@japan","language":"ja","api_key":"private"})
        self.assertEqual(accounts.select_account(cfg,cfg["유튜브_계정"][0]["id"],"person"),"ja")
        self.assertEqual(accounts.public_accounts(cfg)[0]["language"],"ja")
        self.assertNotIn("private",str(accounts.public_accounts(cfg)))
        with self.assertRaises(ValueError): accounts.save_account(cfg,{"name":"Bad","url":"https://www.youtube.com/@bad","language":"bad"})
    def test_japanese_brand_and_local_render(self):
        with patch.object(profiles,"language_code",return_value="ja"), patch.object(profiles,"get",return_value={"이름":"마음","브랜드":{"주색":"#000000"}}):
            brand=profiles.brand("person")
        self.assertEqual(brand["주색"],"#18344A")
        self.assertEqual(brand["언어"],"ja")
        self.assertEqual(brand["배지"],"心の話")
        with tempfile.TemporaryDirectory() as td:
            src=Path(td,"source.png"); out=Path(td,"thumb.jpg")
            Image.new("RGB",(1280,720),"#FFF8ED").save(src)
            self.assertEqual(thumbs.compose(str(src),str(out),"いい人なのに","一緒にいると疲れる",brand=brand,layout="jalnan_pop"),"jp_pop")
            with Image.open(out) as im: self.assertEqual(im.size,(1280,720))
            self.assertFalse(thumbs._JAPANESE.get())
    def test_japanese_wrap_avoids_leading_punctuation(self):
        token=thumbs._JAPANESE.set(True)
        try:
            lines=thumbs._wrap("心が疲れる、そんな日はゆっくり休もう。",8)
            self.assertEqual("".join(lines),"心が疲れる、そんな日はゆっくり休もう。")
            self.assertNotIn(lines[1][0],"、。！？）」』】")
        finally: thumbs._JAPANESE.reset(token)
