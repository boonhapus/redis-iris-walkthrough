# Redis Search MVP brief for column 0 (redis-py 8.1.0, Redis Cloud free tier on Redis 8.6)

> Written 2026-09-30 for the iris-basics MVP rewrite. Read-only research: no Redis or Iris
> service was called, no `.env` value was read. Every fact is tagged **VERIFIED** (docs URL,
> package introspection, or the 2026-09-30 live run recorded in README "Verified live") or
> **UNVERIFIED**. Where this brief and `.agents/plans/research/redis-search.md` disagree, this
> brief wins; where the README "Verified live" facts and current docs disagree, the live facts win.
>
> Introspection was done with `uv run --python 3.13 --no-project --with redis==8.1.0` (plus
> `numpy==2.5.3`, `model2vec==0.9.0`, `huggingface-hub<2`). Docs were read as raw markdown from
> `github.com/redis/docs/content/...`. Commands were captured by subclassing `redis.Redis` and
> printing `execute_command` argv, or by reading `Pipeline.command_stack`, so every "wire" line
> below is what redis-py 8.1.0 actually sends, not a transcription of the docs.

## 0. Headline decisions

1. **Use `client.ft(index).create_index / search / info / dropindex`** for everything except
   `FT.HYBRID`. They are the documented production path, they emit `DIALECT 2` by default, and
   they return `Result` / `dict` objects under both RESP2 and redis-py's RESP3 default. Show the
   wire command next to each call by rebuilding it from `Query.get_args()`; it is the lesson.
2. **Send `FT.HYBRID` as an explicit argv with `execute_command`.** redis-py's `hybrid_search()`
   and its five helper classes are `@experimental` in 8.1.0 and warn on every construction; the
   hand-built argv is the one thing in this column that was proven live on 8.6.2.
3. **Pin `protocol=2` on the client.** Every reply shape in this brief was verified live under
   RESP2. Under redis-py's RESP3 default the `ft()` helpers still work, but raw replies become
   maps and the FT.HYBRID row shape is unverified. RESP3 buys nothing here (no client-side
   caching, no smart handoffs).
4. **`decode_responses=True`**, with one rule: the vector field is never read back through a core
   command (`HGETALL`, `HGET embedding`) and never listed in `RETURN`. Breaking the rule raises
   `UnicodeDecodeError` on core commands and returns silently mangled text from `FT.SEARCH`.
5. **huggingface-hub 2.0.0 cannot be installed next to model2vec 0.9.0.** Pin 1.33.0.
6. **numpy 2.5.3 needs Python 3.12.** Either pin `numpy==2.4.6` under `requires-python
   ">=3.11,<3.14"`, or raise the floor to 3.12 and keep 2.5.3.
7. **No new Redis secrets.** `REDIS_URL` (`redis://default:<password>@<host>:<port>`) is the only
   configuration this column needs.

## 1. Connect

### 1.1 redis-py 8.1.0 constructor defaults (VERIFIED, `inspect.signature(redis.Redis.__init__)`)

| kwarg | 8.1.0 default | Note |
|---|---|---|
| `socket_timeout` | `5` | Docs (produsage.md) still say "10 seconds for both"; the docs are stale. |
| `socket_connect_timeout` | `5` | Same stale docs claim. |
| `socket_keepalive` | `True` | TCP keepalive on by default (idle 30 s, interval 5 s, 3 probes per the docstring). |
| `health_check_interval` | `0` | Off. |
| `retry` | `Retry(ExponentialWithJitterBackoff(base=0.01, cap=1), retries=10)` | `DEFAULT_RETRY_COUNT = 10` in `redis/client.py`; docs say "three times" (stale). Retries only on `ConnectionError`, `TimeoutError` (redis and builtin). |
| `decode_responses` | `False` | |
| `protocol` | `None` | Falls back to `DEFAULT_RESP_VERSION = 3`: a default client sends `HELLO 3` plus `CLIENT SETINFO`, and enables maintenance notifications (docstring: "If not provided and protocol is RESP3, the maintenance notifications will be enabled by default"). |
| `legacy_responses` | `True` | With `protocol=None` this makes `ft().search()` return a `Result`, `ft().info()` a flat dict, `ft().hybrid_search()` a `HybridResult` even though the wire is RESP3 (`_RESP3_TO_RESP2_LEGACY_MODULE_CALLBACKS` in `redis/commands/search/commands.py`). Raw `execute_command("FT.*")` replies are **not** reshaped. |

`Retry(ExponentialBackoff(), 3)` from the checklist: `ExponentialBackoff(cap=0.512, base=0.008)`
computes 8, 16, 32 ms for attempts 0..2 (VERIFIED `compute()`). That is fine for a notebook
against a healthy cloud DB; the redis-py default (10 jittered tries up to 1 s) is also
acceptable. Pick one and say so in the cell; do not do both.

### 1.2 The connect cell (redis-py)

```python
import os
import redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

redis_url = os.environ.get("REDIS_URL")          # marimo loads .env next to pyproject.toml into os.environ
mo.stop(
    not redis_url,
    mo.callout(mo.md("Redis Search is not configured. Set `REDIS_URL` in `.env` (see `.env.example`)."), kind="warn"),
)

rs_client = redis.Redis.from_url(
    redis_url,
    protocol=2,                          # flat FT.* replies; every shape below was verified live under RESP2
    decode_responses=True,               # strings back; the vector field is never read through a core command
    socket_connect_timeout=5,
    socket_timeout=5,
    retry=Retry(ExponentialBackoff(), 3),  # 3 retries on ConnectionError / TimeoutError, 8-32 ms apart
    health_check_interval=30,            # PING before reuse if the connection idled > 30 s (marimo idles a lot)
)
rs_client.ping()                                                   # PING
rs_redis_version = rs_client.info("server")["redis_version"]       # INFO server  -> "8.6.2"
rs_version_tuple = tuple(int(part) for part in rs_redis_version.split(".")[:3])
rs_has_hybrid = rs_version_tuple >= (8, 4)                         # FT.HYBRID is "since 8.4.0"
```

Wire: `PING` then `INFO server`.

- `from_url` passes every kwarg through to the pool (VERIFIED: `connection_pool.connection_kwargs`
  shows `protocol 2, decode_responses True, socket_timeout 5, health_check_interval 30` and a
  `Retry` with `get_retries() == 3`). `?protocol=2` on the URL also works (VERIFIED), but keep
  the URL in `.env` plain and put `protocol=2` in code.
