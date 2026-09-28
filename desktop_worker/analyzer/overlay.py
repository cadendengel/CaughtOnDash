"""Read the dashcam's burned-in overlay: its clock, speed and position.

Most dashcams stamp a line of text into every frame -- on the 2026-09-26 clip,
`09/26/2026 12:16:44 PM N30° 13' 42.00" W097° 37' 12.00" 80MPH`. The video
file carries none of it as metadata, so this is the only way to say *when* and
*where* a moment happened, and how fast the camera car was going. All of it is
private: it is the uploader's own location and speed.

OCR is Tesseract, run as a program like ffprobe rather than through a Python
package: the OCR packages that bundle a model also bundle their own opencv,
and a second opencv in this environment breaks the first (see requirements).
Without Tesseract the step is skipped and says so.

OCR slips, so no single reading is trusted. Each sampled second is cropped
tight to the text row and read three ways; each reading is parsed leniently,
and Tesseract's habit of reading 8 and 9 as S (S has to be allowed, for
southern latitudes) becomes two candidates rather than a wrong answer. The
candidates are then settled against each other over time: the clock offset is
the median of every reading, each second takes the candidate nearest its
neighbours, and a five-sample median filter removes what is left. A median
filter passes steady braking or acceleration through unchanged, so a hard stop
at an impact survives; a one- or two-sample misread does not. On the
2026-09-26 clip one reading put the car at 30°12' where the others said 30°14'
-- 3.7 km apart, a second apart -- and 79 mph was once read as 29.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import statistics
import subprocess
from datetime import datetime, timedelta

# Bands of the frame where overlays live, as (top, bottom) shares of height.
BANDS = {'bottom': (0.92, 1.0), 'top': (0.0, 0.08)}
SAMPLE_SECONDS = 1.0
MAX_SAMPLES = 120
PROBE_SAMPLES = 4           # samples used to decide which band has the text
UPSCALE = 3
WHITELIST = '0123456789/:.NSEWPAMHKkmh "\''
# Yellow and white are the usual overlay colours; the HSV mask catches yellow,
# Otsu catches white or anything else brighter than the road under it.
YELLOW_LOW, YELLOW_HIGH = (15, 60, 120), (45, 255, 255)

CLOCK_TOLERANCE_SECONDS = 2.0
NEIGHBOURS = 3
MEDIAN_WINDOW = 5
MAX_SPEED = 250                       # in the overlay's own unit
MAX_AMBIGUOUS = 3                     # S-for-8/9 slips expanded per line

WINDOWS_TESSERACT = (
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
)
UNIX_TESSERACT = ('/opt/homebrew/bin/tesseract', '/usr/local/bin/tesseract', '/usr/bin/tesseract')


# --- Parsing (pure) ---------------------------------------------------------

DATE_YMD = re.compile(r'(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})')
DATE_XXY = re.compile(r'(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})')
TIME = re.compile(r'(\d{1,2}):(\d{2}):(\d{2})\s*([AP])?\.?\s*M?', re.IGNORECASE)
DMS = r'(\d{1,3})\D{0,3}?(\d{1,2})\D{1,3}?(\d{1,2}(?:\.\d+)?)'
LAT_DMS = re.compile(r'([NS])\s*' + DMS)
LON_DMS = re.compile(r'([EW])\s*' + DMS)
LAT_DEC = re.compile(r'([NS])\s*(\d{1,2}\.\d{3,})')
LON_DEC = re.compile(r'([EW])\s*(\d{1,3}\.\d{3,})')
SPEED = re.compile(r'(\d{1,3})\s*(MPH|KM/?H|KPH)', re.IGNORECASE)


def _date(text: str) -> tuple[int, int, int] | None:
    """(year, month, day). Day-first vs month-first is decided by which part
    cannot be a month; when both can, month-first, the US dashcam default."""
    match = DATE_YMD.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
    else:
        match = DATE_XXY.search(text)
        if not match:
            return None
        first, second, year = (int(g) for g in match.groups())
        month, day = (second, first) if first > 12 else (first, second)
    if not (1 <= month <= 12 and 1 <= day <= 31 and 2000 <= year <= 2100):
        return None
    return year, month, day


def _time(text: str) -> tuple[int, int, int] | None:
    match = TIME.search(text)
    if not match:
        return None
    hour, minute, second = (int(g) for g in match.groups()[:3])
    meridiem = (match.group(4) or '').upper()
    if meridiem and not 1 <= hour <= 12:
        return None
    if meridiem == 'P' and hour != 12:
        hour += 12
    elif meridiem == 'A' and hour == 12:
        hour = 0
    if not (hour < 24 and minute < 60 and second < 60):
        return None
    return hour, minute, second


def _coordinate(text: str, dms: re.Pattern, dec: re.Pattern, limit: float) -> float | None:
    # Decimal first: the degrees-minutes-seconds pattern is lenient enough to
    # misread 33.8688 as 33 degrees 86 minutes and give up.
    match = dec.search(text)
    if match:
        hemisphere, value = match.group(1), float(match.group(2))
    else:
        match = dms.search(text)
        if not match:
            return None
        hemisphere, degrees, minutes, seconds = match.groups()
        degrees, minutes, seconds = int(degrees), int(minutes), float(seconds)
        if minutes >= 60 or seconds >= 60:
            return None
        value = degrees + minutes / 60 + seconds / 3600
    if value > limit:
        return None
    return round(-value if hemisphere in 'SW' else value, 6)


def parse_line(text: str) -> dict:
    """Whatever can be read from one overlay line. Missing fields are absent.

    No letter-for-digit repairs here: the OCR whitelist has no O, I or l to
    confuse, which fixed the 8OMPH-style slips at the source.
    """
    reading = {}

    date, time = _date(text), _time(text)
    if date and time:
        try:
            reading['clock'] = datetime(*date, *time)
        except ValueError:
            pass
    elif time:
        reading['time_of_day'] = time

    lat = _coordinate(text, LAT_DMS, LAT_DEC, 90)
    lon = _coordinate(text, LON_DMS, LON_DEC, 180)
    if lat is not None and lon is not None:
        reading['lat'], reading['lon'] = lat, lon

    speed = SPEED.search(text)
    if speed:
        unit = speed.group(2).upper().replace('KPH', 'KM/H').replace('KMH', 'KM/H')
        reading['speed'] = int(speed.group(1))
        reading['speed_unit'] = 'mph' if unit == 'MPH' else 'km/h'
    return reading


AMBIGUOUS_S = re.compile(r'(?<=\d)S|S(?=\d|MPH|S)')


def readings_from(text: str) -> list[dict]:
    """Every plausible reading of one OCR line.

    Each S touching a digit may really be an 8 or a 9; the original is kept
    too, since S is also the southern hemisphere. Duplicates are dropped.
    """
    positions = [m.start() for m in AMBIGUOUS_S.finditer(text)][:MAX_AMBIGUOUS]
    variants = {text}
    for position in positions:
        variants |= {v[:position] + digit + v[position + 1:] for v in variants for digit in '89'}
    readings, seen = [], set()
    for variant in sorted(variants):
        reading = parse_line(variant)
        key = tuple(sorted((k, str(v)) for k, v in reading.items()))
        if reading and key not in seen:
            seen.add(key)
            readings.append(reading)
    return readings


def median_filter(values: list[float], window: int = MEDIAN_WINDOW) -> list[float]:
    """Running median. Leaves monotone runs -- braking, accelerating, driving
    in a straight line -- exactly as they were; removes short spikes."""
    # Symmetric even at the ends: a lopsided window at the last sample of an
    # accelerating run is all smaller values, and trimmed 103 to 102.
    half = window // 2
    last = len(values) - 1
    return [statistics.median(values[i - reach:i + reach + 1])
            for i in range(len(values)) for reach in (min(half, i, last - i),)]


# --- Consistency over time (pure) ------------------------------------------

def metres_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _to_mps(speed: float, unit: str) -> float:
    return speed * (0.44704 if unit == 'mph' else 1 / 3.6)


def build_track(samples: list[tuple[float, list[dict]]]) -> dict:
    """Turn per-second candidate readings into one consistent track.

    `samples` holds (t_seconds, readings) where readings are parse_line results
    from each thresholding of that frame. Returns the clock mapping and the
    per-sample values that survived the checks.
    """
    # Clock: the offset between the overlay's clock and the video's own time
    # is constant, so take the median over every reading and drop readings
    # that disagree with it.
    offsets = [(r['clock'] - datetime(2000, 1, 1)).total_seconds() - t
               for t, readings in samples for r in readings if 'clock' in r]
    clock = None
    if offsets:
        offset = statistics.median(offsets)
        agreeing = [o for o in offsets if abs(o - offset) <= CLOCK_TOLERANCE_SECONDS]
        # A clock read from whole seconds is uncertain by up to one second;
        # the median over many readings centres it.
        clock = {
            'start': (datetime(2000, 1, 1) + timedelta(seconds=offset)).isoformat(timespec='seconds'),
            'readings': len(offsets),
            'agreeing': len(agreeing),
            'precision_seconds': 1.0,
        }

    # Per-sample candidates, then neighbour votes.
    points = []
    for t, readings in samples:
        speeds = [(r['speed'], r['speed_unit']) for r in readings if 'speed' in r]
        positions = [(r['lat'], r['lon']) for r in readings if 'lat' in r]
        points.append({'t': t, 'speeds': speeds, 'positions': positions})

    def neighbours(index, key, chosen):
        """Neighbouring values: their current choices once there are any,
        every candidate on the first round. Using every candidate throughout
        let a neighbour's wrong 8-or-9 expansion drag the reference -- an
        accelerating 89, 91, 93 came out 89, 89, 89."""
        low, high = max(0, index - NEIGHBOURS), min(len(points), index + NEIGHBOURS + 1)
        others = [j for j in range(low, high) if j != index]
        if chosen:
            return [chosen[j] for j in others if j in chosen]
        return [value for j in others for value in points[j][key]]

    chosen_speed, chosen_position = {}, {}
    for _ in range(3):   # settles in two rounds on the collision clip; three for margin
        speed_round, position_round = {}, {}
        for index, point in enumerate(points):
            candidates = [s for s in point['speeds'] if s[0] <= MAX_SPEED]
            if candidates:
                others = neighbours(index, 'speeds', chosen_speed)
                reference = statistics.median(v for v, _ in others) if others else None
                speed_round[index] = min(
                    candidates, key=lambda c: abs(c[0] - reference) if reference is not None else 0)
            if point['positions']:
                others = neighbours(index, 'positions', chosen_position)
                if others:
                    ref = (statistics.median(p[0] for p in others), statistics.median(p[1] for p in others))
                    position_round[index] = min(point['positions'], key=lambda p: metres_between(*p, *ref))
                elif len(point['positions']) == 1:
                    position_round[index] = point['positions'][0]
        chosen_speed, chosen_position = speed_round, position_round

    corrected = {'speed': 0, 'position': 0}
    speed_keys = sorted(chosen_speed)
    for index, value in zip(speed_keys, median_filter([chosen_speed[i][0] for i in speed_keys])):
        if value != chosen_speed[index][0]:
            corrected['speed'] += 1
        chosen_speed[index] = (int(round(value)), chosen_speed[index][1])
    position_keys = sorted(chosen_position)
    lats = median_filter([chosen_position[i][0] for i in position_keys])
    lons = median_filter([chosen_position[i][1] for i in position_keys])
    for index, lat, lon in zip(position_keys, lats, lons):
        if (lat, lon) != chosen_position[index]:
            corrected['position'] += 1
        chosen_position[index] = (round(lat, 6), round(lon, 6))

    track = []
    for index, point in enumerate(points):
        entry = {'t_seconds': round(point['t'], 2)}
        if clock:
            entry['clock'] = clock_at(clock, point['t'])
        if index in chosen_speed:
            entry['speed'], entry['speed_unit'] = chosen_speed[index]
        if index in chosen_position:
            entry['lat'], entry['lon'] = chosen_position[index]
        if len(entry) > 1:
            track.append(entry)

    return {'clock': clock, 'track': track, 'corrected': corrected}


def clock_at(clock: dict, t_seconds: float) -> str:
    start = datetime.fromisoformat(clock['start'])
    return (start + timedelta(seconds=t_seconds)).isoformat(timespec='seconds')


def at_time(overlay: dict, t_seconds: float) -> dict | None:
    """The overlay's view of one moment: clock, and the nearest speed and position."""
    if not overlay or not overlay.get('available'):
        return None
    result = {}
    if overlay.get('clock'):
        result['clock'] = clock_at(overlay['clock'], t_seconds)
    for keys in (('speed', 'speed_unit'), ('lat', 'lon')):
        candidates = [p for p in overlay.get('track', []) if keys[0] in p]
        if candidates:
            nearest = min(candidates, key=lambda p: abs(p['t_seconds'] - t_seconds))
            if abs(nearest['t_seconds'] - t_seconds) <= 2 * SAMPLE_SECONDS:
                for key in keys:
                    result[key] = nearest[key]
                result[f'{keys[0]}_from_t_seconds'] = nearest['t_seconds']
    return result or None


