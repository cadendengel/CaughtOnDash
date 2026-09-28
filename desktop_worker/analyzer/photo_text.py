"""Text on the owner's photos: identifiers, legible words, plate candidates.

A dashcam at 720p resolves the overlay and nothing written on the vehicle
ahead. The owner's phone photo does: on the 2026-09-26 case photo this reads
UTAH, RENT-A-TRUCK, COMMERCIAL DUTY and ...TRUCKS.COM, which is what led the
investigation to Barco's rental fleet.

Everything here is a *reading*, never a fact. The plate on that photo comes
out as "S39 SCA" against a true "S39 9CA": Tesseract confuses S with 5, 8 and
9, and over the plate's arch artwork it does so consistently. So characters
from a confusable set are flagged with their alternatives, the crop is kept,
and a person confirms against it.

Tesseract runs as a program, as in overlay.py; photos open through Pillow,
with pillow-heif for iPhone HEIC.
"""

from __future__ import annotations

import difflib
import re
import subprocess
from collections import Counter

import overlay

TILE = 1400
TILE_STEP = 1100
SCALES = (1.0, 0.5)          # full size for small print, half for big lettering
MIN_LINE_ALNUM = 4
# Signage is capitals; texture misread as text is mostly lower case ("eggs",
# "pedid", "Saaeecccomee" on the case photo). Also require real words.
MIN_UPPER_SHARE = 0.7
MIN_MEAN_WORD = 3.0
NEAR_DUPLICATE = 0.8          # RENT-A-TRUCK vs SENT-A-TRUCK: one line, read twice

PLATE_WHITELIST = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 '
PLATE_ASPECT = (1.5, 2.6)    # US plates are 2:1; allow for perspective
PLATE_DARK_LEVELS = (90, 110, 130)
PLATE_BANDS = ((0.15, 0.9), (0.25, 0.85))
PLATE_MAX_CANDIDATES = 12
PLATE_MIN_READS = 2

# Characters Tesseract swaps for one another on plates. A plate position
# holding one of these is flagged, with the others as alternatives, whether
# or not the reads disagreed there -- on the case photo every read agreed on S
# where the plate says 9.
CONFUSABLE = [set('S589'), set('0ODQ'), set('1I7'), set('2Z'), set('B8'), set('G6')]

US_STATES = (
    'ALABAMA ALASKA ARIZONA ARKANSAS CALIFORNIA COLORADO CONNECTICUT DELAWARE FLORIDA GEORGIA '
    'HAWAII IDAHO ILLINOIS INDIANA IOWA KANSAS KENTUCKY LOUISIANA MAINE MARYLAND MASSACHUSETTS '
    'MICHIGAN MINNESOTA MISSISSIPPI MISSOURI MONTANA NEBRASKA NEVADA OHIO OKLAHOMA OREGON '
    'PENNSYLVANIA TENNESSEE TEXAS UTAH VERMONT VIRGINIA WASHINGTON WISCONSIN WYOMING'
).split() + ['NEW HAMPSHIRE', 'NEW JERSEY', 'NEW MEXICO', 'NEW YORK', 'NORTH CAROLINA',
             'NORTH DAKOTA', 'RHODE ISLAND', 'SOUTH CAROLINA', 'SOUTH DAKOTA', 'WEST VIRGINIA']

SAFER = ('https://safer.fmcsa.dot.gov/query.asp?searchtype=ANY&query_type=queryCarrierSnapshot'
         '&query_param={param}&query_string={value}')

USDOT = re.compile(r'\b(?:U\.?\s?S\.?\s*)?D\.?\s?O\.?\s?T\.?\s*(?:NO\.?|#|NUMBER)?\s*[:#]?\s*(\d{5,8})\b', re.I)
MC = re.compile(r'\b(?:MC|MX)\s*(?:NO\.?|#)?\s*[-:#]?\s*(\d{5,7})\b', re.I)
PHONE = re.compile(r'(?<!\d)(?:1[\s.\-]?)?\(?([2-9]\d{2})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})(?!\d)')
DOMAIN = re.compile(r'\b((?:www\.)?[a-z0-9][a-z0-9\-]{1,62}\.(?:com|net|org|us|co|biz|info|io))\b', re.I)


# --- Identifiers (pure) -----------------------------------------------------

