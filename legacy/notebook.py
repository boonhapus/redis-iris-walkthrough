# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "marimo==0.24.2",
#     "redis==8.1.0",
#     "numpy>=2.0",
#     "model2vec==0.9.0",
# ]
# ///
"""Redis Iris basics -- a barebones marimo notebook.

Builds the Redis-side mechanics behind three Iris components out of raw redis-py:
Agent Memory (recency + semantic recall), LangCache (semantic cache), Semantic
Routing (guardrail). One credential: REDIS_URL. No LLM, no embedding API.

Launch:  uvx marimo@0.24.2 edit --sandbox notebook.py
"""

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")

with app.setup:
    # Shared imports, constants and helpers. Runs once per session.
    # Helpers live here so the cells below cannot collide on names -- marimo
    # requires every top-level name to be defined in exactly one cell.
    import hashlib
    import os
    import re
    import time
    import uuid
    from collections import deque

    import marimo as mo
    import numpy as np
    import redis

    # Every key and index is namespaced per session, so two people pointing at
    # the same database never collide.
    RUN = uuid.uuid4().hex[:6]
    PREFIX = f"demo:{RUN}:"
    TTL = 3600  # a forgotten demo must not persist in someone's database

    IDX_MEM = f"demo_{RUN}_mem"
    IDX_CACHE = f"demo_{RUN}_cache"
    IDX_ROUTE = f"demo_{RUN}_route"

    TRACE: deque = deque(maxlen=300)

    # ---- safety guard ------------------------------------------------------
    # ACL SETUSER is unsupported on Redis Cloud, so scoping happens client-side.
    ALLOWED = {
        "PING", "INFO", "DBSIZE", "SCAN", "HSET", "HGETALL", "EXPIRE", "TTL",
        "TYPE", "OBJECT", "MEMORY", "LPUSH", "LTRIM", "LRANGE", "LLEN", "DEL",
        "UNLINK", "FT.CREATE", "FT.SEARCH", "FT.AGGREGATE", "FT.INFO",
        "FT._LIST", "FT.DROPINDEX",
    }
    # Where the key sits in argv. Subcommand forms (OBJECT ENCODING k,
    # MEMORY USAGE k) put the key at index 2, not 1.
    KEYPOS = {
        "HSET": 1, "HGETALL": 1, "EXPIRE": 1, "TTL": 1, "TYPE": 1, "LPUSH": 1,
        "LTRIM": 1, "LRANGE": 1, "LLEN": 1, "DEL": 1, "UNLINK": 1,
        "OBJECT": 2, "MEMORY": 2,
    }

    class Denied(Exception):
        """Raised locally -- the command never reaches the server."""

    def _txt(v):
        """Bytes -> str. The client is binary-safe because vectors are bytes."""
        if isinstance(v, bytes):
            return v.decode("utf-8", "replace")
        return v

    def _summarise(reply, limit=70):
        s = repr(_txt(reply)) if isinstance(reply, bytes) else repr(reply)
        return s if len(s) <= limit else s[: limit - 3] + "..."

    def _cli(args):
        """Render one command as copy-pasteable redis-cli syntax."""
        out = []
        for a in args:
            if isinstance(a, bytes) and len(a) > 64:
                out.append(f"<{len(a)} bytes>")  # elide binary vectors
            else:
                s = _txt(a) if isinstance(a, bytes) else str(a)
                out.append(f'"{s}"' if (" " in s or not s) else s)
        return " ".join(out)

    class GuardedTracedRedis(redis.Redis):
        """Reject out-of-scope commands, then time and record what we sent.

        Two things a naive tracer gets wrong: the reply must be bound inside
        `try` (a `finally` block cannot see it), and a raising command must be
        recorded as an error rather than as a plausible latency.
        """

        def _authorise(self, name, args):
            if name not in ALLOWED:
                raise Denied(f"{name} is outside this notebook's allowlist")
            pos = KEYPOS.get(name)
            if pos and len(args) > pos:
                key = _txt(args[pos])
                if isinstance(key, str) and not key.startswith(PREFIX):
                    raise Denied(f"key {key!r} is outside {PREFIX}*")

        def execute_command(self, *args, **kwargs):
            # Authorise inside the try so a refusal is traced too. A denied row
            # shows ~0.0 ms, which is the tell that it never left this process.
            t0 = time.perf_counter()
            try:
                self._authorise(str(args[0]).upper(), args)
                reply = super().execute_command(*args, **kwargs)
            except Exception as exc:
                TRACE.append(
                    (_cli(args), (time.perf_counter() - t0) * 1000, repr(exc), True)
                )
                raise
            ms = (time.perf_counter() - t0) * 1000
            TRACE.append((_cli(args), ms, _summarise(reply), False))
            return reply

    # ---- vectors -----------------------------------------------------------
    def _unit(v):
        v = np.asarray(v, dtype=np.float32)
        n = float(np.linalg.norm(v))
        return v / n if n else v

    def _blob(v):
        """FLOAT32 little-endian bytes -- what a VECTOR field actually stores."""
        return _unit(v).astype(np.float32).tobytes()

    def _hash_embed(text, dim=256):
        """Zero-download fallback: the hashing trick over word tokens.

        Lexical only. "seafood allergy" will NOT match "allergic to shellfish".
        This is the honest degraded mode, never the demo.
        """
        v = np.zeros(dim, dtype=np.float32)
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            h = int.from_bytes(
                hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big"
            )
            v[h % dim] += 1.0
        return _unit(v)

    def render_trace(n=12):
        rows = [
            {"command": c, "ms": round(ms, 1), "reply": r, "error": e}
            for c, ms, r, e in list(TRACE)[-n:][::-1]
        ]
        if not rows:
            return mo.md("_no commands yet_")
        return mo.vstack(
            [
                mo.ui.table(rows, selection=None, pagination=False),
                mo.md(
                    "<small>Latency is one incidental column, not the point. Row 1 "
                    "of a session includes TCP + TLS + AUTH. Pipelined commands do "
                    "not appear here at all: `Pipeline` overrides "
                    "`execute_command`, and `Redis.pipeline()` hardcodes "
                    "`Pipeline(self.connection_pool, ...)` rather than "
                    "`type(self)`.</small>"
                ),
            ]
        )


