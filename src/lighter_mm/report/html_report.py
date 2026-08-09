"""Single-file HTML report (no CDN / external JS)."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from lighter_mm.scoring import ScoredMarket


def write_html_report(result: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    scored: list[ScoredMarket] = result.get("scored") or []
    candidates = [s for s in scored if s.candidate]
    avoid = result.get("avoid") or []
    hours = result.get("hours")

    top10 = scored[:10]
    answer = _executive_answers(scored, candidates, avoid, hours)

    sections = [
        _section_overview(answer, hours, len(scored), len(candidates)),
        _section_table("Top MM Candidates", candidates[:25]),
        _section_table("All Markets", scored),
        _section_metric_focus("Spread", scored, "median_spread_bps", "bp"),
        _section_metric_focus("Depth (±10bp two-sided)", scored, "median_two_sided_depth_10bps_usd", "usd"),
        _section_metric_focus("Trade Frequency", scored, "trades_per_minute_median", "tpm"),
        _section_metric_focus("Volatility (p95 5s)", scored, "p95_abs_mid_move_5s_bps", "bp"),
        _section_metric_focus("Maker Markout 5s", scored, "maker_markout_5s_median_bps", "bp"),
        _section_metric_focus("Funding", scored, "current_funding_rate", "rate"),
        _section_metric_focus("Data Quality", scored, "data_coverage_pct", "pct"),
        _section_avoid(avoid),
        _section_details(scored[:40]),
        _section_charts(top10),
    ]

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Lighter MM Opportunity Report</title>
<style>
:root {{
  --bg: #0f1419;
  --card: #1a222c;
  --text: #e7ecf1;
  --muted: #9aa7b5;
  --accent: #3dd6c6;
  --warn: #f0b429;
  --bad: #f07178;
  --ok: #7fd962;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
  background: radial-gradient(1200px 600px at 10% -10%, #1b3a3a 0%, transparent 50%),
              radial-gradient(900px 500px at 100% 0%, #243049 0%, transparent 45%),
              var(--bg);
  color: var(--text); line-height: 1.45;
}}
header {{
  padding: 2rem 2rem 1rem; border-bottom: 1px solid #2a3542;
}}
header h1 {{ margin: 0 0 .4rem; font-size: 1.8rem; letter-spacing: .02em; }}
header p {{ margin: .2rem 0; color: var(--muted); max-width: 70ch; }}
main {{ padding: 1.25rem 2rem 3rem; }}
section {{ margin: 2rem 0; }}
h2 {{ font-size: 1.25rem; margin: 0 0 .75rem; color: var(--accent); }}
.answer-box {{
  background: linear-gradient(135deg, #17232b, #1d2a38);
  border: 1px solid #2f4052; border-radius: 12px; padding: 1.25rem 1.5rem;
}}
.answer-box ol {{ margin: .5rem 0 0 1.2rem; }}
.answer-box li {{ margin: .55rem 0; }}
table {{
  width: 100%; border-collapse: collapse; font-size: .85rem;
  background: var(--card); border-radius: 10px; overflow: hidden;
}}
th, td {{ padding: .45rem .55rem; border-bottom: 1px solid #2a3542; text-align: right; }}
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
th {{ color: var(--muted); font-weight: 600; background: #121820; position: sticky; top: 0; }}
tr:hover td {{ background: #222c38; }}
.badge {{
  display: inline-block; min-width: 1.4rem; text-align: center;
  padding: .1rem .35rem; border-radius: 4px; font-weight: 700; font-size: .75rem;
}}
.A {{ background: #1f4d3a; color: var(--ok); }}
.B {{ background: #3a4d1f; color: #c6e06a; }}
.C {{ background: #4d3a1f; color: var(--warn); }}
.D {{ background: #4d1f1f; color: var(--bad); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }}
.card {{
  background: var(--card); border: 1px solid #2a3542; border-radius: 10px; padding: 1rem;
}}
.card h3 {{ margin: 0 0 .5rem; font-size: 1rem; }}
.muted {{ color: var(--muted); }}
ul.compact {{ margin: .3rem 0; padding-left: 1.1rem; }}
svg.chart {{ width: 100%; height: 160px; background: #121820; border-radius: 8px; }}
.note {{
  margin-top: 1rem; padding: .8rem 1rem; border-left: 3px solid var(--warn);
  background: #241c10; color: #e8d7b0; font-size: .9rem;
}}
a {{ color: var(--accent); }}
</style>
</head>
<body>
<header>
  <h1>Lighter MM Opportunity Report</h1>
  <p>READ-ONLY research tool. Not a trading bot. Scores combine spread, two-sided depth,
  trade activity, persistence, volatility, and maker markout — not raw spread alone.</p>
  <p class="muted">Window: {html.escape(str(hours))}h · Markets analyzed: {len(scored)} · Candidates: {len(candidates)}</p>
</header>
<main>
{''.join(sections)}
<div class="note">
<strong>Important:</strong> displayed spread × trade count ≠ profit. Queue position, cancel latency,
adverse selection, actual fill probability, inventory risk, funding, and slippage must be validated
separately (e.g. paper trading) before any live market making.
</div>
</main>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")
    latest = path.parent / "latest.html"
    if path.resolve() != latest.resolve():
        latest.write_text(doc, encoding="utf-8")
    return path


def _executive_answers(
    scored: list[ScoredMarket],
    candidates: list[ScoredMarket],
    avoid: list[ScoredMarket],
    hours: Any,
) -> dict[str, Any]:
    strong = [s for s in candidates if s.letter_rank in {"A", "B"}]
    exists = len(strong) > 0 or (len(candidates) >= 3 and (scored and scored[0].score >= 55))
    proceed = exists and any(s.letter_rank == "A" for s in candidates)
    proceed_text = (
        "Yes — a small set of markets looks worth paper-trading next."
        if proceed
        else (
            "Maybe — a few verification candidates exist, but edge looks fragile; paper trade carefully."
            if exists
            else "No clear opportunity in this window — keep collecting or revisit after more data."
        )
    )
    size_recs = {}
    for size in (100, 500, 1000):
        key = f"fit_{size}"
        fits = [s for s in candidates if s.size_fit.get(key)]
        size_recs[size] = [s.row.get("symbol") for s in fits[:5]] or [
            s.row.get("symbol") for s in scored[:3]
        ]
    return {
        "exists": exists,
        "exists_text": (
            "Yes — candidate markets with non-trivial activity, depth, and non-terrible markout appeared."
            if exists
            else "No — after filters, no market looked reliably MM-friendly in this window."
        ),
        "top10": scored[:10],
        "size_recs": size_recs,
        "avoid": avoid[:10],
        "proceed_text": proceed_text,
        "hours": hours,
    }


def _section_overview(answer: dict[str, Any], hours: Any, n_mkt: int, n_cand: int) -> str:
    top_rows = "".join(
        f"<li><strong>{html.escape(str(s.row.get('symbol')))}</strong> "
        f"score {s.score:.1f} ({s.letter_rank}) · "
        f"spread {_fmt(s.row.get('median_spread_bps'))}bp · "
        f"depth10 ${_fmt(s.row.get('median_two_sided_depth_10bps_usd'))} · "
        f"tpm {_fmt(s.row.get('trades_per_minute_median'))} · "
        f"m5 {_fmt(s.row.get('maker_markout_5s_median_bps'), signed=True)}bp · "
        f"m30 {_fmt(s.row.get('maker_markout_30s_median_bps'), signed=True)}bp</li>"
        for s in answer["top10"]
    )
    size_lines = "".join(
        f"<li>${sz}: {html.escape(', '.join(str(x) for x in syms) or 'n/a')}</li>"
        for sz, syms in answer["size_recs"].items()
    )
    avoid_lines = "".join(
        f"<li>{html.escape(str(s.row.get('symbol')))} "
        f"(spread {_fmt(s.row.get('median_spread_bps'))}bp) — "
        f"{html.escape(s.cons[0] if s.cons else 'poor MM profile')}</li>"
        for s in answer["avoid"]
    )
    return f"""
