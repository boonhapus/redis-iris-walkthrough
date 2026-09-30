# Redis Iris basics

A barebones [marimo](https://marimo.io) notebook that builds the Redis mechanics
behind three Iris components out of raw `redis-py`, then takes them apart.

**One credential.** `REDIS_URL` and nothing else. No OpenAI key, no Agent Memory
key, no LangCache key. No LLM call anywhere in the file.

---

## Run it

```
# 0. uv, if you don't have it (Windows)
winget install --id=astral-sh.uv -e

# 1. launch -- no clone, no .env, no virtualenv to manage
uvx marimo@0.24.2 edit --sandbox notebook.py
```

`--sandbox` is not optional. marimo only offers to build a venv from the inline
PEP 723 dependencies when `uv` resolves on `PATH` **and** stdin is a TTY; from a
double-clicked shortcut, a VS Code task, or Git Bash you silently get a
dependency-less environment and an `ImportError` on `import redis`.

Then paste a `REDIS_URL` into the first cell and press **Connect**:

| Where your Redis is | URL |
|---|---|
| Redis Cloud | `rediss://default:<pw>@<host>:<port>` |
| Local | `redis://localhost:6379/0` |

> **The database must have Search and query enabled at creation time.** On Redis
> Cloud that is a checkbox you cannot add to an existing database — if the
> connect cell reports "Search MISSING", create a new one. This applies to
> Standard Essentials, not just Active-Active.

`marimo run notebook.py` serves the same file as an app with the code hidden —
use that in a room, hand over `edit` afterwards.

## What it shows

| Component | What it really is |
|---|---|
| **Agent Memory** | one `HASH` per memory with a vector field, read with `KNN` |
| **LangCache** | the same `HASH` + vector, read with `VECTOR_RANGE` |
| **Semantic Routing** | the same again, read with `FT.AGGREGATE` + `GROUPBY` |

All three are `HASH` + `TEXT` + `VECTOR COSINE`. What differs is the field
names, `HNSW` vs `FLAT`, the threshold, and **which query grammar reads it**.

Three acts: run it, take it apart, read the code. The sharpest beat is in Act 2 —
`KNN 3` always returns 3 rows, so a KNN-backed cache *can never miss*;
`VECTOR_RANGE` returns zero rows outside its radius, which is the only reason a
cache MISS is expressible at all.

### Scope, honestly

Agent Memory and LangCache are **hosted HTTP services that embed text
server-side**, so their own Redis commands are not observable from a notebook.
This builds an equivalent; it does not inspect theirs. Context Retriever is
named but not rebuilt — its per-agent access-tag scoping is enforced
server-side and is not reproducible on Redis Cloud, where `ACL SETUSER` is
unsupported.

Not a performance demo. Latency appears as one incidental column in the command
trace and nowhere else.

## Safety

Because `ACL SETUSER` does not work on Redis Cloud, scoping is client-side:

- Every key and index is namespaced `demo:<random6>:` per session, so two people
  on one database cannot collide.
- `GuardedTracedRedis` refuses any command outside a 22-entry allowlist and any
  key outside the session prefix — **before** it reaches the server. `FLUSHDB`
  and `CONFIG SET` are denied. Refusals appear in the trace at ~0.0 ms, which is
  the tell that they never left the process.
- Every key carries a 3600s TTL, so doing nothing is safe.
- A consent cell shows you the current `DBSIZE` and what will be written, and
  nothing is written until you press the button.
- The teardown cell removes every key and index it created.

Still: point it at a scratch database, not your only Essentials one.

## Tests

No Redis needed for either.

```
.venv/Scripts/python.exe calibrate.py     # are the distances and thresholds right?
.venv/Scripts/python.exe test_offline.py  # guard, tracer, vectors, command argv
```

`calibrate.py` is the useful one. Redis COSINE distance on unit vectors is
`1 - dot(a, b)`, so every threshold in the notebook can be checked before
touching a database. Measured with `potion-base-8M` (256 dims, 1024 bytes per
vector):

| Check | Result |
|---|---|
| Act 1 recall — *"any seafood restrictions?"* → *"allergic to shellfish"* | top-1 correct at `0.681`, margin `0.098` over #2 |
| Act 2 cache — paraphrase vs unrelated | `0.221` vs `0.825`; default radius `0.35` separates them |
| Act 2 router — 4 queries incl. one correct refusal | 4/4 |
| `MIN` vs `AVG` | `MIN` routes the dinner query to `off_topic`; `AVG` blocks it |

**Re-run `calibrate.py` if you change the embedder or the seed phrases.**
Thresholds do not transfer between embedding models. Iris routes at 1536 dims
and its hosted memory index is 3072; this is 256, and `DIM` is fixed at
`FT.CREATE` — so this notebook cannot read an Iris-seeded index, and vice versa.
That is itself worth saying out loud in a session.

### Known soft spot

Act 1's ranking is correct but its absolute distances are compressed (`0.681`
for the right answer). `potion-base-8M` is a *static* embedding model, so KNN —
which only needs the ordering — is safe, while any absolute threshold on the
memory index would be fragile. The cache and router thresholds are fine because
their margins are wide.

## Files

```
notebook.py      the notebook (PEP 723 inline deps, pinned)
calibrate.py     offline threshold calibration
test_offline.py  offline guard/tracer/argv tests
```

## Not yet verified

The command shapes match Redis's documented vector-search syntax and redisvl's
own generated argv, and `marimo check --strict` passes, but **no part of this has
run against a live Redis server** — there was none available where it was built.
First run against a real database is the remaining check: `FT.CREATE` accepting
three indexes, `FT.AGGREGATE` with `GROUPBY`/`REDUCE`/`FILTER` being allowed on
your Cloud database type, and the `FT.SEARCH` replies parsing as expected.
