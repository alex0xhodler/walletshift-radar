"""
scan.py — on-chain ERC-8004 agent discovery via standard JSON-RPC.

Scans for new token mints on the ERC-8004 registry since last_scanned_block,
resolves tokenURI → IPFS/HTTPS metadata, filters real service agents, probes
endpoint health.

Works with any EVM JSON-RPC provider (Chainstack, Infura, publicnode.com, etc.).
Set ETH_MAINNET_RPC_URL (or pass --eth-rpc) to configure the Ethereum endpoint.

Zero walletshift dependency for discovery.  Walletshift overlay (category labels,
existing snapshots) is applied separately in main.py.
"""
import json
import time
import socket
import urllib.request
import urllib.error
from typing import Optional

REGISTRY    = "0x8004a169fb4a3325136eb29fa0ceb6d2e539a432"
ZERO_ADDR   = "0x0000000000000000000000000000000000000000"
TOKEN_URI_SIG = "0xc87b56dd"   # keccak4("tokenURI(uint256)")

# keccak256("Transfer(address,address,uint256)") — standard ERC-721 mint detection
TRANSFER_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_TOPIC   = "0x0000000000000000000000000000000000000000000000000000000000000000"
_MINT_CHUNK  = 2_000  # blocks per eth_getLogs call; safe for Chainstack + public nodes

IPFS_GATEWAYS = [
    "https://cloudflare-ipfs.com/ipfs/",
    "https://ipfs.io/ipfs/",
    "https://gateway.pinata.cloud/ipfs/",
]

_PROBE_TIMEOUT = 8    # seconds per HTTP health probe
_META_TIMEOUT  = 10   # seconds for IPFS/metadata fetch


# ── RPC helpers ───────────────────────────────────────────────────────────────

def _rpc(rpc_url: str, method: str, params: list, retries: int = 3) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    req  = urllib.request.Request(
        rpc_url,
        data=body.encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent":   "walletshift-radar/1.0",
        },
        method="POST",
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return {}


def get_latest_block(rpc_url: str) -> int:
    resp = _rpc(rpc_url, "eth_blockNumber", [])
    return int(resp["result"], 16)


def get_new_mints(rpc_url: str, from_block: int, to_block: int) -> list:
    """
    Return all ERC-8004 mint events (Transfer from 0x0) between from_block and to_block.

    Uses standard eth_getLogs — compatible with any JSON-RPC provider
    (Chainstack, Infura, publicnode.com, etc.).  Chunks in _MINT_CHUNK-block
    windows to stay within provider rate limits.

    Returns list of {token_id: int, block_num: int, block_timestamp: str}.
    """
    mints = []
    start = from_block

    while start <= to_block:
        end  = min(start + _MINT_CHUNK - 1, to_block)
        resp = _rpc(rpc_url, "eth_getLogs", [{
            "fromBlock": hex(start),
            "toBlock":   hex(end),
            "address":   REGISTRY,
            "topics": [
                TRANSFER_SIG,
                ZERO_TOPIC,   # from = 0x0 → mint event only
            ],
        }])

        if resp.get("error"):
            raise RuntimeError(
                f"eth_getLogs error (blocks {start}-{end}): {resp['error']}"
            )

        for log in resp.get("result", []):
            topics = log.get("topics", [])
            if len(topics) < 4:
                continue   # not ERC-721 Transfer (tokenId is topics[3])
            token_id  = int(topics[3], 16)
            block_num = int(log.get("blockNumber", "0x0"), 16)
            mints.append({
                "token_id":        token_id,
                "block_num":       block_num,
                "block_timestamp": "",
            })

        start = end + 1
        if start <= to_block:
            time.sleep(0.05)

    return mints


# ── tokenURI + metadata resolution ───────────────────────────────────────────

def get_token_uri(rpc_url: str, token_id: int) -> Optional[str]:
    """Call tokenURI(token_id) on the registry, return the URI string or None."""
    padded = hex(token_id)[2:].zfill(64)
    resp   = _rpc(rpc_url, "eth_call", [
        {"to": REGISTRY, "data": TOKEN_URI_SIG + padded}, "latest"
    ])
    raw = resp.get("result", "")
    if not raw or raw == "0x" or resp.get("error"):
        return None
    try:
        # ABI-decode: 32-byte offset + 32-byte length + UTF-8 data
        hex_data = raw[2 + 64 + 64:]
        return bytes.fromhex(hex_data).rstrip(b"\x00").decode("utf-8").strip()
    except Exception:
        return None


