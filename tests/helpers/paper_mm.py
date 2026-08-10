"""Shared fixtures for Paper MM tests."""

from __future__ import annotations

import duckdb

from tests.helpers.estimated_fill import setup_books_trades


def setup_paper_mm_tables(
    con: duckdb.DuckDBPyConnection,
    *,
    books: list[tuple],
    trades: list[tuple],
) -> None:
    setup_books_trades(con, books=books, trades=trades)
