"""Tests for column 3, Agent Memory (plan section 5).

No service. The hosted API is a small in-memory stand-in (fake_am.py) behind httpx, so the
requests, the reply parsing and the cells themselves run for real. What the fake cannot tell
us (what the other zero-overlap presets rank first, how it words an error, where it keeps a
memory in the database) is listed in tests/README.md under "What only a live run can show".
"""

import ast
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import notebook_ast as nbast
import pytest
import redis
from fake_am import BASE, KEY, STORE, FakeIndexServer, FakeMemory, similarity
from fake_search import MISSING_INDEX

COLUMN_PREFIX = "am_"  # tests/conftest.py: this module tests column 3


@pytest.fixture(autouse=True)
def column_present(nb):
    if not hasattr(nb, "am_memories"):
        pytest.skip("this module has no column 3")


@pytest.fixture
def service(nb, monkeypatch):
    """A FakeMemory that answers every request the notebook's HTTP client sends."""
    fake = FakeMemory()
    monkeypatch.setattr(nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(fake.handle)))
    return fake


@pytest.fixture
def conn(nb, service):
    found, missing = nb.am_settings(BASE, STORE, nb.Secret(KEY))
    assert missing == []
    return found


@pytest.fixture
def written(nb, conn):
    """A store that holds the column's five memories."""
    nb.am_write_memories(conn)
    return conn


def live_demo(service):
    """Somebody else's memories in the same store: what the live Iris demo keeps there."""
    service.add_memory("demo-1", "Priya prefers aisle seats.", "CUST-DEMO-001",
                       "radish-bank-workshop", hours_old=900)  # fmt: skip
    service.add_memory("demo-2", "Kim only eats vegetarian food.", "CUST-DEMO-002")  # no namespace


def orphan_run(service, run="deadbeef", *, hours_old=5, memories=2, sessions=1, namespace="iris-basics-notebook"):
    """What a run that closed without Clean up leaves behind."""
    for n in range(1, memories + 1):
        service.add_memory(f"ib-{run}-m{n}", f"leftover {n}", f"ib-{run}", namespace, hours_old=hours_old)
    for n in range(1, sessions + 1):
        service.add_session(f"ib-{run}-s{n}", f"ib-{run}", namespace)


# ---- settings ----------------------------------------------------------------------------
class TestSettings:
    def test_nothing_set_names_every_missing_setting(self, nb):
        found, missing = nb.am_settings("", "", None)
        assert found is None
        assert missing == ["MEMORY_API_BASE_URL", "MEMORY_STORE_ID", "MEMORY_API_KEY"]

    def test_the_environment_fills_what_the_fields_leave_empty(self, nb, monkeypatch):
        monkeypatch.setenv("MEMORY_API_BASE_URL", BASE + "/")
        monkeypatch.setenv("MEMORY_STORE_ID", STORE)
        monkeypatch.setenv("MEMORY_API_KEY", KEY)
        found, missing = nb.am_settings("", "  ", None)
        assert missing == [] and found["base"] == BASE and found["store"] == STORE
        assert found["key"].reveal() == KEY

    def test_what_is_typed_wins_over_the_environment(self, nb, monkeypatch):
        monkeypatch.setenv("MEMORY_API_BASE_URL", "https://env.example")
        monkeypatch.setenv("MEMORY_STORE_ID", "env-store")
        monkeypatch.setenv("MEMORY_API_KEY", "env-key-0000")
        found, _ = nb.am_settings(BASE, STORE, nb.Secret(KEY))
        assert (found["base"], found["store"], found["key"].reveal()) == (BASE, STORE, KEY)

    def test_the_endpoint_must_be_https_and_the_error_does_not_echo_it(self, nb):
        with pytest.raises(ValueError) as caught:
            nb.am_settings("http://pasted-secret-value", STORE, nb.Secret(KEY))
        assert "https://" in str(caught.value) and "pasted-secret-value" not in str(caught.value)

    @pytest.mark.parametrize("store", ["has space", "under_score", "x" * 65, "a/b"])
    def test_a_store_id_is_letters_digits_and_hyphens(self, nb, store):
        with pytest.raises(ValueError):
            nb.am_settings(BASE, store, nb.Secret(KEY))

    def test_the_key_is_held_as_a_secret_that_previews_masked(self, nb, conn):
        assert KEY not in repr(conn) and KEY not in str(conn)
        assert KEY in {*nb.SECRETS.forms()}  # registered, so scrub() masks it wherever it turns up

    def test_a_key_with_a_stray_newline_is_refused_without_being_shown(self, nb):
        with pytest.raises(ValueError) as caught:
            nb.am_settings(BASE, STORE, nb.Secret("am-key\ntest-9f3a"))
        assert "9f3a" not in str(caught.value)


# ---- the filter every search carries ---------------------------------------------------------
class TestFilter:
    def test_it_always_holds_this_runs_owner_and_the_notebook_namespace(self, nb):
        assert nb.am_filter() == {
            "ownerId": {"eq": nb.AM_OWNER},
            "namespace": {"eq": nb.AM_NAMESPACE},
        }

    def test_without_a_namespace_it_is_the_owner_alone(self, nb):
        assert nb.am_filter(namespace=None) == {"ownerId": {"eq": nb.AM_OWNER}}

    def test_more_clauses_can_be_added_but_the_owner_cannot_be_replaced(self, nb):
        assert nb.am_filter(topics={"in": ["food"]})["topics"] == {"in": ["food"]}
        with pytest.raises(AssertionError):
            nb.am_filter(ownerId={"eq": "CUST-DEMO-001"})

    def test_a_search_is_always_and_only_a_conjunction(self, nb, service, conn):
        nb.am_search(conn, "boots", nb.am_filter())
        body = service.requests("POST", "/search")[0].body
        assert body["filterOp"] == "all" and body["filter"] == nb.am_filter()

    def test_a_threshold_of_zero_is_sent_and_no_threshold_is_not(self, nb, service, conn):
        nb.am_search(conn, "boots", nb.am_filter(), threshold=0.0)
        nb.am_search(conn, "", nb.am_filter())
        with_zero, without = (c.body for c in service.requests("POST", "/search"))
        assert with_zero["similarityThreshold"] == 0.0 and "similarityThreshold" not in without

    def test_a_search_with_no_owner_or_namespace_never_leaves_the_notebook(self, nb, service, conn):
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_search(conn, "boots", {})
        assert caught.value.kind == "refused" and service.log == []

    def test_a_listing_pages_through_every_result(self, nb, service, conn):
        for n in range(230):
            service.add_memory(f"{nb.AM_OWNER}-m{n + 100}", f"memory {n}", nb.AM_OWNER, nb.AM_NAMESPACE)
        listed = nb.am_list(conn, nb.am_filter())
        assert len(listed["items"]) == 230 and len(listed["calls"]) == 3
        assert [c.body["limit"] for c in service.requests("POST", "/search")] == [100, 100, 100]
        assert all("similarityThreshold" not in c.body for c in service.requests("POST", "/search"))

    def test_a_reply_without_items_is_an_error_not_an_empty_list(self, nb, monkeypatch, conn):
        monkeypatch.setattr(
            nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        )
        with pytest.raises(nb.ServiceError, match="no list of items"):
            nb.am_search(conn, "boots", nb.am_filter())


# ---- ids ------------------------------------------------------------------------------------
class TestIds:
    def test_memory_and_session_ids_carry_the_run_and_a_number(self, nb):
        assert nb.am_memory_id(3) == f"{nb.AM_OWNER}-m3"
        assert nb.am_session_id(2) == f"{nb.AM_OWNER}-s2"
        assert re.fullmatch(r"[a-zA-Z0-9-]{1,64}", nb.am_session_id(12))  # what the service accepts

    def test_a_session_number_is_never_reused_even_after_a_delete(self, nb):
        assert nb.am_next_session() == nb.am_session_id(1)
        nb.ledger_add("am", "session", nb.am_session_id(1))
        nb.ledger_add("am", "session", nb.am_session_id(1), "deleted")
        nb.ledger_add("am", "session", "ib-0000aaaa-s9")  # another run's line is not ours
        assert nb.am_next_session() == nb.am_session_id(2)


# ---- the memories and the presets -------------------------------------------------------------
class TestMemories:
    def test_four_or_five_facts_ready_to_post_with_our_ids_owner_and_namespace(self, nb):
        memories = nb.am_memories()
        assert 4 <= len(memories) <= 5
        assert [m["id"] for m in memories] == [nb.am_memory_id(n) for n in range(1, len(memories) + 1)]
        for memory in memories:
            assert memory["ownerId"] == nb.AM_OWNER and memory["namespace"] == nb.AM_NAMESPACE
            assert memory["memoryType"] == "semantic" and memory["topics"] and memory["text"]

    def test_they_pass_the_choke_point_as_written(self, nb):
        assert nb.http_refusal("am", "POST", f"{BASE}/v1/stores/{STORE}/long-term-memory",
                               {"memories": nb.am_memories()}) is None  # fmt: skip

    def test_there_are_three_presets_and_each_after_a_different_memory(self, nb):
        presets = nb.am_wow_presets()
        ids = {m["id"] for m in nb.am_memories()}
        assert len(presets) == 3
        targets = [p["target"] for p in presets]
        assert len(set(targets)) == 3 and set(targets) <= ids
        assert all(p["query"].strip() and p["label"] and p["lesson"] for p in presets)

    def test_no_preset_shares_a_word_with_its_target_however_it_is_counted(self, nb):
        memories = {m["id"]: m["text"] for m in nb.am_memories()}
        for preset in nb.am_wow_presets():
            fact = memories[preset["target"]]
            assert nb.am_overlap(preset["query"], fact) == [], preset["label"]
            plain = set(re.findall(r"[a-z]+", preset["query"].lower()))
            assert not plain & set(re.findall(r"[a-z]+", fact.lower())), preset["label"]
            # a stricter reader that treats two words with the same first five letters as one
            stems = {w[:5] for w in plain if len(w) > 4}
            assert not stems & {w[:5] for w in re.findall(r"[a-z]+", fact.lower()) if len(w) > 4}

    def test_the_fake_ranks_each_target_first_so_the_flow_can_be_shown(self, nb):
        memories = nb.am_memories()
        for preset in nb.am_wow_presets():
            best = max(memories, key=lambda m: similarity(preset["query"], m["text"]))
            assert best["id"] == preset["target"], preset["label"]

    def test_a_typed_message_is_a_preset_with_no_target(self, nb):
        own = nb.am_wow_preset("Type your own", "  a lighter tent ")
        assert own["query"] == "a lighter tent" and own["target"] is None
        first = nb.am_wow_presets()[0]
        assert nb.am_wow_preset(first["label"], "ignored") == first

    def test_overlap_counts_stems_and_ignores_common_words(self, nb):
        assert nb.am_overlap("Which boots fit wide feet?", "Dana has wide feet and needs boots.") == [
            "boot", "feet", "wide",
        ]  # fmt: skip
        assert nb.am_overlap("the and of", "the and of") == []
        assert nb.am_overlap("Serving", "serve") == []  # a crude stemmer does not pretend otherwise


