"""
db.py — SQLite schema and upsert helpers.

All migrations are idempotent (CREATE TABLE IF NOT EXISTS).
No ORM — plain sqlite3 with parameterised queries.
"""
import sqlite3
import json


# ── schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_state (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    token_id        INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL,
    ens             TEXT,
    category        TEXT,
    label           TEXT,
    owner           TEXT,
    kind            TEXT,
    registry        TEXT,
    network         TEXT,
    reg_date        TEXT,
    cluster_key     TEXT,
    first_seen      TEXT,
    last_seen       TEXT,
    is_active       INTEGER DEFAULT 1,
    source          TEXT    DEFAULT 'onchain',
    token_uri       TEXT,
    description     TEXT,
    unresolved      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS snapshots (
    token_id        INTEGER NOT NULL,
    snapshot_date   TEXT    NOT NULL,
    skills_count    INTEGER,
    live_count      INTEGER,
    dead_count      INTEGER,
    paywalled_count INTEGER,
    endpoint_count  INTEGER,
    x402            INTEGER,
    protos_json     TEXT,
    summary_hash    TEXT,
    PRIMARY KEY (token_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS directory_stats (
    snapshot_date   TEXT PRIMARY KEY,
    total_agents    INTEGER,
    distinct_products INTEGER,
    live_skills_read  INTEGER,
    x402_count      INTEGER,
    category_count  INTEGER
);

CREATE TABLE IF NOT EXISTS category_stats (
    snapshot_date   TEXT    NOT NULL,
    category        TEXT    NOT NULL,
    count           INTEGER,
    distinct_products INTEGER,
    PRIMARY KEY (snapshot_date, category)
);

CREATE TABLE IF NOT EXISTS cluster_stats (
    snapshot_date   TEXT    NOT NULL,
    cluster_key     TEXT    NOT NULL,
    member_count    INTEGER,
    PRIMARY KEY (snapshot_date, cluster_key)
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date      TEXT    NOT NULL,
    type            TEXT    NOT NULL,
    token_id        INTEGER,
    cluster_key     TEXT,
    detail_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_date ON snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_events_date    ON events(event_date);
CREATE INDEX IF NOT EXISTS idx_agents_cluster ON agents(cluster_key);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes (idempotent)."""
    conn.executescript(_SCHEMA)
    conn.commit()


_REPUTATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS reputation_events (
    chain_id          INTEGER NOT NULL,
    block_number      INTEGER NOT NULL,
    tx_hash           TEXT    NOT NULL,
    log_index         INTEGER NOT NULL,
    agent_id          INTEGER NOT NULL,
    client            TEXT    NOT NULL,
    feedback_uri_hash TEXT    NOT NULL,
    score             REAL,
    PRIMARY KEY (chain_id, tx_hash, log_index)
);

CREATE TABLE IF NOT EXISTS agent_reputation (
    chain_id         INTEGER NOT NULL,
    agent_id         INTEGER NOT NULL,
    unique_reviewers INTEGER NOT NULL DEFAULT 0,
    avg_score        REAL,
    min_score        REAL,
    max_score        REAL,
    sybil_flag       INTEGER NOT NULL DEFAULT 0,
    last_block       INTEGER,
    last_updated     TEXT,
    PRIMARY KEY (chain_id, agent_id)
);

CREATE TABLE IF NOT EXISTS sybil_collisions (
    chain_id          INTEGER NOT NULL,
    feedback_uri_hash TEXT    NOT NULL,
    event_count       INTEGER NOT NULL,
    distinct_clients  INTEGER NOT NULL,
    distinct_agents   INTEGER NOT NULL,
    last_updated      TEXT,
    PRIMARY KEY (chain_id, feedback_uri_hash)
);

CREATE INDEX IF NOT EXISTS idx_rep_events_agent
    ON reputation_events(chain_id, agent_id);
CREATE INDEX IF NOT EXISTS idx_rep_events_hash
    ON reputation_events(chain_id, feedback_uri_hash);
"""


def migrate_reputation_schema(conn: sqlite3.Connection) -> None:
    """Add reputation tables to an existing walletshift.db (idempotent)."""
    conn.executescript(_REPUTATION_SCHEMA)
    conn.commit()


# ── upsert helpers ────────────────────────────────────────────────────────────

def upsert_agent(conn: sqlite3.Connection, agent: dict,
                 cluster_key: str, snapshot_date: str,
                 source: str = "walletshift") -> None:
    """
    Insert or update an agent row.

    Accepts both walletshift API dicts (key 'id') and on-chain enriched dicts
    (key 'token_id').  On-chain agents carry token_uri, description, unresolved.
    """
    tid = agent.get("id") or agent.get("token_id")
    if tid is None:
        return

    existing = conn.execute(
        "SELECT first_seen FROM agents WHERE token_id=?", (tid,)
    ).fetchone()
    first_seen = existing["first_seen"] if existing else snapshot_date

    conn.execute("""
        INSERT INTO agents
            (token_id, name, ens, category, label, owner, kind,
             registry, network, reg_date, cluster_key, first_seen, last_seen,
             is_active, source, token_uri, description, unresolved)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)
        ON CONFLICT(token_id) DO UPDATE SET
            name=excluded.name,
            ens=excluded.ens,
            category=COALESCE(excluded.category, category),
            label=COALESCE(excluded.label, label),
            owner=COALESCE(excluded.owner, owner),
            kind=COALESCE(excluded.kind, kind),
            registry=COALESCE(excluded.registry, registry),
            network=COALESCE(excluded.network, network),
            reg_date=COALESCE(excluded.reg_date, reg_date),
            cluster_key=excluded.cluster_key,
            last_seen=excluded.last_seen,
            is_active=1,
            source=excluded.source,
            token_uri=COALESCE(excluded.token_uri, token_uri),
            description=COALESCE(excluded.description, description),
            unresolved=excluded.unresolved
    """, (
        tid,
        agent.get("name") or f"Agent #{tid}",
        agent.get("ens"),
        agent.get("category"),
        agent.get("label"),
        agent.get("owner"),
        agent.get("kind"),
        agent.get("registry"),
        agent.get("network"),
        agent.get("reg") or agent.get("reg_date"),
        cluster_key,
        first_seen,
        snapshot_date,
        source,
        agent.get("token_uri"),
        agent.get("description") or agent.get("descr"),
        1 if agent.get("_unresolved") else 0,
    ))
    conn.commit()


def get_scan_state(conn: sqlite3.Connection, key: str,
                   default: str | None = None) -> str | None:
    row = conn.execute(
        "SELECT value FROM scan_state WHERE key=?", (key,)
    ).fetchone()
    return row[0] if row else default


def set_scan_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("""
        INSERT INTO scan_state (key, value) VALUES (?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, value))
    conn.commit()


def get_unresolved_token_ids(conn: sqlite3.Connection,
                              before_date: str | None = None) -> list:
    """
    Return token_ids whose IPFS metadata couldn't be fetched in a previous run.

    Pass before_date=run_date to exclude tokens first seen today — no point
    retrying IPFS failures that just happened seconds ago.
    """
    if before_date:
        rows = conn.execute(
            "SELECT token_id FROM agents WHERE unresolved=1 AND first_seen < ?",
            (before_date,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT token_id FROM agents WHERE unresolved=1"
        ).fetchall()
    return [r[0] for r in rows]


def upsert_snapshot(conn: sqlite3.Connection, snapshot_date: str,
                    token_id: int, metrics: dict) -> None:
    """Insert or replace a daily snapshot row for one agent."""
    conn.execute("""
        INSERT INTO snapshots
            (token_id, snapshot_date, skills_count, live_count, dead_count,
             paywalled_count, endpoint_count, x402, protos_json, summary_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(token_id, snapshot_date) DO UPDATE SET
            skills_count=excluded.skills_count,
            live_count=excluded.live_count,
            dead_count=excluded.dead_count,
            paywalled_count=excluded.paywalled_count,
            endpoint_count=excluded.endpoint_count,
            x402=excluded.x402,
            protos_json=excluded.protos_json,
            summary_hash=excluded.summary_hash
    """, (
        token_id,
        snapshot_date,
        metrics.get("skills_count"),
        metrics.get("live_count"),
        metrics.get("dead_count"),
        metrics.get("paywalled_count"),
        metrics.get("endpoint_count"),
        1 if metrics.get("x402") else 0,
        metrics.get("protos_json"),
        metrics.get("summary_hash"),
    ))
    conn.commit()


def upsert_directory_stats(conn: sqlite3.Connection, snapshot_date: str,
                           stats: dict) -> None:
    """Insert or replace a daily directory-level summary row."""
    conn.execute("""
        INSERT INTO directory_stats
            (snapshot_date, total_agents, distinct_products,
             live_skills_read, x402_count, category_count)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(snapshot_date) DO UPDATE SET
            total_agents=excluded.total_agents,
            distinct_products=excluded.distinct_products,
            live_skills_read=excluded.live_skills_read,
            x402_count=excluded.x402_count,
            category_count=excluded.category_count
    """, (
        snapshot_date,
        stats.get("total_agents"),
        stats.get("distinct_products"),
        stats.get("live_skills_read"),
        stats.get("x402_count"),
        stats.get("category_count"),
    ))
    conn.commit()


def upsert_category_stats(conn: sqlite3.Connection, snapshot_date: str,
                          category: str, count: int,
                          distinct_products: int) -> None:
    conn.execute("""
        INSERT INTO category_stats (snapshot_date, category, count, distinct_products)
        VALUES (?,?,?,?)
        ON CONFLICT(snapshot_date, category) DO UPDATE SET
            count=excluded.count,
            distinct_products=excluded.distinct_products
    """, (snapshot_date, category, count, distinct_products))
    conn.commit()


def upsert_cluster_stats(conn: sqlite3.Connection, snapshot_date: str,
                         cluster_key: str, member_count: int) -> None:
    conn.execute("""
        INSERT INTO cluster_stats (snapshot_date, cluster_key, member_count)
        VALUES (?,?,?)
        ON CONFLICT(snapshot_date, cluster_key) DO UPDATE SET
            member_count=excluded.member_count
    """, (snapshot_date, cluster_key, member_count))
    conn.commit()


def insert_event(conn: sqlite3.Connection, event_date: str, event_type: str,
                 token_id: int | None, cluster_key: str | None,
                 detail: dict) -> None:
    conn.execute("""
        INSERT INTO events (event_date, type, token_id, cluster_key, detail_json)
        VALUES (?,?,?,?,?)
    """, (event_date, event_type, token_id, cluster_key, json.dumps(detail)))
    conn.commit()


# ── query helpers ─────────────────────────────────────────────────────────────

def get_snapshot_dict(conn: sqlite3.Connection, snapshot_date: str) -> dict:
    """Return {token_id: metrics_dict} for all agents on a given date."""
    rows = conn.execute("""
        SELECT token_id, skills_count, live_count, dead_count,
               paywalled_count, endpoint_count, x402, protos_json
        FROM snapshots WHERE snapshot_date=?
    """, (snapshot_date,)).fetchall()
    result = {}
    for row in rows:
        result[row["token_id"]] = {
            "skills_count":    row["skills_count"],
            "live_count":      row["live_count"],
            "dead_count":      row["dead_count"],
            "paywalled_count": row["paywalled_count"],
            "endpoint_count":  row["endpoint_count"],
            "x402":            bool(row["x402"]),
            "protos_json":     row["protos_json"],
        }
    return result


def get_prev_snapshot_date(conn: sqlite3.Connection,
                           current_date: str) -> str | None:
    """Return the most recent snapshot_date before current_date, or None."""
    row = conn.execute("""
        SELECT snapshot_date FROM snapshots
        WHERE snapshot_date < ?
        ORDER BY snapshot_date DESC LIMIT 1
    """, (current_date,)).fetchone()
    return row["snapshot_date"] if row else None


def get_history(conn: sqlite3.Connection, column: str,
                table: str = "directory_stats",
                limit: int = 30) -> list:
    """Return last `limit` values of `column` from `table`, oldest-first."""
    rows = conn.execute(f"""
        SELECT {column} FROM {table}
        ORDER BY snapshot_date DESC LIMIT ?
    """, (limit,)).fetchall()
    return [r[0] for r in reversed(rows)]
