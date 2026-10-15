"""Turn raw FT.SEARCH replies from redis-py into rows, a markdown table, and text.

redis-py returns a RESP3 map or a legacy RESP2 list.
"""

from __future__ import annotations

import json
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
    separator = "|" + "|".join("---" for _ in columns) + "|"
    body = [
        "| "
        + " | ".join(format_ft_search_cell(column, row.get(column, "")) for column in columns)
        + " |"
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def ft_search_result_summary(total: int, returned: int, kept: int | None = None) -> str:
    """One-line hit count for notebook output."""
    summary = f"{total} matched, {returned} returned"
    if kept is not None:
        summary += f", {kept} kept by the filter in the app"
    return summary


def ft_search_inputs_markdown(source_text: str, params: dict[str, Any] | None = None) -> str:
    """Markdown for the text embedded into ``$vector`` and other PARAMS the reader should see."""
    lines = [f"- **Text** (embedded to `$vector`): `{source_text}`"]
    for label, value in (params or {}).items():
        lines.append(f"- **{label}:** `{value}`")
    return "**Inputs**<br/>" + "<br/>".join(lines)


def _json_default(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


def format_ft_search_reply(reply: list | dict) -> str:
    """Text for the accordion: RESP3 map as JSON; RESP2 as numbered redis-cli-style lines."""
    if isinstance(reply, dict):
        return json.dumps(reply, indent=2, default=_json_default)

    lines = [f"1) (integer) {reply[0]}"]
    for n, item in enumerate(reply[1:], start=2):
        if n % 2 == 0:
            lines.append(f'{n}) "{item}"')
        else:
            lines.append(f"{n}) " + " ".join(f'"{value}"' for value in item))
    return "\n".join(lines)
