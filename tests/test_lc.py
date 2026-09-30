"""Tests for column 1, LangCache: the requests it sends, the filtering of what comes back, the
lab's scoring, the chat, the expiry check and cleanup, then the whole column cell by cell and in
the real marimo runtime. All of it against fake_lc.FakeLangCache: no test reaches a service.

Numbers the fake returns are chosen by fake_lc.score_plan, so a test can say exactly what a
threshold should do. What a real embedding model scores was established by the live run of
2026-09-30, which the README reports; the fake's numbers are chosen, not measured.
"""

import ast
import html
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import notebook_ast as nbast
import pytest
from fake_lc import FakeLangCache, score_plan

COLUMN_PREFIX = "lc_"  # tests/conftest.py: this module tests column 1

RUN = "0123abcd"
HOST, CACHE, KEY = "https://lc.example.com", "cache-abc123", "fake-lc-key-9f3a"
ENV = {"LANGCACHE_HOST": HOST, "LANGCACHE_CACHE_ID": CACHE, "LANGCACHE_API_KEY": KEY}
TAUGHT = "At Cairn Outfitters: "


class Clock:
    """The fake cache's seconds. sleep() moves it, so an expiry needs no waiting."""

    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def fake(nb):
    server = FakeLangCache(CACHE, KEY, clock=Clock())
    server.scores = score_plan(nb.lc_seeds(), nb.lc_probes(), nb.lc_variants(), nb.lc_script())
    return server


@pytest.fixture
def server(fake, mock_http):
    """The fake behind the session module's HTTP client, for tests that call helpers directly."""
    mock_http(handler=fake.handle)
    return fake


@pytest.fixture
def service(fake, monkeypatch):
    """The fake behind every httpx client, for the fresh copies of the module that app.run() uses.

    time.sleep moves the fake's clock instead of waiting, so the expiry check takes no time.
    """
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: fake.handle(request)
    )
    monkeypatch.setattr(time, "sleep", fake.clock.advance)
    return fake


@pytest.fixture
def conn(nb, server):
    connection, _ = nb.lc_connection(HOST, CACHE, nb.Secret(KEY), "")
    return connection


@pytest.fixture
def seeded(nb, conn):
    """A connection to a cache that holds the five gear-care answers."""
    nb.lc_write_seeds(conn)
    return conn


def flat(markup):
    """The words of rendered markup. A callout keeps its HTML escaped inside an attribute."""
    text = html.unescape(markup)
    for escaped, plain in ((r"\u003c", "<"), (r"\u003e", ">"), (r"\"", '"'), (r"\n", "\n")):
        text = text.replace(escaped, plain)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def bodies(server, method, path):
    return [body for m, p, body in server.calls if (m, p) == (method, path)]


# ---- the connection ---------------------------------------------------------------------------
class TestConnection:
    @pytest.mark.parametrize(
        "host",
        [
            "lc.example.com",
            "https://lc.example.com",
            "https://lc.example.com/",
            "  https://LC.example.com/v1/caches/other/entries  ",
        ],
    )
    def test_the_host_is_reduced_to_its_https_origin(self, nb, host):
        connection, missing = nb.lc_connection(host, CACHE, nb.Secret(KEY), "")
        assert missing == [] and connection["base"] == f"{HOST}/v1/caches/{CACHE}"

    @pytest.mark.parametrize(
        "host", ["http://lc.example.com", "ftp://lc.example.com", "https://lc exam ple.com"]
    )
    def test_a_host_that_is_not_https_or_not_a_url_is_refused_without_echoing_it(self, nb, host):
        with pytest.raises(ValueError, match="host") as err:
            nb.lc_connection(host, CACHE, nb.Secret(KEY), "")
        assert "exam" not in str(err.value) and "ftp" not in str(err.value)

    def test_every_missing_setting_is_named_and_no_connection_is_built(self, nb):
        assert nb.lc_connection("", "", None, "") == (
            None, ["LANGCACHE_HOST", "LANGCACHE_CACHE_ID", "LANGCACHE_API_KEY"],
        )  # fmt: skip
        assert nb.lc_connection(HOST, "", nb.Secret(KEY), "")[1] == ["LANGCACHE_CACHE_ID"]

    def test_a_blank_field_falls_back_on_the_environment_and_a_typed_one_wins(
        self, nb, monkeypatch
    ):
        for name, value in ENV.items():
            monkeypatch.setenv(name, value)
        from_env, _ = nb.lc_connection("", "", None, "")
        assert from_env["base"] == f"{HOST}/v1/caches/{CACHE}"
        assert from_env["key"].reveal() == KEY
        typed, _ = nb.lc_connection("other.example.com", "cache-2", nb.Secret("typed-key-0000"), "")
        assert typed["base"] == "https://other.example.com/v1/caches/cache-2"
        assert typed["key"].reveal() == "typed-key-0000"

    @pytest.mark.parametrize("cache_id", ["a/b", "..", "x?y=1", "a b", "é"])
    def test_a_cache_id_that_could_change_the_path_is_refused(self, nb, cache_id):
        with pytest.raises(ValueError, match="Cache ID") as err:
            nb.lc_connection(HOST, cache_id, nb.Secret(KEY), "")
        assert cache_id not in str(err.value)

    def test_the_scope_attribute_is_checked_and_handed_to_the_delete_guard(self, nb):
        connection, _ = nb.lc_connection(HOST, CACHE, nb.Secret(KEY), "scope")
        assert connection["scope"] == "scope" and nb.LC_SCOPE["attr"] == "scope"
        with pytest.raises(ValueError, match="attribute"):
            nb.lc_connection(HOST, CACHE, nb.Secret(KEY), "not valid!")

    def test_the_scope_attribute_can_come_from_the_environment(self, nb, monkeypatch):
        monkeypatch.setenv("LANGCACHE_SCOPE_ATTR", "team")
        assert nb.lc_connection(HOST, CACHE, nb.Secret(KEY), "")[0]["scope"] == "team"

    def test_a_key_with_whitespace_is_refused_without_echoing_it(self, nb):
        with pytest.raises(ValueError, match="whitespace") as err:
            nb.lc_connection(HOST, CACHE, nb.Secret("bad key 0123456789"), "")
        assert "bad key" not in str(err.value)

    def test_the_connection_previews_the_key_masked(self, nb):
        from marimo._messaging.variables import create_variable_value

        connection, _ = nb.lc_connection(HOST, CACHE, nb.Secret(KEY), "")
        assert KEY not in (create_variable_value("lc_conn", connection).value or "")


# ---- searching --------------------------------------------------------------------------------
class TestSearch:
    def test_it_posts_five_results_at_a_rounded_threshold_with_the_bearer_key(
        self, nb, conn, server
    ):
        nb.lc_search(conn, "hello there", 0.8500000000000001)
        assert bodies(server, "POST", "/entries/search") == [
            {
                "prompt": "hello there",
                "similarityThreshold": 0.85,
                "searchStrategies": ["semantic"],
                "maxResults": 5,
            }  # fmt: skip
        ]  # the fake answers 401 unless the header is Authorization: Bearer <the key>

    def test_strategies_are_sent_in_the_order_given(self, nb, conn, server):
        nb.lc_search(conn, "hello", 0.5, ("exact", "semantic"))
        assert bodies(server, "POST", "/entries/search")[0]["searchStrategies"] == [
            "exact", "semantic",
        ]  # fmt: skip

    def test_the_scope_attribute_filters_the_search_only_when_there_is_one(
        self, nb, fake, mock_http
    ):
        fake.attributes = ("scope",)
        mock_http(handler=fake.handle)
        scoped, _ = nb.lc_connection(HOST, CACHE, nb.Secret(KEY), "scope")
        nb.lc_search(scoped, "hello", 0.5)
        plain, _ = nb.lc_connection(HOST, CACHE, nb.Secret(KEY), "")
        nb.lc_search(plain, "hello", 0.5)
        first, second = bodies(fake, "POST", "/entries/search")
        assert first["attributes"] == {"scope": nb.RUN_TAG} and "attributes" not in second

    def test_only_this_runs_entries_are_kept_best_first(self, nb, seeded, server):
        found = nb.lc_search(seeded, "How can I get my rain jacket to bead water again?", 0.5)
        assert [e["prompt"] for e in found["mine"]] == [nb.lc_seeds()["shell"]["prompt"]]
        assert found["mine"][0]["similarity"] == 0.91 and found["foreign"] == []
        assert set(found["mine"][0]) == {"id", "prompt", "response", "similarity", "strategy"}

    def test_an_entry_of_another_app_is_a_number_and_never_shown_anywhere(self, nb, seeded, server):
        server.add_foreign("What is your refund policy for late deliveries?", "SECRET-ANSWER-42")
        server.scores[
            ("refund rules for a late parcel", "what is your refund policy for late deliveries?")
        ] = 0.9
        found = nb.lc_search(seeded, "Refund rules for a late parcel", 0.5)
        assert found["mine"] == [] and found["foreign"] == [0.9]
        shown = repr(found) + repr(list(nb.HTTP_TRACE)) + nb.wire_view("t", found["call"]).text
        for hidden in ("SECRET-ANSWER-42", "refund policy", "late deliveries"):
            assert hidden not in shown
        assert "another app" in shown  # the trace says what it left out

    def test_results_come_back_best_first_whatever_order_the_server_used(self, nb, seeded, server):
        boots, shell = nb.lc_seeds()["boots"]["prompt"], nb.lc_seeds()["shell"]["prompt"]
        server.scores[("q", boots.lower())] = 0.7
        server.scores[("q", shell.lower())] = 0.9
        found = nb.lc_search(seeded, "q", 0.5)
        assert [e["similarity"] for e in found["mine"]] == [0.9, 0.7]

    def test_a_server_that_ignores_max_results_still_works(self, nb, fake, mock_http):
        fake.cap = 1
        mock_http(handler=fake.handle)
        connection, _ = nb.lc_connection(HOST, CACHE, nb.Secret(KEY), "")
        nb.lc_write_seeds(connection)
        boots, shell = nb.lc_seeds()["boots"]["prompt"], nb.lc_seeds()["shell"]["prompt"]
        fake.scores[("q", boots.lower())] = fake.scores[("q", shell.lower())] = 0.8
        assert len(nb.lc_search(connection, "q", 0.5)["mine"]) == 1

    @pytest.mark.parametrize(
        ("key", "cache", "status", "kind"),
        [
            ("wrong-key-000000", CACHE, 401, "authentication-failed"),
            (KEY, "no-such-cache", 404, "cache/not-found"),
        ],
    )
    def test_a_rejected_request_keeps_the_status_and_the_type(
        self, nb, server, key, cache, status, kind
    ):
        connection, _ = nb.lc_connection(HOST, cache, nb.Secret(key), "")
        with pytest.raises(nb.ServiceError) as err:
            nb.lc_search(connection, "hello", 0.5)
        assert err.value.status == status and kind in err.value.detail

    def test_a_424_and_a_429_are_errors_and_never_an_empty_result(self, nb, conn, mock_http):
        for status, kind in (
            (424, "/errors/embeddings/unauthorized"),
            (429, "/errors/too-many-requests"),
        ):
            mock_http(
                status=status,
                json={"type": kind, "title": "t", "detail": "the embedding call failed"},
            )
            with pytest.raises(nb.ServiceError, match=kind):
                nb.lc_search(conn, "hello", 0.5)

    def test_the_key_is_in_no_trace_and_the_curl_uses_a_placeholder(self, nb, seeded, server):
        found = nb.lc_search(seeded, "hello", 0.5)
        assert KEY not in repr(list(nb.HTTP_TRACE)) and found["call"].request["headers"]
        curl = nb.as_curl(found["call"])
        assert "$LANGCACHE_API_KEY" in curl and KEY not in curl
        assert KEY not in nb.wire_view("t", found["call"]).text

    def test_rows_show_the_matches_and_only_the_score_of_another_apps(self, nb):
        mine = [
            {"id": "a1", "prompt": "P", "response": "R", "similarity": 0.9, "strategy": "exact"}
        ]
        rows = nb.lc_hit_rows(mine, [0.95, 0.5])
        assert [r["similarity"] for r in rows] == [0.95, 0.9, 0.5]
        assert [r["matched prompt"] for r in rows] == [
            "another app's entry (hidden)", "P", "another app's entry (hidden)",
        ]  # fmt: skip
        assert set(rows[1]) == {"#", "similarity", "matched prompt"}  # id and strategy: wire view


