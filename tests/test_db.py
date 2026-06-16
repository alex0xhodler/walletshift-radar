"""
Tests for db.py — schema creation, upsert idempotency, query helpers.

Uses an in-memory SQLite DB so no filesystem side-effects.
"""
import json
import sqlite3
import pathlib
import pytest

from walletshift_radar.db import init_db, upsert_agent, upsert_snapshot, upsert_directory_stats

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def test_init_db_creates_all_tables(conn):
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "agents" in tables
    assert "snapshots" in tables
    assert "directory_stats" in tables
    assert "category_stats" in tables
    assert "events" in tables


def test_init_db_idempotent(conn):
    """Calling init_db twice does not raise or duplicate tables."""
    init_db(conn)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "agents" in tables


def test_upsert_agent_inserts_new(conn):
    detail = json.loads((FIXTURES / "detail_1.json").read_text())
    upsert_agent(conn, detail, cluster_key="AgentEinstein", snapshot_date="2026-06-16")
    row = conn.execute("SELECT * FROM agents WHERE token_id=?", (detail["id"],)).fetchone()
    assert row is not None
    assert row["name"] == detail["name"]
    assert row["cluster_key"] == "AgentEinstein"


def test_upsert_agent_is_idempotent(conn):
    """Upserting the same agent twice does not create duplicate rows."""
    detail = json.loads((FIXTURES / "detail_1.json").read_text())
    upsert_agent(conn, detail, cluster_key="AgentEinstein", snapshot_date="2026-06-16")
    upsert_agent(conn, detail, cluster_key="AgentEinstein", snapshot_date="2026-06-16")
    count = conn.execute("SELECT COUNT(*) FROM agents WHERE token_id=?", (detail["id"],)).fetchone()[0]
    assert count == 1


def test_upsert_snapshot_inserts(conn):
    detail = json.loads((FIXTURES / "detail_1.json").read_text())
    upsert_agent(conn, detail, cluster_key="AgentEinstein", snapshot_date="2026-06-16")
    metrics = {
        "skills_count": 24,
        "live_count": 2,
        "dead_count": 1,
        "paywalled_count": 0,
        "endpoint_count": 4,
        "x402": True,
        "protos_json": '["a2a","mcp"]',
        "summary_hash": "abc123",
    }
    upsert_snapshot(conn, "2026-06-16", detail["id"], metrics)
    row = conn.execute(
        "SELECT * FROM snapshots WHERE token_id=? AND snapshot_date=?",
        (detail["id"], "2026-06-16")
    ).fetchone()
    assert row is not None
    assert row["skills_count"] == 24
    assert row["live_count"] == 2


def test_upsert_snapshot_idempotent(conn):
    """Re-upserting same (token_id, date) updates, does not duplicate."""
    detail = json.loads((FIXTURES / "detail_1.json").read_text())
    upsert_agent(conn, detail, cluster_key="AgentEinstein", snapshot_date="2026-06-16")
    metrics = {"skills_count": 10, "live_count": 1, "dead_count": 0,
               "paywalled_count": 0, "endpoint_count": 2, "x402": False,
               "protos_json": '["a2a"]', "summary_hash": "x"}
    upsert_snapshot(conn, "2026-06-16", detail["id"], metrics)
    metrics2 = {**metrics, "skills_count": 15}
    upsert_snapshot(conn, "2026-06-16", detail["id"], metrics2)
    count = conn.execute(
        "SELECT COUNT(*) FROM snapshots WHERE token_id=? AND snapshot_date=?",
        (detail["id"], "2026-06-16")
    ).fetchone()[0]
    assert count == 1
    row = conn.execute(
        "SELECT skills_count FROM snapshots WHERE token_id=? AND snapshot_date=?",
        (detail["id"], "2026-06-16")
    ).fetchone()
    assert row["skills_count"] == 15


def test_upsert_directory_stats(conn):
    stats = {"total_agents": 711, "distinct_products": 270,
             "live_skills_read": 91, "x402_count": 105, "category_count": 13}
    upsert_directory_stats(conn, "2026-06-16", stats)
    row = conn.execute(
        "SELECT * FROM directory_stats WHERE snapshot_date=?", ("2026-06-16",)
    ).fetchone()
    assert row["total_agents"] == 711
    assert row["distinct_products"] == 270
