"""Media normalization: what reaches a part, and what never reaches one."""

import json
from base64 import b64decode, b64encode

import jsonschema
import pytest
from conftest import REGISTRY

import confident_trace as ct
from confident_trace._core import content
from confident_trace._core.content import ContentPolicy
from confident_trace._core.media import Media

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000"
    "000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e"
    "44ae426082"
)
PNG_BASE64 = b64encode(PNG).decode("ascii")


@pytest.fixture
def png_file(tmp_path):
    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    return path


def test_inline_bytes_become_a_blob_part():
    media = Media.from_bytes(PNG, "image/png")
    assert media.to_part() == {
        "type": "blob",
        "mime_type": "image/png",
        "content": PNG_BASE64,
    }
    assert media.byte_size() == len(PNG)


def test_base64_payload_is_passed_through_without_a_round_trip():
    media = Media.from_base64(PNG_BASE64, "image/png")
    assert media.to_part()["content"] is PNG_BASE64
    assert media.byte_size() == len(PNG)


def test_data_uri_splits_into_payload_and_mime_type():
    media = Media.from_data_uri(f"data:image/png;base64,{PNG_BASE64}")
    assert media.mime_type == "image/png"
    assert media.to_part()["content"] == PNG_BASE64


@pytest.mark.parametrize(
    "value",
    [
        "data:image/png,not-base64-encoded",
        "data:image/png;base64",
        "data:image/png;base64,",
        "https://example.com/a.png",
        "",
    ],
)
def test_only_base64_data_uris_parse_as_one(value):
    assert Media.from_data_uri(value) is None


def test_remote_uri_travels_as_a_reference():
    media = Media.from_uri("https://example.com/photo.jpeg?v=2")
    assert media.is_remote
    assert media.to_part() == {
        "type": "uri",
        "mime_type": "image/jpeg",
        "uri": "https://example.com/photo.jpeg?v=2",
    }


def test_declared_mime_type_wins_over_the_extension():
    media = Media.from_uri("https://example.com/photo.png", "image/webp")
    assert media.mime_type == "image/webp"


def test_mime_type_is_normalized():
    assert Media.from_bytes(PNG, " IMAGE/PNG; charset=binary ").mime_type == "image/png"


def test_unknown_extension_leaves_the_mime_type_unset():
    part = Media.from_uri("https://example.com/report").to_part()
    assert part == {"type": "uri", "uri": "https://example.com/report"}


def test_local_file_is_read_only_when_a_part_is_built(png_file, monkeypatch):
    reads = []
    monkeypatch.setattr(
        "confident_trace._core.media._read",
        lambda path: reads.append(path) or PNG,
    )
    media = Media.from_uri(str(png_file))
    assert reads == []
    assert media.byte_size() == len(PNG)  # stat, not a read
    assert reads == []
    assert media.to_part()["content"] == PNG_BASE64
    assert reads == [str(png_file)]


def test_file_uri_resolves_to_its_path(png_file):
    media = Media.from_uri(png_file.as_uri())
    assert media.local_path() == str(png_file)
    assert media.to_part()["content"] == PNG_BASE64


def test_missing_file_records_the_media_without_its_content(tmp_path):
    media = Media.from_uri(str(tmp_path / "gone.png"))
    assert media.to_part() == {
        "type": "blob",
        "mime_type": "image/png",
        "content_omitted": True,
    }
    assert media.byte_size() is None


def test_oversized_media_is_never_read(png_file, monkeypatch):
    monkeypatch.setattr(
        "confident_trace._core.media._read",
        lambda path: pytest.fail("payload read despite being over budget"),
    )
    media = Media.from_uri(str(png_file))
    assert media.to_part(max_bytes=len(PNG) - 1) == {
        "type": "blob",
        "mime_type": "image/png",
        "content_omitted": True,
    }


def test_budget_admits_media_that_fits(png_file):
    part = Media.from_uri(str(png_file)).to_part(max_bytes=len(PNG))
    assert part["content"] == PNG_BASE64


def test_remote_media_ignores_the_budget():
    media = Media.from_uri("https://example.com/huge.png")
    assert media.to_part(max_bytes=1)["uri"] == "https://example.com/huge.png"


