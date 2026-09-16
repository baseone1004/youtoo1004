# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import 썸네일_합성 as T


class ThumbnailComposeTest(unittest.TestCase):
    def _raw(self, root):
        path = root / "raw.jpg"
        im = Image.new("RGB", (1600, 900), (60, 40, 30))
        im.paste((220, 180, 150), (1000, 100, 1500, 800))       # 오른쪽에 밝은 피사체
        im.save(path)
        return str(path)

    def test_person_uses_keyword_layout_and_writes_1280x720(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "썸네일_1.jpg"
            layout = T.compose(self._raw(Path(td)), str(out), "나이 들수록", "시간이 빨리 가는 진짜 이유", "person")
            self.assertEqual(layout, "keyword")
            self.assertEqual(Image.open(out).size, (1280, 720))

    def test_number_in_copy_uses_badge_layout(self):
        self.assertEqual(T.pick_layout("시간이 빨라진", "뇌의 비밀 3가지", "person"), ("badge", "3가지"))
        self.assertEqual(T.pick_layout("", "60대가 후회하는 5 가지", "person"), ("badge", "5가지"))

    def test_mindam_uses_band_layout(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "썸네일_1.jpg"
            self.assertEqual(T.compose(self._raw(Path(td)), str(out), "기억은 줄고", "시간은 달린다", "mindam"), "band")
            self.assertTrue(out.is_file())

    def test_tiers_split_trailing_words_into_tail(self):
        self.assertEqual(T._tiers("나이 들수록", "시간이 빨리 가는 진짜 이유"),
                         [("나이 들수록", "lead"), ("시간이 빨리 가는", "key"), ("진짜 이유", "tail")])
        self.assertEqual(T._tiers("돈 많은 사람들", "절대 말하지 않는 습관"),
                         [("돈 많은 사람들", "lead"), ("절대 말하지 않는", "key"), ("습관", "tail")])
        self.assertEqual(T._tiers("", "습관"), [("습관", "key")])


if __name__ == "__main__":
    unittest.main()


class FallbackThumbnailTest(unittest.TestCase):
    def test_scene_images_become_thumbnails_when_dropshot_fails(self):
        import 대본선택 as app
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "작업.txt"
            script.write_text("[제목]\n60대가 되면 후회하는 5가지\n[대본]\n본문", encoding="utf-8")
            images = root / "작업_자료" / "images"
            images.mkdir(parents=True)
            for i in range(1, 7):
                Image.new("RGB", (640, 360), (40 + i * 20, 60, 90)).save(images / f"{i:03d}.jpg")
            outs = app.fallback_thumbnails(str(script), str(images))
            self.assertEqual(len(outs), 3)
            self.assertTrue(all(Path(o).is_file() for o in outs))
            self.assertEqual(len(app.raw_thumbnails(str(root / "작업_자료" / "썸네일"))), 3)
