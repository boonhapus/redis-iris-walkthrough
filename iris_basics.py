import marimo

__generated_with = "0.25.0"
app = marimo.App(width="columns", app_title="Redis Iris basics")

with app.setup(hide_code=True):
    import getpass
    import hashlib
    import json
    import os
    import platform
    import re
    import sys
    import time
    from datetime import datetime, timedelta, timezone

    import marimo as mo
    import niquests
    import numpy as np
    import redis
    from redis.backoff import ExponentialBackoff
    from redis.retry import Retry

    from cairn_support import sample_data as cairn
    from cairn_support.embedder import EMBEDDING_MODEL_NAME, embed_texts, load_embedding_model
    from cairn_support.formatting import redis_cli, shared_stems

    # One tag for everything this notebook writes from this machine. It is derived from the user
    # and host names, so a fresh kernel finds what an earlier one wrote and Clean up needs no ledger.
    RUN_TAG = hashlib.sha256(f"{getpass.getuser()}@{platform.node()}".encode()).hexdigest()[:8]
    KEY_PREFIX = f"cairn:{RUN_TAG}:"   # every Redis key written by this notebook or by Context Retriever
    KEY_TTL_SECONDS = 3600             # Redis keys and the agent key expire on their own; Clean up only shortens that

    MASKED_HEADERS = {"authorization", "x-api-key"}

    def call_service(service, method, path, *, json_body=None, params=None):
        """One request to a hosted Iris service: (status, parsed body or None, wire).

        `wire` is what went on the wire with the credential header masked, so it is safe to show.
        Nothing is raised on a 4xx or 5xx: the JSON body (type, title, detail) is the teaching
        content, so each cell branches on the status itself. Nothing is retried: every call is
        exactly one request, so the wire view and the request counts in the copy are exact.
        """
        response = service.request(method, path, json=json_body, params=params)
        sent = response.request
        body = None
        if response.content:
            try:
                body = response.json()
            except ValueError:
                body = response.text
        headers = {k: "***" if k.lower() in MASKED_HEADERS else v for k, v in sent.headers.items()}
        wire = {
            "method": sent.method, "url": sent.url, "headers": headers,
            "body": json.loads(sent.body) if sent.body else None,
            "status": response.status_code, "ms": round(response.elapsed.total_seconds() * 1000),
        }
        return response.status_code, body, wire

    def connect_redis(url):
        """One redis-py client with the production checklist applied (redis-py docs, produsage)."""
        return redis.Redis.from_url(
            url,
            protocol=2,                # flat FT.* replies; every reply shape here was verified live under RESP2
            decode_responses=True,     # strings back; the vector field is never read through a core command
            socket_connect_timeout=5,
            socket_timeout=5,
            retry=Retry(ExponentialBackoff(), 3),  # three retries on ConnectionError and TimeoutError
            health_check_interval=30,  # PING before reuse after 30 s idle; a notebook idles a lot
        )


@app.cell(column=0, hide_code=True)
def intro():
    mo.md(rf"""
    # Redis Iris basics

    Agents do not have an intelligence problem. They have a context problem: the right facts, at the
    right moment, from data you already hold. [Redis Iris](https://redis.io/docs/latest/develop/ai/context-engine/)
    is the real-time context engine that supplies them: Redis Search underneath, then LangCache,
    Context Retriever, and Agent Memory as hosted services on Redis Cloud. This notebook takes them
    apart, one column per component, on Cairn Outfitters, a fictional outdoor-gear shop. Every step
    shows the exact `redis-cli` command or JSON request behind it. There is no LLM anywhere in it.

    | Component, left to right | Its one job here |
    |---|---|
    | Redis Search | Answer a question over 36 help articles by keyword, by meaning, and by both |
    | Redis LangCache | Return a stored answer for a question asked in other words |
    | Redis Context Retriever | Turn three Python classes into MCP tools an agent calls over your Redis |
    | Redis Agent Memory | Recall a customer fact with no word in common with the question |

    **Configuration.** Copy `.env.example` to `.env` next to `pyproject.toml`. marimo loads it
    when the kernel starts. A column whose variables are missing says so and stops.

    **Safety.** Everything this kernel writes carries the run tag `{RUN_TAG}`: Redis keys under
    `{KEY_PREFIX}` with a one-hour `EXPIRE`, LangCache entries with `ttlMillis`, a surface named
    `cairn-{RUN_TAG}`, memories owned by `cairn-{RUN_TAG}`. The tag is the same every time you open
    the notebook on this machine, so Clean up in a fresh kernel still finds what an earlier kernel
    wrote. Each column ends with one Clean up button whose label says what it deletes. Nothing is
    written until you press a button that says what it writes.
    """)
    return


@app.cell(column=0, hide_code=True)
def rs_title():
    mo.vstack([
        mo.md(r"""
    # Redis Search

    Users ask in their own words, and a keyword index only knows yours. Redis Search indexes the
    hashes under a key prefix once, then answers by keyword (`BM25STD`), by meaning (a vector
    field), or by both fused in one command (`FT.HYBRID`, Redis 8.4 and later). Its one job in this
    column: answer a question over 36 Cairn Outfitters help articles all three ways.

    The other three columns are built on this engine. The embedding model, `potion-base-8M`, runs
    on your machine, so this column needs one setting, `REDIS_URL`. Docs:
    [Search and query](https://redis.io/docs/latest/develop/ai/search-and-query/).
    """),
        mo.mermaid("""flowchart LR
    A[App] -->|embed locally| V[Vector]
    V -->|HSET| H[("Hash under cairn:run:help:")]
    H -.->|index watches the prefix| I[FT index]
    A -->|"FT.SEARCH / FT.HYBRID"| I"""),
    ])
    return


@app.cell(column=0)
def rs_connect():
    from redis.commands.search.field import NumericField, TagField, TextField, VectorField
    from redis.commands.search.index_definition import IndexDefinition, IndexType
    from redis.commands.search.query import Query
    from redis.exceptions import ResponseError

    mo.stop(
        not os.environ.get("REDIS_URL"),
        mo.callout(mo.md("Redis Search is not configured. Set `REDIS_URL` in the `.env` next to `pyproject.toml` (see `.env.example`), then restart the kernel."), kind="warn"),
    )
    try:
        rs_client = connect_redis(os.environ["REDIS_URL"])      # the URL is never a notebook global
        rs_client.ping()                                        # PING -> PONG
        rs_redis_version = rs_client.info("server")["redis_version"]   # INFO server
    except redis.RedisError as _exc:
        mo.stop(True, mo.callout(mo.md(f"`{type(_exc).__name__}` while connecting. Check `REDIS_URL`."), kind="danger"))
    rs_has_hybrid = tuple(int(part) for part in rs_redis_version.split(".")[:2]) >= (8, 4)
    rs_index_name = f"idx:cairn:{RUN_TAG}:help"
    rs_key_prefix = f"{KEY_PREFIX}help:"
    rs_article_keys = [f"{rs_key_prefix}{article['id']}" for article in cairn.HELP_ARTICLES]   # Clean up never scans
    rs_article_titles = dict(zip(rs_article_keys, (article["title"] for article in cairn.HELP_ARTICLES)))   # key -> title, for FT.HYBRID rows
    try:
        with mo.status.spinner(title=f"Loading {EMBEDDING_MODEL_NAME}", subtitle="about 30 MB the first time"):
            rs_embedding_model = load_embedding_model()
    except RuntimeError as _exc:
        mo.stop(True, mo.callout(mo.md(f"`{EMBEDDING_MODEL_NAME}` is not in the Hugging Face cache and could not be downloaded ({_exc}). Connect once, or copy the model files in, then restart the kernel."), kind="danger"))
    rs_article_vectors = embed_texts(rs_embedding_model, [f"{article['title']}. {article['body']}" for article in cairn.HELP_ARTICLES])
    rs_embedding_dimension = int(rs_article_vectors.shape[1])
    rs_load_button = mo.ui.run_button(label="Load the help centre: 1 FT.CREATE, 36 HSET, 36 EXPIRE", kind="success")
    mo.vstack([
        mo.callout(mo.md(f"Connected to Redis {rs_redis_version}. `FT.HYBRID` {'is available' if rs_has_hybrid else 'needs Redis 8.4 or later, so the hybrid rank below becomes a note'}. {EMBEDDING_MODEL_NAME}: {rs_embedding_dimension} dimensions, {rs_embedding_dimension * 4} bytes per vector, computed on this machine."), kind="success"),
        mo.md(r"""The button below writes one index and 36 hashes to your database, each hash with a one-hour `EXPIRE`. The index watches the prefix `cairn:<run>:help:`, so every `HSET` under it is indexed on write:"""),
        rs_load_button,
    ])
    return (
        IndexDefinition,
        IndexType,
        NumericField,
        Query,
        ResponseError,
        TagField,
        TextField,
        VectorField,
        rs_article_keys,
        rs_article_titles,
        rs_article_vectors,
        rs_client,
        rs_embedding_dimension,
        rs_embedding_model,
        rs_has_hybrid,
        rs_index_name,
        rs_key_prefix,
        rs_load_button,
        rs_redis_version,
    )


@app.cell(column=0)
def rs_load(
    IndexDefinition,
    IndexType,
    NumericField,
    ResponseError,
    TagField,
    TextField,
    VectorField,
    rs_article_keys,
    rs_article_vectors,
    rs_client,
    rs_embedding_dimension,
    rs_index_name,
    rs_key_prefix,
    rs_load_button,
):
    mo.stop(not rs_load_button.value, mo.callout(mo.md("Press *Load the help centre: 1 FT.CREATE, 36 HSET, 36 EXPIRE* above."), kind="info"))
    rs_schema = (
        TextField("title", weight=2.0),
        TextField("body"),
        TagField("category"),
        NumericField("year", sortable=True),
        VectorField("embedding", "HNSW", {"TYPE": "FLOAT32", "DIM": rs_embedding_dimension, "DISTANCE_METRIC": "COSINE"}),
    )
    rs_index_definition = IndexDefinition(prefix=[rs_key_prefix], index_type=IndexType.HASH)
    try:
        rs_client.ft(rs_index_name).create_index(rs_schema, definition=rs_index_definition)   # FT.CREATE
    except ResponseError as _exc:
        if "already exists" not in str(_exc).lower():   # pressing Load again is an update
            raise
    _first_hset_argv = None
    for _start in range(0, len(cairn.HELP_ARTICLES), 12):   # three chunks of 12, under the free tier's 100 ops/s
        _pipe = rs_client.pipeline(transaction=False)
        for _article, _vector in zip(cairn.HELP_ARTICLES[_start:_start + 12], rs_article_vectors[_start:_start + 12]):
            _key = f"{rs_key_prefix}{_article['id']}"
            _pipe.hset(_key, mapping={
                "title": _article["title"], "body": _article["body"], "category": _article["category"],
                "year": _article["year"], "embedding": _vector.astype(np.float32).tobytes(),   # DIM x 4 raw bytes
            })
            _pipe.expire(_key, KEY_TTL_SECONDS)
        _first_hset_argv = _first_hset_argv or _pipe.command_stack[0][0]   # the first HSET as redis-py sends it
        _pipe.execute()
        time.sleep(0.25)
    _info = rs_client.ft(rs_index_name).info()   # FT.INFO
    rs_documents_indexed = int(_info["num_docs"])
    _indexing_failures = int(_info["hash_indexing_failures"])
    rs_help_centre_loaded = True
    mo.vstack([
        mo.hstack([mo.stat(rs_documents_indexed, label="num_docs"), mo.stat(_indexing_failures, label="hash_indexing_failures")]),
        mo.md(f"```\n{redis_cli(['FT.CREATE', rs_index_name, *rs_index_definition.args, 'SCHEMA', *[arg for field in rs_schema for arg in field.redis_args()]])}\n{redis_cli(_first_hset_argv)}\n{redis_cli(['EXPIRE', rs_article_keys[0], KEY_TTL_SECONDS])}\n```"),
        mo.md(f"""Redis returns `OK` for the `FT.CREATE` and indexes each hash as it lands. `FT.INFO` reports `num_docs` {rs_documents_indexed} and `hash_indexing_failures` {_indexing_failures}: a hash that fails to index is counted there, not dropped silently."""),
    ])
    return (rs_help_centre_loaded,)