# ---- events -------------------------------------------------------------------------------------
class TestEvents:
    def test_the_first_event_is_a_user_event_from_our_owner_in_our_namespace(self, nb, service, conn):
        session = nb.am_next_session()
        nb.am_add_event(conn, session, "hello")
        (event,) = service.requests("POST", "/events")
        body = event.body
        assert (body["sessionId"], body["role"], body["actorId"]) == (session, "USER", nb.AM_OWNER)
        assert body["namespace"] == nb.AM_NAMESPACE and body["content"] == [{"text": "hello"}]
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body["createdAt"])  # RFC 3339
        assert service.sessions[session]["ownerId"] == nb.AM_OWNER
        assert service.sessions[session]["namespace"] == nb.AM_NAMESPACE

    def test_a_session_that_opens_with_anything_but_a_user_event_is_refused_before_it_is_sent(
        self, nb, service, conn
    ):
        with pytest.raises(ValueError, match="USER"):
            nb.am_add_event(conn, nb.am_next_session(), "Hi, I am your assistant", role="ASSISTANT")
        assert service.log == [] and nb.ledger_live("am", "session", pending=True) == []

    def test_later_events_keep_the_actor_and_namespace_and_may_have_any_role(self, nb, service, conn):
        session = nb.am_next_session()
        nb.am_add_event(conn, session, "one")
        nb.am_add_event(conn, session, "two", role="ASSISTANT")
        assert [e["role"] for e in service.sessions[session]["events"]] == ["USER", "ASSISTANT"]
        assert all(c.body["actorId"] == nb.AM_OWNER for c in service.requests("POST", "/events"))
        assert all(c.body["namespace"] == nb.AM_NAMESPACE for c in service.requests("POST", "/events"))

    def test_the_session_is_ledgered_as_an_intent_before_the_request_and_created_after(
        self, nb, service, conn, monkeypatch
    ):
        session = nb.am_next_session()
        seen = []
        real = service.handle

        def spy(request):
            seen.append(nb.ledger_state("am", "session").get(session))
            return real(request)

        monkeypatch.setattr(nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(spy)))
        nb.am_add_event(conn, session, "hello")
        nb.am_add_event(conn, session, "again")
        assert seen == ["intent", "created"]

    def test_a_failed_first_event_leaves_an_intent_and_the_next_try_is_still_a_first_event(
        self, nb, service, conn, monkeypatch
    ):
        session = nb.am_next_session()
        monkeypatch.setattr(nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(500, json={"title": "Unexpected", "status": 500, "detail": "boom",
                                                "type": "/errors/unexpected-error"}))))  # fmt: skip
        with pytest.raises(nb.ServiceError):
            nb.am_add_event(conn, session, "hello")
        assert nb.ledger_live("am", "session") == []
        assert nb.ledger_live("am", "session", pending=True) == [session]
        with pytest.raises(ValueError, match="USER"):
            nb.am_add_event(conn, session, "hi", role="ASSISTANT")

    def test_a_session_of_another_owner_is_refused_by_the_choke_point(self, nb, service, conn):
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_call(conn, "POST", "/session-memory/events",
                       body=nb.am_event_body("ib-deadbeef-s1", "hello"))  # fmt: skip
        assert caught.value.kind == "refused" and service.log == []

    def test_the_id_the_service_gave_the_event_comes_back_with_the_call(self, nb, service, conn):
        session = nb.am_next_session()
        event_id, call = nb.am_add_event(conn, session, "hello")
        assert event_id == service.sessions[session]["events"][0]["eventId"]
        assert call.method == "POST" and call.status == 201

    @pytest.mark.parametrize("reply", [{}, {"event": {}}, {"event": "x"}, {"eventId": "a" * 32}, ["e"]])
    def test_a_reply_without_an_event_id_is_an_error_after_the_session_is_ledgered(
        self, nb, service, conn, monkeypatch, reply
    ):
        session = nb.am_next_session()
        monkeypatch.setattr(nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(201, json=reply))))  # fmt: skip
        with pytest.raises(nb.ServiceError, match="no event id"):
            nb.am_add_event(conn, session, "hello")
        assert nb.ledger_live("am", "session") == [session]  # the request went through


# ---- writing --------------------------------------------------------------------------------------
class TestWrite:
    def test_one_bulk_request_with_every_memory(self, nb, service, conn):
        result = nb.am_write_memories(conn)
        (create,) = service.requests("POST", "/long-term-memory")
        assert create.body == {"memories": nb.am_memories()}
        assert result["created"] == [m["id"] for m in nb.am_memories()] and result["errors"] == []
        assert set(service.memories) == {m["id"] for m in nb.am_memories()}
        assert result["call"].method == "POST"

    def test_every_id_is_ledgered_as_an_intent_before_the_request(self, nb, service, conn, monkeypatch):
        seen = []
        real = service.handle

        def spy(request):
            seen.append(sorted(nb.ledger_state("am", "memory").values()))
            return real(request)

        monkeypatch.setattr(nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(spy)))
        nb.am_write_memories(conn)
        assert seen == [["intent"] * len(nb.am_memories())]
        assert sorted(nb.ledger_live("am", "memory")) == sorted(m["id"] for m in nb.am_memories())

    def test_a_request_that_fails_still_leaves_its_ids_findable(self, nb, service, conn):
        service.status = "degraded"
        service.key = "another"  # every request now answers 401
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_write_memories(conn)
        assert caught.value.status == 401
        assert len(nb.ledger_live("am", "memory", pending=True)) == len(nb.am_memories())
        assert nb.ledger_live("am", "memory") == []

    def test_a_memory_the_service_refuses_is_reported_and_not_marked_created(self, nb, service, conn, monkeypatch):
        reply = {"created": [nb.am_memory_id(1)], "errors": [{"id": nb.am_memory_id(2), "error": "bad"}]}
        monkeypatch.setattr(nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(201, json=reply))))  # fmt: skip
        result = nb.am_write_memories(conn)
        assert result["errors"] == reply["errors"]
        assert nb.ledger_live("am", "memory") == [nb.am_memory_id(1)]

    def test_writing_twice_stores_the_same_ids_once(self, nb, service, conn):
        nb.am_write_memories(conn)
        nb.am_write_memories(conn)
        assert len(service.memories) == len(nb.am_memories())

    def test_the_listing_after_a_write_shows_the_five(self, nb, written):
        listed = nb.am_list(written, nb.am_filter())["items"]
        assert {m["id"] for m in listed} == {m["id"] for m in nb.am_memories()}


# ---- errors ------------------------------------------------------------------------------------------
class TestErrors:
    def test_a_424_keeps_the_type_and_detail_and_says_it_is_not_zero_results(self, nb, service, conn):
        service.search_error = (424, "invalid-model-credentials")
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_search(conn, "boots", nb.am_filter())
        assert caught.value.status == 424
        shown = nb.am_failed(caught.value).text
        assert "/errors/invalid-model-credentials" in shown and "the store could not answer" in shown
        assert "424" in shown and "0 results" in shown

    def test_an_unknown_424_type_is_printed_as_it_came(self, nb, service, conn):
        service.search_error = (424, "something-new-in-v2")
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_search(conn, "boots", nb.am_filter())
        assert "/errors/something-new-in-v2" in nb.am_failed(caught.value).text

    @pytest.mark.parametrize(
        "status, slug, words",
        [(401, "authentication-failed", "API key"), (404, "resource-not-found", "store ID"),
         (429, "too-many-requests", "Too many")],
    )  # fmt: skip
    def test_the_common_failures_say_what_to_do(self, nb, service, conn, status, slug, words):
        service.search_error = (status, slug)
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_search(conn, "boots", nb.am_filter())
        assert words in nb.am_failed(caught.value).text

    def test_an_error_callout_never_carries_the_key(self, nb, service, conn):
        service.search_error = (500, "unexpected-error")
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_search(conn, "boots", nb.am_filter())
        assert KEY not in nb.am_failed(caught.value).text and KEY not in str(caught.value)

    def test_other_errors_fall_back_to_the_plain_message(self, nb):
        assert "boom" in nb.am_failed(RuntimeError("boom")).text
        assert "no reply" in nb.am_failed(nb.ServiceError("am", None, "timeout", "no reply")).text

    def test_a_wrong_key_is_a_401_that_names_the_key_and_leaks_nothing(self, nb, service, conn):
        service.key = "a-different-key"
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_health(conn)
        assert caught.value.status == 401 and KEY not in str(caught.value)


# ---- health -----------------------------------------------------------------------------------------------
class TestHealth:
    def test_test_connection_is_one_get_and_shows_the_raw_features(self, nb, service, conn):
        facts = nb.am_health(conn)
        assert [(c.method, c.path) for c in service.log] == [("GET", f"/v1/stores/{STORE}/health")]
        assert facts["status"] == "healthy" and "long-term-memory" in facts["features"]
        shown = nb.am_describe(facts).text
        assert "Connected" in shown and "long-term-memory" in shown

    def test_a_degraded_store_is_a_warning_not_a_success(self, nb, service, conn):
        service.status = "degraded"
        assert "degraded" in nb.am_describe(nb.am_health(conn)).text

    @pytest.mark.parametrize(
        "reply",
        [
            httpx.Response(200, text="<html><body>Redis Cloud</body></html>", headers={"content-type": "text/html"}),
            httpx.Response(200, json=["healthy"]),
            httpx.Response(200, json={"features": {}}),
            httpx.Response(200, json={"status": None}),
            httpx.Response(200, content=b""),
        ],
    )  # fmt: skip
    def test_a_200_that_is_not_the_health_reply_is_not_a_connection(self, nb, monkeypatch, conn, reply):
        # A single-page app answers 200 to any path, so an endpoint copied from the wrong page lands here.
        monkeypatch.setattr(nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(lambda r: reply)))
        with pytest.raises(nb.ServiceError, match="not a JSON object with a status") as caught:
            nb.am_health(conn)
        assert caught.value.status == 200 and "Endpoint" in nb.am_failed(caught.value).text

    def test_features_that_are_not_an_object_are_left_out_rather_than_crashing(self, nb, monkeypatch, conn):
        reply = httpx.Response(200, json={"status": "healthy", "features": ["long-term-memory"]})
        monkeypatch.setattr(nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(lambda r: reply)))
        facts = nb.am_health(conn)
        assert facts["features"] == {} and "healthy" in nb.am_describe(facts).text

    def test_the_authorization_header_is_a_bearer_key_and_the_trace_hides_it(self, nb, service, conn):
        nb.am_health(conn)
        assert KEY not in json.dumps(nb.HTTP_TRACE[-1].request) and "Bearer" not in json.dumps(nb.HTTP_TRACE[-1].request)
        assert "$MEMORY_API_KEY" in nb.as_curl(nb.HTTP_TRACE[-1])

    def test_the_preflight_hook_reports_missing_settings_and_never_calls(self, nb, service):
        assert nb.am_test_connection() == (False, "missing MEMORY_API_BASE_URL, MEMORY_STORE_ID, MEMORY_API_KEY")
        assert service.log == []

    def test_the_preflight_hook_makes_one_read_only_call(self, nb, service, monkeypatch):
        monkeypatch.setenv("MEMORY_API_BASE_URL", BASE)
        monkeypatch.setenv("MEMORY_STORE_ID", STORE)
        monkeypatch.setenv("MEMORY_API_KEY", KEY)
        ok, detail = nb.am_test_connection()
        assert ok is True and "healthy" in detail and KEY not in detail
        assert [c.method for c in service.log] == ["GET"]
        service.status = "unavailable"
        assert nb.am_test_connection()[0] is False


