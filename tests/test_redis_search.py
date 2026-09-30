"""Tests for column 0, Redis Search (plan section 5).

No server. Redis is a small in-memory fake that speaks RESP2 (bytes, flat lists) for exactly
the commands this column sends, so the argv, the reply parsing and the cells themselves run
for real. What the fake cannot tell us is listed in tests/README.md under "What only a live
run can show". How Redis words a missing index is one thing it can be told: FakeSearch answers
in either the 8.6 wording (seen live) or the older one, and the tests below run both.
"""

import ast
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import notebook_ast as nbast
import pytest
import redis
from fake_search import MISSING_INDEX, FakeSearch, missing_index, stems, txt


COLUMN_PREFIX = "rs_"  # tests/conftest.py: this module tests column 0


@pytest.fixture(autouse=True)
def column_present(nb):
    if not hasattr(nb, "rs_articles"):
        pytest.skip("this module has no column 0")


@pytest.fixture
def server(nb, monkeypatch):
    """A FakeSearch that answers every command any client sends (the guard still runs first)."""
    fake = FakeSearch()
    monkeypatch.setattr(nb.redis.Redis, "execute_command", lambda self, *a, **k: fake.handle(a))
    return fake


@pytest.fixture
def client(nb, server):
    return nb.GuardedTracedRedis.from_url(
        "redis://default:fake-pw-1234@db.example:6379", protocol=2, decode_responses=False
    )


@pytest.fixture
def embed(nb):
    """The hashing embedder: deterministic, and needs no model."""
    return nb.rs_hash_embed


@pytest.fixture
def loaded(nb, client, embed):
    """A client on a server that has been seeded."""
    nb.rs_seed_corpus(client, embed, len(embed(["probe"])[0]))
    return client


# ---- the commands: exact argv ---------------------------------------------------------
class TestArgv:
    def test_ft_create_watches_the_document_prefix_and_counts_its_vector_tokens(self, nb):
        argv = nb.rs_create_args(nb.rs_index(), 256)
        assert argv == [
            "FT.CREATE", f"{nb.RS_PREFIX}idx", "ON", "HASH", "PREFIX", 1, f"{nb.RS_PREFIX}doc:",
            "SCHEMA", "title", "TEXT", "WEIGHT", 2, "body", "TEXT", "category", "TAG",
            "year", "NUMERIC", "vec", "VECTOR", "HNSW", 6, "TYPE", "FLOAT32", "DIM", 256,
            "DISTANCE_METRIC", "COSINE",
        ]  # fmt: skip
        vector = argv.index("VECTOR")
        assert argv[vector + 2] == len(argv[vector + 3 :])  # the count is the tokens that follow

    @pytest.mark.parametrize("algorithm", ["FLAT", "HNSW", "SVS-VAMANA"])
    @pytest.mark.parametrize("metric", ["COSINE", "IP", "L2"])
    def test_ft_create_takes_an_algorithm_and_a_metric(self, nb, algorithm, metric):
        argv = nb.rs_create_args("i", 384, algorithm, metric)
        assert argv[argv.index("VECTOR") + 1] == algorithm
        assert argv[argv.index("DIM") + 1] == 384
        assert argv[-2:] == ["DISTANCE_METRIC", metric]

    def test_every_create_passes_the_guard_and_covers_only_the_document_prefix(self, nb, rec):
        for argv in (
            nb.rs_create_args(nb.rs_index(), 256),
            nb.rs_create_args(nb.rs_index() + "2", 256, "FLAT", "L2"),
        ):
            rec.execute_command(*argv)
            assert nb._ft_create_prefixes(argv) == [f"{nb.RS_PREFIX}doc:"]
        assert [a[0] for a in rec.sent if a[0] == "FT.CREATE"] == ["FT.CREATE"] * 2

    def test_hset_writes_text_tag_number_and_the_vector_bytes(self, nb):
        article = {"id": "a1", "title": "T", "body": "B", "category": "care", "year": 2025}
        assert nb.rs_doc_args(article, b"\x00" * 1024) == [
            "HSET", f"{nb.RS_PREFIX}doc:a1", "title", "T", "body", "B", "category", "care",
            "year", 2025, "vec", b"\x00" * 1024,
        ]  # fmt: skip

    def test_the_text_query_is_a_union_of_the_words_that_survive_the_stopwords(self, nb):
        assert nb.rs_text_query("will it fit my feet") == "(fit|my|feet)"
        assert nb.rs_text_query("The Trailhead!") == "(trailhead)"
        assert nb.rs_text_query("the of and") == "" and nb.rs_text_query("  ") == ""

    def test_prefix_and_fuzzy_add_alternatives_for_every_word(self, nb):
        assert nb.rs_text_query("hiking boots", prefix=True) == "(hiking|hiking*|boots|boots*)"
        assert nb.rs_text_query("bots", fuzzy=True) == "(bots|%bots%)"
        assert nb.rs_text_query("ski", prefix=True, fuzzy=True) == "(ski|ski*|%ski%)"
        assert nb.rs_text_query("a b", prefix=True) == "(b)"  # "a" is a stopword, "b" too short

    def test_text_search_asks_for_bm25std_scores_in_dialect_2(self, nb):
        assert nb.rs_text_args("idx", "(a|b)") == [
            "FT.SEARCH", "idx", "(a|b)", "SCORER", "BM25STD", "WITHSCORES",
            "RETURN", 3, "title", "category", "year", "LIMIT", 0, 10, "DIALECT", 2,
        ]  # fmt: skip

    def test_knn_limits_to_k_sorts_by_distance_and_passes_the_vector_as_a_param(self, nb):
        assert nb.rs_knn_args("idx", b"BLOB", 5) == [
            "FT.SEARCH", "idx", "*=>[KNN 5 @vec $BLOB AS dist]",
            "PARAMS", 2, "BLOB", b"BLOB", "SORTBY", "dist", "ASC",
            "RETURN", 4, "title", "category", "year", "dist", "LIMIT", 0, 5, "DIALECT", 2,
        ]  # fmt: skip

    def test_knn_with_a_prefilter_puts_it_before_the_arrow(self, nb):
        pre = nb.rs_filter(["returns", "shipping"], [2024, 2026])
        assert pre == "(@category:{returns|shipping} @year:[2024 2026])"
        argv = nb.rs_knn_args("idx", b"B", 7, pre)
        assert argv[2] == f"{pre}=>[KNN 7 @vec $BLOB AS dist]"
        assert argv[argv.index("LIMIT") + 2] == 7

    def test_a_filter_without_categories_is_only_the_year_range(self, nb):
        assert nb.rs_filter([], (2022, 2026.0)) == "(@year:[2022 2026])"

    def test_range_yields_the_distance_and_can_return_many(self, nb):
        assert nb.rs_range_args("idx", b"B", 0.35) == [
            "FT.SEARCH", "idx", "@vec:[VECTOR_RANGE $R $BLOB]=>{$YIELD_DISTANCE_AS: dist}",
            "PARAMS", 4, "R", 0.35, "BLOB", b"B", "SORTBY", "dist", "ASC",
            "RETURN", 4, "title", "category", "year", "dist", "LIMIT", 0, 100, "DIALECT", 2,
        ]  # fmt: skip

    def test_every_search_is_dialect_2_and_its_params_count_their_tokens(self, nb):
        for argv in (
            nb.rs_text_args("i", "(a)"),
            nb.rs_knn_args("i", b"B", 3),
            nb.rs_range_args("i", b"B", 0.4),
        ):
            assert argv[argv.index("DIALECT") + 1] == 2
            if "PARAMS" in argv:
                at = argv.index("PARAMS")
                count = argv[at + 1]  # name/value pairs, so twice the number of params
                assert count % 2 == 0 and argv[at + 2 + count] == "SORTBY"

    def test_ft_hybrid_uses_the_redispy_grammar_with_rrf(self, nb):
        assert nb.rs_hybrid_args("idx", "(a|b)", b"B", 10) == [
            "FT.HYBRID", "idx", "SEARCH", "(a|b)", "SCORER", "BM25STD",
            "VSIM", "@vec", "$BLOB", "KNN", 2, "K", 10,
            "COMBINE", "RRF", 4, "WINDOW", 20, "CONSTANT", 60,
            "LOAD", 1, "@__key", "LIMIT", 0, 10, "PARAMS", 2, "BLOB", b"B",
        ]  # fmt: skip

    def test_every_read_the_column_sends_is_on_the_guard_allowlist(self, nb, rec):
        rec.execute_command(*nb.rs_text_args("i", "(a)"))
        rec.execute_command(*nb.rs_knn_args("i", b"B", 3))
        rec.execute_command(*nb.rs_range_args("i", b"B", 0.4))
        rec.execute_command(*nb.rs_hybrid_args("i", "(a)", b"B", 10))
        rec.execute_command("FT.INFO", "i")
        assert len(rec.sent) == 5


class TestCommandsSent:
    """The exact argv of the commands that are not built by a helper."""

    @staticmethod
    def argv(server):
        return [tuple(txt(a) for a in argv) for argv in server.sent]

    def test_connect_reads_exactly_these_commands(self, nb, server):
        probe(nb)
        assert self.argv(server) == [
            ("INFO", "server"),
            ("FT._LIST",),
            ("SCAN", "0", "MATCH", "irisnb_*", "COUNT", "1000"),
            ("DBSIZE",),
            ("INFO", "clients"),
        ]

    def test_the_seed_expires_each_key_for_an_hour_right_after_writing_it(self, nb, server, loaded):
        sent = [a for a in server.sent if txt(a[0]) in {"HSET", "EXPIRE"}]
        key = nb.rs_doc_key("ship-times")
        at = next(i for i, a in enumerate(sent) if txt(a[1]) == key)
        assert (txt(sent[at][0]), txt(sent[at + 1][0])) == ("HSET", "EXPIRE")
        assert tuple(txt(a) for a in sent[at + 1]) == ("EXPIRE", key, "3600")

    def test_cleanup_drops_by_name_then_unlinks_the_ledgered_keys_in_one_command(
        self, nb, server, client, loaded
    ):
        server.sent.clear()
        nb.rs_cleanup(client)
        drop, unlink = self.argv(server)
        assert drop == ("FT.DROPINDEX", nb.rs_index())
        assert unlink[0] == "UNLINK" and sorted(unlink[1:]) == sorted(
            nb.rs_doc_key(a["id"]) for a in nb.rs_articles()
        )

    def test_the_index_is_read_with_ft_info_by_name(self, nb, server, client, loaded):
        server.sent.clear()
        nb.rs_index_info(client, nb.rs_index())
        assert self.argv(server) == [("FT.INFO", nb.rs_index())]


# ---- the replies: RESP2 parsing -------------------------------------------------------------
KEY = "irisnb_0123abcd_rs:doc:size-boots"