# ---- writing ----------------------------------------------------------------------------------
class TestSeed:
    def test_there_are_five_gear_care_answers_that_fit_the_api_limits(self, nb):
        seeds = nb.lc_seeds()
        assert len(seeds) == 5
        assert len({s["prompt"] for s in seeds.values()}) == 5
        assert all(1 <= len(s["prompt"]) <= 1024 and s["response"] for s in seeds.values())

    def test_every_probe_points_at_a_real_seed_or_at_none_and_the_variants_are_of_one_seed(
        self, nb
    ):
        probes = nb.lc_probes()
        assert len(probes) == 14 and len(nb.lc_variants()) == 6
        assert nb.lc_seeds()["bag"]["prompt"].lower() == nb.lc_variants()[0]["text"].lower()
        assert {p["target"] for p in probes} <= {None, *nb.lc_seeds()}
        kinds = {p["kind"] for p in probes}
        assert {"paraphrase", "swapped item", "near-miss", "negation", "unrelated"} <= kinds
        assert all(
            p["target"] is None
            for p in probes
            if p["kind"] != "paraphrase" and p["kind"] != "same words"
        )

    def test_every_write_has_a_ttl_the_ledger_and_the_tag_when_scoped(self, nb, fake, mock_http):
        fake.attributes = ("scope",)
        mock_http(handler=fake.handle)
        connection, _ = nb.lc_connection(HOST, CACHE, nb.Secret(KEY), "scope")
        ids = nb.lc_write_seeds(connection)
        assert sorted(ids) == sorted(fake.entries) and sorted(
            nb.ledger_live("lc", "entry")
        ) == sorted(ids)
        for body in bodies(fake, "POST", "/entries"):
            assert body["ttlMillis"] == nb.LC_TTL_MS
            assert body["attributes"] == {"scope": nb.RUN_TAG}

    def test_unscoped_writes_carry_no_attributes(self, nb, seeded, server):
        assert all("attributes" not in b for b in bodies(server, "POST", "/entries"))

    def test_the_body_the_consent_text_shows_is_the_body_that_is_sent(self, nb, conn, server):
        first = next(iter(nb.lc_seeds().values()))
        shown = nb.lc_set_body(first["prompt"], first["response"], nb.LC_TTL_MS, conn["scope"])
        nb.lc_write_seeds(conn)
        assert bodies(server, "POST", "/entries")[0] == shown

    def test_a_ttl_beyond_the_limit_is_refused_before_it_is_sent(self, nb, conn, server):
        with pytest.raises(nb.ServiceError, match="ttlMillis"):
            nb.lc_store(conn, "prompt", "answer", nb.LC_TTL_MS + 1)
        assert server.calls == [] and nb.ledger_live("lc", "entry") == []

    def test_a_reply_without_a_usable_id_is_an_error_and_ledgers_nothing(self, nb, conn, mock_http):
        for reply in ({}, {"entryId": ""}, {"entryId": "../x"}, {"entryId": 7}):
            mock_http(status=201, json=reply)
            with pytest.raises(nb.ServiceError, match="entryId"):
                nb.lc_store(conn, "prompt", "answer", 1000)
        assert nb.ledger_live("lc", "entry") == []

    def test_an_id_of_any_usual_shape_is_accepted(self, nb, conn, mock_http):
        mock_http(status=201, json={"entryId": "b8276423-af16-438e-a8e7-45172ad51904"})
        assert nb.lc_store(conn, "prompt", "answer", 1000) == "b8276423-af16-438e-a8e7-45172ad51904"


# ---- test connection and the floor probe -----------------------------------------------------------------------------
class TestProbe:
    def test_it_searches_once_and_only_reads(self, nb, conn, server):
        facts = nb.lc_probe_cache(conn)
        assert [(m, p) for m, p, _ in server.calls] == [("POST", "/entries/search")]
        assert facts["scope"] == "" and facts["others"] == 0 and facts["note"] == ""

    def test_it_counts_entries_of_other_apps_without_naming_them(self, nb, conn, server):
        server.add_foreign("Cairn Outfitters connection check", "SECRET-ANSWER-42")
        facts = nb.lc_probe_cache(conn)
        assert facts["others"] == 1 and "SECRET" not in repr(facts)

    def test_a_cache_with_the_attribute_keeps_the_scope(self, nb, fake, mock_http):
        fake.attributes = ("scope",)
        mock_http(handler=fake.handle)
        scoped, _ = nb.lc_connection(HOST, CACHE, nb.Secret(KEY), "scope")
        assert nb.lc_probe_cache(scoped)["scope"] == "scope" and nb.LC_SCOPE["attr"] == "scope"

    def test_a_cache_without_attributes_falls_back_to_unscoped_and_says_why(self, nb, server):
        scoped, _ = nb.lc_connection(HOST, CACHE, nb.Secret(KEY), "scope")
        facts = nb.lc_probe_cache(scoped)
        assert facts["scope"] == "" and "no attributes are configured" in facts["note"]
        assert nb.LC_SCOPE["attr"] == ""  # so delete-by-attributes stays refused
        first, second = bodies(server, "POST", "/entries/search")
        assert "attributes" in first and "attributes" not in second

    def test_a_400_that_is_not_about_the_scope_is_not_swallowed(self, nb, conn, mock_http):
        mock_http(status=400, json={"type": "/errors/invalid-data", "title": "t", "detail": "bad"})
        with pytest.raises(nb.ServiceError, match="invalid-data"):
            nb.lc_probe_cache(conn)

    def test_a_wrong_key_stops_the_probe_with_a_401(self, nb, server):
        connection, _ = nb.lc_connection(HOST, CACHE, nb.Secret("wrong-key-000000"), "")
        with pytest.raises(nb.ServiceError) as err:
            nb.lc_probe_cache(connection)
        assert err.value.status == 401

    def test_a_wrong_key_or_address_is_advice_and_not_a_status_code(self, nb, server):
        connection, _ = nb.lc_connection(HOST, CACHE, nb.Secret("wrong-key-000000"), "")
        with pytest.raises(nb.ServiceError) as err:
            nb.lc_probe_cache(connection)
        shown = " ".join(nb.lc_failed(err.value).text.split())
        assert "LANGCACHE_API_KEY" in shown and "Generate key" in shown
        assert "lc HTTP" not in shown and "wrong-key-000000" not in shown
        missing = nb.ServiceError("lc", 404, "http", "no such cache")
        assert "check the host and the Cache ID" in " ".join(nb.lc_failed(missing).text.split())
        assert "Too many requests" in nb.lc_failed(nb.ServiceError("lc", 429, "http", "slow")).text

    def test_describe_says_what_was_found_and_warns_about_a_refused_scope(self, nb, conn):
        call = "POST /v1/caches/c/entries/search"
        found = {"scope": "", "note": "", "ms": 12.0, "others": 3, "call": call}
        plain = flat(nb.lc_describe(conn, found).text)
        assert CACHE in plain and "12 ms" in plain and "entries near the test prompt: 3" in plain
        assert "no scope attribute" in plain and "would not take" not in plain
        assert "The test search, as HTTP" in plain and call in plain
        note = {"scope": "", "note": "no attributes are configured", "ms": 1.0, "others": 0}
        note["call"] = call
        assert "would not take the scope attribute" in flat(nb.lc_describe(conn, note).text)

    def test_the_floor_is_half_when_a_loose_question_comes_back_under_the_default(
        self, nb, seeded, server
    ):
        probe = nb.lc_probe_floor(seeded)
        assert probe == {"floor": 0.5, "lowest": 0.62}
        assert "0.620" in nb.lc_floor_message(probe)
        searches = bodies(server, "POST", "/entries/search")
        assert [b["similarityThreshold"] for b in searches[-2:]] == [0.5, 0.5]

    def test_the_least_similar_entry_of_either_question_sets_the_floor(self, nb, seeded, server):
        loose, shell = "how do i look after my camping gear?", nb.lc_seeds()["shell"]["prompt"]
        server.scores = {(loose, shell.lower()): 0.88}  # the loose one finds nothing else
        server.scores[(shell.lower(), nb.lc_seeds()["tent"]["prompt"].lower())] = 0.7
        assert nb.lc_probe_floor(seeded) == {"floor": 0.5, "lowest": 0.7}

    def test_entries_that_all_score_over_the_default_do_not_prove_a_low_threshold_is_refused(
        self, nb, seeded, server
    ):
        loose, shell = "how do i look after my camping gear?", nb.lc_seeds()["shell"]["prompt"]
        server.scores = {(loose, shell.lower()): 0.88}  # honoured, but nothing lower to show it
        probe = nb.lc_probe_floor(seeded)
        assert probe == {"floor": 0.85, "lowest": 0.88}
        message = nb.lc_floor_message(probe)
        assert "found nothing" not in message and "Nothing scored under 0.85" in message
        assert "may hold every search to its default" in message

    def test_a_cache_that_holds_searches_to_its_default_gets_a_floor_of_085(
        self, nb, seeded, server
    ):
        server.floor = 0.85
        probe = nb.lc_probe_floor(seeded)
        assert probe["floor"] == 0.85 and probe["lowest"] == 1.0
        assert "below cache floor" in nb.lc_floor_message(probe)
        searched = [b["prompt"] for b in bodies(server, "POST", "/entries/search")]
        assert searched[-1] == nb.lc_seeds()["shell"]["prompt"]  # the word-for-word check

    def test_entries_that_are_not_there_give_no_floor_at_all(self, nb, conn, server):
        assert nb.lc_probe_floor(conn) == {"floor": 0.85, "lowest": None}

    def test_expired_entries_are_not_found_either(self, nb, seeded, server):
        server.clock.advance(nb.LC_TTL_MS / 1000 + 1)
        assert nb.lc_probe_floor(seeded)["lowest"] is None

    def test_test_connection_reads_only_and_reports_the_scope(self, nb, server, monkeypatch):
        for name, value in ENV.items():
            monkeypatch.setenv(name, value)
        ok, detail = nb.lc_test_connection()
        assert ok and CACHE in detail and "no scope attribute" in detail
        assert [(m, p) for m, p, _ in server.calls] == [("POST", "/entries/search")]

    def test_test_connection_without_settings_is_a_no_and_sends_nothing(self, nb, server):
        ok, detail = nb.lc_test_connection()
        assert not ok and "LANGCACHE_HOST" in detail and server.calls == []

    def test_test_connection_raises_when_the_key_is_wrong(self, nb, server, monkeypatch):
        monkeypatch.setenv("LANGCACHE_HOST", HOST)
        monkeypatch.setenv("LANGCACHE_CACHE_ID", CACHE)
        monkeypatch.setenv("LANGCACHE_API_KEY", "wrong-key-000000")
        with pytest.raises(nb.ServiceError):
            nb.lc_test_connection()


