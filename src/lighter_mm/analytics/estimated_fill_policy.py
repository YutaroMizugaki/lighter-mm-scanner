"""Estimated Maker Fill policy constants (single source of truth).

Do not change these values without an explicit product decision — they define
v1 ranking semantics (bucket floor, order sizes, horizons, sample floor).
"""

from __future__ import annotations

# Estimated-fill-only downsample; does not change collector book sampling.
SNAPSHOT_BUCKET_MS = 30_000
ORDER_SIZES_USD = (25.0, 50.0, 100.0)
HORIZONS_S = (5, 30)
# Ranking default hypothetical order size (USD).
DEFAULT_ORDER_USD = 50.0
# Below this sample count, rates are treated as unavailable (not measured 0%).
MIN_MEANINGFUL_SAMPLES = 100
# sample_quality() upper bound for "preliminary" (below → reliable).
PRELIMINARY_SAMPLES = 500
