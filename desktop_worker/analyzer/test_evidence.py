"""Tests for private evidence: provenance reading and contact-sheet layout.

Like test_detection, these need no model, no video and no ffprobe. The probe
fixture is the real ffprobe output of an iPhone-exported dashcam clip, trimmed
to the fields that matter.
"""

import unittest

import evidence

IPHONE_EXPORT = {
    'format': {
        'format_name': 'mov,mp4,m4a,3gp,3g2,mj2',
        'format_long_name': 'QuickTime / MOV',
        'duration': '49.233333',
        'size': '14208326',
        'tags': {
            'major_brand': 'qt  ',
            'creation_time': '2026-09-26T18:14:32.000000Z',
            'com.apple.quicktime.creationdate': '2026-09-26T12:16:21-05:00',
        },
    },
    'streams': [
        {'codec_type': 'audio', 'codec_name': 'aac', 'sample_rate': '16000', 'bit_rate': '64000',
         'tags': {'handler_name': 'Core Media Audio', 'creation_time': '2026-09-26T18:14:32.000000Z'}},
        {'codec_type': 'video', 'codec_name': 'hevc', 'width': 1280, 'height': 720,
         'avg_frame_rate': '30/1', 'bit_rate': '2240554',
         'tags': {'handler_name': 'Core Media Video', 'encoder': 'HEVC'}},
    ],
}

DASHCAM_ORIGINAL = {
    'format': {'format_name': 'mov,mp4', 'duration': '60.0', 'size': '100',
               'tags': {'major_brand': 'qt  ', 'creation_time': '2026-09-26T17:16:21Z'}},
    'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1920, 'height': 1080,
                 'tags': {'handler_name': 'VideoHandler'}}],
}


class ProvenanceTests(unittest.TestCase):
    def test_iphone_export_is_recognised_with_the_gap_measured(self):
        summary = evidence.summarize_provenance(IPHONE_EXPORT)
        codes = {hint['code']: hint for hint in summary['hints']}

        self.assertIn('apple_export', codes)
        # Recorded 12:16:21 CDT, written 13:14:32 CDT.
        self.assertEqual(codes['written_after_recording']['gap_seconds'], 3491)
        self.assertEqual(summary['size_bytes'], 14208326)
        self.assertEqual(summary['streams'][1]['codec'], 'hevc')
        self.assertEqual(summary['streams'][1]['bit_rate'], 2240554)

    def test_a_plain_dashcam_file_raises_no_hints(self):
        summary = evidence.summarize_provenance(DASHCAM_ORIGINAL)
        self.assertEqual(summary['hints'], [])
        self.assertIsNone(summary['location'])

    def test_embedded_gps_is_parsed_and_flagged(self):
        probe = {'format': {'tags': {'com.apple.quicktime.location.ISO6709': '+30.2790-097.5839+150.000/'}},
                 'streams': []}
        summary = evidence.summarize_provenance(probe)
        self.assertEqual(summary['location'], {'latitude': 30.279, 'longitude': -97.5839})
        self.assertIn('embedded_location', [hint['code'] for hint in summary['hints']])

    def test_a_small_gap_is_not_an_export(self):
        # Cameras finalise the file when recording stops; a clip's own length
        # of delay is normal, not a re-save.
        container = {'com.apple.quicktime.creationdate': '2026-09-26T12:16:21-05:00',
                     'creation_time': '2026-09-26T17:17:21Z'}
        self.assertEqual(evidence.provenance_hints(container, []), [])

    def test_unparseable_times_are_ignored(self):
        container = {'com.apple.quicktime.creationdate': 'yesterday', 'creation_time': ''}
        self.assertEqual(evidence.provenance_hints(container, []), [])

    def test_missing_ffprobe_is_reported_not_raised(self):
        original = evidence.run_ffprobe
        evidence.run_ffprobe = lambda path: None
        try:
            self.assertEqual(evidence.provenance('x.mp4')['available'], False)
        finally:
            evidence.run_ffprobe = original


class ContactSheetLayoutTests(unittest.TestCase):
    def test_one_frame_a_second_for_short_clips(self):
        indices = evidence.contact_sheet_indices(300, 30.0)  # a 10s clip
        self.assertEqual(len(indices), 10)
        self.assertEqual((indices[0], indices[-1]), (0, 299))

    def test_long_clips_are_thinned_to_the_grid(self):
        indices = evidence.contact_sheet_indices(1477, 30.0)
        self.assertEqual(len(indices), evidence.CONTACT_SHEET_MAX_TILES)
        self.assertEqual((indices[0], indices[-1]), (0, 1476))

    def test_degenerate_inputs(self):
        self.assertEqual(evidence.contact_sheet_indices(0, 30.0), [])
        self.assertEqual(evidence.contact_sheet_indices(5, 0.0), [0])

    def test_grid_is_at_most_six_across_and_fits_every_tile(self):
        for tiles in range(1, 25):
            with self.subTest(tiles=tiles):
                columns, rows = evidence.grid_shape(tiles)
                self.assertLessEqual(columns, 6)
                self.assertGreaterEqual(columns * rows, tiles)
                self.assertLess(columns * (rows - 1), tiles)

    def test_timestamps(self):
        self.assertEqual(evidence.format_timestamp(25.1), '0:25.1')
        self.assertEqual(evidence.format_timestamp(61.0), '1:01.0')


if __name__ == '__main__':
    unittest.main()
