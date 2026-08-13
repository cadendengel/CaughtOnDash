"""Tests for the analyzer's pure helpers.

Run with:  python -m unittest discover -s desktop_worker/analyzer

Deliberately stdlib unittest and deliberately model-free: frame selection,
summary wording and the dashcam heuristic are all decidable from plain data, so
these run on any host in milliseconds without torch, weights, or a video file.
"""

import unittest

import detection


def _detection(counts: dict, sampled: int, egomotion: dict | None = None) -> dict:
    """A detection result shaped like detect() returns."""
    return {
        'tags': sorted(counts, key=lambda label: (-counts[label], label)),
        'counts': counts,
        'frames_sampled': sampled,
        'egomotion': egomotion if egomotion is not None else _egomotion(0.0, 0.0),
    }


def _egomotion(radiality: float, pixels: float) -> dict:
    """An egomotion summary, as summarize_egomotion builds it."""
    return {
        'radiality': radiality,
        'pixels_per_frame': pixels,
        'pairs_measured': 12,
        'looks_like_driving': (
            radiality >= detection.EGOMOTION_MIN_RADIALITY
            and pixels >= detection.EGOMOTION_MIN_PIXELS),
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
        # Wording changed with detect-3.0: orientation is the hard gate, so it
        # is named, and the object evidence is kept alongside it.
        self.assertEqual(result['reason'], 'no road objects detected, but orientation is portrait')
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


class BuildEventsTests(unittest.TestCase):
    """Events carry timestamps, so a wrong one sends a viewer to the wrong place.

    These are observations -- "a car was visible at 0:14" -- not incidents.
    Nothing here claims to know whether anything happened.
    """

    def test_an_event_marks_when_a_label_was_first_seen(self):
        events = detection.build_events(
            kept={'car': 3},
            appearances={'car': [4.0, 2.0, 9.5]},
            best_confidence={'car': 0.91},
            tags=['car'])

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['timestamp_seconds'], 2.0)
        self.assertEqual(event['last_seen_seconds'], 9.5)
        self.assertEqual(event['label'], 'car')
        self.assertEqual(event['confidence'], 0.91)
        self.assertEqual(event['frames_seen'], 3)

    def test_events_are_ordered_by_time(self):
        events = detection.build_events(
            kept={'car': 1, 'person': 1, 'dog': 1},
            appearances={'car': [8.0], 'person': [1.5], 'dog': [4.0]},
            best_confidence={'car': 0.8, 'person': 0.7, 'dog': 0.6},
            tags=['car', 'person', 'dog'])

        self.assertEqual([e['label'] for e in events], ['person', 'dog', 'car'])

    def test_a_label_with_no_recorded_appearance_is_skipped(self):
        # Rather than emit an event at 0.0, which would point a viewer at the
        # start of the clip for something never actually located.
        events = detection.build_events(
            kept={'car': 2}, appearances={}, best_confidence={'car': 0.5}, tags=['car'])

        self.assertEqual(events, [])

    def test_only_kept_tags_become_events(self):
        # Labels discarded for appearing too rarely must not come back as events.
        events = detection.build_events(
            kept={'car': 5},
            appearances={'car': [1.0], 'toaster': [2.0]},
            best_confidence={'car': 0.9, 'toaster': 0.4},
            tags=['car'])

        self.assertEqual([e['label'] for e in events], ['car'])


class FrameSecondsTests(unittest.TestCase):
    def test_converts_a_frame_index_to_seconds(self):
        self.assertEqual(detection._frame_seconds(60, {'fps': 30.0}), 2.0)
        self.assertEqual(detection._frame_seconds(45, {'fps': 30.0}), 1.5)

    def test_a_missing_or_zero_fps_does_not_divide_by_zero(self):
        # Some containers report no frame rate. A timestamp of 0 is wrong but
        # harmless; a ZeroDivisionError would fail the whole analysis.
        self.assertEqual(detection._frame_seconds(60, {}), 0.0)
        self.assertEqual(detection._frame_seconds(60, {'fps': 0}), 0.0)


if __name__ == '__main__':
    unittest.main()


class SamplingFloorTests(unittest.TestCase):
    """Short clips need enough frames for the dashcam verdict to mean anything.

    At one frame a second a five-second clip is judged on four frames, so a
    single detection moves the road-object share by 25 points. A real
    night-time dashcam clip was classified "not dashcam" off one detection in
    four frames.
    """

    def test_a_short_clip_is_sampled_to_the_floor(self):
        # 5 seconds at 60fps: one per second would give 5.
        indices = detection.frame_indices(300, 60.0, 1.0, 300)
        self.assertGreaterEqual(len(indices), 7)

    def test_a_long_clip_is_unaffected(self):
        # 60 seconds already exceeds the floor from duration alone.
        indices = detection.frame_indices(1800, 30.0, 1.0, 300)
        self.assertEqual(len(indices), 60)

    def test_it_never_asks_for_more_frames_than_exist(self):
        # A 3-frame video cannot yield 8 samples.
        indices = detection.frame_indices(3, 30.0, 1.0, 300)
        self.assertLessEqual(len(indices), 3)
        self.assertTrue(all(0 <= i < 3 for i in indices))

    def test_max_frames_still_caps_the_floor(self):
        indices = detection.frame_indices(300, 60.0, 1.0, 2)
        self.assertLessEqual(len(indices), 2)


