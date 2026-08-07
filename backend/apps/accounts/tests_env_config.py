"""Configuration must fail closed.

Before this, DEBUG defaulted to True and SECRET_KEY fell back to a placeholder
committed to this repository. An unset or renamed environment variable on the
host would therefore have downgraded production to a debug server signing
sessions with a publicly known key -- silently, with no error.
"""

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase

from caughtondash.env import (
    INSECURE_SECRET_KEY,
    get_allowed_hosts,
    get_bool,
    get_secret_key,
)


class GetBoolTests(TestCase):
    def test_unset_uses_the_default(self):
        self.assertFalse(get_bool('DEBUG', default=False, environ={}))
        self.assertTrue(get_bool('DEBUG', default=True, environ={}))

    def test_recognised_truthy_and_falsey_values(self):
        for raw in ('1', 'true', 'TRUE', 'Yes', ' on '):
            with self.subTest(raw=raw):
                self.assertTrue(get_bool('DEBUG', environ={'DEBUG': raw}))
        for raw in ('0', 'false', 'No', 'off', ''):
            with self.subTest(raw=raw):
                self.assertFalse(get_bool('DEBUG', default=True, environ={'DEBUG': raw}))

    def test_typo_raises_instead_of_reading_as_true(self):
        # 'Ture' is truthy under the old `in {...}` check only by accident of
        # not matching; the danger is a value like 'no' being treated as True by
        # a naive bool(). Anything unrecognised must be loud.
        with self.assertRaises(ImproperlyConfigured):
            get_bool('DEBUG', environ={'DEBUG': 'Ture'})


class GetSecretKeyTests(TestCase):
    def test_debug_allows_the_placeholder(self):
        self.assertEqual(get_secret_key(debug=True, environ={}), INSECURE_SECRET_KEY)

    def test_debug_still_prefers_a_real_key(self):
        self.assertEqual(get_secret_key(debug=True, environ={'SECRET_KEY': 'real'}), 'real')

    def test_missing_key_with_debug_off_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            get_secret_key(debug=False, environ={})

    def test_blank_key_with_debug_off_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            get_secret_key(debug=False, environ={'SECRET_KEY': '   '})

    def test_placeholder_key_with_debug_off_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            get_secret_key(debug=False, environ={'SECRET_KEY': INSECURE_SECRET_KEY})

    def test_real_key_with_debug_off_is_accepted(self):
        self.assertEqual(
            get_secret_key(debug=False, environ={'SECRET_KEY': 'a-real-secret'}),
            'a-real-secret',
        )


class GetAllowedHostsTests(TestCase):
    def test_explicit_hosts_are_parsed(self):
        hosts = get_allowed_hosts(
            debug=False, environ={'DJANGO_ALLOWED_HOSTS': 'example.com, api.example.com '})
        self.assertEqual(hosts, ['example.com', 'api.example.com'])

    def test_unset_falls_back_to_loopback_only(self):
        # Not an empty list: a deployment that forgets this variable then fails
        # closed with a 400 rather than serving every Host header.
        hosts = get_allowed_hosts(debug=False, environ={})
        self.assertEqual(hosts, ['localhost', '127.0.0.1', '[::1]'])
        self.assertNotIn('*', hosts)