- `client.info("server")` returns a dict with `redis_version` as a string (VERIFIED signature;
  the redis-ai-resources hybrid recipe does `client.info()["redis_version"]` the same way).
- Feature detection of `FT.HYBRID`: the version gate `>= (8, 4)` is what the old notebook used,
  and FT.HYBRID then ran on 8.6.2 (VERIFIED live). Wrap the first FT.HYBRID call in
  `try/except redis.exceptions.ResponseError` as the backstop; the exact "unknown command"
  wording on a pre-8.4 server is UNVERIFIED. `COMMAND INFO FT.HYBRID` on Redis Cloud is
  UNVERIFIED; do not rely on it.
- Exceptions to catch in the connect cell (VERIFIED, produsage.md "Exception handling"):
  `redis.exceptions.ConnectionError` (its subclass `AuthenticationError` for a bad password),
  `redis.exceptions.TimeoutError`, `redis.exceptions.ResponseError`. redis-py exceptions can
  echo the URL in their message, so render `type(exc).__name__` plus a fixed sentence, not `str(exc)`.

### 1.3 protocol 2 vs 3: what changes on 8.1.0 (VERIFIED, docs FT.SEARCH/FT.INFO "Return information" plus parser source)

| Reply | `protocol=2` (raw `execute_command`) | `protocol=None`/3 (raw `execute_command`) | `client.ft()` helper, either protocol |
|---|---|---|---|
| `FT.SEARCH` | `[total, key, [f, v, ...], key, ...]`; with `WITHSCORES` a score sits between key and fields | map `{total_results, results: [{id, extra_attributes: {...}, score?}], attributes, format, warning}` | `Result` with `.total`, `.docs[i].id/.score/<field>` |
| `FT.INFO` | flat `[k, v, k, v, ...]` | map | `dict` keyed by str (`num_docs`, `hash_indexing_failures`, `indexing`, `percent_indexed`, `vector_index_sz_mb`, ...). Cast values with `int()`/`float()`. |
| `FT.HYBRID` | flat pairs `total_results, results: [[f, v, ...], ...], warnings, execution_time` (VERIFIED live 8.6.2 by the old `rs_parse_hybrid`; docs describe "array starting with total", which did not match) | map `{total_results, results: [map...], warnings, execution_time}` (docs); row keys UNVERIFIED | `HybridResult(total_results, results=[dict], warnings, execution_time)`; row values are `bytes` even with `decode_responses=True` because the helper sets `NEVER_DECODE` |
| `FT._LIST` | list | set | n/a |

### 1.4 decode_responses (VERIFIED by introspection)

- `redis.connection.Encoder("utf-8", "strict", True).decode(<real 1024-byte float32 blob>)` raises
  `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xfe`. So `HGETALL` or `HGET key embedding`
  on a `decode_responses=True` client crashes. `HMGET key title body category year` is fine.
- `ft().search()` sets `NEVER_DECODE` and converts fields with `to_string(value)` which is
  `bytes.decode("utf-8", "ignore")`: no crash, but a returned vector becomes garbage text. Always
  `RETURN` explicit fields and leave `embedding` out. If a cell must show the raw bytes, use
  `Query.return_field("embedding", decode_field=False)` (signature VERIFIED).
- Writing bytes is fine on a decoding client: `Encoder.encode(bytes)` passes them through.

### 1.5 Free tier limits (VERIFIED, operate/rc/.../essentials-plan-details.md and neighbours)

