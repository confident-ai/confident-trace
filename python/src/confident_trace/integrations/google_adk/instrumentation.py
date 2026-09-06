"""ADK owns its spans; only suppress our overlapping provider instrumentation."""

from importlib.metadata import PackageNotFoundError, distribution

from .._shared.lifecycle import register_native_inference


def install(runtime):
    try:
        distribution("google-adk")
    except PackageNotFoundError:
        return []
    # Recognition depends on the active span's scope and operation, not the
    # installed SDK version. Unrecognized native spans still export unchanged.
    return register_native_inference("gcp.vertex.agent")
