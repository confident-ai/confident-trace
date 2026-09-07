"""Verify the diagnostic limiter recovers from a lock held across fork."""

import logging
import os

from confident_trace._core import diagnostics
from confident_trace._core.safety import safe

diagnostics.log.setLevel(logging.DEBUG)
diagnostics._lock.acquire()
diagnostics._local.reporting = True
pid = os.fork()
if pid == 0:
    assert safe(lambda: 1 / 0) is None
    assert diagnostics._count == 1
    os._exit(0)
diagnostics._lock.release()
_, status = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(status) == 0