@app.cell(column=0)
def rs_full_text(Query, rs_client, rs_help_centre_loaded, rs_index_name):
    mo.stop(not rs_help_centre_loaded, mo.callout(mo.md("Load the help centre first."), kind="info"))
    _query = Query("hiking boots").scorer("BM25STD").with_scores().return_fields("title", "category").paging(0, 5)
    rs_full_text_result = rs_client.ft(rs_index_name).search(_query)   # FT.SEARCH
    mo.vstack([
        mo.md(r"""The cell below runs one keyword search for *hiking boots*, scored with `BM25STD`. Redis returns the match count and the top five rows with their scores:"""),
        mo.md(f"```\n{redis_cli(['FT.SEARCH', rs_index_name, *_query.get_args()])}\n```"),
        mo.md(f"{rs_full_text_result.total} matches"),
        mo.ui.table(
            [{"title": doc.title, "category": doc.category, "score": round(float(doc.score), 3)} for doc in rs_full_text_result.docs],
            selection=None, show_download=False, pagination=False,
        ),
        mo.md(r"""A title match counts double because the schema gave `title` weight 2."""),
    ])
    return


@app.cell(column=0)
def rs_search_controls(rs_help_centre_loaded):
    mo.stop(not rs_help_centre_loaded)
    rs_question = mo.ui.text(value="will it fit my feet", label="Ask the help centre", full_width=True)
    mo.vstack([
        mo.md(r"""Redis runs the same query vector two ways. `KNN 3` returns the three nearest articles whatever their distance, and `VECTOR_RANGE` returns only what lies within a radius, which can be nothing:"""),
        rs_question,
        mo.md(r"""Ask *best pizza near me* for a miss."""),
    ])
    return (rs_question,)


@app.cell(column=0)
def rs_knn_versus_range(
    Query,
    rs_client,
    rs_embedding_model,
    rs_help_centre_loaded,
    rs_index_name,
    rs_question,
):
    mo.stop(not rs_help_centre_loaded)
    mo.stop(not rs_question.value.strip(), mo.callout(mo.md("Type a question above."), kind="info"))
    rs_query_vector_bytes = embed_texts(rs_embedding_model, [rs_question.value])[0].astype(np.float32).tobytes()
    _knn = Query("*=>[KNN 3 @embedding $vector AS vector_score]").sort_by("vector_score").return_fields("title", "category", "vector_score").paging(0, 3)
    _range = Query("@embedding:[VECTOR_RANGE $radius $vector]=>{$YIELD_DISTANCE_AS: vector_score}").sort_by("vector_score").return_fields("title", "category", "vector_score").paging(0, 10)
    _knn_params = {"vector": rs_query_vector_bytes}
    _range_params = {"radius": 0.5, "vector": rs_query_vector_bytes}   # edit the radius here. 0.5 is a starting point for this model
    rs_knn_result = rs_client.ft(rs_index_name).search(_knn, _knn_params)
    rs_range_result = rs_client.ft(rs_index_name).search(_range, _range_params)
    _knn_table = mo.ui.table(
        [{"title": doc.title, "category": doc.category, "vector_score": round(float(doc.vector_score), 3)} for doc in rs_knn_result.docs],
        selection=None, show_download=False, pagination=False,
    )
    if rs_range_result.total:
        _range_view = mo.ui.table(
            [{"title": doc.title, "category": doc.category, "vector_score": round(float(doc.vector_score), 3)} for doc in rs_range_result.docs],
            selection=None, show_download=False, pagination=False,
        )
    else:
        _range_view = mo.callout(mo.md("0 articles within radius 0.5. KNN could never say this, and this empty answer is what LangCache turns into a cache miss in the next column."), kind="warn", title="MISS")
    mo.vstack([
        mo.md(f"**KNN 3** for *{rs_question.value}*"),
        _knn_table,
        mo.md(f"**VECTOR_RANGE 0.5** for *{rs_question.value}*"),
        _range_view,
        mo.accordion({"redis-cli": mo.md(f"```\n{redis_cli(['FT.SEARCH', rs_index_name, *_knn.get_args(), *rs_client.ft(rs_index_name).get_params_args(_knn_params)])}\n{redis_cli(['FT.SEARCH', rs_index_name, *_range.get_args(), *rs_client.ft(rs_index_name).get_params_args(_range_params)])}\n```")}),
        mo.md(r"""KNN always fills three rows. The range answers with what is close enough, or with nothing."""),
    ])
    return


@app.cell(column=0)
def rs_prefiltered(
    Query,
    rs_client,
    rs_embedding_model,
    rs_help_centre_loaded,
    rs_index_name,
):
    mo.stop(not rs_help_centre_loaded)
    _params = {"vector": embed_texts(rs_embedding_model, ["where is my parcel"])[0].astype(np.float32).tobytes()}
    _unfiltered = Query("*=>[KNN 5 @embedding $vector AS vector_score]").sort_by("vector_score").return_fields("title", "category", "year", "vector_score").paging(0, 5)
    _filtered = Query("(@category:{returns|shipping} @year:[2024 2026])=>[KNN 5 @embedding $vector AS vector_score]").sort_by("vector_score").return_fields("title", "category", "year", "vector_score").paging(0, 5)
    _unfiltered_result = rs_client.ft(rs_index_name).search(_unfiltered, _params)
    _filtered_result = rs_client.ft(rs_index_name).search(_filtered, _params)
    _params_args = rs_client.ft(rs_index_name).get_params_args(_params)
    mo.vstack([
        mo.md(r"""The cell below runs the same `KNN 5` twice for *where is my parcel*, once over every article and once only over returns and shipping articles from 2024 on. The filtered query string is the whole lesson:"""),
        mo.md(f"```\n{redis_cli(['FT.SEARCH', rs_index_name, *_unfiltered.get_args(), *_params_args])}\n{redis_cli(['FT.SEARCH', rs_index_name, *_filtered.get_args(), *_params_args])}\n```"),
        mo.md(r"""**Every article**"""),
        mo.ui.table([{"title": doc.title, "category": doc.category, "year": doc.year, "vector_score": round(float(doc.vector_score), 3)} for doc in _unfiltered_result.docs], selection=None, show_download=False, pagination=False),
        mo.md(r"""**Returns and shipping, 2024 to 2026**"""),
        mo.ui.table([{"title": doc.title, "category": doc.category, "year": doc.year, "vector_score": round(float(doc.vector_score), 3)} for doc in _filtered_result.docs], selection=None, show_download=False, pagination=False),
        mo.md(r"""The filter runs inside the vector search, so all five rows qualify, instead of a top five with the wrong ones thrown away."""),
    ])
    return


@app.cell(column=0)
def rs_engine_preset(rs_help_centre_loaded):
    mo.stop(not rs_help_centre_loaded)
    rs_preset = mo.ui.dropdown(options={preset["label"]: preset["query"] for preset in cairn.SEARCH_PRESETS}, value="Same meaning, different words", label="Question")
    mo.vstack([
        mo.md(r"""Keyword search matches words and vector search matches meaning, and each fails where the other wins. The cell below sends one question through `BM25STD`, through KNN, and through `FT.HYBRID`, which fuses both rankings on the server:"""),
        rs_preset,
    ])
    return (rs_preset,)


@app.cell(column=0)
def rs_engine_results(
    Query,
    ResponseError,
    rs_article_titles,
    rs_client,
    rs_embedding_model,
    rs_has_hybrid,
    rs_help_centre_loaded,
    rs_index_name,
    rs_key_prefix,
    rs_preset,
    rs_redis_version,
):
    mo.stop(not rs_help_centre_loaded)
    rs_engine_question = rs_preset.value
    rs_engine_target = next(preset["target"] for preset in cairn.SEARCH_PRESETS if preset["query"] == rs_engine_question)
    _vector = embed_texts(rs_embedding_model, [rs_engine_question])[0].astype(np.float32).tobytes()
    _bm25 = Query(rs_engine_question).scorer("BM25STD").return_fields("title").paging(0, 10)
    _knn = Query("*=>[KNN 10 @embedding $vector AS vector_score]").sort_by("vector_score").return_fields("title").paging(0, 10)
    _rankings = {"BM25 rank": [doc.id for doc in rs_client.ft(rs_index_name).search(_bm25).docs]}
    _rankings["vector rank"] = [doc.id for doc in rs_client.ft(rs_index_name).search(_knn, {"vector": _vector}).docs]
    _lines = [
        redis_cli(["FT.SEARCH", rs_index_name, *_bm25.get_args()]),
        redis_cli(["FT.SEARCH", rs_index_name, *_knn.get_args(), *rs_client.ft(rs_index_name).get_params_args({"vector": _vector})]),
    ]
    _hybrid_note = mo.callout(mo.md(f"`FT.HYBRID` needs Redis 8.4 or later. This database is {rs_redis_version}."), kind="info")
    if rs_has_hybrid:
        _hybrid_argv = [
            "FT.HYBRID", rs_index_name, "SEARCH", rs_engine_question, "SCORER", "BM25STD",
            "VSIM", "@embedding", "$vector", "KNN", 2, "K", 10, "COMBINE", "RRF", 4, "WINDOW", 20, "CONSTANT", 60,   # the documented defaults, made visible
            "LOAD", 1, "@__key", "LIMIT", 0, 10, "PARAMS", 2, "vector", _vector,
        ]
        _lines.append(redis_cli(_hybrid_argv))
        try:
            _reply = rs_client.execute_command(*_hybrid_argv)   # RESP2: a flat pair list whose results are field lists
            _fields = dict(zip(_reply[::2], _reply[1::2]))
            _rankings["hybrid rank"] = [dict(zip(row[::2], row[1::2]))["__key"] for row in _fields["results"]]
            _hybrid_note = None
        except ResponseError as _exc:
            _hybrid_note = mo.callout(mo.md(f"`FT.HYBRID` answered `{type(_exc).__name__}` on Redis {rs_redis_version}, so the table shows two engines."), kind="info")
    _ranked_keys = list(dict.fromkeys([*_rankings.get("hybrid rank", []), *_rankings["vector rank"], *_rankings["BM25 rank"]]))
    rs_engine_rows = [
        {"article": ("* " if key == f"{rs_key_prefix}{rs_engine_target}" else "") + rs_article_titles.get(key, key), **{engine: (ranking.index(key) + 1 if key in ranking else "-") for engine, ranking in _rankings.items()}}
        for key in _ranked_keys
    ]
    _lessons = {
        "size-boots": "*Trail shoes feel cramped* shares no word with the boot-sizing guide, so `BM25STD` returns nothing and the vector search ranks it first.",
        "warranty-resole": "*Trailhead* is a product name the model has never seen, so the keyword engine wins.",
        "care-waterproof": "*waterproofng* matches no word, yet its vector still lands on waterproofing.",
    }
    mo.vstack([
        mo.md(f"Question: **{rs_engine_question}**. The starred row is the article that answers it."),
        mo.ui.table(rs_engine_rows, selection=None, show_download=False, pagination=False),
        *([_hybrid_note] if _hybrid_note else []),
        mo.md(_lessons[rs_engine_target] + (" In a live run on 2026-09-30, `FT.HYBRID` kept the right article first in all three cases." if "hybrid rank" in _rankings else "")),
        mo.accordion({"redis-cli": mo.md("```\n" + "\n".join(_lines) + "\n```")}),
    ])
    return


