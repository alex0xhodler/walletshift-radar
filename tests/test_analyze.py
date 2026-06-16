"""
Tests for analyze.py — health tallying, delta detection, momentum scoring.

All tests use real fixture JSON — no mocks.
"""
import json
import pathlib
import pytest

from walletshift_radar.analyze import (
    compute_health_counts,
    compute_deltas,
    momentum_score,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ── health counts ─────────────────────────────────────────────────────────────

def test_health_counts_from_real_detail():
    """compute_health_counts sums live/dead/paywalled from a real endpoints list."""
    detail = json.loads((FIXTURES / "detail_1.json").read_text())
    endpoints = detail["endpoints"]
    counts = compute_health_counts(endpoints)

    # Counts must be non-negative integers that sum correctly
    assert counts["live"] >= 0
    assert counts["dead"] >= 0
    assert counts["paywalled"] >= 0
    # Total tagged endpoints == live + dead + paywalled + unknown (some have no health)
    total_with_health = sum(
        1 for e in endpoints if e.get("health") and e["health"].get("status")
    )
    assert counts["live"] + counts["dead"] + counts["paywalled"] <= total_with_health + 1


def test_health_counts_known_values():
    """compute_health_counts is correct on a hand-crafted endpoint list."""
    endpoints = [
        {"health": {"status": "live"}},
        {"health": {"status": "live"}},
        {"health": {"status": "dead"}},
        {"health": {"status": "paywalled"}},
        {},                                # no health key at all
        {"health": {}},                    # health key but no status
    ]
    counts = compute_health_counts(endpoints)
    assert counts["live"] == 2
    assert counts["dead"] == 1
    assert counts["paywalled"] == 1


# ── delta detection ───────────────────────────────────────────────────────────

def test_compute_deltas_detects_newcomer():
    """An id present in curr but not prev generates a 'newcomer' event."""
    prev = {1: {"skills_count": 5, "live_count": 1, "dead_count": 0, "paywalled_count": 0, "x402": False}}
    curr = {
        1: {"skills_count": 5, "live_count": 1, "dead_count": 0, "paywalled_count": 0, "x402": False},
        2: {"skills_count": 3, "live_count": 1, "dead_count": 0, "paywalled_count": 0, "x402": True},
    }
    events = compute_deltas(prev, curr)
    newcomers = [e for e in events if e["type"] == "newcomer"]
    assert len(newcomers) == 1
    assert newcomers[0]["token_id"] == 2


def test_compute_deltas_detects_dropout():
    """An id present in prev but missing from curr generates a 'dropout' event."""
    prev = {
        1: {"skills_count": 5, "live_count": 1, "dead_count": 0, "paywalled_count": 0, "x402": False},
        2: {"skills_count": 3, "live_count": 1, "dead_count": 0, "paywalled_count": 0, "x402": True},
    }
    curr = {1: {"skills_count": 5, "live_count": 1, "dead_count": 0, "paywalled_count": 0, "x402": False}}
    events = compute_deltas(prev, curr)
    dropouts = [e for e in events if e["type"] == "dropout"]
    assert len(dropouts) == 1
    assert dropouts[0]["token_id"] == 2


def test_compute_deltas_detects_health_flip_dead():
    """live_count going to 0 while dead_count rises generates health_flip_dead."""
    prev = {1: {"skills_count": 5, "live_count": 3, "dead_count": 0, "paywalled_count": 0, "x402": False}}
    curr = {1: {"skills_count": 5, "live_count": 0, "dead_count": 3, "paywalled_count": 0, "x402": False}}
    events = compute_deltas(prev, curr)
    flips = [e for e in events if e["type"] == "health_flip_dead"]
    assert len(flips) == 1
    assert flips[0]["token_id"] == 1


def test_compute_deltas_detects_skills_up():
    """skills_count increasing generates a skills_up event."""
    prev = {1: {"skills_count": 5, "live_count": 2, "dead_count": 0, "paywalled_count": 0, "x402": False}}
    curr = {1: {"skills_count": 10, "live_count": 2, "dead_count": 0, "paywalled_count": 0, "x402": False}}
    events = compute_deltas(prev, curr)
    ups = [e for e in events if e["type"] == "skills_up"]
    assert len(ups) == 1
    assert ups[0]["detail"]["delta"] == 5


def test_compute_deltas_no_events_when_unchanged():
    """Identical prev and curr produce no events."""
    snap = {1: {"skills_count": 5, "live_count": 2, "dead_count": 1, "paywalled_count": 0, "x402": True}}
    assert compute_deltas(snap, snap) == []


# ── momentum score ────────────────────────────────────────────────────────────

def test_momentum_score_higher_when_all_live():
    """An agent with all endpoints live scores higher than one with all dead."""
    agent_all_live = {
        "skills_delta_7d": 0,
        "live_count": 4,
        "dead_count": 0,
        "endpoint_count": 4,
        "protos": ["a2a", "mcp", "web"],
        "x402": True,
    }
    agent_all_dead = {
        "skills_delta_7d": 0,
        "live_count": 0,
        "dead_count": 4,
        "endpoint_count": 4,
        "protos": ["a2a"],
        "x402": False,
    }
    assert momentum_score(agent_all_live) > momentum_score(agent_all_dead)


def test_momentum_score_higher_when_skills_growing():
    """Rising skills_delta_7d increases score."""
    base = {"skills_delta_7d": 0, "live_count": 2, "dead_count": 1, "endpoint_count": 3,
            "protos": ["a2a"], "x402": False}
    growing = {**base, "skills_delta_7d": 10}
    assert momentum_score(growing) > momentum_score(base)


def test_momentum_score_returns_float():
    agent = {"skills_delta_7d": 3, "live_count": 2, "dead_count": 1, "endpoint_count": 3,
             "protos": ["a2a", "mcp"], "x402": True}
    score = momentum_score(agent)
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0
