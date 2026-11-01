"""Link a simulated call's trace to the Confident AI test case that placed it."""

import json

from opentelemetry import trace

from ..._core.spans import fields
from ._constants import TEST_CASE_ID_METADATA_KEY


def link_test_case():
    from livekit.agents import get_job_context

    ctx = get_job_context(required=False)
    if ctx is None:
        return
    try:
        test_case_id = json.loads(ctx.job.metadata).get(TEST_CASE_ID_METADATA_KEY)
    except (AttributeError, TypeError, ValueError):
        return
    if isinstance(test_case_id, str):
        fields(
            trace.get_current_span(), {"test_case_id": test_case_id}, only_unset=True
        )
