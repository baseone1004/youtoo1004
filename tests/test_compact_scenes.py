import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import 대본선택 as app
import scene_layout as layout


class CompactScenesTest(unittest.TestCase):
    def test_languages_have_150_contiguous_scenes_without_dropping_text(self):
        for text in ('오늘의 마음을 생각합니다.', '今日の気持ちを考えます。'):
            sentences = [f'{i} {text}' for i in range(413)]
            with tempfile.TemporaryDirectory() as td:
                units = layout.image_units(sentences, td, Path(td) / 'prompts.txt')
                self.assertEqual(len(units), 150)
                self.assertEqual([n for a, b, _ in units for n in range(a, b + 1)], list(range(1, 414)))
                self.assertEqual(' '.join(u[2] for u in units), ' '.join(sentences))
                Path(td, 'prompts.txt').write_text('===001===\n프롬프트: existing', encoding='utf-8')
                self.assertEqual(layout.image_units(sentences, td, Path(td) / 'prompts.txt'), units)

    def test_short_script_does_not_duplicate_scenes(self):
        self.assertEqual(layout.balanced_ranges(['短い。'] * 12), [(i, i) for i in range(1, 13)])
        self.assertEqual(layout.balanced_ranges([]), [])

    def test_existing_prompts_or_images_keep_original_numbering(self):
        for media in (False, True):
            with tempfile.TemporaryDirectory() as td:
                prompts = Path(td, 'prompts.txt')
                if media:
                    Path(td, 'images').mkdir(); Path(td, 'images', '198.jpg').write_bytes(b'existing')
                else:
                    prompts.write_text('===001===\n프롬프트: existing', encoding='utf-8')
                units = layout.image_units(['text'] * 413, td, prompts)
                self.assertEqual(len(units), 413)
                self.assertFalse(Path(td, layout.PLAN_NAME).exists())

    def test_changed_script_cannot_reinterpret_paid_scene_numbers(self):
        with tempfile.TemporaryDirectory() as td:
            prompts = Path(td, 'prompts.txt')
            layout.image_units(['a'] * 300, td, prompts)
            prompts.write_text('===001===\n프롬프트: existing', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '대본이 바뀌었습니다'):
                layout.image_units(['b'] * 300, td, prompts)

    def test_split_subtitles_are_composed_and_updated_after_voice_regeneration(self):
        with tempfile.TemporaryDirectory() as td:
            flow = Path(td, '플로우.txt')
            units = [(1, 2, 'first'), (3, 3, 'second')]
            original = '1: 1-2\n2: 3-5\n3: 6-7\n'
            flow.write_text(original, encoding='utf-8')
            layout.write_image_flow(td, units)
            self.assertIn('1: 1-5\n2: 6-7', flow.read_text(encoding='utf-8'))
            self.assertEqual(Path(td, layout.SENTENCE_FLOW).read_text(encoding='utf-8'), original)
            layout.write_image_flow(td, units)
            self.assertIn('2: 6-7', flow.read_text(encoding='utf-8'))
            flow.write_text('1: 1\n2: 2\n3: 3-4\n', encoding='utf-8')
            layout.write_image_flow(td, units, fresh_narration=True)
            self.assertIn('1: 1-2\n2: 3-4', flow.read_text(encoding='utf-8'))

    def test_bad_sentence_flow_stops_before_overwriting(self):
        with tempfile.TemporaryDirectory() as td:
            flow = Path(td, '플로우.txt')
            flow.write_text('1: 1\n2: 3\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '빠지거나 겹친'):
                layout.write_image_flow(td, [(1, 2, 'text')])
            self.assertEqual(flow.read_text(encoding='utf-8'), '1: 1\n2: 3\n')

    def test_413_sentence_production_asks_five_batches_and_maps_all_826_cues(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td, 'script.txt')
            script.write_text('[대본]\n' + ' '.join(f'오늘 {i}번 이야기를 합니다.' for i in range(1, 414)), encoding='utf-8')
            assets = Path(app.assets_dir(str(script)))
            flow = assets / '플로우.txt'
            source = ''.join(f'{i}: {i*2-1}-{i*2}\n' for i in range(1, 414))
            flow.write_text(source, encoding='utf-8')
            srt = assets / '나레이션.srt'; srt.write_text('unchanged subtitles', encoding='utf-8')
            units = app.scene_units(str(script))
            calls = []
            class FakeAI:
                name = model = 'mock'
                def __init__(self, cfg): pass
                def cost_text(self): return '0'
                def ask(self, system, user):
                    a, b = map(int, re.search(r'\[이번 범위\] (\d+) ~ (\d+)', user).groups())
                    calls.append((a, b))
                    return '\n'.join(f'==={i:03d}===\n대사: {units[i-1][2]}\n프롬프트: illustration {i}' for i in range(a, b+1))
            with patch.object(app, 'AI', FakeAI), patch.object(app.대본생성, 'load_cfg', return_value={}), \
                 patch.object(app, 'read_guideline', return_value='mock'), patch.object(app, 'image_guideline_for', return_value='mock'):
                result = app.make_image_prompts(app.Job('prompts'), dict(script_file=str(script), chunk=30))
            self.assertEqual(result['scenes'], 150)
            self.assertEqual(calls, [(1,30),(31,60),(61,90),(91,120),(121,150)])
            self.assertTrue(app.prompts_complete(result['file'], str(script)))
            self.assertTrue(app.narration_matches(str(flow), str(script)))
            lines = flow.read_text(encoding='utf-8').splitlines()[1:]
            self.assertEqual(len(lines), 150)
            self.assertTrue(lines[-1].endswith('-826'))
            self.assertEqual(Path(assets, layout.SENTENCE_FLOW).read_text(encoding='utf-8'), source)
            self.assertEqual(srt.read_text(encoding='utf-8'), 'unchanged subtitles')
            # Simulate interruption after the first batch. Saved grouping and
            # subtitle ranges must survive while only the remaining 120 scenes
            # are requested again.
            prompts = Path(result['file'])
            blocks = app.prompt_blocks(prompts.read_text(encoding='utf-8'))
            prompts.write_text('\n'.join(f'==={i:03d}===\n{blocks[i]}' for i in range(1, 31)), encoding='utf-8')
            calls.clear()
            with patch.object(app, 'AI', FakeAI), patch.object(app.대본생성, 'load_cfg', return_value={}), \
                 patch.object(app, 'read_guideline', return_value='mock'), patch.object(app, 'image_guideline_for', return_value='mock'):
                app.make_image_prompts(app.Job('prompts'), dict(script_file=str(script), chunk=30))
            self.assertEqual(calls, [(31,60),(61,90),(91,120),(121,150)])
            self.assertTrue(app.prompts_complete(str(prompts), str(script)))
            self.assertEqual(flow.read_text(encoding='utf-8').splitlines()[1:], lines)
            images = assets / 'images'; images.mkdir()
            for i in range(1, 151):
                (images / f'{i:03d}.jpg').write_bytes(b'mock')
            report = app.production_library.require_render_media(flow, images)
            self.assertTrue(report['ok'])
            self.assertEqual(report['matched'], 150)


if __name__ == '__main__':
    unittest.main()
