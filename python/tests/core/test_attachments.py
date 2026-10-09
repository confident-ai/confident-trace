"""Media in custom content fields.

Images and PDFs travel as markers in the field with their bytes in one span
attribute; audio travels inline as `{mimeType, dataBase64 | url}`.
"""

import gc
import json
from base64 import b64decode, b64encode

import pytest
from conftest import spans
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace import _attributes as attrs
from confident_trace._core import spans as spans_module
from confident_trace._core.content import ContentPolicy, MediaBudget
from confident_trace._core.media import MARKER

WAV = b"RIFF$\x00\x00\x00WAVEfmt " + bytes(64)
WAV_BASE64 = b64encode(WAV).decode("ascii")
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000"
    "000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e"
    "44ae426082"
)


def reinit(telemetry, **options):
    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=telemetry[0], exporter=exporter, instrumentations=(), **options
    )
    return exporter


def exported(exporter):
    (span,) = spans(exporter)
    return dict(span.attributes)


def attachments(attributes):
    return json.loads(attributes[attrs.SPAN_ATTACHMENTS])


def marker_ids(value):
    return MARKER.findall(value)


def image():
    return ct.Media.from_bytes(PNG, "image/png")


# Images and PDFs: markers plus attachments.


def test_a_media_value_becomes_a_marker_with_its_bytes_attached(telemetry):
    _, exporter = telemetry
    picture = image()
    with ct.span("describe"):
        ct.update_span(output=picture)
    row = exported(exporter)
    assert json.loads(row[attrs.SPAN_OUTPUT]) == str(picture)
    (media_id,) = marker_ids(row[attrs.SPAN_OUTPUT])
    attachment = attachments(row)[media_id]
    assert attachment["mimeType"] == "image/png"
    assert b64decode(attachment["dataBase64"]) == PNG
    assert attachment["dataBase64"] not in row[attrs.SPAN_OUTPUT]


def test_media_formatted_into_a_string_is_attached(telemetry):
    _, exporter = telemetry
    picture = image()
    with ct.span("describe"):
        ct.update_trace(input=f"What is in this picture? {picture}")
    row = exported(exporter)
    value = json.loads(row["confident.trace.input"])
    assert value == f"What is in this picture? [CONFIDENT:IMAGE:{picture._id}]"
    assert b64decode(attachments(row)[picture._id]["dataBase64"]) == PNG


def test_a_temporary_formatted_media_is_still_found(telemetry):
    _, exporter = telemetry
    text = f"Look: {image()}"
    gc.collect()
    with ct.span("describe"):
        ct.update_span(input=text)
    row = exported(exporter)
    (media_id,) = marker_ids(row[attrs.SPAN_INPUT])
    assert b64decode(attachments(row)[media_id]["dataBase64"]) == PNG


def test_media_nested_in_structured_values_is_attached(telemetry):
    _, exporter = telemetry
    picture = image()
    pdf = ct.Media.from_bytes(PNG, "application/pdf")
    with ct.span("review"):
        ct.update_span(metadata={"pages": [{"shot": picture}], "doc": pdf})
    row = exported(exporter)
    metadata = json.loads(row["confident.span.metadata"])
    assert metadata["pages"][0]["shot"] == f"[CONFIDENT:IMAGE:{picture._id}]"
    assert metadata["doc"] == f"[CONFIDENT:PDF:{pdf._id}]"
    assert set(attachments(row)) == {picture._id, pdf._id}


def test_trace_and_span_fields_on_one_span_share_one_attribute(telemetry):
    _, exporter = telemetry
    question = image()
    answer = ct.Media.from_bytes(PNG, "application/pdf")
    with ct.span("review"):
        ct.update_trace(input=question)
        ct.update_span(output=answer)
    assert set(attachments(exported(exporter))) == {question._id, answer._id}


def test_rewriting_a_field_drops_only_what_it_named(telemetry):
    _, exporter = telemetry
    kept = image()
    with ct.span("review"):
        ct.update_span(input=kept, output=image())
        ct.update_span(output="no media now")
    assert set(attachments(exported(exporter))) == {kept._id}


def test_clearing_the_last_attachment_leaves_an_empty_map(telemetry):
    _, exporter = telemetry
    with ct.span("review"):
        ct.update_span(output=image())
        ct.update_span(output="done")
    assert attachments(exported(exporter)) == {}


def test_media_named_twice_on_a_span_spends_its_budget_once(telemetry):
    exporter = reinit(telemetry, max_media_bytes=len(PNG))
    picture = image()
    with ct.span("review"):
        ct.update_span(input=picture, output=f"replying to {picture}")
    row = exported(exporter)
    assert marker_ids(row[attrs.SPAN_INPUT]) == [picture._id]
    assert marker_ids(row[attrs.SPAN_OUTPUT]) == [picture._id]
    assert list(attachments(row)) == [picture._id]


