import marimo

__generated_with = "0.24.2"
app = marimo.App(width="columns", app_title="Redis Iris basics")


@app.cell(column=0)
def _():
    # Common imports for Notebook work
    import asyncio
    import datetime as dt
    import json
    import textwrap
    import time
    import uuid

    # Notebook driver
    import marimo as mo

    # Notebook-local data support, typing information, or internal machinery.
    from cairn_support.formatting import log
    from cairn_support.sessions import json_request, json_session
    from cairn_support.types import ChatMessage, ChatTurn, Reply

    return (
        ChatMessage,
        ChatTurn,
        Reply,
        asyncio,
        dt,
        json,
        json_request,
        json_session,
        log,
        mo,
        textwrap,
        time,
        uuid,
    )


@app.cell(hide_code=True)
def env_check(mo):
    # Shows the settings the notebook needs and where to find each one. While any is
    # missing, it stops every cell below. os is imported here, not in the imports cell,
    # so each cell that reads the environment waits for this check.
    import os

    # 1. The settings, and where to find each one.
    _docs = "https://redis.io/docs/latest/operate"
    _settings = {
        "REDIS_URL": (
            f"[Database]({_docs}/rc/databases/connect/) > Configuration: Public endpoint and Default user password, "
            "as `redis://default:<password>@<host>:<port>`. Use `rediss://` on TLS."
        ),
        "LANGCACHE_HOST": f"[LangCache]({_docs}/iris/langcache/view-edit-cache/) > your service > Configuration > Connectivity > URL, with `https://`.",
        "LANGCACHE_CACHE_ID": "Same page, Cache ID.",
        "LANGCACHE_API_KEY": "Shown once, when you create the service. Generate key on the same page replaces it.",
        "CTX_ADMIN_KEY": "[Context Retriever > Admin keys](https://cloud.redis.io/#/context-retriever/admin-keys) > New admin key. Shown once.",
        "MEMORY_API_BASE_URL": f"[Agent Memory]({_docs}/iris/agent-memory/view-service/) > your service > Configuration > Endpoint, with `https://`.",
        "MEMORY_STORE_ID": "Same section, Store ID.",
        "MEMORY_API_KEY": "Same service > API keys > New API key. Shown once.",
        "LLM_API_KEY": "An OpenAI key for `gpt` models, an Anthropic key for `claude`, or a Google AI Studio key for `gemini`. `AI_MODEL` sets which.",
    }
    _missing = [name for name in _settings if not os.environ.get(name)]

    # 2. One table row per setting: a green check or a red cross, the name, and where to find it.
    _check = '<span style="color: var(--green-9)">&#10003;</span>'
    _cross = '<span style="color: var(--red-9)">&#10007;</span>'
    _rows = ["| Set | Variable | Where to find it |", "|---|---|:--|"]
    for _name, _where in _settings.items():
        _rows.append(f"| {_cross if _name in _missing else _check} | `{_name}` | {_where} |")

    # 3. The restart button. marimo has no restart call for a cell, so the button ends the
    # kernel process. marimo then shows a "Kernel stopped" banner with a Restart action, and
    # the new kernel reads .env again.
    def _restart_kernel(_):
        os._exit(0)

    env_restart_button = mo.ui.button(label="Restart kernel", kind="danger", on_click=_restart_kernel)

    # 4. The panel. mo.stop shows it and stops the cells below while any setting is missing.
    _panel = mo.callout(
        mo.vstack(
            [
                mo.md(r"""
    ## Settings

    The notebook reads these settings from `.env`, next to `pyproject.toml`, when the kernel starts.
    Copy `.env.example` to `.env`, fill in every value, then restart the kernel to load them.
    Most values come from the [Redis Cloud console](https://cloud.redis.io/).
    """),
                mo.md("\n".join(_rows)),
                env_restart_button,
            ]
        ),
        kind="danger" if _missing else "info",
    )
    mo.stop(bool(_missing), _panel)
    _panel
    return (os,)


@app.cell
def redis_client(os):
    import shlex

    import redis


    REDIS_CLIENT = redis.Redis.from_url(
        os.environ["REDIS_URL"],

        # Make sure we have a keepalive for inactive notebooks.
        health_check_interval=30,

        # Communicate via STR instead of BYTES (purely for notebook UX).
        decode_responses=True,
    )


    # Short id for this notebook's scratch area on a shared Redis (Search keys, Context Retriever surface name).
    CAIRN_SCOPE = "20261021"
    return CAIRN_SCOPE, REDIS_CLIENT, redis, shlex


@app.cell
def ai_model():
    from cairn_support.llm import stream_llm


    # The LLM model needed for all chat interfaces.
    # Supported models by OpenAI, Anthropic, Google (gemini)
    AI_MODEL = "gpt-4o-mini"
    return AI_MODEL, stream_llm


@app.cell
def _():
    from cairn_support.embedder import embed_texts, load_embedding_model


    # potion-base-8M from the Hugging Face cache, about 30 MB the first time
    EMBEDDING_MODEL = load_embedding_model()
    return EMBEDDING_MODEL, embed_texts


@app.cell(column=1, hide_code=True)
def rs_title(CAIRN_SCOPE, mo):
    mo.md(f"""
    # Redis Search

    As Cairn Outfitters, you store your **Help Center** articles in Redis as hashes, mapped like..

      `cairn:<scope>:help:<article-id>: {{ title, body, category, year }}`

    ```json
    {{
      "cairn:{CAIRN_SCOPE}:help:ship-times": {{
        "title": "How long does delivery take?",
        "body": "Standard orders leave our Denver warehouse..",
        "category": "shipping",
        "year": "2025",
        "embedding": "OSgjvXX2dD7aP5M9maqqv.."
      }}
    }}
    ```

    [**Redis Search**](https://redis.io/docs/latest/develop/ai/search-and-query/) is an index over those keys. You declare it once with `FT.CREATE`, pointing at a key prefix. After that, every `HSET` under that prefix is searchable automatically—no separate "index this document" step.

    Redis Search allow you to use Redis as a Document database, Vector database, Secondary index, or Search engine.

    ---

    ## We'll learn how to..

    1. Create the index.
    2. Add documents to the index.
    3. Use `FT.SEARCH` to find the relevant information.
    """)
    return


@app.cell
def rs_config(CAIRN_SCOPE):
    from cairn_support.sample_data import HELP_ARTICLES

    RS_SCOPE = f"cairn:{CAIRN_SCOPE}:"
    RS_KEY_PREFIX = f"{RS_SCOPE}help"
    RS_IDX_NAME   = f"idx:{RS_SCOPE}help"
    return HELP_ARTICLES, RS_IDX_NAME, RS_KEY_PREFIX


@app.cell(hide_code=True)
def rs_config_create(
    EMBEDDING_MODEL,
    RS_IDX_NAME,
    RS_KEY_PREFIX,
    mo,
    textwrap,
):
    rs_button_create = mo.ui.run_button(label="Run the FT.CREATE command")
    rs_button_delete = mo.ui.run_button(label="Drop the index", kind="danger")

    _key_idea = mo.callout(
        mo.md(
            textwrap.dedent(
                """
                Each field's type decides what you can ask later: `TEXT` finds words, `TAG` matches exact labels, `NUMERIC` handles ranges, and `VECTOR` finds meaning. Fields left out of the schema are still stored, but you can't search on them.
                """
            ).strip()
        ),
        kind="info",
        title="Key idea: the schema decides what you can search",
    )

    _md_create = mo.md(
        textwrap.dedent(
            f"""
            ```bash
            FT.CREATE {RS_IDX_NAME}
              ON HASH
              PREFIX 1 {RS_KEY_PREFIX}:
            SCHEMA
              title      TEXT     WEIGHT 2.0
              body       TEXT
              category   TAG
              year       NUMERIC  SORTABLE
              embedding  VECTOR   HNSW 6
                TYPE FLOAT32
                DIM {EMBEDDING_MODEL.dim}
                DISTANCE_METRIC COSINE
            ```
            """
        ).strip()
    )

    _buttons_note = mo.callout(
        mo.md(
            textwrap.dedent(
                """
                **Run the FT.CREATE command** creates the index, and the same press loads the articles in step 2. Pressing it again is harmless. **Drop the index** deletes only the index; the articles stay in Redis until they expire.
                """
            ).strip()
        ),
        kind="success",
        title="What the buttons do",
    )

    _md_options = mo.md(
        textwrap.dedent(
            """
            - `ON HASH` indexes hashes. `ON JSON` would index JSON documents instead.
            - `PREFIX 1` means one prefix follows; an index can watch several.
            - `WEIGHT 2.0` makes a word found in the title count double when full-text results are scored. It has no effect on vector search.
            - `SORTABLE` keeps `year` ready for fast sorting with `SORTBY year`.
            - `HNSW` builds a graph that finds near neighbors quickly, without comparing the question with every article. `FLAT` compares with every one: exact, but slow on large data. In `HNSW 6`, the `6` counts the items that follow: `TYPE`, `DIM` and `DISTANCE_METRIC`, each with its value.
            - `TYPE FLOAT32` stores each number in the vector as 4 bytes.
            - `DIM` must match the number of values the model outputs. The notebook fills it in from the model.
            - `COSINE` compares the direction of two vectors and ignores their length. Step 3 reports it as a distance: 1 − cosine similarity.

            To change a field's type later, drop the index and create it again: the hashes stay in Redis and are indexed again. [`FT.ALTER`](https://redis.io/docs/latest/commands/ft.alter/) can only add fields.
            """
        ).strip()
    )

    _md_syntax = mo.md(
        textwrap.dedent(
            """
            [`FT.CREATE`](https://redis.io/docs/latest/commands/ft.create/) for a [hash index](https://redis.io/docs/latest/develop/ai/search-and-query/indexing/#hash-documents):

            ```bash
            FT.CREATE <name>
              ON HASH
              PREFIX <count> <prefix> [<prefix> ...]
            SCHEMA
              <field> <type> [<options>]
              ...
            ```

            - [Schema definition](https://redis.io/docs/latest/develop/ai/search-and-query/indexing/schema-definition/)
            - [Field and type options](https://redis.io/docs/latest/develop/ai/search-and-query/indexing/field-and-type-options/)
            - [Vectors](https://redis.io/docs/latest/develop/ai/search-and-query/vectors/)
            """
        ).strip()
    )

    mo.vstack(
        [
            mo.md("### 1. Create the index"),
            _key_idea,
            _md_create,
            mo.hstack([rs_button_create, rs_button_delete], justify="start"),
            _buttons_note,
            mo.accordion(
                {
                    "More on each option": _md_options,
                    "General syntax and docs": _md_syntax,
                }
            ),
        ],
        gap=0.75,
    )
    return rs_button_create, rs_button_delete


@app.cell(hide_code=True)
def rs_config_run(
    EMBEDDING_MODEL,
    REDIS_CLIENT,
    RS_IDX_NAME,
    RS_KEY_PREFIX,
    log,
    mo,
    redis,
    rs_button_create,
    rs_button_delete,
    shlex,
    textwrap,
):
    _output = mo.md("Press a button in the cell above to get started.")

    #
    # CREATE FLOW
    #
    if rs_button_create.value:
        try:
            _stmt = f"""
            FT.CREATE {RS_IDX_NAME}
              ON HASH
              PREFIX 1 {RS_KEY_PREFIX}:
            SCHEMA
              title      TEXT     WEIGHT 2.0
              body       TEXT
              category   TAG
              year       NUMERIC  SORTABLE
              embedding  VECTOR   HNSW 6
                                TYPE FLOAT32
                                DIM {EMBEDDING_MODEL.dim}
                                DISTANCE_METRIC COSINE
            """
            _ = textwrap.dedent(_stmt)
            _ = shlex.split(_)
            _ = REDIS_CLIENT.execute_command(*_)
        except redis.exceptions.RedisError as e:
            log(e, level="warning" if "already" in str(e) else "error")
            _ = ""
        finally:
            _output = mo.md(f"**`{RS_IDX_NAME}`** was {'' if _ == 'OK' else 'already '}created.")

    #
    # DELETE FLOW
    #
    if rs_button_delete.value:
        try:
            _ = REDIS_CLIENT.execute_command("FT.DROPINDEX", RS_IDX_NAME)
        except redis.exceptions.RedisError:
            _ = ""
        finally:
            _output = mo.md(f"**`{RS_IDX_NAME}`** was {'' if _ == 'OK' else 'not '}dropped.")

    _output
    return


@app.cell(hide_code=True)
def rs_config_load_prose(
    EMBEDDING_MODEL,
    HELP_ARTICLES,
    RS_KEY_PREFIX,
    mo,
    textwrap,
):
    _article = HELP_ARTICLES[0]
    _key = f"{RS_KEY_PREFIX}:{_article['id']}"
    _body = textwrap.shorten(_article["body"], width=48, placeholder=" ...")

    _md_intro = mo.md(
        textwrap.dedent(
            f"""
            ### 2. Load the documents

            Each help article becomes one Redis [**hash**](https://redis.io/docs/latest/develop/data-types/hashes/): a key that holds named fields, like a small dictionary. Redis Search calls each hash it indexes a **document**. The notebook writes all {len(HELP_ARTICLES)} articles with `HSET`, one hash per article.
            """
        ).strip()
    )

    _key_idea = mo.callout(
        mo.md(
            textwrap.dedent(
                """
                Each key starts with the prefix from step 1, so Redis indexes the hash the moment `HSET` writes it. There's no separate "index this" call. When a key is deleted or expires, it drops out of the index too.
                """
            ).strip()
        ),
        kind="info",
        title="Key idea: writing is indexing",
    )

    _md_command = mo.md(
        textwrap.dedent(
            f"""
            > Store the first article as a hash, and delete it after an hour.

            ```bash
            HSET {_key}
              title "{_article['title']}"
              body "{_body}"
              category {_article['category']}
              year {_article['year']}
              embedding <{EMBEDDING_MODEL.dim * 4} bytes>

            EXPIRE {_key} 3600
            ```

            Reading it piece by piece:

            - `HSET <key>` creates the hash, or updates it if it already exists. The key is the prefix plus the article's id.
            - `title`, `body`, `category` and `year` are the fields the schema declared in step 1. Redis stores every hash value as a string; the index reads `year` as a number because the schema says `NUMERIC`.
            - `embedding` is the vector. The model turns the title and body into {EMBEDDING_MODEL.dim} numbers on this machine, and the notebook stores them as {EMBEDDING_MODEL.dim * 4} raw bytes, 4 per number, to match `TYPE FLOAT32` and `DIM {EMBEDDING_MODEL.dim}`.
            - `EXPIRE <key> 3600` deletes the key after an hour (3,600 seconds), so the demo cleans up after itself.
            """
        ).strip()
    )

    _watch_out = mo.callout(
        mo.md(
            textwrap.dedent(
                f"""
                If a value doesn't fit its field's type, Redis doesn't raise an error. It stores the hash but leaves it out of the index, so no search returns it. That happens with an embedding that isn't exactly {EMBEDDING_MODEL.dim * 4} bytes, for example after switching to a model with a different size, or with a `year` that isn't a number. `FT.INFO` counts these under `hash_indexing_failures`.
                """
            ).strip()
        ),
        kind="warn",
        title="Watch out: a bad value hides the whole article",
    )

    _md_syntax = mo.md(
        textwrap.dedent(
            """
            [`HSET`](https://redis.io/docs/latest/commands/hset/) and [`EXPIRE`](https://redis.io/docs/latest/commands/expire/):

            ```bash
            HSET <key> <field> <value> [<field> <value> ...]

            EXPIRE <key> <seconds>
            ```

            More on [indexing hash documents](https://redis.io/docs/latest/develop/ai/search-and-query/indexing/#hash-documents).
            """
        ).strip()
    )

    _runs_with_step_1 = mo.callout(
        mo.md(
            textwrap.dedent(
                f"""
                There's no button here. **Run the FT.CREATE command** in step 1 also writes the articles, and the line below reports how many. The notebook sends all {len(HELP_ARTICLES) * 2} commands, an `HSET` and an `EXPIRE` per article, in one [pipeline](https://redis.io/docs/latest/develop/using-commands/pipelining/): a single round trip. Pressing the button again rewrites them and restarts the hour.
                """
            ).strip()
        ),
        kind="neutral",
        title="Runs with the step 1 button",
    )

    mo.vstack(
        [
            _md_intro,
            _key_idea,
            _md_command,
            _watch_out,
            mo.accordion({"General syntax and docs": _md_syntax}),
            _runs_with_step_1,
        ],
        gap=0.75,
    )
    return


