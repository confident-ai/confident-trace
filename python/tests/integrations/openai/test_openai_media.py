"""Media blocks across the Chat Completions and Responses spellings."""

import json
from base64 import b64decode, b64encode

import pytest

from confident_trace._core.content import ContentPolicy
from confident_trace.integrations._shared.openai_extraction import messages

PNG = bytes.fromhex("89504e470d0a1a0a")
PNG_BASE64 = b64encode(PNG).decode("ascii")
PNG_DATA_URI = f"data:image/png;base64,{PNG_BASE64}"
PDF_DATA_URI = f"data:application/pdf;base64,{PNG_BASE64}"
URL = "https://example.com/photo.png"


def parts(*blocks):
    encoded = ContentPolicy().encode(
        messages([{"role": "user", "content": list(blocks)}]), shape="input-messages"
    )
    return json.loads(encoded)[0]["parts"]


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image_url", "image_url": {"url": PNG_DATA_URI}},
        {"type": "input_image", "image_url": PNG_DATA_URI},
        {"type": "file", "file": {"file_data": PNG_DATA_URI, "filename": "a.png"}},
        {"type": "input_file", "file_data": PNG_DATA_URI, "filename": "a.png"},
    ],
)
def test_inline_blocks_reach_the_span_as_decodable_bytes(block):
    part = parts(block)[0]
    assert part["type"] == "blob"
    assert part["mime_type"] == "image/png"
    assert b64decode(part["content"]) == PNG


def test_a_pdf_file_block_keeps_its_own_type():
    part = parts({"type": "file", "file": {"file_data": PDF_DATA_URI}})[0]
    assert part["mime_type"] == "application/pdf"
    assert b64decode(part["content"]) == PNG


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image_url", "image_url": {"url": URL}},
        {"type": "input_image", "image_url": URL},
        {"type": "input_file", "file_url": URL},
    ],
)
def test_remote_blocks_travel_as_references(block):
    assert parts(block)[0] == {"type": "uri", "mime_type": "image/png", "uri": URL}


def test_audio_records_its_type_without_carrying_bytes():
    block = {
        "type": "input_audio",
        "input_audio": {"data": PNG_BASE64, "format": "wav"},
    }
    assert parts(block)[0] == {
        "type": "blob",
        "mime_type": "audio/wav",
        "content_omitted": True,
    }


def test_a_file_referenced_only_by_id_still_reports():
    assert parts({"type": "input_file", "file_id": "file-abc"})[0] == {
        "type": "blob",
        "content_omitted": True,
    }


def test_text_and_media_keep_their_order():
    result = parts(
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": PNG_DATA_URI}},
    )
    assert [part["type"] for part in result] == ["text", "blob"]
    assert result[0]["content"] == "what is this?"


def test_sdk_objects_are_read_without_calling_properties():
    class Block:
        def __init__(self, **fields):
            self.__dict__.update(fields)

        @property
        def boom(self):
            raise AssertionError("properties must not be invoked")

    block = Block(type="image_url", image_url=Block(url=PNG_DATA_URI))
    assert b64decode(parts(block)[0]["content"]) == PNG


def test_the_trace_input_copy_carries_no_bytes():
    from confident_trace import _attributes as confident

    value = messages(
        [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": PNG_DATA_URI}}],
            }
        ]
    )
    policy = ContentPolicy()
    # request() encodes the same list twice: once shaped, once as trace input.
    assert (
        "content"
        in json.loads(policy.encode(value, shape="input-messages"))[0]["parts"][0]
    )
    plain = json.loads(policy.encode(value))
    assert plain[0]["parts"][0]["content_omitted"] is True
    assert confident.TRACE_INPUT == "confident.trace.input"