def test_media_over_the_item_limit_becomes_a_note(telemetry):
    exporter = reinit(telemetry, max_media_bytes=len(PNG) - 1)
    with ct.span("review"):
        ct.update_span(output=image())
    row = exported(exporter)
    assert json.loads(row[attrs.SPAN_OUTPUT]) == (
        "<inline_data: image/png, not captured>"
    )
    assert attrs.SPAN_ATTACHMENTS not in row


def test_a_remote_reference_attaches_its_url(telemetry):
    _, exporter = telemetry
    picture = ct.Media.from_uri("https://example.com/photo.png")
    with ct.span("review"):
        ct.update_span(output=picture)
    row = exported(exporter)
    assert json.loads(row[attrs.SPAN_OUTPUT]) == f"[CONFIDENT:IMAGE:{picture._id}]"
    assert attachments(row)[picture._id] == {
        "url": "https://example.com/photo.png",
        "mimeType": "image/png",
    }


def test_a_file_is_read_when_the_field_is_written(telemetry, tmp_path):
    _, exporter = telemetry
    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    picture = ct.Media.from_file(path)
    assert picture.mime_type == "image/png"
    with ct.span("review"):
        ct.update_span(output=picture)
    attachment = attachments(exported(exporter))[picture._id]
    assert b64decode(attachment["dataBase64"]) == PNG


def test_a_missing_file_becomes_a_note(telemetry, tmp_path):
    _, exporter = telemetry
    with ct.span("review"):
        ct.update_span(output=ct.Media.from_file(tmp_path / "gone.png"))
    row = exported(exporter)
    assert "not captured" in json.loads(row[attrs.SPAN_OUTPUT])
    assert attrs.SPAN_ATTACHMENTS not in row


@pytest.mark.parametrize("mime_type", ["video/mp4", "text/csv", None])
def test_types_the_receiver_cannot_store_are_written_as_notes(telemetry, mime_type):
    _, exporter = telemetry
    media = ct.Media.from_bytes(WAV, mime_type)
    assert not MARKER.search(str(media))
    with ct.span("review"):
        ct.update_span(output=media)
    row = exported(exporter)
    assert json.loads(row[attrs.SPAN_OUTPUT]) == media.note()
    assert attrs.SPAN_ATTACHMENTS not in row


def test_marker_text_naming_unknown_media_is_left_alone(telemetry):
    _, exporter = telemetry
    text = "[CONFIDENT:IMAGE:" + "0" * 32 + "]"
    with ct.span("review"):
        ct.update_span(output=text)
    row = exported(exporter)
    assert json.loads(row[attrs.SPAN_OUTPUT]) == text
    assert attrs.SPAN_ATTACHMENTS not in row


def test_disabled_capture_sends_neither_marker_nor_bytes(telemetry):
    exporter = reinit(telemetry, capture_content=False)
    with ct.span("review"):
        ct.update_span(output=image())
    row = exported(exporter)
    assert attrs.SPAN_OUTPUT not in row
    assert attrs.SPAN_ATTACHMENTS not in row


def test_a_truncated_marker_sends_no_bytes(telemetry):
    exporter = reinit(telemetry, max_content_bytes=64)
    with ct.span("review"):
        ct.update_span(output="x" * 64 + str(image()))
    assert attrs.SPAN_ATTACHMENTS not in exported(exporter)


def test_a_captured_copy_of_a_model_request_sends_no_bytes(telemetry):
    _, exporter = telemetry
    request = [{"role": "user", "parts": [image()]}]
    with ct.span("call") as span:
        spans_module.content(span, attrs.TRACE_INPUT, request)
    row = exported(exporter)
    part = json.loads(row[attrs.TRACE_INPUT])[0]["parts"][0]
    assert part["content_omitted"] is True
    assert attrs.SPAN_ATTACHMENTS not in row


def test_a_captured_write_drops_what_the_field_attached(telemetry):
    _, exporter = telemetry
    with ct.span("call") as span:
        ct.update_trace(input=image())
        spans_module.content(span, attrs.TRACE_INPUT, "captured later")
    assert attachments(exported(exporter)) == {}


def test_message_shaped_media_is_unchanged(telemetry):
    assert image().to_part()["type"] == "blob"
    assert ct.Media.from_bytes(WAV, "audio/wav").to_part()["content_omitted"]


# Audio: inline `{mimeType, dataBase64 | url}` values.


