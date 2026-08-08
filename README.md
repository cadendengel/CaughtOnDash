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
- `DB_CONN_MAX_AGE` — seconds to keep a database connection open, default `0`
  (close after each request). Leave it at 0 behind a connection pooler: the
  pooler is the pool, and under ASGI every thread that touches the ORM holds
  its own connection, so a non-zero value can exhaust the pooler's client
  limit and fail every request with EMAXCONNSESSION. Raise it only on a direct
  connection.
- `ASGI_THREADS` — size of the thread pool running synchronous ORM calls,
  default `8`. Each busy thread can hold a database connection, so this
  effectively caps concurrent connections.

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
in this repo -- Render ignores Procfiles, so the dashboard is the source of
truth and the repo can only document it. Production is on daphne; confirm with
`x-render-origin-server` on any response.

Reverting to gunicorn, or any other WSGI server, keeps HTTP working and
silently disables live updates. Nothing errors: the frontend treats a socket
that will not open as "no push" and the worker falls back to its HTTP
heartbeat, so the only symptom is that live status stops being live.

To check the endpoint itself, force HTTP/1.1 -- WebSocket upgrade headers are
meaningless over HTTP/2, so a default curl reports 404 whatever the server is:

```
curl -i -N --http1.1 -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  https://caughtondash.onrender.com/ws/analysis/
```

`101 Switching Protocols` means live.

The worker also heartbeats over `/ws/worker/`, authenticated with
`WORKER_API_TOKEN`. It falls back to the HTTP heartbeat automatically, so a
WSGI deployment or a proxy blocking WebSockets costs slower liveness detection
rather than a broken worker.

**Single process only.** The channel layer is in-memory, so it works because
one instance runs one worker. Two workers and the layer stops carrying messages
between them, with no error -- browsers connected to one process simply never
hear about changes made in the other. Scaling past one worker means moving
CHANNEL_LAYERS to Redis first.

## Deployment Notes

- Use the Supabase **Transaction Pooler** URL for `DATABASE_URL` (port 6543).
  The Session Pooler on 5432 caps clients at 15 — shared between the web app,
  the desktop worker and every browser — and exceeding it fails every request
  with `EMAXCONNSESSION`, including the `migrate` in the build command, which
  makes the deploy that would fix it impossible. The transaction pooler has no
  such cap. Prepared statements and server-side cursors are disabled in
  settings to suit it; both are harmless on the session pooler, so the same
  settings work with either URL.
- Keep `SUPABASE_SERVICE_KEY` server-side only.
- Set `CORS_ALLOWED_ORIGINS` to the deployed frontend origin.
- Run `python manage.py migrate` on the production backend before serving traffic.
