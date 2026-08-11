"""Real-data A/B benchmark comparison helpers (Legacy vs two-stage Analyzer)."""

from __future__ import annotations

import math
import resource
import sys
from datetime import UTC, datetime
from typing import Any

RAW_METRIC_KEYS: tuple[str, ...] = (
    "median_spread_bps",
    "median_two_sided_depth_10bps_usd",
    "trades_per_minute_mean",
    "trades_per_minute_median",
    "pct_time_spread_ge_5bps",
    "data_coverage_pct",
    "observation_coverage_pct",
    "maker_markout_5s_median_bps",
    "maker_markout_30s_median_bps",
    "markout_5s_count",
    "markout_30s_count",
    "estimated_maker_fill_rate_5s_conservative",
    "estimated_maker_fill_rate_30s_conservative",
    "estimated_maker_fill_rate_5s_optimistic",
    "estimated_maker_fill_rate_30s_optimistic",
    "estimated_maker_fill_samples",
)

INTEGER_METRIC_KEYS: frozenset[str] = frozenset(
    {
        "markout_5s_count",
        "markout_30s_count",
        "estimated_maker_fill_samples",
    }
)

RESULT_PASS = "PASS"
RESULT_PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
RESULT_FAIL = "FAIL"

DEFAULT_ABS_TOL = 1e-9
DEFAULT_REL_TOL = 1e-6
MAX_MISMATCH_DETAILS = 100


def peak_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return usage / (1024 * 1024)
    return usage / 1024


def ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_market_metrics(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in RAW_METRIC_KEYS:
        val = row.get(key)
        if val is None:
            out[key] = None
        elif key in INTEGER_METRIC_KEYS:
            out[key] = int(val)
        else:
            out[key] = float(val)
    return out


def markets_from_result(result: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Map market_id → raw metrics + score from analyze_range output."""
    markets: dict[int, dict[str, Any]] = {}
    for scored in result.get("scored") or []:
        row = scored.row if hasattr(scored, "row") else scored.get("row", scored)
        mid = int(row["market_id"])
        entry: dict[str, Any] = extract_market_metrics(row)
        score = scored.score if hasattr(scored, "score") else scored.get("score")
        entry["score"] = float(score) if score is not None else None
        markets[mid] = entry
    return markets


def build_run_snapshot(
    result: dict[str, Any],
    *,
    mode: str,
    start_ms: int,
    end_ms: int,
    elapsed_seconds: float,
    peak_rss_mb_value: float,
) -> dict[str, Any]:
    scored = result.get("scored") or []
    markets = markets_from_result(result)
    snapshot: dict[str, Any] = {
        "mode": mode,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "hours": (end_ms - start_ms) / 3_600_000.0,
        "elapsed_seconds": elapsed_seconds,
        "peak_rss_mb": peak_rss_mb_value,
        "book_row_count": int(result.get("book_row_count") or 0),
        "trade_row_count": int(result.get("trade_row_count") or 0),
        "markout_row_count": int(result.get("markout_row_count") or 0),
        "latest_book_event_ms": result.get("latest_book_event_ms"),
        "markets_scored": len(scored),
        "markets_listed": len(result.get("markets") or []),
        "candidates": len(result.get("candidates") or []),
        "error": result.get("error"),
        "benchmark_profile": result.get("benchmark_profile") or {},
        "markets": markets,
    }
    if mode == "two-stage" and result.get("two_stage"):
        snapshot["two_stage"] = dict(result["two_stage"])
    return snapshot


def values_match(
    a: Any,
    b: Any,
    key: str,
    *,
    abs_tol: float = DEFAULT_ABS_TOL,
    rel_tol: float = DEFAULT_REL_TOL,
) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if key in INTEGER_METRIC_KEYS:
        return int(a) == int(b)
    return math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=abs_tol)


def _rank_by_score(markets: dict[int, dict[str, Any]]) -> dict[int, int]:
    ranked = sorted(
        markets.items(),
        key=lambda item: (item[1].get("score") is not None, item[1].get("score") or 0.0),
        reverse=True,
    )
    return {mid: idx + 1 for idx, (mid, _) in enumerate(ranked)}


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return xs[int(k)]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def compare_snapshots(
    legacy: dict[str, Any],
    two_stage: dict[str, Any],
    *,
    abs_tol: float = DEFAULT_ABS_TOL,
    rel_tol: float = DEFAULT_REL_TOL,
    min_rss_reduction_pct: float = 30.0,
    min_elapsed_reduction_pct: float = 20.0,
    max_two_stage_rss_mb: float = 2800.0,
) -> dict[str, Any]:
    warnings: list[str] = []
    hard_failures: list[str] = []

    if legacy.get("error"):
        hard_failures.append(f"legacy error: {legacy['error']}")
    if two_stage.get("error"):
        hard_failures.append(f"two-stage error: {two_stage['error']}")

    row_fields = (
        "book_row_count",
        "trade_row_count",
        "markout_row_count",
        "latest_book_event_ms",
    )
    row_counts_match = True
    for field in row_fields:
        lv = legacy.get(field)
        tv = two_stage.get(field)
        if lv != tv:
            row_counts_match = False
            hard_failures.append(f"row count mismatch {field}: legacy={lv} two_stage={tv}")

    latest_book_event_match = legacy.get("latest_book_event_ms") == two_stage.get(
        "latest_book_event_ms"
    )

    legacy_markets = legacy.get("markets") or {}
    two_stage_markets = two_stage.get("markets") or {}
    stage2_ids = sorted(two_stage_markets.keys())

    mismatches: list[dict[str, Any]] = []
    mismatch_count = 0
    for mid in stage2_ids:
        lm = legacy_markets.get(mid)
        tm = two_stage_markets.get(mid)
        if lm is None:
            mismatch_count += 1
            if len(mismatches) < MAX_MISMATCH_DETAILS:
                mismatches.append(
                    {"market_id": mid, "field": "*", "legacy": None, "two_stage": tm}
                )
            continue
        for key in RAW_METRIC_KEYS:
            if not values_match(
                lm.get(key),
                tm.get(key),
                key,
                abs_tol=abs_tol,
                rel_tol=rel_tol,
            ):
                mismatch_count += 1
                if len(mismatches) < MAX_MISMATCH_DETAILS:
                    mismatches.append(
                        {
                            "market_id": mid,
                            "field": key,
                            "legacy": lm.get(key),
                            "two_stage": tm.get(key),
                        }
                    )

    legacy_ranks = _rank_by_score(legacy_markets)
    two_stage_ranks = _rank_by_score(two_stage_markets)
    score_deltas: list[float] = []
    rank_deltas: list[int] = []
    score_details: list[dict[str, Any]] = []
    for mid in stage2_ids:
        lm = legacy_markets.get(mid)
        tm = two_stage_markets.get(mid)
        if not lm or not tm:
            continue
        ls = lm.get("score")
        ts = tm.get("score")
        if ls is not None and ts is not None:
            delta = float(ts) - float(ls)
            score_deltas.append(abs(delta))
            lr = legacy_ranks.get(mid)
            tr = two_stage_ranks.get(mid)
            rd = (tr - lr) if lr is not None and tr is not None else None
            if rd is not None:
                rank_deltas.append(abs(rd))
            score_details.append(
                {
                    "market_id": mid,
                    "legacy_score": ls,
                    "two_stage_score": ts,
                    "score_delta": delta,
                    "abs_score_delta": abs(delta),
                    "legacy_rank": lr,
                    "two_stage_rank": tr,
                    "rank_delta": rd,
                }
            )

    legacy_elapsed = float(legacy.get("elapsed_seconds") or 0.0)
    two_stage_elapsed = float(two_stage.get("elapsed_seconds") or 0.0)
    legacy_rss = float(legacy.get("peak_rss_mb") or 0.0)
    two_stage_rss = float(two_stage.get("peak_rss_mb") or 0.0)

    elapsed_reduction_pct = (
        ((legacy_elapsed - two_stage_elapsed) / legacy_elapsed * 100.0)
        if legacy_elapsed > 0
        else 0.0
    )
    rss_reduction_pct = (
        ((legacy_rss - two_stage_rss) / legacy_rss * 100.0) if legacy_rss > 0 else 0.0
    )

    two_stage_meta = two_stage.get("two_stage") or {}
    markets_total_legacy = int(legacy.get("markets_listed") or legacy.get("markets_scored") or 0)
    markets_total_two = int(two_stage_meta.get("markets_total") or 0)
    if markets_total_two and markets_total_legacy and markets_total_two != markets_total_legacy:
        warnings.append(
            f"markets_total mismatch: legacy_listed={markets_total_legacy} "
            f"two_stage_total={markets_total_two}"
        )

    if mismatch_count > 0:
        hard_failures.append(f"raw metric mismatches: {mismatch_count}")

    if two_stage_rss > max_two_stage_rss_mb:
        warnings.append(
            f"two-stage peak RSS {two_stage_rss:.0f}MB exceeds "
            f"recommended {max_two_stage_rss_mb:.0f}MB"
        )
    if rss_reduction_pct < min_rss_reduction_pct:
        warnings.append(
            f"peak RSS reduction {rss_reduction_pct:.1f}% < target {min_rss_reduction_pct:.1f}%"
        )
    if elapsed_reduction_pct < min_elapsed_reduction_pct:
        warnings.append(
            f"elapsed reduction {elapsed_reduction_pct:.1f}% < target "
            f"{min_elapsed_reduction_pct:.1f}%"
        )
    if score_deltas and _percentile(score_deltas, 95) > 5.0:
        warnings.append("large score deltas (p95 abs delta > 5.0)")

    if hard_failures:
        result = RESULT_FAIL
    elif warnings:
        result = RESULT_PASS_WITH_WARNINGS
    else:
        result = RESULT_PASS

    return {
        "result": result,
        "window": {
            "start_ms": legacy.get("start_ms"),
            "end_ms": legacy.get("end_ms"),
            "hours": legacy.get("hours"),
            "start": ms_to_iso(legacy.get("start_ms")),
            "end": ms_to_iso(legacy.get("end_ms")),
        },
        "legacy": {
            "elapsed_seconds": legacy_elapsed,
            "peak_rss_mb": legacy_rss,
            "book_row_count": legacy.get("book_row_count"),
            "trade_row_count": legacy.get("trade_row_count"),
            "markout_row_count": legacy.get("markout_row_count"),
            "markets_scored": legacy.get("markets_scored"),
            "markets_listed": legacy.get("markets_listed"),
        },
        "two_stage": {
            "elapsed_seconds": two_stage_elapsed,
            "peak_rss_mb": two_stage_rss,
            "book_row_count": two_stage.get("book_row_count"),
            "trade_row_count": two_stage.get("trade_row_count"),
            "markout_row_count": two_stage.get("markout_row_count"),
            "markets_scored": two_stage.get("markets_scored"),
            "two_stage_meta": two_stage_meta,
        },
        "performance": {
            "legacy_elapsed_seconds": legacy_elapsed,
            "two_stage_elapsed_seconds": two_stage_elapsed,
            "elapsed_reduction_pct": elapsed_reduction_pct,
            "legacy_peak_rss_mb": legacy_rss,
            "two_stage_peak_rss_mb": two_stage_rss,
            "peak_rss_reduction_pct": rss_reduction_pct,
        },
        "correctness": {
            "row_counts_match": row_counts_match,
            "latest_book_event_match": latest_book_event_match,
            "markets_compared": len(stage2_ids),
            "raw_metric_mismatch_count": mismatch_count,
            "mismatches": mismatches,
            "mismatch_count_total": mismatch_count,
        },
        "score_comparison": {
            "markets": score_details,
            "median_abs_score_delta": _percentile(score_deltas, 50),
            "p95_abs_score_delta": _percentile(score_deltas, 95),
            "max_abs_score_delta": max(score_deltas) if score_deltas else 0.0,
            "median_abs_rank_delta": _percentile(
                [float(x) for x in rank_deltas], 50
            ),
            "max_abs_rank_delta": max(rank_deltas) if rank_deltas else 0,
        },
        "warnings": warnings,
        "hard_failures": hard_failures,
    }


def format_console_summary(comparison: dict[str, Any]) -> str:
    window = comparison.get("window") or {}
    legacy = comparison.get("legacy") or {}
    two_stage = comparison.get("two_stage") or {}
    perf = comparison.get("performance") or {}
    correctness = comparison.get("correctness") or {}
    score_cmp = comparison.get("score_comparison") or {}
    two_meta = two_stage.get("two_stage_meta") or {}

    lines = [
        "=== Lighter MM Analyzer Real Data A/B Benchmark ===",
        "",
        "Window",
        f"  start: {window.get('start')}",
        f"  end:   {window.get('end')}",
        f"  hours: {window.get('hours')}",
        "",
        "Legacy",
        f"  elapsed: {legacy.get('elapsed_seconds', 0):.1f}s",
        f"  peak RSS: {legacy.get('peak_rss_mb', 0):.0f} MB",
        f"  book rows: {legacy.get('book_row_count', 0):,}",
        f"  trade rows: {legacy.get('trade_row_count', 0):,}",
        f"  markout rows: {legacy.get('markout_row_count', 0):,}",
        f"  markets scored: {legacy.get('markets_scored', 0)}",
        "",
        "Two-stage",
        f"  elapsed: {two_stage.get('elapsed_seconds', 0):.1f}s",
        f"  peak RSS: {two_stage.get('peak_rss_mb', 0):.0f} MB",
    ]
    if two_meta:
        lines.extend(
            [
                f"  Stage 1: {two_meta.get('stage1_elapsed_seconds', 0):.1f}s",
                f"  Stage 2: {two_meta.get('stage2_elapsed_seconds', 0):.1f}s",
                f"  selected: {two_meta.get('markets_selected', 0)} / "
                f"{two_meta.get('markets_total', 0)}",
            ]
        )
    lines.extend(
        [
            "",
            "Performance",
            f"  elapsed: {perf.get('elapsed_reduction_pct', 0):+.1f}%",
            f"  peak RSS: {perf.get('peak_rss_reduction_pct', 0):+.1f}%",
            "",
            "Correctness",
            f"  row counts: {'MATCH' if correctness.get('row_counts_match') else 'MISMATCH'}",
            f"  latest book event: "
            f"{'MATCH' if correctness.get('latest_book_event_match') else 'MISMATCH'}",
            f"  raw metrics checked: {correctness.get('markets_compared', 0)}",
            f"  raw metric mismatches: {correctness.get('raw_metric_mismatch_count', 0)}",
            "",
            "Score",
            f"  median abs delta: {score_cmp.get('median_abs_score_delta', 0):.1f}",
            f"  p95 abs delta: {score_cmp.get('p95_abs_score_delta', 0):.1f}",
            f"  max abs delta: {score_cmp.get('max_abs_score_delta', 0):.1f}",
            "",
            f"RESULT: {comparison.get('result')}",
        ]
    )
    warnings = comparison.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"  - {w}")
    failures = comparison.get("hard_failures") or []
    if failures:
        lines.append("")
        lines.append("Failures:")
        for f in failures:
            lines.append(f"  - {f}")
    return "\n".join(lines)
