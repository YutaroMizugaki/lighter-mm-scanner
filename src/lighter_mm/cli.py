"""Typer CLI entrypoint: lighter-mm."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from lighter_mm import __version__
from lighter_mm.analytics.aggregation import analyze_window, scored_to_records
from lighter_mm.collector import run_collector
from lighter_mm.config import Settings, ensure_dirs
from lighter_mm.report.export import export_csv
from lighter_mm.report.html_report import write_html_report
from lighter_mm.storage.sqlite_meta import SqliteMeta

app = typer.Typer(
    name="lighter-mm",
    help="READ-ONLY Lighter MM opportunity research collector (no trading / no keys).",
    no_args_is_help=True,
)
console = Console()


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _settings() -> Settings:
    s = Settings()
    ensure_dirs(s)
    return s


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    _setup_logging(verbose)


@app.command("collect")
def collect(
    hours: float | None = typer.Option(
        None,
        "--hours",
        help="Stop after N hours (omit for continuous until Ctrl+C)",
    ),
) -> None:
    """Collect realtime order books, trades, and markouts from Lighter mainnet."""
    settings = _settings()
    console.print(
        f"[bold]lighter-mm {__version__}[/bold] — READ-ONLY research collector\n"
        f"REST {settings.rest_base_url}\nWS {settings.ws_url}"
    )
    asyncio.run(run_collector(settings, hours=hours))


@app.command("status")
def status() -> None:
    """Show run / DQ status from SQLite metadata."""
    settings = _settings()
    meta = SqliteMeta(settings.data_dir / "metadata.db")
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
    analyze(hours=hours)
    settings = _settings()
    result = analyze_window(settings, hours)
    scored = result.get("scored") or []
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
