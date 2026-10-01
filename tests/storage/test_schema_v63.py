"""预算迁移顺延至 63 后，已有画像控制库可无损升级。"""

import sqlite3

from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase


def test_upgrade_from_v62_preserves_profile_controls_and_adds_budget(tmp_path) -> None:
    path = tmp_path / "upgrade.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 63):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_meta VALUES ('version', '62')")
        connection.execute(
            "INSERT INTO profile_account_controls(account_id, profile_usage_enabled, updated_at)"
            " VALUES ('a', 0, '2026-10-01T00:00:00+00:00')"
        )
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert SCHEMA_VERSION == 63
    row = database.scoped("a").execute(
        "SELECT profile_usage_enabled FROM profile_account_controls WHERE account_id = ?", ("a",)
    ).fetchone()
    assert row["profile_usage_enabled"] == 0
    columns = {
        row["name"] for row in database.connection.execute("PRAGMA table_info(run_budget_ledger)")
    }
    assert {"deadline_at", "recipe_costs_json", "model_calls_used", "version"} <= columns
    assert database.initialize() == SCHEMA_VERSION
