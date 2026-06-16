"""
cluster.py — per-wallet duplicate clustering.

455 of 711 agents in the directory are "Zyfai Rebalancer Agent for 0x<wallet>" —
one registry entry per user wallet.  cluster_key() strips the wallet suffix so
they collapse into one logical product; group_by_cluster() returns {key: [agents]}.
"""
import re

_WALLET_SUFFIX = re.compile(r"\s+for\s+0x[0-9a-fA-F]+\s*$", re.IGNORECASE)


def cluster_key(name: str) -> str:
    """Return the logical product name, stripping any '… for 0x<hex>' wallet suffix."""
    return _WALLET_SUFFIX.sub("", name).strip()


def group_by_cluster(agents: list) -> dict:
    """
    Group a list of agent dicts (as returned by /api/services/search) by cluster_key.

    Returns: {cluster_key_string: [agent, …]}
    """
    groups: dict = {}
    for agent in agents:
        key = cluster_key(agent["name"])
        groups.setdefault(key, []).append(agent)
    return groups