@app.cell(column=0, hide_code=True)
def rs_cleanup_button():
    rs_cleanup_button = mo.ui.run_button(label="Clean up: FT.DROPINDEX and UNLINK 36 keys", kind="danger")
    mo.vstack([
        mo.md(r"""Clean up drops the index with `FT.DROPINDEX` and no `DD`, then unlinks the 36 keys by name. Nothing is scanned, and every key would expire within the hour in any case:"""),
        rs_cleanup_button,
    ])
    return (rs_cleanup_button,)


@app.cell(column=0)
def rs_cleanup(
    ResponseError,
    rs_article_keys,
    rs_cleanup_button,
    rs_client,
    rs_index_name,
):
    mo.stop(not rs_cleanup_button.value)
    try:
        rs_client.ft(rs_index_name).dropindex()   # FT.DROPINDEX with no DD, so the documents stay
        _index_report = "Dropped 1 index"
    except ResponseError as _exc:
        if not re.search(r"index[_ ]not[_ ]found|unknown index|no such index", str(_exc), re.I):
            raise
        _index_report = "The index was already gone"
    rs_keys_removed = rs_client.unlink(*rs_article_keys)   # UNLINK key1 ... key36, by name
    mo.vstack([
        mo.callout(mo.md(f"{_index_report}, unlinked {rs_keys_removed} of {len(rs_article_keys)} keys. Redis returns the count of keys that existed."), kind="success"),
        mo.md(f"```\n{redis_cli(['FT.DROPINDEX', rs_index_name])}\n{redis_cli(['UNLINK', *rs_article_keys[:2]])} ... ({len(rs_article_keys)} keys)\n```"),
        mo.md(r"""`DD` would delete every key under the prefix, and this notebook never trusts a prefix over a list it holds."""),
    ])
    return


@app.cell(column=1, hide_code=True)
def lc_title():
    mo.vstack([
        mo.md(r"""
    # Redis LangCache

    Users rarely ask the same question the same way twice, and an exact-key cache misses every
    rewording. Redis LangCache matches prompts by meaning: it embeds the prompt, searches the cache,
    and returns the stored answer when the similarity clears a threshold. Its one job in this
    column: return a stored answer for a question asked in other words.

    A match is a score above a line, so a wrong answer for a question that only sounds similar is
    possible. The threshold is the dial, and this column turns it. LangCache is in public preview on
    Redis Cloud. Settings: `LANGCACHE_HOST`, `LANGCACHE_CACHE_ID`, and `LANGCACHE_API_KEY`. Docs:
    [LangCache](https://redis.io/docs/latest/develop/ai/context-engine/langcache/),
    [API reference](https://redis.io/docs/latest/develop/ai/context-engine/langcache/api-reference/).
    """),
        mo.mermaid("""flowchart LR
    P[Prompt] --> S["POST /entries/search"]
    S -->|"similarity >= threshold"| H[Stored answer]
    S -->|"data: empty"| L[Call the LLM]
    L --> W["POST /entries with ttlMillis"]
    W -.-> S"""),
        mo.accordion({
            "The same calls with the `langcache` SDK (not installed here)": mo.md(r"""
    ```python
    from langcache import LangCache

    with LangCache(server_url="https://<host>", cache_id="<cache id>", api_key="<api key>") as cache:
        cache.search(prompt="How do I wash a down sleeping bag?", similarity_threshold=0.85)
        cache.set(prompt="...", response="...", ttl_millis=900_000)
        cache.delete_by_id(entry_id="<entryId>")
    ```

    This notebook sends the JSON itself so you can read it.
    """),
        }),
    ])
    return


@app.cell(column=1)
def lc_configure():
    mo.stop(not all(os.environ.get(name) for name in ("LANGCACHE_HOST", "LANGCACHE_CACHE_ID", "LANGCACHE_API_KEY")), mo.callout(mo.md("LangCache is not configured. Set `LANGCACHE_HOST`, `LANGCACHE_CACHE_ID`, and `LANGCACHE_API_KEY` in the `.env` next to `pyproject.toml` (see `.env.example`), then restart the kernel."), kind="warn"))
    _host = os.environ["LANGCACHE_HOST"].strip().rstrip("/")
    if "://" not in _host:
        _host = "https://" + _host
    lc_service = niquests.Session(
        base_url=f"{_host}/v1/caches/{os.environ['LANGCACHE_CACHE_ID'].strip()}",
        headers={
            "Authorization": f"Bearer {os.environ['LANGCACHE_API_KEY'].strip()}",   # the key lives here and nowhere else
            "Accept": "application/json",
            "Content-Type": "application/json",   # pinned; niquests' default adds ;charset=utf-8, untested against the services
            "User-Agent": "redis-iris-basics",
        },
        timeout=(5, 30),        # seconds: connect, read
        disable_http3=True,     # HTTP/1.1 or HTTP/2 only, so the wire view reads the same on every machine
    )
    lc_entry_ttl_milliseconds = 900_000   # 15 minutes: every entry this column stores expires on its own

    def lc_own_hits(reply, own_ids):
        """This kernel's hits, and a copy of every hit that is safe to show."""
        hits = reply.get("data", []) if isinstance(reply, dict) else []
        mine = [hit for hit in hits if hit.get("id") in own_ids]
        shown = [hit if hit.get("id") in own_ids else {"hidden": "another app's entry", "similarity": hit.get("similarity")} for hit in hits]
        return mine, shown

    try:
        lc_check_status, _body, lc_check_wire = call_service(lc_service, "POST", "/entries/search", json_body={"prompt": "Cairn Outfitters connection check", "maxResults": 1})
    except niquests.RequestException as exc:
        mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while reaching LangCache. Check `LANGCACHE_HOST`."), kind="danger"))
    _type = _body.get("type", "") if isinstance(_body, dict) else ""
    _hint = {401: "check `LANGCACHE_API_KEY`", 404: "check `LANGCACHE_CACHE_ID`"}.get(lc_check_status, "read the body below")
    mo.stop(lc_check_status != 200, mo.callout(mo.vstack([mo.md(f"LangCache answered `{lc_check_status}` `{_type}`: {_hint}."), mo.json(_body if isinstance(_body, dict) else {"body": _body})]), kind="danger"))
    lc_store_button = mo.ui.run_button(label="Store 5 gear-care answers with ttlMillis 900000", kind="success")
    mo.vstack([
        mo.callout(mo.md(f"Connected: LangCache answered 200 in {lc_check_wire['ms']} ms."), kind="success"),
        mo.accordion({"Request as sent (credential masked)": mo.json(lc_check_wire)}),
        mo.md(r"""The button below stores five gear-care answers, each with `ttlMillis` 900000 so it expires in 15 minutes whether or not you press Clean up. LangCache answers `201` with an `entryId`:"""),
        lc_store_button,
    ])
    return lc_entry_ttl_milliseconds, lc_own_hits, lc_service, lc_store_button


@app.cell(column=1)
def lc_store(lc_entry_ttl_milliseconds, lc_service, lc_store_button):
    if not lc_store_button.value:
        lc_stored_entries, lc_store_wire = [], None
        _output = mo.callout(mo.md("Press *Store 5 gear-care answers with ttlMillis 900000* above."), kind="info")
    else:
        lc_stored_entries, lc_store_wire, _first_body, _problems = [], None, None, []
        for _row in cairn.GEAR_CARE_ANSWERS:
            _status, _body, _wire = call_service(lc_service, "POST", "/entries", json_body={"prompt": _row["prompt"], "response": _row["response"], "ttlMillis": lc_entry_ttl_milliseconds})
            lc_store_wire, _first_body = lc_store_wire or _wire, _first_body or _body
            if _status == 201 and isinstance(_body, dict) and _body.get("entryId"):
                lc_stored_entries.append({"key": _row["key"], "prompt": _row["prompt"], "entryId": _body["entryId"]})
            else:
                _problems.append({"key": _row["key"], "status": _status, "type": _body.get("type", "") if isinstance(_body, dict) else ""})
        _parts = [
            mo.ui.table(lc_stored_entries, selection=None, pagination=False),
            mo.accordion({"First request as sent (credential masked)": mo.json(lc_store_wire), "Reply": mo.json(_first_body or {})}),
            mo.md(r"""LangCache embeds each prompt as it is stored and answers `201` with the `entryId` you will delete later. Those ids are the only handle on the entries, because a cache cannot be listed."""),
        ]
        if _problems:
            _parts.append(mo.callout(mo.vstack([mo.md(f"LangCache refused {len(_problems)} of the 5 stores. The rows that answered `201` are kept."), mo.json(_problems)]), kind="danger"))
        _output = mo.vstack(_parts)
    _output
    return (lc_stored_entries,)


@app.cell(column=1)
def lc_anatomy_of_a_hit(lc_own_hits, lc_service, lc_stored_entries):
    mo.stop(not lc_stored_entries, mo.callout(mo.md("Store the answers first."), kind="info"))
    _status, _body, _wire = call_service(lc_service, "POST", "/entries/search", json_body={"prompt": "My rain jacket stopped beading water. How do I fix that?", "similarityThreshold": 0.85, "searchStrategies": ["semantic"], "maxResults": 5})
    _mine, _shown = lc_own_hits(_body, {entry["entryId"] for entry in lc_stored_entries})
    _type = _body.get("type", "") if isinstance(_body, dict) else ""
    mo.stop(_status != 200, mo.callout(mo.vstack([mo.md(f"LangCache answered `{_status}` `{_type}`: read the body below."), mo.json(_body if isinstance(_body, dict) else {"body": _body})]), kind="danger"))
    mo.vstack([
        mo.md(r"""The cell below asks a question none of the five prompts contains, at `similarityThreshold` 0.85, the console default for a new service. LangCache returns every entry whose similarity clears that line:"""),
        mo.callout(mo.md(f"LangCache answered `{_status}` with {len(_mine)} of this kernel's entries above the line in {_wire['ms']} ms."), kind="success" if _mine else "warn"),
        mo.accordion({"Request as sent (credential masked)": mo.json(_wire), "Reply (other apps' entries hidden)": mo.json({"data": _shown})}, multiple=True, expanded=True),
        mo.md(r"""`similarity` is the cosine similarity of the two prompt embeddings. In a live run on 2026-09-30 the rain-shell answer came back at 0.896 with no shared noun but *rain*. A miss is `200` with an empty `data`, never a `404`."""),
    ])
    return