- 30 MB, **30 concurrent connections**, **100 ops/sec** ("assumes request size of 1 KiB;
  maximums are capped by actual MB/s reached"), 5 GB/month bandwidth, one free DB per account,
  "we will notify you before limiting". **TLS is not available on Free**, so the URL is
  `redis://`, never `rediss://`. Search is included in every 8.x Cloud database.
- One `redis.Redis` client per kernel is enough; pipelines borrow from the same pool. Do not
  create a client per cell. Whether pipelined commands are counted individually against the
  100 ops/s is UNVERIFIED (docs are silent); the load plan in section 3 stays under the cap
  even if they are.

## 2. Index

### 2.1 Schema and create (redis-py)

```python
from redis.commands.search.field import NumericField, TagField, TextField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.exceptions import ResponseError

rs_key_prefix = f"cairn:help:{run_tag}:"       # every HSET below starts with this
rs_index_name = f"idx:cairn_help:{run_tag}"    # colons in index names worked live on 8.6.2

rs_schema = (
    TextField("title", weight=2.0),            # a title match counts double in BM25
    TextField("body"),
    TagField("category"),                      # exact-match filter, e.g. @category:{returns|shipping}
    NumericField("year", sortable=True),       # range filter, e.g. @year:[2024 2026]
    VectorField("embedding", "HNSW", {"TYPE": "FLOAT32", "DIM": embedding_dim, "DISTANCE_METRIC": "COSINE"}),
)
try:
    rs_client.ft(rs_index_name).create_index(
        rs_schema, definition=IndexDefinition(prefix=[rs_key_prefix], index_type=IndexType.HASH)
    )
except ResponseError as exc:
    if "already exists" not in str(exc).lower():   # re-running the seed cell is fine
        raise
```

Wire (VERIFIED, captured argv):

```
FT.CREATE idx:cairn_help:<run> ON HASH PREFIX 1 cairn:help:<run>: SCORE 1.0 SCHEMA title TEXT WEIGHT 2.0 body TEXT WEIGHT 1.0 category TAG SEPARATOR , year NUMERIC SORTABLE embedding VECTOR HNSW 6 TYPE FLOAT32 DIM 256 DISTANCE_METRIC COSINE
```

- redis-py always adds `SCORE 1.0` when an `IndexDefinition` is passed, `WEIGHT 1.0` on every
  `TextField`, and `SEPARATOR ,` on every `TagField` (VERIFIED). Harmless; mention it once if the
  redis-cli line is shown, or strip those tokens from the display.
- `VECTOR HNSW 6` means six attribute tokens follow (2 x name/value pairs). Adding
  `"M": 16, "EF_CONSTRUCTION": 200` to the dict gives `HNSW 10 ... M 16 EF_CONSTRUCTION 200`
  (VERIFIED captured). HNSW defaults are M 16, EF_CONSTRUCTION 200, EF_RUNTIME 10 (VERIFIED,
  vectors/_index.md). At 36 documents FLAT and HNSW are indistinguishable; HNSW is the
  production default and the one Agent Memory itself uses, so keep it and say why in one line.
- `DIM` is derived from the model (`model.dim`, section 6), never typed. A blob whose length is
  not `DIM * 4` is not indexed and `hash_indexing_failures` counts it (VERIFIED, FT.INFO docs).
- `PREFIX` defaults to `*` = every key in the database (VERIFIED, FT.CREATE docs). The run-tagged
  prefix is the safety fence; never build an index with an empty or shared prefix.
- Field-option facts (VERIFIED, indexing/field-and-type-options.md): TEXT `WEIGHT`, `NOSTEM`,
  `WITHSUFFIXTRIE` (faster `*suffix`); TAG `SEPARATOR` default `,`, case-insensitive unless
  `CASESENSITIVE`; NUMERIC `SORTABLE`.
- `TEMPORARY <seconds>` exists and expiry "drops all the records associated with" the index
  (VERIFIED, FT.CREATE docs). Not recommended: it couples data lifetime to index idleness and
  its Cloud behaviour is UNVERIFIED. Key TTL plus the Clean up button covers the same risk.

### 2.2 Detecting "exists" and "not found"

- Existence probe: `rs_client.ft(rs_index_name).info()` returns a dict if the index exists and
  raises `ResponseError` if not.
- **Not found wording.** Redis 8.6.2 answers `SEARCH_INDEX_NOT_FOUND Index not found: <name>`
  on `FT.INFO` and `FT.DROPINDEX` (VERIFIED live, README). The RediSearch master source
  (`src/redisearch_rs/query_error/src/lib.rs`) defines exactly `SEARCH_INDEX_NOT_FOUND Index not
  found` (VERIFIED source). Older Search builds said `Unknown index name` (from the old
  notebook's comment; UNVERIFIED here). The docs only say "no such index". Match with
  `re.search(r"index[_ ]not[_ ]found|unknown index|no such index", str(exc), re.I)`.
- **Already exists wording.** The FT.CREATE docs say `(error) Index already exists`; the same
  RediSearch source defines `SEARCH_INDEX_EXISTS Index already exists` (VERIFIED source, the
  live 8.6.2 string is UNVERIFIED because the old notebook never hit it). Match on
  `"already exists" in str(exc).lower()`, which covers both.
- After the create, `int(info["num_docs"])` and `int(info["hash_indexing_failures"])` are the two
  numbers to show. FT.INFO's `indexing` is `1` while a background scan runs; with the index
  created before the data, each HSET is indexed on write, so the scan is trivial.

## 3. Load 36 hashes with a float32 vector

### 3.1 redis-py

```python
import time
import numpy as np

RS_KEY_TTL_SECONDS = 3600      # every key expires on its own; Clean up only shortens that
RS_CHUNK = 12                  # 12 documents = 24 commands per round trip

rs_help_articles = load_help_articles()                          # src/, 36 dicts: id, title, body, category, year
rs_vectors = embed([f"{a['title']}. {a['body']}" for a in rs_help_articles])   # (36, 256) float32, unit length

for start in range(0, len(rs_help_articles), RS_CHUNK):
    pipe = rs_client.pipeline(transaction=False)                 # plain pipeline, not MULTI/EXEC
    for article, vector in zip(rs_help_articles[start:start + RS_CHUNK], rs_vectors[start:start + RS_CHUNK]):
        key = f"{rs_key_prefix}{article['id']}"
        pipe.hset(key, mapping={
            "title": article["title"],
            "body": article["body"],
            "category": article["category"],
            "year": article["year"],
            "embedding": vector.astype(np.float32).tobytes(),     # DIM * 4 bytes, or it is silently not indexed
        })
        pipe.expire(key, RS_KEY_TTL_SECONDS)
    pipe.execute()
    time.sleep(0.25)                                             # stay well under 100 ops/s on the free tier

rs_index_info = rs_client.ft(rs_index_name).info()
rs_indexed = int(rs_index_info["num_docs"])                      # expect 36
rs_failures = int(rs_index_info["hash_indexing_failures"])       # expect 0
```

Wire per document (VERIFIED, `Pipeline.command_stack`; `mapping=` is flattened to field/value pairs):

```
HSET cairn:help:<run>:ship-times title "How long does delivery take?" body "Standard orders leave ..." category shipping year 2025 embedding "<1024 bytes>"
EXPIRE cairn:help:<run>:ship-times 3600
```

then `FT.INFO idx:cairn_help:<run>`.

- `pipeline(transaction=True)` is the default and wraps in MULTI/EXEC (VERIFIED, transpipe.md);
  `transaction=False` is the right choice for a bulk load.
- Budget: 36 HSET at about 1.3 KiB each plus 36 EXPIRE = 72 commands, roughly 50 KiB. The free
  tier's 100 ops/s "assumes 1 KiB requests" and is "capped by actual MB/s reached" (about
  100 KiB/s). Three chunks with 0.25 s pauses finish in about one second and never exceed the
  cap even if each pipelined command counts as one op (UNVERIFIED how they are counted).
- `redis-py` offers `client.ft().batch_indexer(chunk_size)` too, but it is built for the legacy
  `FT.ADD` flow; the pipeline above is what the docs and recipes use.
- Byte packing and Redis' expectation (VERIFIED, vectors/_index.md "Hash" and redis-py
  vecsearch.md): FLOAT32 blob via `np.asarray(v, dtype=np.float32).tobytes()`; a 256-d vector is
  1024 bytes; `np.frombuffer(blob, dtype=np.float32)` round-trips exactly.

## 4. Queries (MVP set)