<section class="answer-box" id="overview">
  <h2>Executive answers ({html.escape(str(hours))}h)</h2>
  <ol>
    <li><strong>Viable small-size MM markets?</strong> {html.escape(answer['exists_text'])}</li>
    <li><strong>Top 10 markets</strong><ol>{top_rows or '<li>n/a</li>'}</ol></li>
    <li><strong>Comparison fields</strong> (spread / persistence / depth / tpm / volume / 5s&amp;30s markout / vol)
      are in the tables below. Markets analyzed: {n_mkt}, candidates: {n_cand}.</li>
    <li><strong>Most realistic by order size</strong><ul>{size_lines}</ul></li>
    <li><strong>Wide spread but avoid</strong><ol>{avoid_lines or '<li>n/a</li>'}</ol></li>
    <li><strong>Proceed to paper trading?</strong> {html.escape(answer['proceed_text'])}</li>
  </ol>
</section>
"""


def _section_table(title: str, scored: list[ScoredMarket]) -> str:
    rows = []
    for i, s in enumerate(scored, 1):
        r = s.row
        rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td><a href='#m-{r.get('market_id')}'>{html.escape(str(r.get('symbol')))}</a></td>"
            f"<td><span class='badge {s.letter_rank}'>{s.letter_rank}</span></td>"
            f"<td>{s.score:.1f}</td>"
            f"<td>{_fmt(r.get('median_spread_bps'))}</td>"
            f"<td>{_fmt(r.get('pct_time_spread_ge_5bps'), pct=True)}</td>"
            f"<td>{_fmt(r.get('median_two_sided_depth_10bps_usd'))}</td>"
            f"<td>{_fmt(r.get('trades_per_minute_median'))}</td>"
            f"<td>{_fmt(r.get('total_quote_volume'))}</td>"
            f"<td>{_fmt(r.get('maker_markout_5s_median_bps'), signed=True)}</td>"
            f"<td>{_fmt(r.get('maker_markout_30s_median_bps'), signed=True)}</td>"
            f"<td>{_fmt(r.get('p95_abs_mid_move_5s_bps'))}</td>"
            f"<td>{_fmt(r.get('data_coverage_pct'))}</td>"
            f"<td>{_fmt(s.recommended_max_order_usd)}</td>"
            "</tr>"
        )
    return f"""
