"""
Tests for the services_json caching fix.

Root cause: walletshift-seeded agents have token_uri=NULL; on daily re-probe
enrich_token re-fetches IPFS from scratch every run; IPFS fails for 578/801
agents → no snapshot written → 72% stale data.

Fix: cache endpoint URLs in agents.services_json so the daily probe skips IPFS.
"""
import json
import sqlite3
import pytest

from walletshift_radar.db import (
    init_db, upsert_agent, upsert_snapshot, migrate_services_cache,
)
from walletshift_radar.main import _ws_endpoints_to_services, run


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    migrate_services_cache(c)
    yield c
    c.close()


WS_ENDPOINTS = [
    {"proto": "a2a",  "name": "A2A",  "url": "https://example.com/a2a",  "host": "example.com"},
    {"proto": "x402", "name": "X402", "url": "https://example.com/pay",  "host": "example.com"},
    {"proto": None,   "name": "REST", "url": "https://example.com/api",  "host": "example.com"},
]


# ── migrate_services_cache ─────────────────────────────────────────────────────

class TestMigrateServicesCache:
    def test_adds_services_json_column_to_old_schema(self):
        # Simulate an existing DB created before services_json was added to _SCHEMA
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.executescript("""
            CREATE TABLE agents (
                token_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                unresolved INTEGER DEFAULT 0
            );
        """)
        cols_before = {row["name"] for row in c.execute("PRAGMA table_info(agents)")}
        assert "services_json" not in cols_before

        migrate_services_cache(c)

        cols_after = {row["name"] for row in c.execute("PRAGMA table_info(agents)")}
        assert "services_json" in cols_after
        c.close()

    def test_idempotent_when_column_already_exists(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        init_db(c)
        migrate_services_cache(c)
        migrate_services_cache(c)  # should not raise
        cols = {row["name"] for row in c.execute("PRAGMA table_info(agents)")}
        assert "services_json" in cols
        c.close()


# ── upsert_agent services_json storage ────────────────────────────────────────

class TestUpsertAgentServicesJson:
    def _agent(self, tid=1):
        return {"token_id": tid, "name": f"Agent #{tid}"}

    def test_stores_services_json_when_provided(self, conn):
        services = [{"endpoint": "https://example.com/api", "name": "rest"}]
        upsert_agent(conn, self._agent(), cluster_key="test",
                     snapshot_date="2026-06-22",
                     services_json=json.dumps(services))
        row = conn.execute("SELECT services_json FROM agents WHERE token_id=1").fetchone()
        assert json.loads(row["services_json"]) == services

    def test_null_services_json_does_not_overwrite_existing(self, conn):
        services = [{"endpoint": "https://example.com/api", "name": "rest"}]
        upsert_agent(conn, self._agent(), cluster_key="test",
                     snapshot_date="2026-06-22",
                     services_json=json.dumps(services))
        # Second call with services_json=None should not clear the cached value
        upsert_agent(conn, self._agent(), cluster_key="test",
                     snapshot_date="2026-06-23",
                     services_json=None)
        row = conn.execute("SELECT services_json FROM agents WHERE token_id=1").fetchone()
        assert json.loads(row["services_json"]) == services

    def test_new_services_json_overwrites_old(self, conn):
        old = [{"endpoint": "https://old.example.com", "name": "old"}]
        new = [{"endpoint": "https://new.example.com", "name": "new"}]
        upsert_agent(conn, self._agent(), cluster_key="test",
                     snapshot_date="2026-06-22", services_json=json.dumps(old))
        upsert_agent(conn, self._agent(), cluster_key="test",
                     snapshot_date="2026-06-23", services_json=json.dumps(new))
        row = conn.execute("SELECT services_json FROM agents WHERE token_id=1").fetchone()
        assert json.loads(row["services_json"]) == new


# ── _ws_endpoints_to_services ─────────────────────────────────────────────────

class TestWsEndpointsToServices:
    def test_converts_url_to_endpoint(self):
        services = _ws_endpoints_to_services(WS_ENDPOINTS)
        urls = [s["endpoint"] for s in services]
        assert "https://example.com/a2a" in urls
        assert "https://example.com/pay" in urls

    def test_preserves_name(self):
        services = _ws_endpoints_to_services(WS_ENDPOINTS)
        names = [s["name"] for s in services]
        assert "A2A" in names
        assert "X402" in names

    def test_skips_entries_without_url(self):
        endpoints = [{"proto": "a2a", "name": "A2A", "url": ""},
                     {"proto": "mcp", "name": "MCP"}]
        services = _ws_endpoints_to_services(endpoints)
        assert all(s.get("endpoint") for s in services)

    def test_empty_input_returns_empty_list(self):
        assert _ws_endpoints_to_services([]) == []


# ── fast-path probe: snapshot written when IPFS is unreachable ─────────────────

class TestFastPathProbe:
    """
    Core behavior: if an agent has services_json cached, run() must write a
    daily snapshot even when IPFS and chain RPC are completely unreachable.
    """

    def test_snapshot_written_despite_ipfs_failure(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        init_db(c)
        migrate_services_cache(c)

        # Seed one agent with services_json so the fast path applies
        services = [
            {"endpoint": "http://localhost:19999/does-not-exist", "name": "rest"},
        ]
        upsert_agent(c, {"token_id": 42, "name": "FastAgent"}, cluster_key="FastAgent",
                     snapshot_date="2026-06-21", services_json=json.dumps(services))
        upsert_snapshot(c, "2026-06-21", 42,
                        {"skills_count": 3, "live_count": 1, "dead_count": 0,
                         "paywalled_count": 0, "endpoint_count": 1, "x402": False,
                         "protos_json": '["web"]', "summary_hash": "abc"})
        c.close()

        import walletshift_radar.main as main_mod
        enrich_calls = []
        monkeypatch.setattr(main_mod, "enrich_token", lambda *a, **kw: enrich_calls.append(a) or None)
        monkeypatch.setattr(main_mod, "probe_agent_endpoints",
                            lambda svcs: [{**s, "health": {"status": "dead", "http": None, "url": s.get("endpoint", "")}} for s in svcs])
        monkeypatch.setattr(main_mod, "get_latest_block", lambda *a: 99_999_999)
        monkeypatch.setattr(main_mod, "get_new_mints", lambda *a, **kw: [])

        run(
            db_path=db_path,
            out_path=str(tmp_path / "out.html"),
            alchemy_key="fake",
            run_date="2026-06-22",
            full_probe=True,
            seed_from_walletshift=False,
        )

        c2 = sqlite3.connect(db_path)
        row = c2.execute(
            "SELECT * FROM snapshots WHERE token_id=42 AND snapshot_date='2026-06-22'"
        ).fetchone()
        assert row is not None, "Fast-path agent must get a snapshot even when IPFS is down"
        assert not enrich_calls, "Fast path must not call enrich_token (which would hit IPFS)"
        c2.close()

    def test_skills_count_carried_forward_from_previous_snapshot(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        init_db(c)
        migrate_services_cache(c)

        services = [{"endpoint": "http://localhost:19999/gone", "name": "mcp"}]
        upsert_agent(c, {"token_id": 7, "name": "SkillsAgent"}, cluster_key="SkillsAgent",
                     snapshot_date="2026-06-21", services_json=json.dumps(services))
        upsert_snapshot(c, "2026-06-21", 7,
                        {"skills_count": 12, "live_count": 1, "dead_count": 0,
                         "paywalled_count": 0, "endpoint_count": 1, "x402": False,
                         "protos_json": '["mcp","web"]', "summary_hash": "xyz"})
        c.close()

        import walletshift_radar.main as main_mod
        monkeypatch.setattr(main_mod, "enrich_token", lambda *a, **kw: None)
        monkeypatch.setattr(main_mod, "probe_agent_endpoints",
                            lambda svcs: [{**s, "health": {"status": "dead", "http": None, "url": s.get("endpoint", "")}} for s in svcs])
        monkeypatch.setattr(main_mod, "get_latest_block", lambda *a: 99_999_999)
        monkeypatch.setattr(main_mod, "get_new_mints", lambda *a, **kw: [])

        run(db_path=db_path, out_path=str(tmp_path / "out.html"),
            alchemy_key="fake", run_date="2026-06-22",
            full_probe=True, seed_from_walletshift=False)

        c2 = sqlite3.connect(db_path)
        row = c2.execute(
            "SELECT skills_count FROM snapshots WHERE token_id=7 AND snapshot_date='2026-06-22'"
        ).fetchone()
        assert row is not None
        assert row[0] == 12, "skills_count must be carried from prior snapshot on fast path"
        c2.close()


# ── walletshift seed stores services_json ─────────────────────────────────────

class TestWalletShiftSeedServicesJson:
    def test_seed_stores_services_json_for_agents_with_endpoints(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")

        import walletshift_radar.fetch as fetch_mod
        import walletshift_radar.main as main_mod

        monkeypatch.setattr(main_mod, "get_latest_block", lambda *a: 25_280_001)
        monkeypatch.setattr(main_mod, "get_new_mints", lambda *a, **kw: [])

        search_result = [{
            "id": 99,
            "name": "SeedAgent",
            "category": "defi-trade-execution",
            "summary": "test",
            "protos": ["a2a"],
            "x402": False,
            "skills_count": 5,
            "endpoints": [
                {"proto": "a2a", "name": "A2A", "url": "https://seed.example.com/a2a", "host": "seed.example.com"},
            ],
        }]
        monkeypatch.setattr(fetch_mod, "fetch_all_search",
                            lambda *a, **kw: (search_result, [], {}))

        run(db_path=db_path, out_path=str(tmp_path / "out.html"),
            alchemy_key="fake", run_date="2026-06-22",
            full_probe=False, seed_from_walletshift=True)

        c = sqlite3.connect(db_path)
        row = c.execute("SELECT services_json FROM agents WHERE token_id=99").fetchone()
        assert row is not None
        assert row[0] is not None, "services_json must be stored for WalletShift-seeded agents"
        services = json.loads(row[0])
        urls = [s.get("endpoint") for s in services]
        assert "https://seed.example.com/a2a" in urls
        c.close()


# ── slow path caches services_json for next run ────────────────────────────────

class TestSlowPathCachesServicesJson:
    def test_successful_enrich_stores_services_json(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        init_db(c)
        migrate_services_cache(c)
        # Agent with no services_json yet → will go through slow path
        upsert_agent(c, {"token_id": 55, "name": "SlowAgent"}, cluster_key="SlowAgent",
                     snapshot_date="2026-06-21")
        upsert_snapshot(c, "2026-06-21", 55,
                        {"skills_count": 0, "live_count": 0, "dead_count": 1,
                         "paywalled_count": 0, "endpoint_count": 1, "x402": False,
                         "protos_json": '["web"]', "summary_hash": "aaa"})
        c.close()

        import walletshift_radar.main as main_mod
        monkeypatch.setattr(main_mod, "get_latest_block", lambda *a: 99_999_999)
        monkeypatch.setattr(main_mod, "get_new_mints", lambda *a, **kw: [])
        monkeypatch.setattr(main_mod, "enrich_token", lambda url, tid, probe=True: {
            "token_id": tid,
            "token_uri": "https://meta.example.com/55",
            "name": "SlowAgent",
            "services": [{"endpoint": "https://agent55.example.com/api", "name": "rest",
                          "health": {"status": "live", "http": 200, "url": "https://agent55.example.com/api"}}],
            "protos": ["web"],
            "x402": False,
            "skills_count": 0,
            "live_count": 1,
            "dead_count": 0,
            "paywalled_count": 0,
            "_unresolved": False,
        })

        run(db_path=db_path, out_path=str(tmp_path / "out.html"),
            alchemy_key="fake", run_date="2026-06-22",
            full_probe=True, seed_from_walletshift=False)

        c2 = sqlite3.connect(db_path)
        row = c2.execute("SELECT services_json FROM agents WHERE token_id=55").fetchone()
        assert row is not None
        assert row[0] is not None, "After a successful slow-path probe, services_json must be stored"
        services = json.loads(row[0])
        assert any(s.get("endpoint") == "https://agent55.example.com/api" for s in services)
        assert all("health" not in s for s in services), "health data must be stripped before caching"
        c2.close()

    def test_unresolved_slow_path_does_not_store_services_json(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        init_db(c)
        migrate_services_cache(c)
        upsert_agent(c, {"token_id": 56, "name": "UnresolvedAgent"}, cluster_key="UA",
                     snapshot_date="2026-06-21")
        upsert_snapshot(c, "2026-06-21", 56,
                        {"skills_count": 0, "live_count": 0, "dead_count": 1,
                         "paywalled_count": 0, "endpoint_count": 1, "x402": False,
                         "protos_json": '["web"]', "summary_hash": "bbb"})
        c.close()

        import walletshift_radar.main as main_mod
        monkeypatch.setattr(main_mod, "get_latest_block", lambda *a: 99_999_999)
        monkeypatch.setattr(main_mod, "get_new_mints", lambda *a, **kw: [])
        # IPFS still unreachable — enrich_token returns _unresolved=True
        monkeypatch.setattr(main_mod, "enrich_token",
                            lambda url, tid, probe=True: {"token_id": tid, "token_uri": "ipfs://Qm...", "_unresolved": True})

        run(db_path=db_path, out_path=str(tmp_path / "out.html"),
            alchemy_key="fake", run_date="2026-06-22",
            full_probe=True, seed_from_walletshift=False)

        c2 = sqlite3.connect(db_path)
        row = c2.execute("SELECT services_json FROM agents WHERE token_id=56").fetchone()
        assert row is not None
        assert row[0] is None, "Unresolved agents must not have services_json set"
        c2.close()
