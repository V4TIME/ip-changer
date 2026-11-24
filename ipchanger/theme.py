"""Visual tokens for the control panel.

This module is deliberately free of ``curses`` imports: the values here are
plain data so they can be unit-tested, reused by a future GUI/web front-end,
and documented in ``docs/DESIGN.md`` without starting a terminal.

Design rules encoded here
-------------------------
* One accent, three semantic states. Everything else is neutral.
* No default terminal colours as "brand" colours: cyan-on-black is avoided.
* Low-saturation palette (muted sage / amber / rose / steel) so failures read
  as information, not as an alarm light.
* State is never carried by colour alone -- every state has a glyph and a word.
"""

from __future__ import annotations

from typing import Final

# --- Palette -----------------------------------------------------------------
# Hex values are the source of truth for documentation and non-curses clients.
# They are deliberately desaturated; nothing here is neon or gradient-friendly.
PALETTE: Final[dict[str, str]] = {
    "text": "#d0d0d0",
    "muted": "#8a8a8a",
    "rule": "#585858",
    "accent": "#83a598",
    "ok": "#87a987",
    "warn": "#d7a263",
    "error": "#c46a6a",
}

# xterm-256 indices matching PALETTE above.
COLOR_256: Final[dict[str, int]] = {
    "text": 252,
    "muted": 245,
    "rule": 240,
    "accent": 109,
    "ok": 108,
    "warn": 179,
    "error": 174,
}

# Basic 8-colour terminals: keep the same semantics, accept less nuance.
COLOR_8: Final[dict[str, int]] = {
    "text": 7,  # white
    "muted": 7,
    "rule": 7,
    "accent": 6,  # cyan
    "ok": 2,  # green
    "warn": 3,  # yellow
    "error": 1,  # red
}

ROLES: Final[tuple[str, ...]] = tuple(PALETTE)

# --- Glyphs ------------------------------------------------------------------
# Unicode is used when the terminal locale can encode it; ASCII is a real
# fallback, not an afterthought (Termux, busybox, C locale, dumb terminals).
GLYPHS: Final[dict[str, dict[str, str]]] = {
    "unicode": {
        "on": "●",
        "off": "○",
        "pending": "◐",
        "bad": "✕",
        "arrow": "→",
        "dot": "·",
        "rule": "─",
        "check": "✓",
        "ellipsis": "…",
        "prompt": "›",
    },
    "ascii": {
        "on": "*",
        "off": "o",
        "pending": "~",
        "bad": "x",
        "arrow": "->",
        "dot": "-",
        "rule": "-",
        "check": "ok",
        "ellipsis": "...",
        "prompt": ">",
    },
}

# --- Layout ------------------------------------------------------------------
# A single 2-column row of 4 reads better than a tall list, and collapses to
# one column before the terminal gets cramped.
LAYOUT: Final[dict[str, int]] = {
    "margin": 2,
    "max_width": 98,
    "label_width": 14,
    "value_gap": 2,
    "two_column_min": 88,
    "min_width": 44,
    "min_height": 16,
    "log_rows_min": 2,
}

# Duration after which a transient message is replaced by the idle hint.
MESSAGE_SECONDS: Final[float] = 6.0