<section>
  <h2>{html.escape(title)}</h2>
  <div style="overflow:auto; max-height:520px;">
  <table>
    <thead><tr>
      <th>#</th><th>Symbol</th><th>Rank</th><th>Score</th><th>Med Spread</th>
      <th>%≥5bp</th><th>Depth10</th><th>TPM</th><th>Volume</th>
      <th>M5</th><th>M30</th><th>Vol5 p95</th><th>Coverage</th><th>Rec $</th>
    </tr></thead>
    <tbody>{''.join(rows) or '<tr><td colspan="14">No data</td></tr>'}</tbody>
  </table></div>
  <p class="muted">TPM / volume are market-level activity, not your personal maker fill probability.</p>
</section>
"""


def _section_metric_focus(title: str, scored: list[ScoredMarket], key: str, kind: str) -> str:
    items = sorted(
        scored,
        key=lambda s: (s.row.get(key) is not None, s.row.get(key) or 0),
        reverse=True,
    )[:15]
    lis = []
    for s in items:
        v = s.row.get(key)
        lis.append(
            f"<li>{html.escape(str(s.row.get('symbol')))}: <strong>{_fmt(v, signed=(kind=='bp'))}</strong>"
            f"{'' if kind!='usd' else ' USD'}{'' if kind!='pct' else '%'}</li>"
        )
    return f"<section><h2>{html.escape(title)}</h2><ol>{''.join(lis) or '<li>n/a</li>'}</ol></section>"


def _section_avoid(avoid: list[ScoredMarket]) -> str:
    return _section_table("Wide spread but avoid", avoid)


def _section_details(scored: list[ScoredMarket]) -> str:
    cards = []
    for s in scored:
        r = s.row
        cards.append(
            f"""