All calls use `rs_client.ft(rs_index_name).search(query, query_params)`. `Query(...)` emits
`DIALECT 2` and `LIMIT 0 10` by default (VERIFIED: `Query("trail shoes").get_args()` returns
`['trail shoes', 'DIALECT', 2, 'LIMIT', 0, 10]`; the vecsearch docs say "From v6.0.0 onwards,
redis-py uses query dialect 2 by default"). Rebuild the wire line for display with
`["FT.SEARCH", rs_index_name, *query.get_args(), *rs_client.ft(rs_index_name).get_params_args(params)]`
and print the blob as `"<1024 bytes>"`.

### 4.1 Full-text with BM25 scores

```python
from redis.commands.search.query import Query

rs_text_query = (
    Query("hiking boots")                 # AND of the words across all TEXT fields; "hiking | boots" for OR
    .scorer("BM25STD")
    .with_scores()
    .return_fields("title", "category")
    .paging(0, 5)
)
rs_text_result = rs_client.ft(rs_index_name).search(rs_text_query)
rs_text_result.total                                     # matches, not rows
[(doc.id, round(doc.score, 3), doc.title) for doc in rs_text_result.docs]
```

Wire (VERIFIED captured):

```
FT.SEARCH idx:cairn_help:<run> "hiking boots" SCORER BM25STD WITHSCORES RETURN 2 title category DIALECT 2 LIMIT 0 5
```

Variants (VERIFIED, query/full-text.md; the notebook needs at most one switch):

| Want | Query string | Rule |
|---|---|---|
| prefix | `boot*` or `@title:(boot*)` | at least two characters before `*` |
| suffix / infix | `*proof`, `*proof*` | faster with `WITHSUFFIXTRIE` on the field |
| typo | `%waterproofng%`, `%%..%%` for distance 2 | field-scoped form is `@body:(%word%)` with **no quotes** around the term |
| field-scoped | `@title:(hiking boots)` | `@title|body:(...)` spans two fields |
| boost | `@title:(boots)=>{$weight: 5.0}` | |

- `BM25STD` is the default scorer since 8.4 and has existed since Search 2.10 (Redis 7.4);
  `BM25` is the deprecated name (VERIFIED, advanced-concepts/scoring.md). No fallback is needed
  on any current Cloud version.
- The scoring page says scores are "between 0.0 and 1.0". BM25STD values are not bounded to 1
  in practice; do not put a range in customer copy (UNVERIFIED either way).
- Stopwords (`the`, `a`, `is`, ...) are not indexed, so a query made only of stopwords returns
  nothing (VERIFIED, full-text.md).
- Live fact (README): "Trail shoes feel cramped" returned **no** BM25 row against the 36
  articles, because no article contains "cramped" or "shoes". That is the lexical trap the
  hybrid cell needs.

### 4.2 KNN

```python
k = 3
rs_knn_query = (
    Query(f"*=>[KNN {k} @embedding $vector AS vector_score]")   # "*" = no pre-filter
    .sort_by("vector_score")                                    # ASC: smaller cosine distance = closer
    .return_fields("title", "category", "vector_score")
    .paging(0, k)                                               # LIMIT must be k when k > 10
)
rs_knn_result = rs_client.ft(rs_index_name).search(rs_knn_query, {"vector": query_vector_bytes})
[(doc.title, float(doc.vector_score)) for doc in rs_knn_result.docs]
```

Wire (VERIFIED captured):

```
FT.SEARCH idx:cairn_help:<run> "*=>[KNN 3 @embedding $vector AS vector_score]" RETURN 3 title category vector_score SORTBY vector_score ASC DIALECT 2 LIMIT 0 3 PARAMS 2 vector "<1024 bytes>"
```

- Without `AS`, the distance field is `__embedding_score` (the name depends on the vector
  field; the docs' example is `__vector_score` for a field called `vector`) (VERIFIED,
  query/vector-search.md). Alias it; `vector_score` reads better in a table.
- Default `LIMIT 0 10`, so `KNN 25` without `.paging(0, 25)` returns 10 rows (VERIFIED,
  vectors/_index.md "Important notes" and FT.SEARCH docs).
- COSINE distance = 1 - cos, bounded by 2; on unit vectors it equals `IP` distance
  (VERIFIED, vectors/_index.md distance table). Field values come back as `str`; cast.
- Live fact (README): the vector search ranked the boot guide #1 of 10 for "Trail shoes feel
  cramped".

### 4.3 VECTOR_RANGE (the "a cache can MISS" bridge to LangCache)

```python
rs_range_query = (
    Query("@embedding:[VECTOR_RANGE $radius $vector]=>{$YIELD_DISTANCE_AS: vector_score}")
    .sort_by("vector_score")
    .return_fields("title", "category", "vector_score")
    .paging(0, 10)
)
rs_range_result = rs_client.ft(rs_index_name).search(
    rs_range_query, {"radius": round(float(radius), 4), "vector": query_vector_bytes}
)
rs_range_result.total      # 0 is a legitimate answer: nothing is within the radius
```

Wire (VERIFIED captured):

```
FT.SEARCH idx:cairn_help:<run> "@embedding:[VECTOR_RANGE $radius $vector]=>{$YIELD_DISTANCE_AS: vector_score}" RETURN 3 title category vector_score SORTBY vector_score ASC DIALECT 2 LIMIT 0 10 PARAMS 4 radius 0.5 vector "<1024 bytes>"
```

- Range queries have no default distance field; `$YIELD_DISTANCE_AS` is required to sort by
  distance (VERIFIED, query/vector-search.md). `$EPSILON: 0.01` is the HNSW default.
- Calibration for potion-base-8M from the earlier brief (computed offline, UNVERIFIED live): a
  paraphrase sits near 0.22, unrelated text near 0.83. A default radius of 0.5 with a slider
  0.05 to 1.0 gives a clean HIT/MISS demo. `round(radius, 4)` because a marimo slider hands
  over `0.15000000000000002`.
- The lesson, stated honestly: an unfiltered KNN returns `min(k, N)` rows for any question; a
  range query can return none, and that empty answer is what LangCache's similarity threshold
  turns into a cache MISS. (A filtered KNN can also return fewer than k; do not say KNN "never"
  misses.)

### 4.4 Pre-filtered KNN (TAG + NUMERIC)

```python
categories = ["returns", "shipping"]          # from mo.ui.multiselect
year_from, year_to = 2024, 2026               # from mo.ui.range_slider
rs_prefilter = f"(@category:{{{'|'.join(categories)}}} @year:[{year_from} {year_to}])"
rs_filtered_query = (
    Query(f"{rs_prefilter}=>[KNN 5 @embedding $vector AS vector_score]")
    .sort_by("vector_score")
    .return_fields("title", "category", "year", "vector_score")
    .paging(0, 5)
)
rs_filtered_result = rs_client.ft(rs_index_name).search(rs_filtered_query, {"vector": query_vector_bytes})
```

Wire (VERIFIED captured with a price filter; identical shape):

```
FT.SEARCH idx:cairn_help:<run> "(@category:{returns|shipping} @year:[2024 2026])=>[KNN 5 @embedding $vector AS vector_score]" RETURN 4 title category year vector_score SORTBY vector_score ASC DIALECT 2 LIMIT 0 5 PARAMS 2 vector "<1024 bytes>"
```

- Syntax (VERIFIED, query/combined.md and dialects.md): space = AND, `|` = OR, `-` = NOT,
  `@tag:{a|b}`, `@num:[min max]`, `[(min +inf]` exclusive, DIALECT 2 also allows `@year>=2024`.
  Wrap the whole pre-filter in parentheses before `=>`.
- The filter runs inside the vector search (Redis picks `HYBRID_BATCHES` or `HYBRID_ADHOC_BF`
  per query), so all k results qualify instead of a top k with the wrong ones thrown away
  (VERIFIED, vectors/_index.md "Filters" and "How filtering works"). `HYBRID_POLICY`,
  `FT.PROFILE` and the planner modes are deep-dive material: leave them out of the MVP.
- Category values are single lower-case words in the sample data, so no escaping is needed. If a
  category ever contains a space or punctuation, escape it (`@category:{trail\ shoes}`).

### 4.5 FT.HYBRID with RRF (8.4+) and the fallback

Recommended: build the argv and send it with `execute_command`. This exact argv ran on 8.6.2 and
kept the boot guide #1 for the lexical-trap query (VERIFIED live, README).

```python
k = 10
rs_hybrid_argv = [
    "FT.HYBRID", rs_index_name,
    "SEARCH", "trail shoes feel cramped", "SCORER", "BM25STD",       # bare SCORER works on 8.6.2 (live)
    "VSIM", "@embedding", "$vector", "KNN", 2, "K", k,               # counts include every token that follows
    "COMBINE", "RRF", 4, "WINDOW", 20, "CONSTANT", 60,               # the documented defaults, made visible
    "LOAD", 2, "@__key", "@title",
    "LIMIT", 0, k,
    "PARAMS", 2, "vector", query_vector_bytes,
]
rs_hybrid_reply = rs_client.execute_command(*rs_hybrid_argv)
# RESP2 with decode_responses=True (verified live): a flat pair list ->
#   ["total_results", 10, "results", [["__key", "cairn:...", "title", "..."], ...], "warnings", [...], "execution_time", "0.42"]
rs_hybrid_fields = dict(zip(rs_hybrid_reply[::2], rs_hybrid_reply[1::2]))
rs_hybrid_rows = [dict(zip(row[::2], row[1::2])) for row in rs_hybrid_fields["results"]]
rs_hybrid_keys = [row["__key"] for row in rs_hybrid_rows]           # best first
```

Wire:

```
FT.HYBRID idx:cairn_help:<run> SEARCH "trail shoes feel cramped" SCORER BM25STD VSIM @embedding $vector KNN 2 K 10 COMBINE RRF 4 WINDOW 20 CONSTANT 60 LOAD 2 @__key @title LIMIT 0 10 PARAMS 2 vector "<1024 bytes>"
```

Facts (VERIFIED, commands/ft.hybrid.md unless noted):

- `since: 8.4.0`. Defaults: `LIMIT 10`, `SCORER BM25STD`, `KNN K 10`, `RRF WINDOW 20`,
  `CONSTANT 60`, `EF_RUNTIME 10`. The command "will only return document IDs (`keyid`) and
  scores"; use `LOAD` to get fields. Reserved names: `@__key`, `@__score`, `@vector_distance`,
  `@__combined_score`.
- Parameter-count convention: the number after `KNN`, `COMBINE RRF`, `LOAD`, `PARAMS` counts
  every token that follows in that clause (`KNN 4 K 10 EF_RUNTIME 100`).
- The docs page shows `SCORER 4 BM25 1.5 0.8` (count-prefixed) in one example and bare `SCORER
  scorer` in its argument table. Bare `SCORER BM25STD` is what ran live (VERIFIED live); the
  count-prefixed form is UNVERIFIED.
- `COMBINE LINEAR 4 ALPHA 0.7 BETA 0.3` is the alternative fusion; not needed in the MVP.
- Redis Cloud 8.4 limitations (operate/rc/changelog/version-release-notes/8-4/, from the
  earlier brief): `EXPLAINSCORE`, `SHARD_K_RATIO`, `YIELD_DISTANCE_AS`, `WITHCURSOR` unavailable
  for FT.HYBRID and post-COMBINE `FILTER` unsupported. None of them is used above.
- The FT.HYBRID compatibility table lists Standard and Active-Active only, no "Free & Fixed"
  column. It ran on this account's free-tier 8.6.2 database anyway (VERIFIED live).

redis-py's own wrapper, for the record (VERIFIED captured; every class is `@experimental` and
emits a `UserWarning` on construction and on each `hybrid_search` call):

