"""Tests for candidate-moment scoring.

Synthetic audio and shift series, so these need no video, no ffmpeg and no
model. The numbers in the fusion tests are the ones measured on the
2026-09-26 collision clip.
"""

import unittest

import numpy as np

import moments

RATE = moments.AUDIO_RATE


def _noise(seconds: float, level: float = 0.1, seed: int = 0):
    return np.random.default_rng(seed).uniform(-level, level, int(seconds * RATE)).astype(np.float32)


def _audio(t: float, rise_db: float, strength: float) -> dict:
    return {'t_seconds': t, 'peak_dbfs': -12.7, 'local_median_dbfs': -12.7 - rise_db,
            'rise_db': rise_db, 'strength': strength}


def _jolt(t: float, z: float, strength: float) -> dict:
    return {'t_seconds': t, 'jerk_px': 1.2, 'z': z, 'strength': strength}


class AudioTests(unittest.TestCase):
    def test_a_bang_in_steady_noise_is_found_where_it_happened(self):
        samples = _noise(10)
        samples[int(6.0 * RATE):int(6.02 * RATE)] = 0.9
        events = moments.audio_rises(samples)
        strongest = max(events, key=lambda e: e['rise_db'])
        self.assertAlmostEqual(strongest['t_seconds'], 6.0, places=1)
        self.assertGreater(strongest['rise_db'], moments.AUDIO_FULL_RISE_DB)
        self.assertEqual(strongest['strength'], 1.0)

    def test_steady_noise_alone_raises_nothing_strong(self):
        events = moments.audio_rises(_noise(10))
        self.assertTrue(all(e['strength'] < 0.3 for e in events))

    def test_loud_but_steady_audio_is_not_an_event(self):
        # Wind or road roar: loud throughout, so never loud relative to itself.
        events = moments.audio_rises(_noise(10, level=0.8))
        self.assertTrue(all(e['strength'] < 0.3 for e in events))

    def test_silence_is_no_audio_rather_than_no_events(self):
        self.assertEqual(moments.audio_rises(np.zeros(RATE * 5, dtype=np.float32)), [])
        self.assertEqual(moments.audio_rises(np.zeros(10, dtype=np.float32)), [])


class JoltTests(unittest.TestCase):
    def _shifts(self, spikes=(), response=0.5):
        rng = np.random.default_rng(1)
        rows = [(i / 10, float(rng.normal(0, 0.05)), float(rng.normal(0, 0.05)), response) for i in range(300)]
        for index, size in spikes:
            t, dx, dy, r = rows[index]
            rows[index] = (t, dx + size, dy + size, r)
        return rows

    def test_a_sudden_shift_is_found(self):
        events = moments.jolt_scores(self._shifts(spikes=[(150, 1.5)]))
        strongest = max(events, key=lambda e: e['z'])
        # A one-sample spike changes the shift twice -- into it and out of it.
        self.assertLessEqual(abs(strongest['t_seconds'] - 15.0), 0.1 + 1e-9)
        self.assertEqual(strongest['strength'], 1.0)

    def test_smooth_driving_raises_nothing_strong(self):
        self.assertTrue(all(e['strength'] < 0.5 for e in moments.jolt_scores(self._shifts())))

    def test_unreliable_correlations_are_ignored(self):
        # A vehicle filling the frame: large "shifts" nobody should trust.
        self.assertEqual(moments.jolt_scores(self._shifts(spikes=[(150, 5)], response=0.05)), [])