@app.cell(column=1)
def lc_probes_and_slider(lc_own_hits, lc_service, lc_stored_entries):
    mo.stop(not lc_stored_entries)
    _own_keys = {entry["entryId"]: entry["key"] for entry in lc_stored_entries}
    lc_probe_scores, _first_wire = [], None
    for _probe in cairn.THRESHOLD_PROBES:
        _status, _body, _wire = call_service(lc_service, "POST", "/entries/search", json_body={"prompt": _probe["text"], "similarityThreshold": 0.5, "searchStrategies": ["semantic"], "maxResults": 5})
        _first_wire = _first_wire or _wire
        _mine, _shown = lc_own_hits(_body, set(_own_keys))
        _best = max(_mine, key=lambda hit: hit["similarity"], default=None)
        lc_probe_scores.append({
            "text": _probe["text"], "kind": _probe["kind"], "target": _probe["target"],
            "status": _status, "type": _body.get("type", "") if isinstance(_body, dict) else "",
            "best_key": _own_keys[_best["id"]] if _best else None,
            "similarity": _best["similarity"] if _best else None,
        })
    lc_threshold = mo.ui.slider(0.5, 1.0, step=0.01, value=0.85, debounce=True, show_value=True, label="similarityThreshold")
    mo.vstack([
        mo.md(r"""The cell below searches six labelled questions once each at `similarityThreshold` 0.50, low enough to see every score the server computed. The slider re-applies the cut on your machine, the same cut the server applies when you send the value:"""),
        mo.accordion({"First request as sent (credential masked)": mo.json(_first_wire)}),
        lc_threshold,
    ])
    return lc_probe_scores, lc_threshold


@app.cell(column=1)
def lc_threshold_table(lc_probe_scores, lc_threshold):
    _cut = round(lc_threshold.value, 2)
    _rows = []
    for _probe in lc_probe_scores:
        _above = _probe["similarity"] is not None and _probe["similarity"] >= _cut
        if _probe["status"] != 200:
            _verdict = f"{_probe['status']} {_probe['type']}".strip()   # the search failed, so this row scores nothing
        elif _above and _probe["best_key"] == _probe["target"]:
            _verdict = "HIT"
        elif _above:
            _verdict = "WRONG ANSWER"
        else:
            _verdict = "MISS"
        _rows.append({
            "question": _probe["text"], "kind": _probe["kind"], "best match": _probe["best_key"] or "-",
            "similarity": round(_probe["similarity"], 3) if _probe["similarity"] is not None else "-", "verdict": _verdict,
        })
    _tints = {"HIT": "rgba(46, 160, 67, 0.3)", "WRONG ANSWER": "rgba(248, 81, 73, 0.3)", "MISS": "rgba(110, 118, 129, 0.3)"}   # anything else is amber
    mo.vstack([
        mo.md(r"""Raise the slider until the red rows go, and watch the grey ones multiply:"""),
        mo.ui.table(_rows, selection=None, pagination=False, style_cell=lambda row_id, column, value: {"backgroundColor": _tints.get(value, "rgba(210, 153, 34, 0.3)")} if column == "verdict" else {}),
        mo.md(r"""In a live run on 2026-09-30 the worst wrong answer scored 0.950: the synthetic bag was served the down-bag answer. Nothing wrong was left from 0.96 up, and most paraphrases missed too. Scores vary by model and phrasing, and the trade-off does not."""),
    ])
    return


@app.cell(column=1)
def lc_strategies(lc_own_hits, lc_service, lc_stored_entries):
    mo.stop(not lc_stored_entries)
    _own_keys = {entry["entryId"]: entry["key"] for entry in lc_stored_entries}
    _rows, _first_wire = [], None
    for _variant in cairn.STRATEGY_VARIANTS:   # both variants respell the stored down-bag prompt, so "bag" is the one right entry
        _row = {"variant": _variant["label"], "prompt sent": _variant["text"]}
        for _strategy in ("exact", "semantic"):
            _status, _body, _wire = call_service(lc_service, "POST", "/entries/search", json_body={"prompt": _variant["text"], "similarityThreshold": 0.85, "searchStrategies": [_strategy], "maxResults": 5})
            _first_wire = _first_wire or _wire
            _mine, _shown = lc_own_hits(_body, set(_own_keys))
            _best = max(_mine, key=lambda hit: hit["similarity"], default=None)
            _type = _body.get("type", "") if isinstance(_body, dict) else ""
            _verdict = f"{_status} {_type}".strip() if _status != 200 else ("MISS" if _best is None else f"HIT {_best['similarity']:.2f}")
            _row[_strategy] = "HIT (wrong entry)" if _best and _own_keys[_best["id"]] != "bag" else _verdict
        _rows.append(_row)
    mo.vstack([
        mo.md(r"""`searchStrategies` picks the matcher: `exact` is a case-insensitive string compare that reports `similarity` 1, and `semantic` is the embedding. The cell below sends two spellings of a stored prompt through each:"""),
        mo.ui.table(_rows, selection=None, pagination=False),
        mo.accordion({"First request as sent (credential masked)": mo.json(_first_wire)}),
        mo.md(r"""The default is `semantic` alone. In a live run on 2026-09-30, *HOW DO I WASH MY DOWN SLEEPING BAG?* hit under `exact` and missed under `semantic`, and dropping the question mark did the reverse."""),
    ])
    return


@app.cell(column=1, hide_code=True)
def lc_cache_aside_button():
    lc_cache_aside_button = mo.ui.run_button(label="Run the cache-aside loop: miss, store for 10 s, hit, expire, 404", kind="success")
    mo.vstack([
        mo.md(r"""The button below plays the cache-aside loop with you as the model: a miss, a store with `ttlMillis` 10000, a hit on a reworded question, a wait, a second miss, then a `DELETE` that finds nothing. Every prompt is prefixed *At Cairn Outfitters:* so the entry stays clear of other apps' questions on a shared cache:"""),
        lc_cache_aside_button,
    ])
    return (lc_cache_aside_button,)


@app.cell(column=1)
def lc_cache_aside(
    lc_cache_aside_button,
    lc_own_hits,
    lc_service,
    lc_stored_entries,
):
    mo.stop(not lc_cache_aside_button.value, mo.callout(mo.md("Press *Run the cache-aside loop: miss, store for 10 s, hit, expire, 404* above."), kind="info"))
    _script = cairn.CACHE_ASIDE_SCRIPT
    _own = {entry["entryId"] for entry in lc_stored_entries}
    _search = {"similarityThreshold": 0.85, "searchStrategies": ["semantic"], "maxResults": 5}
    _steps, _wires = [], []
    _status, _body, _wire = call_service(lc_service, "POST", "/entries/search", json_body={"prompt": _script["prefix"] + _script["ask"], **_search})
    _mine, _shown = lc_own_hits(_body, _own)
    _steps.append({"step": "1 ask", "request": "POST /entries/search", "status": _status, "similarity": f"hit {_mine[0]['similarity']:.3f}" if _mine else "miss", "ms": _wire["ms"]})
    _wires.append(_wire)
    _status, _body, _wire = call_service(lc_service, "POST", "/entries", json_body={"prompt": _script["prefix"] + _script["ask"], "response": _script["answer"], "ttlMillis": 10_000})
    _taught_id = _body.get("entryId") if isinstance(_body, dict) else None
    _type = _body.get("type", "") if isinstance(_body, dict) else ""
    mo.stop(_status != 201 or not _taught_id, mo.callout(mo.vstack([mo.md(f"LangCache answered `{_status}` `{_type}` to the store, so the loop stops here. Read the body below."), mo.json(_body if isinstance(_body, dict) else {"body": _body})]), kind="danger"))
    _steps.append({"step": "2 store the answer for 10 s", "request": "POST /entries", "status": _status, "similarity": "-", "ms": _wire["ms"]})
    _wires.append(_wire)
    _status, _body, _wire = call_service(lc_service, "POST", "/entries/search", json_body={"prompt": _script["prefix"] + _script["again"], **_search})
    _mine, _shown = lc_own_hits(_body, _own | {_taught_id})
    _steps.append({"step": "3 ask again in other words", "request": "POST /entries/search", "status": _status, "similarity": f"hit {_mine[0]['similarity']:.3f}" if _mine else "miss", "ms": _wire["ms"]})
    _wires.append(_wire)
    with mo.status.spinner(title="Waiting 12 s for ttlMillis to fire"):
        time.sleep(12)
    _status, _body, _wire = call_service(lc_service, "POST", "/entries/search", json_body={"prompt": _script["prefix"] + _script["again"], **_search})
    _mine, _shown = lc_own_hits(_body, _own | {_taught_id})
    _steps.append({"step": "4 ask again after 12 s", "request": "POST /entries/search", "status": _status, "similarity": f"hit {_mine[0]['similarity']:.3f}" if _mine else "miss", "ms": _wire["ms"]})
    _wires.append(_wire)
    _status, _body, _wire = call_service(lc_service, "DELETE", f"/entries/{_taught_id}")
    _type = _body.get("type", "") if isinstance(_body, dict) else ""
    _steps.append({"step": "5 delete the taught entry", "request": "DELETE /entries/{entryId}", "status": "204: not expired yet, deleted now" if _status == 204 else f"{_status} {_type}".strip(), "similarity": "-", "ms": _wire["ms"]})
    _wires.append(_wire)
    mo.vstack([
        mo.ui.table(_steps, selection=None, pagination=False),
        mo.accordion({"Requests as sent (credential masked)": mo.json(_wires)}),
        mo.md(r"""The miss at step 1 is where your app would call the model, and step 2 is you storing its answer. The `404` at the end is why this notebook can rely on TTL alone."""),
    ])
    return


@app.cell(column=1, hide_code=True)
def lc_cleanup_button():
    lc_cleanup_button = mo.ui.run_button(label="Clean up: DELETE 5 entries by id", kind="danger")
    mo.vstack([
        mo.md(r"""Clean up sends one `DELETE /entries/{entryId}` per id this kernel stored. A `204` is a deletion, and a `404` means the TTL got there first:"""),
        lc_cleanup_button,
    ])
    return (lc_cleanup_button,)


@app.cell(column=1)
def lc_cleanup(lc_cleanup_button, lc_service, lc_stored_entries):
    mo.stop(not lc_cleanup_button.value)
    _gone, _problems, _wires = [], [], []
    for _entry in lc_stored_entries:
        # The session pins Content-Type, so this bodiless DELETE carries it. The live-verified request carried neither.
        _status, _body, _wire = call_service(lc_service, "DELETE", f"/entries/{_entry['entryId']}")
        _wires.append(_wire)
        _type = _body.get("type", "") if isinstance(_body, dict) else ""
        if _status == 204 or (_status == 404 and re.search(r"/(entry/not-found|resource[-/]not-found)$", _type)):
            _gone.append(_status)
        else:
            _problems.append({"key": _entry["key"], "status": _status, "type": _type})
    _summary = mo.md(f"Deleted {_gone.count(204)}, already expired {_gone.count(404)}." if lc_stored_entries else "Nothing stored in this run.")
    mo.vstack([
        mo.callout(_summary, kind="success") if not _problems else mo.callout(mo.vstack([_summary, mo.json(_problems)]), kind="danger"),
        mo.accordion({"Requests as sent (credential masked)": mo.json(_wires)}),
        mo.md(r"""LangCache cannot list a cache, so an entry a crashed kernel left behind is found only by its TTL, and every entry stored here carried one."""),
    ])
    return