@app.cell(hide_code=True)
def rs_config_load(
    EMBEDDING_MODEL,
    HELP_ARTICLES,
    REDIS_CLIENT,
    RS_KEY_PREFIX,
    embed_texts,
    mo,
    rs_button_create,
):
    _output = mo.md("Create the index, then load help articles.")


    #
    # LOAD FLOW
    #
    if rs_button_create.value:
        with REDIS_CLIENT.pipeline(transaction=False) as _pipe:
            for article in HELP_ARTICLES:
                _key = f"{RS_KEY_PREFIX}:{article['id']}"
                _val = article.copy()

                _full_text = f"{article['title']}. {article['body']}"
                _vector = embed_texts(EMBEDDING_MODEL, [_full_text])[0].astype("float32").tobytes()
                _val["embedding"] = _vector

                _pipe.hset(_key, mapping=_val)
                _pipe.expire(_key, time=3600)

            _pipe.execute()

        _output = mo.md(
            f"Wrote **{len(HELP_ARTICLES)}** articles under `{RS_KEY_PREFIX}:` with `TTL=3600`."
        )

    _output
    return


@app.cell(hide_code=True)
def rs_3a_meaning_prose(
    EMBEDDING_MODEL,
    REDIS_CLIENT,
    RS_IDX_NAME,
    log,
    mo,
    redis,
    shlex,
    textwrap,
):
    rs_idx_state = ""
    try:
        _stmt = f"""FT.INFO {RS_IDX_NAME}"""
        _ = textwrap.dedent(_stmt)
        _ = shlex.split(_)
        rs_idx_state = REDIS_CLIENT.execute_command(*_)
    except redis.exceptions.RedisError as e:
        log(e, level="warning" if "already" in str(e) else "error")

    SEARCH_PRESETS: list[dict] = [
        {"label": "by Meaning", "query": "trail shoes feel cramped"},
        {"label": "by Exact", "query": "Trailhead"},
        {"label": "by Typo", "query": "waterproofng"},
    ]

    rs_meaning_pick = mo.ui.dropdown(
        options={f"{preset['label']}: {preset['query']}": preset["query"] for preset in SEARCH_PRESETS},
        value=f"{SEARCH_PRESETS[0]['label']}: {SEARCH_PRESETS[0]['query']}",
        label="User Query",
    )

    _md_intro = mo.md(
        textwrap.dedent(
            f"""
            ### 3a. Search by meaning with `KNN`

            In step 2, each article was stored with an **embedding**: a list of {EMBEDDING_MODEL.dim} numbers that captures what its title and body *mean*. To search by meaning, the notebook embeds the shopper's question with the same model, then asks Redis which stored embeddings are **closest** to the question's. Close embeddings mean similar meaning, even when the two texts share no words.
            """
        ).strip()
    )

    _key_idea = mo.callout(
        mo.md(
            textwrap.dedent(
                """
                Redis measures "close" with the metric the index declared in step 1, `DISTANCE_METRIC COSINE`, and returns it as **`vector_score`**, a cosine distance. **0** means the same meaning, around **1** means unrelated, and **lower is closer**.

                **KNN** stands for *k nearest neighbors*. `KNN 3` returns the **3** closest articles, and it always returns three, even when none of them is a good match.
                """
            ).strip()
        ),
        kind="info",
        title="Key idea: nearest neighbors",
    )

    _md_command = mo.md(
        textwrap.dedent(
            f"""
            > Among all articles, find the 3 closest semantic matches to the user's question.

            ```bash
            FT.SEARCH {RS_IDX_NAME} "(*)=>[KNN 3 @embedding $vector AS vector_score]"
                 RETURN  4  title category year vector_score
                 SORTBY     vector_score
                 DIALECT 2
                 PARAMS  2  vector <{EMBEDDING_MODEL.dim * 4} bytes>
            ```

            Reading it piece by piece:

            - `(*)` is the **filter**: which articles take part. `*` means all of them.
            - `=>` passes those articles to the vector search on its right.
            - `[KNN 3 @embedding $vector AS vector_score]` finds the **3** articles whose `embedding` field is nearest to `$vector`, and names each distance `vector_score`.
            - `RETURN 4 ...` sends back these **4** fields for each hit, and leaves the embedding itself behind.
            - `SORTBY vector_score` puts the closest article first.
            - `DIALECT 2` turns on the query syntax that understands `=>[KNN ...]` and `$` parameters. Vector queries need it.
            - `PARAMS 2 vector <bytes>` fills in `$vector`. The **2** counts the items that follow: the name `vector` and its value, the question's embedding as raw `FLOAT32` bytes. Bytes can't be typed into a command, so the notebook appends them as one extra argument. That's why the command shown with the results ends at `vector`.
            """
        ).strip()
    )

    _md_syntax = mo.md(
        textwrap.dedent(
            """
            [`FT.SEARCH`](https://redis.io/docs/latest/commands/ft.search/) with a `KNN` query:

            ```bash
            FT.SEARCH <index> "<filter>=>[KNN <k> @<vector field> $<param> AS <score name>]"
              RETURN <count> <field> [<field> ...]
              SORTBY <field>
              DIALECT 2
              PARAMS <nargs> <name> <value> [<name> <value> ...]
            ```

            More in the [vector search guide](https://redis.io/docs/latest/develop/ai/search-and-query/query/vector-search/).
            """
        ).strip()
    )

    _try_it = mo.callout(
        mo.md(
            textwrap.dedent(
                """
                Pick each query below and watch where its target article lands in the results.

                - **by Meaning**, *trail shoes feel cramped*: **Finding your boot size** comes first, though no article contains "cramped" or "shoes".
                - **by Typo**, *waterproofng*: **Restoring waterproofing on shells and boots** still comes first. The model splits words into pieces, and `water` and `proof` survive the typo.
                - **by Exact**, *Trailhead*: **Resoling worn boots**, the only article that names this boot, comes third. An article's embedding sums up all of its text, so one product name barely moves it. Exact names are a job for full-text search on the `TEXT` fields: `FT.SEARCH <index> "Trailhead"` returns that article alone.
                """
            ).strip()
        ),
        kind="success",
        title="Try it",
    )

    mo.vstack(
        [
            _md_intro,
            _key_idea,
            _md_command,
            mo.accordion({"General syntax and docs": _md_syntax}),
            _try_it,
            rs_meaning_pick,
        ],
        gap=0.75,
    )
    return rs_idx_state, rs_meaning_pick


@app.cell
def rs_3a_meaning(
    EMBEDDING_MODEL,
    REDIS_CLIENT,
    RS_IDX_NAME,
    convert_to_embedding,
    log,
    mo,
    rs_idx_state,
    rs_meaning_pick,
    rss_table,
    shlex,
):
    mo.stop(not rs_idx_state, mo.md("_No index yet. Run the `FT.CREATE` button above._"))

    log(f"Converting '{rs_meaning_pick.value}' to embedding", level="info")
    _embedding = convert_to_embedding(model=EMBEDDING_MODEL, content=rs_meaning_pick.value)
    log(f"Converted! ({len(_embedding)} bytes)", level="info")

    _query = "(*)=>[KNN 3 @embedding $vector AS vector_score]"
    _stmt = f"""
    FT.SEARCH {RS_IDX_NAME} "{_query}"
         RETURN  4  title category year vector_score
         SORTBY     vector_score
         DIALECT 2
         PARAMS  2  vector
    """
    _ = shlex.split(_stmt)
    _reply = REDIS_CLIENT.execute_command(*_, _embedding)

    rss_table(_reply, query=_stmt, source_text=rs_meaning_pick.value)
    return


@app.cell(hide_code=True)
def rs_3b_meaning_prose(EMBEDDING_MODEL, RS_IDX_NAME, mo, textwrap):
    rs_floor_slider = mo.ui.slider(
        start=0.0,
        stop=1.0,
        step=0.05,
        value=0.4,
        show_value=True,
        debounce=True,
        label="Similarity floor (cosine similarity)",
    )

    _md_intro = mo.md(
        textwrap.dedent(
            """
            ### 3b. Set a similarity floor with `VECTOR_RANGE`

            `KNN` always returns *k* articles, so it can never tell you that nothing matches. [`VECTOR_RANGE`](https://redis.io/docs/latest/develop/ai/search-and-query/query/vector-search/) asks a different question: instead of "the 3 closest articles", it asks for "every article within this distance". The answer can be **no articles at all**.
            """
        ).strip()
    )

    _key_idea = mo.callout(
        mo.md(
            textwrap.dedent(
                """
                An empty reply tells the app it has nothing relevant, so it can say so, ask a follow-up question, or hand off to a person, instead of presenting a weak match as the answer. LangCache and Agent Memory, in later columns, work the same way: when nothing clears their `similarityThreshold`, the reply is empty.
                """
            ).strip()
        ),
        kind="info",
        title="Key idea: an empty reply is an answer",
    )

    _md_command = mo.md(
        textwrap.dedent(
            f"""
            > Among all articles, find up to 5 semantic matches within 0.60 cosine distance of the user's question.

            ```bash
            FT.SEARCH {RS_IDX_NAME} "@embedding:[VECTOR_RANGE $radius $vector]=>{{$YIELD_DISTANCE_AS: vector_score; $EPSILON: 0.5}}"
                 RETURN  4  title category year vector_score
                 SORTBY     vector_score
                 LIMIT   0  5
                 DIALECT 2
                 PARAMS  4  radius  0.60  vector <{EMBEDDING_MODEL.dim * 4} bytes>
            ```

            What's new since 3a:

            - `@embedding:[VECTOR_RANGE $radius $vector]` keeps every article whose `embedding` is within `$radius` of `$vector`. It's a filter on its own, so it needs no `(*)=>` in front.
            - `=>{{...}}` sets options for that range. `$YIELD_DISTANCE_AS` names the distance, as `AS` did in 3a. `$EPSILON` is a search-accuracy setting, explained below.
            - `LIMIT 0 5` skips no hits and returns at most **5**.
            - `PARAMS 4` counts four items now, because there are two parameters: `radius` with its value, and `vector` with the question's bytes.
            """
        ).strip()
    )

    _floor_vs_radius = mo.callout(
        mo.md(
            textwrap.dedent(
                """
                The slider is a **similarity floor**, where higher is stricter. Redis wants a **distance radius**, where lower is stricter. For cosine, distance is 1 − similarity, so the notebook sends `radius = 1 − floor`: a floor of **0.40** becomes a radius of **0.60**.
                """
            ).strip()
        ),
        kind="neutral",
        title="Similarity floor vs. distance radius",
    )

    _md_epsilon = mo.md(
        textwrap.dedent(
            """
            The `HNSW` index from step 1 finds neighbors by hopping through a graph instead of comparing the question with every article. That's fast, but a range search can stop early and miss a match near the edge. `$EPSILON` keeps it exploring candidates up to `radius × (1 + EPSILON)` away; the default is `0.01`. It doesn't loosen the rule: only articles within `$radius` come back.

            It matters even with 36 articles. With the default, a floor of 0.40 returns nothing for *trail shoes feel cramped*, although **Finding your boot size** sits at a distance of about 0.54. With `0.5`, Redis finds it.
            """
        ).strip()
    )

    _md_syntax = mo.md(
        textwrap.dedent(
            """
            [`FT.SEARCH`](https://redis.io/docs/latest/commands/ft.search/) with a `VECTOR_RANGE` query:

            ```bash
            FT.SEARCH <index> "@<vector field>:[VECTOR_RANGE $<radius> $<vector>]=>{$YIELD_DISTANCE_AS: <score name>}"
              RETURN <count> <field> [<field> ...]
              SORTBY <field>
              LIMIT <offset> <count>
              DIALECT 2
              PARAMS <nargs> <name> <value> [<name> <value> ...]
            ```

            Other metrics, `L2` and `IP`, use the same `$radius` on a different distance scale. See [vector field options](https://redis.io/docs/latest/develop/ai/search-and-query/vectors/).
            """
        ).strip()
    )

    _try_it = mo.callout(
        mo.md(
            textwrap.dedent(
                """
                The question still comes from the dropdown in 3a.

                - With *trail shoes feel cramped*, the default floor of 0.40 lets one article through: **Finding your boot size**, at a distance of about 0.54. Raise the floor to 0.50 and the reply is empty.
                - Switch the dropdown to *Trailhead*. The reply is empty at the default floor, while `KNN 3` in 3a still returned three articles.
                """
            ).strip()
        ),
        kind="success",
        title="Try it",
    )

    mo.vstack(
        [
            _md_intro,
            _key_idea,
            _md_command,
            _floor_vs_radius,
            mo.accordion(
                {
                    "What EPSILON does": _md_epsilon,
                    "General syntax and docs": _md_syntax,
                }
            ),
            _try_it,
            rs_floor_slider,
        ],
        gap=0.75,
    )
    return (rs_floor_slider,)


@app.cell
def rs_3b_meaning(
    EMBEDDING_MODEL,
    REDIS_CLIENT,
    RS_IDX_NAME,
    convert_to_embedding,
    log,
    mo,
    rs_floor_slider,
    rs_idx_state,
    rs_meaning_pick,
    rss_table,
    shlex,
):
    mo.stop(not rs_idx_state, mo.md("_No index yet. Run the `FT.CREATE` button above._"))

    log(f"Converting '{rs_meaning_pick.value}' to embedding", level="info")
    _embedding = convert_to_embedding(model=EMBEDDING_MODEL, content=rs_meaning_pick.value)
    log(f"Converted! ({len(_embedding)} bytes)", level="info")

    _radii = round(1 - rs_floor_slider.value, 2)
    _query = "@embedding:[VECTOR_RANGE $radius $vector]=>{$YIELD_DISTANCE_AS: vector_score; $EPSILON: 0.5}"
    _stmt = f"""
    FT.SEARCH {RS_IDX_NAME} "{_query}"
         RETURN  4  title category year vector_score
         SORTBY     vector_score
         LIMIT   0  5
         DIALECT 2
         PARAMS  4  radius  {_radii}  vector
    """
    _ = shlex.split(_stmt)
    _reply = REDIS_CLIENT.execute_command(*_, _embedding)

    rss_table(
        _reply,
        query=_stmt,
        source_text=rs_meaning_pick.value,
        params={
            "Similarity floor": rs_floor_slider.value,
            "$radius": _radii,
        },
    )
    return


