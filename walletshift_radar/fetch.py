"""
fetch.py — WalletShift JSON API client.

Stdlib-only (urllib).  Injectable transport so tests can swap in fixture data.
"""
import json
import time
import urllib.request
import urllib.error
from typing import Callable

BASE = "https://thewalletshift.com"
PAGE_SIZE = 100
_RETRY_DELAYS = [1, 3, 7]   # seconds between retries


def _default_get(url: str) -> dict:
    """Fetch a JSON URL and return the parsed dict."""
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def _retry_get(url: str, get_fn: Callable = _default_get) -> dict:
    """GET with simple linear retry/backoff on network errors."""
    last_exc = None
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return get_fn(url)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last_exc = exc
    raise RuntimeError(f"Failed to fetch {url} after retries: {last_exc}") from last_exc


def fetch_all_search(get_fn: Callable = _default_get) -> tuple[list, list, dict]:
    """
    Paginate through /api/services/search and return all results.

    Returns:
        (all_results, categories, meta)
        meta = {total, live_skills_read, x402_count}
    """
    offset = 0
    all_results = []
    categories = []
    meta = {}

    while True:
        url = f"{BASE}/api/services/search?limit={PAGE_SIZE}&offset={offset}"
        page = _retry_get(url, get_fn)

        if not categories:
            categories = page.get("categories", [])

        results = page.get("results", [])
        all_results.extend(results)

        if not meta:
            meta["total"] = page.get("total", 0)

        offset += len(results)
        if offset >= page.get("total", 0) or not results:
            break
        time.sleep(0.1)   # polite pacing

    return all_results, categories, meta


def fetch_detail(token_id: int, get_fn: Callable = _default_get) -> dict | None:
    """
    Fetch /api/services/{id} for a single agent.

    Returns None on failure so callers can degrade gracefully.
    """
    url = f"{BASE}/api/services/{token_id}"
    try:
        return _retry_get(url, get_fn)
    except RuntimeError:
        return None
