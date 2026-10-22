"""Turn an FT.SEARCH reply into hits, and hits into a markdown table.

redis-py returns a RESP3 map or a legacy RESP2 list. parse_search_reply accepts
either and returns SearchResults: how many matches Redis reported, and one dict
per hit in this reply. SearchResults is defined in schema. format_search_table
renders the help-center fields.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from cairn_support.schema import SearchResults


def parse_search_reply(reply: Mapping[str, Any] | Sequence[Any]) -> SearchResults:
    """Read one FT.SEARCH reply.

    A map is the RESP3 shape, with total_results and results. A list is the RESP2
    shape: [total, key, [field, value, ...], ...].
    """
    if isinstance(reply, Mapping):
        return _parse_map_reply(reply)
    return _parse_list_reply(reply)


def _parse_list_reply(reply: Sequence[Any]) -> SearchResults:
    hits = [
        {"key": key, **dict(zip(fields[::2], fields[1::2]))}
        for key, fields in zip(reply[1::2], reply[2::2])
    ]
    return SearchResults(int(reply[0]), hits)


def _parse_map_reply(reply: Mapping[str, Any]) -> SearchResults:
    hits: list[dict[str, Any]] = []
    for item in reply.get("results") or []:
        attributes = dict(item.get("extra_attributes") or {})
        hits.append({"key": item.get("id"), **attributes})
    return SearchResults(int(reply.get("total_results", 0)), hits)


# The fields Chapter 1's searches RETURN, in table order.
HELP_ARTICLE_COLUMNS: tuple[str, ...] = ("title", "category", "year", "vector_score")

_RIGHT_ALIGNED: frozenset[str] = frozenset({"year", "vector_score"})


def format_search_table(
    hits: Sequence[Mapping[str, Any]],
    columns: Sequence[str] = HELP_ARTICLE_COLUMNS,
) -> str:
    """Markdown table for the help-center fields. year and vector_score are right-aligned."""
    header = "| " + " | ".join(columns) + " |"
    separator = "|" + "|".join("--:" if column in _RIGHT_ALIGNED else ":--" for column in columns) + "|"
    body = [
        "| " + " | ".join(_markdown_cell(column, hit.get(column, "")) for column in columns) + " |"
        for hit in hits
    ]
    return "\n".join([header, separator, *body])


def _markdown_cell(column: str, value: Any) -> str:
    if column == "vector_score":
        return f"{float(value):.4f}"
    return str(value).replace("|", "&#124;")
