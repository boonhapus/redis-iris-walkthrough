from cairn_support.redis_search_replies import (
    ft_search_markdown_table,
    ft_search_rows,
)


def test_ft_search_resp3():
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
    total, rows = ft_search_rows(reply)
    assert total == 1
    assert rows[0]["title"] == "T"


def test_ft_search_resp2():
    reply = [
        1,
        "cairn:run:help:1",
        ["title", "T", "vector_score", "0.12"],
    ]
    total, rows = ft_search_rows(reply)
    assert total == 1
    assert rows[0]["vector_score"] == "0.12"


def test_ft_search_markdown_table():
    rows = [
        {
            "title": "A | B",
            "category": "gear",
            "year": "2025",
            "vector_score": "0.12345",
        }
    ]
    table = ft_search_markdown_table(rows)
    assert "&#124;" in table
    assert "0.1235" in table
    assert "| title | category | year | vector_score |" in table
    assert "|:--|:--|--:|--:|" in table
