# MVP build plan B: teaching-first

Date 2026-09-30. Baseline commit 37a6e0f on `main`. This plan is the contract for one scaffold
agent and four column builders working in parallel on `iris_basics.py`. It is written from the
plan-conformance review (`.agents/reviews/plan-conformance-2026-09-30.md`, section 7 keep list)
and the six research briefs in `.agents/research/`. Every request and command shape below is
one that a brief tags VERIFIED or that the 2026-09-30 live run recorded; the few UNVERIFIED
shapes are marked inline. No network call was made while writing this.

## 0. The angle, and what it costs

Optimise for the strongest teaching moment per component and let everything else be plain.
Each column keeps exactly one swept or slid parameter that teaches architecture, one bridge to
the next column, and one wow that fits in two cells.

| Column | Swept parameter | Bridge | Wow (2 cells) |
|---|---|---|---|
| 0 Redis Search | `radius` on `VECTOR_RANGE` beside `k` on KNN (with an optional TAG pre-filter in the same query) | a radius MISS is a cache MISS | one question through BM25, KNN and `FT.HYBRID` RRF, rank table with the target starred |
| 1 LangCache | `similarityThreshold` re-applied on your machine over six probes searched once | the cache-aside loop is where an LLM would run | miss, teach with `ttlMillis` 10 s, paraphrase HIT, expiry MISS, `DELETE` answers `404` |
| 2 Context Retriever | one field's index kind (`Customer.name`: text, tag, not indexed) changing the exported model, offline | the tools an agent calls are the index kinds you declared | `tools/list` returning the real generated tools, then one accepted and one rejected `tools/call` |
| 3 Agent Memory | `similarityThreshold` as a live re-query dial over a score-less reply | a filter is the tenant boundary | the peanut-allergy memory recalled with 0 words in common, then assembled into the prompt an agent would receive |

Everything not in that table is MVP-plain: connect, seed behind a button, clean up. Budget:
about 1,470 lines and 46 cells (45 column cells plus the intro), against the orchestrator's
"about 1,500 and about 45".

## 1. File tree

```
redis-iris-basics/
  pyproject.toml              full text in section 2; the only dependency list
  uv.lock                     committed; produced by `uv lock`
  .python-version             "3.13"
  .env.example                ten names, comments on their own lines (section 3)
  .gitignore                  section 3
  .gitattributes              unchanged (LF, *.png binary)
  README.md                   rewritten to the tutorial template (section 11)
  iris_basics.py              the notebook, no PEP 723 header (project mode)
  src/cairn_support/
    __init__.py               empty docstring only
    sample_data.py            Cairn Outfitters content as Python literals
    embedder.py               potion-base-8M loader and encoder
    text.py                   the crude stemmer and shared-stem counter
    display.py                redis-cli formatter, connection-config mask, two marimo one-liners
  tests/
    test_notebook.py          app.run() with no credentials; marimo check --strict; column layout
    test_safety.py            text and AST rules on iris_basics.py (no flush, no DD, TTL on every set, ...)
    test_cairn_support.py     unit tests for the package
  .agents/plans/              kept as history (this file lives in plans/candidates/)
  .agents/plans/research/     kept
  .agents/research/           kept
  .agents/reviews/            kept
```

Deleted by the scaffold agent: `legacy/` (5 files), `tests/` (16 files, 16,833 lines, all coupled
to the setup block), `verify.py`, `.agents/build/` (125 files including the screenshots),
`__marimo__/` (already gitignored; delete the folder). `.gitignore` loses its `.agents/build/`
line.

## 2. `pyproject.toml` (full text)

```toml
[project]
name = "redis-iris-basics"
version = "0.2.0"
description = "Redis Iris basics: one marimo notebook that takes Redis Search, LangCache, Context Retriever and Agent Memory apart on Cairn Outfitters sample data."
readme = "README.md"
# <3.14: redis-context-retriever 0.0.6's ContextModel metaclass reads namespace["__annotations__"],
#        which Python 3.14's deferred annotations leave empty inside marimo cells
#        (PydanticUserError "Field 'customer_id' requires a type annotation"; checked 2026-09-30 on 3.14.6).
# >=3.11: redis-context-retriever itself requires 3.11.
requires-python = ">=3.11,<3.14"
dependencies = [
  "marimo==0.25.0",
  "redis==8.1.0",
  "niquests==3.21.2",
  "redis-context-retriever==0.0.6",  # import name: context_surfaces; pulls httpx, fastmcp, pydantic, keyring
  "model2vec==0.9.0",
  # numpy 2.5.x needs Python >=3.12; 2.4.6 is the last release that supports 3.11.
  "numpy==2.5.3; python_version >= '3.12'",
  "numpy==2.4.6; python_version < '3.12'",
  # tokenizers (pulled in by model2vec) caps huggingface-hub at <2.0; 1.33.0 is the last 1.x (2026-09-24).
  "huggingface-hub==1.33.0",
  # marimo parses .env with python-dotenv when it is installed; its fallback parser keeps an
  # inline "# comment" as part of the value.
  "python-dotenv==1.2.3",
]

[dependency-groups]
dev = ["pytest==9.1.1"]

[build-system]
requires = ["uv_build>=0.11.25,<0.12.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
# The distribution is redis-iris-basics; the importable package is cairn_support.
module-name = "cairn_support"

[tool.marimo.runtime]
# The default is already [".env"]; stating it documents where configuration comes from and
# makes the editor's Secrets panel write new keys to this file.
dotenv = [".env"]
# Pick up edits to src/cairn_support without restarting the kernel.
auto_reload = "lazy"

[tool.marimo.package_management]
manager = "uv"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
filterwarnings = [
  # redis-context-retriever 0.0.6's own models emit about 14 of these on import.
  "ignore::pydantic.warnings.PydanticDeprecatedSince20",
]
```

Facts behind the pins are in `.agents/research/niquests-and-packaging-2026-09-30.md` part B.
`redis-agent-memory` and `anywidget` are deliberately absent: Agent Memory is called over REST so
each JSON body is visible, and no custom widget is needed (`mo.ui.anywidget` stays available if
one ever is). httpx remains in `uv.lock` as a transitive dependency of the Context Retriever SDK
and huggingface-hub; the README must not claim it is gone.

## 3. `.env.example`, `.python-version`, `.gitignore`

`.python-version` contains `3.13`.

`.env.example` (comments on their own lines, nothing prefilled, ten names):

```
# Redis Iris basics: settings. Copy this file to .env next to pyproject.toml and fill in the
# columns you want to run. marimo loads .env into the environment when the kernel starts; a
# variable already set in your shell wins over the file. A column whose variables are missing
# shows one "not configured" note and stops. Console: https://cloud.redis.io

# ---- Column 0, Redis Search (also the database Context Retriever loads into) ----
# Databases > your database > Configuration > Public endpoint; Security > Default user (eye icon).
# The free tier has no TLS, so the scheme is redis://, not rediss://.
# REDIS_URL=redis://default:<password>@<host>:<port>

# ---- Column 1, LangCache ----
# LangCache > your service > Configuration > Connectivity. The API key is shown once, at creation.
# Use a service of your own: a cache is shared by everything that points at it.
# LANGCACHE_HOST=https://<region>.langcache.redis.io
# LANGCACHE_CACHE_ID=
# LANGCACHE_API_KEY=

# ---- Column 2, Context Retriever ----
# Context Retriever > Admin keys > New admin key (shown once). Also needs REDIS_URL above, on a
# database with Search and JSON that the hosted service can reach. It is given the password.
# CTX_ADMIN_KEY=
# Optional. Defaults suit Redis Cloud; the MCP address is read from the agent-key listing first.
# CTX_API_URL=https://cloud.redis.io/context-surfaces
# CTX_MCP_URL=https://gcp-us-east4.context-surfaces.redis.io/mcp

# ---- Column 3, Agent Memory ----
# Agent Memory > your service > Configuration > General settings: Endpoint (with https://) and
# Store ID. API keys tab > New API key, shown once.
# MEMORY_API_BASE_URL=https://<endpoint>
# MEMORY_STORE_ID=
# MEMORY_API_KEY=
```

`.gitignore`:

```
# Secrets and per-machine settings (marimo loads .env from this folder)
.env
.env.*
!.env.example

# uv-managed environment and Python caches
.venv/
__pycache__/
.pytest_cache/
*.egg-info/
dist/
build/

# marimo's per-notebook session cache and HTML exports
__marimo__/
*.html

# Editor and OS noise
.idea/
.vscode/
.DS_Store
Thumbs.db
```

## 4. The setup cell (exact contents)

`with app.setup(hide_code=True):` holds imports, the run tag and its two derived constants, one
niquests call helper and one Redis connect helper. Nothing else. Per-column sessions, TTLs and
index names live in the column's configure cell so the reader meets them where they are used.

```python
import marimo

__generated_with = "0.25.0"
app = marimo.App(width="columns", app_title="Redis Iris basics")

with app.setup(hide_code=True):
    import json
    import os
    import re
    import sys
    import time
    import uuid
    from datetime import datetime, timedelta, timezone

    import marimo as mo
    import niquests
    import numpy as np
    import redis
    from redis.backoff import ExponentialBackoff
    from redis.retry import Retry

    from cairn_support import display
    from cairn_support import sample_data as cairn
    from cairn_support import text as cairn_text
    from cairn_support.embedder import EMBEDDING_MODEL_NAME, embed_texts, load_embedding_model

    # One tag for everything this kernel writes. Every Redis key, surface name, owner id and
    # memory id carries it, so each column's Clean up can find its own work without a ledger.
    RUN_TAG = uuid.uuid4().hex[:8]
    KEY_PREFIX = f"cairn:{RUN_TAG}:"   # every Redis key written by this notebook or by Context Retriever
    KEY_TTL_SECONDS = 3600             # Redis keys and the agent key expire on their own; Clean up only shortens that

    MASKED_HEADERS = {"authorization", "x-api-key"}

    def call_service(service, method, path, *, json_body=None, params=None):
        """One request to a hosted Iris service: (status, parsed body or None, wire).

        `wire` is what went on the wire with the credential header masked, so it is safe to show.
        Errors are not raised: every 4xx here carries a JSON body (type, title, detail) that is
        the teaching content, so each cell branches on the status itself.
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
```

Rules the setup enforces by omission: no secret is ever bound to a global (URLs and keys are
read from `os.environ` inside the expression that builds a client or session, because the
Variables panel broadcasts the first 50 characters of any string global); no `mo.ui.text(kind=
"password")`; no `mo.state`; no `mo.ui.chat` (broken in 0.25.0).

## 5. `src/cairn_support`: modules and signatures

Only content that is not about Redis Iris goes here. If a line would appear on a Redis docs page
for one of the four products (a command, a request body, an index schema, a key template, a
`ContextModel`), it stays in the notebook.

### `sample_data.py` (Python literals; no functions except the two builders)

