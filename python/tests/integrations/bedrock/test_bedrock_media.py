"""Converse wraps a source inside a typed block and names formats, not mimes."""

import json
from base64 import b64decode

from confident_trace._core.content import ContentPolicy
from confident_trace.integrations.bedrock.extraction import messages

PNG = bytes.fromhex("89504e470d0a1a0a")


def parts(*blocks):
    encoded = ContentPolicy().encode(
        messages([{"role": "user", "content": list(blocks)}]), shape="input-messages"
    )
    return json.loads(encoded)[0]["parts"]


def test_an_image_block_resolves_its_format_and_nested_bytes():
    block = {"image": {"format": "png", "source": {"bytes": PNG}}}
    part = parts(block)[0]
    assert part["type"] == "blob"
    assert part["mime_type"] == "image/png"
    assert b64decode(part["content"]) == PNG


def test_a_document_block_resolves_to_a_pdf():
    block = {"document": {"format": "pdf", "name": "report", "source": {"bytes": PNG}}}
    part = parts(block)[0]
    assert part["mime_type"] == "application/pdf"
    assert b64decode(part["content"]) == PNG


def test_a_video_block_reports_without_carrying_bytes():
    block = {"video": {"format": "mp4", "source": {"bytes": PNG}}}
    assert parts(block)[0] == {"type": "blob", "content_omitted": True}


def test_an_s3_source_travels_as_a_reference():
    block = {
        "image": {
            "format": "jpeg",
            "source": {"s3Location": {"uri": "s3://bucket/a.jpg"}},
        }
    }
    assert parts(block)[0] == {
        "type": "uri",
        "mime_type": "image/jpeg",
        "uri": "s3://bucket/a.jpg",
    }


def test_text_and_media_keep_their_order():
    result = parts(
        {"text": "what is this?"},
        {"image": {"format": "png", "source": {"bytes": PNG}}},
    )
    assert [part["type"] for part in result] == ["text", "blob"]
    assert result[0]["content"] == "what is this?"


def test_tool_blocks_are_untouched():
    block = {"toolUse": {"toolUseId": "t1", "name": "lookup", "input": {"q": "x"}}}
    assert parts(block)[0]["type"] == "tool_call"
