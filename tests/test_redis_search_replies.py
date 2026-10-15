from cairn_support.redis_search_replies import (
    format_ft_search_reply,
    ft_info_dict,
    ft_search_inputs_markdown,
    ft_search_markdown_table,
    ft_search_result_summary,
    ft_search_rows,
)


def test_ft_info_resp3_dict():
    reply = {"num_docs": 36, "hash_indexing_failures": 0}
    assert ft_info_dict(reply) == reply


def test_ft_info_resp2_list():
    reply = ["num_docs", 36, "hash_indexing_failures", 0]
    assert ft_info_dict(reply) == {"num_docs": 36, "hash_indexing_failures": 0}


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
    assert "{" in format_ft_search_reply(reply)


def test_ft_search_resp2():
    reply = [
        1,
        "cairn:run:help:1",
        ["title", "T", "vector_score", "0.12"],
    ]
    total, rows = ft_search_rows(reply)
    assert total == 1
    assert rows[0]["vector_score"] == "0.12"
    assert "(integer) 1" in format_ft_search_reply(reply)


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


def test_ft_search_result_summary_with_keep():
    assert ft_search_result_summary(36, 5, 3) == "36 matched, 5 returned, 3 kept by the filter in the app"
    assert ft_search_result_summary(0, 0) == "0 matched, 0 returned"


def test_ft_search_inputs_markdown():
    md = ft_search_inputs_markdown("hello", {"$radius": 0.6})
    assert "hello" in md
    assert "$radius" in md
    assert "0.6" in md
