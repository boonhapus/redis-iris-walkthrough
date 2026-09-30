# Redis Agent Memory — Interactive Internals Notebook

**Revised build plan / scoping document — supersedes `redis-marimo-demo-plan.md`**

| | |
|---|---|
| **Audience** | Developers, hands-on. Ideally an Iris prospect; stands alone if not |
| **Deliverable** | One marimo notebook + README + SAFETY.md, in one public repo. Runs against the participant's own Redis Cloud DB or their own local Redis |
| **Connectivity** | Live only. No mock mode, no WASM (Pyodide has no raw TCP sockets, so redis-py cannot reach Redis at all) |
| **Effort** | ~6.5 build-days of work, scheduled over 8, plus 2 days of genuinely empty slack |
| **Stack** | marimo (pinned) · redis-py 7.4.0 · model2vec · numpy · uv |

---

## 1. What this is

One marimo notebook that builds a working **agent memory** — the thing Redis Iris ships
as a hosted Memory API — out of raw redis-py in about forty visible lines, and then opens
the hood on it. The participant tells it a fact, chats about something else, asks a
question with zero keyword overlap, and gets the fact back with a cosine distance. Then
the notebook shows that the feature is a LIST with an `LTRIM` and an `EXPIRE`, a HASH per
memory with a 1024-byte vector field, and one `FT.CREATE` that watches a key prefix.

**Three acts, in file order and in narrative order:**

1. **Act 1 — run it and see it.** Two controls and one button. Remember a fact, ask a
   question, get a recall card plus the exact commands that produced it. ~2 minutes.
2. **Act 2 — break down the components.** Four panels, a vertical scroll, not tabs. Each
   one names a claim, shows a live table, shows the commands, and says when the shape is
   wrong. This is where the internals live.
3. **Act 3 — walk the code.** Six numbered steps rendering the *real* source of the
   functions Act 1 just ran, each with a run gate that executes that one step against
   their database. Closes on a three-rung abstraction ladder: our `FT.SEARCH`, the
   redisvl equivalent, and the one `httpx.post` that is the hosted product.

**What it is not:**

- **Not a performance demo.** No benchmarks, no percentiles, no throughput, no
  cache-hit-ratio, no Redis-vs-SQL. Latency appears exactly once: as one narrow,
  unaggregated column in the command trace, with a footnote that row one includes
  connection setup. If a chart of a duration ever appears in this notebook, it is a bug.
- **Not a data-types tour.** No sorted sets, no streams, no bitmaps, no HLL, no geo, no
  JSON. Coverage is explicitly not a goal — `redis.io`'s per-type tutorials and
  `university.redis.io`'s free "Get started with Redis" path already own that spine and
  are better resourced than a notebook will be.
- **Not an extension of `radish_bank_workshop.ipynb`.** That notebook asks for 12 pasted
  credentials and states "~2 hours". This one asks for one string and pays off in two
  minutes.
- **Not a keyspace profiler.** Redis Insight's Database Analysis already samples up to
  10k keys and reports memory by type, biggest keys, top namespaces and keys without TTL
  — free, GUI, zero build, works against Cloud. We link to it and do not rebuild it.

**What it claims as genuinely unowned content:** copy-pasteable `redis-cli` traces of what
the notebook just did, and the decomposition of a higher-level Redis/Iris feature into the
primitives underneath it. Verified: there is **zero raw Redis** in the `redis-iris-demos`
notebook surface — no `import redis`, no redisvl, no command-level content in
`notebooks/radish_bank_workshop.ipynb` or `notebooks/workshop_helpers.py`. redisvl exists
only as invisible plumbing in `backend/app/`. A prospect who has seen the Iris demo has
seen no Redis. This is the missing rung.

### 1.1 Act 1 subject: chosen and rejected

**Chosen: agent memory.** It is the only subject where the internals are a *by-product of
a feature the customer already wants* — the encoding flip, the 1024-byte field, the
TTL asymmetry and the index-is-not-a-key observation all fall out of building it, so
nobody has to be told "now let's look at `OBJECT ENCODING` for no reason." It also has no
LLM call to avoid, so there is no latency saving to brag about and the demo cannot
accidentally drift into a benchmark.

**Runner-up: "one key, seven instruments"** — a single hardcoded key written through a
dropdown of types and read by seven simultaneous introspection panels (RESP bytes, TYPE,
encoding, size, memory, TTL, event feed). Rejected for three reasons: its Act 1 is an
instrument rather than a demo, so Acts 1 and 2 become the same thing viewed twice and
Act 2 ends up breaking down the *notebook* rather than Redis; it needs `MONITOR` and a
keyspace-notification feed, which means two background threads and a database-wide
`CONFIG SET` on the customer's live database; and it has no bridge to Iris at all. Three
of its parts are grafted in below (the RESP wire row, the expected-vs-observed rendering,
the closing falsifiable predictions).

Also rejected: **the Reddash order-tracking page.** Best single "wait, that's all it is?"
beat anyone proposed (`TYPE` on a GEO key returns `zset`), but it needs ~1.25 days
synthesising a 300-order corpus for a demo whose subject is not the data — the reddash
pack has four drivers, eight hand-written orders, no coordinate field in any schema, and
`output/reddash/` has never been generated. Its geo panel survives as an optional
appendix (§5.5).

---

## 2. Architecture

```
  GitHub repo  (notebook.py, README.md, SAFETY.md)
        │                                  │
        │ uvx marimo@<pin> edit            │ molab.marimo.io/github/...
        ▼                                  ▼
   marimo (local)                    marimo (hosted, Day-0 gated)
        └──────────────┬───────────────────┘
                       │  GuardedTracedRedis (one execute_command override)
                       ▼
        Redis Cloud (rediss://)   OR   their own local Redis (redis://)
```

Two layers, not three. The repo is a delivery vehicle for one file and a badge URL, not a
data plane. There is no seed corpus: the seed is twelve string literals inline in the
notebook. There is no SQL layer.

The vector embedder (`model2vec`, `potion-base-8M`) is a first-run download from
huggingface.co, declared in the PEP 723 header. We do **not** vendor 31 MB of
`model.safetensors`: URL launch has no local repo tree and molab tree materialisation is
unknown, so on both quick-start paths the vendored file would be absent and the notebook
would silently fall through to the download anyway. Own the dependency out loud in the
README instead of describing the demo as "no downloads."

---

## 3. Act 1 — run it and see it

### 3.1 The cells, in order

1. **Header.** Branded `mo.md` with an inline `<style>` block (not `css_file` — see §7).
   One sentence: what you are about to build, and the explicit statement that there is no
   LLM anywhere in this notebook.
2. **Connect.** One `mo.ui.text(kind="password")` for `REDIS_URL`, defaulted from
   `os.environ.get("REDIS_URL")`, placeholder showing both forms:
   `rediss://default:…@redis-12345.c1.<region>.cloud.redislabs.com:12345` and
   `redis://localhost:6379`. One `mo.ui.run_button("Connect")`.
3. **Preflight and capability probe** (§4.1). Renders before any panel. Nothing in Acts 1–3
   runs until it is green.
4. **The bench.** `mo.ui.text_area` + two `mo.ui.run_button`s: **Remember this** and
   **Ask**. Five clickable starter lines as buttons above the box so nobody has to invent
   input.
5. **The recall card.** Deliberately structured, not prose:

   ```
   Recalled 3 of 12 · "allergic to shellfish" dist 0.19
                     · "prefer window seats"   dist 0.61
                     · "no dairy after 8pm"    dist 0.74
   Recent turns 5/5 (window pinned by LTRIM) · TTL sess: 861s · TTL ltm: -1
   ```

   Under it, a collapsed `mo.accordion` holding the exact commands that turn issued, in
   `redis-cli` syntax, copy-pasteable.
6. **The pinned trace** (§4.3), below everything, for the whole session.

### 3.2 Decisions

- **The bench, not a chatbot, is v1.** `mo.ui.chat(model=…, prompts=[…])` is verified to
  take a plain `Callable[[list[ChatMessage], ChatModelConfig], object]` and needs no LLM
  provider, so a chat surface is genuinely available. It is **not** in v1, because the
  chat `model` callable executes outside cell execution, so Redis writes inside it do not
  invalidate Act 2's reader cells — the only fix is `mo.state` driven from `on_message`,
  which marimo's own docs discourage. Two `run_button`s need no `mo.state` at all and
  produce a real dataflow edge. Chat is a stretch goal (§9, open decision 4), built on top
  of the bench, never instead of it.
- **Three beats, and they must land in the first two minutes.**
  1. *Paraphrase recall.* Starter prompts are chosen to have no lexical overlap with the
     recall query. The distance number sits next to each hit, so it reads as arithmetic.
  2. *Recency and semantics are different structures.* After five turns the fact is gone
     from "Recent turns" (`LLEN` pinned at 5 by `LTRIM`) and still recallable from the
     index. One line: "the window forgot it; the index did not."
  3. *TTL asymmetry.* `TTL sess:…` counts down; `TTL ltm:…` returns `-1`. That single
     asymmetry is the whole session-vs-long-term design.
