"""Tests for Clerk session-token verification.

These generate a real RSA keypair and serve it as a fake JWKS document, so the
signature path is genuinely exercised -- no network, no Clerk account. The
attack cases (alg confusion, foreign key, wrong issuer, expiry) are the reason
this module exists, so they are covered explicitly.
"""

import datetime
import json
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase, override_settings

from apps.accounts import jwt_auth
from apps.accounts.jwt_auth import ClerkTokenError, verify_token
from apps.videos.models import Video

ISSUER = 'https://example-app.clerk.accounts.dev'
KID = 'test-key-1'


def _generate_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks_for(private_key, kid=KID):
    public_numbers = private_key.public_key().public_numbers()

    def b64(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, 'big')
        return jwt.utils.base64url_encode(raw).decode('ascii')

    return {
        'keys': [{
            'kty': 'RSA',
            'kid': kid,
            'use': 'sig',
            'alg': 'RS256',
            'n': b64(public_numbers.n),
            'e': b64(public_numbers.e),
        }]
    }


def _make_token(private_key, *, subject='user_abc123', issuer=ISSUER, kid=KID,
                expires_in_seconds=3600, algorithm='RS256', key=None):
    now = datetime.datetime.now(datetime.timezone.utc)
    claims = {
        'sub': subject,
        'iss': issuer,
        'iat': int(now.timestamp()),
        'exp': int((now + datetime.timedelta(seconds=expires_in_seconds)).timestamp()),
    }
    return jwt.encode(
        claims,
        key if key is not None else private_key,
        algorithm=algorithm,
        headers={'kid': kid},
    )


