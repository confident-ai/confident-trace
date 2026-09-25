"""Multimodal content blocks, across the spellings LangChain has shipped."""

import json
from base64 import b64decode, b64encode

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import HumanMessage  # noqa: E402

from confident_trace._core.content import ContentPolicy  # noqa: E402
from confident_trace.integrations.langchain.extraction import messages  # noqa: E402

PNG = bytes.fromhex("89504e470d0a1a0a")
PNG_BASE64 = b64encode(PNG).decode("ascii")
URL = "https://example.com/photo.png"


def parts(*blocks):
    encoded = ContentPolicy().encode(
        messages([HumanMessage(content=list(blocks))]), shape="input-messages"
    )
    return json.loads(encoded)[0]["parts"]


@pytest.mark.parametrize(
    "block",
    [
        {
            "type": "image",
            "source_type": "base64",
            "data": PNG_BASE64,
            "mime_type": "image/png",
        },
        {"type": "image", "base64": PNG_BASE64, "mime_type": "image/png"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": PNG_BASE64},
        },
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{PNG_BASE64}"},
        },
        {"type": "image_url", "image_url": f"data:image/png;base64,{PNG_BASE64}"},
        {"type": "file", "base64": PNG_BASE64, "mime_type": "application/pdf"},
    ],
)
def test_inline_blocks_reach_the_span_as_decodable_bytes(block):
    part = parts(block)[0]
    assert part["type"] == "blob"
    assert b64decode(part["content"]) == PNG


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image", "source_type": "url", "url": URL},
        {"type": "image", "url": URL},
        {"type": "image_url", "image_url": {"url": URL}},
        {"type": "image", "source": {"type": "url", "url": URL}},
    ],
)
def test_remote_blocks_travel_as_references(block):
    assert parts(block)[0] == {
        "type": "uri",
        "mime_type": "image/png",
        "uri": URL,
    }


def test_text_and_media_keep_their_order():
    result = parts(
        {"type": "text", "text": "what is this?"},
        {"type": "image", "base64": PNG_BASE64, "mime_type": "image/png"},
    )
    assert [part["type"] for part in result] == ["text", "blob"]
    assert result[0]["content"] == "what is this?"


def test_a_source_we_cannot_resolve_still_reports_the_media():
    assert parts({"type": "image", "mime_type": "image/png"})[0] == {
        "type": "blob",
        "mime_type": "image/png",
        "content_omitted": True,
    }


def test_unknown_blocks_are_skipped():
    assert parts({"type": "thinking", "thinking": "hmm"}) == []


def test_media_is_omitted_when_content_capture_is_off():
    value = messages(
        [
            HumanMessage(
                content=[
                    {"type": "image", "base64": PNG_BASE64, "mime_type": "image/png"}
                ]
            )
        ]
    )
    assert ContentPolicy(enabled=False).encode(value, shape="input-messages") is None
