"""Tests for the analyzer's pure helpers.

Run with:  python -m unittest discover -s desktop_worker/analyzer

Deliberately stdlib unittest and deliberately model-free: frame selection,
summary wording and the dashcam heuristic are all decidable from plain data, so
these run on any host in milliseconds without torch, weights, or a video file.
"""

import unittest

import detection


def _detection(counts: dict, sampled: int) -> dict:
    """A detection result shaped like detect() returns."""
    return {
        'tags': sorted(counts, key=lambda label: (-counts[label], label)),
        'counts': counts,
        'frames_sampled': sampled,
    }


class FrameIndicesTests(unittest.TestCase):
    def test_samples_about_one_frame_per_second(self):
        # 10s at 30fps -> ~10 frames
        indices = detection.frame_indices(300, 30.0, sample_fps=1.0, max_frames=300)
        self.assertEqual(len(indices), 10)

    def test_spans_the_whole_video(self):
        indices = detection.frame_indices(300, 30.0, sample_fps=1.0, max_frames=300)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 299)

    def test_cap_spreads_across_the_video_rather_than_truncating(self):
        # A long video must still be covered end to end, just more coarsely --
        # truncating would analyse only the opening minutes.
        indices = detection.frame_indices(100000, 30.0, sample_fps=1.0, max_frames=50)
        self.assertEqual(len(indices), 50)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 99999)

    def test_handles_a_video_reporting_no_fps(self):
        self.assertTrue(detection.frame_indices(300, 0.0, 1.0, 300))

    def test_empty_video_yields_nothing(self):
        self.assertEqual(detection.frame_indices(0, 30.0, 1.0, 300), [])

    def test_single_frame_video(self):
        self.assertEqual(detection.frame_indices(1, 30.0, 1.0, 300), [0])


class OrientationTests(unittest.TestCase):
    def test_landscape_portrait_and_square(self):
        self.assertEqual(detection.orientation_of({'width': 1920, 'height': 1080}), 'landscape')
        self.assertEqual(detection.orientation_of({'width': 576, 'height': 1024}), 'portrait')
        self.assertEqual(detection.orientation_of({'width': 512, 'height': 512}), 'square')

    def test_missing_dimensions_are_unknown_not_guessed(self):
        self.assertEqual(detection.orientation_of({}), 'unknown')
        self.assertEqual(detection.orientation_of({'width': 0, 'height': 1080}), 'unknown')


class ClassifyFootageTests(unittest.TestCase):
    LANDSCAPE = {'width': 1920, 'height': 1080}
    PORTRAIT = {'width': 576, 'height': 1024}

    def test_road_objects_throughout_a_landscape_video_look_like_dashcam(self):
        # The real 'Sample Clip': car in every sampled frame, 1920x1080.
        result = detection.classify_footage(
            self.LANDSCAPE, _detection({'car': 9, 'traffic light': 4, 'truck': 1}, 9))

        self.assertTrue(result['looks_like_dashcam'])
        self.assertEqual(result['orientation'], 'landscape')
        self.assertIn('car', result['road_classes_detected'])

    def test_a_teddy_bear_in_portrait_does_not(self):
        # The real 'Timmy Cheese': one non-road class, 576x1024.
        result = detection.classify_footage(
            self.PORTRAIT, _detection({'teddy bear': 12}, 12))

        self.assertFalse(result['looks_like_dashcam'])
        self.assertEqual(result['reason'], 'no road objects detected')
        self.assertEqual(result['road_classes_detected'], [])

    def test_road_objects_in_portrait_are_refused(self):
        # A phone in a car mount. Real road footage, but not what we are
        # detecting, and being wrong here would mislabel someone's upload.
        result = detection.classify_footage(
            self.PORTRAIT, _detection({'car': 10}, 10))

        self.assertFalse(result['looks_like_dashcam'])
        self.assertIn('orientation is portrait', result['reason'])

    def test_a_glimpsed_car_is_not_a_road_scene(self):
        # One parked car in the background of a landscape video.
        result = detection.classify_footage(
            self.LANDSCAPE, _detection({'car': 1, 'teddy bear': 9}, 10))

        self.assertFalse(result['looks_like_dashcam'])
        self.assertIn('intermittently', result['reason'])

    def test_people_alone_are_not_road_evidence(self):
        # 'person' appears in road footage and in everything else, so it must
        # not tip the balance on its own.
        result = detection.classify_footage(
            self.LANDSCAPE, _detection({'person': 10}, 10))

        self.assertFalse(result['looks_like_dashcam'])
        self.assertEqual(result['road_classes_detected'], [])

    def test_nothing_detected_is_not_a_dashcam(self):
        result = detection.classify_footage(self.LANDSCAPE, _detection({}, 8))
        self.assertFalse(result['looks_like_dashcam'])


class SummarizeTests(unittest.TestCase):
    METADATA = {'width': 576, 'height': 1024, 'duration_seconds': 11}

    def test_does_not_call_everything_a_dashcam_clip(self):
        # It used to. A portrait video of a teddy bear was described as a
        # "576x1024 dashcam clip", which the analyzer had no basis to claim.
        summary = detection.summarize(self.METADATA, _detection({'teddy bear': 12}, 12))
        self.assertNotIn('dashcam', summary)
        self.assertIn('576x1024 clip', summary)

    def test_says_so_when_nothing_was_detected(self):
        summary = detection.summarize(self.METADATA, _detection({}, 8))
        self.assertIn('No recognisable objects', summary)

    def test_qualifies_by_how_often_a_class_appeared(self):
        summary = detection.summarize(
            {'width': 1920, 'height': 1080, 'duration_seconds': 9},
            _detection({'car': 9, 'truck': 1}, 9))

        self.assertIn('car (throughout)', summary)
        self.assertIn('truck (briefly)', summary)

    def test_formats_durations_over_a_minute(self):
        summary = detection.summarize(
            {'width': 640, 'height': 480, 'duration_seconds': 125},
            _detection({'car': 5}, 5))
        self.assertIn('2m 05s', summary)


if __name__ == '__main__':
    unittest.main()