```python
from redis.commands.search.hybrid_query import (
    CombinationMethods, CombineResultsMethod, HybridPostProcessingConfig,
    HybridQuery, HybridSearchQuery, HybridVsimQuery, VectorSearchMethods,
)
hybrid = rs_client.ft(rs_index_name).hybrid_search(
    HybridQuery(
        HybridSearchQuery("trail shoes feel cramped", scorer="BM25STD"),
        HybridVsimQuery("@embedding", "$vector", VectorSearchMethods.KNN, {"K": 10}),  # pass "@embedding": redis-py sends the field name verbatim
    ),
    combine_method=CombineResultsMethod(CombinationMethods.RRF, WINDOW=20, CONSTANT=60),
    post_processing=HybridPostProcessingConfig().load("@__key", "@title").limit(0, 10),
    params_substitution={"vector": query_vector_bytes},
)
# -> FT.HYBRID idx SEARCH "trail shoes feel cramped" SCORER BM25STD VSIM @embedding $vector KNN 2 K 10 COMBINE RRF 4 WINDOW 20 CONSTANT 60 LOAD 2 @__key @title LIMIT 0 10 PARAMS 2 vector <blob>
# hybrid.results is a list of dicts; values are bytes (NEVER_DECODE), so .decode() them.
```

The argv is byte-for-byte the same as the hand-built one, which is the point: the wrapper adds
warnings and a bytes-only result for no gain in a teaching notebook. If the wrapper is used,
pass `"@embedding"` with the `@`, as the docs and the live run did; without it redis-py sends
`VSIM embedding ...` and that form is UNVERIFIED.