- **Every write is idempotent.** `memory_id = sha1(text)[:12]`, so a double-fired button
  replays the same `HSET` as a no-op. No `INCR`, no ID-less `XADD`, anywhere.
- **The TTL strip does not tick by default.** One `mo.ui.refresh(options=["5s","10s"])`
  with **no** `default_interval` — the presenter opts in for the TTL beat and turns it off
  after. A timer that re-issues Redis commands on a 100-ops/sec database is load
  generation by accident.
- **Honest disclosure, one line, in the notebook.** `potion-base-8M` is a static embedder:
  it averages per-token vectors, so "I am allergic to shellfish" and "I am **not**
  allergic to shellfish" land almost on top of each other, and word order barely matters.
  The starter prompts deliberately do not hinge on negation. An `EMBED_BACKEND` switch
  (`"static"` / `"openai"`) exists so an NLP-literate room can watch the answer improve
  and hear why.

### 3.3 Command budget

Free Essentials is 100 ops/sec and 30 MB. One Act 1 session: 1 `FT.CREATE`, 12 `HSET`,
~20 `LPUSH`/`LTRIM`/`EXPIRE`, a handful of `FT.SEARCH`. Under 100 commands total, under
100 KB. This is comfortably inside the cap — which is one of the reasons this subject beat
the corpus-heavy alternatives. Act 2's optional widen-a-hash button is the only place that
issues hundreds of commands, and it is pipelined and gated.

---

## 4. Plumbing (build this before any panel)

### 4.1 Preflight and capability probe — one cell, renders first

Resolve → TCP connect → TLS handshake → `PING`, then:

| Probe | Why it is in the preflight |
|---|---|
| `FT._LIST` | **Fire this first.** Search and query on an Active-Active database must be enabled at creation and is only auto-enabled for A-A databases created with or upgraded to Redis 8+. If `FT.CREATE` is unavailable there is no Act 1 at all, not a degraded Act 1 |
| `INFO server` | `redis_version`, `redis_mode` |
| `HELLO` | protocol negotiated, client id, mode, role, **modules present** — eight useful fields, one round trip |
| `CLIENT INFO` | `resp=`, `lib-name`, `lib-ver`, `db`, `addr` for this connection |
| `ACL WHOAMI`, `ACL GETUSER` | the server's own account of what this credential may do |
| `OBJECT ENCODING` on a scratch key | sets the flag that degrades Panels 1–2 on Active-Active |
| `CONFIG GET hash-max-listpack-value` | read live; never hardcode |
| `redis.crc.key_slot(...)` | client-side CRC16, no command issued — the only distributed internals available on Cloud, and the one thing that still renders with no server |

The probe catches `ResponseError` per command and renders a `dict[str, bool]` support
matrix **on screen**. Silent `try/except` is not acceptable for an asset a second SE runs
cold: pre-explaining a missing `OBJECT ENCODING` beats watching it throw.

`ACL DRYRUN` is present but not a headline. `ACL SETUSER` is unsupported on Cloud, so we
can never create a restricted user to test against, and `DRYRUN` against a full-permission
`default` returns OK for everything and teaches nothing.

Fail loudly: catch `AuthenticationError` / `ConnectionError` / `TimeoutError` separately,
render through `mo.callout(kind="danger")` with the parsed host and port echoed back, and
print the specific fix for each. Set `socket_connect_timeout=5` and `socket_timeout=5` so
a blocked-egress or wrong-host case fails in five seconds in front of an audience instead
of hanging. A wrong `REDIS_URL` is the single most likely thing to break in the room.

### 4.2 One client class: guard, then trace

The guard and the tracer both want `execute_command`. Write **one** class, in this order
— reject, then time, then record.

```python
@app.class_definition
class GuardedTracedRedis(redis.Redis):
    def execute_command(self, *args, **kwargs):
        _check_allowed(args)                      # raises on denylist / off-prefix keys
        t0 = time.perf_counter()
        reply = err = None
        try:
            reply = super().execute_command(*args, **kwargs)
        except Exception as e:
            err = e
            raise
        finally:
            TRACE.append(TraceRow(args, reply, err,
                                  (time.perf_counter() - t0) * 1000))
        return reply
```

The original plan's snippet has two real bugs and the fix is itself teaching content: its
`finally` block can never bind the reply, so the promised reply column is permanently
empty, and a raising command is recorded with a plausible-looking duration and no error
marker. Bind the result inside `try`; log the exception in `except`.

`_check_allowed` needs a **keyless-command allowlist** (`PING`, `HELLO`, `INFO`,
`CONFIG GET`, `ACL *`, `COMMAND COUNT`, `DBSIZE`, `FT._LIST`, `FT.CREATE`, `FT.INFO`,
`FT.SEARCH`, `SCAN`) or `INFO` fails the prefix check. Resolve key positions from an
explicit map for the ~15 commands this notebook actually issues — more auditable than
`COMMAND GETKEYSANDFLAGS`, and it works regardless of that command's Cloud status. Hard
denylist: `FLUSHALL`, `FLUSHDB`, `CONFIG SET`, `SHUTDOWN`, `SWAPDB`, `RENAME`.

**Do not** replace the subclass with redis-py's observability hook.
`record_operation_duration` takes only `command_name, duration_seconds, server_address,
server_port, db_namespace, error, is_blocking, batch_size, retry_attempts` — no argv, no
reply. The trace table's entire value is the full command text and the reply, so the hook
structurally cannot serve it. `redis.observability` is also an OpenTelemetry module
(`OTelConfig` imports `opentelemetry.sdk.metrics`), so adopting it would add OTel deps and
build a duration-histogram pipeline, which is a latency-metrics surface by another name.
Delete the half-day the original trim budgeted for evaluating it.

### 4.3 The pinned trace, and the candour banner

One row per command, rendered as copy-pasteable `redis-cli` text, with a copy button.
Columns: command, args, reply, error, ms. The ms column is narrow, unlabelled as a
feature, never aggregated, never charted, never compared.

Two honest notes, both permanent:

- **Row one includes connection setup.** Our wrapper's timer starts before
  `pool.get_connection()`, so the first command carries TCP + TLS + AUTH + HELLO. Note it
  as "our wrapper includes connection setup on row one", not as a redis-py fact —
  redis-py's own `_execute_command` acquires the connection at client.py:722 *before*
  starting its timer at 724.
- **Pipelines and pubsub are not traced.** `Pipeline` overrides `execute_command`
  (client.py:1648) and `Redis.pipeline()` hardcodes
  `Pipeline(self.connection_pool, self.response_callbacks, transaction, shard_hint)`
  rather than `type(self)`. Also bypassed: `Pipeline.immediate_execute_command` (the
  `WATCH` path) and PubSub. Act 3 step 3's `remember_turn()` uses the one pipeline in the
  notebook, so the participant watches the trace go silent exactly there and is told
  exactly why. That lands better with a developer audience than a panel that only works.

**Expandable per-row detail: the RESP bytes.** `redis.connection.Connection()` on an
object that was never connected can serialize any argv:

```python
b"".join(Connection().pack_command("HSET", "demo:r1:ltm:alice:9f2a",
                                   "text", "allergic to shellfish"))
# b'*4\r\n$4\r\nHSET\r\n$22\r\ndemo:r1:ltm:alice:9f2a\r\n$4\r\ntext\r\n$21\r\nallergic to shellfish\r\n'
```

Verified by running it against the installed redis-py 7.4.0. Two build rules: `pack_command`
returns a **list** of chunks, so `b"".join()` it; and build argv and bytes in **one
function called from one place**, so the preview can never diverge from the write path.
A hand-typed RESP string in a panel whose entire value is byte-level precision is exactly
the failure this row exists to prevent — one of the reviewed proposals printed `$13` for a
9-byte key.

---

## 5. Act 2 — the component breakdown

Rendered as a **vertical stack**, not `mo.ui.tabs`. Tabs default `lazy=False`, and a
breakdown reads better as an ordered argument than as hidden drawers. Order follows the
write path, then the read path. Every panel has the same four-slot grammar:

1. one-sentence claim, in the app's language
2. a live table
3. the commands that produced it, as `redis-cli` text
4. one line of "when this is the wrong shape"

Every panel's reads are behind `mo.ui.run_button` + `mo.stop(not btn.value)`. Every
panel's reader cells take the Act 1 write **receipt** as a parameter (§7.3), so the
dependency edge is real rather than a discarded counter.

### 5.1 Panel 1 — working memory is a LIST plus two lifecycle commands

Table: `LRANGE sess:… 0 -1`, `LLEN`, `TTL`, `MEMORY USAGE`, `OBJECT ENCODING`.

Claim: the "recency window" is not a feature, it is `LTRIM`; the "session" is not a
feature, it is `EXPIRE`. Observation: `LPUSH` ten times, `LLEN` stays 5, and the dropped
turn is still recallable from the index.

Encoding is handled honestly: `CONFIG GET list-max-listpack-size` reads **-2**, i.e.
size-based at roughly 8 KB per node, **not an entry count** — so ten short turns will
never flip anything. One "paste a long message" button pushes a single ~9 KB turn and
flips `listpack`→`quicklist` for a size reason, with the -2 on screen explaining it.