| Name | Shape | Source in the current notebook |
|---|---|---|
| `HELP_ARTICLES: list[dict]` | 36 rows, keys `id, title, body, category, year`; six categories, six each | `rs_articles` 1912-2029, verbatim |
| `SEARCH_PRESETS: list[dict]` | 3 rows, keys `label, query, target, lesson` | `rs_wow_presets` 2031-2058, verbatim |
| `GEAR_CARE_ANSWERS: list[dict]` | 5 rows, keys `key, prompt, response` | `lc_seeds` 2656-2680 |
| `THRESHOLD_PROBES: list[dict]` | 6 rows, keys `text, kind, target` (target is a `GEAR_CARE_ANSWERS` key or `None`) | six of `lc_probes` 2682-2704: goose-down paraphrase (bag), tent paraphrase (tent), synthetic bag (None), kayak (None), negation (None), capital of Australia (None) |
| `STRATEGY_VARIANTS: list[dict]` | 3 rows, keys `label, text`: shouting, punctuation, paraphrase | `lc_variants` 2706-2716 |
| `CACHE_ASIDE_SCRIPT: dict` | keys `ask, answer, again` | `lc_script` 2718-2725, verbatim |
| `SHOP_RECORDS: dict[str, list[dict]]` | `Customer` 6, `Product` 6, `Order` 8 | `cr_records` 3378-3411, verbatim |
| `DANA_FACTS: list[dict]` | 5 rows, keys `text, topics` (no id, owner or namespace: those are Iris fields the notebook adds) | `am_memories` 4789-4817 |
| `MEMORY_QUESTIONS: list[dict]` | 3 rows, keys `text, target` (1-based index into `DANA_FACTS`) | `am_wow_presets` 4836-4865 |
| `SESSION_TURNS: list[dict]` | 3 rows, keys `role, text`: USER "I am training for a 50 kilometre trail race in April.", ASSISTANT "Good to know. Which trail shoes are you training in?", USER "What should the kitchen avoid serving me?" | new; the last turn is `MEMORY_QUESTIONS[0]` |

Module docstring states the design constraints the tests enforce: no article says "cramped" or
"shoes", exactly one says "Trailhead", none says "waterproofng"; every LangCache prompt names
gear; every shop id is letters plus digits with no hyphen; each memory question shares no stem
with its target fact.

### `embedder.py`

```python
EMBEDDING_MODEL_NAME = "minishlab/potion-base-8M"
EMBEDDING_MODEL_FILES = ["config.json", "model.safetensors", "tokenizer.json"]

def load_embedding_model() -> "StaticModel":
    """Load potion-base-8M from the Hugging Face cache, downloading about 30 MB the first time.

    Tries `snapshot_download(..., local_files_only=True)` first so a warm machine never touches
    the network, then the online call with the same `allow_patterns`. Raises RuntimeError with a
    one-sentence cause when neither works; there is no degraded fallback, because a word-hashing
    embedder would make the paraphrase examples lie.
    """

def embed_texts(model, texts: list[str]) -> "np.ndarray":
    """Encode texts to a (n, model.dim) float32 array of unit-length vectors."""
```

`os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")` at import (Windows). The
`.astype(np.float32).tobytes()` step stays in the notebook: "a vector is DIM x 4 raw bytes in a
hash field" is a Redis lesson.

### `text.py`

```python
def content_stems(text: str) -> list[str]:
    """Lower-cased words minus common ones, with -ing, -ed, -es or -s cut off. Crude on purpose."""

def shared_stems(question: str, memory: str) -> list[str]:
    """The stems two texts share, sorted; [] is the 'recalled by meaning' evidence."""
```

Ported from `am_stems`/`am_overlap` 4880-4904.

### `display.py`

```python
def redis_cli(argv: list) -> str:
    """One command as copy-pasteable redis-cli syntax: bytes longer than 64 shown as <N bytes>,
    text longer than 120 characters cut with a <N chars> marker, arguments with spaces quoted."""

def mask_connection_config(body: dict) -> dict:
    """A deep copy of a Context Retriever request body with every data_source.connection_config
    value except addr replaced by '****'."""

def not_configured(column_title: str, variable_names: list[str]) -> "mo.Html":
    """The one warn callout a column shows when its variables are missing: names the variables,
    says to copy .env.example to .env next to pyproject.toml, and where the console shows them."""

def request_and_reply(wire: dict, body) -> "mo.Html":
    """mo.accordion({'Request (credential masked)': mo.json(wire), 'Reply': mo.json(body or {})})."""
```

`redis_cli` is ported from 1153-1166 minus the scrub call (nothing typed-in exists to scrub).
`display.py` imports marimo; nothing in it knows a Redis command, a URL or a field name.

## 6. The intro cell

`@app.cell(column=0, hide_code=True)`, one `mo.md(r"""...""")`, about 30 lines:

```
# Redis Iris basics

An agent is only as good as the context it is handed, and that context has to be fetched,
cached, governed and remembered somewhere fast. Redis Iris is the real-time context engine that
does those four jobs: Redis Search underneath, then LangCache, Context Retriever and Agent
Memory as hosted services on Redis Cloud. This notebook takes them apart one column at a time,
on Cairn Outfitters, a fictional outdoor-gear shop, and shows the exact `redis-cli` command or
JSON request behind every step. There is no LLM anywhere in it.

| Column | Service | What you do |
|---|---|---|
| 0 | Redis Search | index 36 help articles, then run one question as keyword, vector and hybrid search |
| 1 | LangCache | store five answers, then turn the similarity threshold and watch hits become wrong answers |
| 2 | Context Retriever | declare a data model, create a surface, call the tools it generated, look at your own Redis |
| 3 | Agent Memory | write five facts about a customer, recall one with no word in common, assemble the prompt |

**Configuration.** Copy `.env.example` to `.env` next to `pyproject.toml`; marimo loads it when
the kernel starts. A column whose variables are missing says so and stops.

**Safety.** Everything this kernel writes carries the run tag `<RUN_TAG>`: Redis keys under
`cairn:<RUN_TAG>:` with a one-hour `EXPIRE`, LangCache entries with `ttlMillis`, a surface named
`iris-basics-<RUN_TAG>`, memories owned by `ib-<RUN_TAG>`. Each column ends with one Clean up
button that deletes only that. Nothing is written until you press a button that says what it
writes.

Docs: [Redis Iris](https://redis.io/docs/latest/develop/ai/context-engine/)
```

The run tag is interpolated with an f-string; nothing else is dynamic.

## 7. Column 0, Redis Search (11 cells, about 330 lines)

All cells `@app.cell(column=0)`; markdown-only cells add `hide_code=True`. Redis Search imports
(`TextField, TagField, NumericField, VectorField, IndexDefinition, IndexType, Query,
ResponseError`) sit at the top of cell 2 so the reader sees where they come from.

**1. `rs_title`** (markdown, hide_code). Renders `mo.vstack([mo.md, mo.mermaid])`. Lead copy:
"An agent's context has to come from somewhere, and for the other three Iris services that
somewhere is Redis Search. You describe an index once with `FT.CREATE`, it watches a key prefix,
and every `HSET` under that prefix is searchable by keyword, by vector, or both in one command.
This column loads a 36-article help centre for Cairn Outfitters and runs one question through
each engine. Text is embedded on this machine by a small local model, so the only credential is
the database URL." Docs link `https://redis.io/docs/latest/develop/ai/search-and-query/` and
`https://redis.io/try-free/`. Mermaid `flowchart LR`: `App -->|embed locally| Vector -->|HSET|
Hash under a prefix -.->|index watches the prefix| FT index`; `App -->|FT.SEARCH| FT index`.
About 28 lines.

**2. `rs_connect`.** Globals: `rs_client`, `rs_redis_version`, `rs_has_hybrid`, `rs_index_name`
(`f"idx:cairn_help:{RUN_TAG}"`), `rs_key_prefix` (`f"{KEY_PREFIX}help:"`), `rs_article_keys`
(the 36 key names, computed from `cairn.HELP_ARTICLES`, so Clean up never scans). UI: none.
Calls: `mo.stop(not os.environ.get("REDIS_URL"), display.not_configured("Redis Search",
["REDIS_URL"]))`; `rs_client = connect_redis(os.environ["REDIS_URL"])` (never a URL global);
`rs_client.ping()`; `rs_client.info("server")["redis_version"]`; `rs_has_hybrid` = version tuple
`>= (8, 4)`. Wire: `PING`, `INFO server`. `except redis.RedisError as exc: mo.stop(True,
mo.callout(f"{type(exc).__name__}: check REDIS_URL", kind="danger"))` (never `str(exc)`, which
can echo the URL). Renders `mo.callout(kind="success")`: "Connected to Redis 8.6.2. `FT.HYBRID`
is available." or "... needs Redis 8.4 or later; the hybrid column below becomes a note." Lead-in:
"The cell below opens one connection with the redis-py production settings and asks the server
its version: Redis answers `PING` with `PONG` and `INFO server` with `redis_version`." About 30
lines.

**3. `rs_embedder`.** Globals: `rs_embedding_model`, `rs_embedding_dimension`,
`rs_article_vectors` (36 x 256 float32), `def rs_query_vector(text) -> bytes` (the
`.astype(np.float32).tobytes()` step, visible). Depends on `rs_redis_version` through its output
sentence, so it never runs, and never downloads, when the column is not configured. Calls:
`with mo.status.spinner(title=f"Loading {EMBEDDING_MODEL_NAME}", subtitle="about 30 MB the first
time")`: `load_embedding_model()`, `embed_texts(model, [f"{a['title']}. {a['body']}" for a in
cairn.HELP_ARTICLES])`. Renders one `mo.md` line: "potion-base-8M: 256 dimensions, 1024 bytes per
vector, computed on this machine. Redis 8.6.2 stores them as a `VECTOR` field in each hash."
Lead-in: "The cell below embeds every article once with a local model, so no vector ever leaves
this machine except as bytes inside an `HSET`:". About 20 lines.

**4. `rs_load_button`** (markdown + button). Globals: `rs_load_button = mo.ui.run_button(
label="Create the index and load 36 articles", kind="success")`. Lead-in: "The button below
writes one index and 36 hashes to your database, every hash with a one-hour `EXPIRE`: the index
declares four field types (`TEXT` with a title weight of 2, `TAG`, `NUMERIC` and a 256-dimension
`VECTOR` using HNSW and cosine distance) and watches the prefix `cairn:<run>:help:`, so each
`HSET` under it is indexed on write." About 14 lines.