# ---- the similarity lens ---------------------------------------------------------------------------------------
class TestSweep:
    def test_eleven_searches_from_zero_to_one_at_limit_100_all_scoped_to_us(self, nb, service, written):
        service.log.clear()
        sweep = nb.am_sweep(written, nb.am_wow_presets()[0]["query"])
        bodies = [c.body for c in service.requests("POST", "/search")]
        assert [b["similarityThreshold"] for b in bodies] == [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        assert {b["limit"] for b in bodies} == {100}
        assert all(b["filter"] == nb.am_filter() for b in bodies)
        assert len(sweep["steps"]) == 11 and len(sweep["calls"]) == 11 and sweep["stopped"] == ""

    def test_it_ticks_once_per_search_for_the_progress_bar(self, nb, written):
        ticks = []
        nb.am_sweep(written, "boots", lambda: ticks.append(1))
        assert len(ticks) == 11

    def test_each_band_brackets_the_similarity_the_fake_really_gave_the_memory(self, nb, written):
        query = nb.am_wow_presets()[1]["query"]
        sweep = nb.am_sweep(written, query)
        assert sweep["monotonic"] and set(sweep["bands"]) == {m["id"] for m in nb.am_memories()}
        for memory in nb.am_memories():
            top = sweep["bands"][memory["id"]]
            score = similarity(query, memory["text"])
            assert top <= score < top + 0.1 + 1e-9, (memory["id"], top, score)

    def test_the_on_topic_memory_has_the_highest_band(self, nb, written):
        preset = nb.am_wow_presets()[2]
        bands = nb.am_sweep(written, preset["query"])["bands"]
        assert max(bands, key=bands.get) == preset["target"]

    def test_the_slider_filters_the_saved_sweep_and_asks_nothing(self, nb, service, written):
        sweep = nb.am_sweep(written, nb.am_wow_presets()[0]["query"])
        sent = len(service.log)
        at_zero = nb.am_band_rows(sweep, 0.0)
        at_half = nb.am_band_rows(sweep, 0.5)
        at_one = nb.am_band_rows(sweep, 1.0)
        assert len(service.log) == sent
        assert [r["at the slider"] for r in at_zero] == ["returned"] * 5
        assert [r["at the slider"] for r in at_half].count("returned") == 1
        assert [r["at the slider"] for r in at_one].count("returned") == 0
        assert at_half[0]["similarity"].startswith("0.9") and "peanuts" in at_half[0]["memory"]

    def test_a_slider_value_off_the_grid_by_float_noise_still_lands_on_a_step(self, nb, written):
        sweep = nb.am_sweep(written, nb.am_wow_presets()[0]["query"])
        assert nb.am_band_rows(sweep, 0.30000000000000004) == nb.am_band_rows(sweep, 0.3)

    def test_the_table_counts_the_words_a_memory_shares_with_the_question(self, nb, written):
        rows = nb.am_band_rows(nb.am_sweep(written, "Which boots fit wide feet?"), 0.0)
        assert max(r["words in common"] for r in rows) == 3 and min(r["words in common"] for r in rows) == 0

    def test_live_agrees_with_the_table_on_a_hard_cut(self, nb, service, written):
        sweep = nb.am_sweep(written, nb.am_wow_presets()[0]["query"])
        for cut in (0.0, 0.5, 1.0):
            check = nb.am_recheck(written, sweep, cut)
            assert set(check["live"]) == set(check["predicted"]), cut
        assert service.requests("POST", "/search")[-1].body["similarityThreshold"] == 1.0

    def test_live_differs_when_the_store_has_changed_since_the_sweep(self, nb, service, written):
        sweep = nb.am_sweep(written, nb.am_wow_presets()[0]["query"])
        service.add_memory(nb.am_memory_id(9), "Dana hates serving peanuts in the kitchen", nb.AM_OWNER, nb.AM_NAMESPACE)
        check = nb.am_recheck(written, sweep, 0.5)
        assert set(check["live"]) != set(check["predicted"])

    def test_a_threshold_that_is_not_a_hard_cut_hides_the_bands(self, nb, service, written):
        service.wobble = nb.am_memory_id(2)
        sweep = nb.am_sweep(written, nb.am_wow_presets()[0]["query"])
        assert sweep["monotonic"] is False
        shown = nb.am_sweep_problem(sweep).text
        assert "hard cut" in shown

    def test_a_clean_sweep_has_no_problem(self, nb, written):
        assert nb.am_sweep_problem(nb.am_sweep(written, "boots")) is None

    def test_a_429_stops_the_sweep_at_once_and_derives_nothing(self, nb, service, written):
        service.rate_limit = 4
        service.log.clear()
        sweep = nb.am_sweep(written, "boots")
        assert len(sweep["steps"]) == 4 and "429" in sweep["stopped"]
        assert len(service.requests("POST", "/search")) == 5  # the four that worked, and the one that did not
        problem = nb.am_sweep_problem(sweep).text
        assert "stopped" in problem and "Nothing is derived" in problem

    def test_any_other_failure_is_raised_for_the_cell_to_show(self, nb, service, written):
        service.search_error = (424, "database-out-of-memory")
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_sweep(written, "boots")
        assert caught.value.status == 424

    def test_a_store_with_no_memories_says_so(self, nb, conn):
        sweep = nb.am_sweep(conn, "boots")
        assert sweep["memories"] == {} and "no memories" in nb.am_sweep_problem(sweep).text

    def test_our_sweep_never_returns_somebody_elses_memory(self, nb, service, written):
        live_demo(service)
        service.add_memory("demo-3", "Dana likes peanuts", "CUST-DEMO-003", nb.AM_NAMESPACE)
        sweep = nb.am_sweep(written, "peanuts")
        assert set(sweep["memories"]) == {m["id"] for m in nb.am_memories()}


# ---- the scope lens ------------------------------------------------------------------------------------------
class TestScope:
    def test_the_same_question_finds_n_then_none_then_none(self, nb, service, written):
        found = nb.am_scope_search(written, "peanuts")
        assert [c["count"] for c in found] == [5, 0, 0]
        assert [c["scope"] for c in found] == [
            "this run's owner", "an owner that does not exist", "a namespace that does not exist",
        ]  # fmt: skip

    def test_every_case_is_a_read_only_search_with_an_owner_and_a_namespace(self, nb, service, written):
        service.log.clear()
        nb.am_scope_search(written, "peanuts")
        assert len(service.log) == 3 and service.writes() == []
        for call in service.log:
            assert call.path.endswith("/long-term-memory/search")
            assert call.body["text"] == "peanuts" and call.body["similarityThreshold"] == 0.0
            assert call.body["filter"]["ownerId"] and call.body["filter"]["namespace"]

    def test_the_made_up_owner_cannot_be_anyones(self, nb):
        nobody = nb.am_nobody()
        assert re.fullmatch(r"[A-Za-z0-9-]{1,64}", nobody)  # a legal id, so the service does not object
        assert not nobody.startswith("ib-") and not re.fullmatch(r"(?:CUST|P)-?\w*\d+", nobody, re.I)
        assert "nobody" in nobody

    def test_the_wrong_owner_and_the_wrong_namespace_each_change_only_one_thing(self, nb, service, written):
        service.log.clear()
        nb.am_scope_search(written, "peanuts")
        mine, fake_owner, other_ns = (c.body["filter"] for c in service.log)
        assert mine == nb.am_filter()
        assert fake_owner == {"ownerId": {"eq": nb.am_nobody()}, "namespace": {"eq": nb.AM_NAMESPACE}}
        assert other_ns["ownerId"] == mine["ownerId"] and other_ns["namespace"] != mine["namespace"]

    def test_a_namespace_eq_misses_a_memory_with_no_namespace_and_ne_finds_it(self, nb, service, conn):
        service.add_memory("m-none", "no namespace at all", nb.AM_OWNER)
        eq = nb.am_search(conn, "", nb.am_filter())["items"]
        not_x = nb.am_filter(namespace=None)
        not_x["namespace"] = {"ne": "elsewhere"}
        ne = nb.am_search(conn, "", not_x)["items"]
        owner_only = nb.am_search(conn, "", nb.am_filter(namespace=None))["items"]
        assert eq == [] and [m["id"] for m in ne] == ["m-none"] == [m["id"] for m in owner_only]


# ---- the memory inspector ------------------------------------------------------------------------------------
def fresh_chat():
    return {"session": None, "turn": None}


class TestTurn:
    def test_a_send_adds_a_user_event_reads_the_session_back_and_searches_by_rank(
        self, nb, service, written
    ):
        service.log.clear()
        chat = fresh_chat()
        turn = nb.am_take_turn(written, chat, nb.am_wow_presets()[0], 3)
        assert [(c.method, c.path.rsplit("/", 2)[-2:]) for c in service.log] == [
            ("POST", ["session-memory", "events"]),
            ("GET", ["session-memory", chat["session"]]),
            ("POST", ["long-term-memory", "search"]),
        ]
        event, _, search = (c.body for c in service.log)
        assert event["role"] == "USER" and event["actorId"] == nb.AM_OWNER and event["namespace"] == nb.AM_NAMESPACE
        assert event["content"] == [{"text": nb.am_wow_presets()[0]["query"]}]
        assert search["similarityThreshold"] == 0.0 and search["limit"] == 3
        assert search["text"] == nb.am_wow_presets()[0]["query"] and search["filter"] == nb.am_filter()
        assert turn["session"] == chat["session"] == nb.am_session_id(1) and len(turn["calls"]) == 3
        assert turn["event_id"] == service.sessions[chat["session"]]["events"][0]["eventId"]

    def test_the_target_comes_back_first_with_no_word_in_common(self, nb, written):
        for preset in nb.am_wow_presets():
            turn = nb.am_take_turn(written, fresh_chat(), preset, 3)
            rows = nb.am_recall_rows(turn)
            assert len(rows) == 3
            assert rows[0]["recalled"] == "#1 of 3 · 0 words in common", preset["label"]
            assert rows[0]["answer"] == "★" and [r["answer"] for r in rows[1:]] == ["", ""]
            assert "came back #1 of 3" in nb.am_wow_summary(turn)

    def test_a_message_of_your_own_stars_nothing_and_counts_its_shared_words(self, nb, written):
        own = nb.am_wow_preset("Type your own", "Which boots fit wide feet?")
        turn = nb.am_take_turn(written, fresh_chat(), own, 3)
        rows = nb.am_recall_rows(turn)
        assert "answer" not in rows[0] and rows[0]["recalled"].startswith("#1 of 3 · 3 words in common")
        assert nb.am_wow_summary(turn) == ""

    def test_one_word_in_common_is_singular(self, nb, written):
        own = nb.am_wow_preset("Type your own", "Tell me about peanuts")
        assert "· 1 word in common" in nb.am_recall_rows(nb.am_take_turn(written, fresh_chat(), own, 1))[0]["recalled"]

    def test_the_limit_is_the_only_cut(self, nb, written):
        for limit in (1, 2, 5):
            turn = nb.am_take_turn(written, fresh_chat(), nb.am_wow_presets()[0], limit)
            assert len(turn["recalled"]) == limit
        rows = nb.am_recall_rows(turn)
        assert rows[-1]["recalled"].startswith("#5 of 5")

    def test_a_target_outside_the_top_n_says_so(self, nb, written):
        preset = nb.am_wow_presets()[2]
        turn = {"preset": preset, "limit": 1, "recalled": [{"id": nb.am_memory_id(5), "text": "x"}]}
        assert "did not make the top 1" in nb.am_wow_summary(turn)

    def test_a_second_send_joins_the_same_session(self, nb, service, written):
        chat = fresh_chat()
        first = nb.am_take_turn(written, chat, nb.am_wow_presets()[0], 3)
        second = nb.am_take_turn(written, chat, nb.am_wow_presets()[1], 3)
        assert first["session"] == second["session"]
        assert [nb.am_event_text(e) for e in second["events"]] == [
            nb.am_wow_presets()[0]["query"], nb.am_wow_presets()[1]["query"],
        ]  # fmt: skip
        assert len(service.requests("POST", "/events")) == 2
        assert nb.ledger_live("am", "session") == [chat["session"]]

    def test_a_new_session_forgets_what_was_said_and_not_what_was_learned(self, nb, service, written):
        chat = fresh_chat()
        first = nb.am_take_turn(written, chat, nb.am_wow_presets()[0], 3)
        chat.update(session=None, turn=None)  # what the New session button does
        again = nb.am_take_turn(written, chat, nb.am_wow_presets()[0], 3)
        assert again["session"] == nb.am_session_id(2) != first["session"]
        assert len(again["events"]) == 1  # the earlier message is not in the new session ...
        assert [m["id"] for m in again["recalled"]] == [m["id"] for m in first["recalled"]]  # ... the memories are
        assert nb.ledger_live("am", "session") == [nb.am_session_id(1), nb.am_session_id(2)]

    def test_a_failed_search_is_raised_after_the_event_went_in(self, nb, service, written):
        service.search_error = (424, "resource-unavailable")
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_take_turn(written, fresh_chat(), nb.am_wow_presets()[0], 3)
        assert caught.value.status == 424 and len(service.requests("POST", "/events")) == 1

    def test_the_event_rows_are_what_the_service_returned(self, nb, written):
        turn = nb.am_take_turn(written, fresh_chat(), nb.am_wow_presets()[1], 3)
        assert nb.am_event_rows(turn["events"]) == [
            {"#": 1, "role": "USER", "actor": nb.AM_OWNER, "text": nb.am_wow_presets()[1]["query"]}
        ]


class TestPromptAssembly:
    ALL = {"summary": True, "events": True, "memories": True}

    def turn(self, nb, service, written, sends=2):
        chat = fresh_chat()
        for n in range(sends):
            turn = nb.am_take_turn(written, chat, nb.am_wow_presets()[n], 2)
        return turn

    def test_all_tiers_show_earlier_messages_and_recalled_memories_but_not_the_question(
        self, nb, service, written
    ):
        turn = self.turn(nb, service, written)
        context = nb.am_context(turn, self.ALL)
        assert context.startswith("Retrieved memory context:\nShort-term memory:\nUSER: ")
        assert nb.am_wow_presets()[0]["query"] in context
        assert nb.am_wow_presets()[1]["query"] not in context  # the question is not context
        assert "Long-term memory:\n- " in context and "toes" not in context
        assert len([line for line in context.splitlines() if line.startswith("- ")]) == 2

    def test_each_tier_switches_off_on_its_own(self, nb, service, written):
        turn = self.turn(nb, service, written)
        no_events = nb.am_context(turn, {**self.ALL, "events": False})
        assert "Short-term" not in no_events and "Long-term memory:" in no_events
        no_memories = nb.am_context(turn, {**self.ALL, "memories": False})
        assert "Long-term" not in no_memories and "Short-term memory:" in no_memories
        assert nb.am_context(turn, {"summary": False, "events": False, "memories": False}).startswith("(nothing")

    def test_the_first_message_has_no_earlier_messages(self, nb, service, written):
        context = nb.am_context(self.turn(nb, service, written, sends=1), self.ALL)
        assert "(no earlier messages)" in context

    def test_a_summary_appears_only_when_the_service_made_one_and_the_tier_is_on(self, nb, service, written):
        turn = self.turn(nb, service, written)
        assert "Session summary" not in nb.am_context(turn, self.ALL)
        turn["summary"] = {"text": "Dana asked about boots."}
        assert "Session summary:\nDana asked about boots." in nb.am_context(turn, self.ALL)
        assert "Session summary" not in nb.am_context(turn, {**self.ALL, "summary": False})

    def test_the_message_just_sent_is_left_out_by_its_id_wherever_the_service_put_it(
        self, nb, service, written
    ):
        turn = self.turn(nb, service, written)
        turn["events"].reverse()  # a service that lists the newest event first
        context = nb.am_context(turn, self.ALL)
        assert nb.am_wow_presets()[0]["query"] in context
        assert nb.am_wow_presets()[1]["query"] not in context

    def test_an_event_the_read_back_does_not_hold_yet_cannot_take_an_earlier_one_with_it(
        self, nb, service, written
    ):
        turn = self.turn(nb, service, written)
        turn["events"] = [e for e in turn["events"] if e["eventId"] != turn["event_id"]]
        assert nb.am_wow_presets()[0]["query"] in nb.am_context(turn, self.ALL)

    def test_only_the_last_six_earlier_messages_are_shown(self, nb, service, written):
        chat, ask = fresh_chat(), nb.am_wow_preset("Type your own", "one")
        for n in range(9):
            turn = nb.am_take_turn(written, chat, {**ask, "query": f"message number {n}"}, 1)
        earlier = [l for l in nb.am_context(turn, self.ALL).splitlines() if l.startswith("USER:")]
        assert len(earlier) == 6 and earlier[-1].endswith("number 7") and earlier[0].endswith("number 2")


# ---- orphans -----------------------------------------------------------------------------------------------------
class TestOrphans:
    def test_an_old_run_in_our_namespace_is_offered_with_its_sessions(self, nb, service, written):
        orphan_run(service, hours_old=5, memories=2, sessions=2)
        found = nb.am_orphan_sweep(written)
        assert sorted(m["id"] for m in found["memories"]) == ["ib-deadbeef-m1", "ib-deadbeef-m2"]
        assert sorted(found["sessions"]) == ["ib-deadbeef-s1", "ib-deadbeef-s2"]
        assert found["total"] == 2 and found["rows"][0]["owner"] == "ib-deadbeef"
        assert found["rows"][0]["offered"] == 2 and found["rows"][0]["sessions offered"] == 2

    def test_a_session_of_that_owner_that_is_not_named_for_the_run_is_left_alone(self, nb, service, written):
        orphan_run(service, sessions=1)
        service.add_session("some-other-thing", "ib-deadbeef", nb.AM_NAMESPACE)
        found = nb.am_orphan_sweep(written)
        assert found["sessions"] == ["ib-deadbeef-s1"] and found["rows"][0]["sessions offered"] == 1

    def test_a_young_run_may_be_a_colleagues_and_is_not_offered(self, nb, service, written):
        orphan_run(service, "cafef00d", hours_old=0.5, sessions=1)
        found = nb.am_orphan_sweep(written)
        assert found["memories"] == [] and found["sessions"] == []
        assert found["rows"][0]["memories"] == 2 and found["rows"][0]["offered"] == 0

    def test_an_owner_that_only_looks_like_ours_is_counted_and_never_offered(self, nb, service, written):
        service.add_memory("x-1", "old", "ib-legacy", nb.AM_NAMESPACE, hours_old=900)
        found = nb.am_orphan_sweep(written)
        assert found["total"] == 1 and found["memories"] == []

    def test_this_runs_memories_and_other_apps_are_not_listed(self, nb, service, written):
        live_demo(service)
        service.add_memory("stranger", "in our namespace but not an ib- owner", "alice", nb.AM_NAMESPACE, hours_old=900)
        found = nb.am_orphan_sweep(written)
        assert found["total"] == 0 and found["rows"] == []

    def test_sessions_are_listed_by_owner_and_never_with_include_all(self, nb, service, written):
        orphan_run(service, sessions=1)
        service.log.clear()
        nb.am_orphan_sweep(written)
        listing = service.requests("GET", "/session-memory")
        assert [c.query.get("filterOwnerId") for c in listing] == ["ib-deadbeef"]
        assert all("includeAll" not in c.query for c in listing)

    def test_the_sweep_is_the_one_search_not_scoped_to_our_owner_and_is_scoped_to_the_namespace(
        self, nb, service, written
    ):
        service.log.clear()
        nb.am_orphan_sweep(written)
        (search,) = service.requests("POST", "/search")
        assert search.body["filter"] == {"namespace": {"eq": nb.AM_NAMESPACE}} and search.body["text"] == ""

    def test_nothing_is_deleted_without_the_typed_confirmation(self, nb, service, written):
        orphan_run(service)
        found = nb.am_orphan_sweep(written)
        service.log.clear()
        for phrase in ("", "yes", "Delete Orphans"):
            with pytest.raises(ValueError, match="to confirm"):
                nb.am_delete_orphans(written, found, phrase)
        assert service.log == [] and "ib-deadbeef-m1" in service.memories

    def test_with_the_confirmation_those_memories_and_sessions_go_and_nothing_else(self, nb, service, written):
        live_demo(service)
        orphan_run(service, hours_old=5)
        orphan_run(service, "cafef00d", hours_old=0.2)
        mine_before = {m["id"] for m in nb.am_memories()}
        found = nb.am_orphan_sweep(written)
        done = nb.am_delete_orphans(written, found, nb.ORPHAN_CONFIRM)
        assert done == {"memories": 2, "sessions": 1, "refused": 0}
        assert "ib-deadbeef-m1" not in service.memories and "ib-deadbeef-s1" not in service.sessions
        assert {"demo-1", "demo-2", "ib-cafef00d-m1", "ib-cafef00d-m2"} <= set(service.memories)
        assert "ib-cafef00d-s1" in service.sessions
        assert mine_before <= set(service.memories)

    def test_the_choke_point_still_refuses_an_unconfirmed_id(self, nb, service, written):
        orphan_run(service)
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_delete_memories(written, ["ib-deadbeef-m1"])
        assert caught.value.kind == "refused" and "ib-deadbeef-m1" in service.memories

    def test_a_listing_error_is_raised_for_the_panel_to_show(self, nb, service, written):
        service.search_error = (424, "resource-unavailable")
        with pytest.raises(nb.ServiceError):
            nb.am_orphan_sweep(written)


# ---- cleanup -------------------------------------------------------------------------------------------------------
class TestCleanup:
    def dirty(self, nb, service, conn):
        """A run that wrote memories, held a session and got two extracted memories."""
        nb.am_write_memories(conn)
        nb.am_add_event(conn, nb.am_next_session(), "I run before sunrise")
        nb.am_add_event(conn, nb.am_next_session(), "and I like tents")
        service.extract()

    def test_it_removes_the_sessions_and_every_memory_the_owner_has_and_reports_them(
        self, nb, service, conn
    ):
        live_demo(service)
        self.dirty(nb, service, conn)
        assert len(service.memories) == 5 + 2 + 2
        done = nb.am_cleanup(conn)
        assert done == {
            "ran": True, "sessions": 2, "sessions_left": 0, "memories": 7, "extracted": 2,
            "left": 0, "refused": 0,
        }  # fmt: skip
        assert set(service.memories) == {"demo-1", "demo-2"} and service.sessions == {}
        assert nb.ledger_live("am", "session", pending=True) == []
        assert nb.ledger_live("am", "memory", pending=True) == []

    def test_it_lists_by_owner_alone_so_a_memory_with_no_namespace_is_found(self, nb, service, conn):
        nb.am_write_memories(conn)
        service.add_memory("srv-1", "extracted from a session that had no namespace", nb.AM_OWNER)
        service.log.clear()
        assert nb.am_cleanup(conn)["extracted"] == 1
        listing = [c.body for c in service.requests("POST", "/search")]
        assert listing and all(b["filter"] == {"ownerId": {"eq": nb.AM_OWNER}} for b in listing)
        assert all(b["text"] == "" and "similarityThreshold" not in b for b in listing)
        assert "srv-1" not in service.memories

    def test_the_order_is_sessions_then_list_then_delete_then_list_again(self, nb, service, conn):
        self.dirty(nb, service, conn)
        service.log.clear()
        nb.am_cleanup(conn)
        steps = []
        for call in service.log:
            what = "a session" if "/session-memory/" in call.path else (
                "a search" if call.path.endswith("/search") else "memories"
            )
            if not steps or steps[-1] != (call.method, what):
                steps.append((call.method, what))
        assert steps == [
            ("DELETE", "a session"), ("POST", "a search"), ("DELETE", "memories"), ("POST", "a search"),
        ]  # fmt: skip

    def test_a_foreign_item_in_the_listing_stops_it_before_any_memory_is_deleted(
        self, nb, service, conn
    ):
        live_demo(service)
        self.dirty(nb, service, conn)
        service.ignore_filters = True  # the store lists everybody's memories, whatever it was asked
        with pytest.raises(ValueError, match="not owned by.*No memory was deleted"):
            nb.am_cleanup(conn)
        assert service.requests("DELETE", "/long-term-memory") == []
        assert {"demo-1", "demo-2"} <= set(service.memories) and len(service.memories) == 9
        assert service.sessions == {}  # the sessions went first, which is why the message says memory

    def test_a_session_the_service_will_not_delete_does_not_hold_up_the_memories(
        self, nb, service, conn, monkeypatch
    ):
        self.dirty(nb, service, conn)
        real = service.handle

        def refuse_sessions(request):
            if request.method == "DELETE" and "/session-memory/" in request.url.path:
                return service.problem(500, "unexpected-error", "boom")
            return real(request)

        monkeypatch.setattr(nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(refuse_sessions)))
        done = nb.am_cleanup(conn)
        assert (done["sessions"], done["sessions_left"], done["memories"]) == (0, 2, 7)
        assert service.memories == {} and len(service.sessions) == 2
        assert sorted(nb.ledger_live("am", "session")) == sorted(service.sessions)  # kept, to try again
        report = nb.am_cleanup_report(done)
        assert "**2** sessions could not be deleted" in report and "expire on their own" in report
        monkeypatch.setattr(nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(real)))
        again = nb.am_cleanup(conn)
        assert (again["sessions"], again["sessions_left"]) == (2, 0) and service.sessions == {}
        assert nb.ledger_live("am", "session", pending=True) == []

    def test_a_key_the_service_refuses_is_reported_and_is_not_counted_as_sessions_left(
        self, nb, service, conn
    ):
        self.dirty(nb, service, conn)
        service.key = "another"  # every request now answers 401
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_cleanup(conn)
        assert caught.value.status == 401 and len(service.memories) == 7

    def test_a_session_and_memories_only_marked_intent_are_cleaned_up_too(self, nb, service, conn):
        # A first event or a create that timed out: the ledger holds the intent, and the service
        # may hold the thing, unlisted for now.
        session = nb.am_next_session()
        nb.ledger_add("am", "session", session, "intent")
        service.add_session(session, nb.AM_OWNER, nb.AM_NAMESPACE)
        ids = [nb.am_memory_id(1), nb.am_memory_id(2)]
        for ident in ids:
            nb.ledger_add("am", "memory", ident, "intent")
            service.add_memory(ident, "written just before the timeout", nb.AM_OWNER, nb.AM_NAMESPACE)
        service.unlisted = set(ids)
        done = nb.am_cleanup(conn)
        assert (done["sessions"], done["memories"], done["left"]) == (1, 2, 0)
        assert service.sessions == {} and service.memories == {}
        assert nb.ledger_live("am", "session", pending=True) == []
        assert nb.ledger_live("am", "memory", pending=True) == []

    def test_one_foreign_item_among_ours_is_enough(self, nb, service, conn):
        self.dirty(nb, service, conn)
        service.add_memory("srv-x", "somebody else's", "CUST-DEMO-001", nb.AM_NAMESPACE)
        service.ignore_filters = True
        with pytest.raises(ValueError):
            nb.am_cleanup(conn)
        assert service.deleted_memory_ids() == [] and "srv-x" in service.memories

    def test_the_choke_point_stops_a_foreign_id_even_if_a_bug_slipped_it_past_the_check(
        self, nb, service, conn
    ):
        self.dirty(nb, service, conn)
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_delete_memories(conn, ["demo-1"])
        assert caught.value.kind == "refused" and service.deleted_memory_ids() == []

    def test_at_most_a_hundred_ids_go_in_one_request(self, nb, service, conn):
        nb.am_write_memories(conn)
        for n in range(250):
            service.add_memory(f"srv-{n:03}", f"extracted {n}", nb.AM_OWNER, nb.AM_NAMESPACE)
        assert nb.am_cleanup(conn)["memories"] == 255
        sizes = [len(c.body["memoryIds"]) for c in service.requests("DELETE", "/long-term-memory")]
        assert sizes == [100, 100, 55] and service.memories == {}

    def test_it_is_safe_to_run_twice_and_the_second_time_finds_nothing(self, nb, service, conn):
        self.dirty(nb, service, conn)
        nb.am_cleanup(conn)
        service.log.clear()
        again = nb.am_cleanup(conn)
        assert again["ran"] and again["memories"] == again["sessions"] == again["left"] == 0
        assert service.requests("DELETE", "/long-term-memory") == []

    def test_re_sweep_catches_what_extraction_added_after_cleanup(self, nb, service, conn):
        self.dirty(nb, service, conn)
        nb.am_cleanup(conn)
        service.add_memory("late-1", "extracted ten minutes later", nb.AM_OWNER, nb.AM_NAMESPACE)
        done = nb.am_cleanup(conn)
        assert done["memories"] == 1 and done["extracted"] == 1 and "late-1" not in service.memories

    def test_a_run_that_wrote_nothing_asks_the_service_nothing(self, nb, service, conn):
        assert nb.am_cleanup(conn)["ran"] is False and service.log == []
        assert "written nothing" in nb.am_cleanup_report(nb.am_cleanup(conn))

    def test_a_session_that_expired_already_is_fine(self, nb, service, conn):
        self.dirty(nb, service, conn)
        service.sessions.clear()  # the store's short-term TTL got there first
        assert nb.am_cleanup(conn)["sessions"] == 2

    def test_what_the_service_still_lists_is_reported_and_stays_in_the_ledger(self, nb, service, conn):
        nb.am_write_memories(conn)
        service.stubborn = {nb.am_memory_id(1)}
        done = nb.am_cleanup(conn)
        assert done["left"] == 1 and "still listed" in nb.am_cleanup_report(done)
        assert nb.ledger_live("am", "memory", pending=True) == [nb.am_memory_id(1)]

    def test_the_report_names_the_counts_and_the_ten_minute_window(self, nb, service, conn):
        self.dirty(nb, service, conn)
        report = nb.am_cleanup_report(nb.am_cleanup(conn))
        assert "**2** sessions" in report and "**7** memories" in report and "**2** of them made by extraction" in report
        assert "10 minutes" in report and "Clean up again" in report

    def test_it_needs_only_the_ledger_and_the_settings_so_a_new_kernel_can_do_it(
        self, nb, service, conn, load_fresh
    ):
        fresh = load_fresh(IRIS_BASICS_RUN="0123abcd")
        found, _ = fresh.am_settings(BASE, STORE, fresh.Secret(KEY))
        service.add_memory("ib-0123abcd-m1", "written by a kernel that has gone", "ib-0123abcd", fresh.AM_NAMESPACE)
        fresh.ledger_add("am", "memory", "ib-0123abcd-m1")
        fresh.HTTP_CLIENT = httpx.Client(transport=httpx.MockTransport(service.handle))
        assert fresh.am_cleanup(found)["memories"] == 1 and "ib-0123abcd-m1" not in service.memories


