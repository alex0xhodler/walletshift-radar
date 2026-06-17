"""
reputation.py — Index NewFeedback events from the ERC-8004 ReputationRegistry.

Scans both Ethereum mainnet and Base L2 using eth_getLogs in chunked block
ranges.  Events are stored in walletshift.db; aggregate tables are recomputed
after each scan.

Usage (called from main.py --reputation):
    scan_chain(alchemy_url, MAINNET_REPUTATION, chain_id=1, conn=conn)
    scan_chain(BASE_RPC,    BASE_REPUTATION,    chain_id=8453, conn=conn)
    recompute_sybil_collisions(conn)
    recompute_agent_reputation(conn)
"""
import json
import sqlite3
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

# ── contract addresses ────────────────────────────────────────────────────────

MAINNET_REPUTATION     = "0x8004baa17c55a88189ae136b182e5fda19de9b63"
BASE_REPUTATION        = "0x8004baa17c55a88189ae136b182e5fda19de9b63"
BSC_REPUTATION         = "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63"
ARC_TESTNET_REPUTATION = "0x8004B663056A597Dffe9eCcC1965A193B7388713"

# keccak256("NewFeedback(uint256,address,bytes32,uint256,uint256,string,string)")
NEW_FEEDBACK_SIG = "0x6a4a61743519c9d648a14e6493f47dbe3ff1aa29e7785c96c8326a205e58febc"

BASE_RPC        = "https://mainnet.base.org"
ARC_TESTNET_RPC = "https://rpc.testnet.arc.network"
# PublicNode: supports 50K block range on mainnet eth_getLogs, no API key needed.
MAINNET_LOGS_RPC = "https://ethereum.publicnode.com"
# BSC: official public endpoints disable eth_getLogs; use BSC_RPC_URL env var for
# a provider that supports it (NodeReal free tier, Ankr, etc.).
BSC_RPC_DEFAULT = "https://bsc-rpc.publicnode.com"

# Per-chain eth_getLogs chunk sizes (blocks per request):
#   mainnet (PublicNode): up to 50K — use 25K to leave headroom
#   Base public: up to 10K — use 2K conservatively
#   BSC: varies; 2K is safest across free providers
#   Arc testnet: 10K limit but dense recent blocks → 1K to stay under 20K results cap
MAINNET_CHUNK = 25_000
ARC_CHUNK     = 1_000
_CHUNK        = 2_000  # default for Base / BSC

# Deployment / genesis blocks — skip the empty pre-deployment history on first run.
# Ethereum mainnet: first NewFeedback events appear around block 24.4M.
MAINNET_DEPLOY_BLOCK    = 24_000_000
# Base L2: deployed ~early 2025; Base block ≈24M at that point (0.5 blocks/sec since Aug 2023).
BASE_DEPLOY_BLOCK       = 24_000_000
# BSC ReputationRegistry deployed ~Feb 2026; skip the first ~55M empty blocks.
BSC_DEPLOY_BLOCK        = 55_000_000
# Lookback for chains where full history is impractical on first run.
# Arc testnet and Base: scan only the last 500K blocks on first run (~11 days on Base,
# ~2-3 days on Arc testnet at 0.4 s/block) to capture current Sybil signals.
ARC_TESTNET_LOOKBACK    = 500_000
BASE_LOOKBACK           = 500_000

# ── RPC helper ────────────────────────────────────────────────────────────────

def _rpc(url: str, method: str, params: list, retries: int = 3) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    req = urllib.request.Request(
        url, data=body.encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent":   "python-httpx/0.27.0",
        },
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return {}


def _get_latest_block(rpc_url: str) -> int:
    r = _rpc(rpc_url, "eth_blockNumber", [])
    return int(r["result"], 16)


# ── ABI decoding ──────────────────────────────────────────────────────────────

def decode_feedback_event(log: dict) -> dict:
    """
    Decode a raw eth_getLogs NewFeedback entry.

    topics layout:
      [0] event signature
      [1] agent_id (uint256, indexed)
      [2] client address (address, indexed — last 20 bytes of 32-byte topic)
      [3] feedbackURI hash (bytes32, indexed)

    data layout (ABI-encoded):
      word 0 (bytes 0-31):  score (uint256; first nibble 'f' = negative int256 → skip)
      word 1 (bytes 32-63): fixedDecimals (uint256)
      remainder: feedbackURI string + card snapshot (not decoded here)
    """
    topics = log["topics"]
    agent_id = int(topics[1], 16)
    client = "0x" + topics[2][-40:]
    feedback_uri_hash = topics[3]

    data_hex = log["data"][2:]  # strip 0x
    score_word    = data_hex[0:64]
    decimals_word = data_hex[64:128]

    if int(score_word[0], 16) > 7:
        # First nibble 8-f → two's-complement negative int256; not a valid score.
        score = None
    else:
        raw = int(score_word, 16)
        decimals = int(decimals_word, 16)
        score = raw / (10 ** decimals) if decimals <= 18 else None

    return {
        "agent_id":          agent_id,
        "client":            client,
        "feedback_uri_hash": feedback_uri_hash,
        "score":             score,
        "block_number":      int(log["blockNumber"], 16),
        "tx_hash":           log["transactionHash"],
        "log_index":         int(log["logIndex"], 16),
    }


# ── upsert ────────────────────────────────────────────────────────────────────