**5. `rs_load`.** Globals: `rs_schema`, `rs_index_definition`, `rs_index_info`,
`rs_documents_indexed`. Calls: `mo.stop(not rs_load_button.value, mo.callout("Press the button
above to load the help centre.", kind="info"))`; `rs_client.ft(rs_index_name).create_index(
rs_schema, definition=IndexDefinition(prefix=[rs_key_prefix], index_type=IndexType.HASH))` with
`except ResponseError` tolerated when `"already exists" in str(exc).lower()`; three
`rs_client.pipeline(transaction=False)` chunks of 12 documents, each `pipe.hset(key, mapping=
{title, body, category, year, "embedding": vector.astype(np.float32).tobytes()})` then
`pipe.expire(key, KEY_TTL_SECONDS)`, `pipe.execute()`, `time.sleep(0.25)`;
`rs_client.ft(rs_index_name).info()`. Wire: `FT.CREATE idx:cairn_help:<run> ON HASH PREFIX 1
cairn:<run>:help: SCORE 1.0 SCHEMA title TEXT WEIGHT 2.0 body TEXT WEIGHT 1.0 category TAG
SEPARATOR , year NUMERIC SORTABLE embedding VECTOR HNSW 6 TYPE FLOAT32 DIM 256 DISTANCE_METRIC
COSINE`; 36 x (`HSET ... embedding "<1024 bytes>"`, `EXPIRE key 3600`); `FT.INFO`. Renders
`mo.vstack`: `mo.hstack([mo.stat(num_docs, label="Documents indexed"), mo.stat(
hash_indexing_failures, label="Indexing failures")])` and a fenced `mo.md` block with two lines
from `display.redis_cli`: the `FT.CREATE` rebuilt as `["FT.CREATE", rs_index_name,
*rs_index_definition.args, "SCHEMA", *[arg for field in rs_schema for arg in
field.redis_args()]]` (builder: confirm `Field.redis_args()` and `IndexDefinition.args` by
introspection on 8.1.0; if absent, rebuild the argv by hand from the same values) and the first
`HSET` with the vector shown as `<1024 bytes>`. Lead-in: "Redis returns `OK` for the `FT.CREATE`
and counts each hash as it lands; `FT.INFO` reports `num_docs` 36 and `hash_indexing_failures`
0, which is how you know every vector was exactly 1024 bytes." About 45 lines.

**6. `rs_search_controls`** (markdown + inputs). Globals: `rs_question = mo.ui.text(value="will it
fit my feet", label="Ask the help centre", full_width=True)`, `rs_k = mo.ui.slider(1, 10, step=1,
value=3, debounce=True, show_value=True, label="k (KNN)")`, `rs_radius = mo.ui.slider(0.05, 1.0,
step=0.05, value=0.5, debounce=True, show_value=True, label="radius (VECTOR_RANGE)")`,
`rs_categories = mo.ui.multiselect(options=sorted({a["category"] for a in cairn.HELP_ARTICLES}),
value=[], label="Pre-filter KNN to these categories (none means all)")`. Depends on
`rs_documents_indexed` so it appears after the load. Lead-in: "Redis runs the same query vector
two ways: `KNN` returns the k nearest articles whatever their distance, and `VECTOR_RANGE`
returns only what lies within a radius, which can be nothing. A TAG pre-filter runs inside the
KNN, so all k results qualify rather than a top k with the wrong ones thrown away:". Try "best
pizza near me" for a MISS. About 22 lines.

**7. `rs_search_results`.** Globals: `rs_knn_query`, `rs_knn_result`, `rs_range_query`,
`rs_range_result`. Calls: `_vector = rs_query_vector(rs_question.value)`; prefilter string
`"*"` or `f"(@category:{{{'|'.join(rs_categories.value)}}})"`; `Query(f"{prefilter}=>[KNN {k}
@embedding $vector AS vector_score]").sort_by("vector_score").return_fields("title",
"category", "vector_score").paging(0, k)`; `Query("@embedding:[VECTOR_RANGE $radius
$vector]=>{$YIELD_DISTANCE_AS: vector_score}").sort_by("vector_score").return_fields("title",
"category", "vector_score").paging(0, 10)`; `rs_client.ft(rs_index_name).search(query,
{"vector": _vector})` and `{... "radius": round(rs_radius.value, 4)}`. Wire: `FT.SEARCH
idx:cairn_help:<run> "*=>[KNN 3 @embedding $vector AS vector_score]" RETURN 3 title category
vector_score SORTBY vector_score ASC DIALECT 2 LIMIT 0 3 PARAMS 2 vector "<1024 bytes>"` and
`FT.SEARCH ... "@embedding:[VECTOR_RANGE $radius $vector]=>{$YIELD_DISTANCE_AS: vector_score}"
... LIMIT 0 10 PARAMS 4 radius 0.5 vector "<1024 bytes>"`, both rendered with `display.redis_cli(
["FT.SEARCH", rs_index_name, *query.get_args(), *rs_client.ft(rs_index_name).get_params_args(
params)])`. Renders: `mo.ui.table` (title, category, vector_score rounded to 3) for KNN with the
query string above it; for the range either a `mo.ui.table` or `mo.callout(kind="warn")`: "0
articles within radius 0.5. KNN could never say this; a radius can, and that empty answer is
exactly what LangCache turns into a cache MISS in column 1."; both redis-cli lines in
`mo.accordion({"redis-cli": ...})`. Lead-in: "KNN always fills k rows; the range answers with
what is close enough or with nothing." About 55 lines.

**8. `rs_engine_controls`** (markdown + inputs). Globals: `rs_preset = mo.ui.dropdown(options=
[p["label"] for p in cairn.SEARCH_PRESETS] + ["Type your own"], value="Same meaning, different
words", label="Question")`, `rs_typed = mo.ui.text(placeholder="Your own question",
full_width=True)`. Depends on `rs_documents_indexed`. Lead-in: "Keyword search matches words,
vector search matches meaning, and each fails where the other wins. The cell below sends one
question through `FT.SEARCH` with `BM25STD`, through KNN, and through `FT.HYBRID`, which runs
both on the server and fuses the two rankings with Reciprocal Rank Fusion:". About 16 lines.

**9. `rs_engine_results`.** Globals: `rs_engine_question`, `rs_engine_target`, `rs_bm25_result`,
`rs_knn_ranking`, `rs_hybrid_rows`, `rs_engine_table`. Calls: `Query(question).scorer("BM25STD")
.with_scores().return_fields("title").paging(0, 10)`; `Query("*=>[KNN 10 @embedding $vector AS
vector_score]").sort_by("vector_score").return_fields("title").paging(0, 10)`; if
`rs_has_hybrid`: `rs_client.execute_command("FT.HYBRID", rs_index_name, "SEARCH", question,
"SCORER", "BM25STD", "VSIM", "@embedding", "$vector", "KNN", 2, "K", 10, "COMBINE", "RRF", 4,
"WINDOW", 20, "CONSTANT", 60, "LOAD", 2, "@__key", "@title", "LIMIT", 0, 10, "PARAMS", 2,
"vector", _vector)` parsed as flat pairs (`dict(zip(reply[::2], reply[1::2]))`, rows likewise;
three lines, RESP2 verified live), wrapped in `try/except ResponseError` that turns the hybrid
column into a note. Wire: the three lines above, `FT.HYBRID ... COMBINE RRF 4 WINDOW 20 CONSTANT
60 LOAD 2 @__key @title LIMIT 0 10 PARAMS 2 vector "<1024 bytes>"` exactly as the live run sent
it. Renders: `mo.ui.table(selection=None)` with columns article, BM25 rank, vector rank, hybrid
rank over the union of the three top-10 lists, a dash where an engine did not rank it, and the
preset's target title prefixed with a star; under it `mo.md(preset["lesson"])`; the three
redis-cli lines in an accordion; when `rs_has_hybrid` is False, `mo.callout(kind="info")`:
"`FT.HYBRID` needs Redis 8.4 or later; this database is 8.2.x." Lead-in: "For *trail shoes feel
cramped* no article contains any of the four words, so `BM25STD` returns no row; the vector
search ranks the boot guide first and `FT.HYBRID` keeps it there (live on 2026-09-30). Switch to
*Trailhead*, an exact product name, and *waterproofng*, a typo, and a different engine wins each
time." About 60 lines.

**10. `rs_cleanup_button`** (markdown + button). Globals: `rs_cleanup_button = mo.ui.run_button(
label="Clean up column 0", kind="danger")`. Lead-in: "Clean up drops the index with `FT.DROPINDEX`
and no `DD`, so the index never deletes a document, then unlinks the 36 keys by name. Nothing is
scanned, and every key would expire within the hour in any case:". About 10 lines.

**11. `rs_cleanup`.** Globals: `rs_index_dropped`, `rs_keys_removed`. Depends only on
`rs_client`, `rs_index_name`, `rs_article_keys`, `rs_cleanup_button`. Calls: `mo.stop(not
rs_cleanup_button.value)`; `rs_client.ft(rs_index_name).dropindex()` with `except ResponseError`
tolerated when `re.search(r"index[_ ]not[_ ]found|unknown index|no such index", str(exc),
re.I)` (Redis 8.6 says `SEARCH_INDEX_NOT_FOUND Index not found: <name>`); `rs_client.unlink(
*rs_article_keys)`. Wire: `FT.DROPINDEX idx:cairn_help:<run>`, `UNLINK cairn:<run>:help:ship-times
... (36 keys)`. Renders `mo.callout(kind="success")`: "Dropped 1 index (or: the index was already
gone), unlinked 36 of 36 keys. Redis returns the count of keys that existed." About 18 lines.

Requests to Redis in a full run: about 80 commands, all under the free tier's 100 ops/s.

## 8. Column 1, LangCache (12 cells, about 340 lines)

All cells `@app.cell(column=1)`. Every search sends `maxResults` 5 and filters `data[]` to ids
this kernel stored (`lc_own_hits`, defined in cell 2), replacing any other item with
`{"hidden": "another app's entry", "similarity": s}` before anything renders. Every store sends
`ttlMillis`. Nothing calls `/flush` or `DELETE /entries` with a body.

**1. `lc_title`** (markdown, hide_code). Copy: "Users rarely ask the same question the same way
twice, and an exact-key cache misses every rewording. Redis LangCache matches prompts by meaning:
a paraphrase gets the stored answer and no model is called. A match is a similarity above a
threshold, so a wrong answer for a question that only sounds similar is possible; the threshold
is the dial, and this column is about turning it. LangCache is in public preview on Redis
Cloud." Links: overview, concepts, API reference, create a service (four URLs from the brief,
section 7). Mermaid `flowchart LR`: `Prompt --> LangCache search`; `search -->|hit| Stored
answer`; `search -->|miss| LLM call --> Store`; `Store -.-> search`. `mo.accordion({"The same
calls with the langcache SDK": mo.md(...)})`, five lines: `LangCache(server_url, cache_id,
api_key).search(prompt=..., similarity_threshold=0.85)`, `.set(prompt, response,
ttl_millis=...)`, `.delete_by_id(entry_id)`; "this notebook sends the JSON itself so you can read
it." About 34 lines.