@pytest.mark.parametrize(
    ("value", "mime_type", "expected"),
    [
        (PNG, "image/png", {"type": "blob", "content": PNG_BASE64}),
        (bytearray(PNG), "image/png", {"type": "blob", "content": PNG_BASE64}),
        (memoryview(PNG), "image/png", {"type": "blob", "content": PNG_BASE64}),
        (
            f"data:image/png;base64,{PNG_BASE64}",
            None,
            {"type": "blob", "content": PNG_BASE64},
        ),
        (PNG_BASE64, "image/png", {"type": "blob", "content": PNG_BASE64}),
        (
            "https://example.com/a.png",
            None,
            {"type": "uri", "uri": "https://example.com/a.png"},
        ),
    ],
)
def test_parse_reads_the_shapes_providers_send(value, mime_type, expected):
    part = Media.parse(value, mime_type).to_part()
    assert {key: part[key] for key in expected} == expected


def test_parse_treats_an_undeclared_string_as_a_path():
    media = Media.parse(PNG_BASE64)
    assert media.uri == PNG_BASE64
    assert media.to_part()["content_omitted"] is True


@pytest.mark.parametrize("value", [None, "", b"", 7, {"url": "a.png"}, []])
def test_parse_declines_what_it_cannot_place(value):
    assert Media.parse(value) is None


def test_repr_never_carries_the_payload():
    text = repr(Media.from_base64(PNG_BASE64, "image/png"))
    assert PNG_BASE64 not in text
    assert "image/png" in text


def user_message(*parts):
    return [{"role": "user", "parts": list(parts)}]


def encoded_parts(policy, value, shape="input-messages"):
    return json.loads(policy.encode(value, shape=shape))[0]["parts"]


def test_media_survives_a_text_limit_it_dwarfs():
    big = Media.from_bytes(PNG * 4000, "image/png")
    parts = encoded_parts(
        ContentPolicy(), user_message({"type": "text", "content": "hi"}, big)
    )
    assert parts[0] == {"type": "text", "content": "hi"}
    assert b64decode(parts[1]["content"]) == PNG * 4000


def test_text_is_still_bounded_while_media_passes():
    media = Media.from_bytes(PNG, "image/png")
    value = user_message({"type": "text", "content": "x" * 5000}, media)
    parts = encoded_parts(ContentPolicy(max_bytes=256), value)
    assert len(parts[0]["content"]) < 5000
    assert b64decode(parts[1]["content"]) == PNG


def test_media_outlives_a_text_limit_smaller_than_the_part_itself():
    media = Media.from_bytes(PNG, "image/png")
    value = user_message({"type": "text", "content": "x" * 5000}, media)
    parts = encoded_parts(ContentPolicy(max_bytes=64), value)
    assert len(parts[0]["content"]) <= 64
    assert b64decode(parts[1]["content"]) == PNG


def test_media_over_its_own_budget_is_omitted_not_truncated():
    media = Media.from_bytes(PNG * 100, "image/png")
    value = user_message(media)
    parts = encoded_parts(ContentPolicy(max_media_bytes=len(PNG)), value)
    assert parts[0] == {
        "type": "blob",
        "mime_type": "image/png",
        "content_omitted": True,
    }


def test_a_zero_budget_turns_inline_media_off():
    value = user_message(Media.from_bytes(PNG, "image/png"))
    assert encoded_parts(ContentPolicy(max_media_bytes=0), value)[0] == {
        "type": "blob",
        "mime_type": "image/png",
        "content_omitted": True,
    }


def test_remote_media_needs_no_budget():
    value = user_message(Media.from_uri("https://example.com/a.png"))
    parts = encoded_parts(ContentPolicy(max_media_bytes=0), value)
    assert parts[0]["uri"] == "https://example.com/a.png"


def test_bounded_media_messages_stay_schema_valid():
    value = user_message(
        {"type": "text", "content": "y" * 2000}, Media.from_bytes(PNG, "image/png")
    )
    for limit in (64, 128, 1024):
        encoded = ContentPolicy(max_bytes=limit).encode(value, shape="input-messages")
        jsonschema.validate(
            json.loads(encoded), REGISTRY["message_schemas"]["input-messages"]
        )


def test_media_outside_a_message_shape_carries_no_bytes():
    encoded = ContentPolicy().encode(
        {"page": Media.from_bytes(PNG * 4000, "image/png")}
    )
    assert json.loads(encoded)["page"] == {
        "type": "blob",
        "mime_type": "image/png",
        "content_omitted": True,
    }


