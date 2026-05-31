AI on the free web service
==========================

This folder documents the Option 1 setup: keep AI on the same free Render web service, run it on demand, and keep the original upload as the analysis source.

What was added
- Shared AI service: `apps.videos.analysis_service`
  - Runs analysis in the same backend process
  - Stores `AIAnalysis` records and merges AI-sourced tags back into the video
  - Tracks a separate `analysis_status` so the UI can show queued, running, done, and failed states

- Django management command: `run_ai_worker` (apps.videos.management.commands.run_ai_worker)
  - Runs the shared analysis pipeline from Render Shell or locally
  - Can still loop for development, but it is no longer a separate deployment target

Next steps for this mode
- Keep uploads at 60 seconds or less.
- Keep the original upload as the AI source.
- Use a small preview copy only for browser playback if you decide to add one later.
- Trigger analysis from the admin re-analysis endpoint or from Render Shell.
- Monitor queued, running, and done states in the UI.

Running locally (one-shot)
- From repo root: `python manage.py run_ai_worker`

Running in loop for development
- `python manage.py run_ai_worker --loop --sleep 10`