**2. `lc_configure`.** Globals: `lc_service` (a `niquests.Session`), `lc_entry_ttl_ms = 900_000`,
`def lc_own_hits(payload, own_ids) -> (mine, shown)`, `lc_check_status`, `lc_check_wire`. Calls:
`mo.stop(not all(os.environ.get(n) for n in ("LANGCACHE_HOST", "LANGCACHE_CACHE_ID",
"LANGCACHE_API_KEY")), display.not_configured("LangCache", [...]))`; host normalised in one line
(`strip().rstrip("/")`, prefix `https://` when no scheme); `lc_service = niquests.Session(
base_url=f"{host}/v1/caches/{os.environ['LANGCACHE_CACHE_ID']}", headers={"Authorization":
f"Bearer {os.environ['LANGCACHE_API_KEY']}", "Accept": "application/json", "Content-Type":
"application/json", "User-Agent": "redis-iris-basics"}, timeout=(5, 30), retries=2,
disable_http3=True)`; `call_service(lc_service, "POST", "/entries/search", json_body={"prompt":
"Cairn Outfitters connection check", "maxResults": 1})`. Request: `POST
{host}/v1/caches/{cacheId}/entries/search` `{"prompt": "Cairn Outfitters connection check",
"maxResults": 1}` -> 200 `{"data": [...]}`. Renders: 200 -> `mo.callout(kind="success")`
"Connected: LangCache answered 200 in N ms."; 401 -> danger "The key was refused (`401
/errors/authentication-failed`): check `LANGCACHE_API_KEY`."; 404 -> danger "No cache with that
id (`404`): check `LANGCACHE_CACHE_ID`."; other -> danger with `mo.json(body)`; plus
`display.request_and_reply(wire, {"data": "<hidden: another app's entries>"})`. Lead-in: "The cell
below builds one HTTP session with the Bearer key and runs a read-only search for a prompt
nobody stored, which proves the host, cache id and key without writing anything:". About 40
lines (the 12-line `lc_own_hits` is the whole client-side filter and stays visible).

**3. `lc_store_button`** (markdown + button). Globals: `lc_store_button = mo.ui.run_button(
label="Store 5 answers (ttlMillis 900000)", kind="success")`. Lead-in: "The button below stores
five gear-care answers, each with `ttlMillis` 900000 so it expires in 15 minutes whether or not
you press Clean up: LangCache embeds each prompt as it is stored and answers `201` with an
`entryId`, and those ids are the only handle on the entries, because a cache cannot be listed."
About 12 lines.

**4. `lc_store`.** Globals: `lc_stored_entries` (list of `{"key", "prompt", "entryId"}`; an
empty list when the button has not been pressed, so Clean up always has something to read),
`lc_store_wires`. Calls: for each row of `cairn.GEAR_CARE_ANSWERS`: `call_service(lc_service,
"POST", "/entries", json_body={"prompt": row["prompt"], "response": row["response"],
"ttlMillis": lc_entry_ttl_ms})` -> 201 `{"entryId": "..."}`; any other status shows its body and
skips the row. Renders: `mo.ui.table(selection=None)` of key, prompt, entryId; `display.
request_and_reply(first wire, first body)`. Lead-in: "LangCache answers `201` with the `entryId`
you will delete later:". About 28 lines.

**5. `lc_anatomy_of_a_hit`.** Globals: `lc_anatomy_status`, `lc_anatomy_hits`, `lc_anatomy_wire`.
Calls: `mo.stop(not lc_stored_entries, mo.callout("Store the answers first.", kind="info"))`;
`call_service(lc_service, "POST", "/entries/search", json_body={"prompt": "My rain jacket stopped
beading water. How do I fix that?", "similarityThreshold": 0.85, "searchStrategies":
["semantic"], "maxResults": 5})` -> 200 `{"data": [{"id", "prompt", "response", "attributes",
"similarity", "searchStrategy"}]}`; `lc_own_hits`. Renders `display.request_and_reply(wire,
shown)` expanded (this is the one place the raw API is fully visible), then `mo.md` decoding it:
"`similarity` is the cosine similarity of the two prompt embeddings; the rain-shell answer came
back at 0.896 in a live run, comfortably above the 0.85 you sent, with no shared noun but
*rain*. A miss is `200` with an empty `data`, never a `404`." Lead-in: "The cell below asks a
question none of the five prompts contains, at the cache's default threshold of 0.85:". About 26
lines.

**6. `lc_probes_and_slider`** (auto, read-only, six requests). Globals: `lc_probe_scores` (list
of `{"text", "kind", "target", "best_key", "similarity"}`), `lc_threshold = mo.ui.slider(0.5,
1.0, step=0.01, value=0.85, debounce=True, show_value=True, label="similarityThreshold")`. Calls:
for each of `cairn.THRESHOLD_PROBES`: `POST /entries/search {"prompt": text,
"similarityThreshold": 0.5, "searchStrategies": ["semantic"], "maxResults": 5}`; keep the best
own hit's key and similarity (or `None`). Renders lead-in and the slider. Lead-in: "The cell
below searches six labelled questions once each at `similarityThreshold` 0.50, low enough to see
every score the server computed; the slider then re-applies the cut on your machine, which is
the same cut the server applies when you send the value, as the cell above did at 0.85:". About
28 lines.

**7. `lc_threshold_table`** (no requests). Globals: `lc_verdict_rows`. Logic: for each probe,
`HIT` when `best_key == target and similarity >= t`; `WRONG ANSWER` when `similarity >= t` and
the best key is not the target (including `target is None`); `MISS` otherwise. Renders
`mo.ui.table(rows, selection=None, style_cell=...)` with columns question, kind, best match,
similarity, verdict at `t`; `style_cell` colours the verdict cell (green, red, grey) with three
fixed CSS colours; under it one hedged line: "In a live run on 2026-09-30 the worst wrong
answer scored 0.950 (the synthetic bag was served the down-bag answer), so nothing wrong was left
from 0.96 up, and by then only 1 of 6 paraphrases still hit. Scores vary by model and phrasing;
the trade-off does not." Lead-in: "Raise the slider until the red rows go and watch the grey
ones multiply:". About 30 lines.

**8. `lc_strategies`** (auto, nine requests). Globals: `lc_strategy_rows`. Calls: for each of the
three `cairn.STRATEGY_VARIANTS` and each of `["exact"]`, `["semantic"]`, `["exact",
"semantic"]`: `POST /entries/search {"prompt": text, "similarityThreshold": 0.85,
"searchStrategies": strategies, "maxResults": 5}`; cell text is `HIT 1.00 (exact)`, `HIT 0.91
(semantic)`, `HIT ... wrong entry`, or `miss`. Renders `mo.ui.table(selection=None)`: variant,
prompt sent, exact, semantic, exact + semantic. Lead-in: "`searchStrategies` picks the matcher:
`exact` is a case-insensitive string compare that reports `similarity` 1, `semantic` is the
embedding, and both tries exact first. The cell below sends three spellings of a stored prompt
through each: live, *HOW DO I WASH MY DOWN SLEEPING BAG?* hit under `exact` and missed under
`semantic`, and dropping the question mark did the reverse." About 32 lines.

**9. `lc_cache_aside_button`** (markdown + button). Globals: `lc_cache_aside_button =
mo.ui.run_button(label="Run the cache-aside loop (about 15 seconds)", kind="success")`. Lead-in:
"The button below plays the cache-aside loop with you as the model: ask *How do I fix a snapped
tent pole?* and miss, store the answer with `ttlMillis` 10000, ask again as *What should I do
about a broken tent pole?* and hit, wait twelve seconds for the entry to expire, ask again and
miss, then `DELETE` it and read the `404`:". About 12 lines.

**10. `lc_cache_aside`.** Globals: `lc_taught_entry_id`, `lc_timeline_rows`. Calls, in order:
`POST /entries/search {"prompt": ask, "similarityThreshold": 0.85, "searchStrategies":
["semantic"], "maxResults": 5}` -> own hits empty (MISS); `POST /entries {"prompt": ask,
"response": answer, "ttlMillis": 10000}` -> 201; `POST /entries/search {"prompt": again, ...}`
-> HIT (0.894 live), filtered to `{taught id} | own ids`; `with mo.status.spinner(title="Waiting
12 s for ttlMillis to fire"): time.sleep(12)`; `POST /entries/search {"prompt": again, ...}` ->
MISS; `DELETE /entries/{lc_taught_entry_id}` -> `404 {"type": "/errors/resource-not-found",
...}` (live wording; `/errors/cache/entry/not-found` in the spec; a `204` means the delete won
the race and is reported as such). Renders `mo.ui.table(selection=None)` with columns step,
request, status, similarity, ms, and one line: "The MISS at step 1 is where your app would call
the model; step 2 is you storing its answer; the 404 at the end is why the MVP can rely on TTL
alone." Prompts come from `cairn.CACHE_ASIDE_SCRIPT`. About 45 lines.

**11. `lc_cleanup_button`** (markdown + button). Globals: `lc_cleanup_button = mo.ui.run_button(
label="Clean up column 1", kind="danger")`. Lead-in: "Clean up sends one `DELETE
/entries/{entryId}` per id this kernel stored. A `204` is a deletion, a `404` of type
`resource-not-found` or `entry/not-found` means the TTL got there first, and anything else is
shown as the service worded it. A cache cannot be listed, so an entry left by a crashed kernel
is found only by its TTL, and every entry here carried one:". About 12 lines.

**12. `lc_cleanup`.** Globals: `lc_deleted_count`, `lc_expired_count`, `lc_cleanup_problems`.
Depends on `lc_service`, `lc_stored_entries`, `lc_cleanup_button`. Calls: `mo.stop(not
lc_cleanup_button.value)`; for each entry: `call_service(lc_service, "DELETE",
f"/entries/{entryId}")`; 204 counted deleted; 404 with `re.search(r"/(entry/not-found|
resource[-/]not-found)$", body.get("type", ""))` counted expired; otherwise appended to
problems. Renders `mo.callout(kind="success")` "Deleted 5, already expired 0." or a danger
callout with `mo.json(problems)`. "Nothing stored in this run" when the list is empty. About 22
lines.

Requests in a full run: 1 + 5 + 1 + 6 + 9 + 5 + 5 = 32.

## 9. Column 2, Context Retriever (11 cells, about 360 lines)

All cells `@app.cell(column=2)`. The three `ContextModel` classes are named `Customer`,
`Product`, `Order` without the `cr_` prefix: the service names its tools after the class names
(`get_customer_by_id`), and that is the lesson. Every other global carries `cr_`.

**1. `cr_title`** (markdown, hide_code). Copy: "An agent that writes its own queries against
your data is a governance problem. Redis Context Retriever turns a data model into MCP tools the
agent calls instead: you declare entities and which fields are indexed, the hosted service
builds a surface of tools over JSON documents in your own Redis database, and the agent never
sees a key or a query. Context Retriever is in preview. This column has the heaviest setup of
the four: an admin key, and a database with Search and JSON that the hosted service can reach.
The service is given that database's password, so use a scratch database." Docs link
`https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/`. Mermaid `flowchart
LR`: `ContextModel -->|POST /context-surfaces| Surface with tools`; `Agent + key -->|tools/call|
Surface`; `Surface -->|JSON.GET, FT.SEARCH| Your Redis`. About 30 lines.

**2. `cr_configure`.** Globals: `cr_admin_service`, `cr_redis`, `cr_api_url`, `cr_mcp_url_default`,
`cr_surface_name` (`f"iris-basics-{RUN_TAG}"`), `def cr_data_source() -> dict` (builds
`{"type": "redis", "connection_config": {"addr", "username", "password", "db", "tls_enabled",
"pool_size": 2, "min_idle_conns": 0}}` from `urllib.parse.urlsplit(os.environ["REDIS_URL"])`
with `unquote` on userinfo, at call time, so no global ever holds the password),
`cr_probe_status`. Calls: `mo.stop(not (os.environ.get("CTX_ADMIN_KEY") and os.environ.get(
"REDIS_URL")), display.not_configured("Context Retriever", ["CTX_ADMIN_KEY", "REDIS_URL"]))`;
`cr_api_url = os.environ.get("CTX_API_URL", "https://cloud.redis.io/context-surfaces").rstrip(
"/")`; `cr_mcp_url_default = os.environ.get("CTX_MCP_URL", "https://gcp-us-east4.context-
surfaces.redis.io/mcp")` (a fallback only; the live address comes from the agent-key listing);
`cr_admin_service = niquests.Session(base_url=f"{cr_api_url}/api/v1", headers={"X-API-Key":
os.environ["CTX_ADMIN_KEY"], "Accept": "application/json", "Content-Type": "application/json",
"User-Agent": "redis-iris-basics"}, timeout=(5, 60), retries=0, disable_http3=True)` (`retries=0`
with the comment "a replayed create would make a second surface holding your password");
`call_service(cr_admin_service, "GET", "/context-surfaces", params={"page_size": 1})` -> 200
(read-only key check; 401 and 404 decoded in one sentence each); `cr_redis = connect_redis(
os.environ["REDIS_URL"])`; `cr_redis.ping()`. Renders success callout "Admin API reachable;
Redis PONG." and the masked wire in an accordion. Lead-in: "The cell below builds two clients:
an admin session carrying `X-API-Key` for the hosted service, checked with one read-only listing,
and a redis-py client for the database the service will write into:". About 40 lines.

**3. `cr_data_model`** (offline; the pytest target). Globals: `Customer`, `Product`, `Order`,
`cr_entity_classes`, `cr_data_model`, `cr_record_keys` (20 key names from
`cls.__redis_key_template__.format(**row)`), `cr_name_index = mo.ui.dropdown(["text", "tag",
"not indexed"], value="text", label="Customer.name index (for the cell below)")`. Calls: `from
context_surfaces import ContextField, ContextModel, ContextRelationship, export_data_model`;
`mo.stop(sys.version_info >= (3, 14), mo.callout("ContextModel needs Python 3.13 or older; run
`uv run marimo edit iris_basics.py`.", kind="warn"))`; the three classes exactly as at
3462-3496 with `field(...)` replaced by direct `ContextField(...)` calls and key templates
`f"{KEY_PREFIX}customer:{{customer_id}}"`, `f"{KEY_PREFIX}product:{{sku}}"`,
`f"{KEY_PREFIX}order:{{order_id}}"`; `assert all(cls.__redis_key_template__.startswith(
KEY_PREFIX) for cls in ...)`; `cr_data_model = export_data_model("Cairn Outfitters",
"Customers, products and orders of a fictional outdoor-gear shop", entities=[Customer, Product,
Order])` (always `entities=`, or the SDK registry exports every class ever defined in the
kernel). Renders `mo.vstack`: a six-row `mo.md` table "what you declare -> what the service
generates" (`is_key_component` -> `get_<entity>_by_id`; any entity -> `list_`, `count_`; a
`numeric` field -> `summarize_`; a `tag` or `numeric` field -> `filter_` with that field in its
menu; a `text` field -> `search_<entity>_by_text`; relationships -> `expand_results`, plus the
three set tools), `mo.accordion({"export_data_model output (3 entities)": mo.json(
cr_data_model)})`, and the dropdown. Lead-in: "The cell below declares the whole data model as
three Python classes: each field's `index` kind is what decides which tools the service
generates, and `__redis_key_template__` is where the service will write each record in your
database:". About 75 lines.

