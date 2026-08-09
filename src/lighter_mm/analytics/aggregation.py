"""DuckDB/Polars aggregation over collected Parquet datasets."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from lighter_mm.config import Settings
from lighter_mm.scoring import (
    CandidateThresholds,
    ScoredMarket,
    avoid_wide_spread_markets,
    score_markets,
)
from lighter_mm.util import percentile


def _connect(data_dir: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute("SET threads TO 4")
    return con


def _glob_patterns(path: Path) -> list[str]:
    """Locate Parquet parts under hive partitions.

    Writers use ``date=YYYY-MM-DD/hour=HH/*.parquet``. An older flat layout
    ``date=YYYY-MM-DD/*.parquet`` is still accepted. When both exist, both are
    returned so migration-era datasets are not silently truncated.
    """
    patterns: list[str] = []
    if list(path.glob("date=*/hour=*/*.parquet")):
        patterns.append(str(path / "date=*/hour=*/*.parquet"))
    if list(path.glob("date=*/*.parquet")):
        patterns.append(str(path / "date=*/*.parquet"))
    return patterns


def _glob_or_none(path: Path) -> str | None:
    """Compatibility helper: prefer hour partitions, else flat date layout."""
    patterns = _glob_patterns(path)
    return patterns[0] if patterns else None


def _read_parquet_window(
    con: duckdb.DuckDBPyConnection, patterns: list[str], start_ms: int
) -> pl.DataFrame:
    if not patterns:
        return pl.DataFrame()
    # DuckDB accepts a list of globs; keep hive partitions for date=/hour=.
    listed = "[" + ", ".join("'" + p.replace("'", "''") + "'" for p in patterns) + "]"
    return con.execute(
        f"""
        SELECT * FROM read_parquet({listed}, hive_partitioning=1, union_by_name=true)
        WHERE timestamp_ms >= {start_ms}
        """
    ).pl()


def analyze_window(settings: Settings, hours: float) -> dict[str, Any]:
    if hours <= 0:
        return {
            "hours": hours,
            "markets": [],
            "scored": [],
            "error": "hours must be > 0 (use a positive lookback window)",
        }
    data_dir = settings.data_dir
    con = _connect(data_dir)
    now_ms = con.execute("SELECT CAST(epoch_ms(current_timestamp) AS BIGINT)").fetchone()[0]
    start_ms = now_ms - int(hours * 3600 * 1000)

    book_globs = _glob_patterns(data_dir / "book_samples")
    trade_globs = _glob_patterns(data_dir / "trades")
    markout_globs = _glob_patterns(data_dir / "markouts")

    if not book_globs:
        return {"hours": hours, "markets": [], "scored": [], "error": "no book_samples yet"}

    book_df = _read_parquet_window(con, book_globs, start_ms)
    trade_df = _read_parquet_window(con, trade_globs, start_ms)
    markout_df = _read_parquet_window(con, markout_globs, start_ms)

    rows = _aggregate_markets(book_df, trade_df, markout_df, hours, settings)
    thresholds = CandidateThresholds(
        min_coverage_pct=settings.min_coverage_pct,
        min_trades_per_hour=settings.min_trades_per_hour,
        min_two_sided_depth_10bps_usd=settings.min_two_sided_depth_10bps_usd,
        min_median_spread_bps=settings.min_median_spread_bps,
    )
    scored = score_markets(rows, thresholds=thresholds)
    return {
        "hours": hours,
        "start_ms": start_ms,
        "end_ms": now_ms,
        "markets": rows,
        "scored": scored,
        "candidates": [s for s in scored if s.candidate],
        "avoid": avoid_wide_spread_markets(scored, 10),
    }


