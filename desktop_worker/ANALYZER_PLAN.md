# Analyzer Implementation Plan

Replacing `PlaceholderAnalyzer` with real video analysis.

**Approach:** the C# worker downloads the video and spawns a Python subprocess
for inference. First real output is metadata and object tags. Both hosts are
supported — Windows with an NVIDIA GPU, and macOS on Apple Silicon.

---

## Goals

- Every value the worker writes is derived from the actual video file.
- Runs on both worker hosts from one Python codebase.
- Each milestone is independently shippable and verifiable.

## Non-goals (for now)

- **Timestamped events.** The API schema promises them, but genuine temporal
  reasoning is far harder than per-frame detection. Return `[]` rather than
  emitting plausible-but-wrong events, which is what the placeholder does today.
- **Natural-language summaries** beyond a template. A real narrative summary
  needs a vision-language model; that is the hosted-API path, deliberately set
  aside.
- Re-architecting the job queue. Claim/progress/complete/fail/cancel all work
  and are verified on both platforms.

---

## Current state (verified, not assumed)

| Piece | State |
|---|---|
| Job queue, claim, complete, fail, cancel | Working, verified end to end on macOS and Windows |
| `IAnalyzer` seam | Correct shape, only a fake implementation behind it |
| Video download | **Never happens.** `ProcessJob` builds a temp path from the job id and hands that non-existent path to the analyzer |
| `LocalVideoStorageService` | Injected into `WorkerLoopService`, never called |
| `downloadedPath` cleanup in `ProcessJob` | Dead code — the variable is always `null` |
| `JobDto.video_url` | Populated by the backend, never consumed |
| `WorkerApiClient.UpdateJobProgress` | Implemented, **never called** by `WorkerLoopService` |
| `HeartbeatLoop` progress | Hardcoded `0` |
| `Video.duration_seconds` | On the model, always `0` |

Consequence of the last three: during a real analysis the backend and web feed
would show 0% for the entire run, then jump to complete. Invisible with a
3-second placeholder; glaring with a real analyzer.

---

## Architecture

C# spawns Python as a plain subprocess. No local server, no port management, no
daemon lifecycle to babysit.

**Protocol — JSON Lines on stdout:**

```
{"type":"progress","stage":"analyzing","progress":45}
{"type":"progress","stage":"detecting_events","progress":70}
{"type":"result","summary":"...","tags":[...],"events":[],"metadata":{...}}
```

- stderr carries human-readable logs, surfaced into the worker's Activity Log.
- Exit code is the verdict; a non-zero exit fails the job with stderr as the error.
- Exactly one `result` line, last. Missing it is a failure even on exit 0.
- Cancellation kills the process tree.

**New C# class:** `PythonAnalyzer : IAnalyzer`, alongside `PlaceholderAnalyzer`.
Which one is used stays config-driven, so the placeholder remains available for
testing without a Python environment.

**Config additions** (`appsettings.json`, gitignored on both hosts):

```json
{
  "Analyzer": "python",
  "PythonExecutable": "python",
  "AnalyzerScriptPath": "analyzer/analyze.py",
  "AnalyzerTimeoutSeconds": 900
}
```

---

## Milestone 1 — Plumbing, no ML — DONE

Needed regardless of which model wins. Deletes dead code and fixes progress
reporting before a slow analyzer exposes it.

- [x] Download `JobDto.VideoUrl` to `LocalVideoStorageService.GetVideoPath(videoId)`
- [x] Report download progress as stage `downloading`
- [x] Call `UpdateJobProgress` from `ProcessJob` so the backend sees real progress
- [x] Feed real progress into `HeartbeatLoop` instead of the hardcoded `0`
- [x] Delete the dead `downloadedPath` branch; clean up the real file in `finally`
- [x] Fail the job with a clear error when the download fails or the URL is empty
- [x] Honour cancellation mid-download

Verified on macOS against the local stack: a 2.9 MB download reported smooth
0-100 progress, 77 progress updates reached the backend, the file was removed
afterwards, and the job completed. A video with no `playback_url` and one with
a dead URL both failed at stage `downloading` with readable errors, where
previously they would have "analyzed" a path that did not exist and reported
success.

**Files:** `Services/WorkerLoopService.cs`, `Services/WorkerApiClient.cs`,
`Services/LocalVideoStorageService.cs`

**Acceptance:**
- A claimed job downloads a real file to the work directory, and the file is
  removed afterwards.
- `GET /api/videos/<id>/` shows `analysis_progress` climbing during the run —
  not 0 then 100.
- A job whose `video_url` is empty fails with a useful message rather than
  "analyzing" a path that does not exist.

**Verify:** local stack on either host. Seed a ready video with a real
`playback_url`, run the worker, watch `analysis_progress` move.

---

## Milestone 2 — Python sidecar, real metadata — DONE

The point where fabricated data disappears. No models yet, so it is the cheapest
possible proof the subprocess contract works on both machines.

- [x] `analyzer/analyze.py` — takes a video path, emits the JSON Lines protocol
- [x] Extract duration, resolution, fps, frame count via OpenCV
- [x] `PythonAnalyzer : IAnalyzer` — spawn, stream stdout, parse, map progress
- [x] Timeout, non-zero exit, and missing-`result` handling
- [x] Kill the process tree on cancellation
- [x] Clear failure when the Python executable or script is missing
- [x] One `analyzer/requirements.txt` for every host (CUDA is an upgrade step)
- [x] C# unit tests for the protocol parser, no Python required — 18 tests

Verified on macOS: a real 640x360 25fps clip produced exactly those values plus
frame count, duration and file size. A corrupt file failed at stage `analyzing`
with "could not read the video", and a missing file with "could not find the
video file" — distinct exit codes, distinct messages.