**4. `cr_model_variant`** (offline; the swept parameter; first to drop if the column is over
budget). Globals: `CustomerVariant`, `cr_variant_field`. Calls: a fourth class identical to
`Customer` except `name: str = ContextField(description="Full name", index=None if
cr_name_index.value == "not indexed" else cr_name_index.value, **({"weight": 2.0} if
cr_name_index.value == "text" else {}))`; `export_data_model("variant", "", entities=
[CustomerVariant])["entities"][0]["fields"]` and pick `name`. Renders `mo.hstack([mo.json(
default name field), mo.json(variant name field)])` and one `mo.md` sentence chosen by the
value: text -> "As `text`, `name` is what generates `search_customer_by_text` (21 tools live on
2026-09-30)."; tag -> "As `tag`, Customer has no text field, so `search_customer_by_text` is not
generated and `name` joins the menus of `filter_customer` and `count_customer` (20 tools; from
the generation rule, not observed live)."; not indexed -> "Not indexed, `name` is still returned
by `get_customer_by_id` but no tool can search or filter on it." Then: "The surface below is
created from the default model; the live `tools/list` there is the proof." Lead-in: "Change one
field's index kind and read what changes in the export:". About 30 lines.

**5. `cr_create_button`** (markdown + button). Globals: `cr_create_button = mo.ui.run_button(
label="Create the surface and load 20 records (sends the database password)", kind="success")`.
Lead-in: "The button below makes five admin calls: `POST /context-surfaces` with the data model
and `data_source.connection_config`, which carries your database password and which the service
keeps until you delete the surface; `POST .../agent-keys` with an `expires_at` one hour out;
`GET .../agent-keys` to read the surface's `mcp_url`; and `POST .../data` once per entity. It
then sets a one-hour `EXPIRE` on the 20 keys the service wrote, because the service sets none:".
About 14 lines.