@app.cell
def _intro():
    mo.md(r"""
    # Redis Iris basics

    Three Iris components, built from raw Redis commands in one file.

    | Component | What it really is |
    |---|---|
    | **Agent Memory** | a `LIST` bounded by `LTRIM` (recency) + one `HASH` per memory with a vector field (recall) |
    | **LangCache** | the same `HASH` + vector, read with `VECTOR_RANGE` instead of `KNN` |
    | **Semantic Routing** | the same again, read with `FT.AGGREGATE` + `GROUPBY` |

    All three are `HASH` + `TEXT` + `VECTOR COSINE`. What differs is the field
    names, the index algorithm, the threshold, and **which query grammar reads
    it** -- that last one is the whole lesson.

    **Act 1** run it &middot; **Act 2** take it apart &middot; **Act 3** read the code.

    > Scope: this is the Redis mechanism. Agent Memory and LangCache are hosted
    > services that embed text server-side, so their own queries are not
    > observable from here -- we build an equivalent, we do not inspect theirs.
    """)
    return


@app.cell
def _connect_ui():
    url_in = mo.ui.text(
        value=os.environ.get("REDIS_URL", ""),
        placeholder="rediss://default:pw@host:port  |  redis://localhost:6379/0",
        kind="password",
        label="REDIS_URL",
        full_width=True,
    )
    connect_btn = mo.ui.run_button(label="Connect")
    mo.vstack(
        [
            url_in,
            connect_btn,
            mo.md(
                "<small>Redis Cloud is just the fast way to get a database. A local "
                "`redis://localhost:6379/0` works identically -- but the database "
                "needs **Search and query enabled at creation time**, which on "
                "Redis Cloud is a checkbox you cannot add afterwards.</small>"
            ),
        ]
    )
    return connect_btn, url_in


@app.cell
def _connect(connect_btn, url_in):
    mo.stop(not connect_btn.value, mo.md("_Paste a URL and press **Connect**._"))
    mo.stop(not url_in.value.strip(), mo.md("**REDIS_URL is empty.**"))

    # decode_responses stays False: vector fields are raw FLOAT32 bytes and a
    # UTF-8 decoder corrupts them. We decode strings ourselves via _txt().
    rds = GuardedTracedRedis.from_url(
        url_in.value.strip(), decode_responses=False, socket_timeout=10
    )
    rds.ping()
    _info = rds.info("server")

    _search_ok = True
    try:
        rds.execute_command("FT._LIST")
    except redis.ResponseError:
        _search_ok = False

    conn_ok = _search_ok
    mo.vstack(
        [
            mo.md(
                f"Connected -- Redis **{_txt(_info.get('redis_version'))}** "
                f"&middot; session prefix `{PREFIX}` &middot; "
                + ("Search **available**" if _search_ok else "Search **MISSING**")
            ),
            mo.md("")
            if _search_ok
            else mo.callout(
                mo.md(
                    "This database has no Search module, so every panel below will "
                    "fail. On Redis Cloud, Search must be enabled **when the "
                    "database is created** -- create a new one rather than trying "
                    "to add it."
                ),
                kind="danger",
            ),
        ]
    )
    return conn_ok, rds


