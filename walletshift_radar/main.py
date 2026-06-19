"""
main.py — daily orchestrator (on-chain primary, walletshift one-time seed).

Pipeline:
  1. Get last_scanned_block from DB  ← persisted; never re-scans old blocks
  2. alchemy_getAssetTransfers from_block→latest for new mints on the registry
  3. For each new token_id: tokenURI → resolve metadata → filter real services → probe health
  4. Retry any previously unresolved tokens (IPFS was down last time)
  5. Snapshot directory stats + cluster stats
  6. Delta analysis → events → dashboard render
  7. Persist last_scanned_block = current_latest

First-time setup (run once):
  python3 -m walletshift_radar.main --alchemy KEY --seed-from-walletshift
    → pulls walletshift API (711 pre-filtered agents, fast) as historical seed
      and sets last_scanned_block to the June 13 cutoff block so the next
      daily run only scans genuinely new on-chain mints.

Subsequent daily runs:
  python3 -m walletshift_radar.main --alchemy KEY
    → scans only NEW blocks since last run, enriches only new agents.
    → always catches up from last successful scan if the machine was off.

Options:
  --db PATH                  SQLite file (default: walletshift.db)
  --out PATH                 HTML output (default: dashboard.html)
  --alchemy KEY              Alchemy API key (or set ALCHEMY_KEY env var)
  --date DATE                Override run date YYYY-MM-DD (testing)
  --seed-from-walletshift    One-time historical seed from walletshift API
  --full-probe               Re-probe ALL active agents' endpoints (weekly use)
"""
import argparse
import json
import os
import sqlite3
import time
from datetime import date

from .scan     import (get_latest_block, get_new_mints, enrich_token,
                       get_token_uri, resolve_metadata, probe_agent_endpoints,
                       is_real_service_agent, _infer_protos)
from .cluster  import cluster_key, group_by_cluster
from .analyze  import compute_health_counts, compute_deltas, momentum_score
from .db       import (
    init_db, upsert_agent, upsert_snapshot, upsert_directory_stats,
    upsert_category_stats, upsert_cluster_stats, insert_event,
    get_snapshot_dict, get_prev_snapshot_date, get_history,
    get_scan_state, set_scan_state, get_unresolved_token_ids,
)
from .render   import render_dashboard

# Block just before walletshift's June-13 2026 snapshot cutoff.
# New installs that don't use --seed-from-walletshift will start scanning here,
# picking up only agents registered after that date.
# (June 13 ≈ block 25,304,816; we use 25,280,000 for a small safety buffer.)
_DEFAULT_START_BLOCK = 25_280_000

_ALCHEMY_BASE = "https://eth-mainnet.g.alchemy.com/v2/"
# publicnode.com returns 403 for eth_getLogs on blocks older than ~48h.
# mevblocker.io provides free public RPC with full historical eth_getLogs.
_PUBLIC_ETH   = "https://rpc.mevblocker.io"


def _resolve_eth_rpc(alchemy_key: str = "", eth_rpc: str = "") -> str:
    """
    Determine the Ethereum mainnet RPC URL to use, in priority order:
      1. --eth-rpc CLI argument (or ETH_MAINNET_RPC_URL env var, resolved by argparse)
      2. --alchemy KEY (legacy; builds https://eth-mainnet.g.alchemy.com/v2/{KEY})
      3. https://ethereum.publicnode.com (public, no auth, 50K block range)
    """
    if eth_rpc:
        return eth_rpc
    if alchemy_key:
        return _ALCHEMY_BASE + alchemy_key
    return _PUBLIC_ETH


# ── snapshot metrics ──────────────────────────────────────────────────────────

def _metrics_from_enriched(enriched: dict) -> dict:
    protos = enriched.get("protos") or []
    return {
        "skills_count":    enriched.get("skills_count") or 0,
        "live_count":      enriched.get("live_count") or 0,
        "dead_count":      enriched.get("dead_count") or 0,
        "paywalled_count": enriched.get("paywalled_count") or 0,
        "endpoint_count":  len(enriched.get("services") or []),
        "x402":            bool(enriched.get("x402")),
        "protos_json":     json.dumps(sorted(protos)),
        "summary_hash":    hex(hash(enriched.get("description") or ""))[-8:],
    }


