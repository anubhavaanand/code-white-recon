"""`recon admin cache-stats` reports eviction metrics without deleting anything."""

import contextlib

from typer.testing import CliRunner

from cli import main as cli_main
from core.models import PatentRecord
from storage.cache import CacheDatabase

runner = CliRunner()


def _record(patent_id: str) -> PatentRecord:
    return PatentRecord(
        id=patent_id,
        title="T",
        assignee="A",
        dates={"filed": "2020-01-01"},
        abstract="AB",
        claims=[],
        image_urls=[],
        status="S",
        family_id="F",
    )


def _seed_expired_search(db: CacheDatabase, query: str = "battery") -> None:
    db.save_search_results(query, [_record("US1")])
    with contextlib.closing(db.get_connection()) as conn:
        with conn:
            conn.execute("UPDATE search_results SET expires_at = datetime('now', '-1 day')")


def _search_result_count(db: CacheDatabase) -> int:
    with contextlib.closing(db.get_connection()) as conn:
        return conn.execute("SELECT COUNT(*) FROM search_results").fetchone()[0]


def test_preview_eviction_policy_is_read_only(tmp_path):
    db = CacheDatabase(db_path=str(tmp_path / "cache.db"), run_maintenance=False)
    _seed_expired_search(db)

    preview = db.preview_eviction_policy()

    assert preview["deleted_expired"] == 1
    assert _search_result_count(db) == 1

    # The enforcing counterpart still cleans up, so the preview is not a no-op.
    enforced = db.enforce_eviction_policy()
    assert enforced["deleted_expired"] == 1
    assert _search_result_count(db) == 0


def test_startup_maintenance_still_runs_by_default(tmp_path):
    db = CacheDatabase(db_path=str(tmp_path / "cache.db"), run_maintenance=False)
    _seed_expired_search(db)

    CacheDatabase(db_path=str(tmp_path / "cache.db"))

    assert _search_result_count(db) == 0


def test_admin_cache_stats_reports_without_deleting(tmp_path, monkeypatch):
    db = CacheDatabase(db_path=str(tmp_path / "cache.db"), run_maintenance=False)
    _seed_expired_search(db)

    calls = []

    def factory(*args, **kwargs):
        calls.append(kwargs)
        return db

    monkeypatch.setattr(cli_main, "CacheDatabase", factory)

    result = runner.invoke(cli_main.app, ["admin", "cache-stats"])

    assert result.exit_code == 0, result.output
    assert "Eviction preview" in result.output
    assert _search_result_count(db) == 1
    assert calls[0]["run_maintenance"] is False
