import marimo

__generated_with = "0.24.2"
app = marimo.App(width="columns", app_title="Redis Iris basics")


@app.cell(column=0, hide_code=True)
def prologue(CAST, mo):
    mo.md(f"""
    # Cairn Outfitters: a support assistant, one Redis Iris service at a time

    Cairn Outfitters, a made-up outdoor-gear shop, is launching an AI support assistant. You build it in four chapters. Each chapter adds a Redis Iris service, and the gap it leaves is what the next one fixes.

    ## The cast

    - **Priya**, customer `{CAST['Priya']}`, leaves for a canoe trip in Maine in three weeks. Her tent is still on order.
    - **Tom**, customer `{CAST['Tom']}`, is the second customer, here whenever a lesson needs two people.

    Each customer id is how later chapters find a customer's records (Chapter 3) and keep one customer's memories apart from another's (Chapter 4).

    ## The chapters

    1. **Search** (Redis Search, in your database) finds the help article closest in meaning to what a customer types. But an article isn't an answer.
    2. **Semantic caching** (Redis LangCache, a hosted service) has a model write the answer from those articles, and reuses it when the question comes in other words. But a cache shared by everyone can't answer about Priya's own order.
    3. **Context engineering and retrieval** (Redis Context Retriever, a hosted service) gives the model Priya's orders and products through tools. But a week later, a new chat has forgotten what she told it.
    4. **Agent memory** (Redis Agent Memory, a hosted service) keeps what customers say and recalls it by meaning, so the assistant remembers Priya.

    ## How to run it

    Check the settings below first. Then work through the columns from left to right, one chapter each: the next chapter starts at the top of the column to the right. Each chapter builds on the last, so start with **Create** in Chapter 1. A chapter's buttons (**Create**, **Ask**, **Seed**) do its work. **Clean up everything** is at the end of Chapter 4. Cells that only run a request or draw a table hide their code, and the functions each chapter builds on show it.
    """)
    return


