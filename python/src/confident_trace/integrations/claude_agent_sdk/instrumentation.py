"""Supply per-transport telemetry options, preserving application-owned options."""

import os
from dataclasses import replace

from opentelemetry.sdk import environment_variables as otel_env

from ..._core import runtime as _runtime
from ..._core.safety import safe
from .._shared.patching import install_targets
from . import _constants as native


def configured_options(runtime, options):
    explicit = options.env
    effective = {**os.environ, **explicit}
    if effective.get(otel_env.OTEL_SDK_DISABLED, "").lower() == "true":
        return options
    if (
        any(
            key in effective
            and effective[key].lower() in {"", "0", "false", "off", "no"}
            for key in (native.ENABLE_TELEMETRY, native.ENABLE_TRACES)
        )
        or effective.get(native.TRACES_EXPORTER, "").lower() == "none"
    ):
        return options
    # Detailed Claude tracing has different routing rules. Never carry our
    # authentication into an application-selected detailed tracing destination.
    if native.DETAILED_ENDPOINT in effective or native.DETAILED_TRACES in effective:
        return options
    env = dict(explicit)
    connection_override = any(key.startswith(native.OTLP_PREFIX) for key in explicit)
    if runtime.otlp_environment is not None and not connection_override:
        env.update(runtime.otlp_environment)
    elif not any(
        effective.get(key)
        for key in (
            otel_env.OTEL_EXPORTER_OTLP_ENDPOINT,
            otel_env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT,
        )
    ):
        return options
    # Preserve the application's signal choices. We enable traces only, leaving
    # the CLI's logs/metrics pipelines unconfigured unless the app requested them.
    for key, value in (
        (native.ENABLE_TELEMETRY, "1"),
        (native.ENABLE_TRACES, "1"),
        (native.TRACES_EXPORTER, "otlp"),
    ):
        if key not in effective:
            env[key] = value
    return replace(options, env=env)


def instrument(runtime):
    def configure(wrapped, instance, args, kwargs):
        if not runtime.active or _runtime.disabled():
            return wrapped(*args, **kwargs)
        options = (
            kwargs.get("options")
            if "options" in kwargs
            else (args[1] if len(args) > 1 else None)
        )
        configured = (
            safe(configured_options, runtime, options) if options is not None else None
        )
        if configured is not None and configured is not options:
            if "options" in kwargs:
                kwargs = {**kwargs, "options": configured}
            else:
                args = (args[0], configured, *args[2:])
        return wrapped(*args, **kwargs)

    return install_targets(
        [
            (
                "claude_agent_sdk._internal.transport.subprocess_cli",
                "SubprocessCLITransport",
                "__init__",
            )
        ],
        lambda original, method: configure,
    )