Commands: `LPUSH` · `LTRIM` · `EXPIRE` · `LRANGE` · `LLEN` · `TTL` · `OBJECT ENCODING` ·
`MEMORY USAGE` · `CONFIG GET`.

### 5.2 Panel 2 — a memory is a HASH, and the vector is just bytes

Table: `HGETALL` on one memory key with the vector field rendered as
`<1024 bytes · FLOAT32 · 256 dims>` rather than mojibake, beside `HSTRLEN key text`,
`HSTRLEN key vector`, `MEMORY USAGE`, and `MEMORY USAGE key SAMPLES 0`.

Three structural payoffs:

- The vector field dominates the key's footprint and the text is rounding error.
  **Read the ratio live; never print a hardcoded percentage.** `1024/(1024+40)` is 96% of
  two *field* byte counts, but `MEMORY USAGE` on a hashtable-encoded hash also carries
  per-field sds and dictEntry overhead across all five fields, so the number the panel
  prints will land materially below that. Display `MEMORY USAGE − payload_bytes` as its
  own number and let it say whatever it says.
- `OBJECT ENCODING` is `hashtable` on the **very first write**, because a 1024-byte field
  exceeds `hash-max-listpack-value` — shown live via `CONFIG GET`, never assumed to be 64.
  No slider, no `CONFIG SET`: the flip is a consequence of the feature we just built.
- `SAMPLES 0` versus the default (5) gives two different answers for the same key, which
  is what makes the word "estimate" mean something. Both are supported on Standard **and**
  Active-Active.

Third line: Redis has no idea those 1024 bytes are 256 floats. `HGET` returns bytes;
`DIM`, `TYPE` and `DISTANCE_METRIC` live in the index schema, which is Panel 3.

Commands: `HSET` · `HGETALL` · `HSTRLEN` · `MEMORY USAGE [SAMPLES 0]` ·
`OBJECT ENCODING` · `CONFIG GET hash-max-listpack-value`.

### 5.3 Panel 3 — the index is not a key

The biggest panel, the one that teaches the most, and the one that gets rebuilt if it is
rushed. Budget 0.6 days for this panel alone. Four observations, each its own button, each
reproducible on demand, in any order, after arbitrary re-runs:

1. `SCAN MATCH demo:{run_id}:ltm:*` returns N hashes and no index. `FT._LIST` returns the
   index. `FT.INFO` `num_docs` == N. **Claim:** the index is a server-side structure that
   observes a key prefix. It has no key, no `TYPE`, no TTL, and does not appear in the
   keyspace.
2. The literal query string, displayed as text:
   `(@owner_id:{alice})=>[KNN 3 @vector $vec AS dist]` with `DIALECT 2` and
   `PARAMS 2 vec <1024 bytes>`. **Claim:** KNN is a query modifier, not a command.
   *Break it #1:* change the tag to an owner with no memories — zero hits, because the tag
   pre-filter narrows candidates before the vector scan. *Break it #2:* strip the
   `(@owner_id:{alice})` filter entirely and another user's memories come back. That is
   the strongest security-adjacent beat available in this notebook and it costs one line.
3. `DEL` one memory hash → `FT.INFO num_docs` goes N→N−1 **with no index command issued**.
   Indexes follow keys, not commands.
4. Write a memory with `EXPIRE 20`, watch `num_docs` drop when it expires. No reindex, no
   sweep command. Expiry propagates into the index.

Also shown: the full `FT.CREATE` as one copy-pasteable string, and `FT.INFO` parsed into a
small table (`num_docs`, `num_terms`, `num_records`, vector index size, hnsw/flat params,
`indexing`, `percent_indexed`, `hash_indexing_failures`). Parse defensively — `FT.INFO`
output is version-dependent and a Cloud version bump must not break the panel.

Uses **FLAT**, not HNSW. At ~12 vectors FLAT is exact and the story is truthful: this is an
exhaustive scan of twelve vectors, and HNSW is the one-word change you make at scale.
Beside it, the production schema from the counterpart repo, quotable verbatim —
`scripts/seed_memories.py:113-116` declares `text_vector`, `VECTOR`, `HNSW`, `6`, `DIM`,
`3072`, `DISTANCE_METRIC`, `COSINE`.

Absence beats get **expected-vs-observed** rendering: print the expected list *before* the
press, so an empty row is an assertion the participant made rather than a bug they
suspect.

Commands: `FT.CREATE` · `FT.INFO` · `FT._LIST` · `FT.SEARCH` · `SCAN` · `DEL` · `EXPIRE`.

### 5.4 Panel 4 — namespace, lifecycle, and what the server says about itself

