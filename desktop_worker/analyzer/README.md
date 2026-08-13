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

**Object tags** — YOLOv8 trained on BDD100K over sampled frames. A driving
vocabulary: `car`, `truck`, `bus`, `motorcycle`, `bicycle`, `person`, `rider`,
`traffic light`, `traffic sign`, `train`.

Sampling defaults to one frame per second, capped at 300 frames. The cap is
spread across the whole video rather than truncating it, so a long clip is still
covered end to end, just more coarsely. A class must appear in at least 5% of
sampled frames to become a tag, which drops single-frame false positives without
losing brief real events.

**A dashcam signal** — whether the footage looks like dashcam material, in
`metadata.classification`:

```json
{"looks_like_dashcam": true,
 "reason": "camera moving with the road, though few road objects were detected",
 "orientation": "landscape",
 "road_classes_detected": [],
 "strongest_road_class_share": 0.0,
 "egomotion": {"radiality": 0.96, "pixels_per_frame": 8.89,
               "looks_like_driving": true}}
```

Landscape orientation is required, and then **either** road objects across most
frames **or** forward/rearward camera motion. Portrait stays a hard gate: a
phone in a car mount is not a dashcam.

Either, not both, because the two signals fail in opposite situations and the
test corpus contains both failures. An empty rural road has no object to count
and scores 0.96 radiality; a clip of gridlock is full of cars and scores 0.16,
because the vehicle is barely moving. Requiring both would reject each of them.

**Egomotion** is optical flow between *adjacent* frames — not between the
one-second samples, where nothing tracks at road speed. It scores how
consistently the flow radiates from a focus, and how fast. The sign is
discarded: a rear-facing camera and a reversing vehicle produce the same field
converging inward (measured −0.975 against +0.974 on the same clip reversed), so
scoring the sign would reject every rear-facing dashcam.

Speed matters as much as shape. A slow synthetic zoom scored 0.998 radiality —
higher than any real dashcam clip — on 0.5 pixels per frame, and an aerial drone
shot scored 0.87 on 1.7. Real driving moves the scene: the clips that depend on
egomotion alone move 3.7 and 8.9 pixels per frame.

Measured on 14 driving clips and 11 others (walking, hiking, cycling, panning,
static): 14/14 driving clips correct, 9/11 others rejected. Both remaining false
positives are bicycle footage shot in city traffic, which passes on the *object*
signal — they contain real cars — and did so before egomotion existed.

It is a heuristic and reports itself as one. The signals behind the verdict are
returned alongside it so a person can disagree. It is not turned into a tag,
because it is a judgement about the upload rather than an observation of it.

`events` stays empty. Locating an incident in time is a different problem from
recognising objects, and inventing timestamps would be exactly the placeholder
behaviour this replaced.

## Tests

```bash
./.venv/bin/python -m unittest discover -s desktop_worker/analyzer
```

Frame selection, summary wording and the dashcam heuristic are pure functions,
so these need no model, no weights and no video file.

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

`detect-2.0.pt` (~20 MB) is a YOLOv8 trained on BDD100K, a real driving
dataset. It is **not** downloaded automatically. The COCO `yolov8n.pt` it
replaced was a well-known checkpoint that ultralytics fetched on demand; these
are custom weights, so each host fetches them once:

```bash
./fetch-weights.sh          # macOS / Linux
.\fetch-weights.ps1         # Windows
```

Both verify a pinned SHA-256 and are safe to re-run — an already-correct file
is left alone. Weights stay gitignored (`desktop_worker/analyzer/*.pt`), which
is why this is a fetch rather than a file in the repo.

If the weights are missing, `analyze.py` fails with a message pointing here
rather than falling back to other weights. That is deliberate: a silent
fallback would produce plausible-looking results stamped with the
`detect-2.0` analyzer version, and "Requeue outdated" would then mark the
corpus as current on a model that never ran.

**Licence:** BDD100K is non-commercial (research/education), and a model
trained on it inherits that. Fine for CaughtOnDash as a personal project;
revisit before any commercial use.

#### Why this model

The COCO-trained `yolov8n.pt` had two failures measured on our own footage:

- It invented non-driving classes — `airplane` and `boat` at confidence 0.35.
- It missed vehicles in snow badly enough to flip the verdict. Two winter
  clips scored a road-object share of 0.21 and 0.08 and were classified "not
  dashcam". They score 0.75 and 0.67 here.

Across 14 test clips, the dashcam verdict went from 10/14 to 12/14 correct,
with zero hallucinated classes. The two remaining failures are genuinely empty
roads with no vehicles to detect — no detector fixes those; see
`DETECTION_IMPROVEMENT_PLAN.md` for the egomotion approach.

**Known limitation:** this checkpoint reads TikTok's search-bar overlay as a
`traffic sign` at 0.66 confidence. Since `traffic sign` counts toward the
road-scene signal and a watermark persists across every frame, a landscape clip
with social-media chrome could score as a road scene on the overlay alone.
Portrait phone video is still rejected on orientation. Not observed on burned-in
dashcam timestamps, which produce no detection.

### Speed

Roughly, for a five-minute clip at one frame per second:

| Host | Device | Time |
|---|---|---|
| Desktop, RTX 3070 Ti | `cuda` | seconds |
| Mac, Apple M3 | `mps` | 10-20s |
| Laptop, Intel i7-10610U | `cpu` | 1-2 min |

All workable. Lower `--sample-fps` if the slowest host becomes a problem.