# ---- extraction -----------------------------------------------------------------------------------------------------
class TestExtraction:
    def test_the_kickoff_posts_a_short_conversation_to_a_new_session(self, nb, service, conn):
        sent = nb.am_extract_kickoff(conn)
        events = service.sessions[sent["session"]]["events"]
        assert sent["events"] == len(events) == len(nb.am_extract_conversation()) == 4
        assert [e["role"] for e in events] == ["USER"] * 4
        assert service.sessions[sent["session"]]["ownerId"] == nb.AM_OWNER
        assert service.sessions[sent["session"]]["namespace"] == nb.AM_NAMESPACE
        assert sent["call"].method == "POST" and nb.ledger_live("am", "session") == [sent["session"]]

    def test_a_check_before_extraction_finds_nothing_and_after_it_finds_the_services_own_ids(
        self, nb, service, written
    ):
        nb.am_extract_kickoff(written)
        assert nb.am_extracted(written)["items"] == []
        made = service.extract()
        found = nb.am_extracted(written)["items"]
        assert [m["id"] for m in found] == [made[0]["id"]]
        assert found[0]["ownerId"] == nb.AM_OWNER and found[0]["namespace"] == nb.AM_NAMESPACE
        assert nb.am_extracted_rows(found)[0]["type"] == "episodic"

    def test_the_memories_we_wrote_are_never_shown_as_extracted(self, nb, service, written):
        assert nb.am_extracted(written)["items"] == []

    def test_a_check_is_a_read_and_scoped_to_our_owner_and_namespace(self, nb, service, written):
        service.log.clear()
        nb.am_extracted(written)
        assert service.writes() == [] and service.log[0].body["filter"] == nb.am_filter()

    def test_the_conversation_is_made_up_and_says_nothing_about_a_real_person(self, nb):
        assert all(len(t) < 100 for t in nb.am_extract_conversation())
        assert not any(re.search(r"@|\d{3}-\d{4}|\bssn\b", t, re.I) for t in nb.am_extract_conversation())


