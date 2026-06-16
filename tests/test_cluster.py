"""
Tests for cluster.py — per-wallet duplicate clustering.

Real fixture data confirms names like "Zyfai Rebalancer Agent for 0xFc1C76fA0a5"
appear 455 times in the directory (one per user wallet).  cluster_key() must strip
the wallet suffix so they collapse into a single logical product.
"""
import json
import pathlib
import pytest

from walletshift_radar.cluster import cluster_key, group_by_cluster

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_wallet_suffix_stripped():
    """A name ending in ' for 0x<hex>' loses the suffix."""
    assert cluster_key("Zyfai Rebalancer Agent for 0xFc1C76fA0a5") == "Zyfai Rebalancer Agent"


def test_wallet_suffix_stripped_lowercase():
    """Suffix matching is case-insensitive for the 0x prefix."""
    assert cluster_key("Zyfai Rebalancer Agent for 0xfcE4E0B238c") == "Zyfai Rebalancer Agent"


def test_plain_name_unchanged():
    """Agents without the wallet pattern keep their full name as cluster key."""
    assert cluster_key("AgentEinstein") == "AgentEinstein"
    assert cluster_key("EmblemAI") == "EmblemAI"


def test_name_with_for_but_no_hex_unchanged():
    """Names containing 'for' but not a 0x address are NOT stripped."""
    assert cluster_key("Data Agent for Crypto") == "Data Agent for Crypto"


def test_group_by_cluster_collapses_wallet_instances():
    """group_by_cluster returns one entry per logical product with a member list."""
    page = json.loads((FIXTURES / "search_page_zyfai.json").read_text())
    results = page["results"]
    # All 5 results on this page are Zyfai wallet instances
    assert all("Zyfai" in r["name"] for r in results)

    groups = group_by_cluster(results)
    assert len(groups) == 1
    key = list(groups.keys())[0]
    assert key == "Zyfai Rebalancer Agent"
    assert len(groups[key]) == 5


def test_group_by_cluster_keeps_distinct_agents():
    """Non-clustered agents each get their own group with one member."""
    page = json.loads((FIXTURES / "search_page1.json").read_text())
    results = page["results"]
    # page1 has no Zyfai — each agent should be its own cluster
    groups = group_by_cluster(results)
    assert len(groups) == len(results)
    for members in groups.values():
        assert len(members) == 1
