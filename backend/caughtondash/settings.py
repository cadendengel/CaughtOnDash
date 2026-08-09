"""Django settings for the CaughtOnDash backend.

This is intentionally minimal so you can add endpoints step by step.
Use sqlite for local development and swap to Postgres when you are ready.
"""

from pathlib import Path
import sys
import os

from dotenv import load_dotenv
import dj_database_url
from corsheaders.defaults import default_headers

from caughtondash.env import get_allowed_hosts, get_bool, get_int, get_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

# These fail closed: DEBUG defaults off, and with DEBUG off a missing or
# placeholder SECRET_KEY raises rather than falling back to a value that is
# published in this repository. See caughtondash/env.py.
DEBUG = get_bool('DEBUG', default=False)

# The test runner needs a key but must not require a production secret to be
# present, so it is allowed the development placeholder. This only ever applies
# to `manage.py test`, never to a served process.
RUNNING_TESTS = 'test' in sys.argv

SECRET_KEY = get_secret_key(DEBUG or RUNNING_TESTS)
ALLOWED_HOSTS = get_allowed_hosts(DEBUG)

INSTALLED_APPS = [
    # daphne must precede staticfiles: it replaces runserver with an ASGI one,
    # so WebSockets work locally without a separate server.
    'daphne',
    'channels',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    'rest_framework',
    'corsheaders',
    'apps.accounts',
    'apps.videos',
    'apps.feed',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'caughtondash.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'caughtondash.wsgi.application'
ASGI_APPLICATION = 'caughtondash.asgi.application'

# Database configuration: use DATABASE_URL for production (AWS RDS, etc.),
# otherwise fall back to local sqlite3 for development.
DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL:
    # conn_max_age=0: close the connection at the end of each request.
    #
    # Persistent connections made sense under gunicorn, where one worker with a
    # few threads held a few connections. Under daphne every sync ORM call runs
    # in asgiref's thread pool -- up to min(32, cpu+4) threads -- and each
    # thread keeps its own connection alive for the full conn_max_age. Against
    # Supabase's session pooler, which allows 15 clients, that exhausts the pool
    # and every request then fails with EMAXCONNSESSION.
    #
    # There is also nothing to gain here: the session pooler *is* the connection
    # pool, so holding Django-side connections open duplicates it while
    # competing for the same 15 slots.
    #
    # ASGI_THREADS caps the pool as a second line of defence, so the worst case
    # is bounded even if something re-enables persistent connections later.
    #
    # TRANSACTION-POOLER CAVEAT (QA ISSUE-6): conn_max_age=0 is correct for the
    # SESSION pooler (15-client cap) but is a liability on the TRANSACTION pooler
    # (200-client cap). With age=0 every request opens a fresh client connection
    # to Supavisor and closes it; sustained traffic churns hundreds of
    # short-lived client connections, and if Supavisor frees them slowly they
    # pile toward the 200 cap. A sustained (even sequential) load test drove the
    # whole site to EMAXCONN 5xx for ~39 minutes. On the transaction pooler,
    # raising DB_CONN_MAX_AGE (e.g. 30-60) so connections are REUSED reduces that
    # churn -- but verify under load before trusting it, and keep it 0 on the
    # session pooler. This is why it stays an env var rather than a hardcoded value.
    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            engine='django.db.backends.postgresql',
            conn_max_age=get_int('DB_CONN_MAX_AGE', 0),
        )
    }

    # Settings required by Supabase's transaction pooler (port 6543).
    #
    # The session pooler on 5432 caps clients at 15 -- a hard ceiling shared by
    # the web app, the desktop worker and every browser. Hitting it fails every
    # request with EMAXCONNSESSION, and no amount of connection hygiene raises
    # the limit. The transaction pooler has no such cap, but it hands each
    # statement a different backend, so anything that assumes a persistent
    # server-side session breaks:
    #
    # - prepared statements are cached per session, and psycopg reuses them by
    #   name; on a different backend that name does not exist
    # - server-side cursors live in the session that opened them
    #
    # Harmless on the session pooler too, so they are unconditional rather than
    # keyed off the port -- one behaviour regardless of which URL is configured.
    DATABASES['default'].setdefault('OPTIONS', {})
    DATABASES['default']['OPTIONS']['prepare_threshold'] = None
    DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] = True
    # Enforce SSL for RDS (sslmode=require by default in DATABASE_URL)
    # If using AWS RDS Certificate Bundle for verify-full, add below:
    # DATABASES['default']['OPTIONS'] = {
    #     'sslmode': 'verify-full',
    #     'sslrootcert': '/path/to/rds-ca-bundle.pem',
    # }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'OPTIONS': {
                # The desktop worker reports progress while the browser is also
                # reading, and SQLite serialises writers. Without these, a job
                # that reports progress every few percent produces a steady
                # trickle of "database is locked" 500s locally. WAL lets readers
                # continue during a write, and the timeout waits for a busy
                # writer instead of failing instantly.
                #
                # Local development only; production uses Postgres.
                'timeout': 20,
                'init_command': 'PRAGMA journal_mode=WAL;',
                # IMMEDIATE takes the write lock at BEGIN. The default defers it,
                # so a transaction that reads and then writes must upgrade its
                # lock mid-flight -- and an upgrade that loses the race fails
                # instantly with SQLITE_BUSY, which 'timeout' cannot wait out.
                # Every worker write (claim, progress, complete) has that shape.
                'transaction_mode': 'IMMEDIATE',
            },
        }
    }