def find_identifiers(lines: list[str]) -> list[dict]:
    """Things worth looking up, from OCR lines. Deduplicated, in order found.

    USDOT and MC numbers carry a link to FMCSA's public carrier snapshot --
    the registry commercial vehicles are required to display them for.
    """
    found, seen = [], set()

    def add(kind, value, line, **extra):
        if (kind, value) not in seen:
            seen.add((kind, value))
            found.append({'kind': kind, 'value': value, 'line': line, **extra})

    for line in lines:
        for match in USDOT.finditer(line):
            add('usdot', match.group(1), line, lookup=SAFER.format(param='USDOT', value=match.group(1)))
        for match in MC.finditer(line):
            add('mc', match.group(1), line, lookup=SAFER.format(param='MC_MX', value=match.group(1)))
        for match in PHONE.finditer(line):
            add('phone', '-'.join(match.groups()), line)
        for match in DOMAIN.finditer(line):
            domain = match.group(1).lower()
            has_www = domain.startswith('www.')
            domain = domain[4:] if has_www else domain
            # A domain that starts its line may have lost its beginning to the
            # edge of what OCR saw: the case photo's BARCOTRUCKS.COM read as
            # COTRUCKS.COM. Say so rather than present a fragment as the address.
            starts_line = not line[:match.start()].strip(' .,;:|-')
            add('domain', domain, line, may_be_truncated=starts_line and not has_www)
        upper = line.upper()
        for state in US_STATES:
            if re.search(rf'\b{state}\b', upper):
                add('state', state.title(), line)
    return found


def legible_lines(lines: list[str]) -> list[str]:
    """Lines that look like signage rather than texture read as letters.

    Near-duplicates -- the same sign read at two scales -- keep the first
    reading, which comes from the full-resolution pass.
    """
    kept = []
    for line in lines:
        text = line.strip(' |')
        alnum = sum(ch.isalnum() for ch in text)
        letters = [ch for ch in text if ch.isalpha()]
        if alnum < MIN_LINE_ALNUM or len(letters) < 3 or alnum / max(1, len(text.replace(' ', ''))) < 0.8:
            continue
        if sum(ch.isupper() for ch in letters) / len(letters) < MIN_UPPER_SHARE:
            continue
        words = [w for w in re.split(r'\s+', text) if w]
        if sum(len(w) for w in words) / len(words) < MIN_MEAN_WORD:
            continue
        # Mostly one repeated character is texture: "eeee", "aaaa".
        if max(Counter(text.lower().replace(' ', '')).values()) > 0.5 * alnum:
            continue
        if any(difflib.SequenceMatcher(None, text.upper(), other.upper()).ratio() >= NEAR_DUPLICATE
               for other in kept):
            continue
        kept.append(text)
    return kept


# --- Plates (pure) ----------------------------------------------------------

def _alternatives(char: str) -> list[str]:
    for group in CONFUSABLE:
        if char in group:
            return sorted(group - {char})
    return []


def plate_consensus(reads: list[str]) -> dict | None:
    """Combine several reads of one plate into a reading with its doubts.

    Reads are compared without spaces; the most common length wins, then
    each position takes its most common character. A position is ambiguous if
    the reads disagreed there or its character is confusable.
    """
    cleaned = [r.strip() for r in reads if r and r.strip()]
    compact = [r.replace(' ', '') for r in cleaned]
    plausible = [c for c in compact if 5 <= len(c) <= 8
                 and any(ch.isdigit() for ch in c) and any(ch.isalpha() for ch in c)]
    if len(plausible) < PLATE_MIN_READS:
        return None

    length = Counter(len(c) for c in plausible).most_common(1)[0][0]
    same = [c for c in plausible if len(c) == length]
    if len(same) < PLATE_MIN_READS:
        return None

    chars, positions = [], []
    for index in range(length):
        votes = Counter(c[index] for c in same)
        char, count = votes.most_common(1)[0]
        seen_alternatives = sorted(v for v in votes if v != char)
        alternatives = sorted(set(seen_alternatives) | set(_alternatives(char)))
        chars.append(char)
        if seen_alternatives or _alternatives(char):
            positions.append({'index': index, 'read': char, 'could_be': alternatives,
                              'agreement': round(count / len(same), 2)})

    text = ''.join(chars)
    # Put the space back where most spaced reads had it.
    gaps = Counter(r.index(' ') for r in cleaned if ' ' in r and len(r.replace(' ', '')) == length)
    if gaps:
        gap = gaps.most_common(1)[0][0]
        text = text[:gap] + ' ' + text[gap:]
    return {'text': text, 'reads': len(same), 'uncertain': positions}


# --- Reading images ---------------------------------------------------------

def _ocr(tesseract: str, image, psm: str, whitelist: str | None = None) -> list[str]:
    import cv2

    ok, buffer = cv2.imencode('.png', image)
    if not ok:
        return []
    args = [tesseract, 'stdin', 'stdout', '--psm', psm]
    if whitelist:
        args += ['-c', f'tessedit_char_whitelist={whitelist}']
    completed = subprocess.run(args, input=buffer.tobytes(), capture_output=True, timeout=120, check=False)
    return [line.strip() for line in completed.stdout.decode('utf-8', errors='replace').splitlines() if line.strip()]


