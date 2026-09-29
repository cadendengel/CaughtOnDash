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