@app.cell(column=2, hide_code=True)
def cr_title():
    mo.vstack([
        mo.md(r"""
    # Redis Context Retriever

    An agent that writes its own queries against your database is a governance problem. Redis
    Context Retriever takes a data model you declare in Python, provisions indexes in your own
    Redis, and generates MCP tools whose arguments are constrained by that model. Its one job in
    this column: turn three Python classes into MCP tools an agent can call over your Redis.

    The agent gets `get_customer_by_id` and `filter_customer`, never a raw command. Context
    Retriever is in preview. This column needs an admin key (`CTX_ADMIN_KEY`) and, in `REDIS_URL`, a
    scratch database with Search and JSON that the hosted service can reach, because the service is
    given that database's password. Docs:
    [Context Retriever](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/).
    """),
        mo.mermaid("""flowchart LR
    M[ContextModel classes] -->|export_data_model| J[data_model JSON]
    J -->|"POST /context-surfaces"| S["Surface + agent key"]
    S -->|"writes JSON, builds indexes"| R[(Your Redis)]
    A[Agent] -->|"tools/call over MCP"| S"""),
    ])
    return


@app.cell(column=2)
def cr_configure():
    from urllib.parse import unquote, urlsplit

    mo.stop(not (os.environ.get("CTX_ADMIN_KEY") and os.environ.get("REDIS_URL")), mo.callout(mo.md("Context Retriever is not configured. Set `CTX_ADMIN_KEY` and `REDIS_URL` in the `.env` next to `pyproject.toml` (see `.env.example`), then restart the kernel."), kind="warn"))
    cr_api_url = os.environ.get("CTX_API_URL", "https://cloud.redis.io/context-surfaces").rstrip("/")
    cr_mcp_url_default = os.environ.get("CTX_MCP_URL", "https://gcp-us-east4.context-surfaces.redis.io/mcp")   # fallback only; the live address comes from the agent-key listing
    cr_surface_name = f"cairn-{RUN_TAG}"
    cr_admin_service = niquests.Session(
        base_url=f"{cr_api_url}/api/v1",
        headers={"X-API-Key": os.environ["CTX_ADMIN_KEY"].strip(), "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "redis-iris-basics"},
        timeout=(5, 60),        # surface creation and import do real work
        disable_http3=True,
    )

    def cr_data_source():
        """The data_source block of the create request, built from REDIS_URL at call time so the password is never a global."""
        _url = urlsplit(os.environ["REDIS_URL"])
        return {"type": "redis", "connection_config": {
            "addr": f"{_url.hostname}:{_url.port or 6379}",
            "username": unquote(_url.username or "default"),
            "password": unquote(_url.password or ""),
            "db": int(_url.path.lstrip("/") or 0),
            "tls_enabled": _url.scheme == "rediss",   # TLS is the scheme; redis-py's connection_kwargs never carry an ssl key
            "pool_size": 2, "min_idle_conns": 0,      # a free database allows 30 connections
        }}

    try:
        cr_check_status, _body, cr_check_wire = call_service(cr_admin_service, "GET", "/context-surfaces", params={"page_size": 1})   # read-only key check
        cr_redis = connect_redis(os.environ["REDIS_URL"])
        cr_redis.ping()
    except niquests.RequestException as exc:
        mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while reaching the admin API. Check `CTX_API_URL`."), kind="danger"))
    except redis.RedisError as exc:
        mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while connecting. Check `REDIS_URL`."), kind="danger"))
    mo.stop(cr_check_status != 200, mo.callout(mo.vstack([mo.md(f"The admin API answered `{cr_check_status}` `{_body.get('type', '') if isinstance(_body, dict) else ''}`: {'check `CTX_ADMIN_KEY`' if cr_check_status == 401 else 'check `CTX_API_URL`' if cr_check_status == 404 else 'read the body below'}."), mo.json(_body if isinstance(_body, dict) else {"body": _body})]), kind="danger"))
    mo.vstack([mo.callout(mo.md("The admin API accepted this key, and Redis answered `PONG`."), kind="success"), mo.accordion({"Request as sent (credential masked)": mo.json(cr_check_wire)})])
    return (
        cr_admin_service,
        cr_data_source,
        cr_mcp_url_default,
        cr_redis,
        cr_surface_name,
    )


@app.cell(column=2)
def cr_data_model():
    from typing import Any
    from context_surfaces import ContextField, ContextModel, ContextRelationship, export_data_model

    mo.stop(sys.version_info >= (3, 14), mo.callout(mo.md("`ContextModel` needs Python 3.13 or older. Run `uv run marimo edit iris_basics.py`."), kind="warn"))

    # The service names its tools after these classes (get_customer_by_id), so they carry no cr_ prefix.
    class Customer(ContextModel):
        """A shopper at Cairn Outfitters, an outdoor-gear shop."""

        __redis_key_template__ = f"{KEY_PREFIX}customer:{{customer_id}}"
        customer_id: str = ContextField(description="Customer ID such as C001", is_key_component=True)
        name: str = ContextField(description="Full name", index="text", weight=2.0)
        tier: str = ContextField(description="Loyalty tier", index="tag", allowed_values=["bronze", "silver", "gold"])
        region: str = ContextField(description="Sales region: emea or apac", index="tag", allowed_values=["emea", "apac"])
        lifetime_value: float = ContextField(description="Lifetime spend in USD", index="numeric", sortable=True)
        orders: Any = ContextRelationship(description="Orders this customer placed", target="Order", source_field="customer_id")

    class Product(ContextModel):
        """An item in the Cairn catalogue."""

        __redis_key_template__ = f"{KEY_PREFIX}product:{{sku}}"
        sku: str = ContextField(description="Stock keeping unit such as P100", is_key_component=True)
        title: str = ContextField(description="Product title", index="text", weight=2.0)
        category: str = ContextField(description="Product category", index="tag")
        price: float = ContextField(description="List price in USD", index="numeric", sortable=True)

    class Order(ContextModel):
        """A purchase of one product by one customer."""

        __redis_key_template__ = f"{KEY_PREFIX}order:{{order_id}}"
        order_id: str = ContextField(description="Order ID such as O1001", is_key_component=True)
        customer_id: str = ContextField(description="Customer who placed the order", index="tag")
        sku: str = ContextField(description="Product ordered", index="tag")
        region: str = ContextField(description="Sales region: emea or apac", index="tag", allowed_values=["emea", "apac"])
        status: str = ContextField(description="placed, shipped, delivered, or returned", index="tag", allowed_values=["placed", "shipped", "delivered", "returned"])
        total: float = ContextField(description="Order total in USD", index="numeric", sortable=True)
        customer: Any = ContextRelationship(description="Buyer", target="Customer", source_field="customer_id")
        product: Any = ContextRelationship(description="Item bought", target="Product", source_field="sku")

    cr_entity_classes = {"Customer": Customer, "Product": Product, "Order": Order}
    assert all(cls.__redis_key_template__.startswith(KEY_PREFIX) for cls in cr_entity_classes.values())
    cr_data_model = export_data_model("Cairn Outfitters", "Customers, products, and orders of a fictional outdoor-gear shop", entities=[Customer, Product, Order])   # always entities=: without it the SDK exports every ContextModel ever defined in the kernel
    cr_record_keys = [cls.__redis_key_template__.format(**row) for entity, cls in cr_entity_classes.items() for row in cairn.SHOP_RECORDS[entity]]
    mo.vstack([
        mo.md(r"""
    The cell below declares the whole data model as three Python classes, and nothing has been sent
    yet. Each field's `index` kind decides which tools the service generates, and
    `__redis_key_template__` is where each record will be written in your database:
    """),
        mo.md(r"""
    | You declare | The service generates |
    |---|---|
    | `is_key_component=True` | `get_<entity>_by_id` |
    | Any entity | `list_<entity>` and `count_<entity>` |
    | A `numeric` field | `summarize_<entity>` |
    | A `tag` or `numeric` field | `filter_<entity>`, with that field in its menu |
    | A `text` field | `search_<entity>_by_text` |
    | A `ContextRelationship` | `expand_results`, plus the three set tools |
    """),
        mo.accordion({"export_data_model output (3 entities)": mo.json(cr_data_model)}),
    ])
    return cr_data_model, cr_entity_classes, cr_record_keys


@app.cell(column=2, hide_code=True)
def cr_create_button():
    cr_create_button = mo.ui.run_button(label="Create the surface, an agent key, and load 20 records (sends your database password)", kind="success")
    mo.vstack([
        mo.md(r"""
    The button below makes six admin calls: one `POST /context-surfaces` with the data model and your
    database password, one agent key with a one-hour `expires_at`, one `GET` for the surface's `mcp_url`,
    and one `POST .../data` per entity. The service keeps the password until you delete the surface:
    """),
        cr_create_button,
    ])
    return (cr_create_button,)


@app.cell(column=2)
def cr_create(
    cr_admin_service,
    cr_create_button,
    cr_data_model,
    cr_data_source,
    cr_entity_classes,
    cr_mcp_url_default,
    cr_record_keys,
    cr_redis,
    cr_surface_name,
):
    cr_surface_id, cr_surface_status, cr_mcp_service, cr_mcp_url, cr_agent_key_expires = None, None, None, None, None
    cr_indexes_before, cr_import_counts, cr_create_wire, cr_create_problem = set(), {}, None, None
    if not cr_create_button.value:
        _output = mo.callout(mo.md("Press *Create the surface, an agent key, and load 20 records (sends your database password)* above."), kind="info")
    else:
        _step, _status = "create", None
        try:   # nothing raises once a request may have gone out, so cr_surface_name reaches Clean up in this kernel
            cr_indexes_before = set(cr_redis.execute_command("FT._LIST"))
            _status, _body, _wire = call_service(cr_admin_service, "POST", "/context-surfaces", json_body={
                "name": cr_surface_name,
                "description": "Made by the Redis Iris basics notebook. Safe to delete.",
                "metadata": {"created_by": "iris-basics-notebook", "run": RUN_TAG},
                "data_model": cr_data_model,
                "data_source": cr_data_source(),
            })  # never retried (niquests default retries=0): a replayed create would make a second surface holding your password
            _wire["body"]["data_source"]["connection_config"]["password"] = "***"   # mask before this dict is bound to anything else
            cr_create_wire, _step = _wire, "reply"
        except (niquests.RequestException, redis.RedisError) as exc:
            cr_create_problem = f"{type(exc).__name__} at step {_step}"
        if cr_create_problem:
            _output = mo.callout(mo.md(f"`{cr_create_problem}`. The service may still have made the surface, so press Clean up below: it finds every surface named `{cr_surface_name}`."), kind="warn")
        elif _status != 201:
            # The request carried the database password. No 400/422 body from this API has ever been
            # observed and validation errors commonly echo their input, so the reply is never rendered:
            # only the status and three short strings, after checking the password is not in the body.
            _password = cr_data_source()["connection_config"]["password"]
            _body_text = json.dumps(_body) if _body is not None else ""
            _safe = isinstance(_body, dict) and (not _password or _password not in _body_text)
            _fields = {name: str(_body.get(name, ""))[:200] for name in ("type", "title", "detail")} if _safe else {}
            _output = mo.callout(mo.md(f"The admin API answered `{_status}` `{_fields.get('type', '')}`. {_fields.get('title', '')} {_fields.get('detail', '')}".strip() + (" The reply is not shown because it may echo the request." if not _safe else "")), kind="danger")
        else:
            try:
                cr_surface_id, cr_surface_status, _step = _body["id"], _body["status"], "agent key"     # provisioning_indices live
                _expires = (datetime.now(timezone.utc) + timedelta(seconds=KEY_TTL_SECONDS)).isoformat(timespec="seconds")
                _key_status, _key_body, _key_wire = call_service(cr_admin_service, "POST", f"/context-surfaces/{cr_surface_id}/agent-keys", json_body={"name": f"{cr_surface_name}-agent", "expires_at": _expires})
                cr_agent_key_expires, _step = _key_body.get("expires_at", _expires), "mcp_url"
                _list_status, _list_body, _list_wire = call_service(cr_admin_service, "GET", f"/context-surfaces/{cr_surface_id}/agent-keys", params={"page_size": 50})
                cr_mcp_url = next((item["mcp_url"] for item in ((_list_body.get("agent_keys") if isinstance(_list_body, dict) else None) or []) if item.get("id") == _key_body["id"] and item.get("mcp_url")), cr_mcp_url_default)
                cr_mcp_service = niquests.Session(headers={"X-API-Key": _key_body["key"], "Content-Type": "application/json", "User-Agent": "redis-iris-basics"}, timeout=(5, 30), disable_http3=True)   # the key goes here and nowhere else; no Accept header (the live run sent httpx's Accept: */*; a 406 means add "application/json, text/event-stream" and update the safety test)
                for _entity, _cls in cr_entity_classes.items():
                    _step = f"import {_entity}"
                    _import_status, _import_body, _import_wire = call_service(cr_admin_service, "POST", f"/context-surfaces/{cr_surface_id}/data", json_body={"entity": _entity, "records": [_cls(**row).model_dump() for row in cairn.SHOP_RECORDS[_entity]], "options": {"on_conflict": "overwrite", "on_error": "fail_fast"}})
                    assert 200 <= _import_status < 300 and not _import_body.get("failed"), _import_body
                    cr_import_counts[_entity] = _import_body["imported"]
                _step = "EXPIRE"
                for _key in cr_record_keys:
                    cr_redis.expire(_key, KEY_TTL_SECONDS)     # the service sets no TTL on what it writes
            except Exception as exc:
                cr_create_problem = f"{type(exc).__name__} at step {_step}"
            _summary = mo.callout(mo.md(f"Surface `{cr_surface_id}` (status `{cr_surface_status}`), agent key expires at {cr_agent_key_expires}, imported {' + '.join(str(count) for count in cr_import_counts.values())} records, {len(cr_record_keys)} keys set to expire in 1 h."), kind="success") if cr_create_problem is None else mo.callout(mo.md(f"`{cr_create_problem}`. Press Clean up below: it finds every surface named `{cr_surface_name}`."), kind="warn")
            _output = mo.vstack([_summary, mo.accordion({"Create request as sent (password masked)": mo.json(cr_create_wire)}), mo.md("The service answers `201` with the surface id and a status, and the import lands as JSON documents at the key templates you declared. The notebook sets a one-hour `EXPIRE` on those 20 keys because the service sets none. Pressing Create again makes a second surface with the same name, and Clean up deletes both.")])
    _output
    return cr_indexes_before, cr_mcp_service, cr_mcp_url, cr_surface_id


@app.cell(column=2)
def cr_tools(cr_mcp_service, cr_mcp_url):
    mo.stop(cr_mcp_service is None, mo.callout(mo.md("Create the surface first."), kind="info"))
    _status, _body, cr_tools_wire = call_service(cr_mcp_service, "POST", cr_mcp_url, json_body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    mo.stop(_status != 200 or not isinstance(_body, dict) or "result" not in _body, mo.callout(mo.vstack([mo.md(f"The MCP endpoint answered `{_status}` to `tools/list`: read the body below."), mo.json(_body if isinstance(_body, dict) else {"body": _body})]), kind="danger"))
    cr_tools_list = _body["result"]["tools"]
    _rows = [{
        "tool": tool["name"],
        "description": tool.get("description", ""),
        "arguments": ", ".join(name + ("*" if name in tool.get("inputSchema", {}).get("required", []) else "") for name in tool.get("inputSchema", {}).get("properties", {})),
    } for tool in cr_tools_list]
    mo.vstack([
        mo.md(r"""
    The cell below asks the surface what it can do, with one JSON-RPC `tools/list` over MCP
    authenticated by the agent key. Every row is a tool the service generated from the data model,
    and nothing here was hand-written:
    """),
        mo.ui.table(_rows, selection=None, pagination=False, label=f"{len(_rows)} tools from 3 entities"),
        mo.md("In a live run on 2026-09-30 this account's surface listed 21 tools (the unified tool surface). Required arguments are starred."),
        mo.accordion({"Request as sent (credential masked)": mo.json(cr_tools_wire)}),
    ])
    return (cr_tools_list,)


@app.cell(column=2)
def cr_call_tool(cr_mcp_service, cr_mcp_url, cr_tools_list):
    mo.stop(cr_mcp_service is None)
    _schema = next((tool.get("inputSchema", {}) for tool in cr_tools_list if tool["name"] == "get_customer_by_id"), {})
    mo.stop("id" not in _schema.get("required", []), mo.callout(mo.md(f"`get_customer_by_id` now requires `{_schema.get('required', [])}` rather than `id`. Change the argument in this cell to match."), kind="warn"))
    _request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_customer_by_id", "arguments": {"id": "C001"}}}
    _status, _body, cr_call_wire = call_service(cr_mcp_service, "POST", cr_mcp_url, json_body=_request)
    _result = (_body.get("result") or {}) if isinstance(_body, dict) else {}
    _text = "".join(part.get("text", "") for part in _result.get("content") or [])
    mo.stop(_status != 200 or not _result or _result.get("isError"), mo.callout(mo.md(f"The service answered `{_status}` and no record: {_text or json.dumps(_body)[:200]}"), kind="danger"))
    mo.stop(not _text.lstrip().startswith("{"), mo.callout(mo.md(f"The tool answered text, not a JSON record: {_text[:200]}"), kind="warn"))
    cr_customer_record = json.loads(_text)
    mo.vstack([
        mo.md(r"""
    The cell below calls one generated tool the way an agent would: `get_customer_by_id` with
    `{"id": "C001"}`. The service reads the JSON document from your Redis and returns the record
    plus a `_retrieval` stamp:
    """),
        mo.hstack([mo.json(_request, label="JSON-RPC request"), mo.json(cr_customer_record, label="result.content[0].text, parsed")], widths="equal"),
        mo.md("The `_retrieval` stamp names the surface that served it and when. The agent sent an argument, not a key or a query."),
    ])
    return


@app.cell(column=2)
def cr_rejected_call(cr_mcp_service, cr_mcp_url):
    mo.stop(cr_mcp_service is None)
    _request_a = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "filter_customer", "arguments": {"limit": 5, "offset": 0}}}
    _request_b = {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "filter_customer", "arguments": {"tag_conditions": [{"field": "lifetime_value", "value": "4200"}]}}}
    _views, cr_rejected_wires = [], {}
    for _label, _request in (("Request A, no condition", _request_a), ("Request B, a tag condition on a numeric field", _request_b)):
        _status, _body, _wire = call_service(cr_mcp_service, "POST", cr_mcp_url, json_body=_request)
        _body = _body if isinstance(_body, dict) else {"body": _body}
        _result = _body.get("result") or {}
        if _status != 200 or isinstance(_body.get("error"), dict):   # a 401 (expired agent key), 406 or 5xx is not an accepted call
            _error = _body.get("error") if isinstance(_body.get("error"), dict) else {"code": f"HTTP {_status}", "message": _body.get("type") or "no JSON-RPC result in the reply"}
            _views.append(mo.callout(mo.md(f"`{_error.get('code')}` {_error.get('message')}"), kind="danger", title=f"{_label}: rejected before anything reached Redis"))
        elif _result.get("isError"):
            _views.append(mo.callout(mo.md(f"`isError` true: {''.join(part.get('text', '') for part in _result.get('content') or [])}"), kind="danger", title=f"{_label}: the tool refused it"))
        else:
            _views.append(mo.callout(mo.json(_body), kind="warn", title=f"{_label}: accepted, so the schema was not enforced here"))
        cr_rejected_wires[f"{_label} (HTTP {_status})"] = mo.json(_wire)
    mo.vstack([
        mo.md(r"""
    The cell below sends two calls an agent should not get away with: `filter_customer` with no
    condition, then a tag condition on the numeric field `lifetime_value`. The service's own answer
    to each is printed as it arrives:
    """),
        *_views,
        mo.md("In a live run on 2026-09-30 the first call came back as a tool error, `at least one condition is required`. The second is the schema's job to refuse, with a JSON-RPC `-32602` or a tool error, and what it says here is what this service does."),
        mo.accordion(cr_rejected_wires),
    ])
    return


