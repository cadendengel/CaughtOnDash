# Desktop Worker

Claims dashcam videos from the backend, downloads them, analyzes them locally,
and reports the results back.

Runs on Windows and macOS from a shared core. GPU is an accelerator, never a
requirement — the analyzer uses CUDA, Apple Silicon MPS, or CPU depending on
what the host has.

## Layout

```
src/
  CaughtOnDash.Worker.Core/        net8.0   — services, models, protocol. All logic.
  CaughtOnDash.Worker.Core.Tests/  net8.0   — unit tests, no Python required
  worker/                          net8.0-windows — WPF host
  CaughtOnDash.Worker.Mac/         net8.0   — Avalonia host
analyzer/                          Python 3.12 — the analysis itself
```

Everything meaningful lives in `Core`; the two host projects are thin UI shells
over the same `WorkerSession`. WPF cannot build on macOS, which is why the
Avalonia host exists.

## Build and run

```bash
# macOS
dotnet run --project src/CaughtOnDash.Worker.Mac

# Windows
dotnet run --project src\worker\CaughtOnDash.Worker.csproj

# tests (either platform)
dotnet test src/CaughtOnDash.Worker.Core.Tests
```

Analyzer setup — Python version, dependencies, and the optional CUDA upgrade —
is in [`analyzer/README.md`](analyzer/README.md).

## Configuration

`appsettings.json` beside the host project, gitignored because it holds the API
token:

```json
{
  "BackendUrl": "https://your-backend",
  "ApiToken": "the WORKER_API_TOKEN from the backend environment",
  "WorkerId": "caden-desktop-1",
  "WorkerName": "Caden Desktop",
  "Analyzer": "python",
  "PythonExecutable": "/absolute/path/to/analyzer/.venv/bin/python",
  "AnalyzerScriptPath": "/absolute/path/to/analyzer/analyze.py",
  "AnalyzerTimeoutSeconds": 900
}
```

`"Analyzer": "placeholder"` skips Python entirely and returns stub results —
useful for exercising the queue on a host with no Python environment. It is the
default, so a misconfigured worker still runs rather than failing to start.

## How a job flows

1. Poll `/api/videos/worker/jobs/next/` every 15 seconds
2. Claim it — the backend rejects a second claim on the same job
3. Download the video to a temp working directory
4. Run the analyzer, forwarding progress to the backend as it goes
5. Report results, or report the failure with the stage it happened at
6. Delete the downloaded file, including after a failure

A heartbeat goes out every 10 seconds throughout, carrying the current stage and
progress. A worker that dies mid-job leaves its video in `processing`; the
backend treats that as stale after five minutes and lets it be re-queued.

## What the analyzer reports

Real container metadata — resolution, fps, frame count, duration — and object
tags detected with YOLOv8n over sampled frames. Detected tags are published to
the video's `tags` with source `ai`, which is what the feed renders and what
search indexes.

`events` is deliberately empty. Locating an incident in time is a different
problem from recognising objects, and timestamps that were never measured would
be worse than none.

## Logs

The Activity Log panel shows the current session. The same lines also go to a
file that outlives the process:

```
Windows   %LOCALAPPDATA%\CaughtOnDash\logs\worker.log
macOS     ~/Library/Logs/CaughtOnDash/worker.log
Linux     ~/.local/state/CaughtOnDash/logs/worker.log
```

The path is printed as the first line of the log itself, so it can be found
without remembering the table above. Files roll at 2 MB, five kept.

Near the top of every run is the effective configuration:

```
Worker mac-1 (Mac) -> backend https://caughtondash.onrender.com, token set (44 chars), analyzer python
```

That line exists because "which backend is it actually talking to" has been the
first question in most of this project's worker problems, and the token is
reduced to a length so a log can be pasted somewhere without leaking one.

Logging never throws: an unwritable location disables file output and the
worker carries on. If the file is missing, the console line says why.

## Troubleshooting

**Heartbeats appear to be rejected** — read the log file before assuming the
backend is at fault. `Connection refused` means nothing is listening at
`BackendUrl`, which is a different problem from a 401.

**Every request returns 401** — `ApiToken` does not match `WORKER_API_TOKEN` on
the backend. They are separate from user authentication; Clerk settings do not
affect the worker.

**Jobs are found but never claimed** — usually a stale build. Check that the
worker log prints a real job id rather than all zeros.

**"Analyzer is missing a Python dependency"** — the venv exists but
`requirements.txt` was not installed into it, or `PythonExecutable` points at
system Python instead of the venv.

**The queue is always empty** — a video only becomes claimable once its upload
completes (`status='ready'`). A record whose upload failed stays `pending` and
is skipped.