@pytest.mark.parametrize(
    "mime_type", ["audio/wav", "video/mp4", "text/csv", "application/octet-stream"]
)
def test_bytes_the_consumer_cannot_keep_are_never_carried(mime_type):
    media = Media.from_bytes(PNG, mime_type)
    assert media.to_part() == {
        "type": "blob",
        "mime_type": mime_type,
        "content_omitted": True,
    }


@pytest.mark.parametrize(
    "mime_type", ["image/png", "image/svg+xml", "application/pdf", "application/x-pdf"]
)
def test_storable_types_still_carry_their_bytes(mime_type):
    assert Media.from_bytes(PNG, mime_type).to_part()["content"] == PNG_BASE64


def test_a_reference_travels_whatever_its_type():
    media = Media.from_uri("https://example.com/talk.mp3", "audio/mpeg")
    assert media.to_part() == {
        "type": "uri",
        "mime_type": "audio/mpeg",
        "uri": "https://example.com/talk.mp3",
    }


def test_unstorable_media_spends_no_budget():
    value = user_message(Media.from_bytes(PNG * 4000, "audio/wav"))
    assert (
        content.media_length(
            json.loads(ContentPolicy().encode(value, shape="input-messages"))
        )
        == content.MEDIA_OVERHEAD
    )


def test_a_span_media_total_stops_later_payloads():
    first = Media.from_bytes(PNG, "image/png")
    second = Media.from_bytes(PNG, "image/png")
    policy = ContentPolicy(max_media_total_bytes=len(PNG))
    parts = encoded_parts(policy, user_message(first, second))
    assert b64decode(parts[0]["content"]) == PNG
    assert parts[1]["content_omitted"] is True


def test_the_span_total_is_spent_by_payloads_not_references():
    remote = Media.from_uri("https://example.com/a.png")
    inline = Media.from_bytes(PNG, "image/png")
    policy = ContentPolicy(max_media_total_bytes=len(PNG))
    parts = encoded_parts(policy, user_message(remote, inline))
    assert parts[0]["type"] == "uri"
    assert b64decode(parts[1]["content"]) == PNG


def test_each_attribute_gets_its_own_span_total():
    policy = ContentPolicy(max_media_total_bytes=len(PNG))
    for _ in range(3):
        parts = encoded_parts(policy, user_message(Media.from_bytes(PNG, "image/png")))
        assert b64decode(parts[0]["content"]) == PNG


def test_the_per_item_limit_still_applies_under_a_large_total():
    policy = ContentPolicy(max_media_bytes=len(PNG) - 1, max_media_total_bytes=10**6)
    parts = encoded_parts(policy, user_message(Media.from_bytes(PNG, "image/png")))
    assert parts[0]["content_omitted"] is True


def test_one_budget_is_shared_across_a_span_attributes(telemetry):
    _, exporter = telemetry
    policy = ContentPolicy(max_media_total_bytes=len(PNG))
    with ct.span("request") as span:
        first = content.span_budget(span, policy, "input-messages")
        second = content.span_budget(span, policy, "output-messages")
        assert first is second
        unshaped = content.span_budget(span, policy, None)
        assert unshaped is not first and unshaped.remaining == 0


def test_separate_spans_do_not_share_a_budget(telemetry):
    _, exporter = telemetry
    policy = ContentPolicy(max_media_total_bytes=len(PNG))
    with ct.span("a") as first_span, ct.span("b") as second_span:
        assert content.span_budget(
            first_span, policy, "input-messages"
        ) is not content.span_budget(second_span, policy, "input-messages")


def test_a_span_media_total_spans_its_attributes(telemetry):
    _, exporter = telemetry
    policy = ContentPolicy(max_media_total_bytes=len(PNG))
    with ct.span("request") as span:
        budget = content.span_budget(span, policy, "input-messages")
        first = policy.encode(
            user_message(Media.from_bytes(PNG, "image/png")),
            shape="input-messages",
            budget=budget,
        )
        second = policy.encode(
            [
                {
                    "role": "assistant",
                    "finish_reason": "stop",
                    "parts": [Media.from_bytes(PNG, "image/png")],
                }
            ],
            shape="output-messages",
            budget=content.span_budget(span, policy, "output-messages"),
        )
    assert "content" in json.loads(first)[0]["parts"][0]
    assert json.loads(second)[0]["parts"][0]["content_omitted"] is True
