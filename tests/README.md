# Tests

Notes for whoever maintains the notebook. The customer and presenter document is
[../README.md](../README.md).

## Running them

```sh
uv run --no-project --python 3.13 verify.py
```

Offline and credential-free. It works on a temp copy in an empty folder with every setting
removed from the environment, then runs `marimo check --strict`, the pytest suite in this
folder, and a headless `marimo export html` that must exit 0. A skipped test is a check that
did not run, so it makes the pytest row WARN (and the verdict says how many warned) instead
of PASS. The calibration tests skip when the embedding model is not in the local cache;
`verify.py --preflight` caches it.

```sh
uv run --no-project --python 3.13 verify.py --preflight --env-file <settings file>
```

Warms the uv environment and the model cache, then runs each column's read-only connection
test and prints a go / no-go table. The verdict is GO only when at least one column really
answered its test; if none did (no settings, or a wrong file) it says NOT TESTED and exits 1.
Values in the file are never printed.

```sh
uv run --no-project --python 3.13 verify.py --live --env-file <settings file>
```

The full run against your own services: it exports the notebook twice under a fresh run id
(`AUTORUN=demo`, then `cleanup`), judges each export by its session file, sweeps whatever the
ledger still shows, waits out one extraction cadence, and reports the footprint, which must be
empty. It writes to the accounts in the file, so run it once, by hand, after `--preflight`.
It was run once against real accounts, on 2026-09-30: the README's "Verified live" section has
what it found. Run it again after a change to any column, by hand, once.

The suite is hermetic: it refuses real network hosts, points settings at an empty file, and
uses recording fakes for Redis and stand-ins for the services (`fake_*.py`) behind
`httpx.MockTransport` or, for the real `marimo export`, behind a patched `httpx.Client.send`.

## What is in here

| File | What it tests |
|---|---|
| `test_shared.py` | The setup block: settings, secrets, scrubbing, redaction, every HTTP and Redis choke point, the ledger, the grants, the UI helpers, and the notebook's structure (items, columns, prefixes, the intro, canonical format, cleanup independence) |
| `test_redis_search.py` `test_lc.py` `test_cr.py` `test_am.py` | One column each: the exact requests, the parsing, the cells run cell by cell against a stand-in, the real kernel driven control by control, and the real `marimo export` (AUTORUN demo, then cleanup) |
| `test_integration.py` | All four columns in one export on one Redis database: no error, every write under the run prefix, everything gone after cleanup, no secret in a session file |
| `test_verify.py` | `verify.py` itself |
| `kernel_driver.py` | Runs the whole notebook in a real marimo kernel and presses or sets one control at a time |
| `notebook_ast.py` | Reads the notebook's cells and column helpers from the source, for the structure tests |

The column helpers (`rs_`, `lc_`, `cr_`, `am_` functions) are defined at the end of the setup
block, not as `@app.function` items: marimo edit makes a cell of every item, and nearly 200
hidden helpers made *Run all* take 40 seconds and a control change 20. `test_shared.py` keeps
the item count small, and checks that those functions use only names the setup block defines.

## Test hooks

These exist for the tests and for `verify.py`; a presenter never sets them. Set them in the
real environment only. marimo copies a `.env` into the environment before it starts, so the
notebook reads the *names* in the `.env` beside it (and beside the nearest `pyproject.toml`)
and ignores any of these three that a file names: a shared `.env` must not press write buttons
unasked, or give two presenters the same run id.

| Variable | Effect |
|---|---|
| `IRIS_BASICS_RUN` | Pin the 8-hex-digit run id, so `verify.py` can find the ledger and the footprint |
| `IRIS_BASICS_AUTORUN` | `demo` or `cleanup`: press the demo or cleanup buttons for you. Honoured only with a valid `IRIS_BASICS_RUN`, and never in `marimo edit`. A red banner shows when it is on. Buttons that must never autorun (orphan deletes, Reset, New session, the extraction kick-off, the look inside the database) read their value directly |
| `IRIS_BASICS_HEADLESS=1` | marimo reports `edit` mode for `marimo export` too, and AUTORUN is refused there without this |
| `IRIS_NB_MODULE`, `IRIS_NB_PATH` | Which notebook module the suite imports, and from where (default `iris_basics`, beside `tests/`) |

## Verified offline, by column

