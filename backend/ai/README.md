AI Worker (starter)
====================

This folder contains a minimal AI worker skeleton and Dockerfiles to run an AI analysis worker.

What was added
- Django management command: `run_ai_worker` (apps.videos.management.commands.run_ai_worker)
  - Polls for `Video` objects with `status='ready'` and no `ai`-sourced tags
  - Appends simple heuristic AI tags to `video.tags` to bootstrap the pipeline
  - Can run once or as a continuous loop via `--loop`

- Docker resources: `docker/ai_worker/Dockerfile` and `docker/ai_worker/worker-entrypoint.sh`

Next steps to integrate real models
- Replace the heuristic tagger in `run_ai_worker._suggest_tags_from_text` with real analysis:
  - Extract audio with `ffmpeg`
  - Run whisper.cpp (or HF) for ASR. The worker now supports optional Hugging Face Inference transcription when `HF_API_TOKEN` is set. It will extract audio via `ffmpeg` and POST the WAV bytes to the HF Inference API. Set `HF_ASR_MODEL` to choose a model (default `openai/whisper-large-v2`).
  - Sample frames and run object detection (YOLOv8/Ultralytics)
  - Call a local LLM or HF endpoint to assemble JSON analysis, validate, then persist

- Consider adding an `AIAnalysis` model to store full analysis JSON and provenance, and to track `analysis_status` separately from `status`.

Running locally (one-shot)
- From repo root: `python manage.py run_ai_worker`

Running in loop for development
- `python manage.py run_ai_worker --loop --sleep 10`

Building the Docker image
- From repo root: `docker build -f docker/ai_worker/Dockerfile -t caughtondash-ai-worker .`
