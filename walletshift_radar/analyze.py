"""
analyze.py — health tallying, delta detection, momentum scoring.

All functions are pure (no I/O) and operate on plain Python dicts, making
them trivially testable against real fixture data.
"""


# ── health counts ─────────────────────────────────────────────────────────────

def compute_health_counts(endpoints: list) -> dict:
    """
    Tally live / dead / paywalled counts from an agent's endpoints list.

    Each endpoint may carry a 'health' key with 'status' in {live, dead, paywalled}.
    Endpoints without a health record contribute 0 to any count.
    """
    live = dead = paywalled = 0
    for ep in endpoints:
        status = (ep.get("health") or {}).get("status")
        if status == "live":
            live += 1
        elif status == "dead":
            dead += 1
        elif status == "paywalled":
            paywalled += 1
    return {"live": live, "dead": dead, "paywalled": paywalled}


# ── delta / event detection ───────────────────────────────────────────────────

def compute_deltas(
    prev: dict,   # {token_id: snapshot_metrics_dict}
    curr: dict,   # {token_id: snapshot_metrics_dict}
) -> list:
    """
    Compare two daily snapshots and emit structured events.

    Event types:
      newcomer        — id in curr but not prev
      dropout         — id in prev but not curr
      skills_up       — skills_count increased
      skills_down     — skills_count decreased
      health_flip_live  — was all-dead, now has live endpoints
      health_flip_dead  — had live endpoints, now all dead
      x402_added      — x402 flag flipped False→True
      x402_removed    — x402 flag flipped True→False

    Returns a list of event dicts.
    """
    events = []
    prev_ids = set(prev)
    curr_ids = set(curr)

    for tid in curr_ids - prev_ids:
        events.append({"type": "newcomer", "token_id": tid, "detail": {}})

    for tid in prev_ids - curr_ids:
        events.append({"type": "dropout", "token_id": tid, "detail": {}})

    for tid in prev_ids & curr_ids:
        p, c = prev[tid], curr[tid]

        # skills deltas
        ps, cs = p.get("skills_count", 0) or 0, c.get("skills_count", 0) or 0
        if cs > ps:
            events.append({"type": "skills_up", "token_id": tid,
                           "detail": {"from": ps, "to": cs, "delta": cs - ps}})
        elif cs < ps:
            events.append({"type": "skills_down", "token_id": tid,
                           "detail": {"from": ps, "to": cs, "delta": ps - cs}})

        # health flips
        pl, pd = p.get("live_count", 0) or 0, p.get("dead_count", 0) or 0
        cl, cd = c.get("live_count", 0) or 0, c.get("dead_count", 0) or 0
        if pl > 0 and cl == 0 and cd > 0:
            events.append({"type": "health_flip_dead", "token_id": tid,
                           "detail": {"prev_live": pl, "curr_dead": cd}})
        elif pd > 0 and cl > 0 and pl == 0:
            events.append({"type": "health_flip_live", "token_id": tid,
                           "detail": {"curr_live": cl}})

        # x402 flips
        px, cx = bool(p.get("x402")), bool(c.get("x402"))
        if not px and cx:
            events.append({"type": "x402_added", "token_id": tid, "detail": {}})
        elif px and not cx:
            events.append({"type": "x402_removed", "token_id": tid, "detail": {}})

    return events


# ── momentum score ────────────────────────────────────────────────────────────

# Weights (must sum to 1.0)
_W_SKILLS   = 0.40   # normalised 7-day skills growth
_W_HEALTH   = 0.30   # live / (live + dead) ratio
_W_PROTOS   = 0.20   # protocol breadth (0‥1 across {a2a, mcp, web, x402})
_W_X402     = 0.10   # x402 monetisation flag
_ALL_PROTOS = {"a2a", "mcp", "web", "x402"}
_MAX_DELTA  = 20.0   # cap for skills_delta normalisation


def momentum_score(agent: dict) -> float:
    """
    Composite momentum score in [0, 1] for a single agent.

    Input dict keys:
      skills_delta_7d  — int, skills_count change over last 7 days
      live_count       — int, number of live endpoints today
      dead_count       — int, number of dead endpoints today
      endpoint_count   — int, total endpoints
      protos           — list[str], e.g. ["a2a", "mcp"]
      x402             — bool

    Score formula:
      0.40 * norm_skills_delta + 0.30 * live_ratio + 0.20 * proto_breadth + 0.10 * x402
    """
    delta = float(agent.get("skills_delta_7d") or 0)
    live  = float(agent.get("live_count") or 0)
    dead  = float(agent.get("dead_count") or 0)
    protos = set(agent.get("protos") or [])
    x402  = 1.0 if agent.get("x402") else 0.0

    norm_delta = min(max(delta, 0.0), _MAX_DELTA) / _MAX_DELTA
    live_ratio = live / (live + dead) if (live + dead) > 0 else 0.0
    proto_breadth = len(protos & _ALL_PROTOS) / len(_ALL_PROTOS)

    score = (
        _W_SKILLS * norm_delta
        + _W_HEALTH * live_ratio
        + _W_PROTOS * proto_breadth
        + _W_X402   * x402
    )
    return round(min(max(score, 0.0), 1.0), 4)