def _aggregate_markets(
    book_df: pl.DataFrame,
    trade_df: pl.DataFrame,
    markout_df: pl.DataFrame,
    hours: float,
    settings: Settings,
) -> list[dict[str, Any]]:
    if book_df.is_empty():
        return []

    # Exclude stale samples from spread/depth analytics
    good = book_df.filter(pl.col("is_stale") == False)  # noqa: E712
    markets = book_df.select(["market_id", "symbol"]).unique().to_dicts()
    out: list[dict[str, Any]] = []

    expected_samples = max(1, int((hours * 3600) / settings.book_sample_interval_seconds))

    for m in markets:
        mid = m["market_id"]
        symbol = m["symbol"]
        b = good.filter(pl.col("market_id") == mid)
        # Coverage is usable (non-stale) samples only — stale rows look like data
        # but carry null spreads / zero depths and must not inflate coverage.
        actual = b.height
        coverage = min(100.0, 100.0 * actual / expected_samples)

        if b.is_empty():
            row = {
                "market_id": mid,
                "symbol": symbol,
                "observation_hours": hours,
                "data_coverage_pct": coverage,
            }
            out.append(row)
            continue

        spreads = [x for x in b["spread_bps"].drop_nulls().to_list() if x is not None]
        depth5 = _col_list(b, "two_sided_depth_5bps_usd")
        depth10 = _col_list(b, "two_sided_depth_10bps_usd")
        depth25 = _col_list(b, "two_sided_depth_25bps_usd")
        bbo_depth = []
        for bb, ba in zip(
            b["best_bid_size_usd"].to_list(), b["best_ask_size_usd"].to_list(), strict=False
        ):
            if bb is not None and ba is not None:
                bbo_depth.append(min(bb, ba))

        persistence = _spread_persistence(b, settings.spread_thresholds_bps)
        vol = _volatility_from_mids(b)

        tstats = _trade_stats(trade_df, mid, window_minutes=max(1, int(hours * 60)))
        mstats = _markout_stats(markout_df, mid)

        last = b.sort("timestamp_ms").tail(1)
        funding_cur = _first(last, "current_funding_rate")
        funding = _first(last, "funding_rate")
        oi = _first(last, "open_interest")
        daily_q = _first(last, "daily_quote_token_volume")

        row = {
            "market_id": mid,
            "symbol": symbol,
            "observation_hours": hours,
            "data_coverage_pct": coverage,
            "daily_quote_volume_usd": daily_q,
            "open_interest": oi,
            "median_bbo_depth_usd": percentile(bbo_depth, 50),
            "median_two_sided_depth_5bps_usd": percentile(depth5, 50),
            "median_two_sided_depth_10bps_usd": percentile(depth10, 50),
            "median_two_sided_depth_25bps_usd": percentile(depth25, 50),
            "mean_spread_bps": (sum(spreads) / len(spreads)) if spreads else None,
            "median_spread_bps": percentile(spreads, 50),
            "p10_spread_bps": percentile(spreads, 10),
            "p25_spread_bps": percentile(spreads, 25),
            "p75_spread_bps": percentile(spreads, 75),
            "p90_spread_bps": percentile(spreads, 90),
            "p95_spread_bps": percentile(spreads, 95),
            **persistence,
            **tstats,
            **vol,
            **mstats,
            "current_funding_rate": funding_cur,
            "funding_rate": funding,
        }
        out.append(row)
    return out


def _col_list(df: pl.DataFrame, name: str) -> list[float]:
    if name not in df.columns:
        return []
    return [float(x) for x in df[name].drop_nulls().to_list()]


def _first(df: pl.DataFrame, name: str) -> Any:
    if df.is_empty() or name not in df.columns:
        return None
    return df[name][0]


def _spread_persistence(book: pl.DataFrame, thresholds: list[float]) -> dict[str, Any]:
    spreads = book.select(["timestamp_ms", "spread_bps"]).drop_nulls().sort("timestamp_ms")
    if spreads.is_empty():
        return {f"pct_time_spread_ge_{int(t)}bps": None for t in thresholds}

    ts = spreads["timestamp_ms"].to_list()
    sp = spreads["spread_bps"].to_list()
    # Assume each sample represents sample_interval until next
    total = 0.0
    above = {t: 0.0 for t in thresholds}
    durations: dict[float, list[float]] = {t: [] for t in thresholds}
    open_run: dict[float, float | None] = {t: None for t in thresholds}

    for i in range(len(ts)):
        dt = (ts[i + 1] - ts[i]) / 1000.0 if i + 1 < len(ts) else 5.0
        if dt <= 0 or dt > 60:
            dt = 5.0
        total += dt
        s = sp[i]
        for t in thresholds:
            if s is not None and s >= t:
                above[t] += dt
                if open_run[t] is None:
                    open_run[t] = 0.0
                open_run[t] = (open_run[t] or 0.0) + dt
            else:
                if open_run[t] is not None:
                    durations[t].append(open_run[t] or 0.0)
                    open_run[t] = None
    for t in thresholds:
        if open_run[t] is not None:
            durations[t].append(open_run[t] or 0.0)

    out: dict[str, Any] = {}
    for t in thresholds:
        key = int(t) if float(t).is_integer() else t
        out[f"pct_time_spread_ge_{key}bps"] = (above[t] / total) if total > 0 else None
        durs = durations[t]
        out[f"spread_ge_{key}bps_event_count"] = len(durs)
        out[f"spread_ge_{key}bps_median_duration_seconds"] = percentile(durs, 50)
        out[f"spread_ge_{key}bps_p90_duration_seconds"] = percentile(durs, 90)
        out[f"spread_ge_{key}bps_max_duration_seconds"] = max(durs) if durs else None
    return out


