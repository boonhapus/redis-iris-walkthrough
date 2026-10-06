import marimo

__generated_with = "0.25.0"
app = marimo.App(width="columns", app_title="Redis Iris basics")

with app.setup(hide_code=True):
    import getpass
    import hashlib
    import json
    import os
    import platform
    import time
    from datetime import datetime, timedelta, timezone

    import anthropic
    import marimo as mo
    import niquests
    import numpy as np
    import redis

    from cairn_support import sample_data as cairn
    from cairn_support.embedder import EMBEDDING_MODEL_NAME, embed_texts, load_embedding_model
    from cairn_support.formatting import redis_cli, shared_stems

    # One tag for everything this notebook writes from this machine. It is derived from the user
    # and host names, so Clean up in a fresh kernel finds what an earlier kernel wrote.
    RUN_TAG = hashlib.sha256(f"{getpass.getuser()}@{platform.node()}".encode()).hexdigest()[:8]
    KEY_PREFIX = f"cairn:{RUN_TAG}:"   # every Redis key written by this notebook or by Context Retriever
    KEY_TTL_SECONDS = 3600             # every key expires on its own; Clean up only makes it sooner

    def iris_session(base_url, headers, timeout=(5, 30)):
        """An HTTP session for one hosted Iris service: JSON in and out, no retries, no HTTP/3."""
        return niquests.Session(
            base_url=base_url,
            # Content-Type is pinned because niquests would otherwise append ;charset=utf-8.
            headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "redis-iris-basics", **headers},
            timeout=timeout,
            disable_http3=True,
        )

    def call_service(service, method, path, *, json_body=None, params=None, ok=(200, 201, 204)):
        """One request to a hosted Iris service: (status, parsed body, wire).

        `wire` is the request as sent with the credential header masked, so it is safe to show. A
        status outside `ok` raises with the problem type and title only: a problem's detail can echo
        the request, and one request here carries a database password.
        """
        response = service.request(method, path, json=json_body, params=params)
        if response.status_code not in ok:
            problem = response.json() if "json" in response.headers.get("Content-Type", "") else {}   # an error page is not JSON
            raise RuntimeError(f"{method} {path} answered {response.status_code} {problem.get('type', '')} {problem.get('title', '')}".rstrip())
        body = response.json() if response.content else None
        sent = response.request
        wire = {
            "method": sent.method, "url": sent.url,
            "headers": {k: "***" if k.lower() in ("authorization", "x-api-key") else v for k, v in sent.headers.items()},
            "body": json.loads(sent.body) if sent.body else None,
            "status": response.status_code, "ms": round(response.elapsed.total_seconds() * 1000),
        }
        return response.status_code, body, wire


@app.cell(column=0, hide_code=True)
def intro():
    mo.md(rf"""
    # Redis Iris basics

    Agents do not have an intelligence problem. They have a context problem. [Redis Iris](https://redis.io/docs/latest/develop/ai/context-engine/)
    is the real-time context engine for agents. This notebook gives one column to Redis Search, which
    Iris runs on, and one to each of three Iris services hosted on Redis Cloud: LangCache, Context
    Retriever, and Agent Memory. Every column shows three principles on sample data from Cairn
    Outfitters, a fictional outdoor-gear shop. Each puts its component behind a chat or a console
    and shows the `redis-cli` command or JSON request for every turn. The chats call Claude (`claude-opus-5-5`).

    | Column, left to right | The three principles it shows |
    |---|---|
    | Redis Search | An index follows every write under its prefix. Redis fuses keyword and vector rankings in one command. Filters run inside the vector search. |
    | Redis LangCache | Your app searches before the LLM and stores after it. The threshold trades wrong answers for misses. A cached answer lives until its time to live (TTL) runs out. |
    | Redis Context Retriever | The data model generates the tools. The agent can call only what the model defines. Context Retriever is a layer on top of your Redis. |
    | Redis Agent Memory | Long-term memory recalls facts by meaning. The owner and namespace are the tenant boundary. An agent turn reads both tiers. |

    **Status.** LangCache and Agent Memory are in public preview on Redis Cloud, and Context Retriever is in preview.

    **Configuration.** Copy `.env.example` to `.env` next to `pyproject.toml`. marimo loads it
    when the kernel starts. The cells under this one build one client per service. A service with
    no settings fails in its own client cell, and every cell in its column shows marimo's
    *An ancestor raised an exception* notice. The other columns still run. The chats in the Search,
    LangCache, and Agent Memory columns also need `ANTHROPIC_API_KEY`. Without it the Claude client
    still builds, and each chat message that reaches Claude raises an error in the chat.

    **Safety.** Nothing is written until you press a seed button, submit the Search form, or send a
    chat message. A LangCache miss stores Claude's answer, and each Agent Memory message writes two
    session events: the message and the agent's reply. Until you press a column's seed button, the
    cells that need its data show marimo's *Ancestor stopped* notice.

    Everything written to Redis, Context Retriever, and Agent Memory carries the run tag `{RUN_TAG}`.
    Redis keys live under `{KEY_PREFIX}` with a one-hour `EXPIRE`. The Redis Search index has no
    TTL, and neither does the Context Retriever surface, named `cairn-{RUN_TAG}`. Agent Memory has
    no TTL per record, so sessions and memories owned by `cairn-{RUN_TAG}` last until the store's
    defaults expire them. The tag is the same in every kernel on this machine, so those three
    columns' Clean up buttons find what an earlier kernel wrote.

    LangCache entries carry no tag, and each has a `ttlMillis` of at most 15 minutes. LangCache's
    Clean up runs after a seed press in this kernel, and it deletes the five gear-care answers and
    the answers this kernel's chat stored. Each column ends with a Clean up button.
    """)
    return


@app.cell(column=0)
def redis_client():
    # redis-py retries a dropped connection by default. The health check PINGs before reusing a
    # connection that sat idle for 30 s, which a notebook does a lot.
    REDIS_CLIENT = redis.Redis.from_url(
        os.environ["REDIS_URL"],
        protocol=2,               # flat FT.* replies: every reply shape here was verified live under RESP2
        decode_responses=True,    # strings back; the vector field is never read through a core command
        health_check_interval=30,
    )
    return (REDIS_CLIENT,)


@app.cell(column=0)
def embedding_model():
    with mo.status.spinner(title=f"Loading {EMBEDDING_MODEL_NAME}", subtitle="about 30 MB the first time"):
        EMBEDDING_MODEL = load_embedding_model()   # runs on this machine; Redis stores the vectors it makes
    return (EMBEDDING_MODEL,)


