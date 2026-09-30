"""Unit tests for src/cairn_support: the boundary rule, the sample-data constraints the notebook
examples rely on, the two formatting helpers, and the embedder when its model is already cached.
"""

import ast
import re
from pathlib import Path

import numpy as np
import pytest

from cairn_support import formatting
from cairn_support import sample_data as data

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "cairn_support"
FORBIDDEN_IMPORTS = {"redis", "niquests", "marimo"}
SHOP_ID = re.compile(r"^[A-Za-z]+[0-9]+$")


def _stems(text: str) -> set[str]:
    return set(formatting._stems(text))


def _mentions(article: dict, word: str) -> bool:
    return word.lower() in (article["title"] + " " + article["body"]).lower()


# ---- boundary ----


def test_package_has_no_iris_imports():
    modules = sorted(PACKAGE_DIR.glob("*.py"))
    assert len(modules) >= 4
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots = {(node.module or "").split(".")[0]}
            else:
                continue
            assert not roots & FORBIDDEN_IMPORTS, f"{module.name} imports {roots & FORBIDDEN_IMPORTS}"


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
    assert len(data.THRESHOLD_PROBES) == 6
    for probe in data.THRESHOLD_PROBES:
        assert set(probe) == {"text", "kind", "target"}
        assert probe["target"] is None or probe["target"] in keys


def test_strategy_variants_and_cache_aside_script():
    assert len(data.STRATEGY_VARIANTS) == 2
    assert all(set(variant) == {"label", "text"} for variant in data.STRATEGY_VARIANTS)
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
    assert len(user_facts) == 2
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
