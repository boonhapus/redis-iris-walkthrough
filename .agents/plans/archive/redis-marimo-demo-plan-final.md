# Redis Agent Memory — Interactive Internals Notebook

**Build plan / scoping document — supersedes `redis-marimo-demo-plan.md`**

| | |
|---|---|
| **Audience** | Developers, hands-on. Ideally an Iris prospect; stands alone if not |
| **Deliverable** | One marimo notebook + README + SAFETY.md + LICENSE + a two-minute presenter script, in one public repo. Runs against their own Redis Cloud DB or their own local Redis |
| **Connectivity** | Live only. No mock mode, no WASM (Pyodide has no raw TCP sockets, so redis-py cannot reach Redis at all) |
| **Effort** | ~6.5 build-days of work, scheduled over 8, plus 2 days of empty slack |
| **Stack** | marimo (pinned) · redis-py 7.4.0 · model2vec · numpy · uv |

---

## 1. What this is

One marimo notebook that builds a working **agent memory** — the thing Redis Iris ships as a hosted
Memory API — out of raw redis-py in about forty visible lines, then opens the hood on it. The
participant states a fact, chats about something else, asks a question with zero keyword overlap,
and gets the fact back with a cosine distance. The notebook then shows the feature is a LIST with an
`LTRIM` and an `EXPIRE`, a HASH per memory with one binary vector field, and one `FT.CREATE`
watching a key prefix.

**Three acts, in file order and in narrative order.** **Act 1 — run it and see it:** two controls, one
button, a recall card, and the exact commands that produced it, in ~2 minutes. **Act 2 — break down
the components:** four panels, vertical scroll, not tabs. **Act 3 — walk the code:** six steps
rendering the *real* source of the functions Act 1 ran, each gated to run that step, closing on a
three-rung abstraction ladder. The boundary: **Act 2 shows what the value *is*; Act 3 shows the call
that made it.**

**Not a performance demo:** no benchmarks, percentiles, throughput, cache-hit-ratio, Redis-vs-SQL or
load generation. Latency appears once, as one narrow unaggregated column in the trace; a chart of a
duration anywhere in this notebook is a bug. **Not a data-types tour** — `redis.io`'s per-type
tutorials and `university.redis.io`'s free path own that spine. **Not an extension of
`radish_bank_workshop.ipynb`**, which asks for 12 pasted credentials and states "~2 hours". **Not a
keyspace profiler:** Redis Insight's Database Analysis already does memory-by-type, biggest keys, top
namespaces and keys without TTL, free and GUI. What *is* unowned: copy-pasteable `redis-cli` traces of
what the notebook just did, and the decomposition of a Redis/Iris feature into its primitives — and
there is **zero raw Redis** in the `redis-iris-demos` notebook surface, where redisvl is invisible
plumbing in `backend/app/`, so a prospect who has seen the Iris demo has seen no Redis.

**Act 1's subject is agent memory**, because it is the only candidate where the internals are a
by-product of a feature the customer already wants: the encoding flip, the binary vector field, the
TTL asymmetry and the index-is-not-a-key observation all fall out of building it, so nobody is told
"now let's look at `OBJECT ENCODING` for no reason." It has no LLM call, so the demo has nothing to
brag about and cannot drift into a benchmark. Rejected: **"one key, seven instruments"**, whose Act 1
is an instrument rather than a demo, so Act 2 would break down the notebook rather than Redis, and
which needs `MONITOR`, two threads and a database-wide `CONFIG SET` — its RESP wire row,
expected-vs-observed rendering and closing predictions survive here; and the **Reddash order page**,
the best "wait, that's all it is?" beat proposed (`TYPE` on a GEO key returns `zset`), which costs
~1.25 days synthesising a corpus for a demo whose subject is not the data (its geo beat survives as
appendix §5.1).

---

## 2. Architecture

```
  GitHub repo ──▶ uvx marimo@<pin> edit --sandbox            (local, primary)
              ──▶ molab.marimo.io/github/...                 (hosted, Day-0 gated)
     both ──▶ GuardedTracedRedis (one execute_command: reject → time → record)
          ──▶ Redis Cloud (rediss://)  OR  their own local Redis (redis://)
```

Two layers, not three: the repo delivers one file and a badge URL, not a data plane. No seed corpus
(the seed is twelve string literals inline), no SQL layer. The embedder (`model2vec`,
`potion-base-8M`) is a first-run download declared in the PEP 723 header; we do not vendor 31 MB of
weights, because URL launch has no local repo tree and molab tree materialisation is unknown, so on
both quick-start paths the vendored file would be absent and the download would happen anyway.

---

## 3. Act 1 — run it and see it

Seven cells: **header** (branded `mo.md` with an inline `<style>` block, one sentence on what you are
about to build and that there is no LLM in this file); **connect** (one `mo.ui.text(kind="password")`
for `REDIS_URL`, defaulted from `os.environ.get("REDIS_URL") or "redis://localhost:6379/0"`, plus a
`run_button`); **consent-then-probe**; **seed**; **bench**; **recall card**; **pinned trace** (§4.3).

**Consent and the capability probe are one cell, rendered first.** Read-only first: `DBSIZE`, a
zero-result `SCAN MATCH demo:{run_id}:*`, `FT._LIST` showing no colliding index. Then the gate —
*"this writes ~15 keys and creates one index on the database at `<host>`; you currently have N
keys."* Only after that press does the cell write its scratch key `demo:{run_id}:_probe` and run the
write-dependent probes, so the probe cannot precede the consent it needs. The **seed** cell then
writes twelve facts, `run_button`-gated, in one pipeline with the TTLs, printing its own `redis-cli`
equivalent — the first internals beat rather than dead plumbing, and the write that gate guards.

| Decision | Why |
|---|---|
| A two-button bench (**Remember this** / **Ask**) with five clickable starter lines, not a chatbot | `mo.ui.chat(model=…)` needs no LLM provider, but its callable executes outside cell execution, so writes inside it do not invalidate Act 2's readers; the only fix is `mo.state` from `on_message`, which marimo's docs discourage. Chat is a stretch goal built *on* the bench |
| The recall card is structured, not prose — `Recalled 3 of 12 · "allergic to shellfish" dist 0.19 · … / Recent turns 5/5 (pinned by LTRIM) · TTL sess 861s · TTL ltm -1` — and it carries three beats, all inside two minutes | The numbers are the content, and that turn's commands sit under the card in a collapsed accordion, which is `TRACE` filtered by tag rather than a second renderer (§4.3). *Paraphrase recall:* starter prompts share no words with the query, and the distance beside each hit reads as arithmetic. *Recency and semantics are different structures:* after five turns the fact is gone from Recent turns (`LLEN` pinned at 5 by `LTRIM`) and still recallable from the index — "the window forgot it; the index did not." *TTL asymmetry:* `TTL sess:…` counts down, `TTL ltm:…` returns `-1`, which is the whole session-vs-long-term design |
| Every write is idempotent (`memory_id = sha1(text)[:12]`), and vector geometry is derived, never typed (`VEC_BYTES = len(embed(""))`, `DIM = VEC_BYTES // 4`) | A double-fired button replays the same `HSET` as a no-op, so no `INCR` and no ID-less `XADD` anywhere. `VEC_BYTES` and `DIM` are referenced by Panel 2's field renderer, the recall card, the RESP row and `FT.CREATE`'s `DIM` — four hardcoded copies would be four things to get wrong when the embedder changes |
| The TTL strip does not tick by default (one `mo.ui.refresh(options=["5s","10s"])`, no `default_interval`), and the embedder gets one line of honest disclosure in the notebook | The presenter opts in for the TTL beat and turns it off after: a timer re-issuing commands on a 100-ops/sec database is load generation by accident. And `potion-base-8M` averages per-token vectors, so "allergic to shellfish" and "**not** allergic to shellfish" land almost on top of each other — starter prompts do not hinge on negation, and an `EMBED_BACKEND` switch (`"static"` / `"openai"`) lets an NLP-literate room watch the answer improve and hear why |
| Command budget: 1 `FT.CREATE`, 12 `HSET`, ~20 `LPUSH`/`LTRIM`/`EXPIRE`, a few `FT.SEARCH` | Under 100 commands and 100 KB, well inside free Essentials. Act 2's optional widen-a-hash button is the only thing issuing hundreds; it is pipelined and gated |