@app.cell(column=2)
def cr_peek(cr_indexes_before, cr_record_keys, cr_redis, cr_surface_id):
    mo.stop(cr_surface_id is None)
    cr_first_document = cr_redis.json().get(cr_record_keys[0])
    cr_new_indexes = sorted(name for name in set(cr_redis.execute_command("FT._LIST")) - cr_indexes_before if cr_surface_id in name)   # the service names its indexes idx:<surface-id>:<hash>:<entity>
    _rows, _commands = [], [redis_cli(["JSON.GET", cr_record_keys[0]]), redis_cli(["FT._LIST"])]
    for _name in cr_new_indexes:
        _info = cr_redis.ft(_name).info()
        _definition = dict(zip(_info["index_definition"][::2], _info["index_definition"][1::2]))   # RESP2 hands index_definition back as a flat list
        _rows.append({"index": _name, "documents": _info["num_docs"], "prefix": ", ".join(_definition.get("prefixes", []))})
        _commands.append(redis_cli(["FT.INFO", _name]))
    mo.vstack([
        mo.md(r"""
    Where did the data go: the cell below reads your own database with `JSON.GET` and `FT.INFO`:
    """),
        mo.json(cr_first_document or {"missing": cr_record_keys[0]}, label=cr_record_keys[0]),
        mo.ui.table(_rows, selection=None, pagination=False, label=f"{len(_rows)} indexes the service built"),
        mo.md("```\n" + "\n".join(_commands) + "\n```"),
        mo.md("The agent never saw Redis. The service wrote these JSON documents into your database, built one index per entity, and the tool call above read them back through the service."),
    ])
    return


@app.cell(column=2, hide_code=True)
def cr_cleanup_button():
    cr_cleanup_button = mo.ui.run_button(label="Clean up: DELETE the surface and UNLINK 20 keys", kind="danger")
    mo.vstack([
        mo.md(rf"""
    Clean up deletes every surface named `cairn-{RUN_TAG}` on this account, which also revokes their agent
    keys. It then unlinks the 20 keys by name and drops any index those surfaces left under the run prefix,
    without `DD`. A surface has no TTL and holds your password, so this button matters more than the other three:
    """),
        cr_cleanup_button,
    ])
    return (cr_cleanup_button,)


