"""Unit tests for src/cairn_support: the boundary rule, the sample-data constraints the notebook
examples rely on, the two formatting helpers, and the embedder when its model is already cached.
The last section covers run_tag, sessions, and llm, with no network.
"""

import ast
import re
from pathlib import Path

import numpy as np
import pytest

from cairn_support import formatting
from cairn_support import sample_data as data

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "cairn_support"
SHOP_ID = re.compile(r"^[A-Za-z]+[0-9]+$")


def _stems(text: str) -> set[str]:
    return set(formatting._stems(text))


def _mentions(article: dict, word: str) -> bool:
    return word.lower() in (article["title"] + " " + article["body"]).lower()


def _import_roots(module: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"), filename=str(module))):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    return roots


# ---- boundary ----


def test_package_never_imports_marimo():
    modules = sorted(PACKAGE_DIR.glob("*.py"))
    assert len(modules) >= 8
    for module in modules:
        assert "marimo" not in _import_roots(module), f"{module.name} imports marimo"


def test_sample_data_imports_nothing():
    assert _import_roots(PACKAGE_DIR / "sample_data.py") == set()


# ---- Redis Search ----


def test_help_articles_shape():
    assert len(data.HELP_ARTICLES) == 36
    assert all(set(article) == {"id", "title", "body", "category", "year"} for article in data.HELP_ARTICLES)
    ids = [article["id"] for article in data.HELP_ARTICLES]
    assert len(set(ids)) == 36
    categories = {article["category"] for article in data.HELP_ARTICLES}
    assert len(categories) == 6
    for category in categories:
        assert sum(article["category"] == category for article in data.HELP_ARTICLES) == 6


def test_help_articles_leave_each_engine_something_to_win():
    assert not any(_mentions(article, "cramped") for article in data.HELP_ARTICLES)
    assert not any(_mentions(article, "shoes") for article in data.HELP_ARTICLES)
    assert sum(_mentions(article, "Trailhead") for article in data.HELP_ARTICLES) == 1
    assert not any(_mentions(article, "waterproofng") for article in data.HELP_ARTICLES)


def test_search_presets_point_at_articles():
    ids = {article["id"] for article in data.HELP_ARTICLES}
    assert len(data.SEARCH_PRESETS) == 3
    for preset in data.SEARCH_PRESETS:
        assert set(preset) == {"label", "query", "target"}
        assert preset["target"] in ids


def test_help_articles_support_the_prefilter_example():
    for category in ("returns", "shipping"):
        assert any(
            article["category"] == category and 2024 <= article["year"] <= 2026
            for article in data.HELP_ARTICLES
        )


# ---- LangCache ----


def test_gear_care_answers_have_unique_keys():
    assert len(data.GEAR_CARE_ANSWERS) == 5
    keys = [entry["key"] for entry in data.GEAR_CARE_ANSWERS]
    assert len(set(keys)) == 5
    assert all(set(entry) == {"key", "prompt", "response"} for entry in data.GEAR_CARE_ANSWERS)


def test_threshold_probes_target_a_stored_answer_or_none():
    keys = {entry["key"] for entry in data.GEAR_CARE_ANSWERS}
    assert len(data.THRESHOLD_PROBES) == 7
    for probe in data.THRESHOLD_PROBES:
        assert set(probe) == {"text", "kind", "target"}
        assert probe["target"] is None or probe["target"] in keys


def test_threshold_probes_the_cache_aside_step_relies_on():
    # lc_cache_aside asks GEAR_CARE_ANSWERS[1] first, then THRESHOLD_PROBES[0] as the same question in other words.
    assert data.THRESHOLD_PROBES[0]["target"] == data.GEAR_CARE_ANSWERS[1]["key"] == "bag"
    # Index 3 is the all-caps copy: the one probe the `exact` strategy can hit.
    caps = data.THRESHOLD_PROBES[3]
    bag = next(entry for entry in data.GEAR_CARE_ANSWERS if entry["key"] == "bag")
    assert caps["target"] == "bag" and caps["kind"] == "same words, capitals"
    assert caps["text"] == caps["text"].upper() and caps["text"].lower() == bag["prompt"].lower()


def test_cache_aside_script():
    assert set(data.CACHE_ASIDE_SCRIPT) == {"prefix", "ask", "answer", "again"}
    assert data.CACHE_ASIDE_SCRIPT["prefix"].endswith(" ")


# ---- Context Retriever ----


def test_shop_records_shape_and_ids():
    assert [len(data.SHOP_RECORDS[entity]) for entity in ("Customer", "Product", "Order")] == [6, 6, 8]
    id_fields = {"Customer": "customer_id", "Product": "sku", "Order": "order_id"}
    for entity, rows in data.SHOP_RECORDS.items():
        for row in rows:
            assert SHOP_ID.match(row[id_fields[entity]]), row


def test_shop_orders_reference_existing_customers_and_products():
    customer_ids = {row["customer_id"] for row in data.SHOP_RECORDS["Customer"]}
    skus = {row["sku"] for row in data.SHOP_RECORDS["Product"]}
    for order in data.SHOP_RECORDS["Order"]:
        assert order["customer_id"] in customer_ids
        assert order["sku"] in skus


# ---- Agent Memory ----


def test_dana_facts_shape():
    assert len(data.DANA_FACTS) == 5
    assert all(set(fact) == {"text", "topics"} for fact in data.DANA_FACTS)


def test_memory_questions_share_no_stem_with_their_fact():
    assert len(data.MEMORY_QUESTIONS) == 3
    for question in data.MEMORY_QUESTIONS:
        fact = data.DANA_FACTS[question["target"] - 1]
        assert formatting.shared_stems(question["text"], fact["text"]) == []


def test_session_turns():
    assert len(data.SESSION_TURNS) == 4
    assert all(set(turn) == {"role", "text"} for turn in data.SESSION_TURNS)
    assert data.SESSION_TURNS[-1]["text"] == data.MEMORY_QUESTIONS[0]["text"]
    known = set().union(*(_stems(fact["text"]) for fact in data.DANA_FACTS))
    user_facts = [turn for turn in data.SESSION_TURNS[:-1] if turn["role"] == "USER"]
    assert user_facts == [data.SESSION_TURNS[0], data.SESSION_TURNS[2]], "am_chat offers turns 0 and 2 as Dana's two facts"
    for turn in user_facts:
        assert _stems(turn["text"]) - known, turn


# ---- formatting ----


def test_redis_cli_elides_long_bytes():
    assert formatting.redis_cli(["HSET", "key", "vec", b"\x00" * 1024]) == "HSET key vec <1024 bytes>"


def test_redis_cli_quotes_an_argument_with_a_space():
    assert formatting.redis_cli(["SET", "k", "two words"]) == 'SET k "two words"'


def test_redis_cli_truncates_long_text():
    rendered = formatting.redis_cli(["SET", "k", "x" * 200])
    assert "<200 chars>" in rendered
    assert len(rendered) < 200


def test_shared_stems():
    assert formatting.shared_stems("wash the boots", "washing boots") == ["boot", "wash"]


# ---- embedder ----


def test_embedder_loads_from_cache(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    from huggingface_hub import snapshot_download

    from cairn_support import embedder

    try:
        snapshot_download(
            embedder.EMBEDDING_MODEL_NAME,
            allow_patterns=embedder.EMBEDDING_MODEL_FILES,
            local_files_only=True,
        )
    except Exception:
        pytest.skip("potion-base-8M is not in the local Hugging Face cache")
    model = embedder.load_embedding_model()
    assert model.dim == 256
    vectors = embedder.embed_texts(model, ["trail shoes feel cramped", "Finding your boot size"])
    assert vectors.shape == (2, 256)
    assert vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)


# ---- run_tag, sessions, llm ----


def test_run_tag_is_eight_hex_characters_in_the_prefix():
    from cairn_support.run_tag import KEY_PREFIX, RUN_TAG

    assert re.fullmatch(r"[0-9a-f]{8}", RUN_TAG)
    assert KEY_PREFIX == f"cairn:{RUN_TAG}:"


def test_json_session_pins_the_json_headers():
    from cairn_support.sessions import json_session

    session = json_session("https://example.invalid/v1", {"Authorization": "Bearer x"})
    assert session.headers["Content-Type"] == "application/json"
    assert session.headers["Authorization"] == "Bearer x"
    assert "Accept" not in session.headers or session.headers["Accept"] == "*/*"


def _wsgi(environ, start_response):
    # JSON on /json, JSON sent as text/plain on /plain, an empty 204 on /empty, and the gateway's 403 HTML page anywhere else.
    path = environ["PATH_INFO"]
    if path == "/json":
        start_response("200 OK", [("Content-Type", "application/json")])
        return [b'{"data": []}']
    if path == "/plain":
        start_response("201 Created", [("Content-Type", "text/plain")])
        return [b'{"id": "a"}']
    if path == "/empty":
        start_response("204 No Content", [])
        return [b""]
    start_response("403 Forbidden", [("Content-Type", "text/html")])
    return [b"<html>Forbidden</html>"]


@pytest.mark.parametrize(
    ("path", "status", "payload"),
    [("/json", 200, {"data": []}), ("/plain", 201, {"id": "a"}), ("/empty", 204, None), ("/other", 403, {"type": "Forbidden"})],
)
def test_json_request_returns_status_payload_and_ms(path, status, payload):
    import niquests

    from cairn_support.sessions import json_request

    with niquests.Session(app=_wsgi) as session:  # mounted at wsgi://default, the base URL, so nothing leaves the process
        reply = json_request(session, "POST", path, {"q": 1})
    assert (reply.status, reply.payload) == (status, payload)
    assert isinstance(reply.ms, int)


def test_stream_llm_sends_user_and_assistant_messages_only():
    from types import SimpleNamespace

    from cairn_support.llm import _history

    messages = [
        SimpleNamespace(role="system", content="hidden"),
        SimpleNamespace(role="user", content="hi"),
        {"role": "assistant", "content": "hello"},
    ]
    assert _history(messages) == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]


def test_recording_collects_the_requests_inside_the_block_only():
    import niquests

    from cairn_support.sessions import json_request, recording

    with niquests.Session(app=_wsgi) as session:
        json_request(session, "GET", "/json")
        with recording() as wire:
            json_request(session, "POST", "/json", {"q": 1})
            json_request(session, "DELETE", "/empty")
        json_request(session, "GET", "/other")
    assert [(e.method, e.path, e.body, e.status, e.payload) for e in wire] == [
        ("POST", "/json", {"q": 1}, 200, {"data": []}),
        ("DELETE", "/empty", None, 204, None),
    ]
