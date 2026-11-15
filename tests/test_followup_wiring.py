"""Regression tests for the follow-up wiring / data-integrity fixes.

These cover the confirmed-but-previously-unwired features:

* collections dedupe on (patent_id, collection_name)
* startup maintenance runs at most once per database path per process
* search history is recorded by the CLI
* exports append an audit row to export_log
* non-empty citation graphs are cached and reused
"""

import pytest

from core.models import PatentRecord


def _record(pid="US12345678B2", **kw) -> PatentRecord:
    base = dict(
        id=pid,
        title="Test Patent",
        assignee="ACME Corp",
        dates={"filed": "2022-06-15"},
        abstract="abstract",
        claims=[],
        image_urls=[],
        status="active",
        family_id="FAM",
    )
    base.update(kw)
    return PatentRecord(**base)


# ── 1. Collection dedupe ───────────────────────────────────────────────────


def test_save_to_collection_dedupes(tmp_path):
    from storage.cache import CacheDatabase

    db = CacheDatabase(db_path=str(tmp_path / "c.db"))
    db.save_to_collection(_record())
    db.save_to_collection(_record())

    assert len(db.get_collection()) == 1


def test_save_to_collection_dedupes_per_collection(tmp_path):
    from storage.cache import CacheDatabase

    db = CacheDatabase(db_path=str(tmp_path / "c.db"))
    db.save_to_collection(_record(), collection_name="a")
    db.save_to_collection(_record(), collection_name="b")

    assert len(db.get_collection("a")) == 1
    assert len(db.get_collection("b")) == 1
    assert len(db.get_all_collections()) == 2


# ── 2. Startup maintenance once per process/path ───────────────────────────


def test_startup_maintenance_runs_once_per_path(tmp_path, monkeypatch):
    import storage.cache as cache_mod
    from storage.cache import CacheDatabase

    calls = {"n": 0}

    def fake_evict(self, *a, **kw):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(CacheDatabase, "enforce_eviction_policy", fake_evict)
    path = str(tmp_path / f"maint-{calls['n']}.db")

    CacheDatabase(db_path=path)
    CacheDatabase(db_path=path)
    CacheDatabase(db_path=path)

    assert calls["n"] == 1
    assert cache_mod is not None


# ── 3. CLI search records history ──────────────────────────────────────────


def test_search_command_records_history(monkeypatch):
    from typer.testing import CliRunner

    from cli import main as cli_main

    history: list[dict] = []

    class _FakeDB:
        def is_cache_valid(self, query):
            return False

        def save_search_results(self, query, results, sources=None):
            pass

        def add_search_history(self, query, **kw):
            history.append({"query": query, **kw})
            return 1

        def save_to_collection(self, record, *a, **kw):
            pass

    async def fake_search_all(query, sources=None):
        return [_record(id="US1")]

    monkeypatch.setattr(cli_main, "search_all", fake_search_all)
    monkeypatch.setattr(cli_main, "CacheDatabase", lambda *a, **k: _FakeDB())

    result = CliRunner().invoke(cli_main.app, ["search", "battery"], catch_exceptions=True)
    assert result.exit_code == 0
    assert len(history) == 1
    assert history[0]["query"] == "battery"
    assert history[0]["result_count"] == 1
    assert history[0]["tui_mode"] is False


# ── 4. Export audit log ────────────────────────────────────────────────────


def test_export_records_logs_audit(tmp_path, monkeypatch):
    import storage.cache as cache_mod
    from cli.export import export_records

    logged: list[dict] = []

    class _FakeDB:
        def log_export(self, **kw):
            logged.append(kw)
            return 1

    monkeypatch.setattr(cache_mod, "CacheDatabase", lambda *a, **k: _FakeDB())

    out = tmp_path / "out.csv"
    export_records([_record()], "csv", str(out))

    assert out.exists()
    assert len(logged) == 1
    assert logged[0]["export_format"] == "csv"
    assert logged[0]["record_count"] == 1
    assert logged[0]["file_size_bytes"] == out.stat().st_size


# ── 5. Citation graph caching ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_citations_caches_and_reuses(tmp_path, monkeypatch):
    import core.citations as cit
    from storage.cache import CacheDatabase

    db_path = str(tmp_path / "cit.db")
    CacheDatabase(db_path=db_path)  # ensure schema

    calls = {"n": 0}
    node = cit.CitationNode(id="US9", title="Back", assignee="X", date="2020-01-01")

    async def fake_backward(pid):
        calls["n"] += 1
        return [node]

    async def fake_forward(pid):
        return []

    monkeypatch.setattr(cit, "_fetch_backward_citations", fake_backward)
    monkeypatch.setattr(cit, "_fetch_forward_citations", fake_forward)

    import storage.cache as cache_mod

    RealDB = cache_mod.CacheDatabase
    monkeypatch.setattr(cache_mod, "CacheDatabase", lambda *a, **k: RealDB(db_path=db_path))

    first = await cit.fetch_citations("US123", "Test Corp")
    assert len(first.backward) == 1
    assert calls["n"] == 1

    second = await cit.fetch_citations("US123", "Test Corp")
    assert len(second.backward) == 1
    assert calls["n"] == 1, "cached graph must not re-hit the scraper"
    assert second.backward[0].id == "US9"