@app.cell(hide_code=True)
def rs_3c_meaning_prose(EMBEDDING_MODEL, RS_IDX_NAME, mo, textwrap):
    rss_year_slider = mo.ui.slider(
        start=2022,
        stop=2026,
        step=1,
        value=2022,
        show_value=True,
        debounce=True,
        label="Year floor",
    )

    _md_intro = mo.md(
        textwrap.dedent(
            """
            ### 3c. Filter inside the vector search, not after it

            Real searches mix meaning with plain rules: only recent articles, only one category, only one customer's data. Step 1 declared `year` as a `NUMERIC` field, so Redis can apply a year rule itself. This step runs the same search two ways and compares **where** the rule is applied.
            """
        ).strip()
    )

    _scenario = mo.callout(
        mo.md(
            textwrap.dedent(
                """
                - **Question:** *Where is my parcel?*, embedded into `$vector`.
                - **Goal:** the **5** articles closest in meaning, with `KNN 5`.
                - **Rule:** only articles from the **year floor** on. Set it with the slider below.
                """
            ).strip()
        ),
        kind="neutral",
        title="The same on both sides",
    )

    _md_command = mo.md(
        textwrap.dedent(
            f"""
            **Post-filter**, the left results:

            > Among all articles, find the 5 closest semantic matches to the user's question.

            ```bash
            FT.SEARCH {RS_IDX_NAME} "*=>[KNN 5 @embedding $vector AS vector_score]"
                 RETURN  4  title category year vector_score
                 SORTBY     vector_score
                 DIALECT 2
                 PARAMS  2  vector <{EMBEDDING_MODEL.dim * 4} bytes>
            ```

            The notebook then drops any article older than the year floor.

            **Pre-filter**, the right results:

            > Among articles from 2022 and later, find the 5 closest semantic matches to the user's question.

            ```bash
            FT.SEARCH {RS_IDX_NAME} "(@year:[2022 +inf])=>[KNN 5 @embedding $vector AS vector_score]"
                 RETURN  4  title category year vector_score
                 SORTBY     vector_score
                 DIALECT 2
                 PARAMS  2  vector <{EMBEDDING_MODEL.dim * 4} bytes>
            ```

            - `@year:[2022 +inf]` is a [numeric range](https://redis.io/docs/latest/develop/ai/search-and-query/query/range/) on the `year` field: 2022 and later, with no upper limit. The searches below put the slider's floor in place of 2022.
            - `*` without parentheses means the same as `(*)` in 3a. The rest works as in 3a, with `KNN 5` in place of `KNN 3`.
            """
        ).strip()
    )

    _key_idea = mo.callout(
        mo.md(
            textwrap.dedent(
                """
                Whatever sits left of `=>` decides which articles may compete, and the KNN on the right ranks only those. Filter **after** the KNN and you can end up with fewer than 5 results: older articles took slots that newer ones never got a chance at. Filter **before** it and Redis returns 5 whenever at least 5 articles pass the rule.
                """
            ).strip()
        ),
        kind="info",
        title="Key idea: filter first, then rank",
    )

    _md_more_filters = mo.md(
        textwrap.dedent(
            """
            - `@year:[2024 2025]` is a closed range: 2024 through 2025.
            - `@category:{shipping}` matches a `TAG`. `@category:{shipping | returns}` matches either one.
            - `@title:boots` is a full-text match on a `TEXT` field.
            - `(@category:{shipping} @year:[2025 +inf])` needs both, because a space means AND.

            A rule can only use fields the index declares in step 1. See [combined queries](https://redis.io/docs/latest/develop/ai/search-and-query/query/combined/).
            """
        ).strip()
    )

    _md_agent = mo.md(
        textwrap.dedent(
            """
            Agent Memory sends a `filter` on `ownerId` with every search, and it's applied inside the vector search, like the right-hand query. Filtering afterward would let other customers' memories fill the *k* slots and leave this customer with fewer results, or none.
            """
        ).strip()
    )

    _try_it = mo.callout(
        mo.md(
            textwrap.dedent(
                """
                Move the year floor and compare the two sides.

                - At **2022** and **2023**, both sides show the same five articles.
                - At **2025**, the post-filter keeps 3 of its 5. The pre-filter still returns five, adding **Free shipping threshold** and **Backordered and out of stock items**.
                - At **2026**, the post-filter keeps one. The pre-filter still fills all five slots, including weak matches like **Choosing a pack size by torso length**, because KNN returns *k* whenever enough articles pass.
                """
            ).strip()
        ),
        kind="success",
        title="Try it",
    )

    mo.vstack(
        [
            _md_intro,
            _scenario,
            _md_command,
            _key_idea,
            mo.accordion(
                {
                    "Other rules you can put before =>": _md_more_filters,
                    "Why it matters for Agent Memory": _md_agent,
                }
            ),
            _try_it,
            rss_year_slider,
        ],
        gap=0.75,
    )
    return (rss_year_slider,)


@app.cell
def rs_3c_meaning(
    EMBEDDING_MODEL,
    REDIS_CLIENT,
    RS_IDX_NAME,
    convert_to_embedding,
    mo,
    rs_idx_state,
    rss_table,
    rss_year_slider,
    shlex,
):
    mo.stop(not rs_idx_state, mo.md("_No index yet. Run the `FT.CREATE` button above._"))

    _year = rss_year_slider.value
    _vector = convert_to_embedding(model=EMBEDDING_MODEL, content="Where is my parcel?")

    _unfiltered = f"""
    FT.SEARCH {RS_IDX_NAME}
         "*=>[KNN 5 @embedding $vector AS vector_score]"
         RETURN  4  title category year vector_score
         SORTBY     vector_score
         DIALECT 2
         PARAMS  2  vector
    """
    _filtered = f"""
    FT.SEARCH {RS_IDX_NAME} 
         "(@year:[{_year} +inf])=>[KNN 5 @embedding $vector AS vector_score]"
         RETURN  4  title category year vector_score
         SORTBY     vector_score
         DIALECT 2
         PARAMS  2  vector
    """
    _top5 = REDIS_CLIENT.execute_command(*shlex.split(_unfiltered), _vector)
    _knn5 = REDIS_CLIENT.execute_command(*shlex.split(_filtered), _vector)

    mo.hstack(
        [
            mo.vstack(
                [
                    mo.md("**Post-filter** — KNN on all articles, then the app drops older years"),
                    rss_table(
                        _top5,
                        query=_unfiltered,
                        source_text="Where is my parcel?",
                        params={"Year floor (app filter)": _year},
                        keep=lambda row: int(row["year"]) >= _year,
                    ),
                ],
                gap=0.5,
            ),
            mo.vstack(
                [
                    mo.md("**Pre-filter** — `@year` range before `=>`, then KNN 5"),
                    rss_table(
                        _knn5,
                        query=_filtered,
                        source_text="Where is my parcel?",
                        params={"@year floor": _year},
                    ),
                ],
                gap=0.5,
            ),
        ],
        widths="equal",
        gap=1.5,
    )
    return


@app.cell(hide_code=True)
def rs_build_md(mo, textwrap):
    _hidden = mo.callout(
        mo.md(
            textwrap.dedent(
                """
                Two helper functions run behind the steps above: `convert_to_embedding` turns each question into the bytes Redis searches, and `rss_table` draws each result. Their cells are at the bottom of this column with the code collapsed. Expand them in `marimo edit`, or pick **Show code** from the app menu in `marimo run`.
                """
            ).strip()
        ),
        kind="neutral",
        title="Hidden: the helper code",
    )

    _md_commands = mo.md(
        textwrap.dedent(
            """
            Each step's code is in the cell right under its explanation. `FT.CREATE` and every `FT.SEARCH` there are written as text, the way `redis-cli` takes them. `shlex.split` turns that text into the arguments `REDIS_CLIENT.execute_command` sends. A search also needs the question's embedding as raw bytes: `convert_to_embedding`, in the `rs_connect` cell, makes them by running the model on this machine.
            """
        ).strip()
    )

    _md_results = mo.md(
        textwrap.dedent(
            """
            `rss_table`, in the `rs_render` cell, turns each `FT.SEARCH` reply into what you see under each search: the inputs, the command, a hit count, a table and the raw reply. It reads the reply with helpers from `cairn_support.redis_search_replies`.
            """
        ).strip()
    )

    mo.vstack(
        [
            _hidden,
            mo.accordion(
                {
                    "How commands reach Redis": _md_commands,
                    "How results are drawn": _md_results,
                }
            ),
        ],
        gap=0.75,
    )
    return


@app.cell(hide_code=True)
def rs_connect(embed_texts):
    def convert_to_embedding(model, content: str) -> bytes:
        """Convert text to an embedding vector."""
        return embed_texts(model, [content])[0].astype("float32").tobytes()

    return (convert_to_embedding,)


@app.cell(hide_code=True)
def rs_render(mo, textwrap):
    from cairn_support.redis_search_replies import (
        format_ft_search_reply,
        ft_search_inputs_markdown,
        ft_search_markdown_table,
        ft_search_result_summary,
        ft_search_rows,
    )


    def rss_table(
        reply: list | dict,
        *,
        query: str,
        source_text: str,
        params: dict | None = None,
        keep=None,
    ) -> mo.Html:
        """An FT.SEARCH reply as the command, a count, a table, and the raw reply. RESP3 is a map with ``total_results`` and ``results``; RESP2 is a flat list.
        `keep` is a filter the app applies to the rows after Redis answers."""
        total, rows = ft_search_rows(reply)
        kept = [row for row in rows if keep(row)] if callable(keep) else rows
        summary = ft_search_result_summary(total, len(rows), len(kept) if callable(keep) else None)
        table = ft_search_markdown_table(kept) if kept else None
        command = textwrap.dedent(query).strip()
        return mo.vstack(
            [
                mo.md(ft_search_inputs_markdown(source_text, params)),
                mo.md(f"```bash\n{command}\n```"),
                mo.md(f"**{summary}**"),
                mo.md(table if table else "_No article._"),
                mo.accordion(
                    {"The reply as Redis sent it": mo.md("```text\n" + format_ft_search_reply(reply) + "\n```")}
                ),
            ],
            gap=0.5,
        )

    return (rss_table,)


@app.cell(column=2, hide_code=True)
def lc_title(mo):
    mo.md(r"""
    # Redis LangCache

    Customers rarely ask the same question the same way twice, and an exact-match cache misses every rewording.
    Redis LangCache embeds each prompt itself and matches it by meaning, so a reworded question can get a stored answer without an LLM call.
    Select Seed to store five Cairn Outfitters gear-care answers, then ask Cairn support and set the threshold, strategy, and TTL to see three principles:

    1. **Your app searches before the LLM and stores after it.** `POST /entries/search` answers 200 with `data: []` on a miss, so the app calls the LLM and sends `POST /entries`, which answers 201 with an `entryId`. Both carry one Bearer token, and the pane beside the chat shows each turn's search, its reply, and the store after a miss.
    2. **The threshold trades wrong answers for misses.** A request's `similarityThreshold`, the same name the Agent Memory column tunes, is the floor on each entry's `similarity` and overrides the cache's default, 0.85 at creation. Move the slider and seven questions re-sort into hit, wrong answer, and miss, while `exact` in `searchStrategies` hits the same words regardless of case, at `similarity` 1.
    3. **A cached answer lives until its time to live (TTL) runs out or your app deletes it.** A new cache's default TTL is no expiration, so the app sends `ttlMillis` with every `POST /entries` to cap how stale an answer gets. Pick 10 seconds and a new question misses, hits at once when repeated, then misses again, so Clean up's `DELETE /entries/{entryId}` reads 404 after its TTL.

    Embeddings: LangCache embeds inside the service, so this column sends text and never a vector. You pick the embedding provider and model in the console when you create the service: Redis's own provider, OpenAI with your key, or your own endpoint that speaks the OpenAI embeddings API.
    `LLM_API_KEY` is the chat's key and plays no part in that choice.

    Settings: `LANGCACHE_HOST` (with `https://`), `LANGCACHE_CACHE_ID`, and `LANGCACHE_API_KEY`, shown once at creation, plus `LLM_API_KEY`, which the chat needs on a miss.
    The first two, the base URL and the `{cacheId}` in every path, are on the service's Connectivity page, and the third is the Bearer token.
    Docs: [LangCache](https://redis.io/docs/latest/develop/ai/context-engine/langcache/),
    [concepts](https://redis.io/docs/latest/develop/ai/context-engine/langcache/concepts/),
    [create a service](https://redis.io/docs/latest/operate/iris/langcache/create-service/),
    [API and SDK examples](https://redis.io/docs/latest/develop/ai/context-engine/langcache/api-examples/), and
    [API reference](https://redis.io/docs/latest/develop/ai/context-engine/langcache/api-reference/).

    Each line reads source, target, and what passes between them:

    ```text
    App       -> LangCache        POST /entries/search: prompt, similarityThreshold, searchStrategies, maxResults 5
    LangCache -> embedding model  the prompt, embedded by the model chosen at creation
    LangCache -> your Redis       a vector search over the cache's index in the database you selected
    LangCache -> App              200, data: [entries at or above the threshold, best first], or data: []
    App       -> LLM              the question alone, only when data is empty
    App       -> LangCache        POST /entries: prompt, response, ttlMillis, then 201 with an entryId
    ```
    """)
    return


@app.cell(hide_code=True)
def lc_store_button(lcs_session, mo):
    _ = lcs_session  # the buttons appear once the session exists, so a press never runs ahead of the settings check
    lcs_seed_button = mo.ui.run_button(label="Seed")
    lcs_cleanup_button = mo.ui.run_button(label="Clean up", kind="danger")

    mo.vstack(
        [
            mo.md(r"""
    ## Seed five gear-care answers and keep their ids

    Seed stores five answers with `POST /entries`, each with `ttlMillis` 900000, and LangCache answers 201 with an `entryId` it derives from the prompt.
    Entries cannot be listed, so the table below is this kernel's ledger, and there is no Refresh.
    A second Seed gets the same five ids and replaces each response and TTL. Each answer the chat stores on a miss joins the ledger with the TTL you picked.
    Clean up sends `DELETE /entries/{entryId}` for every id in the ledger. 204 is deleted, and 404 means the TTL ran out first.
    """),
            mo.hstack([lcs_seed_button, lcs_cleanup_button], justify="start"),
        ]
    )
    return lcs_cleanup_button, lcs_seed_button


@app.cell(hide_code=True)
def lc_store(
    GEAR_CARE_ANSWERS,
    lcs_chatbot,
    lcs_cleanup_button,
    lcs_entries,
    lcs_entry,
    lcs_forget,
    lcs_seed,
    lcs_seed_button,
    lcs_store_table,
    lcs_wire,
    mo,
):
    _note = ""
    if lcs_seed_button.value:
        _results = lcs_seed()
        _failed = [f"{r['key']}: {r['status']} {r['type']}" for r in _results if r["status"] != 201]
        _note = (
            f"Stored {len(_results) - len(_failed)} of {len(_results)} answers, each 201 with an `entryId`, for 900000 ms."
            + (f" Failed: {', '.join(_failed)}." if _failed else "")
        )
    if lcs_cleanup_button.value:
        _deleted, _gone, _failed = lcs_forget()
        _note = (
            f"Clean up sent `DELETE` for each id in the ledger. {len(_deleted)} answered 204, and {len(_gone)} answered 404 because the TTL had run out."
            + (f" Failed: {', '.join(_failed)}." if _failed else "")
        )
    _ = lcs_chatbot.value  # a miss stores an answer, so each turn lists the ledger again

    lcs_store = dict(lcs_entries)  # a snapshot of the ledger, so the sweep and the workbench rerun after Seed, Clean up, and each turn

    _sends = []  # with an empty ledger, show the request Seed would send for the first answer
    if not lcs_store:
        _first = GEAR_CARE_ANSWERS[0]
        _sends = [
            mo.md("**What Seed sends**, for the first answer:"),
            lcs_wire("POST", "/entries", lcs_entry(_first["prompt"], _first["response"], 900_000)),
        ]

    mo.vstack(([mo.md(_note)] if _note else []) + [lcs_store_table(lcs_store)] + _sends)
    return (lcs_store,)


@app.cell(hide_code=True)
def lc_controls(mo):
    # No control sits upstream of this chat. A LangCache turn has no session, and the model sees the question alone,
    # so a changed threshold, strategy, or TTL applies to the next turn and the history stays, which the steps below
    # rely on. All three controls live in lc_chat and are read at call time through lcs_settings().
    mo.md(r"""
    ## Ask Cairn support and watch each search

    Type `/` in the chat box to pick a starter message. Send the goose-down question first. It says launder where the stored prompt says wash, yet the down-bag answer is served. The pane shows the search and its reply.
    Send the synthetic bag question next. The same answer comes back at a higher similarity, and it is wrong for a synthetic bag, so the pane says wrong answer.
    Move `similarityThreshold` to 0.96, and the table loses the wrong answer and every hit with it.

    Set `ttlMillis` to 10 seconds and send the synthetic bag question again. At 0.96 it misses, the model answers, and the app stores the answer for 10000 ms.
    Send it again right away, and the chat's own answer is served at similarity 1. Wait fifteen seconds and send it a third time, and the miss returns.

    Set `searchStrategies` to `exact` and send the all-caps copy of the down-bag prompt, which `semantic` missed. It hits at similarity 1. Send the goose-down question again, and `exact` misses it.
    Set the strategy back to `semantic`, the slider to 0.85, and the TTL to 1 minute, then send the snapped tent pole question. It misses and is stored.
    Send the broken tent pole rewording, and the chat's own answer is served at about 0.94.
    Select Clean up to delete by id. The 10 second answers have expired, so their `DELETE` answers 404. The seeds answer 204, and so does the tent pole answer if its minute has not passed.
    """)
    return


