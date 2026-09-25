"""Image and document blocks across the source types Anthropic accepts."""

import json
from base64 import b64decode, b64encode

import pytest

from confident_trace._core.content import ContentPolicy
from confident_trace.integrations.anthropic.extraction import messages

PNG = bytes.fromhex("89504e470d0a1a0a")
PNG_BASE64 = b64encode(PNG).decode("ascii")
URL = "https://example.com/photo.png"


def parts(*blocks):
    encoded = ContentPolicy().encode(
        messages([{"role": "user", "content": list(blocks)}]), shape="input-messages"
    )
    return json.loads(encoded)[0]["parts"]


@pytest.mark.parametrize("kind", ["image", "document"])
def test_base64_sources_reach_the_span_as_decodable_bytes(kind):
    mime = "image/png" if kind == "image" else "application/pdf"
    part = parts(
        {
            "type": kind,
            "source": {"type": "base64", "media_type": mime, "data": PNG_BASE64},
        }
    )[0]
    assert part["type"] == "blob"
    assert part["mime_type"] == mime
    assert b64decode(part["content"]) == PNG


def test_url_sources_travel_as_references():
    block = {"type": "image", "source": {"type": "url", "url": URL}}
    assert parts(block)[0] == {"type": "uri", "mime_type": "image/png", "uri": URL}


def test_a_file_id_source_still_reports_the_media():
    block = {"type": "document", "source": {"type": "file", "file_id": "file_abc"}}
    assert parts(block)[0] == {"type": "blob", "content_omitted": True}


def test_a_text_document_is_captured_as_text():
    block = {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": "the contract"},
    }
    assert parts(block)[0] == {"type": "text", "content": "the contract"}


def test_text_and_media_keep_their_order():
    result = parts(
        {"type": "text", "text": "what is this?"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": PNG_BASE64},
        },
    )
    assert [part["type"] for part in result] == ["text", "blob"]
    assert result[0]["content"] == "what is this?"


def test_tool_use_blocks_are_untouched():
    block = {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": "x"}}
    assert parts(block)[0]["type"] == "tool_call"


def test_sdk_objects_are_read_without_calling_properties():
    class Block:
        def __init__(self, **fields):
            self.__dict__.update(fields)

        @property
        def boom(self):
            raise AssertionError("properties must not be invoked")

    block = Block(
        type="image",
        source=Block(type="base64", media_type="image/png", data=PNG_BASE64),
    )
    assert b64decode(parts(block)[0]["content"]) == PNG
