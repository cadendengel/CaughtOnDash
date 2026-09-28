"""Field-level encryption for incident details.

Other-party details -- a name, a licence number, a policy number -- belong to
someone who never signed up for this site. They are encrypted before they reach
the database, so a leaked backup or an over-broad SQL query yields ciphertext.

Keys come from INCIDENT_ENCRYPTION_KEYS: comma-separated Fernet keys, newest
first. Every key decrypts; only the first encrypts, so a key is rotated by
prepending a new one and re-saving rows at leisure.

Fails closed. With no key configured, reading or writing an encrypted value
raises rather than storing plaintext -- the rest of the site keeps working,
only this data is unavailable.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def generate_key() -> str:
    """A new key, for the README's setup step and for tests."""
    return Fernet.generate_key().decode('ascii')


def _fernet() -> MultiFernet:
    keys = [key.strip() for key in getattr(settings, 'INCIDENT_ENCRYPTION_KEYS', []) if key.strip()]
    if not keys:
        raise ImproperlyConfigured(
            'INCIDENT_ENCRYPTION_KEYS is not set, so incident details cannot be read '
            'or stored. Generate one with: python -c "from cryptography.fernet import '
            'Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        return MultiFernet([Fernet(key.encode('ascii')) for key in keys])
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            'INCIDENT_ENCRYPTION_KEYS contains a value that is not a Fernet key.'
        ) from exc


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode('utf-8')).decode('ascii')


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode('ascii')).decode('utf-8')
    except InvalidToken as exc:
        # Not a wrong password to shrug at: either the key was removed while
        # rows still depend on it, or the row was altered outside the app.
        raise ImproperlyConfigured(
            'An incident field could not be decrypted with any configured key.'
        ) from exc


class EncryptedTextField(models.TextField):
    """A TextField stored as a Fernet token. Empty stays empty, unencrypted.

    An empty string is not worth hiding and keeping it empty means a blank
    field needs no key -- a report with no other party can be read anywhere.
    Not searchable or orderable in SQL, by design.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ''):
            return value
        return encrypt(value)

    def from_db_value(self, value, expression, connection):
        if value in (None, ''):
            return value
        return decrypt(value)
