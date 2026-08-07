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

## Milestone 2 — Python sidecar, real metadata

The point where fabricated data disappears. No models yet, so it is the cheapest
possible proof the subprocess contract works on both machines.

- [ ] `analyzer/analyze.py` — takes a video path, emits the JSON Lines protocol
- [ ] Extract duration, resolution, fps, frame count via OpenCV
- [ ] `PythonAnalyzer : IAnalyzer` — spawn, stream stdout, parse, map progress
- [ ] Timeout, non-zero exit, and missing-`result` handling
- [ ] Kill the process tree on cancellation
- [ ] Clear failure when the Python executable or script is missing
- [ ] `analyzer/requirements-cuda.txt` and `analyzer/requirements-mps.txt`
- [ ] C# unit tests for the protocol parser using a fake script (no Python needed in CI)

**Acceptance:**
- `ai_metadata` contains real duration/resolution/fps for a known file.
- Killing the worker mid-analysis leaves no orphaned Python process.
- A missing Python environment fails the job with an actionable message.

**Verify:** run against a real uploaded video on both hosts; compare reported
duration against the file's actual duration.

---

## Milestone 3 — Object tags

- [ ] YOLOv8n via ultralytics, device auto-detect (`cuda` → `mps` → `cpu`)
- [ ] Sample ~1 frame/second rather than every frame
- [ ] Aggregate detections above a confidence threshold into tags
- [ ] Pin the model version so the two machines cannot silently diverge
- [ ] Keep per-class counts and confidences in `ai_metadata`
- [ ] Templated summary from the real detections
- [ ] Document first-run weight download and cache location

COCO's vocabulary happens to fit dashcam footage well: `car`, `truck`, `bus`,
`motorcycle`, `person`, `bicycle`, `traffic light`, `stop sign`.

**Acceptance:**
- A clip with visible vehicles produces vehicle tags; an empty road does not.
- Same video on both hosts produces the same tags (modulo float noise).
- Runtime is proportional to duration and stays under the configured timeout.

---

## Milestone 4 — Wire the results through

- [ ] Populate `Video.duration_seconds` from analyzer metadata
- [ ] Decide and implement the `ai_tags` shape (see Open decisions)
- [ ] Surface real analysis state in the UI where it is useful
- [ ] Update `desktop_worker/README.md`, which still describes the worker as
      something to be built

---

## Cross-platform setup

Identical Python code; only the install differs.

| | Windows | macOS |
|---|---|---|
| Device | `cuda` | `mps` |
| Torch install | CUDA-specific index URL | default wheel |
| Requirements | `requirements-cuda.txt` | `requirements-mps.txt` |

```python
device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
```

Everything above that line is shared. Pin torch and ultralytics versions in both
files.

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