# ---- the threshold lab ------------------------------------------------------------------------------------------------
@pytest.fixture
def swept(nb, seeded, server):
    return nb.lc_sweep(seeded, nb.lc_probes(), 0.5)


def count(nb, swept, threshold):
    verdicts = [nb.lc_verdict(row, threshold) for row in swept]
    return {v: verdicts.count(v) for v in set(verdicts)}


class TestLab:
    def test_each_question_is_searched_once_at_the_floor(self, nb, seeded, server):
        before = len(server.calls)
        nb.lc_sweep(seeded, nb.lc_probes(), 0.5)
        sent = server.calls[before:]
        assert len(sent) == 14 and {b["similarityThreshold"] for _, _, b in sent} == {0.5}
        assert [b["prompt"] for _, _, b in sent] == [p["text"] for p in nb.lc_probes()]

    def test_moving_the_slider_sends_nothing(self, nb, swept, server):
        before = len(server.calls)
        for threshold in (0.5, 0.7, 0.85, 0.95):
            nb.lc_scoreboard(swept, threshold)
            nb.lc_lab_rows(swept, threshold, 0.5)
        assert len(server.calls) == before

    def test_at_the_cache_default_four_paraphrases_hit_and_one_wrong_answer_is_served(
        self, nb, swept
    ):
        assert count(nb, swept, 0.85) == {
            "hit": 4, "false hit": 1, "missed paraphrase": 2, "miss": 7,
        }  # fmt: skip

    def test_lowering_the_threshold_finds_every_paraphrase_and_serves_more_wrong_answers(
        self, nb, swept
    ):
        assert count(nb, swept, 0.80) == {"hit": 6, "false hit": 3, "miss": 5}

    def test_above_the_worst_false_hit_none_are_left_and_recall_has_dropped(self, nb, swept):
        assert "false hit" not in count(nb, swept, 0.88)
        assert count(nb, swept, 0.88)["hit"] == 4

    def test_the_tiles_score_precision_recall_and_hit_rate(self, nb, swept):
        board = nb.lc_scoreboard(swept, 0.85)
        assert board["precision"] == 4 / 5 and board["recall"] == 4 / 6
        assert board["hit_rate"] == 5 / 14
        assert (board["right"], board["hits"], board["should"], board["asked"]) == (4, 5, 6, 14)
        tiles = nb.lc_lab_tiles(board).text
        for shown in (
            "80%",
            "67%",
            "36%",
            "4 of 5 hits",
            "4 of 6 paraphrases",
            "5 of 14 questions",
        ):
            assert shown in tiles

    def test_a_ratio_with_nothing_to_divide_is_a_dash_and_not_a_crash(self, nb, swept):
        board = nb.lc_scoreboard(swept, 1.0001)
        assert board["precision"] is None and board["hit_rate"] == 0
        assert nb.lc_lab_tiles(board).text.count("-") >= 1

    def test_a_hit_on_the_wrong_seed_is_a_false_hit_even_for_a_paraphrase(self, nb, swept):
        row = next(r for r in swept if r["probe"]["kind"] == "paraphrase")
        wrong = dict(row["best"], prompt=nb.lc_seeds()["stove"]["prompt"])
        assert nb.lc_verdict(row | {"best": wrong}, 0.5) == "false hit"

    def test_summary_names_the_worst_false_hit_and_what_clearing_it_costs(self, nb, swept):
        assert nb.lc_lab_summary(swept) == (
            "The highest false hit scored **0.870**. From **0.88** up there are none, and 4 of "
            "the 6 paraphrases still hit."
        )

    def test_summary_when_nothing_was_ever_wrong(self, nb, swept):
        clean = [r for r in swept if r["probe"]["target"] or r["probe"]["kind"] == "unrelated"]
        assert "No question was served a wrong answer" in nb.lc_lab_summary(clean)

    def test_rows_carry_the_question_the_closest_prompt_its_score_and_the_verdict(self, nb, swept):
        rows = nb.lc_lab_rows(swept, 0.85, 0.5)
        assert [r["question"] for r in rows] == [p["text"] for p in nb.lc_probes()]
        first = rows[0]
        assert first["closest entry"] == "bag" and first["kind"] == "same words"
        assert first["similarity"] == "1.000" and first["verdict"] == "hit"
        assert rows[-1]["closest entry"] == "-" and rows[-1]["similarity"] == "under 0.50"

    def test_a_row_with_no_match_says_below_cache_floor_when_the_floor_is_raised(self, nb, swept):
        assert nb.lc_lab_rows(swept, 0.85, 0.85)[-1]["similarity"] == "below cache floor"

    def test_another_apps_entry_fills_an_empty_row_and_is_never_named(self, nb, seeded, server):
        server.add_foreign("What is your refund policy for late deliveries?", "SECRET-ANSWER-42")
        server.scores[
            (
                "what are your store opening hours?",
                "what is your refund policy for late deliveries?",
            )
        ] = 0.9
        swept = nb.lc_sweep(seeded, nb.lc_probes(), 0.5)
        row = next(r for r in swept if r["probe"]["text"] == "What are your store opening hours?")
        assert nb.lc_verdict(row, 0.85) == "foreign" and nb.lc_verdict(row, 0.95) == "miss"
        rows = nb.lc_lab_rows(swept, 0.85, 0.5)
        shown = next(r for r in rows if r["question"] == row["probe"]["text"])
        assert shown["closest entry"] == "another app"
        assert shown["similarity"] == "0.900"
        html = nb.lc_lab_table(rows).text
        assert "SECRET" not in html and "refund policy" not in html and "foreign" in html

    def test_the_scores_count_our_entries_only(self, nb, seeded, server):
        before = nb.lc_scoreboard(nb.lc_sweep(seeded, nb.lc_probes(), 0.5), 0.85)
        server.add_foreign("What is your refund policy?", "x")
        for probe in nb.lc_probes():
            server.scores[(probe["text"].lower(), "what is your refund policy?")] = 0.99
        after = nb.lc_scoreboard(nb.lc_sweep(seeded, nb.lc_probes(), 0.5), 0.85)
        assert after == before

    def test_a_stored_prompt_is_named_by_its_key_and_any_other_prompt_by_itself(self, nb):
        assert nb.lc_seed_key("  HOW do I wash my  down sleeping bag? ") == "bag"
        assert nb.lc_seed_key(TAUGHT + "x") == TAUGHT + "x"

    def test_the_table_tints_by_verdict_and_escapes_everything(self, nb):
        rows = [
            {"question": "<script>alert(1)</script>", "kind": "<b>k</b>", "closest entry": "p",
             "similarity": "0.9", "verdict": verdict}
            for verdict in ("hit", "false hit", "missed paraphrase", "foreign", "miss")
        ]  # fmt: skip
        html = nb.lc_lab_table(rows).text
        assert "<script>" not in html and "&lt;script&gt;" in html and "<b>" not in html
        assert html.count("<th>") == 4  # the kind is under the question, not a column of its own
        assert "&lt;/script&gt;<br><small>&lt;b&gt;k&lt;/b&gt;</small>" in html
        assert html.count("background: rgba") == 4  # a plain miss is not tinted
        for verdict in ("hit", "false hit", "missed paraphrase", "foreign", "miss"):
            assert f"<td>{verdict}</td>" in html  # the words carry it too, not only the colour


# ---- searchStrategies -----------------------------------------------------------------------------------------------------
class TestStrategies:
    @pytest.fixture
    def sweep(self, nb, seeded):
        return nb.lc_strategy_sweep(seeded, nb.lc_variants(), 0.5)

    def test_every_variant_is_asked_under_the_three_lists_in_order(self, nb, seeded, server):
        before = len(server.calls)
        nb.lc_strategy_sweep(seeded, nb.lc_variants(), 0.5)
        sent = [b["searchStrategies"] for _, _, b in server.calls[before:]]
        assert sent == [["semantic"], ["exact"], ["exact", "semantic"]] * 6

    def test_exact_finds_a_change_of_case_and_not_a_change_of_punctuation(self, nb, sweep):
        rows = {r["variant"]: r for r in nb.lc_strategy_rows(sweep, 0.85)}
        assert rows["case"]["exact"] == rows["shouting"]["exact"] == "HIT"
        for label in ("punctuation", "spacing", "paraphrase"):
            assert rows[label]["exact"] == "miss"

    def test_semantic_finds_the_paraphrase_and_says_which_strategy_answered(self, nb, sweep):
        rows = {r["variant"]: r for r in nb.lc_strategy_rows(sweep, 0.85)}
        assert rows["paraphrase"]["semantic"] == "HIT 0.90"
        assert rows["paraphrase"]["exact + semantic"] == "HIT 0.90 (semantic)"
        assert rows["loose paraphrase"]["semantic"] == "miss"
        assert nb.lc_strategy_rows(sweep, 0.5)[-1]["semantic"] == "HIT 0.78"

    def test_both_together_try_exact_first(self, nb, sweep):
        rows = {r["variant"]: r for r in nb.lc_strategy_rows(sweep, 0.85)}
        assert rows["case"]["exact + semantic"] == "HIT (exact)"
        assert rows["punctuation"]["exact + semantic"] == "HIT 0.98 (semantic)"

    def test_the_threshold_moves_the_table_without_a_request(self, nb, sweep, server):
        before = len(server.calls)
        assert nb.lc_strategy_rows(sweep, 0.98)[2]["semantic"] == "HIT 0.98"
        assert nb.lc_strategy_rows(sweep, 0.985)[2]["semantic"] == "miss"
        assert len(server.calls) == before

    def test_an_answer_from_another_entry_is_called_wrong(self, nb, sweep):
        other = dict(sweep[0]["found"]["semantic"], prompt=nb.lc_seeds()["stove"]["prompt"])
        sweep[0]["found"]["semantic"] = other
        assert nb.lc_strategy_rows(sweep, 0.5)[0]["semantic"] == "HIT 1.00 wrong entry"

    def test_an_exact_match_is_a_hit_whatever_the_slider_and_whatever_score_it_carries(
        self, nb, sweep
    ):
        # the service's score for an exact hit is not documented: 0, or none at all, must do
        sweep[0]["found"]["exact"] = dict(sweep[0]["found"]["exact"], similarity=0.0)
        for threshold in (0.5, 0.99, 1.0):
            row = nb.lc_strategy_rows(sweep, threshold)[0]
            assert row["exact"] == "HIT"

    def test_a_semantic_entry_under_the_slider_is_still_a_miss(self, nb, sweep):
        sweep[4]["found"]["semantic"] = dict(sweep[4]["found"]["semantic"], similarity=0.0)
        assert nb.lc_strategy_rows(sweep, 0.5)[4]["semantic"] == "miss"


