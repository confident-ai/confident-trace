"""Run directly with Python or collect with pytest; no provider calls required."""
import asyncio
import json
import unittest
from uuid import uuid4

import confident_trace as ct
from confident_trace._core import runtime
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class StreamingInputTests(unittest.TestCase):
    def setUp(self):
        self.sink = InMemorySpanExporter()
        ct.init(tracer_provider=TracerProvider(shutdown_on_exit=False),
                exporter=self.sink, instrumentations=("langchain",))

    def tearDown(self):
        ct.shutdown()

    def test_sync_and_async_sequence(self):
        chain = ChatPromptTemplate.from_template("Explain {topic}.") | FakeListChatModel(responses=["Answer"]) | StrOutputParser()
        self.assertEqual("".join(chain.stream({"topic": "volcanoes"})), "Answer")

        async def run():
            return "".join([part async for part in chain.astream({"topic": "penguins"})])
        self.assertEqual(asyncio.run(run()), "Answer")
        ct.flush()
        roots = [s for s in self.sink.get_finished_spans() if s.parent is None]
        self.assertEqual(len(roots), 2)
        for root, topic in zip(roots, ["volcanoes", "penguins"]):
            self.assertEqual(json.loads(root.attributes['confident.span.input']), {"topic": topic})
            self.assertEqual(json.loads(root.attributes['confident.trace.input']), {"topic": topic})
            self.assertEqual(json.loads(root.attributes['confident.span.output']), 'Answer')

    def test_prompt_outputs(self):
        from langchain_core.prompts import PromptTemplate
        expected = [{"role": "user", "parts": [{"type": "text", "content": "Explain volcanoes."}]}]
        for prompt in [ChatPromptTemplate.from_template("Explain {topic}."),
                       PromptTemplate.from_template("Explain {topic}.")]:
            chain = prompt | FakeListChatModel(responses=["Answer"]) | StrOutputParser()
            chain.invoke({"topic": "volcanoes"})
            list(chain.stream({"topic": "volcanoes"}))
            async def run():
                return [part async for part in chain.astream({"topic": "volcanoes"})]
            asyncio.run(run())
        ct.flush()
        prompts = [s for s in self.sink.get_finished_spans() if s.name in ("ChatPromptTemplate", "PromptTemplate")]
        self.assertEqual(len(prompts), 6)
        for result in prompts:
            output = json.loads(result.attributes['confident.span.output'])
            self.assertEqual(output, expected if result.name == "ChatPromptTemplate" else "Explain volcanoes.")

    def test_nested_prompt_values(self):
        from confident_trace.integrations.langchain.extraction import generic
        from langchain_core.prompt_values import ChatPromptValue, StringPromptValue
        from langchain_core.messages import HumanMessage
        result = generic({"prompt": ChatPromptValue(messages=[HumanMessage(content="Hi")]),
                          "text": [StringPromptValue(text="Hello")]})
        self.assertEqual(result["prompt"][0]["parts"][0]["content"], "Hi")
        self.assertEqual(result["text"], ["Hello"])

    def test_error_and_falsey_inputs(self):
        bridge = runtime.current()._langchain_bridge
        for value in [False, 0, "", {}, [], {"topic": "failed stream"}]:
            run_id = uuid4()
            bridge.on_chain_start({}, {"input": ""}, run_id=run_id)
            bridge.on_chain_error(ValueError("intentional"), run_id=run_id, inputs=value)
        ct.flush()
        spans = self.sink.get_finished_spans()
        self.assertEqual(len(spans), 6)
        self.assertEqual([json.loads(s.attributes['confident.trace.input']) for s in spans],
                         [False, 0, "", {}, [], {"topic": "failed stream"}])
        self.assertTrue(all(s.status.is_ok is False for s in spans))

    def test_explicit_input_wins(self):
        bridge = runtime.current()._langchain_bridge
        run_id = uuid4()
        bridge.on_chain_start({}, {"input": ""}, run_id=run_id)
        op = bridge.lookup(run_id).operation
        from opentelemetry import context
        token = context.attach(op.ctx)
        try:
            ct.update_span(input="explicit span")
            ct.update_trace(input="explicit trace")
        finally:
            context.detach(token)
        bridge.on_chain_end("answer", run_id=run_id, inputs={"topic": "volcanoes"})
        ct.flush()
        result = self.sink.get_finished_spans()[0]
        self.assertEqual(json.loads(result.attributes['confident.span.input']), "explicit span")
        self.assertEqual(json.loads(result.attributes['confident.trace.input']), "explicit trace")


if __name__ == '__main__':
    unittest.main()