class FusionTests(unittest.TestCase):
    def test_the_collision_agrees_on_both_signals(self):
        found = moments.find_moments([_audio(26.1, 6.6, 0.717)], [_jolt(26.2, 15.2, 1.0)])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['t_seconds'], 26.1)  # the audio locates it more precisely
        self.assertIn('both signals agree within a second', found[0]['reasons'])

    def test_the_car_carrier_pass_is_below_the_bar_but_still_possible(self):
        audio, jolt = [_audio(15.7, 3.5, 0.1)], [_jolt(16.5, 9.2, 0.65)]
        self.assertEqual(moments.find_moments(audio, jolt), [])
        possible = moments.find_moments(audio, jolt, 3, moments.MIN_POSSIBLE_SCORE)
        self.assertEqual([m['t_seconds'] for m in possible], [15.7])

    def test_signals_more_than_a_second_apart_do_not_combine(self):
        self.assertEqual(moments.find_moments([_audio(10.0, 5, 0.4)], [_jolt(12.0, 9, 0.6)]), [])

    def test_one_signal_alone_is_only_possible(self):
        # The production corpus: every jolt-only "moment" was a turn, a bump, a
        # cut or a stutter. The collision's jolt with its sound stripped is
        # still listed -- as worth a glance, not as a moment.
        self.assertEqual(moments.find_moments([], [_jolt(26.2, 15.2, 1.0)]), [])
        possible = moments.find_moments([], [_jolt(26.2, 15.2, 1.0)], 3, moments.MIN_POSSIBLE_SCORE)
        self.assertEqual([(m['t_seconds'], m['score']) for m in possible], [(26.2, 0.7)])
        # And a loud sound alone -- a TikTok end-card jingle -- likewise.
        self.assertEqual(moments.find_moments([_audio(7.6, 10.6, 1.0)], []), [])

    def test_the_collision_still_scores_high(self):
        found = moments.find_moments([_audio(26.1, 6.6, 0.717)], [_jolt(26.2, 15.2, 1.0)])
        self.assertEqual([(m['t_seconds'], m['score']) for m in found], [(26.1, 0.91)])

    def test_nearby_candidates_collapse_to_the_strongest(self):
        # 21.5 is backed by both signals and outscores 20.0, three seconds or
        # less away, which is dropped rather than reported as a second moment.
        audio = [_audio(20.0, 5, 0.5), _audio(21.5, 6, 0.6), _audio(40.0, 8, 0.9)]
        jolt = [_jolt(21.4, 12, 1.0), _jolt(40.2, 12, 1.0)]
        found = moments.find_moments(audio, jolt)
        self.assertEqual([m['t_seconds'] for m in found], [21.5, 40.0])

    def test_possible_list_skips_what_was_already_reported(self):
        audio = [_audio(20.0, 8, 1.0), _audio(21.0, 4, 0.55)]
        jolt = [_jolt(20.1, 12, 1.0)]
        reported = moments.find_moments(audio, jolt)
        possible = moments.find_moments(audio, jolt, 3, moments.MIN_POSSIBLE_SCORE, exclude=reported)
        self.assertEqual(possible, [])

    def test_at_most_max_moments_ordered_by_time(self):
        times = (50.0, 10.0, 30.0)
        found = moments.find_moments([_audio(t, 8, 1.0) for t in times], [_jolt(t, 12, 1.0) for t in times],
                                     max_moments=2)
        self.assertEqual(len(found), 2)
        self.assertEqual(found, sorted(found, key=lambda m: m['t_seconds']))


class CorpusFailureModeTests(unittest.TestCase):
    """The false alarms found reviewing the production corpus, one each."""

    def test_the_first_second_and_last_half_second_are_ignored(self):
        events = [_jolt(t, 12, 1.0) for t in (0.2, 0.4, 0.9, 1.2, 23.4, 23.7)]
        kept = moments.usable_events(events, duration=24.0, cuts=[])
        self.assertEqual([e['t_seconds'] for e in kept], [1.2, 23.4])

    def test_events_beside_a_scene_cut_are_ignored(self):
        events = [_jolt(5.81, 43.7, 1.0), _audio(7.6, 10.6, 1.0), _jolt(12.0, 12, 1.0)]
        kept = moments.usable_events(events, duration=20.0, cuts=[5.9, 7.7])
        self.assertEqual([e['t_seconds'] for e in kept], [12.0])

    def test_no_jerk_is_measured_across_a_gap(self):
        # Steady motion, then a sample dropped (duplicate, cut or unreliable
        # pair), then steady motion in another direction. Compared across the
        # gap that would look like a jolt; it is not measured.
        before = [(i / 10, 1.0, 0.0, 0.5) for i in range(20)]
        after = [(3.0 + i / 10, -3.0, 2.0, 0.5) for i in range(20)]
        self.assertEqual(moments.jolt_scores(before + after), [])

    def test_the_burst_window_shifts_at_the_edges(self):
        self.assertEqual(moments.burst_window(0.3, 24.0), (0.0, 2.0))      # not 0.0 repeated
        self.assertEqual(moments.burst_window(12.0, 24.0), (11.0, 13.0))
        self.assertEqual(moments.burst_window(23.8, 24.0), (22.0, 24.0))
        self.assertEqual(moments.burst_window(0.5, 1.2), (0.0, 1.2))