**Fallback on servers below 8.4: skip with a note.** Show the BM25 and KNN rank columns, and in
place of the hybrid column a `mo.callout(kind="info")`: "FT.HYBRID needs Redis 8.4 or later;
this database is 8.2.x." That keeps one code path in the column. The alternative is the
six-line client-side RRF from the redis-ai-resources hybrid recipe (`score += 1 / (60 + rank)`
over the two top-10 lists), labelled "client-side RRF"; it is honest and cheap, but it is a
second implementation of the thing the cell is teaching. Only add it if the demo must run on
7.4/8.2 databases; Redis Cloud offers 8.6 for new free databases today (VERIFIED live).

## 5. Clean up

```python
import re

try:
    rs_client.ft(rs_index_name).dropindex()                     # FT.DROPINDEX idx   (no DD: documents stay)
    rs_index_dropped = True
except ResponseError as exc:
    if not re.search(r"index[_ ]not[_ ]found|unknown index|no such index", str(exc), re.I):
        raise
    rs_index_dropped = False                                    # already gone, not an error

rs_keys = [f"{rs_key_prefix}{article['id']}" for article in rs_help_articles]   # deterministic: no SCAN
rs_keys_removed = rs_client.unlink(*rs_keys)                    # UNLINK key1 ... key36 -> count actually removed
```

Wire (VERIFIED captured): `FT.DROPINDEX idx:cairn_help:<run>` then
`UNLINK cairn:help:<run>:ship-times cairn:help:<run>:ship-express ...`.

Why never `DD` (VERIFIED, commands/ft.dropindex.md and best-practices/index-mgmt-best-practices.md):

- `FT.DROPINDEX index DD` deletes the document keys as well, asynchronously, and carries ACL
  category `@dangerous`. The index management guide says "Be cautious with the `DD` flag to
  avoid unintended data deletion."
- The blast radius is the index's `PREFIX`, not the notebook's key list. A wrong or empty
  prefix (default `*`) turns `DD` into "delete every matching key in the database", and Redis
  Cloud cannot scope the notebook to a narrower user (ACL SETUSER is unsupported on Cloud).
- If an initial scan is still running, `DD` deletes only the hashes indexed so far and leaves the
  rest, so the result is not even predictable.
- The notebook knows exactly which 36 keys it wrote, so `UNLINK` on that list is both narrower
  and idempotent (a second press returns 0). Keys also carry a one-hour TTL, so a closed laptop
  cleans up by itself and only the index outlives the run.
- `UNLINK` is the non-blocking `DEL`; on 36 keys either is fine, but `UNLINK` is the production
  habit.

Order does not matter for correctness; dropping the index first means the `UNLINK` does not
trigger 36 index updates. Never `FLUSHALL`/`FLUSHDB` (both are supported on Cloud and would work).

## 6. Embeddings: model2vec 0.9.0 potion-base-8M

### 6.1 Dependency facts (VERIFIED, PyPI JSON and `uv pip compile --python-version 3.13 --python-platform windows`)

- `model2vec==0.9.0` requires `jinja2, joblib, numpy, safetensors, tokenizers>=0.20, tqdm`;
  `requires_python >=3.10`. It does not depend on huggingface-hub directly; tokenizers does.
- `tokenizers 0.23.2` (latest stable) requires `huggingface-hub<2.0,>=0.16.4`. The pre-releases
  `1.0.0rc1/rc2` (2026-09-21) keep the same `<2.0` pin.