---

## 4. Plumbing — build this before any panel

### 4.1 Preflight capability probe

Resolve → TCP → TLS → `PING`, then the read-only probes, then (post-consent) the ones needing a
scratch key. Catch `ResponseError` per command and render the support matrix **on screen**; silent
`try/except` is unacceptable for an asset a second SE runs cold.

| Probe | Why it is in the preflight |
|---|---|
| `FT._LIST` | **Fire this first.** Search and query must be enabled **at database creation** — on Standard Essentials as well as Active-Active — and cannot be added later. Without `FT.CREATE` there is no Act 1 at all, not a degraded Act 1 |
| `HELLO`, `INFO server`, `CLIENT INFO` | `HELLO` gives protocol, client id, mode, role and **modules present** in one round trip; `INFO server` gives `redis_version` and `redis_mode`; `CLIENT INFO` gives `resp=`, `lib-name`, `lib-ver`, `db`, `addr` for this connection |
| `ACL WHOAMI`, `ACL GETUSER` | The server's own account of what this credential may do. `ACL DRYRUN` is present but not a headline: `ACL SETUSER` is unsupported on Cloud, so we can never create a restricted user to test against, and `DRYRUN` against a full-permission `default` returns OK for everything |
| `OBJECT ENCODING` on `demo:{run_id}:_probe` | Sets the flag that degrades Panels 1–2 on Active-Active (post-consent: this is a write) |
| `CONFIG GET hash-max-listpack-value`, and `redis.crc.key_slot(...)` | Read thresholds live; never hardcode. `key_slot` is client-side CRC16 with no command issued — the only distributed internals available on Cloud, and the one thing that renders with no server |
| *(failure path)* | Catch `AuthenticationError` / `ConnectionError` / `TimeoutError` separately and render through `mo.callout(kind="danger")` with the host and port echoed back and the fix for each; set `socket_connect_timeout=5` / `socket_timeout=5` so a blocked-egress or wrong-host case fails in five seconds instead of hanging |

### 4.2 One client class: guard, then trace

Both the guard and the tracer want `execute_command`. One class — reject, time, record.

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

The original snippet's `finally` could never bind the reply, so the promised reply column was
permanently empty, and a raising command was recorded with a plausible duration and no error marker.
Bind in `try`, log in `except`.

`_check_allowed` needs a **keyless allowlist** — `PING`, `HELLO`, `INFO`, `CONFIG GET`, `ACL *`,
`COMMAND COUNT`, `DBSIZE`, `SCAN`, `FT._LIST`, `FT.CREATE`, `FT.INFO`, `FT.SEARCH`, `FT.DROPINDEX` —
or `INFO` fails the prefix check and teardown fails its own guard; an explicit **key-position map**
for the ~15 commands issued (more auditable than `COMMAND GETKEYSANDFLAGS`, and independent of its
Cloud status), including `UNLINK` and the **subcommand forms whose key sits at argv[2]**,
`OBJECT ENCODING k` and `MEMORY USAGE k`, since a map assuming argv[1] silently prefix-checks the
word `ENCODING` and passes everything; and a **denylist** of `FLUSHALL`, `FLUSHDB`, `CONFIG SET`,
`SHUTDOWN`, `SWAPDB`, `RENAME`. **Do not** substitute redis-py's observability hook:
`record_operation_duration` gets no argv and no reply, which is the trace table's entire value, and
it is an OpenTelemetry module — a duration-histogram pipeline is a latency surface by another name.

### 4.3 The pinned trace — the only command renderer

One row per command as copy-pasteable `redis-cli` text, with a copy button. Columns: command, args,
reply, error, ms — the ms column narrow, unlabelled as a feature, never aggregated, charted or
compared. **Rule: the trace is the single renderer of commands in this notebook**; Act 1's accordion
and every Act 2 panel's slot 3 are `TRACE` filtered by tag. Four copies of the same surface would be
four things to keep in sync, and are why Act 2 risks reading as Act 3 twice.

| Rule / permanent note | Detail |
|---|---|
| One function owns argv, the RESP bytes and the binary-argv elision rule (`<{VEC_BYTES} bytes>` for the vector) | Called from one place, so the expandable per-row RESP detail cannot diverge from the write path. A `Connection()` that was never connected still serializes any argv via `pack_command`, which returns a **list** of chunks, so `b"".join()` it. Hand-typed RESP in a row whose whole value is byte-level precision is the failure this prevents — it is easy to write `$13` for a 9-byte key |
| Every source location quoted is a **commit-SHA permalink**, never a line number | Line numbers in redis-py, marimo and the counterpart repo all move, so a walkthrough citing `client.py:1648` cites the wrong line in a month |
| **Row one includes connection setup** | Our timer starts before `pool.get_connection()`, so command #1 carries TCP + TLS + AUTH + HELLO. Stated as a fact about our wrapper, not about redis-py, which acquires the connection before starting its own timer |
| **Pipelines and pubsub are not traced** | `Pipeline` overrides `execute_command`, and `Redis.pipeline()` hardcodes `Pipeline(self.connection_pool, …)` rather than `type(self)`; `immediate_execute_command` (the `WATCH` path) and PubSub bypass too. Act 3 step 3 is the notebook's one pipeline, so the trace goes silent exactly there and the participant is told why — which lands better with developers than a panel that only works |

---

## 5. Act 2 — the component breakdown

A **vertical stack**, not `mo.ui.tabs` (tabs default `lazy=False`, and a breakdown reads better as an
ordered argument than as hidden drawers). Order follows the write path, then the read path. Every
panel: claim → live table → commands (filtered trace) → one line of "when this is the wrong shape".
Every read is gated by `run_button` + `mo.stop(...)`, and every reader cell takes the Act 1 write
**receipt** as a parameter (§7), so the dependency edge is real.

| Panel | Claim | Live table / commands | Wrong shape when |
|---|---|---|---|
| **1 · Working memory is a LIST plus two lifecycle commands** | The "recency window" is not a feature, it is `LTRIM`; the "session" is not a feature, it is `EXPIRE` | `LRANGE` · `LLEN` · `TTL` · `MEMORY USAGE` · `OBJECT ENCODING` · `CONFIG GET list-max-listpack-size`; `LPUSH` ten times, `LLEN` stays 5, the dropped turn is still recallable from the index | you need random access or search over the window — a LIST gives neither |
| **2 · A memory is a HASH, and the vector is just bytes** | Redis has no idea those bytes are floats; `DIM`, `TYPE` and `DISTANCE_METRIC` live in the index schema, not the value | `HGETALL` with the vector rendered `<{VEC_BYTES} bytes · FLOAT32 · {DIM} dims>` · `HSTRLEN text` · `HSTRLEN vector` · `MEMORY USAGE` · `OBJECT ENCODING` · `CONFIG GET hash-max-listpack-value` | fields are always read as a unit — then it is a serialized string, one round trip |
| **3 · The index is not a key** | An index is a server-side structure observing a key prefix: no key, no `TYPE`, no TTL, absent from the keyspace. KNN is a query modifier, not a command | `FT.CREATE` (full, copy-pasteable) · `FT._LIST` · `FT.INFO` parsed to a table · `FT.SEARCH` · `SCAN` · `DEL` · `EXPIRE` | your filter is the whole query — then it is a tag lookup or a plain key |
| **4 · Namespace, lifecycle, and what the server says about itself** | Two memory tiers, two lifecycles, one prefix: session keys carry a TTL, LTM keys return `-1` | `scan_iter(match=…, count=100)` → key · `TYPE` · `TTL` · `MEMORY USAGE` · `OBJECT ENCODING` · element count · `key_slot(key.encode())`, sorted by TTL; plus live `redis.__version__` / `marimo.__version__` | you want biggest-keys, top-namespaces or memory-by-type — Insight ships all four |

