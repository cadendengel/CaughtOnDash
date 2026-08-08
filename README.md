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

## Serving

The backend must be served over **ASGI**, not WSGI, or WebSockets will not
connect. Live analysis status depends on it.

```
daphne -b 0.0.0.0 -p $PORT caughtondash.asgi:application
```

On Render this is the service's start command, set in the dashboard rather than
in this repo. Replacing gunicorn with the above is the change; leaving gunicorn
in place keeps HTTP working and silently disables live updates, which the
frontend treats as "no push" rather than an error.

**Single process only.** The channel layer is in-memory, so it works because
one instance runs one worker. Two workers and the layer stops carrying messages
between them, with no error -- browsers connected to one process simply never
hear about changes made in the other. Scaling past one worker means moving
CHANNEL_LAYERS to Redis first.

## Deployment Notes

- Use the Supabase Session Pooler URL for `DATABASE_URL`.
- Keep `SUPABASE_SERVICE_KEY` server-side only.
- Set `CORS_ALLOWED_ORIGINS` to the deployed frontend origin.
- Run `python manage.py migrate` on the production backend before serving traffic.