# ---- reading a session ------------------------------------------------------------------------------------------------
class TestSessionRead:
    def test_the_summarised_events_are_asked_for_only_when_the_switch_is_on(self, nb, service, conn):
        session = nb.am_next_session()
        nb.am_add_event(conn, session, "hello")
        service.log.clear()
        nb.am_get_session(conn, session)
        nb.am_get_session(conn, session, True)
        assert [c.query for c in service.log] == [{}, {"includeSummarisedEvents": "true"}]
        assert all(c.method == "GET" and c.path.endswith(f"/session-memory/{session}") for c in service.log)

    def test_the_reply_and_the_call_come_back_and_a_missing_session_is_an_error(self, nb, service, conn):
        session = nb.am_next_session()
        nb.am_add_event(conn, session, "hello")
        read = nb.am_get_session(conn, session)
        assert read["session"]["ownerId"] == nb.AM_OWNER
        assert nb.am_event_text(read["session"]["events"][0]) == "hello" and read["call"].method == "GET"
        with pytest.raises(nb.ServiceError) as caught:
            nb.am_get_session(conn, "ib-deadbeef-s9")
        assert caught.value.status == 404


# ---- under the hood --------------------------------------------------------------------------------------------------
class TestUnderTheHood:
    @pytest.fixture
    def database(self, nb, monkeypatch):
        holder = SimpleNamespace(server=None)
        monkeypatch.setattr(
            nb.redis.Redis, "execute_command", lambda self, *a, **k: holder.server.handle(a)
        )
        return holder

    @pytest.fixture
    def client(self, nb, database):
        return nb.GuardedTracedRedis.from_url("redis://default:fake-pw-1234@db.example:6379", protocol=2)

    def test_it_reads_the_index_and_our_first_memory_and_only_reads(self, nb, database, client, conn):
        database.server = FakeIndexServer(STORE, nb.am_memories())
        hood = nb.am_hood(client, conn)
        assert hood["index"] == f"memory:{STORE}:ltm" and hood["docs"] == "5"
        assert hood["vector"] == {"algorithm": "HNSW", "data_type": "FLOAT32", "dim": 3072, "distance_metric": "COSINE"}
        assert hood["memory"]["text"] == nb.am_memories()[0]["text"]
        assert hood["memory"]["owner_id"] == nb.AM_OWNER and hood["memory"]["topics"] == "food,allergy"
        assert hood["vector_bytes"] == 3072 * 4
        assert set(database.server.names()) == {"FT.INFO", "FT.SEARCH", "HMGET", "HSTRLEN"}
        assert not {"HSET", "DEL", "UNLINK", "FT.CREATE", "FT.DROPINDEX", "EXPIRE"} & set(database.server.names())

    def test_the_view_shows_the_dimension_and_the_byte_count(self, nb, database, client, conn):
        database.server = FakeIndexServer(STORE, nb.am_memories(), dim=1536)
        shown = nb.am_hood_view(nb.am_hood(client, conn)).text
        assert "DIM 1536" in shown and "6144" in shown and "1536" in shown and "redis-cli" in shown

    def test_the_commands_are_shown_as_redis_cli(self, nb, database, client, conn):
        database.server = FakeIndexServer(STORE, nb.am_memories())
        hood = nb.am_hood(client, conn)
        assert hood["commands"][0] == f"FT.INFO memory:{STORE}:ltm"
        assert hood["commands"][1].startswith(f"FT.SEARCH memory:{STORE}:ltm")

    @pytest.mark.parametrize("wording", MISSING_INDEX)  # Redis 8.6.2 says "Index not found"
    def test_a_database_without_the_index_is_a_soft_message(
        self, nb, database, client, conn, wording
    ):
        database.server = FakeIndexServer("some-other-store", nb.am_memories())
        database.server.wording = wording
        hood = nb.am_hood(client, conn)
        assert hood["missing"] is True
        assert "not the one behind this service" in nb.am_hood_view(hood).text

    def test_a_memory_that_is_not_in_the_index_yet_says_to_write_first(self, nb, database, client, conn):
        database.server = FakeIndexServer(STORE, [])
        hood = nb.am_hood(client, conn)
        assert hood["memory"] is None and "write the memories first" in nb.am_hood_view(hood).text

    def test_another_error_is_raised_not_swallowed(self, nb, database, client, conn):
        def boom(*args):
            raise redis.ResponseError("ERR something else")

        database.server = SimpleNamespace(handle=boom)
        with pytest.raises(redis.ResponseError, match="something else"):
            nb.am_hood(client, conn)


# ---- the SDK snippet ---------------------------------------------------------------------------------------------------
class TestSdkSnippet:
    def test_the_snippet_is_the_same_search_written_with_the_typed_models(self, nb):
        text = nb.am_sdk_snippet("What should the kitchen avoid serving me?")
        assert text.startswith("```python") and text.endswith("```")
        assert "redis-agent-memory==0.4.1" in text and "search_long_term_memory" in text
        for needed in ("OwnerIDFilter", "NamespaceFilter", nb.AM_OWNER, nb.AM_NAMESPACE, "kitchen avoid"):
            assert needed in text
        assert "dropped without a word" in text  # the reason for the typed models

    def test_the_sdk_is_never_imported_by_the_notebook(self, nb_file):
        for node in ast.walk(ast.parse(nb_file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith("redis_agent_memory") for a in node.names)
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("redis_agent_memory")

    def test_the_header_does_not_list_it_as_a_dependency(self, nb_file):
        header = nb_file.read_text(encoding="utf-8").split("# ///", 2)[1]
        assert "redis-agent-memory" not in header


# ---- the notebook's shape --------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def column(nb_file):
    """Column 3's items in file order: (kind, node)."""
    source = nb_file.read_text(encoding="utf-8")
    return [(i.kind, i.node) for i in nbast.column_items(source, 3)]


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
        and isinstance(stmt.targets[0], ast.Name)
        and isinstance(stmt.value, ast.Call)
        and nbast.call_name(stmt.value) in kinds
    }


