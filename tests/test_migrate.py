import sqlite3

import storage.migrate as migrations


def test_current_version_uses_numeric_components():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE schema_version (version TEXT PRIMARY KEY)")
        conn.executemany(
            "INSERT INTO schema_version VALUES (?)", [("0.2.0",), ("0.10.0",)]
        )
        assert migrations.get_current_version(conn) == "0.10.0"


def test_migrate_applies_versions_in_numeric_order(tmp_path, monkeypatch):
    for version in ("0.2.0", "0.10.0"):
        (tmp_path / f"{version}.sql").write_text(
            "CREATE TABLE IF NOT EXISTS applied (version TEXT);"
            f"INSERT INTO applied VALUES ('{version}');"
        )
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(
        migrations, "MIGRATIONS", [("0.10.0", "0.10.0.sql"), ("0.2.0", "0.2.0.sql")]
    )
    db_path = str(tmp_path / "cache.db")
    assert migrations.migrate(db_path) == ["0.2.0", "0.10.0"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT version FROM applied ORDER BY rowid").fetchall() == [
            ("0.2.0",), ("0.10.0",)
        ]
    assert migrations.migrate(db_path) == []


def test_migration_target_is_compared_numerically(tmp_path, monkeypatch):
    for version in ("0.2.0", "0.10.0"):
        (tmp_path / f"{version}.sql").write_text("SELECT 1;")
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(
        migrations, "MIGRATIONS", [("0.2.0", "0.2.0.sql"), ("0.10.0", "0.10.0.sql")]
    )
    db_path = str(tmp_path / "cache.db")
    assert migrations.migrate(db_path, target_version="0.2.0") == ["0.2.0"]
    assert migrations.migrate(db_path, target_version="0.10.0") == ["0.10.0"]
