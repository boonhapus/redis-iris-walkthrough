from cairn_support.redis_search_replies import format_search_table, parse_search_reply


def test_parse_resp3_map() -> None:
    reply = {
        "total_results": 1,
        "results": [
            {
                "id": "cairn:run:help:1",
                "extra_attributes": {
                    "title": "T",
                    "category": "gear",
                    "year": "2024",
                    "vector_score": "0.12",
                },
                "values": [],
            }
        ],
        "warning": [],
    }
    results = parse_search_reply(reply)
    assert results.total == 1
    assert results.hits[0]["key"] == "cairn:run:help:1"
    assert results.hits[0]["title"] == "T"


def test_parse_resp2_list() -> None:
    reply = [
        1,
        "cairn:run:help:1",
        ["title", "T", "vector_score", "0.12"],
    ]
    results = parse_search_reply(reply)
    assert results.total == 1
    assert results.hits[0]["key"] == "cairn:run:help:1"
    assert results.hits[0]["vector_score"] == "0.12"


def test_parse_map_without_results() -> None:
    results = parse_search_reply({"total_results": 4})
    assert results.total == 4
    assert results.hits == []


def test_format_search_table() -> None:
    rows = [
        {
            "title": "A | B",
            "category": "gear",
            "year": "2025",
            "vector_score": "0.12345",
        }
    ]
    table = format_search_table(rows)
    assert "&#124;" in table
    assert "0.1235" in table
    assert "| title | category | year | vector_score |" in table
    assert "|:--|:--|--:|--:|" in table