class TestShape:
    def test_the_column_runs_in_the_planned_order(self, cells):
        # one marker global per stage: config, connect, orphans, write, ready, then each core
        # cell as controls / fetch / render, the wow, cleanup, and the deep dive
        markers = [
            "am_base", "am_conn", "am_orphan_drop", "am_extract_start", "am_seed", "am_ready",
            "am_lens_query", "am_swept", "am_lens_cut", "am_scoped",
            "am_wow_case", "am_chat", "am_turn", "am_cleanup_btn",
            "am_deep", "am_dd_session", "am_dd_read", "am_extract_check", "am_hood_url",
        ]  # fmt: skip
        at = [next(i for i, c in enumerate(cells) if m in nbast.defs(c)) for m in markers]
        assert at == sorted(at) and len(set(at)) == len(markers)
        for controls, fetch in (
            ("am_lens_query", "am_swept"), ("am_dd_session", "am_dd_read"),
        ):  # fmt: skip
            assert at[markers.index(fetch)] > at[markers.index(controls)]

    def test_the_helpers_come_last_all_hidden_and_all_prefixed(self, column, nb_file):
        kinds = [kind for kind, _ in column]
        assert set(kinds[kinds.index("function") :]) == {"function"}
        functions = nbast.column_items(nb_file.read_text(encoding="utf-8"), 3)
        assert all(i.hide_code is True for i in functions if i.kind == "function")
        assert all(node.name.startswith("am_") for kind, node in column if kind == "function")

    def test_every_global_a_cell_returns_has_the_column_prefix(self, cells):
        for cell in cells:
            assert all(n.startswith("am_") for n in nbast.defs(cell)), nbast.defs(cell)

    def test_the_cells_that_need_a_connection_or_the_memories_stop_without_them(self, cells):
        first = [ast.unparse(c.body[0]) for c in cells if c.body]
        # orphans, the extraction kick-off, write, deep dive
        assert first.count("mo.stop(am_conn is None, None)") == 4
        assert first.count("mo.stop(not am_ready, None)") == 2  # the search and the wow controls

    def test_only_the_demo_and_cleanup_buttons_can_autorun(self, cells):
        pressed = {
            (c.args[0].id, ast.unparse(nbast.keyword(c, "stage") or ast.Constant("demo")))
            for cell in cells
            for c in nbast.calls(cell, "pressed")
        }
        assert pressed == {
            ("am_connect", "'demo'"), ("am_seed", "'demo'"), ("am_lens_sweep", "'demo'"),
            ("am_lens_live", "'demo'"), ("am_send", "'demo'"),
            ("am_cleanup_btn", "'cleanup'"), ("am_resweep_btn", "'cleanup'"),
        }  # fmt: skip

    def test_orphan_delete_new_session_kickoff_hood_and_reset_are_read_with_a_plain_value(
        self, cells
    ):
        # The hood button only looks, but under AUTORUN it would connect to whatever REDIS_URL
        # names, which is rarely the database behind the service.
        source = "\n".join(ast.unparse(c) for c in cells)
        for button in (
            "am_orphan_drop", "am_new_session", "am_extract_start", "am_extract_check",
            "am_hood_btn", "am_reset_btn",
        ):  # fmt: skip
            assert f"{button}.value" in source and f"pressed({button}" not in source, button

    def test_every_run_button_in_the_column_is_accounted_for(self, cells):
        assert set(ui_elements(cells, "mo.ui.run_button")) == {
            "am_connect", "am_orphan_drop", "am_seed", "am_lens_sweep", "am_lens_live", "am_send",
            "am_new_session", "am_cleanup_btn", "am_resweep_btn", "am_reset_btn",
            "am_extract_start", "am_extract_check", "am_hood_btn",
        }  # fmt: skip

    def test_the_four_presenter_notes_are_plain_say_and_expect_calls(self, cells):
        calls = [c for cell in cells for c in nbast.calls(cell, "notes")]
        assert len(calls) == 4  # the lens, the scope, the wow and the extraction kick-off
        assert all(len(c.args) == 2 and not c.keywords for c in calls)

    def test_a_cell_that_shows_notes_or_flips_a_tier_never_calls_the_service(self, cells):
        for cell in cells:
            reads = set(nbast.refs(cell))
            if nbast.calls(cell, "notes") or reads & {
                "am_tier_summary", "am_tier_events", "am_tier_memories"
            }:  # fmt: skip
                if "am_extract_start" in nbast.defs(cell):
                    continue  # the kick-off's cell only gates on the connection; a click sends
                assert "am_conn" not in reads, "a display cell must not be a fetch cell"

    def test_the_cleanup_cell_reads_only_config_and_its_own_buttons(self, cells):
        cleanup = [c for c in cells if "stage='cleanup'" in ast.unparse(c)]
        assert len(cleanup) == 1
        assert set(nbast.refs(cleanup[0])) == {
            "am_base", "am_store", "am_key", "am_cleanup_btn", "am_resweep_btn", "am_reset_btn",
            "am_set_gen",
        }  # fmt: skip

    def test_the_buttons_that_delete_are_red_and_the_second_cleanup_button_says_what_it_does(self, cells):
        buttons = ui_elements(cells, "mo.ui.run_button")
        for name in ("am_cleanup_btn", "am_resweep_btn", "am_orphan_drop"):
            assert ast.literal_eval(nbast.keyword(buttons[name], "kind")) == "danger", name
        label = ast.literal_eval(nbast.keyword(buttons["am_resweep_btn"], "label"))
        assert label.startswith("Clean up again") and "Re-sweep" not in label

    def test_a_control_of_the_inspector_moves_nothing_that_asks_the_service(self, cells):
        # A cell re-runs when anything it reads changes. Send reads the controls, so whatever reads
        # what Send emits (the turn) must not be a cell that calls the service.
        controls = {"am_wow_case", "am_wow_own", "am_wow_limit"}
        (send,) = [c for c in cells if controls & set(nbast.refs(c))]
        assert "am_turn" in nbast.defs(send)
        for cell in cells:
            if "am_turn" in nbast.refs(cell):
                assert "am_conn" not in nbast.refs(cell), ast.unparse(cell)[:100]

    def test_the_session_list_follows_the_posted_token_and_not_the_turn(self, cells):
        # A marimo state getter registers a dependency only when it is called.
        (sessions,) = [c for c in cells if "am_dd_session" in nbast.defs(c)]
        assert set(nbast.refs(sessions)) == {"am_chat", "am_deep", "am_posted"}
        assert nbast.calls(sessions, "am_posted")
        bumpers = [c for c in cells if nbast.calls(c, "am_set_posted")]
        assert len(bumpers) == 2
        assert {n for c in bumpers for n in nbast.refs(c)} & {"am_send", "am_extract_start"} == {
            "am_send", "am_extract_start",
        }  # fmt: skip

    def test_the_inspector_starts_on_the_first_preset_so_autorun_sends_it(self, cells):
        source = "\n".join(ast.unparse(c) for c in cells)
        assert "value=_labels[0]" in source and "value=am_wow_presets()[0]['query']" in source
        assert ast.literal_eval(nbast.keyword(ui_elements(cells, "mo.ui.text")["am_wow_own"], "value")).strip()

    def test_every_slider_waits_for_the_drag_to_end_and_no_text_box_fires_per_keystroke(self, cells):
        # A slider sends a value on every tick of a drag, and each value can be a billed search.
        def debounced(call, default):
            given = nbast.keyword(call, "debounce")
            return ast.literal_eval(given) if given is not None else default

        sliders = ui_elements(cells, "mo.ui.slider")
        assert set(sliders) == {"am_lens_cut", "am_wow_limit"}
        for name, call in sliders.items():
            assert debounced(call, default=False), name
        for name, call in ui_elements(cells, "mo.ui.text").items():
            assert debounced(call, default=True), name

    def test_the_slider_can_only_sit_on_a_threshold_the_sweep_tried(self, cells):
        slider = ui_elements(cells, "mo.ui.slider")["am_lens_cut"]
        assert "am_swept" in ast.unparse(nbast.keyword(slider, "steps"))

    def test_the_slider_starts_at_the_value_the_live_run_calibrated_and_that_is_a_step(self, nb, cells):
        """Live, 2026-09-30: at 0.5 nothing came back; the on-topic memory sat at 0.3 to 0.4."""
        slider = ui_elements(cells, "mo.ui.slider")["am_lens_cut"]
        start = ast.literal_eval(nbast.keyword(slider, "value"))
        assert start == 0.3 and start in nb.am_thresholds()

    def test_at_that_start_the_live_bands_keep_the_on_topic_memory_and_cut_the_rest(self, nb):
        """The bands the live sweep of "What should the kitchen avoid serving me?" produced."""
        live = {
            nb.am_memory_id(1): 0.3, nb.am_memory_id(5): 0.2, nb.am_memory_id(4): 0.1,
            nb.am_memory_id(2): 0.0, nb.am_memory_id(3): 0.0,
        }  # fmt: skip
        memories = {m["id"]: m for m in nb.am_memories()}
        sweep = {"query": nb.am_wow_presets()[0]["query"], "bands": live, "memories": memories}
        rows = nb.am_band_rows(sweep, 0.3)
        assert [r["at the slider"] for r in rows] == ["returned"] + ["cut"] * 4
        assert rows[0]["similarity"] == "0.3 to 0.4" and "peanuts" in rows[0]["memory"]
        assert [r["at the slider"] for r in nb.am_band_rows(sweep, 0.5)] == ["cut"] * 5

    def test_the_write_button_says_what_it_writes_before_it_is_pressed(self, cells):
        label = ast.unparse(nbast.keyword(ui_elements(cells, "mo.ui.run_button")["am_seed"], "label"))
        assert "am_memories()" in label and "memories" in label

    def test_the_config_cell_links_the_console_and_warns_against_shared_services(self, cells):
        text = "\n".join(ast.unparse(c) for c in nbast.config_panel(cells, "am_base"))
        for needed in ("cloud.redis.io", "Never on a shared service", "General settings", "API keys",
                       "create-service", "Flush", "Delete service", "AM_OWNER", "AM_NAMESPACE"):  # fmt: skip
            assert needed in text, needed

    def test_the_key_field_is_a_secret_text_that_is_never_prefilled(self, cells):
        (config,) = [c for c in cells if "am_base" in nbast.defs(c)]
        secrets = [n for n in ast.walk(config) if isinstance(n, ast.Call) and nbast.call_name(n) == "SecretText"]
        assert [ast.unparse(s.args[0]) for s in secrets] == ["'MEMORY_API_KEY'"]
        assert "env('MEMORY_API_KEY')" not in ast.unparse(config)
        assert "env('MEMORY_API_BASE_URL')" in ast.unparse(config)  # the non-secret ones are

    def test_connect_calls_the_reset_token_so_reset_reaches_it(self, cells):
        # A marimo state getter registers a dependency only when it is called.
        (connect,) = [c for c in cells if "am_conn" in nbast.defs(c)]
        assert nbast.calls(connect, "am_gen")

    def test_the_chat_follows_the_reset_token_too(self, cells):
        (state,) = [c for c in cells if "am_chat" in nbast.defs(c)]
        assert nbast.calls(state, "am_gen")

    def test_connect_shows_the_recorded_live_panel_when_there_are_no_settings(self, cells):
        (connect,) = [c for c in cells if "am_conn" in nbast.defs(c)]
        assert [ast.unparse(c.args[0]) for c in nbast.calls(connect, "recorded_live")] == ["'am'"]

    def test_the_chat_is_built_by_hand_and_only_the_send_cell_writes_to_it(self, cells):
        assert "mo.ui.chat" not in "\n".join(ast.unparse(c) for c in cells)  # broken in marimo 0.25.0
        writers = [
            c for c in cells
            if any(isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store)
                   and ast.unparse(n.value) == "am_chat" for n in ast.walk(c))
        ]  # fmt: skip
        assert len(writers) == 1
        assert {"am_send", "am_new_session"} <= set(nbast.refs(writers[0]))

    def test_every_hosted_call_names_the_am_component(self, nb_file):
        source = nb_file.read_text(encoding="utf-8")
        mine = [
            n for i in nbast.column_items(source, 3)
            for n in ast.walk(i.node) if isinstance(n, ast.Call) and nbast.call_name(n) == "http_call"
        ]  # fmt: skip
        assert mine and all(ast.unparse(c.args[0]) == "'am'" for c in mine)

    def test_the_deep_dive_switch_follows_autorun(self, cells):
        (switch,) = [c for c in cells if "am_deep" in nbast.defs(c)]
        assert "mo.ui.switch(value=AUTORUN == 'demo'" in ast.unparse(switch)

    def test_a_cell_that_calls_the_service_catches_its_errors_and_shows_them(self, cells):
        calls_service = (
            "am_take_turn", "am_sweep", "am_scope_search", "am_list", "am_health", "am_write_memories",
            "am_orphan_sweep", "am_delete_orphans", "am_cleanup", "am_extract_kickoff",
            "am_extracted", "am_recheck", "am_get_session", "am_hood",
        )  # fmt: skip
        found = [c for c in cells if any(nbast.calls(c, name) for name in calls_service)]
        assert len(found) >= 12
        for cell in found:
            handlers = [
                ast.unparse(h.type) for t in ast.walk(cell) if isinstance(t, ast.Try) for h in t.handlers
            ]
            assert handlers and all("ServiceError" in h for h in handlers), ast.unparse(cell)[:100]
            assert nbast.calls(cell, "am_failed"), ast.unparse(cell)[:100]

    def test_the_sdk_snippet_sits_in_a_collapsed_accordion_and_is_text_only(self, cells):
        text = "\n".join(ast.unparse(c) for c in cells)
        assert "am_sdk_snippet(am_swept['query'])" in text and "mo.accordion" in text
        assert "official SDK" in text and "text only, not installed" in text


# ---- the whole column, cell by cell, against the fake ------------------------------------------------------------------------
RUN = "0123abcd"
SETTINGS = {"MEMORY_API_BASE_URL": BASE, "MEMORY_STORE_ID": STORE, "MEMORY_API_KEY": KEY}


def texts(outputs):
    return " ".join(getattr(o, "text", str(o)) for o in outputs)


class Btn:
    """Stands in for a mo.ui.run_button in app.run(defs=...)."""

    def __init__(self, value):
        self.value = value