def _metrics_from_ws_result(result: dict) -> dict:
    endpoints = result.get("endpoints", [])
    counts    = compute_health_counts(endpoints)
    protos    = result.get("protos") or []
    return {
        "skills_count":    result.get("skills_count") or 0,
        "live_count":      counts["live"],
        "dead_count":      counts["dead"],
        "paywalled_count": counts["paywalled"],
        "endpoint_count":  len(endpoints),
        "x402":            bool(result.get("x402")),
        "protos_json":     json.dumps(sorted(protos)),
        "summary_hash":    hex(hash(result.get("summary") or ""))[-8:],
    }


# ── walletshift overlay (optional enrichment) ─────────────────────────────────

def _seed_from_walletshift(conn: sqlite3.Connection, run_date: str,
                            cutoff_block: int) -> None:
    """
    One-time historical seed: pull walletshift's 711 pre-filtered agents,
    store them in the DB, and set last_scanned_block to the cutoff so future
    daily runs only scan genuinely new on-chain mints.

    After this runs once, the pipeline never needs walletshift again.
    """
    from .fetch import fetch_all_search
    print("  [seed] Fetching walletshift index (one-time historical seed) …")
    try:
        all_results, categories, _ = fetch_all_search()
    except Exception as exc:
        raise RuntimeError(f"walletshift seed fetch failed: {exc}") from exc

    print(f"  [seed] Got {len(all_results)} agents from walletshift")
    for r in all_results:
        tid  = r["id"]
        ckey = cluster_key(r["name"])
        # Build an agent dict compatible with upsert_agent
        agent = {
            "token_id":    tid,
            "id":          tid,
            "name":        r.get("name"),
            "ens":         r.get("ens"),
            "category":    r.get("category"),
            "label":       r.get("label"),
            "reg_date":    None,   # walletshift search doesn't return reg date
            "description": r.get("summary"),
        }
        upsert_agent(conn, agent, cluster_key=ckey,
                     snapshot_date=run_date, source="walletshift")
        metrics = _metrics_from_ws_result(r)
        upsert_snapshot(conn, run_date, tid, metrics)

    # Category stats
    for cat in categories:
        upsert_category_stats(conn, run_date, cat["key"],
                              cat.get("count", 0), cat.get("count", 0))

    # Set scan cursor so next run picks up from just after walletshift's cutoff
    set_scan_state(conn, "last_scanned_block", str(cutoff_block))
    print(f"  [seed] last_scanned_block set to {cutoff_block} (≈ June 13 2026)")
    print(f"  [seed] Done — daily on-chain scans will now find only new agents")


# ── reg_histogram ─────────────────────────────────────────────────────────────

def _reg_histogram(conn: sqlite3.Connection) -> list:
    rows = conn.execute("""
        SELECT strftime('%Y-%W', reg_date) AS week, COUNT(*) AS cnt
        FROM agents WHERE reg_date IS NOT NULL
        GROUP BY week ORDER BY week
    """).fetchall()
    return [{"week": row[0], "count": row[1]} for row in rows]


# ── cluster watch ─────────────────────────────────────────────────────────────

def _cluster_watch(conn: sqlite3.Connection, limit: int = 12) -> list:
    keys = conn.execute("""
        SELECT cluster_key, member_count FROM cluster_stats
        WHERE snapshot_date=(SELECT MAX(snapshot_date) FROM cluster_stats)
        ORDER BY member_count DESC LIMIT ?
    """, (limit,)).fetchall()
    result = []
    for row in keys:
        key  = row[0]
        hist = [r[0] for r in conn.execute("""
            SELECT member_count FROM cluster_stats
            WHERE cluster_key=? ORDER BY snapshot_date
        """, (key,)).fetchall()]
        result.append({"cluster_key": key, "history": hist})
    return result


# ── momentum board ────────────────────────────────────────────────────────────

