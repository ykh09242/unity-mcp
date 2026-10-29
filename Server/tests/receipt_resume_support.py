"""Real receipt admission fence with isolated storage/authority I/O for transport tests."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

from models.response_limits import ResponseOwner
from services.execution_receipt_context import execution_owner
from transport.execution_receipts import PreparedExecution


@contextmanager
def prepared_receipt(project):
    async def io(callback):
        return callback()

    async def check():
        return None

    memory = ResponseOwner()
    runtime = SimpleNamespace(io=io, close_if_idle=lambda: None)
    authority = SimpleNamespace(check_now=lambda: None, check=check, grants=None)
    prepared = PreparedExecution(
        runtime, authority, SimpleNamespace(project_hash=project), None, frozenset(), memory
    )
    prepared.invocation = Mock()
    token = execution_owner.set(prepared)
    try:
        yield prepared
    finally:
        execution_owner.reset(token)
        prepared.close()