@app.cell(hide_code=True)
def lc_workbench(
    lcs_chatbot,
    lcs_get_strategy,
    lcs_get_threshold,
    lcs_json,
    lcs_prompts,
    lcs_request,
    lcs_strategy,
    lcs_threshold,
    lcs_threshold_table,
    lcs_ttl,
    lcs_turn_scores,
    lcs_turns,
    lcs_wire,
    mo,
):
    _t = round(lcs_get_threshold(), 2)
    _strategy = lcs_get_strategy()
    _ = lcs_chatbot.value  # a turn appends to lcs_turns, and this rerun shows it
    _turn = lcs_turns[-1] if lcs_turns else None

    if _turn is None:
        _data = [
            mo.md("**Next turn**, the first starter message until you send one. The controls are read when you send."),
            mo.md("**Search request**, from `lcs_request`"),
            lcs_wire("POST", "/entries/search", lcs_request(lcs_prompts[0], _t, _strategy)),
        ]
    else:
        _decision = _turn["decision"]
        if _decision in ("hit", "wrong answer"):
            _line = f"**{_decision}**: similarity {_turn['similarity']:.3f} in {_turn['ms']} ms, served from the cache with no model call."
        elif _decision == "miss" and "entry_id" in _turn:
            _line = f"**miss**: `data` was empty, so the model answered and the app stored it for {_turn['ttl_ms']} ms, entryId `{_turn['entry_id']}`."
        elif _decision == "another app's entry":
            _line = f"**another app's entry**: the best match, at similarity {_turn['similarity']:.3f}, is not from this kernel, so nothing was served or stored."
        elif _decision == "error":
            _line = f"**error**: LangCache answered {_turn['status']}, so nothing was served or stored."
        else:
            _line = "**miss, nothing stored**: `data` was empty, and the model call failed or the store did not answer 201, so nothing was stored."
        _data = [
            mo.md(f"**Last turn**, {_turn['question']}"),
            mo.md(_line),
            mo.md("**Search request**, from `lcs_request`"),
            lcs_wire("POST", "/entries/search", _turn["search"]),
            mo.md(f"**Reply**, {_turn['status']}, with other apps' entries hidden"),
            lcs_json(_turn["reply"]),
        ]
        if "store" in _turn:
            _data += [
                mo.md(f"**Store request**, from `lcs_entry`, answered {_turn['store_status']} in {_turn['store_ms']} ms"),
                lcs_wire("POST", "/entries", _turn["store"]),
            ]
    _data += [
        mo.md("---"),
        mo.md("Moving the slider redraws this pane without a new request. The app sends one search per turn and one store after a miss. The notebook adds seven searches behind the table after each turn while the ledger holds entries."),
        mo.md(
            "Each row is a question's best entry from this kernel at 0.50. "
            "The seven questions are searched with `maxResults` 5 after Seed and after each turn, while the ledger holds entries. "
            "The slider is the cut LangCache applies when you send `similarityThreshold`: an entry at or above it is served, "
            "a hit when it is the question's target, a wrong answer when not."
        ),
        lcs_threshold,
        lcs_threshold_table(lcs_turn_scores, _t),
    ]

    mo.hstack(
        [
            mo.vstack([mo.hstack([lcs_strategy, lcs_ttl], justify="start", gap=2), lcs_chatbot]),
            mo.vstack(_data),
        ],
        widths=[5, 6],
        gap=1.5,
        align="start",
    )
    return


@app.cell(hide_code=True)
def lc_build_md(mo):
    mo.md(r"""
    ## The LangCache code: session, request, store, turn

    One cell per step, with the code visible in `marimo edit`. In `marimo run`, pick **Show code** from the app menu.
    Every `lcs_call` goes to LangCache, and the rest is the app. Seed and Clean up call the store functions, and each chat turn calls `lcs_turn`.
    """)
    return


@app.cell
def lc_connect(Reply, json_request, json_session, os):
    # Client. Three settings from .env, one session that carries the Bearer token, and one function for every LangCache request below.
    from cairn_support.sample_data import GEAR_CARE_ANSWERS


    _host = os.environ["LANGCACHE_HOST"].strip().rstrip("/")  # the Connectivity page's URL, with or without https://
    lcs_session = json_session(
        f"{_host if '://' in _host else 'https://' + _host}/v1/caches/{os.environ['LANGCACHE_CACHE_ID'].strip()}",
        {"Authorization": f"Bearer {os.environ['LANGCACHE_API_KEY']}"},  # the one credential, read here and bound to nothing
    )


    def lcs_call(method: str, path: str, body: dict | None = None) -> Reply:
        """One request to the cache, with no retry: the HTTP status, the reply as a dict (None on a 204), and the milliseconds it took."""
        return json_request(lcs_session, method, path, body)

    return GEAR_CARE_ANSWERS, lcs_call, lcs_session


@app.cell
def lc_request_fns():
    # Request. The two bodies the app sends.
    def lcs_request(prompt: str, threshold: float, strategy: str) -> dict:
        """The search body. `similarityThreshold` overrides the cache's default for this request (principle 2), `searchStrategies` is `semantic` or `exact`, and `maxResults` 5 returns the entries under the best one too."""
        return {"prompt": prompt, "similarityThreshold": round(threshold, 2), "searchStrategies": [strategy], "maxResults": 5}


    def lcs_entry(prompt: str, response: str, ttl_ms: int) -> dict:
        """The store body. `ttlMillis` goes with every entry, because a new cache's default TTL is no expiration (principle 3)."""
        return {"prompt": prompt, "response": response, "ttlMillis": ttl_ms}


    return lcs_entry, lcs_request


@app.cell
def lc_store_fns(GEAR_CARE_ANSWERS, lcs_call, lcs_entry, time):
    # Store. The ledger of what this kernel stored, and the seed and delete functions over it.
    lcs_entries = {}  # entryId -> prompt, key, ttlMillis, stored_at, source. Entries cannot be listed, so this is what Clean up walks.


    def lcs_remember(entry_id: str, prompt: str, key: str, ttl_ms: int, source: str) -> None:
        """Record a stored entry under the id LangCache returned. The ledger is the app's, because the API has no list call."""
        lcs_entries[entry_id] = {"prompt": prompt, "key": key, "ttlMillis": ttl_ms, "stored_at": time.time(), "source": source}


    def lcs_seed() -> list[dict]:
        """Store the five gear-care answers with `POST /entries`, each with ttlMillis 900000 (principle 3), and keep every entryId.
        LangCache derives the id from the prompt, so a second Seed gets the same ids and replaces each response and TTL. Returns one row per answer."""
        results = []
        for answer in GEAR_CARE_ANSWERS:
            status, payload, _ = lcs_call("POST", "/entries", lcs_entry(answer["prompt"], answer["response"], 900_000))
            if status == 201:
                lcs_remember(payload["entryId"], answer["prompt"], answer["key"], 900_000, "seed")
            results.append({"key": answer["key"], "status": status, "entryId": (payload or {}).get("entryId"), "type": (payload or {}).get("type", "")})
        return results


    def lcs_forget() -> tuple[list[str], list[str], list[str]]:
        """Delete every entry in the ledger by id with `DELETE /entries/{entryId}`. 204 is deleted, 404 is already gone (its TTL ran out, or an earlier Clean up),
        and anything else is reported and kept. Deleting by id touches nothing another app stored."""
        deleted, gone, failed = [], [], []
        for entry_id in list(lcs_entries):
            status, payload, _ = lcs_call("DELETE", f"/entries/{entry_id}")
            if status == 204:
                deleted.append(entry_id)
            elif status == 404 and "cache/not-found" not in (payload or {}).get("type", ""):
                gone.append(entry_id)
            else:
                failed.append(f"{entry_id}: {status} {(payload or {}).get('type', '')}")
                continue
            del lcs_entries[entry_id]
        return deleted, gone, failed

    return lcs_entries, lcs_forget, lcs_remember, lcs_seed


@app.cell
def lc_turn_fn(
    AI_MODEL,
    ChatMessage,
    ChatTurn,
    lcs_call,
    lcs_entries,
    lcs_entry,
    lcs_remember,
    lcs_request,
    lcs_settings,
    stream_llm,
):
    # Turn. One chat turn: the search, then a served hit, or the model and the store on a miss.
    lcs_system = "You are the Cairn Outfitters support agent. Answer the customer in two or three sentences."


    async def lcs_turn(messages: list[ChatMessage]) -> ChatTurn:
        """One cache-aside turn: search LangCache with the question, serve a hit, or on a miss call the model and store its answer (principle 1)."""
        question = str(messages[-1].content)
        threshold, strategy, ttl_ms = lcs_settings()  # the three controls, read when you send

        # Search before the LLM. A miss is 200 with data: [], never a 404, and the reply is best first.
        status, payload, _ = lcs_call("POST", "/entries/search", lcs_request(question, threshold, strategy))
        if status != 200:
            yield f"LangCache answered {status} ({(payload or {}).get('type', 'no body')}). Nothing was served or stored."
            return
        data = payload["data"]

        if data and data[0]["id"] in lcs_entries:
            # A hit: the best entry is one this kernel stored, so its response is the answer and the model is never called.
            yield data[0]["response"]
            return

        if data:
            # The best entry belongs to another app on this cache. A real app would serve it. The notebook shows its similarity only and stores nothing.
            yield f"LangCache's best match, at similarity {data[0]['similarity']:.3f}, is an entry this notebook did not store, so it is not shown. Nothing was stored."
            return

        # A miss: the model answers the question alone, because a cached answer is keyed by the prompt and must fit anyone who asks it.
        answer = ""
        async for text in stream_llm(lcs_system, [{"role": "user", "content": question}], AI_MODEL):
            answer += text
            yield text

        # Store after the LLM, with the TTL you picked. LangCache answers 201 with the entryId that Clean up deletes.
        status, stored, _ = lcs_call("POST", "/entries", lcs_entry(question, answer, ttl_ms))
        if status == 201:
            lcs_remember(stored["entryId"], question, question, ttl_ms, "chat")

    return (lcs_turn,)


@app.cell(hide_code=True)
def lc_machinery_md(mo):
    mo.md(r"""
    ## Notebook plumbing: control state, chat object, threshold sweep, renderers

    Four hidden cells follow: the control state, the chat object with its three controls, the seven searches behind the threshold table, and the renderers for requests and tables.
    Read `lc_settings`, `lc_chat`, `lc_turn_sweep`, and `lc_render` in `iris_basics.py`, or expand them in `marimo edit`. `lc_chat` records the requests each turn sends, and `lcs_record` in `lc_render` files them for the pane.
    """)
    return


@app.cell(hide_code=True)
def lc_settings(mo):
    # Control state. The three controls live in lc_chat, so a turn cannot read their .value from inside the chat cell.
    # A cell that references these getters reruns on every change, so the turn reads them through lcs_settings() instead.
    # lc_chat reads the getters for the controls' start values, and marimo exempts the cell that owns a control.
    lcs_get_threshold, lcs_set_threshold = mo.state(0.85)
    lcs_get_strategy, lcs_set_strategy = mo.state("semantic")
    lcs_get_ttl, lcs_set_ttl = mo.state("1 minute")
    lcs_ttls = {"10 seconds": 10_000, "1 minute": 60_000, "15 minutes": 900_000}  # the dropdown's labels and the ttlMillis each sends


    def lcs_settings() -> tuple[float, str, int]:
        """The controls' current values, read at call time: the threshold rounded to two decimals, the strategy, and ttlMillis. A cell that calls this does not rerun when a control changes."""
        return round(lcs_get_threshold(), 2), lcs_get_strategy(), lcs_ttls[lcs_get_ttl()]

    return (
        lcs_get_strategy,
        lcs_get_threshold,
        lcs_get_ttl,
        lcs_set_strategy,
        lcs_set_threshold,
        lcs_set_ttl,
        lcs_settings,
        lcs_ttls,
    )


@app.cell(hide_code=True)
def lc_chat(
    CACHE_ASIDE_SCRIPT,
    THRESHOLD_PROBES,
    lcs_get_strategy,
    lcs_get_threshold,
    lcs_get_ttl,
    lcs_note,
    lcs_record,
    lcs_set_strategy,
    lcs_set_threshold,
    lcs_set_ttl,
    lcs_ttls,
    lcs_turn,
    mo,
):
    from cairn_support.sessions import recording  # collects the requests a block sends, for the pane

    # debounce=True: the setter fires when the slider is released, not on every step. The radio and the dropdown set state the same way.
    lcs_threshold = mo.ui.slider(
        start=0.5, stop=1.0, step=0.01, value=lcs_get_threshold(), show_value=True, debounce=True,
        label="similarityThreshold", on_change=lcs_set_threshold,
    )
    lcs_strategy = mo.ui.radio(options=["semantic", "exact"], value=lcs_get_strategy(), inline=True, label="searchStrategies", on_change=lcs_set_strategy)
    lcs_ttl = mo.ui.dropdown(options=list(lcs_ttls), value=lcs_get_ttl(), label="ttlMillis for an answer stored on a miss", on_change=lcs_set_ttl)
    lcs_prompts = [probe["text"] for probe in THRESHOLD_PROBES] + [
        CACHE_ASIDE_SCRIPT["prefix"] + CACHE_ASIDE_SCRIPT["ask"],
        CACHE_ASIDE_SCRIPT["prefix"] + CACHE_ASIDE_SCRIPT["again"],
    ]


    async def _turn(messages):
        """lcs_turn, with the requests it sends recorded for the pane and a line on what the cache did. mo.ui.chat keeps a turn,
        and the pane redraws, only when it ends with text, so an error ends the reply with a line too."""
        error = None
        with recording() as wire:
            try:
                async for text in lcs_turn(messages):
                    yield text
            except Exception as e:
                error = e
        yield lcs_note(lcs_record(str(messages[-1].content), wire), error)

    # Displayed beside the data in lc_workbench. lcs_turn is the Iris turn above.
    lcs_chatbot = mo.ui.chat(_turn, prompts=lcs_prompts, max_height=520)
    return lcs_chatbot, lcs_prompts, lcs_strategy, lcs_threshold, lcs_ttl


@app.cell(hide_code=True)
def lc_turn_sweep(THRESHOLD_PROBES, lcs_call, lcs_request, lcs_store):
    def lcs_sweep(store: dict[str, dict]) -> dict[str, dict]:
        """For each of the seven probes, one semantic search at 0.50 with maxResults 5. Keeps the best entry this kernel stored, its similarity,
        and how many other apps' entries came back, so the slider can re-cut the scores with no new request."""
        scores = {}
        for probe in THRESHOLD_PROBES:
            status, payload, _ = lcs_call("POST", "/entries/search", lcs_request(probe["text"], 0.5, "semantic"))
            data = payload["data"] if status == 200 else []
            best = next((item for item in data if item["id"] in store), None)
            scores[probe["text"]] = {
                "kind": probe["kind"],
                "entry": store[best["id"]] if best else None,
                "similarity": best["similarity"] if best else None,
                "hidden": sum(item["id"] not in store for item in data),
            }
        return scores


    # Seven searches after Seed, Clean up, and each turn (a miss stores an answer that can become a probe's best entry).
    # A slider move reruns the workbench, not this cell. With an empty ledger nothing is searched.
    lcs_turn_scores = lcs_sweep(lcs_store) if lcs_store else {}
    return (lcs_turn_scores,)


