import pytest

from src.utils.retry import RetryResult, call_with_retry
from src.utils.trace import ExecutionTracer


def test_execution_trace_does_not_store_provider_payload():
    tracer = ExecutionTracer("Analyst", "ANL-TEST")
    with pytest.raises(RuntimeError), tracer.step("provider_call"):
        raise RuntimeError("token=must-not-appear")

    assert tracer.steps[0].error_message == "RuntimeError"


def test_retry_metadata_does_not_store_provider_payload():
    result = RetryResult()

    def fail():
        raise ConnectionError("url-with-sensitive-query")

    with pytest.raises(ConnectionError):
        call_with_retry(fail, max_attempts=1, sleep=lambda _seconds: None, result=result)

    assert result.errors == ["attempt 1: ConnectionError"]
