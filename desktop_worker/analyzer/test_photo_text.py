"""Tests for reading text on the owner's photos.

The OCR lines and plate reads are real Tesseract output from the 2026-09-26
case photo, so these need no Tesseract and no photo.
"""

import unittest

import photo_text

CASE_LINES = [
    'Saaeecccomee |', 'pedid', 'UTAH', '12026', 'RENT-A-TRUCK', 'COTRUCKS.COM', '33.4761',
    'COMMERCIAL DUTY', 'eggs', '8 Se DRO ER <p SORE POE,', 'BARCQ', 'SENT-A-TRUCK', 'OMMERCLAL DUTY',
]
CASE_PLATE_READS = ['S39 SCA', 'S99 SCA', '539 SCA', 'S39 SCA', 'G99 OCA', 'G4 OP', 'OO', 'G3 O']


class IdentifierTests(unittest.TestCase):
    def test_the_case_photo(self):
        found = {(i['kind'], i['value']) for i in photo_text.find_identifiers(CASE_LINES)}
        self.assertEqual(found, {('state', 'Utah'), ('domain', 'cotrucks.com')})

    def test_a_domain_starting_its_line_may_be_truncated(self):
        # BARCOTRUCKS.COM came back as COTRUCKS.COM.
        cut, = photo_text.find_identifiers(['COTRUCKS.COM'])
        whole, = photo_text.find_identifiers(['WWW.BARCOTRUCKS.COM'])
        mid, = photo_text.find_identifiers(['VISIT barcotrucks.com'])
        self.assertTrue(cut['may_be_truncated'])
        self.assertFalse(whole['may_be_truncated'])
        self.assertEqual(whole['value'], 'barcotrucks.com')
        self.assertFalse(mid['may_be_truncated'])

    def test_carrier_numbers_link_to_fmcsa(self):
        found = photo_text.find_identifiers(['USDOT 1234567', 'U.S. DOT NO. 7654321', 'MC-123456'])
        by_kind = {(i['kind'], i['value']): i for i in found}
        self.assertIn('USDOT', by_kind[('usdot', '1234567')]['lookup'])
        self.assertIn('query_string=7654321', by_kind[('usdot', '7654321')]['lookup'])
        self.assertIn('MC_MX', by_kind[('mc', '123456')]['lookup'])

    def test_phone_numbers(self):
        found = photo_text.find_identifiers(['1.800.453.4761', '(512) 827-7020'])
        self.assertEqual([i['value'] for i in found if i['kind'] == 'phone'], ['800-453-4761', '512-827-7020'])

    def test_a_partial_number_is_not_a_phone(self):
        self.assertEqual(photo_text.find_identifiers(['33.4761', '4761']), [])

    def test_two_word_states(self):
        self.assertEqual(photo_text.find_identifiers(['NEW MEXICO'])[0]['value'], 'New Mexico')


class LegibleTests(unittest.TestCase):
    def test_signage_is_kept_and_texture_dropped(self):
        kept = photo_text.legible_lines(CASE_LINES)
        for sign in ('UTAH', 'RENT-A-TRUCK', 'COTRUCKS.COM', 'COMMERCIAL DUTY', 'BARCQ'):
            self.assertIn(sign, kept)
        for noise in ('Saaeecccomee', 'pedid', 'eggs', '8 Se DRO ER <p SORE POE,'):
            self.assertNotIn(noise, kept)

    def test_the_same_sign_read_twice_is_kept_once(self):
        kept = photo_text.legible_lines(CASE_LINES)
        self.assertNotIn('SENT-A-TRUCK', kept)
        self.assertNotIn('OMMERCLAL DUTY', kept)


class PlateTests(unittest.TestCase):
    def test_the_case_plate_is_read_with_its_doubt_flagged(self):
        reading = photo_text.plate_consensus(CASE_PLATE_READS)
        # The plate says S39 9CA. Every read agreed on S at position 3, so the
        # vote alone would be confidently wrong; the confusable flag is what
        # puts the 9 in front of a person.
        self.assertEqual(reading['text'], 'S39 SCA')
        position = next(u for u in reading['uncertain'] if u['index'] == 3)
        self.assertIn('9', position['could_be'])

    def test_characters_nobody_confuses_are_not_flagged(self):
        reading = photo_text.plate_consensus(['AHC 3434', 'AHC 3434', 'AHC 3434'])
        self.assertEqual(reading['text'], 'AHC 3434')
        self.assertEqual(reading['uncertain'], [])
        # B-8, 1-I-7 and 2-Z are confusable pairs, so these are all flagged.
        confusable = photo_text.plate_consensus(['ABC 1234'] * 3)
        self.assertEqual([u['index'] for u in confusable['uncertain']], [1, 3, 4])

    def test_disagreement_is_flagged_even_for_unconfusable_characters(self):
        reading = photo_text.plate_consensus(['AHC 3434', 'AHC 3434', 'AXC 3434'])
        position = next(u for u in reading['uncertain'] if u['index'] == 1)
        self.assertEqual(position['could_be'], ['X'])
        self.assertAlmostEqual(position['agreement'], 0.67, places=2)

    def test_one_stray_read_among_many_is_not_a_doubt(self):
        reads = ['AHC 3434'] * 9 + ['AXC 3434']
        self.assertEqual(photo_text.plate_consensus(reads)['uncertain'], [])
        reads = ['AHC 3434'] * 8 + ['AXC 3434'] * 2
        self.assertEqual([u['index'] for u in photo_text.plate_consensus(reads)['uncertain']], [1])

    def test_too_few_plausible_reads_is_no_plate(self):
        self.assertIsNone(photo_text.plate_consensus(['S39 SCA']))
        self.assertIsNone(photo_text.plate_consensus(['CIQROP', 'NEN', 'AE']))   # no digits

    def test_the_same_plate_found_twice_is_one_candidate(self):
        # The plate and its frame, as on the case photo.
        groups = photo_text.group_regions([(1296, 3788, 984, 404), (1408, 3796, 764, 372), (100, 100, 200, 100)])
        self.assertEqual(sorted(len(g) for g in groups), [1, 2])


if __name__ == '__main__':
    unittest.main()