@app.cell(column=2)
def cr_cleanup(
    cr_admin_service,
    cr_cleanup_button,
    cr_record_keys,
    cr_redis,
    cr_surface_id,
    cr_surface_name,
):
    mo.stop(not cr_cleanup_button.value)
    _targets = set()
    for _page in range(1, 11):   # at most 10 pages of 50; the listing itself is never shown, it names other surfaces
        _list_status, _body, _wire = call_service(cr_admin_service, "GET", "/context-surfaces", params={"page": _page, "page_size": 50})
        _body = _body if isinstance(_body, dict) else {}
        _targets |= {surface["id"] for surface in _body.get("context_surfaces") or [] if surface.get("name") == cr_surface_name}
        if not (_body.get("pagination") or {}).get("has_next"):
            break
    _targets |= {cr_surface_id} - {None}
    _replies = {}
    for _surface in sorted(_targets):
        _status, _body, _wire = call_service(cr_admin_service, "DELETE", f"/context-surfaces/{_surface}")   # 200, 204 and 404 all mean gone
        _replies[f"DELETE {_surface} answered {_status}"] = {"gone": _status in (200, 204, 404), "reply": _body if isinstance(_body, dict) else {"body": _body}}
    cr_keys_removed = cr_redis.unlink(*cr_record_keys)
    cr_indexes_dropped = []
    for _name in [name for name in cr_redis.execute_command("FT._LIST") if any(surface in name for surface in _targets)]:
        try:
            _definition = cr_redis.ft(_name).info()["index_definition"]
            if all(prefix.startswith(KEY_PREFIX) for prefix in dict(zip(_definition[::2], _definition[1::2])).get("prefixes") or [""]):   # no prefixes entry fails the check
                cr_redis.ft(_name).dropindex()     # never delete_documents=True: the keys go by name above
                cr_indexes_dropped.append(_name)
        except redis.ResponseError as exc:
            if not re.search(r"index[_ ]not[_ ]found|unknown index|no such index", str(exc), re.I):
                raise
    mo.vstack([
        mo.callout(mo.md(("" if _list_status == 200 else f"The surface listing answered `{_list_status}`, so only the id this kernel knows was tried. ") + f"Deleted {sum(reply['gone'] for reply in _replies.values())} of {len(_targets)} surfaces named `{cr_surface_name}`, unlinked {cr_keys_removed} of {len(cr_record_keys)} keys, dropped {len(cr_indexes_dropped)} indexes left under `{KEY_PREFIX}`."), kind="success" if _list_status == 200 and all(reply["gone"] for reply in _replies.values()) else "danger"),
        mo.md("```\n" + "\n".join([redis_cli(["UNLINK", *cr_record_keys])] + [redis_cli(["FT.DROPINDEX", name]) for name in cr_indexes_dropped]) + "\n```"),
        mo.accordion({key: mo.json(value["reply"]) for key, value in _replies.items()}),
        mo.md("The documents in your database stay when a surface is deleted, which is why the notebook unlinks them by name. In a live run on 2026-09-30 the service dropped its own indexes with the surface, so the `FT.DROPINDEX` pass is the check that it did."),
    ])
    return


@app.cell(column=3, hide_code=True)
def am_title():
    mo.vstack([
        mo.md(r"""
    # Redis Agent Memory

    An agent that forgets a user's allergy between sessions is not unintelligent. It has no memory.
    Redis Agent Memory gives it two tiers: session memory, the ordered events of the conversation in
    progress, and long-term memory, facts recalled by meaning across sessions. Its one job in this
    column: recall a customer fact with no word in common with the question.

    Facts reach long-term memory two ways: your app writes them directly, or the service extracts
    them from sessions in the background, minutes later. Every call carries an owner id and a
    namespace, because the store is shared by every app that points at it. Agent Memory is in public
    preview. Settings: `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY`. Docs:
    [Agent Memory](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/),
    [REST quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/rest-api-quickstart/).
    """),
        mo.mermaid("""flowchart LR
    A[App] -->|"POST /session-memory/events"| S[Session memory]
    S -.->|"extraction, minutes later"| L[Long-term memory]
    A -->|"POST /long-term-memory"| L
    A -->|"POST /long-term-memory/search"| L"""),
    ])
    return


@app.cell(column=3)
def am_configure():
    mo.stop(
        not all(os.environ.get(name) for name in ("MEMORY_API_BASE_URL", "MEMORY_STORE_ID", "MEMORY_API_KEY")),
        mo.callout(mo.md("Agent Memory is not configured. Set `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY` in the `.env` next to `pyproject.toml` (see `.env.example`), then restart the kernel."), kind="warn"),
    )
    am_owner_id = f"cairn-{RUN_TAG}"                 # the tenant boundary on a shared store
    am_namespace = "iris-basics-notebook"
    am_memory_ids = [f"{am_owner_id}-m{n}" for n in range(1, len(cairn.DANA_FACTS) + 1)]   # chosen here, not by the service
    # A session is one conversation, so its id is per kernel. Clean up lists sessions by owner, so it finds earlier ones too.
    am_session_id = f"{am_owner_id}-s{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    am_service = niquests.Session(
        base_url=f"{os.environ['MEMORY_API_BASE_URL'].strip().rstrip('/')}/v1/stores/{os.environ['MEMORY_STORE_ID'].strip()}",
        headers={"Authorization": f"Bearer {os.environ['MEMORY_API_KEY'].strip()}", "Accept": "application/json",
                 "Content-Type": "application/json", "User-Agent": "redis-iris-basics"},
        timeout=(5, 30),
        disable_http3=True,
    )
    try:
        am_health_status, am_health_body, am_health_wire = call_service(am_service, "GET", "/health")
    except niquests.RequestException as exc:
        mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while reaching Agent Memory. Check `MEMORY_API_BASE_URL`."), kind="danger"))
    mo.stop(
        not isinstance(am_health_body, dict),
        mo.callout(mo.md("The endpoint answered with a page or an empty body, not JSON. `MEMORY_API_BASE_URL` is the Endpoint on the service's General settings, not a console page."), kind="danger"),
    )
    _hint = {401: "check `MEMORY_API_KEY`", 404: "check `MEMORY_STORE_ID`"}.get(am_health_status, "read the body below")
    mo.stop(
        am_health_status != 200,
        mo.callout(mo.vstack([mo.md(f"Agent Memory answered `{am_health_status}` `{am_health_body.get('type', '')}`: {_hint}."), mo.json(am_health_body)]), kind="danger"),
    )
    mo.vstack([
        mo.callout(mo.md(f"Store `{am_health_body.get('status')}`. Every call in this column carries `ownerId` `{am_owner_id}` and `namespace` `{am_namespace}`. On a store other apps share, that filter is the tenant boundary."), kind="success"),
        mo.accordion({"Request as sent (credential masked)": mo.json(am_health_wire), "Reply": mo.json(am_health_body)}),
    ])
    return am_memory_ids, am_namespace, am_owner_id, am_service, am_session_id


@app.cell(column=3, hide_code=True)
def am_write_button():
    am_write_button = mo.ui.run_button(label="Write 5 memories about Dana in one POST", kind="success")
    mo.vstack([
        mo.md("The button below writes five facts about Dana, a made-up Cairn customer, in one bulk `POST /long-term-memory`. The ids, the owner, and the namespace are chosen here rather than by the service:"),
        am_write_button,
    ])
    return (am_write_button,)


@app.cell(column=3)
def am_write(
    am_memory_ids,
    am_namespace,
    am_owner_id,
    am_service,
    am_write_button,
):
    mo.stop(not am_write_button.value, mo.callout(mo.md("Press *Write 5 memories about Dana in one POST* above."), kind="info"))
    try:
        _status, _body, am_write_wire = call_service(
            am_service, "POST", "/long-term-memory",
            json_body={"memories": [
                {"id": memory_id, "text": fact["text"], "ownerId": am_owner_id, "namespace": am_namespace, "memoryType": "semantic", "topics": fact["topics"]}
                for memory_id, fact in zip(am_memory_ids, cairn.DANA_FACTS)
            ]},
        )
    except niquests.RequestException as exc:
        mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while reaching Agent Memory. Check `MEMORY_API_BASE_URL`."), kind="danger"))
    _reply = _body if isinstance(_body, dict) else {}
    mo.stop(_status == 424, mo.callout(mo.md(f"`424` `{_reply.get('type', '')}`: {_reply.get('detail', '')}. This is a failure of the store's database or model, not 0 results."), kind="danger"))
    _hint = {401: "check `MEMORY_API_KEY`", 404: "check `MEMORY_STORE_ID`"}.get(_status, "read the body below")
    mo.stop(_status != 201, mo.callout(mo.vstack([mo.md(f"Agent Memory answered `{_status}` `{_reply.get('type', '')}`: {_hint}."), mo.json(_reply if _reply else {"body": _body})]), kind="danger"))
    am_memories_written = True
    mo.vstack([
        mo.ui.table([{"id": memory["id"], "text": memory["text"], "topics": ", ".join(memory["topics"])} for memory in am_write_wire["body"]["memories"]], selection=None, pagination=False),
        mo.accordion({"Request as sent (credential masked)": mo.json(am_write_wire), "Reply": mo.json(_reply)}),
        mo.md("Agent Memory answers `201` with `created`, and in a live run the memories were searchable at once. Because the ids are yours, the same request twice is an update, and Clean up can name them without a ledger."),
    ])
    return (am_memories_written,)


@app.cell(column=3)
def am_search_controls(am_memories_written):
    mo.stop(not am_memories_written, mo.callout(mo.md("Write the memories first."), kind="info"))
    am_question = mo.ui.text(value=cairn.MEMORY_QUESTIONS[0]["text"], label="Ask", full_width=True)
    am_threshold = mo.ui.slider(0.0, 1.0, step=0.05, value=0.3, debounce=True, show_value=True, label="similarityThreshold")
    mo.vstack([
        mo.md("The reply of `POST /long-term-memory/search` carries no score, so `similarityThreshold` is the only knob. Everything at or above it comes back in rank order, and everything below is cut:"),
        am_question,
        am_threshold,
        mo.md("Two more questions with no word in common with their memory: *My toes feel squashed on long descents.* and *Somewhere freezing to visit next year?*"),
    ])
    return am_question, am_threshold


@app.cell(column=3)
def am_search(
    am_memories_written,
    am_namespace,
    am_owner_id,
    am_question,
    am_service,
    am_threshold,
):
    mo.stop(not am_memories_written)
    am_search_body = {
        "text": am_question.value,
        "filter": {"ownerId": {"eq": am_owner_id}, "namespace": {"eq": am_namespace}},
        "filterOp": "all", "similarityThreshold": round(am_threshold.value, 2), "limit": 5,
    }
    try:
        _status, _body, am_search_wire = call_service(am_service, "POST", "/long-term-memory/search", json_body=am_search_body)
    except niquests.RequestException as exc:
        mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while reaching Agent Memory. Check `MEMORY_API_BASE_URL`."), kind="danger"))
    _reply = _body if isinstance(_body, dict) else {}
    mo.stop(_status == 424, mo.callout(mo.md(f"`424` `{_reply.get('type', '')}`: {_reply.get('detail', '')}. This is a failure of the store's database or model, not 0 results."), kind="danger"))
    mo.stop(_status != 200, mo.callout(mo.vstack([mo.md(f"Agent Memory answered `{_status}` `{_reply.get('type', '')}`: read the body below."), mo.json(_reply if _reply else {"body": _body})]), kind="danger"))
    am_recalled_memories = _reply.get("items", [])
    _rows = [
        {"rank": rank, "memory": item["text"], "topics": ", ".join(item.get("topics") or []), "words in common": len(shared_stems(am_question.value, item["text"]))}
        for rank, item in enumerate(am_recalled_memories, 1)
    ]
    mo.vstack([
        mo.ui.table(_rows, selection=None, pagination=False) if _rows else mo.callout(mo.md(f"0 memories at or above {am_search_body['similarityThreshold']}."), kind="warn"),
        mo.json(am_search_body, label="Request body"),
        mo.md("Agent Memory answers with the memories at or above the threshold, best first, and says nothing about how close they were."),
        mo.md("In a live run on 2026-09-30 the allergy memory was #1 of 5 at 0.0 with 0 words in common, alone at 0.3, and absent at 0.5. Another store or model may place the cut elsewhere, so start around 0.3 and move it."),
        mo.accordion({"Request as sent (credential masked)": mo.json(am_search_wire), "Reply": mo.json(_reply)}),
    ])
    return