# ---- the chat -------------------------------------------------------------------------------------------------------------------------
class TestChat:
    ASK = "How can I get my rain jacket to bead water again?"

    def test_a_question_that_matches_is_a_hit_with_the_whole_badge(self, nb, seeded):
        history = []
        nb.lc_ask(seeded, history, self.ASK, 0.8)
        (turn,) = history
        hit = turn["hit"]
        assert (
            turn["kind"] == "ask"
            and hit["similarity"] == 0.91
            and hit["id"] in nb.ledger_live("lc", "entry")
        )
        text = nb.lc_turn_view(turn).text
        for part in ("HIT", "0.910", hit["prompt"], hit["id"], " ms", hit["response"][:20]):
            assert part in text

    def test_a_question_that_does_not_is_a_miss_that_names_the_threshold(self, nb, seeded):
        history = []
        nb.lc_ask(seeded, history, "How do I fix a snapped tent pole?", 0.8)
        assert history[0]["hit"] is None and history[0]["threshold"] == 0.8
        text = nb.lc_turn_view(history[0]).text
        assert "MISS" in text and "0.80" in text and "LLM" in text

    def test_the_slider_is_the_threshold_of_the_search(self, nb, seeded, server):
        history = []
        nb.lc_ask(seeded, history, self.ASK, 0.95)
        assert history[0]["hit"] is None
        assert bodies(server, "POST", "/entries/search")[-1]["similarityThreshold"] == 0.95

    def test_a_blank_question_is_a_message_and_sends_nothing(self, nb, seeded, server):
        history, before = [], len(server.calls)
        nb.lc_ask(seeded, history, "   ", 0.8)
        assert len(server.calls) == before
        assert (
            history[0]["kind"] == "refused"
            and "Type a question" in nb.lc_turn_view(history[0]).text
        )

    def test_a_match_of_another_app_is_a_miss_for_us_and_says_so(self, nb, seeded, server):
        server.add_foreign("What is your refund policy for late deliveries?", "SECRET-ANSWER-42")
        server.scores[
            ("refund rules for a late parcel", "what is your refund policy for late deliveries?")
        ] = 0.9
        history = []
        nb.lc_ask(seeded, history, "Refund rules for a late parcel", 0.8)
        text = nb.lc_turn_view(history[0]).text
        assert history[0]["hit"] is None and history[0]["hidden"] == 1
        assert "MISS" in text and "Another app" in text and "SECRET" not in text

    def test_teach_stores_the_prefixed_question_for_two_minutes_and_ledgers_it(
        self, nb, seeded, server
    ):
        history, taught = [], set()
        nb.lc_ask(seeded, history, nb.lc_script()["ask"], 0.8)
        nb.lc_teach(seeded, history, taught, nb.lc_script()["answer"])
        body = bodies(server, "POST", "/entries")[-1]
        assert (
            body["prompt"]
            == TAUGHT + nb.lc_script()["ask"]
            == nb.lc_taught_prompt(nb.lc_script()["ask"])
        )
        assert (
            body["response"] == nb.lc_script()["answer"]
            and body["ttlMillis"] == nb.LC_TEACH_TTL_MS == 120_000
        )
        entry_id = history[-1]["entry_id"]
        assert taught == {entry_id} and entry_id in nb.ledger_live("lc", "entry")
        assert history[-1]["kind"] == "teach" and TAUGHT in nb.lc_turn_view(history[-1]).text

    def test_the_taught_callout_gives_the_paraphrase_to_ask_only_for_the_preset_question(
        self, nb, seeded
    ):
        history, taught = [], set()
        nb.lc_ask(seeded, history, nb.lc_script()["ask"], 0.8)
        nb.lc_teach(seeded, history, taught, "An answer.")
        assert f"For example: {nb.lc_script()['again']}" in flat(nb.lc_turn_view(history[-1]).text)
        nb.lc_ask(seeded, history, "Where can I dry my tent?", 0.99)  # your own question
        nb.lc_teach(seeded, history, taught, "An answer.")
        text = flat(nb.lc_turn_view(history[-1]).text)
        assert "Taught." in text and "For example" not in text

    @pytest.mark.parametrize(
        "question",
        ["What is the capital of Australia?", "Should I buy shares?", "Can I return a laptop?"],
    )
    def test_teach_refuses_a_question_with_no_word_from_the_cairn_vocabulary(
        self, nb, seeded, server, question
    ):
        history, taught = [], set()
        nb.lc_ask(seeded, history, question, 0.99)
        before = len(bodies(server, "POST", "/entries"))
        nb.lc_teach(seeded, history, taught, "An answer.")
        assert history[-1]["kind"] == "refused" and "not about Cairn gear" in history[-1]["why"]
        assert len(bodies(server, "POST", "/entries")) == before and taught == set()

    def test_the_vocabulary_is_matched_by_whole_words_only(self, nb):
        assert nb.lc_teach_refusal("Where can I buy a tent?", "yes") == ""
        assert nb.lc_teach_refusal("Is the tents sale on?", "yes") == ""
        assert "not about Cairn gear" in nb.lc_teach_refusal("Is the intent clear?", "yes")
        assert "answer" in nb.lc_teach_refusal("tent", "  ")

    def test_a_prompt_that_would_be_too_long_once_prefixed_is_refused(self, nb):
        assert "1024" in nb.lc_teach_refusal("tent " + "x" * 1010, "yes")

    def test_teach_needs_a_question_that_missed_and_is_not_taught_yet(self, nb, seeded, server):
        history, taught = [], set()
        nb.lc_teach(seeded, history, taught, "An answer.")
        assert "misses first" in history[-1]["why"]
        nb.lc_ask(seeded, history, self.ASK, 0.8)  # a hit
        nb.lc_teach(seeded, history, taught, "An answer.")
        assert history[-1]["kind"] == "refused" and taught == set()
        nb.lc_ask(seeded, history, nb.lc_script()["ask"], 0.8)
        nb.lc_teach(seeded, history, taught, "An answer.")
        nb.lc_teach(seeded, history, taught, "An answer.")  # a second click
        assert history[-1]["kind"] == "refused" and len(taught) == 1

    def test_the_scripted_run_asks_teaches_and_asks_again_and_the_paraphrase_hits(
        self, nb, seeded, server
    ):
        history, taught = [], set()
        nb.lc_autorun_chat(seeded, history, taught, 0.8)
        assert [t["kind"] for t in history] == ["ask", "teach", "ask"]
        assert history[0]["question"] == nb.lc_script()["ask"] and history[0]["hit"] is None
        assert history[2]["question"] == nb.lc_script()["again"]
        assert (
            history[2]["hit"]["id"] == history[1]["entry_id"]
            and history[2]["hit"]["similarity"] == 0.91
        )
        assert history[2]["hit"]["prompt"] == TAUGHT + nb.lc_script()["ask"]

    def test_the_scripted_run_does_not_teach_a_question_that_already_hits(self, nb, seeded, server):
        server.scores[(nb.lc_script()["ask"].lower(), nb.lc_seeds()["tent"]["prompt"].lower())] = (
            0.99
        )
        history, taught = [], set()
        nb.lc_autorun_chat(seeded, history, taught, 0.8)
        assert [t["kind"] for t in history] == ["ask", "ask"] and taught == set()

    def test_once_served_a_taught_entry_can_be_deleted_and_is_then_a_miss(self, nb, seeded, server):
        history, taught = [], set()
        nb.lc_autorun_chat(seeded, history, taught, 0.8)
        entry_id = history[2]["hit"]["id"]
        assert nb.lc_delete(seeded, entry_id) == "deleted"
        assert entry_id not in server.entries and entry_id not in nb.ledger_live("lc", "entry")
        nb.lc_ask(seeded, history, nb.lc_script()["again"], 0.8)
        assert history[-1]["hit"] is None

    def test_a_taught_entry_that_is_never_asked_for_still_expires_within_two_minutes(
        self, nb, seeded, server
    ):
        history, taught = [], set()
        nb.lc_ask(seeded, history, nb.lc_script()["ask"], 0.8)
        nb.lc_teach(seeded, history, taught, nb.lc_script()["answer"])
        server.clock.advance(121)
        nb.lc_ask(seeded, history, nb.lc_script()["again"], 0.8)
        assert history[-1]["hit"] is None

    def test_the_shared_cache_warning_names_the_two_minutes(self, nb):
        assert "visible to other apps for 120 s" in nb.lc_shared_warning()


# ---- expiry ----------------------------------------------------------------------------------------------------------------------------
class TestExpiry:
    def test_the_entry_lives_ten_seconds_and_is_ledgered(self, nb, conn, server):
        entry = nb.lc_ttl_store(conn)
        assert bodies(server, "POST", "/entries")[-1]["ttlMillis"] == 10_000
        assert entry["id"] in nb.ledger_live("lc", "entry") and entry["checks"] == []

    def test_a_check_inside_ten_seconds_hits_and_deletes_nothing(self, nb, conn, server):
        entry = nb.lc_ttl_store(conn)
        server.clock.advance(4)
        nb.lc_ttl_look(conn, entry)
        assert (
            entry["checks"][0]["search"].startswith("HIT")
            and entry["checks"][0]["delete by id"] == "-"
        )
        assert not entry["deleted"] and entry["id"] in server.entries

    def test_the_first_miss_deletes_by_id_and_the_404_means_expired(self, nb, conn, server):
        entry = nb.lc_ttl_store(conn)
        server.clock.advance(11)
        nb.lc_ttl_look(conn, entry)
        assert entry["checks"][0] == {
            "after": entry["checks"][0]["after"], "search": "MISS",
            "delete by id": "404: already expired",
        }  # fmt: skip
        assert entry["deleted"] and entry["id"] not in nb.ledger_live("lc", "entry")
        assert server.calls[-1][0] == "DELETE"

    def test_later_checks_do_not_delete_again(self, nb, conn, server):
        entry = nb.lc_ttl_store(conn)
        server.clock.advance(11)
        nb.lc_ttl_look(conn, entry)
        before = len(server.calls)
        nb.lc_ttl_look(conn, entry)
        assert entry["checks"][1]["delete by id"] == "-"
        assert [m for m, _, _ in server.calls[before:]] == ["POST"]

    def test_an_entry_that_search_cannot_find_but_delete_can_is_reported_as_still_there(
        self, nb, conn, server
    ):
        entry = nb.lc_ttl_store(conn)
        server.scores[(entry["prompt"].lower(), entry["prompt"].lower())] = 0.5  # not found at 0.9
        nb.lc_ttl_look(conn, entry)
        assert entry["checks"][0]["delete by id"] == "204: it was still there"

    def test_the_scripted_check_waits_out_the_expiry_before_looking_again(
        self, nb, conn, server, monkeypatch
    ):
        slept = []
        monkeypatch.setattr(nb.time, "sleep", lambda s: (slept.append(s), server.clock.advance(s)))
        entry = nb.lc_ttl_store(conn)
        nb.lc_ttl_script(conn, entry)
        assert 10.5 < slept[0] <= 11 and len(slept) == 1
        assert [c["search"][:3] for c in entry["checks"]] == ["HIT", "MIS"]
        assert entry["checks"][1]["delete by id"] == "404: already expired"


