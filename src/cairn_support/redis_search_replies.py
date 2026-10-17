"""Turn raw FT.SEARCH replies from redis-py into rows, and rows into a markdown table.

redis-py returns a RESP3 map or a legacy RESP2 list.
"""

from __future__ import annotations

from typing import Any


def _search_rows_resp2(reply: list) -> tuple[int, list[dict[str, Any]]]:
    rows = [
        {"key": key, **dict(zip(fields[::2], fields[1::2]))}
        for key, fields in zip(reply[1::2], reply[2::2])
    ]
    return int(reply[0]), rows


def _search_rows_resp3(reply: dict) -> tuple[int, list[dict[str, Any]]]:
    rows = []
    for item in reply.get("results") or []:
        attrs = dict(item.get("extra_attributes") or {})
        rows.append({"key": item.get("id"), **attrs})
    return int(reply.get("total_results", 0)), rows


def ft_search_rows(reply: list | dict) -> tuple[int, list[dict[str, Any]]]:
    """Match count and one dict per hit (key plus returned hash fields)."""
    if isinstance(reply, dict):
        return _search_rows_resp3(reply)
    return _search_rows_resp2(reply)


HELP_SEARCH_COLUMNS = ("title", "category", "year", "vector_score")


def format_ft_search_cell(column: str, value: Any) -> str:
    """One markdown table cell, with pipe escaping and vector_score formatting."""
    if column == "vector_score":
        return f"{float(value):.4f}"
    return str(value).replace("|", "&#124;")


def ft_search_markdown_table(
    rows: list[dict[str, Any]],
    columns: tuple[str, ...] = HELP_SEARCH_COLUMNS,
) -> str:
    """Markdown table for the standard Help Center RETURN fields."""
    header = "| " + " | ".join(columns) + " |"
    separator = "|" + "|".join("--:" if column in ("year", "vector_score") else ":--" for column in columns) + "|"
    body = [
        "| "
        + " | ".join(format_ft_search_cell(column, row.get(column, "")) for column in columns)
        + " |"
        for row in rows
    ]
    return "\n".join([header, separator, *body])