def upsert_reputation_event(conn: sqlite3.Connection, chain_id: int,
                            event: dict) -> None:
    conn.execute("""
        INSERT INTO reputation_events
            (chain_id, block_number, tx_hash, log_index,
             agent_id, client, feedback_uri_hash, score)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(chain_id, tx_hash, log_index) DO UPDATE SET
            score=excluded.score
    """, (
        chain_id,
        event["block_number"],
        event["tx_hash"],
        event["log_index"],
        event["agent_id"],
        event["client"],
        event["feedback_uri_hash"],
        event["score"],
    ))


# ── aggregation ───────────────────────────────────────────────────────────────

def recompute_sybil_collisions(conn: sqlite3.Connection) -> None:
    """
    Rebuild sybil_collisions from reputation_events.

    A collision is a feedback_uri_hash used by more than one (client, agent_id)
    pair — evidence of coordinated fake reviews using recycled feedback content.
    Single-use hashes (one client reviewing one agent) are not collisions.
    """
    conn.execute("DELETE FROM sybil_collisions")
    conn.execute("""
        INSERT INTO sybil_collisions
            (chain_id, feedback_uri_hash, event_count,
             distinct_clients, distinct_agents, last_updated)
        SELECT
            chain_id,
            feedback_uri_hash,
            COUNT(*)                    AS event_count,
            COUNT(DISTINCT client)      AS distinct_clients,
            COUNT(DISTINCT agent_id)    AS distinct_agents,
            ?
        FROM reputation_events
        GROUP BY chain_id, feedback_uri_hash
        HAVING COUNT(DISTINCT client) > 1 OR COUNT(DISTINCT agent_id) > 1
    """, (datetime.now(timezone.utc).isoformat(),))
    conn.commit()


def recompute_agent_reputation(conn: sqlite3.Connection) -> None:
    """
    Rebuild agent_reputation aggregates from reputation_events + sybil_collisions.

    sybil_flag=1 if any reviewer targeting this agent used a feedback_uri_hash
    that also appears in sybil_collisions (i.e. a coordinated/recycled hash).
    """
    conn.execute("DELETE FROM agent_reputation")
    conn.execute("""
        INSERT INTO agent_reputation
            (chain_id, agent_id, unique_reviewers,
             avg_score, min_score, max_score,
             sybil_flag, last_block, last_updated)
        SELECT
            r.chain_id,
            r.agent_id,
            COUNT(DISTINCT r.client)        AS unique_reviewers,
            AVG(r.score)                    AS avg_score,
            MIN(r.score)                    AS min_score,
            MAX(r.score)                    AS max_score,
            MAX(CASE WHEN sc.feedback_uri_hash IS NOT NULL THEN 1 ELSE 0 END)
                                            AS sybil_flag,
            MAX(r.block_number)             AS last_block,
            ?                               AS last_updated
        FROM reputation_events r
        LEFT JOIN sybil_collisions sc
            ON sc.chain_id = r.chain_id
           AND sc.feedback_uri_hash = r.feedback_uri_hash
        GROUP BY r.chain_id, r.agent_id
    """, (datetime.now(timezone.utc).isoformat(),))
    conn.commit()


# ── scanning ──────────────────────────────────────────────────────────────────

def scan_chain(rpc_url: str, contract_address: str, chain_id: int,
               conn: sqlite3.Connection,
               from_block: Optional[int] = None,
               to_block: Optional[int] = None,
               chunk: int = _CHUNK,
               throttle: float = 0.0,
               genesis_block: int = 1,
               lookback_blocks: Optional[int] = None) -> int:
    """
    Fetch all NewFeedback events for one chain in chunked eth_getLogs requests.

    throttle: seconds to sleep between chunk requests (for public free-tier RPCs).
    genesis_block: lowest block to scan from on first run (skip empty history).
    lookback_blocks: when set and no prior data exists, start at
                     max(genesis_block, to_block - lookback_blocks) rather than
                     genesis_block — useful for very active chains where full
                     history is impractical to index on first run.

    Returns the number of new events inserted.
    """
    if to_block is None:
        to_block = _get_latest_block(rpc_url)
    if from_block is None:
        # Resume from last indexed block, or the chain genesis / deploy block.
        row = conn.execute(
            "SELECT MAX(block_number) FROM reputation_events WHERE chain_id=?",
            (chain_id,)
        ).fetchone()
        if row[0] is not None:
            from_block = row[0] + 1
        elif lookback_blocks is not None:
            from_block = max(genesis_block, to_block - lookback_blocks)
        else:
            from_block = genesis_block

    inserted = 0
    start = from_block
    while start <= to_block:
        end = min(start + chunk - 1, to_block)
        resp = _rpc(rpc_url, "eth_getLogs", [{
            "address":   contract_address,
            "topics":    [NEW_FEEDBACK_SIG],
            "fromBlock": hex(start),
            "toBlock":   hex(end),
        }])
        if "error" in resp:
            err = resp["error"]
            raise ValueError(
                f"eth_getLogs error at blocks {start}-{end}: "
                f"code={err.get('code')} message={err.get('message')}"
            )
        logs = resp.get("result", [])
        for log in logs:
            event = decode_feedback_event(log)
            upsert_reputation_event(conn, chain_id=chain_id, event=event)
            inserted += 1
        conn.commit()
        start = end + 1
        if throttle > 0:
            time.sleep(throttle)

    return inserted