@app.cell(hide_code=True)
def lc_render(dt, json, lcs_entries, mo, time):
    # Turn log and labels. lcs_record files each turn for the pane from the requests lc_chat recorded, lcs_note adds a line on what the
    # cache did, lcs_verdict labels a served entry, and lcs_shown hides other apps' entries. The renderers for requests and tables follow.
    from cairn_support.sample_data import CACHE_ASIDE_SCRIPT, THRESHOLD_PROBES

    lcs_turns = []  # one record per turn, newest last, so the workbench can show the last turn's requests and reply
    lcs_targets = {probe["text"]: probe["target"] for probe in THRESHOLD_PROBES}  # the seed each probe is after, or None when no stored answer fits
    lcs_targets[CACHE_ASIDE_SCRIPT["prefix"] + CACHE_ASIDE_SCRIPT["again"]] = CACHE_ASIDE_SCRIPT["prefix"] + CACHE_ASIDE_SCRIPT["ask"]  # the rewording is after the answer the chat stored for the first question


    def lcs_verdict(record: dict, question: str) -> str:
        """`hit`, or `wrong answer` when a labeled question is served an entry it is not after (principle 2). A seed's key is its name, such as `bag`, a chat answer's key is the question it was stored for, and an unlabeled question trusts the cache."""
        if question not in lcs_targets:
            return "hit"
        return "hit" if record["key"] in (lcs_targets[question], question) else "wrong answer"


    def lcs_shown(data: list[dict]) -> list[dict]:
        """The reply's entries as the pane shows them. An entry this kernel did not store keeps only its similarity, because a cache is shared by every app that points at it."""
        return [item if item["id"] in lcs_entries else {"hidden": "another app's entry", "similarity": item["similarity"]} for item in data]


    def lcs_record(question: str, wire: list) -> dict | None:
        """File one turn for the pane beside the chat from the requests it sent: the search and its reply, then the store after a miss.
        The decision is read back from the reply. An entry this kernel stored is `hit` or `wrong answer` (principle 2), because the app serves either one."""
        if not wire:
            return None
        search, store = wire[0], next(iter(wire[1:]), None)
        record = {"question": question, "search": search.body, "status": search.status, "ms": search.ms}
        if search.status != 200:
            record.update(decision="error", reply=search.payload)
        else:
            data = search.payload["data"]
            record["reply"] = {"data": lcs_shown(data)}
            if data:
                served = lcs_entries.get(data[0]["id"])
                record.update(similarity=data[0]["similarity"], served=served)
                record["decision"] = lcs_verdict(served, question) if served else "another app's entry"
            elif store is not None and store.status == 201:
                record.update(decision="miss", entry_id=store.payload["entryId"])
            else:
                record["decision"] = "miss, nothing stored"
            if store is not None:
                record.update(store=store.body, store_status=store.status, store_ms=store.ms, ttl_ms=store.body["ttlMillis"])
        lcs_turns.append(record)
        return record


    def lcs_note(record: dict | None, error: Exception | None) -> str:
        """The line the chat adds under a reply: where a served answer came from, the entryId a miss stored, or why the turn stopped."""
        if error is not None:
            kept = " The search still ran, and nothing was stored." if record else ""
            return f"\n\n*The turn stopped with {type(error).__name__}.{kept}*"
        if record is None or record["decision"] in ("error", "another app's entry"):
            return ""  # the turn's own reply says what happened
        if record["decision"] in ("hit", "wrong answer"):
            served = record["served"]
            source = f"Seed as `{served['key']}`" if served["source"] == "seed" else f"this chat {time.time() - served['stored_at']:.0f} s ago"
            return f"\n\n*Served from the cache at similarity {record['similarity']:.3f} in {record['ms']} ms, no model call. Stored by {source}.*"
        if record["decision"] == "miss":
            return f"\n\n*Miss, so the model answered and the app stored it: 201, entryId `{record['entry_id']}`, ttlMillis {record['ttl_ms']}.*"
        return f"\n\n*Miss. The store answered {record.get('store_status')}, so nothing was cached.*"


    def lcs_wire(method: str, path: str, body: dict | None) -> mo.Html:
        """The request as text: method and path on the cache, the Bearer header with the key masked, the pinned Content-Type, and the JSON body."""
        lines = [f"{method} /v1/caches/<LANGCACHE_CACHE_ID>{path}", "Authorization: Bearer <LANGCACHE_API_KEY>", "Content-Type: application/json"]
        if body is not None:
            lines += ["", json.dumps(body, indent=2)]
        return mo.md("```text\n" + "\n".join(lines) + "\n```")


    def lcs_json(payload) -> mo.Html:
        """A reply as a JSON block."""
        return mo.md("```json\n" + json.dumps(payload, indent=2) + "\n```")


    def lcs_label(record: dict | None) -> str:
        """A seed by its key, a chat answer by the question it was stored for, or a dash."""
        if record is None:
            return "-"
        if record["source"] == "seed":
            return f"`{record['key']}`"
        prompt = record["prompt"].replace("|", "&#124;")
        return "chat: " + prompt[:32] + ("..." if len(prompt) > 32 else "")


    def lcs_store_table(entries: dict[str, dict]) -> mo.Html:
        """What this kernel stored, one row per entryId, with when it was stored and when its TTL runs out."""
        if not entries:
            return mo.md("_No entries from this kernel. Select Seed to store five._")
        rows = ["| source | prompt | entryId | ttlMillis | stored at | expires at |", "|---|---|---|---|---|---|"]
        for entry_id, record in entries.items():
            stored = dt.datetime.fromtimestamp(record["stored_at"])
            expires = stored + dt.timedelta(milliseconds=record["ttlMillis"])
            prompt = record["prompt"].replace("|", "&#124;")
            rows.append(f"| {record['source']} | {prompt} | `{entry_id}` | {record['ttlMillis']} | {stored:%H:%M:%S} | {expires:%H:%M:%S} |")
        return mo.md("\n".join(rows))


    def lcs_threshold_table(scores: dict[str, dict], threshold: float) -> mo.Html:
        """One row per probe: its kind, the best entry this kernel stored at 0.50, the similarity, and the verdict at the slider: hit, wrong answer, or miss."""
        if not scores:
            return mo.md("_No scores yet. Select Seed, and the seven questions are searched at 0.50._")
        threshold = round(threshold, 2)
        counts = {"hit": 0, "wrong answer": 0, "miss": 0}
        rows = [f"| question | kind | best own entry | similarity | at {threshold:.2f} |", "|---|---|---|---|---|"]
        for text, score in scores.items():
            verdict = "miss" if score["entry"] is None or score["similarity"] < threshold else lcs_verdict(score["entry"], text)
            counts[verdict] += 1
            similarity = f"{score['similarity']:.3f}" if score["similarity"] is not None else "-"
            hidden = f" (+{score['hidden']} hidden)" if score["hidden"] else ""
            rows.append(f"| {text} | {score['kind']} | {lcs_label(score['entry'])}{hidden} | {similarity} | {verdict} |")
        rows.append(f"| **at {threshold:.2f}** | | | | **{counts['hit']} hit, {counts['wrong answer']} wrong answer, {counts['miss']} miss** |")
        return mo.md("\n".join(rows))

    return (
        CACHE_ASIDE_SCRIPT,
        THRESHOLD_PROBES,
        lcs_json,
        lcs_note,
        lcs_record,
        lcs_store_table,
        lcs_threshold_table,
        lcs_turns,
        lcs_wire,
    )


@app.cell(column=3, hide_code=True)
def cr_title(mo):
    mo.md(r"""
    # Redis Context Retriever

    Giving an agent tools over your customer data means writing each tool, indexing each entity, and hosting a server it can reach.
    Redis Context Retriever generates a surface of MCP tools from a data model you declare, and answers each call from your own Redis database.
    Select Create to build a surface from three Cairn Outfitters classes, then call its tools from the console to see three principles:

    1. **Each field's `index` kind decides which tools the surface lists.** Three `ContextModel` classes mark each indexed field `text`, `tag`, or `numeric` in `ContextField(index=...)`, three of the field types the Search column declares in `FT.CREATE`. `export_data_model(entities=[...])` turns the classes into the `data_model`, and `tools/list` then shows `search_customer_by_text` and no `search_order_by_text`, because Order has no `text` field.
    2. **`connection_config` ties the surface to your own Redis database.** `POST /context-surfaces` carries that `data_model` and a `data_source.connection_config` built from the same `REDIS_URL` the Search column uses, with `addr`, `username`, `password`, `db`, and `tls_enabled`. On import the service writes JSON documents at each class's `__redis_key_template__` and builds one Redis Search index per entity, which `JSON.GET` and `FT._LIST` show.
    3. **An admin key creates the surface, and the agent holds only an agent key.** `CTX_ADMIN_KEY` goes in `X-API-Key` on every admin request, and Create uses it to make one agent key with an `expires_at` and read that key's `mcp_url`. The console sends `tools/call` to that `mcp_url` with the agent key alone, and `get_customer_by_id` for `C001` returns the record with a `_retrieval` stamp naming the surface.

    Embeddings: none here. The three classes declare `text`, `tag`, and `numeric` fields and no `vector` field, so no tool in this column matches by meaning.

    Context Retriever does not support Redis Flex or Active-Active databases.
    Settings: `CTX_ADMIN_KEY` from the Redis Cloud Admin keys page, shown once, plus `REDIS_URL` for a database with Search and JSON that the hosted service can reach.
    Create sends that database's password to the service, which keeps it until Clean up deletes the surface.
    `CTX_API_URL` and `CTX_MCP_URL` are optional, and their defaults suit Redis Cloud.
    Docs: [Context Retriever](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/) and
    [concepts](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/concepts/).

    Each line reads source, target, and what passes between them:

    ```text
    App     -> data_model  export_data_model over Customer, Product, and Order, in this kernel
    App     -> admin API   POST /context-surfaces: data_model and connection_config, with the admin key
    App     -> admin API   POST .../agent-keys with expires_at, then GET .../agent-keys for the key's mcp_url
    App     -> admin API   POST .../data once per entity, the 20 Cairn records
    service -> your Redis  JSON documents at the key templates, one index per entity, a read per tools/call
    App     -> mcp_url     tools/list after Create and on Refresh, then one tools/call per Send, with the agent key
    ```
    """)
    return


@app.cell(hide_code=True)
def cr_model(crs_data_model, crs_record_keys, json, mo):
    # The declared model, as the reader sees it: one row per entity with the index kind of each field and the tools the rule
    # predicts. The service's own list comes after Create, in cr_surface, so the prediction is checked against tools/list.
    _families = {
        "key": "`get_{e}_by_id`",
        "any": "`list_{e}`, `count_{e}`",
        "filter": "`filter_{e}`",
        "numeric": "`summarize_{e}`",
        "text": "`search_{e}_by_text`",
    }
    _rows = ["| entity | key | text | tag | numeric | tools the kinds add |", "|---|---|---|---|---|:--|"]
    for _entity in crs_data_model["entities"]:
        _e = _entity["name"].lower()
        _by_kind = {"text": [], "tag": [], "numeric": []}
        _key = ""
        for _field in _entity["fields"]:
            if _field["is_key_component"]:
                _key = _field["name"]
            for _index in _field["redis_indices"]:
                _by_kind.setdefault(_index["type"], []).append(_field["name"])
        _tools = [_families["key"], _families["any"]]
        if _by_kind["tag"] or _by_kind["numeric"]:
            _tools.append(_families["filter"])
        if _by_kind["numeric"]:
            _tools.append(_families["numeric"])
        if _by_kind["text"]:
            _tools.append(_families["text"])
        _rows.append(
            f"| {_entity['name']} | `{_key}` | {', '.join(_by_kind['text']) or '-'} | {', '.join(_by_kind['tag']) or '-'} "
            f"| {', '.join(_by_kind['numeric']) or '-'} | {', '.join(_tools).format(e=_e)} |"
        )

    mo.vstack(
        [
            mo.md(r"""
    ## Declare three classes and read the data model they export

    `Customer`, `Product`, and `Order` are `ContextModel` classes in `cr_model_fns` below, and each field's `ContextField(index=...)` is `text`, `tag`, `numeric`, or none.
    `export_data_model(entities=[...])` turns them into the `data_model` JSON that Create sends, with one `redis_key_template` per entity under this run's prefix.
    The table predicts the tools from the index kinds, and `tools/list` after Create checks the prediction. `get_` needs a key component, `filter_` a `tag` or `numeric` field, `summarize_` a `numeric` field, and `search_` a `text` field.
    `intersect_results`, `union_results`, `except_results`, and `expand_results` are listed once for every surface.
    """),
            mo.md("\n".join(_rows)),
            mo.md(f"The templates name {len(crs_record_keys)} keys, from `{crs_record_keys[0]}` to `{crs_record_keys[-1]}`. Nothing has been sent yet."),
            mo.accordion({"The data_model, from export_data_model": mo.md(f"```json\n{json.dumps(crs_data_model, indent=2)}\n```")}),
        ]
    )
    return


@app.cell(hide_code=True)
def cr_create_button(crs_admin_session, mo):
    _ = crs_admin_session  # the buttons appear once the admin session exists, so a press never runs ahead of the settings check
    crs_create_button = mo.ui.run_button(label="Create")
    crs_refresh_button = mo.ui.run_button(label="Refresh")
    crs_cleanup_button = mo.ui.run_button(label="Clean up", kind="danger")

    mo.vstack(
        [
            mo.md(r"""
    ## Create the surface, import 20 records, and list its tools

    Create sends four kinds of admin request, each with `CTX_ADMIN_KEY` in `X-API-Key`.
    `POST /context-surfaces` carries the `data_model` and your database password, `POST .../agent-keys` makes a key that expires in an hour, `GET .../agent-keys` reads that key's `mcp_url`, and `POST .../data` runs once per entity.
    The service keeps the password to reach your database until Clean up deletes the surface.
    Refresh lists the tools again and re-reads your database. Clean up deletes every surface named `cairn-<run>`, unlinks the 20 documents the delete leaves behind, and drops any index the service left.
    """),
            mo.hstack([crs_create_button, crs_refresh_button, crs_cleanup_button], justify="start"),
        ]
    )
    return crs_cleanup_button, crs_create_button, crs_refresh_button


@app.cell(hide_code=True)
def cr_create(
    crs_admin_base,
    crs_cleanup_button,
    crs_create_agent_key,
    crs_create_body,
    crs_create_button,
    crs_create_surface,
    crs_delete_surfaces,
    crs_drop_indexes,
    crs_expire_records,
    crs_import_records,
    crs_record_keys,
    crs_set_calls,
    crs_set_surface,
    crs_shown,
    crs_surface,
    crs_surface_name,
    crs_unlink_records,
    mo,
):
    # Create and Clean up. The surface goes into state through crs_set_surface, so this cell never reads the getter and never
    # reruns on its own change. Create runs once per press and is never retried, since a replayed POST would make a second
    # surface holding the password. The create reply holds the agent key, so it is never this cell's last expression.
    _note = ""
    if crs_create_button.value:
        if crs_surface():
            _note = "This kernel already holds a surface. Select Clean up first, so a second surface never takes your password."
        else:
            _body = crs_create_body()
            try:
                _made = crs_create_surface(_body)
            except OSError as _error:  # a timeout or a dropped connection: the service may have made the surface before the reply was lost
                raise RuntimeError(f"Create got no reply ({type(_error).__name__}), so a surface may exist anyway. Select Clean up to delete every surface named {crs_surface_name}.") from _error
            # From here on the surface, and the password it holds, exist on the service. The finally gives every key the service
            # managed to write its TTL even when a call raises, and the re-raised error says how to remove the surface.
            try:
                _access = crs_create_agent_key(_made["id"])
                _imports = crs_import_records(_made["id"])
            except Exception as _error:
                raise RuntimeError(f"{str(_error).rstrip('.')}. Select Clean up to remove the surface this left.") from _error
            finally:
                _expiring = crs_expire_records()
            _shown = crs_shown("POST", crs_admin_base + "/context-surfaces", _body)  # the pane keeps this masked copy, never _body
            crs_set_surface({**_made, **_access, "imports": _imports, "expiring": _expiring, "create_request": _shown})
            _counts = ", ".join(f"{n} {entity.lower()}s" for entity, n in _imports.items())
            _note = f"Created surface `{_made['id']}` and imported {_counts}. {_expiring} of {len(crs_record_keys)} keys now expire in an hour."
    if crs_cleanup_button.value:
        _current = crs_surface()
        _deleted = crs_delete_surfaces(_current["id"] if _current else None)
        _unlinked = crs_unlink_records()
        _dropped = crs_drop_indexes()
        crs_set_surface(None)
        crs_set_calls([])
        _plural = "" if len(_deleted) == 1 else "s"
        _index_word = "index" if len(_dropped) == 1 else "indexes"
        _statuses = ", ".join(str(status) for status in _deleted.values()) or "none found"
        _note = f"Deleted {len(_deleted)} surface{_plural} named `{crs_surface_name}` ({_statuses}), unlinked {_unlinked} of {len(crs_record_keys)} keys, and dropped {len(_dropped)} {_index_word} the delete had left."

    mo.md(_note) if _note else None
    return


