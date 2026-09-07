"""Process-global lifecycle checks with no inherited tracing configuration."""

import os
import sys
import threading
from uuid import uuid4

import wrapt
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace._core import runtime
from confident_trace.integrations import registry

mode = sys.argv[1]
original = BaseChatModel.stream
provider = TracerProvider(shutdown_on_exit=False)
exporter = InMemorySpanExporter()
witness = InMemorySpanExporter()
provider.add_span_processor(SimpleSpanProcessor(witness))
if mode == "disabled":
    os.environ["OTEL_SDK_DISABLED"] = "true"
ct.init(
    tracer_provider=provider,
    exporter=exporter,
    instrumentations=("langchain", "langgraph"),
)
model = FakeListChatModel(responses=["hello"])
assert model.invoke("hi").content == "hello"
ct.flush()
if mode == "disabled":
    assert not exporter.get_finished_spans()
else:
    first = vars(BaseChatModel)["stream"]
    bridge = runtime.current()._langchain_bridge
    assert registry.instrument(runtime.current(), ("langgraph", "langchain")) == []
    assert vars(BaseChatModel)["stream"] is first
    assert len(exporter.get_finished_spans()) == 1
    assert exporter.get_finished_spans() == witness.get_finished_spans()
    if mode == "fork":
        run_id = uuid4()
        bridge.on_chain_start(None, {}, run_id=run_id)
        acquired, release = threading.Event(), threading.Event()

        def hold_lock():
            with bridge.lock:
                acquired.set()
                release.wait(10)

        thread = threading.Thread(target=hold_lock)
        thread.start()
        assert acquired.wait(5)
        pid = os.fork()
        if pid == 0:
            try:
                assert not bridge.runs
                assert model.invoke("child").content == "hello"
                ct.flush()
                ct.shutdown()
            except BaseException:
                os._exit(1)
            os._exit(0)
        release.set()
        thread.join(5)
        assert os.waitpid(pid, 0)[1] == 0
        bridge.on_chain_end({}, run_id=run_id)
        assert not bridge.runs
    elif mode == "later-wrapper":
        later = wrapt.FunctionWrapper(
            first, lambda wrapped, instance, args, kwargs: wrapped(*args, **kwargs)
        )
        BaseChatModel.stream = later
        ct.shutdown()
        assert vars(BaseChatModel)["stream"] is later
        assert "".join(x.content for x in model.stream("hi")) == "hello"
        assert not bridge.runs
        BaseChatModel.stream = original
    elif mode == "reinit":
        ct.shutdown()
        assert BaseChatModel.stream is original
        assert model.invoke("untraced").content == "hello"
        assert len(witness.get_finished_spans()) == 1
        exporter = InMemorySpanExporter()
        ct.init(
            tracer_provider=provider, exporter=exporter, instrumentations=("langgraph",)
        )
        assert model.invoke("new").content == "hello"
        ct.flush()
        assert len(exporter.get_finished_spans()) == 1
        assert runtime.current()._langchain_bridge is not bridge
        assert bridge.closed
ct.shutdown()
assert BaseChatModel.stream is original
