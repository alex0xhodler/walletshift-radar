"""
charts.py — ASCII / Unicode terminal-style chart primitives.

All functions return plain strings; the caller embeds them in HTML <pre> blocks.
No external dependencies.
"""

_SPARK = "▁▂▃▄▅▆▇█"
_FILL  = "█"
_EMPTY = "░"


# ── sparkline ─────────────────────────────────────────────────────────────────

def sparkline(values: list) -> str:
    """
    Return a single-line Unicode sparkline for a sequence of numeric values.

    Uses the 8-level Braille-style block spectrum ▁▂▃▄▅▆▇█.
    Flat series → all middle characters.  Single-value → mid char.
    """
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo
    chars = []
    for v in values:
        if span == 0:
            idx = len(_SPARK) // 2
        else:
            idx = round((v - lo) / span * (len(_SPARK) - 1))
        chars.append(_SPARK[idx])
    return "".join(chars)


# ── horizontal bar ────────────────────────────────────────────────────────────

def hbar(value: float, max_val: float, width: int = 20) -> str:
    """
    Horizontal filled bar of exactly `width` characters.

    Filled portion = █, empty = ░.
    value==max_val → fully filled.  value==0 → all empty.
    """
    if width <= 0:
        return ""
    if max_val <= 0:
        return _EMPTY * width
    ratio = min(max(value / max_val, 0.0), 1.0)
    filled = round(ratio * width)
    return _FILL * filled + _EMPTY * (width - filled)


# ── diverging bar ─────────────────────────────────────────────────────────────

def diverging_bar(pos: float, neg: float, max_val: float, width: int = 40) -> str:
    """
    Diverging ASCII bar split at center.

    Positive side (right, ▶) shows gainers; negative side (left, ◀) shows losers.
    Total string length is always `width + 3` (fixed label glyphs around the center).

    Layout:  [neg bar ◀][  ▶ pos bar]
    """
    if width < 4:
        width = 4
    half = width // 2
    if max_val <= 0:
        max_val = 1.0

    pos_cells = round(min(pos / max_val, 1.0) * half)
    neg_cells = round(min(neg / max_val, 1.0) * half)

    left  = (_EMPTY * (half - neg_cells)) + ("█" * neg_cells)
    right = ("█" * pos_cells) + (_EMPTY * (half - pos_cells))
    return f"{left}◀▶{right}"


# ── box table ─────────────────────────────────────────────────────────────────

def box_table(headers: list, rows: list, col_sep: str = " │ ") -> str:
    """
    Render a Unicode box-drawing table.

    headers: list of column header strings
    rows:    list of lists of cell strings (same width as headers)
    Returns a multi-line string suitable for embedding in a <pre> block.
    """
    # column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))

    def fmt_row(cells):
        return " │ ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells))

    separator_inner = "─┼─".join("─" * w for w in widths)
    separator_top   = "─┬─".join("─" * w for w in widths)
    separator_bot   = "─┴─".join("─" * w for w in widths)

    lines = [
        "┌─" + separator_top + "─┐",
        "│ " + fmt_row(headers) + " │",
        "├─" + separator_inner + "─┤",
    ]
    for row in rows:
        lines.append("│ " + fmt_row(row) + " │")
    lines.append("└─" + separator_bot + "─┘")
    return "\n".join(lines)