@app.cell
def _embedder(conn_ok):
    mo.stop(not conn_ok)

    # One embedder serves all three components. No API key either way.
    try:
        from model2vec import StaticModel

        # force_download defaults to True in model2vec 0.9.0, which re-validates
        # all 10 snapshot files against the hub on every kernel start.
        _model = StaticModel.from_pretrained(
            "minishlab/potion-base-8M", force_download=False
        )

        def embed(text):
            return _unit(_model.encode([text])[0])

        embed_name = "potion-base-8M"
        embed_warn = None
    except Exception as _exc:  # no HF egress, no cache, or install failed
        def embed(text):
            return _hash_embed(text)

        embed_name = "hashing-fallback"
        embed_warn = str(_exc)[:160]

    DIM = int(len(embed("probe")))  # derived, never typed -- FT.CREATE needs it

    mo.vstack(
        [
            mo.md(
                f"Embedder: **{embed_name}** &middot; `DIM = {DIM}` &middot; "
                f"`{DIM * 4}` bytes per vector"
            ),
            mo.md("")
            if embed_warn is None
            else mo.callout(
                mo.md(
                    f"Fell back to lexical hashing (`{embed_warn}`). Paraphrase "
                    "recall **will not work** -- it matches words, not meaning. "
                    "Fix the model download before showing this to anyone."
                ),
                kind="warn",
            ),
        ]
    )
    return DIM, embed, embed_name


@app.cell
def _consent_ui(conn_ok, rds):
    mo.stop(not conn_ok)
    # Read-only reconnaissance before anything is written.
    _dbsize = rds.dbsize()
    _mine = rds.scan(cursor=0, match=f"{PREFIX}*", count=100)[1]
    seed_btn = mo.ui.run_button(label=f"Create 3 indexes + ~20 keys under {PREFIX}")
    mo.vstack(
        [
            mo.md(
                f"This database holds **{_dbsize}** keys. This notebook will add "
                f"~20 under `{PREFIX}` (each with a {TTL}s TTL) and create 3 "
                f"indexes. Existing keys under our prefix: **{len(_mine)}**."
            ),
            seed_btn,
        ]
    )
    return (seed_btn,)