- Therefore **`model2vec==0.9.0` + `huggingface-hub==2.0.0` is unsatisfiable** (uv: "model2vec==0.9.0
  depends on huggingface-hub>=0.16.4,<2.0 ... your requirements are unsatisfiable").
- Unpinned resolution on Python 3.13 (2026-09-30): `huggingface-hub==1.33.0`, `tokenizers==0.23.2`,
  `safetensors==0.8.0`, `numpy==2.5.3`, `hf-xet==1.6.0`, `httpx==0.28.1` (24 packages).
- `huggingface-hub 1.33.0` depends on `httpx<1,>=0.23.0`. **httpx stays installed as a transitive
  dependency** after the notebook's own HTTP code moves to niquests. That is not a conflict;
  just do not claim "httpx is gone" in the README.
- `huggingface-hub 2.0.0` depends on `httpx2<3,>=2.0.0` instead; irrelevant until tokenizers lifts
  its pin.
- numpy: `2.4.6` is the last release with `requires_python >=3.11`; `2.5.0` and later need
  `>=3.12`. With `requires-python = ">=3.11,<3.14"` pin `numpy==2.4.6`; or set the floor to 3.12
  and pin `2.5.3`. This must be decided before `uv lock` or the lock fails on 3.11.

Recommended pins for pyproject.toml (this column's share): `redis==8.1.0`, `model2vec==0.9.0`,
`huggingface-hub==1.33.0`, `numpy==2.4.6` (or `2.5.3` with a 3.12 floor). Let `uv.lock` pin
`tokenizers`, `safetensors` and `hf-xet`; listing them as direct dependencies only adds churn.

### 6.2 Loading (VERIFIED, signatures from huggingface_hub 1.33.0 and model2vec 0.9.0; behaviour from the earlier brief's runs)

```python
import os
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")   # Windows: silence the symlink warning
import numpy as np
from huggingface_hub import snapshot_download
from model2vec import StaticModel

EMBEDDING_MODEL = "minishlab/potion-base-8M"
EMBEDDING_MODEL_FILES = ["config.json", "model.safetensors", "tokenizer.json"]   # the three files model2vec's layout check needs

def load_embedding_model() -> StaticModel:
    try:   # cache first: no network, sub-second
        model_path = snapshot_download(EMBEDDING_MODEL, allow_patterns=EMBEDDING_MODEL_FILES, local_files_only=True)
    except Exception:   # not cached yet: about 30 MB, a few seconds
        model_path = snapshot_download(EMBEDDING_MODEL, allow_patterns=EMBEDDING_MODEL_FILES)
    return StaticModel.from_pretrained(model_path)     # a local folder path returns at once

embedding_model = load_embedding_model()
embedding_dim = embedding_model.dim                    # 256; property returns embedding.shape[1]

def embed(texts: list[str]) -> np.ndarray:
    vectors = np.asarray(embedding_model.encode(list(texts)), dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)   # already unit length; this makes it explicit

def to_float32_bytes(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()            # 256 * 4 = 1024 bytes
```

- `snapshot_download(repo_id, *, allow_patterns=None, ignore_patterns=None, local_files_only=False,
  cache_dir=None, token=None, force_download=False, revision=None, ...)` (VERIFIED signature).
  The same `allow_patterns` must be passed on both calls: with `local_files_only=True` and a
  different pattern set, 1.33.0 raises `IncompleteSnapshotError` on a partial snapshot
  (VERIFIED by the earlier brief's verifier on 1.33.0).
- model2vec's layout check (`model2vec/persistence/datamodels.py`) needs exactly `config.json`,
  `model.safetensors`, `tokenizer.json` (VERIFIED source). `README.md` is not needed; the
  30 MB ONNX file is skipped. Total about 31 MB instead of 61 MB.
- `StaticModel.from_pretrained(path)` with an existing local folder returns it directly
  (`_resolve_folder`: `if folder_or_repo_path.exists(): return`), so the Windows
  `force_download=False` bug in model2vec never comes into play (VERIFIED source).
- `StaticModel.from_pretrained(path, token=None, normalize=None, subfolder=None, quantize_to=None,
  dimensionality=None, vocabulary_quantization=None, force_download=True)`;
  `encode(sentences, show_progress_bar=False, max_length=512, batch_size=1024,
  use_multiprocessing=True, multiprocessing_threshold=10000)` (VERIFIED signatures). Output
  `(n, 256)` float32 with norms 0.99999994 to 1.0 and `model.normalize is True` (VERIFIED by the
  earlier brief). 36 texts never reach the multiprocessing threshold.
- Failure mode: if the download fails (no network, blocked host) the column should say so and
  stop (`mo.stop`), not degrade to a word-hashing embedder. The old fallback produced a demo
  that could not find paraphrases while looking like it worked.

### 6.3 Where it lives

The embedding loader, the 36 Cairn articles, and a `redis_cli(argv)` formatter are not Redis
Iris and belong in `src/` (rule 6). Keep `to_float32_bytes` and the `HSET ... embedding <bytes>`
mapping visible in the notebook: "a vector is DIM x 4 raw bytes in a hash field" is a Redis
lesson, not display glue.

## 7. Recommended column 0 (9 cells, about 350 lines including markdown)

| # | Cell | Contents |
|---|---|---|
| 1 | Title | Two short paragraphs: the Query Engine is what the rest of Iris rests on; you describe an index once, it watches a key prefix, every `HSET` under it is searchable. Docs link. Mermaid `App -> embed locally -> HSET -> index watches prefix -> FT.SEARCH`. |
| 2 | Connect | Section 1.2 verbatim. Output: one line "Connected: Redis 8.6.2, Search on, FT.HYBRID available". `mo.stop` with the not-configured note if `REDIS_URL` is missing. Connecting on load is read-only (`PING`, `INFO server`); no button needed. |
| 3 | Embed | Section 6.2 from `src/`; output "potion-base-8M, 256 dimensions, 1024 bytes per vector, computed on this machine". |
| 4 | Create and load (run_button) | Sections 2.1 and 3.1. Output: `num_docs`, `hash_indexing_failures`, and the `FT.CREATE` plus one `HSET` as redis-cli. |
| 5 | Example 1: full-text | Text input, one switch "also match typos and prefixes" that rewrites each word to `word|word*|%word%`. Table `title, category, score`. redis-cli line. |
| 6 | Example 2: KNN vs VECTOR_RANGE | One question box, `k` slider, radius slider (both `debounce=True`). KNN table, then a HIT/MISS callout for the range. Both redis-cli lines. |
| 7 | Example 3: pre-filtered KNN | Category multiselect and year range slider feeding section 4.4; show filtered vs unfiltered top 5. |
| 8 | Wow: one question, three engines | Dropdown of three presets ("Trail shoes feel cramped" lexical trap, an exact product word, a typo) plus free text. One table: article, BM25 rank, vector rank, hybrid rank. Hybrid via section 4.5; note instead of a column when `rs_has_hybrid` is False. Three redis-cli lines. |
| 9 | Clean up (run_button, kind="danger") | Section 5. Output: "Dropped 1 index, unlinked 36 keys" or "already gone". |

Copy follows `demo/.agents/VOICE.md`: problem before product, "you", no exclamation marks, hedge
numbers not limits, `FT.CREATE`/`FT.SEARCH` in code font.

### 7.1 Keep from the current column 0

- The 36 Cairn articles (`rs_articles`, six categories x six, written so that no article says
  "cramped" or "shoes" while the boot guide answers both). Move to `src/`.
- The three-preset wow idea and its live-verified lexical trap.
- The KNN-vs-range HIT/MISS framing and the "that is LangCache's threshold" hand-off.
- The `redis_cli(argv)` formatter (blob shown as `"<1024 bytes>"`). Move to `src/`.
- The not-found regex (`index[_ ]not[_ ]found|unknown index|no such index`).
- Chunked writes with a pause, EXPIRE on every key, FT.DROPINDEX without DD.

### 7.2 Drop, and why

| Drop | Why |
|---|---|
| `SecretText("REDIS_URL")` field, `redis_url_secret`, `register_url_password`, scrub registry | marimo loads `.env` natively; a typed-in URL is a second config path with its own leak surface. |
| Test connection button, `rs_probe` (`FT._LIST`, `DBSIZE`, `SCAN irisnb_*`, `INFO clients`), `rs_describe` | `PING` + `INFO server` is enough and read-only; the leftovers scan exists only because of the ledger. |
| "Leftovers from other runs" accordion, `rs_leftovers`, `rs_drop_orphans`, typed confirmation | Orphan handling is out of scope by decision; TTL on keys bounds the damage, and index leftovers are visible in Redis Insight. |
| Disk ledger (`ledger_add`, `ledger_live`) | The key list is deterministic from the sample data plus run tag; nothing to remember. |
| `GuardedTracedRedis`, allowlist, `redis_cli_since`, "Command trace" accordion | Guard is replaced by the run-tag prefix and the no-DD/no-FLUSH rule; the trace is replaced by one redis-cli line per example. |
| `connect_redis` pinning `decode_responses=False` and hand-written `rs_parse_search`/`rs_parse_hybrid`/`rs_parse_info` RESP2 parsers | `client.ft()` returns `Result`/`dict`; only FT.HYBRID keeps a 3-line parse. |
| `rs_hash_embed` fallback and the "Fell back to word hashing" warning | A degraded embedder makes the paraphrase demo lie. Fail loudly instead. |
| `rs_tokens`/`rs_text_query` stopword tokenizer that turns input into `(a|b|a*|%a%)` | Redis already drops stopwords; send the user's words, with one optional switch. |
| Presenter notes (`notes()`, `ib_notes`), `recorded_live("rs")`, `status_callout` wrappers, `with_spinner` | Removed by decision; `mo.callout` and `mo.status.spinner` are native. |
| Reset button, `mo.state` generation token, `pressed(stage=...)`, AUTORUN/HEADLESS branches | Removed by decision; a marimo re-run is the reset. |
| Deep dive "index anatomy" (`rs_build_second`, FLAT/HNSW/SVS dropdown, metric dropdown, `rs_compare_indexes`) | Second index over the same prefix doubles memory and ops for a lesson that reads "same results at this scale". Not MVP. |
| `rs_has_svs` and SVS-VAMANA mentions | Not used once the deep dive goes. |

### 7.3 Naming (full words, `rs_` prefix)

`rs_client`, `rs_redis_version`, `rs_has_hybrid`, `rs_key_prefix`, `rs_index_name`,
`rs_help_articles`, `rs_vectors`, `rs_index_info`, `rs_text_query`/`rs_text_result`,
`rs_knn_query`/`rs_knn_result`, `rs_range_query`/`rs_range_result`, `rs_prefilter`,
`rs_filtered_result`, `rs_hybrid_argv`/`rs_hybrid_rows`, `rs_seed_button`, `rs_cleanup_button`,
`embedding_model`, `embedding_dim`, `embed`, `to_float32_bytes`, `query_vector_bytes`.

## 8. Secrets and configuration

- Required: `REDIS_URL = redis://default:<password>@<host>:<port>` from the Redis Cloud console
  (database -> Configuration -> Public endpoint; password under Default user, eye icon). Free
  tier: `redis://`, not `rediss://` (no TLS on Free). No other Redis secret is needed; there is no
  API key for the local embedder.
- Optional and not recommended: `REDIS_URL` with `?protocol=2` (redundant with the code).
- `.env` is loaded by marimo from the directory of `pyproject.toml`; the connect cell reads
  `os.environ` only.

## 9. Open questions (UNVERIFIED, in priority order)

1. Live wording of the 8.6.2 "index already exists" error (source says `SEARCH_INDEX_EXISTS
   Index already exists`; the `"already exists"` substring match covers both spellings).
2. Whether pipelined commands count individually against the free tier's 100 ops/s. The load
   plan is safe either way.
3. FT.HYBRID row shape under RESP3 (`protocol=None`). Moot with `protocol=2`.
4. `VSIM embedding` (no `@`) as emitted by redis-py's `HybridVsimQuery("embedding", ...)`. Moot
   if `"@embedding"` is passed, which is what the live run sent.
5. The exact `ResponseError` text for `FT.HYBRID` on a pre-8.4 server. The version gate runs first.
6. The BM25STD score range (docs claim 0..1). Not needed in copy.
7. Whether the potion-base-8M calibration numbers (paraphrase about 0.22, unrelated about 0.83
   cosine distance) hold for the new default radius; measure once live and adjust the slider default.

## 10. Sources

Docs (raw markdown from github.com/redis/docs, 2026-09-30): develop/clients/redis-py/{produsage,
connect, vecsearch, transpipe}.md; develop/ai/search-and-query/query/{full-text, vector-search,
combined}.md; develop/ai/search-and-query/advanced-concepts/{scoring, dialects, expiration}.md;
develop/ai/search-and-query/vectors/_index.md; develop/ai/search-and-query/indexing/{schema-definition,
field-and-type-options}.md; develop/ai/search-and-query/best-practices/{index-mgmt-best-practices,
dev-to-prod-best-practices}.md; commands/{ft.create, ft.search, ft.hybrid, ft.dropindex}.md;
operate/rc/subscriptions/view-essentials-subscription/essentials-plan-details.md;
operate/rc/databases/create-database/create-free-database.md; operate/rc/security/database-security/tls-ssl.md.
(`develop/clients/redis-py/queryindex.md` and `develop/ai/search-and-query/indexing.md` do not
exist; `develop/ai/search-and-query/` has no hybrid-search page, FT.HYBRID is documented only
under `commands/`.)

Recipes: redis-developer/redis-ai-resources python-recipes/vector-search/00_redispy.ipynb
(redis-py field classes, KNN/range/pre-filter/BM25 queries, `try: ft().info() except: create_index`)
and 02_hybrid_search.ipynb (version gate `>= 8.4.0`, redisvl `HybridQuery`, client-side
`fuse_rankings_rrf`).

Code: redis 8.1.0 (`redis/client.py`, `redis/commands/search/{commands,query,field,
index_definition,hybrid_query,hybrid_result,result,_util}.py`, `redis/connection.py`,
`redis/retry.py`, `redis/backoff.py`); model2vec 0.9.0 (`model2vec/persistence/{persistence,
datamodels}.py`, `StaticModel.dim`); huggingface_hub 1.33.0 (`snapshot_download` signature);
RediSearch master `src/redisearch_rs/query_error/src/lib.rs`; PyPI JSON for model2vec,
tokenizers, huggingface-hub, numpy.

Repo: `iris_basics.py` lines 1277-1320 and 1912-2640 (old column 0 helpers), 5684-6449 (old
column 0 cells); `README.md` "Verified live"; `.agents/plans/iris-basics-4-column-plan.md`
sections 5 and 10; `.agents/plans/research/redis-search.md` (Verifier corrections).