# Keep automated tests isolated from the managed Postgres instance so they do not
# leave behind open sessions or depend on external cleanup behavior.
if 'test' in sys.argv:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    }

# Connection pooling: persistent connections reduce overhead.
# CONN_MAX_AGE is set via dj_database_url but can be overridden here if needed.
if 'CONN_MAX_AGE' not in DATABASES['default'].get('OPTIONS', {}):
    DATABASES['default']['CONN_MAX_AGE'] = 600

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Transport security. Only applied with DEBUG off so local http:// development
# keeps working; secure cookies over plain http would simply never be sent.
if not DEBUG:
    # Render (and Cloudflare in front of it) terminate TLS and forward over
    # http, so Django needs this header to know the original request was https.
    # Without it, is_secure() is False and any redirect below would loop.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # Opt-in, because both can lock you out of a misconfigured deployment:
    # a redirect loop if the proxy header is wrong, and a browser-cached HSTS
    # policy that outlives the mistake. Enable once https is known good.
    SECURE_SSL_REDIRECT = get_bool('SECURE_SSL_REDIRECT', default=False)
    SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0'))
    if SECURE_HSTS_SECONDS:
        SECURE_HSTS_INCLUDE_SUBDOMAINS = get_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=True)
        SECURE_HSTS_PRELOAD = get_bool('SECURE_HSTS_PRELOAD', default=False)

_default_cors_origins = 'http://localhost:5173,https://caught-on-dash.vercel.app'
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv('CORS_ALLOWED_ORIGINS', _default_cors_origins).split(',')
    if origin.strip()
]

# WebSocket delivery of analysis state.
#
# InMemoryChannelLayer works because this runs as a single process: Render's
# free tier is one instance and uvicorn defaults to one worker. It breaks
# silently across processes, so scaling past one worker means moving to Redis.
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

# Clerk server-side secret, used by the sync_profiles_from_clerk management command.
# Read through settings (not os.getenv directly) so tests can override it.
CLERK_SECRET_KEY = os.getenv('CLERK_SECRET_KEY', '')

# Clerk session-token verification.
# CLERK_ISSUER is the Frontend API origin, e.g. https://<slug>.clerk.accounts.dev
# Its JWKS document is fetched from <issuer>/.well-known/jwks.json.
CLERK_ISSUER = os.getenv('CLERK_ISSUER', '').strip().rstrip('/')

# When True, a verified bearer token is the ONLY accepted identity and the
# X-Clerk-User-Id header is ignored. Leave False until the frontend ships tokens,
# then flip it -- the header is trivially forgeable and must not remain accepted.
REQUIRE_CLERK_JWT = os.getenv('REQUIRE_CLERK_JWT', 'False').lower() in {'1', 'true', 'yes'}

CORS_ALLOW_HEADERS = list(default_headers) + [
    'x-clerk-user-id',
    'x-skip-view-count',
]

REST_FRAMEWORK = {
    # Add Clerk/JWT authentication once you start wiring protected endpoints.
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.AllowAny'],
}