def test_audio_in_a_message_travels_inline(telemetry):
    _, exporter = telemetry
    with ct.span("turn"):
        ct.update_trace(
            input={
                "role": "user",
                "content": "What's my balance?",
                "audio": ct.Media.from_bytes(WAV, "audio/wav"),
            }
        )
    row = exported(exporter)
    assert json.loads(row[attrs.TRACE_INPUT]) == {
        "role": "user",
        "content": "What's my balance?",
        "audio": {"mimeType": "audio/wav", "dataBase64": WAV_BASE64},
    }
    assert attrs.SPAN_ATTACHMENTS not in row


def test_audio_under_any_key_and_beside_other_media(telemetry):
    _, exporter = telemetry
    picture = image()
    with ct.span("turn"):
        ct.update_span(
            output=[
                {
                    "role": "assistant",
                    "content": f"Here it is: {picture}",
                    "voice": ct.Media.from_bytes(WAV, "audio/ogg"),
                }
            ]
        )
    row = exported(exporter)
    (message,) = json.loads(row[attrs.SPAN_OUTPUT])
    assert message["voice"] == {"mimeType": "audio/ogg", "dataBase64": WAV_BASE64}
    assert list(attachments(row)) == [picture._id]


def test_remote_audio_travels_as_its_url(telemetry):
    _, exporter = telemetry
    with ct.span("turn"):
        ct.update_span(output={"audio": ct.Media.from_uri("https://x.io/a.mp3")})
    assert json.loads(exported(exporter)[attrs.SPAN_OUTPUT]) == {
        "audio": {"mimeType": "audio/mpeg", "url": "https://x.io/a.mp3"}
    }


def test_an_audio_file_is_read_when_the_field_is_written(telemetry, tmp_path):
    _, exporter = telemetry
    path = tmp_path / "turn.wav"
    path.write_bytes(WAV)
    with ct.span("turn"):
        ct.update_span(input={"audio": ct.Media.from_file(path)})
    audio = json.loads(exported(exporter)[attrs.SPAN_INPUT])["audio"]
    assert audio == {"mimeType": "audio/wav", "dataBase64": WAV_BASE64}


def test_audio_is_exempt_from_the_text_limit(telemetry):
    exporter = reinit(telemetry, max_content_bytes=64)
    long_audio = ct.Media.from_bytes(WAV * 50, "audio/wav")
    with ct.span("turn"):
        ct.update_span(input={"role": "user", "content": "hi", "audio": long_audio})
    audio = json.loads(exported(exporter)[attrs.SPAN_INPUT])["audio"]
    assert b64decode(audio["dataBase64"]) == WAV * 50


def test_audio_over_the_item_limit_becomes_a_note(telemetry):
    exporter = reinit(telemetry, max_media_bytes=len(WAV) - 1)
    with ct.span("turn"):
        ct.update_span(input={"audio": ct.Media.from_bytes(WAV, "audio/wav")})
    assert json.loads(exported(exporter)[attrs.SPAN_INPUT]) == {
        "audio": "<inline_data: audio/wav, not captured>"
    }


def test_audio_shares_the_span_media_total():
    policy = ContentPolicy(max_media_total_bytes=len(WAV))
    budget = MediaBudget(policy.max_media_bytes, policy.max_media_total_bytes)

    def write():
        value = {"audio": ct.Media.from_bytes(WAV, "audio/wav")}
        return json.loads(policy.encode(value, budget=budget, markers=True))["audio"]

    assert write()["dataBase64"] == WAV_BASE64
    assert write() == "<inline_data: audio/wav, not captured>"


def test_audio_formatted_into_a_string_is_a_note(telemetry):
    _, exporter = telemetry
    audio = ct.Media.from_bytes(WAV, "audio/wav")
    assert str(audio) == "<inline_data: audio/wav, not captured>"
    with ct.span("turn"):
        ct.update_span(input=f"Listen: {audio}")
    row = exported(exporter)
    assert json.loads(row[attrs.SPAN_INPUT]) == f"Listen: {audio.note()}"
    assert attrs.SPAN_ATTACHMENTS not in row


def test_disabled_capture_sends_no_audio(telemetry):
    exporter = reinit(telemetry, capture_content=False)
    with ct.span("turn"):
        ct.update_span(input={"audio": ct.Media.from_bytes(WAV, "audio/wav")})
    assert attrs.SPAN_INPUT not in exported(exporter)


def test_captured_audio_keeps_its_omitted_part(telemetry):
    _, exporter = telemetry
    request = [{"role": "user", "parts": [ct.Media.from_bytes(WAV, "audio/wav")]}]
    with ct.span("call") as span:
        spans_module.content(span, attrs.TRACE_INPUT, request)
    part = json.loads(exported(exporter)[attrs.TRACE_INPUT])[0]["parts"][0]
    assert part == {"type": "blob", "mime_type": "audio/wav", "content_omitted": True}