@override_settings(CLERK_ISSUER=ISSUER, REQUIRE_CLERK_JWT=False)
class ClerkTokenVerificationTests(TestCase):
    def setUp(self):
        jwt_auth.reset_jwks_cache()
        self.private_key = _generate_keypair()
        self.jwks = _jwks_for(self.private_key)
        patcher = mock.patch.object(jwt.PyJWKClient, 'fetch_data', return_value=self.jwks)
        self.fetch_data = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(jwt_auth.reset_jwks_cache)

    def test_valid_token_is_accepted(self):
        claims = verify_token(_make_token(self.private_key))
        self.assertEqual(claims['sub'], 'user_abc123')

    def test_expired_token_is_rejected(self):
        token = _make_token(self.private_key, expires_in_seconds=-60)
        with self.assertRaises(ClerkTokenError) as ctx:
            verify_token(token)
        self.assertIn('expired', str(ctx.exception).lower())

    def test_wrong_issuer_is_rejected(self):
        token = _make_token(self.private_key, issuer='https://attacker.example.com')
        with self.assertRaises(ClerkTokenError):
            verify_token(token)

    def test_token_signed_by_a_different_key_is_rejected(self):
        foreign_key = _generate_keypair()
        token = _make_token(foreign_key)  # same kid, wrong private key
        with self.assertRaises(ClerkTokenError):
            verify_token(token)

    def test_unsigned_token_is_rejected(self):
        # alg: none -- the classic "just drop the signature" attack.
        token = _make_token(self.private_key, algorithm='none', key='')
        with self.assertRaises(ClerkTokenError):
            verify_token(token)

    def test_hmac_signed_with_public_key_is_rejected(self):
        # Algorithm confusion: sign HS256 using the RSA public key as the shared
        # secret. Pinning algorithms=['RS256'] is what stops this.
        #
        # PyJWT refuses to *encode* this, so the token is assembled by hand --
        # which is what an attacker would do anyway.
        import hashlib
        import hmac

        from cryptography.hazmat.primitives import serialization

        public_pem = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        now = datetime.datetime.now(datetime.timezone.utc)
        header = {'alg': 'HS256', 'typ': 'JWT', 'kid': KID}
        claims = {
            'sub': 'user_attacker',
            'iss': ISSUER,
            'iat': int(now.timestamp()),
            'exp': int((now + datetime.timedelta(hours=1)).timestamp()),
        }
        encode = lambda obj: jwt.utils.base64url_encode(  # noqa: E731
            json.dumps(obj, separators=(',', ':')).encode()
        )
        signing_input = encode(header) + b'.' + encode(claims)
        signature = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
        token = (signing_input + b'.' + jwt.utils.base64url_encode(signature)).decode()

        with self.assertRaises(ClerkTokenError):
            verify_token(token)

    def test_token_without_subject_is_rejected(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        token = jwt.encode(
            {'iss': ISSUER, 'iat': int(now.timestamp()),
             'exp': int((now + datetime.timedelta(hours=1)).timestamp())},
            self.private_key, algorithm='RS256', headers={'kid': KID},
        )
        with self.assertRaises(ClerkTokenError):
            verify_token(token)

    def test_missing_issuer_setting_is_an_error(self):
        with override_settings(CLERK_ISSUER=''):
            with self.assertRaises(ClerkTokenError):
                verify_token(_make_token(self.private_key))

    def test_jwks_is_cached_across_calls(self):
        for _ in range(3):
            verify_token(_make_token(self.private_key))
        self.assertEqual(self.fetch_data.call_count, 1)


@override_settings(CLERK_ISSUER=ISSUER)
class IdentityResolutionTests(TestCase):
    """How get_identity()/current_clerk_user_id() treat tokens vs the header."""

    def setUp(self):
        jwt_auth.reset_jwks_cache()
        self.private_key = _generate_keypair()
        patcher = mock.patch.object(
            jwt.PyJWKClient, 'fetch_data', return_value=_jwks_for(self.private_key))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(jwt_auth.reset_jwks_cache)

        self.private_video = Video.objects.create(
            owner_clerk_user_id='user_owner', title='Private clip',
            status='ready', visibility='private')

    def _auth(self, subject):
        return {'HTTP_AUTHORIZATION': f'Bearer {_make_token(self.private_key, subject=subject)}'}

    @override_settings(REQUIRE_CLERK_JWT=False)
    def test_verified_token_wins_over_a_conflicting_header(self):
        # A caller presenting a valid token for user_stranger cannot claim to be
        # the owner via the header.
        response = self.client.get(
            f'/api/videos/{self.private_video.id}/',
            HTTP_X_CLERK_USER_ID='user_owner',
            **self._auth('user_stranger'),
        )
        self.assertEqual(response.status_code, 404)

    @override_settings(REQUIRE_CLERK_JWT=False)
    def test_token_owner_can_read_own_private_video(self):
        response = self.client.get(
            f'/api/videos/{self.private_video.id}/', **self._auth('user_owner'))
        self.assertEqual(response.status_code, 200)

    @override_settings(REQUIRE_CLERK_JWT=False)
    def test_header_still_accepted_while_flag_is_off(self):
        response = self.client.get(
            f'/api/videos/{self.private_video.id}/', HTTP_X_CLERK_USER_ID='user_owner')
        self.assertEqual(response.status_code, 200)

    @override_settings(REQUIRE_CLERK_JWT=True)
    def test_header_is_ignored_once_flag_is_on(self):
        # This is the whole point of the cutover: the forgeable header stops working.
        response = self.client.get(
            f'/api/videos/{self.private_video.id}/', HTTP_X_CLERK_USER_ID='user_owner')
        self.assertEqual(response.status_code, 404)

    @override_settings(REQUIRE_CLERK_JWT=True)
    def test_token_still_works_once_flag_is_on(self):
        response = self.client.get(
            f'/api/videos/{self.private_video.id}/', **self._auth('user_owner'))
        self.assertEqual(response.status_code, 200)

    @override_settings(REQUIRE_CLERK_JWT=True)
    def test_comment_attribution_cannot_be_forged_once_flag_is_on(self):
        video = Video.objects.create(
            owner_clerk_user_id='user_owner', title='Clip', status='ready', visibility='public')
        response = self.client.post(
            f'/api/videos/{video.id}/comments/',
            data=json.dumps({'text': 'hi', 'clerk_user_id': 'user_victim'}),
            content_type='application/json',
            **self._auth('user_real'),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['comment']['user_clerk_user_id'], 'user_real')