| Panel decision | Why |
|---|---|
| Both encoding beats are consequences of the feature, so neither needs a slider | Panel 1 reads `list-max-listpack-size` = **-2**, ~8 KB per node and *not* an entry count, so ten short turns flip nothing; one "paste a long message" button pushes a single ~9 KB turn and flips `listpack`→`quicklist` for a size reason. Panel 2's hash is `hashtable` on the **first write**, because the vector field exceeds `hash-max-listpack-value` — read live, never assumed to be 64 |
| Read Panel 2's memory ratio live; never print a hardcoded percentage. **Dropped:** the `MEMORY USAGE … SAMPLES 0` comparison | `MEMORY USAGE` on a hashtable-encoded hash also carries per-field sds and dictEntry overhead, so show `MEMORY USAGE − payload_bytes` as its own number and let it say what it says. And the default is `SAMPLES 5` while a memory hash has ~5 fields: with `nfields ≤ samples` Redis visits every field, so `SAMPLES 0` prints an *identical* number and the "what estimate means" payoff would be the same figure twice on screen |
| **Panel 3 is the biggest panel, and the one that gets rebuilt if rushed — budget 0.6d.** Four observations, each its own button, reproducible in any order after arbitrary re-runs | (1) `SCAN` returns N hashes and no index, while `FT._LIST` returns the index and `FT.INFO num_docs` == N. (2) The literal query string as text, `(@owner_id:{alice})=>[KNN 3 @vector $vec AS dist]` with `DIALECT 2` and `PARAMS 2 vec <…>`, plus two break-its: retag to an owner with no memories and get zero hits, because the tag pre-filter narrows candidates before the vector scan, or strip the filter and another user's memories come back — the strongest security-adjacent beat here, at one line of cost. (3) `DEL` one memory hash and `num_docs` goes N→N−1 **with no index command issued**, because indexes follow keys, not commands. (4) `EXPIRE 20` on a memory and `num_docs` drops with no reindex and no sweep — **UNVERIFIED on Active-Active**, where CRDT tombstone semantics may not propagate expiry into the index (Day 0 item 2) |
| Panel 3 uses **FLAT**, not HNSW | At twelve vectors FLAT is exact and the story is truthful: an exhaustive scan of twelve vectors. HNSW is a different index structure declared in the same one line of schema, so the panel shows both `FT.CREATE` strings side by side and frames the choice as a schema decision. Beside it, quotable by permalink, the production schema: `scripts/seed_memories.py` declares `text_vector`, `VECTOR`, `HNSW`, `6`, `DIM`, `3072`, `DISTANCE_METRIC`, `COSINE` |
| Parse `FT.INFO` defensively; render absence beats as expected-vs-observed | Whitelist `num_docs`, `num_terms`, `num_records`, vector index size, hnsw/flat params, `indexing`, `percent_indexed`, `hash_indexing_failures`, and render unknown keys raw — output is version-dependent and a Cloud bump must not break the panel. Print the expected list *before* the press, so an empty row is an assertion the participant made, not a bug they suspect |
| Panel 4 says `SCAN`, never `KEYS`, and says why on screen; the one-line cleanup lives here too | `key_slot` takes **bytes** (it calls `key.find(b"{")`, so a `str` raises `TypeError`), and `{tag}` co-location is two lines: `key_slot(b"{demo:r1}:a") == key_slot(b"{demo:r1}:b") == 4466`. Cleanup being visible in-panel is why nothing is abandoned in their database |

### 5.1 Optional appendix — "the geo index is just a sorted set" (0.25d)

Off-subject, so an appendix — included only if Nick wants one more "wait, that's all it is?" beat, or
if the target is Active-Active and Panels 1–2 lost their encoding punchline, since this beat survives
A-A intact. `GEOADD` three points, then the same instruments: `TYPE` → `zset`; `ZSCORE` → the 52-bit
interleaved geohash integer; `GEOHASH` → the 11-char string for the same point; `ZREM` → gone from
`GEOSEARCH`; a bogus `ZADD` score → `GEOPOS` returns nonsense, because there is no validation layer.
Doc-verbatim: "there is no GEODEL command because you can use ZREM to remove elements. The Geo index
structure is just a sorted set."

### 5.2 Corrected encoding facts

The original §5.3 was wrong on three of six. Do not reproduce the original chains anywhere.

| Type | Original plan said | Actual behaviour |
|---|---|---|
| **Hash** | `listpack` → `hashtable` | **Correct.** Flips on entry count (`hash-max-listpack-entries`) **or** on any single value over `hash-max-listpack-value`. Our vector field flips it on write #1 |
| **Sorted set** | `listpack` → `skiplist` | **Correct.** Same either-or: `zset-max-listpack-entries` or `zset-max-listpack-value` |
| **String** | `int` → `embstr` → `raw` | **Correct.** Representation, not size: `SET k "hi"` is `embstr`, then `APPEND k "!"` is `raw` at three bytes, because APPEND needs a mutable sds |
| **Set** | `intset` → `listpack` → `hashtable` | **Wrong as a growth path.** `maybeConvertIntset()` sends an over-threshold intset **straight to hashtable**, and with defaults (intset 512, listpack 128) the 513th integer is already past 128. `intset`→`listpack` is unreachable by growth — it happens only when a **non-integer member** joins a small integer set. Encoding follows what the data *is*, not only how much there is |
| **List** | `listpack` → `quicklist` by entry count | **No entry-count knob exists.** `list-max-listpack-size` defaults to **-2**, size-based at ~8 KB per node. Flip it with one ~9 KB element, never with a hundred small ones |
| **Stream** | an encoding transition | **There is none.** `OBJECT ENCODING` returns `stream`, full stop — documented as "a radix tree of listpacks". Use `XINFO STREAM` `radix-tree-keys` / `radix-tree-nodes` against `stream-node-max-entries` **and** `stream-node-max-bytes` instead |

**The notebook never issues `CONFIG SET`** — not once, not behind a checkbox: it is database-wide and
a reactive slider bound to it would fire on every drag event. Cross a threshold by adding data, and
**read every threshold live**. Supported on Standard **and** Active-Active:
`hash-max-listpack-entries/-value`, `zset-max-listpack-entries/-value`, `list-max-listpack-size`,
`set-max-intset-entries`, `stream-node-max-entries/-bytes`, `notify-keyspace-events`.
`set-max-listpack-entries` is absent from that table, and `maxmemory` / `maxmemory-policy` / `lfu-*` /
`maxmemory-samples` are absent from the CONFIG subset — so no eviction content is available, which is
fine, because it was out of scope. `CONFIG GET` on an unsupported setting **returns an error**, not
an empty reply: wrap it and render the error.

### 5.3 Active-Active degradation, decided in advance

