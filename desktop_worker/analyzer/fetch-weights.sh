#!/usr/bin/env bash
# Fetch the detection weights.
#
# detect-2.0.pt is a YOLOv8 trained on BDD100K (driving footage). Unlike the
# COCO yolov8n.pt it replaced, ultralytics will NOT download this on demand --
# it is not one of the well-known checkpoints -- so every host that runs the
# analyzer needs this script run once. detection.py raises a clear error rather
# than falling back if the file is missing.
#
# Weights are gitignored (see .gitignore: desktop_worker/analyzer/*.pt), which
# is why this fetches rather than the repo carrying a 20 MB binary.
#
# Licence note: BDD100K is non-commercial (research/education). A model trained
# on it inherits that. Fine for CaughtOnDash today; revisit before any
# commercial use.
set -euo pipefail

URL='https://huggingface.co/shravanda/yolo26-bdd100k/resolve/main/yolo26-bdd100k.pt'
SHA256='cb4868fa302584fedd95b0f08ef5555ae8f16999f6dcd38c18172f68846fd07e'

# Land next to this script (== next to detection.py), not in the caller's cwd.
DEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${DEST_DIR}/detect-2.0.pt"

checksum_of() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        sha256sum "$1" | awk '{print $1}'
    fi
}

if [ -f "$DEST" ] && [ "$(checksum_of "$DEST")" = "$SHA256" ]; then
    echo "detect-2.0.pt already present and verified."
    exit 0
fi

echo "Downloading detection weights (~20 MB)..."
# Download to a temporary name so an interrupted run cannot leave a truncated
# file that looks valid enough for the analyzer to load.
TMP="${DEST}.partial"
trap 'rm -f "$TMP"' EXIT
curl -fL --retry 3 --progress-bar -o "$TMP" "$URL"

ACTUAL="$(checksum_of "$TMP")"
if [ "$ACTUAL" != "$SHA256" ]; then
    echo "Checksum mismatch -- refusing to install." >&2
    echo "  expected $SHA256" >&2
    echo "  actual   $ACTUAL" >&2
    exit 1
fi

mv "$TMP" "$DEST"
trap - EXIT
echo "Installed $DEST"