`SCAN MATCH demo:{run_id}:*` (never `KEYS`, and say why on screen) → `mo.ui.table` of
key · `TYPE` · `TTL` · `MEMORY USAGE` · `OBJECT ENCODING` · element count ·
`key_slot(key.encode())`. Sorted by TTL. Use redis-py's `scan_iter(match=…, count=100,
_type=…)`; `key_slot` takes **bytes** (it calls `key.find(b"{")`, so a `str` raises
`TypeError`), and `{tag}` co-location is a two-line demo:
`key_slot(b"{demo:r1}:a") == key_slot(b"{demo:r1}:b") == 4466`.

Claim: two memory tiers, two lifecycles, one prefix. Session keys have a TTL; LTM keys
return `-1`. The one-line cleanup is visible, so nothing is abandoned in their database.

Beside it, a one-line link to Redis Insight's Database Analysis: this is a five-row
illustration of what four introspection commands return, not a keyspace profiler, and we
are not rebuilding a shipped product.

Deliberately **not** in this panel: biggest-keys, top-namespaces, keys-without-TTL,
memory-by-type. Insight does all four.

### 5.5 Optional appendix panel — "the geo index is just a sorted set" (0.25d)

Off-subject for agent memory, so it is an appendix, not a panel. Include it only if Nick
wants one more "wait, that's all it is?" beat, or if the target database is Active-Active
and Panels 1–2 have lost their encoding punchline — because this beat survives A-A intact.

`GEOADD` three points, then read them with the same instruments: `TYPE` → `zset`;
`ZSCORE` → the 52-bit interleaved geohash integer; `GEOHASH` → the 11-char string for the
same point; `ZRANGE WITHSCORES` → spatially clustered order; `ZREM` → gone from
`GEOSEARCH`; `ZADD` a bogus score → `GEOPOS` returns a nonsense coordinate, because there
is no validation layer. Doc-backed verbatim from `redis.io/docs/latest/commands/geoadd`:
"Data is stored into the key as a sorted set"; "there is no GEODEL command because you can
use ZREM to remove elements. The Geo index structure is just a sorted set."; "Latitude and
Longitude bits are interleaved to form a unique 52-bit integer." `GEOADD` is supported on
Cloud Standard **and** Active-Active.

### 5.6 Corrected encoding facts — the original §5.3 was wrong on three of six

These corrections are load-bearing. Do not reproduce the original chains anywhere.

| Type | Original plan said | Actual behaviour |
|---|---|---|
| **Hash** | `listpack` → `hashtable` | **Correct.** Flips on entry count (`hash-max-listpack-entries`) **or** on any single value exceeding `hash-max-listpack-value`. Our 1024-byte vector field flips it on write #1 |
| **Sorted set** | `listpack` → `skiplist` | **Correct.** Same either-or: `zset-max-listpack-entries` or `zset-max-listpack-value` |
| **String** | `int` → `embstr` → `raw` | **Correct.** Representation, not size: `SET k "hi"` is `embstr`, then `APPEND k "!"` is `raw` at three bytes, because APPEND needs a mutable sds |
| **Set** | `intset` → `listpack` → `hashtable` | **Wrong as a growth path.** `maybeConvertIntset()` in `t_set.c` sends an over-threshold intset **straight to hashtable**, and with defaults `set-max-intset-entries` 512 / `set-max-listpack-entries` 128 the 513th integer is already past 128. `intset`→`listpack` is unreachable by growth — it only happens when a **non-integer member** is added to a small integer set. Encoding is chosen by what the data *is*, not only how much of it there is |
| **List** | `listpack` → `quicklist` by entry count | **No entry-count knob exists.** `list-max-listpack-size` defaults to **-2**, i.e. size-based at ~8 KB per node. Flip it with one ~9 KB element, never with a hundred small ones |
| **Stream** | an encoding transition | **There is none.** `OBJECT ENCODING` returns `stream`, full stop — documented as "a radix tree of listpacks." Use `XINFO STREAM` `radix-tree-keys` / `radix-tree-nodes` against `stream-node-max-entries` **and** `stream-node-max-bytes` (both readable on Cloud) instead |

Two hard rules that follow:

- **The notebook never issues `CONFIG SET`.** Not once, not behind a checkbox. A `CONFIG
  SET` on a supported threshold is **database-wide** and mutates the customer's live
  configuration; a reactive slider bound to it would fire on every drag event. Read
  thresholds with `CONFIG GET` and cross the real value by adding data.
- **Read every threshold live.** Do not hardcode 64, 128 or 512 — the `CONFIG GET` doc's
  own example prints `hash-max-listpack-entries 512`, and a managed database may differ.
- Per the Redis Open Source configuration-settings compatibility table, these are
  supported on Cloud Standard **and** Active-Active: `hash-max-listpack-entries`,
  `hash-max-listpack-value`, `zset-max-listpack-entries`, `zset-max-listpack-value`,
  `list-max-listpack-size`, `set-max-intset-entries`, `stream-node-max-entries`,
  `stream-node-max-bytes`, `notify-keyspace-events`. `set-max-listpack-entries` is absent
  from the table entirely, and `maxmemory` / `maxmemory-policy` / `lfu-*` /
  `maxmemory-samples` are absent from the CONFIG subset — so there is no eviction-policy
  content available, which is fine, because it was out of scope anyway. **Calling
  `CONFIG GET` on an unsupported setting returns an error**, not an empty reply: wrap in
  `try/except` and render the error, which is a better teaching moment.

### 5.7 Active-Active degradation, decided in advance

`OBJECT ENCODING` is not supported on Active-Active. The documented substitute,
`CRDT.DEBUG ENCODING`, sits in a `DEBUG` family that is otherwise blocked, so whether it
even answers is unknown — **do not** make it the A-A path. On Active-Active, Panels 1–2
drop the encoding row and fall back to `MEMORY USAGE` + `HSTRLEN` + element count (all
verified on both database types), with a visible, labelled, explained banner rather than a
silent swap. That is materially weaker content, which is why §8 item 2 asks the customer
which database type they have *before* the meeting, not during it.

The bigger A-A risk is not cosmetic: if Search and query was not enabled at database
creation, `FT.CREATE` fails and there is no Act 1. Hence `FT._LIST` is probe #1.

---

## 6. Act 3 — the step-by-step code walkthrough

Six numbered steps, one marimo cell each, presenting the **exact functions Act 1 ran** —
not a retyped copy. Under each block: two sentences of why, and a `mo.ui.run_button` +
`mo.stop()` gate that executes that one step against their database so a new line appears
in the pinned trace. Reading it and running it are the same gesture.

1. **Connect and the key schema.** `Redis.from_url(..., decode_responses=False)` — and why
   `False`: the vector field is bytes, so global decoding breaks it, and text fields are
   decoded at the call site instead. State it as a real decision with a real cost. The key
   schema (`demo:{run_id}:sess:{owner}:{sid}`, `demo:{run_id}:ltm:{owner}:{sha1}`) is
   presented as a design decision with consequences: one `SCAN` prefix, one cleanup, owner
   isolation enforceable by both key pattern and index tag. Note that
   `Redis.__init__` sets `auto_close_connection_pool=True` when it builds its own pool and
   `__del__` closes it, so a re-run of this cell does not leak against the 30-connection
   cap — and that clients are therefore built in exactly one cell with an explicit
   `max_connections`.
2. **`embed(text) -> bytes`.** Eight lines: load the `StaticModel` once, `encode`,
   `.astype(np.float32).tobytes()`. This is where Panel 2's 1024 bytes come from.
3. **`remember_turn()`** — `LPUSH` + `LTRIM` + `EXPIRE` in one `pipeline()`. The notebook's
   only pipeline, and therefore invisible to the tracer — closing the loop with §4.3's
   banner while the trace visibly goes quiet.
4. **`remember_fact()`** — deterministic `memory_id`, one `HSET` with the vector, then an
   explicit `EXPIRE` (HSET cannot set a TTL inline). Idempotency is the double-fire
   defence.
5. **`ensure_index()`** — `FT.INFO` in a `try`, `FT.CREATE` in the `except`. This is lifted
   in shape from `scripts/seed_memories.py:_ensure_memory_index` in `redis-iris-demos`, and
   the notebook says so: the production asset has the same idempotent-create problem and
   solves it the same way.
6. **`recall()`** — embed the query, build the filter+KNN string, `FT.SEARCH` with
   `DIALECT 2` and `PARAMS`, merge with `LRANGE`. The two result sets are merged **in
   Python, not by Redis**, and the note is explicit that this is the author's job.

**Closing cell — the abstraction ladder, three rungs, same recall.** Both non-obvious rungs
are verbatim-quotable from the counterpart repo, verified:

- **rung 1** — ours: `FT.SEARCH "(@owner_id:{alice})=>[KNN 3 @vector $vec AS dist]"
  PARAMS 2 vec <1024 bytes> DIALECT 2`
- **rung 2** — redisvl's equivalent, shown as **code only**. redisvl is deliberately not a
  notebook dependency, so nothing is hidden and no extra wheels land. Beside it, the
  production `HNSW / DIM 3072 / COSINE` schema from `scripts/seed_memories.py:113-116`.
- **rung 3** — `MemoryService.search_long_term_memory`
  (`backend/app/memory_service.py:147-170`, `_url` at `:115`): one `httpx.post` to
  `{api_base_url}/v1/stores/{store_id}/long-term-memory/search`.

That names honestly what the hosted product buys: not the primitives, the *operating* of
them. It is the bridge stated as code rather than as a slide, and it is an argument the
customer can repeat to their own architect.

**Second closing cell — four falsifiable predictions**, answer keys in a collapsed
accordion. Better handoff than a summary, and free:

1. Make `FT.INFO num_docs` drop without issuing any index command.
2. Make a recall return zero hits without changing the query text.
3. Make a memory hash's `MEMORY USAGE` drop while its field count goes up.
4. Find the command in the trace that the tracer did **not** record, and say why.

---

## 7. marimo engineering decisions

These cost a day each if discovered late. Solve them before building any panel.

### 7.1 Structure: `with app.setup:` plus top-level definitions

marimo 0.24 serializes a cell containing exactly one function or class as a **real
module-level `def`/`class`** in the `.py` file. Use it:

```python
with app.setup(hide_code=True):
    import marimo as mo
    import inspect, textwrap, time, hashlib
    import redis

@app.function
def embed(text: str) -> bytes: ...

