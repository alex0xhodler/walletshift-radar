"""
Unit tests for the pure (network-free) functions in scan.py.

is_real_service_agent() is the spam filter — wrong behaviour here means junk
agents get stored and served downstream.  _infer_protos() drives protocol tags
in both the dashboard and the sigmatic API.

get_new_mints() is tested by patching _rpc() so no network calls are made.
"""
import json
import pytest
from unittest.mock import patch, call
from walletshift_radar.scan import (
    is_real_service_agent, _infer_protos, get_new_mints,
    TRANSFER_SIG, ZERO_TOPIC, REGISTRY, _MINT_CHUNK,
)


# ── is_real_service_agent() ────────────────────────────────────────────────────

class TestIsRealServiceAgent:
    def test_no_services_key_returns_false(self):
        assert is_real_service_agent({}) is False

    def test_empty_services_list_returns_false(self):
        assert is_real_service_agent({"services": []}) is False

    def test_none_services_returns_false(self):
        assert is_real_service_agent({"services": None}) is False

    def test_service_with_endpoint_returns_true(self):
        meta = {"services": [{"endpoint": "https://api.example.com"}]}
        assert is_real_service_agent(meta) is True

    def test_service_with_empty_string_endpoint_returns_false(self):
        meta = {"services": [{"endpoint": ""}]}
        assert is_real_service_agent(meta) is False

    def test_service_with_whitespace_only_endpoint_returns_false(self):
        meta = {"services": [{"endpoint": "   "}]}
        assert is_real_service_agent(meta) is False

    def test_service_with_none_endpoint_returns_false(self):
        meta = {"services": [{"endpoint": None}]}
        assert is_real_service_agent(meta) is False

    def test_service_missing_endpoint_key_returns_false(self):
        meta = {"services": [{"name": "x402", "type": "api"}]}
        assert is_real_service_agent(meta) is False

    def test_non_dict_service_entries_skipped(self):
        meta = {"services": ["https://api.example.com", None, 42]}
        assert is_real_service_agent(meta) is False

    def test_mixed_bad_and_good_service_returns_true(self):
        meta = {"services": [
            {"endpoint": ""},
            {"name": "no-endpoint"},
            {"endpoint": "https://real.example.com"},
        ]}
        assert is_real_service_agent(meta) is True

    def test_multiple_services_all_empty_returns_false(self):
        meta = {"services": [
            {"endpoint": ""},
            {"endpoint": None},
            {"endpoint": "  "},
        ]}
        assert is_real_service_agent(meta) is False


# ── _infer_protos() ────────────────────────────────────────────────────────────

class TestInferProtos:
    def test_empty_services_returns_empty_list(self):
        assert _infer_protos([]) == []

    def test_a2a_inferred_from_service_name(self):
        result = _infer_protos([{"name": "a2a-endpoint"}])
        assert "a2a" in result

    def test_a2a_inferred_from_a2aSkills_key(self):
        result = _infer_protos([{"name": "generic", "a2aSkills": ["task/run"]}])
        assert "a2a" in result

    def test_mcp_inferred_from_service_name(self):
        result = _infer_protos([{"name": "mcp-server"}])
        assert "mcp" in result

    def test_mcp_inferred_from_mcpTools_key(self):
        result = _infer_protos([{"name": "generic", "mcpTools": ["search"]}])
        assert "mcp" in result

    def test_x402_inferred_from_service_name(self):
        result = _infer_protos([{"name": "x402"}])
        assert "x402" in result

    def test_x402_not_inferred_when_name_doesnt_contain_x402(self):
        result = _infer_protos([{"name": "payment-gateway"}])
        assert "x402" not in result

    def test_web_inferred_from_http_endpoint(self):
        result = _infer_protos([{"endpoint": "https://api.example.com"}])
        assert "web" in result

    def test_web_inferred_from_http_no_s(self):
        result = _infer_protos([{"endpoint": "http://api.example.com"}])
        assert "web" in result

    def test_web_not_inferred_from_non_http_endpoint(self):
        result = _infer_protos([{"endpoint": "ipfs://QmFoo"}])
        assert "web" not in result

    def test_result_is_sorted(self):
        services = [
            {"name": "x402"},
            {"name": "mcp-server"},
            {"name": "a2a-agent"},
            {"endpoint": "https://api.example.com"},
        ]
        result = _infer_protos(services)
        assert result == sorted(result)

    def test_no_duplicates_from_multiple_signals(self):
        services = [
            {"name": "a2a-v1", "a2aSkills": ["run"]},
            {"name": "a2a-v2"},
        ]
        result = _infer_protos(services)
        assert result.count("a2a") == 1

    def test_multiple_protocols_detected(self):
        services = [
            {"name": "a2a-mcp-server", "mcpTools": ["search"]},
            {"name": "x402", "endpoint": "https://pay.example.com"},
        ]
        result = _infer_protos(services)
        assert "a2a" in result
        assert "mcp" in result
        assert "x402" in result
        assert "web" in result

    def test_non_dict_entries_skipped(self):
        result = _infer_protos(["a2a", None, 42, {"name": "x402"}])
        assert "x402" in result
        assert "a2a" not in result  # the string "a2a" is skipped, not the name key