@app.cell(hide_code=True)
def cr_surface(
    crs_document,
    crs_get_surface,
    crs_indexes,
    crs_list_tools,
    crs_read_surface,
    crs_record_keys,
    crs_refresh_button,
    json,
    mo,
):
    # What the surface holds, on both sides. Left, the service: the create reply, the agent key's expiry, and tools/list.
    # Right, your database: JSON.GET of the first key and the indexes the service built. Reruns on Create, Clean up, and Refresh.
    _surface = crs_get_surface()
    _ = crs_refresh_button.value

    if _surface is None:
        crs_tools = []
        _service = [mo.md("**The service**\n\n_No surface in this kernel. Select Create, or Clean up to delete one a restarted kernel left behind._")]
    else:
        try:
            crs_tools = crs_list_tools(_surface)
            _problem = ""
        except Exception as _error:  # the agent key's hour is up, the service refused, or the reply was not JSON: say why, and keep the console and the call log
            crs_tools = []
            _problem = str(_error) if isinstance(_error, RuntimeError) else f"tools/list stopped with {type(_error).__name__}: {_error}"
        _names = [tool["name"] for tool in crs_tools]
        _searches = ", ".join(f"`{name}`" for name in sorted(_names) if name.startswith("search_"))
        _missing = " There is no `search_order_by_text`, because Order has no `text` field." if "search_order_by_text" not in _names else ""
        _imports = ", ".join(f"{n} {entity.lower()}s" for entity, n in _surface["imports"].items())
        _rows = ["| tool | arguments, required starred |", "|---|---|"]
        for _tool in crs_tools:
            _schema = _tool.get("inputSchema", {})
            _required = set(_schema.get("required", []))
            _arguments = ", ".join(f"`{p}`" + ("*" if p in _required else "") for p in _schema.get("properties", {}))
            _rows.append(f"| `{_tool['name']}` | {_arguments or '-'} |")
        _listing = (
            [mo.md(f"The tool list is empty, and the console keeps its presets. {_problem}")]
            if _problem
            else [
                mo.md(f"`tools/list` returned {len(_names)} tools. The text searches are {_searches}, one per entity with a `text` field.{_missing}"),
                mo.md("\n".join(_rows)),
            ]
        )
        _service = [
            mo.md(
                f"**The service**\n\n`POST /context-surfaces` answered {_surface['http']} with id `{_surface['id']}`, status `{crs_read_surface(_surface['id'])['status']}` now. "
                f"Imported {_imports}. Agent key `****{_surface['agent_key'][-4:]}` expires at {_surface['key_expires_at']}, and its `mcp_url` is `{_surface['mcp_url']}`."
            ),
            *_listing,
            mo.accordion({"The create request as sent, password and admin key masked": mo.md(f"```json\n{json.dumps(_surface['create_request'], indent=2)}\n```")}),
        ]

    _indexes = crs_indexes()  # FT._LIST, then FT.INFO per index, keeping the ones that carry the entity prefixes
    _document = crs_document(crs_record_keys[0])
    _database = [
        mo.md(f"**Your database**\n\n`JSON.GET {crs_record_keys[0]}`"),
        mo.md(f"```json\n{json.dumps(_document, indent=2)}\n```" if _document else "_No document at that key. The service writes it on import, and Clean up or the one-hour TTL removes it._"),
    ]
    if _indexes:
        _index_rows = ["| index, from FT._LIST | prefix | num_docs |", "|---|---|---|"]
        _index_rows += [f"| `{i['name']}` | `{', '.join(i['prefixes'])}` | {i['num_docs']} |" for i in _indexes]
        _database += [mo.md("One index per entity, built by the service. `FT.INFO` gives each one's prefix and document count."), mo.md("\n".join(_index_rows))]
    else:
        _database.append(mo.md("_No index carries this run's entity prefixes. The service builds three on Create and drops them on delete._"))

    mo.hstack([mo.vstack(_service), mo.vstack(_database)], widths="equal", align="start", gap=1.5)
    return (crs_tools,)


@app.cell(hide_code=True)
def cr_controls(crs_tools, mo):
    # The pick, upstream of the console form so a new pick rebuilds the form with its tool and arguments. Examples come from
    # each tool's inputSchema, with a few named overrides. Three presets step outside the model.
    crs_examples = {
        "get_customer_by_id": {"id": "C001"},
        "get_product_by_id": {"id": "P100"},
        "get_order_by_id": {"id": "O1001"},
        "search_customer_by_text": {"query": "Ada"},
        "search_product_by_text": {"query": "tent"},
        "filter_customer": {"tag_conditions": [{"field": "tier", "value": "gold"}]},
        "filter_order": {"numeric_conditions": [{"field": "total", "min_value": 100}]},
        "list_customer": {"limit": 3, "sort_by": "lifetime_value", "sort_order": "desc"},
        "count_order": {"function": "count", "group_by": "status"},
        "summarize_customer": {"field": "lifetime_value", "function": "sum", "group_by": "tier"},
    }
    crs_presets = {
        "filter_customer, no condition": ("filter_customer", {"limit": 5, "offset": 0}),
        "filter_customer, a tag condition on a numeric field": ("filter_customer", {"tag_conditions": [{"field": "lifetime_value", "value": "4200"}]}),
        "run_query, a tool the surface does not list": ("run_query", {"query": "FT.SEARCH idx *"}),
    }


    def crs_example(tool: dict) -> dict:
        """Arguments to start from: the named example, or each required property filled from its schema."""
        if tool["name"] in crs_examples:
            return crs_examples[tool["name"]]
        schema = tool.get("inputSchema", {})
        starters = {"string": "", "integer": 1, "number": 1, "boolean": False, "array": [], "object": {}}
        example = {}
        for name in schema.get("required", []):
            prop = schema.get("properties", {}).get(name, {})
            example[name] = prop["enum"][0] if prop.get("enum") else starters.get(prop.get("type"), "")
        return example


    _options = {tool["name"]: (tool["name"], crs_example(tool)) for tool in crs_tools} | crs_presets
    crs_pick = mo.ui.dropdown(
        options=_options,
        value="get_customer_by_id" if "get_customer_by_id" in _options else next(iter(_options)),
        label="Tool or preset",
        searchable=True,
    )  # displayed in the workbench, upstream of the form so a new pick prefills it

    mo.md(r"""
    ## Call the tools from the console and read the replies

    Pick `get_customer_by_id` and select Send. The reply is Ada Lovelace's record with a `_retrieval` stamp that names the surface, read from the key `JSON.GET` shows above.
    Change `C001` to `C999` in the arguments and send again to see how the surface answers an id it does not hold.

    Pick `search_customer_by_text` with `Ada`, then look for an order search in the list. Order has no `text` field, so the surface generated none.
    Pick `filter_customer`, then the no-condition preset. The first returns gold customers as data, and the second comes back with `isError` and the reason in the text.

    The tag-condition preset puts a numeric field where the schema allows tag fields. The schema should reject it with a JSON-RPC error, code -32602, a shape an agent must tell apart from a tool error.
    The last preset names a tool the surface never generated. An agent gets the tools the data model made and nothing else, so expect a JSON-RPC error for that one too.

    Compare the `X-API-Key` in the request below, `****` plus the agent key's last four characters, with the `****` in the create request above: the admin key never appears in a tool call.
    """)
    return (crs_pick,)


@app.cell(hide_code=True)
def cr_workbench(
    crs_args_field,
    crs_get_calls,
    crs_get_surface,
    crs_pick,
    crs_request,
    crs_send_button,
    crs_shown,
    crs_tool_field,
    json,
    mo,
):
    # Left, the console: the pick, the form, and Send. Right, the data behind the last call: the log, the reply text, and the
    # request crs_request builds from the form as it stands, with the agent key masked.
    _surface = crs_get_surface()
    _calls = crs_get_calls()
    _tail = _surface["agent_key"][-4:] if _surface else ""
    _url = _surface["mcp_url"] if _surface else "<mcp_url, after Create>"

    try:
        _arguments = json.loads(crs_args_field.value or "{}")
        _request = mo.md(f"```json\n{json.dumps(crs_shown('POST', _url, crs_request(crs_tool_field.value.strip(), _arguments), _tail), indent=2)}\n```")
    except ValueError as _error:
        _request = mo.md(f"The arguments are not valid JSON, so Send has nothing to post. {_error}")

    _data = [mo.md("**Calls**, newest first" if _calls else "**Calls**, none yet. Send posts one read-only `tools/call` and adds a row here.")]
    if _calls:
        _rows = ["| at (UTC) | tool | arguments | HTTP | kind |", "|---|---|---|---|---|"]
        for _call in _calls:
            _shown = _call["arguments"] if isinstance(_call["arguments"], str) else json.dumps(_call["arguments"])
            _shown = (_shown[:80] + "...") if len(_shown) > 80 else _shown
            _rows.append(f"| {_call['at']} | `{_call['tool']}` | `{_shown.replace('|', '&#124;')}` | {_call['http']} | {_call['kind']} |")
        _data += [
            mo.md("\n".join(_rows)),
            mo.md("---"),
            mo.md(f"**Reply**, {_calls[0]['kind']}. Data is `result.content[0].text` parsed, a tool error is that text with `isError` true, and a JSON-RPC error is the top-level `error`."),
            mo.md(f"```json\n{_calls[0]['text'][:3000]}\n```"),
        ]
    _data += [
        mo.md("---"),
        mo.md("Editing the form redraws the request below without sending it."),
        mo.md("**Request**, from `crs_request` for the form as it stands, with the agent key masked"),
        _request,
    ]

    mo.hstack(
        [
            mo.vstack([crs_pick, crs_tool_field, crs_args_field, crs_send_button]),
            mo.vstack(_data),
        ],
        widths=[5, 6],
        gap=1.5,
        align="start",
    )
    return


@app.cell(hide_code=True)
def cr_build_md(mo):
    mo.md(r"""
    ## The Context Retriever code: model, client, surface, database, call

    One cell per step, with the code visible in `marimo edit`. In `marimo run`, pick **Show code** from the app menu.
    Every `crs_admin` call carries the admin key, every `crs_mcp` call carries the agent key, and `REDIS_CLIENT` reads your database directly.
    Create and Clean up call the surface and database functions, and each Send calls `crs_call`.
    """)
    return


@app.cell
def cr_model_fns(CAIRN_SCOPE):
    # Model. Three classes, each field's index kind, and the data_model they export. Sends nothing.
    from context_surfaces import ContextField, ContextModel, ContextRelationship, export_data_model

    from cairn_support.sample_data import SHOP_RECORDS

    _redis_prefix = f"cairn:{CAIRN_SCOPE}:"

    class Customer(ContextModel):
        """A Cairn Outfitters shopper."""

        __redis_key_template__ = _redis_prefix + "customer:{customer_id}"  # the text before the first brace becomes the index prefix

        customer_id: str = ContextField(description="Customer id, such as C001", is_key_component=True)  # get_customer_by_id takes this
        name: str = ContextField(description="Full name", index="text")  # text: search_customer_by_text
        tier: str = ContextField(description="Loyalty tier", index="tag", allowed_values=["bronze", "silver", "gold"])  # tag: filter_customer, count_customer
        region: str = ContextField(description="Sales region", index="tag", allowed_values=["emea", "apac"])
        lifetime_value: float = ContextField(description="Lifetime spend in USD", index="numeric")  # numeric: summarize_customer, sort_by
        orders: list["Order"] = ContextRelationship(description="Orders this customer placed", target="Order", source_field="customer_id")


    class Product(ContextModel):
        """An item Cairn Outfitters sells."""

        __redis_key_template__ = _redis_prefix + "product:{sku}"

        sku: str = ContextField(description="Stock keeping unit, such as P100", is_key_component=True)
        title: str = ContextField(description="Product title", index="text")
        category: str = ContextField(description="Product category", index="tag")
        price: float = ContextField(description="Price in USD", index="numeric")


    class Order(ContextModel):
        """One order: a customer, a product, and a status. No text field, so no search_order_by_text."""

        __redis_key_template__ = _redis_prefix + "order:{order_id}"

        order_id: str = ContextField(description="Order id, such as O1001", is_key_component=True)
        customer_id: str = ContextField(description="The buyer's customer id", index="tag")
        sku: str = ContextField(description="The product's sku", index="tag")
        region: str = ContextField(description="Sales region", index="tag", allowed_values=["emea", "apac"])
        status: str = ContextField(description="Order status", index="tag", allowed_values=["placed", "shipped", "delivered", "returned"])
        total: float = ContextField(description="Order total in USD", index="numeric")
        customer: "Customer" = ContextRelationship(description="Buyer", target="Customer", source_field="customer_id")
        product: "Product" = ContextRelationship(description="Item bought", target="Product", source_field="sku")


    crs_models = [Customer, Product, Order]
    crs_data_model = export_data_model(  # always with entities=[...]: with None, the SDK exports every class ever defined in this kernel
        "Cairn Outfitters", "Customers, products, and orders of an outdoor gear shop", entities=crs_models
    )
    crs_record_keys = [model.__redis_key_template__.format(**row) for model in crs_models for row in SHOP_RECORDS[model.__name__]]  # the 20 keys the service writes
    crs_entity_prefixes = [model.__redis_key_template__.split("{")[0] for model in crs_models]  # the index prefixes, for the database pane and Clean up
    return (
        SHOP_RECORDS,
        crs_data_model,
        crs_entity_prefixes,
        crs_models,
        crs_record_keys,
    )


@app.cell
def cr_connect(Reply, json_request, json_session, os, redis):
    # Client. The admin session with the admin key from .env, the MCP session for the agent key, and the connection_config the surface gets.
    from cairn_support.types import Surface, ToolCall


    crs_admin_base = os.environ.get("CTX_API_URL", "https://cloud.redis.io/context-surfaces") + "/api/v1"
    crs_mcp_default = os.environ.get("CTX_MCP_URL", "https://gcp-us-east4.context-surfaces.redis.io/mcp")  # used only if the agent-key listing carries no mcp_url
    crs_admin_session = json_session(crs_admin_base, {"X-API-Key": os.environ["CTX_ADMIN_KEY"]}, timeout=(5, 60))  # never retried: a replayed create would make a second surface holding the password
    crs_mcp_session = json_session(timeout=(5, 60))  # no key on the session: each call carries the agent key of its surface


    def crs_admin(method: str, path: str, body: dict | None = None, ok: tuple = (200, 201)) -> Reply:
        """One admin request under /api/v1 with the admin key (principle 3): the HTTP status, the reply as a dict, and the milliseconds it took.
        A status outside `ok` raises with the reply's detail and never the request, since one request carries the database password."""
        reply = json_request(crs_admin_session, method, path, body)
        if reply.status not in ok:
            error = reply.payload if isinstance(reply.payload, dict) else {}
            # a bad key answers {"error": "Invalid API key"} (probed 2026-10-08), other failures "detail" or "message", and a reply that is not JSON has only "type", its reason
            detail = error.get("detail") or error.get("message") or error.get("error") or error.get("type") or ""
            raise RuntimeError(f"{method} {path} answered {reply.status}. {detail}".strip())
        return reply


    def crs_mcp(surface: Surface, body: dict):
        """One JSON-RPC POST to the surface's mcp_url with the agent key alone: no admin key, no initialize handshake (principle 3).
        Returns the HTTP response as it came, so crs_call can show a reply that is not JSON."""
        return crs_mcp_session.post(surface["mcp_url"], json=body, headers={"X-API-Key": surface["agent_key"]})


    def crs_connection_config(url: str) -> dict:
        """The database the surface reads and writes, from the same REDIS_URL the Search column uses (principle 2).
        redis-py's parse_url reads the URL the way REDIS_CLIENT does, with the password unquoted. pool_size 2 and min_idle_conns 0 fit a 30-connection database."""
        parts = redis.connection.parse_url(url)
        return {
            "addr": f"{parts['host']}:{parts.get('port', 6379)}",
            "username": parts.get("username") or "default",
            "password": parts.get("password", ""),
            "db": parts.get("db", 0),
            "tls_enabled": parts.get("connection_class") is redis.SSLConnection,  # rediss://
            "pool_size": 2,
            "min_idle_conns": 0,
        }


    return (
        Surface,
        ToolCall,
        crs_admin,
        crs_admin_base,
        crs_admin_session,
        crs_connection_config,
        crs_mcp,
        crs_mcp_default,
    )