# --- Reading frames ---------------------------------------------------------

def find_tesseract() -> str | None:
    configured = os.environ.get('TESSERACT_PATH')
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which('tesseract')
    if found:
        return found
    for candidate in (*WINDOWS_TESSERACT, *UNIX_TESSERACT):
        if os.path.isfile(candidate):
            return candidate
    return None


def _tight(band):
    """Crop a band to its text row, found as the rows holding overlay-coloured
    pixels. Thresholding the whole band let dash reflections pick the
    threshold, and some frames came back blank."""
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, YELLOW_LOW, YELLOW_HIGH) | cv2.inRange(hsv, (0, 0, 200), (180, 40, 255))
    rows = np.where(mask.sum(axis=1) > 255 * 20)[0]
    cols = np.where(mask.sum(axis=0) > 0)[0]
    if len(rows) == 0 or len(cols) == 0:
        return band
    top, bottom = max(0, rows[0] - 4), min(band.shape[0], rows[-1] + 5)
    left, right = max(0, cols[0] - 8), min(band.shape[1], cols[-1] + 9)
    return band[top:bottom, left:right]


def _thresholded(band):
    """Three black-on-white renderings of the band's text row.

    Otsu on the upscaled grey image was the most reliable single method on
    the collision clip (clock 49/49 frames, position 42, speed 45); Otsu
    before upscaling and a yellow mask each recover some frames it misses.
    """
    import cv2

    text = _tight(band)
    grey = cv2.cvtColor(text, cv2.COLOR_BGR2GRAY)

    def black_on_white(mask):
        mask = cv2.resize(mask, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)
        return 255 - cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)[1]

    big = cv2.resize(grey, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)
    return [
        255 - cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
        black_on_white(cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]),
        black_on_white(cv2.inRange(cv2.cvtColor(text, cv2.COLOR_BGR2HSV), YELLOW_LOW, YELLOW_HIGH)),
    ]


