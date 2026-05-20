"""Django settings for the CaughtOnDash backend.

This is intentionally minimal so you can add endpoints step by step.
Use sqlite for local development and swap to Postgres when you are ready.
"""

from pathlib import Path
import os

from dotenv import load_dotenv
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-change-me')
DEBUG = os.getenv('DEBUG', 'True').lower() in {'1', 'true', 'yes'}
ALLOWED_HOSTS = [host.strip() for host in os.getenv('DJANGO_ALLOWED_HOSTS', '').split(',') if host.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
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
    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            engine='django.db.backends.postgresql',
            conn_max_age=600,  # Persistent connection pool: 10 minutes
        )
    }
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
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:5173').split(',')
    if origin.strip()
]

REST_FRAMEWORK = {
    # Add Clerk/JWT authentication once you start wiring protected endpoints.
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.AllowAny'],
}
