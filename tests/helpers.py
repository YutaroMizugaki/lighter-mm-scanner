from __future__ import annotations


def enrich_book_row(row: dict) -> dict:
    stale = bool(row.get("is_stale"))
    mid = row.get("mid")
    out = dict(row)
    out.setdefault("is_usable", mid is not None)
    out.setdefault("is_inactive", stale)
    out.setdefault("book_update_age_ms", 600_000 if stale else 0)
    return out