**6. `cr_create`.** Globals: `cr_surface_id` (`None` when the button has not been pressed or the
create failed, so Clean up always runs), `cr_surface_status`, `cr_mcp_service`, `cr_mcp_url`,
`cr_agent_key_expires`, `cr_indexes_before`, `cr_import_counts`, `cr_create_wire`. Calls:
`cr_indexes_before = set(cr_redis.execute_command("FT._LIST"))`; `POST /context-surfaces
{"name": cr_surface_name, "description": "Made by the Redis Iris basics notebook. Safe to
delete.", "metadata": {"created_by": "iris-basics-notebook", "run": RUN_TAG}, "data_model":
cr_data_model, "data_source": cr_data_source()}` -> 201 `{"id", "status": "provisioning_indices",
...}`; `POST /context-surfaces/{id}/agent-keys {"name": f"{cr_surface_name}-agent",
"expires_at": (datetime.now(timezone.utc) + timedelta(seconds=KEY_TTL_SECONDS)).isoformat(
timespec="seconds")}` -> 201 `{"id", "key", ...}`, and the key goes straight into
`cr_mcp_service = niquests.Session(headers={"X-API-Key": body["key"], "Accept":
"application/json", "Content-Type": "application/json"}, timeout=(5, 30), retries=0,
disable_http3=True)` and nowhere else (never a global, never the cell's last expression); `GET
/context-surfaces/{id}/agent-keys?page_size=50` -> `cr_mcp_url` from the item whose `id`
matches, else `cr_mcp_url_default`; for each entity: `POST /context-surfaces/{id}/data
{"entity": "Customer", "records": [Customer(**row).model_dump() for row in
cairn.SHOP_RECORDS["Customer"]], "options": {"on_conflict": "overwrite", "on_error":
"fail_fast"}}` -> 2xx `{"imported": 6, "failed": 0, "errors": []}`; `cr_redis.expire(key,
KEY_TTL_SECONDS)` for each of `cr_record_keys`. After the surface POST succeeds the rest runs in
one `try/except` that records the failed step in `cr_create_problem` instead of raising, so
`cr_surface_id` survives for Clean up. Renders `mo.callout(kind="success")`: "Surface <id>
(status provisioning_indices), agent key expires at <time>, imported 6 + 6 + 8 records, 20 keys
set to expire in 1 h." and `mo.accordion({"Create request (password masked)": mo.json(
display.mask_connection_config(cr_create_wire["body"]))})`. Lead-in: "The service answers `201`
with the surface id and a status; the data import lands as JSON documents at the key templates
you declared:". About 60 lines.

**7. `cr_tools`.** Globals: `cr_tools_list`, `cr_tools_wire`. Calls: `mo.stop(cr_surface_id is
None, mo.callout("Create the surface first.", kind="info"))`; `call_service(cr_mcp_service,
"POST", cr_mcp_url, json_body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params":
{}})` -> 200 `{"jsonrpc", "id", "result": {"tools": [{"name", "description", "inputSchema"}]}}`.
Renders `mo.ui.table(selection=None, label=f"{n} tools from 3 entities")` with columns tool,
description, arguments (from `inputSchema.properties`, `required` starred), built from the reply
and never from a hardcoded list; the request in an accordion. Lead-in: "The cell below asks the
surface what it can do with one JSON-RPC `tools/list` over MCP, authenticated by the agent key:
every row is a tool the service generated from the data model (21 on 2026-09-30, unified
surface), and nothing here was hand-written." About 30 lines.

**8. `cr_tool_calls`** (read-only, two requests). Globals: `cr_get_request`, `cr_get_record`,
`cr_rejected_request`, `cr_rejected_reply`. Calls: the id argument name is read from the schema
(`next(iter(tool["inputSchema"]["required"]))` for `get_customer_by_id`; it was `id` live);
`POST cr_mcp_url {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name":
"get_customer_by_id", "arguments": {"id": "C001"}}}` -> 200, `json.loads(result.content[0].text)`
gives the record plus `_retrieval {source, retrieved_at, source_version}`; then `POST cr_mcp_url
{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "filter_customer",
"arguments": {"tag_conditions": [{"field": "lifetime_value", "value": "4200"}]}}}` -> a JSON-RPC
`error {code: -32602, message}` (the `field` enum holds tag fields only) or, if the service
prefers, `result.isError` with `Error executing tool: ...`; either is shown verbatim. Renders
`mo.vstack`: `mo.json(cr_get_request)`, `mo.callout(mo.json(cr_get_record), kind="success")`,
one gloss "The `_retrieval` stamp names the surface that served it and when; the agent sent an
argument, not a key or a query."; `mo.json(cr_rejected_request)`, `mo.callout(kind="danger")`
with `error.code` and `error.message` (or the `isError` text) exactly as received; if the call is
accepted, `mo.callout(kind="warn")` "Accepted: the service did not enforce the enum here" with
the rows. Lead-in: "The cell below calls two tools as an agent would. The first asks for customer
C001 by id; the second puts a tag condition on a numeric field, which the tool's own
`inputSchema` forbids, so the service rejects it before anything reaches Redis:". About 45 lines.

**9. `cr_peek`** (read-only, your own Redis). Globals: `cr_first_document`, `cr_new_indexes`,
`cr_index_rows`. Calls: `mo.stop(cr_surface_id is None)`; `cr_redis.json().get(cr_record_keys[0])`;
`set(cr_redis.execute_command("FT._LIST")) - cr_indexes_before`; for each new index
`cr_redis.ft(name).info()` -> `num_docs` and `index_definition` prefixes (RESP2 flat list:
`dict(zip(d[::2], d[1::2]))["prefixes"]`). Wire: `JSON.GET cairn:<run>:customer:C001`, `FT._LIST`,
`FT.INFO idx:<surface-id>:<hash>:customer`. Renders `mo.json(cr_first_document, label=
cr_record_keys[0])` and `mo.ui.table(selection=None)` index, documents, prefix; one gloss: "The
agent never saw Redis. The service wrote these JSON documents into your database, built one
index per entity, and the tool call above read them back through the service." Lead-in: "Where
did the data go: the cell below reads your own database with `JSON.GET` and `FT.INFO`:". About
30 lines.

**10. `cr_cleanup_button`** (markdown + button). Globals: `cr_cleanup_button = mo.ui.run_button(
label="Clean up column 2", kind="danger")`. Lead-in: "Clean up deletes the surface, which also
revokes its agent key and, on this service, drops the indexes it built; then unlinks the 20 keys
by name, because the docs say the documents in your database stay; then lists indexes again and
drops any straggler under the run prefix without `DD`. A surface has no TTL and holds your
password, so this button matters more than the other three:". About 12 lines.

**11. `cr_cleanup`.** Globals: `cr_surface_deleted`, `cr_keys_removed`, `cr_indexes_left`.
Depends on `cr_admin_service`, `cr_redis`, `cr_record_keys`, `cr_surface_id`,
`cr_cleanup_button`. Calls: `mo.stop(not cr_cleanup_button.value)`; if `cr_surface_id`: `DELETE
/context-surfaces/{id}` (200, 204 and 404 all count as gone; anything else shows the body);
`cr_redis.unlink(*cr_record_keys)`; `FT._LIST`, and for each index whose `FT.INFO` prefixes all
start with `KEY_PREFIX`: `cr_redis.ft(name).dropindex()` (never `delete_documents=True`),
`SEARCH_INDEX_NOT_FOUND` tolerated. Renders success callout "Deleted 1 surface, unlinked 20 of 20
keys, 0 indexes left under cairn:<run>:" or "No surface was created in this run; unlinked 0
keys." About 30 lines.

## 10. Column 3, Agent Memory (11 cells, about 330 lines)

All cells `@app.cell(column=3)`. Every request carries `ownerId` `ib-<run>`, and every teaching
search also `namespace` `iris-basics-notebook`; Clean up lists by owner alone.

**1. `am_title`** (markdown, hide_code). Copy: "An agent forgets everything between sessions
unless something remembers for it. Redis Agent Memory gives it two tiers: session memory, the
ordered events of the conversation in progress, and long-term memory, facts recalled by meaning
across sessions. Facts reach long-term memory two ways: your app writes them directly, or the
service extracts them from sessions in the background, minutes later. Agent Memory is in public
preview. This column writes facts directly, recalls one with no word in common with the
question, and assembles the prompt an agent would receive." Docs link
`https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/`. Mermaid `flowchart LR`:
`App -->|events| Session memory -->|extraction, minutes| Long-term memory`; `App -->|direct
write| Long-term memory`; `Search by meaning --> Long-term memory`. About 28 lines.

**2. `am_configure`.** Globals: `am_service`, `am_owner_id` (`f"ib-{RUN_TAG}"`), `am_namespace`
(`"iris-basics-notebook"`), `am_health_status`, `am_health_body`, `am_health_wire`. Calls:
`mo.stop(not all(os.environ.get(n) for n in ("MEMORY_API_BASE_URL", "MEMORY_STORE_ID",
"MEMORY_API_KEY")), display.not_configured("Agent Memory", [...]))`; `am_service =
niquests.Session(base_url=f"{os.environ['MEMORY_API_BASE_URL'].rstrip('/')}/v1/stores/
{os.environ['MEMORY_STORE_ID']}", headers={"Authorization": f"Bearer
{os.environ['MEMORY_API_KEY']}", "Accept": "application/json", "Content-Type":
"application/json", "User-Agent": "redis-iris-basics"}, timeout=(5, 30), retries=2,
disable_http3=True)`; `call_service(am_service, "GET", "/health")` -> 200 `{"status": "healthy",
"features": {...}}`; a `str` body (HTML) -> danger "MEMORY_API_BASE_URL is the Endpoint on the
service's General settings, not a console page"; 401 and 404 decoded in one sentence each.
Renders success callout "Store healthy." with `mo.json(am_health_body)`, the wire accordion,
and one `mo.md` line: "Every call in this column carries `ownerId` `ib-<run>` and `namespace`
`iris-basics-notebook`. On a store other apps share, that filter is the tenant boundary."
Lead-in: "The cell below builds one HTTP session with the Bearer key and asks the store for its
health, which also proves the store id and the key:". About 36 lines.

**3. `am_write_button`** (markdown + button). Globals: `am_write_button = mo.ui.run_button(
label="Write 5 memories about Dana", kind="success")`. Lead-in: "The button below writes five
facts about Dana, a made-up Cairn customer, in one bulk `POST /long-term-memory`: the ids
(`ib-<run>-m1` to `m5`), the owner and the namespace are chosen here rather than by the service,
which is what makes an idempotent write and a Clean up without a ledger possible." About 12
lines.

**4. `am_write`.** Globals: `am_memory_ids` (the five ids, computed even when the button has
not been pressed), `am_written`, `am_write_wire`. Calls: `mo.stop(not am_write_button.value,
mo.callout("Press the button above to write the memories.", kind="info"))`; `call_service(
am_service, "POST", "/long-term-memory", json_body={"memories": [{"id": f"{am_owner_id}-m{n}",
"text": fact["text"], "ownerId": am_owner_id, "namespace": am_namespace, "memoryType":
"semantic", "topics": fact["topics"]} for n, fact in enumerate(cairn.DANA_FACTS, 1)]})` -> 201
`{"created": ["ib-<run>-m1", ...]}`; 424 -> danger callout "`424 <type>`: <detail>. This is a
failure of the store's database or model, not 0 results." Renders `mo.ui.table(selection=None)`
id, text, topics and `display.request_and_reply(wire, body)`. Lead-in: "The service answers
`201` with `created`, and the memories are searchable at once in a live run:". About 30 lines.

**5. `am_search_controls`** (markdown + inputs). Globals: `am_question = mo.ui.text(value=
cairn.MEMORY_QUESTIONS[0]["text"], label="Ask", full_width=True)`, `am_threshold = mo.ui.slider(
0.0, 1.0, step=0.05, value=0.3, debounce=True, show_value=True, label="similarityThreshold")`.
Depends on `am_written`. Lead-in: "The reply of `POST /long-term-memory/search` carries no
score, so `similarityThreshold` is the only knob: everything at or above it comes back in rank
order and everything below is cut. Slide it and watch the request body and the rows change. Two
more questions with no word in common with their memory: *My toes feel squashed on long
descents.* and *Somewhere freezing to visit next year?*". About 20 lines.

**6. `am_search`** (one request per change; the swept parameter and the wow). Globals:
`am_search_body`, `am_recalled_memories`, `am_search_wire`, `am_recall_rows`. Calls:
`call_service(am_service, "POST", "/long-term-memory/search", json_body={"text":
am_question.value, "filter": {"ownerId": {"eq": am_owner_id}, "namespace": {"eq":
am_namespace}}, "filterOp": "all", "similarityThreshold": round(am_threshold.value, 2),
"limit": 5})` -> 200 `{"items": [{"id", "text", "memoryType", "ownerId", "namespace",
"topics", "createdAt", "updatedAt"}]}`; 424 shown as a failure. Renders `mo.ui.table(
selection=None)` rank, memory, topics, words in common (`len(cairn_text.shared_stems(question,
text))`), or `mo.callout(kind="warn")` "0 memories at or above <t>." ; `mo.json(am_search_body,
label="Request body")`; one hedged line: "In a live run on 2026-09-30 the allergy memory was #1
of 3 at 0.0 with 0 words in common, alone at 0.3, and nothing came back at 0.5. Another store or
model may place the cut elsewhere; start around 0.3 and move it." Lead-in: "Redis Agent Memory
answers with the memories at or above the threshold, best first, and says nothing about how
close they were:". About 40 lines.

**7. `am_scope_lens`** (read-only, three requests per question change). Globals:
`am_scope_rows`. Calls: same `text`, `"similarityThreshold": 0.0`, `"limit": 100`, under three
filters: `{"ownerId": {"eq": am_owner_id}, "namespace": {"eq": am_namespace}}`; `{"ownerId":
{"eq": "nobody-owns-this-id"}, "namespace": {"eq": am_namespace}}`; `{"ownerId": {"eq":
am_owner_id}, "namespace": {"eq": "no-such-namespace"}}`. Renders `mo.ui.table(selection=None)`
scope, filter (as JSON text), memories returned, expected 5 / 0 / 0, and a footnote: "`namespace
eq` never matches a record that has no namespace at all, which is why Clean up below lists by
owner alone." Lead-in: "The same question under three filters shows what scopes a shared
store: the owner and the namespace, both sent on every call:". About 30 lines.

**8. `am_session_button`** (markdown + button). Globals: `am_session_button = mo.ui.run_button(
label="Send 3 events and assemble the prompt", kind="success")`. Lead-in: "A session's owner and
namespace come from its first event only. The button below posts a USER event from `ib-<run>`
carrying the namespace, an ASSISTANT reply from `cairn-agent`, and a second USER turn that asks
the kitchen question; then it reads the session back, searches long-term memory with that last
turn as an agent would, and assembles the prompt the agent would receive:". About 12 lines.

**9. `am_session`** (the second half of the wow; five requests). Globals: `am_session_id`
(`f"{am_owner_id}-s1"`), `am_session_reply`, `am_session_recall`, `am_prompt_context`. Calls:
`mo.stop(not am_session_button.value, ...)`; for each turn of `cairn.SESSION_TURNS`: `POST
/session-memory/events {"sessionId": am_session_id, "actorId": am_owner_id or "cairn-agent",
"role": "USER" | "ASSISTANT", "content": [{"text": text}], "createdAt": now as RFC 3339 `Z`,
"namespace": am_namespace (first event only), "metadata": {"source": "iris-basics"}}` -> 201
`{"event": {"eventId", "actorId", "sessionId", "role", "content", "createdAt",
"systemTimestamp"}}`; `GET /session-memory/{am_session_id}` -> 200 `{"sessionId", "ownerId",
"namespace", "events": [...], "summary"?}`; `POST /long-term-memory/search {"text": last USER
text, "filter": {owner, namespace}, "filterOp": "all", "similarityThreshold": 0.0, "limit": 3}`.
Builds the literal string:
```
Retrieved memory context:
Session summary:
(none yet: this store summarizes only when that is switched on and the session is long enough)
Short-term memory:
USER: I am training for a 50 kilometre trail race in April.
ASSISTANT: Good to know. Which trail shoes are you training in?
Long-term memory:
- Dana is allergic to peanuts and tree nuts, so trail snacks and camp meals must not contain them.
- ...
```
Renders `mo.vstack`: one line "Session `ib-<run>-s1`: `ownerId` <first actor>, `namespace`
<from the first event>."; `mo.ui.table(selection=None)` role, actor, text, createdAt;
`mo.plain_text(am_prompt_context)` under the heading "What the agent receives for the last turn";
one line "#1 of 3 · 0 words in common" for the top recalled memory (the review's format). Lead-in:
"The session read-back shows the owner and namespace the first event fixed; the assembled prompt
is what the two tiers are for." About 55 lines.

**10. `am_cleanup_button`** (markdown + button). Globals: `am_cleanup_button = mo.ui.run_button(
label="Clean up column 3", kind="danger")`. Lead-in: "Clean up deletes the session, lists every
memory under the owner with no namespace filter so anything extraction added is caught too,
checks each item's `ownerId` on this side before deleting, deletes by id, and lists again.
Extraction can add memories minutes after the last event, so if you sent events press this again
in about ten minutes; long-term memories otherwise last a year:". About 12 lines.

**11. `am_cleanup`.** Globals: `am_sessions_deleted`, `am_memories_deleted`,
`am_memories_left`, `am_cleanup_problem`. Depends on `am_service`, `am_owner_id`,
`am_cleanup_button` and `cairn.DANA_FACTS` (the ids are recomputed, so this cell runs even if
Write was never pressed). Calls: `mo.stop(not am_cleanup_button.value)`; `GET
/session-memory?filterOwnerId=<owner>&limit=1000` -> `{"items": [...]}` (never `includeAll`),
then `DELETE /session-memory/{id}` for each (204 and 404 both gone), falling back to
`ib-<run>-s1` when the listing fails; `POST /long-term-memory/search {"text": "", "filter":
{"ownerId": {"eq": am_owner_id}}, "limit": 100}` paged on `pageToken` (cap 50 pages); abort with
a danger callout if any item's `ownerId` differs; `DELETE /long-term-memory {"memoryIds":
[...]}` in chunks of 100 over the union of listed ids and `ib-<run>-m1..m5` -> 200 `{"deleted":
[...]}`; list again. Renders success callout "Deleted 1 session and 5 memories (0 with ids the
service chose); 0 left under ib-<run>." About 40 lines.

Requests in a full run: 1 + 1 + 1 per slider move + 3 + 5 + about 6.

## 11. Line-count budget

| Region | Cells | Lines | Notes |
|---|---|---|---|
| Header, `App(...)`, setup block | 1 | 75 | imports, three constants, two helpers with docstrings |
| Intro | 1 | 32 | |
| Column 0, Redis Search | 11 | 330 | wow cell is the largest at about 60 |
| Column 1, LangCache | 12 | 340 | `lc_own_hits` and the cache-aside timeline are the two biggest blocks |
| Column 2, Context Retriever | 11 | 360 | 75 lines of `ContextModel` classes are the product and are not compressed |
| Column 3, Agent Memory | 11 | 330 | |
| `if __name__ == "__main__": app.run()` | | 3 | |
| **Total** | **47 cells incl. setup** | **about 1,470** | ceiling 1,500; if a column runs over, drop in this order: `cr_model_variant`, `lc_strategies` (fold two variants into the threshold table), `rs_categories` from the KNN controls |

`src/cairn_support`: about 330 lines (`sample_data.py` 200, `embedder.py` 40, `text.py` 25,
`display.py` 45, `__init__.py` 3). Tests: about 220 lines.

## 12. Per-column Clean up, stated exactly

| Column | Deletes | How it knows what to delete | Tolerates | Never |
|---|---|---|---|---|
| 0 | `FT.DROPINDEX idx:cairn_help:<run>`; `UNLINK` the 36 keys | index name and key list are derived from `RUN_TAG` and `cairn.HELP_ARTICLES` in `rs_connect` | index already gone (`SEARCH_INDEX_NOT_FOUND`, `Unknown index name`, `no such index`); `UNLINK` returns fewer than 36 | `DD`, `FLUSH*`, `SCAN`-and-delete, any key outside `cairn:<run>:` |
| 1 | `DELETE /entries/{id}` per stored id | `lc_stored_entries`, defined (possibly empty) by the store cell; the taught entry deletes itself in its own cell | `404` of type `resource-not-found` or `entry/not-found` counts as expired | `/flush`, `DELETE /entries` with a body, Generate key, Delete service |
| 2 | `DELETE /context-surfaces/{id}`; `UNLINK` the 20 keys; `FT.DROPINDEX` any index whose every prefix starts with `cairn:<run>:` | `cr_surface_id` (always defined, `None` if never created); `cr_record_keys` from the key templates | `200`, `204` or `404` on the DELETE; index already gone | `DD`, `PUT`, deleting a surface not created in this run, `FLUSH*` |
| 3 | `DELETE /session-memory/{id}` for sessions listed under the owner; `DELETE /long-term-memory {"memoryIds"}` for memories listed under the owner plus the five deterministic ids | `filterOwnerId=ib-<run>`; owner-only `text: ""` listing; client-side `ownerId` check on every item | `404` on a session; a listing that lags behind a write | `includeAll`, deleting an id whose `ownerId` was not verified, Flush, Delete service |

If any cell between seed and Clean up fails, Clean up still runs: its only dependencies are the
configure cell, the seed cell's always-defined ids, and its own button.

## 13. Test suite

Runs with `uv run pytest` (dev group). Credentials are absent by construction: `app.run()` does
not load `.env`, and the notebook test deletes the ten configuration names from the process
environment with `monkeypatch.delenv`, so no test can reach a Redis or Iris service.

**`tests/test_notebook.py`**

- `test_notebook_runs_without_credentials`: deletes the ten names, sets `HF_HUB_OFFLINE=1`
  (belt and braces; the embedder cell is gated behind `rs_connect` anyway), imports
  `iris_basics`, calls `outputs, defs = iris_basics.app.run()`; asserts it returned (a raising
  cell propagates), `len(outputs) >= 45`, `defs["cr_data_model"]["entity_count"] == 3`,
  `len(defs["cr_record_keys"]) == 20`, every `redis_key_template` in the export starts with
  `defs["KEY_PREFIX"]`, and none of `rs_client`, `lc_service`, `cr_admin_service`, `am_service`
  is in `defs` (each configure cell stopped on its "not configured" note).
- `test_marimo_check_strict`: `subprocess.run([sys.executable, "-m", "marimo", "check",
  "--strict", "iris_basics.py"])` exits 0 (falls back to `shutil.which("marimo")`; both
  resolutions are UNVERIFIED under `uv run pytest` and the builder confirms one).
- `test_columns_are_complete_and_contiguous`: regex over the file for `column=(\d)`; asserts
  the set is `{0, 1, 2, 3}`, every non-setup cell carries `column=`, and the sequence is
  non-decreasing in file order (guards marimo issue #3543).
- `test_no_pep723_header`: the file does not contain `# /// script`.

