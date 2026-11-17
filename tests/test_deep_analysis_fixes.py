"""Regression tests for issues found during the deep analysis pass.

Each test maps to a verified, reproducible defect (not a false alarm):

* DetailScreen citation toggle crashed (missing ``_reveal_overlay``).
* SearchScreen footer referenced an uninitialised ``_active_query``.
* ``is_safe_url`` accepted look-alike hosts via substring matching.
* USPTO abstract was character-spaced when the API returned a string.
* Translation cache pointed at a non-existent CWD database.
* ``_check_circuit_breakers`` forced Safe Mode when only unrelated rows existed.
* ``semantic_search`` lost similarity ranking by iterating a set.
"""

from unittest.mock import PropertyMock, patch

import pytest

from core.models import PatentRecord


def _record(**kw) -> PatentRecord:
    base = dict(
        id="US12345678B2",
        title="Test Patent",
        assignee="ACME Corp",
        dates={"filed": "2022-06-15"},
        abstract="A solid state battery electrolyte.",
        claims=[],
        image_urls=[],
        status="active",
        family_id="FAM001",
    )
    base.update(kw)
    return PatentRecord(**base)


# ── 1. DetailScreen citation toggle ────────────────────────────────────────


def test_detail_screen_has_overlay_helpers():
    """DetailScreen must expose the reveal/conceal helpers used by its watcher."""
    from textual.widgets import Static

    from tui.screens import DetailScreen

    assert hasattr(DetailScreen, "_reveal_overlay")
    assert hasattr(DetailScreen, "_conceal_overlay")

    screen = DetailScreen(_record())
    widget = Static("x", classes="hidden")
    screen._reveal_overlay(widget)
    assert "hidden" not in widget.classes
    screen._conceal_overlay(widget)
    assert "hidden" in widget.classes


@pytest.mark.asyncio
async def test_detail_screen_translate_does_not_report_completion_before_translation():
    from tui.screens import DetailScreen

    notifications = []
    screen = DetailScreen(_record())
    fake_app = type("App", (), {"notify": notifications.append})()

    with (
        patch.object(DetailScreen, "app", new_callable=PropertyMock, return_value=fake_app),
        patch.object(DetailScreen, "watch__show_translation"),
    ):
        await screen.action_translate()

    assert notifications == []


# ── 2. SearchScreen footer / _active_query ─────────────────────────────────


def test_search_screen_active_query_defaults_to_none():
    from tui.screens import SearchScreen

    screen = SearchScreen()
    assert screen._active_query is None
    # Must not raise even before a search has run.
    assert "BOOL:" not in screen._build_footer_line()


def test_search_screen_footer_reflects_active_boolean_query():
    from core.query import parse_boolean_query
    from tui.screens import SearchScreen

    screen = SearchScreen()
    screen._active_query = parse_boolean_query("ti:battery")
    line = screen._build_footer_line()
    assert "BOOL:" in line
    assert "title:battery" in line


# ── 3. is_safe_url host validation ─────────────────────────────────────────


def test_is_safe_url_rejects_lookalike_hosts():
    from tui.widgets.image_tab import is_safe_url

    assert is_safe_url("https://uspto.gov.evil.com/x.png") is False
    assert is_safe_url("https://patentimages.storage.googleapis.com.evil.com/a.png") is False
    assert is_safe_url("https://evil.com/uspto.gov") is False
    assert is_safe_url("https://lens.org.attacker.io/a.png") is False


def test_is_safe_url_accepts_trusted_hosts_and_subdomains():
    from tui.widgets.image_tab import is_safe_url

    assert is_safe_url("https://patentimages.storage.googleapis.com/a.png") is True
    assert is_safe_url("https://api.lens.org/image.png") is True
    assert is_safe_url("https://patents.google.com/x.png") is True
    assert is_safe_url("http://patentimages.storage.googleapis.com/a.png") is False