@app.cell
def cr_admin_fns(
    CAIRN_SCOPE,
    SHOP_RECORDS,
    crs_admin,
    crs_connection_config,
    crs_data_model,
    crs_mcp_default,
    crs_models,
    dt,
    os,
):
    # Surface. The admin calls: the create request, create the surface, make an agent key and find its mcp_url, import the records, read, list by name, and delete.
    crs_surface_name = "cairn-" + CAIRN_SCOPE


    def crs_create_body() -> dict:
        """The create request: the data_model (principle 1) and a data_source whose connection_config comes from REDIS_URL (principle 2).
        It holds the database password, so crs_create_surface sends it once and the pane shows only a masked copy."""
        return {
            "name": crs_surface_name,
            "description": "Made by the Redis Iris basics notebook. Safe to delete.",
            "metadata": {"created_by": "iris-basics-notebook", "scope": CAIRN_SCOPE},
            "data_model": crs_data_model,
            "data_source": {"type": "redis", "connection_config": crs_connection_config(os.environ["REDIS_URL"])},
        }


    def crs_create_surface(body: dict) -> dict:
        """POST /context-surfaces with the create request. The password crosses the wire here and nowhere else.
        Returns the id, name, status, and HTTP status. Never retried: a replay would make a second surface holding the password."""
        status, reply, _ = crs_admin("POST", "/context-surfaces", body)
        return {"id": reply["id"], "name": reply["name"], "status": reply["status"], "http": status}


    def crs_create_agent_key(surface_id: str) -> dict:
        """POST .../agent-keys with expires_at an hour out, then GET .../agent-keys for that key's mcp_url (principle 3).
        The key itself is in the POST reply only, so keep it and show at most its last four characters."""
        expires_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=3600)).isoformat(timespec="seconds")
        made = crs_admin("POST", f"/context-surfaces/{surface_id}/agent-keys", {"name": crs_surface_name + "-agent", "expires_at": expires_at}).payload
        listed = crs_admin("GET", f"/context-surfaces/{surface_id}/agent-keys?page_size=50").payload
        mine = next((key for key in listed.get("agent_keys", []) if key.get("id") == made["id"]), {})
        return {"agent_key": made["key"], "key_expires_at": made.get("expires_at", expires_at), "mcp_url": mine.get("mcp_url") or crs_mcp_default}


    def crs_import_records(surface_id: str) -> dict[str, int]:
        """POST .../data once per entity. Each record is Model(**row).model_dump(), so a field the class does not declare never reaches the wire.
        The service writes one JSON document per record at the key template and indexes it (principle 2). Returns the imported count per entity."""
        counts = {}
        for model in crs_models:
            records = [model(**row).model_dump() for row in SHOP_RECORDS[model.__name__]]
            body = {"entity": model.__name__, "records": records, "options": {"on_conflict": "overwrite", "on_error": "fail_fast"}}
            reply = crs_admin("POST", f"/context-surfaces/{surface_id}/data", body).payload
            counts[model.__name__] = reply.get("imported", len(records))
        return counts


    def crs_read_surface(surface_id: str) -> dict:
        """GET .../{id}: the surface with its current status, or a 404 marker once it is deleted."""
        status, reply, _ = crs_admin("GET", f"/context-surfaces/{surface_id}", ok=(200, 404))
        return reply if status == 200 else {"id": surface_id, "status": "deleted (404)"}


    def crs_surfaces_named(name: str) -> list[str]:
        """The ids of every surface on the account with this name, from the paged listing. Clean up uses it, so a surface a restarted kernel forgot is still found."""
        found, page = [], 1
        while page <= 40:
            reply = crs_admin("GET", f"/context-surfaces?page={page}&page_size=50").payload
            found += [surface["id"] for surface in reply.get("context_surfaces", []) if surface.get("name") == name]
            if not reply.get("pagination", {}).get("has_next"):
                break
            page += 1
        return found


    def crs_delete_surfaces(known_id: str | None = None) -> dict[str, int]:
        """DELETE .../{id} for every surface named cairn-<run>, plus the one this kernel holds. 200 or 204 deleted, 404 already gone.
        The agent keys die with the surface and the service drops the indexes it built, but the documents stay in your database."""
        statuses = {}
        for surface_id in dict.fromkeys(([known_id] if known_id else []) + crs_surfaces_named(crs_surface_name)):
            statuses[surface_id] = crs_admin("DELETE", f"/context-surfaces/{surface_id}", ok=(200, 204, 404)).status
        return statuses


    return (
        crs_create_agent_key,
        crs_create_body,
        crs_create_surface,
        crs_delete_surfaces,
        crs_import_records,
        crs_read_surface,
        crs_surface_name,
    )


@app.cell
def cr_redis_fns(REDIS_CLIENT, crs_entity_prefixes, crs_record_keys, redis):
    # Database. The notebook's own reads of what the service wrote, and the part of Clean up the service leaves to you.
    def crs_indexes() -> list[dict]:
        """The indexes the service built for this run: every index whose prefixes are among the three entity prefixes.
        FT._LIST names them, and FT.INFO gives each one's prefixes and num_docs."""
        found = []
        for name in REDIS_CLIENT.execute_command("FT._LIST"):
            try:
                info = REDIS_CLIENT.ft(name).info()
            except redis.ResponseError:  # dropped between the two commands
                continue
            definition = info.get("index_definition", [])
            if isinstance(definition, list):  # legacy flat key-value list from older wire shapes
                definition = dict(zip(definition[::2], definition[1::2]))
            prefixes = list(definition.get("prefixes", []))
            if prefixes and all(prefix in crs_entity_prefixes for prefix in prefixes):
                found.append({"name": name, "prefixes": prefixes, "num_docs": int(info.get("num_docs", 0))})
        return found


    def crs_document(key: str) -> dict | None:
        """JSON.GET of one key the service wrote, or None once it is gone. The agent never sends this command: it calls a tool, and the service reads here."""
        return REDIS_CLIENT.json().get(key)


    def crs_expire_records() -> int:
        """EXPIRE 3600 on each of the 20 keys. The service sets no TTL, so this is the notebook's backstop. Returns how many keys existed."""
        return sum(1 for key in crs_record_keys if REDIS_CLIENT.expire(key, 3600))


    def crs_unlink_records() -> int:
        """UNLINK the 20 keys by name. DELETE on the surface leaves the documents, so this is the notebook's job. Returns how many existed."""
        return REDIS_CLIENT.unlink(*crs_record_keys)


    def crs_drop_indexes() -> list[str]:
        """FT.DROPINDEX, without DD, on any index still carrying the entity prefixes after the surface is gone. The service drops its own on DELETE, so this usually finds nothing."""
        dropped = []
        for index in crs_indexes():
            try:
                REDIS_CLIENT.ft(index["name"]).dropindex(delete_documents=False)
            except redis.ResponseError:  # already gone
                continue
            dropped.append(index["name"])
        return dropped

    return (
        crs_document,
        crs_drop_indexes,
        crs_expire_records,
        crs_indexes,
        crs_unlink_records,
    )