# ---- cleanup ---------------------------------------------------------------------------------------------------------------------------
class TestCleanup:
    def test_it_deletes_every_ledgered_entry_by_id_and_counts_them(self, nb, seeded, server):
        nb.lc_autorun_chat(seeded, [], set(), 0.8)  # a taught entry, still live
        before = len(nb.ledger_live("lc", "entry"))
        done = nb.lc_cleanup(seeded)
        assert done == {"entries": before, "deleted": before, "expired": 0}
        assert server.entries == {} and nb.ledger_live("lc", "entry", pending=True) == []
        deletes = [p for m, p, _ in server.calls if m == "DELETE"]
        assert len(deletes) == before and all(p.startswith("/entries/") for p in deletes)

    def test_an_entry_that_expired_counts_as_done_not_as_an_error(self, nb, seeded, server):
        server.clock.advance(nb.LC_TTL_MS / 1000 + 1)
        done = nb.lc_cleanup(seeded)
        assert done == {"entries": 5, "deleted": 0, "expired": 5}
        assert nb.ledger_live("lc", "entry") == []

    def test_a_second_cleanup_has_nothing_to_do(self, nb, seeded, server):
        nb.lc_cleanup(seeded)
        before = len(server.calls)
        assert nb.lc_cleanup(seeded)["entries"] == 0 and len(server.calls) == before

    def test_it_never_flushes_and_never_deletes_by_attributes(self, nb, seeded, server):
        nb.lc_autorun_chat(seeded, [], set(), 0.8)
        nb.lc_cleanup(seeded)
        assert not server.flushed and not server.wiped
        assert ("DELETE", "/entries") not in [(m, p) for m, p, _ in server.calls]
        assert all("flush" not in p for _, p, _ in server.calls)

    def test_a_wrong_cache_id_is_an_error_and_leaves_the_ledger_for_a_retry(
        self, nb, seeded, server
    ):
        wrong, _ = nb.lc_connection(HOST, "no-such-cache", nb.Secret(KEY), "")
        with pytest.raises(nb.ServiceError):
            nb.lc_cleanup(wrong)
        assert len(nb.ledger_live("lc", "entry")) == 5 and len(server.entries) == 5

    def test_cleanup_reads_the_documented_expiry_wording_as_well_as_the_live_one(
        self, nb, seeded, server
    ):
        server.missing_entry = "/errors/cache/entry/not-found"  # the API docs' type
        server.entries.clear()  # every entry has expired
        assert nb.lc_cleanup(seeded) == {"entries": 5, "deleted": 0, "expired": 5}
        assert nb.ledger_live("lc", "entry") == []

    def test_a_404_that_names_the_cache_is_the_wrong_cache_id_and_keeps_the_ledger(
        self, nb, seeded, mock_http
    ):
        reply = {"type": "/errors/cache/not-found", "title": "t", "detail": "d"}
        mock_http(status=404, json=reply)
        with pytest.raises(nb.ServiceError, match="Cache ID") as caught:
            nb.lc_delete(seeded, nb.ledger_live("lc", "entry")[0])
        assert caught.value.status == 404 and "/errors/cache/not-found" in str(caught.value)
        assert len(nb.ledger_live("lc", "entry")) == 5  # nothing was deleted: kept for a retry

    @pytest.mark.parametrize(
        "reply",
        [
            {"json": {"type": "/errors/token/not-found", "title": "t", "detail": "d"}},
            {"json": {"title": "t", "detail": "no type"}},
            {"text": "<html>404 Not Found</html>"},
            {},
        ],
        ids=["token", "untyped", "html", "empty"],
    )
    def test_a_404_that_says_nothing_about_the_entry_or_the_cache_is_an_error_and_not_an_expiry(
        self, nb, seeded, mock_http, reply
    ):
        mock_http(status=404, **reply)
        with pytest.raises(nb.ServiceError, match="said neither") as caught:
            nb.lc_delete(seeded, nb.ledger_live("lc", "entry")[0])
        assert "Cache ID" not in str(caught.value)  # it does not blame the Cache ID
        assert len(nb.ledger_live("lc", "entry")) == 5  # nothing was deleted: kept for a retry

    @pytest.mark.parametrize(
        "kind",
        [
            "/errors/resource-not-found",  # the live service, 2026-09-30
            "/errors/cache/entry/not-found",  # the API docs
            "/errors/resource/not-found",
        ],
    )
    def test_a_404_that_names_the_entry_or_the_resource_is_an_expiry(
        self, nb, seeded, mock_http, kind
    ):
        mock_http(status=404, json={"type": kind, "title": "t"})
        entry_id = nb.ledger_live("lc", "entry")[0]
        assert nb.lc_delete(seeded, entry_id) == "expired"
        assert entry_id not in nb.ledger_live("lc", "entry")

    def test_the_live_404_for_an_expired_entry_is_resource_not_found_and_counts_as_expired(
        self, nb, seeded, server, mock_http
    ):
        """Live, 2026-09-30: DELETE of an expired entry answered 404 /errors/resource-not-found."""
        assert server.missing_entry == "/errors/resource-not-found"
        entry_id = nb.ledger_live("lc", "entry")[0]
        del server.entries[entry_id]
        seen = []

        def handler(request):
            answer = server.handle(request)
            seen.append((answer.status_code, answer.json() if answer.content else None))
            return answer

        mock_http(handler=handler)
        assert nb.lc_delete(seeded, entry_id) == "expired"
        assert seen[-1][0] == 404 and seen[-1][1]["type"] == "/errors/resource-not-found"
        assert entry_id not in nb.ledger_live("lc", "entry")

    def test_a_real_error_stops_it_and_keeps_what_is_left_in_the_ledger(
        self, nb, seeded, server, mock_http
    ):
        first = nb.ledger_live("lc", "entry")[0]
        calls = []

        def handler(request):
            calls.append(request.url.path)
            if len(calls) == 2:
                return httpx.Response(
                    500, json={"type": "/errors/unexpected-error", "title": "t", "detail": "boom"}
                )
            return server.handle(request)

        mock_http(handler=handler)
        with pytest.raises(nb.ServiceError, match="boom"):
            nb.lc_cleanup(seeded)
        assert (
            first not in nb.ledger_live("lc", "entry") and len(nb.ledger_live("lc", "entry")) == 4
        )

    def test_only_this_runs_ledger_is_read(self, nb, seeded, server):
        nb.ledger_add("lc", "entry", "e" * 32)
        assert nb.ledger_live("lc", "entry", run="deadbeef") == []
        other = nb.ledger_dir() / "deadbeef.jsonl"
        other.write_text(
            json.dumps({"column": "lc", "kind": "entry", "ident": "f" * 32, "state": "created"})
            + "\n"
        )
        nb.lc_cleanup(seeded)
        assert not [p for m, p, _ in server.calls if m == "DELETE" and p.endswith("f" * 32)]

    def test_an_id_that_this_run_did_not_create_cannot_be_deleted(self, nb, conn, server):
        with pytest.raises(nb.ServiceError, match="not created by this run"):
            nb.lc_delete(conn, "d" * 32)
        assert server.calls == []

    def test_the_report_gives_the_counts_and_says_when_there_was_nothing(self, nb):
        assert "Nothing to clean up" in nb.lc_cleanup_report(
            {"entries": 0, "deleted": 0, "expired": 0}
        )
        report = nb.lc_cleanup_report({"entries": 7, "deleted": 5, "expired": 2})
        assert "**5** of 7" in report and "**2**" in report and "404" in report


# ---- nothing here can flush --------------------------------------------------------------------------------------------------------
def column_items(nb_file):
    """Column 1's cells, then its helpers (which sit at the end of the setup block)."""
    return nbast.column_items(nb_file.read_text(encoding="utf-8"), 1)


def flush_uses(node):
    """Every call named flush, and every string holding a /flush path, anywhere under node."""
    found = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = nbast.call_name(child) or getattr(child.func, "attr", "")
            if "flush" in name.lower():
                found.append(name)
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if "/flush" in child.value.lower():
                found.append(child.value)
    return found