Two things surfaced only by running it end to end:

- Milestone 1 reported progress on **every percent**, so a fast download fired
  ~77 HTTP requests and write transactions in under a second and SQLite answered
  "database is locked". Backend reporting is now throttled to 10% steps, stage
  changes, and a 5s heartbeat; the UI still updates on every tick.
- Even throttled, the local sqlite settings needed `transaction_mode=IMMEDIATE`.
  Django's default defers the write lock, so a read-then-write transaction must
  upgrade mid-flight, and a failed upgrade returns SQLITE_BUSY instantly — which
  `timeout` cannot wait out. Every worker write has that shape. With WAL plus
  IMMEDIATE, a full job run produces zero locks.

**Acceptance:**
- `ai_metadata` contains real duration/resolution/fps for a known file.
- Killing the worker mid-analysis leaves no orphaned Python process.
- A missing Python environment fails the job with an actionable message.

**Verify:** run against a real uploaded video on both hosts; compare reported
duration against the file's actual duration.

---

## Milestone 3 — Object tags — DONE

- [x] YOLOv8n via ultralytics, device auto-detect (`cuda` → `mps` → `cpu`)
- [x] Sample ~1 frame/second rather than every frame
- [x] Aggregate detections above a confidence threshold into tags
- [x] Pin the model version so hosts cannot silently diverge
- [x] Keep per-class counts and confidences in `ai_metadata`
- [x] Templated summary from the real detections
- [x] Document first-run weight download and cache location

COCO's vocabulary happens to fit dashcam footage well: `car`, `truck`, `bus`,
`motorcycle`, `person`, `bicycle`, `traffic light`, `stop sign`.

Verified on macOS end to end through the worker: a clip containing a bus and
people produced `["bus", "person"]` at 0.88/0.87 confidence on `mps`, and a
synthetic clip with no real objects produced `[]` with a summary saying so
rather than implying an empty road. `--no-detect` still reports metadata and
says detection was not run.

Two dependency problems surfaced during the install and are worth remembering:

- Installing ultralytics unpinned pulled its own numpy and opencv, silently
  overriding the milestone 2 pins. **Two opencv distributions both provide
  `cv2` and overwrite each other**, and uninstalling either breaks the other --
  which happened here and needed a forced reinstall to repair. Requirements now
  pin exact versions and keep exactly one opencv.
- Model weights download into the working directory on first run, so they are
  gitignored. A new host needs network access for its first analysis.

Sampling is capped at 300 frames and spread across the whole video rather than
truncating it, so a long clip stays covered end to end on the slow laptop.

---

## Milestone 4 — Wire the results through

- [ ] Populate `Video.duration_seconds` from analyzer metadata
- [ ] Decide and implement the `ai_tags` shape (see Open decisions)
- [ ] Surface real analysis state in the UI where it is useful
- [ ] Update `desktop_worker/README.md`, which still describes the worker as
      something to be built

---

## Hosts and hardware

GPU is an **optional accelerator, never a requirement**. The same script runs
everywhere and uses whatever it finds:

```python
device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
```

| Host | Compute | Role |
|---|---|---|
| Home desktop — Ryzen 9 5900X, RTX 3070 Ti | `cuda` | Primary for real workloads. Not reachable from this network yet. |
| This Mac — Apple M3, 8 core, 16 GB | `mps` | Development and everyday analysis |
| Windows laptop — i7-10610U, Intel UHD | `cpu` | Correctness test bed. Works, just slow. |

Rough YOLOv8n cost at 1 frame/second on a 5-minute clip: seconds on the 3070 Ti,
10-20s on the M3, 1-2 minutes on the laptop. All acceptable; only the laptop is
slow enough to notice.

**One base requirements file** covers every host: the default PyPI torch wheel
gives MPS on macOS and CPU on Windows. CUDA is a documented upgrade step on the
desktop only, installing torch from NVIDIA's index. No per-platform requirements
files, no code differences.

## Python environment

System Python is unusable here: the Mac has only 3.14 (too new for torch and
opencv wheels) and the Windows laptop defaults to 3.13. Pin **Python 3.12** in a
dedicated venv per host, kept out of git.

| Host | Provisioning |
|---|---|
| Windows | 3.12 already installed: `py -3.12 -m venv .venv` |
| Mac | `brew install python@3.12`, then `python3.12 -m venv .venv` |

Same version everywhere means one pinned requirements file is meaningful rather
than aspirational.

---

## Open decisions

**`ai_tags` shape.** Stored as plain strings, while `tags` uses
`{text, source}` and the frontend renders those as source-coloured pills. AI
tags cannot participate today. Normalising to `{text, source: 'ai'}` is the
consistent choice — `tagging.py` already supports an `ai` source — but it is a
schema change touching existing rows.

**Whether tags merge into `tags`.** Keeping `ai_tags` separate preserves the
distinction between what a human said and what a model guessed. Merging makes
search and filtering simpler. Search currently indexes `tags` only, so AI tags
are invisible to search either way until this is settled.

**Confidence threshold.** Too low produces noise, too high misses things. Start
at 0.5, tune against real footage.

---

## Risks

| Risk | Mitigation |
|---|---|
| Two Python environments drift | Pinned requirements per platform; assert versions in the `result` metadata |
| First-run model download needs network | Document it; fail with a clear message rather than hanging |
| Orphaned Python processes | Kill the process tree on cancel; test it explicitly |
| Long analyses look hung | Milestone 1's progress reporting lands first, on purpose |
| Placeholder results already in production | One video currently carries them; re-analyze once M3 ships |

---

## Suggested order

M1 → M2 → M3 → M4, each its own branch and PR.

M1 and M2 together remove every fabricated value from the pipeline, which is the
majority of the benefit. M3 is where it becomes genuinely useful.