@app.cell
def cr_call_fn(Surface, ToolCall, crs_mcp, json):
    # Call. tools/list after Create and on Refresh, and one read-only tools/call per Send, sorted into the three reply shapes an agent must tell apart.
    def crs_request(name: str, arguments: dict) -> dict:
        """The JSON-RPC body of one tool call. The agent sends this and the agent key, nothing else: no query, no key name, no index name (principle 3)."""
        return {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


    def crs_list_tools(surface: Surface) -> list[dict]:
        """tools/list: every tool the data model generated, each with a name, a description, and an inputSchema (principle 1).
        A non-200 answer, or a 200 that carries a JSON-RPC error, raises a RuntimeError that says why."""
        response = crs_mcp(surface, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        if response.status_code != 200:
            raise RuntimeError(f"tools/list answered {response.status_code}. The agent key expires an hour after Create: Clean up, then Create again.")
        reply = response.json()
        error = reply.get("error")
        if error:  # HTTP 200 can still carry a top-level JSON-RPC error
            code, message = (error.get("code"), error.get("message")) if isinstance(error, dict) else (None, error)
            raise RuntimeError(f"tools/list returned JSON-RPC error {code}. {message}")
        return reply["result"]["tools"]


    def crs_call(surface: Surface, name: str, arguments: dict) -> ToolCall:
        """One tools/call, and the reply sorted three ways. Data: result.content[0].text is a JSON string, so it is parsed.
        Tool error: HTTP 200 with result.isError true and the reason in that text. JSON-RPC error: a top-level error with a code, -32602 when the arguments break the schema.
        A rejected or expired agent key is none of these: HTTP 401 with a plain error string, shown as the HTTP status."""
        response = crs_mcp(surface, crs_request(name, arguments))
        try:
            reply = response.json()
        except ValueError:
            reply = None

        if not isinstance(reply, dict):  # None or a top-level array: not a JSON-RPC reply
            kind, text = "not JSON", response.text[:400]
        elif isinstance(reply.get("error"), dict):
            kind, text = f"JSON-RPC error {reply['error'].get('code')}", json.dumps(reply["error"], indent=2)
        elif "error" in reply:  # a rejected or expired agent key answers HTTP 401 with {"error": "Invalid API key"}, probed 2026-10-08
            kind, text = f"HTTP {response.status_code}", str(reply["error"])
        else:
            result = reply.get("result", {})
            content = result.get("content") or [{}]
            text = content[0].get("text", json.dumps(result, indent=2))
            kind = "tool error" if result.get("isError") else "data"
            if kind == "data":
                try:
                    text = json.dumps(json.loads(text), indent=2)
                except ValueError:
                    pass
        return {"tool": name, "arguments": arguments, "http": response.status_code, "kind": kind, "text": text}

    return crs_call, crs_list_tools, crs_request


@app.cell(hide_code=True)
def cr_machinery_md(mo):
    mo.md(r"""
    ## Notebook plumbing: surface state, console form, Send

    Three hidden cells follow: the surface and call-log state with the request masker, the console form prefilled from the pick, and the Send handler.
    Read `cr_settings`, `cr_console`, and `cr_send` in `iris_basics.py`, or expand them in `marimo edit`.
    """)
    return


@app.cell(hide_code=True)
def cr_settings(json, mo):
    # Surface and call-log state, and the mask for a request on display. cr_create sets the surface after Create and clears it
    # after Clean up, and cr_send appends to the log after each Send. A cell that references a getter reruns on every setter call,
    # so the display cells read the getters and the action cells read the surface through crs_surface() instead. The agent key
    # lives inside the surface dict, which no cell renders: the summary shows its last four characters only. crs_shown masks the
    # password and the keys of a request, so the create request kept in the surface is the masked copy.
    crs_get_surface, crs_set_surface = mo.state(None)
    crs_get_calls, crs_set_calls = mo.state([])


    def crs_surface() -> dict | None:
        """The surface this kernel created, read at call time: id, status, mcp_url, agent key, expiry, import counts, and the masked create request. None before Create and after Clean up."""
        return crs_get_surface()


    def crs_shown(method: str, url: str, body: dict, key_tail: str = "") -> dict:
        """A request for display. X-API-Key shows **** alone for the admin key or **** plus the agent key's last four characters, and a connection_config password shows ****."""
        shown = json.loads(json.dumps(body))
        config = shown.get("data_source", {}).get("connection_config")
        if config:
            config["password"] = "****"
        return {"method": method, "url": url, "headers": {"X-API-Key": "****" + key_tail, "Content-Type": "application/json"}, "json": shown}

    return (
        crs_get_calls,
        crs_get_surface,
        crs_set_calls,
        crs_set_surface,
        crs_shown,
        crs_surface,
    )


@app.cell(hide_code=True)
def cr_console(crs_pick, json, mo):
    # The console form, rebuilt from the pick. Each field starts from the pick's tool and arguments and stays editable. Send is
    # a run_button, True only for the run its press triggers. cr_send reads the three values, since a cell cannot read the value
    # of an element it defines.
    _name, _arguments = crs_pick.value
    crs_tool_field = mo.ui.text(value=_name, label="Tool", full_width=True)
    crs_args_field = mo.ui.text_area(value=json.dumps(_arguments, indent=2), label="Arguments (JSON)", rows=7, full_width=True)
    crs_send_button = mo.ui.run_button(label="Send tools/call")
    return crs_args_field, crs_send_button, crs_tool_field


@app.cell(hide_code=True)
def cr_send(
    crs_args_field,
    crs_call,
    crs_send_button,
    crs_set_calls,
    crs_surface,
    crs_tool_field,
    dt,
    json,
):
    # Send. One tools/call per press, appended to the log through the setter. Nothing is sent when the arguments are not JSON
    # or when no surface exists, and the row says so.
    if crs_send_button.value:
        _surface = crs_surface()
        _name = crs_tool_field.value.strip()
        try:
            _arguments = json.loads(crs_args_field.value or "{}")
        except ValueError as _error:
            _row = {"tool": _name, "arguments": crs_args_field.value, "http": "", "kind": "not sent, arguments are not JSON", "text": str(_error)}
        else:
            if _surface:
                try:
                    _row = crs_call(_surface, _name, _arguments)
                except OSError as _error:  # a timeout or a dropped connection: the row says so, and the console stays
                    _row = {"tool": _name, "arguments": _arguments, "http": "", "kind": f"no reply, {type(_error).__name__}", "text": str(_error)[:400]}
            else:
                _row = {"tool": _name, "arguments": _arguments, "http": "", "kind": "not sent, no surface in this kernel", "text": ""}
        _row["at"] = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S")
        crs_set_calls(lambda calls: [_row, *calls])
    return


@app.cell(column=4, hide_code=True)
def am_title(mo):
    mo.md(r"""
    # Redis Agent Memory

    An agent that forgets a customer's two dogs between conversations asks about them again every time.
    Redis Agent Memory keeps each conversation in session memory and durable facts in long-term memory, written by the app or extracted from session events.
    Select Seed to write memories for two Cairn Outfitters customers, Tom and Priya, then chat as either to see three principles:

    1. **Long-term memory recalls by meaning.** "Which foods should I avoid?" shares no keyword with "I'm allergic to peanuts and tree nuts", yet the search returns it for Priya. The search is a vector search, and `similarityThreshold` is its cosine similarity floor, the same name the LangCache column tunes.
    2. **The `ownerId` filter scopes recall to one customer.** The app sends `filter: {ownerId: ...}` with every search, so Tom's agent does not see Priya's allergy.
    3. **The app builds the prompt.** Each turn writes the message to session memory, searches long-term memory, and puts what it recalls in the system prompt.

    Embeddings: Agent Memory embeds inside the service. It stores each long-term memory with a vector embedding and searches those vectors, so the app sends text and never a vector.
    The service's own AI model settings, which cover embedding, extraction, and summarization, are chosen in the console when you create it: Redis-managed keys by default, or your own provider key and models.
    The embedding model is fixed after creation, and `LLM_API_KEY` is only the chat's key.

    Settings: `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY`, plus `LLM_API_KEY` for the chat.
    Docs: [Agent Memory](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/overview/),
    [Python SDK quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/), and
    [developer guide](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/developer-guide/).

    Each line reads source, target, and what passes between them:

    ```text
    App        -> long-term   POST /long-term-memory, the Seed memories, each with an ownerId
    App        -> session     POST /session-memory/events, the user's message, every turn
    App        -> long-term   POST /long-term-memory/search: text, similarityThreshold, ownerId
    long-term  -> App         the memories at or above the threshold, best first, no score
    App        -> LLM         the system prompt with the recalled memories, and the history
    session    -> long-term   extraction in the background, a few minutes after a turn
    ```
    """)
    return


@app.cell(hide_code=True)
def am_store_button(ams_client, mo):
    _ = ams_client  # the buttons appear once the client exists, so a press never runs ahead of the settings check
    ams_seed_button = mo.ui.run_button(label="Seed")
    ams_refresh_button = mo.ui.run_button(label="Refresh")
    ams_cleanup_button = mo.ui.run_button(label="Clean up", kind="danger")

    mo.vstack(
        [
            mo.md(r"""
    ## Seed and list two customers' long-term memories

    Seed writes three memories per customer with `POST /long-term-memory`, each tagged with the `ownerId` the app chooses.
    Refresh lists them again, including anything the store has extracted from a session a few minutes after a turn.
    Seeds carry type `semantic`. An extracted memory carries the type the extractor chooses, such as `episodic`.
    Clean up deletes every session and every long-term memory tagged Tom or Priya.
    """),
            mo.hstack([ams_seed_button, ams_refresh_button, ams_cleanup_button], justify="start"),
        ]
    )
    return ams_cleanup_button, ams_refresh_button, ams_seed_button


@app.cell(hide_code=True)
async def am_store(
    ams_chatbot,
    ams_cleanup_button,
    ams_forget,
    ams_memories,
    ams_memory_table,
    ams_refresh_button,
    ams_seed,
    ams_seed_button,
    ams_seeds,
    mo,
):
    _note = ""
    if ams_seed_button.value:
        _ids = await ams_seed()
        _note = f"Wrote {len(_ids)} new memories to long-term memory." if _ids else "Every seed memory is already in long-term memory."
    if ams_cleanup_button.value:
        _sessions, _memories = 0, 0
        for _owner in ams_seeds:
            _deleted_sessions, _deleted_memories = await ams_forget(_owner)
            _sessions += len(_deleted_sessions)
            _memories += len(_deleted_memories)
        _note = f"Deleted {_memories} memories and {_sessions} sessions for {' and '.join(ams_seeds)}."
    _ = ams_refresh_button.value  # Refresh lists again, for memories extracted in the background
    _ = ams_chatbot.value  # and so does each chat turn, so the tables agree with the bars

    ams_store = {}
    for _owner in ams_seeds:
        ams_store[_owner] = await ams_memories(_owner)

    mo.vstack(
        ([mo.md(_note)] if _note else [])
        + [
            mo.hstack(
                [ams_memory_table(_owner, _found) for _owner, _found in ams_store.items()],
                widths="equal",
                align="start",
            )
        ]
    )
    return (ams_store,)


@app.cell(hide_code=True)
def am_owner(ams_seeds, mo):
    ams_owner = mo.ui.dropdown(options=list(ams_seeds), value=next(iter(ams_seeds)), label="Chat as")  # displayed in the workbench, upstream of the chat so a switch starts a new session

    mo.md(r"""
    ## Chat with the Cairn agent as Tom or Priya and watch the recall

    Type `/` in the chat box to pick a starter message. Send the first one as Tom. The agent has nothing on food, and the Priya bars show the allergy the `filter` kept out.
    Send the next two as Tom, and his dogs and his Dolomites trip appear in the system prompt.

    Switch to Priya, which starts a new conversation, and send the same three. The foods question now recalls the allergy, with no keyword shared.
    Move `similarityThreshold` and watch memories enter and leave the system prompt.

    Send the packing question next. Alone it is a weak match, so set N to 2 and the trip question rides along in the search text.
    The last message tells the agent something new. A few minutes later, Refresh can list a memory the store extracted from it.
    """)
    return (ams_owner,)


@app.cell(hide_code=True)
def am_workbench(
    ams_bars,
    ams_build_system,
    ams_chatbot,
    ams_get_threshold,
    ams_get_turns,
    ams_owner,
    ams_request,
    ams_session_id,
    ams_threshold,
    ams_turn_bands,
    ams_turn_events,
    ams_turn_live,
    ams_turn_query,
    ams_turns,
    json,
    mo,
):
    _t = round(ams_get_threshold(), 2)
    _n = ams_get_turns()
    _owner = ams_owner.value
    _shown = min(_n, sum(m.role == "user" for m in ams_chatbot.value))
    _recalled = [text for text, band in ams_turn_bands[_owner].items() if round(band, 2) >= _t]

    _data = [
        ams_turns,
        mo.md(
            ("**Search text**, the last user message:" if _shown == 1 else f"**Search text**, the last {_shown} user messages, oldest first:")
            if ams_turn_live
            else "**Search text**, the first starter message until you send one:"
        ),
        mo.md(f"```text\n{ams_turn_query}\n```"),
        mo.md(
            "Each bar is the highest `similarityThreshold` that returns the memory, found by running this search at 21 thresholds. "
            "The API returns no score. Dark bars are at or above the slider's line, and those memories go into the system prompt."
        ),
        ams_threshold,
        mo.md(f"**ownerId {_owner}**, what the `filter` returns"),
        ams_bars(ams_turn_bands[_owner], _t),
    ]
    for _other in (o for o in ams_turn_bands if o != _owner):
        _data += [mo.md(f"**ownerId {_other}**, the same search for comparison. The agent never sends it."), ams_bars(ams_turn_bands[_other], _t)]
    _data += [
        mo.md("---"),
        mo.md("Moving a slider redraws this pane without a new turn."),
        mo.md("**Search request**, from `ams_request`"),
        mo.md(f"```json\n{json.dumps(ams_request(ams_turn_query, _owner, _t), indent=2)}\n```"),
        mo.md("**System prompt**, from `ams_build_system`"),
        mo.md(f"```text\n{ams_build_system(_owner, _recalled)}\n```"),
        mo.md(
            f"**Session `{ams_session_id}`**, the user messages the app wrote ({len(ams_turn_events)})"
            if ams_turn_events
            else f"**Session `{ams_session_id}`**, no messages written yet"
        ),
    ]
    if ams_turn_events:
        _data.append(mo.md("\n".join(f"- {text}" for text in ams_turn_events)))

    mo.hstack(
        [
            mo.vstack([ams_owner, ams_chatbot]),
            mo.vstack(_data),
        ],
        widths=[5, 6],
        gap=1.5,
        align="start",
    )
    return


@app.cell(hide_code=True)
def am_build_md(mo):
    mo.md(r"""
    ## The Agent Memory code: client, store, prompt, turn

    One cell per step, with the code visible in `marimo edit`. In `marimo run`, pick **Show code** from the app menu.
    Every call on `ams_client` goes to Agent Memory, and the rest is the app. Seed and Clean up call the store functions, and each chat turn calls `ams_turn`.
    """)
    return


@app.cell
def am_connect(os):
    # Client. Three settings from .env, and one object for every Agent Memory call below.
    from redis_agent_memory import AgentMemory, models


    ams_client = AgentMemory(
        os.environ["MEMORY_API_BASE_URL"],
        store_id=os.environ["MEMORY_STORE_ID"],
        api_key=os.environ["MEMORY_API_KEY"],
    )
    return ams_client, models


@app.cell
def am_store_fns(ams_client, asyncio, models):
    # Store. Seed, list, and delete the long-term memories of two customers.
    ams_seeds = {
        "Tom": [
            "I've got two dogs, Max and Cece",
            "I wear size 11 trail shoes",
            "I'm planning a hiking trip in the Dolomites in August",
        ],
        "Priya": [
            "I have a cat named Miso",
            "I'm allergic to peanuts and tree nuts",
            "I'm planning a canoe trip in Maine in June",
        ],
    }


    async def ams_memories(owner: str) -> list[models.MemoryRecord]:
        """Every long-term memory whose `ownerId` is `owner`, up to 100. With no `text`, the search endpoint lists by filter alone."""
        r = await ams_client.search_long_term_memory_async(
            request={"filter": {"ownerId": {"eq": owner}}, "limit": 100}
        )
        return r.items


    async def ams_seed() -> list[str]:
        """Write memories straight to long-term memory, each tagged with the `ownerId` the app chooses (the write side of principle 2).
        The ids are the app's too, so a retry or a second Seed rewrites the same records and creates nothing new."""
        created: list[str] = []

        for owner, texts in ams_seeds.items():
            before = {m.id for m in await ams_memories(owner)}
            memories = [
                {"id": f"{owner.lower()}-seed-{i}", "text": text, "ownerId": owner}  # no memoryType, so the store files it as "semantic"
                for i, text in enumerate(texts, 1)
            ]
            await ams_client.bulk_create_long_term_memories_async(memories=memories)
            created += [m["id"] for m in memories if m["id"] not in before]

        return created


    async def ams_forget(owner: str) -> tuple[list[str], list[str]]:
        """Delete every session and every long-term memory about `owner`. Returns the session ids and the memory ids deleted."""
        sessions = await ams_client.list_sessions_async(filter_owner_id=owner)
        await asyncio.gather(*(ams_client.delete_session_memory_async(session_id=s) for s in sessions.items))

        memory_ids = [m.id for m in await ams_memories(owner)]
        if memory_ids:
            await ams_client.bulk_delete_long_term_memories_async(memory_ids=memory_ids)

        return sessions.items, memory_ids

    return ams_forget, ams_memories, ams_seed, ams_seeds


@app.cell
def am_prompt_fns(ChatMessage):
    # Prompt. The search text, the search request, and the system prompt the app builds.
    def ams_query(messages: list[ChatMessage], n: int) -> str:
        """The text to search with: the last `n` user messages, in order."""
        user_messages = [str(m.content) for m in messages if m.role == "user"]
        return "\n".join(user_messages[-n:])


    def ams_request(query: str, owner: str, threshold: float) -> dict:
        """The search request. `text` and `similarityThreshold` match by meaning (principle 1), `filter` scopes recall to one `ownerId` (principle 2), and `limit` caps the results at ten."""
        return {
            "text": query,
            "similarityThreshold": threshold,
            "filter": {"ownerId": {"eq": owner}},
            "limit": 10,
        }


    def ams_build_system(owner: str, facts: list[str]) -> str:
        """The system prompt. The app, not Agent Memory, decides what the model sees (principle 3)."""
        system = "You are the Cairn Outfitters support agent. Answer the customer in two or three sentences."

        if facts:
            system += f"\n\nWhat you remember about {owner}:\n" + "\n".join(f"- {fact}" for fact in facts)

        return system

    return ams_build_system, ams_query, ams_request


@app.cell
def am_turn_fn(
    AI_MODEL,
    ChatMessage,
    ChatTurn,
    ams_build_system,
    ams_client,
    ams_owner,
    ams_query,
    ams_request,
    ams_settings,
    dt,
    models,
    stream_llm,
    uuid,
):
    # Turn. One chat turn: the session write, the long-term search, and the prompt.
    ams_session_id = f"{ams_owner.value.lower()}-{uuid.uuid4().hex[:12]}"  # a new session each time the customer changes


    async def ams_turn(messages: list[ChatMessage]) -> ChatTurn:
        """One agent turn: write the message to session memory, search long-term memory, and put what it recalls in the system prompt."""
        owner = ams_owner.value  # the customer from the dropdown
        threshold, turns = ams_settings()  # the two sliders, read when you send

        # Session memory: the user's message, as an event in this session.
        # The first event's actorId becomes the session's ownerId, and the memories the store extracts from the session carry it.
        # Extraction runs a few minutes later. The prompt below never reads these events.
        await ams_client.add_session_event_async(
            session_id=ams_session_id,
            actor_id=owner,
            role=models.MessageRole.USER,
            content=[{"text": str(messages[-1].content)}],
            created_at=dt.datetime.now(dt.timezone.utc),
        )

        # Long-term memory: this owner's memories, by meaning, searched with the last N user messages.
        found = await ams_client.search_long_term_memory_async(request=ams_request(ams_query(messages, turns), owner, threshold))

        # The prompt: the app puts the recalled memories in front of the model.
        system = ams_build_system(owner, [m.text for m in found.items])
        async for text in stream_llm(system, messages, AI_MODEL):
            yield text

    return ams_session_id, ams_turn


@app.cell(hide_code=True)
def am_machinery_md(mo):
    mo.md(r"""
    ## Notebook plumbing: slider state, chat object, threshold sweep, renderers

    Four hidden cells follow: slider state, chat object, the threshold sweep behind the bars, and the renderers for bars and tables.
    Read `am_settings`, `am_chat`, `am_turn_sweep`, and `am_render` in `iris_basics.py`, or expand them in `marimo edit`.
    """)
    return


@app.cell(hide_code=True)
def am_settings(mo):
    # Slider state. The sliders live in am_chat, so a turn cannot read their .value from inside the chat cell.
    # A cell that references these getters reruns on every move, so the turn reads them through ams_settings() instead.
    # am_chat reads the getters for the sliders' start values, and marimo exempts the cell that owns a control.
    ams_get_threshold, ams_set_threshold = mo.state(0.3)
    ams_get_turns, ams_set_turns = mo.state(1)


    def ams_settings() -> tuple[float, int]:
        """The sliders' current values, read at call time. A cell that calls this does not rerun when a slider moves."""
        return round(ams_get_threshold(), 2), ams_get_turns()

    return (
        ams_get_threshold,
        ams_get_turns,
        ams_set_threshold,
        ams_set_turns,
        ams_settings,
    )


@app.cell(hide_code=True)
def am_chat(
    ams_get_threshold,
    ams_get_turns,
    ams_set_threshold,
    ams_set_turns,
    ams_turn,
    mo,
):
    # debounce=True: the setter fires when the slider is released, not on every step.
    ams_threshold = mo.ui.slider(
        start=0.0, stop=1.0, step=0.05, value=ams_get_threshold(), show_value=True, debounce=True,
        label="similarityThreshold", on_change=ams_set_threshold,
    )
    ams_turns = mo.ui.slider(
        start=1, stop=10, step=1, value=ams_get_turns(), show_value=True, debounce=True,
        label="Search with the last N user messages", on_change=ams_set_turns,
    )
    ams_prompts = [
        "Which foods should I avoid?",
        "What pets do I have?",
        "Where am I headed this summer?",
        "Any packing tips?",
        "I'm training for a marathon in October.",
    ]


    async def _turn(messages):
        """ams_turn, with an error turned into the last line of the reply. mo.ui.chat keeps a turn, and the pane redraws, only when it ends with text."""
        try:
            async for text in ams_turn(messages):
                yield text
        except Exception as error:
            yield f"\n\n*The turn stopped with {type(error).__name__}. The pane beside the chat shows what the session and the search hold.*"

    # Displayed beside the data in am_workbench. ams_turn is the Iris turn above.
    ams_chatbot = mo.ui.chat(_turn, prompts=ams_prompts, max_height=520)
    return ams_chatbot, ams_prompts, ams_threshold, ams_turns


@app.cell(hide_code=True)
async def am_turn_sweep(
    ams_chatbot,
    ams_client,
    ams_get_turns,
    ams_prompts,
    ams_query,
    ams_request,
    ams_session_id,
    ams_store,
    asyncio,
    models,
):
    from redis_agent_memory import errors  # NotFoundErrorResponseContent, for a session Clean up deleted


    async def ams_sweep(query: str, store: dict[str, list[models.MemoryRecord]]) -> dict[str, dict[str, float]]:
        """For each owner in `store`, the highest threshold that returns each memory. The API returns no score."""
        grid = [round(0.05 * i, 2) for i in range(21)]  # 0.00 to 1.00
        pairs = [(owner, t) for owner in store for t in grid]
        gate = asyncio.Semaphore(12)  # 42 searches per sweep; keep a dozen in flight

        async def recall(owner: str, t: float) -> list[models.MemoryRecord]:
            async with gate:
                r = await ams_client.search_long_term_memory_async(request={**ams_request(query, owner, t), "limit": 100})
                return r.items

        hits = await asyncio.gather(*(recall(owner, t) for owner, t in pairs))

        bands = {owner: {m.text: 0.0 for m in records} for owner, records in store.items()}
        for (owner, t), found in zip(pairs, hits):
            for m in found:
                bands[owner][m.text] = max(bands[owner].get(m.text, 0.0), t)

        return bands


    async def ams_session_texts(session_id: str) -> list[str]:
        """The user messages the app wrote to this session, or none when the session is gone (Clean up deletes it)."""
        try:
            session = await ams_client.get_session_memory_async(session_id=session_id)
        except errors.NotFoundErrorResponseContent:
            return []
        return [e.content[0].text for e in session.events]


    # The search text behind the last turn (the first starter prompt before any chat), how high a threshold each
    # memory clears for it, for every customer, and the session so far. Depends on the chat, N, and the store, not on the threshold.
    ams_turn_live = any(m.role == "user" for m in ams_chatbot.value)
    ams_turn_query = ams_query(ams_chatbot.value, ams_get_turns()) if ams_turn_live else ams_prompts[0]
    ams_turn_bands = await ams_sweep(ams_turn_query, ams_store)
    ams_turn_events = await ams_session_texts(ams_session_id) if ams_turn_live else []
    return ams_turn_bands, ams_turn_events, ams_turn_live, ams_turn_query


@app.cell(hide_code=True)
def am_render(mo, models):
    import html  # escapes each memory's text inside the bars


    def ams_bars(bands: dict[str, float], threshold: float) -> mo.Html:
        """One row per memory, in store order."""
        threshold = round(threshold, 2)
        rows = []
        for text, band in bands.items():
            state = "ams-bar" if round(band, 2) >= threshold else "ams-bar ams-bar-below"
            rows.append(
                '<div class="ams-row">'
                f'<div class="ams-label" title="{html.escape(text)}">{html.escape(text)}</div>'
                '<div class="ams-track">'
                f'<div class="{state}" style="width:{band * 100:.0f}%"></div>'
                f'<div class="ams-line" style="left:{threshold * 100:.0f}%"></div>'
                "</div>"
                f'<div class="ams-value">{band:.2f}</div>'
                "</div>"
            )
        ticks = '<div class="ams-row"><div></div><div class="ams-ticks"><span>0</span><span>0.5</span><span>1</span></div><div></div></div>'
        body = "".join(rows) + ticks if rows else '<div class="ams-empty">No memories yet. Select Seed to write them.</div>'
        css = """
    <style>
    .ams-bars { font: 13px system-ui, -apple-system, "Segoe UI", sans-serif; color: #52514e; }
    .ams-row { display: grid; grid-template-columns: minmax(0, 2fr) 3fr 3.2em; gap: 0 8px; align-items: center; margin: 6px 0; }
    .ams-label { overflow-wrap: anywhere; }
    .ams-track { position: relative; min-width: 0; height: 12px; background: #e1e0d9; border-radius: 0 4px 4px 0; }
    .ams-bar { position: absolute; left: 0; top: 0; height: 100%; background: #2a78d6; border-radius: 0 4px 4px 0; }
    .ams-bar-below { background: #9ec5f4; }
    .ams-line { position: absolute; top: -4px; height: 20px; width: 2px; background: #0b0b0b; }
    .ams-value { color: #0b0b0b; font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
    .ams-ticks { display: flex; justify-content: space-between; color: #898781; font-size: 11px; border-top: 1px solid #c3c2b7; padding-top: 2px; }
    .ams-empty { color: #898781; }
    </style>
    """
        return mo.Html('<div class="ams-bars">' + body + "</div>" + css)


    def ams_memory_table(owner: str, found: list[models.MemoryRecord]) -> mo.Html:
        """One owner's long-term memories as a markdown table, headed with the `ownerId`."""
        head = f"**ownerId {owner}** ({len(found)})"
        if not found:
            return mo.md(head + "\n\n_No memories yet._")
        rows = [f"| {m.text.replace('|', '&#124;')} | {m.memory_type} |" for m in found]
        return mo.md("\n".join([head, "", "| memory | type |", "|---|---|", *rows]))

    return ams_bars, ams_memory_table


if __name__ == "__main__":
    app.run()
