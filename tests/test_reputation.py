"""
Tests for reputation.py — schema migration, event decoding, and aggregation.

All tests use in-memory SQLite so no filesystem side-effects.
Decode tests use synthetic hex payloads constructed from the NewFeedback ABI.
Aggregation tests insert known rows and verify SQL logic directly.
"""
import sqlite3
import pytest

from walletshift_radar.db import init_db, migrate_reputation_schema
from walletshift_radar.reputation import (
    decode_feedback_event,
    upsert_reputation_event,
    recompute_agent_reputation,
    recompute_sybil_collisions,
)

# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    """In-memory DB with core schema + reputation schema applied."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    migrate_reputation_schema(c)
    yield c
    c.close()


def _make_log(agent_id: int, client: str, feedback_uri_hash: str,
              score_word: str, decimals_word: str,
              block_number: int = 100, tx_hash: str = "0xabc", log_index: int = 0) -> dict:
    """Build a synthetic eth_getLogs NewFeedback entry."""
    agent_hex = hex(agent_id)[2:].zfill(64)
    client_padded = "000000000000000000000000" + client[2:].lower()
    data = "0x" + score_word + decimals_word
    return {
        "topics": [
            "0x6a4a61743519c9d648a14e6493f47dbe3ff1aa29e7785c96c8326a205e58febc",
            "0x" + agent_hex,
            "0x" + client_padded,
            feedback_uri_hash,
        ],
        "data": data,
        "blockNumber": hex(block_number),
        "transactionHash": tx_hash,
        "logIndex": hex(log_index),
    }

# 95 with 0 decimals
_SCORE_95   = "000000000000000000000000000000000000000000000000000000000000005f"
_SCORE_9500 = "000000000000000000000000000000000000000000000000000000000000251c"
_DEC_0      = "0000000000000000000000000000000000000000000000000000000000000000"
_DEC_2      = "0000000000000000000000000000000000000000000000000000000000000002"
_NEGATIVE   = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

_CLIENT_A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_CLIENT_B = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_CLIENT_C = "0xcccccccccccccccccccccccccccccccccccccccc"
_HASH_1   = "0x1111111111111111111111111111111111111111111111111111111111111111"
_HASH_2   = "0x2222222222222222222222222222222222222222222222222222222222222222"

# ── Phase 1: schema migration ─────────────────────────────────────────────────

