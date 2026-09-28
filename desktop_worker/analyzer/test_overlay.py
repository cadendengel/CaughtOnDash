"""Tests for reading the dashcam overlay.

The OCR strings are real Tesseract output from the 2026-09-26 collision clip,
slips included, so these need no Tesseract and no video.
"""

import unittest
from datetime import datetime

import overlay

CLEAN = '09/26/2026 12:16:44 PM  N30 13\' 42.00"W097" 37\' 12.00" 80MPH'


class ParseLineTests(unittest.TestCase):
    def test_a_clean_line(self):
        reading = overlay.parse_line(CLEAN)
        self.assertEqual(reading['clock'], datetime(2026, 9, 26, 12, 16, 44))
        self.assertAlmostEqual(reading['lat'], 30 + 13 / 60 + 42 / 3600, places=6)
        self.assertAlmostEqual(reading['lon'], -(97 + 37 / 60 + 12 / 3600), places=6)
        self.assertEqual((reading['speed'], reading['speed_unit']), (80, 'mph'))

    def test_the_separators_tesseract_actually_produced(self):
        # The degree sign comes back as ", ' or nothing at all.
        for text in ('N30"13\'17.00"W097\' 37\' 20.00" 79MPH',
                     'N30 13\' 17.00"W097" 37\' 20.00" 79MPH',
                     'N30"13\' 17.00" W097" 37\'20.00" 79MPH'):
            with self.subTest(text=text):
                reading = overlay.parse_line(text)
                self.assertAlmostEqual(reading['lat'], 30.221389, places=5)
                self.assertAlmostEqual(reading['lon'], -97.622222, places=5)

    def test_twelve_hour_clock(self):
        self.assertEqual(overlay.parse_line('09/26/2026 12:05:00 AM')['clock'].hour, 0)
        self.assertEqual(overlay.parse_line('09/26/2026 12:05:00 PM')['clock'].hour, 12)
        self.assertEqual(overlay.parse_line('09/26/2026 01:05:00 PM')['clock'].hour, 13)
        self.assertEqual(overlay.parse_line('09/26/2026 13:05:00')['clock'].hour, 13)

    def test_date_orders(self):
        self.assertEqual(overlay.parse_line('2026/09/26 12:00:00')['clock'].date().isoformat(), '2026-09-26')
        self.assertEqual(overlay.parse_line('26/09/2026 12:00:00')['clock'].month, 9)   # day > 12: day first
        self.assertEqual(overlay.parse_line('09/10/2026 12:00:00')['clock'].month, 9)   # ambiguous: month first

    def test_decimal_degrees_and_kmh(self):
        reading = overlay.parse_line('S33.8688 E151.2093 64KM/H')
        self.assertEqual((reading['lat'], reading['lon']), (-33.8688, 151.2093))
        self.assertEqual((reading['speed'], reading['speed_unit']), (64, 'km/h'))

    def test_impossible_values_are_refused(self):
        self.assertNotIn('lat', overlay.parse_line('N30 73\' 42.00" W097 37\' 12.00"'))    # 73 minutes
        self.assertNotIn('clock', overlay.parse_line('09/26/2026 25:16:44'))
        self.assertNotIn('clock', overlay.parse_line('13/13/2026 12:16:44'))
        self.assertEqual(overlay.parse_line(''), {})


class AmbiguityTests(unittest.TestCase):
    def test_s_for_eight_or_nine_becomes_both(self):
        speeds = {r['speed'] for r in overlay.readings_from(CLEAN.replace('80MPH', 'S6MPH'))}
        # The literal reading (6) is kept too -- S can be a hemisphere -- and
        # the neighbouring seconds decide between them.
        self.assertTrue({86, 96} <= speeds)

    def test_a_lone_s_before_mph(self):
        speeds = {r.get('speed') for r in overlay.readings_from('SMPH')}
        self.assertEqual(speeds, {8, 9})

    def test_southern_hemisphere_is_still_read(self):
        lats = {r['lat'] for r in overlay.readings_from('S33 52\' 7.00" E151 12\' 33.00"') if 'lat' in r}
        self.assertIn(round(-(33 + 52 / 60 + 7 / 3600), 6), lats)


