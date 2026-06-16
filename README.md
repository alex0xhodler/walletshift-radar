# walletshift-radar

Daily dashboard for the ERC-8004 on-chain agent economy. Tracks agent registrations, endpoint health, protocol adoption, and platform growth on Ethereum mainnet.

**Live**: https://walletshift-radar.vercel.app  
**Registry**: `0x8004a169fb4a3325136eb29fa0ceb6d2e539a432`

---

## What it shows

- New agents entering the registry since the last scan
- Endpoint health (live / dead / paywalled) per agent and per category
- Momentum score: `0.4×skills_Δ + 0.3×live_ratio + 0.2×protocol_breadth + 0.1×x402`
- Platform adoption curves (Zyfai and similar per-wallet deployers)
- Interactive category explorer with sorting and search

Trends (sparklines, skills movers, delta events) accumulate from day 2 onward — day 1 is baseline only.

---

## Setup

Python 3.11+ required. No external packages — stdlib only.

**First run** — seed historical agents from walletshift, then scan on-chain from June 13 2026 forward:

```bash
python3 -m walletshift_radar.main \
  --alchemy YOUR_ALCHEMY_KEY \
  --seed-from-walletshift
```

**Subsequent runs** — scans only new blocks since last run:

```bash
python3 -m walletshift_radar.main --alchemy YOUR_ALCHEMY_KEY
```

Get a free Alchemy key at [alchemy.com](https://alchemy.com).

---

## Daily automation (macOS launchd)

Runs at 09:00 daily. Edit `run.sh` with your Alchemy key, then:

```bash
cp com.walletshift.radar.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.walletshift.radar.plist
```

To also push the fresh dashboard to Vercel on each run, append to `run.sh`:

```bash
git add dashboard.html && git commit -m "chore: daily snapshot $(date +%F)" && git push
```

---

## Vercel

Every `git push main` triggers a rebuild. The build command (`vercel.json`) runs a walletshift-only seed — no Alchemy key, no IPFS resolution, ~4 seconds:

```
python3 -m walletshift_radar.main --seed-from-walletshift --no-chain-scan
```

Vercel always shows the 711 walletshift-curated agents. On-chain-only agents and historical sparklines are local-only (they require the persistent SQLite DB).

---

## CLI reference

| Flag | Default | Description |
|------|---------|-------------|
| `--alchemy KEY` | `$ALCHEMY_KEY` | Alchemy API key |
| `--seed-from-walletshift` | off | One-time: seed from walletshift API, set block cursor to June 13 2026 |
| `--no-chain-scan` | off | Skip on-chain scan; no Alchemy key required |
| `--full-probe` | off | Re-probe all active agents' endpoints (use weekly) |
| `--db PATH` | `walletshift.db` | SQLite path |
| `--out PATH` | `dashboard.html` | HTML output path |
| `--date YYYY-MM-DD` | today | Override run date |

---

## Architecture

```
walletshift_radar/
  scan.py     on-chain: alchemy_getAssetTransfers, tokenURI, IPFS resolution, endpoint probes
  fetch.py    walletshift API client (paginated search + detail)
  db.py       SQLite schema and upsert helpers; last_scanned_block cursor
  cluster.py  collapse per-wallet instances ("Zyfai … for 0x…") into logical products
  analyze.py  delta detection, momentum scoring, event classification
  charts.py   Unicode sparklines ▁▂▃▅▇, hbar █░, diverging bar
  render.py   HTML generator: Chart.js charts, interactive category explorer
  main.py     daily pipeline orchestrator
tests/
  fixtures/   real API JSON saved at setup time
  test_*.py   unit tests (no network, no mocks)
```

**Data sources:**
- Alchemy RPC — `alchemy_getAssetTransfers` to find new mints (no block-range limit), `eth_call` for `tokenURI`, direct endpoint health probes
- walletshift.com JSON API — category labels, ENS, curated agent list for the historical seed
- IPFS gateways — cloudflare-ipfs.com → ipfs.io → gateway.pinata.cloud (tried in order)

**Real-service filter**: `services.length > 0` and at least one service has a non-empty `endpoint`. Mirrors walletshift's own spam filter; the ~35k collectible NFTs on the registry are excluded.

**Block cursor**: `scan_state.last_scanned_block` is written after every successful run. The next run starts from `last_scanned_block + 1`, so no blocks are ever re-scanned and no mints are missed even if the machine was off for days.