@app.class_definition
class GuardedTracedRedis(redis.Redis): ...
```

This is load-bearing, not cosmetic. With the ordinary wrapped-cell form,
`inspect.getsource` on a cell-defined **class** raises `OSError: could not find class
definition`, because the class's `__qualname__` says top-level while the file has it
indented inside a cell function. Reproduced in kernel mode on 0.24.2. With
`@app.class_definition` it works. Second payoff: the `.py` reads as normal Python on
GitHub, which matters because the quick-start path is a raw GitHub URL.

Constraints marimo enforces on these cells: exactly one definition; no references to
variables defined by other cells (move them to the setup cell); no names starting with
`_`; no non-indented trailing comments. That constraint set is a feature here — it forces
every piece of Act 1 machinery into a named, self-contained, `getsource`-able unit, which
is exactly the Act 3 inventory.

Two gotchas:

- **Decorator asymmetry.** `inspect.getsource` on an `@app.function` returns source
  starting at `def` (decorator absent, because inspect uses `co_firstlineno`), but on an
  `@app.class_definition` it returns the literal `@app.class_definition` line first
  (because the AST node carries `decorator_list`). Strip it:
  `[l for l in src.splitlines() if not l.lstrip().startswith("@app.")]`. Keep
  `textwrap.dedent` — a no-op in kernel mode, required in script mode, costs nothing.
- **Never trust line numbers.** In kernel mode `getsource` resolves through
  `linecache.cache` pseudo-files, not the real file, so `getsourcelines` offsets are
  meaningless.

**File layout = narrative order = topological order.** A prelude region at the top
(`with app.setup:` plus all `@app.function` / `@app.class_definition` cells,
`hide_code=True` so they collapse in edit mode and on molab), then Act 1, Act 2, Act 3.
marimo resolves by dataflow rather than file position, so an Act-1-first / definitions-last
layout *works* — reject it anyway: `marimo export ipynb` defaults to `--sort topological`
and would hoist the definitions above Act 1, and anyone reading the `.py` on GitHub would
see a file whose order contradicts its own act headings. Act 3 reaches back up to the
prelude via `getsource`, which does not care about position.

### 7.2 Code disclosure: two mechanics, two jobs

- **`mo.show_code(output, position="above")`** for panel-side code — the code that actually
  produced the panel beside it, with no second copy to keep in sync. Its docstring
  guarantees "all occurrences of `mo.show_code(...)` will be replaced with ...", so the
  wrapper never appears in what the audience reads. **Footgun:** the substitution is
  `re.sub(r"mo\.show_code\((.*)\)", r"\1", code, flags=re.DOTALL)` — greedy to the last
  `)` in the cell. Hard rule: `mo.show_code(...)` must be the cell's **last expression**
  and the cell must end at its closing paren. Two calls in one cell, or any code after it,
  renders mangled source to the customer.
- **`inspect.getsource`** for Act 3's six steps, because a step-by-step walkthrough needs
  to slice — show `execute_command`'s try/except in isolation, annotate it, show the next
  chunk. One helper: `walk(obj, first=None, last=None)` that dedents, strips `@app.` lines,
  slices between marker substrings, and returns
  `mo.ui.code_editor(value=chunk, language="python", disabled=True)`.
  `show_copy_button` already defaults `True`, which is what makes the walkthrough
  copy-pasteable. Use `language="shell"` from the same helper for `redis-cli` traces —
  `language` is an unvalidated `str`, so an unrecognised value silently loses highlighting
  rather than erroring.

**Both mechanics render code as OUTPUT, which is why one file serves both delivery modes.**
Code visibility in `marimo run` is a **global** server flag with no per-cell granularity
(`session_manager.py:449`: `return self.mode == SessionMode.EDIT or self.include_code`),
and `CellConfig.hide_code` is editor-only. So: `marimo edit --sandbox` is the supported
authoring and handoff path (and is what molab gives you regardless);
`marimo run --sandbox` **without** `--include-code` is the optional room path, where Acts
1–2 source is blanked before it reaches the browser while Act 3 renders normally because
its disclosure is output. No mode switching mid-demo, no divergence between paths, no
restarting the server and losing the connection.

### 7.3 Reactivity: delete the epoch button, use a write receipt

The original §6.1 global epoch counter was right for six independent tabs and is now worse
than the alternative. Act 1 writes *and* renders its own result, so it needs no epoch;
Act 3 touches Redis only behind its own gates; only Act 2's observational readers need
invalidation after a write.

Have the Act 1 write cell **return a receipt** — `write_receipt = {"keys": n, "at":
time.time(), "prefix": PREFIX}` — and have each Act 2 reader take `write_receipt` as a
parameter. The dependency edge is then genuine, self-documenting, and carries useful data
instead of being a discarded `epoch.value` no-op that a future editor deletes as dead code.

Add exactly one `mo.ui.refresh(label="Re-read Redis")`, no `default_interval`, for the case
where the participant mutated the database from `redis-cli` in another window.

Gate every write with `mo.ui.run_button` + `mo.stop(not btn.value)` — `run_button` is the
only button whose value resets to `False` after dependents run, which is exactly the
one-shot semantics required. A counter button stays truthy forever and would re-fire the
write on every unrelated re-run. (Note the original plan's §6.1 counter example was the
right primitive for an epoch signal and the wrong one for a write gate; both are now moot.)

### 7.4 MultipleDefinitionError: one underscore solves it

`variables.py:62-63`: `is_local(name)` returns true for `name == "__"` or
(`startswith("_")` and not `startswith("__")`). Single-underscore names are invisible to
the dataflow graph and can be reused in every cell. `__name` is **not** local.

The discipline:

- Single-underscore **every** intermediate: `_rows`, `_enc`, `_src`, `_k`. This absorbs
  nearly all collision pressure, and Act 2's four panels repeat the same shapes constantly.
- Only names that genuinely cross act boundaries get bare names, and there are about a
  dozen: the setup imports, the `@app.function` / `@app.class_definition` names,
  `REDIS_URL`, `PREFIX`, `RUN_ID`, the client, `TRACE`, `write_receipt`, and the UI
  elements. Keep that list as a comment at the top of the prelude and grep it in a
  pre-commit check.
- Prefix UI elements by act: `a1_remember`, `a1_ask`, `a2p3_del`, `a3s4_run`. Doubles as
  the collision namespace and as documentation of which act owns the control.
- Two hard constraints: `@app.function` / `@app.class_definition` cells **cannot** define
  underscore names, and an underscore name cannot be passed between cells — so never
  underscore anything Act 3 needs to `getsource`.

### 7.5 Containers, layout, threads

- **`mo.accordion(items, multiple=True, lazy=True)`** for Act 3's step bodies. `lazy=True`
  is load-bearing: both `accordion` and `mo.ui.tabs` default `lazy=False`, so without it
  opening Act 3 fires every Redis command in the notebook — a visible stall on a
  100-ops/sec database in front of the customer. **Correction that matters:** `lazy=True`
  only defers *rendering* of an object that was already constructed when the cell built
  the dict. The values must be **zero-arg callables** —
  `mo.accordion({"Step 4": lambda: _step4()}, multiple=True, lazy=True)` — or the commands
  have already run. Keep lazy bodies output-only; building `mo.ui.*` inside a lazy callable
  is the risky case, since the UI-element registry holds elements weakly and the container
  must retain strong refs.
- **No `mo.Thread` anywhere in v1.** No `MONITOR` feed, no keyspace-notification feed.
  Cleanup on cell invalidation is the author's responsibility via `should_exit`, a blocking
  read does not check it until a message arrives, and a leaked connection costs one of
  thirty on every unclean re-run. Both feeds are cut for reasons beyond fragility — see the
  ledger.
- **No `css_file`, no `layout_file`.** Both are documented as "relative to the app file",
  and `marimo edit <URL>` downloads the notebook to a temporary file, so neither path
  resolves on the URL or molab paths. Do the brand treatment inline as a `mo.md` cell
  emitting a `<style>` block; use `width="medium"` plus `mo.vstack` / `mo.hstack` instead
  of a grid layout. Set `app_title` — it is an in-file scalar and it labels the browser
  tab in front of the customer. Avoid `width="columns"`: multi-column fights a linear
  three-act read.
- `mo.outline()` for in-page act navigation; `mo.callout(kind="danger")` for the
  connect-failure state.
- **Setup-cell fragility:** every top-level definition hard-depends on the setup cell, so
  one failed import kills all three acts with no callout path. Keep setup to marimo,
  stdlib and redis. Import `model2vec` / `numpy` in an ordinary cell, so an embedder
  failure still leaves Acts 2 and 3 alive against a pre-seeded keyspace.

---

## 8. Safety — what replaces `ACL SETUSER`

The original §9 said "ship a scoped ACL user, not `default`: read/write limited to
`demo:*`. Include the `ACL SETUSER` line in the README." That command **does not work on
Redis Cloud** — `ACL SETUSER` / `DELUSER` / `GENPASS` / `LOAD` / `LOG` / `SAVE` are
unsupported and the docs redirect to console RBAC, a paid capability. The README points at
console RBAC and says it is paid; it does not instruct.

So enforcement moves client-side and honesty moves front and centre. Six layers, in
`SAFETY.md` and summarised in the notebook:

1. **Namespace.** `demo:{run_id}:` where `run_id` is generated per session and displayed,
   not editable. Not a fixed `demo:*`, which can collide with keys they already own. The
   index name is `idx:demo:{run_id}:ltm`, so `FT.CREATE` cannot collide either.
2. **Guard.** `GuardedTracedRedis` (§4.2): key-position map for the ~15 commands used,
   keyless-command allowlist, hard denylist. The guard does **not** cover pipelines, for
   the same reason the tracer does not — so `remember_turn()`'s key construction goes
   through the one prefixing helper, and the guard stays defence in depth.
3. **Preflight consent.** Before the first write: show `DBSIZE`, require
   `SCAN MATCH demo:{run_id}:*` to return zero and `FT._LIST` to show no colliding index,
   then gate on a `run_button` reading "this writes ~15 keys and creates one index on the
   database at `<host>`; you currently have N keys."
4. **TTL.** Every key expires. `SET ... EX` inline where possible; an explicit `EXPIRE`
   immediately after `HSET` / `LPUSH`, which cannot set one inline. Be honest about what
   this buys: a TTL stops the demo filling their database and does **nothing** about a
   name collision — `HSET demo:r1:ltm:… ` on a key they already own would overwrite their
   value and then delete it. TTL is the second layer, not the first.
5. **Teardown.** A scoped `SCAN MATCH demo:{run_id}:* COUNT 100` + `UNLINK`, plus
   `FT.DROPINDEX` for this run's index and any orphaned `idx:demo:*` from earlier runs
   (found with `FT._LIST`, which is Panel 3's content anyway). Show the `redis-cli`
   equivalent. **Never `FLUSHDB`.**
6. **Transparency over enforcement.** Print `ACL WHOAMI` and `ACL GETUSER`. If they are
   connecting as `default` on Essentials — the likely case — say it plainly in the
   notebook: *this credential can erase your database; the only thing stopping that is our
   client-side guard.* That sentence is more credible to a developer audience than a fake
   ACL line, and it is the truth.

Four hard don'ts:

- **Never name a demo prefix `memory:`.** `scripts/flush_redis.py` in `redis-iris-demos`
  is a SCAN-and-delete whose allowlist is the single `memory:` prefix — so `make setup` /
  `make reset` would delete the demo's keys. Do not run those targets, do not run that
  script. (The one-line fix to that script is worth a separate PR.)
- **The credential is on screen.** Use `mo.ui.text(kind="password")` and never screenshot
  that cell. A `rediss://default:<password>@…` string is otherwise visible and lives in the
  notebook's UI state.
- **Pasting a live credential into molab is a security-review question**, not just an
  egress question — a third-party hosted runtime holds it. This is part of Day 0 item 1,
  not an afterthought.
- **No `CONFIG SET`, ever.** Written down as a rule, checked at each dry run.

---

## 9. Delivery — two paths, one file

### 9.1 Local (primary)

