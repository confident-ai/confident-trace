import asyncio
import gzip
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from opentelemetry import trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.sdk.trace import TracerProvider

import confident_trace as ct

captured = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        if self.path == "/v1/traces":
            # A collector that ignores authentication can hide export failures.
            if self.headers.get("x-confident-api-key") != "offline":
                self.send_response(401)
                self.end_headers()
                return
            r = ExportTraceServiceRequest.FromString(body)
            for rs in r.resource_spans:
                for ss in rs.scope_spans:
                    for s in ss.spans:
                        captured.append(
                            (
                                s.name,
                                s.trace_id.hex(),
                                s.parent_span_id.hex(),
                                s.span_id.hex(),
                            )
                        )
            self.send_response(200)
            self.end_headers()
            return
        if "/messages" in self.path:
            data = json.loads(body)
            msg = {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": data.get("model", "claude-sonnet-4-6"),
                "content": [{"type": "text", "text": "Hello."}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 3, "output_tokens": 2},
            }
            if data.get("stream"):
                events = [
                    (
                        "message_start",
                        {
                            "type": "message_start",
                            "message": {**msg, "content": [], "stop_reason": None},
                        },
                    ),
                    (
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        },
                    ),
                    (
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "Hello."},
                        },
                    ),
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    (
                        "message_delta",
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                            "usage": {"output_tokens": 2},
                        },
                    ),
                    ("message_stop", {"type": "message_stop"}),
                ]
                out = "".join(
                    f"event: {n}\ndata: {json.dumps(v)}\n\n" for n, v in events
                ).encode()
                typ = "text/event-stream"
            else:
                out = json.dumps(msg).encode()
                typ = "application/json"
        else:
            out = b"{}"
            typ = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.mark.asyncio
@pytest.mark.parametrize("native_enabled", [True, False])
async def test_mega_drains_native_export_and_preserves_parentage(
    monkeypatch, tmp_path, native_enabled
):
    # Use the real bundled CLI, fake HTTP model responses, and an actual local
    # OTLP receiver: an env-capture fixture cannot prove native span parentage.
    path = Path(__file__).resolve().parents[3] / "examples/mega/mega.py"
    spec = importlib.util.spec_from_file_location("mega_native_test", path)
    mega = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mega)
    captured.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    provider = TracerProvider()
    trace.set_tracer_provider(provider)
    ct.init(
        tracer_provider=provider,
        endpoint=url + "/v1/traces",
        api_key="offline",
        instrumentations=("claude_agent_sdk",),
    )

    def options(**kwargs):
        return ClaudeAgentOptions(
            **kwargs,
            env={
                **({} if native_enabled else {"CLAUDE_CODE_ENABLE_TELEMETRY": "0"}),
                "ANTHROPIC_API_KEY": "offline-test",
                "ANTHROPIC_BASE_URL": url,
                "CLAUDE_CONFIG_DIR": str(tmp_path),
                "OTEL_METRICS_EXPORTER": "none",
                "OTEL_LOGS_EXPORTER": "none",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
        )

    monkeypatch.setattr("claude_agent_sdk.ClaudeAgentOptions", options)

    async def request(index):
        with ct.span(f"request.{index}") as span:
            result = await mega.claude_job(None)
            assert result == "Hello."
            return f"{span.context.trace_id:032x}"

    try:
        ids = await asyncio.wait_for(asyncio.gather(request(0), request(1)), 60)
        ct.shutdown()
        assert len(set(ids)) == 2
        assert {s[1] for s in captured} == set(ids)
        for tid in ids:
            rows = [s for s in captured if s[1] == tid]
            by_name = {s[0]: s for s in rows}
            if not native_enabled:
                assert len(rows) == 2, rows
                assert set(by_name) == {
                    f"request.{ids.index(tid)}",
                    "claude.invocation",
                }
                assert (
                    by_name["claude.invocation"][2]
                    == by_name[f"request.{ids.index(tid)}"][3]
                )
                continue
            assert len(rows) == 4, rows
            invocation = by_name["claude.invocation"]
            interaction = by_name["claude_code.interaction"]
            model = by_name["claude_code.llm_request"]
            assert invocation[2] == next(
                s[3] for s in rows if s[0].startswith("request.")
            )
            assert interaction[2] == invocation[3]
            assert model[2] == interaction[3]
    finally:
        ct.shutdown()
        provider.shutdown()
        server.shutdown()
        server.server_close()
        thread.join()
