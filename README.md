# CaughtOnDash

Dashcam upload and feed app with a Django backend, Supabase PostgreSQL, and a Vite frontend.

## Local Setup

Backend:

```powershell
cd backend
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
- `SECRET_KEY`
- `DEBUG`
- `DJANGO_ALLOWED_HOSTS`
- `CORS_ALLOWED_ORIGINS`
- `DATABASE_URL` for Supabase PostgreSQL Session Pooler
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `SUPABASE_BUCKET`

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
