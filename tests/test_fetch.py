"""
Tests for fetch.py — retry logic, pagination, and graceful degradation.

All tests use an injected get_fn so no network calls are made.
time.sleep is patched to keep the suite fast without changing the
logic under test (retry counts, return values, error propagation).
"""
import json
import urllib.error
import pytest

from walletshift_radar.fetch import (
    _retry_get,
    fetch_all_search,
    fetch_detail,
    BASE,
    PAGE_SIZE,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _always_return(value):
    """get_fn that always returns value."""
    def _get(url):
        return value
    return _get


def _fail_then_return(n_fails, value):
    """get_fn that raises URLError n_fails times then returns value."""
    calls = {"count": 0}
    def _get(url):
        calls["count"] += 1
        if calls["count"] <= n_fails:
            raise urllib.error.URLError("network error")
        return value
    return _get


def _always_raise(exc_factory):
    """get_fn that always raises the given exception."""
    def _get(url):
        raise exc_factory()
    return _get


# ── _retry_get() ───────────────────────────────────────────────────────────────

class TestRetryGet:
    def test_returns_on_first_success(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        result = _retry_get("http://example.com", get_fn=_always_return({"ok": True}))
        assert result == {"ok": True}

    def test_succeeds_after_one_failure(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        result = _retry_get("http://x.com", get_fn=_fail_then_return(1, {"data": 42}))
        assert result == {"data": 42}

    def test_succeeds_after_two_failures(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        result = _retry_get("http://x.com", get_fn=_fail_then_return(2, {"data": 99}))
        assert result == {"data": 99}

    def test_raises_runtime_error_after_all_retries_exhausted(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        with pytest.raises(RuntimeError, match="Failed to fetch"):
            _retry_get("http://x.com", get_fn=_always_raise(lambda: urllib.error.URLError("down")))

    def test_catches_oserror(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        with pytest.raises(RuntimeError):
            _retry_get("http://x.com", get_fn=_always_raise(lambda: OSError("connection reset")))

    def test_catches_json_decode_error(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        def _bad_json(url):
            raise json.JSONDecodeError("bad", "", 0)
        with pytest.raises(RuntimeError):
            _retry_get("http://x.com", get_fn=_bad_json)

    def test_does_not_catch_value_error(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        with pytest.raises(ValueError):
            _retry_get("http://x.com", get_fn=_always_raise(lambda: ValueError("unexpected")))

    def test_passes_correct_url_to_get_fn(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        seen = []
        def _capture(url):
            seen.append(url)
            return {}
        _retry_get("https://exact-url.com/path", get_fn=_capture)
        assert seen == ["https://exact-url.com/path"]

    def test_sleeps_between_retries(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda d: sleeps.append(d))
        _retry_get("http://x.com", get_fn=_fail_then_return(2, {}))
        # first attempt has delay=0 (no sleep), second has delay=1, third has delay=3
        assert sleeps == [1, 3]


# ── fetch_all_search() ────────────────────────────────────────────────────────

class TestFetchAllSearch:
    def test_single_page_returns_all_results(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        page = {"results": [{"id": 1}, {"id": 2}], "total": 2, "categories": ["ai"]}
        results, categories, meta = fetch_all_search(get_fn=_always_return(page))
        assert len(results) == 2
        assert results[0]["id"] == 1

    def test_categories_from_first_page(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        page = {"results": [{"id": 1}], "total": 1, "categories": ["defi", "nft"]}
        _, categories, _ = fetch_all_search(get_fn=_always_return(page))
        assert categories == ["defi", "nft"]

    def test_meta_total_from_first_page(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        page = {"results": [{"id": 1}], "total": 500, "categories": []}
        _, _, meta = fetch_all_search(get_fn=_always_return(page))
        assert meta["total"] == 500

    def test_empty_results_stops_pagination(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        page = {"results": [], "total": 0, "categories": []}
        results, _, _ = fetch_all_search(get_fn=_always_return(page))
        assert results == []

    def test_multi_page_aggregates_all_results(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        calls = {"n": 0}
        page1_items = [{"id": i} for i in range(PAGE_SIZE)]
        page2_items = [{"id": i + PAGE_SIZE} for i in range(50)]

        def _paginated(url):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"results": page1_items, "total": PAGE_SIZE + 50, "categories": ["x"]}
            return {"results": page2_items, "total": PAGE_SIZE + 50, "categories": ["ignored"]}

        results, categories, meta = fetch_all_search(get_fn=_paginated)
        assert len(results) == PAGE_SIZE + 50
        assert categories == ["x"]   # only from first page
        assert meta["total"] == PAGE_SIZE + 50

    def test_correct_url_with_offset(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        urls_seen = []
        calls = {"n": 0}

        def _capture(url):
            urls_seen.append(url)
            calls["n"] += 1
            if calls["n"] == 1:
                return {"results": [{"id": 1}] * PAGE_SIZE, "total": PAGE_SIZE + 1, "categories": []}
            return {"results": [{"id": 2}], "total": PAGE_SIZE + 1, "categories": []}

        fetch_all_search(get_fn=_capture)
        assert urls_seen[0] == f"{BASE}/api/services/search?limit={PAGE_SIZE}&offset=0"
        assert urls_seen[1] == f"{BASE}/api/services/search?limit={PAGE_SIZE}&offset={PAGE_SIZE}"


# ── fetch_detail() ────────────────────────────────────────────────────────────

class TestFetchDetail:
    def test_returns_dict_on_success(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        detail = {"token_id": 42, "name": "Test Agent"}
        result = fetch_detail(42, get_fn=_always_return(detail))
        assert result == detail

    def test_returns_none_when_all_retries_fail(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        result = fetch_detail(42, get_fn=_always_raise(lambda: urllib.error.URLError("down")))
        assert result is None

    def test_correct_url_for_token_id(self, monkeypatch):
        monkeypatch.setattr("walletshift_radar.fetch.time.sleep", lambda _: None)
        seen = []
        def _capture(url):
            seen.append(url)
            return {}
        fetch_detail(12345, get_fn=_capture)
        assert seen[0] == f"{BASE}/api/services/12345"