```bash
uvx marimo@0.24.2 edit --sandbox notebook.py            # clone-or-download, then this
uvx marimo@0.24.2 edit --sandbox --trusted \
    https://raw.githubusercontent.com/<org>/<repo>/v1.0.0/notebook.py   # 30-second taste
```

- **Pin the marimo version in the launch command**, not only in the inline deps. marimo is
  pre-1.0 and shipped ~7 releases in 10 weeks (0.24.2 on 2026-09-11). `mo.tabs` is
  *already* deprecated in the current release in favour of `mo.ui.tabs`, so API drift in
  this window is demonstrated, not hypothetical.
- **Pass `--sandbox` explicitly.** `uvx marimo edit` does honour PEP 723 inline deps —
  `_cli/sandbox.py` prompts "Run in a sandboxed venv containing this notebook's
  dependencies?" default `True` — but the prompt is **skipped when stdin is not a TTY**, so
  a double-clicked shortcut or a VS Code task silently gets a dependency-less env and an
  `ImportError` on `import redis`.
- **`--trusted` is mandatory on the URL form.** The remote-URL Docker prompt defaults to
  yes and hard-errors with "Docker is not installed" if the user just presses Enter.
- **The URL form is for getting started, not for keeping.** `marimo edit <URL>` downloads
  to a temporary file, so Act 3 exploration is not saved. The leave-behind instruction is
  clone-or-download.
- **Set first-run expectations in the README with real numbers:** a prompt plus a two-pass
  uv install of roughly 216 MB, plus a ~30 MB `potion-base-8M` download on first embed.
  Pre-warm the uv cache and the HF cache on the demo machine.
- `--host` defaults to `127.0.0.1` on every command, so there is no firewall prompt.
  `--headless` is useful for a scripted dry run. Leave token auth on.

### 9.2 molab (sanctioned backup — gated on Day 0)

`molab.marimo.io/github/<org>/<repo>/blob/v1.0.0/notebook.py` — the structural twin of a
Colab badge, which the counterpart repo already sets the precedent for. Badge goes in the
README next to the uvx line.

What molab gives us:

- **Credentials, cleanly.** molab has a Secrets panel that writes a persisted `.env`;
  marimo loads it automatically, and the docs state the `.env` "is not forked so you can
  share a notebook and its data without sharing your credentials." That is the right
  semantics for handing a customer a URL, and it means the connect cell reads `REDIS_URL`
  from the environment on both paths with no molab branch. *Caveat:* marimo issue #5972 is
  titled "Secrets in Molab are shared together with `.env`", which reads like a
  contradiction — confirm the no-leak guarantee before telling a customer their credential
  is private.
- **GitHub sync.** Synced notebooks use GitHub as source of truth: develop locally, push,
  see it reflected. Correct authoring loop.

What molab does not give us:

- **No usable filesystem.** Only files uploaded through the sidebar or cached with
  `mo.persistent_cache` persist. Irrelevant here — there is no corpus — but it is a second
  independent reason not to vendor model weights. If the HF download proves slow, wrap the
  model load in `mo.persistent_cache`, the only persistence molab documents.
- **PEP 723 handling is unverified.** molab's model is a mutable `pyproject.toml` plus
  auto-install-on-import via uv, not an immutable script header. What we lose is **version
  pinning**, and our redis-py findings are version-specific. Two cheap defences: keep the
  header pinned for the local path regardless, and have Panel 4 print `redis.__version__`
  and `marimo.__version__` as live values — a version skew then becomes visible
  in-narrative data instead of a silent divergence.
- **Sessions are time-boxed:** 12 h max, 90 min idle. Forks land in the user's own molab
  account, so the molab path produces a fork, not a file. Say that plainly rather than
  implying it is the same leave-behind.

### 9.3 The blocking unknown

**Whether a molab container can open a TCP connection to a Redis Cloud endpoint is
UNKNOWN**, and it is worse than "port 6379": Redis Cloud endpoints are
`redis-<port>.c<n>.<region>.cloud.redislabs.com` with a **per-database random high port**,
so the real question is arbitrary outbound high-port TCP from a hosted container.

The evidence cuts both ways. molab runs on Modal sandboxes (stated in marimo's own launch
post). Modal's sandbox networking gives three controls: `block_network`, a **domain**
allowlist that governs TLS on **port 443 only**, and a **CIDR** allowlist under which
"non-TLS traffic (HTTP, raw TCP, UDP) to IPs that are not on a CIDR allowlist is blocked",
defaulting to all-allowed. A `rediss://host:12345` connection is classified as raw TCP by
that rule, not TLS, so if marimo set any CIDR allowlist the connection fails and no
notebook code fixes it. In our favour: molab markets Postgres/MySQL connectivity, which is
non-HTTP TCP on 5432/3306 and would be blocked by the same rule. **Probably works, not
safe to assume.** Day 0 item 1.

### 9.4 Local Redis instead of Cloud

Fully supported, one field, no branch. `Redis.from_url` dispatches TLS on the scheme, so
`redis://localhost:6379` needs no extra flag, no checkbox, and no toggle UI — a toggle
would imply the distinction matters to the lesson, and it does not. The connect field
defaults to `os.environ.get("REDIS_URL") or "redis://localhost:6379/0"`, so a participant
who already runs Redis changes **nothing**.

Redis Cloud is a convenience for getting started quickly, not a subject of the demo. The
README says exactly that, once, and states the free Essentials constraints honestly: 30 MB,
30 connections, 100 ops/sec, 5 GB/month, no replica, and **new Essentials subscriptions
allow only ONE database** — so a developer who already has one cannot provision a clean
one. That last fact is why §8 exists.

There is no local Redis *install* instruction anywhere in this repo. One line: "Already
have Redis locally? Use `redis://localhost:6379` instead."

### 9.5 Repo and the ipynb hedge

```
redis-agent-memory-demo/
├── notebook.py          # PEP 723 inline deps, every version pinned
├── .env.example         # REDIS_URL only
├── README.md
├── SAFETY.md
├── Makefile             # one target: export-ipynb
└── pyproject.toml
```

README order: (1) the pinned uvx one-liner; (2) the molab badge with its one-sentence
caveat once Day 0 answers it; (3) "Connect to Redis Cloud" — five minutes, free Essentials,
framed as a convenience, with the constraints above; (4) "Already have a local Redis?";
(5) link to SAFETY.md; (6) links to Redis Insight for keyspace analysis and to
`university.redis.io`'s free "Get started with Redis" path for the data-types curriculum
this demo deliberately does not duplicate; (7) one line recording the
`notify-keyspace-events` finding so nobody rediscovers it — the config string must be
`KEA` plus `noc`, because `A` is an alias for `g$lshztdxea` and `AKE` therefore excludes
`m`, `n`, `o` and `c`, so `new`, `overwritten` and `type_changed` are invisible under plain
`KEA`.

**Ship `marimo export ipynb` as a read-only artifact**, generated by a make target, never
hand-edited, with a header cell saying so. Three conditions: use `--sort top-down` (the
default is `topological` and would scramble the acts — with the prelude-at-top layout,
top-down is also topologically valid, so the export actually runs); accept that it is lossy
(every `mo.ui` control degrades, so Act 1 does not survive) and market it as "the code
walkthrough, readable without installing anything", which is the half an ipynb is good at;
and note that **`nbformat` is not a marimo dependency** — the export fails on a clean
install with "nbformat is required", so the make target needs `uv run --with nbformat`.

This matters because the counterpart repo's top-level README has no Notebooks section at
all (grep for notebook/colab/workshop returns nothing), so an ipynb is the only format that
renders inline on GitHub and in every corporate viewer.

**No WASM.** `export html-wasm` genuinely cannot work: in-browser Pyodide has no raw TCP
sockets, so redis-py cannot reach Redis at all. That is the real reason, and it is the same
egress question as molab in a form that is definitively answered.

---

## 10. Day 0 prerequisites

Half a day. Nothing is built. Items 1–3 are questions for Nick or the customer; 4–7 are
things you run. **If item 1 fails, escalate before Day 1** — it changes the delivery
promise, and discovering it on Day 7 is the expensive version.

1. **BLOCKING — molab reachability and molab credential policy.** Push a five-line
   notebook to a public GitHub repo, open it at the molab URL form, paste `REDIS_URL` into
   the Secrets panel, and run
   `socket.create_connection((host, port), 5)` against **the real per-database high port**
   from the Cloud endpoint, then `Redis.from_url(...).ping()`. If it fails, disambiguate in
   the same session: a plain socket to a known-open non-443 port (e.g.
   `socket.create_connection(("portquiz.net", 8080), 5)`) succeeding proves general non-443
   egress and points at Redis Cloud's config instead. Record the exact exception type — a
   connect **timeout** means a silent drop (allowlist); **refused** means something else.
   In the same session: does molab honour PEP 723, does `model2vec` auto-install, does the
   `potion-base-8M` download work. And separately, from Nick: is pasting a Cloud credential
   into a third-party hosted runtime acceptable for this customer at all.
2. **Standard or Active-Active?** Decides whether Act 2's encoding rows ship at all. If
   Active-Active: probe `FT._LIST` **first** — Search on A-A must be enabled at creation
   and is only auto-enabled for A-A databases on Redis 8+, and without it there is no Act 1.
   Do not bank on `CRDT.DEBUG ENCODING`; test whether it even answers, and assume it does
   not.