@app.cell
def imports():
    # Common imports for Notebook work
    import asyncio
    import datetime as dt
    import json
    import time
    import uuid

    # Notebook driver
    import marimo as mo

    # Notebook-local data support, typing information, or internal machinery.
    from cairn_support.sample_data import CAST
    from cairn_support.sessions import json_request, json_session, recording
    from cairn_support.types import ChatMessage, ChatTurn, Reply

    return (
        CAST,
        ChatMessage,
        ChatTurn,
        Reply,
        asyncio,
        dt,
        json,
        json_request,
        json_session,
        mo,
        recording,
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

    # 2. One table row per setting: a check or a cross in the theme's colours, the name, and where to find it.
    _check = '<span style="color: var(--iris-ok)">&#10003;</span>'
    _cross = '<span style="color: var(--iris-danger)">&#10007;</span>'
    _rows = ["| Set | Variable | Where to find it |", "|:-:|:--|:--|"]
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
    # stream_llm streams a reply. answer_with_tools also runs the tool calls the model asks for (Chapter 3 on).
    from cairn_support.llm import answer_with_tools, stream_llm


    # The chat model that writes every answer in Chapters 2 to 4.
    # Supported models by OpenAI, Anthropic, Google (gemini)
    AI_MODEL = "gpt-4o-mini"
    return AI_MODEL, answer_with_tools, stream_llm


@app.cell
def embedding():
    from cairn_support.embedder import embed_texts, load_embedding_model


    # potion-base-8M from the Hugging Face cache, about 30 MB the first time
    EMBEDDING_MODEL = load_embedding_model()
    return EMBEDDING_MODEL, embed_texts


@app.cell(column=1, hide_code=True)
def rs_title(EMBEDDING_MODEL, HELP_ARTICLES, RS_IDX_NAME, RS_KEY_PREFIX, mo):
    _article = HELP_ARTICLES[0]
    _key = f"{RS_KEY_PREFIX}:{_article['id']}"
    _body = _article["body"][:44].rsplit(" ", 1)[0] + " ..."  # the first few words, as a preview
    _dim = EMBEDDING_MODEL.dim

    rs_create_button = mo.ui.run_button(label="Create", tooltip="Create the index and load the articles")

    _story = mo.md(f"""
    # Chapter 1. Search: find the right article

    Before the assistant has a chat model, Cairn's customers search the help center in their own words.

    - Tom types *my trail shoes feel cramped*, and wants the boot-size guide. No article says "cramped". He also tries *Trailhead*, the name of Cairn's boot line.
    - Priya, packing for her canoe trip, mistypes *waterproofng*. She should still get the waterproofing article.
    - Priya then searches *Where is my parcel?*, because her tent hasn't arrived.

    **Redis Search** answers all three. It's an index over Redis keys that can match words, labels, numbers, and meaning. It runs inside your Redis database. You talk to it with Redis commands, which this chapter writes the way you'd type them into `redis-cli`, Redis's command-line tool. The notebook sends the same text from Python, so you never need a terminal.

    ## You'll learn how to

    1. Declare an index, and see that writing a document indexes it.
    2. Find articles by meaning with `KNN`.
    3. Set a similarity floor with `VECTOR_RANGE`, so "nothing matches" is an answer.
    4. Filter before ranking, with a rule on `year`.

    ## The articles

    Each of the {len(HELP_ARTICLES)} help articles is one Redis **hash**: a key that holds named fields, like a small dictionary. Besides a title and body, each has a category and the `year` it was written. The key is the prefix `{RS_KEY_PREFIX}:` plus the article's id.

    ```json
    {{
      "{_key}": {{
        "title": "{_article['title']}",
        "body": "{_body}",
        "category": "{_article['category']}",
        "year": "{_article['year']}",
        "embedding": "OSgjvXX2dD7aP5M9maqqv..."
      }}
    }}
    ```

    The `embedding` holds {_dim} numbers that capture what the article *means*. An embedding model that runs on this machine makes them from the title and body.
    """)

    rss_create_command = f"""FT.CREATE {RS_IDX_NAME}
      ON HASH
      PREFIX 1 {RS_KEY_PREFIX}:
    SCHEMA
      title      TEXT     WEIGHT 2.0
      body       TEXT
      category   TAG
      year       NUMERIC  SORTABLE
      embedding  VECTOR   HNSW 6
        TYPE FLOAT32
        DIM {_dim}
        DISTANCE_METRIC COSINE"""

    _schema = mo.md(f"""
    ## Step 1. Declare the schema

    > Index every hash whose key starts with `{RS_KEY_PREFIX}:`, and say what each field is for.

    ```bash
    {rss_create_command}
    ```

    Reading it piece by piece:

    - `ON HASH` indexes hashes. `PREFIX 1 <prefix>` picks the keys. A number after a keyword counts the items that follow: the **1** counts the prefixes, and in `HNSW 6` the **6** counts `TYPE`, `DIM`, and `DISTANCE_METRIC`, each with its value.
    - `title` and `body` are `TEXT`, so Redis can search their words. `WEIGHT 2.0` counts a word found in the title double when it scores those searches. It has no effect on vector search.
    - `category` is a `TAG`, an exact label. `year` is `NUMERIC`, so Redis can match a range, and `SORTABLE` keeps it ready for sorting.
    - `embedding` is a `VECTOR`. `HNSW` finds near neighbors by hopping through a graph, without comparing the question with every article. `TYPE FLOAT32` is 4 bytes per number, `DIM` must equal the embedding model's {_dim} numbers, and `COSINE` compares direction and ignores length.
    """)

    _key_idea = mo.callout(
        mo.md("Each field's type decides what you can ask later: `TEXT` finds words, `TAG` matches exact labels, `NUMERIC` handles ranges, and `VECTOR` finds meaning. A field left out of the schema is still stored, but you can't search on it."),
        kind="info",
        title="Key idea: the schema decides what you can search",
    )

    _write = mo.md(f"""
    ### Writing is indexing

    > Store an article as a hash, and delete it after an hour.

    ```bash
    HSET {_key}
      title "{_article['title']}"
      body "{_body}"
      category {_article['category']}
      year {_article['year']}
      embedding <{_dim * 4} bytes>

    EXPIRE {_key} 3600
    ```

    Reading it piece by piece:

    - `HSET <key>` creates the hash, or updates it. The key is the index's prefix plus the article's id, so Redis indexes the hash the moment it's written. There's no separate "index this" call.
    - `title`, `body`, `category`, and `year` are the schema's fields. Redis stores every value as a string, and the index reads `year` as a number because the schema says `NUMERIC`.
    - `embedding` is the vector: {_dim} numbers at 4 bytes each, {_dim * 4} raw bytes, to match `TYPE FLOAT32` and `DIM {_dim}`. A value that doesn't fit its field, such as an embedding of the wrong length, raises no error. Redis stores the hash and leaves it out of the index, and `FT.INFO` counts it under `hash_indexing_failures`.
    - `EXPIRE <key> 3600` deletes the key after an hour (3,600 seconds), and a deleted key leaves the index too. The demo cleans up after itself.
    """)

    _try_it = mo.callout(
        mo.md(f"Press **Create**. It sends `FT.CREATE` first, then an `HSET` and an `EXPIRE` for each of the {len(HELP_ARTICLES)} articles, batched together in one pipeline. A line below says what was written. Press it again whenever you like: the index already exists, and the articles are rewritten with a fresh hour."),
        kind="success",
        title="Try it",
    )

    mo.vstack([_story, _schema, _key_idea, _write, _try_it, rs_create_button], gap=0.75)
    return rs_create_button, rss_create_command


@app.cell(hide_code=True)
def rs_create(
    HELP_ARTICLES,
    REDIS_CLIENT,
    RS_IDX_NAME,
    RS_KEY_PREFIX,
    mo,
    redis,
    rs_create_button,
    rss_create_command,
    rss_vector,
    shlex,
):
    # Create. On a press: FT.CREATE, then every article as a hash with an hour to live. On every run: FT.INFO, which the search steps check.
    if rs_create_button.value:
        try:
            REDIS_CLIENT.execute_command(*shlex.split(rss_create_command))
        except redis.ResponseError as _error:
            if "already exists" not in str(_error):  # pressing Create again is fine
                raise
        with REDIS_CLIENT.pipeline(transaction=False) as _pipe:
            for _article in HELP_ARTICLES:
                _key = f"{RS_KEY_PREFIX}:{_article['id']}"
                _fields = {_name: _value for _name, _value in _article.items() if _name != "id"}  # the id is in the key
                _fields["embedding"] = rss_vector(f"{_article['title']}. {_article['body']}")
                _pipe.hset(_key, mapping=_fields)
                _pipe.expire(_key, 3600)
            _pipe.execute()

    rss_index_info = None  # stays None while there is no index
    try:
        _info = REDIS_CLIENT.execute_command("FT.INFO", RS_IDX_NAME)
        rss_index_info = _info if isinstance(_info, dict) else dict(zip(_info[::2], _info[1::2]))  # RESP3 is a map, RESP2 a flat list
    except redis.ResponseError:
        pass
    _docs = int(rss_index_info["num_docs"]) if rss_index_info else 0

    if rs_create_button.value:
        _line = f"Wrote **{len(HELP_ARTICLES)}** articles under `{RS_KEY_PREFIX}:` with `EXPIRE 3600`. `{RS_IDX_NAME}` holds **{_docs}** documents."
    elif _docs:
        _line = f"`{RS_IDX_NAME}` holds **{_docs}** documents."
    else:
        _line = "No articles are indexed yet. Press **Create** in step 1."
    mo.md(_line)
    return (rss_index_info,)


@app.cell
def rs_search_fn(
    CAIRN_SCOPE,
    EMBEDDING_MODEL,
    REDIS_CLIENT,
    embed_texts,
    shlex,
):
    # Search. Names the index and the articles, and defines the one function every later chapter calls (the KNN query, written as text the way redis-cli takes it). Create, in the hidden cell above, does the work.
    from cairn_support.redis_search_replies import ft_search_rows  # reads an FT.SEARCH reply, RESP2 or RESP3, as one dict per hit
    from cairn_support.sample_data import HELP_ARTICLES


    RS_KEY_PREFIX = f"cairn:{CAIRN_SCOPE}:help"  # one hash per article, at <prefix>:<article id>
    RS_IDX_NAME = f"idx:{RS_KEY_PREFIX}"


    def rss_vector(text: str) -> bytes:
        """The text's embedding as raw FLOAT32 bytes, the form the hash stores and PARAMS takes."""
        return embed_texts(EMBEDDING_MODEL, [text])[0].astype("float32").tobytes()


    def rss_search(question: str, k: int = 3, where: str = "*") -> list[dict]:
        """KNN over the help center: the `k` articles closest in meaning to `question` among those `where` lets in, best first. Raises redis.ResponseError when the index doesn't exist."""
        command = f"""
        FT.SEARCH {RS_IDX_NAME} "({where})=>[KNN {k} @embedding $vector AS vector_score]"
             RETURN  5  title body category year vector_score
             SORTBY     vector_score
             DIALECT 2
             PARAMS  2  vector
        """
        reply = REDIS_CLIENT.execute_command(*shlex.split(command), rss_vector(question))
        return ft_search_rows(reply)[1]

    return (
        HELP_ARTICLES,
        RS_IDX_NAME,
        RS_KEY_PREFIX,
        ft_search_rows,
        rss_search,
        rss_vector,
    )


@app.cell(hide_code=True)
def rs_knn_md(EMBEDDING_MODEL, RS_IDX_NAME, mo):
    rs_meaning_pick = mo.ui.dropdown(
        options={
            "Tom: my trail shoes feel cramped": "my trail shoes feel cramped",
            "Tom: Trailhead": "Trailhead",
            "Priya: waterproofng": "waterproofng",
        },
        value="Tom: my trail shoes feel cramped",
        label="Who asks",
    )

    _intro = mo.md(f"""
    ## Step 2. Search by meaning with `KNN`

    Tom's question shares no word with the article he needs. So the notebook embeds the question with the same embedding model that embedded the articles, and asks Redis for the stored embeddings **closest** to it. That is a k-nearest-neighbors search, `KNN`. Close embeddings mean similar meaning, even when the texts share no words. The `rss_search` function above sends this command, and every later chapter calls it.

    > Among all articles, find the 3 closest in meaning to the question.

    ```bash
    FT.SEARCH {RS_IDX_NAME} "(*)=>[KNN 3 @embedding $vector AS vector_score]"
         RETURN  5  title body category year vector_score
         SORTBY     vector_score
         DIALECT 2
         PARAMS  2  vector <{EMBEDDING_MODEL.dim * 4} bytes>
    ```

    Reading it piece by piece:

    - `(*)` is the **filter**: which articles take part, and `*` means all of them. `=>` passes them to the vector search on its right.
    - `[KNN 3 @embedding $vector AS vector_score]` finds the **3** articles whose `embedding` is nearest to `$vector`, and names each distance `vector_score`.
    - `RETURN 5 ...` sends back these **5** fields and leaves the embedding behind. `SORTBY vector_score` puts the closest article first.
    - `DIALECT 2` turns on the query syntax that understands `=>[KNN ...]` and `$` parameters.
    - `PARAMS 2 vector <bytes>` fills in `$vector`. The **2** counts the items that follow: the name `vector` and its value, the question's embedding as raw `FLOAT32` bytes. Bytes can't be typed into a command, so the notebook appends them as one extra argument.
    """)

    _key_idea = mo.callout(
        mo.md("Redis measures closeness with the metric the index declared in step 1, `DISTANCE_METRIC COSINE`, and returns it as **`vector_score`**, a cosine distance. **0** means the same meaning, around **1** means unrelated, and **lower is closer**. `KNN 3` always returns three articles, even when none of them is a good match."),
        kind="info",
        title="Key idea: nearest neighbors",
    )

    _try_it = mo.callout(
        mo.md("""
    Pick each question and watch where its target article lands.

    - **Tom: my trail shoes feel cramped.** **Finding your boot size** comes first, at 0.5326, though no article contains "cramped" or "shoes". The next two score 0.7250 and 0.7351.
    - **Priya: waterproofng.** **Restoring waterproofing on shells and boots** still comes first, at 0.5219. The embedding model splits words into pieces, and `water` and `proof` survive the typo.
    - **Tom: Trailhead.** **Resoling worn boots**, the only article that names the Trailhead boots, comes third, at 0.8677, barely behind the first (0.8549). An embedding sums up all of an article's text, so one product name barely moves it. Exact names are a job for full-text search on the `TEXT` fields: `FT.SEARCH <index> "Trailhead"`.
    """),
        kind="success",
        title="Try it",
    )

    mo.vstack([_intro, _key_idea, _try_it, rs_meaning_pick], gap=0.75)
    return (rs_meaning_pick,)


@app.cell(hide_code=True)
def rs_knn(mo, redis, rs_meaning_pick, rss_index_info, rss_search, rss_table):
    _hint = mo.md("_No articles to search yet. Press **Create** in step 1._")
    mo.stop(not rss_index_info or not int(rss_index_info["num_docs"]), _hint)
    try:
        _rows = rss_search(rs_meaning_pick.value)
    except redis.ResponseError as _error:  # Clean up everything dropped the index, and rss_index_info doesn't know yet
        mo.stop("index" in str(_error).lower(), _hint)
        raise
    mo.stop(not _rows, mo.md("_No articles came back. They expire an hour after Create, so press **Create** again in step 1._"))
    rss_table(_rows, f"`KNN 3` for *{rs_meaning_pick.value}*")
    return


@app.cell(hide_code=True)
def rs_floor_md(EMBEDDING_MODEL, RS_IDX_NAME, mo):
    rs_floor_slider = mo.ui.slider(start=0.0, stop=1.0, step=0.05, value=0.4, show_value=True, debounce=True, label="Similarity floor (cosine similarity)")

    _intro = mo.md(f"""
    ## Step 3. Set a similarity floor with `VECTOR_RANGE`

    `KNN` always returns its 3, so it can't say that nothing matches. Tom's *Trailhead* got three articles in step 2, and every one was a weak match. `VECTOR_RANGE` asks a different question: not "the 3 closest articles" but "every article within this distance". The answer can be **no articles at all**.

    > Among all articles, find up to 5 within 0.60 cosine distance of the question.

    ```bash
    FT.SEARCH {RS_IDX_NAME} "@embedding:[VECTOR_RANGE $radius $vector]=>{{$YIELD_DISTANCE_AS: vector_score; $EPSILON: 0.5}}"
         RETURN  5  title body category year vector_score
         SORTBY     vector_score
         LIMIT   0  5
         DIALECT 2
         PARAMS  4  radius  0.60  vector <{EMBEDDING_MODEL.dim * 4} bytes>
    ```

    What's new since step 2:

    - `@embedding:[VECTOR_RANGE $radius $vector]` keeps every article whose `embedding` is within `$radius` of `$vector`. It's a filter on its own, so no `(*)=>` comes before it.
    - `=>{{...}}` sets options for that range. `$YIELD_DISTANCE_AS` names the distance, as `AS` did in step 2. `$EPSILON` is an accuracy setting, explained below.
    - `LIMIT 0 5` skips no hits and returns at most **5**.
    - `PARAMS 4` counts four items now, because there are two parameters: `radius` with its value, and `vector` with the question's bytes.
    - The slider is a **similarity floor**, where higher is stricter. `$radius` is a **distance**, where lower is stricter. For cosine, distance is `1 - similarity`, so the notebook sends `radius = 1 - floor`: a floor of 0.40 becomes a radius of 0.60.
    """)

    _key_idea = mo.callout(
        mo.md("An empty reply tells the app it has nothing relevant, so it can say so, ask a follow-up question, or hand off to a person, instead of passing off a weak match as the answer. Chapters 2 and 4 use this same floor under another name: LangCache and Agent Memory call it `similarityThreshold`, and an empty reply means nothing cleared it."),
        kind="info",
        title="Key idea: an empty reply is an answer",
    )

    _epsilon = mo.md("""
    The `HNSW` index finds neighbors by hopping through a graph instead of comparing the question with every article. That's fast, but a range search *can* stop early and miss an article near the edge of the radius. `$EPSILON` keeps it exploring candidates up to `radius * (1 + EPSILON)` away, and the default is `0.01`. It doesn't loosen the rule: only articles within `$radius` come back.

    Whether the default misses depends on the index. Right after a fresh index is first loaded, a floor of 0.40 can return nothing for *my trail shoes feel cramped*, although **Finding your boot size** sits at a distance of about 0.53. With `0.5`, Redis finds it. That's why the notebook sends `$EPSILON: 0.5`.
    """)

    _try_it = mo.callout(
        mo.md("""
    The question still comes from the dropdown in step 2.

    - With Tom's *my trail shoes feel cramped*, the slider's starting floor of 0.40 lets one article through: **Finding your boot size**, at a distance of 0.5326. Raise the floor to 0.50, a radius of 0.50, and the reply is empty.
    - Switch the dropdown to Priya's *waterproofng*. At 0.40 only **Restoring waterproofing on shells and boots** gets through (0.5219).
    - Switch to Tom's *Trailhead*. The reply is empty at 0.40, and still empty at 0.30, while `KNN 3` in step 2 returned three articles.
    """),
        kind="success",
        title="Try it",
    )

    mo.vstack([_intro, _key_idea, mo.accordion({"What EPSILON does": _epsilon}), _try_it, rs_floor_slider], gap=0.75)
    return (rs_floor_slider,)


@app.cell(hide_code=True)
def rs_floor(
    REDIS_CLIENT,
    RS_IDX_NAME,
    ft_search_rows,
    mo,
    redis,
    rs_floor_slider,
    rs_meaning_pick,
    rss_index_info,
    rss_table,
    rss_vector,
    shlex,
):
    _hint = mo.md("_No articles to search yet. Press **Create** in step 1._")
    mo.stop(not rss_index_info or not int(rss_index_info["num_docs"]), _hint)
    _radius = round(1 - rs_floor_slider.value, 2)
    _command = f"""
    FT.SEARCH {RS_IDX_NAME} "@embedding:[VECTOR_RANGE $radius $vector]=>{{$YIELD_DISTANCE_AS: vector_score; $EPSILON: 0.5}}"
         RETURN  5  title body category year vector_score
         SORTBY     vector_score
         LIMIT   0  5
         DIALECT 2
         PARAMS  4  radius  {_radius}  vector
    """
    try:
        _indexed = ft_search_rows(REDIS_CLIENT.execute_command("FT.SEARCH", RS_IDX_NAME, "*", "LIMIT", 0, 0))[0]  # a live count: rss_index_info only changes on Create
        mo.stop(not _indexed, mo.md("_No articles came back. They expire an hour after Create, so press **Create** again in step 1._"))
        _reply = REDIS_CLIENT.execute_command(*shlex.split(_command), rss_vector(rs_meaning_pick.value))
    except redis.ResponseError as _error:  # Clean up everything dropped the index, and rss_index_info doesn't know yet
        mo.stop("index" in str(_error).lower(), _hint)
        raise
    rss_table(ft_search_rows(_reply)[1], f"`VECTOR_RANGE` for *{rs_meaning_pick.value}*, floor {rs_floor_slider.value:.2f}, radius {_radius:.2f}")
    return


@app.cell(hide_code=True)
def rs_filter_md(EMBEDDING_MODEL, RS_IDX_NAME, mo):
    rs_year_slider = mo.ui.slider(start=2022, stop=2026, step=1, value=2025, show_value=True, debounce=True, label="Earliest year")

    _intro = mo.md(f"""
    ## Step 4. Filter before ranking

    Priya searches *Where is my parcel?*, because her tent hasn't arrived. Older articles may describe policies that have changed, so the assistant should skip articles from before an earliest year. There are two places to apply that rule, and they don't give the same results.

    > Among articles from 2022 and later, find the 5 closest in meaning to the question.

    ```bash
    FT.SEARCH {RS_IDX_NAME} "(@year:[2022 +inf])=>[KNN 5 @embedding $vector AS vector_score]"
         RETURN  5  title body category year vector_score
         SORTBY     vector_score
         DIALECT 2
         PARAMS  2  vector <{EMBEDDING_MODEL.dim * 4} bytes>
    ```

    Reading it piece by piece:

    - `@year:[2022 +inf]` is a numeric range on the `year` field: 2022 and later, with no upper limit. It works because step 1 declared `year` as `NUMERIC`. The **Earliest year** slider puts its year in place of 2022.
    - It sits left of `=>`, where `*` sat in step 2, so Redis applies it **before** the vector search ranks anything. The rest is as in step 2, with `KNN 5`.
    - The notebook runs the **pre-filter**, the command above, beside a **post-filter**: step 2's command with `KNN 5`, then the app drops older articles in Python.

    ```python
    post = [hit for hit in rss_search(question, k=5) if int(hit["year"]) >= earliest]
    pre = rss_search(question, k=5, where=f"@year:[{{earliest}} +inf]")
    ```
    """)

    _key_idea = mo.callout(
        mo.md("Whatever sits left of `=>` decides which articles may compete, and the KNN on the right ranks only those. Filter **after** the KNN and you can end up with fewer than 5 results, because older articles took slots that newer ones never got a chance at. Filter **before** it and Redis returns 5 whenever at least 5 articles pass the rule. You'll use the same idea twice more: Context Retriever's filter tools in Chapter 3, and the `ownerId` filter on every Agent Memory search in Chapter 4, so one customer's records never crowd out another's."),
        kind="info",
        title="Key idea: filter before ranking",
    )

    _try_it = mo.callout(
        mo.md("""
    The slider starts at 2025. Move the earliest year and compare the two sides.

    - At **2025**, the post-filter keeps 3 of its 5. The pre-filter still returns five, adding **Free shipping threshold** and **Backordered and out of stock items**.
    - At **2022** and **2023**, both sides show the same five articles.
    - At **2026**, the post-filter keeps one, **Missing and overdue shipments**. The pre-filter still fills all five slots, down to weak matches like **Choosing a pack size by torso length**, because KNN returns *k* whenever enough articles pass.
    """),
        kind="success",
        title="Try it",
    )

    mo.vstack([_intro, _key_idea, _try_it, rs_year_slider], gap=0.75)
    return (rs_year_slider,)


@app.cell(hide_code=True)
def rs_filter(
    mo,
    redis,
    rs_year_slider,
    rss_index_info,
    rss_search,
    rss_table,
):
    _hint = mo.md("_No articles to search yet. Press **Create** in step 1._")
    mo.stop(not rss_index_info or not int(rss_index_info["num_docs"]), _hint)
    _earliest = rs_year_slider.value
    _question = "Where is my parcel?"
    try:
        _post = [_hit for _hit in rss_search(_question, k=5) if int(_hit["year"]) >= _earliest]
        _pre = rss_search(_question, k=5, where=f"@year:[{_earliest} +inf]")
    except redis.ResponseError as _error:  # Clean up everything dropped the index, and rss_index_info doesn't know yet
        mo.stop("index" in str(_error).lower(), _hint)
        raise
    mo.stop(not _pre, mo.md("_No articles came back. They expire an hour after Create, so press **Create** again in step 1._"))
    _post_table = rss_table(_post, f"Post-filter: `KNN 5`, then the app drops articles before {_earliest}")
    _pre_table = rss_table(_pre, f"Pre-filter: `@year:[{_earliest} +inf]` before `=>`, then `KNN 5`")
    mo.hstack([_post_table, _pre_table], widths="equal", gap=1.5)
    return


@app.cell(hide_code=True)
def rs_ends(mo):
    _ends_on = mo.md("**Where this stops.** She gets a short list, mostly shipping articles. She wanted an answer, and no article knows where her tent is. In Chapter 2, a model reads these articles and writes the answer.")

    _docs = mo.md("""
    - The [Redis Search overview](https://redis.io/docs/latest/develop/ai/search-and-query/) and the [hashes page](https://redis.io/docs/latest/develop/data-types/hashes/)
    - [`FT.CREATE`](https://redis.io/docs/latest/commands/ft.create/), the [schema definition](https://redis.io/docs/latest/develop/ai/search-and-query/indexing/schema-definition/), and the [field and type options](https://redis.io/docs/latest/develop/ai/search-and-query/indexing/field-and-type-options/)
    - [`HSET`](https://redis.io/docs/latest/commands/hset/), [`EXPIRE`](https://redis.io/docs/latest/commands/expire/), and [indexing hash documents](https://redis.io/docs/latest/develop/ai/search-and-query/indexing/#hash-documents)
    - [`FT.SEARCH`](https://redis.io/docs/latest/commands/ft.search/) and the [vector search guide](https://redis.io/docs/latest/develop/ai/search-and-query/query/vector-search/), which covers `KNN` and `VECTOR_RANGE`
    - [Range queries](https://redis.io/docs/latest/develop/ai/search-and-query/query/range/) and [combined queries](https://redis.io/docs/latest/develop/ai/search-and-query/query/combined/), for other rules you can put before `=>`, such as `@category:{shipping}`
    """)

    mo.vstack([_ends_on, mo.accordion({"Docs for this chapter": _docs})], gap=0.75)
    return


@app.cell(hide_code=True)
def rs_render(mo):
    from html import escape

    from cairn_support.redis_search_replies import ft_search_markdown_table


    def rss_text(text: str) -> mo.Html:
        """Text as the model gets it, in a code block. mo.md would add a blank line before every list item."""
        return mo.Html(f'<span class="markdown prose dark:prose-invert contents"><div class="language-text codehilite"><pre><span></span><code>{escape(text)}</code></pre></div></span>')


    def rss_table(rows: list[dict], caption: str = "") -> mo.Html:
        """The hits as a markdown table (title, category, year, vector_score) under a one-line caption with the hit count."""
        table = ft_search_markdown_table(rows) if rows else "_No article._"
        return mo.md(f"**Hits: {len(rows)}.** {caption}\n\n{table}")

    return rss_table, rss_text


@app.cell(column=2, hide_code=True)
def lc_title(mo):
    _intro = mo.md("""
    # Chapter 2. Semantic caching: answer once, serve many

    Priya wanted an answer, not a list of articles. So Cairn puts a model on top of Chapter 1: search the help center, then let the model write the answer from the articles. It works, but customers ask the same care questions in different words, and each one pays for a model call and the wait for it.

    **Redis LangCache** is a hosted cache that matches by meaning, and unlike Chapter 1's Redis commands, you call it over HTTP. It stores each answer under the question it answered, and a reworded question can reuse it.

    ## You'll learn how to

    1. Put a cache in front of the model: search it first, store the model's answer after, with a TTL (time to live). This is cache-aside.
    2. Set `similarityThreshold`, the similarity floor from Chapter 1, and see what the choice trades.
    """)

    _hops = mo.md("""
    Each line reads source, target, and what passes between them:

    ```text
    App       -> LangCache        POST /entries/search: prompt, similarityThreshold
    LangCache -> embedding model  the model chosen when the service was created
    LangCache -> your Redis       a vector search over the cache's index
    LangCache -> App              200, data: [the best entry at or above the threshold], or data: []
    App       -> your Redis       only when data is empty: FT.SEARCH for the help articles closest to the question (Chapter 1)
    App       -> LLM              only when data is empty: the question, with those articles in the system prompt
    App       -> LangCache        POST /entries: prompt, response, ttlMillis, then 201 with an entryId
    ```
    """)

    _requests = mo.md("""
    ## Step 1. Search the cache before the model

    > Has anyone asked this before?

    ```text
    POST /v1/caches/<LANGCACHE_CACHE_ID>/entries/search
    Authorization: Bearer <LANGCACHE_API_KEY>
    Content-Type: application/json

    {"prompt": "How do I wash my down sleeping bag?", "similarityThreshold": 0.85}
    ```

    Reading it piece by piece:

    - The path holds the cache's id. `Authorization` carries the one credential, which the notebook shows as a placeholder and never prints.
    - `prompt` is the question. LangCache embeds it inside the service and compares it with the stored prompts, so the app sends text, never a vector.
    - `similarityThreshold` is Chapter 1's similarity floor under LangCache's name: an entry scoring at or above it is a hit, so a higher number is stricter.
    - The reply is HTTP 200 either way. `data` holds the best entry at or above the floor, with its `similarity` (LangCache returns one entry unless a request sets `maxResults`). A miss is `data: []`, not a 404.

    ## Step 2. Store the answer after the model

    > Keep what the model wrote, so the next customer who asks the same thing doesn't cost a model call.

    ```text
    POST /v1/caches/<LANGCACHE_CACHE_ID>/entries
    Authorization: Bearer <LANGCACHE_API_KEY>
    Content-Type: application/json

    {"prompt": "How do I wash my down sleeping bag?", "response": "<the model's answer>", "ttlMillis": 900000}
    ```

    Reading it piece by piece:

    - `prompt` is the question the answer was written for. Later questions are compared with it.
    - `response` is the answer the model wrote.
    - `ttlMillis` is how long the entry lives: 900000 ms is 15 minutes. A new cache never expires entries unless you send it, so the app sends it every time.
    - A 201 returns the `entryId`. LangCache derives it from the prompt, so storing the same prompt again replaces the entry. Entries can't be listed, so the app keeps each id it gets in a ledger, and Clear sends `DELETE /entries/{entryId}` for each one.
    """)

    _key_idea = mo.callout(
        mo.md("A hit means the question is close in meaning, not that the answer is right. A threshold only trades wrong answers for misses, so cache only answers that are right for anyone who asks."),
        kind="info",
        title="Key idea: similar isn't the same",
    )

    _try_it = mo.callout(
        mo.md("""
    Ask the four questions in order with **Ask**, at 0.85 unless a step says otherwise. Each miss stores its answer, so asking a question twice hits it. Press **Clear** to start again: it deletes the entries this notebook stored, and any others expire after 15 minutes. **Ask** runs `lcs_answer`, the code shown below the results.

    1. Question 1, Tom's. It misses: the cache is empty, so the assistant searches the help center, the model writes the answer from the articles, and the app stores it. Read the system prompt: the app decides what the model sees. Note the total time.
    2. Question 2, another customer's. It hits at about 0.905 with no model call, though it says *launder* where the stored prompt says *wash*. Compare its total with the one you noted for question 1.
    3. Question 3, Priya's. She's served the down answer at about 0.950, higher than the real rewording. Look for care that only suits down, such as drying with tennis balls. The synthetic article says to skip them.
    4. Set the threshold to 0.96 and ask question 3, then question 2, again. Both miss: the nearest stored prompt scores 0.950 for question 3 and 0.905 for question 2. Look for the synthetic article's advice in Priya's new answer. No threshold keeps the right hit and drops the wrong one, because the wrong match scores higher.
    5. Set the threshold back to 0.85 and ask question 4, Priya's. Nothing stored is close (about 0.62), so it misses and the model answers from whatever articles it gets. Look for what it can't say: where her tent is. The app stores that answer for everyone.
    """),
        kind="success",
        title="Try it",
    )

    _asks = [
        ("Tom", "How do I wash my down sleeping bag?"),
        ("Another customer", "What's the right way to launder a goose-down sleeping bag?"),
        ("Priya", "How do I wash my synthetic sleeping bag?"),
        ("Priya", "Where's my tent?"),
    ]
    _questions = {f"{n}. {who}: {text}": text for n, (who, text) in enumerate(_asks, 1)}

    # Ask is a button, not a form: a form ignores a second press when its values haven't changed. A typed question wins over the picked one.
    lcs_ask = (
        mo.md("{pick}\n\n{text}\n\n{threshold}")
        .batch(
            pick=mo.ui.dropdown(_questions, value=next(iter(_questions)), label="Question", full_width=True),
            text=mo.ui.text(label="Or type your own", placeholder="Overrides the question above when filled", full_width=True),
            threshold=mo.ui.slider(start=0.80, stop=1.00, step=0.01, value=0.85, label="similarityThreshold", show_value=True, debounce=True),
        )
    )
    lcs_go = mo.ui.run_button(label="Ask")
    lcs_clear = mo.ui.run_button(label="Clear", kind="danger", tooltip="Delete this kernel's cache entries by id")

    mo.vstack(
        [
            _intro,
            mo.accordion({"The cache-aside path: who sends what to whom": _hops}),
            _requests,
            _key_idea,
            _try_it,
            lcs_ask,
            mo.hstack([lcs_go, lcs_clear], justify="start"),
        ],
        gap=0.75,
    )
    return lcs_ask, lcs_clear, lcs_go


@app.cell(hide_code=True)
async def lc_ask(
    json,
    lcs_answer,
    lcs_ask,
    lcs_call,
    lcs_clear,
    lcs_entries: dict[str, str],
    lcs_forget,
    lcs_go,
    lcs_request,
    lcs_system,
    mo,
    recording,
    redis,
    rss_search,
    rss_text,
    time,
):
    # Clear comes first on purpose: it deletes this kernel's entries by id, says how many went, and stops, so a Clear never asks a question.
    if lcs_clear.value:
        _deleted, _expired = lcs_forget()
        _held = f" {len(lcs_entries)} could not be deleted and are still held." if lcs_entries else ""
        mo.stop(True, mo.md(f"Cleared with `DELETE /entries/<entryId>`: {_deleted} deleted (204), and {_expired} had already expired (404).{_held}"))
    mo.stop(not lcs_go.value, mo.md("Pick a question, set a threshold, and press **Ask**."))
    _question = lcs_ask.value["text"].strip() or lcs_ask.value["pick"]
    _threshold = round(lcs_ask.value["threshold"], 2)

    # One extra search at threshold 0 finds the nearest stored prompt, so a miss can show how close it came. It runs before the turn, because a miss stores its own answer, which would then be nearest.
    _probe = lcs_call("POST", "/entries/search", lcs_request(_question, 0))
    _nearest = _probe.payload["data"][:1] if _probe.status == 200 else []
    _no_match = "The cache holds nothing this notebook stored." if _probe.status == 200 and not (_nearest or lcs_entries) else "No stored prompt came back from a second search at threshold 0."

    # The turn, timed, with its LangCache requests recorded in order: the search, then the store after a miss.
    _start = time.perf_counter()
    try:
        with recording() as _wire:
            _answer = await lcs_answer(_question, _threshold)
    except redis.ResponseError as _error:
        mo.stop("index" in str(_error).lower(), mo.md("The help center has no index yet. Press **Create** in Chapter 1 first."))
        raise
    except Exception as _error:
        if not _wire or _wire[0].status != 200:  # the cache search itself failed, so there is no search to show
            raise
        _answer = f"**No answer.** The turn failed after the cache search: `{type(_error).__name__}: {' '.join(str(_error).split())[:160]}`"
    _total = round((time.perf_counter() - _start) * 1000)
    _search, *_stores = _wire


    def _fence(kind: str, text: str) -> str:
        return f"```{kind}\n{text}\n```"


    def _request(x) -> str:
        """A recorded request as the notebook sent it, with the key masked."""
        return _fence("text", f"{x.method} /v1/caches/<LANGCACHE_CACHE_ID>{x.path}\nAuthorization: Bearer <LANGCACHE_API_KEY>\n\n{json.dumps(x.body, indent=2)}")


    _hits = _search.payload["data"]
    if _hits:
        _verdict = f"**Hit.** The stored prompt `{_hits[0]['prompt']}` scored {_hits[0]['similarity']:.3f}, at or above your threshold of {_threshold}, so its answer was served with no model call."
        _middle = []
    else:
        _near = f"The nearest stored prompt (found by a second search at threshold 0), `{_nearest[0]['prompt']}`, scored {_nearest[0]['similarity']:.3f}, below your threshold of {_threshold}." if _nearest else _no_match
        _verdict = f"**Miss.** {_near} So the app searches the help center and asks the model" + (", and stores the answer." if _stores else ".")
        _articles = rss_search(_question)  # the same search the turn ran, to show what the model saw
        _middle = [
            "**Help articles** `rss_search` found, closest first:",
            "\n".join(f"- {a['title']}" for a in _articles) or "_rss_search found no articles._",
            "**System prompt** from `lcs_system`, which the model gets with the question as the user message:",
            rss_text(lcs_system(_articles)),
            *[f"**Store request**, answered {s.status} in {s.ms} ms{'' if s.status == 201 else ', so nothing was stored'}\n\n{_request(s)}" for s in _stores],
        ]
    _timings = [f"cache search {_search.ms} ms", *(f"store {s.ms} ms" for s in _stores), f"total {_total} ms"]
    mo.vstack(
        [mo.md(_part) if isinstance(_part, str) else _part for _part in [
            _verdict,
            "**Search request**",
            _request(_search),
            f"**Reply from LangCache**, {_search.status} in {_search.ms} ms",
            _fence("json", json.dumps(_search.payload, indent=2)),
            *_middle,
            "**Answer**",
            _answer.replace("$", r"\$"),  # a bare $ would start a math span
            "**Timings**: " + ", ".join(_timings) + ".",
        ]],
        gap=0.75,
    )
    return


@app.cell
def lc_connect(Reply, json_request, json_session, os):
    # Client. One session that carries the Bearer token, the two request bodies, and a ledger of the entries this kernel stored.
    _host = os.environ["LANGCACHE_HOST"].strip().rstrip("/")  # the Connectivity page's URL, with or without https://
    lcs_session = json_session(
        f"{_host if '://' in _host else 'https://' + _host}/v1/caches/{os.environ['LANGCACHE_CACHE_ID'].strip()}",
        {"Authorization": f"Bearer {os.environ['LANGCACHE_API_KEY']}"},  # the one credential, read here and bound to nothing
    )

    # Entries can't be listed, so the app keeps the id of each one it stores: entryId -> prompt. Clear deletes by these ids.
    lcs_entries: dict[str, str] = {}


    def lcs_call(method: str, path: str, body: dict | None = None) -> Reply:
        """One request to the cache, with no retry: the HTTP status, the reply as a dict (None on a 204), and the milliseconds it took."""
        return json_request(lcs_session, method, path, body)


    def lcs_request(prompt: str, threshold: float) -> dict:
        """The search body. `similarityThreshold` is Chapter 1's similarity floor: an entry at or above it is a hit."""
        return {"prompt": prompt, "similarityThreshold": round(threshold, 2)}


    def lcs_entry(prompt: str, response: str) -> dict:
        """The store body. `ttlMillis` goes with every entry, because a new cache never expires entries unless you send it."""
        return {"prompt": prompt, "response": response, "ttlMillis": 900_000}


    def lcs_forget() -> tuple[int, int]:
        """Delete every ledger entry by id with `DELETE /entries/{entryId}`, never the whole cache. Returns how many answered 204 (deleted) and 404 (expired already). Anything else stays in the ledger."""
        counts = {204: 0, 404: 0}
        for entry_id in list(lcs_entries):
            status, _, _ = lcs_call("DELETE", f"/entries/{entry_id}")
            if status in counts:
                counts[status] += 1
                del lcs_entries[entry_id]
        return counts[204], counts[404]

    return lcs_call, lcs_entries, lcs_entry, lcs_forget, lcs_request


@app.cell
def lc_answer_fn(
    AI_MODEL,
    lcs_call,
    lcs_entries: dict[str, str],
    lcs_entry,
    lcs_request,
    rss_search,
    stream_llm,
):
    # Assistant. The cache comes first. On a miss the app decides what the model sees: the articles Chapter 1's search finds.
    def lcs_system(articles: list[dict]) -> str:
        """The assistant's base prompt: answer in two or three sentences, from these help articles."""
        return (
            "You are the Cairn Outfitters support assistant. Answer in two or three sentences. "
            "Use the help articles below; if they don't answer the question, say so and say what the customer can do next.\n\n"
            "Help articles:\n" + "\n".join(f"- {a['title']}: {a['body']}" for a in articles)
        )


    async def lcs_answer(question: str, threshold: float) -> str:
        """Cache-aside: serve a stored answer when one is close enough, else answer from the help center and store that answer."""
        # Search the cache before the model. A miss is 200 with data: [], never a 404.
        status, payload, _ = lcs_call("POST", "/entries/search", lcs_request(question, threshold))
        if status != 200:
            raise RuntimeError(f"LangCache answered {status} ({(payload or {}).get('type', 'no body')}) to the search")

        # A hit serves the best stored answer, and the model is never called.
        hits = payload["data"]
        if hits:
            return hits[0]["response"]

        # A miss: search the help center, and let the model answer from those articles. It never learns who is asking, because the stored answer goes to everyone.
        articles = rss_search(question)
        if not articles:  # they expire an hour after Create, and an answer without them would be stored for everyone
            raise RuntimeError("the help center holds no articles, so press Create in Chapter 1 again")
        answer = "".join([text async for text in stream_llm(lcs_system(articles), [{"role": "user", "content": question}], AI_MODEL)])

        # Store after the model, with the TTL. A 201 returns the entryId, which the ledger keeps so Clear can delete it.
        status, stored, _ = lcs_call("POST", "/entries", lcs_entry(question, answer))
        if status == 201:
            lcs_entries[stored["entryId"]] = question
        return answer

    return lcs_answer, lcs_system


@app.cell(hide_code=True)
def lc_ends(mo):
    _ends_on = mo.md("**Where this stops.** Priya's tent is on its way, and the assistant can't tell her where. A personal answer can't come from a shared cache, or from the help center. Chapter 3 gives the assistant her records.")

    _docs = mo.md("""
    - [LangCache](https://redis.io/docs/latest/develop/ai/context-engine/langcache/) and its [concepts](https://redis.io/docs/latest/develop/ai/context-engine/langcache/concepts/)
    - [Create a service](https://redis.io/docs/latest/operate/iris/langcache/create-service/)
    - [API and SDK examples](https://redis.io/docs/latest/develop/ai/context-engine/langcache/api-examples/) and the [API reference](https://redis.io/docs/latest/develop/ai/context-engine/langcache/api-reference/)
    """)

    mo.vstack([_ends_on, mo.accordion({"Docs for this chapter": _docs})], gap=0.75)
    return


@app.cell(column=3, hide_code=True)
def cr_title(CAIRN_SCOPE, mo):
    mo.md(f"""
    # Chapter 3. Context engineering and retrieval: answer about her

    Chapter 2 ended on Priya's question, "Where's my tent?" A shared cache can't answer it, and neither can the help center, because the answer is in her order. Cairn keeps its customers, products, and orders in Redis as JSON documents. This chapter lets the assistant read them.

    **Redis Context Retriever** turns a data model you declare into tools, which are functions a model can call. It serves them over **MCP**, the Model Context Protocol, a standard way to list tools and call them. The assistant chooses which tools to call, and Context Retriever answers each call from your database.

    Your app also decides what the model sees for each question, and that is **context engineering**. Here it is who is asking, Chapter 1's articles, and what the tools return.

    Priya's customer record is one JSON document, at the key your data model names:

    ```text
    JSON.GET cairn:{CAIRN_SCOPE}:customer:C001
    {{"customer_id":"C001","lifetime_value":4200.0,"name":"Priya Shah","region":"amer","tier":"gold"}}
    ```

    ## You'll learn how to

    1. Declare a data model, see that it decides the tools, and change it to fix a wrong answer.
    2. Tell the admin key, which makes the surface, from the agent key, which an agent sends with its tool calls.
    3. Assemble what the model sees: who is asking, which articles, and what the tools return.
    """)
    return


@app.cell
def cr_model_fns(CAIRN_SCOPE):
    # Model. Three classes, each field's index kind, and the data_model they export. Sends nothing.
    from context_surfaces import ContextField, ContextModel, ContextRelationship, export_data_model

    from cairn_support.sample_data import SHOP_RECORDS

    _redis_prefix = f"cairn:{CAIRN_SCOPE}:"


    class Customer(ContextModel):
        """A Cairn Outfitters shopper, such as Priya (C001) or Tom (C002)."""

        __redis_key_template__ = _redis_prefix + "customer:{customer_id}"

        customer_id: str = ContextField(description="Customer id, such as C001", is_key_component=True)
        name: str = ContextField(description="Full name", index="text")
        tier: str = ContextField(description="Loyalty tier", index="tag", allowed_values=["bronze", "silver", "gold"])
        region: str = ContextField(description="Sales region", index="tag", allowed_values=["amer", "emea", "apac"])
        lifetime_value: float = ContextField(description="Lifetime spend in USD", index="numeric")
        orders: list["Order"] = ContextRelationship(description="Orders this customer placed", target="Order", source_field="customer_id")


    class Product(ContextModel):
        """An item Cairn Outfitters sells, such as the Ultralight Tent 2P."""

        __redis_key_template__ = _redis_prefix + "product:{sku}"

        sku: str = ContextField(description="Stock keeping unit, such as P102", is_key_component=True)
        title: str = ContextField(description="Product title", index="text")
        category: str = ContextField(description="Product category", index="tag")
        price: float = ContextField(description="Price in USD", index="numeric")


    class Order(ContextModel):
        """One order: a customer, a product, and a status, such as Priya's tent."""

        __redis_key_template__ = _redis_prefix + "order:{order_id}"

        order_id: str = ContextField(description="Order id, such as O1001", is_key_component=True)
        customer_id: str = ContextField(description="The buyer's customer id", index="tag")
        sku: str = ContextField(description="The product's sku", index="tag")
        region: str = ContextField(description="Sales region", index="tag", allowed_values=["amer", "emea", "apac"])
        status: str = ContextField(description="Order status", index="tag", allowed_values=["placed", "shipped", "delivered", "returned"])
        total: float = ContextField(description="Order total in USD", index="numeric")
        customer: "Customer" = ContextRelationship(description="Buyer", target="Customer", source_field="customer_id")
        product: "Product" = ContextRelationship(description="Item bought", target="Product", source_field="sku")


    crs_models = [Customer, Product, Order]
    crs_data_model = export_data_model("Cairn Outfitters", "Customers, products, and orders of an outdoor gear shop", entities=crs_models)
    crs_record_keys = [model.__redis_key_template__.format(**row) for model in crs_models for row in SHOP_RECORDS[model.__name__]]
    crs_entity_prefixes = [model.__redis_key_template__.split("{")[0] for model in crs_models]
    return (
        SHOP_RECORDS,
        crs_data_model,
        crs_entity_prefixes,
        crs_models,
        crs_record_keys,
    )


@app.cell(hide_code=True)
def cr_model(CAIRN_SCOPE, crs_data_model, crs_record_keys, mo):
    _rows = ["The index kinds on those classes predict these tools.", "", "| entity | text | tag | numeric | tools the kinds add |", "|:--|:--|:--|:--|:--|"]
    _total = 0
    for _entity in crs_data_model["entities"]:
        _e = _entity["name"].lower()
        _kinds = {k: [f["name"] for f in _entity["fields"] for i in f["redis_indices"] if i["type"] == k] for k in ("text", "tag", "numeric")}
        _rules = {"get_{}_by_id": True, "list_{}": True, "count_{}": True, "filter_{}": _kinds["tag"] or _kinds["numeric"], "summarize_{}": _kinds["numeric"], "search_{}_by_text": _kinds["text"]}
        _tools = [f"`{name.format(_e)}`" for name, wanted in _rules.items() if wanted]
        _total += len(_tools)
        _rows.append(f"| {_entity['name']} | {' | '.join(', '.join(_kinds[k]) or '-' for k in _kinds)} | {', '.join(_tools)} |")

    crs_create_button = mo.ui.run_button(label="Create")
    crs_cleanup_button = mo.ui.run_button(label="Clean up", kind="danger")

    _classes = mo.md(f"""
    ## Step 1. The data model decides the tools

    Priya's tent order is a JSON document, and the assistant can't read it yet. You describe the records in the three classes above, and Context Retriever writes the tools that read them.

    Reading the classes piece by piece:

    - `__redis_key_template__` says where a record lives, and `is_key_component=True` marks the field that fills it. Customer C001 is the key `{crs_record_keys[0]}`.
    - `index="text"`, `"tag"`, and `"numeric"` are three of the field types you declared in Chapter 1's `FT.CREATE`, here for JSON documents.
    - `allowed_values` travel with the tools, so the model sees the legal tiers, regions, and statuses.
    - `ContextRelationship` says how entities connect: an order points at its customer and its product.
    """)

    _key_idea = mo.callout(mo.md("In Chapter 1 the schema decided what you could search. Here the index kinds decide which tools exist: `get_` needs a key component, `filter_` a tag or numeric field, `summarize_` a numeric field, and `search_` a text field. Order has no text field, so there is no `search_order_by_text`."), kind="info", title="Key idea: the data model decides the tools")

    _predicted = mo.md("\n".join(_rows) + f"\n\nThat is {_total} tools. The service adds four set tools: `except_results`, `expand_results`, `intersect_results`, and `union_results`. The whole list, with every description and input schema, is about 35,000 characters of JSON, and the app sends it with every model request.")

    _surface_md = mo.md(f"""
    ## Step 2. Create the surface

    You send the data model to the service once. It builds a surface, the hosted endpoint that serves your tools, and the notebook adds an agent key and the records:

    ```text
    POST /context-surfaces
      {{"name": "cairn-{CAIRN_SCOPE}", "data_model": {{...}}, "data_source": {{"type": "redis",
        "connection_config": {{"addr": ..., "password": ..., ...}}}}}}
    POST /context-surfaces/{{id}}/agent-keys   {{"name": "cairn-{CAIRN_SCOPE}-agent"}}
    POST /context-surfaces/{{id}}/data         {{"entity": "Customer", "records": [...], "options": {{...}}}}   once per entity
    ```

    Reading it piece by piece:

    - All three carry the admin key in `X-API-Key`. It creates and deletes surfaces, so only the admin functions send it.
    - `data_model` is the three classes as JSON. `connection_config` is your database, built from `REDIS_URL`, password included, so the hosted service can read and write it.
    - The second request makes the agent key, the key an agent sends with its tool calls. Its reply holds `mcp_url`, where it sends them, and an expiry the service picks, 90 days out. The admin key never goes there.
    - Each import writes one JSON document per record and indexes it. The service sets no TTL, so the notebook sets one hour on those keys. After an hour the records expire: press **Clean up**, then **Create**.
    """)

    _watch_out = mo.callout(mo.md("Create sends the database password from `REDIS_URL` to the hosted service, which keeps it until **Clean up** deletes the surface. Deleting the surface also ends its agent key. **Clean up** finds the surface by name, even after a restart."), kind="warn", title="Watch out: the service holds your password")
    _try_it = mo.callout(mo.md("Press **Create**, then compare the service's own tool list (`tools/list`) with the table above and read Priya's document. **Clean up** deletes the surface, its documents, and any leftover index. Chapter 4 reuses this surface, so keep it until **Clean up everything**."), kind="success", title="Try it")

    mo.vstack([_classes, _predicted, _key_idea, _surface_md, _watch_out, _try_it, mo.hstack([crs_create_button, crs_cleanup_button], justify="start")], gap=0.75)
    return crs_cleanup_button, crs_create_button


@app.cell(hide_code=True)
def cr_create(
    REDIS_CLIENT,
    crs_cleanup,
    crs_cleanup_button,
    crs_create_button,
    crs_create_surface,
    crs_list_tools,
    crs_record_keys,
    crs_set_surface,
    crs_shown,
    crs_surface,
    json,
    mo,
):
    # Create and Clean up, then the current state. This cell never reads the state getter, so storing the surface doesn't rerun it.
    _surface, _note = crs_surface(), ""
    if crs_create_button.value:
        if _surface:
            _note = "This kernel already holds a surface. Press **Clean up** first, so a second one never takes your password."
        else:
            _surface = crs_create_surface()
            crs_set_surface(_surface)
    if crs_cleanup_button.value:
        _note, _surface = crs_cleanup(), None
    mo.stop(not _surface, mo.md(_note or "No surface yet. Press **Create**."))

    _shown, _tools = crs_shown(_surface), crs_list_tools(_surface)
    _signatures = [f"{t['name']}({', '.join(a + '*' * (a in t['inputSchema'].get('required', [])) for a in t['inputSchema'].get('properties', {}))})" for t in _tools]
    _listing = mo.md(f"{_note}\n\n- Surface `{_shown['id']}`. Imported {', '.join(f'{n} {name.lower()}s' for name, n in _surface['imports'].items())}.\n- Agent key `{_shown['agent_key']}`, expires {_shown['key_expires_at']}. Its `mcp_url` is `{_shown['mcp_url']}`.\n- `tools/list` returned {len(_tools)} tools, {len(json.dumps(_tools)):,} characters of JSON. Required arguments are starred.\n\n```text\n" + "\n".join(_signatures) + "\n```")
    _record = mo.md(f"`JSON.GET {crs_record_keys[0]}`\n\n```json\n{json.dumps(REDIS_CLIENT.json().get(crs_record_keys[0]), indent=2)}\n```")
    mo.vstack([_listing, _record], gap=0.75)
    return


@app.cell
def cr_connect(
    CAIRN_SCOPE,
    REDIS_CLIENT,
    Reply,
    SHOP_RECORDS,
    crs_data_model,
    crs_models,
    crs_record_keys,
    json_request,
    json_session,
    os,
    redis,
):
    # Admin side. Every call here carries the admin key from .env: it makes the surface, the agent key, and the records. An agent never sees it.
    crs_surface_name = f"cairn-{CAIRN_SCOPE}"
    crs_admin_session = json_session(os.environ.get("CTX_API_URL", "https://cloud.redis.io/context-surfaces") + "/api/v1", {"X-API-Key": os.environ["CTX_ADMIN_KEY"]}, timeout=(5, 60))


    def crs_admin(method: str, path: str, body: dict | None = None, ok: tuple = (200, 201)) -> Reply:
        """One admin request. A status outside `ok` raises with the reply's detail and never the request, which can hold the database password."""
        reply = json_request(crs_admin_session, method, path, body)
        if reply.status not in ok:
            error = reply.payload if isinstance(reply.payload, dict) else {}
            raise RuntimeError(f"{method} {path} answered {reply.status}. {error.get('detail') or error.get('message') or error.get('error') or error.get('type') or ''}".strip())
        return reply


    def crs_connection_config(url: str) -> dict:
        """The database the surface reads and writes, parsed from the same REDIS_URL as REDIS_CLIENT. The pool sizes fit a 30-connection database."""
        parts = redis.connection.parse_url(url)
        return {
            "addr": f"{parts['host']}:{parts.get('port', 6379)}",
            "username": parts.get("username") or "default",
            "password": parts.get("password", ""),
            "db": parts.get("db", 0),
            "tls_enabled": parts.get("connection_class") is redis.SSLConnection,  # rediss://
            "pool_size": 2, "min_idle_conns": 0,
        }


    def crs_create_body() -> dict:
        """The create request: the data model, and the database it reads. It holds the password, so only crs_create_surface sends it."""
        return {
            "name": crs_surface_name,
            "data_model": crs_data_model,
            "data_source": {"type": "redis", "connection_config": crs_connection_config(os.environ["REDIS_URL"])},
        }


    def crs_create_surface() -> dict:
        """Create the surface, make an agent key, and import the records. The create POST is never retried: a replay would make a second surface holding the password."""
        try:
            made = crs_admin("POST", "/context-surfaces", crs_create_body()).payload
        except OSError as error:  # a timeout or a dropped connection: the service may have made the surface before the reply was lost
            raise RuntimeError(f"Create got no reply ({type(error).__name__}), so a surface may exist anyway. Press Clean up to delete every surface named {crs_surface_name}.") from error
        path, imports = f"/context-surfaces/{made['id']}", {}
        try:
            key = crs_admin("POST", path + "/agent-keys", {"name": crs_surface_name + "-agent"}).payload  # no expires_at: the service ignores it and picks 90 days
            mcp_url = key.get("mcp_url") or ""  # the key's reply says where an agent sends its calls
            if not mcp_url:
                raise RuntimeError("the agent key reply holds no mcp_url")
            for model in crs_models:  # Model(**row).model_dump(): a field the class doesn't declare never reaches the wire
                records = [model(**row).model_dump() for row in SHOP_RECORDS[model.__name__]]
                body = {"entity": model.__name__, "records": records, "options": {"on_conflict": "overwrite", "on_error": "fail_fast"}}
                imports[model.__name__] = crs_admin("POST", path + "/data", body).payload.get("imported", len(records))
        except Exception as error:
            raise RuntimeError(f"{str(error).rstrip('.')}. Press Clean up to remove the surface this left.") from error
        finally:
            for record_key in crs_record_keys:  # the service sets no TTL, so the notebook does, even when a call above raised
                REDIS_CLIENT.expire(record_key, 3600)
        return {
            "id": made["id"], "name": made["name"], "status": made["status"], "agent_key": key["key"],
            "key_expires_at": key.get("expires_at", "(not in the reply)"), "mcp_url": mcp_url, "imports": imports,
        }


    def crs_add_item(surface: dict) -> None:
        """Step 4: add `item` (the product's title, as a TAG) to Order, PUT the new data model to the same surface, and import the orders again."""
        from context_surfaces import ContextField, export_data_model

        titles = {row["sku"]: row["title"] for row in SHOP_RECORDS["Product"]}
        customer, product, order = crs_models

        class Order(order):  # the same entity name, so every tool keeps its name
            """One order: a customer, a product, and a status, such as Priya's tent."""

            item: str = ContextField(description="What was bought: the product's title", index="tag", allowed_values=list(titles.values()))

        model = export_data_model(crs_data_model["title"], crs_data_model["description"], entities=[customer, product, Order])
        crs_admin("PUT", f"/context-surfaces/{surface['id']}", {"data_model": model})
        records = [Order(**row, item=titles[row["sku"]]).model_dump() for row in SHOP_RECORDS["Order"]]
        crs_admin("POST", f"/context-surfaces/{surface['id']}/data", {"entity": "Order", "records": records, "options": {"on_conflict": "overwrite", "on_error": "fail_fast"}})
        for record_key in crs_record_keys:  # an order key that expired comes back from the import with no TTL
            REDIS_CLIENT.expire(record_key, 3600)

    return crs_add_item, crs_admin, crs_create_surface, crs_surface_name


@app.cell(hide_code=True)
def cr_lifecycle(
    REDIS_CLIENT,
    crs_admin,
    crs_entity_prefixes,
    crs_record_keys,
    crs_surface_name,
    mo,
    redis,
):
    # The surface, kept for the other cells. The notebook's one mo.state, with a private getter: a cell that references a getter reruns on every set, which would redefine the turn functions and reset the Chapter 4 chat. Other cells call crs_surface() instead.
    _get_surface, crs_set_surface = mo.state(None)


    def crs_surface() -> dict | None:
        return _get_surface()


    def crs_cleanup() -> str:
        ids = []
        for page in range(1, 41):  # the account lists its surfaces 50 a page, so this also finds one a restarted kernel forgot
            reply = crs_admin("GET", f"/context-surfaces?page={page}&page_size=50").payload
            ids += [item["id"] for item in reply.get("context_surfaces", []) if item.get("name") == crs_surface_name]
            if not reply.get("pagination", {}).get("has_next"):
                break
        for surface_id in ids:
            crs_admin("DELETE", f"/context-surfaces/{surface_id}", ok=(200, 204, 404))
        unlinked, dropped = REDIS_CLIENT.unlink(*crs_record_keys), []  # the service drops its indexes on DELETE but leaves the documents
        for name in REDIS_CLIENT.execute_command("FT._LIST"):
            try:
                definition = REDIS_CLIENT.ft(name).info().get("index_definition", {})
                prefixes = (definition if isinstance(definition, dict) else dict(zip(definition[::2], definition[1::2]))).get("prefixes", [])  # RESP3 or RESP2
                if prefixes and all(prefix in crs_entity_prefixes for prefix in prefixes):
                    REDIS_CLIENT.ft(name).dropindex(delete_documents=False)
                    dropped.append(name)
            except redis.ResponseError:  # dropped between the two commands
                continue
        crs_set_surface(None)
        return f"Surfaces deleted: {len(ids)}; record keys unlinked: {unlinked} of {len(crs_record_keys)}; leftover indexes dropped: {len(dropped)}."


    def crs_shown(surface: dict) -> dict:
        """The surface for display: the agent key becomes **** plus its last four characters."""
        return {**surface, "agent_key": "****" + surface["agent_key"][-4:]}

    return crs_cleanup, crs_set_surface, crs_shown, crs_surface


@app.cell
def cr_answer_fn(
    AI_MODEL,
    CAST,
    ChatMessage,
    ChatTurn,
    answer_with_tools,
    crs_surface,
    json,
    json_request,
    json_session,
    lcs_system,
    rss_search,
):
    # Agent side. Every call here carries the agent key and nothing else: no admin key, no database password.
    _sessions = {}  # one session per agent key, so every call after the first reuses its connection


    def _mcp(surface: dict, method: str, params: dict):
        """One JSON-RPC POST to the surface's mcp_url, with the agent key. json_request lets recording() see it."""
        key = surface["agent_key"]
        if key not in _sessions:
            _sessions[key] = json_session(headers={"X-API-Key": key}, timeout=(5, 60))  # no base URL: mcp_url is absolute
        return json_request(_sessions[key], "POST", surface["mcp_url"], {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


    def crs_list_tools(surface: dict) -> list[dict]:
        """tools/list with the agent key: every tool the data model generated, each with a name, a description, and an inputSchema."""
        reply = _mcp(surface, "tools/list", {})
        if reply.status != 200 or "error" in reply.payload:
            raise RuntimeError(f"tools/list answered {reply.status}. If the agent key has expired, press Clean up, then Create.")
        return reply.payload["result"]["tools"]


    def crs_call(surface: dict, name: str, arguments: dict) -> str:
        """tools/call with the agent key: the text to hand back to the model. A tool error is text too, so the model can read a failure and try again."""
        payload = _mcp(surface, "tools/call", {"name": name, "arguments": arguments}).payload or {}
        if "result" in payload:
            return (payload["result"].get("content") or [{}])[0].get("text", "")
        return json.dumps(payload.get("error", payload))


    def crs_build_system(customer_id: str, articles: list[dict], extra: str = "") -> str:
        """Chapter 2's prompt, plus who is asking, plus `extra` (Chapter 4 adds what she told us before)."""
        who = next(name for name, cid in CAST.items() if cid == customer_id)
        system = lcs_system(articles) + f"\n\nYou are talking to {who}, customer id {customer_id}. Look up their orders and products with the tools. Never look up another customer's records."
        return system + "\n\n" + extra if extra else system


    async def crs_answer(customer_id: str, messages: list[ChatMessage], extra: str = "") -> ChatTurn:
        """The app decides what the model sees (identity, articles, `extra`). The model decides which tools to call."""
        surface = crs_surface()  # None before Create: the assistant then answers from the help center alone
        tools = crs_list_tools(surface) if surface else []
        question = str(messages[-1].content)
        system = crs_build_system(customer_id, rss_search(question), extra)
        async for text in answer_with_tools(system, messages, AI_MODEL, tools, lambda name, arguments: crs_call(surface, name, arguments)):
            yield text

    return crs_answer, crs_build_system, crs_list_tools


@app.cell(hide_code=True)
def cr_ask_md(CAST, mo):
    # Ask is a button, not a form: a form ignores a second press when its values haven't changed. A typed question wins over the picked one.
    # Tom's question is worded so rss_search finds returns-refund first. "Has the refund for my trail shoes gone through?" doesn't retrieve it.
    _questions = ["Where's my tent? I leave for Maine on June 12.", "When will the refund for my trail shoes reach my card?", "Which sleeping bag should I get for the trip?"]
    crs_ask = mo.md("{customer}\n\n{pick}\n\n{text}").batch(
        customer=mo.ui.dropdown(options=CAST, value="Priya", label="Ask as"),
        pick=mo.ui.dropdown(options=_questions, value=_questions[0], label="Question", full_width=True),
        text=mo.ui.text(label="Or type your own", placeholder="Overrides the question above when filled", full_width=True),
    )
    crs_go = mo.ui.run_button(label="Ask")

    _step = mo.md("""
    ## Step 3. Ask as a customer

    Each question goes through `crs_answer`. The app puts who is asking and Chapter 1's articles in the system prompt, and hands over the tool list. The model decides which tools to call. When it picks one, `crs_call` sends a request like this and gives the reply text back to the model, which can call another tool or write the answer:

    ```text
    POST <mcp_url>
    X-API-Key: <the agent key>

    {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
     "params": {"name": "filter_order", "arguments": {"tag_conditions": [{"field": "customer_id", "value": "C001"}]}}}
    ```

    Reading it piece by piece:

    - `X-API-Key` is the agent key, the only key a tool call carries. MCP messages are JSON-RPC: `jsonrpc` and `id` are the envelope, and `method` says what to do. `"tools/call"` runs one tool, and `"tools/list"` returns the menu of tools, which `crs_answer` hands to the model.
    - `name` and `arguments` are the model's choice, written against the tool's `inputSchema`, a JSON Schema for its arguments.
    - A tag condition on `customer_id` is Chapter 1's filter before ranking again: the rule decides which records may come back, as `@year` did, so only Priya's orders do.
    - The reply is `result.content[0].text`, JSON text: a filter returns `count` and `results`, a `get_` the bare record. A tool error is not an exception but HTTP 200, with the reason as text the model reads and can retry.
    """)

    _scope = mo.callout(mo.md("An agent key can carry access tags, which filter what the key can see, but this data model declares no ACL mapping. The identity line in the prompt is the only scope the demo shows."), kind="warn", title="Watch out: identity is a line in the prompt")

    _try_it = mo.callout(mo.md("""
    - Ask as Priya, with the tent question. The answer is often wrong: the model adds a guessed `sku` "tent", or a `status` such as "placed", to `filter_order`, gets `count` 0, and tells her she has no tent order. Read the tool calls to see which it guessed.
    - Why it guesses: the data model decides the tools, and an order has a `sku` but no title. A tag condition matches exact labels, as Chapter 1's `TAG` fields did, so a guess that none of her orders match returns `count` 0, and the model reads the empty reply as the answer. Press **Ask** again and compare: the runs that come out right (O1001 is `shipped`) didn't guess a `sku`. Step 4 puts the product's name on each order.
    - Ask as Tom, with the refund question. The refund article joins the prompt, so the model may call no tool. If it does, compare it with Priya's. His shoes, O1004, are `returned`.
    - Ask Priya's tent question as Tom. Look at the id in the arguments, and at whether the assistant says Tom has no tent order. It can also call his jacket a tent, because his orders hold only skus too.
    """), kind="success", title="Try it")

    mo.vstack([_step, _scope, _try_it, crs_ask, crs_go], gap=0.75)
    return crs_ask, crs_go


@app.cell(hide_code=True)
async def cr_ask(
    crs_answer,
    crs_ask,
    crs_build_system,
    crs_go,
    crs_surface,
    json,
    mo,
    recording,
    redis,
    rss_search,
    rss_text,
):
    mo.stop(not crs_go.value, mo.md("Pick a customer and a question, then press **Ask**."))
    mo.stop(crs_surface() is None, mo.md("Press **Create** in step 2 first, then press **Ask** again: until a surface exists, the assistant has no tools."))
    _customer = crs_ask.value["customer"]
    _question = crs_ask.value["text"].strip() or crs_ask.value["pick"]
    try:
        _articles = rss_search(_question)  # the prompt below is rebuilt from these, the same articles the turn retrieves
    except redis.ResponseError:  # the index doesn't exist
        _articles = []
    mo.stop(not _articles, mo.md("Chapter 1's help center has no articles. Press **Create** there: the articles expire an hour after each press."))

    with recording() as _exchanges:
        try:
            _answer = "".join([text async for text in crs_answer(_customer, [mo.ai.ChatMessage(role="user", content=_question)])])
        except Exception as _error:  # the model call failed, the service refused the agent key, or the tool list didn't load
            _answer = f"The turn failed. `{type(_error).__name__}`: {str(_error)[:300]}"

    _calls = []
    for _exchange in (x for x in _exchanges if (x.body or {}).get("method") == "tools/call"):
        _params, _payload = _exchange.body["params"], _exchange.payload or {}
        _text = (_payload.get("result", {}).get("content") or [{}])[0].get("text") or json.dumps(_payload)
        _calls.append(f"**{len(_calls) + 1}. `{_params['name']}`** with `{json.dumps(_params['arguments'])}`, {_exchange.ms} ms\n\n"
                      f"```json\n{_text[:700]}{'...' if len(_text) > 700 else ''}\n```")

    _saw = mo.vstack([mo.md(f"**What the model saw**: this system prompt for `{_customer}`, your question as the user message, and the tools from **Create**:"), rss_text(crs_build_system(_customer, _articles))])
    _asked = mo.md("**What it asked for**\n\n" + ("\n\n".join(_calls) or "_No tool calls were made._"))
    mo.vstack([_saw, _asked, mo.md("**Answer**\n\n" + _answer.replace("$", r"\$"))], gap=0.75)  # a bare $ would start a math span
    return


@app.cell(hide_code=True)
def cr_fix_md(mo):
    crs_fix_button = mo.ui.run_button(label="Update the data model")

    _step = mo.md("""
    ## Step 4. Fix the interface, not the model

    Priya's order holds only a `sku`, and nothing in P102 says tent, so the model guessed. Give each order what was bought, as a field the tools can filter on. `crs_add_item`, above, adds `item` to the Order class and sends the new data model to the same surface, then imports the orders again, each with its `item`. The surface keeps its id, agent key, and `mcp_url`, and the service rebuilds the tools from the new model:

    ```text
    PUT /context-surfaces/{id}   {"data_model": {...}}
    ```

    Reading it piece by piece:

    - `index="tag"` makes `item` an exact label, like Chapter 1's `TAG` fields, so `filter_order` can match it.
    - `allowed_values` puts every title in the tool's description, as `item (values: ...)`, and the service refuses an unlisted value with the valid ones in its error. Titles, not codes: P102 says nothing about tents.
    - The cost: this suits a short list, because the service caps `allowed_values` at 128 values, and `item` repeats `Product.title`, so the two must change together.
    """)

    mo.vstack([_step, mo.callout(mo.md("Press **Update the data model**, then ask Priya's tent question again in step 3, as Priya and then as Tom. Look for `item` set to `Ultralight Tent 2P` in the tool call, and compare the answers with the ones you got before."), kind="success", title="Try it"), crs_fix_button], gap=0.75)
    return (crs_fix_button,)


@app.cell(hide_code=True)
def cr_fix(crs_add_item, crs_fix_button, crs_list_tools, crs_surface, mo):
    mo.stop(not crs_fix_button.value or crs_surface() is None, mo.md("_Press **Create** in step 2 and ask in step 3, then press **Update the data model**._"))
    crs_add_item(crs_surface())
    _item = next((part for tool in crs_list_tools(crs_surface()) if tool["name"] == "filter_order" for part in tool["description"].replace(". ", "; ").split("; ") if part.startswith("item")), "")
    mo.md(f"Updated surface `{crs_surface()['id']}`. `filter_order` now lists:\n\n```text\n{_item}\n```\n\nAsk again in step 3.")
    return


@app.cell(hide_code=True)
def cr_ends(mo):
    _docs = mo.md("""
    - [Context Retriever](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/). It does not support Redis Flex or Active-Active databases.
    - [MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
    - Function calling: [OpenAI](https://developers.openai.com/api/docs/guides/function-calling), [Anthropic](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview), [Gemini](https://ai.google.dev/gemini-api/docs/function-calling)
    """)

    mo.vstack([mo.md("**Where this stops.** A week later, in a new chat, Priya asks, \"Which sleeping bag should I get for the trip?\" You can ask it as Priya in step 3. Her orders and the help articles say nothing about her being allergic to down or paddling in Maine in June, so look at what the answer has to go on. She told the assistant last week, and it forgot. Chapter 4 gives the assistant a memory."), mo.accordion({"Docs for this chapter": _docs})], gap=0.75)
    return


@app.cell(column=4, hide_code=True)
def am_title(ams_client, mo):
    _ = ams_client  # the buttons wait for the settings check, so a press never runs ahead of it
    ams_seed_button = mo.ui.run_button(label="Seed")
    ams_refresh_button = mo.ui.run_button(label="Refresh")

    _story = mo.md(r"""
    # Chapter 4. Agent memory: remember her

    A week later, Priya opens a new chat and asks, "Which sleeping bag should I get for the trip?" Her orders don't say she's allergic to down feathers or that she's paddling in Maine in June. She told the assistant last week, and every chat starts empty, so it forgot.

    **Redis Agent Memory** keeps what customers say as **long-term memories**, one set per customer. Each turn writes the customer's message to **session memory**, recalls that customer's memories by meaning, and adds them to the context Chapter 3 built.

    ## You'll learn how to

    1. Keep each customer's memories apart with an `ownerId` filter that runs before ranking, the way Chapter 1's `@year` filter did.
    2. Tell session memory, which holds one chat, from long-term memory, which the service extracts from sessions in the background.
    3. Recall a memory by meaning, even when it shares no words with the question.
    4. Run the complete turn: memory, help articles, tools, and the model.

    ## Step 1. Store each customer's memories

    Agent Memory stores each memory as one record. This is the first of Priya's:

    ```json
    {
      "id": "c001-seed-1",
      "text": "I'm allergic to down feathers",
      "ownerId": "C001"
    }
    ```

    Reading it piece by piece:

    - `id` is the app's choice, not the service's, so a second **Seed** rewrites the same record instead of adding a copy.
    - `text` is the fact, in the customer's words.
    - `ownerId` is the customer's id, the same `C001` that keys her record in Chapter 3. Recall filters on it.
    - **Seed** sends no `memoryType`, so the store files each memory as `semantic`, a durable fact. A memory the service extracts from a conversation can be `episodic`, a snapshot of one session. The **type** column in the tables shows which.

    **Seed** writes four memories for Priya and three for Tom with `POST /long-term-memory`. **Refresh** lists them again, including any the service extracted in the background after a chat turn.
    """)

    _try_it = mo.callout(mo.md("Press **Seed**. The tables under the buttons list Priya's four memories and Tom's three. Press it again and the counts stay the same, because a second **Seed** rewrites the same ids."), kind="success", title="Try it")

    mo.vstack([_story, _try_it, mo.hstack([ams_seed_button, ams_refresh_button], justify="start")], gap=0.75)
    return ams_refresh_button, ams_seed_button


@app.cell(hide_code=True)
async def am_store(
    CAST,
    ams_memories,
    ams_refresh_button,
    ams_seed,
    ams_seed_button,
    mo,
):
    # Seed writes the memories. Every run, for any reason, then lists what the store holds for each customer.
    _note = f"**Seed** sent {len(await ams_seed())} memories." if ams_seed_button.value else ""
    _ = ams_refresh_button.value  # a Refresh press reruns this cell, to list memories extracted in the background

    _tables = []
    for _name, _owner in CAST.items():
        _found = await ams_memories(_owner)
        _rows = [f"| {_m.text.replace('|', '&#124;')} | {_m.memory_type} |" for _m in _found]
        _tables.append(mo.md("\n".join([f"**{_name}**, `ownerId` {_owner} ({len(_found)})", "", "| memory | type |", "|:--|:--|", *_rows])))

    mo.vstack([mo.md(_note), mo.hstack(_tables, widths="equal", align="start")])
    return


@app.cell
def am_connect(os):
    # Client. Three settings from .env, and one object for every Agent Memory call in this chapter. The steps name the HTTP request behind each call, and this SDK sends it for you.
    from redis_agent_memory import AgentMemory, models


    ams_client = AgentMemory(
        os.environ["MEMORY_API_BASE_URL"],
        store_id=os.environ["MEMORY_STORE_ID"],
        api_key=os.environ["MEMORY_API_KEY"],
    )
    return ams_client, models


@app.cell
def am_store_fns(CAST, ams_client, asyncio, models):
    # Store. Seed, list, and delete the long-term memories of the two customers.
    ams_seeds: dict[str, list[str]] = {
        CAST["Priya"]: [
            "I'm allergic to down feathers",
            "I'm planning a canoe trip in Maine in June",
            "I always sleep cold",
            "I have a cat named Miso",
        ],
        CAST["Tom"]: [
            "I wear size 11 trail shoes",
            "I'm planning a hiking trip in the Dolomites in August",
            "I've got two dogs, Max and Cece",
        ],
    }


    async def ams_memories(owner: str) -> list[models.MemoryRecord]:
        """Every long-term memory whose `ownerId` is `owner`, up to 100. With no `text`, the search endpoint lists by filter alone."""
        r = await ams_client.search_long_term_memory_async(request={"filter": {"ownerId": {"eq": owner}}, "limit": 100})
        return r.items


    async def ams_seed() -> list[str]:
        """Write every seed memory, each tagged with its customer's `ownerId`. The ids are the app's, so a second Seed rewrites
        the same records. Returns the ids written."""
        memories = [
            {"id": f"{owner.lower()}-seed-{i}", "text": text, "ownerId": owner}  # no memoryType, so the store files it as "semantic"
            for owner, texts in ams_seeds.items()
            for i, text in enumerate(texts, 1)
        ]
        await ams_client.bulk_create_long_term_memories_async(memories=memories)
        return [m["id"] for m in memories]


    async def ams_forget(owner: str) -> tuple[list[str], list[str]]:
        """Delete every session and every long-term memory about `owner`. Returns the session ids and the memory ids deleted."""
        sessions = await ams_client.list_sessions_async(filter_owner_id=owner)
        await asyncio.gather(*(ams_client.delete_session_memory_async(session_id=s) for s in sessions.items))

        memory_ids = [m.id for m in await ams_memories(owner)]
        if memory_ids:
            await ams_client.bulk_delete_long_term_memories_async(memory_ids=memory_ids)

        return sessions.items, memory_ids

    return ams_forget, ams_memories, ams_seed


@app.cell(hide_code=True)
def am_chat_md(CAST, mo):
    ams_owner = mo.ui.dropdown(options=CAST, value="Priya", label="Chat as")  # shown beside the chat, and read by am_turn_fn, so a switch starts a new session

    _session = mo.md(r"""
    ## Step 2. Write each message to session memory

    Each turn starts by writing the customer's message to this chat's session with `POST /session-memory/events`. For Priya's sleeping bag question:

    ```json
    {
      "sessionId": "c001-3f9a2b7c1d4e",
      "actorId": "C001",
      "role": "USER",
      "content": [{"text": "Which sleeping bag should I get for the trip?"}],
      "createdAt": "2026-10-23T15:04:05Z"
    }
    ```

    Reading it piece by piece:

    - `sessionId` is the app's, one per chat. Switching **Chat as** to another customer starts a new one.
    - `actorId` is the customer's id. A session takes its `ownerId` from its first event, so the memories the service extracts from it are hers.
    - `role` is `USER`: the app writes the customer's messages, not the assistant's replies.
    - `content` holds the message as typed, and `createdAt` says when, by the app's clock.
    """)

    _extraction = mo.callout(mo.md("Session memory holds one chat. The service extracts long-term memories from it within about five minutes, in its own words. A turn never recalls its own message, and a memory can say more than she did."), kind="warn", title="Watch out: extraction is slow")

    _recall = mo.md(r"""
    ## Step 3. Recall them by meaning

    Each turn asks Agent Memory (`POST /long-term-memory/search`) for the customer's memories closest in meaning to what they just said. For Priya's sleeping bag question:

    ```json
    {
      "text": "Which sleeping bag should I get for the trip?",
      "similarityThreshold": 0.15,
      "filter": {"ownerId": {"eq": "C001"}},
      "limit": 10
    }
    ```

    Reading it piece by piece:

    - `text` is the last user message. The match is by meaning, so "Which sleeping bag should I get for the trip?" can find "I'm allergic to down feathers" with no word in common.
    - `similarityThreshold` is the same floor as in Chapters 1 and 2, fixed at 0.15. Each service scores on its own scale. The floor is low on purpose: a memory the model doesn't need costs a few tokens, but one it never sees can cost the answer. The reply has no score, so you only see which memories cleared it.
    - `filter.ownerId` is Chapter 1's filter before ranking: only C001's memories are ranked, so Tom's never come back for Priya.
    - `limit` caps the reply at ten memories.

    `ams_turn` is the whole recipe. It writes the message to session memory, recalls with this request, and hands the memories to Chapter 3's `crs_answer`, which adds them to the prompt. The app decides what the model sees, and now that includes what she told it before.
    """)

    mo.vstack([_session, _extraction, _recall], gap=0.75)
    return (ams_owner,)


@app.cell
def am_turn_fn(
    ChatMessage,
    ChatTurn,
    ams_client,
    ams_owner,
    crs_answer,
    dt,
    models,
    uuid,
):
    # Turn. One chat turn: the session write, the long-term recall, and Chapter 3's answer with the memories added.
    ams_session_id = f"{ams_owner.value.lower()}-{uuid.uuid4().hex[:12]}"  # a new session each time the customer changes


    def ams_request(text: str, owner: str) -> dict:
        """The recall request: `text` matched by meaning, clearing a similarity floor, among `owner`'s memories only."""
        return {
            "text": text,
            "similarityThreshold": 0.15,  # the same floor as Chapters 1 and 2, fixed here
            "filter": {"ownerId": {"eq": owner}},  # Chapter 1's filter before ranking
            "limit": 10,
        }


    def ams_memory_block(owner: str, memories: list[str]) -> str:
        """What the app adds to the system prompt: the customer's memories, or nothing when there are none."""
        lines = "\n".join(f"- {text}" for text in memories)
        return f"What customer {owner} told you in earlier chats. Use it when it matters to the question:\n{lines}" if memories else ""


    async def ams_turn(messages: list[ChatMessage]) -> ChatTurn:
        """One assistant turn: write the message to session memory, recall memories, and answer as Chapter 3 does."""
        owner, question = ams_owner.value, str(messages[-1].content)  # the customer id from the dropdown

        # Session memory: the message, as an event in this chat's session. Its actorId is the customer's id.
        await ams_client.add_session_event_async(
            session_id=ams_session_id, actor_id=owner, role=models.MessageRole.USER,
            content=[{"text": question}], created_at=dt.datetime.now(dt.timezone.utc),
        )

        # Long-term memory: this customer's memories closest in meaning to the question.
        found = await ams_client.search_long_term_memory_async(request=ams_request(question, owner))

        # The answer: Chapter 3's, with the memories added to what the model sees.
        async for text in crs_answer(owner, messages, extra=ams_memory_block(owner, [m.text for m in found.items])):
            yield text

    return ams_memory_block, ams_request, ams_session_id, ams_turn


@app.cell(hide_code=True)
def am_chat(ams_owner, ams_turn, mo, recording, redis):
    ams_log = []  # one record per turn, for the pane beside the chat


    async def _turn(messages):
        """ams_turn inside recording(), noted in ams_log. A failed turn ends with a line of text, because mo.ui.chat keeps a turn only then."""
        with recording() as exchanges:
            record = {"question": str(messages[-1].content), "owner": ams_owner.value, "exchanges": exchanges, "error": ""}
            ams_log.append(record)
            try:
                async for text in ams_turn(messages):
                    yield text
            except redis.ResponseError:  # rss_search found no index: Chapter 1's Create never ran, or Clean up everything dropped it
                record["error"] = "ResponseError"
                yield "Chapter 1's index is missing: press **Create** there, then ask again."
            except Exception as error:
                record["error"] = type(error).__name__
                yield f"\n\n*The turn stopped with {record['error']}. The pane beside the chat shows what the search and the session hold.*"


    ams_chatbot = mo.ui.chat(
        _turn,
        prompts=[
            "Which sleeping bag should I get for the trip?",
            "How do I wash my sleeping bag after the trip?",
            "Where's my tent?",
            "I'm bringing my dog Juniper on the trip.",
        ],
        max_height=520,
    )
    return ams_chatbot, ams_log


@app.cell(hide_code=True)
async def am_pane(
    CAST,
    ams_chatbot,
    ams_client,
    ams_log,
    ams_memory_block,
    ams_owner,
    ams_request,
    ams_session_id,
    asyncio,
    crs_build_system,
    json,
    mo,
    rss_search,
    rss_text,
):
    _names = {customer_id: name for name, customer_id in CAST.items()}


    def _lines(items: list[str], empty: str = "none") -> str:
        return "\n".join(f"- {item}" for item in items) or f"- {empty}"


    _try_it = mo.callout(mo.md("""
    After **Seed**, chat as Priya. Type `/` to open the starter prompts (the menu lists them alphabetically) and send them in the order below. The pane beside the chat shows each turn's recall, prompt, tool calls, and session. The assistant answers from Chapter 1's articles, so press **Create** there first, or again if the articles have expired.

    1. "Which sleeping bag should I get for the trip?" Look for an answer that weighs her memories and the temperature-rating article. Does the pane show a catalog lookup?
    2. "How do I wash my sleeping bag after the trip?" Chapter 2's cache served Priya the down answer for her synthetic bag. Here, look for synthetic care, and for the recalled memory behind it.
    3. "Where's my tent?" Look for a `filter_order` call for `C001`. It needs Chapter 3's surface. O1001 is `shipped`.
    4. "I'm bringing my dog Juniper on the trip." This is a new fact. Wait up to five minutes, then press **Refresh** in step 1 to see whether the service extracted it.

    Then switch **Chat as** to Tom, which starts a new session, and send the first prompt again. Only his memories can come back, and the pane shows what the same search finds for Priya, which the assistant never sends.
    """), kind="success", title="Try it")

    if not ams_log:
        _data = mo.md("Send a message to fill this pane.")
    else:
        _last = ams_log[-1]
        _owner = _last["owner"]
        _other = next(customer_id for customer_id in CAST.values() if customer_id != _owner)
        try:
            # Three service calls, for display only: this turn's recall again, the same search for the other customer, and the session.
            _mine, _theirs, _session = await asyncio.gather(
                ams_client.search_long_term_memory_async(request=ams_request(_last["question"], _owner)),
                ams_client.search_long_term_memory_async(request=ams_request(_last["question"], _other)),
                ams_client.get_session_memory_async(session_id=ams_session_id),
            )
            _recalled = [m.text for m in _mine.items]
            _said = [event.content[0].text for event in _session.events]
            _system = crs_build_system(_owner, rss_search(_last["question"]), ams_memory_block(_owner, _recalled))
            _calls = [
                f"`{e.body['params']['name']}` `{json.dumps(e.body['params']['arguments'])}`, HTTP {e.status}, {e.ms} ms"
                for e in _last["exchanges"] if e.body and e.body.get("method") == "tools/call"
            ]
            _data = mo.vstack(
                [
                    mo.md(f"**Recalled for {_names[_owner]}** ({_owner}), the search `ams_request` builds\n\n" + _lines(_recalled, "nothing cleared the floor")),
                    mo.md(f"**The same search for {_names[_other]}** ({_other}). The assistant never sends it, and the `ownerId` filter keeps it out of the prompt\n\n" + _lines([m.text for m in _theirs.items], "nothing cleared the floor")),
                    mo.vstack([mo.md("**System prompt**, from `crs_build_system`"), rss_text(_system)]),
                    mo.md("**Tool calls**, from the recorded requests\n\n" + _lines(_calls, "none before the turn stopped" if _last["error"] else "none this turn")),
                    mo.md(f"**Session `{ams_session_id}`**, the user messages the app wrote ({len(_said)})\n\n" + _lines(_said)),
                ]
            )
        except Exception as _error:  # the chat is part of this output, so an error here must not replace it
            _data = mo.md("Chapter 1's index is missing, so the turn stopped before the answer. Press **Create** there." if type(_error).__name__ == "ResponseError" else f"The pane couldn't read the turn back: {type(_error).__name__}.")

    # The page keeps a chat's messages when its cell reruns, so Tom's chat sits one level deeper than Priya's: a switch mounts a new chat.
    _chat = mo.vstack([ams_owner, ams_chatbot])
    for _ in range(list(CAST.values()).index(ams_owner.value)):
        _chat = mo.vstack([_chat])

    mo.vstack([mo.md("## Step 4. Run the whole turn"), _try_it, mo.hstack([_chat, _data], widths=[5, 6], gap=1.5, align="start")], gap=0.75)
    return


@app.cell(hide_code=True)
def am_epilogue(ams_client, mo):
    _ = ams_client  # the button waits for the settings check, like Seed and Refresh
    am_cleanup_button = mo.ui.run_button(label="Clean up everything", kind="danger")

    _docs = mo.md("""
    - [Overview](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/overview/)
    - [Python SDK quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/)
    - [Developer guide](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/developer-guide/)
    """)

    _epilogue = mo.md(r"""
    ## Epilogue. One turn, four layers

    Here is what Priya's question, "Which sleeping bag should I get for the trip?", used, layer by layer:

    1. **Agent Memory** wrote her message to session memory and recalled her long-term memories.
    2. **Redis Search** found the help articles closest in meaning to her question.
    3. **Context Retriever** listed its tools and answered any calls the model made for her orders.
    4. **The model** got the system prompt the app assembled, the chat, and the tool results, and wrote the answer.

    **LangCache** sits in front of questions whose answer is the same for everyone. It isn't in this turn, because the right answer here is about her.

    Four ideas came back along the way:

    - **The schema decides what you can search.** Chapter 1 declared field types in `FT.CREATE`. In Chapter 3, the data model decides the tools.
    - **A similarity floor.** `VECTOR_RANGE` in Chapter 1, then the same floor, called `similarityThreshold`, in Chapters 2 and 4.
    - **Filter before ranking.** `@year` in Chapter 1, the filter tools in Chapter 3, `ownerId` in Chapter 4.
    - **The app decides what the model sees.** The articles in Chapter 2, her identity and the tool results in Chapter 3, her memories in Chapter 4.
    """)

    _cleanup = mo.md(r"""
    ### Clean up everything

    This removes what the chapters created, by name or id and nothing else: Chapter 1's index and article keys, Chapter 2's cache entries, Chapter 3's surface (which ends its agent key), record keys, and indexes, and the memories and sessions of Priya and Tom. Redis keys and cache entries expire on their own, and memories last a year. The surface doesn't expire. It holds your database password and stays until it's deleted, so press **Clean up everything** when you're done.
    """)

    mo.vstack([_epilogue, mo.accordion({"Docs for this chapter": _docs}), _cleanup, am_cleanup_button], gap=0.75)
    return (am_cleanup_button,)


@app.cell(hide_code=True)
async def am_cleanup(
    CAST,
    HELP_ARTICLES,
    REDIS_CLIENT,
    RS_IDX_NAME,
    RS_KEY_PREFIX,
    am_cleanup_button,
    ams_forget,
    crs_cleanup,
    lcs_forget,
    mo,
    redis,
):
    mo.stop(not am_cleanup_button.value)

    # Context Retriever first: the surface holds the database password and never expires on its own.
    _surface = crs_cleanup()

    # Redis Search: drop the index without DD, then unlink each article key by name.
    try:
        REDIS_CLIENT.execute_command("FT.DROPINDEX", RS_IDX_NAME)
    except redis.ResponseError:  # Chapter 1 never created it, or it is already gone
        pass
    _keys = REDIS_CLIENT.unlink(*(f"{RS_KEY_PREFIX}:{_article['id']}" for _article in HELP_ARTICLES))

    # LangCache and Agent Memory clean up with their own functions.
    _deleted, _gone = lcs_forget()
    _forgot = [await ams_forget(_owner) for _owner in CAST.values()]

    mo.md(f"""
    - **Redis Search:** the index is gone, and {_keys} article keys unlinked.
    - **LangCache:** deleted {_deleted} entries, {_gone} had already expired.
    - **Context Retriever:** {_surface}
    - **Agent Memory:** deleted {sum(len(_m) for _, _m in _forgot)} memories and {sum(len(_s) for _s, _ in _forgot)} sessions for {' and '.join(CAST)}.

    The results above still show the last run. To run the chapters again, press **Create** in Chapter 1 and in Chapter 3, then **Seed** in Chapter 4. **Refresh** updates the memory tables.
    """)
    return


if __name__ == "__main__":
    app.run()