`OBJECT ENCODING` is not supported on Active-Active, and the documented substitute
`CRDT.DEBUG ENCODING` sits in an otherwise-blocked `DEBUG` family, so whether it answers at all is
unknown — **do not** make it the A-A path. On A-A, Panels 1–2 drop the encoding row and fall back to
`MEMORY USAGE` + `HSTRLEN` + element count (supported on both database types) behind a visible,
labelled, explained banner rather than a silent swap. That is materially weaker content, which is why
we ask which database type they have *before* the meeting. Three A-A claims are **UNVERIFIED** and
belong to Day 0 item 2, not to assumption: that Search is auto-enabled for A-A databases created with
or upgraded to Redis 8+; that **vector KNN works on A-A at all**; and Panel 3 observation 4 under
CRDT tombstone semantics. If the second fails, there is no Act 1 on Active-Active.

---

## 6. Act 3 — the step-by-step code walkthrough

Six numbered steps, one cell each, presenting the **exact functions Act 1 ran** — not a retyped copy.
Under each: two sentences of why, and a `run_button` + `mo.stop()` gate that runs that one step so a
new line appears in the pinned trace. Reading it and running it are the same gesture.

| Step | What it is, and the decision it carries |
|---|---|
| 1 · **Connect and the key schema** | `from_url(..., decode_responses=False)`, and why `False`: the vector field is bytes, so global decoding breaks it and text is decoded at the call site. The schema (`demo:{run_id}:sess:{owner}:{sid}`, `demo:{run_id}:ltm:{owner}:{sha1}`) buys one `SCAN` prefix, one cleanup, and owner isolation enforceable by key pattern *and* index tag. `Redis.__init__` sets `auto_close_connection_pool=True` for pools it builds and `__del__` closes them, so a re-run does not leak against the 30-connection cap — hence clients are built in exactly one cell with an explicit `max_connections` |
| 2 · **`embed(text) -> bytes`** | Load the `StaticModel` once, `encode`, `.astype(np.float32).tobytes()`. This is where `VEC_BYTES` comes from |
| 3 · **`remember_turn()`** | `LPUSH` + `LTRIM` + `EXPIRE` in one `pipeline()`: the notebook's only pipeline, invisible to the tracer, so the trace goes quiet here and closes §4.3's loop |
| 4 · **`remember_fact()`** | Deterministic `memory_id`, one `HSET` with the vector, then an explicit `EXPIRE` (HSET cannot set a TTL inline). Idempotency is the double-fire defence |
| 5 · **`ensure_index()`** | `FT.INFO` in a `try`, `FT.CREATE` in the `except`. Lifted in shape from `_ensure_memory_index` in `redis-iris-demos` (permalink), and the notebook says so: the production asset has the same problem and solves it the same way |
| 6 · **`recall()`** | Embed the query, build the filter+KNN string, `FT.SEARCH` with `DIALECT 2` and `PARAMS`, merge with `LRANGE`. The two result sets are merged **in Python, not by Redis**, and the note says so: that merge is the author's job |

**Closing cell — the abstraction ladder, three rungs, same recall.** Rung 1, ours:
`FT.SEARCH "(@owner_id:{alice})=>[KNN 3 @vector $vec AS dist]" PARAMS 2 vec <…> DIALECT 2`. Rung 2,
redisvl's equivalent as **code only** — redisvl is deliberately not a dependency, so nothing is hidden
and no extra wheels land — beside the production `HNSW / DIM 3072 / COSINE` schema. Rung 3,
`MemoryService.search_long_term_memory`: one `httpx.post` to
`{api_base_url}/v1/stores/{store_id}/long-term-memory/search`. Both non-obvious rungs are
verbatim-quotable from the counterpart repo by permalink, and together they name honestly what the
hosted product buys — not the primitives, the *operating* of them — as code rather than as a slide.

**Second closing cell — four falsifiable predictions**, answer keys in a collapsed accordion; better
handoff than a summary, and free. (1) Make `FT.INFO num_docs` drop without issuing any index command.
(2) Make a recall return zero hits without changing the query text. (3) Make a memory hash's
`MEMORY USAGE` drop while its field count goes up — *the answer key says plainly that the only
solution is deleting the vector field, since hash encoding never converts back to listpack.* (4) Find
the command in the trace the tracer did **not** record.

---

## 7. marimo engineering decisions

Each of these costs a day if discovered late. Settle them before building any panel.

| Decision | Why it is load-bearing |
|---|---|
| **`with app.setup(hide_code=True):` for imports, plus one top-level `@app.function` / `@app.class_definition` per definition** | marimo serializes a cell holding exactly one function or class as a real module-level `def`/`class`. In the ordinary wrapped-cell form, `inspect.getsource` on a cell-defined **class** raises `OSError: could not find class definition`, because the class's `__qualname__` says top-level while the file has it indented inside a cell function; with `@app.class_definition` it works. Second payoff: the `.py` reads as normal Python on GitHub, which matters because the quick-start path is a raw GitHub URL. The constraints those cells impose — one definition, no references to other cells, no `_` names — force every piece of Act 1 machinery into a named, `getsource`-able unit, which is exactly the Act 3 inventory. Two gotchas: `@app.function` returns source starting at `def` while `@app.class_definition` returns the decorator line first, so strip lines starting `@app.` and keep `textwrap.dedent`; and never trust line numbers, since kernel mode resolves through `linecache` pseudo-files |
| **File layout = narrative order = topological order:** prelude at the top (setup plus every `@app.*` cell, `hide_code=True`), then Acts 1, 2, 3 | marimo resolves by dataflow, so an Act-1-first layout works — reject it anyway, because `marimo export ipynb` defaults to `--sort topological` and would hoist the definitions above Act 1, and a reader of the `.py` on GitHub would see an order contradicting the act headings |
| Two disclosure mechanics, two jobs: `mo.show_code` for panels, `inspect.getsource` for Act 3 | `mo.show_code(output, position="above")` keeps panel code beside its output with no second copy to sync — **footgun:** its substitution is greedy to the last `)` in the cell, so the hard rule is that `mo.show_code(...)` is the cell's last expression and the cell ends at its closing paren. Act 3 needs to slice, so one helper `walk(obj, first=None, last=None)` dedents, strips `@app.` lines, slices between marker substrings and returns a disabled `mo.ui.code_editor`; `show_copy_button` defaults `True`, which is what makes the walkthrough copy-pasteable, and `language="shell"` renders the traces |
| **Both mechanics render code as OUTPUT, which is why one file serves both delivery modes** | Code visibility in `marimo run` is a **global** server flag with no per-cell granularity, and `CellConfig.hide_code` is editor-only. So `marimo edit --sandbox` is the authoring and handoff path (and what molab gives you regardless), while `marimo run --sandbox` without `--include-code` is the optional room path: Acts 1–2 source is blanked before it reaches the browser, Act 3 renders normally because its disclosure is output. No mode switching mid-demo |
| **Delete the epoch button; use a write receipt** — `write_receipt = {"keys": n, "at": …, "prefix": …}`, taken as a parameter by every Act 2 reader — and gate every write with `run_button` + `mo.stop(not btn.value)` | A global counter was right for six independent tabs and is now a fake dataflow edge a future editor deletes as dead code; a receipt is a genuine edge carrying useful data. Plus exactly one `mo.ui.refresh(label="Re-read Redis")`, no `default_interval`, for when they mutated the database from `redis-cli` in another window. `run_button` is the only button whose value resets to `False` after dependents run, which is the required one-shot semantics; a counter button stays truthy forever and re-fires on unrelated re-runs |
| **`MultipleDefinitionError`: one underscore solves it** | Single-underscore names are invisible to the dataflow graph and reusable in every cell (`__name` is *not* local), so single-underscore every intermediate; Act 2's panels repeat the same shapes constantly. About a dozen names cross act boundaries and keep bare names — the setup imports, the `@app.*` names, `REDIS_URL`, `PREFIX`, `RUN_ID`, `VEC_BYTES`, the client, `TRACE`, `write_receipt`, and the UI elements, prefixed by act (`a1_remember`, `a2p3_del`, `a3s4_run`). Keep that list as a prelude comment and grep it in CI. Two constraints: `@app.function` cells cannot define underscore names, and an underscore name cannot cross cells — so never underscore what Act 3 must `getsource` |
| `mo.accordion(items, multiple=True, lazy=True)` for Act 3's step bodies | Both `accordion` and `mo.ui.tabs` default `lazy=False`, so without `lazy=True` opening Act 3 fires every Redis command in the notebook — a visible stall on a 100-ops/sec database in front of the customer. **It only defers rendering of an already-constructed object**, so values must be zero-arg callables (`{"Step 4": lambda: _step4()}`) and lazy bodies stay output-only; building `mo.ui.*` inside one is the risky case, since the registry holds elements weakly |
| **No `mo.Thread`, no `css_file`, no `layout_file`, no grid layout, and a minimal setup cell** | Thread cleanup on invalidation is the author's job via `should_exit`, a blocking read does not check it until a message arrives, and a leaked connection costs one of thirty per unclean re-run — so no `MONITOR` feed and no keyspace feed in v1. `css_file` / `layout_file` are relative to the app file, and `marimo edit <URL>` downloads to a temporary file, so neither resolves on the URL or molab paths: brand inline with a `mo.md` `<style>` cell, use `width="medium"` with `mo.vstack` / `mo.hstack`, set `app_title`, avoid `width="columns"`, use `mo.outline()`. And keep setup to marimo, stdlib and redis — every top-level definition hard-depends on it, so importing `model2vec` / `numpy` in an ordinary cell keeps Acts 2 and 3 alive against a pre-seeded keyspace if the embedder fails |