3. **Does the customer's Essentials subscription already contain its one permitted
   database, and does it hold keys they care about?** If yes, §8's guard is mandatory rather
   than prudent, and the run-id-scoped index name is load-bearing.
4. **Run a command sweep against the actual target database and paste the real replies into
   this document.** `FT._LIST` / `FT.CREATE` / `FT.INFO` / `FT.SEARCH`; `OBJECT ENCODING`,
   `OBJECT REFCOUNT`, `OBJECT IDLETIME`; `MEMORY USAGE` and `MEMORY USAGE … SAMPLES 0`;
   `HSTRLEN`; `CONFIG GET` on `hash-max-listpack-entries`, `hash-max-listpack-value`,
   `list-max-listpack-size`, `zset-max-listpack-entries`, `set-max-intset-entries`,
   `set-max-listpack-entries` (expected to **error**, which is content), `stream-node-max-*`;
   `HELLO`; `CLIENT INFO`; `COMMAND COUNT`; `ACL WHOAMI` / `GETUSER` / `DRYRUN`.
   Note up front that redis-py 7.4.0 deliberately raises `NotImplementedError` from
   `command_info()`, `command_docs()` and `latency_histogram()` — any COMMAND-introspection
   panel must go through `execute_command` directly.
5. **`model2vec` / `potion-base-8M` under `uvx marimo@<pin> edit --sandbox` on Windows with
   a cold uv cache.** Record the real download size and first-embed time for the README.
6. **Pin the marimo version** and confirm the cold-cache `uvx` launch end to end, including
   the `--sandbox` flag on a non-TTY invocation.
7. **Write down that the notebook will never issue `CONFIG SET`**, and check it at each dry
   run.

**De-risk for free:** nothing in Acts 1–3 is blocked on `redis://localhost:6379`. Rehearse
and author against local Redis, so every Day-0 unknown stops blocking *building* — only
the customer-facing run is conditional. That takes Days 1–6 off the critical path of
item 1's answer.

**Pre-meeting email, three items, no credentials by email:** which Redis Cloud database
this will point at (or a local `redis://host:port` if they prefer); is it Standard or
Active-Active; does that database contain keys they care about.

---

## 11. Schedule — 6.5 days of work over 8, plus 2 genuinely empty

| Day | Work | Ships |
|---|---|---|
| **0** (0.5d) | §10 in full. Escalate if item 1 or 2 fails | a filled-in capability table |
| **1** (1.0d) | Prelude and `with app.setup:` spine, connect cell, preflight + capability probe, `GuardedTracedRedis` with guard-then-trace, key schema, idempotent `ensure_index`, teardown, naming conventions written into the file header | nothing visual |
| **2** (1.0d) | `redis-cli` renderer + RESP wire row from the one argv/bytes function; embed layer with `EMBED_BACKEND` switch; Act 1 bench and recall card started | the trace |
| **3** (0.75d) | Act 1 finished: recall card copy, TTL strip, starter prompts, write receipt | **Act 1 demoable** |
| **4** (1.0d) | Act 2 Panels 1, 2 and 4. Panel 1's template is the expensive one; 2 and 4 share it | three panels |
| **5** (1.0d) | **Act 2 Panel 3** — the index panel, 0.6d on its own, plus the two break-it controls and defensive `FT.INFO` parsing | the best content in the demo |
| **6** (0.75d) | Act 3: `show_code` / `getsource` plumbing, six step gates, the three-rung ladder, the four predictions | **all three acts** |
| **7** (0.75d) | Packaging: PEP 723 pins, molab badge and repo layout, README, SAFETY.md, ipynb make target, global-name audit | the repo |
| **8** (1.0d) | Two cold dry runs — fresh machine, cold uv cache, fresh Cloud DB — plus one molab run. Fix list | the fix list |
| **9–10** | **EMPTY.** For the dry-run fix list and for whichever Day-0 unknown resolved badly | — |

Bottom-up: 0.5 verification + 1.0 plumbing + 0.25 embed + 0.75 Act 1 + 1.5 Act 2 +
0.75 Act 3 + 0.75 packaging + 1.0 dry runs = **6.5 days of work**, spread over 8 calendar
working days because Day 0's answers gate Day 1 and the fix list needs a day to absorb.
Days 9–10 are real slack, not pre-spent on cut features — which is precisely how the
original 3.5-day estimate came apart: "two full dry runs on a cold machine" was inside
Day 3 alongside theming and a comparison panel, and the "half day buffer" was already
allocated to the Beyond-core tab and exercises.

If Nick needs it smaller: drop the RESP wire row (−0.25), drop Panel 4's key_slot column
and the `SAMPLES 0` comparison (−0.25), drop the ipynb export (−0.25) — that lands near
5.75. **Do not cut** Panel 3, the dry runs, or the safety guard. If Days 9–10 go unused,
spend them on the counterpart repo's README Notebooks section and the one-line
`scripts/flush_redis.py` fix, not on new notebook surface.

---

## 12. Trim ledger

Skim this next to the original.

