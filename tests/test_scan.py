"""
Unit tests for the pure (network-free) functions in scan.py.

is_real_service_agent() is the spam filter — wrong behaviour here means junk
agents get stored and served downstream.  _infer_protos() drives protocol tags
in both the dashboard and the sigmatic API.
"""
import pytest
from walletshift_radar.scan import is_real_service_agent, _infer_protos


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