class TestNoFlush:
    """The setup block names flush legitimately (in http_refusal), so only column 1 is read."""

    def test_no_call_is_named_flush_and_no_string_holds_a_flush_path(self, nb_file):
        for item in column_items(nb_file):
            assert flush_uses(item.node) == [], item.node.name

    @pytest.mark.parametrize(
        "code",
        [
            "lc.flush()",
            "client.flush(request=None)",
            "get_cache().flush()",
            "flush_all()",
            "url = '/v1/caches/x/flush'",
            "url = f'{base}/FLUSH'",
        ],
    )
    def test_the_rule_would_catch_it(self, code):
        assert flush_uses(ast.parse(code)), code

    def test_the_rule_leaves_alone_what_only_mentions_the_word(self):
        code = "note = 'Flush and Delete service are never used'\nfluster()\ndelete_entry(1)"
        assert flush_uses(ast.parse(code)) == []

    def test_delete_by_attributes_is_not_used_either(self, nb_file):
        for item in column_items(nb_file):
            for node in ast.walk(item.node):
                if isinstance(node, ast.Constant) and node.value == "DELETE":
                    assert item.node.name == "lc_delete"
                if isinstance(node, ast.Constant) and node.value == "/entries":
                    assert item.node.name in ("lc_store", "lc_delete"), item.node.name

    def test_no_sdk_and_no_client_of_its_own(self, column):
        nodes = [n for _, item in column for n in ast.walk(item)]
        assert not [n for n in nodes if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {n.id for n in nodes if isinstance(n, ast.Name)}
        assert not names & {"HTTP_CLIENT", "LangCache", "SearchStrategy"}
        source = "\n".join(ast.unparse(item) for _, item in column)
        for banned in ("httpx.Client", "mo.ui.chat", "clear_on_submit", ".form("):
            assert banned not in source, banned


# ---- the notebook's shape ---------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def column(nb_file):
    """Column 1's items in file order, without its title cell: (kind, node)."""
    return [
        (i.kind, i.node)
        for i in column_items(nb_file)
        if "# 1 · LangCache" not in ast.unparse(i.node)
    ]


@pytest.fixture(scope="module")
def cells(column):
    return [node for kind, node in column if kind == "cell"]


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


class TestShape:
    def test_the_column_runs_in_the_planned_order(self, cells):
        # one marker global per stage: config, connect, write, floor, then each core cell as
        # controls / fetch, the chat, cleanup, and the deep dive
        markers = [
            "lc_host", "lc_conn", "lc_seed", "lc_ready", "lc_anat_query", "lc_anat", "lc_lab_go",
            "lc_lab", "lc_strat_go", "lc_strat", "lc_history", "lc_chat_q", "lc_last",
            "lc_cleanup_btn", "lc_deep", "lc_ttl_go", "lc_ttl_entry",
        ]  # fmt: skip
        at = [next(i for i, c in enumerate(cells) if m in nbast.defs(c)) for m in markers]
        assert at == sorted(at) and len(set(at)) == len(markers)
        for controls, fetch in (
            ("lc_anat_query", "lc_anat"), ("lc_lab_go", "lc_lab"), ("lc_strat_go", "lc_strat"),
            ("lc_chat_q", "lc_last"),
        ):  # fmt: skip
            assert at[markers.index(fetch)] == at[markers.index(controls)] + 1

    def test_the_helpers_come_last_all_hidden_and_all_prefixed(self, column, nb_file):
        kinds = [kind for kind, _ in column]
        assert set(kinds[kinds.index("function") :]) == {"function"}
        assert all(i.hide_code is True for i in column_items(nb_file) if i.kind == "function")
        assert all(node.name.startswith("lc_") for kind, node in column if kind == "function")

    def test_every_global_a_cell_returns_has_the_column_prefix(self, cells):
        for cell in cells:
            assert all(n.startswith("lc_") for n in nbast.defs(cell)), nbast.defs(cell)

    def test_the_cells_that_use_the_cache_stop_when_there_is_no_connection(self, cells):
        first = [ast.unparse(c.body[0]) for c in cells if c.body]
        assert first.count("mo.stop(lc_conn is None, None)") == 1  # the write button
        assert first.count("mo.stop(not lc_ready, None)") == 5  # four controls cells, deep dive

    def test_only_the_demo_and_cleanup_buttons_can_autorun(self, cells):
        pressed = {
            (c.args[0].id, ast.unparse(nbast.keyword(c, "stage") or ast.Constant("demo")))
            for cell in cells
            for c in nbast.calls(cell, "pressed")
        }
        assert pressed == {
            ("lc_connect", "'demo'"), ("lc_seed", "'demo'"), ("lc_lab_go", "'demo'"),
            ("lc_strat_go", "'demo'"), ("lc_ttl_go", "'demo'"), ("lc_cleanup_btn", "'cleanup'"),
        }  # fmt: skip

    def test_the_chat_and_check_buttons_are_scripted_under_autorun_and_read_with_a_plain_value(
        self, cells
    ):
        source = "\n".join(ast.unparse(c) for c in cells)
        for button in ("lc_send", "lc_teach_btn", "lc_ttl_check", "lc_reset_btn"):
            assert f"{button}.value" in source and f"pressed({button}" not in source
        assert len(re.findall(r"AUTORUN == 'demo' and \(?not lc_history\)?", source)) == 1
        assert (
            len(re.findall(r"AUTORUN == 'demo' and \(?not lc_ttl_entry\['checks'\]", source)) == 1
        )

    def test_every_run_button_in_the_column_is_accounted_for(self, cells):
        assert set(ui_elements(cells, "mo.ui.run_button")) == {
            "lc_connect", "lc_seed", "lc_lab_go", "lc_strat_go", "lc_send", "lc_teach_btn",
            "lc_cleanup_btn", "lc_reset_btn", "lc_ttl_go", "lc_ttl_check",
        }  # fmt: skip

    def test_the_four_presenter_notes_are_plain_say_and_expect_calls(self, cells):
        calls = [c for cell in cells for c in nbast.calls(cell, "notes")]
        assert len(calls) == 4  # three core cells and the chat
        assert all(len(c.args) == 2 and not c.keywords for c in calls)

    def test_a_cell_that_shows_notes_or_rescores_never_calls_the_cache_but_the_chat(self, cells):
        for cell in cells:
            reads = set(nbast.refs(cell))
            if "lc_history" in reads:
                continue  # the chat: its buttons are False on every run but the click's
            notes = bool(nbast.calls(cell, "notes"))
            if notes or reads & {"lc_lab_threshold", "lc_strat_threshold"}:
                assert "lc_conn" not in reads, "a display cell must not be a fetch cell"
            if "lc_lab_go" in reads or "lc_strat_go" in reads:
                assert not notes and not reads & {"lc_lab_threshold", "lc_strat_threshold"}

    def test_the_chat_history_lives_in_a_cell_that_only_a_reset_re_runs(self, cells):
        (holder,) = [c for c in cells if "lc_history" in nbast.defs(c)]
        assert nbast.refs(holder) == ["lc_gen"] and nbast.calls(holder, "lc_gen")
        (turn,) = [c for c in cells if "lc_last" in nbast.defs(c)]
        assert {"lc_history", "lc_taught", "lc_send"} <= set(nbast.refs(turn))

    def test_the_taught_entry_is_deleted_by_a_cell_that_runs_after_the_chat(self, cells):
        (reap,) = [
            c
            for c in cells
            if "lc_taught" in nbast.refs(c) and "lc_last" in nbast.refs(c) and not nbast.defs(c)
        ]
        assert nbast.calls(reap, "lc_delete")
        assert (
            cells.index(reap)
            == next(i for i, c in enumerate(cells) if "lc_last" in nbast.defs(c)) + 1
        )

    def test_the_cleanup_cell_reads_only_config_and_its_own_buttons(self, cells):
        cleanup = [c for c in cells if "stage='cleanup'" in ast.unparse(c)]
        assert len(cleanup) == 1
        assert set(nbast.refs(cleanup[0])) == {
            "lc_cache_id", "lc_cleanup_btn", "lc_host", "lc_key", "lc_reset_btn", "lc_scope",
            "lc_set_gen",
        }  # fmt: skip

    def test_every_slider_waits_for_the_drag_and_starts_at_the_floor(self, cells):
        sliders = ui_elements(cells, "mo.ui.slider")
        assert set(sliders) == {
            "lc_anat_threshold",
            "lc_lab_threshold",
            "lc_strat_threshold",
            "lc_chat_threshold",
        }
        for name, call in sliders.items():
            assert ast.literal_eval(nbast.keyword(call, "debounce")), name
            assert ast.unparse(call.args[0]) == "lc_floor", name
        for name, call in ui_elements(cells, "mo.ui.text", "mo.ui.text_area").items():
            given = nbast.keyword(call, "debounce")
            assert ast.literal_eval(given) if given is not None else True, name

    def test_the_seed_button_says_what_it_writes_before_it_is_pressed(self, cells):
        button = ui_elements(cells, "mo.ui.run_button")["lc_seed"]
        label = ast.unparse(nbast.keyword(button, "label"))
        assert "lc_seeds()" in label and "cache entries" in label

    def test_the_config_cell_links_the_console_and_warns_against_a_shared_cache(self, cells):
        text = "\n".join(ast.unparse(c) for c in nbast.config_panel(cells, "lc_host"))
        for needed in (
            "cloud.redis.io",
            "create-service",
            "Never on a shared cache",
            "Generate key",
        ):
            assert needed in text, needed

    def test_the_secret_is_a_secret_field_and_the_other_settings_start_from_the_environment(
        self, cells
    ):
        fields = ui_elements(cells, "mo.ui.text", "SecretText")
        assert nbast.call_name(fields["lc_key"]) == "SecretText"
        for name, setting in (("lc_host", "LANGCACHE_HOST"), ("lc_cache_id", "LANGCACHE_CACHE_ID"),
                              ("lc_scope", "LANGCACHE_SCOPE_ATTR")):  # fmt: skip
            assert ast.unparse(nbast.keyword(fields[name], "value")) == f"env('{setting}')"

    def test_connect_calls_the_reset_token_and_shows_the_recorded_panel_without_settings(
        self, cells
    ):
        (connect,) = [c for c in cells if "lc_conn" in nbast.defs(c)]
        assert nbast.calls(connect, "lc_gen")
        assert [ast.unparse(c.args[0]) for c in nbast.calls(connect, "recorded_live")] == ["'lc'"]

    def test_every_hosted_call_in_a_cell_is_guarded_by_a_message_not_a_traceback(self, cells):
        for cell in cells:
            if any(
                nbast.calls(cell, name)
                for name in (
                    "lc_search",
                    "lc_sweep",
                    "lc_strategy_sweep",
                    "lc_ttl_store",
                    "lc_ask",
                    "lc_cleanup",
                    "lc_write_seeds",
                )
            ):
                caught = [
                    ast.unparse(h.type)
                    for t in ast.walk(cell)
                    if isinstance(t, ast.Try)
                    for h in t.handlers
                ]
                assert caught and all("ServiceError" in c for c in caught), ast.unparse(cell)[:80]

    def test_the_deep_dive_switch_follows_autorun(self, cells):
        (switch,) = [c for c in cells if "lc_deep" in nbast.defs(c)]
        assert "mo.ui.switch(value=AUTORUN == 'demo'" in ast.unparse(switch)

    def test_the_test_connection_hook_exists_and_takes_nothing(self, column):
        (hook,) = [n for k, n in column if n.name == "lc_test_connection"]
        assert not hook.args.args


# ---- the whole column, cell by cell, against the fake ---------------------------------------------------------------
def texts(outputs):
    """Every output as rendered, and again with the markup taken off: a callout keeps its words
    in an attribute, and a table keeps its rows in one."""
    raw = " ".join(getattr(o, "text", str(o)) for o in outputs)
    return raw + "\n" + flat(raw)


class Btn:
    """Stands in for a mo.ui.run_button in app.run(defs=...)."""

    def __init__(self, value):
        self.value = value


CONFIG = {
    "lc_host": SimpleNamespace(value=""), "lc_cache_id": SimpleNamespace(value=""),
    "lc_key": SimpleNamespace(value=None), "lc_connect": Btn(True),
    "lc_gen": lambda: 0, "lc_set_gen": lambda update: None,
}  # fmt: skip


def chat(question, *, send=True, teach=False, answer="An answer.", threshold=0.8):
    return {
        "lc_chat_q": SimpleNamespace(value=question),
        "lc_chat_threshold": SimpleNamespace(value=threshold),
        "lc_send": Btn(send), "lc_teach_answer": SimpleNamespace(value=answer),
        "lc_teach_btn": Btn(teach),
    }  # fmt: skip


class TestWholeColumn:
    def run(self, load_fresh, stage, defs=None, **env):
        fresh = load_fresh(
            IRIS_BASICS_RUN=RUN, IRIS_BASICS_AUTORUN=stage, IRIS_BASICS_HEADLESS="1", **env
        )
        outputs, found = fresh.app.run(defs=defs)
        return fresh, texts(outputs), found

    def clicked(self, load_fresh, defs, scope="", **env):
        """A user's turn after a demo run: AUTORUN off, the connection made, some buttons pressed."""
        config = {**CONFIG, "lc_scope": SimpleNamespace(value=scope)}
        return self.run(load_fresh, "", {**config, **defs}, **{**ENV, **env})

    def test_with_no_settings_only_the_config_and_the_always_available_cleanup_render(
        self, load_fresh, service
    ):
        outputs, defs = load_fresh().app.run()
        text = texts(outputs)
        assert "LangCache is not configured" in text and "LANGCACHE_HOST" in text
        assert defs["lc_conn"] is None
        # without settings the column still shows what a real run looked like, labelled as such
        assert "Recorded live on 2026-09-30" in text and "not a simulation" in text
        assert "rain jacket to bead water again" in text and "0.8961312" in text
        for later in (
            "Store the gear-care answers",
            "Anatomy of a hit",
            "2 · Threshold lab",
            "asks the cache first",
        ):
            assert later not in text, later
        assert "Clean up" in text and "Deep dive" not in text
        assert service.calls == []

    def test_pressing_cleanup_without_settings_only_says_so(self, load_fresh, service):
        _, text, _ = self.run(load_fresh, "cleanup")
        # twice in the column's cells, and each of those as markup and as words
        assert text.count("LangCache is not configured") == 4 and service.calls == []

    def test_with_settings_but_no_press_nothing_reaches_the_cache(self, load_fresh, service):
        outputs, defs = load_fresh(**ENV).app.run()
        assert "Test connection" in texts(outputs) and defs["lc_conn"] is None
        assert service.calls == []

    def test_autorun_demo_drives_the_column_from_the_test_to_the_deep_dive(
        self, load_fresh, service
    ):
        fresh, text, defs = self.run(load_fresh, "demo", **ENV)
        assert (
            defs["lc_ready"] is True and defs["lc_floor"] == 0.5 and defs["lc_conn"]["scope"] == ""
        )
        assert "Connected" in text and "Searchable" in text and "HIT" in text
        assert defs["lc_anat"]["mine"] and len(defs["lc_lab"]) == 14 and len(defs["lc_strat"]) == 6
        assert "The highest false hit scored" in text and "false hit</td>" in text
        assert (
            fresh.lc_scoreboard(defs["lc_lab"], defs["lc_lab_threshold"].value)["precision"] == 0.8
        )
        assert [t["kind"] for t in defs["lc_history"]] == ["ask", "teach", "ask"]
        assert defs["lc_history"][2]["hit"] is not None and "MISS" in text
        assert "is gone (deleted)" in text and defs["lc_taught"] == set()
        assert [c["search"][:3] for c in defs["lc_ttl_entry"]["checks"]] == ["HIT", "MIS"]
        assert "404: already expired" in text
        # what is left in the cache is the five stored answers, and the ledger agrees
        assert {e["prompt"] for e in service.live().values()} == {
            s["prompt"] for s in fresh.lc_seeds().values()
        }
        assert sorted(fresh.ledger_live("lc", "entry")) == sorted(service.live())
        assert all(
            b["ttlMillis"] in (fresh.LC_TTL_MS, fresh.LC_TEACH_TTL_MS, 10_000)
            for b in bodies(service, "POST", "/entries")
        )

    def test_autorun_demo_never_flushes_or_deletes_by_attributes(self, load_fresh, service):
        self.run(load_fresh, "demo", **ENV)
        self.run(load_fresh, "cleanup", **ENV)
        assert not service.flushed and not service.wiped
        assert all(p.startswith("/entries/") for m, p, _ in service.calls if m == "DELETE")
        assert all(p != "/flush" for _, p, _ in service.calls)

    def test_autorun_cleanup_removes_it_all_from_the_ledger_without_connecting(
        self, load_fresh, service
    ):
        self.run(load_fresh, "demo", **ENV)
        service.calls.clear()
        fresh, text, defs = self.run(load_fresh, "cleanup", **ENV)
        assert service.live() == {} and "Deleted 5 of 5 entries" in text
        assert defs["lc_conn"] is None, "the demo cells did not run"
        assert {m for m, _, _ in service.calls} == {"DELETE"}  # none of Test connection's reads
        assert fresh.ledger_live("lc", "entry", pending=True) == []

    def test_cleanup_still_works_when_the_test_connection_would_fail(
        self, load_fresh, service, monkeypatch
    ):
        self.run(load_fresh, "demo", **ENV)
        monkeypatch.setattr(
            service,
            "search",
            lambda body: service.error(424, "/errors/embeddings/unauthorized", "Unauthorized"),
        )
        _, text, defs = self.run(load_fresh, "cleanup", **ENV)
        assert service.live() == {} and "Deleted" in text and defs["lc_conn"] is None

    def test_the_cleanup_and_reset_buttons_work_when_clicked_and_only_reset_bumps_the_token(
        self, load_fresh, service
    ):
        def clicked(cleanup, reset):
            bumps = []
            config = {
                "lc_host": SimpleNamespace(value=""), "lc_cache_id": SimpleNamespace(value=""),
                "lc_key": SimpleNamespace(value=None), "lc_scope": SimpleNamespace(value=""),
                "lc_connect": Btn(False), "lc_gen": lambda: 0,
                "lc_set_gen": lambda update: bumps.append(update(0)),
                "lc_cleanup_btn": Btn(cleanup), "lc_reset_btn": Btn(reset),
            }  # fmt: skip
            return bumps, self.run(load_fresh, "", config, **ENV)

        self.run(load_fresh, "demo", **ENV)
        bumps, (_, text, _) = clicked(cleanup=True, reset=False)
        assert service.live() == {} and bumps == [] and "Deleted" in text
        self.run(load_fresh, "demo", **ENV)
        bumps, _ = clicked(cleanup=False, reset=True)
        assert service.live() == {} and bumps == [1]

    def test_a_wrong_key_is_a_red_message_that_stops_at_the_test_and_never_shows_the_key(
        self, load_fresh, service
    ):
        _, text, defs = self.run(
            load_fresh, "demo", **{**ENV, "LANGCACHE_API_KEY": "wrong-key-000000"}
        )
        assert defs["lc_conn"] is None and "authentication-failed" in text
        assert "wrong-key-000000" not in text and "Store the gear-care answers" not in text
        assert [m for m, _, _ in service.calls] == ["POST"]

    def test_a_wrong_cache_id_is_a_message_too(self, load_fresh, service):
        _, text, defs = self.run(
            load_fresh, "demo", **{**ENV, "LANGCACHE_CACHE_ID": "no-such-cache"}
        )
        assert defs["lc_conn"] is None and "cache/not-found" in text and service.entries == {}

    @pytest.mark.parametrize(
        ("name", "value", "shown"),
        [
            ("LANGCACHE_HOST", "http://lc.example.com", "must be an https"),
            ("LANGCACHE_CACHE_ID", "a/b", "Cache ID"),
            ("LANGCACHE_API_KEY", "two words", "whitespace"),
            ("LANGCACHE_SCOPE_ATTR", "bad name!", "attribute name"),
        ],
    )
    def test_a_bad_setting_is_a_message_that_sends_nothing(
        self, load_fresh, service, name, value, shown
    ):
        _, text, defs = self.run(load_fresh, "demo", **{**ENV, name: value})
        assert defs["lc_conn"] is None and shown in text and service.calls == []
        assert name != "LANGCACHE_API_KEY" or value not in text

    def test_a_cache_with_the_attribute_tags_everything_and_filters_every_search(
        self, load_fresh, service
    ):
        service.attributes = ("scope",)
        fresh, text, defs = self.run(load_fresh, "demo", LANGCACHE_SCOPE_ATTR="scope", **ENV)
        assert defs["lc_conn"]["scope"] == "scope" and "every search filters on it" in text
        tags = {e["attributes"]["scope"] for e in service.live().values()}
        assert tags == {fresh.RUN_TAG}
        assert all(
            b["attributes"] == {"scope": fresh.RUN_TAG}
            for b in bodies(service, "POST", "/entries/search")
        )
        assert "Shared cache" not in text

    def test_a_cache_without_the_attribute_runs_unscoped_and_says_why(self, load_fresh, service):
        _, text, defs = self.run(load_fresh, "demo", LANGCACHE_SCOPE_ATTR="scope", **ENV)
        assert defs["lc_conn"]["scope"] == "" and "would not take the scope attribute" in text
        assert "no attributes are configured" in text and defs["lc_ready"] is True
        assert all("attributes" not in b for b in bodies(service, "POST", "/entries"))

    def test_a_cache_that_holds_searches_to_085_starts_every_slider_there(
        self, load_fresh, service
    ):
        service.floor = 0.85
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["lc_floor"] == 0.85 and "below cache floor" in text
        for name in (
            "lc_anat_threshold",
            "lc_lab_threshold",
            "lc_strat_threshold",
            "lc_chat_threshold",
        ):
            assert defs[name].start == 0.85 and defs[name].value >= 0.85, name

    def test_sliders_start_at_the_floor_when_the_server_goes_lower(self, load_fresh, service):
        _, _, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["lc_lab_threshold"].start == 0.5 and defs["lc_lab_threshold"].value == 0.85
        assert defs["lc_chat_threshold"].value == 0.8

    def test_every_text_box_has_a_question_so_autorun_has_something_to_ask(
        self, load_fresh, service
    ):
        _, _, defs = self.run(load_fresh, "demo", **ENV)
        for name in ("lc_anat_query", "lc_chat_q", "lc_teach_answer"):
            assert defs[name].value.strip(), name

    def test_no_entry_of_another_app_is_shown_anywhere_and_the_cell_says_so(
        self, load_fresh, service
    ):
        service.add_foreign("What is your refund policy for late deliveries?", "SECRET-ANSWER-42")
        anat = "How can I get my rain jacket to bead water again?"
        service.scores[(anat.lower(), "what is your refund policy for late deliveries?")] = 0.95
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["lc_anat"]["foreign"] == [0.95]
        assert "another app" in text and "(hidden)" in text
        for hidden in ("SECRET-ANSWER-42", "refund policy", "late deliveries"):
            assert hidden not in text, hidden

    def test_the_key_appears_in_no_output_and_no_variables_panel_preview(self, load_fresh, service):
        from marimo._messaging.variables import create_variable_value

        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert KEY not in text
        for name, value in defs.items():
            assert KEY not in (create_variable_value(name, value).value or ""), name

    def test_a_429_in_the_middle_of_the_sweep_is_a_message_and_not_a_traceback(
        self, load_fresh, service, monkeypatch
    ):
        real = service.search

        def tired(body):
            if body["prompt"] == "How should I store my kayak between trips?":
                return service.error(
                    429, "/errors/too-many-requests", "Too many requests", "slow down"
                )
            return real(body)

        monkeypatch.setattr(service, "search", tired)
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert "too-many-requests" in text and "lc_lab" not in defs and defs["lc_ready"] is True

    def test_a_424_when_writing_is_a_message_and_the_column_stays_shut(
        self, load_fresh, service, monkeypatch
    ):
        monkeypatch.setattr(
            service, "store",
            lambda body: service.error(424, "/errors/embeddings/unauthorized", "Unauthorized", "the embedding key was rejected"),
        )  # fmt: skip
        _, text, defs = self.run(load_fresh, "demo", **ENV)
        assert defs["lc_ready"] is False and "embeddings/unauthorized" in text
        assert "Anatomy of a hit" not in text and service.entries == {}

    def test_entries_that_have_expired_send_the_user_back_to_the_button(self, load_fresh, service):
        self.run(load_fresh, "demo", **ENV)
        service.clock.advance(901)
        _, text, defs = self.clicked(load_fresh, {})
        assert defs["lc_ready"] is False and "expired" in text and "Anatomy of a hit" not in text

    def test_a_returning_user_finds_the_entries_without_writing_again(self, load_fresh, service):
        self.run(load_fresh, "demo", **ENV)
        before = len(bodies(service, "POST", "/entries"))
        _, text, defs = self.clicked(load_fresh, {})
        assert defs["lc_ready"] is True and len(bodies(service, "POST", "/entries")) == before

    def test_send_asks_the_cache_and_a_miss_offers_teach_with_the_red_warning(
        self, load_fresh, service
    ):
        self.run(load_fresh, "demo", **ENV)
        _, text, defs = self.clicked(load_fresh, chat("How do I fix a snapped tent pole?"))
        assert [t["kind"] for t in defs["lc_history"]] == ["ask"] and defs["lc_last"]["hit"] is None
        assert "MISS" in text and "Teach it." in text
        assert TAUGHT + "How do I fix a snapped tent pole?" in text
        assert "Shared cache:" in text and "visible to other apps for 120 s" in text

    def test_the_red_warning_is_not_shown_when_the_run_is_scoped(self, load_fresh, service):
        service.attributes = ("scope",)
        self.run(load_fresh, "demo", LANGCACHE_SCOPE_ATTR="scope", **ENV)
        _, text, _ = self.clicked(
            load_fresh, chat("How do I fix a snapped tent pole?"), scope="scope"
        )
        assert "Teach it." in text and "Shared cache:" not in text

    def test_send_with_a_matching_question_shows_the_badge_and_no_teach_controls(
        self, load_fresh, service
    ):
        self.run(load_fresh, "demo", **ENV)
        question = "How can I get my rain jacket to bead water again?"
        _, text, defs = self.clicked(load_fresh, chat(question))
        assert defs["lc_last"]["hit"]["similarity"] == 0.91 and "similarity 0.910" in text
        assert "Teach it." not in text and "is gone" not in text  # a stored answer is not deleted

    def test_teach_stores_the_last_miss_and_the_paraphrase_that_follows_is_served_and_reaped(
        self, load_fresh, service
    ):
        fresh, _, _ = self.run(load_fresh, "demo", **ENV)
        script, connection = fresh.lc_script(), fresh.lc_connection("", "", None, "")[0]
        # The history belongs to a cell that only Reset re-runs, so a click's run is handed it.
        history, taught = [], set()
        fresh.lc_ask(connection, history, script["ask"], 0.8)
        kept = {"lc_history": history, "lc_taught": taught}

        pressed_teach = chat("", send=False, teach=True, answer="Splint it with the repair sleeve.")
        _, text, _ = self.clicked(load_fresh, {**kept, **pressed_teach})
        (entry_id,) = taught
        stored = service.entries[entry_id]
        assert stored["prompt"] == TAUGHT + script["ask"] and stored["ttl_ms"] == 120_000
        assert history[-1]["kind"] == "teach" and "Taught." in text and "Teach it." not in text
        assert f"For example: {script['again']}" in text  # the paraphrase to ask is on screen

        _, text, defs = self.clicked(load_fresh, {**kept, **chat(script["again"])})
        assert history[-1]["hit"]["id"] == entry_id and history[-1]["hit"]["response"] == (
            "Splint it with the repair sleeve."
        )
        assert "is gone (deleted)" in text and taught == set() and entry_id not in service.entries

    def test_teach_refuses_off_topic_questions_in_the_notebook_too(self, load_fresh, service):
        self.run(load_fresh, "demo", **ENV)
        before = len(bodies(service, "POST", "/entries"))
        _, text, defs = self.clicked(load_fresh, chat("What is the capital of Australia?"))
        assert defs["lc_history"][-1]["kind"] == "ask" and "Teach it." in text
        history = defs["lc_history"]
        kept = {"lc_history": history, "lc_taught": set()}
        _, text, _ = self.clicked(load_fresh, {**kept, **chat("", send=False, teach=True)})
        assert history[-1]["kind"] == "refused" and "not about Cairn gear" in text
        assert "Teach it." in text  # still there, to try again
        assert len(bodies(service, "POST", "/entries")) == before

    def test_a_refused_teach_or_a_blank_send_leaves_the_teach_controls_for_another_try(
        self, load_fresh, service
    ):
        fresh, _, _ = self.run(load_fresh, "demo", **ENV)
        script, connection = fresh.lc_script(), fresh.lc_connection("", "", None, "")[0]
        history, taught = [], set()
        fresh.lc_ask(connection, history, script["ask"], 0.8)  # a miss, not taught yet
        kept = {"lc_history": history, "lc_taught": taught}

        blank_answer = chat("", send=False, teach=True, answer="   ")
        _, text, _ = self.clicked(load_fresh, {**kept, **blank_answer})
        assert history[-1]["kind"] == "refused" and "Type the answer to store first." in text
        assert "Teach it." in text  # the answer box is still there to type into
        _, text, _ = self.clicked(load_fresh, {**kept, **chat("  ")})
        assert "Type a question first" in text and "Teach it." in text and taught == set()

        teach = chat("", send=False, teach=True, answer="Splint it with the repair sleeve.")
        _, text, _ = self.clicked(load_fresh, {**kept, **teach})
        assert history[-1]["kind"] == "teach" and len(taught) == 1
        assert "Taught." in text and "Teach it." not in text  # taught: nothing left to teach

    def test_a_blank_question_is_a_message(self, load_fresh, service):
        self.run(load_fresh, "demo", **ENV)
        before = len(bodies(service, "POST", "/entries/search"))
        _, text, defs = self.clicked(load_fresh, chat("  "))
        searched = bodies(service, "POST", "/entries/search")[before:]
        assert "Type a question first" in text and all(b["prompt"].strip() for b in searched)
        assert defs["lc_history"][-1]["kind"] == "refused"

    def test_an_error_while_asking_is_a_message_and_the_history_survives(
        self, load_fresh, service, monkeypatch
    ):
        self.run(load_fresh, "demo", **ENV)
        monkeypatch.setattr(
            service, "search",
            lambda body: service.error(424, "/errors/embeddings/unauthorized", "Unauthorized", "no embedding key"),
        )  # fmt: skip
        _, text, defs = self.clicked(load_fresh, chat("How do I fix a snapped tent pole?"))
        assert "embeddings/unauthorized" in text and defs["lc_history"] == []

    def test_the_expiry_check_can_be_pressed_by_hand(self, load_fresh, service):
        fresh, _, defs = self.run(load_fresh, "demo", **ENV)
        entry = fresh.lc_ttl_store(fresh.lc_connection("", "", None, "")[0])
        controls = {"lc_ttl_check": Btn(True), "lc_ttl_entry": entry, "lc_ttl_go": Btn(False)}
        service.clock.advance(11)
        _, text, defs = self.clicked(
            load_fresh, {"lc_deep": SimpleNamespace(value=True), **controls}
        )
        assert entry["checks"][0]["delete by id"] == "404: already expired" and "404" in text

    def test_the_store_button_lists_the_questions_it_will_write(self, load_fresh, service):
        fresh, text, _ = self.clicked(load_fresh, {})
        for key, seed in fresh.lc_seeds().items():
            assert key in text and seed["prompt"] in text
        assert service.entries == {}

    ASK = "How can I get my rain jacket to bead water again?"  # 0.91 against the shell entry

    def searched(self, service, prompt):
        """The similarityThreshold of every search for prompt since the calls were cleared."""
        return [
            b["similarityThreshold"]
            for b in bodies(service, "POST", "/entries/search")
            if b["prompt"] == prompt
        ]

    def test_the_anatomy_slider_is_the_threshold_of_its_request_and_the_verdict(
        self, load_fresh, service
    ):
        self.run(load_fresh, "demo", **ENV)

        def anatomy(threshold):
            service.calls.clear()
            controls = {
                "lc_anat_query": SimpleNamespace(value=self.ASK),
                "lc_anat_threshold": SimpleNamespace(value=threshold),
            }
            _, text, defs = self.clicked(load_fresh, controls)
            return self.searched(service, self.ASK), text, defs["lc_anat"]

        sent, text, found = anatomy(0.95)
        assert sent == [0.95] and found["mine"] == [] and "MISS at 0.95" in text
        sent, text, found = anatomy(0.9)
        assert sent == [0.9] and found["mine"][0]["similarity"] == 0.91 and "HIT at 0.90" in text

    def test_the_chat_slider_is_the_threshold_of_the_search_it_sends(self, load_fresh, service):
        self.run(load_fresh, "demo", **ENV)
        service.calls.clear()
        _, text, defs = self.clicked(load_fresh, chat(self.ASK, threshold=0.95))
        assert 0.95 in self.searched(service, self.ASK)
        assert defs["lc_last"]["hit"] is None and "MISS at 0.95" in text

    def test_the_lab_slider_re_scores_the_tiles_and_the_rows(self, load_fresh, service):
        self.run(load_fresh, "demo", **ENV)

        def lab(threshold):
            controls = {
                "lc_lab_go": Btn(True),
                "lc_lab_threshold": SimpleNamespace(value=threshold),
            }
            return self.clicked(load_fresh, controls)[1]

        high, default = lab(0.95), lab(0.85)
        assert "1 of 14 questions hit" in high and "5 of 14 questions hit" in default
        assert "<td>false hit</td>" not in high and "<td>false hit</td>" in default
        assert "<td>missed paraphrase</td>" in high

    def test_the_presenter_notes_of_the_chat_name_the_paraphrase_to_ask(self, load_fresh, service):
        self.run(load_fresh, "demo", **ENV)
        fresh, text, _ = self.clicked(load_fresh, chat(self.ASK))
        assert "Say:" in text and f"try: {fresh.lc_script()['again']}" in text

    def test_the_strategies_slider_re_scores_the_semantic_column(self, load_fresh, service):
        self.run(load_fresh, "demo", **ENV)

        def strategies(threshold):
            controls = {
                "lc_strat_go": Btn(True),
                "lc_strat_threshold": SimpleNamespace(value=threshold),
            }
            return self.clicked(load_fresh, controls)[1]

        high, default = strategies(0.95), strategies(0.85)
        assert "HIT 0.90" in default and "HIT 0.90" not in high
        assert "HIT 0.96" in high and "below 0.95" in high


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
        assert "LangCache is not configured" in session
        assert "Recorded live on 2026-09-30" in session
        for later in (
            "Store the gear-care answers",
            "Anatomy of a hit",
            "2 · Threshold lab",
            "asks the cache first",
        ):
            assert later not in session, later


# ---- the real marimo runtime ------------------------------------------------------------------------------------------------
RUNNER = """
import json
import pickle
import sys
from pathlib import Path

sys.path.insert(0, {tests!r})
import httpx
from fake_lc import FakeLangCache

server, state = FakeLangCache({cache!r}, {key!r}), Path({state!r})
server.scores = {{(q, p): s for q, p, s in json.loads(Path({scores!r}).read_text("utf-8"))}}
if state.exists():
    server.restore(pickle.loads(state.read_bytes()))


def handle(self, request):
    response = server.handle(request)
    state.write_bytes(pickle.dumps(server.snapshot()))
    return response


httpx.HTTPTransport.handle_request = handle  # marimo spawns its kernel, which re-imports this file

if __name__ == "__main__":
    from marimo._cli.cli import main

    args = ["-y", "export", "html", "--no-sandbox", {notebook!r}, "-o", "out.html", "-f"]
    main(args, standalone_mode=False)
"""


class TestRealRuntime:
    """`marimo export` itself, not app.run(): its kernel cancels the descendants of a stop."""

    def export(self, nb, nb_file, tmp_path, stage):
        work = tmp_path / "real"
        work.mkdir(exist_ok=True)
        shutil.copy2(nb_file, work / nb_file.name)
        state = tmp_path / "server.pkl"
        plan = score_plan(nb.lc_seeds(), nb.lc_probes(), nb.lc_variants(), nb.lc_script())
        scores = tmp_path / "scores.json"
        scores.write_text(json.dumps([[q, p, s] for (q, p), s in plan.items()]), encoding="utf-8")
        (work / "runner.py").write_text(
            RUNNER.format(
                tests=str(Path(__file__).parent),
                cache=CACHE,
                key=KEY,
                state=str(state),
                scores=str(scores),
                notebook=nb_file.name,
            ),
            encoding="utf-8",
        )
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        env.update(
            PYTHONIOENCODING="utf-8", PYTHONUTF8="1", IRIS_BASICS_RUN=RUN,
            IRIS_BASICS_AUTORUN=stage, IRIS_BASICS_HEADLESS="1", **ENV,
        )  # fmt: skip
        done = subprocess.run(
            [sys.executable, "runner.py"], cwd=work, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
        session = work / "__marimo__" / "session" / f"{nb_file.name}.json"
        entries, written = pickle.loads(state.read_bytes())
        return json.loads(session.read_text(encoding="utf-8")), entries, written

    @staticmethod
    def errors(session, nb_file):
        """The errors in this column's cells. Column 0 has no database here, so its own cells stop,
        and so do their descendants: they are not this column's business."""
        items = nbast.items(nb_file.read_text(encoding="utf-8"))
        number = 1
        assert len(session["cells"]) == len(items) + 1  # the setup block comes first
        return [
            o
            for item, cell in zip(items, session["cells"][1:], strict=True)
            if item.column == number
            for o in cell.get("outputs") or []
            if o["type"] == "error"
        ]

    @staticmethod
    def words(session):
        outputs = [
            o["data"].get("text/html", "")
            for c in session["cells"]
            for o in c.get("outputs") or []
            if o["type"] == "data"
        ]
        raw = "\n".join(outputs)
        return raw + "\n" + flat(raw)

    def test_autorun_demo_then_cleanup_run_in_the_real_kernel(self, nb, nb_file, tmp_path):
        session, entries, written = self.export(nb, nb_file, tmp_path, "demo")
        assert self.errors(session, nb_file) == []
        text = self.words(session)
        for shown in (
            "Connected",
            "Searchable",
            "highest false hit",
            "is gone",
            "404: already expired",
        ):
            assert shown in text, shown
        # seven entries were written, and the taught one and the ten-second one are already gone
        assert written == 7
        assert {e["prompt"] for e in entries.values()} == {
            s["prompt"] for s in nb.lc_seeds().values()
        }
        assert all(e["ttl_ms"] == nb.LC_TTL_MS for e in entries.values())

        session, entries, _ = self.export(nb, nb_file, tmp_path, "cleanup")
        assert entries == {} and "Deleted 5 of 5 entries" in self.words(session)
        # Test connection did not run, so its descendants were cancelled; cleanup ran regardless.
        assert {e["ename"] for e in self.errors(session, nb_file)} == {"ancestor-stopped"}
