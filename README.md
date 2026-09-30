# Redis Iris basics

A single marimo notebook that takes the Redis Iris context engine apart, one column per
component: **Redis Search** (the query engine underneath), **LangCache**, **Context
Retriever** and **Agent Memory**. It calls the real hosted services with your own credentials
and shows the exact HTTP JSON or `redis-cli` command behind every call, so you can see what an
agent would really send. There is no LLM and no OpenAI key anywhere.

Each column asks only for its own settings. Without them it shows a "not configured" note
(column 2 also has a data-model cell that works offline), so a column you have no service for
stays out of your way. Columns 1 to 3 add a panel, "Recorded live on 2026-09-30", with a few
real outputs of a real run (a cache HIT, the tools a data model became, a memory recalled by
meaning); the identifiers of the account it ran on are replaced by placeholders.

Docs: [Redis Iris context engine](https://redis.io/docs/latest/develop/ai/context-engine/) ·
[redis.io/iris](https://redis.io/iris/)

## Run it

Install [uv](https://docs.astral.sh/uv/) once.

```powershell
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```sh
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then, from this folder:

```sh
# Presenting: code visible
uvx marimo@0.25.0 edit --sandbox iris_basics.py

# Handing over: app mode, code hidden, runs by itself when opened
uvx marimo@0.25.0 run --sandbox iris_basics.py
```

- **`edit` opens with nothing run.** Press **Ctrl+Shift+R** (*Run all stale cells*, or the
  yellow play button at the bottom right) and wait about 15 seconds for the yellow marks to
  clear. A cell that says *Waiting for a connection or a button above* is waiting for a
  column's **Test connection**. A notebook cannot switch this on for itself: marimo ignores
  such a setting in a script's header.
- **One column fills the screen** in `edit` (about 1200 px wide). Zoom the browser out to
  67-75%, or drag a column's border, to see two.
- `--sandbox` makes marimo read the dependencies from the script header (`# /// script`) and
  build a private environment. Without a terminal it skips this silently, so keep the flag.
  The header pins Python below 3.14 because Context Retriever's `ContextModel` breaks on 3.14;
  if you launch some other way, use Python 3.13.
- The first launch downloads about 110 packages, and column 0 downloads a ~30 MB embedding
  model the first time it needs one. `verify.py --preflight` warms both before a meeting.
- In `edit`, marimo's browser console logs many "Language server initialization failed"
  errors. They come from marimo, not from this notebook; you can turn its language servers off
  in marimo's user settings if they distract you.
- Do not change the notebook's width setting in `marimo edit` and then save: marimo drops the
  column assignments when you do (marimo issue #3543). Edit the `.py` directly instead.

## Configuration

Put each column's settings in the field at the top of the column, or in a file. Console links
and the click path for every value are in each column and in `.env.example`.

| Column | Required | Optional | Where (https://cloud.redis.io) |
|---|---|---|---|
| 0 Search | `REDIS_URL`, or `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` (`REDIS_USERNAME`, `REDIS_SSL`, `REDIS_DB`) | | Databases → your database → Configuration → Public endpoint; Security → Default user → eye icon. Free 30 MB database: https://redis.io/try-free/ |
| 1 LangCache | `LANGCACHE_HOST`, `LANGCACHE_CACHE_ID`, `LANGCACHE_API_KEY` | `LANGCACHE_SCOPE_ATTR` | LangCache → your service → Configuration → Connectivity. The key is shown once; **never press Generate key on a shared cache**. Use a dedicated LangCache service for this notebook |
| 2 Context Retriever | `CTX_ADMIN_KEY`, `REDIS_URL` (reachable by the hosted service; Search and JSON) | `CTX_API_URL`, `CTX_MCP_URL` | Context Retriever → Admin keys → New admin key (shown once; a service must exist first) |
| 3 Agent Memory | `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, `MEMORY_API_KEY` | | Agent Memory → your service → Configuration → General settings; API keys tab |

The redis.io free tier has no TLS, so the URL is `redis://`, not `rediss://`. `REDIS_URL`
feeds column 0, column 2 and the "under the hood" look in column 3.

**Where the file is.** Either a `.env` next to `iris_basics.py` (copy `.env.example`), or any
file with the same names, such as another project's `.env`:

```powershell
$env:IRIS_BASICS_ENV_FILE='C:\path\to\redis-iris-demos\.env'   # PowerShell
```

```sh
IRIS_BASICS_ENV_FILE=/path/to/redis-iris-demos/.env            # sh
```

Use one or the other, not both. The process environment comes first, then the named file, but
marimo itself loads the `.env` beside the notebook into the environment before the notebook
starts, so with both, that `.env` wins. Only the names in the table are ever read. Comments in
a settings file must sit on their own lines, because the loaders disagree about an inline
`# note`. Saving a new `.env` needs a restart of `marimo run`; in `edit`, re-run the cell.

Two more settings are not credentials: `IRIS_BASICS_NO_DOTENV=1` stops the fallback to the
`.env` beside the notebook, and `IRIS_BASICS_LEDGER_DIR` moves the ledger of created resources
(default `%LOCALAPPDATA%\iris-basics`, else `~/.iris-basics`).

## Safety

Everything the notebook writes carries a run id (`<run>` below). Nothing is written until you
press a button that says what it will write and where. A ledger on disk records what each run
created, so Clean up still works after a kernel restart and touches nothing that is not yours.
Secrets are never prefilled into a field and are scrubbed from every trace and message. On a
shared service never use **Flush**, **Delete service** or **Generate key**; the notebook
refuses to.

| | Scope | Expires | Clean up | Never |
|---|---|---|---|---|
| **Redis** (columns 0 and 2) | keys start `irisnb_<run>_`; nothing else is written | keys after 1 h (column 2's after 2 h); indexes do not | unlinks the ledgered keys, drops the ledgered indexes; leftover *indexes* of other runs only after a typed confirmation, and once that run's keys are gone | `FLUSH*`, `CONFIG`, `DD`, a scan-and-delete, a write outside the prefix |
| **LangCache** | entries found by id in the ledger; tagged `iris-basics-<run>` only if your cache defines a scope attribute | every entry after 15 min (a taught answer 2 min, the expiry test 10 s) | delete by id; a `404` of type `resource-not-found` or `entry/not-found` counts as expired, while a `cache/not-found` or any other `404` is an error and the id stays in the ledger. A cache cannot be listed, so leftovers cannot be found: the expiry is the net | flush, delete by attribute, Generate key |
| **Context Retriever** | surface `iris-basics-<run>` stamped with the run; key templates under `irisnb_<run>_cr:`; a pool of 2 connections; an agent key | keys and the agent key after 2 h; the surface does not | deletes the surface, unlinks the keys, drops any index the service built and left (every prefix under the run; the service drops its own with the surface); other runs' stamped surfaces older than 2 h after a typed confirmation | `PUT`, Delete service, touching a surface that is not ledgered or confirmed |
| **Agent Memory** | owner `ib-<run>`, namespace `iris-basics-notebook`, ids `ib-<run>-...`; every session opens with a USER event carrying both | nothing (memories last a year) | deletes the ledgered sessions, then every memory listed under the owner, and none if one has another owner; **Clean up again** catches late extractions; other runs' memories older than 2 h after a typed confirmation | Flush, Delete service, `includeAll`, deleting an id that is neither ledgered nor owner-checked |

Every request to a hosted service also passes one checkpoint that refuses whatever is not tied
to this run. Redis Cloud cannot give the notebook a narrower Redis user, so on a shared
database those limits are the notebook's own: use a scratch database.

## What each column shows

Turn on **Presenter notes** for a *Say / Expect* line under each core cell. The core path of a
column takes about three minutes; the **Deep dive** switch adds the rest.

### 0 · Redis Search

The Redis Query Engine that the other three components are built on, taken apart on a
36-article help centre for a fictional outdoor retailer, Cairn Outfitters. Needs one Redis
database with Search (a free 30 MB one is plenty); the embedding model, `potion-base-8M`,
runs on your machine, so there is no API key.

**Core path:** Test connection (version, `FT.HYBRID` and `SVS-VAMANA` support, key and
connection counts, leftovers) → Load the help centre (one `FT.CREATE`, 36 `HSET`s, each with a
one-hour `EXPIRE`) → KNN or a radius → the wow, one query as keyword, vector and hybrid search.
Also: full-text with prefix and typo switches, a pre-filter by category and year, and a deep
dive that builds a second index (`FLAT`, `HNSW` or `SVS-VAMANA`) over the same documents.

**Talk track.** Keyword search matches words, vector search matches meaning, and each fails
where the other wins. *Trail shoes feel cramped* shares no word with the boot guide, so
keyword search returns nothing while the vector search ranks the boot guide first;
*Trailhead* is a product name, which keyword search finds and the vector search only guesses
at; a typo returns no keyword hits at all. Hybrid fuses the rankings and has the right article
first each time, though it is not magic: it rewards an article that both lists rank (try
*will it fit my feet* under *Type your own*). Then the bridge to column 1: KNN can never say
"nothing is close enough", only a radius can, and that empty answer is a cache MISS.

**Clean up** drops the ledgered indexes and unlinks the ledgered keys.

### 1 · LangCache

Redis's managed semantic cache for LLM answers, taken apart on gear-care answers for Cairn
Outfitters. Needs a LangCache service: a host, a Cache ID and an API key. **Use a service of
your own.** A cache is shared by everything that points at it, and the live Iris demo searches
its cache with no filter, so anything stored on a shared one can be served to somebody else's
app until it expires.

**Core path:** Test connection (one search for a made-up prompt; checks the key and Cache ID)
→ Store the gear-care answers (one `POST .../entries` each, with `ttlMillis`) → Threshold lab
→ the wow, a chat that asks the cache first. Also: anatomy of a hit, `searchStrategies` (exact,
semantic, both), and a deep dive on expiry.

**Talk track.** A cache can only say "close enough", and how close is a dial. The lab is that
dial: fourteen labelled questions, searched once each, then re-scored on your machine as you
slide the threshold. Raise it until the red rows (false hits: a wrong answer served) go, and
watch the amber ones (paraphrases turned away) multiply. That is the bridge from column 0: a
radius search that could return nothing, with a managed embedder. `exact` is the cheap, safe end
of the dial; only `semantic` can be wrong. The chat closes the loop with no model in it: a MISS
is where the LLM would run, so you play the model and *Teach it*; asked again in other words,
it is a HIT, and the taught entry is deleted straight away. With no scope attribute a red note
says a taught answer is visible to other apps for 120 s.

**Clean up** deletes, by id, every entry the ledger names.

### 2 · Context Retriever

A data model turned into governed MCP tools, taken apart on Cairn Outfitters: three Python
classes (`Customer`, `Product`, `Order`) and 20 records. The heaviest setup of the four. Needs
a Context Retriever **admin key**, and a **Redis database with Search and JSON that the hosted
service can reach over the internet**, because the service, not this notebook, writes the data
and builds the indexes. It is therefore given the database's password: use a scratch database.
"The agent" is a form generated from each tool's own `inputSchema`.

**Core path:** change a field's index in the data model (offline, no credentials) → Create the
surface and load the data (one button that says it sends the database password) → the wow, the
tool explorer, with **Break it**. Also: Test connection, where the data went (`JSON.GET` and
`FT.INFO` on your own database), paging and provenance, and a deep dive with the tool catalogue.

**Talk track.** An agent never writes a query: it calls the tools a data model makes. The
prediction names the tools the service served on 2026-09-30 (`filter_customer`,
`count_customer`, set tools) and says which fields each takes. Set `Order.status` to *not
indexed* and `status` drops out of what `filter_order` accepts; set `Customer.name` to `tag` and
`search_customer_by_text` disappears while `filter_customer` accepts `name`. A tag with
`allowed_values` becomes a fixed menu, and the service enforces it: **Break it** comes back as a
`-32602` rejection before anything reaches Redis. The records are JSON documents in *your*
database, which the service wrote and indexed. The tool set depends on your model *and* the
service's version (an older service names the tools the classic way, such as
`filter_customer_by_tier`, and the diff shows it); the explorer and the paging cell work for
both because they are built from each tool's schema, not from a list of names.

**Clean up** deletes the surface, unlinks the keys and drops the indexes the service built.

### 3 · Agent Memory

The hosted Redis Agent Memory service, taken apart on a made-up Cairn customer, Dana. Needs an
Agent Memory service in Redis Cloud (endpoint, store ID and API key) and no LLM key: the service
embeds and extracts with Redis's own models unless you brought your own. Every call is plain
HTTP, shown as the exact JSON sent and received.

**Core path:** Test connection → **Start extraction now** (optional; extraction takes minutes,
so press it before you present the other columns) → Write long-term memories → Sweep (one
search at 11 thresholds, because the API returns no score) → the wow, a memory inspector. Also:
the scope lens (N, 0, 0), and a deep dive on session memory, checking for extracted memories,
and a read-only look inside the database behind the service.

**Talk track.** Agent memory is two tiers. Short-term memory is the ordered events of the
conversation in progress. Long-term memory is facts recalled by meaning, across sessions. Send
*What should the kitchen avoid serving me?*: not one word of it is in the peanut-allergy memory
(the count says 0), so a keyword index would return nothing; the service gets there by meaning.
Press **New session** and send it again: the events reset and the memory does not. Then the
lens: a similarity threshold is a cut, this API will not say how close a match is, so the
sweep works it out (the slider starts at 0.3, where the allergy memory stays and the rest go)
and **Re-query live** tests the assumption. The scope lens is why every call
carries an owner and a namespace on a store others share. Last, extraction: the service also
writes memories from your sessions on its own, minutes later, with ids it chose.

**Clean up** deletes the sessions and every memory under the run's owner; **Clean up again**,
about ten minutes later, catches what extraction added since.

## Verified live on 2026-09-30

`verify.py --live` ran the whole notebook (AUTORUN demo, then cleanup) against real services: a
Redis Cloud database on Redis 8.6.2, LangCache, Context Retriever and Agent Memory. The first run
found the problems listed below; after the fixes a second run passed all 28 of its steps. What
the runs showed, with the numbers:

- **Redis Search.** Redis 8.6.2 with Search; `FT.HYBRID` (8.4+) and `SVS-VAMANA` (8.2+) both
  available. 36 hashes indexed (256 dimensions, `COSINE`, `HNSW`). *Trail shoes feel cramped*:
  `BM25STD` returned no row, the vector search ranked the boot guide #1 of 10, and `FT.HYBRID`
  with `RRF` kept it #1. Redis words a missing index `SEARCH_INDEX_NOT_FOUND Index not found:
  <name>` (seen on `FT.INFO` and `FT.DROPINDEX`), not `Unknown index name`.
- **LangCache.** A per-request `similarityThreshold` of 0.50 was honoured under the cache's 0.85
  default: the least similar entry that came back scored 0.659. `maxResults` 5 returned five
  entries, best first. The anatomy cell's rain-jacket paraphrase scored 0.896 and the taught
  entry's paraphrase 0.894; the lab's paraphrases scored 0.812 to 0.949. In the lab the highest
  false hit scored 0.950, so from 0.96 up there are none, and by then only 1 of the 6
  paraphrases still hits. `exact` reports a similarity of 1. The taught answer was a HIT at
  0.894 and was deleted straight after. Another app's entry near the test prompt came back as a
  score only and stayed hidden. The lab and the strategies table met no rate limit. `DELETE`
  of an entry that has expired answers `404` with type
  `/errors/resource-not-found` (the API docs say `/errors/cache/entry/not-found`); both count as
  expired.
- **Context Retriever.** This account serves the **unified** tool surface: 21 tools for the
  three-entity model, `get_`, `list_`, `count_`, `summarize_` and `filter_<entity>`,
  `search_<entity>_by_text` for the two entities with a text field, and `intersect_results`,
  `union_results`, `except_results` and `expand_results`. Create answered `201` with the surface
  in status `provisioning_indices`, the agent-key listing named the MCP address, and 20 records
  (6 Customer, 6 Product, 8 Order) imported. The service built three indexes, named
  `idx:<surface id>:<hash>:<entity>`, and **dropped them together with the surface**; the data
  stays until it is unlinked (20 of 20 keys were). `get_customer_by_id` with `C001` returned the
  record plus a `_retrieval` stamp (`source`, `retrieved_at`, `source_version`). A `filter_`
  tool called with no condition answers `isError`: *at least one condition is required*.
- **Agent Memory.** The store reported healthy. Five memories were written and listed at once.
  *What should the kitchen avoid serving me?* shares no word with the peanut-allergy memory, and
  that memory came back **#1 of 3** at `similarityThreshold` 0.0. The reply carries no score.
  The sweep put that memory in the band 0.3 to 0.4 and the other four in 0.0 to 0.3, so the
  slider starts at 0.3; at 0.5 nothing comes back, and a live re-query agreed with the table.
  The scope lens found 5, 0 and 0. A second Clean up, at least 11 minutes after the run's last
  event (one question in one session; the extraction kick-off was not pressed), found no memory
  that extraction had made.
- **Secrets.** The scan found none of the six secrets in any spelling in the HTML exports and the
  session files.

**What it found, now fixed:** the new Redis wording of a missing index stopped the deep dive
and two cleanups (the notebook now knows both wordings, and an index that is already gone is
counted apart and never stops a cleanup); Clean up trusted the wrong `404` type for an expired LangCache
entry; the Context Retriever paging call sent no condition; column 2's prediction used classic
names, so it now follows the unified ones; and the Agent Memory slider started at a value where
nothing is returned.

**Footprint after Clean up (second run):** empty. No key or index under the run's prefix, no new
index anywhere, nothing live in the ledger, no memory or session for the run's owner, and no
surface of the run (its id also answers `404` when fetched directly, which does not depend on
the surface listing, capped at 500). The notebook's own Clean up cells removed everything, so
`verify.py`'s safety-net sweep had nothing to do. A LangCache entry stored for 5 s was listed,
then gone after about 6 s, and its delete answered `404`. The first run had left 36 keys of
column 0 (removed by hand, all under the run's prefix and all carrying a TTL), one expired
LangCache entry and three indexes the service had already dropped, for the reasons above.

**Not exercised live:** the orphan panels and their typed-confirmation deletes; the **Reset**
buttons; column 3's **Start extraction now**, **Check for extracted memories**, **New session**
and "under the hood" look, and the second and third question of its inspector; `SVS-VAMANA`
index creation (the deep dive's second index ran as `FLAT` and found the same nearest article,
0.369, as `HNSW`), and column 0's wow questions after the first; a LangCache scope attribute, and
the end of its expiry deep dive (the entry stored for 10 s was gone when it was deleted more than
11 s later, which is all the run showed); a Context Retriever surface on the classic tool surface,
two runs on one database, and a restricted Redis user. The list of what the tests can and cannot
show is in
[tests/README.md](tests/README.md).

## Before the meeting

- [ ] Settings in ONE place: a `.env` beside `iris_basics.py`, or
      `$env:IRIS_BASICS_ENV_FILE='C:\path\to\redis-iris-demos\.env'` (PowerShell) /
      `IRIS_BASICS_ENV_FILE=...` (sh). Not both: the `.env` beside the notebook wins.
- [ ] `uv run --no-project --python 3.13 verify.py --preflight --env-file <file>` says GO. It
      warms uv and the ~30 MB model and runs each column's read-only connection test.
- [ ] Use your own LangCache, Context Retriever and Agent Memory services and a scratch database.
- [ ] Do NOT run redis-iris-demos `make setup` or `make reset` during or just before the
      meeting: they flush the shared cache and store you are demoing against.
- [ ] Launch `uvx marimo@0.25.0 edit --sandbox iris_basics.py`, press **Ctrl+Shift+R**, zoom the
      browser to 67-75%, press **Test connection** in every column, and leave the tab open.
- [ ] Turn **Presenter notes** on. No red AUTORUN banner.
- [ ] Column 3: press **Start extraction now** straight after its Test connection.
- [ ] Do not restart the kernel or the server mid-session: a new run id cannot clean up the old
      run, and its surfaces and memories become offered leftovers only after 2 h.
- [ ] Afterwards: **Clean up** in every column, and **Clean up again** in column 3 about ten
      minutes later.
- [ ] Before handing the folder over: delete `__marimo__/` and `.env`.

## Handing it to a customer

- Delete `__marimo__/` (it holds outputs, stdout and tracebacks) and `.env`.
- Send `iris_basics.py`, `.env.example` and this README. Give them their own LangCache,
  Context Retriever and Agent Memory services rather than yours.
- They run `uvx marimo@0.25.0 run --sandbox iris_basics.py`, or `edit` to read the code.

## Tests

```sh
uv run --no-project --python 3.13 verify.py
```

Offline and credential-free: `marimo check --strict`, the pytest suite in `tests/`, and a
headless `marimo export html` that must exit 0. `verify.py --preflight --env-file <file>` is
the go / no-go before a meeting (above). What the tests prove, what only a live run can show,
and how `verify.py --live` and the test hooks work is in [tests/README.md](tests/README.md).