class MedianFilterTests(unittest.TestCase):
    def test_monotone_runs_pass_through_exactly(self):
        # Hard braking into an impact must survive.
        braking = [80, 80, 78, 70, 55, 35, 12, 0, 0, 0]
        self.assertEqual(overlay.median_filter(braking), braking)
        accelerating = [89, 91, 93, 95, 96, 97, 99, 101, 102, 103]
        self.assertEqual(overlay.median_filter(accelerating), accelerating)

    def test_short_spikes_are_removed(self):
        self.assertEqual(overlay.median_filter([79, 79, 29, 29, 79, 79, 79]), [79, 79, 79, 79, 79, 79, 79])


def _sample(t, text):
    return (t, overlay.readings_from(text))


def _line(clock_second, lat_seconds, speed):
    return (f'09/26/2026 12:16:{clock_second:02d} PM N30 13\' {lat_seconds}.00" '
            f'W097" 37\' 12.00" {speed}MPH')


class TrackTests(unittest.TestCase):
    def test_misreads_are_outvoted(self):
        samples = [_sample(t + 0.5, _line(20 + t, 17 + t, 79)) for t in range(10)]
        samples[4] = _sample(4.5, _line(24, 21, 29))                  # 79 read as 29
        samples[6] = _sample(6.5, _line(26, 23, 79).replace("13'", "15'"))  # 3.7 km away
        track = overlay.build_track(samples)['track']

        self.assertEqual({p['speed'] for p in track}, {79})
        self.assertLess(max(p['lat'] for p in track), 30 + 14 / 60)

    def test_clock_is_the_median_offset(self):
        samples = [_sample(t + 0.5, _line(20 + t, 17, 79)) for t in range(10)]
        samples[3] = _sample(3.5, _line(59, 17, 79))                   # a bad clock read
        built = overlay.build_track(samples)
        self.assertEqual(built['clock']['start'], '2026-09-26T12:16:19')
        self.assertEqual(built['clock']['agreeing'], 9)

    def test_at_time_uses_the_nearest_reading(self):
        built = overlay.build_track([_sample(t + 0.5, _line(20 + t, 17 + t, 70 + t)) for t in range(10)])
        seen = overlay.at_time({'available': True, **built}, 6.4)
        self.assertEqual(seen['clock'], '2026-09-26T12:16:25')
        self.assertEqual(seen['speed'], 76)
        self.assertIsNone(overlay.at_time({'available': False}, 6.4))

    def test_nothing_readable_is_an_empty_track(self):
        built = overlay.build_track([(0.5, []), (1.5, [])])
        self.assertIsNone(built['clock'])
        self.assertEqual(built['track'], [])


class ConsistencyTests(unittest.TestCase):
    def test_a_real_overlay_frame_has_two_of_clock_position_speed(self):
        self.assertTrue(overlay.is_overlay_frame(overlay.readings_from(CLEAN)))
        self.assertTrue(overlay.is_overlay_frame([{'speed': 80, 'clock': 'x'}]))

    def test_stray_text_is_not_an_overlay_frame(self):
        # What a QA clip without an overlay produced: a lone "7 MPH".
        self.assertFalse(overlay.is_overlay_frame(overlay.readings_from('7MPH')))
        self.assertFalse(overlay.is_overlay_frame([]))

    def test_the_collision_clip_passes_and_the_qa_clip_does_not(self):
        self.assertTrue(overlay.is_consistent_overlay(49, 49))
        self.assertTrue(overlay.is_consistent_overlay(25, 49))   # heavy OCR misses still pass
        self.assertFalse(overlay.is_consistent_overlay(2, 20))
        self.assertFalse(overlay.is_consistent_overlay(0, 0))


if __name__ == '__main__':
    unittest.main()
