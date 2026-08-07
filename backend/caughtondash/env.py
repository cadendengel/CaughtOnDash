"""Environment parsing for settings, kept here so it can be unit tested.

The rule these follow: an unset or malformed variable must fail *closed*. A
missing DEBUG flag should not produce a debug server, and a missing SECRET_KEY
should not silently fall back to a value published in the repository.
"""

from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

TRUTHY = {'1', 'true', 'yes', 'on'}
FALSEY = {'0', 'false', 'no', 'off', ''}

# Kept only for local development; refused whenever DEBUG is off.
INSECURE_SECRET_KEY = 'django-insecure-change-me'


def get_bool(name: str, default: bool = False, environ: dict | None = None) -> bool:
    """Parse a boolean env var, failing closed on anything unrecognised."""
    environ = os.environ if environ is None else environ
    raw = environ.get(name)

    if raw is None:
        return default

    value = raw.strip().lower()
    if value in TRUTHY:
        return True
    if value in FALSEY:
        return False

    # A typo like DEBUG=Ture must not read as True.
    raise ImproperlyConfigured(
        f'{name} must be a boolean (one of {sorted(TRUTHY | FALSEY - {""})}), got {raw!r}.'
    )


def get_secret_key(debug: bool, environ: dict | None = None) -> str:
    """Return SECRET_KEY, refusing to run with a known value outside DEBUG."""
    environ = os.environ if environ is None else environ
    secret_key = (environ.get('SECRET_KEY') or '').strip()

    if debug:
        return secret_key or INSECURE_SECRET_KEY

    if not secret_key:
        raise ImproperlyConfigured(
            'SECRET_KEY must be set when DEBUG is off. Refusing to start with a '
            'default key, which would make session and password-reset tokens '
            'forgeable by anyone who has read the source.'
        )

    if secret_key == INSECURE_SECRET_KEY:
        raise ImproperlyConfigured(
            'SECRET_KEY is still the development placeholder. Set a real secret '
            'before running with DEBUG off.'
        )

    return secret_key


def get_allowed_hosts(debug: bool, environ: dict | None = None) -> list[str]:
    """Hosts Django will serve.

    Falls back to loopback rather than an empty list so local development works
    with DEBUG off, while a real deployment must name its own host explicitly --
    an unset variable then fails closed with a 400 rather than serving anyone.
    """
    environ = os.environ if environ is None else environ
    raw = environ.get('DJANGO_ALLOWED_HOSTS', '')
    hosts = [host.strip() for host in raw.split(',') if host.strip()]

    if hosts:
        return hosts

    return ['localhost', '127.0.0.1', '[::1]']
