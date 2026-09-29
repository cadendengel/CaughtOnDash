"""Tests for the Nexar evaluation harness (eval/nexar.py).

The scoring is checked on hand-built records; measurement on a small synthetic
clip whose camera pans steadily and is knocked once, at 3 s.
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

_spec = importlib.util.spec_from_file_location('nexar', Path(__file__).parent / 'eval' / 'nexar.py')
nexar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nexar)


def _event(t, z):
    return {'t_seconds': t, 'z': z}


class LabelTests(unittest.TestCase):
    def test_reads_both_classes_and_matches_columns_loosely(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            for label, rows in (('positive', 'file_name,time_of_event,time_of_alert,weather\n00001.mp4,19.5,18.1,Rain\n'),
                                ('negative', 'file_name,time_of_event,time_of_alert,weather\n00002.mp4,,,Clear\n')):
                (data / 'train' / label).mkdir(parents=True)
                (data / 'train' / label / 'metadata.csv').write_text(rows, encoding='utf-8')

            clips = {c['id']: c for c in nexar.read_labels(data)}

        self.assertEqual(clips['00001']['label'], 'positive')
        self.assertEqual(clips['00001']['time_of_event'], 19.5)
        self.assertEqual(clips['00001']['extra'], {'weather': 'Rain'})
        self.assertIsNone(clips['00002']['time_of_event'])


class OutcomeTests(unittest.TestCase):
    POSITIVE = {'label': 'positive', 'time_of_event': 20.0}

    def test_a_jolt_at_the_event_is_a_hit(self):
        outcome = nexar.clip_outcome(self.POSITIVE, [_event(20.4, 9.0)], threshold=8, window=1.0)
        self.assertEqual(outcome, {'hit': True, 'false_alarms': 0, 'offset': 0.4})

    def test_a_jolt_elsewhere_in_a_crash_clip_is_a_false_alarm(self):
        outcome = nexar.clip_outcome(self.POSITIVE, [_event(5.0, 9.0)], threshold=8, window=1.0)
        self.assertEqual((outcome['hit'], outcome['false_alarms']), (False, 1))

    def test_below_the_threshold_nothing_fires(self):
        outcome = nexar.clip_outcome(self.POSITIVE, [_event(20.0, 5.0)], threshold=8, window=1.0)
        self.assertEqual((outcome['hit'], outcome['false_alarms']), (False, 0))

    def test_any_jolt_on_a_negative_is_a_false_alarm(self):
        outcome = nexar.clip_outcome({'label': 'negative'}, [_event(3.0, 12.0), _event(9.0, 4.0)],
                                     threshold=8, window=1.0)
        self.assertEqual((outcome['hit'], outcome['false_alarms']), (None, 1))


def _knocked_clip(path: str, seconds: float = 6.0, fps: float = 30.0, knock_at: float = 3.0):
    """A textured scene panning 1 px per frame, knocked sideways for one frame."""
    import cv2

    rng = np.random.default_rng(1)
    scene = cv2.GaussianBlur(rng.integers(0, 255, (400, 900), dtype=np.uint8), (0, 0), 2)
    scene = cv2.cvtColor(scene, cv2.COLOR_GRAY2BGR)
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (320, 180))
    knock = round(knock_at * fps)
    for index in range(round(seconds * fps)):
        x = 100 + index + (18 if knock <= index < knock + 3 else 0)
        writer.write(np.ascontiguousarray(scene[100:280, x:x + 320]))
    writer.release()


class MeasureTests(unittest.TestCase):
    def test_a_knock_is_found_at_its_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'knock.mp4')
            _knocked_clip(path)
            record = nexar.measure_one({'id': 'knock', 'label': 'positive', 'path': path,
                                        'time_of_event': 3.0, 'time_of_alert': None, 'extra': {}})

        self.assertNotIn('error', record)
        self.assertFalse(record['has_audio'])
        self.assertAlmostEqual(record['duration'], 6.0, places=1)
        summary = nexar.summarise([record], window=1.0)
        at_four = next(row for row in summary['sweep'] if row['z'] == 4)
        self.assertEqual(at_four['hit_rate'], 1.0)
        self.assertEqual(summary['positive_peak_z']['none'], 0)


if __name__ == '__main__':
    unittest.main()
