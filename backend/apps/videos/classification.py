"""Surfacing the analyzer's dashcam verdict.

`classify_footage` has been running on every analysis since M3, storing its
verdict and reasoning in `ai_metadata`, and nothing ever read it back out. A
person looking at the site could not tell an actual dashcam clip from a
portrait video of someone's living room, which is the distinction the
classifier exists to draw.

The verdict is a heuristic and is presented as one. Its reason travels with it
everywhere, so a viewer can see what it was based on and disagree -- the
analyzer's own docstring makes the same point, and a bare true/false badge
would quietly turn a guess into a fact.
"""

from __future__ import annotations


def dashcam_classification(ai_metadata) -> dict | None:
    """The dashcam verdict from stored analysis metadata, or None.

    None means "no opinion" rather than "not a dashcam": a video that has not
    been analyzed, was analyzed before the classifier existed, or was analyzed
    with detection skipped has nothing to say here. Callers must render that
    differently from a negative verdict, which is an actual judgement.
    """
    if not isinstance(ai_metadata, dict):
        return None

    raw = ai_metadata.get('classification')
    if not isinstance(raw, dict):
        return None

    verdict = raw.get('looks_like_dashcam')
    if not isinstance(verdict, bool):
        # Present but malformed -- an older or hand-edited record. Treat it as
        # no opinion rather than coercing it into one.
        return None

    return {
        'looks_like_dashcam': verdict,
        # Always sent. A verdict without its reasoning is not reviewable.
        'reason': str(raw.get('reason') or ''),
        'orientation': str(raw.get('orientation') or ''),
    }
