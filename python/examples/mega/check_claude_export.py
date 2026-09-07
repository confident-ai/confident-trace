"""One Claude call with native OTLP delivery receipts. See README.md."""

import asyncio
import gzip
import json
import os
import tempfile
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.util.re import parse_env_headers

import confident_trace as ct
from confident_trace.integrations.claude_agent_sdk.instrumentation import (
    configured_options,
)


class ExportCheck:
    """Forward each incoming batch once; inspect identity, never content."""

    def __init__(self, destination, header_names):
        self.destination = destination
        self.header_names = header_names
        self.receipts = []
        self.lock = threading.Lock()
        check = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                if self.path != "/v1/traces" or not 0 < size <= 16 * 1024 * 1024:
                    self.send_error(400)
                    return
                body = self.rfile.read(size)
                headers = {
                    key: self.headers[key]
                    for key in (*check.header_names, "Content-Type", "Content-Encoding")
                    if key in self.headers
                }
                receipt = {"spans": [], "http_status": None, "rejected_spans": None}
                try:
                    decoded = (
                        gzip.decompress(body)
                        if self.headers.get("Content-Encoding") == "gzip"
                        else body
                    )
                    request = ExportTraceServiceRequest.FromString(decoded)
                    receipt["spans"] = [
                        {
                            "name": span.name,
                            "trace_id": span.trace_id.hex(),
                            "span_id": span.span_id.hex(),
                            "parent_id": span.parent_span_id.hex(),
                        }
                        for resource in request.resource_spans
                        for scope in resource.scope_spans
                        for span in scope.spans
                    ]
                    # No application-level retry or redirects: never export a
                    # second copy or forward authentication to another host.
                    with httpx.Client(timeout=20, follow_redirects=False) as client:
                        response = client.post(
                            check.destination, content=body, headers=headers
                        )
                    receipt["http_status"] = response.status_code
                    if response.status_code == 200:
                        result = ExportTraceServiceResponse.FromString(response.content)
                        receipt["rejected_spans"] = (
                            result.partial_success.rejected_spans
                        )
                    status, payload = response.status_code, response.content
                    content_type = response.headers.get(
                        "content-type", "application/x-protobuf"
                    )
                except Exception as error:
                    receipt["error_type"] = type(error).__name__
                    status, payload, content_type = 502, b"", "application/x-protobuf"
                with check.lock:
                    check.receipts.append(receipt)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1/traces"

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def telemetry_stages(path):
    """Emit fixed labels only; raw CLI diagnostics may contain credentials."""
    markers = {
        "model_request_dispatched": "[API:timing] dispatching to ",
        "model_response_started": "Stream started - received first chunk",
        "telemetry_enabled": "[3P telemetry] isTelemetryEnabled=true ",
        "telemetry_disabled": "[3P telemetry] isTelemetryEnabled=false ",
        "init_failed": "[3P telemetry] Telemetry init failed:",
        "otel_diagnostic_error": "[3P telemetry] OTEL diag error:",
        "waiting_for_remote_settings": "[3P telemetry] Waiting for remote managed settings fetch before telemetry init",
        "remote_settings_settled": "[3P telemetry] Remote managed settings fetch settled, initializing telemetry",
        "remote_settings_init_failed": "[3P telemetry] Telemetry init failed (remote settings path)",
        "traces_export_succeeded": "[3P telemetry] First traces export: SUCCESS",
        "traces_export_failed": "[3P telemetry] First traces export: FAILED",
    }
    found = []
    try:
        with path.open(errors="replace") as log:
            for line in log:
                for label, marker in markers.items():
                    if marker in line and label not in found:
                        found.append(label)
    except OSError:
        return {"debug_log_available": False, "stages": []}
    return {"debug_log_available": True, "stages": found}


async def run():
    runtime = ct.init(instrumentations=("claude_agent_sdk",))
    try:
        options = configured_options(runtime, ClaudeAgentOptions())
        effective = {**os.environ, **options.env}
        if (
            not runtime.active
            or options.env.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL") != "http/protobuf"
        ):
            raise RuntimeError(
                "This check requires active tracing and HTTP/protobuf export"
            )
        headers = parse_env_headers(
            effective.get("OTEL_EXPORTER_OTLP_TRACES_HEADERS", ""), liberal=True
        )
        destination = effective["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
        auth = {
            "confident_api_key_configured": bool(headers.get("x-confident-api-key")),
            "anthropic_api_key_set": bool(effective.get("ANTHROPIC_API_KEY")),
        }
        print(json.dumps(auth))
        if (
            urlsplit(destination).hostname == "confident-otel-new-us.up.railway.app"
            and not auth["confident_api_key_configured"]
        ):
            print("FAIL: Set CONFIDENT_API_KEY in this shell before running the check.")
            return 1
        with (
            ExportCheck(destination, tuple(headers)) as check,
            tempfile.TemporaryDirectory(prefix="ct-check-") as directory,
        ):
            env = {**options.env, "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": check.endpoint}
            debug_path = Path(directory) / "telemetry-debug.log"
            options = replace(
                options,
                env=env,
                cwd=directory,
                setting_sources=[],
                extra_args={**options.extra_args, "debug-file": str(debug_path)},
                tools=[],
                max_turns=1,
                stderr=lambda _: None,
            )
            result = None
            query_error = None
            try:
                with ct.span("claude.export-check") as root:
                    async for message in query(
                        prompt="Reply with OK. Do not use tools.", options=options
                    ):
                        if isinstance(message, ResultMessage):
                            result = message
            except Exception as error:
                # ResultError exposes structured status fields. Never dump its
                # raw result/errors/message: those can contain credentials.
                query_error = {"type": type(error).__name__}
                for field in ("api_error_status", "exit_code"):
                    value = getattr(error, field, None)
                    if type(value) is int:
                        query_error[field] = value
                reason = getattr(error, "terminal_reason", None)
                if reason in ("api_error", "max_turns", "max_budget_usd"):
                    query_error["terminal_reason"] = reason
            ct.flush()
            report = {
                "trace_id": f"{root.context.trace_id:032x}",
                "parent_id": f"{root.context.span_id:016x}",
                "query_succeeded": query_error is None
                and result is not None
                and not result.is_error,
                "query_error": query_error,
                "receipts": check.receipts,
                "telemetry": telemetry_stages(debug_path),
            }
        print(json.dumps(report, indent=2))
        if not check.receipts:
            print("FAIL: No native OTLP batch reached the local receiver.")
            return 1
        accepted = all(
            r["http_status"] == 200 and r["rejected_spans"] == 0 for r in check.receipts
        )
        native = [s for r in check.receipts for s in r["spans"]]
        interactions = [s for s in native if s["name"] == "claude_code.interaction"]
        linked = (
            bool(interactions)
            and all(s["trace_id"] == report["trace_id"] for s in native)
            and all(s["parent_id"] == report["parent_id"] for s in interactions)
        )
        success = report["query_succeeded"] and accepted and linked
        print(
            "PASS: Native batches accepted and linked; check backend storage by trace ID."
            if success
            else "FAIL: Inspect receipt status and parent IDs above."
        )
        print(
            "This check uses Python for the final HTTP hop; it does not validate the CLI's direct TLS/proxy path."
        )
        return 0 if success else 1
    finally:
        ct.shutdown()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(asyncio.wait_for(run(), 120)))
    except Exception as error:
        print(f"FAIL: {type(error).__name__}")
        raise SystemExit(1) from None
