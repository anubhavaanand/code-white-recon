import json
import logging
from unittest.mock import patch

from core.metrics import (
    JSONFormatter,
    MetricsCollector,
    MetricSnapshot,
    Severity,
    log_event,
    setup_logging,
)


def test_severity_values():
    assert Severity.P0.value == "critical"
    assert Severity.P1.value == "high"
    assert Severity.P2.value == "medium"
    assert Severity.P3.value == "low"


def test_json_formatter_formats_record():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="recon",
        level=logging.INFO,
        pathname="metrics.py",
        lineno=10,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    record.component = "search"
    record.event = "search_start"
    record.extra = {"query": "battery", "sources": ["uspto"]}

    output = formatter.format(record)
    parsed = json.loads(output)

    assert parsed["level"] == "INFO"
    assert parsed["component"] == "search"
    assert parsed["event"] == "search_start"
    assert parsed["message"] == "Test message"
    assert parsed["query"] == "battery"
    assert parsed["sources"] == ["uspto"]
    assert "session_id" in parsed
    assert "version" in parsed
    assert "timestamp" in parsed


def test_setup_logging_and_log_event(tmp_path):
    log_dir = tmp_path / "logs"
    with patch("os.path.expanduser", return_value=str(log_dir)):
        logger = setup_logging(debug=True)
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) == 1

        log_event("info", "test_comp", "test_event", "hello metrics", extra={"key": "val"})


def test_metrics_collector_search_tracking():
    collector = MetricsCollector(session_id="test_sess", version="0.2.0")

    # Record normal searches (cache miss, cache hit, and error)
    collector.record_search(latency_ms=120.0, cache_hit=False)
    collector.record_search(latency_ms=30.0, cache_hit=True)
    collector.record_search(latency_ms=500.0, cache_hit=False, error=True)

    assert collector.search_count == 3
    assert collector.cache_hits == 1
    assert collector.cache_misses == 2
    assert collector.search_errors == 1
    assert len(collector.latencies) == 3


def test_metrics_collector_api_calls_and_rates():
    collector = MetricsCollector(session_id="test_sess", version="0.2.0")

    collector.record_api_call(source="uspto", success=True, rate_limit_used=0.45)
    collector.record_api_call(source="uspto", success=False, rate_limit_used=0.80)
    collector.record_api_call(source="epo", success=True, rate_limit_used=0.20)

    assert collector.api_calls["uspto"]["total"] == 2
    assert collector.api_calls["uspto"]["errors"] == 1
    assert collector.api_calls["uspto"]["rate_limit_max"] == 0.80
    assert collector.api_calls["epo"]["total"] == 1
    assert collector.api_calls["epo"]["errors"] == 0


def test_metrics_collector_memory_info():
    collector = MetricsCollector(session_id="test_sess", version="0.2.0")
    mem = collector.get_memory_mb()
    # psutil is in dev dependencies; if present, it should be a float or None
    if mem is not None:
        assert isinstance(mem, float)
        assert mem > 0


def test_metrics_collector_snapshot_and_alerts(tmp_path):
    collector = MetricsCollector(session_id="test_sess", version="0.2.0")

    # Record 15 searches with low cache hits (<50%) and errors
    for _ in range(12):
        collector.record_search(latency_ms=100.0, cache_hit=False)
    for _ in range(2):
        collector.record_search(latency_ms=20.0, cache_hit=True)

    # Record API error rate > 5% and rate limit proximity > 0.76
    for _ in range(5):
        collector.record_api_call("uspto", success=False, rate_limit_used=0.90)
    for _ in range(5):
        collector.record_api_call("uspto", success=True, rate_limit_used=0.50)

    snapshot = collector.snapshot()
    assert isinstance(snapshot, MetricSnapshot)
    assert snapshot.session_id == "test_sess"
    assert snapshot.version == "0.2.0"
    assert snapshot.cache_hit_ratio < 0.5
    assert snapshot.api_error_rate == 0.5

    # Check alert rules triggered
    alert_metrics = {a["metric"] for a in snapshot.active_alerts}
    assert "recon_api_error_rate" in alert_metrics
    assert "recon_rate_limit_proximity" in alert_metrics
    assert "recon_cache_hit_ratio" in alert_metrics

    # Write snapshot to file
    out_file = str(tmp_path / "metrics.jsonl")
    collector.write_snapshot(path=out_file)
    with open(out_file) as f:
        line = f.readline()
        data = json.loads(line)
        assert data["session_id"] == "test_sess"
        assert len(data["active_alerts"]) >= 3


def test_event_loop_blocking_detection():
    collector = MetricsCollector(session_id="test_sess", version="0.2.0")
    collector._last_block_check = 0.0  # Force elapsed > 100ms
    blocked = collector._check_event_loop_blocking()
    assert blocked > 100
