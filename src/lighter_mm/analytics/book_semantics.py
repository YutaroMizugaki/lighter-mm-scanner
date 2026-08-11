"""Shared book usability / spread SQL predicates (Stage 1 + Full Analyzer)."""

from __future__ import annotations

BOOK_OBSERVED_PREDICATE = """
    is_usable = true
    OR (is_usable IS NULL AND mid IS NOT NULL)
"""

BOOK_METRICS_GOOD_PREDICATE = """
    is_usable = true
    OR (is_usable IS NULL AND is_stale = false AND mid IS NOT NULL)
"""

EFFECTIVE_SPREAD_EXPR = """
    CASE
        WHEN spread_bps IS NOT NULL THEN spread_bps
        WHEN best_bid IS NOT NULL AND best_ask IS NOT NULL AND mid IS NOT NULL
             AND best_bid > 0 AND best_ask > 0 AND mid > 0 AND best_ask >= best_bid
        THEN (best_ask - best_bid) / mid * 10000.0
        ELSE NULL
    END
"""

VALID_BID_ASK_PREDICATE = """
    best_bid IS NOT NULL AND best_ask IS NOT NULL AND mid IS NOT NULL
    AND best_bid > 0 AND best_ask > 0 AND mid > 0 AND best_ask >= best_bid
"""
