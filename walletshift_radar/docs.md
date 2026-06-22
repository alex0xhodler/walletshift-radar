# Noridoc: walletshift_radar (Python package)

Path: @/walletshift-radar/walletshift_radar

### Overview

- Core Python package for WalletShift Radar: a daily pipeline that indexes the ERC-8004 on-chain agent economy into a local SQLite database and renders a HTML dashboard.
- Two distinct data planes live here: the behavioral plane (daily health probes, snapshot deltas, cluster detection) and the reputation plane (on-chain `NewFeedback` event indexing, sybil detection). They write to the same `walletshift.db` but run independently.
- Sigmatic (`@/sigmatic`) reads this database as its sole data source; `walletshift_radar` never reads from Sigmatic.

### How it fits into the larger codebase

```
ERC-8004 Registry (on-chain)
        │
        ├─ main.py --alchemy KEY   ──► scan.py / fetch.py / analyze.py
        │   │                                  │
        │   │   Step 6 --full-probe:            │
        │   │   ┌─ agents.services_json set? ──►│
        │   │   │  YES → fast path             walletshift.db
        │   │   │         probe_agent_endpoints (behavioral tables)
        │   │   │         (skip chain + IPFS)          │
        │   │   └─ NO  → slow path                     │
        │   │            enrich_token()                 │
        │   │            (chain → IPFS → probe)         │
        │   │            → cache services_json ─────────┘
        │   │
        └─ main.py --reputation    ──► reputation.py
                                               │
                                          walletshift.db (reputation tables)

walletshift.db ──► sigmatic/queries.py   (read-only by Sigmatic API)
walletshift.db ──► render.py             (produces dashboard.html)
walletshift.db ──► build_web.py          (produces Vercel marketing site)
```