def _fetch_url(url: str, timeout: int) -> Optional[bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def resolve_metadata(uri: str) -> Optional[dict]:
    """
    Resolve a tokenURI to its JSON metadata dict.

    Handles:
      - https://...          → fetch directly
      - ipfs://CID           → try IPFS_GATEWAYS in order
      - ipfs://CID/path      → same, preserve path
    """
    if not uri:
        return None

    if uri.startswith("https://") or uri.startswith("http://"):
        raw = _fetch_url(uri, _META_TIMEOUT)
    elif uri.startswith("ipfs://"):
        cid_path = uri[7:]   # strip "ipfs://"
        raw = None
        for gw in IPFS_GATEWAYS:
            raw = _fetch_url(gw + cid_path, _META_TIMEOUT)
            if raw:
                break
    else:
        return None

    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def is_real_service_agent(metadata: dict) -> bool:
    """
    Return True if this EIP-8004 metadata describes a real callable service.

    Filter criteria (replicating walletshift's spam filter):
      - has a non-empty 'services' list
      - at least one service has a non-empty 'endpoint' string
    """
    services = metadata.get("services") or []
    return any(
        bool((s.get("endpoint") or "").strip())
        for s in services
        if isinstance(s, dict)
    )


# ── endpoint health probing ───────────────────────────────────────────────────

def probe_endpoint(url: str) -> dict:
    """
    HTTP-probe a single service endpoint.

    Returns {status: live|paywalled|dead, http: int|None, url: str}.
    """
    if not url or not url.startswith(("http://", "https://")):
        return {"status": "dead", "http": None, "url": url}
    try:
        req  = urllib.request.Request(url, method="GET",
                                      headers={"User-Agent": "walletshift-radar/1.0"})
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (urllib.error.URLError, socket.timeout, OSError):
        return {"status": "dead", "http": None, "url": url}

    if code == 402:
        status = "paywalled"
    elif 200 <= code < 300:
        status = "live"
    else:
        status = "dead"
    return {"status": status, "http": code, "url": url}


def probe_agent_endpoints(services: list) -> list:
    """
    Probe all endpoint URLs found in a metadata services list.

    Returns the services list enriched with a 'health' key per entry.
    Throttles to avoid hammering a single host.
    """
    results = []
    for svc in services:
        if not isinstance(svc, dict):
            continue
        url    = (svc.get("endpoint") or "").strip()
        health = probe_endpoint(url) if url else {"status": "dead", "http": None, "url": url}
        results.append({**svc, "health": health})
        time.sleep(0.05)
    return results


# ── full per-token enrichment ─────────────────────────────────────────────────

def enrich_token(rpc_url: str, token_id: int,
                 probe: bool = True) -> Optional[dict]:
    """
    Full enrichment for a single token_id:
      tokenURI → metadata → filter → probe endpoints.

    Returns a dict ready to be stored, or None if not a real service agent
    or if metadata is unresolvable (caller should retry next run).
    """
    uri = get_token_uri(rpc_url, token_id)
    if not uri:
        return None

    metadata = resolve_metadata(uri)
    if metadata is None:
        # IPFS unreachable — caller stores token_id for retry
        return {"token_id": token_id, "token_uri": uri, "_unresolved": True}

    if not is_real_service_agent(metadata):
        return None   # identity claim / collectible / placeholder — skip

    services = metadata.get("services") or []
    if probe:
        services = probe_agent_endpoints(services)

    # Derive protocol tags from service types
    protos = _infer_protos(services)

    return {
        "token_id":    token_id,
        "token_uri":   uri,
        "name":        metadata.get("name") or f"Agent #{token_id}",
        "description": metadata.get("description") or "",
        "image":       metadata.get("image") or "",
        "version":     metadata.get("version") or "",
        "services":    services,
        "protos":      protos,
        "x402":        any(s.get("name", "").lower() == "x402" for s in services),
        "skills_count": sum(
            len(s.get("a2aSkills") or s.get("mcpTools") or [])
            for s in services
            if isinstance(s, dict)
        ),
        "live_count":  sum(
            1 for s in services
            if isinstance(s, dict) and (s.get("health") or {}).get("status") == "live"
        ),
        "dead_count":  sum(
            1 for s in services
            if isinstance(s, dict) and (s.get("health") or {}).get("status") == "dead"
        ),
        "paywalled_count": sum(
            1 for s in services
            if isinstance(s, dict) and (s.get("health") or {}).get("status") == "paywalled"
        ),
        "_unresolved": False,
    }


def _infer_protos(services: list) -> list:
    protos = set()
    for s in services:
        if not isinstance(s, dict):
            continue
        name = (s.get("name") or "").lower()
        if "a2a" in name or s.get("a2aSkills"):
            protos.add("a2a")
        if "mcp" in name or s.get("mcpTools"):
            protos.add("mcp")
        if "x402" in name:
            protos.add("x402")
        if s.get("endpoint", "").startswith("http"):
            protos.add("web")
    return sorted(protos)
