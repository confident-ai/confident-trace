"""Google parts carry raw bytes inline and a uri for stored files."""

import json
from base64 import b64decode, b64encode

from confident_trace._core.content import ContentPolicy
from confident_trace.integrations.google_genai.extraction import messages

PNG = bytes.fromhex("89504e470d0a1a0a")
PNG_BASE64 = b64encode(PNG).decode("ascii")


class Part:
    def __init__(self, **fields):
        self.__dict__.update(fields)

    @property
    def boom(self):
        raise AssertionError("properties must not be invoked")


def parts(*blocks):
    encoded = ContentPolicy().encode(
        messages([{"role": "user", "parts": list(blocks)}]), shape="input-messages"
    )
    return json.loads(encoded)[0]["parts"]


def test_inline_data_carries_raw_bytes():
    block = Part(inline_data=Part(mime_type="image/png", data=PNG))
    part = parts(block)[0]
    assert part["type"] == "blob"
    assert part["mime_type"] == "image/png"
    assert b64decode(part["content"]) == PNG


def test_inline_data_accepts_an_already_encoded_payload():
    block = {"inline_data": {"mime_type": "image/png", "data": PNG_BASE64}}
    assert b64decode(parts(block)[0]["content"]) == PNG


def test_a_pdf_travels_inline_like_an_image():
    block = {"inline_data": {"mime_type": "application/pdf", "data": PNG}}
    part = parts(block)[0]
    assert part["mime_type"] == "application/pdf"
    assert b64decode(part["content"]) == PNG


def test_file_data_travels_as_a_reference():
    block = Part(file_data=Part(mime_type="image/png", file_uri="gs://bucket/a.png"))
    assert parts(block)[0] == {
        "type": "uri",
        "mime_type": "image/png",
        "uri": "gs://bucket/a.png",
    }


def test_audio_records_its_type_without_carrying_bytes():
    block = {"inline_data": {"mime_type": "audio/wav", "data": PNG}}
    assert parts(block)[0] == {
        "type": "blob",
        "mime_type": "audio/wav",
        "content_omitted": True,
    }


def test_text_and_media_keep_their_order():
    result = parts(
        {"text": "what is this?"},
        {"inline_data": {"mime_type": "image/png", "data": PNG}},
    )
    assert [part["type"] for part in result] == ["text", "blob"]
    assert result[0]["content"] == "what is this?"
