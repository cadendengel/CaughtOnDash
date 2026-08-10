# Making the analyzer better — a plan, not a tweak

## The problem, precisely

The dashcam classifier decides `looks_like_dashcam` from two signals
(`desktop_worker/analyzer/detection.py`):

1. a road object (car/truck/bus/…) present in **≥ 50%** of sampled frames
   (`DASHCAM_ROAD_SHARE = 0.5`), and
2. landscape orientation.

This equates "dashcam" with "busy traffic," which is wrong for any quiet road.
Measured on our own test corpus:

| clip | road-object share | verdict | reality |
|---|---|---|---|
| Whiteout conditions (snow) | 1.00 | ✅ dashcam | correct |
| Snow-lined lanes (snow) | 0.083 | ❌ "intermittently" | **wrong** — empty rural road |
| Low winter sun | 0.208 | ❌ "intermittently" | **wrong** — quiet road |

The two snow clips land on opposite sides of the verdict, so **it is not the
snow** — it is traffic density. An empty snowy lane is still dashcam footage;
there just aren't vehicles to count.

## Why threshold tuning is a dead end

The night fix lowered `DEFAULT_CONFIDENCE` 0.5→0.35 and floored the sample count.
That helps *faint* objects. It cannot help *absent* ones, and it has a cost:

- At 0.35 the analyzer already hallucinates `airplane`, `boat`, `teddy bear` on
  road clips. Lower it further and moderation gets noisier.
- Every condition (night, snow, rain, fog, tunnel) wants its own threshold, and
  they conflict. That is a treadmill, not a roadmap.

The object count is a **proxy** for the real question. It should corroborate the
verdict, not be the gate.

## The sustainable signal: camera egomotion

The defining property of a dashcam is not what it sees but **how it moves**: a
forward-mounted camera on a moving vehicle produces a characteristic optical-flow
field — the scene streams outward from a vanishing point near the horizon. This
is independent of weather, lighting, and traffic density, so it correctly passes
an empty snowy road and correctly rejects a handheld portrait video of a room.

### Implementation sketch (in the existing analyzer)

The analyzer already samples frames and has OpenCV available. Add an egomotion
pass alongside detection:

1. On the sampled frames, compute sparse optical flow (`cv2.goodFeaturesToTrack`
   + `cv2.calcOpticalFlowPyrLK`) or dense flow (`cv2.calcOpticalFlowFarneback`)
   between consecutive samples.
2. Fit a focus-of-expansion: forward driving yields flow vectors that radiate
   from a stable point roughly on the horizon. Score how radial + consistent the
   field is across the clip.
3. Combine into the verdict: `looks_like_dashcam = strong_egomotion AND landscape`,
   with the object share kept as a secondary/corroborating signal and surfaced in
   the reason (so a human can still see it).

This is a bounded, self-contained addition to `detection.py` — no new model, no
GPU requirement, runs on the frames already decoded. Estimated a few days
including tuning against a labeled set of our clips (dashcam vs handheld vs
static).

### If egomotion proves insufficient

Fallbacks, in increasing cost:
- Upgrade the detector (YOLOv8n → v8s/m) for fewer misses and fewer
  hallucinations at a saner confidence.
- Confidence-weight the object share instead of a hard count.
- Fine-tune a small scene classifier on labeled dashcam frames (the highest
  ceiling, the most effort).

## Why this pairs with the requeue-all we just built

Any of these changes the analyzer output for existing videos. That is exactly
what version-aware **requeue-all** is for: bump `ANALYZER_VERSION` in `analyze.py`
(e.g. `detect-1.0` → `detect-2.0`), deploy the new analyzer, press **Requeue
outdated**, and the whole corpus re-runs onto the new algorithm — skipping
anything already current. The version stamp + requeue button make the analyzer
safely iterable, which is the precondition for "ever-improving."

## Recommended sequence

1. **Label a small ground-truth set** from our own clips (dashcam / not) — needed
   to measure any change honestly. Without it, every tweak is a guess.
2. **Prototype the egomotion score** in `detection.py`, measure against the set.
3. If it clears the bar, ship it as `detect-2.0`, requeue-all, spot-check.
4. Keep the object signal as corroboration, never the sole gate.