- `main.py` is the single entry point for both the behavioral pipeline (`run()`) and the reputation indexer (`_run_reputation_scan()`). The `--reputation` flag routes to the latter and returns immediately without running the behavioral pipeline.
- `db.py` owns the schema for both planes. The behavioral schema is initialized via `init_db(conn)` and `migrate_services_cache(conn)` on every run; the reputation schema is added lazily via `migrate_reputation_schema(conn)` (called by `--reputation` and by Sigmatic's test `conftest.py` before any test touches the DB).
- `analyze.py`'s `momentum_score()` is imported directly by `@/sigmatic/sigmatic/queries.py` to keep scoring consistent between the dashboard and the API without duplicating the formula.

### Core Implementation

- **Behavioral pipeline** (`main.py run()`): 10-step daily orchestrator — determine scan range from persisted `last_scanned_block`, find new mints via Alchemy `getAssetTransfers`, enrich each token (tokenURI → IPFS → health probe), snapshot all active agents, compute cluster/directory stats, emit delta events, persist scan cursor, render dashboard. IPFS failures are recorded as `unresolved=1` and retried on subsequent runs.
- **Two-path full re-probe** (Step 6, triggered by `--full-probe`): agents whose `services_json` is populated take the **fast path** — endpoint URLs are read directly from the DB and passed to `probe_agent_endpoints()`, skipping chain RPC and IPFS entirely. Agents without `services_json` take the **slow path** — `enrich_token()` runs the full chain → IPFS → HTTP probe chain, and on success the result is written back to `services_json` for future fast-path eligibility.
- **`services_json` population sources**: WalletShift-seeded agents get it from the WalletShift API's `endpoints[]` on seed (converted by `_ws_endpoints_to_services()`). On-chain agents get it from IPFS metadata on first successful `enrich_token()` resolution, in Steps 3, 4, or 6 (slow path). Health probe results are always stripped via `_services_without_health()` before writing — only endpoint URLs and names are persisted.
- **Schema migration** (`migrate_services_cache()`): adds `services_json TEXT` to an existing `agents` table via `ALTER TABLE` if the column is absent (checked via `PRAGMA table_info`). Called automatically at the start of every `run()`, before any agent reads or writes.
- **Reputation indexer** (`main.py _run_reputation_scan()`): scans `NewFeedback` events across 4 chains using `reputation.scan_chain()`, then calls `recompute_sybil_collisions()` and `recompute_agent_reputation()` to rebuild the two aggregate tables from scratch. BSC and Arc scans are wrapped in try/except and skipped gracefully if the RPC doesn't support `eth_getLogs`.
- **`reputation.scan_chain()`**: resumes from `MAX(block_number)` per chain (never re-scans), uses per-chain chunk sizes (25K mainnet, 2K Base/BSC, 1K Arc), and exponential-backoff retry. The `User-Agent: python-httpx/0.27.0` header is required for PublicNode — it blocks the default Python UA.
- **ABI decoding** (`decode_feedback_event()`): extracts `agent_id` and `client` from indexed topics 1/2, `feedback_uri_hash` from topic 3, and decodes `score`/`decimals` from the first two ABI words of `data`. A leading `f` nibble in the score word signals a negative int256 (artifact on Arc testnet); these decode as `score=None`. `decimals > 18` also coerces to `None`.
- **Sybil detection** (`recompute_sybil_collisions()`): a `feedback_uri_hash` used by more than one `(client, agent_id)` pair is a collision. The query groups by `(chain_id, feedback_uri_hash)` and filters with `HAVING COUNT(DISTINCT client) > 1 OR COUNT(DISTINCT agent_id) > 1`. Collisions populate `sybil_collisions` before `recompute_agent_reputation()` runs so the LEFT JOIN can set `sybil_flag=1` on any agent touched by a colliding hash.
- **Schema separation**: `init_db()` creates the behavioral tables (including `services_json` in the `agents` schema); `migrate_services_cache()` adds it to pre-existing DBs; `migrate_reputation_schema()` creates the three reputation tables. All are idempotent.

### Things to Know

- **`services_json` format**: `[{"endpoint": "<url>", "name": "<label>"}, ...]`. Health probe results (the `"health"` key) are always stripped before storage — `services_json` is endpoint-location-only, not a health cache.
- **`skills_count` on fast path**: IPFS is the only source of per-endpoint tool lists. When the fast path runs, it cannot recount tools, so `skills_count` is carried forward from the most recent prior snapshot. `protos_json` is similarly inherited unless it can be inferred from service names.
- **`upsert_agent` COALESCE guard**: the `ON CONFLICT` clause uses `COALESCE(excluded.services_json, services_json)` — passing `services_json=None` leaves any existing cached value intact. A non-None value always overwrites. This means partial updates (e.g., updating name only) never accidentally clear the cache.
- **RPC strategy per chain**: Mainnet uses PublicNode (free, 50K-block `eth_getLogs` range; overridable via `MAINNET_ETH_LOGS_RPC`). Base uses the official public RPC. BSC has no reliable free RPC with `eth_getLogs` support — defaults to `bsc-rpc.publicnode.com` but is designed to be overridden via `BSC_RPC_URL`; scan failure is non-fatal. Arc testnet uses Circle's public RPC. Alchemy (used for the behavioral pipeline) was rejected for reputation indexing because its free tier limits `eth_getLogs` to 10-block ranges.
- **First-run optimization**: `genesis_block` skips empty pre-deployment history (mainnet starts at block 24M, BSC at 55M). Arc testnet uses `lookback_blocks=500_000` instead of scanning from genesis because its dense recent block activity would otherwise take impractically long.
- **Aggregate tables are fully rebuilt each scan**: `recompute_sybil_collisions()` and `recompute_agent_reputation()` both start with `DELETE FROM ...` before re-inserting. This means sybil state always reflects the complete indexed event set, not incremental patches.
- **`reputation_events` primary key** is `(chain_id, tx_hash, log_index)`. The upsert only updates `score` on conflict — agent/client/hash fields are immutable once written.
- **The `--reputation` flag is a separate CLI mode** — it does not interact with `--alchemy`, snapshot tables, or the dashboard render. Running `--reputation` and the daily behavioral pipeline together requires two separate invocations.
- **`walletshift_radar.analyze.momentum_score`** is a shared dependency: both this package's dashboard and Sigmatic's API import it. Any change to the scoring formula propagates to both consumers simultaneously.

Created and maintained by Nori.
