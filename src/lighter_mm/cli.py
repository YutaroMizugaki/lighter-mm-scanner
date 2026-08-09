"""Typer CLI entrypoint: lighter-mm."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from lighter_mm import __version__
from lighter_mm.analytics.aggregation import analyze_window, scored_to_records
from lighter_mm.cloud.dashboard_data import build_dashboard_payload, collector_status_label
from lighter_mm.cloud.estimate import estimate_storage
from lighter_mm.collector import run_collector
from lighter_mm.config import Settings, build_storage_backend, ensure_dirs
from lighter_mm.logging_setup import setup_logging
from lighter_mm.report.export import export_csv
from lighter_mm.report.html_report import write_html_report
from lighter_mm.storage.sqlite_meta import SqliteMeta
from lighter_mm.storage.state import RunState

app = typer.Typer(
    name="lighter-mm",
    help="READ-ONLY Lighter MM opportunity research collector (no trading / no keys).",
    no_args_is_help=True,
)
console = Console()


def _settings() -> Settings:
    s = Settings()
    ensure_dirs(s)
    return s


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    settings = Settings()
    setup_logging(structured=settings.structured_logging or settings.is_cloud, verbose=verbose)


@app.command("collect")
def collect(
    hours: float | None = typer.Option(
        None,
        "--hours",
        help="Stop after N hours (default: RUN_TARGET_HOURS; 0=continuous)",
    ),
) -> None:
    """Collect realtime order books, trades, and markouts from Lighter mainnet."""
    settings = _settings()
    console.print(
        f"[bold]lighter-mm {__version__}[/bold] — READ-ONLY research collector\n"
        f"env={settings.environment} REST {settings.rest_base_url}\nWS {settings.ws_url}"
    )
    asyncio.run(run_collector(settings, hours=hours))


@app.command("status")
def status() -> None:
    """Show run / DQ status from local SQLite metadata."""
    settings = _settings()
    meta = SqliteMeta(settings.effective_data_dir / "metadata.db")
    try:
        summary = meta.status_summary()
    finally:
        meta.close()
    run = summary.get("active_run")
    console.print(f"Markets in DB: {summary.get('markets')}")
    if run:
        console.print(
            f"Active run: {run.get('run_id')} status={run.get('status')} "
            f"started={run.get('started_at')} hours={run.get('hours')}"
        )
    else:
        console.print("No active run recorded.")
    counters = summary.get("counters") or {}
    if counters:
        console.print(counters)


@app.command("run-status")
def run_status() -> None:
    """Show durable run state (local remote/ or GCS)."""
    settings = _settings()
    backend = build_storage_backend(settings)
    pointer = backend.download_json(f"{settings.gcs_prefix.rstrip('/')}/state/active_run.json")
    if not pointer:
        console.print("No active_run pointer found.")
        raise typer.Exit(1)
    run_id = pointer.get("run_id")
    state_raw = backend.download_json(
        f"{settings.gcs_prefix.rstrip('/')}/runs/{run_id}/state/state.json"
    )
    console.print_json(json.dumps(pointer))
    if state_raw:
        state = RunState.model_validate(state_raw)
        label = collector_status_label(
            state,
            ok_minutes=settings.status_ok_minutes,
            warn_minutes=settings.status_warn_minutes,
        )
        console.print(f"Status label: {label}")
        console.print_json(json.dumps(state_raw))


@app.command("cloud-status")
def cloud_status() -> None:
    """Alias for run-status (durable/cloud pointer)."""
    run_status()


@app.command("generate-dashboard-data")
def generate_dashboard_data(
    hours: float = typer.Option(72, "--hours"),
    out_dir: Path = typer.Option(Path("reports/dashboard"), "--out-dir"),
) -> None:
    """Generate lightweight dashboard JSON locally and/or upload via storage backend."""
    settings = _settings()
    backend = build_storage_backend(settings)
    pointer = backend.download_json(f"{settings.gcs_prefix.rstrip('/')}/state/active_run.json")
    state = None
    if pointer and pointer.get("run_id"):
        raw = backend.download_json(
            f"{settings.gcs_prefix.rstrip('/')}/runs/{pointer['run_id']}/state/state.json"
        )
        if raw:
            state = RunState.model_validate(raw)
    payload = build_dashboard_payload(settings, hours=hours, state=state)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(json.dumps(payload["latest"], indent=2), encoding="utf-8")
    (out_dir / "markets.json").write_text(
        json.dumps({"markets": payload["markets"]}, indent=2), encoding="utf-8"
    )
    (out_dir / "candidates.json").write_text(
        json.dumps({"candidates": payload["candidates"]}, indent=2), encoding="utf-8"
    )
    mdir = out_dir / "market"
    mdir.mkdir(exist_ok=True)
    for sym, detail in payload["market_details"].items():
        (mdir / f"{sym}.json").write_text(json.dumps(detail, indent=2), encoding="utf-8")
    # Also publish through backend public prefix
    prefix = settings.gcs_public_prefix.rstrip("/")
    backend.upload_json(f"{prefix}/latest.json", payload["latest"], public=True)
    backend.upload_json(
        f"{prefix}/markets.json", {"markets": payload["markets"]}, public=True
    )
    console.print(f"Wrote dashboard JSON to {out_dir} and storage public prefix")


@app.command("estimate-storage")
def estimate_storage_cmd(
    bytes_so_far: int | None = typer.Option(None, "--bytes"),
    hours: float = typer.Option(0.1, "--hours", help="Elapsed observation hours"),
) -> None:
    """Estimate GCS/disk growth from observed bytes."""
    settings = _settings()
    if bytes_so_far is None:
        root = settings.effective_data_dir
        total = 0
        for p in root.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
        bytes_so_far = total
    est = estimate_storage(bytes_so_far=bytes_so_far, elapsed_hours=hours)
    console.print_json(json.dumps(est, indent=2))


@app.command("analyze")
def analyze(
    hours: float = typer.Option(24, "--hours", help="Lookback window hours"),
) -> None:
    """Aggregate Parquet data and print summary stats."""
    settings = _settings()
    result = analyze_window(settings, hours)
    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(1)
    scored = result["scored"]
    console.print(
        f"Analyzed {len(scored)} markets over {hours}h · "
        f"candidates={len(result.get('candidates') or [])}"
    )
    table = Table(title=f"Top 15 by MM Opportunity Score ({hours}h)")
    for col in ("#", "Symbol", "Score", "Rank", "Spread", "Depth10", "TPM", "M5", "M30"):
        table.add_column(col)
    for i, s in enumerate(scored[:15], 1):
        r = s.row
        table.add_row(
            str(i),
            str(r.get("symbol")),
            f"{s.score:.1f}",
            s.letter_rank,
            _n(r.get("median_spread_bps")),
            _n(r.get("median_two_sided_depth_10bps_usd")),
            _n(r.get("trades_per_minute_median")),
            _n(r.get("maker_markout_5s_median_bps"), signed=True),
            _n(r.get("maker_markout_30s_median_bps"), signed=True),
        )
    console.print(table)


@app.command("rank")
def rank(
    hours: float = typer.Option(72, "--hours", help="Lookback window hours"),
    top: int = typer.Option(25, "--top", help="Rows to show"),
) -> None:
    """Rank markets by MM Opportunity Score."""
    settings = _settings()
    result = analyze_window(settings, hours)
    scored = result.get("scored") or []
    console.print(f"Analyzed {len(scored)} markets over {hours}h")
    console.print("\n[bold]MM CANDIDATES[/bold]")
    cands = [s for s in scored if s.candidate][:top]
    if not cands:
        console.print("(none passed filters)")
    for i, s in enumerate(cands, 1):
        console.print(
            f"{i:2d}. {s.row.get('symbol'):12s} {s.letter_rank} {s.score:5.1f} "
            f"rec=${s.recommended_max_order_usd or '-'}"
        )


@app.command("report")
def report(
    hours: float = typer.Option(72, "--hours", help="Lookback window hours"),
    out: Path = typer.Option(Path("reports/latest.html"), "--out", help="HTML output path"),
) -> None:
    """Generate local single-file HTML report."""
    settings = _settings()
    result = analyze_window(settings, hours)
    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(1)
    path = write_html_report(result, out)
    console.print(f"Wrote {path}")


@app.command("export")
def export_cmd(
    hours: float = typer.Option(72, "--hours"),
    format: str = typer.Option("csv", "--format", help="csv"),
    out: Path = typer.Option(Path("reports/ranking.csv"), "--out"),
) -> None:
    """Export ranking table."""
    if format.lower() != "csv":
        console.print("Only csv is supported in MVP")
        raise typer.Exit(2)
    settings = _settings()
    result = analyze_window(settings, hours)
    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(1)
    path = export_csv(result.get("scored") or [], out)
    console.print(f"Wrote {path} ({len(scored_to_records(result.get('scored') or []))} rows)")


def _n(v: object, signed: bool = False) -> str:
    if v is None:
        return "-"
    x = float(v)  # type: ignore[arg-type]
    return f"{x:+.2f}" if signed else f"{x:.2f}"


if __name__ == "__main__":
    app()