@pytest.fixture
def wire(service, monkeypatch):
    """The fake, behind every httpx client: a fresh copy of the notebook builds its own."""

    def send(self, request, **kwargs):
        response = service.handle(request)
        response.request = request
        return response

    monkeypatch.setattr(httpx.Client, "send", send)
    return service


class TestWholeColumn:
    def run(self, load_fresh, stage, defs=None, **env):
        fresh = load_fresh(
            IRIS_BASICS_RUN=RUN, IRIS_BASICS_AUTORUN=stage, IRIS_BASICS_HEADLESS="1", **env
        )
        outputs, found = fresh.app.run(defs=defs)
        return fresh, texts(outputs), found

    def test_with_no_settings_only_the_config_and_the_always_available_cleanup_render(
        self, load_fresh, wire
    ):
        outputs, defs = load_fresh().app.run()
        text = texts(outputs)
        assert "Agent Memory is not configured" in text and "MEMORY_API_KEY" in text
        assert defs["am_conn"] is None
        # without settings the column still shows what a real run looked like, labelled as such
        assert "Recorded live on 2026-09-30" in text and "not a simulation" in text
        assert "#1 of 3 · 0 words in common" in text and "0.3 to 0.4" in text
        for later in ("Write long-term memories", "what a threshold lets through", "a memory inspector", "Deep dive"):
            assert later not in text, later
        assert "Leftovers" not in text and "Clean up" in text
        assert wire.log == []

    def test_pressing_cleanup_without_settings_only_says_so(self, load_fresh, wire):
        _, text, _ = self.run(load_fresh, "cleanup")
        assert text.count("Agent Memory is not configured") == 2 and wire.log == []

    def test_with_settings_but_no_test_press_nothing_reaches_the_service(self, load_fresh, wire):
        outputs, defs = load_fresh(**SETTINGS).app.run()
        assert "Press" in texts(outputs) and "Test connection" in texts(outputs)
        assert defs["am_conn"] is None and wire.log == []

    def test_a_malformed_endpoint_is_a_message_and_nothing_is_sent(self, load_fresh, wire):
        settings = {**SETTINGS, "MEMORY_API_BASE_URL": "http://am.example.test"}
        _, text, defs = self.run(load_fresh, "demo", **settings)
        assert defs["am_conn"] is None and "must start with https://" in text
        assert wire.log == []

    def test_a_wrong_key_stops_at_test_connection_and_says_so(self, load_fresh, wire):
        _, text, defs = self.run(load_fresh, "demo", **{**SETTINGS, "MEMORY_API_KEY": "wrong-key-0000"})
        assert defs["am_conn"] is None and "API key was refused" in text and "wrong-key-0000" not in text
        assert wire.writes() == []

    def test_autorun_demo_drives_the_column_from_connect_to_the_deep_dive(self, load_fresh, wire):
        live_demo(wire)
        fresh, text, defs = self.run(load_fresh, "demo", **SETTINGS)
        preset = fresh.am_wow_presets()[0]
        assert defs["am_conn"] is not None and defs["am_ready"] is True
        assert "memories are stored" in text and "The health check" in text
        assert [s["threshold"] for s in defs["am_swept"]["steps"]] == fresh.am_thresholds()
        assert "would return" in text and "hard cut" in text
        assert [c["count"] for c in defs["am_scoped"]] == [5, 0, 0]
        assert defs["am_turn"]["preset"] == preset and defs["am_turn"]["ask"] == preset["query"]
        assert [m["id"] for m in defs["am_turn"]["recalled"]][0] == preset["target"]
        assert "#1 of 3 · 0 words in common" in text and "★" in text
        assert "Prompt assembly" in text and "Retrieved memory context" in text
        # The session list follows a state token, which app.run() does not act on: the next test
        # runs its cells with the token given, and TestRealRuntime runs them in the real kernel.
        assert "Auto-extraction" in text and "Under the hood" in text  # the deep dive is open
        assert "No database URL" not in text  # under the hood looks only when its button is clicked
        assert sorted(fresh.ledger_live("am", "memory")) == sorted(m["id"] for m in fresh.am_memories())
        assert fresh.ledger_live("am", "session") == [fresh.am_session_id(1)]

    def test_under_the_hood_is_soft_without_a_url_and_looks_only_when_clicked(self, load_fresh, wire):
        live_demo(wire)
        _, text, defs = self.run(load_fresh, "demo", **SETTINGS)
        assert defs["am_hood_btn"].value is False and "No database URL" not in text
        clicked = {"am_hood_btn": Btn(True), "am_hood_url": Btn(None)}
        _, text, _ = self.run(load_fresh, "demo", clicked, **SETTINGS)
        assert "No database URL" in text  # with no REDIS_URL it says so and goes no further

    def test_the_demo_writes_only_our_ids_and_never_deletes_or_starts_the_kickoff(self, load_fresh, wire):
        live_demo(wire)
        orphan_run(wire)
        fresh, _, defs = self.run(load_fresh, "demo", **SETTINGS)
        assert wire.requests("DELETE") == []  # not one delete, not even the orphans on offer
        assert all(c.body["memories"][0]["ownerId"] == fresh.AM_OWNER for c in wire.requests("POST", "/long-term-memory"))
        events = wire.requests("POST", "/events")
        assert len(events) == 1 and events[0].body["sessionId"] == fresh.am_session_id(1)  # the kick-off did not run
        assert defs["am_extract_start"].value is False and defs["am_extract_check"].value is False
        assert defs["am_new_session"].value is False
        assert defs["am_orphan_drop"].value is False and defs["am_reset_btn"].value is False
        assert {"demo-1", "demo-2", "ib-deadbeef-m1", "ib-deadbeef-m2"} <= set(wire.memories)

    def test_the_extraction_kickoff_and_its_check_run_only_when_clicked(self, load_fresh, wire):
        conversation = []

        def sent():
            return [c for c in wire.requests("POST", "/events") if c.body["content"][0]["text"] in conversation]

        def click(start, check):
            controls = {"am_extract_start": Btn(start), "am_extract_check": Btn(check)}
            fresh, text, defs = self.run(load_fresh, "demo", controls, **SETTINGS)
            conversation[:] = fresh.am_extract_conversation()
            return text

        click(False, False)
        assert sent() == []  # not even the demo starts it
        assert "Posted" in click(True, False) and len(sent()) == 4
        assert "None yet" in click(False, True)
        wire.extract()
        assert "memories with ids the service chose" in click(False, True)

    def test_every_search_the_demo_sends_carries_an_owner_or_a_namespace(self, load_fresh, wire):
        self.run(load_fresh, "demo", **SETTINGS)
        searches = wire.requests("POST", "/search")
        assert len(searches) >= 15
        assert all(c.body["filter"].get("ownerId") or c.body["filter"].get("namespace") for c in searches)
        assert all("includeAll" not in c.query for c in wire.log)

    def test_autorun_cleanup_removes_it_all_from_the_ledger_without_connecting(self, load_fresh, wire):
        live_demo(wire)
        self.run(load_fresh, "demo", **SETTINGS)
        wire.extract()  # the store's extraction gets round to the session, after the demo
        wire.log.clear()
        fresh, text, defs = self.run(load_fresh, "cleanup", **SETTINGS)
        assert set(wire.memories) == {"demo-1", "demo-2"} and wire.sessions == {}
        assert "Deleted" in text and "made by extraction" in text
        assert defs["am_conn"] is None, "the demo cells did not run"
        assert not [c for c in wire.log if c.path.endswith("/health")]  # none of Test connection's reads
        assert fresh.ledger_live("am", "memory", pending=True) == []
        assert fresh.ledger_live("am", "session", pending=True) == []

    def test_the_cleanup_and_reset_buttons_work_when_clicked_and_only_reset_bumps_the_token(
        self, load_fresh, wire
    ):
        def clicked(cleanup, reset):
            bumps = []
            config = {
                "am_base": SimpleNamespace(value=BASE), "am_store": SimpleNamespace(value=STORE),
                "am_key": SimpleNamespace(value=None), "am_connect": Btn(False),
                "am_gen": lambda: 0, "am_set_gen": lambda update: bumps.append(update(0)),
                "am_cleanup_btn": Btn(cleanup), "am_resweep_btn": Btn(False), "am_reset_btn": Btn(reset),
            }  # fmt: skip
            return bumps, self.run(load_fresh, "", config, **SETTINGS)

        self.run(load_fresh, "demo", **SETTINGS)
        bumps, (_, text, _) = clicked(cleanup=True, reset=False)
        assert wire.memories == {} and bumps == [] and "Deleted" in text
        self.run(load_fresh, "demo", **SETTINGS)
        bumps, _ = clicked(cleanup=False, reset=True)
        assert wire.memories == {} and bumps == [1]

    def test_re_sweep_is_a_cleanup_button_too(self, load_fresh, wire):
        self.run(load_fresh, "demo", **SETTINGS)
        wire.add_memory("late-1", "extracted late", f"ib-{RUN}", "iris-basics-notebook")
        config = {
            "am_base": SimpleNamespace(value=BASE), "am_store": SimpleNamespace(value=STORE),
            "am_key": SimpleNamespace(value=None), "am_connect": Btn(False), "am_gen": lambda: 0,
            "am_set_gen": lambda update: None, "am_cleanup_btn": Btn(False),
            "am_resweep_btn": Btn(True), "am_reset_btn": Btn(False),
        }  # fmt: skip
        _, text, _ = self.run(load_fresh, "", config, **SETTINGS)
        assert "late-1" not in wire.memories and wire.memories == {}

    def test_cleanup_stops_cold_when_the_store_lists_somebody_elses_memory(self, load_fresh, wire):
        live_demo(wire)
        self.run(load_fresh, "demo", **SETTINGS)
        wire.ignore_filters = True
        wire.log.clear()
        _, text, _ = self.run(load_fresh, "cleanup", **SETTINGS)
        assert "not owned by" in text and wire.requests("DELETE", "/long-term-memory") == []
        assert {"demo-1", "demo-2"} <= set(wire.memories)

    def test_a_424_is_a_red_message_naming_its_type_never_zero_results(self, load_fresh, wire):
        wire.search_error = (424, "invalid-model-credentials")
        _, text, defs = self.run(load_fresh, "demo", **SETTINGS)
        assert defs["am_ready"] is False and "/errors/invalid-model-credentials" in text
        assert "424" in text and "memories are stored" not in text

    def test_the_key_appears_in_no_output_and_no_variables_panel_preview(self, load_fresh, wire):
        from marimo._messaging.variables import create_variable_value

        _, text, defs = self.run(load_fresh, "demo", **SETTINGS)
        assert KEY not in text
        for name, value in defs.items():
            assert KEY not in (create_variable_value(name, value).value or ""), name

    def test_leftovers_from_another_run_are_listed_and_the_demo_leaves_them_alone(self, load_fresh, wire):
        orphan_run(wire, hours_old=5, memories=2, sessions=1)
        orphan_run(wire, "cafef00d", hours_old=0.2, memories=1, sessions=0)
        _, text, defs = self.run(load_fresh, "demo", **SETTINGS)
        assert "Leftovers from other runs (3 memories)" in text and "ib-deadbeef" in text
        assert "Delete those orphans" in text
        assert len(defs["am_orphans"]["memories"]) == 2 and defs["am_orphans"]["sessions"] == ["ib-deadbeef-s1"]
        assert {"ib-deadbeef-m1", "ib-cafef00d-m1"} <= set(wire.memories)

    def test_no_leftovers_says_so(self, load_fresh, wire):
        _, text, _ = self.run(load_fresh, "demo", **SETTINGS)
        assert "Leftovers from other runs (0 memories)" in text and "None found" in text
        assert "Delete those orphans" not in text

    def test_an_orphan_is_deleted_only_after_the_typed_confirmation(self, load_fresh, wire):
        orphan_run(wire, hours_old=5, memories=2, sessions=1)
        live_demo(wire)

        def clicked(phrase):
            fresh = load_fresh(
                IRIS_BASICS_RUN=RUN, IRIS_BASICS_AUTORUN="demo", IRIS_BASICS_HEADLESS="1", **SETTINGS
            )
            conn, _ = fresh.am_settings(BASE, STORE, fresh.Secret(KEY))
            controls = {  # the panel's three globals: what it found, the box and the button
                "am_orphans": fresh.am_orphan_sweep(conn),
                "am_orphan_confirm": SimpleNamespace(value=phrase), "am_orphan_drop": Btn(True),
            }  # fmt: skip
            return texts(fresh.app.run(defs=controls)[0])

        assert "to confirm" in clicked("nope") and "ib-deadbeef-m1" in wire.memories
        assert "Deleted" in clicked("delete orphans")
        assert "ib-deadbeef-m1" not in wire.memories and "ib-deadbeef-s1" not in wire.sessions
        assert {"demo-1", "demo-2"} <= set(wire.memories)

    @staticmethod
    def inspector(fresh, conn, chat, bumps, *, send=False, new=False, label=None, own="x", limit=2):
        """The globals the inspector's cells take from the ones above them, and its controls."""
        return {
            "am_chat": chat, "am_conn": conn, "am_ready": True,
            "am_posted": lambda: 0, "am_set_posted": lambda update: bumps.append(update(0)),
            "am_new_session": Btn(new), "am_send": Btn(send),
            "am_wow_case": SimpleNamespace(value=label or fresh.am_wow_presets()[1]["label"]),
            "am_wow_own": SimpleNamespace(value=own), "am_wow_limit": SimpleNamespace(value=limit),
            "am_tier_summary": SimpleNamespace(value=True), "am_tier_events": SimpleNamespace(value=True),
            "am_tier_memories": SimpleNamespace(value=True),
        }  # fmt: skip

    def test_send_and_new_session_work_through_the_cell(self, load_fresh, wire):
        fresh = load_fresh(IRIS_BASICS_RUN=RUN, **SETTINGS)
        conn, _ = fresh.am_settings(BASE, STORE, fresh.Secret(KEY))
        fresh.am_write_memories(conn)
        labels = [p["label"] for p in fresh.am_wow_presets()]
        bumps, chat = [], {"session": None, "turn": None}

        def press(**kwargs):
            outputs, defs = fresh.app.run(defs=self.inspector(fresh, conn, chat, bumps, **kwargs))
            return texts(outputs), defs

        text, defs = press()  # nothing pressed yet
        assert defs["am_turn"] is None and "Send a message" in text and wire.requests("POST", "/events") == []
        assert bumps == []
        text, defs = press(send=True)
        assert defs["am_turn"]["session"] == fresh.am_session_id(1) and "#1 of 2" in text
        assert chat["turn"] is defs["am_turn"] and len(defs["am_turn"]["recalled"]) == 2
        assert bumps == [1]
        text, defs = press(send=True, label=labels[0])
        assert len(defs["am_turn"]["events"]) == 2 and defs["am_turn"]["session"] == fresh.am_session_id(1)
        assert bumps == [1, 1]
        text, defs = press(new=True)
        assert chat == {"session": None, "turn": None} and defs["am_turn"] is None
        assert bumps == [1, 1]  # a new session posts nothing
        text, defs = press(send=True)
        assert defs["am_turn"]["session"] == fresh.am_session_id(2) and len(defs["am_turn"]["events"]) == 1
        assert bumps == [1, 1, 1]

    def test_the_deep_dive_lists_the_sessions_and_opens_the_one_send_started(self, load_fresh, wire):
        fresh = load_fresh(IRIS_BASICS_RUN=RUN, **SETTINGS)
        conn, _ = fresh.am_settings(BASE, STORE, fresh.Secret(KEY))
        fresh.am_write_memories(conn)
        chat = {"session": None, "turn": None}
        fresh.am_take_turn(conn, chat, fresh.am_wow_presets()[0], 3)  # what Send does
        kickoff = fresh.am_extract_kickoff(conn)["session"]  # and the extraction kick-off
        controls = self.inspector(fresh, conn, chat, []) | {"am_deep": SimpleNamespace(value=True)}
        outputs, defs = fresh.app.run(defs=controls)
        dropdown = defs["am_dd_session"]
        assert list(dropdown.options) == [fresh.am_session_id(1), kickoff]
        assert dropdown.value == fresh.am_session_id(1)  # the inspector's, not just the newest
        assert "the actor of the first event" in texts(outputs) and "No summary yet" in texts(outputs)
        assert len(defs["am_dd_read"]["session"]["events"]) == 1

    @pytest.mark.parametrize("own", ["", "   ", "\n"])
    def test_a_blank_message_of_your_own_is_not_sent_and_asks_for_one(self, load_fresh, wire, own):
        fresh = load_fresh(IRIS_BASICS_RUN=RUN, **SETTINGS)
        conn, _ = fresh.am_settings(BASE, STORE, fresh.Secret(KEY))
        fresh.am_write_memories(conn)
        wire.log.clear()
        bumps, chat = [], {"session": None, "turn": None}
        controls = self.inspector(fresh, conn, chat, bumps, send=True, label="Type your own", own=own)
        outputs, defs = fresh.app.run(defs=controls)
        assert "Type a message" in texts(outputs) and "#1 of" not in texts(outputs)
        assert wire.requests("POST", "/events") == [] and wire.requests("GET") == []
        assert bumps == [] and chat["session"] is None
        blank = [c for c in wire.requests("POST", "/search") if c.body["filter"] == fresh.am_filter()]
        assert [c for c in blank if c.body["text"] == ""] == []  # not a listing of the memories either
        assert fresh.ledger_live("am", "session", pending=True) == []
        controls = self.inspector(fresh, conn, chat, bumps, send=True, label="Type your own", own="a tent")
        outputs, defs = fresh.app.run(defs=controls)  # and the next Send with words in it works
        assert "Type a message" not in texts(outputs) and "#1 of 2" in texts(outputs)

    def test_a_failed_send_is_shown_in_the_inspector_and_the_next_send_still_works(self, load_fresh, wire):
        fresh = load_fresh(IRIS_BASICS_RUN=RUN, **SETTINGS)
        conn, _ = fresh.am_settings(BASE, STORE, fresh.Secret(KEY))
        fresh.am_write_memories(conn)
        bumps, chat = [], {"session": None, "turn": None}
        controls = self.inspector(
            fresh, conn, chat, bumps, send=True, label=fresh.am_wow_presets()[0]["label"], limit=3
        )
        wire.search_error = (424, "model-quota-exceeded")
        outputs, defs = fresh.app.run(defs=controls)
        assert "model-quota-exceeded" in texts(outputs) and "note" in defs["am_turn"]
        assert bumps == [1]  # the event went in before the search failed, so the session list is stale
        wire.search_error = None
        outputs, defs = fresh.app.run(defs=controls)
        assert "note" not in defs["am_turn"] and "#1 of 3" in texts(outputs)

    def test_an_endpoint_that_answers_with_a_web_page_is_not_a_connection(self, load_fresh, monkeypatch):
        page = httpx.Response(200, text="<html>console</html>", headers={"content-type": "text/html"})

        def send(self, request, **kwargs):
            page.request = request
            return page

        monkeypatch.setattr(httpx.Client, "send", send)
        _, text, defs = self.run(load_fresh, "demo", **SETTINGS)
        assert defs["am_conn"] is None and "not a JSON object with a status" in text
        assert "Connected" not in text and "Write long-term memories" not in text

    def test_connecting_without_the_demo_writes_nothing_until_the_write_button_is_pressed(
        self, load_fresh, wire
    ):
        config = {
            "am_base": SimpleNamespace(value=BASE), "am_store": SimpleNamespace(value=STORE),
            "am_key": SimpleNamespace(value=None), "am_connect": Btn(True),
            "am_gen": lambda: 0, "am_set_gen": lambda update: None,
        }  # fmt: skip
        _, text, defs = self.run(load_fresh, "", config, **SETTINGS)
        assert defs["am_conn"] is not None and defs["am_ready"] is False
        assert "Nothing is written until you press the button" in text
        assert wire.writes() == [] and wire.memories == {} and wire.sessions == {}