**`tests/test_safety.py`** (text and `ast` over `iris_basics.py`; one function per rule)

- no `FLUSH` token, no `/flush` string, no `flushall`/`flushdb` call;
- no `"DD"` string argument and no `delete_documents=True`;
- no `includeAll`;
- no `call_service(..., "DELETE", "/entries", json_body=...)` (walk `ast.Call` nodes: a DELETE
  whose path is exactly `/entries` with a body);
- every dict literal containing both `"prompt"` and `"response"` keys also contains
  `"ttlMillis"`;
- every dict literal containing a `"memories"` key or a `"filter"` key contains `"ownerId"`
  (directly or in a nested literal);
- every `__redis_key_template__` assignment is an f-string starting with `{KEY_PREFIX}`;
- no `mo.ui.text(kind="password")`, no `mo.ui.chat`, no `mo.state`, no `mo.ui.form`;
- no `os.environ` read whose result is assigned to a name that does not start with `_`
  (secrets never become globals); allowed exceptions are the two non-secret `CTX_*_URL` reads.

**`tests/test_cairn_support.py`**

- `HELP_ARTICLES`: 36 rows, 6 categories x 6, unique ids, no title or body contains "cramped"
  or "shoes", exactly one contains "Trailhead", none "waterproofng";
- `GEAR_CARE_ANSWERS`: 5 unique keys; `THRESHOLD_PROBES` targets are keys or `None`;
  `STRATEGY_VARIANTS` has 3 rows; `CACHE_ASIDE_SCRIPT` has `ask`, `answer`, `again`;
- `SHOP_RECORDS`: 6 / 6 / 8, every id matches `^[A-Za-z]+[0-9]+$`;
- `DANA_FACTS` has 5 rows; each `MEMORY_QUESTIONS` entry shares zero stems with its target fact
  (`text.shared_stems`), and `SESSION_TURNS[-1]["text"] == MEMORY_QUESTIONS[0]["text"]`;
- `display.redis_cli` renders a 1024-byte value as `<1024 bytes>`, quotes an argument with a
  space, and truncates a 200-character string with a `<200 chars>` marker;
- `display.mask_connection_config` masks `password`, `username`, `db`, `tls_enabled`,
  `pool_size` and leaves `addr`;
- `embedder.load_embedding_model`: skipped unless the snapshot is already in the Hugging Face
  cache (`local_files_only=True` succeeds); when it runs, `model.dim == 256` and
  `embed_texts` returns unit-norm float32.

## 14. README outline (VOICE.md tutorial template)

- H1: `How to take the Redis Iris services apart with one marimo notebook`
- `> **TL;DR:**` two sentences: what Redis Iris is (docs framing) and "In this tutorial, you
  will run a notebook that lets you index, cache, retrieve, and remember with the four Redis
  services behind an agent's context."
- `> **Note:** This tutorial uses the code from the following git repository:` + URL.
- Intro, problem-first form: "When your agent needs context, you need a search index, a
  semantic cache, governed access to your data, and a memory that outlives the session. Redis
  gives you all four in one data layer, and this notebook shows the exact command or request
  behind each."
- `## What you'll learn` (four bullets, one per column, mechanism not benefit)
- `## What you'll build` (one notebook, four columns, one `.env`; screenshot optional)
- `## Prerequisites`: uv; Python 3.13 (uv installs it); a Redis 8 database with Search and
  JSON, either local or Redis Cloud; Iris credentials flat ("A LangCache service (API URL, cache
  ID, and API key)", "A Context Retriever admin key", "An Agent Memory service (endpoint, store
  ID, and API key)"); `> **Note:** The notebook does not start a local Redis instance; the only
  thing that runs on your machine is a 30 MB embedding model.`
- `## Step 1. Clone the repository`
- `## Step 2. Configure the environment`: "Copy the sample file:" + `cp .env.example .env` +
  gloss "Open `.env` and fill in the variables for the columns you want to run." + Variable |
  Default | Purpose table for the ten names, credentials linked to the console in the Purpose
  cell.