def _tiled_lines(tesseract: str, image) -> list[str]:
    import cv2

    lines = []
    for scale in SCALES:
        scaled = image if scale == 1.0 else cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        height, width = scaled.shape[:2]
        for top in range(0, max(1, height - TILE + TILE_STEP), TILE_STEP):
            for left in range(0, max(1, width - TILE + TILE_STEP), TILE_STEP):
                lines += _ocr(tesseract, scaled[top:top + TILE, left:left + TILE], '11')
    return lines


def _overlap(a, b) -> float:
    """Intersection over the smaller box's area."""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return (x2 - x1) * (y2 - y1) / min(a[2] * a[3], b[2] * b[3])


def group_regions(regions: list[tuple[int, int, int, int]]) -> list[list[tuple[int, int, int, int]]]:
    """Regions that are the same plate found twice -- a plate and its frame,
    on the case photo -- grouped, so their reads pool into one candidate."""
    groups = []
    for region in regions:
        for group in groups:
            if any(_overlap(region, other) >= 0.5 for other in group):
                group.append(region)
                break
        else:
            groups.append([region])
    return groups


def plate_regions(image) -> list[tuple[int, int, int, int]]:
    """Plate-shaped, filled rectangles, in full-image pixels, largest first."""
    import cv2

    factor = 4
    small = cv2.resize(image, None, fx=1 / factor, fy=1 / factor, interpolation=cv2.INTER_AREA)
    grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.dilate(cv2.Canny(cv2.bilateralFilter(grey, 9, 75, 75), 40, 160), None)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    area_limit = small.shape[0] * small.shape[1] / 20
    regions = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if h == 0 or not PLATE_ASPECT[0] <= w / h <= PLATE_ASPECT[1]:
            continue
        if not 400 <= w * h <= area_limit or cv2.contourArea(contour) < 0.6 * w * h:
            continue
        regions.append((x * factor, y * factor, w * factor, h * factor))
    regions.sort(key=lambda r: -r[2] * r[3])
    return regions[:PLATE_MAX_CANDIDATES]


def read_plate(tesseract: str, crop) -> list[str]:
    """Several reads of one plate crop: its characters are dark on a light,
    often illustrated, background, so dark-pixel masks at a few levels."""
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    height = crop.shape[0]
    reads = []
    for level in PLATE_DARK_LEVELS:
        mask = cv2.inRange(hsv, (0, 0, 0), (180, 255, level))
        for top, bottom in PLATE_BANDS:
            band = cv2.morphologyEx(mask[int(height * top):int(height * bottom)], cv2.MORPH_OPEN,
                                    np.ones((3, 3), np.uint8))
            image = cv2.copyMakeBorder(255 - band, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
            for psm in ('7', '8'):
                reads += _ocr(tesseract, image, psm, PLATE_WHITELIST)
    return reads


def load_photo(path: str):
    """A photo as an upright BGR array, HEIC included."""
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass   # JPEG and PNG still open; HEIC then fails with a clear error
    with Image.open(path) as image:
        upright = ImageOps.exif_transpose(image).convert('RGB')
        return cv2.cvtColor(np.array(upright), cv2.COLOR_RGB2BGR)


def read(path: str, out_dir: str | None, artifact_id: str, log=lambda message: None) -> tuple[dict, list[dict]]:
    """Everything legible on one photo: (findings, plate-crop artifacts)."""
    import os

    import cv2

    tesseract = overlay.find_tesseract()
    if tesseract is None:
        return {'available': False, 'reason': 'Tesseract is not installed on this worker'}, []

    image = load_photo(path)
    lines = _tiled_lines(tesseract, image)
    plates, artifacts = [], []
    for number, group in enumerate(group_regions(plate_regions(image)), start=1):
        reads = [r for x, y, w, h in group for r in read_plate(tesseract, image[y:y + h, x:x + w])]
        reading = plate_consensus(reads)
        if reading is None:
            continue
        # The tightest box of the group: the plate rather than its surround.
        x, y, w, h = min(group, key=lambda r: r[2] * r[3])
        crop = image[y:y + h, x:x + w]
        reading['bbox'] = [x, y, w, h]
        plates.append(reading)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            target = os.path.join(out_dir, f'photo_{artifact_id}_plate{number}.jpg')
            if cv2.imwrite(target, crop, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                artifacts.append({'path': target, 'kind': 'plate_crop', 'bbox': [x, y, w, h],
                                  'label': f"Plate candidate: {reading['text']}",
                                  'width': int(w), 'height': int(h), 'source_artifact_id': artifact_id})

    legible = legible_lines(lines)
    findings = {
        'available': True,
        'identifiers': find_identifiers(legible),
        'legible': legible,
        'plates': plates,
    }
    log(f"Photo {artifact_id}: {len(legible)} legible line(s), "
        f"{len(findings['identifiers'])} identifier(s), {len(plates)} plate candidate(s)")
    return findings, artifacts
