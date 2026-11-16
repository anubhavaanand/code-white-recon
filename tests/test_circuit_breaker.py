import pytest

from clients.circuit_breaker import CircuitBreaker, CircuitOpenError


def test_record_failure_opens_circuit() -> None:
    breaker = CircuitBreaker(name="test", threshold=2)

    breaker.check()
    breaker.record_failure()
    breaker.check()
    breaker.record_failure()

    with pytest.raises(CircuitOpenError):
        breaker.check()


def test_record_success_closes_circuit() -> None:
    breaker = CircuitBreaker(name="test", threshold=1)

    breaker.record_failure()
    with pytest.raises(CircuitOpenError):
        breaker.check()

    breaker.record_success()
    breaker.check()
    assert breaker.failure_count == 0