- `## Step 3. Start the notebook`: `uv run marimo edit iris_basics.py`; "Or present it as an
  app:" `uv run marimo run iris_basics.py`; gloss on Run all and column width in edit mode.
- `## Step 4. Search the help centre` (column 0), `## Step 5. Turn the similarity threshold`
  (column 1), `## Step 6. Generate tools from a data model` (column 2), `## Step 7. Recall a
  memory by meaning` (column 3): each with the button to press, the literal output to expect
  ("Redis returns `num_docs` 36"), no contingencies.
- `## Step 8. Clean up`: one button per column and what each deletes.
- `## How it works`: Key | Type | Purpose table (`cairn:<run>:help:<id>` HASH; `idx:cairn_help:
  <run>` index; `cairn:<run>:customer:<id>` JSON written by the service; `idx:<surface>:<hash>:
  <entity>`; LangCache entry ids; `ib-<run>-m<n>` memory ids; `ib-<run>-s1` session); the raw
  `FT.CREATE` and the three request bodies after the client code; one mermaid of the four
  columns; the safety table (scope, expires, Clean up, never).
- `## Troubleshooting`: `### A column says "not configured"` (Verify that `.env` sits next to
  `pyproject.toml` and the names match `.env.example`); `### Context Retriever fails to define
  the model` (Verify that `uv run python --version` reports 3.13); `### The hybrid column shows a
  note` (Verify the Redis version is 8.4 or later); `### Agent Memory answers 424` (Verify the
  store's database and model in the console; this is a failure, not 0 results); `### LangCache
  answers 401` (Verify the key; a regenerated key invalidates the old one for every app).
- `## FAQ`: "Do I need Redis Cloud?" (No. ...), "Does the notebook call an LLM?" (No. ...),
  "What does the notebook leave behind if I close the laptop?" (Redis keys and the agent key
  expire in an hour, LangCache entries in 15 minutes; a Context Retriever surface and Agent
  Memory records do not, so press Clean up), "Can I point it at a shared LangCache?" (Yes, with
  the TTL caveat ...), "Where do the numbers in the copy come from?" (one live run on
  2026-09-30; yours will differ).
- `## Next steps`: imperatives with linked titles (build a semantic cache in a FastAPI app;
  build an agent with Agent Memory and LangGraph; read the Context Retriever quickstart).
- `## Additional resources`: the four docs anchors and the redis-ai-resources notebook collection.

No sales CTA, no "developers" in body prose, sentence-case headings, serial commas, no
exclamation marks, one dash style.

## 15. Deliberately dropped from the current notebook

| Dropped | Lines today | Reason (one line) |
|---|---|---|
| `SecretRegistry`, `scrub`, `Secret`, `SecretText`, `validate_secret`, `resolve_secret` | 136-280, 373-419 | Nothing is typed in, so nothing needs scrubbing; secrets live in `os.environ` only. |
| Hand-rolled env parser (`ENV_ALLOWLIST`, `parse_env`, `env`, `redis_url_from_env`, split `REDIS_*` fields) | 281-372 | marimo loads `.env` natively; `REDIS_URL` is the one Redis setting. |
| AUTORUN / HEADLESS / `pressed()` and `.env` name sniffing | 61-134 | Test hooks visible in every consent cell; the MVP tests call `app.run()` instead. |
| Recursive redaction and runtime secret learning | 441-531 | Only two headers and one body field ever carry a secret; `call_service` masks the headers and `mask_connection_config` the field. |
| HTTP choke points, route allowlist, `_dispatch` | 606-865 | A second implementation of each API written to refuse the notebook's own code; the invariants become `tests/test_safety.py`. |
| `http_call`, `HTTP_TRACE`, `as_curl`, `wire_view` | 533-560, 866-956 | `call_service` returns the wire dict; `mo.json` renders it. |
| `GuardedTracedRedis`, `_ft_create_prefixes`, `_confirm_index`, reads/writes allowlists | 999-1275 | A newcomer cannot tell the wrapper from redis-py; the run prefix, `EXPIRE` and no-`DD` carry the safety. |
| Grants, orphans, typed confirmations, `owned_index`, `claim_index` | 1384-1520, 5940-5988, 7283-7325, 7970-8014 | Deleting other runs' leftovers is not an MVP concern; TTL bounds it. |
| Disk ledger | 1522-1589 | Every id is deterministic from the run tag, or kept in the cell that created it. |
| `RECORDED_LIVE` panels | 1683-1909 | Removed by decision; live numbers move into hedged one-liners in the copy. |
| Presenter notes, `notes()`, the CSS toggle, the three CSS hacks on marimo internals | 1655-1664, 5717-5734, 5814-5822 | Talk track for the presenter, not teaching for the audience; CSS keyed on marimo class names is fragile. |
| Reset buttons and `mo.state` generation tokens | 5906, 6555, 7209, 7940 and bumps | A marimo re-run is the reset. |
| Test connection buttons and the extra probes (`FT._LIST`, `DBSIZE`, `SCAN irisnb_*`, `INFO clients`) | 5901-5937, 2411-2459 | `PING` and `INFO server` on load are enough and read-only. |
| `rs_hash_embed` word-hashing fallback | 2094-2108 | A degraded embedder makes the paraphrase demo lie; fail with a sentence instead. |
| `rs_tokens`/`rs_text_query` `(a\|a*\|%a%)` rewriting and the prefix/typo switches | 2152-2164 | Redis drops stopwords itself; the wow's three presets already show BM25 winning and losing. |
| Separate full-text example cell | 6085-6140 | BM25 is one of the wow's three engines; a second showing adds no lesson. |
| Separate pre-filter example with the year range slider | 6211-6276 | Folded into the KNN cell as a TAG multiselect; one query string teaches filter-inside-KNN. |
| Deep dive: second index FLAT/HNSW/SVS-VAMANA, `rs_compare_indexes`, `rs_has_svs` | 6386-6446, 2603-2640 | Doubles memory for a lesson that reads "same results at this scale". |
| LangCache floor probe, scope attribute detection, `LANGCACHE_SCOPE_ATTR` | 2887-2970 | The floor is verified live; the MVP sends no attributes. |
| LangCache 14-probe lab, precision/recall/hit-rate `mo.stat` tiles, scoreboard | 3008-3160 | Six probes and a verdict column carry the trade-off. |
| Cache-first chat, Teach text field, vocabulary gate, shared-cache red note, `lc_taught_prompt` prefix | 6856-6988, 3202-3271 | `mo.ui.chat` is broken on 0.25.0 and free-text teaching risks leaking to other apps; a scripted loop teaches the same thing. |
| LangCache TTL deep dive as a separate cell | 7044-7100 | Folded into the cache-aside wow (`ttlMillis` 10000, then the 404). |
| `cr_unified_tools`, `cr_predict_tools`, `cr_tool_diff` | 3571-3715 | Predicts what `tools/list` then says; the live list is the proof. |
| Field editor with `cr_edits` state and Keep this change | 7328-7442 | Replaced by one dropdown on one field, offline, honest about what is inferred. |
| Schema-driven form generator and Break it planner | 4395-4621 | Two fixed `tools/call`s teach what the generic MCP client did, in a tenth of the code. |
| Paging and provenance sliders | 7577-7638 | `has_more` and `_retrieval` appear in the two fixed calls. |
| Surface adoption, settle polling, `cr_confirm_gone`, surface listing | 3952-3977, 4044-4095, 4677-4697, 3769 | Create once; Clean up deletes by the id you kept. |
| Tool catalogue deep dive | 7803-7829 | The `tools/list` table is the catalogue. |
| Agent Memory 11-threshold sweep, band table, monotonicity check, Re-query live | 5060-5164, 8137-8271 | A debounced live re-query per slider move teaches the score-less API in three moves. |
| Memory inspector chat, presets dropdown, New session, tier toggles | 8322-8465 | Folded into one session cell that assembles the prompt once. |
| Start extraction now, Check for extracted memories, Clean up again button | 8017-8066, 8608-8651, 8471 | Not exercised live; the same Clean up button pressed later catches extraction. |
| Under the hood (`am_hood`, service `REDIS_URL` field) | 5547-5640, 8654-8709 | Needs the service's backing database URL, which customers do not have. |
| `verify.py`, `tests/` (16 files), `legacy/`, `.agents/build/` | repo | Coupled to the setup block being removed; replaced by three small test files. |
| PEP 723 header and `--sandbox` launch | 1-15 | Project mode: `pyproject.toml` and `uv run marimo edit iris_basics.py`. |

## 16. Rules for the builders

1. Every markdown cell is `mo.md(r"""...""")`, never one-line `mo.md("...")` (`marimo check
   --strict` MF007). No cell contains only `pass` (MF004).
2. A `run_button` is defined in one cell and read in the next; no cell reads its own element.
3. Seed and create cells define their ids as empty or `None` when unpressed (no `mo.stop`), so
   Clean up cells always run. Example cells use `mo.stop(not <ids>, mo.callout(..., kind="info"))`.
4. Never assign a secret (`REDIS_URL`, any key, the agent key) to a global; read `os.environ`
   inside the expression that builds the client. Never let a reply that may carry a key be a
   cell's last expression.
5. Show the wire with `display.redis_cli` or `call_service`'s `wire`; never `print`, never
   `repr(session)`.
6. Hedge every number from the live run ("in a live run on 2026-09-30", "scores vary"); state
   limits flat ("a surface has no TTL"). Redis is the actor; commands uppercase and backticked;
   no exclamation marks; lead-ins end with a colon and name actor and effect.
7. Do not change the notebook width in the editor (marimo #3543); edit the `.py` directly.

## 17. Risks flagged (not relitigated)

- `Field.redis_args()` and `IndexDefinition.args` for rebuilding the `FT.CREATE` line are
  UNVERIFIED on redis-py 8.1.0; the fallback is a hand-built argv from the same values.
- `python -m marimo` under `uv run pytest` is UNVERIFIED; so is `shutil.which("marimo")`.
- The `-32602` rejection wording and HTTP status for `filter_customer` on a numeric field is
  inferred from the schema shape, not observed live; the cell prints whatever comes back.
- `cr_model_variant` states the tool consequence of `tag` and `not indexed` from the generation
  rule, not from a live surface; the copy says so.
- The cache-aside wow sleeps 12 s inside a cell; a slow store could still answer the 404 as a
  204 (reported as such), and a 429 would surface as text.
- Python 3.11 is locked by marker but never run; a `uv sync --python 3.11 && uv run pytest`
  once before shipping, or raise the floor to 3.12.
- Column 1 has 12 cells, one over the guideline; `lc_strategies` is the fold candidate.
- Without a ledger, a LangCache Clean up after a kernel restart finds nothing; every entry
  carries `ttlMillis`, and the README says the cache self-heals in 15 minutes.
- `mo.ui.table` with `style_cell` colouring and 36-row tables in a 740 px run-mode column are
  unverified without a browser; `max_height=` is the fallback.