**0 · Redis Search.** Every command's exact arguments (the vector attribute count, `PREFIX`,
`DIALECT 2`, `LIMIT 0 k`); parsing of RESP2 replies as bytes; a missing index in either of its
wordings as already gone (Clean up, the deep dive's first build, a search after Clean up);
that every write stays under the
run prefix and is refused outside it; the whole column, cell by cell, against an in-memory
stand-in for a Redis 8 server, including cleanup and Reset; and, with the real model, that
vector search ranks the intended article first for the paraphrase and the typo, that no article
shares a word with the paraphrase, and that hybrid has the intended article first for all
three questions.

**1 · LangCache.** The exact JSON of every request (`maxResults` 5, the rounded threshold,
`searchStrategies` in order, `ttlMillis` on every write, the scope attribute only when there is
one); that only this run's ids are kept and that another app's prompt and answer appear in no
return value, trace, table or wire view; the attribute fallback on a 400; the floor probe for a
cache that goes low, one that holds to 0.85, one whose entries all score over 0.85 and one
whose entries are gone; the lab's scoring at several thresholds; the strategies table; the chat
(HIT and MISS badges, Teach's prefix, expiry and refusals, the scripted run, the delete after
the HIT); the expiry check with the clock moved; cleanup by id from the ledger, with a `404` as
expired (`/errors/resource-not-found` as the live service words it, or the documented
`/errors/cache/entry/not-found`) unless it names the cache as missing; no flush or delete by
attribute anywhere (a walk over this column's code, not a grep). The stand-in returns similarities that the tests choose, so they
prove the notebook's logic and nothing about real scores.

**2 · Context Retriever.** The exact requests (the create body with its stamp, pool of 2, TLS
from `rediss://`, the password decoded from a percent-encoded URL, the agent key's expiry, one
`POST .../data` per entity, `EXPIRE` by name, also after a failed import); that the ledger
holds the surface and its keys before the calls that make them and the indexes as soon as they
exist, through a lost create reply, a second press of Create and a failed import; that another
account's surfaces never reach a wire view, a callout or a variable; cleanup's order, that it
never sends `DD` or a scan and leaves other runs' and column 0's objects alone, and that an
index the service already dropped with the surface is counted as gone, in either wording of a
missing index, and is no stop; the prediction, which follows the unified tool names and says
which fields each tool takes, so that every index choice changes it; the JSON-RPC shapes and
the verbatim error code and message; a form built from the schema of every tool on both a
classic and a unified surface; paging with a condition derived from the schema (a unified
`filter_` tool refuses a call with none); that every **Break it** plan is refused; and that the
recorded panels shown without settings hold no identifier of the account they were captured on.

**3 · Agent Memory.** The request behind every cell (paths, camelCase bodies, `limit`,
`filterOp`, a threshold of `0.0` sent as `0.0`); that every search carries an owner and a
namespace and every session opens with a USER event from the run's owner; the 11-step sweep,
its bands against the scores of a stand-in, a 429 stopping it, and a threshold that is not a
hard cut hiding the bands; the scope lens at N, 0, 0; the inspector's requests in order and its
words-in-common count; a 424 showing its `type` and detail and never "0 results"; the orphan
panel; Clean up, including that a listing that returns somebody else's memory makes it delete
none, and that Clean up again catches a late memory.

**All four together** (`test_integration.py`, the real `marimo export`, AUTORUN demo then
cleanup, one Redis database): no error output in any cell; every Redis write under
`irisnb_<run>_`; columns 0 and 2 on one `REDIS_URL` do not take each other's keys for another
app's; after cleanup no key, index, surface, cache entry, memory or session of the run is left
and the ledger shows nothing live; no secret of any column in a session file.

## What only a live run can show

Everything above runs against stand-ins. `verify.py --live` ran once, on 2026-09-30, and the
README's "Verified live" section lists what it settled, with the numbers. The stand-ins were
then made to answer the way the services did: `FakeSearch`, `FakeWorld` and `FakeIndexServer`
word a missing index as Redis 8.6.2 does (and, with `wording="older"`, the way earlier Search did),
`FakeWorld` serves the unified tool surface by default (`style="classic"` gives the older one) and
can drop its indexes with a deleted surface (`drops_indexes`), and `FakeLangCache` answers a
`DELETE` of an expired entry with `404 /errors/resource-not-found`. What is still open:

- **Redis Search.** `SVS-VAMANA` index creation and the `FT.INFO` numbers the second-index deep
  dive reads (the run stopped at that cell before the fix); the wording of a missing index from
  `FT.SEARCH`; whether `FT.CREATE ... TEMPORARY` is accepted on the free tier, which would let
  the index expire like its keys.
- **LangCache.** The exact 400 text for a scoped search on a cache without attributes, and a
  cache that defines a scope attribute at all; whether the expiry deep dive reads "gone" the
  moment `ttlMillis` is up (the run showed the 10 s entry gone by the time it was deleted, over
  11 s after it was stored).
- **Context Retriever.** The paging call with the condition the notebook now derives from the
  schema (the run sent none), and **Break it** with the argument it now prefers; a classic
  tool surface; **whether two runs' service-named indexes collide** (two presenters on one
  database), and so whether another run's index can be found and offered for dropping the way
  column 0's are, which today it cannot; whether a listing of surfaces includes each one's
  model; whether a free Essentials database, or a restricted data-access role, works for the
  service; and any account with more than 500 surfaces, whose listing the notebook cuts short.
  The console links use the root `https://cloud.redis.io` and a click path, because the deep
  links were not confirmed.
- **Agent Memory.** What the other two presets rank first (keep only the ones that rank the
  intended memory first); what the service does when `similarityThreshold` is left out; how it
  words its 424 and 429 errors; whether creating an existing id overwrites it; whether a
  session's events come back oldest first; whether deleting a session stops extraction that has
  not run yet; what extraction writes, and how soon (a session of one question produced nothing
  in 11 minutes; the kick-off conversation was not run); how a listing with only a filter pages
  past 100; and, for the deep dive, the layout of the database behind the service (the `id`
  field, the hash field names, the `FT.INFO` reply for the vector field), which the notebook
  takes from the demo repo's reconstruction of the schema and reads on a best-effort basis.
  The SDK snippet was run once by hand against `redis-agent-memory==0.4.1` with a mock
  transport and sent the request the notebook sends, except that the SDK leaves out
  `filterOp`; that check is not part of the suite, because the SDK is not a dependency.
- **The pages.** How the tables and diagrams look in the browser was checked by hand in
  `marimo edit` and `marimo run` at 1536 x 864 and 1920 x 1080 without credentials. The
  configured cells were not seen in a browser.