def _ocr(tesseract: str, image) -> str:
    import cv2

    ok, buffer = cv2.imencode('.png', image)
    if not ok:
        return ''
    completed = subprocess.run(
        [tesseract, 'stdin', 'stdout', '--psm', '7', '-l', 'eng',
         '-c', f'tessedit_char_whitelist={WHITELIST}'],
        input=buffer.tobytes(), capture_output=True, timeout=30, check=False,
    )
    return completed.stdout.decode('utf-8', errors='replace').strip()


def _band(frame, name: str):
    top, bottom = BANDS[name]
    height = frame.shape[0]
    return frame[int(height * top):max(int(height * top) + 1, int(height * bottom)), :]


def _useful(reading: dict) -> int:
    return sum(key in reading for key in ('clock', 'lat', 'speed'))


def read(path: str, metadata: dict, log=lambda message: None) -> dict:
    """OCR the overlay across the clip. Never raises for a missing tool."""
    import cv2

    tesseract = find_tesseract()
    if tesseract is None:
        return {'available': False, 'reason': 'Tesseract is not installed on this worker'}

    fps = metadata.get('fps') or 30.0
    duration = metadata.get('duration_seconds') or 0.0
    if duration <= 0:
        return {'available': False, 'reason': 'video has no usable duration'}

    count = min(MAX_SAMPLES, max(1, int(duration / SAMPLE_SECONDS)))
    times = [duration * (i + 0.5) / count for i in range(count)]

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return {'available': False, 'reason': 'video could not be reopened'}

    def frame_at(t):
        capture.set(cv2.CAP_PROP_POS_FRAMES, min(int(t * fps), max(0, (metadata.get('frame_count') or 1) - 1)))
        ok, frame = capture.read()
        return frame if ok else None

    try:
        # Which band holds the text: decided on a few frames, then only that
        # band is read, halving the OCR cost.
        scores = {name: 0 for name in BANDS}
        for t in times[:: max(1, len(times) // PROBE_SAMPLES)][:PROBE_SAMPLES]:
            frame = frame_at(t)
            if frame is None:
                continue
            for name in BANDS:
                scores[name] += max(_useful(parse_line(_ocr(tesseract, image)))
                                    for image in _thresholded(_band(frame, name))[:1])
        band = max(scores, key=scores.get)
        if scores[band] == 0:
            return {'available': False, 'reason': 'no readable overlay found', 'bands_checked': list(BANDS)}

        samples, lines = [], []
        for t in times:
            frame = frame_at(t)
            if frame is None:
                continue
            texts = [_ocr(tesseract, image) for image in _thresholded(_band(frame, band))]
            readings = [r for text in texts for r in readings_from(text)]
            if len(lines) < 3:
                lines.append(max(texts, key=len))
            samples.append((t, readings))
    finally:
        capture.release()

    built = build_track(samples)
    speeds = [p['speed'] for p in built['track'] if 'speed' in p]
    log(f"Overlay: {band} band, {len(samples)} frames read, {len(built['track'])} usable"
        + (f", speed {min(speeds)}-{max(speeds)}" if speeds else ''))
    return {
        'available': True,
        'band': band,
        'samples_read': len(samples),
        'samples_parsed': sum(1 for _, readings in samples if readings),
        'example_text': lines,
        **built,
    }
