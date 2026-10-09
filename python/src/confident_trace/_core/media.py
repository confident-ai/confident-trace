"""Media normalized out of provider payloads into GenAI content parts or attachments."""

from __future__ import annotations

import re
from base64 import b64encode
from collections import deque
from os import fspath, stat
from os.path import basename, splitext
from urllib.parse import unquote, urlparse
from uuid import uuid4
from weakref import WeakValueDictionary

from .safety import safe

_DATA_URI = "data:"
_FILE_URI = "file:"
_REMOTE_SCHEMES = ("http://", "https://")

_MIME_BY_EXTENSION = {
    ".aac": "audio/aac",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".flac": "audio/flac",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".wav": "audio/wav",
    ".webp": "image/webp",
}

_PDF_MIME_TYPES = ("application/pdf", "application/x-pdf")
AUDIO_MIME_PREFIX = "audio/"

MARKER = re.compile(r"\[CONFIDENT:(?:IMAGE|PDF):([0-9a-f]{32})\]")
_NOTE_MIME = re.compile(r"^[a-z0-9.+-]+/[a-z0-9.+-]+$")

_REGISTRY: WeakValueDictionary = WeakValueDictionary()
_RECENT: deque = deque(maxlen=16)

_MAX_PATH_LENGTH = 1024
_MIN_BASE64_LENGTH = 32
_BASE64_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\r\n"
)


def _read(path):
    with open(path, "rb") as handle:
        return handle.read()


def _file_size(path):
    return stat(path).st_size


def _mime(value):
    if type(value) is not str:
        return None
    return value.split(";", 1)[0].strip().lower() or None


def _mime_from_path(path):
    return _MIME_BY_EXTENSION.get(splitext(urlparse(path).path or path)[1].lower())


