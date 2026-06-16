"""
Tests for charts.py — ASCII sparklines, bar charts, box-drawing tables.

These are pure utility functions; we test their exact output contracts.
"""
import pytest
from walletshift_radar.charts import sparkline, hbar, diverging_bar, box_table


# ── sparkline ─────────────────────────────────────────────────────────────────

SPARK_CHARS = "▁▂▃▄▅▆▇█"

def test_sparkline_all_same_returns_mid_char():
    """Flat series → all same character (mid-range)."""
    result = sparkline([5, 5, 5, 5])
    assert len(set(result)) == 1  # all same char


def test_sparkline_ascending_ends_higher():
    """Ascending series: last char >= first char in the block spectrum."""
    result = sparkline([1, 2, 3, 4, 5, 6, 7, 8])
    assert SPARK_CHARS.index(result[-1]) >= SPARK_CHARS.index(result[0])


def test_sparkline_length_matches_input():
    values = [10, 20, 5, 30, 15]
    assert len(sparkline(values)) == len(values)


def test_sparkline_single_value():
    """Single value should not crash."""
    result = sparkline([42])
    assert len(result) == 1
    assert result in SPARK_CHARS


def test_sparkline_returns_only_block_chars():
    result = sparkline([1, 5, 3, 7, 2])
    for ch in result:
        assert ch in SPARK_CHARS


# ── hbar ──────────────────────────────────────────────────────────────────────

def test_hbar_full_when_value_equals_max():
    """Value == max → bar is completely filled."""
    result = hbar(10, 10, width=10)
    assert result == "██████████"


def test_hbar_empty_when_zero():
    result = hbar(0, 10, width=10)
    assert result == "░░░░░░░░░░"


def test_hbar_length_equals_width():
    result = hbar(5, 10, width=20)
    assert len(result) == 20


def test_hbar_proportional():
    """Half value → roughly half filled."""
    result = hbar(5, 10, width=10)
    filled = result.count("█")
    assert filled == 5


# ── diverging_bar ─────────────────────────────────────────────────────────────

def test_diverging_bar_positive_only():
    result = diverging_bar(pos=5, neg=0, max_val=10, width=20)
    assert "▶" in result or "+" in result or "█" in result


def test_diverging_bar_negative_only():
    result = diverging_bar(pos=0, neg=5, max_val=10, width=20)
    assert "◀" in result or "-" in result or "▓" in result or "█" in result


def test_diverging_bar_length_consistent():
    r1 = diverging_bar(pos=5, neg=0, max_val=10, width=20)
    r2 = diverging_bar(pos=0, neg=5, max_val=10, width=20)
    assert len(r1) == len(r2)


# ── box_table ─────────────────────────────────────────────────────────────────

def test_box_table_contains_headers():
    headers = ["Name", "Count", "Score"]
    rows = [["AgentEinstein", "24", "0.87"], ["EmblemAI", "24", "0.71"]]
    result = box_table(headers, rows)
    for h in headers:
        assert h in result


def test_box_table_contains_data():
    headers = ["Name", "Count"]
    rows = [["AgentEinstein", "24"]]
    result = box_table(headers, rows)
    assert "AgentEinstein" in result
    assert "24" in result


def test_box_table_uses_box_drawing():
    headers = ["A", "B"]
    rows = [["x", "y"]]
    result = box_table(headers, rows)
    # Must contain at least one box-drawing character
    box_chars = "─│┌┐└┘├┤┬┴┼╔╗╚╝║═"
    assert any(ch in result for ch in box_chars)
