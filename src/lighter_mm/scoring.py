"""MM Opportunity Score — single place for ranking logic.

Uses cross-sectional percentile ranks plus hard penalties.
This is intentionally editable without touching collectors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lighter_mm.analytics.estimated_fill_policy import MIN_MEANINGFUL_SAMPLES
from lighter_mm.util import percentile

ORDER_SIZE_USD = [25.0, 50.0, 100.0, 250.0, 500.0, 1000.0]


@dataclass
class ScoreWeights:
    trade_activity: float = 15.0
    estimated_maker_fill: float = 20.0
    spread: float = 20.0
    two_sided_depth: float = 15.0
    maker_markout: float = 20.0
    data_quality_persistence: float = 10.0


@dataclass
class CandidateThresholds:
    min_coverage_pct: float = 90.0
    min_trades_per_hour: float = 30.0
    min_two_sided_depth_10bps_usd: float = 200.0
    min_median_spread_bps: float = 1.0
    min_markout_samples_5s: int = 20
    min_markout_samples_30s: int = 20
    min_estimated_maker_fill_samples: int = MIN_MEANINGFUL_SAMPLES
    min_median_trades_per_minute: float | None = None
    min_observation_hours: float = 1.0
    min_markout_5s_median_bps: float = -5.0
    min_markout_30s_median_bps: float = -15.0


@dataclass
class ScoredMarket:
    row: dict[str, Any]
    score: float
    rank_components: dict[str, float | None]
    penalties: list[str] = field(default_factory=list)
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    candidate: bool = False
    letter_rank: str = "D"
    recommended_max_order_usd: float | None = None
    size_fit: dict[str, bool] = field(default_factory=dict)


def _pct_rank(values: list[float | None], x: float | None) -> float:
    xs = [v for v in values if v is not None]
    if not xs or x is None:
        return 0.0
    # fraction of values strictly less than x, plus half ties
    less = sum(1 for v in xs if v < x)
    ties = sum(1 for v in xs if v == x)
    return (less + 0.5 * ties) / len(xs) * 100.0


def recommended_max_order(depth_5: float | None, depth_10: float | None) -> float | None:
    """Conservative: 25% of median two-sided depth at 10bps (fallback 5bps)."""
    base = depth_10 if depth_10 is not None else depth_5
    if base is None or base <= 0:
        return None
    # Snap down to nearest configured notional
    cap = base * 0.25
    fit = [n for n in ORDER_SIZE_USD if n <= cap]
    return fit[-1] if fit else None


def size_fit_map(depth_10: float | None) -> dict[str, bool]:
    out = {}
    d = depth_10 or 0.0
    for n in ORDER_SIZE_USD:
        # Require two-sided depth at least 4x order size within 10bps
        out[f"fit_{int(n)}"] = d >= n * 4.0
    return out


def build_narratives(
    row: dict[str, Any],
    penalties: list[str],
    *,
    min_coverage_pct: float = 90.0,
    thresholds: CandidateThresholds | None = None,
) -> tuple[list[str], list[str], list[str]]:
    thr = thresholds or CandidateThresholds()
    coverage_floor = thr.min_coverage_pct if thresholds is not None else min_coverage_pct
    fill_min = thr.min_estimated_maker_fill_samples

    pros: list[str] = []
    cons: list[str] = []
    warnings: list[str] = []

    med_spread = row.get("median_spread_bps")
    pers5 = row.get("pct_time_spread_ge_5bps")
    depth10 = row.get("median_two_sided_depth_10bps_usd")
    tpm = row.get("trades_per_minute_median")
    m5 = row.get("maker_markout_5s_median_bps")
    m30 = row.get("maker_markout_30s_median_bps")
    vol5 = row.get("p95_abs_mid_move_5s_bps")
    funding = row.get("current_funding_rate")

    if med_spread is not None and med_spread >= 3:
        pros.append("persistent/usable spread" if (pers5 or 0) >= 0.25 else "meaningful median spread")
    if (pers5 or 0) >= 0.35:
        pros.append("spread >=5bp for substantial fraction of time")
    fill30 = row.get("estimated_maker_fill_rate_30s_conservative")
    fill_samples = int(row.get("estimated_maker_fill_samples") or 0)

    if (tpm or 0) >= 5:
        pros.append("high trade frequency (market-level; not Estimated Maker Fill)")
    if fill30 is not None and fill30 >= 0.3:
        pros.append("meaningful Estimated Maker Fill @30s conservative ($50)")
    if (depth10 or 0) >= 1000:
        pros.append("sufficient two-sided depth within ±10bp")
    if m5 is not None and m5 > 0:
        pros.append("positive 5s maker markout")
    if m30 is not None and m30 > 0:
        pros.append("positive 30s maker markout")

    if med_spread is not None and med_spread < 1.5:
        cons.append("tight spread — limited edge after fees/latency")
    if (tpm or 0) < 1:
        cons.append("low trade activity")
    if fill30 is not None and fill30 <= 0.0 and fill_samples >= fill_min:
        cons.append("Estimated Maker Fill ~0 @30s conservative (touch rarely clears)")
    elif fill_samples < fill_min:
        warnings.append(
            f"Estimated Maker Fill sample insufficient (<{fill_min})"
        )
    if (depth10 or 0) < 500:
        cons.append("thin two-sided depth")
    if m5 is not None and m5 < 0:
        cons.append("negative 5s maker markout (adverse selection)")
    if vol5 is not None and med_spread is not None and vol5 > med_spread * 2:
        cons.append("elevated 5s volatility vs spread")

    if funding is not None and abs(float(funding)) > 0.01:
        warnings.append("funding relatively high")
    cov = row.get("observation_coverage_pct") or row.get("data_coverage_pct") or 0
    if coverage_floor <= cov < 95:
        warnings.append("data coverage below 95%")
    for p in penalties:
        if p not in warnings:
            warnings.append(p)

    # Structured summary lines for report cards
    summary = [
        f"SPREAD: median {med_spread:.2f}bp" if med_spread is not None else "SPREAD: n/a",
        f"DEPTH: ${depth10:,.0f} within ±10bp" if depth10 is not None else "DEPTH: n/a",
        f"ACTIVITY: {tpm:.2f} trades/min (market-level)" if tpm is not None else "ACTIVITY: n/a",
        (
            f"MARKOUT: {m5:+.2f}bp @5s / {m30:+.2f}bp @30s"
            if m5 is not None and m30 is not None
            else "MARKOUT: n/a"
        ),
        (
            f"EST. FILL: {fill30*100:.0f}% @30s cons. ($50)"
            if fill30 is not None
            else "EST. FILL: n/a"
        ),
        (
            f"PERSISTENCE: spread >=5bp for {(pers5 or 0)*100:.0f}% of observed time"
        ),
    ]
    pros = summary + pros
    return pros, cons, warnings


def letter_rank(score: float, candidate: bool, penalties: list[str]) -> str:
    hard = any("strong penalty" in p.lower() or "coverage" in p.lower() for p in penalties)
    if not candidate or hard:
        if score >= 55:
            return "C"
        return "D"
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 45:
        return "C"
    return "D"


def _activity_blend_series(rows: list[dict[str, Any]]) -> list[float | None]:
    activity_median = [r.get("trades_per_minute_median") for r in rows]
    activity_mean = [r.get("trades_per_minute_mean") for r in rows]
    # Blend mean (70%) + median (30%) for cross-sectional activity rank.
    activity_blend: list[float | None] = []
    for i in range(len(rows)):
        m = activity_mean[i]
        med = activity_median[i]
        if m is not None and med is not None:
            activity_blend.append(0.7 * float(m) + 0.3 * float(med))
        elif m is not None:
            activity_blend.append(float(m))
        elif med is not None:
            activity_blend.append(float(med))
        else:
            activity_blend.append(None)
    return activity_blend


def _spread_for_rank_series(rows: list[dict[str, Any]]) -> list[float | None]:
    spreads = [r.get("median_spread_bps") for r in rows]
    spread_for_rank: list[float | None] = []
    for s in spreads:
        if s is None:
            spread_for_rank.append(None)
        else:
            # diminishing returns above 20bp
            spread_for_rank.append(min(float(s), 20.0))
    return spread_for_rank


def _dq_raw_value(
    persistence: float | None, coverage: float | None
) -> float | None:
    if persistence is not None and coverage is not None:
        return float(persistence) * 100.0 * 0.6 + float(coverage) * 0.4
    if coverage is not None:
        return float(coverage)
    return None


def _compute_rank_components(
    rows: list[dict[str, Any]],
    *,
    index: int,
    activity: list[float | None],
    spread_for_rank: list[float | None],
    depth: list[float | None],
    markout: list[float | None],
    estimated_fill: list[float | None],
    persistence: list[float | None],
    coverage: list[float | None],
) -> dict[str, float | None]:
    pr_act = _pct_rank(activity, activity[index] if index < len(activity) else None)
    pr_spr = _pct_rank(
        spread_for_rank,
        spread_for_rank[index] if index < len(spread_for_rank) else None,
    )
    pr_dep = _pct_rank(depth, depth[index] if index < len(depth) else None)
    pr_mk = _pct_rank(markout, markout[index] if index < len(markout) else None)
    fill_val = estimated_fill[index] if index < len(estimated_fill) else None
    if fill_val is None:
        pr_fill: float | None = None
    else:
        pr_fill = _pct_rank(estimated_fill, fill_val)

    pers_val = persistence[index] if index < len(persistence) else None
    cov_val = coverage[index] if index < len(coverage) else None
    dq_raw = _dq_raw_value(pers_val, cov_val)
    dq_vals = [
        _dq_raw_value(
            persistence[j] if j < len(persistence) else None,
            coverage[j] if j < len(coverage) else None,
        )
        for j in range(len(rows))
    ]
    pr_dq = _pct_rank(dq_vals, dq_raw)

    return {
        "trade_activity": pr_act,
        "estimated_maker_fill": pr_fill,
        "spread": pr_spr,
        "two_sided_depth": pr_dep,
        "maker_markout": pr_mk,
        "data_quality_persistence": pr_dq,
    }


def _compute_rank_components_for_row(
    row: dict[str, Any],
    *,
    activity: list[float | None],
    spread_for_rank: list[float | None],
    depth: list[float | None],
    markout: list[float | None],
    estimated_fill: list[float | None],
    persistence: list[float | None],
    coverage: list[float | None],
    dq_vals: list[float | None],
) -> dict[str, float | None]:
    """Cross-sectional ranks for one row against peer value series."""
    act = row.get("trades_per_minute_median")
    act_mean = row.get("trades_per_minute_mean")
    if act is not None and act_mean is not None:
        activity_val: float | None = 0.7 * float(act_mean) + 0.3 * float(act)
    elif act_mean is not None:
        activity_val = float(act_mean)
    elif act is not None:
        activity_val = float(act)
    else:
        activity_val = None

    spread_val = row.get("median_spread_bps")
    if spread_val is not None:
        spread_val = min(float(spread_val), 20.0)

    pr_act = _pct_rank(activity, activity_val)
    pr_spr = _pct_rank(spread_for_rank, spread_val)
    pr_dep = _pct_rank(depth, row.get("median_two_sided_depth_10bps_usd"))
    pr_mk = _pct_rank(markout, row.get("maker_markout_5s_median_bps"))
    fill_val = row.get("estimated_maker_fill_rate_30s_conservative")
    if fill_val is None:
        pr_fill: float | None = None
    else:
        pr_fill = _pct_rank(estimated_fill, fill_val)

    pers_val = row.get("pct_time_spread_ge_5bps")
    cov_val = row.get("observation_coverage_pct") or row.get("data_coverage_pct")
    dq_raw = _dq_raw_value(pers_val, cov_val)
    pr_dq = _pct_rank(dq_vals, dq_raw)

    return {
        "trade_activity": pr_act,
        "estimated_maker_fill": pr_fill,
        "spread": pr_spr,
        "two_sided_depth": pr_dep,
        "maker_markout": pr_mk,
        "data_quality_persistence": pr_dq,
    }


def _peer_dq_values(
    peer: list[dict[str, Any]],
) -> list[float | None]:
    return [
        _dq_raw_value(
            r.get("pct_time_spread_ge_5bps"),
            r.get("observation_coverage_pct") or r.get("data_coverage_pct"),
        )
        for r in peer
    ]


def _peer_activity_values(peer: list[dict[str, Any]]) -> list[float | None]:
    out: list[float | None] = []
    for r in peer:
        m = r.get("trades_per_minute_mean")
        med = r.get("trades_per_minute_median")
        if m is not None and med is not None:
            out.append(0.7 * float(m) + 0.3 * float(med))
        elif m is not None:
            out.append(float(m))
        elif med is not None:
            out.append(float(med))
        else:
            out.append(None)
    return out


def _peer_spread_for_rank(peer: list[dict[str, Any]]) -> list[float | None]:
    out: list[float | None] = []
    for r in peer:
        s = r.get("median_spread_bps")
        if s is None:
            out.append(None)
        else:
            out.append(min(float(s), 20.0))
    return out


def _compute_weighted_score(
    components: dict[str, float | None],
    weights: ScoreWeights,
) -> float:
    weight_total = (
        weights.trade_activity
        + weights.estimated_maker_fill
        + weights.spread
        + weights.two_sided_depth
        + weights.maker_markout
        + weights.data_quality_persistence
    )
    component_weights = {
        "trade_activity": weights.trade_activity,
        "estimated_maker_fill": weights.estimated_maker_fill,
        "spread": weights.spread,
        "two_sided_depth": weights.two_sided_depth,
        "maker_markout": weights.maker_markout,
        "data_quality_persistence": weights.data_quality_persistence,
    }
    weighted_sum = 0.0
    used_weight = 0.0
    for name, pr in components.items():
        if pr is None:
            continue
        w = component_weights[name]
        weighted_sum += float(pr) * w
        used_weight += w
    # Renormalize when Estimated Fill (or another component) is unavailable.
    denom = used_weight if used_weight > 0 else weight_total
    return weighted_sum / denom


def _apply_score_penalties(
    row: dict[str, Any],
    score: float,
    thresholds: CandidateThresholds,
) -> tuple[float, list[str]]:
    penalties: list[str] = []
    cov = row.get("observation_coverage_pct") or row.get("data_coverage_pct") or 0.0
    if cov < thresholds.min_coverage_pct:
        score *= 0.55
        penalties.append(
            f"strong penalty: observation coverage < {thresholds.min_coverage_pct:.0f}%"
        )
    tc = row.get("total_trade_count") or 0
    hours = max(row.get("observation_hours") or 1.0, 0.01)
    if tc < thresholds.min_trades_per_hour * hours * 0.25:
        score *= 0.5
        penalties.append("strong penalty: extremely low trade count")
    d10 = row.get("median_two_sided_depth_10bps_usd") or 0.0
    if d10 < thresholds.min_two_sided_depth_10bps_usd:
        score *= 0.75
        penalties.append("penalty: two-sided depth very thin")
    m5 = row.get("maker_markout_5s_median_bps")
    if m5 is not None and m5 < 0:
        score *= 0.7
        penalties.append("penalty: median 5s maker markout < 0")
    m30 = row.get("maker_markout_30s_median_bps")
    if m30 is not None and m30 < thresholds.min_markout_30s_median_bps:
        score *= 0.55
        penalties.append("strong penalty: 30s markout largely negative")
    fill30 = row.get("estimated_maker_fill_rate_30s_conservative")
    fill_samples = int(row.get("estimated_maker_fill_samples") or 0)
    if (
        fill30 is not None
        and fill30 <= 0.0
        and fill_samples >= thresholds.min_estimated_maker_fill_samples
    ):
        score *= 0.85
        penalties.append(
            "penalty: Estimated Maker Fill ~0 @30s conservative ($50)"
        )
    score = max(0.0, min(100.0, score))
    return score, penalties


def _is_candidate(
    row: dict[str, Any],
    *,
    thresholds: CandidateThresholds,
    pr_act: float,
    peer_count: int,
) -> bool:
    cov = row.get("observation_coverage_pct") or row.get("data_coverage_pct") or 0.0
    d10 = row.get("median_two_sided_depth_10bps_usd") or 0.0
    m5 = row.get("maker_markout_5s_median_bps")
    m30 = row.get("maker_markout_30s_median_bps")
    fill_samples = int(row.get("estimated_maker_fill_samples") or 0)

    tpm_median = float(row.get("trades_per_minute_median") or 0.0)
    tpm_mean = float(row.get("trades_per_minute_mean") or 0.0)
    hours_obs = max(row.get("observation_hours") or 1.0, 0.01)
    trades_per_hour = (row.get("total_trade_count") or 0) / hours_obs
    trades_per_hour_from_mean = tpm_mean * 60.0
    activity_ok = (
        trades_per_hour >= thresholds.min_trades_per_hour
        or trades_per_hour_from_mean >= thresholds.min_trades_per_hour
    )
    if thresholds.min_median_trades_per_minute is not None:
        activity_ok = activity_ok and tpm_median >= thresholds.min_median_trades_per_minute

    m5_count = int(row.get("markout_5s_count") or 0)
    m30_count = int(row.get("markout_30s_count") or 0)
    obs_hours = max(row.get("observation_hours") or 0.0, 0.0)
    observation_ok = obs_hours >= thresholds.min_observation_hours

    fill_sample_ok = fill_samples >= thresholds.min_estimated_maker_fill_samples
    candidate = (
        cov >= thresholds.min_coverage_pct
        and activity_ok
        and observation_ok
        and d10 >= thresholds.min_two_sided_depth_10bps_usd
        and (row.get("median_spread_bps") or 0) >= thresholds.min_median_spread_bps
        and m5 is not None
        and m5 >= thresholds.min_markout_5s_median_bps
        and m30 is not None
        and m30 >= thresholds.min_markout_30s_median_bps
        and m5_count >= thresholds.min_markout_samples_5s
        and m30_count >= thresholds.min_markout_samples_30s
        and fill_sample_ok
    )
    # Also require activity percentile not bottom 20% among peers when enough markets
    if peer_count >= 10 and pr_act < 20:
        candidate = False
    return candidate


def score_markets(
    rows: list[dict[str, Any]],
    weights: ScoreWeights | None = None,
    thresholds: CandidateThresholds | None = None,
    peer_rows: list[dict[str, Any]] | None = None,
) -> list[ScoredMarket]:
    weights = weights or ScoreWeights()
    thresholds = thresholds or CandidateThresholds()
    if not rows:
        return []

    peer = peer_rows if peer_rows is not None else rows

    if peer_rows is not None:
        activity = _peer_activity_values(peer)
        spread_for_rank = _peer_spread_for_rank(peer)
        depth = [r.get("median_two_sided_depth_10bps_usd") for r in peer]
        markout = [r.get("maker_markout_5s_median_bps") for r in peer]
        estimated_fill = [r.get("estimated_maker_fill_rate_30s_conservative") for r in peer]
        persistence = [r.get("pct_time_spread_ge_5bps") for r in peer]
        coverage = [
            r.get("observation_coverage_pct") or r.get("data_coverage_pct") for r in peer
        ]
        dq_vals = _peer_dq_values(peer)
        peer_count = len(peer)
    else:
        activity = _activity_blend_series(rows)
        spread_for_rank = _spread_for_rank_series(rows)
        depth = [r.get("median_two_sided_depth_10bps_usd") for r in rows]
        markout = [r.get("maker_markout_5s_median_bps") for r in rows]
        estimated_fill = [r.get("estimated_maker_fill_rate_30s_conservative") for r in rows]
        persistence = [r.get("pct_time_spread_ge_5bps") for r in rows]
        coverage = [
            r.get("observation_coverage_pct") or r.get("data_coverage_pct") for r in rows
        ]
        dq_vals = _peer_dq_values(rows)
        peer_count = len(rows)

    scored: list[ScoredMarket] = []
    for i, row in enumerate(rows):
        if peer_rows is not None:
            components = _compute_rank_components_for_row(
                row,
                activity=activity,
                spread_for_rank=spread_for_rank,
                depth=depth,
                markout=markout,
                estimated_fill=estimated_fill,
                persistence=persistence,
                coverage=coverage,
                dq_vals=dq_vals,
            )
        else:
            components = _compute_rank_components(
                rows,
                index=i,
                activity=activity,
                spread_for_rank=spread_for_rank,
                depth=depth,
                markout=markout,
                estimated_fill=estimated_fill,
                persistence=persistence,
                coverage=coverage,
            )
        score = _compute_weighted_score(components, weights)
        score, penalties = _apply_score_penalties(row, score, thresholds)

        pr_act = float(components["trade_activity"] or 0.0)
        candidate = _is_candidate(
            row,
            thresholds=thresholds,
            pr_act=pr_act,
            peer_count=peer_count,
        )

        pros, cons, warnings = build_narratives(
            row,
            penalties,
            min_coverage_pct=thresholds.min_coverage_pct,
            thresholds=thresholds,
        )
        rec = recommended_max_order(
            row.get("median_two_sided_depth_5bps_usd"),
            row.get("median_two_sided_depth_10bps_usd"),
        )
        letter = letter_rank(score, candidate, penalties)
        scored.append(
            ScoredMarket(
                row=row,
                score=score,
                rank_components=components,
                penalties=penalties,
                pros=pros,
                cons=cons,
                warnings=warnings,
                candidate=candidate,
                letter_rank=letter,
                recommended_max_order_usd=rec,
                size_fit=size_fit_map(row.get("median_two_sided_depth_10bps_usd")),
            )
        )

    scored.sort(key=lambda s: s.score, reverse=True)
    return scored


def avoid_wide_spread_markets(scored: list[ScoredMarket], n: int = 10) -> list[ScoredMarket]:
    """Wide spread but poor MM suitability."""
    bad = [
        s
        for s in scored
        if (s.row.get("median_spread_bps") or 0) >= 5
        and (
            s.letter_rank in {"C", "D"}
            or (s.row.get("maker_markout_5s_median_bps") or 0) < 0
            or (s.row.get("trades_per_minute_median") or 0) < 0.5
            or (s.row.get("median_two_sided_depth_10bps_usd") or 0) < 300
        )
    ]
    bad.sort(key=lambda s: (s.row.get("median_spread_bps") or 0), reverse=True)
    return bad[:n]


def cross_section_stats(values: list[float]) -> dict[str, float | None]:
    return {
        "p10": percentile(values, 10),
        "p25": percentile(values, 25),
        "p50": percentile(values, 50),
        "p75": percentile(values, 75),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
    }