| Original | Disposition | Reason |
|---|---|---|
| §1 "the exact command, the round-trip latency, the internal encoding, the memory footprint" | **change** | Latency struck from the thesis. Three promises now: the command issued, how Redis represents the value, what a feature decomposes into |
| §1 "re-seed it from their own data" | **cut** | An fsspec affordance; dies with fsspec. The leave-behind is the notebook plus the traces |
| §2 three-layer architecture | **change** | Two layers. No data plane, no SQL layer |
| §3.1–3.2 fsspec `github://` chain, second seed repo, `SEED_ROOT` widget | **cut** | Its central claim is false: `CachingFileSystem.__init__` eagerly instantiates `GithubFileSystem`, whose `__init__` unconditionally calls `self.ls("")` → a live `requests.get` to api.github.com. Measured 2 API calls cold, 1 per fresh process warm, ProxyError on a fully warm cache with the network down. Not an offline fallback. Also `expiry_time` defaults to 604800 (silent weekly refetch), and URL launch has no local `data/` dir |
| §3.2 `GITHUB_TOKEN` escape hatch | **cut** | `GithubFileSystem` does not read `GITHUB_TOKEN` from env, and `if (username is None) ^ (token is None)` hard-errors on a token alone. Moot anyway |
| §3.3 GitHub-events corpus + eight-type mapping table | **cut** | There is no corpus. The seed is twelve string literals |
| §4.1–4.3 Datasette, `seed.db`, sqlite-utils, JSON API, Datasette Lite | **cut** | Two of its three stated jobs are latency and load generation, both out of scope; the third ("a second leave-behind surface") is not worth a second server, a version pin and a third artifact. The one surviving idea is a sentence, not a layer |
| §5.1 item 7 "Redis vs Datasette" panel | **cut** | On correction 4's prohibited list by name |
| §5.1 item 1 Config cell | **change** | One `mo.ui.text(kind="password")` for `REDIS_URL`, a displayed non-editable `run_id`, a TTL constant. One credential, not fourteen keys |
| §5.1 item 2 Connect & validate, `INFO` essentials | **change** | Promoted to the preflight + capability probe, and the field list fixed: `maxmemory` and eviction policy are absent from Cloud's CONFIG subset entirely, so those panels cannot be populated. `HELLO` replaces the fixed `INFO` list — on Cloud, "INFO returns a different set of fields than Redis Open Source". Never request `INFO commandstats` or `latencystats`; they *are* the out-of-scope surfaces |
| §5.1 item 3 Seed / reset | **change** | No fsspec. Gated on `run_button`, prefix-guarded, TTL in the same pipeline, and it prints its own `redis-cli` equivalent — so the seed step is the first internals beat rather than dead plumbing |
| §5.1 item 4 Keyspace explorer | **change** | Narrowed to Panel 4 and reframed from "profiler" to "key shape and lifecycle". `scan_iter` over the run prefix only. Biggest-keys / top-namespaces / keys-without-TTL cut — Insight ships them |
| §5.1 item 5 + §5.2 six type tabs and the per-tab template | **cut** | Biggest line item in the original and the reason 3.5 days was fantasy. Replaced by four linear Act 2 panels with a four-slot grammar. Slot 3 ("visualization — chart or table") is gone: a bar chart of star counts teaches nothing about Redis. Slot 5 (Big-O) survives as one line of prose |
| §5.1 item 6 "Beyond core" — Bitmap, HLL, Geo, JSON, Vector | **cut** | Four of five have no honest data here and no coordinate field exists in any domain schema. The vector teaser becomes the demo's actual subject instead of a widget. Geo survives as an optional appendix (§5.5) |
| §5.1 item 8 + §6.2 command tracer | **keep, fixed** | Now the spine. Two real bugs fixed (`finally` can never bind the reply; a raising command is recorded with a plausible duration and no error marker). Merged with the guard into one `execute_command`. Latency demoted to one incidental column |
| §5.1 item 9 Exercises | **change** | Three or four open-ended prompts → four closed, falsifiable predictions with answer keys. Open-ended prompts in a leave-behind get skipped |
| §5.3 the encoding widget | **change** | Idea keeps, facts rebuilt — the original is wrong on Set, List and Stream (§5.6). Mechanism changes from a slider that `CONFIG SET`s a threshold to observing a flip *caused by the feature*. `CONFIG SET` on a threshold is database-wide and would fire on every drag event |
| §6.1 the epoch button | **change** | Replaced by a write receipt that carries real data plus one `mo.ui.refresh`. The counter button was right for six tabs and is now a fake dataflow edge a future editor deletes as dead code |
| §6.3 write-cell gating | **change** | Requirement keeps, primitive changes: `mo.ui.run_button` + `mo.stop`, the only button whose value resets to `False` after dependents run. The §6.1 counter stays truthy forever and re-fires |
| §6.4 keyspace-notification feed in `mo.Thread` | **cut** | Needs a database-wide `CONFIG SET` on the customer's live config, and "restore the prior value" is a promise you cannot keep if the notebook dies mid-run. Plus `mo.Thread` cleanup is the author's job and a blocking read never checks `should_exit`. The one finding worth keeping (`KEA`+`noc`) goes in the README as one line |
| §7 "Redis Cloud restricts admin commands by plan tier" | **change** | Wrong framing: restrictions are by **database type** (Standard vs Active-Active) plus a fixed command/config allowlist. Table rewritten; `LATENCY` row deleted (no latency panel, and `LATENCY HISTOGRAM` raises `NotImplementedError` in redis-py while the rest are blocked); `MONITOR`/`DEBUG` row rewritten. Not half an hour — a script whose real replies get pasted in (§10.4) |
| §8 three-day schedule + half-day buffer | **change** | 6.5 days of work over 8, plus 2 empty. The original buffer was pre-spent on two features that are now cut |
| §9 PEP 723 + `uvx` one command | **keep, hardened** | Now the whole local story. Pin `marimo@<ver>` in the command; pass `--sandbox` explicitly (the prompt is skipped on a non-TTY stdin); `--trusted` mandatory on the URL form; real first-run numbers in the README |
| §9 `marimo run` for the room, edit mode afterward | **change** | Not a two-mode story. `--include-code` is a global server flag with no per-cell granularity, so edit mode would expose Act 1 and run mode without `mo.show_code` would hide Act 3. One file: all disclosure is rendered as **output**, so `edit` and `run` show identical content |
| §9 "Ship a scoped ACL user" + the `ACL SETUSER` README line | **cut** | Not merely unhelpful — wrong. Unsupported on Cloud; docs redirect to console RBAC, a paid capability. Replaced by §8's six client-side layers |
| §9 "TTL everything" | **keep, rescoped** | A TTL stops the demo filling their database and does nothing about a key-name collision. Second layer, not first |
| §9 README appendix ("swap in your own seed data — change one fsspec URL") | **change** | fsspec is gone. New appendix set in §9.5, including the molab badge and the Insight / university.redis.io links |
| §10 risk table | **change** | Rewritten (§13). Four rows are now moot: GitHub rate limits, Datasette overshadowing, the `filecache::` venue-network mitigation (verified false), and anything latency- or eviction-shaped |
| §11 open decisions | **change** | 1 (customer's own corpus) → no, there is no corpus. 2 (Datasette hosting) → moot. 3 (design system) → neutral plus their logo, inline CSS. 4 (vector teaser) → it *is* the demo. Genuinely open items in §14 |

**Also cut, and never reintroduced:** `MONITOR` in the notebook (it streams the customer's
other clients' commands from their single Essentials database onto a shared screen — a
conversation with their security team, not a panel; and it costs a connection plus a
thread whose `next_command()` blocks forever because `read_response` is called with no
timeout and disconnects on `socket.timeout` by default). `ACL DRYRUN` as a headline (you
cannot create a restricted user on Cloud, so every DRYRUN against `default` returns OK).
`redis.observability` as a tracer replacement (§4.2). Vendored model weights (§2).
`css_file` / `layout_file` / grid layout (§7.5). `mo.ui.tabs` anywhere.

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| **molab cannot reach the Cloud endpoint's high port** → the sanctioned backup does not exist | Day 0 item 1, before any notebook code. If it fails, escalate rather than absorb: the fallback backup is a local `uvx` run on a phone hotspot plus the static ipynb, both strictly worse than molab |
| **Target database is Active-Active without Search enabled** → `FT.CREATE` fails and there is no Act 1 at all | `FT._LIST` is capability probe #1. README's first paragraph: point this at a Standard database, or an Active-Active database on Redis 8+ |
| **Target database is Active-Active with Search** → Panels 1–2 lose the encoding row | Built degradation to `MEMORY USAGE` + `HSTRLEN`, visible and labelled. Confirm the database type before the meeting. The §5.5 geo appendix is the one marquee beat that survives A-A intact |
| **The notebook writes to the customer's only database, which holds real keys** | §8's six layers. New Essentials subscriptions allow one database, so this is the common case, not the edge case |
| **Static embedder fails on negation in front of an NLP-literate room** | Disclosed in one line in the notebook; starter prompts avoid negation; `EMBED_BACKEND="openai"` switchable live with an explanation of why the answer improves. Not solved |
| **First-run downloads (~216 MB uv + ~30 MB model) on venue wifi** | Pre-warm both caches on the demo machine; the README states the real numbers. Note the original's `filecache::` mitigation was verified false |
| **A wrong `REDIS_URL` in the room** — now the top single point of failure, since there is no mock mode and no WASM path | Per-exception error copy through `mo.callout`, 5 s connect timeout so it fails fast, and a local `redis://localhost:6379` fallback the presenter switches to in one field |
| **marimo pre-1.0 churn** (~7 releases in 10 weeks; `mo.tabs` already deprecated) | Pin `marimo@<ver>` in the launch command, the README and the inline deps. Panel 4 prints `marimo.__version__` and `redis.__version__` live, so skew is visible. ~2 hrs/quarter to re-pin |
| **`MultipleDefinitionError` across four near-identical Act 2 panels** | Single-underscore locals plus the act-prefixed UI names, decided Day 1 and grepped pre-commit, not refactored on Day 7 |
| **`mo.show_code`'s greedy substitution mangles a panel's displayed source in front of the customer** | Hard rule in the file header: `mo.show_code(...)` is the cell's last expression and the cell ends at its closing paren |
| **Panel 3's `FT.INFO` parsing breaks on a Cloud version bump** | Parse defensively, whitelist the fields used, render unknown keys as raw rather than erroring |
| **Connection exhaustion against the 30-connection cap across re-runs** | Clients built in exactly one cell with an explicit `max_connections`; no threads; `auto_close_connection_pool` left at its default |
| **The demo is handed to a non-Iris prospect**, where Act 3's ladder is dead weight | It still stands as "build agent memory from primitives." Pre-decide the framing (§14 item 2) rather than discovering it mid-meeting |
| **Act 1's recall card is visually plainer than the Iris UI the participant just saw** | The framing must be "engine, not product" from the first sentence, or it reads as a downgrade rather than a reveal. That sentence is a deliverable, not a nicety |

---

## 14. Open decisions

Only the ones Nick genuinely still has to make.

1. **Which database does the room point at?** Because new Essentials subscriptions allow
   only one database, a customer with an existing Essentials DB cannot provision a clean
   one. Either we ship a throwaway Cloud database of our own for the room and hand them the
   repo for their own database afterwards, or we run in theirs with §8's guard and their
   explicit consent. This also decides whether §10 item 2's Standard/Active-Active answer
   is ours to control or theirs.
2. **Is this pitched to an Iris prospect?** If yes, Act 3's three-rung ladder is the close
   and the framing is "engine, not product." If no, the ladder gets cut and the notebook
   stands as "build agent memory from primitives" — which it does, but the differentiating
   argument evaporates and the ~1 hour spent on the ladder is wasted.
3. **Embedder default.** `potion-base-8M` (a ~30 MB first-run download, no second
   credential, weak on negation) or OpenAI embeddings (better answers, a second credential
   to paste, which is exactly the friction correction 2 asks us to remove)? Recommendation:
   static by default, `EMBED_BACKEND` switchable, and pre-warm the cache.
4. **Is `mo.ui.chat` worth the stretch?** It turns Act 1 from a two-button bench into a
   conversation, at the cost of the one place in the notebook that fights marimo's
   execution model (`mo.state` driven from `on_message`, which marimo's own docs
   discourage). Recommendation: build the bench first and decide on Day 6 with real slack
   visible, never on Day 3 with optimism.
5. **If molab egress fails, what is the backup?** A screen recording, or does the demo
   become local-only and we drop the badge? Decide now, so Day 0's answer triggers a plan
   rather than a discussion.
6. **Two ten-minute PRs to the counterpart repo — in or out of this scope?** Adding a
   Notebooks section to `redis-iris-demos`' top-level README (it currently has none, so
   this demo is undiscoverable next to `radish_bank_workshop.ipynb`), and the one-line fix
   to `scripts/flush_redis.py`'s allowlist so `make reset` stops SCAN-deleting non-`memory:`
   keys.