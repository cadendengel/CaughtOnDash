# CaughtOnDash Backend Starter

This folder is a Django backend starter for the project.

## What is here

- `caughtondash/` - Django project settings, root URLs, ASGI, and WSGI.
- `apps/accounts/` - Clerk bootstrap and profile endpoint stubs.
- `apps/videos/` - Video upload and management endpoint stubs.
- `apps/feed/` - Feed endpoint stub.

## Suggested first implementation order

1. Add Clerk token verification in the accounts app.
2. Create a local user/profile record for each Clerk identity.
3. Add video upload and completion endpoints.
4. Add feed pagination once videos exist.

## Endpoint placeholders

The files contain `TODO` comments for each endpoint so you can fill in the logic yourself.

## Local setup later

1. Create a virtual environment.
2. Install `requirements.txt`.
3. Copy `.env.example` to `.env`.
4. Run Django migrations after the first models are added.