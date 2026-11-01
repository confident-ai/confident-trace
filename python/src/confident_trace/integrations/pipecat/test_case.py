"""Link a simulated call's trace to the Confident AI test case that placed it."""

from contextvars import ContextVar

from ..._core.spans import fields
from ._constants import TEST_CASE_ID_BODY_KEY

_test_case_id = ContextVar("confident_pipecat_test_case_id", default=None)


def remember_test_case(runner_args):
    body = getattr(runner_args, "body", None)
    test_case_id = body.get(TEST_CASE_ID_BODY_KEY) if isinstance(body, dict) else None
    if isinstance(test_case_id, str):
        _test_case_id.set(test_case_id)


def link_test_case(observer):
    test_case_id = _test_case_id.get()
    span = getattr(observer, "_conversation_span", None)
    if test_case_id is None or span is None:
        return
    fields(span, {"test_case_id": test_case_id}, only_unset=True)
