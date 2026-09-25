"""Safe value access and bounded primitive decoding."""

import json
from enum import Enum
from itertools import islice

from ..._core.media import Media

_MIME_KEYS = ("mime_type", "mimeType", "media_type")
_DATA_KEYS = ("data", "base64", "bytes")
_REFERENCE_KEYS = ("url", "image_url", "file_data", "file_url", "uri", "file_uri")
_NESTED_KEYS = (
    "source",
    "inline_data",
    "file_data",
    "image",
    "document",
    "video",
    "s3Location",
)
# Bedrock is the deepest: a typed wrapper, a source, then an S3 location.
_MAX_NESTING = 3
_MIME_BY_FORMAT = {
    "gif": "image/gif",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "mp3": "audio/mpeg",
    "pdf": "application/pdf",
    "png": "image/png",
    "wav": "audio/wav",
    "webp": "image/webp",
}


def get(value, key, default=None):
    if type(value) is dict:
        return value.get(key, default)
    # SDK models store data in __dict__; avoid invoking arbitrary properties.
    try:
        data = object.__getattribute__(value, "__dict__")
        if type(data) is dict and key in data:
            return data[key]
        extras = object.__getattribute__(value, "__pydantic_extra__")
        return extras.get(key, default) if type(extras) is dict else default
    except (AttributeError, TypeError):
        return default


def sequence(value, limit=128):
    return islice(value if type(value) in (list, tuple) else (), limit)


def string(value):
    # Accept enum-backed strings without coercing arbitrary objects.
    if isinstance(value, Enum):
        value = value.value
    return value if type(value) is str else None


def arguments(value):
    if type(value) is str and len(value) <= 16384:
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            pass  # Partial streaming JSON stays a string.
    return value


def field(source, keys):
    for key in keys:
        value = string(get(source, key))
        if value is not None:
            return value
    return None


def nested(source):
    for key in (string(get(source, "type")), *_NESTED_KEYS):
        value = get(source, key) if key else None
        if value is not None:
            return value
    return None


def media(block):
    # Convert one provider media block into Media object.
    source = block
    mime = None
    for _ in range(_MAX_NESTING):
        mime = (
            mime
            or field(source, _MIME_KEYS)
            or _MIME_BY_FORMAT.get(field(source, ("format",)))
        )
        inner = nested(source)
        if type(inner) is str:
            return Media.parse(inner, mime) or Media(mime_type=mime)
        if inner is None:
            break
        source = inner
    for key in _DATA_KEYS:
        raw = get(source, key)
        if isinstance(raw, (bytes, bytearray, memoryview)):
            return Media.from_bytes(raw, mime) or Media(mime_type=mime)
        encoded = string(raw)
        if encoded is not None:
            return Media.from_base64(encoded, mime) or Media(mime_type=mime)
    reference = field(source, _REFERENCE_KEYS)
    if reference is not None:
        return Media.parse(reference, mime) or Media(mime_type=mime)
    return Media(mime_type=mime)