# ---- the no-credentials export --------------------------------------------------------------------------------------------------
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
        assert "Agent Memory is not configured" in session
        assert "Recorded live on 2026-09-30" in session
        for later in ("Write long-term memories", "what a threshold lets through", "a memory inspector"):
            assert later not in session, later


# ---- the real marimo runtime ---------------------------------------------------------------------------------------------------
RUNNER = """
import os
import pickle
import sys
from pathlib import Path

sys.path.insert(0, {tests!r})
import httpx
from fake_am import FakeMemory

server, state = FakeMemory(), Path({state!r})
if state.exists():
    server.load(pickle.loads(state.read_bytes()))


def send(self, request, **kwargs):
    response = server.handle(request)
    state.write_bytes(pickle.dumps(server.state()))
    response.request = request
    return response


httpx.Client.send = send  # marimo spawns its kernel, which re-imports this file

if __name__ == "__main__":
    from marimo._cli.cli import main

    if os.environ.get("FAKE_EXTRACT"):
        server.extract()  # the store's extraction gets round to the sessions, between the runs
        state.write_bytes(pickle.dumps(server.state()))
    args = ["-y", "export", "html", "--no-sandbox", {notebook!r}, "-o", "out.html", "-f"]
    main(args, standalone_mode=False)
"""


class TestRealRuntime:
    """`marimo export` itself, not app.run(): its kernel cancels the descendants of a stop."""

    def export(self, nb_file, tmp_path, stage, *, extract=False, seed=None):
        work = tmp_path / "real"
        work.mkdir(exist_ok=True)
        shutil.copy2(nb_file, work / nb_file.name)
        state = tmp_path / "server.pkl"
        if seed is not None:
            state.write_bytes(pickle.dumps(seed.state()))
        (work / "runner.py").write_text(
            RUNNER.format(tests=str(Path(__file__).parent), state=str(state), notebook=nb_file.name),
            encoding="utf-8",
        )
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        env.update(
            PYTHONIOENCODING="utf-8", PYTHONUTF8="1", IRIS_BASICS_RUN=RUN,
            IRIS_BASICS_AUTORUN=stage, IRIS_BASICS_HEADLESS="1", FAKE_EXTRACT="1" if extract else "",
            **SETTINGS,
        )  # fmt: skip
        done = subprocess.run(
            [sys.executable, "runner.py"], cwd=work, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
        session = work / "__marimo__" / "session" / f"{nb_file.name}.json"
        after = FakeMemory()
        after.load(pickle.loads(state.read_bytes()))
        return json.loads(session.read_text(encoding="utf-8")), after

    @staticmethod
    def errors(session, nb_file):
        """The error outputs of column 3's cells.

        The assembled notebook holds the other columns too, and the ones whose settings are not
        given stop (Redis Search with no REDIS_URL, say), which is their business. The session
        lists the setup block first and then every cell and function in file order.
        """
        items = nbast.items(nb_file.read_text(encoding="utf-8"))
        assert len(session["cells"]) == len(items) + 1
        mine = {n for n, item in enumerate(items, 1) if item.column == 3}
        return [
            o for n, c in enumerate(session["cells"]) if n in mine
            for o in c.get("outputs") or [] if o["type"] == "error"
        ]  # fmt: skip

    def test_autorun_demo_then_cleanup_run_in_the_real_kernel_and_remove_what_the_demo_made(
        self, nb_file, tmp_path
    ):
        elsewhere = FakeMemory()
        live_demo(elsewhere)
        orphan_run(elsewhere)
        session, after_demo = self.export(nb_file, tmp_path, "demo", seed=elsewhere)
        assert self.errors(session, nb_file) == []
        text = json.dumps(session)
        for shown in ("memories are stored", "#1 of 3", "Prompt assembly", "the actor of the first event"):
            assert shown in text, shown
        made = {i for i in after_demo.memories if i.startswith(f"ib-{RUN}-")}
        assert made == {f"ib-{RUN}-m{n}" for n in range(1, 6)}
        assert set(after_demo.sessions) == {f"ib-{RUN}-s1", "ib-deadbeef-s1"}
        assert {"demo-1", "demo-2", "ib-deadbeef-m1"} <= set(after_demo.memories)  # untouched
        assert len(after_demo.requests("POST", "/events")) == 1  # the kick-off did not autorun
        assert after_demo.requests("DELETE") == []  # nor did the orphan delete
        ledger = tmp_path / "ledger" / f"{RUN}.jsonl"
        assert len(re.findall(r'"state": "created"', ledger.read_text(encoding="utf-8"))) == 6

        session, after_cleanup = self.export(nb_file, tmp_path, "cleanup", extract=True)
        text = json.dumps(session)
        assert "Deleted" in text and "made by extraction" in text
        # everything the demo made is gone, the memory extraction added later too
        assert not any(i.startswith(f"ib-{RUN}-") for i in after_cleanup.memories)
        assert set(after_cleanup.memories) == {"demo-1", "demo-2", "ib-deadbeef-m1", "ib-deadbeef-m2"}
        assert set(after_cleanup.sessions) == {"ib-deadbeef-s1"}
        # Test connection did not run, so its descendants were cancelled; cleanup ran regardless.
        assert {e["ename"] for e in self.errors(session, nb_file)} <= {"ancestor-stopped"}
        second_run = after_cleanup.log[len(after_demo.log) :]
        assert second_run and not [c for c in second_run if c.path.endswith("/health")]
        assert {c.method for c in second_run} == {"DELETE", "POST"}  # no GET at all
