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
            self.assertEqual(layout, "navy_mint")
            self.assertEqual(Image.open(out).size, (1280, 720))

    def test_mindam_uses_band_layout(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "썸네일_1.jpg"
            self.assertEqual(T.compose(self._raw(Path(td)), str(out), "기억은 줄고", "시간은 달린다", "mindam"), "hanji_seal")
            self.assertTrue(out.is_file())

    def test_every_layout_renders_with_custom_brand(self):
        brand = {"주색": "#112233", "강조색": "#33CCAA", "바탕색": "#FFF8E8", "보조색": "#CC3322", "배지": "내채널", "사진_톤": "sepia"}
        with tempfile.TemporaryDirectory() as td:
            raw = self._raw(Path(td))
            for name in T.레이아웃_이름:
                out = Path(td) / f"{name}.jpg"
                got = T.compose(raw, str(out), "짧은 윗줄", "조금 더 긴 아랫줄 문구입니다", "person", layout=name, brand=brand)
                self.assertEqual(got, "bottom" if name == "bottom_two" else name)
                self.assertEqual(Image.open(out).size, (1280, 720))



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