class TestReplyParsing:
    def test_scored_rows_carry_key_score_and_fields(self, nb):
        reply = [
            2,
            KEY.encode(), b"3.5",
            [b"title", b"Finding your boot size", b"category", b"sizing", b"year", b"2025"],
            b"irisnb_0123abcd_rs:doc:care-boots", b"1.25",
            [b"title", b"Cleaning boots", b"category", b"care", b"year", b"2025"],
        ]  # fmt: skip
        total, rows = nb.rs_parse_search(reply, scored=True)
        assert total == 2
        assert rows[0] == {
            "key": KEY, "score": 3.5, "title": "Finding your boot size",
            "category": "sizing", "year": "2025",
        }  # fmt: skip
        assert rows[1]["score"] == 1.25 and rows[1]["key"].endswith("care-boots")

    def test_knn_rows_have_a_float_distance(self, nb):
        reply = [1, KEY.encode(), [b"title", b"t", b"dist", b"0.369281"]]
        assert nb.rs_parse_search(reply) == (1, [{"key": KEY, "title": "t", "dist": 0.369281}])

    def test_an_empty_range_result_is_no_rows(self, nb):
        assert nb.rs_parse_search([0]) == (0, [])
        assert nb.rs_parse_search([b"0"]) == (0, [])

    def test_a_row_may_return_no_fields(self, nb):
        assert nb.rs_parse_search([1, KEY.encode(), []]) == (1, [{"key": KEY}])

    def test_text_is_decoded_as_utf8(self, nb):
        _, rows = nb.rs_parse_search([1, KEY.encode(), [b"title", "café".encode()]])
        assert rows[0]["title"] == "café"

    def test_info_reads_the_numbers_from_bytes_or_ints(self, nb):
        reply = [
            b"index_name", b"idx",
            b"index_definition", [b"key_type", b"HASH", b"prefixes", [b"p:"]],
            b"attributes", [[b"identifier", b"vec", b"type", b"VECTOR"]],
            b"num_docs", b"36", b"vector_index_sz_mb", b"0.045",
            b"total_index_memory_sz_mb", b"0.31",
            b"hash_indexing_failures", 2, b"indexing", 1, b"percent_indexed", b"0.5",
        ]  # fmt: skip
        assert nb.rs_parse_info(reply) == {
            "docs": 36, "failures": 2, "indexing": True, "vector_mb": 0.045, "total_mb": 0.31,
        }  # fmt: skip

    def test_info_with_fields_missing_is_zeros_not_a_crash(self, nb):
        zeros = {"docs": 0, "failures": 0, "indexing": False, "vector_mb": 0.0, "total_mb": 0.0}
        assert nb.rs_parse_info([b"index_name", b"idx"]) == zeros
        assert nb.rs_parse_info(b"not a list") == zeros

    def test_hybrid_reads_the_keys_in_order(self, nb):
        reply = [
            b"total_results", 2, b"results", [[b"__key", b"a"], [b"__key", b"b"]],
            b"warnings", [], b"execution_time", b"1.2",
        ]  # fmt: skip
        assert nb.rs_parse_hybrid(reply) == ["a", "b"]
        assert nb.rs_parse_hybrid([b"results", []]) == []

    @pytest.mark.parametrize(
        "reply",
        [
            [2, [b"__key", b"a"], [b"__key", b"b"]],  # the docs' array-with-a-total shape
            [3] + [[b"__key", b"k"]] * 3,  # the same, with an even length: not a map either
            [5] + [[b"__key", b"k"]] * 5,
            [b"total_results", 1, b"results", [[b"title", b"t"]]],  # no @__key
            [b"total_results", 1],  # no results
            b"OK",
            None,
        ],
    )
    def test_hybrid_replies_it_does_not_recognise_are_a_valueerror(self, nb, reply):
        with pytest.raises(ValueError):
            nb.rs_parse_hybrid(reply)

    def test_unknown_index_is_recognised_however_it_is_capitalised(self, nb):
        for message in ("Unknown index name", "Unknown Index name", "idx: no such index"):
            assert nb.is_unknown_index(redis.ResponseError(message)), message
        assert not nb.is_unknown_index(redis.ResponseError("OOM command not allowed"))

    @pytest.mark.parametrize(
        "message",
        [
            "SEARCH_INDEX_NOT_FOUND Index not found: irisnb_0badc0de_rs:idx2",  # Redis 8.6.2, live
            "search_index_not_found index not found: idx",
            "Index not found: idx:some-surface:337a0390:customer",
            "INDEX NOT FOUND",
        ],
    )
    def test_redis_8_6_words_a_missing_index_differently_and_it_is_still_recognised(
        self, nb, message
    ):
        assert nb.is_unknown_index(redis.ResponseError(message))

    @pytest.mark.parametrize(
        "message",
        [
            "LOADING Redis is loading the dataset in memory",
            "SEARCH_INDEX_EXISTS Index already exists",
            "Index already exists",
            "Not found: check the host",
        ],
    )
    def test_other_errors_are_not_taken_for_a_missing_index(self, nb, message):
        assert not nb.is_unknown_index(redis.ResponseError(message))

    @pytest.mark.parametrize("wording", MISSING_INDEX)
    def test_the_fake_words_a_missing_index_in_both_ways_and_the_notebook_reads_both(
        self, nb, server, client, wording
    ):
        server.wording = wording
        for command in (("FT.INFO",), ("FT.DROPINDEX",)):
            with pytest.raises(redis.ResponseError) as caught:
                client.execute_command(*command, nb.rs_index())
            assert nb.is_unknown_index(caught.value)
        assert str(missing_index("i", wording)) == MISSING_INDEX[wording].format(name="i")


# ---- rank fusion and the rank table ------------------------------------------------------------
def ranked(nb, target, bm25, vector, hybrid):
    """A wow result whose engines ranked the given article ids, best first."""

    def keys(ids):
        return [nb.rs_doc_key(i) for i in ids]

    return {
        "preset": {"target": target, "query": "q"},
        "BM25": keys(bm25),
        "vector": keys(vector),
        "hybrid": keys(hybrid),
    }


class TestRanking:
    def test_rrf_rewards_agreement_over_a_single_first_place(self, nb):
        # y and z sit in both lists; x is first in one list only and still loses to both
        assert nb.rs_rrf(["x", "y", "z"], ["y", "z", "w"]) == ["y", "z", "x", "w"]

    def test_rrf_of_one_empty_ranking_is_the_other(self, nb):
        assert nb.rs_rrf([], ["x", "y"]) == ["x", "y"]
        assert nb.rs_rrf([], []) == []

    def test_the_rank_table_has_a_row_per_document_and_stars_the_target(self, nb):
        wow = ranked(
            nb, "size-boots",
            ["size-jackets", "size-packs"], ["size-boots", "size-packs"], ["size-packs", "size-boots"],
        )  # fmt: skip
        rows = nb.rs_rank_rows(wow, "hybrid")
        assert [r["article"] for r in rows] == [
            "Choosing a pack size by torso length",
            "Finding your boot size",
            "Jacket and shell fit guide",
        ]
        boots = next(r for r in rows if r["answer"] == "★")
        assert (boots["BM25 rank"], boots["vector rank"], boots["hybrid rank"]) == (None, 1, 2)
        assert sum(r["answer"] == "★" for r in rows) == 1

    def test_sorting_by_an_engine_puts_its_first_place_first_and_unranked_last(self, nb):
        wow = ranked(
            nb, "size-boots", ["size-jackets"], ["size-boots", "size-packs"], ["size-boots"]
        )
        assert nb.rs_rank_rows(wow, "BM25")[0]["article"] == "Jacket and shell fit guide"
        by_vector = [r["article"] for r in nb.rs_rank_rows(wow, "vector")]
        assert by_vector[:2] == ["Finding your boot size", "Choosing a pack size by torso length"]

    def test_the_summary_says_where_each_engine_put_the_target_or_that_it_missed(self, nb):
        wow = ranked(
            nb, "size-boots", ["size-jackets"], ["size-boots"], ["size-jackets", "size-boots"]
        )
        line = nb.rs_wow_summary(wow)
        assert "Finding your boot size" in line
        assert "BM25 missed it" in line and "vector #1" in line and "hybrid #2" in line

    def test_a_question_of_the_users_own_has_no_answer_to_star_or_summarise(self, nb):
        own = nb.rs_wow_preset("Type your own", "  hiking socks ")
        assert own["query"] == "hiking socks" and own["target"] is None and own["lesson"]
        wow = ranked(nb, None, ["size-boots"], ["size-boots", "size-packs"], ["size-boots"])
        wow["preset"] = own
        rows = nb.rs_rank_rows(wow, "hybrid")
        assert [list(r) for r in rows] == [
            ["article", "category", "BM25 rank", "vector rank", "hybrid rank"]
        ] * 2
        assert nb.rs_wow_summary(wow) == ""

    def test_a_label_that_names_a_preset_gives_that_preset_whatever_was_typed(self, nb):
        for preset in nb.rs_wow_presets():
            assert nb.rs_wow_preset(preset["label"], "anything") == preset

    def test_every_preset_names_a_real_article_and_has_a_lesson(self, nb):
        ids = {a["id"] for a in nb.rs_articles()}
        presets = nb.rs_wow_presets()
        assert len(presets) == 3 and len({p["label"] for p in presets}) == 3
        for preset in presets:
            assert preset["target"] in ids and preset["query"].strip() and preset["lesson"]


# ---- the corpus ----------------------------------------------------------------------------------
class TestCorpus:
    def test_36_articles_six_in_each_of_six_categories(self, nb):
        articles = nb.rs_articles()
        assert len(articles) == 36 and len({a["id"] for a in articles}) == 36
        assert sorted(Counter(a["category"] for a in articles).values()) == [6] * 6

    def test_every_article_has_the_five_fields_and_a_year_in_range(self, nb):
        for a in nb.rs_articles():
            assert set(a) == {"id", "title", "body", "category", "year"}
            assert a["title"] and a["body"] and re.fullmatch(r"[a-z]+", a["category"])
            assert 2022 <= a["year"] <= 2026 and re.fullmatch(r"[a-z-]+", a["id"])