def _volatility_from_mids(book: pl.DataFrame) -> dict[str, Any]:
    """Approximate horizon moves using 5s samples (1s uses consecutive samples as proxy)."""
    mids = book.select(["timestamp_ms", "mid"]).drop_nulls().sort("timestamp_ms")
    if mids.height < 3:
        return {}
    ts = mids["timestamp_ms"].to_list()
    mid = mids["mid"].to_list()

    def moves(horizon_s: int) -> list[float]:
        out = []
        j = 0
        for i in range(len(ts)):
            target = ts[i] + horizon_s * 1000
            while j < len(ts) and ts[j] < target:
                j += 1
            if j >= len(ts):
                break
            if abs(ts[j] - target) > horizon_s * 1000:
                continue
            a, b = mid[i], mid[j]
            if a and b and a > 0:
                out.append(abs(math.log(b / a)) * 10000.0)
        return out

    # 1s cannot be measured from 5s samples precisely — use consecutive sample abs move
    consec = []
    for i in range(1, len(mid)):
        a, b = mid[i - 1], mid[i]
        if a and b and a > 0:
            consec.append(abs(math.log(b / a)) * 10000.0)

    out: dict[str, Any] = {
        "p50_abs_mid_move_1s_bps": percentile(consec, 50),  # proxy: ~sample interval
        "p95_abs_mid_move_1s_bps": percentile(consec, 95),
        "median_abs_mid_move_1s_bps": percentile(consec, 50),
        "p90_abs_mid_move_1s_bps": percentile(consec, 90),
    }
    for h, label in [(5, "5s"), (30, "30s"), (60, "60s")]:
        mv = moves(h)
        out[f"p50_abs_mid_move_{label}_bps"] = percentile(mv, 50)
        out[f"p95_abs_mid_move_{label}_bps"] = percentile(mv, 95)
        out[f"median_abs_mid_move_{label}_bps"] = percentile(mv, 50)
        out[f"p90_abs_mid_move_{label}_bps"] = percentile(mv, 90)
    return out


def _trade_stats(
    trade_df: pl.DataFrame, market_id: int, *, window_minutes: int = 1
) -> dict[str, Any]:
    """Trade activity over the observation window.

    Mean/median/p90 TPM include zero-trade minutes. Counting only active minutes
    previously inflated sparse markets toward ~1.0 TPM and let them pass
    ``min_trades_per_hour`` candidacy filters incorrectly.
    """
    empty = {
        "total_trade_count": 0,
        "trades_per_minute_mean": 0.0,
        "trades_per_minute_median": 0.0,
        "trades_per_minute_p90": 0.0,
        "total_quote_volume": 0.0,
        "median_trade_size_usd": None,
        "median_intertrade_ms": None,
    }
    n_minutes = max(1, int(window_minutes))
    if trade_df.is_empty():
        return empty
    t = trade_df.filter(
        (pl.col("market_id") == market_id) & (pl.col("type") == "trade")
    ).sort("timestamp_ms")
    if t.is_empty():
        return empty
    # per-minute counts (active minutes only), then pad zeros to wall-clock window
    minutes = (
        t.with_columns((pl.col("timestamp_ms") // 60_000).alias("minute"))
        .group_by("minute")
        .agg(pl.len().alias("cnt"), pl.col("usd_amount").sum().alias("vol"))
    )
    cnts = [float(c) for c in minutes["cnt"].to_list()]
    zeros = max(0, n_minutes - len(cnts))
    padded = cnts + [0.0] * zeros
    ts = t["timestamp_ms"].to_list()
    inter = [float(ts[i] - ts[i - 1]) for i in range(1, len(ts)) if ts[i] >= ts[i - 1]]
    sizes = [float(x) for x in t["usd_amount"].drop_nulls().to_list()]
    return {
        "total_trade_count": t.height,
        "trades_per_minute_mean": float(t.height) / float(n_minutes),
        "trades_per_minute_median": percentile(padded, 50) or 0.0,
        "trades_per_minute_p90": percentile(padded, 90) or 0.0,
        "total_quote_volume": float(t["usd_amount"].sum()),
        "median_trade_size_usd": percentile(sizes, 50),
        "median_intertrade_ms": percentile(inter, 50),
    }


def _markout_stats(markout_df: pl.DataFrame, market_id: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if markout_df.is_empty():
        for h in (1, 5, 30, 60):
            out[f"maker_markout_{h}s_mean_bps"] = None
            out[f"maker_markout_{h}s_median_bps"] = None
        out["pct_positive_markout_5s"] = None
        out["pct_positive_markout_30s"] = None
        return out
    m = markout_df.filter(pl.col("market_id") == market_id)
    for h in (1, 5, 30, 60):
        vals = [
            float(x)
            for x in m.filter(pl.col("horizon_s") == h)["maker_markout_bps"].drop_nulls().to_list()
        ]
        out[f"maker_markout_{h}s_mean_bps"] = (sum(vals) / len(vals)) if vals else None
        out[f"maker_markout_{h}s_median_bps"] = percentile(vals, 50)
        if h in (5, 30):
            out[f"pct_positive_markout_{h}s"] = (
                (sum(1 for v in vals if v > 0) / len(vals)) if vals else None
            )
    return out


def scored_to_records(scored: list[ScoredMarket]) -> list[dict[str, Any]]:
    records = []
    for s in scored:
        rec = dict(s.row)
        rec.update(
            {
                "mm_opportunity_score": s.score,
                "letter_rank": s.letter_rank,
                "is_candidate": s.candidate,
                "recommended_max_order_usd": s.recommended_max_order_usd,
                "pros": " | ".join(s.pros),
                "cons": " | ".join(s.cons),
                "warnings": " | ".join(s.warnings),
                **s.size_fit,
                **{f"pct_{k}": v for k, v in s.rank_components.items()},
            }
        )
        records.append(rec)
    return records