@app.cell(column=3)
def am_scope_lens(
    am_memories_written,
    am_namespace,
    am_owner_id,
    am_question,
    am_service,
):
    mo.stop(not am_memories_written)
    _scopes = [
        ("this owner, this namespace", am_owner_id, am_namespace, 5),
        ("an owner that does not exist", "nobody-owns-this-id", am_namespace, 0),
        ("a namespace that does not exist", am_owner_id, "no-such-namespace", 0),
    ]
    _rows, _wires = [], {}
    for _scope, _owner, _scope_namespace, _expected in _scopes:
        _status, _body, _wire = call_service(
            am_service, "POST", "/long-term-memory/search",
            json_body={"text": am_question.value, "filter": {"ownerId": {"eq": _owner}, "namespace": {"eq": _scope_namespace}}, "filterOp": "all", "similarityThreshold": 0.0, "limit": 100},
        )
        mo.stop(_status != 200, mo.callout(mo.vstack([mo.md(f"Agent Memory answered `{_status}` `{_body.get('type', '') if isinstance(_body, dict) else ''}`: read the body below."), mo.json(_body if isinstance(_body, dict) else {"body": _body})]), kind="danger"))
        _rows.append({"scope": _scope, "filter as sent": json.dumps(_wire["body"]["filter"]), "memories returned": len((_body if isinstance(_body, dict) else {}).get("items", [])), "expected": _expected})
        _wires[f"Request {len(_rows)}, {_scope} (credential masked)"] = mo.json(_wire)
    mo.vstack([
        mo.md("The same question runs under three filters. The owner and the namespace, both sent on every call, are what scope a shared store:"),
        mo.ui.table(_rows, selection=None, pagination=False),
        mo.md("`namespace eq` never matches a record that has no namespace at all, which is why Clean up below lists by owner alone."),
        mo.accordion(_wires),
    ])
    return


@app.cell(column=3, hide_code=True)
def am_session_button():
    am_session_button = mo.ui.run_button(label="Send 4 session events and assemble the prompt", kind="success")
    mo.vstack([
        mo.md("The button below posts four session events, two of them facts worth remembering, then searches long-term memory with the last turn and assembles the prompt an agent would receive. A session's owner and namespace come from its first event only:"),
        am_session_button,
        mo.md("Extraction runs in the background on the store's cadence, every 5 minutes by default and configurable from 60 to 600 seconds. It can turn the two facts into long-term memories with ids the service chooses."),
    ])
    return (am_session_button,)


@app.cell(column=3)
def am_session(
    am_namespace,
    am_owner_id,
    am_service,
    am_session_button,
    am_session_id,
):
    mo.stop(not am_session_button.value, mo.callout(mo.md("Press *Send 4 session events and assemble the prompt* above."), kind="info"))
    _wires, _question = {}, cairn.SESSION_TURNS[-1]["text"]
    for _turn in cairn.SESSION_TURNS:
        _status, _body, _wire = call_service(am_service, "POST", "/session-memory/events", json_body={
            "sessionId": am_session_id, "actorId": am_owner_id if _turn["role"] == "USER" else "cairn-agent",
            "role": _turn["role"], "content": [{"text": _turn["text"]}], "namespace": am_namespace,
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),   # the app's own time, so it must order the four events
            "metadata": {"source": "iris-basics"},
        })
        mo.stop(_status != 201, mo.callout(mo.vstack([mo.md(f"Agent Memory answered `{_status}` `{_body.get('type', '') if isinstance(_body, dict) else ''}`: read the body below."), mo.json(_body if isinstance(_body, dict) else {"body": _body})]), kind="danger"))
        _wires[f"Event {len(_wires) + 1}, {_turn['role']} (credential masked)"] = mo.json(_wire)
    _last_event_id = ((_body if isinstance(_body, dict) else {}).get("event") or {}).get("eventId")   # the question just sent, kept out of the short-term tier by id rather than by position
    _status, _session, _wire = call_service(am_service, "GET", f"/session-memory/{am_session_id}")
    mo.stop(_status != 200, mo.callout(mo.vstack([mo.md(f"Agent Memory answered `{_status}` `{_session.get('type', '') if isinstance(_session, dict) else ''}`: read the body below."), mo.json(_session if isinstance(_session, dict) else {"body": _session})]), kind="danger"))
    _wires["Session read-back (credential masked)"] = mo.json(_wire)
    _status, _found, _wire = call_service(am_service, "POST", "/long-term-memory/search", json_body={"text": _question, "filter": {"ownerId": {"eq": am_owner_id}, "namespace": {"eq": am_namespace}}, "filterOp": "all", "similarityThreshold": 0.0, "limit": 3})
    mo.stop(_status != 200, mo.callout(mo.vstack([mo.md(f"Agent Memory answered `{_status}` `{_found.get('type', '') if isinstance(_found, dict) else ''}`: read the body below."), mo.json(_found if isinstance(_found, dict) else {"body": _found})]), kind="danger"))
    _wires["Long-term memory search (credential masked)"] = mo.json(_wire)
    _session, _found = (_session if isinstance(_session, dict) else {}), (_found if isinstance(_found, dict) else {})   # a 200 with an empty or non-JSON body is not a dict
    _events, _recalled = _session.get("events", []), _found.get("items", [])
    _history = [event for event in _events if event.get("eventId") != _last_event_id] if _last_event_id else _events[:-1]
    am_prompt_context = "\n".join([
        "Retrieved memory context:",
        "Session summary:", (_session.get("summary") or {}).get("text") or "(none yet: this store summarizes only when that is switched on and the session is long enough)",
        "Short-term memory:", *[f"{event['role']}: {event['content'][0]['text']}" for event in _history],
        "Long-term memory:", *[f"- {item['text']}" for item in _recalled],
    ])
    mo.vstack([
        mo.md(f"Session `{am_session_id}`: `ownerId` `{_session.get('ownerId')}`, `namespace` `{_session.get('namespace')}`."),
        mo.ui.table([{"role": event["role"], "actor": event["actorId"], "text": event["content"][0]["text"], "createdAt": event["createdAt"]} for event in _events], selection=None, pagination=False),
        mo.md("**What the agent receives for the last turn**"),
        mo.plain_text(am_prompt_context),
        mo.md(f"Top memory for the last turn: #1 of {len(_recalled)} · {len(shared_stems(_question, _recalled[0]['text']))} words in common.") if _recalled else mo.callout(mo.md("0 memories came back for the last turn. Write the memories first."), kind="warn"),
        mo.md("The session read-back shows the owner and namespace the first event fixed. The assembled prompt is what the two tiers are for."),
        mo.accordion(_wires),
    ])
    return


@app.cell(column=3, hide_code=True)
def am_cleanup_button():
    am_cleanup_button = mo.ui.run_button(label="Clean up: delete this owner's sessions and memories", kind="danger")
    mo.vstack([
        mo.md("Clean up deletes the session, lists every memory under the owner with no namespace filter, checks each item's `ownerId` on this side, deletes by id, and lists again. If you sent session events, press it again after the extraction cadence has passed, ten minutes to be safe, because extracted memories otherwise last a year:"),
        am_cleanup_button,
    ])
    return (am_cleanup_button,)


@app.cell(column=3)
def am_cleanup(
    am_cleanup_button,
    am_memory_ids,
    am_owner_id,
    am_service,
    am_session_id,
):
    mo.stop(not am_cleanup_button.value)
    _status, _body, _wire = call_service(am_service, "GET", "/session-memory", params={"filterOwnerId": am_owner_id, "limit": 1000})
    _sessions = _body.get("items", []) if _status == 200 and isinstance(_body, dict) else [am_session_id]   # by owner, never every session; this kernel's id if the listing fails
    _sessions_deleted = 0
    for _session in _sessions:
        _status, _body, _wire = call_service(am_service, "DELETE", f"/session-memory/{_session}")
        _sessions_deleted += _status in (204, 404)   # 404 is gone already
    _listed, _token, _deleted, _delete_wire, _delete_reply = [], None, [], None, {}
    for _page in range(50):   # owner alone, no namespace: extracted memories may carry none
        _status, _body, _wire = call_service(am_service, "POST", "/long-term-memory/search", json_body={"text": "", "filter": {"ownerId": {"eq": am_owner_id}}, "limit": 100} | ({"pageToken": _token} if _token else {}))
        _listed += _body.get("items", []) if isinstance(_body, dict) else []
        _token = _body.get("nextPageToken") if isinstance(_body, dict) else None
        if not _token:
            break
    mo.stop(_status != 200, mo.callout(mo.vstack([mo.md(f"Agent Memory answered `{_status}` `{_body.get('type', '') if isinstance(_body, dict) else ''}` on the owner listing: read the body below. No memory was deleted."), mo.json(_body if isinstance(_body, dict) else {"body": _body})]), kind="danger"))
    _strangers = [item for item in _listed if item.get("ownerId") != am_owner_id]
    mo.stop(bool(_strangers), mo.callout(mo.md(f"The listing returned {len(_strangers)} memories not owned by `{am_owner_id}`, so the filter was not applied as sent. No memory was deleted."), kind="danger"))
    _ids = list(dict.fromkeys([item["id"] for item in _listed] + am_memory_ids))   # listed ids plus the five chosen here, in case the listing lags
    for _start in range(0, len(_ids), 100):
        _status, _body, _delete_wire = call_service(am_service, "DELETE", "/long-term-memory", json_body={"memoryIds": _ids[_start:_start + 100]})
        _delete_reply = _body if isinstance(_body, dict) else {}   # `deleted`, plus `errors` when an id failed
        _deleted += _delete_reply.get("deleted", [])
    _status, _body, _wire = call_service(am_service, "POST", "/long-term-memory/search", json_body={"text": "", "filter": {"ownerId": {"eq": am_owner_id}}, "limit": 100})   # if memories stay listed here with no `errors` in the delete reply, send "similarityThreshold": 0.0 on the two listing calls
    _left = _body.get("items", []) if isinstance(_body, dict) else []
    mo.vstack([
        mo.callout(mo.md(f"Deleted {_sessions_deleted} session{'' if _sessions_deleted == 1 else 's'} and {len(_deleted)} memories ({len(set(_deleted) - set(am_memory_ids))} with ids the service chose). {len(_left)} left under `{am_owner_id}`" + ("." if _status == 200 else f", by a second listing that answered `{_status}`, so press Clean up again.")), kind="warn" if _left or _status != 200 else "success"),
        mo.accordion({"Last DELETE /long-term-memory as sent (credential masked)": mo.json(_delete_wire or {}), "Last DELETE reply": mo.json(_delete_reply), "Second listing as sent (credential masked)": mo.json(_wire)}),
    ])
    return


if __name__ == "__main__":
    app.run()
