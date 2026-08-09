"""MM Opportunity Score — single place for ranking logic.

Uses cross-sectional percentile ranks plus hard penalties.
This is intentionally editable without touching collectors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lighter_mm.util import percentile

ORDER_SIZE_USD = [25.0, 50.0, 100.0, 250.0, 500.0, 1000.0]


@dataclass
class ScoreWeights:
    trade_activity: float = 25.0
    spread: float = 20.0
    two_sided_depth: float = 20.0
    maker_markout: float = 25.0
    data_quality_persistence: float = 10.0


@dataclass
class CandidateThresholds:
    min_coverage_pct: float = 90.0
    min_trades_per_hour: float = 30.0
    min_two_sided_depth_10bps_usd: float = 200.0
    min_median_spread_bps: float = 1.0
    min_markout_samples_5s: int = 20
    min_markout_samples_30s: int = 20
    min_median_trades_per_minute: float | None = None
    min_observation_hours: float = 1.0
    min_markout_5s_median_bps: float = -5.0
    min_markout_30s_median_bps: float = -15.0


@dataclass
class ScoredMarket:
    row: dict[str, Any]
    score: float
    rank_components: dict[str, float]
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
) -> tuple[list[str], list[str], list[str]]:
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
    if (tpm or 0) >= 5:
        pros.append("high trade frequency (market-level; not your fill probability)")
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
    if (depth10 or 0) < 500:
        cons.append("thin two-sided depth")
    if m5 is not None and m5 < 0:
        cons.append("negative 5s maker markout (adverse selection)")
    if vol5 is not None and med_spread is not None and vol5 > med_spread * 2:
        cons.append("elevated 5s volatility vs spread")

    if funding is not None and abs(float(funding)) > 0.01:
        warnings.append("funding relatively high")
    cov = row.get("data_coverage_pct") or 0
    if min_coverage_pct <= cov < 95:
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


def score_markets(
    rows: list[dict[str, Any]],
    weights: ScoreWeights | None = None,
    thresholds: CandidateThresholds | None = None,
) -> list[ScoredMarket]:
    weights = weights or ScoreWeights()
    thresholds = thresholds or CandidateThresholds()
    if not rows:
        return []

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

    spreads = [r.get("median_spread_bps") for r in rows]
    # Prefer moderate spreads: score distance from 0 but cap extreme wide spreads
    spread_for_rank = []
    for s in spreads:
        if s is None:
            spread_for_rank.append(None)
        else:
            # diminishing returns above 20bp
            spread_for_rank.append(min(float(s), 20.0))

    activity = activity_blend
    depth = [r.get("median_two_sided_depth_10bps_usd") for r in rows]
    markout = [r.get("maker_markout_5s_median_bps") for r in rows]
    persistence = [r.get("pct_time_spread_ge_5bps") for r in rows]
    coverage = [r.get("data_coverage_pct") for r in rows]

    scored: list[ScoredMarket] = []
    for i, row in enumerate(rows):
        pr_act = _pct_rank(activity, activity[i])
        pr_spr = _pct_rank(spread_for_rank, spread_for_rank[i])
        pr_dep = _pct_rank(depth, depth[i])
        pr_mk = _pct_rank(markout, markout[i])
        # blend persistence + coverage for DQ component
        dq_raw = None
        if persistence[i] is not None and coverage[i] is not None:
            dq_raw = float(persistence[i]) * 100.0 * 0.6 + float(coverage[i]) * 0.4
        elif coverage[i] is not None:
            dq_raw = float(coverage[i])
        dq_vals = []
        for j in range(len(rows)):
            if persistence[j] is not None and coverage[j] is not None:
                dq_vals.append(float(persistence[j]) * 100.0 * 0.6 + float(coverage[j]) * 0.4)
            elif coverage[j] is not None:
                dq_vals.append(float(coverage[j]))
            else:
                dq_vals.append(None)
        pr_dq = _pct_rank(dq_vals, dq_raw)

        components = {
            "trade_activity": pr_act,
            "spread": pr_spr,
            "two_sided_depth": pr_dep,
            "maker_markout": pr_mk,
            "data_quality_persistence": pr_dq,
        }
        score = (
            components["trade_activity"] * weights.trade_activity
            + components["spread"] * weights.spread
            + components["two_sided_depth"] * weights.two_sided_depth
            + components["maker_markout"] * weights.maker_markout
            + components["data_quality_persistence"] * weights.data_quality_persistence
        ) / 100.0

        penalties: list[str] = []
        cov = row.get("data_coverage_pct") or 0.0
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

        score = max(0.0, min(100.0, score))

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
        )
        # Also require activity percentile not bottom 20% among peers when enough markets
        if len(rows) >= 10 and pr_act < 20:
            candidate = False

        pros, cons, warnings = build_narratives(
            row, penalties, min_coverage_pct=thresholds.min_coverage_pct
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