class ConfidenceDefaultTests(unittest.TestCase):
    def test_the_default_admits_low_light_detections(self):
        """Measured, not guessed.

        On a real night clip the model scored cars at 0.35-0.5; a 0.5 gate
        discarded them and the footage read as "not dashcam". The negative
        case -- a portrait video of a teddy bear -- reports zero road objects
        at this threshold, so the sensitivity does not invent road scenes.
        """
        self.assertEqual(detection.DEFAULT_CONFIDENCE, 0.35)


class EgomotionTests(unittest.TestCase):
    """The second signal, and the cases that made it necessary."""

    LANDSCAPE = {'width': 1920, 'height': 1080}
    PORTRAIT = {'width': 576, 'height': 1024}

    # Measured on real clips, so a threshold change has to face them.
    EMPTY_ROAD = _egomotion(0.96, 8.89)        # road-day-1: no objects at all
    SLOWER_EMPTY_ROAD = _egomotion(0.95, 3.70)  # road-day-2: the weaker of the two
    GRIDLOCK = _egomotion(0.16, 9.03)           # dusk-traffic: full of cars, barely radial
    DRONE = _egomotion(0.87, 1.67)              # aerial: radial, but far too slow
    SLOW_ZOOM = _egomotion(0.998, 0.48)         # synthetic: perfectly radial, barely moving
    WALKING = _egomotion(0.55, 1.16)

    def test_an_empty_road_is_dashcam_footage_on_motion_alone(self):
        """The failure this signal exists for: nothing to detect, clearly driving."""
        result = detection.classify_footage(
            self.LANDSCAPE, _detection({}, 22, self.EMPTY_ROAD))

        self.assertTrue(result['looks_like_dashcam'])
        self.assertIn('camera moving with the road', result['reason'])

    def test_the_slower_empty_road_also_clears_the_bar(self):
        result = detection.classify_footage(
            self.LANDSCAPE, _detection({}, 22, self.SLOWER_EMPTY_ROAD))

        self.assertTrue(result['looks_like_dashcam'])

    def test_gridlock_still_passes_on_objects_when_the_camera_is_not_moving(self):
        """Why the two signals are OR'd: requiring motion would break this."""
        result = detection.classify_footage(
            self.LANDSCAPE, _detection({'car': 16}, 16, self.GRIDLOCK))

        self.assertTrue(result['looks_like_dashcam'])
        self.assertFalse(result['egomotion']['looks_like_driving'])

    def test_an_aerial_shot_is_radial_but_too_slow_to_count(self):
        result = detection.classify_footage(
            self.LANDSCAPE, _detection({'car': 2}, 14, self.DRONE))

        self.assertFalse(result['looks_like_dashcam'])

    def test_a_slow_zoom_is_more_radial_than_any_dashcam_and_still_rejected(self):
        result = detection.classify_footage(
            self.LANDSCAPE, _detection({}, 12, self.SLOW_ZOOM))

        self.assertFalse(result['looks_like_dashcam'])

    def test_walking_does_not_look_like_driving(self):
        result = detection.classify_footage(
            self.LANDSCAPE, _detection({}, 20, self.WALKING))

        self.assertFalse(result['looks_like_dashcam'])

    def test_portrait_is_rejected_however_convincing_the_motion(self):
        """Orientation stays a hard gate: a phone in a car mount is not a dashcam."""
        result = detection.classify_footage(
            self.PORTRAIT, _detection({}, 22, self.EMPTY_ROAD))

        self.assertFalse(result['looks_like_dashcam'])
        self.assertIn('portrait', result['reason'])

    def test_a_rear_facing_camera_scores_like_a_forward_one(self):
        """Radiality is unsigned: inward flow is a reversing or rear camera.

        Measured +0.974 forward and -0.975 on the same clip reversed, so
        scoring the sign would reject every rear-facing dashcam.
        """
        summary = detection.summarize_egomotion([(0.96, 8.0), (0.95, 8.2)])

        self.assertTrue(summary['looks_like_driving'])
        self.assertGreater(summary['radiality'], 0)

    def test_pairs_that_could_not_be_measured_are_ignored_not_counted_as_zero(self):
        """A few untrackable frames should not drag a clip below the threshold."""
        summary = detection.summarize_egomotion([None, (0.96, 8.0), None, (0.94, 7.5)])

        self.assertEqual(summary['pairs_measured'], 2)
        self.assertTrue(summary['looks_like_driving'])

    def test_no_measurable_pairs_is_not_driving(self):
        summary = detection.summarize_egomotion([None, None])

        self.assertFalse(summary['looks_like_driving'])
        self.assertEqual(summary['radiality'], 0.0)

    def test_a_missing_egomotion_block_does_not_break_the_verdict(self):
        """Older stored results have no egomotion key at all."""
        result = detection.classify_footage(self.LANDSCAPE, {'counts': {'car': 9}, 'frames_sampled': 9})

        self.assertTrue(result['looks_like_dashcam'])