@app.cell(column=0)
def llm_client():
    LLM = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY, or a profile from `ant auth login`
    LLM_MODEL = "claude-opus-5-5"

    def ask_llm(system, messages):
        """One Claude reply as text. Low effort keeps a chat turn quick, and a declined request
        is re-run server-side on the model Anthropic recommends for that refusal category. A
        refusal that survives the fallback raises, so no chat stores it as an answer."""
        response = LLM.beta.messages.create(
            model=LLM_MODEL, max_tokens=16000, system=system, messages=messages,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude declined this request ({response.stop_details.category or 'no category'}).")
        return "".join(block.text for block in response.content if block.type == "text")
    return LLM_MODEL, ask_llm


@app.cell(column=0)
def langcache_client():
    LANGCACHE = iris_session(
        f"{os.environ['LANGCACHE_HOST'].rstrip('/')}/v1/caches/{os.environ['LANGCACHE_CACHE_ID']}",
        {"Authorization": f"Bearer {os.environ['LANGCACHE_API_KEY']}"},
    )
    return (LANGCACHE,)


@app.cell(column=0)
def context_retriever_client():
    CONTEXT_RETRIEVER = iris_session(
        f"{(os.environ.get('CTX_API_URL') or 'https://cloud.redis.io/context-surfaces').rstrip('/')}/api/v1",   # `or`: a blank line in .env keeps the default
        {"X-API-Key": os.environ["CTX_ADMIN_KEY"]},
        timeout=(5, 60),   # surface creation and import do real work
    )
    return (CONTEXT_RETRIEVER,)


@app.cell(column=0)
def agent_memory_client():
    AGENT_MEMORY = iris_session(
        f"{os.environ['MEMORY_API_BASE_URL'].rstrip('/')}/v1/stores/{os.environ['MEMORY_STORE_ID']}",
        {"Authorization": f"Bearer {os.environ['MEMORY_API_KEY']}"},
    )
    MEMORY_OWNER_ID = f"cairn-{RUN_TAG}"   # every memory and session this notebook writes belongs to this owner
    MEMORY_NAMESPACE = "iris-basics-notebook"
    return AGENT_MEMORY, MEMORY_NAMESPACE, MEMORY_OWNER_ID


@app.cell(column=0)
def langcache_search_helper(LANGCACHE):
    def langcache_search(prompt, own_ids, threshold=0.85, strategies=("semantic",)):
        """One search: (the hits whose id is in own_ids, best first; the wire with every other entry hidden)."""
        _, body, wire = call_service(LANGCACHE, "POST", "/entries/search", json_body={
            "prompt": prompt, "similarityThreshold": threshold, "searchStrategies": list(strategies), "maxResults": 5})
        wire["reply"] = {"data": [hit if hit["id"] in own_ids else {"hidden": "not stored by this press", "similarity": hit["similarity"]} for hit in body["data"]]}
        return [hit for hit in body["data"] if hit["id"] in own_ids], wire
    return (langcache_search,)


@app.cell(column=1, hide_code=True)
def rs_title():
    mo.md(r"""
    # Redis Search

    Customers ask in their own words, and a keyword index finds only the words your articles use.
    Redis Search keeps text, tag, numeric, and vector fields in one index over your hashes.
    Load 36 Cairn Outfitters help articles, then ask the help center, filter it, and add an article to see three principles:

    1. **An index follows every write under its prefix.** `FT.CREATE` names a key prefix once, and Redis indexes each `HSET` under it as part of the write, so no sync job runs.
    2. **Redis fuses keyword and vector rankings in one command.** Keyword and vector search fail on different questions, and `FT.HYBRID` merges the two with Reciprocal Rank Fusion (RRF).
    3. **Filters run inside the vector search.** Redis picks the k nearest articles from those that pass a tag or numeric filter, so it returns fewer than k only when fewer pass.

    Redis Search is available in Redis Open Source, Redis Software, and Redis Cloud, and `FT.HYBRID` needs Redis 8.4 or later. Settings: `REDIS_URL`, plus `ANTHROPIC_API_KEY`, which the chat and the cell under it need.
    Docs: [Redis Search](https://redis.io/docs/latest/develop/ai/search-and-query/), [vectors and filters](https://redis.io/docs/latest/develop/ai/search-and-query/vectors/), and [`FT.HYBRID`](https://redis.io/docs/latest/commands/ft.hybrid/).

    Each line reads source, target, and what passes between them:

    ```text
    App   -> FT index  FT.CREATE over the prefix cairn:<run>:help:
    App   -> vector    potion-base-8M embeds on this machine
    App   -> hash      HSET title, body, category, year, and embedding
    hash  -> FT index  indexed as part of the HSET
    App   -> FT index  FT.HYBRID: BM25STD and KNN, fused with RRF, the filter inside both
    App   -> Claude    the top 3 articles and the question, for the answer
    ```
    """)
    return


@app.cell(column=1, hide_code=True)
def rs_seed_button():
    rs_index_name = f"idx:cairn:{RUN_TAG}:help"
    rs_key_prefix = f"{KEY_PREFIX}help:"
    rs_article_keys = [f"{rs_key_prefix}{article['id']}" for article in cairn.HELP_ARTICLES]   # Clean up never scans
    rs_reader_key = f"{rs_key_prefix}reader"   # the one article the form writes, which Clean up also unlinks by name
    rs_seed_button = mo.ui.run_button(label="Load the help center: 1 FT.CREATE, 36 HSET, 36 EXPIRE", kind="success")
    mo.vstack([
        mo.md(r"""The button below writes one index and 36 hashes to your database. Each hash carries a one-hour `EXPIRE`, and the index has no TTL, so it stays until Clean up. The index watches the prefix `cairn:<run>:help:`, and the chat appears once it is loaded:"""),
        rs_seed_button,
    ])
    return rs_article_keys, rs_index_name, rs_key_prefix, rs_reader_key, rs_seed_button


@app.cell(column=1)
def rs_seed(EMBEDDING_MODEL, REDIS_CLIENT, rs_article_keys, rs_index_name, rs_key_prefix, rs_seed_button):
    from redis.commands.search.field import NumericField, TagField, TextField, VectorField
    from redis.commands.search.index_definition import IndexDefinition, IndexType
    from redis.commands.search.query import Query

    mo.stop(not rs_seed_button.value)
    _vectors = embed_texts(EMBEDDING_MODEL, [f"{article['title']}. {article['body']}" for article in cairn.HELP_ARTICLES])
    _schema = (
        TextField("title", weight=2.0), TextField("body"), TagField("category"), NumericField("year", sortable=True),
        VectorField("embedding", "HNSW", {"TYPE": "FLOAT32", "DIM": _vectors.shape[1], "DISTANCE_METRIC": "COSINE"}),
    )
    _definition = IndexDefinition(prefix=[rs_key_prefix], index_type=IndexType.HASH)
    rs_index = REDIS_CLIENT.ft(rs_index_name)
    try:
        _created = rs_index.create_index(_schema, definition=_definition)   # FT.CREATE
    except redis.ResponseError as _exc:
        if "already exists" not in str(_exc).lower():   # a second press rewrites the same 36 keys
            raise
        _created = str(_exc)
    _rows, _first_hset = [], None
    for _start in range(0, len(cairn.HELP_ARTICLES), 12):   # three pipelines of 12, under the free tier's 100 ops/s
        _pipe = REDIS_CLIENT.pipeline(transaction=False)
        for _article, _vector in zip(cairn.HELP_ARTICLES[_start:_start + 12], _vectors[_start:_start + 12]):
            _key = f"{rs_key_prefix}{_article['id']}"
            _pipe.hset(_key, mapping={"title": _article["title"], "body": _article["body"], "category": _article["category"],
                                      "year": _article["year"], "embedding": _vector.astype(np.float32).tobytes()})   # DIM x 4 raw bytes
            _pipe.expire(_key, KEY_TTL_SECONDS)
        _first_hset = _first_hset or _pipe.command_stack[0][0]   # read before execute() clears the stack
        _pipe.execute()
        _info = rs_index.info()   # FT.INFO, straight after the pipeline
        _rows.append({"HSET sent": _start + 12, "num_docs": int(_info["num_docs"]), "hash_indexing_failures": int(_info["hash_indexing_failures"])})
        time.sleep(0.25)
    _in_step = sum(row["num_docs"] == row["HSET sent"] for row in _rows)
    mo.vstack([
        mo.ui.table(_rows, selection=None, show_download=False, pagination=False),
        mo.md(f"""Redis answered `{_created}` to the `FT.CREATE`, and after it the app's only writes are `HSET` and `EXPIRE`. The `FT.INFO` straight after each pipeline finds `num_docs` equal to the hashes sent in {_in_step} of {len(_rows)} rows. A second press before Clean up rewrites keys the index holds, so the first row shows the full count."""),
        mo.md(f"```\n{redis_cli(['FT.CREATE', rs_index_name, *_definition.args, 'SCHEMA', *[arg for field in _schema for arg in field.redis_args()]])}\n{redis_cli(_first_hset)}\n{redis_cli(['EXPIRE', rs_article_keys[0], KEY_TTL_SECONDS])}\n{redis_cli(['FT.INFO', rs_index_name])}\n```"),
    ])
    return Query, rs_index


@app.cell(column=1)
def rs_chat(EMBEDDING_MODEL, Query, REDIS_CLIENT, ask_llm, rs_index, rs_index_name):
    _all_categories = sorted({article["category"] for article in cairn.HELP_ARTICLES})
    rs_categories = mo.ui.multiselect(options=_all_categories, value=_all_categories, label="Categories (none means all)")
    rs_year_floor = mo.ui.slider(2022, 2026, value=2022, label="Year floor", show_value=True)
    rs_turns = []   # one dict per question: the filter, every command, the ranks, and the timings

    def rs_prefilter(categories, year_floor):
        return f"(@category:{{{'|'.join(categories or _all_categories)}}} @year:[{year_floor} 2026])"

    def rs_knn(prefilter, k):
        return Query(f"{prefilter}=>[KNN {k} @embedding $vector AS vector_score]").sort_by("vector_score").return_fields("title", "category", "year", "vector_score").paging(0, k)

    def _rank(keys, key):
        return keys.index(key) + 1 if key in keys else "-"

    def _model(messages, config):
        question = messages[-1].content
        categories, year_floor = rs_categories.value or _all_categories, rs_year_floor.value   # read at call time, so moving a control keeps the chat
        prefilter = rs_prefilter(categories, year_floor)
        words = "".join(ch if ch.isalnum() else " " for ch in question)   # - | @ { } and the like are query syntax, so only letters and digits pass
        params = {"vector": embed_texts(EMBEDDING_MODEL, [question])[0].tobytes()}
        hybrid = [
            "FT.HYBRID", rs_index_name, "SEARCH", f"{words} {prefilter}", "SCORER", "BM25STD",
            "VSIM", "@embedding", "$vector", "KNN", 2, "K", 10, "FILTER", prefilter,   # FILTER: the vector side's pre-filter
            "COMBINE", "RRF", 4, "WINDOW", 20, "CONSTANT", 60,
            "LOAD", 1, "@__key", "LIMIT", 0, 10, "PARAMS", 2, "vector", params["vector"],
        ]
        started = time.perf_counter()
        reply = REDIS_CLIENT.execute_command(*hybrid)   # RESP2: a flat pair list whose results are field lists
        hybrid_ms = round((time.perf_counter() - started) * 1000)
        hybrid_keys = [dict(zip(row[::2], row[1::2]))["__key"] for row in dict(zip(reply[::2], reply[1::2]))["results"]]
        bm25 = Query(f"{words} {prefilter}").scorer("BM25STD").return_fields("title").paging(0, 10)
        bm25_keys = [doc.id for doc in rs_index.search(bm25).docs]
        knn_keys = [doc.id for doc in rs_index.search(rs_knn(prefilter, 10), params).docs]
        top5 = rs_index.search(rs_knn("*", 5), params).docs
        inside5 = rs_index.search(rs_knn(prefilter, 5), params).docs
        pipe = REDIS_CLIENT.pipeline(transaction=False)
        for key in hybrid_keys[:3]:
            pipe.hmget(key, "title", "body", "category", "year")
        articles = pipe.execute()
        system = (
            "You answer customers for Cairn Outfitters, an outdoor gear shop. Use only the help articles below, "
            "and name each article you use by its title. If they do not answer the question, say so.\n\n"
            + "\n\n".join(f"{title}\n{body}" for title, body, _, _ in articles)
        )
        started = time.perf_counter()
        answer = ask_llm(system, [{"role": "user", "content": question}])   # the question and its articles only, not the chat history
        claude_ms = round((time.perf_counter() - started) * 1000)
        rs_turns.append({
            "question": question, "filter": prefilter,
            "rows": [{"article": title, "category": category, "year": int(year), "BM25STD": _rank(bm25_keys, key),
                      "KNN": _rank(knn_keys, key), "FT.HYBRID": _rank(hybrid_keys, key)}
                     for key, (title, _, category, year) in zip(hybrid_keys, articles)],
            "matched": {"BM25STD": len(bm25_keys), "KNN": len(knn_keys)},
            "top 5, then filter in the app": sum(doc.category in categories and int(doc.year) >= year_floor for doc in top5),
            "filter inside KNN 5": len(inside5),
            "ms": {"FT.HYBRID": hybrid_ms, "Claude": claude_ms},
            "commands": [
                redis_cli(hybrid),
                redis_cli(["FT.SEARCH", rs_index_name, *bm25.get_args()]),
                redis_cli(["FT.SEARCH", rs_index_name, *rs_knn(prefilter, 10).get_args(), *rs_index.get_params_args(params)]),
                redis_cli(["FT.SEARCH", rs_index_name, *rs_knn("*", 5).get_args(), *rs_index.get_params_args(params)]),
                redis_cli(["FT.SEARCH", rs_index_name, *rs_knn(prefilter, 5).get_args(), *rs_index.get_params_args(params)]),
                *[redis_cli(["HMGET", key, "title", "body", "category", "year"]) for key in hybrid_keys[:3]],
            ],
        })
        return answer + "\n\n*Sent to Claude, in `FT.HYBRID` order:* " + ", ".join(title for title, _, _, _ in articles)

    rs_chat = mo.ui.chat(_model, prompts=[
        *[preset["query"] for preset in cairn.SEARCH_PRESETS],
        "Where is my parcel?",
        "Can I return a gift I got in December?",
    ])
    mo.vstack([
        mo.md(r"""Redis runs each question below through `FT.HYBRID`, with the category and year filters inside the query. Claude answers from the top three articles, and the reply lists them:"""),
        mo.hstack([rs_categories, rs_year_floor], justify="start", gap=2),
        rs_chat,
    ])
    return rs_categories, rs_chat, rs_knn, rs_prefilter, rs_turns, rs_year_floor


@app.cell(column=1)
def rs_last_turn(rs_chat, rs_turns):
    _turn = rs_turns[-1] if rs_chat.value and rs_turns else None
    _first = _turn and _turn["rows"][:1]

    def _place(rank):
        return "outside the top ten" if rank == "-" else f"at {rank}"

    mo.md("Ask the help center a question above, and this cell shows what Redis did with it.") if _turn is None else mo.vstack([
        mo.md(f"The table below ranks each article Claude received for *{_turn['question']}* under `BM25STD`, k-nearest neighbor (KNN) search, and `FT.HYBRID`, all with the same filter:"),
        mo.ui.table(_turn["rows"], selection=None, show_download=False, pagination=False),
        mo.md(
            (f"`FT.HYBRID` ranks *{_first[0]['article']}* first, `BM25STD` puts it {_place(_first[0]['BM25STD'])}, and KNN puts it {_place(_first[0]['KNN'])}. " if _first else "`FT.HYBRID` returned no article, so the keys have expired. Press Load again. ")
            + f"Of a possible ten, `BM25STD` returned {_turn['matched']['BM25STD']} and KNN returned {_turn['matched']['KNN']}. `-` means outside the top ten."
        ),
        mo.md(f"With the filter `{_turn['filter']}`, a top 5 filtered in the app kept {_turn['top 5, then filter in the app']} rows, and `KNN 5` with the filter inside returned {_turn['filter inside KNN 5']}."),
        mo.md(f"`FT.HYBRID` answered in {_turn['ms']['FT.HYBRID']} ms, and Claude in {_turn['ms']['Claude']} ms."),
        mo.accordion({"redis-cli": mo.md("```\n" + "\n".join(_turn["commands"]) + "\n```")}),
    ])
    return


@app.cell(column=1)
def rs_filter_sweep(EMBEDDING_MODEL, Query, rs_categories, rs_index, rs_index_name, rs_knn, rs_prefilter):
    _categories = rs_categories.value or sorted({article["category"] for article in cairn.HELP_ARTICLES})
    _params = {"vector": embed_texts(EMBEDDING_MODEL, ["where is my parcel"])[0].tobytes()}
    _top5 = rs_index.search(rs_knn("*", 5), _params).docs
    _rows = []
    for _year in range(2022, 2027):
        _rows.append({
            "year floor": _year,
            "articles that pass": rs_index.search(Query(rs_prefilter(_categories, _year)).no_content().paging(0, 0)).total,
            "top 5, then filter in the app": sum(doc.category in _categories and int(doc.year) >= _year for doc in _top5),
            "filter inside KNN 5": len(rs_index.search(rs_knn(rs_prefilter(_categories, _year), 5), _params).docs),
        })
    _params_args = rs_index.get_params_args(_params)
    mo.vstack([
        mo.md(r"""The cell below runs `KNN 5` for *where is my parcel* once with no filter, then once per year floor with the categories chosen above:"""),
        mo.ui.table(_rows, selection=None, show_download=False, pagination=False),
        mo.md(r"""Filtering the top five in the app drops the rows that fail, so it can return fewer than five while more articles pass. With the filter in front of `=>`, Redis keeps reading nearest neighbors until five articles pass or none are left."""),
        mo.md(f"```\n{redis_cli(['FT.SEARCH', rs_index_name, *rs_knn('*', 5).get_args(), *_params_args])}\n{redis_cli(['FT.SEARCH', rs_index_name, *rs_knn(rs_prefilter(_categories, 2024), 5).get_args(), *_params_args])}\n{redis_cli(['FT.SEARCH', rs_index_name, *Query(rs_prefilter(_categories, 2024)).no_content().paging(0, 0).get_args()])}\n```"),
    ])
    return


@app.cell(column=1, hide_code=True)
def rs_article_form(rs_index):
    rs_article_form = mo.ui.dictionary({
        "title": mo.ui.text(value="Holiday gift returns", label="Title", full_width=True),
        "body": mo.ui.text_area(value="Gifts bought from November 15 to December 31 can be returned until January 31. Bring the gift receipt or the order number.", label="Body", full_width=True),
        "category": mo.ui.dropdown(options=sorted({article["category"] for article in cairn.HELP_ARTICLES}), value="returns", label="Category"),
        "year": mo.ui.slider(2022, 2026, value=2026, label="Year", show_value=True),   # the bounds every chat query filters on
    }).form(submit_button_label="Add the article: 1 HSET, 1 EXPIRE")
    mo.vstack([
        # Reading rs_index makes this cell a child of rs_seed: a new Load builds a new, unsubmitted form,
        # so rs_add_article stops instead of replaying the last submit.
        mo.md(f"""The form below writes one more hash for `{rs_index.index_name}` to index, with a one-hour `EXPIRE`. Each submit replaces it. Submit it, then ask the chat about it:"""),
        rs_article_form,
    ])
    return (rs_article_form,)


@app.cell(column=1)
def rs_add_article(EMBEDDING_MODEL, REDIS_CLIENT, rs_article_form, rs_index, rs_index_name, rs_reader_key):
    mo.stop(rs_article_form.value is None)
    _article = rs_article_form.value
    _fields = {"title": _article["title"], "body": _article["body"], "category": _article["category"], "year": int(_article["year"]),
               "embedding": embed_texts(EMBEDDING_MODEL, [f"{_article['title']}. {_article['body']}"])[0].tobytes()}
    _before = int(rs_index.info()["num_docs"])   # FT.INFO
    REDIS_CLIENT.hset(rs_reader_key, mapping=_fields)
    REDIS_CLIENT.expire(rs_reader_key, KEY_TTL_SECONDS)
    _after = int(rs_index.info()["num_docs"])   # FT.INFO
    mo.vstack([
        mo.ui.table([{"key": rs_reader_key, "num_docs before": _before, "num_docs after": _after}], selection=None, show_download=False, pagination=False),
        mo.md(f"""`num_docs` went from {_before} to {_after}, and the app wrote only the hash. The next question in the chat searches this article too. A second submit replaces it."""),
        mo.md(f"```\n{redis_cli(['FT.INFO', rs_index_name])}\n{redis_cli(['HSET', rs_reader_key, *[part for item in _fields.items() for part in item]])}\n{redis_cli(['EXPIRE', rs_reader_key, KEY_TTL_SECONDS])}\n{redis_cli(['FT.INFO', rs_index_name])}\n```"),
    ])
    return


@app.cell(column=1, hide_code=True)
def rs_cleanup_button():
    rs_cleanup_button = mo.ui.run_button(label="Clean up: FT.DROPINDEX and UNLINK by name", kind="danger")
    mo.vstack([
        mo.md(r"""Clean up drops the index with `FT.DROPINDEX` and no `DD`, then unlinks the 36 keys and the form's article, by name. Nothing is scanned. The keys would expire within the hour anyway, but the index stays until Clean up drops it:"""),
        rs_cleanup_button,
    ])
    return (rs_cleanup_button,)


@app.cell(column=1)
def rs_cleanup(REDIS_CLIENT, rs_article_keys, rs_cleanup_button, rs_index_name, rs_reader_key):
    mo.stop(not rs_cleanup_button.value)
    try:
        REDIS_CLIENT.ft(rs_index_name).dropindex()   # FT.DROPINDEX with no DD, so the documents stay
        _index_report = "Dropped 1 index"
    except redis.ResponseError as _exc:
        if not any(_phrase in str(_exc).lower() for _phrase in ("not found", "unknown index", "no such index")):   # 8.6: SEARCH_INDEX_NOT_FOUND; older builds: Unknown index name
            raise
        _index_report = "The index was already gone"
    _keys = [*rs_article_keys, rs_reader_key]
    _removed = REDIS_CLIENT.unlink(*_keys)   # UNLINK key1 ... keyN, by name
    mo.vstack([
        mo.callout(mo.md(f"{_index_report}. Unlinked {_removed} of {len(_keys)} keys. `UNLINK` returns the count of keys that existed. The chat and the filter table need a new Load."), kind="success"),
        mo.md(f"```\n{redis_cli(['FT.DROPINDEX', rs_index_name])}\n{redis_cli(['UNLINK', *_keys[:2]])} ... ({len(_keys)} keys)\n```"),
        mo.md(r"""`DD` would delete every key under the prefix."""),
    ])
    return


@app.cell(column=2, hide_code=True)
def lc_title():
    mo.md(r"""
    # Redis LangCache

    Customers rarely ask the same question the same way twice, and an exact-match cache misses every rewording.
    Redis LangCache matches prompts by meaning, so a reworded question can get a stored answer without an LLM call.
    Store five Cairn Outfitters gear-care answers, then ask Cairn support below and move the threshold to see three principles:

    1. **Your app searches before the LLM and stores after it.** LangCache embeds every prompt itself, and your app calls the LLM only on a miss and then stores the answer.
    2. **The threshold trades wrong answers for misses.** LangCache serves any entry whose similarity clears `similarityThreshold`, so a question that only sounds similar can get a wrong answer.
    3. **A cached answer lives until its time to live (TTL) runs out.** Until `ttlMillis` passes or your app deletes it, LangCache serves the answer unchanged, so the TTL caps how stale it gets.

    LangCache is in public preview on Redis Cloud. Settings: `LANGCACHE_HOST` (with `https://`), `LANGCACHE_CACHE_ID`, and `LANGCACHE_API_KEY`, plus `ANTHROPIC_API_KEY`, which the chat needs on a miss. Docs:
    [LangCache](https://redis.io/docs/latest/develop/ai/context-engine/langcache/),
    [concepts](https://redis.io/docs/latest/develop/ai/context-engine/langcache/concepts/),
    [API and SDK examples](https://redis.io/docs/latest/develop/ai/context-engine/langcache/api-examples/), and
    [API reference](https://redis.io/docs/latest/develop/ai/context-engine/langcache/api-reference/).

    Each line reads source, target, and what passes between them:

    ```text
    App       -> LangCache        POST /entries/search with the prompt text
    LangCache -> embedding model  the prompt, embedded on the service side
    LangCache -> App              data: [entries above the threshold], or data: []
    App       -> Claude           the question, only when data is empty
    App       -> LangCache        POST /entries with the answer and ttlMillis
    ```
    """)
    return


@app.cell(column=2, hide_code=True)
def lc_seed_button():
    lc_seed_button = mo.ui.run_button(label="Store 5 answers for 15 minutes: 5 POST /entries, 16 searches", kind="success")
    mo.vstack([
        mo.md(r"""The button below runs the cache-aside loop on five gear-care answers. Each press sends a search, five stores with `ttlMillis` 900000, and the question again in other words, then 14 searches for the threshold table. The chat and the threshold table appear after the first press:"""),
        lc_seed_button,
    ])
    return (lc_seed_button,)


@app.cell(column=2)
def lc_seed(LANGCACHE, langcache_search, lc_seed_button):
    mo.stop(not lc_seed_button.value)
    # Step 1 asks the down-bag question before this press stores it, so every entry it finds is hidden.
    _, _ask_wire = langcache_search(cairn.GEAR_CARE_ANSWERS[1]["prompt"], set())
    _others = len(_ask_wire["reply"]["data"])
    _steps = [{"step": "1 ask", "request": "POST /entries/search", "status": _ask_wire["status"], "result": f"hit on {_others} hidden {'entry' if _others == 1 else 'entries'}" if _others else "miss", "ms": _ask_wire["ms"]}]

    lc_entry_keys, _store_wires = {}, []
    for _row in cairn.GEAR_CARE_ANSWERS:
        _, _body, _wire = call_service(LANGCACHE, "POST", "/entries", json_body={
            "prompt": _row["prompt"], "response": _row["response"], "ttlMillis": 900_000})   # 15 minutes
        lc_entry_keys[_body["entryId"]] = _row["key"]
        _store_wires.append({**_wire, "reply": _body})
    _steps.append({"step": "2 store 5 answers", "request": "POST /entries x5", "status": " ".join(str(_wire["status"]) for _wire in _store_wires), "result": "5 entryIds", "ms": sum(_wire["ms"] for _wire in _store_wires)})

    _hits, _again_wire = langcache_search(cairn.THRESHOLD_PROBES[0]["text"], lc_entry_keys)
    _steps.append({"step": "3 ask in other words", "request": "POST /entries/search", "status": _again_wire["status"], "result": f"hit: {lc_entry_keys[_hits[0]['id']]} at {_hits[0]['similarity']:.3f}" if _hits else "miss", "ms": _again_wire["ms"]})

    mo.vstack([
        mo.ui.table(_steps, selection=None, pagination=False),
        mo.accordion({"Requests as sent (credential masked, other entries hidden)": mo.json([_ask_wire, _store_wires[0], _again_wire])}),
        mo.md(r"""A miss is `200` with an empty `data`, never a `404`. Step 2 stores five answers written in advance, and the chat below stores Claude's answers the same way."""),
        mo.md(r"""Each request carries only text. LangCache embeds it on the service side, while the Redis Search column embeds on this machine. In a live run on 2026-10-05 step 3 hit the down-bag answer at 0.905, although *launder* and *wash* share no word."""),
        mo.md(r"""LangCache derives each `entryId` from the prompt text, so a second press reuses the same five ids. In a scratch check on 2026-10-05 a second store of one prompt answered `201` with the same id and replaced the response and TTL. Step 1 of that press then hits the earlier down-bag entry and any close answer the chat stored, all shown hidden."""),
    ])
    return (lc_entry_keys,)


@app.cell(column=2)
def lc_chat_store():
    lc_chat_entries = {}   # every answer the chat stored, by entryId; its own cell, so a seed press that resets the chat keeps them for Clean up
    return (lc_chat_entries,)


@app.cell(column=2)
def lc_chat(LANGCACHE, ask_llm, langcache_search, lc_chat_entries, lc_entry_keys):
    lc_threshold = mo.ui.slider(0.5, 1.0, step=0.01, value=0.85, show_value=True, label="`similarityThreshold`")
    lc_strategy = mo.ui.dropdown(["semantic", "exact"], value="semantic", label="`searchStrategies`")
    lc_ttl = mo.ui.dropdown({"10 s": 10_000, "1 minute": 60_000, "15 minutes": 900_000}, value="1 minute", label="`ttlMillis` for an answer stored on a miss")
    lc_turns = []   # one dict per turn
    _targets = {probe["text"]: probe["target"] for probe in cairn.THRESHOLD_PROBES}

    def _model(messages, config):
        question = messages[-1].content
        own = {**lc_entry_keys, **{entry_id: entry["prompt"] for entry_id, entry in lc_chat_entries.items()}}
        _, wire = langcache_search(question, own, threshold=lc_threshold.value, strategies=(lc_strategy.value,))
        _, candidates_wire = langcache_search(question, own, threshold=0.5)   # where the line sits, for the cell below
        turn = {
            "question": question, "search": f"{lc_strategy.value}, {lc_threshold.value:.2f}", "decision": "miss, no answer stored",
            "served": "-", "similarity": "-", "entry age s": "-", "ttlMillis": "-", "cache ms": wire["ms"], "Claude ms": "-", "store ms": "-",
            "candidates": [{"entry": own[hit["id"]] if "id" in hit else hit["hidden"], "similarity": round(hit["similarity"], 3),
                            "clears the threshold under semantic": "yes" if hit["similarity"] >= lc_threshold.value else "no"} for hit in candidates_wire["reply"]["data"]],
            "wires": [wire, candidates_wire],
        }
        lc_turns.append(turn)
        top = wire["reply"]["data"][0] if wire["reply"]["data"] else None   # what LangCache would serve

        if top and "hidden" in top:   # an entry this notebook did not store: show none of it, and store nothing over it
            turn.update(decision="hit on a hidden entry", similarity=round(top["similarity"], 3))
            return f"LangCache returned an entry from another writer or an earlier kernel, at {top['similarity']:.3f}. The notebook hides it and stores nothing."

        if top:
            label = own[top["id"]]
            right = question not in _targets or label in (_targets[question], question)
            turn.update(decision="hit" if right else "wrong answer", served=label, similarity=round(top["similarity"], 3), ttlMillis=900_000)
            if top["id"] in lc_chat_entries:
                turn["ttlMillis"] = lc_chat_entries[top["id"]]["ttlMillis"]
                turn["entry age s"] = round(time.monotonic() - lc_chat_entries[top["id"]]["stored"], 1)
            return f"{top['response']}\n\n*LangCache {turn['decision']}: `{label}` at {top['similarity']:.3f} in {wire['ms']} ms.*"

        started = time.perf_counter()
        answer = ask_llm("You are Cairn Outfitters gear-care support. Answer in at most three sentences.",
                         [{"role": "user", "content": question}])   # the question alone, so the stored answer fits anyone who asks it
        turn["Claude ms"] = round((time.perf_counter() - started) * 1000)
        _, body, store_wire = call_service(LANGCACHE, "POST", "/entries", json_body={"prompt": question, "response": answer, "ttlMillis": lc_ttl.value})
        if body["entryId"] in lc_chat_entries:   # the chat stored this question before and the search missed it, so the TTL ran out or Clean up deleted it
            turn["entry age s"] = round(time.monotonic() - lc_chat_entries[body["entryId"]]["stored"], 1)
        lc_chat_entries[body["entryId"]] = {"prompt": question, "ttlMillis": lc_ttl.value, "stored": time.monotonic()}
        turn["decision"], turn["ttlMillis"], turn["store ms"] = "miss", lc_ttl.value, store_wire["ms"]
        turn["wires"].append({**store_wire, "reply": body})
        return f"{answer}\n\n*LangCache miss in {wire['ms']} ms, so Claude answered in {turn['Claude ms']} ms. Stored with `ttlMillis` {lc_ttl.value}.*"

    lc_chat = mo.ui.chat(_model, prompts=[probe["text"] for probe in cairn.THRESHOLD_PROBES] + [
        cairn.CACHE_ASIDE_SCRIPT["prefix"] + cairn.CACHE_ASIDE_SCRIPT["ask"], cairn.CACHE_ASIDE_SCRIPT["prefix"] + cairn.CACHE_ASIDE_SCRIPT["again"]])
    mo.vstack([
        mo.md(r"""The chat below is Cairn support. Each question searches LangCache at the similarity threshold and strategy you set, and a hit returns the stored answer. A miss goes to Claude, and the notebook stores its answer with the `ttlMillis` you pick:"""),
        mo.hstack([lc_threshold, lc_strategy, lc_ttl], justify="start", wrap=True),
        lc_chat,
    ])
    return lc_chat, lc_strategy, lc_threshold, lc_ttl, lc_turns


@app.cell(column=2)
def lc_last_turn(lc_chat, lc_turns):
    if not lc_chat.value:
        _output = mo.md("Ask Cairn support a question, or pick a suggested prompt, and this cell shows what LangCache did.")
    else:
        _turn = lc_turns[-1]
        _claude = f"then Claude answered in {_turn['Claude ms']} ms" if _turn["decision"] == "miss" else "and no Claude call followed"
        _output = mo.vstack([
            mo.md(f"""Last turn: **{_turn['decision']}**. LangCache answered the search (`{_turn['search']}`) in {_turn['cache ms']} ms, {_claude}. A second `semantic` search at 0.50 returns at most five of the closest entries and marks which clear your threshold:"""),
            mo.ui.table(_turn["candidates"], selection=None, pagination=False),
            mo.md(r"""Every turn so far, oldest first. A hit on an answer the chat stored, or a miss that stores it again, shows the answer's age next to its `ttlMillis`:"""),
            mo.ui.table([{key: value for key, value in turn.items() if key not in ("candidates", "wires")} for turn in lc_turns], selection=None, pagination=False),
            mo.accordion({"Last turn's requests as sent (credential masked, other entries hidden)": mo.json(_turn["wires"])}),
            mo.md(r"""Claude sees only the question, so a stored answer fits anyone who asks it. Pick 10 s, ask a question the cache misses, and ask it again before and after 10 s. A new service's default TTL is no expiration, so an answer stored without `ttlMillis` is served until your app deletes it."""),
        ])
    _output
    return


@app.cell(column=2)
def lc_threshold_sweep(langcache_search, lc_entry_keys):
    lc_probe_scores, lc_sweep_wire = [], None
    for _probe in cairn.THRESHOLD_PROBES:
        _exact, _ = langcache_search(_probe["text"], lc_entry_keys, strategies=("exact",))
        _semantic, _wire = langcache_search(_probe["text"], lc_entry_keys, threshold=0.5)   # one low search feeds every threshold the slider can set
        lc_sweep_wire = lc_sweep_wire or _wire
        lc_probe_scores.append({**_probe, "exact": _exact[:1], "semantic": _semantic[:1]})
    return lc_probe_scores, lc_sweep_wire


@app.cell(column=2)
def lc_threshold_table(lc_entry_keys, lc_probe_scores, lc_sweep_wire, lc_threshold):
    def _verdict(hits, target):
        if not hits or hits[0]["similarity"] < lc_threshold.value:
            return "miss"
        return "hit" if lc_entry_keys[hits[0]["id"]] == target else "wrong answer"   # a None target is always wrong

    _column = f"semantic at {lc_threshold.value:.2f}"
    _rows = [{
        "question": _probe["text"], "kind": _probe["kind"],
        "served": lc_entry_keys[_probe["semantic"][0]["id"]] if _probe["semantic"] else "-",
        "similarity": round(_probe["semantic"][0]["similarity"], 3) if _probe["semantic"] else "-",
        "exact": _verdict(_probe["exact"], _probe["target"]), _column: _verdict(_probe["semantic"], _probe["target"]),
    } for _probe in lc_probe_scores]
    _rows.append({"question": "hits / wrong answers", "kind": "", "served": "", "similarity": "", **{
        _key: f"{sum(_row[_key] == 'hit' for _row in _rows)} / {sum(_row[_key] == 'wrong answer' for _row in _rows)}" for _key in ("exact", _column)}})
    _tints = {"hit": "rgba(46, 160, 67, 0.3)", "wrong answer": "rgba(248, 81, 73, 0.3)", "miss": "rgba(110, 118, 129, 0.3)"}
    mo.vstack([
        mo.md(r"""The table below scores seven labeled questions against this notebook's five entries, with one `exact` and one `semantic` search at 0.50 each. Moving the slider re-applies the similarity threshold to those scores without a new request:"""),
        lc_threshold,
        mo.ui.table(_rows, selection=None, pagination=False, style_cell=lambda row_id, column, value: {"backgroundColor": _tints[value]} if value in _tints else {}),
        mo.accordion({"First semantic request as sent (credential masked, other entries hidden)": mo.json(lc_sweep_wire)}),
        mo.md(r"""In a live run on 2026-10-05 the synthetic bag scored 0.950 against the down-bag answer, above every paraphrase. No similarity threshold in that run could keep a hit and remove that wrong answer."""),
        mo.md(r"""Under `exact` only the all-caps copy can hit, and in that run `semantic` returned no entry for it at 0.50. The docs recommend starting at the tight end of 0.8 to 0.9, then loosening while you spot-check matches."""),
    ])
    return


@app.cell(column=2, hide_code=True)
def lc_cleanup_button():
    lc_cleanup_button = mo.ui.run_button(label="Clean up: DELETE the 5 answers and the chat's answers by id", kind="danger")
    mo.vstack([
        mo.md(r"""Clean up sends one `DELETE /entries/{entryId}` for each of the five gear-care prompts and for each answer the chat stored. A `204` is a deletion, and a `404` means the TTL or an earlier Clean up removed the entry first:"""),
        lc_cleanup_button,
    ])
    return (lc_cleanup_button,)


@app.cell(column=2)
def lc_cleanup(LANGCACHE, lc_chat_entries, lc_cleanup_button, lc_entry_keys):
    mo.stop(not lc_cleanup_button.value)
    _statuses, _wires = [], []
    for _entry_id in [*lc_entry_keys, *lc_chat_entries]:   # one id per prompt: LangCache derives it from the prompt text, so this deletes that prompt's entry whoever stored it
        _status, _, _wire = call_service(LANGCACHE, "DELETE", f"/entries/{_entry_id}", ok=(204, 404))
        _statuses.append(_status)
        _wires.append(_wire)
    mo.vstack([
        mo.callout(mo.md(f"Deleted {_statuses.count(204)}, already gone {_statuses.count(404)}."), kind="success"),
        mo.accordion({"Requests as sent (credential masked)": mo.json(_wires)}),
        mo.md(r"""Because LangCache derives each id from the prompt text, Clean up deletes the entries for these prompts, whoever stored them. On a cache of your own, that means this notebook. Every entry this column stores also carries a TTL."""),
    ])
    return


@app.cell(column=3, hide_code=True)
def cr_title():
    mo.md(r"""
    # Redis Context Retriever

    An agent that writes its own queries can run any query your database allows, including one planted in a ticket it reads.
    Redis Context Retriever gives the agent a fixed set of MCP tools instead, generated from a data model you declare once.
    Create a surface from three Cairn classes, then call its tools from the console to see three principles:

    1. **The data model generates the tools.** Three Python classes become the MCP tools the surface lists, and each field's `index` kind decides which tools exist.
    2. **The agent can call only what the model defines.** A console sends the tool and JSON arguments you choose, with three presets that step outside the model, and a table shows each answer.
    3. **Context Retriever is a layer on top of your Redis.** It writes JSON documents and one index per entity into your database, and the documents stay after Clean up deletes the surface.

    Context Retriever is in preview on Redis Cloud, and it does not support Redis Flex or Active-Active databases.
    Settings: `CTX_ADMIN_KEY`, plus a `REDIS_URL` for a scratch database with Search and JSON that the hosted service can reach.
    Create sends that database's password to the service, which keeps it until Clean up deletes the surface.
    Docs: [Context Retriever](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/) and
    [concepts](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/concepts/).

    Each line reads source, target, and what passes between them:

    ```text
    Python classes  -> data_model JSON  export_data_model, in this kernel
    data_model JSON -> surface          POST /context-surfaces, with your database password
    surface         -> your Redis       JSON documents, one index per entity, a read per tool call
    notebook        -> surface          tools/list once, then one tools/call per console submit, with an agent key
    ```
    """)
    return


@app.cell(column=3)
def cr_data_model():
    from typing import Any
    from context_surfaces import ContextField, ContextModel, ContextRelationship, export_data_model

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
        """An item in the Cairn catalog."""

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
    cr_data_model = export_data_model("Cairn Outfitters", "Customers, products, and orders of a fictional outdoor-gear shop", entities=[Customer, Product, Order])   # always entities=: without it the SDK exports every ContextModel ever defined in the kernel
    cr_record_keys = [cls.__redis_key_template__.format(**row) for entity, cls in cr_entity_classes.items() for row in cairn.SHOP_RECORDS[entity]]
    mo.vstack([
        mo.md(r"""
    This cell declares the data model as three Python classes and sends nothing.
    `__redis_key_template__` names the key each record gets in your Redis.
    Each field's `index` kind decides which tools the service generates:

    | You declare | The service generates |
    |---|---|
    | `is_key_component=True` | `get_<entity>_by_id` |
    | Any entity | `list_<entity>` and `count_<entity>` |
    | A `numeric` field | `summarize_<entity>` |
    | A `tag` or `numeric` field | `filter_<entity>`, with that field in its menu |
    | A `text` field | `search_<entity>_by_text` |
    | A `ContextRelationship` | no tool, only an entry under `relationships` in the exported data model |
    | Any model | `intersect_results`, `union_results`, `except_results`, and `expand_results` |
    """),
        mo.accordion({f"export_data_model output ({cr_data_model['entity_count']} entities)": mo.json(cr_data_model)}),
    ])
    return cr_data_model, cr_entity_classes, cr_record_keys


@app.cell(column=3, hide_code=True)
def cr_create_button():
    cr_surface_name = f"cairn-{RUN_TAG}"
    cr_create_button = mo.ui.run_button(label="Create the surface: 6 admin requests, with your database password", kind="success")
    mo.vstack([
        mo.md(r"""
    The button below sends six admin requests:

    1. `POST /context-surfaces` with the data model and your database password.
    2. `POST .../agent-keys` for an agent key that expires in one hour.
    3. `GET .../agent-keys` for the surface's MCP address.
    4. `POST .../data` three times, once per entity.
    """),
        cr_create_button,
        mo.md("The service keeps the password until Clean up deletes the surface. If you press Create again, Clean up still deletes every surface with this name."),
    ])
    return cr_create_button, cr_surface_name


@app.cell(column=3)
def cr_create(
    CONTEXT_RETRIEVER,
    REDIS_CLIENT,
    cr_create_button,
    cr_data_model,
    cr_entity_classes,
    cr_record_keys,
    cr_surface_name,
):
    from urllib.parse import unquote, urlsplit

    mo.stop(not cr_create_button.value)
    _url = urlsplit(os.environ["REDIS_URL"])   # cell-local, so the password is never a global
    # Never retried (niquests' default): a replayed create would make a second surface holding your password.
    _status, _surface, _wire = call_service(CONTEXT_RETRIEVER, "POST", "/context-surfaces", json_body={
        "name": cr_surface_name,
        "description": "Made by the Redis Iris basics notebook. Safe to delete.",
        "metadata": {"created_by": "iris-basics-notebook", "run": RUN_TAG},
        "data_model": cr_data_model,
        "data_source": {"type": "redis", "connection_config": {
            "addr": f"{_url.hostname}:{_url.port or 6379}",
            "username": unquote(_url.username or "default"),
            "password": unquote(_url.password or ""),
            "db": int(_url.path.lstrip("/") or 0),
            "tls_enabled": _url.scheme == "rediss",
            "pool_size": 2, "min_idle_conns": 0,   # a free database allows 30 connections
        }},
    })
    _wire["body"]["data_source"]["connection_config"]["password"] = "***"   # mask on the line after the call, before anything can show it
    cr_surface_id = _surface["id"]
    _expires = (datetime.now(timezone.utc) + timedelta(seconds=KEY_TTL_SECONDS)).isoformat(timespec="seconds")
    _, _key, _ = call_service(CONTEXT_RETRIEVER, "POST", f"/context-surfaces/{cr_surface_id}/agent-keys", json_body={"name": f"{cr_surface_name}-agent", "expires_at": _expires})
    _, _keys, _ = call_service(CONTEXT_RETRIEVER, "GET", f"/context-surfaces/{cr_surface_id}/agent-keys", params={"page_size": 50})
    cr_mcp_url = next(item["mcp_url"] for item in _keys["agent_keys"] if item["id"] == _key["id"])
    # The agent key lives in this session's headers and nowhere else. Accept: */* is what the live run sent.
    cr_mcp = iris_session(cr_mcp_url, {"X-API-Key": _key["key"], "Accept": "*/*"})
    _imported = {}
    try:
        for _entity, _model in cr_entity_classes.items():
            _, _reply, _ = call_service(CONTEXT_RETRIEVER, "POST", f"/context-surfaces/{cr_surface_id}/data", json_body={
                "entity": _entity,
                "records": [_model(**row).model_dump() for row in cairn.SHOP_RECORDS[_entity]],
                "options": {"on_conflict": "overwrite", "on_error": "fail_fast"},
            })
            _imported[_entity] = f"{_reply['imported']} imported, {_reply['failed']} failed"
    finally:   # the service sets no TTL on what it writes
        _expired = sum(REDIS_CLIENT.expire(_record_key, KEY_TTL_SECONDS) for _record_key in cr_record_keys)
    mo.vstack([
        mo.callout(mo.md(f"Context Retriever answered `{_status}` with surface `{cr_surface_id}`, status `{_surface['status']}`. The agent key expires at {_expires}."), kind="success"),
        mo.md("\n".join(f"- `{entity}`: {counts}" for entity, counts in _imported.items())),
        mo.accordion({"Create request as sent (credential and password masked)": mo.json(_wire)}),
        mo.md(f"The service sets no TTL on what it writes. The notebook sent a one-hour `EXPIRE` to each of the {len(cr_record_keys)} keys, and Redis answered `1` for {_expired}:"),
        mo.md("```\n" + redis_cli(["EXPIRE", cr_record_keys[0], KEY_TTL_SECONDS]) + "\n```"),
    ])
    return cr_mcp, cr_mcp_url, cr_surface_id


@app.cell(column=3)
def cr_tools(cr_mcp, cr_mcp_url):
    # The absolute URL as the path: niquests sends it unchanged, where "" would drop a trailing slash.
    _, _body, _wire = call_service(cr_mcp, "POST", cr_mcp_url, json_body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    _rows = sorted(({
        "entity": next((name for name in ("customer", "product", "order") if name in tool["name"].split("_")), "set tools"),
        "tool": tool["name"],
        "description": tool.get("description", ""),
        "arguments": ", ".join(name + ("*" if name in tool["inputSchema"].get("required", []) else "") for name in tool["inputSchema"]["properties"]),
    } for tool in _body["result"]["tools"]), key=lambda row: row["entity"])
    _counts = {row["entity"]: sum(other["entity"] == row["entity"] for other in _rows) for row in _rows}
    cr_tool_names = sorted(tool["name"] for tool in _body["result"]["tools"])
    mo.vstack([
        mo.md("This cell sends one JSON-RPC `tools/list` over MCP with the agent key. Every row is a tool the service generated from the three classes above:"),
        mo.ui.table(_rows, selection=None, pagination=False, label=f"{len(_rows)} tools: " + ", ".join(f"{entity} {count}" for entity, count in _counts.items())),
        mo.md("Required arguments are starred. In a live run on 2026-09-30 this account's surface listed 21 tools. Order has no `text` field, so the service generated no `search_order_by_text`."),
        mo.accordion({"Request as sent (credential masked)": mo.json(_wire)}),
    ])
    return (cr_tool_names,)


@app.cell(column=3)
def cr_console(cr_tool_names):
    # One example per generated tool, from the Cairn records: id, a tag field and value, a numeric field.
    _entities = {"customer": ("C001", "tier", "gold", "lifetime_value"), "product": ("P102", "category", "camping", "price"), "order": ("O1001", "status", "delivered", "total")}
    _examples = {}
    for _entity, (_id, _tag, _value, _number) in _entities.items():
        _examples |= {
            f"get_{_entity}_by_id": {"id": _id},
            f"list_{_entity}": {"limit": 5},
            f"count_{_entity}": {"function": "count", "group_by": _tag},
            f"summarize_{_entity}": {"field": _number, "function": "avg"},
            f"filter_{_entity}": {"tag_conditions": [{"field": _tag, "value": _value}]},
        }
    _sets = [{"entity": "Customer", "tag_conditions": [{"field": "tier", "value": "gold"}]}, {"entity": "Customer", "tag_conditions": [{"field": "region", "value": "emea"}]}]
    _examples |= {
        "search_customer_by_text": {"query": "Ada"}, "search_product_by_text": {"query": "tent"},
        "intersect_results": {"sets": _sets}, "union_results": {"sets": _sets}, "except_results": {"sets": _sets},
        "expand_results": {"set": _sets[0], "relationship": "orders"},
    }
    # Three calls that step outside the model: a tool rule, an argument it never offered, and the tool list.
    _presets = {
        "filter_customer, no condition": ("filter_customer", {"limit": 5, "offset": 0}),
        "filter_customer, a tag condition on a numeric field": ("filter_customer", {"tag_conditions": [{"field": "lifetime_value", "value": "4200"}]}),
        "run_query, a raw query no generated tool takes": ("run_query", {"query": "KEYS *"}),
    }
    cr_example = mo.ui.dropdown(
        options={**{name: (name, _examples.get(name, {})) for name in cr_tool_names}, **_presets},   # a tool with no example here starts from {}
        value="get_customer_by_id", label="Tool or preset", searchable=True,
    )
    cr_calls = []   # one dict per submitted call. A change to the dropdown or the form never re-runs this cell
    mo.vstack([
        mo.md(f"The console below sends one read-only `tools/call` per submit, with an agent key that expires one hour after Create. When it expires, press Clean up, then Create. Pick one of the {len(cr_tool_names)} generated tools, or a preset that steps outside the model:"),
        cr_example,
    ])
    return cr_calls, cr_example


@app.cell(column=3)
def cr_call_form(cr_example):
    _tool, _arguments = cr_example.value
    cr_call_form = mo.md("**Tool** {tool}\n\n**Arguments, as JSON** {arguments}").batch(
        tool=mo.ui.text(value=_tool, full_width=True),
        arguments=mo.ui.code_editor(value=json.dumps(_arguments, indent=2), language="json", min_height=120),
    ).form(submit_button_label="Send tools/call (read-only)")
    mo.vstack([
        cr_call_form,
        mo.md("You can edit the tool name and the arguments before you send. The table below labels each answer as data, a tool error, or a JSON-RPC error. Any HTTP status other than 200 or 400 raises an error in the cell instead."),
    ])
    return (cr_call_form,)


@app.cell(column=3)
def cr_last_call(cr_call_form, cr_calls, cr_mcp, cr_mcp_url, cr_tool_names):
    if cr_call_form.value is not None:   # None until this form's first submit
        _tool, _arguments = cr_call_form.value["tool"], json.loads(cr_call_form.value["arguments"])
        # A JSON-RPC error may ride on a 200 or a 400, so both are answers here.
        _status, _body, _wire = call_service(cr_mcp, "POST", cr_mcp_url, json_body={"jsonrpc": "2.0", "id": len(cr_calls) + 2, "method": "tools/call", "params": {"name": _tool, "arguments": _arguments}}, ok=(200, 400))
        _error = _body.get("error")   # a JSON-RPC error carries no result
        _text = _error["message"] if _error else "".join(part["text"] for part in _body["result"]["content"])
        _answer = f"JSON-RPC error {_error['code']}" if _error else "tool error" if _body["result"].get("isError") else "data"
        cr_calls.append({"row": {"call": len(cr_calls) + 1, "tool": _tool, "in tools/list": _tool in cr_tool_names, "arguments": json.dumps(_arguments), "HTTP": _status, "answer": _answer, "reply": _text[:160]}, "text": _text, "wire": _wire})
    mo.vstack([
        mo.md("This cell sends the form's tool and arguments as one JSON-RPC `tools/call` over MCP with the agent key. The table lists every call to this surface, newest first:"),
        mo.ui.table([call["row"] for call in reversed(cr_calls)], selection=None, pagination=False, label=f"{len(cr_calls)} {'call' if len(cr_calls) == 1 else 'calls'} to this surface"),
        mo.md(f"**Call {len(cr_calls)}, `{cr_calls[-1]['row']['tool']}`: reply text as returned**"),
        mo.plain_text(cr_calls[-1]["text"]),
        mo.accordion({f"Call {len(cr_calls)} as sent (credential masked)": mo.json(cr_calls[-1]["wire"])}),
        mo.md("In a live run on 2026-09-30 the no-condition call came back as a tool error, `at least one condition is required`. The table prints each answer as the service sends it. A `get_customer_by_id` reply carries a `_retrieval` stamp that names the surface that served the record."),
    ]) if cr_calls else mo.md("Send a tool call above, and this cell shows what the surface answered.")
    return


@app.cell(column=3)
def cr_peek(REDIS_CLIENT, cr_record_keys, cr_surface_id):
    _indexes = sorted(name for name in REDIS_CLIENT.execute_command("FT._LIST") if cr_surface_id in name)   # the service names them idx:<surface-id>:<hash>:<entity>
    _rows, _commands = [], [redis_cli(["JSON.GET", cr_record_keys[0]]), redis_cli(["FT._LIST"])]
    for _name in _indexes:
        _info = REDIS_CLIENT.ft(_name).info()
        _definition = dict(zip(_info["index_definition"][::2], _info["index_definition"][1::2]))   # RESP2 hands index_definition back as a flat list
        _rows.append({"index": _name, "documents": _info["num_docs"], "prefix": ", ".join(_definition["prefixes"])})
        _commands.append(redis_cli(["FT.INFO", _name]))
    mo.vstack([
        mo.md("This cell reads your own database with `JSON.GET`, `FT._LIST`, and `FT.INFO`, to show where the service put the data:"),
        mo.json(REDIS_CLIENT.json().get(cr_record_keys[0]), label=cr_record_keys[0]),
        mo.ui.table(_rows, selection=None, pagination=False, label=f"{len(_rows)} indexes the service built"),
        mo.md("```\n" + "\n".join(_commands) + "\n```"),
        mo.md("Context Retriever wrote these JSON documents at your key templates and built one index per entity in your database. The console's tool calls above read them back through the service."),
    ])
    return


@app.cell(column=3, hide_code=True)
def cr_cleanup_button(cr_surface_name):
    cr_cleanup_button = mo.ui.run_button(label="Clean up: DELETE the surface and UNLINK 20 keys", kind="danger")
    mo.vstack([
        mo.md(f"Clean up deletes every surface named `{cr_surface_name}` on this account, which also revokes their agent keys. It then counts what the delete left in your Redis and unlinks the 20 keys by name. A surface has no TTL and holds your password, so press this button when you finish:"),
        cr_cleanup_button,
    ])
    return (cr_cleanup_button,)


@app.cell(column=3)
def cr_cleanup(
    CONTEXT_RETRIEVER,
    REDIS_CLIENT,
    cr_cleanup_button,
    cr_record_keys,
    cr_surface_name,
):
    mo.stop(not cr_cleanup_button.value)
    _surfaces, _page, _more = [], 1, True
    while _more:   # the listing names other surfaces on the account, so it is never shown
        _, _body, _ = call_service(CONTEXT_RETRIEVER, "GET", "/context-surfaces", params={"page": _page, "page_size": 50})
        _surfaces += [surface["id"] for surface in _body["context_surfaces"] if surface["name"] == cr_surface_name]
        _more, _page = _body["pagination"]["has_next"], _page + 1
    # Statuses only: the reply shape is unverified, and the surface held your password.
    _statuses = [call_service(CONTEXT_RETRIEVER, "DELETE", f"/context-surfaces/{surface}", ok=(200, 204, 404))[0] for surface in _surfaces]
    _indexes_left = [name for name in REDIS_CLIENT.execute_command("FT._LIST") if any(surface in name for surface in _surfaces)]
    _documents_left = REDIS_CLIENT.exists(*cr_record_keys)
    _unlinked = REDIS_CLIENT.unlink(*cr_record_keys)
    _answers = ", ".join(f"`{status}`" for status in _statuses) or "nothing, because none was listed"
    _already_gone = _statuses.count(404)
    _deleted = len(_statuses) - _already_gone
    mo.vstack([
        mo.callout(mo.md(f"Context Retriever deleted {_deleted} {'surface' if _deleted == 1 else 'surfaces'} named `{cr_surface_name}` and found {_already_gone} already gone (`DELETE` answered {_answers}). Your Redis still holds {len(_indexes_left)} of their indexes. {_documents_left} of the {len(cr_record_keys)} documents remained, so the notebook unlinked {_unlinked}."), kind="success"),
        mo.md("```\n" + "\n".join([redis_cli(["FT._LIST"]), redis_cli(["EXISTS", *cr_record_keys]), redis_cli(["UNLINK", *cr_record_keys])]) + "\n```"),
        mo.md("Deleting a surface leaves the data in your database, as the Context Retriever docs state."),
    ])
    return


@app.cell(column=4, hide_code=True)
def am_title():
    mo.md(r"""
    # Redis Agent Memory

    An agent that forgets a customer's nut allergy between conversations can suggest the wrong trail snack next time.
    Redis Agent Memory keeps each conversation in session memory and durable facts in long-term memory, written by the app or extracted from session events.
    Write five memories about Dana, a made-up Cairn customer, then chat as Dana and change what the agent recalls to see three principles:

    1. **Long-term memory recalls facts by meaning.** Questions that share no word with the fact they are after still find it, ranked best first with no score in the reply.
    2. **The owner and namespace are the tenant boundary.** The API key opens the whole store, and Agent Memory returns only memories matching the `ownerId` and `namespace` the app sends.
    3. **An agent turn reads both tiers.** The app fetches the session by id in event order, searches long-term memory with the latest message, and joins both into one prompt.

    Agent Memory is in public preview on Redis Cloud. Settings: `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY`, plus `ANTHROPIC_API_KEY`, which the chat and the cell under it need.
    Docs: [Agent Memory](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/),
    [developer guide](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/developer-guide/), and
    [REST quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/rest-api-quickstart/).

    Each line reads source, target, and what passes between them:

    ```text
    App            -> long-term memory  POST /long-term-memory, five memories with ids the app chooses
    App            -> session memory    POST /session-memory/events, the customer's message
    App            -> long-term memory  POST /long-term-memory/search, the message, filtered by owner and namespace
    App            -> session memory    GET /session-memory/{sessionId}, events in order
    App            -> Claude            the session events and the recalled memories, one call
    App            -> session memory    POST /session-memory/events, the agent's reply
    session memory -> long-term memory  background extraction, every 5 minutes by default
    ```
    """)
    return


@app.cell(column=4, hide_code=True)
def am_seed_button():
    am_seed_button = mo.ui.run_button(label="Write 5 memories about Dana: 1 POST /long-term-memory", kind="success")
    mo.vstack([
        mo.md("The button below writes five memories about Dana in one bulk `POST /long-term-memory`. Each memory carries an `id`, `ownerId`, and `namespace` chosen here:"),
        am_seed_button,
    ])
    return (am_seed_button,)


@app.cell(column=4)
def am_seed(
    AGENT_MEMORY,
    MEMORY_NAMESPACE,
    MEMORY_OWNER_ID,
    am_seed_button,
):
    mo.stop(not am_seed_button.value)
    am_memories = [
        {"id": f"{MEMORY_OWNER_ID}-m{n}", "text": fact["text"], "ownerId": MEMORY_OWNER_ID,
         "namespace": MEMORY_NAMESPACE, "memoryType": "semantic", "topics": fact["topics"]}
        for n, fact in enumerate(cairn.DANA_FACTS, 1)
    ]
    _status, _body, _wire = call_service(AGENT_MEMORY, "POST", "/long-term-memory", json_body={"memories": am_memories})
    mo.vstack([
        mo.ui.table(am_memories, selection=None, pagination=False),
        mo.md(
            f"Agent Memory answered `{_status}`, and `created` lists {len(_body['created'])} of the {len(am_memories)} ids sent. "
            "The ids are chosen here, so a second press writes the same five memories. "
            "In a live run on 2026-10-05 a second write answered `201` with the same five ids, and the owner still held 5 memories."
        ),
        mo.accordion({"Request as sent (credential masked)": mo.json(_wire), "Reply": mo.json(_body)}),
    ])
    return (am_memories,)


@app.cell(column=4)
def am_chat(
    AGENT_MEMORY,
    MEMORY_NAMESPACE,
    MEMORY_OWNER_ID,
    ask_llm,
):
    # The controls live in this cell, so moving one never re-runs it and the chat history stays.
    am_threshold = mo.ui.slider(0.0, 1.0, step=0.05, value=0.0, debounce=True, show_value=True, label="`similarityThreshold`")
    am_scope = mo.ui.dropdown(
        options={
            "Dana: this notebook's owner and namespace": {"ownerId": {"eq": MEMORY_OWNER_ID}, "namespace": {"eq": MEMORY_NAMESPACE}},
            "Another customer: a different ownerId": {"ownerId": {"eq": f"{MEMORY_OWNER_ID}-other"}, "namespace": {"eq": MEMORY_NAMESPACE}},
            "Another app: a different namespace": {"ownerId": {"eq": MEMORY_OWNER_ID}, "namespace": {"eq": "no-such-namespace"}},
        },
        value="Dana: this notebook's owner and namespace",
        label="Recall scope",
    )
    am_turns = []         # one record per completed turn, read by the cells below
    _session_ids = []     # the first message, or the first after the chat is cleared, starts a session

    def _post_event(session_id, role, text):
        _, _, wire = call_service(AGENT_MEMORY, "POST", "/session-memory/events", json_body={
            "sessionId": session_id, "actorId": MEMORY_OWNER_ID if role == "USER" else "cairn-agent",
            "role": role, "content": [{"text": text}], "namespace": MEMORY_NAMESPACE,
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "metadata": {"source": "iris-basics"},
        })
        return wire

    def _model(messages, _config):
        question = messages[-1].content
        if len(messages) == 1:
            _session_ids.append(f"{MEMORY_OWNER_ID}-s{datetime.now(timezone.utc):%Y%m%d%H%M%S}")
        session_id = _session_ids[-1]

        # 1. App -> session memory: POST /session-memory/events, the customer's message
        customer_wire = _post_event(session_id, "USER", question)

        # 2. App -> long-term memory: POST /long-term-memory/search, filtered by the scope and threshold above
        search = {"text": question, "filter": am_scope.value, "filterOp": "all", "similarityThreshold": am_threshold.value, "limit": 3}
        _, found, search_wire = call_service(AGENT_MEMORY, "POST", "/long-term-memory/search", json_body=search)

        # 3. App -> session memory: GET /session-memory/{sessionId}, events in order
        _, session, read_wire = call_service(AGENT_MEMORY, "GET", f"/session-memory/{session_id}")

        # 4. App -> Claude: the session events and the recalled memories, one call
        system = "\n".join([
            "You are the Cairn Outfitters support agent. Answer the customer in two or three sentences.",
            "Long-term memory recalled for this customer:",
            *[f"- {item['text']}" for item in found["items"]],
        ])
        history = [{"role": event["role"].lower(), "content": event["content"][0]["text"]} for event in session["events"]]
        started = time.perf_counter()
        reply = ask_llm(system, history)
        llm_ms = round((time.perf_counter() - started) * 1000)

        # 5. App -> session memory: POST /session-memory/events, the agent's reply
        agent_wire = _post_event(session_id, "ASSISTANT", reply)

        am_turns.append({
            "question": question, "sessionId": session_id, "search": search, "found": found, "session": session,
            "system": system, "history": history, "llm_ms": llm_ms,
            "wires": {"Customer turn": customer_wire, "Long-term memory search": search_wire,
                      "Session read-back": read_wire, "Agent turn": agent_wire},
        })
        return reply

    am_chat = mo.ui.chat(_model, prompts=[
        *[question["text"] for question in cairn.MEMORY_QUESTIONS],
        cairn.SESSION_TURNS[0]["text"],   # the two facts Dana states in the sample conversation
        cairn.SESSION_TURNS[2]["text"],
    ])
    mo.vstack([
        mo.md("The chat below is the Cairn agent, and you are Dana. Each message becomes a session event, then a long-term memory search under these two controls:"),
        mo.hstack([am_scope, am_threshold], justify="start", gap=2),
        am_chat,
        mo.md("Agent Memory also extracts facts from session events in the background, every 5 minutes by default. It can turn what Dana tells the agent into long-term memories."),
    ])
    return am_chat, am_scope, am_threshold, am_turns


@app.cell(column=4)
def am_last_turn(am_chat, am_turns):
    am_chat.value   # reading the chat re-runs this cell after every reply
    if not am_turns:
        _view = mo.md("Ask the Cairn agent a question above, and this cell shows what the agent received.")
    else:
        _turn = am_turns[-1]
        _events = [{"role": event["role"], "actor": event["actorId"], "text": event["content"][0]["text"], "createdAt": event["createdAt"]}
                   for event in _turn["session"]["events"]]
        _recalled = [{"rank": rank, "memory": item["text"], "content words in common with the message": len(shared_stems(_turn["question"], item["text"]))}
                     for rank, item in enumerate(_turn["found"]["items"], 1)]
        _prompt = "\n".join([_turn["system"], "", *[f"{message['role']}: {message['content']}" for message in _turn["history"]]])
        _view = mo.vstack([
            mo.md(
                f"**Session memory.** Agent Memory returned session `{_turn['sessionId']}` by id, in event order. "
                f"Its `ownerId` is `{_turn['session']['ownerId']}` and its `namespace` is `{_turn['session']['namespace']}`. "
                "The API reference says a session takes both from its first event."
            ),
            mo.ui.table(_events, selection=None, pagination=False),
            mo.md(
                f"**Long-term memory.** The search for the message returned {len(_recalled)} of at most 3 memories "
                f"at `similarityThreshold` {_turn['search']['similarityThreshold']}, with this filter: `{json.dumps(_turn['search']['filter'])}`"
            ),
            mo.ui.table(_recalled, selection=None, pagination=False),
            mo.md(
                f"**What Claude received.** The app made one call. Session events: {len(_turn['history'])}. Recalled memories: {len(_recalled)}. "
                f"Claude answered in {_turn['llm_ms']} ms."
            ),
            mo.plain_text(_prompt),
            mo.accordion({
                **{f"{step} as sent (credential masked)": mo.json(wire) for step, wire in _turn["wires"].items()},
                "Long-term memory search reply": mo.json(_turn["found"]),
            }),
        ])
    _view
    return


@app.cell(column=4)
def am_recall(
    AGENT_MEMORY,
    am_memories,
    am_scope,
    am_threshold,
):
    _rows = []
    for _question in cairn.MEMORY_QUESTIONS:
        _, _body, _wire = call_service(AGENT_MEMORY, "POST", "/long-term-memory/search", json_body={
            "text": _question["text"], "filter": am_scope.value,
            "filterOp": "all", "similarityThreshold": am_threshold.value, "limit": 100,
        })
        _ids = [item["id"] for item in _body["items"]]
        _fact = am_memories[_question["target"] - 1]
        _rank = f"#{_ids.index(_fact['id']) + 1} of {len(_ids)}" if _fact["id"] in _ids else f"not in the {len(_ids)} returned"
        _rows.append({"question": _question["text"], "fact it is after": _fact["text"],
                      "content words in common": len(shared_stems(_question["text"], _fact["text"])), "rank": _rank})
    mo.vstack([
        mo.md("The cell below asks three questions, each sharing no content word with the fact it is after. It uses the scope and similarity threshold set above the chat. At the default 0.0 every memory comes back ranked:"),
        mo.ui.table(_rows, selection=None, pagination=False),
        mo.md(
            "Content words are word stems with stopwords removed. "
            "The reply ranks memories best first with no score, and `similarityThreshold` cuts on a similarity it never shows. "
            "Under the other two scopes every row reads `not in the 0 returned`, with the same API key. "
            "The key alone reaches every record in the store, and `GET /session-memory?includeAll=true` lists every session in it."
        ),
        mo.accordion({"Last request as sent (credential masked)": mo.json(_wire), "Last reply": mo.json(_body)}),
    ])
    return


@app.cell(column=4, hide_code=True)
def am_cleanup_button():
    am_cleanup_button = mo.ui.run_button(label="Clean up: DELETE this owner's sessions and memories", kind="danger")
    mo.vstack([
        mo.md("Agent Memory sets no TTL per record. By default, the store expires sessions after 1 hour and long-term memories after 365 days. Clean up deletes this owner's sessions, lists its memories by owner alone, and deletes only those whose `ownerId` is this notebook's:"),
        am_cleanup_button,
        mo.md("After chatting, press it again ten minutes later to catch extracted memories."),
    ])
    return (am_cleanup_button,)


@app.cell(column=4)
def am_cleanup(
    AGENT_MEMORY,
    MEMORY_OWNER_ID,
    am_cleanup_button,
):
    mo.stop(not am_cleanup_button.value)
    # filterOwnerId, never includeAll: includeAll lists every session in the shared store.
    _, _sessions, _ = call_service(AGENT_MEMORY, "GET", "/session-memory", params={"filterOwnerId": MEMORY_OWNER_ID, "limit": 1000})
    _gone = []
    for _session_id in _sessions["items"]:
        if _session_id.startswith(f"{MEMORY_OWNER_ID}-s"):   # only sessions this notebook named
            _status, _, _ = call_service(AGENT_MEMORY, "DELETE", f"/session-memory/{_session_id}", ok=(204, 404))
            _gone.append(_status)
    # Owner alone, no namespace and no threshold: namespace eq never matches a memory without one.
    _, _listed, _wire = call_service(AGENT_MEMORY, "POST", "/long-term-memory/search",
                                     json_body={"text": "", "filter": {"ownerId": {"eq": MEMORY_OWNER_ID}}, "limit": 100})
    _ids = [item["id"] for item in _listed["items"] if item.get("ownerId") == MEMORY_OWNER_ID]   # only this notebook's memories
    _deleted = call_service(AGENT_MEMORY, "DELETE", "/long-term-memory", json_body={"memoryIds": _ids})[1]["deleted"] if _ids else []
    _extracted = sum(not memory_id.startswith(f"{MEMORY_OWNER_ID}-m") for memory_id in _deleted)
    mo.vstack([
        mo.callout(mo.md(f"Sessions deleted: {_gone.count(204)} of {len(_gone)} listed. Memories deleted: {len(_deleted)}, {_extracted} of them extracted from sessions. Press again to confirm 0 are left."), kind="success"),
        mo.accordion({"Owner listing as sent (credential masked)": mo.json(_wire)}),
    ])
    return


if __name__ == "__main__":
    app.run()