def _momentum_boards(conn: sqlite3.Connection, run_date: str,
                     curr_snap: dict, prev_snap: dict,
                     name_lookup: dict) -> tuple[list, list, list]:
    scores = []
    for tid, metrics in curr_snap.items():
        prev_m = prev_snap.get(tid, {})
        skills_delta = (metrics.get("skills_count") or 0) - (prev_m.get("skills_count") or 0)
        protos = json.loads(metrics.get("protos_json") or "[]")
        agent_data = {
            "skills_delta_7d": skills_delta,
            "live_count":      metrics.get("live_count") or 0,
            "dead_count":      metrics.get("dead_count") or 0,
            "endpoint_count":  metrics.get("endpoint_count") or 0,
            "protos":          protos,
            "x402":            bool(metrics.get("x402")),
        }
        score = momentum_score(agent_data)
        scores.append({
            "id":         tid,
            "name":       name_lookup.get(tid, f"#{tid}"),
            "score":      score,
            "live_count": agent_data["live_count"],
            "dead_count": agent_data["dead_count"],
            "protos":     protos,
            "x402":       agent_data["x402"],
        })

    scores.sort(key=lambda x: x["score"], reverse=True)
    momentum_board = scores[:20]
    deathwatch     = [s for s in reversed(scores) if s["score"] < 0.15][:15]
    return momentum_board, deathwatch, scores


# ── main pipeline ─────────────────────────────────────────────────────────────