# ── 4. USPTO abstract normalisation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_uspto_abstract_string_not_char_spaced(monkeypatch):
    import httpx

    from clients.base import BaseAsyncClient
    from clients.patent_apis import USPTOClient
    from core.config import Config

    monkeypatch.setattr("clients.patent_apis.load_config", lambda: Config(uspto_api_key="TEST"))

    async def mock_get(self, url, params=None, headers=None, max_retries=4):
        return httpx.Response(
            200,
            json={
                "response": {
                    "docs": [
                        {
                            "patentNumber": "US1",
                            "inventionTitle": "T",
                            "applicantName": "A",
                            "filingDate": "2020-01-01",
                            "abstractText": "A solid state battery.",
                            "applicationStatusCategory": "active",
                        }
                    ]
                }
            },
        )

    monkeypatch.setattr(BaseAsyncClient, "get_with_backoff", mock_get)

    async def mock_sleep(x):
        pass

    monkeypatch.setattr("asyncio.sleep", mock_sleep)

    results = await USPTOClient().search("battery")
    assert results[0].abstract == "A solid state battery."


# ── 5. Translation cache persistence ───────────────────────────────────────


def test_translation_cache_round_trip(tmp_path):
    import storage.cache as cache_mod
    from storage.cache import CacheDatabase

    db_path = str(tmp_path / "t.db")
    CacheDatabase(db_path=db_path)  # ensure schema

    with patch.object(cache_mod, "CacheDatabase", lambda *a, **k: CacheDatabase(db_path=db_path)):
        import core.translation as tr

        assert tr._get_cached_translation("一种固态电池") is None
        tr._save_translation_cache("一种固态电池", "solid state battery")
        assert tr._get_cached_translation("一种固态电池") == "solid state battery"


# ── 6. Circuit breaker false positive ──────────────────────────────────────


class _FakeHealthDB:
    def __init__(self, rows):
        self._rows = rows

    def get_all_source_health(self):
        return self._rows


def test_circuit_breaker_no_false_positive_for_unknown_source():
    import core.search as cs

    rows = [{"source_name": "epo", "circuit_open": False}]
    with patch.object(cs, "CacheDatabase", lambda *a, **k: _FakeHealthDB(rows)):
        assert cs._check_circuit_breakers(["uspto"]) is False


def test_circuit_breaker_true_when_all_requested_open():
    import core.search as cs

    rows = [
        {"source_name": "uspto", "circuit_open": True},
        {"source_name": "epo", "circuit_open": False},
    ]
    with patch.object(cs, "CacheDatabase", lambda *a, **k: _FakeHealthDB(rows)):
        assert cs._check_circuit_breakers(["uspto"]) is True
        assert cs._check_circuit_breakers(["uspto", "epo"]) is False


# ── 7. semantic_search ranking ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_semantic_search_preserves_similarity_order(tmp_path):
    import core.ai as ai_mod
    import core.search as cs
    from storage.cache import CacheDatabase

    db_path = str(tmp_path / "sem.db")
    db = CacheDatabase(db_path=db_path)

    # Three collection records; embeddings chosen so B > A > C in similarity.
    for pid, emb in (("USA", [1.0, 0.0]), ("USB", [0.9, 0.1]), ("USC", [0.1, 0.9])):
        db.save_embedding(pid, emb)
        db.save_to_collection(_record(id=pid))

    async def fake_embedding(self, text):
        return [1.0, 0.0]

    with patch.object(cs, "CacheDatabase", lambda *a, **k: CacheDatabase(db_path=db_path)):
        with patch.object(ai_mod.AIProvider, "generate_embedding", fake_embedding):
            results = await cs.semantic_search("battery", top_k=3)

    ids = [r.id for r in results]
    assert ids[0] == "USA"
    assert ids.index("USB") < ids.index("USC")


# ── 8. `search` must not auto-populate the saved collection ─────────────────


def test_search_command_does_not_populate_collection(monkeypatch):
    from typer.testing import CliRunner

    from cli import main as cli_main

    saved_to_collection: list = []

    class _FakeDB:
        def is_cache_valid(self, query):
            return False

        def save_search_results(self, query, results, sources=None):
            pass

        def add_search_history(self, query, **kw):
            return 1

        def save_to_collection(self, record, *a, **kw):
            saved_to_collection.append(record)

    async def fake_search_all(query, sources=None):
        return [_record(id="US1"), _record(id="US2")]

    monkeypatch.setattr(cli_main, "search_all", fake_search_all)
    monkeypatch.setattr(cli_main, "CacheDatabase", lambda *a, **k: _FakeDB())

    result = CliRunner().invoke(cli_main.app, ["search", "battery"], catch_exceptions=True)
    assert result.exit_code == 0
    assert saved_to_collection == []
