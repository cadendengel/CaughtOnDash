# CaughtOnDash

Dashcam upload and feed app with a Django backend, Supabase PostgreSQL, and a Vite frontend.

## Local Setup

Backend:

```powershell
cd backend
$env:DEBUG = "True"   # DEBUG defaults to False; required for local development
python manage.py migrate
python manage.py runserver
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

## Environment Variables

Backend:
- `SECRET_KEY` — required whenever `DEBUG` is off. The server refuses to start
  without it, or if it is still the development placeholder.
- `DEBUG` — defaults to `False`. Set `DEBUG=True` for local development;
  an unrecognised value is rejected rather than guessed at.
- `DJANGO_ALLOWED_HOSTS` — comma separated. Defaults to loopback only, so a
  deployment that omits it answers 400 rather than serving any host.
- `CORS_ALLOWED_ORIGINS`
- `DATABASE_URL` for Supabase PostgreSQL Session Pooler
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `SUPABASE_BUCKET`
- `WORKER_API_TOKEN` — shared bearer token for the desktop worker API.
- `CLERK_ISSUER` — e.g. `https://<slug>.clerk.accounts.dev`. Required for
  session-token verification; without it no token can be verified.
- `CLERK_SECRET_KEY` — used by `sync_profiles_from_clerk`.
- `REQUIRE_CLERK_JWT` — when true, a verified token is the only accepted
  identity and the `X-Clerk-User-Id` header is ignored.

Optional transport security (applied only when `DEBUG` is off):
- `SECURE_SSL_REDIRECT` — defaults to `False`.
- `SECURE_HSTS_SECONDS` — defaults to `0` (off). Enable once https is known
  good; browsers cache the policy, so a mistake outlives the deploy.

Frontend:
- `VITE_API_BASE`
- `VITE_CLERK_PUBLISHABLE_KEY`

## Tests

```powershell
cd backend
python manage.py test apps.videos
python manage.py check
```

## Deployment Notes

- Use the Supabase Session Pooler URL for `DATABASE_URL`.
- Keep `SUPABASE_SERVICE_KEY` server-side only.
- Set `CORS_ALLOWED_ORIGINS` to the deployed frontend origin.
- Run `python manage.py migrate` on the production backend before serving traffic.
