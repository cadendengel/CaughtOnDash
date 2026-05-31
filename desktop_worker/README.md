# Desktop Worker

This folder will hold the local desktop worker that runs on your PC and processes videos manually when you are available.

The worker is intended to work with the main CaughtOnDash backend and frontend:

- The backend stores the queue and video status.
- The desktop worker checks in with the backend and reports whether it is online.
- The worker downloads videos, processes them locally, and sends results back to the server.
- The backend updates the frontend and can notify you by phone or email when work is waiting.

## Recommended Stack

- C# / .NET for the desktop app shell and user interface.
- Python for the actual video analysis and GPU-heavy processing.
- HTTPS requests for communication with the backend.

## What This Worker Should Do

- Show whether the worker is online or offline.
- Show how many videos are waiting to be processed.
- Let you start or stop processing manually.
- Claim one video at a time from the backend.
- Download the video, run analysis locally, and upload the results.
- Send a heartbeat so the backend knows the app is running.

## Basic Workflow

1. Start the desktop worker on your PC.
2. The worker sends a heartbeat to the backend.
3. The backend marks the worker as online.
4. If there are queued videos, you can begin processing them.
5. The worker requests a video, analyzes it, and returns the result.
6. The backend saves the result and updates the frontend.

## Suggested Folder Layout

```text
desktop_worker/
	README.md
	src/
		worker/        # C# app shell and UI
		analyzer/      # Python analysis code
	scripts/         # helper scripts for local setup
```

## Setup Notes

Before building the worker, make sure you have:

- .NET SDK installed
- Python 3.11 or newer installed
- GPU drivers installed if you plan to use local GPU processing
- Backend API URL and authentication details from the main app

## Environment Variables

These are example values the worker may need:

```env
BACKEND_API_URL=https://your-backend.example.com
WORKER_ID=your-worker-id
WORKER_TOKEN=your-worker-token
PYTHON_PATH=C:\\Path\\To\\Python.exe
```

## Next Steps

When you start implementing this worker, add:

- a C# project for the UI and backend communication
- a Python module for analysis
- a heartbeat endpoint or polling loop
- a result upload endpoint
- a small settings screen for backend URL and credentials

## Notes

This README is intentionally basic and can be expanded once the worker code exists.