---

## 8. Safety — what replaces `ACL SETUSER`

`ACL SETUSER` / `DELUSER` / `GENPASS` / `LOAD` / `LOG` / `SAVE` are unsupported on Redis Cloud; the
docs redirect to console RBAC, a paid capability. So the README points at console RBAC and says it is
paid rather than instructing, and enforcement moves client-side — six layers, in `SAFETY.md`:

| Layer | What it is |
|---|---|
| 1 · **Namespace** | `demo:{run_id}:`, generated per session, displayed, not editable — not a fixed `demo:*`, which can collide with keys they own. The index is `idx:demo:{run_id}:ltm`, so `FT.CREATE` cannot collide either |
| 2 · **Guard** | `GuardedTracedRedis` (§4.2): key-position map with subcommand offsets, keyless allowlist, denylist. It does **not** cover pipelines, for the same reason the tracer does not, so `remember_turn()`'s keys go through the one prefixing helper and the guard is defence in depth |
| 3 · **Preflight consent** | Act 1 cell 3: `DBSIZE`, a zero-result scoped `SCAN`, `FT._LIST` showing no colliding index, then a `run_button` naming the host and the key count. It gates the seed cell and every write after it, including the probe's own scratch key |
| 4 · **TTL** | Every key expires — `SET … EX` inline where possible, an explicit `EXPIRE` right after `HSET` / `LPUSH`, which cannot. A TTL stops the demo filling their database and does **nothing** about a name collision, since an `HSET` on a key they own would overwrite their value and then delete it. Second layer, not first |
| 5 · **Teardown** | Scoped `SCAN MATCH demo:{run_id}:* COUNT 100` + `UNLINK`, plus `FT.DROPINDEX` for this run's index and any orphaned `idx:demo:*` from earlier runs (found with `FT._LIST`, which is Panel 3's content anyway). Show the `redis-cli` equivalent. **Never `FLUSHDB`** |
| 6 · **Transparency over enforcement** | Print `ACL WHOAMI` and `ACL GETUSER`; if they connect as `default` on Essentials — the likely case — the notebook says plainly: *this credential can erase your database; the only thing stopping that is our client-side guard.* More credible to a developer audience than a fake ACL line, and it is the truth |

Five hard don'ts, all in `SAFETY.md`: **never name a demo prefix `memory:`**, because
`scripts/flush_redis.py` in `redis-iris-demos` is a SCAN-and-delete whose allowlist is that single
prefix, so `make setup` / `make reset` would delete the demo's keys; **the credential is on screen**,
so use `kind="password"` and never screenshot that cell; **`--trusted` on a remote URL is a
code-execution trust bypass**, not a convenience flag — only ever against our own pinned tag URL;
**pasting a live credential into molab is a security-review question**, not only an egress one, since
a third-party runtime holds it; and **no `CONFIG SET`, ever**, checked at each dry run.

---

## 9. Delivery — two paths, one file

```bash
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"      # README step 0 (Windows)
curl -LsSf https://astral.sh/uv/install.sh | sh                 # README step 0 (macOS / Linux)
uvx marimo@<pin> edit --sandbox notebook.py                     # clone-or-download, then this
uvx marimo@<pin> edit --sandbox --trusted <raw-url>@v1.0.0      # 30-second taste
```