def _decoded_length(encoded):
    body = encoded.strip()
    padding = len(body) - len(body.rstrip("="))
    return max(len(body) // 4 * 3 - padding, 0)


def supported_media(mime_type):
    return type(mime_type) is str and (
        mime_type.startswith("image/") or mime_type in _PDF_MIME_TYPES
    )


def attachable_media(mime_type):
    return supported_media(mime_type) or (
        type(mime_type) is str and mime_type.startswith(AUDIO_MIME_PREFIX)
    )


def _marker_type(mime_type):
    if type(mime_type) is not str:
        return None
    if mime_type.startswith("image/"):
        return "IMAGE"
    if mime_type in _PDF_MIME_TYPES:
        return "PDF"
    return None


def registered(media_id):
    return _REGISTRY.get(media_id)


def _is_base64(value):
    return (
        len(value) >= _MIN_BASE64_LENGTH
        and len(value) % 4 == 0
        and set(value) <= _BASE64_CHARS
    )


class Media:
    """One non-text payload, read only when a part or attachment is built.

    Formatting an image or PDF as text (`str(media)`, an f-string) yields a
    marker that a traced field can carry anywhere in its value. Audio is not
    formatted into text: as a value it becomes `{mimeType, dataBase64 | url}`.
    """

    __slots__ = (
        "mime_type",
        "uri",
        "_data",
        "_encoded",
        "_size",
        "_unreadable",
        "_id",
        "__weakref__",
    )

    def __init__(self, *, uri=None, data=None, encoded=None, mime_type=None):
        self.uri = uri if type(uri) is str and uri else None
        if type(data) is bytes:
            self._data = data
        elif isinstance(data, (bytearray, memoryview)):
            self._data = bytes(data)
        else:
            self._data = None
        self._encoded = encoded if type(encoded) is str and encoded else None
        self.mime_type = _mime(mime_type) or (
            _mime_from_path(self.uri) if self.uri else None
        )
        self._size = None
        self._unreadable = False
        self._id = uuid4().hex

    @classmethod
    def from_bytes(cls, data, mime_type=None):
        if not isinstance(data, (bytes, bytearray, memoryview)) or not data:
            return None
        return cls(data=data, mime_type=mime_type)

    @classmethod
    def from_base64(cls, encoded, mime_type=None):
        if type(encoded) is not str or not encoded:
            return None
        return cls(encoded=encoded, mime_type=mime_type)

    @classmethod
    def from_uri(cls, uri, mime_type=None):
        if type(uri) is not str or not uri:
            return None
        return cls(uri=uri, mime_type=mime_type)

    @classmethod
    def from_file(cls, path, mime_type=None):
        """A local file, read only when its bytes are first needed."""
        try:
            path = fspath(path)
        except TypeError:
            return None
        return cls.from_uri(path, mime_type) if type(path) is str else None

    @classmethod
    def from_data_uri(cls, value):
        if type(value) is not str or not value.startswith(_DATA_URI):
            return None
        header, separator, payload = value[len(_DATA_URI) :].partition(",")
        if not separator or not payload:
            return None
        parameters = header.split(";")
        if "base64" not in (parameter.strip().lower() for parameter in parameters):
            return None
        return cls(encoded=payload, mime_type=parameters[0])

    @classmethod
    def parse(cls, value, mime_type=None):
        """Read an opaque value; prefer a constructor when the field is named."""
        if isinstance(value, (bytes, bytearray, memoryview)):
            return cls.from_bytes(value, mime_type)
        if type(value) is not str or not value:
            return None
        if value.startswith(_DATA_URI):
            return cls.from_data_uri(value)
        if value.startswith(_REMOTE_SCHEMES) or value.startswith(_FILE_URI):
            return cls.from_uri(value, mime_type)
        # A declared mime type is what separates raw base64 from a short path.
        if mime_type is not None and _is_base64(value):
            return cls.from_base64(value, mime_type)
        if len(value) <= _MAX_PATH_LENGTH:
            return cls.from_uri(value, mime_type)
        return None

    @property
    def is_inline(self):
        return self._data is not None or self._encoded is not None

    @property
    def is_audio(self):
        return type(self.mime_type) is str and self.mime_type.startswith(
            AUDIO_MIME_PREFIX
        )

    @property
    def is_remote(self):
        if self.is_inline or self.uri is None:
            return False
        scheme = urlparse(self.uri).scheme
        return len(scheme) > 1 and scheme != "file"

    @property
    def filename(self):
        if self.uri is None:
            return None
        return basename(urlparse(self.uri).path) or None

    def local_path(self):
        if self.is_inline or self.uri is None or self.is_remote:
            return None
        parsed = urlparse(self.uri)
        # A single-character scheme is a Windows drive letter, not a scheme.
        if len(parsed.scheme) > 1 and parsed.scheme != "file":
            return None
        if parsed.scheme != "file":
            return self.uri
        raw = f"//{parsed.netloc}{parsed.path}" if parsed.netloc else parsed.path
        return unquote(raw) or None

    def byte_size(self):
        """Decoded size, without reading a file or decoding base64."""
        if self._size is not None:
            return self._size
        if self._data is not None:
            self._size = len(self._data)
        elif self._encoded is not None:
            self._size = _decoded_length(self._encoded)
        else:
            path = self.local_path()
            if path is None or self._unreadable:
                return None
            size = safe(_file_size, path)
            if size is None:
                self._unreadable = True
                return None
            self._size = size
        return self._size

    def base64(self, max_bytes=None):
        if max_bytes is not None:
            size = self.byte_size()
            if size is None or size > max_bytes:
                return None
        if self._encoded is not None:
            return self._encoded
        if self._data is None:
            path = self.local_path()
            if path is None or self._unreadable:
                return None
            data = safe(_read, path)
            if data is None:
                self._unreadable = True
                return None
            self._data = data
            self._size = len(data)
        self._encoded = b64encode(self._data).decode("ascii")
        return self._encoded

    def to_part(self, max_bytes=None):
        part = {"type": "uri" if self.is_remote else "blob"}
        if self.mime_type is not None:
            part["mime_type"] = self.mime_type
        if self.is_remote:
            # A reference costs nothing to carry whatever its type.
            part["uri"] = self.uri
            return part
        encoded = self.base64(max_bytes) if supported_media(self.mime_type) else None
        if encoded is None:
            part["content_omitted"] = True
        else:
            part["content"] = encoded
        return part

    def to_attachment(self, max_bytes=None):
        """The receiver's attachment shape, or None when the bytes can't travel."""
        if self.is_remote:
            attachment = {"url": self.uri}
            if self.mime_type is not None:
                attachment["mimeType"] = self.mime_type
            return attachment
        if not attachable_media(self.mime_type):
            return None
        encoded = self.base64(max_bytes)
        if encoded is None:
            return None
        return {"dataBase64": encoded, "mimeType": self.mime_type}

    def note(self):
        """Text that replaces this media where its content isn't captured."""
        mime = self.mime_type
        valid = mime is not None and _NOTE_MIME.match(mime)
        return f"<inline_data: {mime if valid else 'unknown'}, not captured>"

    def __str__(self):
        kind = _marker_type(self.mime_type)
        if kind is None:
            return self.note()
        _REGISTRY[self._id] = self
        _RECENT.append(self)
        return f"[CONFIDENT:{kind}:{self._id}]"

    def __repr__(self):
        # Reprs reach logs; the payload must never ride along.
        source = self.uri if self.uri is not None else "inline"
        size = "?" if self._size is None else self._size
        return f"Media(mime_type={self.mime_type!r}, source={source!r}, bytes={size})"
