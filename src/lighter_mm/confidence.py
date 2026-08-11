"""Data Confidence layer — heuristic reliability index for MM Opportunity Score.

Confidence measures how much we trust the existing score based on sample counts,
coverage, and observation duration. It is not a statistical confidence interval.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

SampleState = Literal["missing", "zero", "value"]


@dataclass(frozen=True)
class ConfidenceConfig:
    k_markout: float = 200.0
    k_fill: float = 200.0
    k_trade: float = 300.0
    duration_tau_hours: float = 24.0
    geomean_epsilon: float = 0.05
    low_confidence_threshold: float = 0.30
    high_confidence_threshold: float = 0.80
    weight_markout: float = 0.35
    weight_fill: float = 0.25
    weight_coverage: float = 0.20
    weight_trade: float = 0.10
    weight_duration: float = 0.10
    markout_weight_5s: float = 0.70
    markout_weight_30s: float = 0.30

    @property
    def leaf_weight_markout_5s(self) -> float:
        return self.weight_markout * self.markout_weight_5s

    @property
    def leaf_weight_markout_30s(self) -> float:
        return self.weight_markout * self.markout_weight_30s


DEFAULT_CONFIG = ConfidenceConfig()

# Coverage confidence anchors (pct, confidence) — linear between points, >=99 → 1.0
_COVERAGE_ANCHORS: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (80.0, 0.50),
    (90.0, 0.80),
    (95.0, 0.95),
    (99.0, 1.0),
)

@dataclass
class ConfidenceResult:
    confidence: float
    effective_score: float
    confidence_label: str
    confidence_reasons: list[str] = field(default_factory=list)
    confidence_breakdown: dict[str, Any] = field(default_factory=dict)


def sample_confidence(n: float, k: float) -> float:
    """Hill / saturation curve: n / (n + k)."""
    if k <= 0:
        return 1.0
    nn = max(0.0, float(n))
    return nn / (nn + k)


def coverage_confidence_from_pct(coverage_pct: float) -> float:
    if coverage_pct >= 99.0:
        return 1.0
    for i in range(len(_COVERAGE_ANCHORS) - 1):
        x0, y0 = _COVERAGE_ANCHORS[i]
        x1, y1 = _COVERAGE_ANCHORS[i + 1]
        if x0 <= coverage_pct <= x1:
            if x1 == x0:
                return y1
            t = (coverage_pct - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return 1.0


def duration_confidence_from_hours(hours: float, tau_hours: float) -> float:
    if tau_hours <= 0:
        return 1.0
    h = max(0.0, float(hours))
    return 1.0 - math.exp(-h / tau_hours)


def _is_finite_number(raw: Any) -> bool:
    if raw is None:
        return False
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v)


def _parse_sample_field(raw: Any) -> tuple[SampleState, float | None]:
    """Parse sample count: missing, observed zero, or positive value."""
    if raw is None:
        return "missing", None
    if not _is_finite_number(raw):
        return "missing", None
    v = float(raw)
    if v < 0:
        return "missing", None
    if v == 0:
        return "zero", 0.0
    return "value", v


def _parse_duration_field(raw: Any) -> tuple[SampleState, float | None]:
    if raw is None:
        return "missing", None
    if not _is_finite_number(raw):
        return "missing", None
    v = float(raw)
    if v < 0:
        return "missing", None
    if v == 0:
        return "zero", 0.0
    return "value", v


def _parse_coverage_field(raw: Any) -> tuple[SampleState, float | None]:
    if raw is None:
        return "missing", None
    if not _is_finite_number(raw):
        return "missing", None
    v = float(raw)
    if v < 0 or v > 100:
        return "missing", None
    return "value", v


def _resolve_coverage_pct(row: dict[str, Any]) -> tuple[SampleState, float | None]:
    data_state, data_val = _parse_coverage_field(row.get("data_coverage_pct"))
    obs_state, obs_val = _parse_coverage_field(row.get("observation_coverage_pct"))
    if data_state == "missing" and obs_state == "missing":
        return "missing", None
    vals: list[float] = []
    if data_state != "missing" and data_val is not None:
        vals.append(data_val)
    if obs_state != "missing" and obs_val is not None:
        vals.append(obs_val)
    if not vals:
        return "missing", None
    return "value", min(vals)


def _leaf_weight(key: str, config: ConfidenceConfig) -> float:
    if key == "markout_5s":
        return config.leaf_weight_markout_5s
    if key == "markout_30s":
        return config.leaf_weight_markout_30s
    if key == "fill":
        return config.weight_fill
    if key == "coverage":
        return config.weight_coverage
    if key == "trade":
        return config.weight_trade
    if key == "duration":
        return config.weight_duration
    raise ValueError(f"unknown leaf key: {key}")


def _component_confidence(
    key: str,
    state: SampleState,
    raw_value: float | None,
    config: ConfidenceConfig,
) -> float | None:
    """Return component confidence for aggregation, or None if missing."""
    if state == "missing":
        return None
    if state == "zero":
        return 0.0
    if raw_value is None:
        return None
    if key == "markout_5s":
        return sample_confidence(raw_value, config.k_markout)
    if key == "markout_30s":
        return sample_confidence(raw_value, config.k_markout)
    if key == "fill":
        return sample_confidence(raw_value, config.k_fill)
    if key == "trade":
        return sample_confidence(raw_value, config.k_trade)
    if key == "coverage":
        return coverage_confidence_from_pct(raw_value)
    if key == "duration":
        return duration_confidence_from_hours(raw_value, config.duration_tau_hours)
    return None


def _breakdown_display_value(state: SampleState, confidence: float | None) -> Any:
    if state == "missing":
        return None
    if confidence is None:
        return None
    return confidence


def _compute_markout_summary(
    c5_state: SampleState,
    c5_conf: float | None,
    c30_state: SampleState,
    c30_conf: float | None,
    config: ConfidenceConfig,
) -> Any:
    """Dashboard/debug summary — not used in overall confidence."""
    eps = config.geomean_epsilon
    internal: list[tuple[float, float]] = []
    if c5_state != "missing" and c5_conf is not None:
        internal.append((config.markout_weight_5s, c5_conf))
    if c30_state != "missing" and c30_conf is not None:
        internal.append((config.markout_weight_30s, c30_conf))
    if not internal:
        return None
    fraction = sum(w for w, _ in internal)
    if fraction <= 0:
        return None
    geo = 1.0
    for w, c in internal:
        c_agg = max(c, eps)
        geo *= c_agg ** (w / fraction)
    return geo * fraction


def _overall_confidence(
    present: dict[str, float],
    config: ConfidenceConfig,
) -> float:
    if not present:
        return 0.0
    available_weight = sum(_leaf_weight(k, config) for k in present)
    if available_weight <= 0:
        return 0.0
    eps = config.geomean_epsilon
    geo = 1.0
    for key, conf in present.items():
        w = _leaf_weight(key, config)
        c_agg = max(conf, eps)
        geo *= c_agg ** (w / available_weight)
    overall = geo * available_weight
    return max(0.0, min(1.0, overall))


def _confidence_label(confidence: float, config: ConfidenceConfig) -> str:
    if confidence < config.low_confidence_threshold:
        return "low"
    if confidence < config.high_confidence_threshold:
        return "medium"
    return "high"


def _build_reasons(
    states: dict[str, SampleState],
    breakdown: dict[str, Any],
    coverage_pct: float | None,
    config: ConfidenceConfig,
) -> list[str]:
    reasons: list[str] = []
    m5 = breakdown.get("markout_5s")
    m30 = breakdown.get("markout_30s")
    markout_low = False
    if m5 is not None and m5 < 0.25:
        markout_low = True
    if m30 is not None and m30 < 0.25:
        markout_low = True
    if markout_low:
        reasons.append("low_markout_samples")
    fill = breakdown.get("estimated_fill")
    if fill is not None and fill < 0.25:
        reasons.append("low_fill_samples")
    trades = breakdown.get("trades")
    if trades is not None and trades < 0.25:
        reasons.append("low_trade_observations")
    if coverage_pct is not None and coverage_pct < 95.0:
        reasons.append("low_coverage")
    dur = breakdown.get("duration")
    if dur is not None and dur < 0.30:
        reasons.append("short_observation_duration")
    if states.get("markout_5s") == "missing" or states.get("markout_30s") == "missing":
        if "low_markout_samples" not in reasons:
            reasons.append("missing_markout_samples")
    if states.get("fill") == "missing":
        reasons.append("missing_fill_samples")
    if states.get("trade") == "missing":
        reasons.append("missing_trade_observations")
    if states.get("coverage") == "missing":
        reasons.append("missing_coverage")
    if states.get("duration") == "missing":
        reasons.append("missing_observation_duration")
    return reasons[:3]


def compute_market_confidence(
    row: dict[str, Any],
    raw_score: float,
    *,
    config: ConfidenceConfig | None = None,
) -> ConfidenceResult:
    config = config or DEFAULT_CONFIG

    m5_state, m5_val = _parse_sample_field(row.get("markout_5s_count"))
    m30_state, m30_val = _parse_sample_field(row.get("markout_30s_count"))
    fill_state, fill_val = _parse_sample_field(row.get("estimated_maker_fill_samples"))
    trade_state, trade_val = _parse_sample_field(row.get("total_trade_count"))
    cov_state, cov_val = _resolve_coverage_pct(row)
    dur_state, dur_val = _parse_duration_field(row.get("observation_hours"))

    states = {
        "markout_5s": m5_state,
        "markout_30s": m30_state,
        "fill": fill_state,
        "trade": trade_state,
        "coverage": cov_state,
        "duration": dur_state,
    }

    leaf_conf: dict[str, float | None] = {
        "markout_5s": _component_confidence("markout_5s", m5_state, m5_val, config),
        "markout_30s": _component_confidence("markout_30s", m30_state, m30_val, config),
        "fill": _component_confidence("fill", fill_state, fill_val, config),
        "trade": _component_confidence("trade", trade_state, trade_val, config),
        "coverage": _component_confidence("coverage", cov_state, cov_val, config),
        "duration": _component_confidence("duration", dur_state, dur_val, config),
    }

    present = {k: v for k, v in leaf_conf.items() if v is not None}
    overall = _overall_confidence(present, config)

    markout_summary = _compute_markout_summary(
        m5_state,
        leaf_conf["markout_5s"],
        m30_state,
        leaf_conf["markout_30s"],
        config,
    )

    breakdown: dict[str, Any] = {
        "markout": markout_summary,
        "markout_5s": _breakdown_display_value(m5_state, leaf_conf["markout_5s"]),
        "markout_30s": _breakdown_display_value(m30_state, leaf_conf["markout_30s"]),
        "estimated_fill": _breakdown_display_value(fill_state, leaf_conf["fill"]),
        "trades": _breakdown_display_value(trade_state, leaf_conf["trade"]),
        "coverage": _breakdown_display_value(cov_state, leaf_conf["coverage"]),
        "duration": _breakdown_display_value(dur_state, leaf_conf["duration"]),
    }

    label = _confidence_label(overall, config)
    reasons = _build_reasons(states, breakdown, cov_val, config)
    effective = max(0.0, min(100.0, raw_score * overall))

    return ConfidenceResult(
        confidence=overall,
        effective_score=effective,
        confidence_label=label,
        confidence_reasons=reasons,
        confidence_breakdown=breakdown,
    )


def confidence_fields_for_json(result: ConfidenceResult, raw_score: float) -> dict[str, Any]:
    """Serialize confidence fields for dashboard / API JSON."""
    return {
        "raw_score": round(raw_score, 2),
        "confidence": round(result.confidence, 4),
        "effective_score": round(result.effective_score, 2),
        "confidence_label": result.confidence_label,
        "confidence_reasons": list(result.confidence_reasons),
        "confidence_breakdown": dict(result.confidence_breakdown),
    }


def scored_market_sort_key(s: Any) -> tuple[float, float, str]:
    """Deterministic ranking: effective_score, raw_score, symbol."""
    symbol = ""
    row = getattr(s, "row", None)
    if isinstance(row, dict):
        symbol = str(row.get("symbol") or "")
    return (
        -float(getattr(s, "effective_score", 0.0)),
        -float(getattr(s, "score", 0.0)),
        symbol,
    )