| Path | Decisions and costs |
|---|---|
| **Local (primary)** | Step 0 is the uv install line — without it the 30-second taste is an error message. **Pin marimo in the launch command**, not only in the inline deps: it is pre-1.0, shipped ~7 releases in 10 weeks, and `mo.tabs` is *already* deprecated in favour of `mo.ui.tabs`. **Pass `--sandbox` explicitly** — `uvx marimo edit` does honour PEP 723 inline deps and prompts with default `True`, but the prompt is **skipped when stdin is not a TTY**, so a double-clicked shortcut or a VS Code task silently gets a dependency-less env and an `ImportError` on `import redis`. **`--trusted` is mandatory on the URL form** (the remote-URL Docker prompt defaults to yes and hard-errors with "Docker is not installed" on a bare Enter) and it is a trust bypass — see §8. The URL form is for getting started, not keeping: it downloads to a temporary file, so Act 3 exploration is not saved and the leave-behind instruction is clone-or-download. `--host` defaults to `127.0.0.1`, so there is no firewall prompt. First-run expectations go in the README with real numbers: a prompt plus a two-pass uv install of roughly 216 MB, plus a ~30 MB `potion-base-8M` download on first embed, both caches pre-warmed on the demo machine — **install-time** setup numbers, not Redis measurements |
| **molab (sanctioned backup, gated on Day 0)** — `molab.marimo.io/github/<org>/<repo>/blob/v1.0.0/notebook.py`, the structural twin of a Colab badge the counterpart repo already sets a precedent for | Gives us a Secrets panel writing a persisted `.env` that marimo loads automatically and that the docs say "is not forked so you can share a notebook and its data without sharing your credentials", so the connect cell reads `REDIS_URL` from the environment on both paths with no molab branch (*caveat:* marimo issue #5972, "Secrets in Molab are shared together with `.env`", reads like a contradiction — confirm before telling a customer their credential is private); plus GitHub sync, so we develop locally and push. Does **not** give us a usable filesystem (only sidebar uploads and `mo.persistent_cache` persist — irrelevant with no corpus, and a second reason not to vendor weights); **verified PEP 723 handling** (its model is a mutable `pyproject.toml` plus auto-install-on-import, so we lose version pinning — keep the header pinned for the local path regardless, and have Panel 4 print `redis.__version__` / `marimo.__version__` live so skew becomes visible in-narrative data); or persistence (12 h max, 90 min idle, and forks land in the user's own molab account, so the molab path produces a fork, not a file — say so plainly) |
| **Local Redis instead of Cloud** | Fully supported, one field, no branch: `Redis.from_url` dispatches TLS on the scheme, so `redis://localhost:6379` needs no flag and no toggle UI — a toggle would imply the distinction matters to the lesson, and it does not. Redis Cloud is a convenience for getting started quickly, not a subject of the demo; the README says that once and states the free Essentials constraints honestly — 30 MB, 30 connections, 100 ops/sec, 5 GB/month, no replica, and **new Essentials subscriptions allow only ONE database**, so a developer who already has one cannot provision a clean one, which is why §8 exists. No local Redis *install* instruction, just one line: "Already have Redis locally? Use `redis://localhost:6379` instead." |
| **Room size** | 30 connections and 100 ops/sec is **one database per participant**, so there are two supported shapes and the README names the default: **presenter-driven, participants watch and take the repo home** — the only shape that works for a room of any size — or **one free Essentials database each**, which needs everyone provisioned beforehand and collides with the one-database limit for anyone who already has one. Never one shared database with thirty clients |

**The blocking unknown. Whether a molab container can open a TCP connection to a Redis Cloud endpoint
is UNVERIFIED**, and it is worse than "port 6379": Cloud endpoints carry a **per-database random high
port**, so the real question is arbitrary outbound high-port TCP from a hosted container. molab runs
on Modal sandboxes (stated in marimo's launch post), and Modal's networking offers `block_network`, a
**domain** allowlist governing TLS on **port 443 only**, and a **CIDR** allowlist under which
"non-TLS traffic (HTTP, raw TCP, UDP) to IPs that are not on a CIDR allowlist is blocked", defaulting
to all-allowed. A `rediss://host:12345` connection is raw TCP by that rule, so if marimo set any CIDR
allowlist it fails and no notebook code fixes it. In our favour: molab markets Postgres/MySQL
connectivity, non-HTTP TCP on 5432/3306, which the same rule would block. **Probably works, not safe
to assume.** Day 0 item 1.

One repo, nine files: `notebook.py` (PEP 723 inline deps, every version pinned), `.env.example`
(`REDIS_URL` only), `README.md`, `SAFETY.md`, `LICENSE`, `PRESENTER.md`, `Makefile` (export-ipynb,
smoke), `.github/workflows/`, `pyproject.toml`. No `data/`.

README order: (0) install uv; (1) the pinned uvx one-liner; (2) **create the database with Search and
query enabled** — not an Active-Active-only requirement, it applies to Standard Essentials too and
cannot be added after creation; (3) the molab badge with its caveat, once Day 0 answers it;
(4) "Connect to Redis Cloud", five minutes, framed as a convenience, with the Essentials constraints
and the room-size note; (5) "Already have a local Redis?"; (6) SAFETY.md; (7) Redis Insight and
`university.redis.io`'s free path, for the profiling and data-types content this demo does not
duplicate; (8) one line recording that `notify-keyspace-events` must be `KEA` plus `noc`, because `A`
aliases `g$lshztdxea` and `AKE` therefore excludes `m`, `n`, `o` and `c`.

| Deliverable | Why it is on the list |
|---|---|
| **`PRESENTER.md`** — two minutes | Click order, the three Act 1 beats, the framing sentence ("engine, not product"), and two failure branches: wrong URL → switch the field to local; no Search → skip to Act 3 and walk the code. The actual insurance for "a second SE runs it cold", and the one artifact no amount of notebook polish substitutes for |
| **`LICENSE`** (MIT, matching `redis-iris-demos`) and a **CI smoke check** (one workflow) | A public repo with no licence is not a leave-behind. And two pre-commit promises otherwise have nothing enforcing them: `marimo export ipynb` must succeed, and a grep must confirm no bare name outside the dozen-name prelude list. The counterpart repo has no CI at all — `# NBVAL_SKIP` is aspirational, nbval is not installed, there is no `.github` directory |
| **`marimo export ipynb`** as a read-only artifact, from the make target, never hand-edited, with a header cell saying so. **No WASM** | Use `--sort top-down` (the default `topological` would scramble the acts; with the prelude-at-top layout, top-down is also topologically valid, so the export runs); accept that it is lossy, since every `mo.ui` control degrades and Act 1 does not survive, and market it as "the code walkthrough, readable without installing anything"; and note `nbformat` is **not** a marimo dependency, so the target needs `uv run --with nbformat`. It matters because the counterpart repo's README has no Notebooks section at all, and an ipynb is the only format that renders inline on GitHub and in every corporate viewer. `export html-wasm`, by contrast, cannot work at all: in-browser Pyodide has no raw TCP sockets — the same egress question as molab, definitively answered |

---

## 10. Day 0 prerequisites

Half a day, nothing built. Items 1–3 are questions for Nick or the customer; 4–7 you run. **If item 1
fails, escalate before Day 1** — it changes the delivery promise, and Day 7 is the expensive time to
find out.

| # | Item |
|---|---|
| 1 | **BLOCKING — molab reachability and credential policy.** Push a five-line notebook to a public repo, open the molab URL form, paste `REDIS_URL` into Secrets, and run `socket.create_connection((host, port), 5)` against **the real per-database high port**, then `Redis.from_url(...).ping()`. If it fails, disambiguate in the same session: a plain socket to a known-open non-443 port succeeding proves general non-443 egress and points at Redis Cloud's config instead. Record the exact exception — a connect **timeout** means a silent drop (allowlist), **refused** means something else. Same session: does molab honour PEP 723, does `model2vec` auto-install, does the model download work. And from Nick: is pasting a Cloud credential into a third-party hosted runtime acceptable for this customer at all |
| 2 | **Standard or Active-Active — and resolve the three UNVERIFIED A-A claims.** Probe `FT._LIST` first. Then, on an actual A-A database: is Search auto-enabled on Redis 8+; does **vector KNN work on A-A at all** (if not, there is no Act 1 there); does an expiring key leave the index under CRDT tombstone semantics. Do not bank on `CRDT.DEBUG ENCODING`; test whether it answers at all |
| 3 | **Does the customer's Essentials subscription already hold its one permitted database, and does it hold keys they care about?** If yes, §8's guard is mandatory rather than prudent and the run-id-scoped index name is load-bearing |
| 4 | **Run a command sweep against the actual target database and paste the real replies in here.** `FT._LIST` / `FT.CREATE` / `FT.INFO` / `FT.SEARCH`; `OBJECT ENCODING` / `REFCOUNT` / `IDLETIME`; `MEMORY USAGE`; `HSTRLEN`; `CONFIG GET` on `hash-max-listpack-entries`, `hash-max-listpack-value`, `list-max-listpack-size`, `zset-max-listpack-entries`, `set-max-intset-entries`, `set-max-listpack-entries` (expected to **error**, which is content), `stream-node-max-*`; `HELLO`; `CLIENT INFO`; `COMMAND COUNT`; `ACL WHOAMI` / `GETUSER` / `DRYRUN`. redis-py 7.4.0 deliberately raises `NotImplementedError` from `command_info()`, `command_docs()` and `latency_histogram()`, so COMMAND introspection must call `execute_command` directly |
| 5 | **`model2vec` / `potion-base-8M` under `uvx marimo@<pin> edit --sandbox` on Windows with a cold uv cache.** Record the download size and the one-off model-load time as **install-time setup expectations** for the README — not as a Redis measurement, and not in the notebook. Confirm `len(embed(""))`, so `VEC_BYTES` and `DIM` derive from a known value |
| 6 | **Pin the marimo version** and confirm the cold-cache `uvx` launch end to end, including `--sandbox` on a non-TTY invocation |
| 7 | **Write down that the notebook will never issue `CONFIG SET`**, and check it at each dry run |

**De-risk for free:** nothing in Acts 1–3 is blocked on `redis://localhost:6379`. Author and rehearse
against local Redis, so every Day-0 unknown stops blocking *building* and only the customer-facing run
is conditional — that takes Days 1–6 off item 1's critical path. **Pre-meeting email, three items, no
credentials by email:** which database this points at (or a local `redis://host:port` if they prefer);
Standard or Active-Active; does it contain keys they care about.

---

## 11. Schedule — 6.5 days of work over 8, plus 2 genuinely empty

| Day | Work | Ships |
|---|---|---|
| **0** (0.5d) | §10 in full. Escalate if item 1 or 2 fails | a filled-in capability table |
| **1** (1.0d) | Prelude and `with app.setup:` spine, connect cell, consent+preflight cell, `GuardedTracedRedis`, key schema, idempotent `ensure_index`, teardown, naming conventions in the file header, **and `SAFETY.md` written beside the guard** | nothing visual |
| **2** (1.0d) | The one argv/bytes function: `redis-cli` renderer + RESP wire row + binary-argv elision rule; embed layer with `EMBED_BACKEND` and derived `VEC_BYTES` | the trace |
| **3** (0.75d) | Act 1: seed cell, bench, recall card copy, TTL strip, starter prompts, write receipt | **Act 1 demoable** |
| **4** (1.0d) | Act 2 Panels 1, 2 and 4. Panel 1's template is the expensive one; 2 and 4 share it | three panels |
| **5** (1.0d) | **Act 2 Panel 3** — 0.6d on its own, plus the two break-it controls and defensive `FT.INFO` parsing | the best content in the demo |
| **6** (0.75d) | Act 3: `show_code` / `getsource` plumbing, six step gates, the three-rung ladder, the four predictions | **all three acts** |
| **7** (0.75d) | Packaging: PEP 723 pins, molab badge, repo layout, README, LICENSE, ipynb make target, CI smoke check, global-name audit | the repo |
| **8** (1.0d) | Two cold dry runs — fresh machine, cold uv cache, fresh Cloud DB — plus one molab run. `PRESENTER.md` written from the first run | the script + the fix list |
| **9–10** | **EMPTY.** For the fix list and for whichever Day-0 unknown resolved badly | — |

Bottom-up: 0.5 verification + 1.0 plumbing + 0.25 embed + 0.75 Act 1 + 1.5 Act 2 + 0.75 Act 3 + 0.75
packaging + 1.0 dry runs = **6.5 days of work**, over 8 calendar days because Day 0 gates Day 1 and the
fix list needs a day to absorb. Days 9–10 are real slack, not pre-spent on cut features — which is how
the original 3.5-day estimate came apart: "two full dry runs on a cold machine" sat inside Day 3 beside
theming and a comparison panel, and the half-day buffer was already spent on the Beyond-core tab and
exercises. If it must be smaller: drop the RESP wire row (−0.25), Panel 4's `key_slot` column (−0.15),
the ipynb export (−0.25) — near 5.85. **Do not cut** Panel 3, the dry runs, the safety guard, or
`PRESENTER.md`. Unused slack goes to the counterpart repo's README Notebooks section and the one-line
`scripts/flush_redis.py` fix, not to new notebook surface.

---

## 12. Trim ledger

Skim this next to the original.

| Original | Disposition | Reason |
|---|---|---|
| §1 "the exact command, the round-trip latency, the internal encoding, the memory footprint" | **change** | Latency struck from the thesis; three promises remain — the command issued, how Redis represents the value, what a feature decomposes into |
| §1 "re-seed it from their own data" | **cut** | An fsspec affordance; dies with fsspec. The leave-behind is the notebook plus the traces |
| §2 three-layer architecture | **change** | Two layers. No data plane, no SQL layer |
| §3.1–3.2 fsspec `github://` chain, seed repo, `SEED_ROOT` widget, `GITHUB_TOKEN` escape hatch | **cut** | Its central claim is false: `CachingFileSystem.__init__` eagerly instantiates `GithubFileSystem`, whose `__init__` calls `self.ls("")` → a live request to api.github.com. Measured 2 calls cold, 1 per fresh process warm, ProxyError on a fully warm cache with the network down — not an offline fallback. And `GithubFileSystem` does not read `GITHUB_TOKEN` from env; a token alone hard-errors |
| §3.3 GitHub-events corpus + eight-type mapping table | **cut** | There is no corpus. The seed is twelve string literals |
| §4.1–4.3 Datasette, `seed.db`, sqlite-utils, JSON API, Datasette Lite | **cut** | Two of its three stated jobs are latency and load generation, both out of scope; the third is not worth a second server, a version pin and a third artifact |
| §5.1 item 7 "Redis vs Datasette" panel | **cut** | On correction 4's prohibited list by name |
| §5.1 item 1 Config cell | **change** | One `mo.ui.text(kind="password")`, a displayed non-editable `run_id`, a TTL constant. One credential, not fourteen fields |
| §5.1 item 2 Connect & validate, `INFO` essentials | **change** | Becomes the consent + capability probe, field list fixed: `maxmemory` and eviction policy are absent from Cloud's CONFIG subset, so those panels cannot be populated. `HELLO` replaces the fixed `INFO` list. Never request `INFO commandstats` or `latencystats` — they *are* the out-of-scope surfaces |
| §5.1 item 3 Seed / reset | **change** | No fsspec. Now Act 1's seed cell: `run_button`-gated, prefix-guarded, TTLs in the same pipeline, printing its own `redis-cli` equivalent, guarded by the consent gate |
| §5.1 item 4 Keyspace explorer | **change** | Narrowed to Panel 4 and reframed from "profiler" to "key shape and lifecycle". Biggest-keys / top-namespaces / keys-without-TTL cut — Insight ships them |
| §5.1 item 5 + §5.2 six type tabs and the per-tab template | **cut** | The biggest line item in the original and the reason 3.5 days was fantasy. Replaced by four linear panels with a four-slot grammar; the "chart or table" slot is gone (a bar chart of star counts teaches nothing about Redis) and Big-O survives as one line of prose |
| §5.1 item 6 "Beyond core" — Bitmap, HLL, Geo, JSON, Vector | **cut** | Four of five have no honest data here. The vector teaser becomes the demo's subject instead of a widget; geo survives as an optional appendix (§5.1) |
| §5.1 item 8 + §6.2 command tracer | **keep, fixed** | Now the spine and the *only* command renderer. Two real bugs fixed, merged with the guard into one `execute_command`, latency demoted to one incidental column |
| §5.1 item 9 Exercises | **change** | Three or four open-ended prompts → four closed, falsifiable predictions with answer keys. Open-ended prompts in a leave-behind get skipped |
| §5.3 the encoding widget | **change** | Idea keeps, facts rebuilt — the original is wrong on Set, List and Stream (§5.2). Mechanism changes from a slider that `CONFIG SET`s a threshold to observing a flip *caused by the feature* |
| §6.1 the epoch button | **change** | Replaced by a write receipt carrying real data, plus one `mo.ui.refresh`. The counter was right for six tabs and is now a fake edge a future editor deletes as dead code |
| §6.3 write-cell gating | **change** | Requirement keeps, primitive changes: `mo.ui.run_button` + `mo.stop`, the only button whose value resets to `False` after dependents run |
| §6.4 keyspace-notification feed in `mo.Thread` | **cut** | Needs a database-wide `CONFIG SET` on live config, and "restore the prior value" is a promise you cannot keep if the notebook dies mid-run. The one keeper (`KEA`+`noc`) goes in the README as one line |
| §7 "Redis Cloud restricts admin commands by plan tier" | **change** | Wrong framing: restrictions are by **database type** plus a fixed allowlist. Table rewritten; `LATENCY` row deleted; `MONITOR`/`DEBUG` row rewritten. Not half an hour — a script whose real replies get pasted in (§10 item 4) |
| §8 three-day schedule + half-day buffer | **change** | 6.5 days over 8, plus 2 empty. The original buffer was pre-spent on two now-cut features |
| §9 PEP 723 + `uvx` one command | **keep, hardened** | Now the whole local story: uv install as step 0, `marimo@<pin>` in the command, `--sandbox` explicit, `--trusted` on the URL form, real first-run install numbers |
| §9 `marimo run` for the room, edit mode afterward | **change** | Not a two-mode story. `--include-code` is a global flag, so edit would expose Act 1 and run would hide Act 3. One file: all disclosure is **output**, so both modes show identical content |
| §9 "Ship a scoped ACL user" + the `ACL SETUSER` README line | **cut** | Not merely unhelpful — wrong. Unsupported on Cloud; docs redirect to paid console RBAC. Replaced by §8's six client-side layers |
| §9 "TTL everything", and §9 repo layout | **keep, rescoped** / **change** | TTL stops the demo filling their database and does nothing about a key-name collision: second layer, not first. Layout keeps the single-file spirit, plus `SAFETY.md`, `LICENSE` (MIT, matching the counterpart), `PRESENTER.md`, a CI smoke check and an ipynb make target; `data/` is gone |
| §9 README appendix ("swap in your own seed data — change one fsspec URL") | **change** | fsspec is gone. New appendix set in §9: Search-at-creation, Cloud-as-convenience, local Redis, room size, Insight and university.redis.io |
| §10 risk table | **change** | Rewritten (§13). Four rows moot: GitHub rate limits, Datasette overshadowing, the `filecache::` venue-network mitigation, and anything latency- or eviction-shaped. "marimo is a second unfamiliar tool" is kept and upgraded |
| §11 open decisions | **change** | 1 (their own corpus) → no corpus exists. 2 (Datasette hosting) → moot. 3 (design system) → neutral plus their logo, inline CSS. 4 (vector teaser) → it *is* the demo. Genuinely open items in §14 |

**Also cut, never reintroduced:** `MONITOR` in the notebook (it streams their other clients' commands
from their single Essentials database onto a shared screen — a security conversation, not a panel; plus
a connection and a thread whose `next_command()` blocks indefinitely). `ACL DRYRUN` as a headline.
`redis.observability` as a tracer replacement (§4.2). Vendored weights (§2). `css_file` /
`layout_file` / grid layout (§7). `mo.ui.tabs`. The `MEMORY USAGE … SAMPLES 0` comparison (§5).

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| **molab cannot reach the Cloud endpoint's high port** → the sanctioned backup does not exist | Day 0 item 1, before any notebook code. If it fails, escalate rather than absorb: the fallback is a local `uvx` run on a phone hotspot plus the static ipynb, both strictly worse than molab |
| **Search and query was not enabled at database creation** (Standard Essentials or A-A) → `FT.CREATE` fails and there is no Act 1 at all | `FT._LIST` is capability probe #1; README item 2 is "create the database with Search and query enabled"; `PRESENTER.md` branch: skip to Act 3 and walk the code |
| **Target database is Active-Active** → Panels 1–2 lose the encoding row, and three A-A claims are UNVERIFIED, including whether vector KNN works there at all | Built degradation to `MEMORY USAGE` + `HSTRLEN`, visible and labelled; confirm the database type before the meeting; Day 0 item 2 resolves the three claims; the §5.1 geo appendix is the one marquee beat that survives A-A intact |
| **The notebook writes to the customer's only database, which holds real keys** | §8's six layers. New Essentials subscriptions allow one database, so this is the common case, not the edge case |
| **marimo is a second unfamiliar tool** — and the risk *grew*: `marimo edit` is now primary, so participants see notebook chrome and reactive re-runs, and the molab backup asks them to fork into a molab account | One-command `uvx` launch with the flags pre-baked in the README; `PRESENTER.md` drives the room so nobody learns marimo live; `marimo run --sandbox` stays available as an app-mode room path precisely because all disclosure is output; the ipynb export is the zero-install read |
| **Static embedder fails on negation in front of an NLP-literate room** | Disclosed in one line in the notebook; starter prompts avoid negation; `EMBED_BACKEND="openai"` switchable live with an explanation of why the answer improves. Not solved |
| **First-run downloads (~216 MB uv + ~30 MB model) on venue wifi** | Pre-warm both caches on the demo machine; the README states the real install-time numbers. The original's `filecache::` mitigation does not hold |
| **A wrong `REDIS_URL` in the room** — the top single point of failure, with no mock mode and no WASM path | Per-exception error copy through `mo.callout`, a 5 s connect timeout so it fails fast, and a local `redis://localhost:6379` fallback the presenter switches to in one field |
| **marimo pre-1.0 churn** (~7 releases in 10 weeks; `mo.tabs` already deprecated) | Pin `marimo@<ver>` in the launch command, the README and the inline deps; Panel 4 prints `marimo.__version__` and `redis.__version__` live, so skew is visible. ~2 hrs/quarter to re-pin |
| **`MultipleDefinitionError` across four near-identical panels**, and **quoted source drifting** so the walkthrough cites the wrong lines of redis-py or the counterpart repo | Single-underscore locals plus act-prefixed UI names, decided Day 1 and grepped in CI, not refactored on Day 7. Every quote is a commit-SHA permalink, never a line number, and the CI smoke check catches structural breaks in our own file |
| **`mo.show_code`'s greedy substitution mangles displayed source in front of the customer** | Hard rule in the file header: `mo.show_code(...)` is the cell's last expression and the cell ends at its closing paren |
| **Panel 3's `FT.INFO` parsing breaks on a Cloud version bump** | Parse defensively, whitelist the fields used, render unknown keys raw rather than erroring |
| **Connection exhaustion against the 30-connection cap across re-runs** | Clients built in exactly one cell with an explicit `max_connections`; no threads; `auto_close_connection_pool` left at its default; presenter-driven room shape by default |
| **The demo is handed to a non-Iris prospect**, where Act 3's ladder is dead weight | It still stands as "build agent memory from primitives." Pre-decide the framing (§14 item 2) rather than discovering it mid-meeting |
| **Act 1's recall card is visually plainer than the Iris UI the participant just saw** | The framing must be "engine, not product" from the first sentence, or it reads as a downgrade rather than a reveal. That sentence is in `PRESENTER.md` |

---

## 14. Open decisions

1. **Which database does the room point at, and in which shape?** New Essentials subscriptions allow
   only one database, so a customer with an existing Essentials DB cannot provision a clean one.
   Either we ship a throwaway Cloud database of our own and hand them the repo for theirs afterwards,
   or we run in theirs with §8's guard and explicit consent. This also decides whether §10 item 2's
   answer is ours to control or theirs, and whether the room is presenter-driven.
2. **Is this pitched to an Iris prospect?** If yes, Act 3's ladder is the close and the framing is
   "engine, not product." If no, the ladder gets cut and the notebook stands as "build agent memory
   from primitives" — which it does, but the differentiating argument evaporates.
3. **Embedder default.** `potion-base-8M` (~30 MB first-run download, no second credential, weak on
   negation) or OpenAI embeddings (better answers, a second credential to paste, which is exactly the
   friction correction 2 asks us to remove)? Recommendation: static by default, switchable.
4. **Is `mo.ui.chat` worth the stretch?** It turns Act 1 from a two-button bench into a conversation,
   at the cost of the one place that fights marimo's execution model. Recommendation: build the bench
   first and decide on Day 6 with real slack visible.
5. **If molab egress fails, what is the backup?** A screen recording, or local-only with no badge?
   Decide now, so Day 0's answer triggers a plan, not a debate.
6. **Two ten-minute PRs to the counterpart repo — in or out of scope?** A Notebooks section in
   `redis-iris-demos`' top-level README (it has none, so this demo is undiscoverable next to
   `radish_bank_workshop.ipynb`), and the one-line fix to `scripts/flush_redis.py`'s allowlist.
