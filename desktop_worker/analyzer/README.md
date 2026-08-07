# Analyzer

Python video analysis, invoked by the C# desktop worker as a subprocess.

The worker downloads a video, runs `analyze.py` against it, and reads results
back as JSON Lines on stdout. There is no server and no daemon: one process per
job, which cannot wedge the worker if it crashes.

## Setup

Requires **Python 3.12**. Newer versions are ahead of the wheels we need; older
ones are untested. Same version on every host so the pinned requirements mean
something.

### macOS

```bash
brew install python@3.12
cd desktop_worker/analyzer
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

### Windows

```powershell
cd desktop_worker\analyzer
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

### NVIDIA GPU (optional)

The default wheels give MPS on macOS and CPU everywhere else, which is enough to
run. On a machine with an NVIDIA GPU, install CUDA torch afterwards for a large
speedup. Nothing else changes — the analyzer detects the device at runtime.

```bash
./.venv/bin/pip install --index-url https://download.pytorch.org/whl/cu124 torch torchvision
```

`.venv/` is gitignored. Each host provisions its own.

## Point the worker at it

In the worker's `appsettings.json` (also gitignored):

```json
{
  "Analyzer": "python",
  "PythonExecutable": "/absolute/path/to/desktop_worker/analyzer/.venv/bin/python",
  "AnalyzerScriptPath": "/absolute/path/to/desktop_worker/analyzer/analyze.py",
  "AnalyzerTimeoutSeconds": 900
}
```

On Windows the interpreter is `.venv\Scripts\python.exe`.

Set `"Analyzer": "placeholder"` to fall back to the stub — useful on a machine
with no Python environment. An unrecognised value falls back to the placeholder
with a warning rather than refusing to start.

## Running it directly

Useful when the worker reports an analyzer failure and you want the raw output:

```bash
./.venv/bin/python analyze.py /path/to/video.mp4
```

## Protocol

One JSON object per line on stdout:

```
{"type":"progress","stage":"analyzing","progress":45}
{"type":"result","summary":"...","tags":[],"events":[],"metadata":{...}}
```

Exactly one `result`, last. Anything human-readable goes to stderr and is
surfaced in the worker's activity log. Non-JSON lines on stdout are ignored, so
a chatty library cannot fail a job.

Exit codes carry meaning, so the worker can explain a failure rather than just
reporting one:

| Code | Meaning |
|---|---|
| 0 | Success (a `result` line must have been emitted) |
| 2 | Video file not found |
| 3 | Missing Python dependency — install requirements |
| 4 | Video could not be read: corrupt, truncated, or unsupported |
| 5 | Object detection failed (model missing, or video unreadable partway) |

## What it reports

**Container metadata** — width, height, fps, frame count, duration, file size.

**Object tags** — YOLOv8n over sampled frames. COCO's vocabulary suits dashcam
footage: `car`, `truck`, `bus`, `motorcycle`, `person`, `bicycle`,
`traffic light`, `stop sign`.

Sampling defaults to one frame per second, capped at 300 frames. The cap is
spread across the whole video rather than truncating it, so a long clip is still
covered end to end, just more coarsely. A class must appear in at least 5% of
sampled frames to become a tag, which drops single-frame false positives without
losing brief real events.

`events` stays empty. Locating an incident in time is a different problem from
recognising objects, and inventing timestamps would be exactly the placeholder
behaviour this replaced.

### Options

```bash
./.venv/bin/python analyze.py video.mp4 --sample-fps 2 --max-frames 500
./.venv/bin/python analyze.py video.mp4 --confidence 0.4
./.venv/bin/python analyze.py video.mp4 --no-detect     # metadata only
```

`--no-detect` is the fallback for a host without the ML dependencies installed:
metadata still works and the summary says detection was not run, rather than
implying nothing was there.

### Model weights

`yolov8n.pt` (~6 MB) downloads on first run into the working directory and is
gitignored. The first analysis on a new host therefore needs network access and
takes noticeably longer than subsequent ones — measured on the CPU-only laptop,
an 8 second clip took 58s cold and 6.8s warm. Nearly all of that gap is the
download, not inference.

### Speed

Roughly, for a five-minute clip at one frame per second:

| Host | Device | Time |
|---|---|---|
| Desktop, RTX 3070 Ti | `cuda` | seconds |
| Mac, Apple M3 | `mps` | 10-20s |
| Laptop, Intel i7-10610U | `cpu` | 1-2 min |

All workable. Lower `--sample-fps` if the slowest host becomes a problem.