<div class="card" id="m-{r.get('market_id')}">
  <h3>{html.escape(str(r.get('symbol')))} · {s.letter_rank} · {s.score:.1f}</h3>
  <p class="muted">market_id={r.get('market_id')} · coverage={_fmt(r.get('data_coverage_pct'))}% ·
  rec order ${_fmt(s.recommended_max_order_usd)}</p>
  <p><strong>Pros</strong></p><ul class="compact">{''.join(f'<li>{html.escape(p)}</li>' for p in s.pros[:8])}</ul>
  <p><strong>Cons</strong></p><ul class="compact">{''.join(f'<li>{html.escape(p)}</li>' for p in s.cons[:6]) or '<li>none flagged</li>'}</ul>
  <p><strong>Warnings</strong></p><ul class="compact">{''.join(f'<li>{html.escape(p)}</li>' for p in s.warnings[:6]) or '<li>none</li>'}</ul>
</div>"""
        )
    return f"<section><h2>Market details</h2><div class='grid'>{''.join(cards)}</div></section>"


def _section_charts(top: list[ScoredMarket]) -> str:
    """Inline SVG bar charts — no external JS."""
    def bar_chart(title: str, key: str) -> str:
        vals = [(s.row.get("symbol"), s.row.get(key)) for s in top if s.row.get(key) is not None]
        if not vals:
            return f"<div class='card'><h3>{html.escape(title)}</h3><p class='muted'>n/a</p></div>"
        max_v = max(abs(float(v)) for _, v in vals) or 1.0
        width = 320
        height = 160
        bar_w = width / max(len(vals), 1)
        parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img">']
        parts.append(
            f'<text x="8" y="14" fill="#9aa7b5" font-size="11">{html.escape(title)}</text>'
        )
        for i, (sym, v) in enumerate(vals):
            mag = abs(float(v)) / max_v
            h = mag * 110
            x = i * bar_w + 4
            y = 140 - h
            color = "#3dd6c6" if float(v) >= 0 else "#f07178"
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w-8:.1f}" height="{h:.1f}" fill="{color}"/>'
            )
            parts.append(
                f'<text x="{x+2:.1f}" y="154" fill="#9aa7b5" font-size="9">{html.escape(str(sym)[:6])}</text>'
            )
        parts.append("</svg>")
        return f"<div class='card'>{''.join(parts)}</div>"

    charts = "".join(
        [
            bar_chart("Median spread (bp)", "median_spread_bps"),
            bar_chart("Two-sided depth 10bp ($)", "median_two_sided_depth_10bps_usd"),
            bar_chart("Trades/min median", "trades_per_minute_median"),
            bar_chart("Maker markout 5s (bp)", "maker_markout_5s_median_bps"),
            bar_chart("Maker markout 30s (bp)", "maker_markout_30s_median_bps"),
            bar_chart("Coverage %", "data_coverage_pct"),
        ]
    )
    # Embed raw JSON for power users
    payload = [
        {
            "symbol": s.row.get("symbol"),
            "score": s.score,
            "letter": s.letter_rank,
            **{k: s.row.get(k) for k in (
                "median_spread_bps",
                "median_two_sided_depth_10bps_usd",
                "trades_per_minute_median",
                "maker_markout_5s_median_bps",
                "maker_markout_30s_median_bps",
            )},
        }
        for s in top
    ]
    return f"""
<section>
  <h2>Charts (Top markets)</h2>
  <div class="grid">{charts}</div>
  <script type="application/json" id="top-data">{json.dumps(payload)}</script>
</section>
"""


def _fmt(v: Any, signed: bool = False, pct: bool = False) -> str:
    if v is None:
        return "-"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return html.escape(str(v))
    if pct:
        return f"{x*100:.1f}%"
    if signed:
        return f"{x:+.2f}"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return f"{x:.2f}"
