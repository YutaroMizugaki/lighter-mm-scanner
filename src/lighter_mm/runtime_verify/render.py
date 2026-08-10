"""Human-readable and JSON output for runtime verification."""

from __future__ import annotations

import json

from lighter_mm.runtime_verify.models import VerifyReport


def render_human(report: VerifyReport) -> str:
    lines: list[str] = []
    section_order = (
        "Deployment",
        "Collector",
        "WebSocket",
        "Storage",
        "Analyzer",
        "Public data",
        "Dashboard",
    )
    for section in section_order:
        checks = report.sections.get(section)
        if not checks:
            continue
        lines.append(f"=== {section} ===")
        for c in checks:
            label = c.name.replace("_", " ").replace(".", " ")
            lines.append(f"{c.level.value} {label:<28} {c.message}")

    if report.timestamps:
        lines.append("=== Timestamps ===")
        for key, val in report.timestamps.items():
            lines.append(f"  {key.replace('_', ' ')}: {val}")

    if report.run_ids:
        lines.append("=== Run IDs ===")
        for key, val in report.run_ids.items():
            lines.append(f"  {key}: {val or '—'}")

    if report.expected_git_sha:
        lines.append(f"Expected SHA ({report.expected_sha_source}): {report.expected_git_sha}")
    else:
        lines.append(f"Expected SHA: UNKNOWN ({report.expected_sha_source})")

    result = report.status.upper()
    lines.append(f"RESULT: {result}")
    return "\n".join(lines)


def render_json(report: VerifyReport) -> str:
    return json.dumps(report.to_dict(), indent=2)