@app.cell
def _seed(DIM, embed, rds, seed_btn):
    mo.stop(not seed_btn.value, mo.md("_Press the button above to seed._"))

    # ---- three index definitions: same family, three configurations --------
    schemas = {
        IDX_MEM: (
            f"{PREFIX}mem:",
            ["text", "TEXT", "owner", "TAG"],
            "HNSW",  # what the hosted Agent Memory index uses
        ),
        IDX_CACHE: (
            f"{PREFIX}cache:",
            ["prompt", "TEXT", "response", "TEXT"],
            "FLAT",  # redisvl's SemanticCache -- and it has no TAG field at all
        ),
        IDX_ROUTE: (
            f"{PREFIX}route:",
            ["reference", "TEXT", "route_name", "TAG"],
            "FLAT",
        ),
    }
    created = []
    for _idx, (_pfx, _fields, _algo) in schemas.items():
        try:
            rds.execute_command(
                "FT.CREATE", _idx, "ON", "HASH", "PREFIX", 1, _pfx, "SCHEMA",
                *_fields,
                "vector", "VECTOR", _algo, 6,
                "TYPE", "FLOAT32", "DIM", DIM, "DISTANCE_METRIC", "COSINE",
            )
            created.append(_idx)
        except redis.ResponseError as _e:
            if "Index already exists" not in str(_e):
                raise

    MEMORIES = [
        "allergic to shellfish",
        "prefers window seats",
        "no dairy after 8pm",
        "travels with a service dog",
        "vegetarian on weekdays",
    ]
    CACHED = [
        (
            "What are your fixed deposit rates?",
            "Our 12-month fixed deposit pays 3.8% p.a.",
        )
    ]
    ROUTES = {
        "banking": [
            "what is my account balance",
            "transfer money to savings",
            "open a fixed deposit",
            "my card was declined",
        ],
        "off_topic": [
            "what is the weather tomorrow",
            "recommend a good restaurant",
            "who won the football match",
        ],
    }

    def _write(pipe, key, mapping):
        pipe.hset(key, mapping=mapping)
        pipe.expire(key, TTL)

    # One pipelined burst. Note this is invisible to the tracer by design --
    # Pipeline overrides execute_command, so the guard does not see it either.
    _p = rds.pipeline(transaction=False)
    for _t in MEMORIES:
        _write(
            _p,
            f"{PREFIX}mem:{hashlib.sha1(_t.encode()).hexdigest()[:12]}",
            {"text": _t, "owner": "alice", "vector": _blob(embed(_t))},
        )
    for _q, _a in CACHED:
        _write(
            _p,
            f"{PREFIX}cache:{hashlib.sha1(_q.encode()).hexdigest()[:12]}",
            {"prompt": _q, "response": _a, "vector": _blob(embed(_q))},
        )
    for _route, _refs in ROUTES.items():
        for _ref in _refs:
            _write(
                _p,
                f"{PREFIX}route:{hashlib.sha1(_ref.encode()).hexdigest()[:12]}",
                {
                    "reference": _ref,
                    "route_name": _route,
                    "vector": _blob(embed(_ref)),
                },
            )
    _p.execute()

    seeded = True
    mo.vstack(
        [
            mo.md(
                f"Seeded **{len(MEMORIES)}** memories, **{len(CACHED)}** cache "
                f"entry, **{sum(len(v) for v in ROUTES.values())}** route "
                f"references. Indexes created: "
                f"`{', '.join(created) or 'already existed'}`."
            ),
            mo.accordion(
                {
                    "The FT.CREATE we just issued (three configurations, one grammar)": mo.md(
                        "```\n"
                        + "\n\n".join(
                            f"FT.CREATE {i} ON HASH PREFIX 1 {p} SCHEMA "
                            f"{' '.join(map(str, f))} vector VECTOR {a} 6 "
                            f"TYPE FLOAT32 DIM {DIM} DISTANCE_METRIC COSINE"
                            for i, (p, f, a) in schemas.items()
                        )
                        + "\n```\n"
                        "`HASH` + `TEXT` + `VECTOR COSINE` is common to all three. "
                        "`HNSW` vs `FLAT`, the TAG fields and the field names are "
                        "the only differences -- the cache genuinely has no TAG "
                        "field."
                    )
                }
            ),
        ]
    )
    return ROUTES, seeded


@app.cell
def _bench_ui(seeded):
    mo.stop(not seeded)
    ask_in = mo.ui.text(
        value="does the customer have any seafood restrictions?",
        label="Ask",
        full_width=True,
    )
    ask_btn = mo.ui.run_button(label="Recall")
    mo.vstack([mo.md("## Act 1 -- run it"), ask_in, ask_btn])
    return ask_btn, ask_in


@app.cell
def _recall(ask_btn, ask_in, embed, rds):
    mo.stop(not ask_btn.value, mo.md("_Press **Recall**._"))
    _q = ask_in.value

    # KNN top-k: the Agent Memory recall shape.
    _res = rds.execute_command(
        "FT.SEARCH", IDX_MEM,
        "*=>[KNN 3 @vector $vec AS dist]",
        "PARAMS", 2, "vec", _blob(embed(_q)),
        "SORTBY", "dist", "ASC",
        "RETURN", 2, "text", "dist",
        "DIALECT", 2,
    )
    _hits = []
    for _i in range(1, len(_res), 2):
        _flat = _res[_i + 1]
        _f = {
            _txt(_flat[_j]): _txt(_flat[_j + 1]) for _j in range(0, len(_flat), 2)
        }
        _hits.append((_f.get("text", ""), float(_f.get("dist", "nan"))))

    mo.vstack(
        [
            mo.md(
                f"**Recalled {len(_hits)} of 5** for _{_q}_\n\n"
                + "\n".join(f"- `{d:.3f}` &middot; {t}" for t, d in _hits)
            ),
            mo.md(
                "<small>Zero words in common with the stored memory, and the "
                "number beside each hit is arithmetic you can check. Lower "
                "distance = closer.</small>"
            ),
            mo.accordion({"Commands": render_trace(4)}),
        ]
    )
    return