class TestReputationSchema:
    def test_reputation_events_table_is_created(self, conn):
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "reputation_events" in tables

    def test_agent_reputation_table_is_created(self, conn):
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "agent_reputation" in tables

    def test_sybil_collisions_table_is_created(self, conn):
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "sybil_collisions" in tables

    def test_migrate_reputation_schema_is_idempotent(self, conn):
        """Calling migrate twice must not raise or corrupt data."""
        migrate_reputation_schema(conn)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "reputation_events" in tables

    def test_reputation_events_has_required_columns(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(reputation_events)")}
        assert cols >= {"chain_id", "agent_id", "client", "feedback_uri_hash",
                        "score", "block_number", "tx_hash", "log_index"}

    def test_agent_reputation_has_required_columns(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_reputation)")}
        assert cols >= {"chain_id", "agent_id", "unique_reviewers",
                        "avg_score", "sybil_flag", "last_block"}

    def test_sybil_collisions_has_required_columns(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sybil_collisions)")}
        assert cols >= {"chain_id", "feedback_uri_hash",
                        "event_count", "distinct_clients", "distinct_agents"}


# ── Phase 2: event decoding ───────────────────────────────────────────────────

class TestDecodeFeedbackEvent:
    def test_extracts_agent_id(self):
        log = _make_log(42, _CLIENT_A, _HASH_1, _SCORE_95, _DEC_0)
        result = decode_feedback_event(log)
        assert result["agent_id"] == 42

    def test_extracts_client_address(self):
        log = _make_log(1, _CLIENT_A, _HASH_1, _SCORE_95, _DEC_0)
        result = decode_feedback_event(log)
        assert result["client"] == _CLIENT_A

    def test_extracts_feedback_uri_hash(self):
        log = _make_log(1, _CLIENT_A, _HASH_1, _SCORE_95, _DEC_0)
        result = decode_feedback_event(log)
        assert result["feedback_uri_hash"] == _HASH_1

    def test_score_with_zero_decimals(self):
        log = _make_log(1, _CLIENT_A, _HASH_1, _SCORE_95, _DEC_0)
        result = decode_feedback_event(log)
        assert result["score"] == pytest.approx(95.0)

    def test_score_with_nonzero_decimals(self):
        """9500 with 2 decimals → 95.0"""
        log = _make_log(1, _CLIENT_A, _HASH_1, _SCORE_9500, _DEC_2)
        result = decode_feedback_event(log)
        assert result["score"] == pytest.approx(95.0)

    def test_negative_encoded_score_is_none(self):
        """Scores whose first hex nibble is 'f' are two's-complement negatives — skip."""
        log = _make_log(1, _CLIENT_A, _HASH_1, _NEGATIVE, _DEC_0)
        result = decode_feedback_event(log)
        assert result["score"] is None

    def test_negative_score_nibble_8_is_none(self):
        """First nibble '8' is also a two's-complement negative int256 — must return None."""
        score_8 = "8" + "0" * 63   # 0x8000...0000 — the most-negative int256
        log = _make_log(1, _CLIENT_A, _HASH_1, score_8, _DEC_0)
        result = decode_feedback_event(log)
        assert result["score"] is None

    def test_extracts_block_number(self):
        log = _make_log(1, _CLIENT_A, _HASH_1, _SCORE_95, _DEC_0, block_number=999)
        result = decode_feedback_event(log)
        assert result["block_number"] == 999

    def test_extracts_tx_hash(self):
        log = _make_log(1, _CLIENT_A, _HASH_1, _SCORE_95, _DEC_0, tx_hash="0xdeadbeef")
        result = decode_feedback_event(log)
        assert result["tx_hash"] == "0xdeadbeef"


# ── Phase 2: upsert idempotency ───────────────────────────────────────────────

class TestUpsertReputationEvent:
    def test_inserts_event(self, conn):
        event = decode_feedback_event(
            _make_log(42, _CLIENT_A, _HASH_1, _SCORE_95, _DEC_0, tx_hash="0xaaa", log_index=0)
        )
        upsert_reputation_event(conn, chain_id=1, event=event)
        count = conn.execute("SELECT COUNT(*) FROM reputation_events").fetchone()[0]
        assert count == 1

    def test_is_idempotent(self, conn):
        """Inserting the same (chain_id, tx_hash, log_index) twice must not duplicate."""
        event = decode_feedback_event(
            _make_log(42, _CLIENT_A, _HASH_1, _SCORE_95, _DEC_0, tx_hash="0xaaa", log_index=0)
        )
        upsert_reputation_event(conn, chain_id=1, event=event)
        upsert_reputation_event(conn, chain_id=1, event=event)
        count = conn.execute("SELECT COUNT(*) FROM reputation_events").fetchone()[0]
        assert count == 1


# ── Phase 2: aggregation ──────────────────────────────────────────────────────

class TestRecomputeAgentReputation:
    def _insert(self, conn, agent_id, client, feedback_uri_hash, score,
                chain_id=1, tx_hash="0xabc", log_index=0):
        event = decode_feedback_event(
            _make_log(agent_id, client, feedback_uri_hash,
                      _SCORE_95, _DEC_0, tx_hash=tx_hash, log_index=log_index)
        )
        event["score"] = score
        upsert_reputation_event(conn, chain_id=chain_id, event=event)

    def test_counts_unique_reviewers(self, conn):
        self._insert(conn, 1, _CLIENT_A, _HASH_1, 80.0, tx_hash="0x01")
        self._insert(conn, 1, _CLIENT_B, _HASH_1, 90.0, tx_hash="0x02")
        self._insert(conn, 1, _CLIENT_C, _HASH_1, 70.0, tx_hash="0x03")
        recompute_agent_reputation(conn)
        row = conn.execute(
            "SELECT unique_reviewers FROM agent_reputation WHERE chain_id=1 AND agent_id=1"
        ).fetchone()
        assert row["unique_reviewers"] == 3

    def test_computes_avg_score(self, conn):
        self._insert(conn, 1, _CLIENT_A, _HASH_1, 80.0, tx_hash="0x01")
        self._insert(conn, 1, _CLIENT_B, _HASH_1, 100.0, tx_hash="0x02")
        recompute_agent_reputation(conn)
        row = conn.execute(
            "SELECT avg_score FROM agent_reputation WHERE chain_id=1 AND agent_id=1"
        ).fetchone()
        assert row["avg_score"] == pytest.approx(90.0)

    def test_sybil_flag_set_when_reviewer_used_colliding_hash(self, conn):
        """
        If a reviewer's feedback_uri_hash was also used by other clients targeting
        other agents (i.e. it's a sybil_collision), the agent gets sybil_flag=1.
        Agent 1 reviewed by CLIENT_A using HASH_1.
        Agent 2 reviewed by CLIENT_B using HASH_1.
        HASH_1 is therefore a collision → both agents get sybil_flag=1.
        """
        # Agent 1: reviewed by CLIENT_A with HASH_1
        self._insert(conn, 1, _CLIENT_A, _HASH_1, 90.0, tx_hash="0x01", log_index=0)
        # Agent 2: reviewed by CLIENT_B with same HASH_1
        self._insert(conn, 2, _CLIENT_B, _HASH_1, 90.0, tx_hash="0x02", log_index=0)
        recompute_sybil_collisions(conn)
        recompute_agent_reputation(conn)
        row = conn.execute(
            "SELECT sybil_flag FROM agent_reputation WHERE chain_id=1 AND agent_id=1"
        ).fetchone()
        assert row["sybil_flag"] == 1

    def test_sybil_flag_not_set_when_hash_unique(self, conn):
        """An agent whose reviewers use unique hashes is not Sybil-flagged."""
        self._insert(conn, 1, _CLIENT_A, _HASH_1, 90.0, tx_hash="0x01")
        self._insert(conn, 1, _CLIENT_B, _HASH_2, 85.0, tx_hash="0x02")
        recompute_sybil_collisions(conn)
        recompute_agent_reputation(conn)
        row = conn.execute(
            "SELECT sybil_flag FROM agent_reputation WHERE chain_id=1 AND agent_id=1"
        ).fetchone()
        assert row["sybil_flag"] == 0

    def test_null_scores_excluded_from_avg(self, conn):
        self._insert(conn, 1, _CLIENT_A, _HASH_1, 80.0, tx_hash="0x01")
        self._insert(conn, 1, _CLIENT_B, _HASH_2, None, tx_hash="0x02")
        recompute_agent_reputation(conn)
        row = conn.execute(
            "SELECT avg_score, unique_reviewers FROM agent_reputation WHERE chain_id=1 AND agent_id=1"
        ).fetchone()
        assert row["avg_score"] == pytest.approx(80.0)
        assert row["unique_reviewers"] == 2


class TestRecomputeSybilCollisions:
    def _insert(self, conn, agent_id, client, feedback_uri_hash,
                chain_id=1, tx_hash="0xabc", log_index=0):
        event = decode_feedback_event(
            _make_log(agent_id, client, feedback_uri_hash,
                      _SCORE_95, _DEC_0, tx_hash=tx_hash, log_index=log_index)
        )
        upsert_reputation_event(conn, chain_id=chain_id, event=event)

    def test_finds_coordinated_hash_collision(self, conn):
        """
        3 distinct clients use the same hash targeting 3 distinct agents.
        Should appear in sybil_collisions with event_count=3, distinct_clients=3, distinct_agents=3.
        """
        self._insert(conn, 1, _CLIENT_A, _HASH_1, tx_hash="0x01")
        self._insert(conn, 2, _CLIENT_B, _HASH_1, tx_hash="0x02")
        self._insert(conn, 3, _CLIENT_C, _HASH_1, tx_hash="0x03")
        recompute_sybil_collisions(conn)
        row = conn.execute(
            "SELECT event_count, distinct_clients, distinct_agents "
            "FROM sybil_collisions WHERE chain_id=1 AND feedback_uri_hash=?",
            (_HASH_1,)
        ).fetchone()
        assert row is not None
        assert row["event_count"] == 3
        assert row["distinct_clients"] == 3
        assert row["distinct_agents"] == 3

    def test_unique_hashes_not_in_collisions(self, conn):
        """A hash used by only one (client, agent) pair is not a collision."""
        self._insert(conn, 1, _CLIENT_A, _HASH_1, tx_hash="0x01")
        recompute_sybil_collisions(conn)
        row = conn.execute(
            "SELECT * FROM sybil_collisions WHERE feedback_uri_hash=?", (_HASH_1,)
        ).fetchone()
        assert row is None

    def test_recompute_is_idempotent(self, conn):
        """Calling recompute twice produces the same result, not doubled counts."""
        self._insert(conn, 1, _CLIENT_A, _HASH_1, tx_hash="0x01")
        self._insert(conn, 2, _CLIENT_B, _HASH_1, tx_hash="0x02")
        recompute_sybil_collisions(conn)
        recompute_sybil_collisions(conn)
        count = conn.execute(
            "SELECT COUNT(*) FROM sybil_collisions WHERE feedback_uri_hash=?", (_HASH_1,)
        ).fetchone()[0]
        assert count == 1
