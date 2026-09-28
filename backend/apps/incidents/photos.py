"""Supplementary photos: what the owner took of the other vehicle.

In the 2026-09-26 case the dashcam never resolved the plate -- 720p at road
speed -- and the photo taken three minutes later did. The photo also carried
what the investigation relied on: when (12:19:32 -05:00), where (to 2 m),
which way the phone faced (47 deg), how fast it was moving (36 km/h), and the
zoom that made the plate legible (177 mm equivalent).

The original is stored untouched and fingerprinted, like the video. A JPEG
preview is stored beside it because browsers cannot show HEIC.
"""

from __future__ import annotations

import io
from datetime import datetime

MAX_PHOTO_BYTES = 40 * 1024 * 1024
PREVIEW_MAX_SIDE = 2048
PREVIEW_QUALITY = 85

# ISO BMFF brands that mean HEIF/HEIC: the ftyp box names one of these.
HEIF_BRANDS = {b'heic', b'heix', b'hevc', b'hevx', b'heim', b'heis', b'mif1', b'msf1'}

EXIF_IFD, GPS_IFD = 0x8769, 0x8825
MAKE, MODEL, SOFTWARE = 271, 272, 305
DATETIME_ORIGINAL, OFFSET_TIME_ORIGINAL = 36867, 36881
FOCAL_LENGTH_35MM, LENS_MODEL = 41989, 42036


def sniff(data: bytes) -> tuple[str, str] | None:
    """(content_type, extension) by content, or None if not a photo we take."""
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', 'jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', 'png'
    if len(data) >= 12 and data[4:8] == b'ftyp' and data[8:12] in HEIF_BRANDS:
        return 'image/heic', 'heic'
    return None


def _open(data: bytes):
    from PIL import Image
    import pillow_heif

    pillow_heif.register_heif_opener()
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _degrees(dms, ref) -> float | None:
    try:
        degrees, minutes, seconds = (float(part) for part in dms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    value = degrees + minutes / 60 + seconds / 3600
    return round(-value if str(ref).upper() in ('S', 'W') else value, 7)


def _text(value) -> str | None:
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    value = str(value).strip('\x00 ').strip() if value is not None else ''
    return value or None


def read_exif(exif) -> dict:
    """The parts of a photo's EXIF that place it in time and space.

    Takes a Pillow Exif object. Pure apart from that, so it is tested with
    EXIF built in memory rather than with anyone's real photo.
    """
    result = {}
    base = exif.get_ifd(EXIF_IFD)
    gps = exif.get_ifd(GPS_IFD)

    taken = _text(base.get(DATETIME_ORIGINAL))
    offset = _text(base.get(OFFSET_TIME_ORIGINAL))
    if taken:
        try:
            moment = datetime.strptime(taken, '%Y:%m:%d %H:%M:%S')
            result['taken_at'] = moment.isoformat() + (offset or '')
            result['taken_at_has_zone'] = bool(offset)
        except ValueError:
            pass

    device = {key: _text(exif.get(tag)) for key, tag in (('make', MAKE), ('model', MODEL), ('software', SOFTWARE))}
    device['lens'] = _text(base.get(LENS_MODEL))
    device = {key: value for key, value in device.items() if value}
    if device:
        result['device'] = device
    focal = _number(base.get(FOCAL_LENGTH_35MM))
    if focal:
        result['focal_length_35mm'] = int(focal)

    if gps:
        place = {}
        lat = _degrees(gps.get(2), gps.get(1)) if gps.get(2) else None
        lon = _degrees(gps.get(4), gps.get(3)) if gps.get(4) else None
        if lat is not None and lon is not None:
            place['lat'], place['lon'] = lat, lon
        altitude = _number(gps.get(6))
        if altitude is not None:
            place['altitude_m'] = round(-altitude if gps.get(5) in (1, b'\x01') else altitude, 1)
        accuracy = _number(gps.get(31))
        if accuracy is not None:
            place['accuracy_m'] = round(accuracy, 1)
        heading = _number(gps.get(17))
        if heading is not None:
            place['heading_deg'] = round(heading, 1)
            place['heading_ref'] = 'true' if _text(gps.get(16)) == 'T' else 'magnetic'
        speed = _number(gps.get(13))
        if speed is not None:
            unit = _text(gps.get(12)) or 'K'
            place['speed_kmh'] = round(speed * {'K': 1.0, 'M': 1.609344, 'N': 1.852}.get(unit, 1.0), 1)
        if place:
            result['gps'] = place
    return result


def process(data: bytes) -> dict:
    """Metadata and a JPEG preview for one photo.

    Returns {'metadata', 'preview', 'width', 'height'}; preview is JPEG bytes,
    rotated upright, at most PREVIEW_MAX_SIDE on its longer side.
    """
    from PIL import ImageOps

    image = _open(data)
    metadata = read_exif(image.getexif())
    upright = ImageOps.exif_transpose(image).convert('RGB')
    metadata['width'], metadata['height'] = upright.size
    upright.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
    buffer = io.BytesIO()
    # No EXIF in the preview: it is for looking at, and the metadata that
    # matters is already extracted and stored beside it.
    upright.save(buffer, 'JPEG', quality=PREVIEW_QUALITY)
    return {'metadata': metadata, 'preview': buffer.getvalue(),
            'width': upright.size[0], 'height': upright.size[1]}
