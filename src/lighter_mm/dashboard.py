"""Rich live terminal dashboard for collect mode."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text


class LiveDashboard:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._live: Live | None = None
        self.state: dict[str, Any] = {
            "runtime_s": 0.0,
            "markets_ready": 0,
            "markets_total": 0,
            "ws_ok": False,
            "dropped_connections": 0,
            "book_resyncs": 0,
            "nonce_gaps": 0,
            "samples_written": 0,
            "trades": 0,
            "markouts": 0,
            "top": [],
        }

    def start(self) -> None:
        self._live = Live(self.render(), console=self.console, refresh_per_second=2)
        self._live.start()

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def update(self, **kwargs: Any) -> None:
        self.state.update(kwargs)
        if self._live is not None:
            self._live.update(self.render())

    def render(self) -> Group:
        runtime = int(self.state.get("runtime_s", 0))
        hh = runtime // 3600
        mm = (runtime % 3600) // 60
        ss = runtime % 60
        header = Text()
        header.append("Lighter MM Scanner\n", style="bold")
        header.append(f"Runtime: {hh:02d}:{mm:02d}:{ss:02d}  ")
        header.append(
            f"Markets: {self.state['markets_ready']}/{self.state['markets_total']}  "
        )
        header.append("WS: ")
        header.append("OK" if self.state["ws_ok"] else "DOWN", style="green" if self.state["ws_ok"] else "red")
        header.append(
            f"\nDropped connections: {self.state['dropped_connections']}  "
            f"Book resyncs: {self.state['book_resyncs']}  "
            f"Nonce gaps: {self.state['nonce_gaps']}\n"
        )
        header.append(
            f"Samples written: {self.state['samples_written']:,}  "
            f"Trades: {self.state['trades']:,}  "
            f"Markouts: {self.state['markouts']:,}\n"
        )
        header.append(
            "Note: table below is LIVE snapshot ranking, not 24–72h historical score.\n",
            style="dim",
        )

        table = Table(title="Top opportunities now (live)", expand=True)
        table.add_column("Rank", justify="right")
        table.add_column("Symbol")
        table.add_column("Spread")
        table.add_column("Depth10bp")
        table.add_column("Trades/min")
        table.add_column("Markout5s")
        for i, row in enumerate(self.state.get("top") or [], start=1):
            table.add_row(
                str(i),
                str(row.get("symbol", "")),
                _fmt_bps(row.get("spread_bps")),
                _fmt_usd(row.get("depth_10bps")),
                _fmt_num(row.get("tpm")),
                _fmt_bps(row.get("markout_5s"), signed=True),
            )
        footer = Text(
            f"Updated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC",
            style="dim",
        )
        return Group(header, table, footer)


def _fmt_bps(v: Any, signed: bool = False) -> str:
    if v is None:
        return "-"
    if signed:
        return f"{float(v):+.2f}bp"
    return f"{float(v):.2f}bp"


def _fmt_usd(v: Any) -> str:
    if v is None:
        return "-"
    x = float(v)
    if x >= 1000:
        return f"${x/1000:.1f}k"
    return f"${x:.0f}"


def _fmt_num(v: Any) -> str:
    if v is None:
        return "-"
    return f"{float(v):.1f}"
