"""Tests for the NINA audio evaluation harness (eval/nina.py)."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

import moments

sys.path.insert(0, str(Path(__file__).parent / 'eval'))
_spec = importlib.util.spec_from_file_location('nina', Path(__file__).parent / 'eval' / 'nina.py')
nina = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nina)


class LabelTests(unittest.TestCase):
    def test_reads_audacity_labels_and_skips_frequency_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            labels = Path(tmp)
            # Audacity writes a backslash line with the frequency range under
            # each label, and some files use bare carriage returns.
            (labels / '-2GLBKAHlUM.txt').write_text(
                '5.87\t14.67\tCrash\n\\\t832.5\t832.5\r21.0\t25.6\thorn\n', encoding='utf-8')
            videos = nina.read_labels(labels)

        self.assertEqual(list(videos), ['-2GLBKAHlUM'])
        self.assertEqual(videos['-2GLBKAHlUM'], [
            {'start': 5.87, 'end': 14.67, 'cls': 'crash'},
            {'start': 21.0, 'end': 25.6, 'cls': 'horn'},
        ])


class ScoringTests(unittest.TestCase):
    SEGMENT = {'start': 10.0, 'end': 12.0, 'cls': 'crash'}

    def test_a_rise_inside_the_segment_or_its_slack_fires(self):
        self.assertTrue(nina.segment_fires(self.SEGMENT, [[11.0, 6.0]], threshold=5))
        self.assertTrue(nina.segment_fires(self.SEGMENT, [[12.4, 6.0]], threshold=5))
        self.assertFalse(nina.segment_fires(self.SEGMENT, [[13.0, 6.0]], threshold=5))
        self.assertFalse(nina.segment_fires(self.SEGMENT, [[11.0, 4.0]], threshold=5))

    def test_rate_counts_rises_per_second_inside_segments(self):
        segments = [('v', {'start': 0.0, 'end': 10.0, 'cls': 'driving'})]
        rises = {'v': [[1.0, 4.0], [2.0, 9.0], [15.0, 9.0]]}
        self.assertAlmostEqual(nina.rate_in(segments, rises, threshold=3), 0.2)
        self.assertAlmostEqual(nina.rate_in(segments, rises, threshold=8), 0.1)


class MeasureTests(unittest.TestCase):
    def test_every_rise_is_kept_and_the_threshold_restored(self):
        rng = np.random.default_rng(0)
        samples = rng.uniform(-0.05, 0.05, moments.AUDIO_RATE * 6).astype(np.float32)
        samples[3 * moments.AUDIO_RATE:3 * moments.AUDIO_RATE + 800] *= 20   # a bang at 3 s

        rises = nina.rises_at_floor(samples)

        self.assertEqual(moments.AUDIO_MIN_RISE_DB, 3.0)
        bang = max(rises, key=lambda e: e['rise_db'])
        self.assertAlmostEqual(bang['t_seconds'], 3.0, delta=0.1)
        self.assertGreater(bang['rise_db'], 20)
        self.assertTrue(any(e['rise_db'] < 3.0 for e in rises))   # below the usual floor, kept for sweeping


if __name__ == '__main__':
    unittest.main()