class _Box:
    def __init__(self, cls, conf, xyxy):
        self.cls, self.conf = cls, conf
        self.xyxy = np.array([xyxy], dtype=float)


class _Prediction:
    names = {0: 'car', 1: 'truck', 2: 'person'}

    def __init__(self, boxes):
        self.boxes = boxes


class _FakeModel:
    """Stands in for YOLO: a truck that grows as the clip goes on, plus a
    person bigger than anything, who must be ignored."""

    def __init__(self):
        self.calls = 0

    def predict(self, frame, **kwargs):
        self.calls += 1
        grow = min(self.calls, 15)
        return [_Prediction([
            _Box(1, 0.8, (10, 10, 10 + 4 * grow, 10 + 2 * grow)),
            _Box(2, 0.9, (0, 0, frame.shape[1], frame.shape[0])),
        ])]


class ClosestApproachTests(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile

        import cv2

        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, 'clip.avi')
        writer = cv2.VideoWriter(self.path, cv2.VideoWriter_fourcc(*'MJPG'), 10, (160, 120))
        rng = np.random.default_rng(2)
        for _ in range(60):
            writer.write(rng.integers(0, 255, (120, 160, 3), dtype=np.uint8))
        writer.release()
        self.metadata = {'fps': 10.0, 'frame_count': 60}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_largest_vehicle_is_measured_and_cropped_in_file_pixels(self):
        import cv2

        capture = cv2.VideoCapture(self.path)
        try:
            # A 20px letterbox on the left and 10px on top.
            box = {'x': 20, 'y': 10, 'width': 140, 'height': 110}
            result = moments.closest_approach(
                capture, _FakeModel(), 'cpu', {'t_seconds': 3.0}, self.metadata, box)
        finally:
            capture.release()

        observation = result['observation']
        self.assertEqual(observation['label'], 'truck')  # the person is not a vehicle
        x, y, w, h = observation['bbox']
        self.assertEqual((x, y), (30, 20))               # offset back into the file's pixels
        self.assertGreaterEqual(w, 0.8 * 60)             # near the largest it grew to
        self.assertEqual(result['crop'].shape[:2], (h, w))
        self.assertLessEqual(observation['frame_share'], observation['largest_frame_share'])

    def test_a_distant_vehicle_is_not_reported_as_the_closest(self):
        import cv2

        class Tiny:
            def predict(self, frame, **kwargs):
                return [_Prediction([_Box(0, 0.9, (5, 5, 25, 20))])]   # ~1.6% of the frame

        capture = cv2.VideoCapture(self.path)
        try:
            self.assertIsNone(moments.closest_approach(
                capture, Tiny(), 'cpu', {'t_seconds': 3.0}, self.metadata, None))
        finally:
            capture.release()

    def test_no_vehicles_means_no_observation(self):
        import cv2

        class Empty:
            def predict(self, frame, **kwargs):
                return [_Prediction([])]

        capture = cv2.VideoCapture(self.path)
        try:
            self.assertIsNone(moments.closest_approach(
                capture, Empty(), 'cpu', {'t_seconds': 3.0}, self.metadata, None))
        finally:
            capture.release()


if __name__ == '__main__':
    unittest.main()