@app.cell
def _grammars_ui(seeded):
    mo.stop(not seeded)
    probe_in = mo.ui.text(
        value="how much do fixed deposits pay?", label="Cache probe", full_width=True
    )
    radius_in = mo.ui.slider(
        start=0.05, stop=1.0, step=0.05, value=0.35, label="VECTOR_RANGE radius"
    )
    probe_btn = mo.ui.run_button(label="Run both grammars")
    mo.vstack(
        [
            mo.md(
                """
                ## Act 2 -- take it apart

                ### Two query grammars, and the MISS is the grammar

                `KNN 3` **always returns 3 rows**, so a KNN-backed cache can never
                miss. `VECTOR_RANGE` returns **zero rows** when nothing is inside
                the radius -- and that is the only reason a cache MISS is
                expressible at all.
                """
            ),
            probe_in,
            radius_in,
            probe_btn,
        ]
    )
    return probe_btn, probe_in, radius_in


@app.cell
def _grammars(embed, probe_btn, probe_in, radius_in, rds):
    mo.stop(not probe_btn.value, mo.md("_Press **Run both grammars**._"))
    _v = _blob(embed(probe_in.value))

    _knn = rds.execute_command(
        "FT.SEARCH", IDX_CACHE, "*=>[KNN 3 @vector $vec AS dist]",
        "PARAMS", 2, "vec", _v,
        "SORTBY", "dist", "ASC",
        "RETURN", 2, "prompt", "dist",
        "DIALECT", 2,
    )
    _rng = rds.execute_command(
        "FT.SEARCH", IDX_CACHE,
        "@vector:[VECTOR_RANGE $r $vec]=>{$YIELD_DISTANCE_AS: dist}",
        "PARAMS", 4, "r", radius_in.value, "vec", _v,
        "RETURN", 2, "response", "dist",
        "DIALECT", 2,
    )
    _hit = _rng[0] > 0

    mo.vstack(
        [
            mo.md(
                f"| grammar | rows returned | verdict |\n"
                f"|---|---|---|\n"
                f"| `*=>[KNN 3 @vector $vec AS dist]` | **{_knn[0]}** | "
                f"cannot express a miss |\n"
                f"| `@vector:[VECTOR_RANGE $r $vec]` at r={radius_in.value} | "
                f"**{_rng[0]}** | {'**HIT**' if _hit else '**MISS**'} |"
            ),
            mo.md(
                f"> {_txt(_rng[2][1])}"
                if _hit
                else "> _Nothing inside the radius. Widen it and the same query "
                "hits -- which is what a threshold is: a policy choice, not a "
                "fact._"
            ),
            mo.md(
                "<small>A threshold set too loose returns a confidently wrong "
                "cached answer. That trade -- not speed -- is what a semantic "
                "cache is.</small>"
            ),
        ]
    )
    return


@app.cell
def _router_ui(seeded):
    mo.stop(not seeded)
    route_in = mo.ui.text(
        value="can you recommend somewhere for dinner", label="Route", full_width=True
    )
    agg_in = mo.ui.dropdown(options=["MIN", "AVG"], value="MIN", label="REDUCE")
    route_btn = mo.ui.run_button(label="Classify")
    mo.vstack(
        [
            mo.md(
                """
                ### A router is a GROUP BY

                One radius for the query, set to `max(per-route thresholds)`. The
                per-route thresholds are then applied **afterwards**, in a `FILTER`
                over the grouped result. That split is the non-obvious part.
                """
            ),
            route_in,
            agg_in,
            route_btn,
        ]
    )
    return agg_in, route_btn, route_in


