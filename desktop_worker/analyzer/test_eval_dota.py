"""Tests for the DoTA fused-rule evaluation harness (eval/dota.py)."""

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'eval'))
_spec = importlib.util.spec_from_file_location('dota', Path(__file__).parent / 'eval' / 'dota.py')
dota = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dota)

CLIP = {'video_start': 300, 'video_end': 399, 'anomaly_start': 40, 'anomaly_end': 70,
        'anomaly_class': 'ego: lateral', 'num_frames': 100}


def _record(event_start=12.0, event_end=15.0, duration=30.0, knock_at=13.0, bang_at=13.0, bang_db=9.0):
    """A clip panning steadily, knocked once, with one loud sound."""
    shifts = []
    t = 0.0
    while t < duration:
        dx = 1.0 + (0.05 if int(t * 10) % 2 else -0.05)   # a little ordinary wobble
        if abs(t - knock_at) < 0.05:
            dx += 15.0
        shifts.append([round(t, 2), dx, 0.0, 0.8])
        t += 0.1
    rises = [[round(i * 0.1, 1), 1.0, -30.0] for i in range(int(duration * 10))]
    rises[int(bang_at * 10)] = [bang_at, bang_db, -8.0]
    return {'id': 'x', 'duration': duration, 'has_audio': True, 'rises': rises,
            'shifts': shifts, 'cuts': [], 'event_start': event_start, 'event_end': event_end}


class TimeTests(unittest.TestCase):
    def test_frames_are_every_third_source_frame(self):
        t = dota.clip_times(CLIP, fps=30.0)
        # Clip starts at 300 * 3 / 30 = 30 s; the section starts 5 s earlier.
        self.assertAlmostEqual(t['section_start'], 25.0)
        self.assertAlmostEqual(t['event_start'], 5.0 + 4.0)    # anomaly frame 40 -> 4 s in
        self.assertAlmostEqual(t['event_end'], 5.0 + 7.0)
        self.assertAlmostEqual(t['section_duration'], 5.0 + 10.0 + 5.0)

    def test_a_25_fps_source_stretches_the_timeline(self):
        self.assertAlmostEqual(dota.clip_times(CLIP, fps=25.0)['section_start'], 300 * 3 / 25 - 5)

    def test_only_ego_clips_by_default(self):
        self.assertTrue(CLIP['anomaly_class'].startswith('ego'))


class ScoreTests(unittest.TestCase):
    def test_sound_and_jolt_together_on_the_anomaly_is_a_hit(self):
        record = _record()
        found, _ = dota.run_rule(record)
        self.assertEqual(len(found), 1)
        self.assertTrue(dota.on_event(found[0], record))
        self.assertEqual(dota.kind(found[0]), 'both')
        self.assertEqual(dota.summarise([record])['moment_on_anomaly'], 1.0)

    def test_the_pair_away_from_the_anomaly_is_a_false_moment(self):
        record = _record(knock_at=24.0, bang_at=24.0)
        summary = dota.summarise([record])
        self.assertEqual(summary['moment_on_anomaly'], 0.0)
        self.assertGreater(summary['false_moments_per_hour'], 0)

    def test_a_silent_clip_is_not_counted(self):
        record = {**_record(), 'has_audio': False, 'rises': []}
        self.assertEqual(dota.summarise([record])['with_audio'], 0)


if __name__ == '__main__':
    unittest.main()
