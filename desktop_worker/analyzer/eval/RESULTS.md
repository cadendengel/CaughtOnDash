# Nexar evaluation results

Camera-jolt signal (moments.py, detect-4.6) against the Nexar collision-prediction
training set, measured 2026-09-29: 1500 clips, 750 positive (collision or
near-miss, event time annotated), 750 negative. No clip has audio, so only the jolt
half of a moment is tested. Hit = a jolt within +/-1.0s of the labelled event.

Reproduce with `python eval/nexar.py score` after `fetch` and `measure`.

| jolt z >= | hit rate | by chance | negatives flagged | false alarms / min | median offset |
|---|---|---|---|---|---|
| 4 | 94.3% | 79.6% | 100.0% | 48.27 | +0.24s |
| 6 | 88.1% | 55.2% | 98.1% | 25.42 | +0.25s |
| 8 | 80.5% | 37.2% | 91.7% | 15.23 | +0.27s |
| 10 | 73.1% | 26.6% | 77.7% | 10.27 | +0.27s |
| 12 | 67.2% | 20.1% | 65.2% | 7.51 | +0.27s |
| 15 | 59.1% | 14.6% | 49.7% | 5.24 | +0.27s |
| 20 | 49.6% | 10.1% | 36.0% | 3.46 | +0.30s |
| 30 | 33.9% | 6.0% | 25.1% | 2.02 | +0.27s |

- Positives with no jolt near the event: 1 (median peak z there: 19.8).
- The clip's strongest jolt is the event in 49.2% of positives.
- A clip's peak jolt separates positive from negative clips with AUC 0.680 (0.5 = coin).

## Reading

- The jolt is genuine evidence at an event: roughly three times the chance rate at every threshold.
- Alone it does not flag incidents: ordinary driving produces a z >= 12 jolt about every 8 seconds.
  This is the measured case for requiring sound and jolt to agree (SINGLE_SIGNAL_MAX).
- The jolt lags the labelled time by a median ~0.27 s, inside AGREE_SECONDS (1.0).
- Jolt-only `possible` entries (z >= 8) would appear on ~92% of ordinary clips like these.
  Not changed yet: that depends on how often a sound coincides with a random jolt,
  which needs footage with audio.

# NINA audio results

Audio signal (moments.audio_rises, detect-4.6) against NINA's labelled in-vehicle
sounds, measured 2026-09-29. Of NINA's 212 YouTube source videos, 129 could still
be downloaded; 83 are removed or private, and they held almost all the crash
labels, so only **10 crash segments** remain. Reproduce with
`python eval/nina.py fetch`, `measure`, `score`.

Share of labelled segments holding a rise of at least 8 dB (full audio strength):

| sound | segments | rise >= 8 dB |
|---|---|---|
| door | 3 | 100% |
| pothole | 115 | 47% |
| talking | 34 | 35% |
| crash | 10 | 30% |
| music | 17 | 24% |
| horn | 13 | 23% |
| meteo (rain, hail) | 77 | 18% |
| driving | 18 | 17% |
| sirens (ambulance, fire, police) | 275 | 0-3% |

A 2 s stretch of ordinary driving holds a rise of >= 3 dB 48% of the time, >= 5 dB 17%,
>= 8 dB 5%, >= 10 dB 0% (156 stretches).

## Reading

- Everyday sounds, potholes, doors and talking above all, trip the audio signal about
  as readily as crashes do. Alone, the sound is as weak as the jolt: the evidence for
  requiring both runs in both directions.
- Too few crash segments survive to measure the audio hit rate.
- `score` also combines these with Nexar's jolts assuming independence. That estimate
  (about 325 false moments per hour) contradicts the only paired audio-and-video data
  there is: the production corpus re-run on detect-4.6 produced no false moments. The
  two datasets come from different cameras and microphones and do not combine; the
  estimate is kept as a bound, not used for tuning.
- No thresholds changed. Tuning the fused rule needs footage with sound and picture
  recorded together: the owner's own labelled moments, or re-fetched DoTA/CCD sources.

# Paired sound-and-picture footage: none left publicly (2026-09-30)

Testing the fused rule needs crashes with sound and picture recorded together.
`eval/dota.py` runs the full rule on DoTA's 2,724 ego-involved crash clips, but
**all 188 of DoTA's YouTube source videos are now removed or private** (checked
2026-09-30; YouTube's oEmbed lookup confirms 404/403, while still-listed videos
return 200). CCD's clips are rebuilt from 10 fps frames and carry no audio. The
tool is kept in case the sources resurface or local copies turn up.

Until then, the only paired, labelled footage is the owner's own: moment
verdicts recorded in the incident report (`export_moment_labels`).