@app.cell
def _router(ROUTES, agg_in, embed, rds, route_btn, route_in):
    mo.stop(not route_btn.value, mo.md("_Press **Classify**._"))

    THRESH = {"banking": 0.7, "off_topic": 0.5}
    _radius = max(THRESH.values())
    _filter = " || ".join(
        f"(@route_name == '{r}' && @distance < {t})" for r, t in THRESH.items()
    )
    _args = [
        "FT.AGGREGATE", IDX_ROUTE,
        "@vector:[VECTOR_RANGE $r $vec]=>{$YIELD_DISTANCE_AS: vector_distance}",
        "GROUPBY", 1, "@route_name",
        "REDUCE", agg_in.value, 1, "@vector_distance", "AS", "distance",
        "SORTBY", 2, "@distance", "ASC", "MAX", 1,
        "FILTER", _filter,
        "PARAMS", 4, "r", _radius, "vec", _blob(embed(route_in.value)),
        "DIALECT", 2,
    ]
    _res = rds.execute_command(*_args)
    _rows = []
    for _row in _res[1:]:
        _rows.append(
            {_txt(_row[_i]): _txt(_row[_i + 1]) for _i in range(0, len(_row), 2)}
        )

    _winner = _rows[0]["route_name"] if _rows else None
    mo.vstack(
        [
            mo.md(
                (
                    f"### Route: **{_winner}** &middot; distance "
                    f"`{float(_rows[0]['distance']):.3f}`"
                )
                if _winner
                else "### **Blocked** -- no route inside its threshold"
            ),
            mo.md("```\n" + _cli(_args) + "\n```"),
            mo.md(
                f"<small>{sum(len(v) for v in ROUTES.values())} reference phrases, "
                f"{len(ROUTES)} routes. Switch `REDUCE` to `AVG` and a query near "
                "one strong example loses to a route whose examples are uniformly "
                "mediocre -- which is why production picks `MIN`.</small>"
            ),
        ]
    )
    return


@app.cell
def _walkthrough(DIM, embed_name, seeded):
    mo.stop(not seeded)
    mo.md(
        f"""
        ## Act 3 -- read the code

        Everything above is three functions over one schema. Open this file in
        `marimo edit` and read the cells in order; the parts worth knowing:

        | Step | Cell | The point |
        |---|---|---|
        | 1 | `_connect` | `decode_responses=False` -- vector fields are raw `FLOAT32` bytes and a UTF-8 decoder corrupts them |
        | 2 | `_embedder` | `DIM` is **derived** (`len(embed("probe"))`), never typed. Here: `{DIM}` from `{embed_name}` |
        | 3 | `_seed` | one `FT.CREATE` shape, three configurations |
        | 4 | `_recall` | `KNN` -- ranking |
        | 5 | `_grammars` | `VECTOR_RANGE` -- thresholding |
        | 6 | `_router` | `FT.AGGREGATE` + `GROUPBY` -- argmin over labelled sets |

        ### Where this stops, and the product starts

        | | this notebook | the Iris service |
        |---|---|---|
        | **Agent Memory** | you embed, you `HSET`, you `KNN` | extracts memories from session events, summarises older ones, sensitive-data exclusions, per-tier retention, multi-session recall |
        | **LangCache** | your index at a radius you picked | managed embeddings, TTL and eviction policy, hit metrics, hybrid exact+semantic, attribute-scoped invalidation |
        | **Context Retriever** | *not built here* | per-agent keys whose access tags filter what each agent may see -- **server-side, row-scoped**, and not reproducible on Redis Cloud, where `ACL SETUSER` is unsupported |

        Redis's own docs take this position: their DIY memory-layer guide builds it
        "using only standard Redis commands -- no Redis Agent Memory SDK, no
        managed service", then names the product as the production path.

        Two honest limits. Different embedders cannot share an index: `DIM` is
        fixed at `FT.CREATE`, so this notebook's `{DIM}`-dim index cannot read an
        Iris-seeded one. And with no LLM here, **memory quality** -- what is worth
        remembering, and summarisation -- is not demonstrable at all.
        """
    )
    return


@app.cell
def _trace_panel(seeded):
    mo.stop(not seeded)
    mo.vstack([mo.md("## Command trace"), render_trace(14)])
    return


@app.cell
def _teardown_ui(seeded):
    mo.stop(not seeded)
    drop_btn = mo.ui.run_button(label=f"Delete everything under {PREFIX}")
    mo.vstack(
        [
            mo.md("## Teardown"),
            mo.md(
                f"Every key already carries a {TTL}s TTL, so doing nothing is also "
                "safe. Indexes do not expire, so this drops them explicitly."
            ),
            drop_btn,
        ]
    )
    return (drop_btn,)


@app.cell
def _teardown(drop_btn, rds):
    mo.stop(not drop_btn.value)
    _n = 0
    _cur = 0
    while True:
        _cur, _keys = rds.scan(cursor=_cur, match=f"{PREFIX}*", count=200)
        if _keys:
            rds.unlink(*_keys)
            _n += len(_keys)
        if _cur == 0:
            break
    for _idx in (IDX_MEM, IDX_CACHE, IDX_ROUTE):
        try:
            rds.execute_command("FT.DROPINDEX", _idx)
        except redis.ResponseError:
            pass
    mo.md(f"Deleted **{_n}** keys and 3 indexes. Database is as you found it.")
    return


if __name__ == "__main__":
    app.run()