# ---- the seed --------------------------------------------------------------------------------------
class TestSeed:
    def test_it_creates_the_index_first_then_writes_and_expires_every_article(
        self, nb, server, loaded
    ):
        writes = [n for n in server.names() if n in {"FT.CREATE", "HSET", "EXPIRE"}]
        assert writes[0] == "FT.CREATE" and writes[1:] == ["HSET", "EXPIRE"] * 36
        assert len(server.hashes) == 36 and set(server.ttls.values()) == {nb.KEY_TTL_S}
        assert all(k.startswith(nb.rs_doc_key()) for k in server.hashes)

    def test_every_write_stays_under_the_run_prefix(self, nb, server, loaded):
        assert server.writes() and all(t.startswith(nb.PREFIX) for _, t in server.writes())
        assert not {"DEL", "FLUSHDB", "CONFIG"} & set(server.names())

    def test_every_write_is_paced_by_the_client_and_no_read_is(
        self, nb, server, client, embed, monkeypatch
    ):
        paced = []
        monkeypatch.setattr(nb, "pace", lambda: paced.append(1))
        nb.rs_seed_corpus(client, embed, 256)
        nb.rs_search_text(client, "boots")
        assert len(paced) == len(server.writes()) == 1 + 36 + 36

    def test_each_vector_is_dim_times_four_bytes(self, server, loaded):
        assert {len(f["vec"]) for f in server.hashes.values()} == {256 * 4}

    def test_the_ledger_has_the_index_and_every_key_as_created(self, nb, server, loaded):
        assert nb.ledger_live("rs", "index") == [nb.rs_index()]
        assert sorted(nb.ledger_live("rs", "key")) == sorted(server.hashes)

    def test_intent_is_on_disk_before_each_create_reaches_the_server(
        self, nb, server, client, embed, monkeypatch
    ):
        seen, real = [], server.handle

        def spy(args):
            name = txt(args[0]).upper()
            if name in {"FT.CREATE", "HSET"}:
                kind = "index" if name == "FT.CREATE" else "key"
                seen.append(nb.ledger_state("rs", kind).get(txt(args[1])))
            return real(args)

        monkeypatch.setattr(server, "handle", spy)
        nb.rs_seed_corpus(client, embed, 256)
        assert seen == ["intent"] * 37
        assert set(nb.ledger_state("rs", "key").values()) == {"created"}

    def test_a_create_that_fails_leaves_an_intent_cleanup_can_still_find(
        self, nb, server, client, embed, monkeypatch
    ):
        def oom(*args):
            raise redis.ResponseError("OOM command not allowed when used memory > 'maxmemory'.")

        monkeypatch.setattr(server, "ft_create", oom)
        with pytest.raises(redis.ResponseError, match="OOM"):
            nb.rs_seed_corpus(client, embed, 256)
        assert nb.ledger_live("rs", "index") == []
        assert nb.ledger_live("rs", "index", pending=True) == [nb.rs_index()]

    def test_pressing_seed_twice_just_writes_the_same_things_again(
        self, nb, server, client, embed, loaded
    ):
        nb.rs_seed_corpus(client, embed, 256)
        assert len(server.hashes) == 36 and list(server.indexes) == [nb.rs_index()]

    def test_it_cannot_write_outside_the_prefix_even_if_told_to(self, nb, server, client):
        with pytest.raises(nb.Denied):
            client.execute_command("HSET", "prod:customer:1", "name", "x")
        with pytest.raises(nb.Denied):
            client.execute_command(
                "FT.CREATE", "prod_idx", "ON", "HASH", "PREFIX", 1, "prod:", "SCHEMA", "t", "TEXT"
            )
        assert server.writes() == []


# ---- the reads ---------------------------------------------------------------------------------------
class TestSearches:
    def test_keyword_search_finds_the_article_with_the_word_and_scores_it(self, nb, loaded):
        found = nb.rs_search_text(loaded, "Trailhead")
        assert found["total"] == 1 and found["argv"][2] == "(trailhead)"
        assert found["rows"][0]["title"] == "Resoling worn boots" and found["rows"][0]["score"] > 0

    def test_a_title_match_outranks_a_body_match(self, nb, loaded):
        rows = nb.rs_search_text(loaded, "boots")["rows"]
        assert "boot" in rows[0]["title"].lower() and "boot" not in rows[-1]["title"].lower()
        assert rows[0]["score"] > rows[-1]["score"]

    def test_prefix_and_fuzzy_widen_what_matches(self, nb, loaded):
        assert nb.rs_search_text(loaded, "bots")["total"] == 0
        assert nb.rs_search_text(loaded, "bots", fuzzy=True)["total"] > 0
        assert nb.rs_search_text(loaded, "boo")["total"] == 0
        assert nb.rs_search_text(loaded, "boo", prefix=True)["total"] > 0

    def test_text_that_is_all_stopwords_sends_nothing(self, nb, loaded, server):
        before = len(server.sent)
        assert nb.rs_search_text(loaded, "the of") == {"argv": None, "total": 0, "rows": []}
        assert len(server.sent) == before

    def test_knn_always_returns_k_rows_even_for_nonsense(self, nb, loaded, embed):
        for k in (1, 3, 10):
            assert len(nb.rs_knn_search(loaded, embed, "zzzz qqqq xxxx", k)["rows"]) == k

    def test_a_radius_can_return_nothing_where_knn_cannot(self, nb, loaded, embed):
        knn = nb.rs_knn_search(loaded, embed, "zzzz qqqq xxxx", 3)
        nothing = nb.rs_range_search(loaded, embed, "zzzz qqqq xxxx", 0.05)
        assert len(knn["rows"]) == 3 and nothing["rows"] == []

    def test_a_wider_radius_returns_more_and_the_nearest_first(self, nb, loaded, embed):
        nearest = nb.rs_knn_search(loaded, embed, "fit feet boots", 10)["rows"]
        third = nearest[2]["dist"]
        small = nb.rs_range_search(loaded, embed, "fit feet boots", third + 1e-6)["rows"]
        large = nb.rs_range_search(loaded, embed, "fit feet boots", 2.0)["rows"]
        assert [r["key"] for r in small] == [r["key"] for r in nearest[:3]]
        assert len(large) == 36
        assert [r["dist"] for r in large] == sorted(r["dist"] for r in large)

    def test_a_slider_value_is_rounded_before_it_is_sent(self, nb, loaded, embed):
        found = nb.rs_range_search(loaded, embed, "boots", 0.15000000000000002)
        assert found["radius"] == 0.15 and found["argv"][found["argv"].index("R") + 1] == 0.15

    def test_the_prefilter_keeps_every_row_inside_the_filter(self, nb, loaded, embed):
        every_year = nb.rs_filter(["returns"], [2022, 2026])
        rows = nb.rs_knn_search(loaded, embed, "how long does it take", 5, every_year)["rows"]
        assert len(rows) == 5 and {r["category"] for r in rows} == {"returns"}
        recent = nb.rs_filter(["returns"], [2024, 2026])
        rows = nb.rs_knn_search(loaded, embed, "how long does it take", 5, recent)["rows"]
        assert len(rows) == 4, "four returns articles were reviewed in 2024 or later"
        assert all(2024 <= int(r["year"]) <= 2026 for r in rows)

    def test_a_filter_that_leaves_fewer_than_k_returns_fewer(self, nb, loaded, embed):
        only_one = nb.rs_filter(["warranty"], [2026, 2026])
        assert len(nb.rs_knn_search(loaded, embed, "anything", 5, only_one)["rows"]) == 1

    def test_distances_come_back_as_floats_between_0_and_2(self, nb, loaded, embed):
        rows = nb.rs_knn_search(loaded, embed, "boots", 5)["rows"]
        assert all(isinstance(r["dist"], float) and 0 <= r["dist"] <= 2 for r in rows)

    def test_the_table_rows_show_rank_then_score_or_distance_then_the_article(
        self, nb, loaded, embed
    ):
        text = nb.rs_hit_rows(nb.rs_search_text(loaded, "Trailhead")["rows"])
        assert list(text[0]) == ["#", "score", "title", "category", "year"]
        knn = nb.rs_hit_rows(nb.rs_knn_search(loaded, embed, "boots", 2)["rows"])
        assert list(knn[1]) == ["#", "dist", "title", "category", "year"] and knn[1]["#"] == 2


# ---- the wow --------------------------------------------------------------------------------------------
class TestWow:
    def test_hybrid_comes_from_ft_hybrid_when_the_server_has_it(self, nb, loaded, embed):
        exact_name = nb.rs_wow_presets()[1]
        wow = nb.rs_wow_search(loaded, embed, exact_name, has_hybrid=True)
        assert wow["source"] == "FT.HYBRID" and wow["note"] == ""
        assert len(wow["commands"]) == 3 and wow["commands"][2].startswith("FT.HYBRID")
        assert wow["hybrid"][0] == nb.rs_doc_key("warranty-resole")

    def test_below_8_4_it_fuses_the_two_rankings_itself_and_says_so(
        self, nb, loaded, embed, server
    ):
        wow = nb.rs_wow_search(loaded, embed, nb.rs_wow_presets()[1], has_hybrid=False)
        assert wow["source"] == "client-side RRF" and "8.4" in wow["note"]
        assert wow["hybrid"] == nb.rs_rrf(wow["BM25"], wow["vector"])
        assert "FT.HYBRID" not in server.names() and len(wow["commands"]) == 2

    def test_a_server_that_rejects_ft_hybrid_falls_back_and_says_why(
        self, nb, loaded, embed, server
    ):
        server.hybrid = False
        wow = nb.rs_wow_search(loaded, embed, nb.rs_wow_presets()[1], has_hybrid=True)
        assert wow["source"] == "client-side RRF" and "did not work" in wow["note"]
        assert "unknown command" in wow["note"]
        assert wow["hybrid"] == nb.rs_rrf(wow["BM25"], wow["vector"])
        assert len(wow["commands"]) == 3  # the failed attempt is still in the trace

    @pytest.mark.parametrize("rows", [2, 3, 5])
    def test_a_reply_in_an_unexpected_shape_falls_back_too(
        self, nb, loaded, embed, server, monkeypatch, rows
    ):
        # The docs' shape is a total followed by the rows. With an odd count that is an
        # even-length list of lists, which once raised a TypeError instead of falling back.
        reply = [rows] + [[b"__key", b"k"]] * rows
        monkeypatch.setattr(server, "ft_hybrid", lambda *a: reply)
        wow = nb.rs_wow_search(loaded, embed, nb.rs_wow_presets()[1], has_hybrid=True)
        assert wow["source"] == "client-side RRF" and "unrecognised" in wow["note"]

    def test_a_question_of_the_users_own_runs_all_three_engines_and_has_no_target(
        self, nb, loaded, embed
    ):
        own = nb.rs_wow_preset("Type your own", "Trailhead boots")
        wow = nb.rs_wow_search(loaded, embed, own, has_hybrid=True)
        assert wow["preset"]["target"] is None and wow["source"] == "FT.HYBRID"
        assert wow["BM25"] and wow["vector"] and len(wow["commands"]) == 3
        assert nb.rs_doc_key("warranty-resole") in wow["BM25"]

    def test_the_typo_gives_keyword_search_nothing_and_hybrid_falls_back_on_vectors(
        self, nb, loaded, embed
    ):
        wow = nb.rs_wow_search(loaded, embed, nb.rs_wow_presets()[2], has_hybrid=True)
        assert wow["BM25"] == [] and len(wow["vector"]) == 10 and wow["hybrid"] == wow["vector"]

    def test_every_preset_fills_a_table_of_known_articles_from_at_most_ten_per_engine(
        self, nb, loaded, embed
    ):
        for preset in nb.rs_wow_presets():
            wow = nb.rs_wow_search(loaded, embed, preset, has_hybrid=True)
            assert all(len(wow[engine]) <= 10 for engine in ("BM25", "vector", "hybrid"))
            rows = nb.rs_rank_rows(wow, "hybrid")
            assert 0 < len(rows) <= 30 and sum(r["answer"] == "★" for r in rows) <= 1


# ---- connect: the read-only probe --------------------------------------------------------------------
def probe(nb, url="redis://default:fake-pw-1234@h:6379"):
    return nb.rs_probe(nb.GuardedTracedRedis.from_url(url))