# ── get_new_mints() ────────────────────────────────────────────────────────────

def _make_transfer_log(token_id: int, block_num: int, to_addr: str = "0xdeadbeef") -> dict:
    """Build a minimal eth_getLogs Transfer log for a mint event."""
    to_topic = "0x" + to_addr.lstrip("0x").zfill(64)
    token_topic = "0x" + hex(token_id)[2:].zfill(64)
    return {
        "topics": [TRANSFER_SIG, ZERO_TOPIC, to_topic, token_topic],
        "blockNumber": hex(block_num),
        "transactionHash": "0xabc",
        "logIndex": "0x0",
    }


class TestGetNewMints:
    def test_empty_range_returns_empty(self):
        with patch("walletshift_radar.scan._rpc", return_value={"result": []}) as mock_rpc:
            result = get_new_mints("http://rpc", 100, 100)
        assert result == []
        mock_rpc.assert_called_once()

    def test_single_mint_parsed_correctly(self):
        log = _make_transfer_log(token_id=42, block_num=1000)
        with patch("walletshift_radar.scan._rpc", return_value={"result": [log]}):
            result = get_new_mints("http://rpc", 1000, 1000)
        assert len(result) == 1
        assert result[0]["token_id"] == 42
        assert result[0]["block_num"] == 1000
        assert result[0]["block_timestamp"] == ""

    def test_multiple_mints_all_returned(self):
        logs = [
            _make_transfer_log(token_id=1, block_num=100),
            _make_transfer_log(token_id=2, block_num=101),
            _make_transfer_log(token_id=3, block_num=102),
        ]
        with patch("walletshift_radar.scan._rpc", return_value={"result": logs}):
            result = get_new_mints("http://rpc", 100, 200)
        assert [r["token_id"] for r in result] == [1, 2, 3]

    def test_chunks_large_range(self):
        """A range larger than _MINT_CHUNK must produce multiple _rpc calls."""
        call_count = []

        def fake_rpc(url, method, params):
            call_count.append(1)
            return {"result": []}

        span = _MINT_CHUNK * 3
        with patch("walletshift_radar.scan._rpc", side_effect=fake_rpc):
            with patch("walletshift_radar.scan.time.sleep"):
                get_new_mints("http://rpc", 0, span - 1)

        assert len(call_count) == 3

    def test_eth_get_logs_request_has_correct_filter(self):
        """eth_getLogs must filter on Transfer event + zero-from-address."""
        captured = {}

        def fake_rpc(url, method, params):
            captured["method"] = method
            captured["filter"] = params[0]
            return {"result": []}

        with patch("walletshift_radar.scan._rpc", side_effect=fake_rpc):
            get_new_mints("http://rpc", 1000, 1999)

        assert captured["method"] == "eth_getLogs"
        f = captured["filter"]
        assert f["address"] == REGISTRY
        assert f["topics"][0] == TRANSFER_SIG
        assert f["topics"][1] == ZERO_TOPIC

    def test_log_with_fewer_than_4_topics_skipped(self):
        """ERC-20 Transfer logs (no tokenId topic) must be silently ignored."""
        erc20_log = {
            "topics": [TRANSFER_SIG, ZERO_TOPIC, "0x" + "d" * 64],
            "blockNumber": "0x1",
            "transactionHash": "0xabc",
            "logIndex": "0x0",
        }
        with patch("walletshift_radar.scan._rpc", return_value={"result": [erc20_log]}):
            result = get_new_mints("http://rpc", 1, 1)
        assert result == []

    def test_rpc_error_raises_runtime_error(self):
        with patch("walletshift_radar.scan._rpc",
                   return_value={"error": {"code": -32000, "message": "block range too large"}}):
            with pytest.raises(RuntimeError, match="eth_getLogs error"):
                get_new_mints("http://rpc", 1, 100)

    def test_chunk_boundaries_cover_full_range(self):
        """Ensure from/to blocks in each chunk tile exactly without gaps or overlaps."""
        ranges = []

        def fake_rpc(url, method, params):
            f = params[0]
            ranges.append((int(f["fromBlock"], 16), int(f["toBlock"], 16)))
            return {"result": []}

        span = _MINT_CHUNK * 2 + 500
        with patch("walletshift_radar.scan._rpc", side_effect=fake_rpc):
            with patch("walletshift_radar.scan.time.sleep"):
                get_new_mints("http://rpc", 0, span - 1)

        assert ranges[0][0] == 0
        assert ranges[-1][1] == span - 1
        # No gap between consecutive chunks
        for i in range(len(ranges) - 1):
            assert ranges[i + 1][0] == ranges[i][1] + 1