def run(db_path: str, out_path: str, alchemy_key: str,
        run_date: str, full_probe: bool, seed_from_walletshift: bool,
        no_chain_scan: bool = False, eth_rpc: str = "") -> None:

    url  = _resolve_eth_rpc(alchemy_key, eth_rpc)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    # ── Step 0 (one-time): seed from walletshift ─────────────────────────────
    if seed_from_walletshift:
        _seed_from_walletshift(conn, run_date, cutoff_block=_DEFAULT_START_BLOCK)
        # After seeding, fall through to also scan the June 13→now gap immediately

    # ── Step 1: determine scan range ─────────────────────────────────────────
    if no_chain_scan:
        latest_block = int(get_scan_state(conn, "last_scanned_block") or _DEFAULT_START_BLOCK)
        new_mints    = []
        print(f"[{run_date}] Chain scan skipped (walletshift-only mode)")
    else:
        latest_block = get_latest_block(url)
        last_scanned = int(get_scan_state(conn, "last_scanned_block") or _DEFAULT_START_BLOCK)
        from_block   = last_scanned + 1
        print(f"[{run_date}] Scanning blocks {from_block} → {latest_block} "
              f"(+{latest_block - from_block:,} blocks)")

        # ── Step 2: find new mints ────────────────────────────────────────────
        if from_block <= latest_block:
            new_mints = get_new_mints(url, from_block, latest_block)
            print(f"  → {len(new_mints)} new mint(s) found")
        else:
            new_mints = []
            print(f"  → already up to date")

    # ── Step 3: enrich new mints (skipped in --no-chain-scan mode) ──────────────
    newly_added  = []
    unresolvable = []

    for mint in new_mints:  # empty list when no_chain_scan=True
        tid = mint["token_id"]
        time.sleep(0.15)
        enriched = enrich_token(url, tid, probe=True)

        if enriched is None:
            # Not a real service agent — skip
            continue
        if enriched.get("_unresolved"):
            # IPFS unreachable — record for retry
            conn.execute("""
                INSERT OR IGNORE INTO agents
                    (token_id, name, source, unresolved, first_seen, last_seen,
                     cluster_key, is_active)
                VALUES (?,?,?,1,?,?,?,1)
            """, (tid, f"Agent #{tid}", "onchain", run_date, run_date, f"Agent #{tid}"))
            conn.commit()
            unresolvable.append(tid)
            continue

        ckey = cluster_key(enriched["name"])
        upsert_agent(conn, enriched, cluster_key=ckey,
                     snapshot_date=run_date, source="onchain")
        metrics = _metrics_from_enriched(enriched)
        upsert_snapshot(conn, run_date, tid, metrics)
        newly_added.append(tid)

    if newly_added:
        print(f"  → {len(newly_added)} real service agents added")
    if unresolvable:
        print(f"  → {len(unresolvable)} tokens unresolvable (IPFS down?) — will retry next run")

    # ── Step 4: retry previously unresolved tokens (skipped in no-chain-scan) ───
    retry_ids = [] if no_chain_scan else get_unresolved_token_ids(conn, before_date=run_date)
    if retry_ids:
        print(f"  Retrying {len(retry_ids)} previously unresolved token(s) …")
    for tid in retry_ids:
        time.sleep(0.15)
        enriched = enrich_token(url, tid, probe=True)
        if enriched and not enriched.get("_unresolved"):
            ckey = cluster_key(enriched["name"])
            upsert_agent(conn, enriched, cluster_key=ckey,
                         snapshot_date=run_date, source="onchain")
            upsert_snapshot(conn, run_date, tid, _metrics_from_enriched(enriched))
            newly_added.append(tid)

    # (walletshift overlay only happens via --seed-from-walletshift on first run)

    # ── Step 6: snapshot ALL active agents that don't yet have today's snapshot
    #    (for the daily health re-probe — only if --full-probe or no ws overlay)
    existing_snap_tids = {
        row[0] for row in conn.execute(
            "SELECT token_id FROM snapshots WHERE snapshot_date=?", (run_date,)
        ).fetchall()
    }
    all_active = conn.execute(
        "SELECT token_id, name, token_uri FROM agents WHERE is_active=1 AND unresolved=0"
    ).fetchall()

    needs_probe = [row for row in all_active
                   if row["token_id"] not in existing_snap_tids]

    if needs_probe and full_probe:
        print(f"  Full re-probe of {len(needs_probe)} agent(s) …")
        for i, row in enumerate(needs_probe):
            tid = row["token_id"]
            enriched = enrich_token(url, tid, probe=True)
            if enriched and not enriched.get("_unresolved"):
                upsert_snapshot(conn, run_date, tid, _metrics_from_enriched(enriched))
            elif not enriched:
                # No longer a real service agent — mark inactive
                conn.execute("UPDATE agents SET is_active=0, last_seen=? WHERE token_id=?",
                             (run_date, tid))
                conn.commit()
            time.sleep(0.12)
            if (i + 1) % 50 == 0:
                print(f"    probed {i+1}/{len(needs_probe)} …")

    # ── Step 7: cluster stats + directory stats ───────────────────────────────
    all_agent_rows = conn.execute(
        "SELECT token_id, name FROM agents WHERE is_active=1"
    ).fetchall()
    name_lookup = {row["token_id"]: row["name"] for row in all_agent_rows}

    # cluster stats
    groups = group_by_cluster([{"id": r["token_id"], "name": r["name"]}
                                for r in all_agent_rows])
    for key, members in groups.items():
        upsert_cluster_stats(conn, run_date, key, len(members))

    curr_snap = get_snapshot_dict(conn, run_date)
    prev_date = get_prev_snapshot_date(conn, run_date)
    prev_snap = get_snapshot_dict(conn, prev_date) if prev_date else {}

    # directory stats
    total_agents      = len(all_agent_rows)
    distinct_products = len(groups)
    live_total        = sum(m.get("live_count") or 0 for m in curr_snap.values())
    x402_count        = sum(1 for m in curr_snap.values() if m.get("x402"))
    chain_only_count  = conn.execute(
        "SELECT COUNT(*) FROM agents WHERE source='onchain' AND is_active=1"
    ).fetchone()[0]
    cat_count = conn.execute(
        "SELECT COUNT(DISTINCT category) FROM agents WHERE is_active=1 AND category IS NOT NULL"
    ).fetchone()[0]

    dir_stats = {
        "total_agents":      total_agents,
        "distinct_products": distinct_products,
        "live_skills_read":  live_total,
        "x402_count":        x402_count,
        "category_count":    cat_count,
        "chain_only":        chain_only_count,
        "last_scanned_block": latest_block,
        "source_note":       f"on-chain ({url})",
    }
    upsert_directory_stats(conn, run_date, dir_stats)

    # ── Step 8: delta events ──────────────────────────────────────────────────
    events = compute_deltas(prev_snap, curr_snap)
    for ev in events:
        tid  = ev.get("token_id")
        ckey = cluster_key(name_lookup.get(tid, f"#{tid}")) if tid else None
        insert_event(conn, run_date, ev["type"], tid, ckey, ev.get("detail", {}))
    if events:
        print(f"  → {len(events)} delta events")

    # ── Step 9: persist scan state ────────────────────────────────────────────
    if not no_chain_scan:
        set_scan_state(conn, "last_scanned_block", str(latest_block))
        set_scan_state(conn, "last_scan_date", run_date)
        print(f"  ✓ last_scanned_block updated to {latest_block}")

    # ── Step 10: render dashboard ─────────────────────────────────────────────
    print("  Rendering dashboard …")

    dir_history_rows = conn.execute("""
        SELECT snapshot_date, total_agents, distinct_products,
               live_skills_read, x402_count
        FROM directory_stats ORDER BY snapshot_date
    """).fetchall()
    dir_history = [dict(r) for r in dir_history_rows]

    # newcomers from events
    newcomer_ids = {row[0] for row in conn.execute(
        "SELECT token_id FROM events WHERE event_date=? AND type='newcomer'",
        (run_date,)
    ).fetchall()}
    newcomers = []
    for tid in newcomer_ids:
        a = conn.execute(
            "SELECT token_id, name, category, source FROM agents WHERE token_id=?", (tid,)
        ).fetchone()
        m = curr_snap.get(tid, {})
        if a:
            newcomers.append({
                "id":           a["token_id"],
                "name":         a["name"],
                "category":     a["category"] or "—",
                "skills_count": m.get("skills_count"),
                "live_count":   m.get("live_count"),
                "dead_count":   m.get("dead_count"),
                "protos":       json.loads(m.get("protos_json") or "[]"),
                "source":       a["source"],
            })

    # dropouts
    dropout_rows = conn.execute("""
        SELECT e.token_id, a.name FROM events e
        JOIN agents a ON e.token_id=a.token_id
        WHERE e.event_date=? AND e.type='dropout'
    """, (run_date,)).fetchall()
    dropouts = [{"id": r[0], "name": r[1]} for r in dropout_rows]

    # health flips
    flip_rows = conn.execute("""
        SELECT e.token_id, a.name, e.detail_json FROM events e
        JOIN agents a ON e.token_id=a.token_id
        WHERE e.event_date=? AND e.type='health_flip_dead'
    """, (run_date,)).fetchall()
    health_flips_dead = [
        {"id": r[0], "name": r[1], **json.loads(r[2])} for r in flip_rows
    ]

    # skills movers
    mover_rows = conn.execute("""
        SELECT e.token_id, a.name, e.type, e.detail_json FROM events e
        JOIN agents a ON e.token_id=a.token_id
        WHERE e.event_date=? AND e.type IN ('skills_up','skills_down')
        ORDER BY json_extract(e.detail_json,'$.delta') DESC
    """, (run_date,)).fetchall()
    skills_up, skills_down = [], []
    for r in mover_rows:
        d     = json.loads(r[3])
        entry = {"id": r[0], "name": r[1], "delta": d.get("delta", 0),
                 "skills_count": curr_snap.get(r[0], {}).get("skills_count")}
        (skills_up if r[2] == "skills_up" else skills_down).append(entry)

    # category render
    cat_rows = conn.execute("""
        SELECT cs.category, cs.count, cs.distinct_products,
               a.label
        FROM category_stats cs
        LEFT JOIN agents a ON a.category=cs.category AND a.label IS NOT NULL
        WHERE cs.snapshot_date=?
        GROUP BY cs.category
    """, (run_date,)).fetchall()
    prev_cat = {}
    if prev_date:
        prev_cat = {r[0]: r[1] for r in conn.execute(
            "SELECT category, count FROM category_stats WHERE snapshot_date=?",
            (prev_date,)
        ).fetchall()}
    cat_render = []
    for row in cat_rows:
        cat = row[0]
        cnt = row[1]
        cat_render.append({
            "key":    cat,
            "label":  row[3] or cat,
            "count":  cnt,
            "distinct_products": row[2],
            "delta":  (cnt - prev_cat[cat]) if cat in prev_cat else None,
        })

    # momentum + deathwatch
    momentum_board, deathwatch, all_scores = _momentum_boards(
        conn, run_date, curr_snap, prev_snap, name_lookup
    )

    # ── Chart / explorer data ─────────────────────────────────────────────────
    # Full agent list for interactive category explorer (resolved agents only)
    agent_explorer_rows = conn.execute("""
        SELECT a.token_id, a.name, a.category, a.cluster_key, a.source,
               COALESCE(s.live_count, 0)   live_count,
               COALESCE(s.dead_count, 0)   dead_count,
               COALESCE(s.skills_count, 0) skills_count,
               COALESCE(s.x402, 0)         x402,
               COALESCE(s.protos_json, '[]') protos_json
        FROM agents a
        LEFT JOIN snapshots s ON a.token_id=s.token_id AND s.snapshot_date=?
        WHERE a.is_active=1 AND a.unresolved=0
        ORDER BY a.category NULLS LAST, a.name
    """, (run_date,)).fetchall()
    agents_all = [dict(r) for r in agent_explorer_rows]

    # Inject momentum score into each agent row
    score_lookup = {s["id"]: round(s["score"], 3) for s in all_scores}
    for a in agents_all:
        a["score"] = score_lookup.get(a["token_id"], 0.0)

    # Per-category live/dead breakdown for charts
    cat_chart = [dict(r) for r in conn.execute("""
        SELECT a.category,
               COUNT(*)                         n,
               SUM(COALESCE(s.live_count, 0))   total_live,
               SUM(COALESCE(s.dead_count, 0))   total_dead,
               SUM(COALESCE(s.x402, 0))         x402_n
        FROM agents a
        LEFT JOIN snapshots s ON a.token_id=s.token_id AND s.snapshot_date=?
        WHERE a.is_active=1 AND a.unresolved=0 AND a.category IS NOT NULL
        GROUP BY a.category ORDER BY n DESC
    """, (run_date,)).fetchall()]

    # Multi-instance platform adoption series (token_id distribution = time proxy)
    platform_series = []
    for ckey, members in sorted(groups.items(), key=lambda x: len(x[1]), reverse=True):
        if len(members) < 2:
            break
        ids = sorted([m["id"] for m in members])
        platform_series.append({"name": ckey, "count": len(members), "ids": ids})
        if len(platform_series) >= 6:
            break

    # Protocol distribution (agent count per protocol tag)
    proto_dist = {}
    for row in agents_all:
        for p in json.loads(row.get("protos_json") or "[]"):
            proto_dist[p] = proto_dist.get(p, 0) + 1

    render_data = {
        "directory_history": dir_history,
        "summary":           dir_stats,
        "categories":        cat_render,
        "newcomers":         newcomers,
        "dropouts":          dropouts,
        "health_flips_dead": health_flips_dead,
        "top_skills_up":     sorted(skills_up, key=lambda x: x["delta"], reverse=True),
        "top_skills_down":   sorted(skills_down, key=lambda x: x["delta"], reverse=True),
        "momentum_board":    momentum_board,
        "deathwatch":        deathwatch,
        "cluster_watch":     _cluster_watch(conn),
        "reg_histogram":     _reg_histogram(conn),
        # chart / explorer additions
        "agents_all":        agents_all,
        "cat_chart":         cat_chart,
        "platform_series":   platform_series,
        "proto_dist":        proto_dist,
    }

    html = render_dashboard(render_data, run_date)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    conn.close()
    print(f"  ✓ dashboard → {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _run_reputation_scan(db_path: str, alchemy_key: str) -> None:
    """
    Index NewFeedback events on all monitored chains and rebuild aggregates.

    Chains scanned (alchemy_key retained for future paid-plan upgrades):
      1       Ethereum Mainnet  — PublicNode (50K-block chunks, no key needed)
      8453    Base              — public RPC, 2 K chunk, 0.2 s throttle
      56      BSC               — configurable via BSC_RPC_URL env var; skipped
                                  gracefully if the RPC doesn't support eth_getLogs
      5042002 Arc Testnet       — public RPC, 1 K chunk, 0.5 s throttle;
                                  on first run scans last 500 K blocks only
    """
    import urllib.error
    from walletshift_radar.db import migrate_reputation_schema
    from walletshift_radar.reputation import (
        scan_chain, recompute_sybil_collisions, recompute_agent_reputation,
        MAINNET_REPUTATION, MAINNET_LOGS_RPC, MAINNET_CHUNK, MAINNET_DEPLOY_BLOCK,
        BASE_REPUTATION, BASE_RPC, BASE_DEPLOY_BLOCK, BASE_LOOKBACK,
        BSC_REPUTATION, BSC_RPC_DEFAULT, BSC_DEPLOY_BLOCK,
        ARC_TESTNET_REPUTATION, ARC_TESTNET_RPC, ARC_CHUNK, ARC_TESTNET_LOOKBACK,
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    migrate_reputation_schema(conn)

    try:
        mainnet_rpc = os.environ.get("MAINNET_ETH_LOGS_RPC", MAINNET_LOGS_RPC)
        print(f"Scanning Ethereum Mainnet ReputationRegistry ({mainnet_rpc})…")
        n_mainnet = scan_chain(
            mainnet_rpc, MAINNET_REPUTATION, chain_id=1, conn=conn,
            chunk=MAINNET_CHUNK, throttle=0.3, genesis_block=MAINNET_DEPLOY_BLOCK,
        )
        print(f"  {n_mainnet:,} new events")

        base_rpc = os.environ.get("BASE_RPC_URL", BASE_RPC)
        print(f"Scanning Base ReputationRegistry ({base_rpc})…")
        n_base = scan_chain(
            base_rpc, BASE_REPUTATION, chain_id=8453, conn=conn,
            throttle=0.2, genesis_block=BASE_DEPLOY_BLOCK, lookback_blocks=BASE_LOOKBACK,
        )
        print(f"  {n_base:,} new events")

        bsc_rpc = os.environ.get("BSC_RPC_URL", BSC_RPC_DEFAULT)
        print(f"Scanning BSC ReputationRegistry ({bsc_rpc})…")
        try:
            n_bsc = scan_chain(
                bsc_rpc, BSC_REPUTATION, chain_id=56, conn=conn,
                throttle=0.5, genesis_block=BSC_DEPLOY_BLOCK,
            )
            print(f"  {n_bsc:,} new events")
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            print(f"  ⚠ BSC scan skipped ({exc}). Set BSC_RPC_URL to a provider "
                  "that supports eth_getLogs (NodeReal, Ankr, etc.).")

        print("Scanning Arc Testnet ReputationRegistry…")
        try:
            n_arc = scan_chain(
                ARC_TESTNET_RPC, ARC_TESTNET_REPUTATION,
                chain_id=5042002, conn=conn,
                chunk=ARC_CHUNK, throttle=0.5,
                lookback_blocks=ARC_TESTNET_LOOKBACK,
            )
            print(f"  {n_arc:,} new events")
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            print(f"  ⚠ Arc Testnet scan skipped ({exc}).")

        print("Recomputing Sybil collision tables…")
        recompute_sybil_collisions(conn)
        recompute_agent_reputation(conn)

        total = conn.execute("SELECT COUNT(*) FROM reputation_events").fetchone()[0]
        flagged = conn.execute(
            "SELECT COUNT(*) FROM agent_reputation WHERE sybil_flag=1"
        ).fetchone()[0]
        by_chain = conn.execute("""
            SELECT chain_id, COUNT(*) FROM reputation_events GROUP BY chain_id
        """).fetchall()
        print(f"Done. {total:,} total events, {flagged} agents Sybil-flagged.")
        chain_labels = {1: "mainnet", 8453: "base", 56: "bsc", 5042002: "arc_testnet"}
        for row in by_chain:
            label = chain_labels.get(row[0], f"chain_{row[0]}")
            print(f"  {label}: {row[1]:,} events")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="WalletShift Radar — on-chain daily pipeline")
    parser.add_argument("--db",         default="walletshift.db")
    parser.add_argument("--out",        default="dashboard.html")
    parser.add_argument("--alchemy",    default=os.environ.get("ALCHEMY_KEY", ""),
                        help="Alchemy API key (builds https://eth-mainnet.g.alchemy.com/v2/KEY)")
    parser.add_argument("--eth-rpc",
                        default=os.environ.get("ETH_MAINNET_RPC_URL", ""),
                        help="Ethereum mainnet JSON-RPC URL (Chainstack, Infura, etc.). "
                             "Overrides --alchemy. Set ETH_MAINNET_RPC_URL env var instead.")
    parser.add_argument("--date",       default=None)
    parser.add_argument("--full-probe", action="store_true",
                        help="Re-probe ALL active agents' endpoints (use weekly)")
    parser.add_argument("--seed-from-walletshift", action="store_true",
                        help="One-time historical seed from walletshift API")
    parser.add_argument("--no-chain-scan", action="store_true",
                        help="Skip on-chain block scan (no RPC key needed)")
    parser.add_argument("--reputation", action="store_true",
                        help="Index NewFeedback events from ReputationRegistry on mainnet + Base "
                             "and recompute Sybil collision tables")
    args = parser.parse_args()

    # No validation needed: _resolve_eth_rpc() always returns a URL
    # (mevblocker default when neither --alchemy nor --eth-rpc is given).

    if args.reputation:
        _run_reputation_scan(args.db, args.alchemy)
        return

    run_date = args.date or date.today().isoformat()
    run(args.db, args.out, args.alchemy, run_date,
        args.full_probe, args.seed_from_walletshift,
        no_chain_scan=args.no_chain_scan, eth_rpc=args.eth_rpc)


if __name__ == "__main__":
    main()