class TestProbe:
    @pytest.mark.parametrize(
        "version, hybrid, svs",
        [
            ("8.6.0", True, True), ("8.4.0", True, True), ("8.2.3", False, True),
            ("8.0.2", False, False), ("7.4.2", False, False), ("9.0.0", True, True),
            ("", False, False),
        ],
    )  # fmt: skip
    def test_the_version_sets_the_two_feature_flags(self, nb, server, version, hybrid, svs):
        server.version = version
        facts = probe(nb)
        assert (facts["hybrid"], facts["svs"]) == (hybrid, svs)

    def test_it_reports_size_clients_indexes_and_this_and_other_runs_keys(self, nb, server):
        server.hashes.update(
            {
                f"{nb.RS_PREFIX}doc:a": {}, f"{nb.RS_PREFIX}doc:b": {},
                "irisnb_deadbeef_rs:doc:x": {}, "prod:1": {},
            }
        )  # fmt: skip
        server.ttls["irisnb_deadbeef_rs:doc:x"] = 1500
        server.add_index("irisnb_deadbeef_rs:idx", "irisnb_deadbeef_rs:doc:")
        facts = probe(nb)
        assert facts["dbsize"] == 4 and facts["clients"] == 3 and facts["where"] == "h:6379"
        assert facts["runs"] == {
            nb.RUN: {"keys": 2, "ttl": -1},
            "deadbeef": {"keys": 1, "ttl": 1500},
        }
        assert facts["indexes"] == ["irisnb_deadbeef_rs:idx"]

    def test_it_only_reads(self, nb, server):
        probe(nb)
        assert server.writes() == []
        assert set(server.names()) <= {"INFO", "FT._LIST", "SCAN", "TTL", "DBSIZE"}

    def test_without_search_it_raises_a_config_error_that_says_how_to_fix_it(self, nb, server):
        server.search = False
        with pytest.raises(nb.ServiceError) as caught:
            probe(nb)
        assert caught.value.kind == "config" and "no Search" in str(caught.value)
        assert "redis.io/try-free" in str(caught.value) and caught.value.__cause__ is None

    def test_the_default_user_on_a_database_with_other_data_is_flagged_as_shared(self, nb, server):
        server.hashes["prod:customer:1"] = {}
        assert probe(nb)["shared"] is True
        assert probe(nb, "redis://app:fake-pw-1234@h:6379")["shared"] is False

    def test_an_empty_database_or_only_our_own_keys_is_not_shared(self, nb, server):
        assert probe(nb)["shared"] is False
        server.hashes[f"{nb.RS_PREFIX}doc:a"] = {}
        assert probe(nb)["shared"] is False

    def test_a_foreign_index_alone_makes_it_shared(self, nb, server):
        server.add_index("memory:store:ltm", "memory:store:ltm:")
        assert probe(nb)["shared"] is True

    def test_describe_shows_the_version_and_only_warns_when_shared(self, nb, server):
        text = nb.rs_describe(probe(nb)).text
        assert "8.4.0" in text and "FT.HYBRID" in text and "scratch" not in text
        server.hashes["prod:1"] = {}
        assert "scratch" in nb.rs_describe(probe(nb)).text

    def test_test_connection_is_read_only_and_says_what_it_found(self, nb, server, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://default:fake-pw-1234@h:6379")
        ok, detail = nb.rs_test_connection()
        assert ok is True and "Redis 8.4.0" in detail and "FT.HYBRID yes" in detail
        assert server.writes() == [] and "fake-pw-1234" not in detail

    def test_test_connection_without_settings_is_a_no_not_a_crash(self, nb, server):
        assert nb.rs_test_connection() == (False, "no REDIS_URL or REDIS_HOST is set")
        assert server.sent == []

    def test_test_connection_composes_a_url_from_the_split_settings(self, nb, server, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "db.example")
        monkeypatch.setenv("REDIS_PASSWORD", "p@ss/word-fake")
        assert nb.rs_test_connection()[0] is True

    def test_test_connection_raises_when_search_is_missing(self, nb, server, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://default:fake-pw-1234@h:6379")
        server.search = False
        with pytest.raises(nb.ServiceError, match="no Search"):
            nb.rs_test_connection()  # verify.py --preflight catches it and reports it scrubbed


class TestUrlSecret:
    def test_what_was_typed_wins_over_the_environment(self, nb, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://default:from-env-1234@h:1")
        typed = nb.Secret("redis://default:typed-fake-5678@h:2")
        assert nb.redis_url_secret(typed).reveal() == "redis://default:typed-fake-5678@h:2"

    def test_the_environment_is_the_fallback_and_none_means_not_configured(self, nb, monkeypatch):
        assert nb.redis_url_secret(None) is None
        monkeypatch.setenv("REDIS_URL", "redis://default:from-env-1234@h:1")
        assert nb.redis_url_secret(None).reveal() == "redis://default:from-env-1234@h:1"

    def test_the_result_is_a_secret_that_never_previews_the_url(self, nb, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://default:from-env-1234@h:1")
        secret = nb.redis_url_secret(None)
        assert isinstance(secret, nb.Secret) and "from-env-1234" not in repr(secret) + str(secret)

    def test_a_url_with_whitespace_is_refused_without_echoing_it(self, nb):
        with pytest.raises(ValueError) as caught:
            nb.redis_url_secret(nb.Secret("redis://default:pa ss-fake-1@h:1"))
        assert "pa ss-fake-1" not in str(caught.value)


# ---- cleanup and orphans ----------------------------------------------------------------------------------
class TestCleanup:
    def test_it_drops_the_ledgered_indexes_without_dd_and_unlinks_the_ledgered_keys(
        self, nb, server, client, loaded
    ):
        server.sent.clear()
        assert nb.rs_cleanup(client) == {
            "indexes": 1, "dropped": 1, "gone": 0, "keys": 36, "removed": 36,
        }  # fmt: skip
        assert server.hashes == {} and server.indexes == {}
        assert server.names() == ["FT.DROPINDEX", "UNLINK"]
        assert not [a for a in server.sent if any(txt(x).upper() == "DD" for x in a)]

    def test_it_works_from_the_ledger_and_never_scans_or_touches_other_runs(
        self, nb, server, client, loaded
    ):
        server.hashes["irisnb_deadbeef_rs:doc:other"] = {}
        server.sent.clear()
        nb.rs_cleanup(client)
        assert not {"SCAN", "KEYS"} & set(server.names())
        assert list(server.hashes) == ["irisnb_deadbeef_rs:doc:other"]

    def test_a_second_cleanup_has_nothing_to_do(self, nb, server, client, loaded):
        nb.rs_cleanup(client)
        assert nb.ledger_live("rs", "index", pending=True) == []
        assert nb.ledger_live("rs", "key", pending=True) == []
        server.sent.clear()
        assert nb.rs_cleanup(client) == {
            "indexes": 0, "dropped": 0, "gone": 0, "keys": 0, "removed": 0,
        }  # fmt: skip
        assert server.sent == []

    def test_keys_that_already_expired_are_counted_not_errors(self, nb, server, client, loaded):
        for key in list(server.hashes)[:5]:
            del server.hashes[key]
        assert nb.rs_cleanup(client)["removed"] == 31

    @pytest.mark.parametrize("wording", MISSING_INDEX)
    def test_an_index_that_is_already_gone_is_counted_apart_and_never_stops_the_cleanup(
        self, nb, server, client, loaded, wording
    ):
        server.indexes.clear()
        server.wording = wording
        done = nb.rs_cleanup(client)
        assert (done["indexes"], done["dropped"], done["gone"]) == (1, 0, 1)
        assert done["removed"] == 36 and server.hashes == {}
        assert nb.ledger_live("rs", "index", pending=True) == []
        assert nb.ledger_live("rs", "key", pending=True) == []

    def test_the_second_index_that_was_never_built_is_gone_too_in_redis_8_6_wording(
        self, nb, server, client, loaded
    ):
        nb.ledger_add("rs", "index", nb.rs_index() + "2", "intent")  # a build that never got far
        done = nb.rs_cleanup(client)
        assert (done["indexes"], done["dropped"], done["gone"]) == (2, 1, 1)
        assert server.indexes == {}

    def test_a_create_that_timed_out_is_still_cleaned_up_by_name(self, nb, server, client):
        nb.ledger_add("rs", "index", nb.rs_index(), "intent")
        nb.ledger_add("rs", "key", nb.rs_doc_key("a"), "intent")
        server.add_index(nb.rs_index(), nb.rs_doc_key())
        server.hashes[nb.rs_doc_key("a")] = {}
        assert nb.rs_cleanup(client) == {
            "indexes": 1, "dropped": 1, "gone": 0, "keys": 1, "removed": 1,
        }  # fmt: skip

    def test_a_real_error_is_not_swallowed_and_leaves_the_ledger_to_retry(
        self, nb, server, client, monkeypatch
    ):
        nb.ledger_add("rs", "index", nb.rs_index())

        def busy(*args):
            raise redis.ResponseError("LOADING Redis is loading the dataset in memory")

        monkeypatch.setattr(server, "ft_dropindex", busy)
        with pytest.raises(redis.ResponseError):
            nb.rs_cleanup(client)
        assert nb.ledger_live("rs", "index") == [nb.rs_index()]

    def test_a_ledger_line_outside_the_prefix_is_refused_by_the_guard(self, nb, server, client):
        server.hashes["prod:customer:1"] = {}
        nb.ledger_add("rs", "key", "prod:customer:1")
        with pytest.raises(nb.Denied):
            nb.rs_cleanup(client)
        assert "prod:customer:1" in server.hashes and server.writes() == []

    def test_the_report_gives_the_counts_and_says_when_there_was_nothing(self, nb):
        nothing = {"indexes": 0, "dropped": 0, "gone": 0, "keys": 0, "removed": 0}
        assert "Nothing to clean up" in nb.rs_cleanup_report(nothing)
        text = nb.rs_cleanup_report(
            {"indexes": 2, "dropped": 1, "gone": 1, "keys": 36, "removed": 30}
        )
        assert "**1** of 2 indexes" in text and "**30** of 36 keys" in text
        assert "Already gone: 1 of the indexes and 6 of the keys" in text
        everything = nb.rs_cleanup_report(
            {"indexes": 2, "dropped": 2, "gone": 0, "keys": 36, "removed": 36}
        )
        assert "**36** of 36 keys" in everything and "already gone" not in everything.lower()

    def test_an_index_that_was_gone_reads_as_information_and_not_as_a_stop(self, nb):
        text = nb.rs_cleanup_report(
            {"indexes": 1, "dropped": 0, "gone": 1, "keys": 36, "removed": 36}
        )
        assert "Already gone: 1 of the indexes." in text
        assert "stopped" not in text.lower() and "error" not in text.lower()

    def test_only_this_runs_ledger_is_read(self, nb, server, client, load_fresh):
        other = load_fresh(IRIS_BASICS_RUN="deadbeef")
        other.ledger_add("rs", "key", other.rs_doc_key("theirs"))
        server.hashes[other.rs_doc_key("theirs")] = {}
        assert nb.rs_cleanup(client)["keys"] == 0 and other.rs_doc_key("theirs") in server.hashes


class TestOrphans:
    OTHER = "irisnb_deadbeef_rs:idx"

    def test_leftovers_list_other_runs_keys_and_indexes_but_not_this_runs(self, nb, server):
        server.hashes.update(
            {
                f"{nb.RS_PREFIX}doc:a": {}, "irisnb_deadbeef_rs:doc:x": {},
                "irisnb_cafef00d_rs:doc:y": {},
            }
        )  # fmt: skip
        server.ttls["irisnb_deadbeef_rs:doc:x"] = 125
        server.add_index(self.OTHER, "irisnb_deadbeef_rs:doc:")
        server.add_index(nb.rs_index(), nb.rs_doc_key())
        server.add_index("memory:store:ltm", "memory:store:ltm:")
        left = nb.rs_leftovers(probe(nb))
        assert {r["run"]: r["keys"] for r in left["runs"]} == {"deadbeef": 1, "cafef00d": 1}
        by_run = {r["run"]: r["expires"] for r in left["runs"]}
        assert by_run == {"deadbeef": "in ~3 min", "cafef00d": "never (no TTL)"}
        assert left["indexes"] + left["live"] == [self.OTHER]

    def leftovers(self, nb, server, ttl):
        """rs_leftovers for another run's index, whose one key has this TTL (None: no keys)."""
        server.add_index(self.OTHER, "irisnb_deadbeef_rs:doc:")
        if ttl is not None:
            server.hashes["irisnb_deadbeef_rs:doc:x"] = {}
            if ttl != -1:
                server.ttls["irisnb_deadbeef_rs:doc:x"] = ttl
        return nb.rs_leftovers(probe(nb))

    def test_an_index_whose_keys_are_still_counting_down_is_not_offered_for_dropping(
        self, nb, server
    ):
        left = self.leftovers(nb, server, ttl=1500)
        assert left["indexes"] == [] and left["live"] == [self.OTHER]

    @pytest.mark.parametrize("ttl", [None, -1])
    def test_an_index_is_offered_once_its_keys_are_gone_or_will_never_expire(self, nb, server, ttl):
        left = self.leftovers(nb, server, ttl)
        assert left["indexes"] == [self.OTHER] and left["live"] == []

    def test_a_key_that_vanished_between_the_scan_and_the_ttl_is_gone(
        self, nb, server, monkeypatch
    ):
        monkeypatch.setattr(server, "ttl", lambda key: -2)
        left = self.leftovers(nb, server, ttl=1500)
        assert [r["expires"] for r in left["runs"]] == ["gone"]
        assert left["indexes"] == [self.OTHER]

    @pytest.mark.parametrize(
        "ttl, shown",
        [
            (3600, "in ~60 min"), (3599, "in ~60 min"), (61, "in ~2 min"),
            (60, "in ~1 min"), (1, "in ~1 min"), (0, "in ~1 min"),
        ],
    )  # fmt: skip
    def test_the_minutes_are_rounded_up_not_off_by_one(self, nb, server, ttl, shown):
        left = self.leftovers(nb, server, ttl)
        assert left["runs"][0]["expires"] == shown

    def test_the_drop_needs_the_typed_confirmation_and_never_reaches_the_server_without_it(
        self, nb, server, client
    ):
        server.add_index(self.OTHER, "irisnb_deadbeef_rs:doc:")
        server.sent.clear()
        with pytest.raises(ValueError):
            nb.rs_drop_orphans(client, [self.OTHER], "yes please")
        assert server.names() == ["FT.INFO"] and self.OTHER in server.indexes

    def test_a_confirmed_drop_removes_the_index_without_dd_and_leaves_the_documents(
        self, nb, server, client
    ):
        server.add_index(self.OTHER, "irisnb_deadbeef_rs:doc:")
        server.hashes["irisnb_deadbeef_rs:doc:x"] = {}
        assert nb.rs_drop_orphans(client, [self.OTHER], nb.ORPHAN_CONFIRM) == [self.OTHER]
        assert self.OTHER not in server.indexes and "irisnb_deadbeef_rs:doc:x" in server.hashes
        assert not [a for a in server.sent if any(txt(x).upper() == "DD" for x in a)]

    def test_one_confirmation_is_good_for_one_drop_only(self, nb, server, client):
        server.add_index(self.OTHER, "irisnb_deadbeef_rs:doc:")
        nb.rs_drop_orphans(client, [self.OTHER], nb.ORPHAN_CONFIRM)
        with pytest.raises(nb.Denied):
            client.execute_command("FT.DROPINDEX", self.OTHER)  # the grant was spent

    @pytest.mark.parametrize("foreign", ["memory:store:ltm", "prod_idx", "irisnb_"])
    def test_an_index_that_is_not_another_runs_is_never_dropped(self, nb, server, client, foreign):
        server.add_index(foreign, "memory:store:ltm:")
        with pytest.raises(ValueError):
            nb.rs_drop_orphans(client, [foreign], nb.ORPHAN_CONFIRM)
        assert foreign in server.indexes and server.writes() == []

    def test_this_runs_own_index_is_not_an_orphan(self, nb, server, client):
        server.add_index(nb.rs_index(), nb.rs_doc_key())
        with pytest.raises(ValueError):
            nb.rs_drop_orphans(client, [nb.rs_index()], nb.ORPHAN_CONFIRM)

    @pytest.mark.parametrize("wording", MISSING_INDEX)
    def test_an_index_that_vanished_meanwhile_is_skipped(self, nb, server, client, wording):
        server.wording = wording
        assert nb.rs_drop_orphans(client, [self.OTHER], nb.ORPHAN_CONFIRM) == []

    @pytest.mark.parametrize("wording", MISSING_INDEX)
    def test_an_index_dropped_between_the_look_and_the_drop_is_skipped_not_a_stop(
        self, nb, server, client, wording, monkeypatch
    ):
        server.wording = wording
        server.add_index(self.OTHER, "irisnb_deadbeef_rs:doc:")
        server.add_index("irisnb_deadbeef_rs:idx3", "irisnb_deadbeef_rs:doc:")
        original = server.ft_dropindex

        def raced(name, *rest):
            if txt(name) == self.OTHER:
                original(name, *rest)  # somebody else's drop wins
            return original(name, *rest)

        monkeypatch.setitem(server.__dict__, "ft_dropindex", raced)
        done = nb.rs_drop_orphans(client, [self.OTHER, "irisnb_deadbeef_rs:idx3"], nb.ORPHAN_CONFIRM)
        assert done == ["irisnb_deadbeef_rs:idx3"] and server.indexes == {}


# ---- the deep dive ---------------------------------------------------------------------------------------------
class TestDeepDive:
    def test_a_second_index_over_the_same_prefix_is_ledgered(self, nb, loaded, server):
        name = nb.rs_build_second(loaded, 256, "FLAT", "L2")
        assert name == nb.rs_index() + "2"
        second = server.indexes[name]
        assert second["prefixes"] == [nb.rs_doc_key()]
        assert (second["algorithm"], second["metric"]) == ("FLAT", "L2")
        assert name in nb.ledger_live("rs", "index")

    @pytest.mark.parametrize("wording", MISSING_INDEX)
    def test_the_first_build_drops_an_index_that_does_not_exist_yet_in_either_wording(
        self, nb, loaded, server, wording
    ):
        server.wording = wording  # Redis 8.6.2 answered this FT.DROPINDEX "Index not found"
        assert nb.rs_index() + "2" not in server.indexes
        name = nb.rs_build_second(loaded, 256, "SVS-VAMANA", "COSINE")
        assert name in server.indexes and name in nb.ledger_live("rs", "index")

    def test_building_again_replaces_it_and_cleanup_drops_both_indexes(self, nb, loaded, server):
        nb.rs_build_second(loaded, 256, "FLAT", "L2")
        nb.rs_build_second(loaded, 256, "HNSW", "IP")
        assert server.indexes[nb.rs_index() + "2"]["algorithm"] == "HNSW"
        assert nb.rs_cleanup(loaded)["dropped"] == 2 and server.indexes == {}

    def test_the_comparison_shows_both_indexes_and_the_same_top_3_at_this_scale(
        self, nb, loaded, embed
    ):
        second = nb.rs_build_second(loaded, 256, "FLAT", "COSINE")
        names = {nb.rs_index(): "HNSW · COSINE", second: "FLAT · COSINE"}
        compared = nb.rs_compare_indexes(loaded, embed, "will it fit my feet", names)
        rows = compared["rows"]
        assert [r["index"] for r in rows] == ["HNSW · COSINE", "FLAT · COSINE"]
        assert all(r["documents"] == 36 and r["failed to index"] == 0 for r in rows)
        assert rows[0]["nearest 3"] == rows[1]["nearest 3"]
        assert "same three articles in the same order" in compared["note"]

    def test_the_note_reports_each_indexes_nearest_distance_as_it_came_back(
        self, nb, loaded, embed
    ):
        second = nb.rs_build_second(loaded, 256, "FLAT", "L2")
        names = {nb.rs_index(): "HNSW · COSINE", second: "FLAT · L2"}
        note = nb.rs_compare_indexes(loaded, embed, "will it fit my feet", names)["note"]
        first = nb.rs_knn_search(loaded, embed, "will it fit my feet", 1)["rows"][0]["dist"]
        other = nb.rs_knn_search(loaded, embed, "will it fit my feet", 1, index=second)
        assert (
            f"{first:.3f} by HNSW · COSINE and {other['rows'][0]['dist']:.3f} by FLAT · L2" in note
        )

    def test_indexes_that_disagree_are_reported_as_disagreeing(
        self, nb, loaded, embed, monkeypatch
    ):
        tops = iter([["a", "b", "c"], ["a", "c", "b"]])

        def search(client, embed, query, k, prefilter="*", index=None):
            return {"rows": [{"key": key, "title": key, "dist": 0.5} for key in next(tops)]}

        monkeypatch.setattr(nb, "rs_knn_search", search)
        second = nb.rs_build_second(loaded, 256, "FLAT", "COSINE")
        note = nb.rs_compare_indexes(loaded, embed, "q", {nb.rs_index(): "1", second: "2"})["note"]
        assert "disagree" in note and "same three" not in note

    def test_indexes_with_no_articles_left_say_so_instead_of_comparing(
        self, nb, server, loaded, embed
    ):
        second = nb.rs_build_second(loaded, 256, "FLAT", "COSINE")
        server.hashes.clear()  # the hour is up
        compared = nb.rs_compare_indexes(loaded, embed, "q", {nb.rs_index(): "1", second: "2"})
        assert "expired" in compared["note"] and len(compared["rows"]) == 2


# ---- the embedder ---------------------------------------------------------------------------------------------------
class TestEmbedder:
    def test_the_hashing_fallback_gives_unit_rows_of_the_right_shape(self, nb):
        matrix = nb.rs_hash_embed(["hiking boots", "boots", ""])
        assert matrix.shape == (3, 256) and str(matrix.dtype) == "float32"
        assert matrix[0] @ matrix[0] == pytest.approx(1.0) and not matrix[2].any()

    def test_it_matches_words_not_meaning(self, nb):
        boots, shoes, hiking_boots = nb.rs_hash_embed(["boots", "shoes", "hiking boots"])
        assert boots @ hiking_boots > 0.5 and boots @ shoes < 0.2

    def test_a_blob_is_dim_times_four_little_endian_float32_bytes(self, nb):
        import numpy as np

        vector = nb.rs_hash_embed(["boots"])[0]
        blob = nb.rs_blob(vector)
        assert len(blob) == 256 * 4 and (np.frombuffer(blob, dtype="<f4") == vector).all()

    def test_when_the_model_cannot_be_had_it_falls_back_and_says_why(self, nb, monkeypatch):
        import huggingface_hub

        def refuse(*args, **kwargs):
            raise OSError("no network for tests")

        monkeypatch.setattr(huggingface_hub, "snapshot_download", refuse)
        name, embed, problem = nb.rs_load_embedder()
        assert name == "hashing fallback" and embed is nb.rs_hash_embed
        assert "OSError" in problem and "no network for tests" in problem

    def test_it_tries_the_local_cache_before_the_network_and_asks_for_four_files(
        self, nb, monkeypatch
    ):
        import huggingface_hub

        calls = []

        def spy(repo, **kwargs):
            calls.append(kwargs)
            raise OSError("stop here")

        monkeypatch.setattr(huggingface_hub, "snapshot_download", spy)
        nb.rs_load_embedder()
        assert [c.get("local_files_only", False) for c in calls] == [True, False]
        files = ["config.json", "model.safetensors", "tokenizer.json", "README.md"]
        assert all(c["allow_patterns"] == files for c in calls)


# ---- calibration: what the real embedding model does with the shipped presets -------------------------------------
def ui_default(nb_file, name, keyword="value"):
    """The literal `keyword=` given where a cell builds the UI element called `name`."""
    for node in ast.walk(ast.parse(nb_file.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Assign)
            and [t.id for t in node.targets if isinstance(t, ast.Name)] == [name]
            and isinstance(node.value, ast.Call)
        ):
            given = nbast.keyword(node.value, keyword)
            if given is not None:
                return ast.literal_eval(given)
    raise LookupError(f"{name} has no literal {keyword}=")


@pytest.fixture(scope="module")
def potion(nb):
    """potion-base-8M from the local cache only, since tests never touch the network."""
    try:
        from huggingface_hub import snapshot_download

        files = ["config.json", "model.safetensors", "tokenizer.json", "README.md"]
        snapshot_download("minishlab/potion-base-8M", local_files_only=True, allow_patterns=files)
    except Exception:
        pytest.skip("potion-base-8M is not cached; `verify.py --preflight` warms it")
    name, embed, problem = nb.rs_load_embedder()
    assert (name, problem) == ("potion-base-8M", "")
    return embed


class TestCalibration:
    @pytest.fixture
    def corpus(self, nb, potion):
        articles = nb.rs_articles()
        return articles, potion([f"{a['title']}. {a['body']}" for a in articles])

    @staticmethod
    def nearest(potion, corpus, query):
        """(article id, cosine distance) nearest first."""
        articles, vectors = corpus
        distances = 1 - vectors @ potion([query])[0]
        return [(articles[i]["id"], float(distances[i])) for i in distances.argsort()]

    def test_the_model_is_256_wide_and_unit_length(self, potion):
        vectors = potion(["hiking boots", "a tent"])
        assert vectors.shape == (2, 256)
        assert (vectors**2).sum(axis=1) == pytest.approx([1, 1], rel=1e-5)

    def test_vector_search_ranks_the_target_first_for_the_paraphrase_and_the_typo(
        self, nb, potion, corpus
    ):
        trap, _name, typo = nb.rs_wow_presets()
        for preset in (trap, typo):
            found = self.nearest(potion, corpus, preset["query"])
            assert found[0][0] == preset["target"], (preset["query"], found[:3])
            assert found[1][1] - found[0][1] > 0.05, "the win should not be a coin flip"

    def test_the_lexical_trap_shares_no_word_with_any_article_after_stemming(self, nb):
        # Reciprocal rank fusion cannot lift an article that only the vector search lists above
        # a keyword hit that both lists rank, and the keyword hits of a paraphrase are mostly
        # such articles ("fit" finds the sizing guides the vector search likes too). So the
        # trap is a question keyword search has nothing at all to say about, and hybrid can
        # only agree with the vector order. Checked by hand against a Snowball stemmer, which
        # is what RediSearch uses; this stemmer is cruder, so the words are also kept plain.
        trap = nb.rs_wow_presets()[0]
        wanted = set(stems(trap["query"]))
        sharing = [
            a["id"] for a in nb.rs_articles() if wanted & set(stems(a["title"] + " " + a["body"]))
        ]
        assert sharing == []

    def test_the_exact_name_is_in_one_article_only_and_the_typo_in_none(self, nb):
        _trap, name, typo = nb.rs_wow_presets()
        articles = nb.rs_articles()

        def words(article):
            return re.findall(r"[^\W_]+", (article["title"] + " " + article["body"]).lower())

        assert [a["id"] for a in articles if name["query"].lower() in words(a)] == [name["target"]]
        assert not [a for a in articles if typo["query"].lower() in words(a)]
        target = next(a for a in articles if a["id"] == typo["target"])
        assert "waterproofing" in target["title"].lower()  # the word the typo was meant to be

    def test_the_default_radius_hits_the_paraphrase_and_misses_the_unrelated_question(
        self, nb_file, potion, corpus
    ):
        radius = ui_default(nb_file, "rs_vec_radius")
        near = [d for _, d in self.nearest(potion, corpus, ui_default(nb_file, "rs_vec_query"))]
        far = [d for _, d in self.nearest(potion, corpus, "best pizza near me")]
        assert 0 < sum(d <= radius for d in near) < len(near)
        assert not any(d <= radius for d in far), "the presenter's MISS example must miss"

    def test_the_default_question_finds_the_boot_guide_inside_the_default_radius(
        self, nb_file, potion, corpus
    ):
        found = self.nearest(potion, corpus, ui_default(nb_file, "rs_vec_query"))
        assert found[0][0] == "size-boots" and found[0][1] < ui_default(nb_file, "rs_vec_radius")

    def test_the_default_filter_still_fills_k_and_visibly_changes_the_top_5(
        self, nb, nb_file, potion, corpus
    ):
        categories = ui_default(nb_file, "rs_pf_categories")
        low, high = ui_default(nb_file, "rs_pf_years")
        articles = {a["id"]: a for a in nb.rs_articles()}
        order = [a for a, _ in self.nearest(potion, corpus, ui_default(nb_file, "rs_pf_query"))]
        passing = [
            a
            for a in order
            if articles[a]["category"] in categories and low <= articles[a]["year"] <= high
        ]
        assert len(passing) >= 5, "the filtered query must still fill k=5"
        assert set(order[:5]) - set(passing[:5]), "the filter should change the top 5"

    def test_the_wow_end_to_end_with_real_embeddings(self, nb, potion, server, client):
        nb.rs_seed_corpus(client, potion, 256)
        trap, name, typo = (nb.rs_wow_search(client, potion, p, True) for p in nb.rs_wow_presets())
        boots, resole, proof = (
            nb.rs_doc_key(i) for i in ("size-boots", "warranty-resole", "care-waterproof")
        )
        assert trap["BM25"] == [] and trap["vector"][0] == boots
        assert name["BM25"] == [resole] and resole in name["vector"]
        assert typo["BM25"] == [] and typo["vector"][0] == proof
        # The point of the cell: whichever engine is blind, hybrid puts the answer first.
        assert [w["hybrid"][0] for w in (trap, name, typo)] == [boots, resole, proof]

    def test_the_default_own_question_shows_why_fusion_needs_agreement(
        self, nb, nb_file, potion, server, client
    ):
        # The caveat in the own-question lesson, true of its default text: keyword search
        # matches the vague word "fit" in sizing guides that the vector search also likes, so
        # they outrank the boot guide, which only the vector search lists.
        nb.rs_seed_corpus(client, potion, 256)
        own = nb.rs_wow_preset("Type your own", ui_default(nb_file, "rs_wow_own"))
        wow = nb.rs_wow_search(client, potion, own, True)
        boots = nb.rs_doc_key("size-boots")
        assert wow["vector"][0] == boots and wow["hybrid"].index(boots) >= 3


# ---- the notebook's shape ------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def column(nb_file):
    """Column 0's items in file order: (kind, node)."""
    source = nb_file.read_text(encoding="utf-8")
    return [(i.kind, i.node) for i in nbast.column_items(source, 0)]


@pytest.fixture(scope="module")
def cells(column):
    return [node for kind, node in column if kind == "cell"]


class TestShape:
    def test_the_column_runs_in_the_planned_order(self, cells):
        # one marker global per stage: config, connect, orphans, embedder, seed, then each
        # core cell as controls / fetch / render, the wow, cleanup, and the deep dive
        markers = [
            "rs_url", "rs_client", "rs_orphan_drop", "rs_dim", "rs_seed", "rs_ready",
            "rs_ft_query", "rs_ft_found", "rs_vec_query", "rs_knn", "rs_pf_query", "rs_pf_found",
            "rs_wow_case", "rs_wow", "rs_cleanup_btn", "rs_deep", "rs_algo",
        ]  # fmt: skip
        at = [next(i for i, c in enumerate(cells) if m in nbast.defs(c)) for m in markers]
        assert at == sorted(at) and len(set(at)) == len(markers)
        for controls, fetch in (
            ("rs_ft_query", "rs_ft_found"), ("rs_vec_query", "rs_knn"),
            ("rs_pf_query", "rs_pf_found"), ("rs_wow_case", "rs_wow"),
        ):  # fmt: skip
            assert at[markers.index(fetch)] == at[markers.index(controls)] + 1

    def test_the_helpers_come_last_all_hidden_and_all_prefixed(self, column, nb_file):
        kinds = [kind for kind, _ in column]
        assert set(kinds[kinds.index("function") :]) == {"function"}
        functions = nbast.column_items(nb_file.read_text(encoding="utf-8"), 0)
        assert all(i.hide_code is True for i in functions if i.kind == "function")
        assert all(node.name.startswith("rs_") for kind, node in column if kind == "function")

    def test_every_global_a_cell_returns_has_the_column_prefix(self, cells):
        for cell in cells:  # the intro cell that opens the column is ib_
            assert all(n.startswith(("rs_", "ib_")) for n in nbast.defs(cell)), nbast.defs(cell)

    def test_the_first_cells_to_use_the_connection_stop_when_there_is_none(self, cells):
        first = [ast.unparse(c.body[0]) for c in cells if c.body]
        assert first.count("mo.stop(rs_client is None, None)") == 2  # the orphans panel, embedder
        assert first.count("mo.stop(not rs_ready, None)") == 5  # four controls cells, deep switch

    def test_only_the_demo_and_cleanup_buttons_can_autorun(self, cells):
        pressed = {
            (c.args[0].id, ast.unparse(nbast.keyword(c, "stage") or ast.Constant("demo")))
            for cell in cells
            for c in nbast.calls(cell, "pressed")
        }
        assert pressed == {
            ("rs_connect", "'demo'"), ("rs_seed", "'demo'"), ("rs_build", "'demo'"),
            ("rs_cleanup_btn", "'cleanup'"),
        }  # fmt: skip

    def test_the_orphan_and_reset_buttons_are_read_with_a_plain_value(self, cells):
        source = "\n".join(ast.unparse(c) for c in cells)
        for button in ("rs_orphan_drop", "rs_reset_btn"):
            assert f"{button}.value" in source and f"pressed({button}" not in source

    def test_every_run_button_in_the_column_is_accounted_for(self, cells):
        buttons = {
            t.id
            for cell in cells
            for stmt in ast.walk(cell)
            if isinstance(stmt, ast.Assign)
            and isinstance(stmt.value, ast.Call)
            and nbast.call_name(stmt.value) == "mo.ui.run_button"
            for t in stmt.targets
        }
        assert buttons == {
            "rs_connect", "rs_orphan_drop", "rs_seed", "rs_cleanup_btn", "rs_reset_btn", "rs_build",
        }  # fmt: skip

    def test_the_four_presenter_notes_are_plain_say_and_expect_calls(self, cells):
        calls = [c for cell in cells for c in nbast.calls(cell, "notes")]
        assert len(calls) == 4  # three core cells and the wow
        assert all(len(c.args) == 2 and not c.keywords for c in calls)

    def test_a_cell_that_shows_notes_or_sorts_never_queries_redis(self, cells):
        for cell in cells:
            reads = set(nbast.refs(cell))
            notes = bool(nbast.calls(cell, "notes"))
            if notes or "rs_wow_sort" in reads:
                assert "rs_client" not in reads, "a display cell must not be a fetch cell"
            if "rs_client" in reads:
                assert not notes and "rs_wow_sort" not in reads

    def test_the_cleanup_cell_reads_only_config_and_its_own_buttons(self, cells):
        cleanup = [c for c in cells if "stage='cleanup'" in ast.unparse(c)]
        assert len(cleanup) == 1
        assert set(nbast.refs(cleanup[0])) == {
            "rs_cleanup_btn", "rs_reset_btn", "rs_set_gen", "rs_url",
        }  # fmt: skip

    def test_every_text_box_starts_with_a_question_so_autorun_has_something_to_ask(self, nb_file):
        for name in ("rs_ft_query", "rs_vec_query", "rs_pf_query", "rs_wow_own"):
            assert ui_default(nb_file, name).strip(), name

    @staticmethod
    def ui_elements(cells, *kinds):
        """{global name: the mo.ui call that builds it} for the given kinds, across the cells."""
        return {
            stmt.targets[0].id: stmt.value
            for cell in cells
            for stmt in ast.walk(cell)
            if isinstance(stmt, ast.Assign)
            and isinstance(stmt.value, ast.Call)
            and nbast.call_name(stmt.value) in kinds
        }

    def test_every_slider_waits_for_the_drag_to_end_and_no_text_box_fires_per_keystroke(
        self, cells
    ):
        # A slider sends a value on every tick of a drag, and each value is a Redis command
        # on a free tier that allows 100 a second. A text box already waits for Enter or blur.
        def debounced(call, default):
            given = nbast.keyword(call, "debounce")
            return ast.literal_eval(given) if given is not None else default

        sliders = self.ui_elements(cells, "mo.ui.slider", "mo.ui.range_slider")
        assert set(sliders) == {"rs_vec_k", "rs_vec_radius", "rs_pf_years"}
        for name, call in sliders.items():
            assert debounced(call, default=False), name
        for name, call in self.ui_elements(cells, "mo.ui.text").items():
            assert debounced(call, default=True), name

    def test_the_wow_offers_a_question_of_the_users_own(self, cells):
        menu = self.ui_elements(cells, "mo.ui.dropdown")["rs_wow_case"]
        assert "Type your own" in ast.unparse(menu.args[0])
        assert "rs_wow_own" in self.ui_elements(cells, "mo.ui.text")

    def test_the_seed_button_says_what_it_writes_before_it_is_pressed(self, cells):
        button = self.ui_elements(cells, "mo.ui.run_button")["rs_seed"]
        label = ast.unparse(nbast.keyword(button, "label"))
        assert "rs_articles()" in label and "articles" in label and "1 index" in label

    def test_the_config_cell_links_the_console_and_warns_against_shared_databases(self, cells):
        text = "\n".join(ast.unparse(c) for c in nbast.config_panel(cells, "rs_url"))
        for needed in ("cloud.redis.io", "redis.io/try-free", "Never on a shared service"):
            assert needed in text, needed

    def test_connect_calls_the_reset_token_so_reset_reaches_it(self, cells):
        # A marimo state getter registers a dependency only when it is called.
        (connect,) = [c for c in cells if "rs_client" in nbast.defs(c)]
        assert nbast.calls(connect, "rs_gen")

    def test_connect_shows_the_recorded_live_panel_when_there_is_no_database(self, cells):
        (connect,) = [c for c in cells if "rs_client" in nbast.defs(c)]
        assert [ast.unparse(c.args[0]) for c in nbast.calls(connect, "recorded_live")] == ["'rs'"]

    def test_the_vector_length_is_asked_of_the_embedder_and_never_typed(self, cells):
        (embedder,) = [c for c in cells if "rs_dim" in nbast.defs(c)]
        (dim,) = [
            n.value
            for n in ast.walk(embedder)
            if isinstance(n, ast.Assign)
            and [t.id for t in n.targets if isinstance(t, ast.Name)] == ["rs_dim"]
        ]
        assert nbast.calls(dim, "rs_embed")

    def test_every_search_shows_a_message_and_not_a_traceback_when_the_index_is_gone(self, cells):
        fetches = [
            c
            for c in cells
            if {"rs_ft_found", "rs_knn", "rs_pf_found", "rs_wow"} & set(nbast.defs(c))
        ]
        assert len(fetches) == 4
        for cell in fetches:
            caught = [
                ast.unparse(h.type)
                for t in ast.walk(cell)
                if isinstance(t, ast.Try)
                for h in t.handlers
            ]
            assert caught == ["redis.RedisError"] and nbast.calls(cell, "rs_search_failed")

    def test_the_deep_dive_switch_follows_autorun(self, cells):
        (switch,) = [c for c in cells if "rs_deep" in nbast.defs(c)]
        assert "mo.ui.switch(value=AUTORUN == 'demo'" in ast.unparse(switch)


# ---- the whole column, cell by cell, against the fake -------------------------------------------------------------------
URL = "redis://default:fake-pw-9f3a@db.example:6379"
RUN = "0123abcd"


def texts(outputs):
    return " ".join(getattr(o, "text", str(o)) for o in outputs)


class Btn:
    """Stands in for a mo.ui.run_button in app.run(defs=...)."""

    def __init__(self, value):
        self.value = value


@pytest.fixture
def no_model(monkeypatch):
    """Make the model unavailable, so the column runs offline on the hashing fallback."""
    import huggingface_hub

    def refuse(*args, **kwargs):
        raise OSError("the model is not available in tests")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", refuse)


class TestWholeColumn:
    def run(self, load_fresh, stage, defs=None, **env):
        fresh = load_fresh(
            IRIS_BASICS_RUN=RUN, IRIS_BASICS_AUTORUN=stage, IRIS_BASICS_HEADLESS="1", **env
        )
        fresh.WRITE_RATE = 1_000_000  # pacing has its own tests; 72 writes would take 4 s
        outputs, found = fresh.app.run(defs=defs)
        return fresh, texts(outputs), found

    def test_with_no_settings_only_the_config_and_the_always_available_cleanup_render(
        self, load_fresh, server
    ):
        outputs, defs = load_fresh().app.run()
        text = texts(outputs)
        assert "Redis Search is not configured" in text and "REDIS_URL" in text
        assert defs["rs_client"] is None and not defs["rs_facts"]
        assert defs["rs_has_hybrid"] is False and defs["rs_has_svs"] is False
        for later in ("Load the help centre", "1 · Full-text", "one query, three engines"):
            assert later not in text, later
        assert "Leftovers" not in text and "Deep dive" not in text and "Clean up" in text
        assert server.sent == []

    def test_pressing_cleanup_without_settings_only_says_so(self, load_fresh, server):
        _, text, _ = self.run(load_fresh, "cleanup")
        assert text.count("Redis Search is not configured") == 2 and server.sent == []

    def test_with_settings_but_no_connect_press_nothing_reaches_redis(self, load_fresh, server):
        outputs, defs = load_fresh(REDIS_URL=URL).app.run()
        assert "Press" in texts(outputs) and "Test connection" in texts(outputs)
        assert defs["rs_client"] is None and server.sent == []

    def test_autorun_demo_drives_the_column_from_connect_to_the_deep_dive(
        self, load_fresh, server, no_model
    ):
        fresh, text, defs = self.run(load_fresh, "demo", REDIS_URL=URL)
        assert defs["rs_ready"] is True and defs["rs_client"] is not None
        assert defs["rs_has_hybrid"] is True and defs["rs_has_svs"] is True
        assert "Fell back to word hashing" in text and "articles are indexed" in text
        assert len(server.hashes) == 36
        assert set(server.indexes) == {fresh.rs_index(), fresh.rs_index() + "2"}
        assert defs["rs_ft_found"]["total"] > 0
        assert len(defs["rs_knn"]["rows"]) == defs["rs_vec_k"].value
        assert len(defs["rs_pf_found"]["rows"]) > 0 and defs["rs_wow"]["source"] == "FT.HYBRID"
        assert "Finding your boot size" in text and "★" in text
        assert "The three commands, as redis-cli" in text
        assert "same three articles in the same order" in text  # the deep dive, from its rows
        assert all(t.startswith(fresh.PREFIX) for _, t in server.writes())
        assert sorted(fresh.ledger_live("rs", "key")) == sorted(server.hashes)

    def test_autorun_cleanup_removes_it_all_from_the_ledger_without_connecting(
        self, load_fresh, server, no_model
    ):
        self.run(load_fresh, "demo", REDIS_URL=URL)
        server.sent.clear()
        fresh, text, defs = self.run(load_fresh, "cleanup", REDIS_URL=URL)
        assert server.hashes == {} and server.indexes == {} and "Dropped" in text
        assert defs["rs_client"] is None, "the demo cells did not run"
        # its own connection (PING), the two deletes, and none of Connect's reads
        assert set(server.names()) == {"PING", "FT.DROPINDEX", "UNLINK"}
        assert fresh.ledger_live("rs", "key", pending=True) == []

    def test_cleanup_still_works_when_connect_would_fail(self, load_fresh, server, no_model):
        self.run(load_fresh, "demo", REDIS_URL=URL)
        server.search = False  # Connect would now fail: no Search
        self.run(load_fresh, "cleanup", REDIS_URL=URL)
        assert server.hashes == {}

    def test_the_cleanup_and_reset_buttons_work_when_clicked_and_only_reset_bumps_the_token(
        self, load_fresh, server, no_model
    ):
        def clicked(cleanup, reset):
            bumps = []
            config = {
                "rs_url": SimpleNamespace(value=None), "rs_connect": Btn(False),
                "rs_gen": lambda: 0, "rs_set_gen": lambda update: bumps.append(update(0)),
                "rs_cleanup_btn": Btn(cleanup), "rs_reset_btn": Btn(reset),
            }  # fmt: skip
            return bumps, self.run(load_fresh, "", config, REDIS_URL=URL)

        self.run(load_fresh, "demo", REDIS_URL=URL)
        bumps, (_, text, _) = clicked(cleanup=True, reset=False)
        assert server.hashes == {} and bumps == [] and "Dropped" in text
        self.run(load_fresh, "demo", REDIS_URL=URL)
        bumps, _ = clicked(cleanup=False, reset=True)
        assert server.hashes == {} and bumps == [1]

    def test_a_database_without_search_stops_at_connect_and_says_how_to_fix_it(
        self, load_fresh, server, no_model
    ):
        server.search = False
        _, text, defs = self.run(load_fresh, "demo", REDIS_URL=URL)
        assert defs["rs_client"] is None and "no Search" in text and "redis.io/try-free" in text
        assert server.writes() == [] and "articles are indexed" not in text

    def test_a_refused_connection_is_a_red_message_not_a_traceback(
        self, load_fresh, monkeypatch, no_model
    ):
        def refuse(self, *args, **kwargs):
            raise redis.ConnectionError("Error 111 connecting to db.example:6379. Refused.")

        monkeypatch.setattr(redis.Redis, "execute_command", refuse)
        _, text, defs = self.run(load_fresh, "demo", REDIS_URL=URL)
        assert defs["rs_client"] is None and "Refused" in text and "fake-pw-9f3a" not in text

    def test_a_common_word_says_the_table_holds_only_the_top_ten_of_the_matches(
        self, load_fresh, server, no_model
    ):
        controls = {
            "rs_ft_query": SimpleNamespace(value="order"),
            "rs_ft_prefix": SimpleNamespace(value=False),
            "rs_ft_fuzzy": SimpleNamespace(value=False),
        }
        _, text, defs = self.run(load_fresh, "demo", controls, REDIS_URL=URL)
        total = defs["rs_ft_found"]["total"]
        assert (
            total > 10 and f"<strong>{total}</strong> articles match; the top 10 are shown" in text
        )

    @pytest.mark.parametrize("wording", MISSING_INDEX)
    def test_a_dropped_index_is_a_message_in_every_search_not_a_traceback(
        self, load_fresh, server, no_model, monkeypatch, wording
    ):
        def gone(*args):
            raise missing_index(args[0], wording)

        monkeypatch.setattr(server, "ft_search", gone)  # what Clean up leaves the cells below
        _, text, defs = self.run(load_fresh, "demo", REDIS_URL=URL)
        assert text.count("The index is gone") == 4
        assert "rs_ft_found" not in defs and "rs_wow" not in defs

    def test_the_wow_takes_a_question_of_the_users_own_and_stars_nothing(
        self, load_fresh, server, no_model
    ):
        controls = {
            "rs_wow_case": SimpleNamespace(value="Type your own"),
            "rs_wow_own": SimpleNamespace(value="Trailhead boots"),
            "rs_wow_sort": SimpleNamespace(value="hybrid"),
        }
        _, text, defs = self.run(load_fresh, "demo", controls, REDIS_URL=URL)
        assert defs["rs_wow"]["preset"]["query"] == "Trailhead boots"
        assert "<strong>Trailhead boots</strong>" in text and "really after" not in text

    def test_a_question_with_no_word_keyword_search_can_use_asks_for_more(
        self, load_fresh, server, no_model
    ):
        controls = {
            "rs_wow_case": SimpleNamespace(value="Type your own"),
            "rs_wow_own": SimpleNamespace(value="the of and"),
            "rs_wow_sort": SimpleNamespace(value="hybrid"),
        }
        _, text, defs = self.run(load_fresh, "demo", controls, REDIS_URL=URL)
        assert "rs_wow" not in defs and "Type a word or two" in text

    def test_a_url_with_a_stray_space_is_a_message_that_does_not_echo_it(
        self, load_fresh, server, no_model
    ):
        _, text, defs = self.run(load_fresh, "demo", REDIS_URL="redis://default:pa ss-9f3a@db:6379")
        assert defs["rs_client"] is None and "whitespace" in text and "pa ss-9f3a" not in text
        assert server.sent == []

    def test_a_ledger_line_outside_the_prefix_is_a_message_and_deletes_nothing(
        self, load_fresh, server, no_model
    ):
        server.hashes["prod:customer:1"] = {}
        fresh = load_fresh(IRIS_BASICS_RUN=RUN)
        fresh.ledger_add("rs", "key", "prod:customer:1")
        _, text, _ = self.run(load_fresh, "cleanup", REDIS_URL=URL)
        assert "is outside" in text and "prod:customer:1" in server.hashes

    def test_below_8_4_the_hybrid_column_is_client_side_and_labelled(
        self, load_fresh, server, no_model
    ):
        server.version, server.hybrid = "8.2.1", False
        _, text, defs = self.run(load_fresh, "demo", REDIS_URL=URL)
        assert defs["rs_has_hybrid"] is False and defs["rs_has_svs"] is True
        assert defs["rs_wow"]["source"] == "client-side RRF" and "client-side RRF" in text
        assert "FT.HYBRID" not in server.names()

    def test_on_7_4_there_is_no_svs_option_in_the_deep_dive(self, load_fresh, server, no_model):
        server.version = "7.4.2"
        _, _, defs = self.run(load_fresh, "demo", REDIS_URL=URL)
        assert defs["rs_has_svs"] is False and "SVS-VAMANA" not in defs["rs_algo"].options

    def test_the_password_appears_in_no_output_and_no_variables_panel_preview(
        self, load_fresh, server, no_model
    ):
        from marimo._messaging.variables import create_variable_value

        _, text, defs = self.run(load_fresh, "demo", REDIS_URL=URL)
        assert "fake-pw-9f3a" not in text
        for name, value in defs.items():
            assert "fake-pw-9f3a" not in (create_variable_value(name, value).value or ""), name

    def test_leftovers_from_another_run_are_listed_and_the_demo_leaves_them_alone(
        self, load_fresh, server, no_model
    ):
        server.hashes["irisnb_deadbeef_rs:doc:x"] = {}
        server.add_index("irisnb_deadbeef_rs:idx", "irisnb_deadbeef_rs:doc:")
        server.ttls["irisnb_deadbeef_rs:doc:x"] = 600
        _, text, defs = self.run(load_fresh, "demo", REDIS_URL=URL)
        assert "Leftovers from other runs (1 keys, 1 indexes)" in text
        assert "irisnb_deadbeef_rs:idx" in text and "irisnb_deadbeef_rs:idx" in server.indexes
        assert "Not offered yet" in text and "Indexes to drop" not in text  # its keys still live
        assert all("deadbeef" not in t for _, t in server.writes())
        assert defs["rs_orphan_drop"].value is False and defs["rs_reset_btn"].value is False

    def test_once_the_other_runs_keys_are_gone_its_index_is_offered_for_dropping(
        self, load_fresh, server, no_model
    ):
        server.add_index("irisnb_deadbeef_rs:idx", "irisnb_deadbeef_rs:doc:")
        _, text, _ = self.run(load_fresh, "demo", REDIS_URL=URL)
        assert "Indexes to drop" in text and "Not offered yet" not in text

    def test_an_orphan_index_is_dropped_only_after_the_typed_confirmation(
        self, load_fresh, server, no_model
    ):
        server.add_index("irisnb_deadbeef_rs:idx", "irisnb_deadbeef_rs:doc:")

        def clicked(phrase):
            controls = {
                "rs_orphan_confirm": SimpleNamespace(value=phrase),
                "rs_orphan_drop": Btn(True),
            }
            return self.run(load_fresh, "demo", controls, REDIS_URL=URL)[1]

        assert "to confirm" in clicked("nope")
        assert "irisnb_deadbeef_rs:idx" in server.indexes
        assert "Dropped" in clicked("delete orphans")
        assert "irisnb_deadbeef_rs:idx" not in server.indexes


# ---- the no-credentials export ------------------------------------------------------------------------------------------
class TestNoCredentialsExport:
    def test_a_real_export_shows_the_config_message_and_nothing_else_of_the_column(
        self, nb_file, tmp_path
    ):
        work = tmp_path / "exported"
        work.mkdir()
        shutil.copy2(nb_file, work / nb_file.name)
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        done = subprocess.run(
            [sys.executable, "-m", "marimo", "-y", "export", "html", "--no-sandbox",
             nb_file.name, "-o", "out.html", "-f"],
            cwd=work, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
        session = (work / "__marimo__" / "session" / f"{nb_file.name}.json").read_text("utf-8")
        assert "Redis Search is not configured" in session
        for later in ("Load the help centre", "1 · Full-text", "one query, three engines"):
            assert later not in session, later


# ---- the real marimo runtime ----------------------------------------------------------------------------------------
RUNNER = """
import os
import pickle
import sys
from pathlib import Path

sys.path.insert(0, {tests!r})
import redis
from fake_search import FakeSearch, missing_index

server, state = FakeSearch(), Path({state!r})
if state.exists():
    server.hashes, server.ttls, server.indexes = pickle.loads(state.read_bytes())


def handle(self, *args, **kwargs):
    wording = os.environ.get("FAKE_INDEX_DROPPED")  # "" for a server that has its index
    if wording and args[0] == "FT.SEARCH" and server.hashes:
        raise missing_index(args[1], wording)  # Clean up ran; the cells below did not
    reply = server.handle(args)
    state.write_bytes(pickle.dumps((server.hashes, server.ttls, server.indexes)))
    return reply


redis.Redis.execute_command = handle  # marimo spawns its kernel, which re-imports this file

if __name__ == "__main__":
    from marimo._cli.cli import main

    args = ["-y", "export", "html", "--no-sandbox", {notebook!r}, "-o", "out.html", "-f"]
    main(args, standalone_mode=False)
"""


class TestRealRuntime:
    """`marimo export` itself, not app.run(): its kernel cancels the descendants of a stop."""

    def export(self, nb_file, tmp_path, stage, dropped=""):
        """`dropped` is how the server words the missing index ("8.6", "older"), or "" for none."""
        work = tmp_path / "real"
        work.mkdir(exist_ok=True)
        shutil.copy2(nb_file, work / nb_file.name)
        state = tmp_path / "server.pkl"
        (work / "runner.py").write_text(
            RUNNER.format(
                tests=str(Path(__file__).parent), state=str(state), notebook=nb_file.name
            ),
            encoding="utf-8",
        )
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        env.update(
            PYTHONIOENCODING="utf-8", PYTHONUTF8="1", HF_HUB_OFFLINE="1", REDIS_URL=URL,
            IRIS_BASICS_RUN=RUN, IRIS_BASICS_AUTORUN=stage, IRIS_BASICS_HEADLESS="1",
        )  # fmt: skip
        env.update(FAKE_INDEX_DROPPED=dropped)
        done = subprocess.run(
            [sys.executable, "runner.py"], cwd=work, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
        session = work / "__marimo__" / "session" / f"{nb_file.name}.json"
        hashes, _, indexes = pickle.loads(state.read_bytes())
        return json.loads(session.read_text(encoding="utf-8")), hashes, indexes

    @staticmethod
    def errors(session, nb_file):
        """The errors in column 0's cells: another column with no settings stops its own cells."""
        items = nbast.items(nb_file.read_text(encoding="utf-8"))
        assert len(session["cells"]) == len(items) + 1  # the setup block comes first
        return [
            o
            for item, cell in zip(items, session["cells"][1:], strict=True)
            if item.column == 0
            for o in cell.get("outputs") or []
            if o["type"] == "error"
        ]

    def test_autorun_demo_then_cleanup_run_in_the_real_kernel(self, nb_file, tmp_path):
        session, hashes, indexes = self.export(nb_file, tmp_path, "demo")
        assert self.errors(session, nb_file) == []
        text = json.dumps(session)
        assert "articles are indexed" in text and "Finding your boot size" in text
        assert len(hashes) == 36 and len(indexes) == 2

        session, hashes, indexes = self.export(nb_file, tmp_path, "cleanup")
        assert hashes == {} and indexes == {} and "Dropped" in json.dumps(session)
        # Connect did not run, so its descendants were cancelled; cleanup ran regardless.
        assert {e["ename"] for e in self.errors(session, nb_file)} == {"ancestor-stopped"}

    @pytest.mark.parametrize("wording", MISSING_INDEX)
    def test_a_dropped_index_is_a_message_in_the_real_kernel_where_a_stop_cancels_the_rest(
        self, nb_file, tmp_path, wording
    ):
        session, _, _ = self.export(nb_file, tmp_path, "demo", dropped=wording)
        assert json.dumps(session).count("The index is gone") == 4
        # Each search's display cell is cancelled by its stop, and nothing else went wrong.
        assert {e["ename"] for e in self.errors(session, nb_file)} == {"ancestor-stopped"}
