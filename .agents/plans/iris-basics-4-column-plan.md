# Redis Iris basics — 4-column marimo notebook

**Build plan, rev 2 (2026-09-29).** This revision incorporates the 3-lens plan critique in
`research/plan-critique.json`, which found 58 issues (5 of them blockers, all fixed below; see
§9 for what changed). It supersedes everything in `archive/`.

| | |
|---|---|
| **Deliverable** | `redis-iris-basics/iris_basics.py`: one self-contained marimo notebook, `width="columns"`, one column per Iris component, plus `README.md`, `.env.example`, `.gitignore`, `verify.py`, `tests/` |
| **Columns** | 0 · Redis Search  ·  1 · LangCache  ·  2 · Context Retriever  ·  3 · Agent Memory |
| **Per column** | title cell (markdown + docs link + mermaid) → config cell (inputs + Redis Cloud link for every secret) → 2–3 core bite-size parameter cells → a "wow" cell → cleanup cell; extra cells behind a per-column **Deep dive** switch |
| **Launch** | `uvx marimo@0.25.0 edit --sandbox iris_basics.py` (presenting with code); `uvx marimo@0.25.0 run --sandbox iris_basics.py` (app mode, code hidden) |
| **Stack (pinned)** | `marimo==0.25.0`, `redis==8.1.0`, `numpy==2.4.6`, `model2vec==0.9.0`, `huggingface-hub==1.33.0`, `httpx==0.28.1`, `redis-context-retriever==0.0.6`. Every dependency is pinned exactly (rev 2.1), to the set the research validated, so a customer's fresh install cannot drift onto versions nobody ran; `test_header_pins_the_stack` asserts all seven. No altair unless a chart earns it. `requires-python = ">=3.11,<3.14"` |
| **LLM** | None. No OpenAI key anywhere |
| **Research** | `research/*.md` (verified API shapes). **In each file the "Verifier corrections" section overrides the body.** Implementers code from these, not from memory |

---

## 0. Where we were, and what changed

- `legacy/notebook.py` is the old 3-act, one-credential, raw-redis-py design. It was never run
  live and research found real defects in it (`legacy/README-legacy.md`). The pieces worth reusing
  are ported below, with those defects fixed.
- The three `redis-marimo-demo-plan-*.md` files (now `archive/`) described a single-component
  "Agent Memory internals" notebook with no hosted services. That doesn't fit this brief, so they
  were archived; the harvest is in §8.
- **This design calls the real hosted Iris services.** Each column asks for its own secrets and
  shows where in Redis Cloud to get them. The same env-var names as `redis-iris-demos` are used,
  and `IRIS_BASICS_ENV_FILE` can point at that repo's `.env`, so nothing is copied.

| Criterion | How | Honest gap |
|---|---|---|
| Hand a customer a notebook | One `.py`, PEP 723 deps, one `uvx` command. Hosted columns without credentials show a clearly labelled **"Recorded live on <date>"** panel of real outputs (captured in Phase 5, not a simulator) | First launch downloads ~110 wheels plus a ~30 MB model; `verify.py --preflight` warms both |
| Teaches architecture | Mermaid diagram per column. A wire view (exact HTTP JSON or redis-cli argv) behind every call. "Where did my data go" peeks into the customer's own Redis. Sweeps rather than single shots | Hosted internals are only visible where they land in Redis |
| Near-zero setup | Column 0 needs one free DB. Hosted columns need 2–3 console values each. Every column's offline cells run with no credentials | Context Retriever is heaviest: admin key + a DB the hosted service can reach |

---

## 1. Hard facts the build must respect (verified in research or critique)

1. **`mo.ui.chat` is broken in marimo 0.25.0.** Build chat from `mo.ui.text`, `mo.ui.run_button(label="Send")`
   and a history list held in a cell that never re-runs, with `if send.value: history.append(q.value)`.
   Never use `.form(clear_on_submit=True)`: it drops a repeated identical question.
2. **redis-py 8.1.0 defaults to RESP3.** Connect with `protocol=2, decode_responses=False`.
   `FT.INFO` `index_definition` then comes back as a flat list of **bytes**, so decode it explicitly.
3. **Free tier:** 30 MB, 30 connections, 100 ops/s (shared with anything else on the DB), and no TLS
   (`redis://`). Search is built in on 8.x, so never tell users to recreate the DB. Feature-detect
   `FT.HYBRID` (≥ 8.4) and `SVS-VAMANA` (≥ 8.2).
4. **marimo 0.25.0 auto-loads `.env` next to the notebook** before setup runs, and `mo.notebook_dir()`
   may return `None`. **In `marimo edit`, every global's value (UI element values included,
   truncated to 50 characters) is sent to the browser's Variables panel.**
5. `marimo edit/run/export` write `__marimo__/session/*.json` next to the notebook, containing
   outputs, stdout and tracebacks.
6. Top-level `await` works; `asyncio.run()` in a cell raises.
7. A cell whose ancestor raised or called `mo.stop` is cancelled. **Anything that must always run
   (cleanup) must not depend on gated cells.**
8. `@app.function(column=N, hide_code=True)` is valid, passes `check --strict`, can be imported from
   the module, and can call other `@app.function`s. `check --strict` does **not** flag an undefined
   global inside one. One-line `mo.md("...")` fails `--strict` (MF007); use triple-quoted `mo.md(r"""...""")`.
9. `ContextModel` breaks on Python 3.14, and uv's default Python on this machine is 3.14.6. Pin
   `--python 3.13` everywhere.
10. Hosted **Agent Memory search returns no score**. Background extraction runs on **every** session
    with events, minutes later. A session's `namespace` comes only from its first event, and
    namespace `eq` never matches a record with no namespace.
11. **LangCache is shared with the live Iris demo**, which searches with no attributes at 0.82 and
    no expiry by default. `maxResults` defaults to 1, so an unscoped search can return the demo's
    entries. Console **Generate key** kills the old key. The demo's own `make setup/reset` flushes
    the cache.
12. **Context Retriever:** tool names come from `ContextModel.__name__`, and the tool surface may be
    classic or unified, so read `inputSchema`. The service writes RedisJSON and creates indexes under
    names it chooses. Deleting a surface does not delete data or (probably) indexes. The
    create-surface **body contains the DB password**, and the create-agent-key **response contains the
    agent key**.
13. model2vec's `force_download=False` does nothing. Load with `huggingface_hub.snapshot_download(
    allow_patterns=["config.json","model.safetensors","tokenizer.json","README.md"])`, trying
    `local_files_only=True` first.

---

## 2. File layout

```
redis-iris-basics/
  iris_basics.py        the notebook (single file)
  README.md             run it · before the meeting · what each column shows · safety · handing it to a customer
  .env.example          every allowlisted var, commented out; comments on their own lines
  .gitignore            .env, __marimo__/, __pycache__/, *.html, .agents/build/
  verify.py             offline (default) · --preflight · --live   (Python; no bash; Windows-first)
  tests/
    conftest.py         imports the module named by IRIS_NB_MODULE (default iris_basics) from IRIS_NB_PATH;
                        clears allowlisted env vars and IRIS_BASICS_*; points IRIS_BASICS_ENV_FILE at an
                        empty file; installs an autouse transport that raises on any real host
    test_shared.py  test_verify.py  test_redis_search.py  test_langcache.py  test_context_retriever.py  test_agent_memory.py
  legacy/  .agents/plans/  .agents/build/ (Phase 3–4 only)
```

---

## 3. Notebook architecture (the contract every phase follows)

### 3.1 Layout and presenting mode

- `marimo.App(width="columns", app_title="Redis Iris basics")`. **Column indices: 0 Search,
  1 LangCache, 2 Context Retriever, 3 Agent Memory.** Every `@app.cell` / `@app.function` carries an
  explicit `column=N`, and each column's items are contiguous. Order within a column: title →
  config → core cells → wow → cleanup → deep-dive cells → that column's `@app.function` helpers
  (all `hide_code=True`) at the bottom.
- `with app.setup(hide_code=True):`.
- **Edit mode is the presenting mode** (the brief wants visible code). It fits about 2 columns on
  screen with sideways scrolling. Run mode is for handing over (code hidden), with
  `--content-width` set to ~560 px via inline `<style>` in the intro cell if that works in a
  browser, or else via `css_file`.
- Wide content (comparisons, side panels) **stacks vertically or uses `mo.ui.tabs`**. No
  side-by-side tables inside a column.
- The top of column 0 is the **intro**: what Redis Iris is, the four columns with anchor links,
  launch and safety notes, a **Presenter notes** switch (`ib_notes`) and a red banner when AUTORUN
  is on. Intro globals use the `ib_` prefix.

### 3.2 Cell recipe

1. **Title** (`hide_code=True`): `# N · Component`, 3–6 sentences, **docs link**, a quick-create
   link, and a small `mo.mermaid` diagram.
2. **Config**: input fields for **non-secret** values, prefilled from env. **Secret fields are never
   prefilled.** They stay empty with a placeholder like `using $LANGCACHE_API_KEY (••••1234)` and are
   resolved as `field.value.strip() or env(NAME)` inside helpers at call time. Include a "Where do I
   get these?" block (Redis Cloud console link and click path), a "Never on a shared service:
   Flush · Delete service · Generate key" callout, a read-only **Test connection** button, and an
   **Orphans** sub-panel (see §3.6).
3. **Core bite-size cells (2–3)**: each is a *controls cell* plus a *result cell*. A `###` heading,
   one idea, controls that change a real parameter, the result, and a collapsed wire view.
4. **Wow**: one interactive payoff.
5. **Cleanup** (always available; see §3.6).
6. **Deep dive** (behind the column's `*_deep` switch, gated with `mo.stop(not deep.value, None)`):
   extra bite-size cells.

**Reactivity rules:**
- (a) Every control that feeds a hosted or Redis call uses `debounce=True` or sits behind a
  run_button.
- (b) Index builds and **all writes** sit behind a run_button.
- (c) Split *fetch* (gated, stores the result) from *render* (reacts only to display controls).
- (d) Re-score client-side wherever possible (the "sweep once, slide locally" pattern).
- (e) If the config is missing, **only the config cell** renders `not_configured`. Downstream cells
  use `mo.stop(..., None)`, or show the column's "Recorded live" panel.
- (f) When `ib_notes.value` is on, show a one-line `Say: … / Expect: …` callout under each core cell.

### 3.3 Naming contract

- Column prefixes for every global (UI, results, helpers): **`rs_` `lc_` `cr_` `am_`**. Intro uses
  `ib_`. Locals use `_`.
- **Exception:** Context Retriever entity classes must be named `Customer`/`Product`/`Order`,
  because tool names come from `__name__`. Define them inside
  `@app.function def cr_build_models(key_prefix, index_overrides=None) -> dict[str, type]`, which
  imports `context_surfaces` locally. Cells import it as `import context_surfaces as cr_cs`.
- The setup block defines the only unprefixed names and is **frozen after Phase 2** (after an
  Opus review). Column phases report needed changes as findings instead of editing it.
- `@app.function` helpers may reference only setup names, other `@app.function`s and builtins.
  `test_shared.py` enforces this via `co_names`.

### 3.4 Setup block (Phase 1; shared)

**Identity and test hook**
- `RUN`: `IRIS_BASICS_RUN` if it matches `^[0-9a-f]{8}$`, else `uuid4().hex[:8]`.
- `PREFIX = f"irisnb_{RUN}_"`; sub-prefixes `RS_PREFIX = PREFIX+"rs:"`, `CR_PREFIX = PREFIX+"cr:"`.
- `KEY_TTL_S = 3600`; `LC_TTL_MS = 900_000` (15 min); `LC_TEACH_TTL_MS = 120_000`.
- `AUTORUN` is `"demo"` or `"cleanup"`, and is honoured **only** when `IRIS_BASICS_AUTORUN` is one
  of those **and** `IRIS_BASICS_RUN` is set and valid; otherwise it is `""`. Both come from the
  real environment only (ignored if the `.env` marimo loads names them), and neither is honoured
  in `marimo edit`. marimo reports mode `edit` for `marimo export` too, so an export must also
  set `IRIS_BASICS_HEADLESS=1`, a test hook that presenters never use (rev 2.2).
- `pressed(btn, stage="demo") -> bool` returns `btn.value or (AUTORUN == stage)`. Buttons that must
  never autorun (orphan delete, reclaim, extraction kick-off, New session) use plain `btn.value`,
  and a test asserts this. Every chat or multi-step cell defines a scripted AUTORUN sequence from
  its presets. **Every text input defaults to a non-empty preset.**

**Config and secrets**
- `ENV_ALLOWLIST`: `REDIS_URL, REDIS_HOST, REDIS_PORT, REDIS_USERNAME, REDIS_PASSWORD, REDIS_SSL,
  REDIS_DB, LANGCACHE_HOST, LANGCACHE_CACHE_ID, LANGCACHE_API_KEY, LANGCACHE_SCOPE_ATTR,
  CTX_ADMIN_KEY, CTX_API_URL, CTX_MCP_URL, MEMORY_API_BASE_URL, MEMORY_STORE_ID, MEMORY_API_KEY`.
  Never read `CTX_SURFACE_ID`, `MCP_AGENT_KEY`, `MEMORY_OWNER_ID`, `MEMORY_NAMESPACE`, `OPENAI_*`
  or `DEMO_*`.
- `env(name)` checks `os.environ` first (non-empty only). If absent, it reads a private dict parsed
  from `IRIS_BASICS_ENV_FILE`, falling back to `(mo.notebook_dir() or Path.cwd())/.env` unless
  `IRIS_BASICS_NO_DOTENV=1`. The parser strips whitespace, `\r` and surrounding quotes, and treats
  `""` as unset. It **does not write to `os.environ`**, and it serves allowlisted names only.
- `redis_url_from_env()` returns `REDIS_URL`, or composes one from the split fields with
  percent-encoded userinfo.
- `Secret`: a non-`str` holder with a masked `repr`/`str` (`••••1234`) and `.reveal()`. It is used
  in config objects so the Variables panel never shows a secret. `validate_secret(name, value)`
  rejects whitespace and control characters without printing the value.
- `SECRETS` is a runtime registry of every secret value seen: env secrets, the unquoted URL
  password, minted agent keys. `scrub(text)` replaces each one, in raw, percent-encoded,
  JSON-escaped and HTML-escaped forms, with `****`. It also masks regex patterns (`c[se]_(admin|agent)_\w+`,
  `Bearer \S+`, `rediss?://[^:@/\s]*:[^@\s]+@`, whose username may be empty: `redis://:password@host`).

**HTTP**
- `HTTP_CLIENT` is a monkeypatchable `httpx.Client`.
- `http_call(component, method, url, *, headers=None, json=None, params=None, timeout=10.0,
  expect=(200,201,204)) -> (status, body)`. It uses `client.request(...)`, which works for
  DELETE with a body. It catches every exception and raises `ServiceError(component, status,
  kind, detail)`, whose message has passed through `scrub()`.
  - It **recursively redacts** request and response JSON by key name (`password`, `pass`, `key`,
    `api_key`, `apiKey`, `token`, `secret`, `authorization`, and all of `connection_config`
    except `addr`) before anything is stored.
  - It records `(component, method, url, redacted request, status, ms, redacted response summary)`
    in `HTTP_TRACE`.
  - `as_curl(entry)` uses `$ENV_VAR` placeholders.
  - **Choke points:** it sends only GET, POST, DELETE and PUT; any other verb is refused. It
    refuses `/flush` on every component, judging the path as httpx will send it (percent-decoded,
    dot segments resolved, `;parameters` dropped from each segment): any segment named `flush` is
    refused. It refuses LangCache `DELETE .../entries` unless the attributes
    equal `{scope_attr: f"iris-basics-{RUN}"}`. It refuses Agent Memory
    `DELETE /long-term-memory` unless every id is in the ledger or ownership-verified, and
    `DELETE /session-memory/{id}` unless the id is in the ledger. It refuses Context Retriever
    `DELETE /context-surfaces/{id}` and any `PUT` unless the id is in the ledger or in a
    user-confirmed orphan set.
- Timeout classes: reads about 10 s; create surface or import about 30 s. `with_spinner(service, fn)`.

**Redis**
- `connect_redis(url)` sets `protocol=2, decode_responses=False, socket_timeout=10`, registers the
  password in `SECRETS`, and scrubs errors.
- `GuardedTracedRedis`:
  - **Writes** (`HSET, EXPIRE, UNLINK, DEL, JSON.SET, FT.CREATE, FT.DROPINDEX`) must target keys or
    index names under `PREFIX`, with every key checked on multi-key commands.
  - `FT.CREATE` must contain `PREFIX n p1..pn` with n ≥ 1, and every p must be non-empty and under
    `PREFIX`.
  - `FT.DROPINDEX … DD` is **always denied**.
  - `FT.DROPINDEX` of a name outside `PREFIX` is allowed only for names in `OWNED_INDEXES`.
  - `pipeline()` and `transaction()` raise `Denied`, and so do the pipelines of redis-py's module
    clients (`ft()`, `json()`, `ts()`, which build them straight from the connection pool) and
    `ft().batch_indexer()`. `test_shared.py` checks every module accessor redis-py offers.
  - **Reads** come from an allowlist (`PING INFO DBSIZE SCAN TYPE TTL EXISTS HGETALL HMGET HSTRLEN
    MEMORY OBJECT JSON.GET FT._LIST FT.INFO FT.SEARCH FT.AGGREGATE FT.HYBRID FT.PROFILE
    FT.EXPLAINCLI`) and may touch any key.
  - `REDIS_TRACE` records every call; denials show about 0 ms.
- `owned_index(info, sub_prefix) -> bool` decodes bytes and requires a non-empty prefix list with
  **every** prefix under `sub_prefix`. It **fails closed**. Only this function can add to
  `OWNED_INDEXES`.
- `_cli(argv)` renders argv with vectors elided as `<N bytes>`. `render_redis_trace(n)`.
- Writes are paced (≤ 20 ops/s). `SCAN` uses `COUNT 1000`. Expire or delete **by name from the
  ledger**; `SCAN` is only for reclaim and footprint checks.

**Ledger (survives kernel restarts; how verify.py finds things)**
- Path: `IRIS_BASICS_LEDGER_DIR` or `%LOCALAPPDATA%/iris-basics` (or `~/.iris-basics`), file
  `{RUN}.jsonl`.
- `ledger_add(column, kind, ident, state)` appends a line, where `state` is `"intent"` before a
  create (so a timed-out create can be found by name), `"created"` after, and `"deleted"` on removal.
- `ledger_live(column, kind)` returns the created-but-not-deleted ids. `ledger_runs()` lists all
  RUN files. The ledger never stores secrets.

**UI**
- `wire_view(title, payload)`, `status_callout`, `not_configured(component, missing)`,
  `notes(say, expect)` (renders only when the caller passes `ib_notes.value`), `recorded_live(component)`
  (the static Phase 5 capture; empty until then).

### 3.5 Configuration contract

| Column | Required | Optional | Where (https://cloud.redis.io) |
|---|---|---|---|
| Search | `REDIS_URL` or `REDIS_HOST/PORT/PASSWORD[/USERNAME/SSL/DB]` | — | Databases → DB → Configuration → Public endpoint · Security → Default user → eye icon. Free: https://redis.io/try-free/ |
| LangCache | `LANGCACHE_HOST`, `LANGCACHE_CACHE_ID`, `LANGCACHE_API_KEY` | `LANGCACHE_SCOPE_ATTR` | LangCache → service → Configuration → Connectivity. The key is shown once; never press Generate key on a shared cache. **README recommends a dedicated LangCache service for the notebook** |
| Context Retriever | `CTX_ADMIN_KEY`, `REDIS_URL` (reachable by the hosted service; Search + JSON) | `CTX_API_URL`, `CTX_MCP_URL` | Context Retriever → Admin keys → New admin key (shown once; the service must exist first) |
| Agent Memory | `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, `MEMORY_API_KEY` | — | Agent Memory → service → Configuration → General settings · API keys tab |

Exact console paths are in each research file's *Secrets* section.

### 3.6 Safety model

**Cleanup is independent of everything else.** Each column's cleanup cell depends only on that
column's config, the ledger (read from disk at click time) and its own button, which uses
`pressed(btn, stage="cleanup")`. It never depends on a gated cell. A test stops or raises every
non-cleanup cell and asserts cleanup still renders and runs. Each column also has a **Reset** that
runs cleanup, clears its in-memory state and bumps a `*_gen` token.

| Resource | Scope | Expiry | Cleanup | Orphans (other RUNs; explicit click only, plain `btn.value`) | Never |
|---|---|---|---|---|---|
| Search keys + indexes | `RS_PREFIX` | EXPIRE on every key | from the ledger: `UNLINK` keys, `FT.DROPINDEX` (no DD), tolerating "Unknown index" | `SCAN irisnb_*` (count by RUN, TTLs); indexes that are `owned_index(…, "irisnb_")` for other RUNs | FLUSH*, CONFIG, writes outside PREFIX, anything under `memory:*` |
| LangCache | Cairn prompts; `iris-basics-{RUN}` attribute tag if `LANGCACHE_SCOPE_ATTR` is set | `ttlMillis` on **every** set (15 min; taught 2 min) | delete-by-id from the ledger (404 = expired) | cannot be enumerated; say so and rely on TTL | flush; delete-by-attributes except for the run's own tag |
| Agent Memory | `ownerId ib-{RUN}` and namespace `iris-basics-notebook` on every call. **Every session's first event is USER with `actorId ib-{RUN}` and the namespace** (a helper enforces it) | none server-side | delete tracked sessions → list LTM filtered on `ownerId` only → **client-side check that every item's ownerId is `ib-{RUN}` (abort if any is not)** → bulk delete. Then tell the presenter extraction may add memories for about 10 min; **Re-sweep** catches them | namespace sweep listing, keeping items whose ownerId starts `ib-` and isn't the current RUN, deleted after a typed confirm | `includeAll`; deleting any id not ledgered or ownership-verified |
| Context Retriever | surface `iris-basics-{RUN}`, `metadata {created_by, run}`; key templates asserted under `CR_PREFIX`; `pool_size 2, min_idle_conns 0`; agent key `expires_at` now + 2 h | EXPIRE on CR keys by name after import | `DELETE` the surface → UNLINK keys by name → drop indexes that are **new since the pre-create `FT._LIST` snapshot** and pass `owned_index(…, CR_PREFIX)` → report any new index left behind | surfaces with `metadata.run != RUN` older than 2 h, deleted after a typed confirm | PUT; touching any surface not ledgered or confirmed |

Consent: each column's first write is a run_button whose label says what will be written and
where. For Context Retriever, the consent text also says the DB password is sent to the hosted
service. The config shows a warning when the user is `default` on a DB that also hosts other apps.

### 3.7 Verification

- **`verify.py` (offline, default).** Works on a temp copy in an empty directory, with allowlisted
  and `IRIS_BASICS_*` vars cleared and `IRIS_BASICS_NO_DOTENV=1`. It uses
  `uv run --no-project --python 3.13 --with-requirements iris_basics.py --with pytest …` (Phase 1
  confirms the flag works on a PEP 723 script and asserts Python < 3.14). Steps:
  1. `marimo check --strict`
  2. pytest
  3. `marimo -y export html --no-sandbox`, which must exit 0 with every column in its
     not-configured state
- **`verify.py --preflight --env-file P`.** Warms the sandbox env and the model cache, runs every
  read-only Test connection, and prints a go/no-go table.
- **`verify.py --live --env-file P`** (Phase 5).
  1. Sets `IRIS_BASICS_RUN=<new>`, `IRIS_BASICS_ENV_FILE=P` and `IRIS_BASICS_HEADLESS=1` in the
     child process only; values are never printed. Without HEADLESS the export's AUTORUN is
     refused (marimo calls an export `edit` mode).
  2. Export with `IRIS_BASICS_AUTORUN=demo` (demo cells run), then export again with
     `AUTORUN=cleanup` (cleanup cells run, reading the ledger from disk).
  3. In a `finally`, sweep anything the ledger still shows as live, using the module's own cleanup
     `@app.function`s.
  4. Set a LangCache TTL sentinel (`ttlMillis=5000`, not ledgered). Poll up to 60 s and fail if it
     survives, then delete it by id.
  5. Wait one extraction cadence (about 11 min), then re-sweep Agent Memory.
  6. **Footprint report:** `SCAN irisnb_{RUN}_*`, remaining ledger items, Agent Memory listing for
     `ib-{RUN}`, surfaces named `iris-basics-{RUN}`, and new FT indexes. All must be empty.
  7. **Secret scan:** JSON-decode the export HTML and the `__marimo__/session` JSON, then scan for
     every secret (raw, JSON-, HTML- and percent-escaped forms), minted agent keys (the child writes
     them to a temp file) and the §3.4 regexes. Any hit fails.
  8. Delete the temp directory.
- **Browser check (Phase 4).** A Playwright screenshot of `marimo edit` and `marimo run` at 1536×864.

---

## 4. Shared content decisions

- **One fictional company:** *Cairn Outfitters*, an outdoor-gear retailer. Its content (help
  articles, gear-care Q&A, customers and orders, a hiker's preferences) sits far from every Iris
  demo domain (bank, food, electronics, healthcare, telco, betting, finance).
- **Not a performance demo.** No timing charts and no invented LLM-latency numbers. Latency appears
  only as an incidental trace column and a cache-ms badge.
- Core path per column is about 3 minutes (title → config → 1–2 core cells → wow); deep dives are
  optional. Presenter notes (§3.2f) carry the talk track.

---

## 5. Column specs

### Column 0 · Redis Search (`rs_`)

- **Title:** Redis Query Engine (full-text, vector, hybrid and aggregations over data you already
  store). Docs https://redis.io/docs/latest/develop/ai/search-and-query/ . Mermaid: app → local
  embed → `HSET` → `FT` index watching a prefix → `FT.SEARCH`.
- **Config:** `REDIS_URL` (secret-style field). **Connect** runs PING, reads the version with
  `INFO server`, probes `FT._LIST`, reads `INFO clients`, and sets `rs_has_hybrid` / `rs_has_svs`.
  It also shows `DBSIZE` and the existing `irisnb_*` counts.
- **Embedder:** potion-base-8M loaded via §1.13, inside `mo.status.spinner("first run: ~30 MB model")`.
  `DIM` is derived, and the hashing fallback shows a loud warning.
- **Seed (consent):** about 36 Cairn help articles (`title` TEXT weight 2, `body` TEXT, `category`
  TAG, and one NUMERIC). HASH under `RS_PREFIX+"doc:"`, paced, EXPIRE each, ledgered. Index
  `RS_PREFIX+"idx"` is `HNSW COSINE`. Show the `FT.CREATE`.
- **Core cells:**
  1. *Full-text*: query box plus prefix/fuzzy toggles, `SCORER BM25STD WITHSCORES`.
  2. *Vector: KNN vs VECTOR_RANGE*: a `k` slider and a radius slider (both debounced), with rows
     returned by each. KNN always returns k rows and range can return 0, so only range can express
     a cache MISS (the bridge to column 1).
  3. *Hybrid pre-filter*: a category multiselect and a numeric range, feeding a pre-filtered KNN.
- **Wow: "one query, three engines".** A query box with presets (a lexical trap with zero shared
  words, an exact keyword, a typo). **One rank table** has rows = docs and columns = BM25 rank |
  vector rank | hybrid rank (`FT.HYBRID` RRF when available, otherwise client-side RRF labelled as
  such), with the intended doc highlighted, plus a command trace. Offline test: vector top-1 is the
  target for every paraphrase preset, and the lexical trap has zero overlap after crude stemming and
  stopword removal. The Phase 5 live assertion is that BM25 top-k excludes the target.
- **Deep dive:** *Index anatomy*. Choose FLAT/HNSW (and SVS-VAMANA if available) and a metric, then
  build a **second index over the same prefix** and compare `FT.INFO`. Plain text: "same results
  at this scale; ANN parameters matter at 10⁵+ docs". Optionally an `FT.PROFILE` look.
- **Cleanup:** drop the ledgered indexes (no DD) and UNLINK the ledgered keys.

### Column 1 · LangCache (`lc_`)

- **Title:** a managed semantic cache, so a paraphrase gets the stored answer instead of a new LLM
  call. Docs https://redis.io/docs/latest/develop/ai/context-engine/langcache/ . Mermaid:
  cache-aside.
- **Config:** host, cache ID, API key (secret), and optional scope attribute (a one-line note on
  attributes goes here; there is no attributes cell). Include the shared-cache and
  never-Generate-key warnings.
  - **Test connection** is a read-only search for a sentinel prompt. It also runs a **floor probe**
    (a known paraphrase at 0.5): if nothing comes back below the cache's default, record
    `lc_floor`, which sets slider minimums.
  - Detect attribute support via the 400 "no attributes are configured" error.
- **Result filtering (all searches):** request `maxResults=5` and keep only results whose `id` is
  in the ledger. Show any other match as "another app's entry (hidden)" and never show its prompt
  or response. Record in §7 whether `maxResults` is honoured.
- **Seed (consent):** 4–6 Cairn gear-care Q&A entries with `ttlMillis`, ledgered, and the request
  JSON shown.
- **Core cells:**
  1. *Anatomy of a hit*: one search, with the full request and response JSON.
  2. *Threshold lab*: about 15 labelled probes (paraphrases, near-misses, entity or number swaps,
     negations, unrelated), each searched **once** at the floor. A slider then re-scores
     client-side. Rows are coloured hit / false-hit / missed-paraphrase / foreign, with `mo.stat`
     tiles for precision, recall and hit rate, scored on our own entries only.
  3. *searchStrategies*: exact vs semantic vs both, on case, punctuation and paraphrase variants.
- **Wow: cache-first chat (DIY, §1.1).** Each Send searches at the slider threshold.
  - A **hit** shows the cached answer with a badge: similarity, matched prompt, entry id, cache ms.
  - A **miss** shows "teach it". **Teach** auto-prefixes the stored prompt with "At Cairn
    Outfitters: " and shows the stored prompt. It refuses prompts that contain no word from a fixed
    Cairn vocabulary. It stores with `LC_TEACH_TTL_MS`, and **deletes the taught entry right after
    its paraphrase HIT is shown**.
  - When unscoped, a red callout says "shared cache: taught answers are visible to other apps for
    120 s".
  - The AUTORUN script: preset question → teach → paraphrase.
- **Deep dive:** *TTL*: store with `ttlMillis=10000`, then **Check again** (under AUTORUN it sleeps
  11 s) turns HIT into MISS, and delete-by-id then returns 404.
- **Cleanup:** delete-by-id from the ledger and report counts. A test walks the AST of column 1's cells
  and functions and fails on any call named `flush` or any string containing `/flush`. It must not
  grep the whole file: the setup block names `flush` legitimately, in `http_refusal`.

### Column 2 · Context Retriever (`cr_`)

- **Title:** a data model becomes governed MCP tools over your own Redis data. Agents query through
  tools, never raw Redis. Docs https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/ .
  Mermaid: ContextModel → admin API → surface → MCP tools; agent + key → MCP → service → your Redis
  (JSON + indexes).
- **Config:** admin key (secret), `REDIS_URL` (secret-style; the hosted service must reach it), and
  optional API/MCP URLs. **Test connection** runs `GET /api/v1/context-surfaces?page_size=…`
  (read-only) and shows the orphans panel. Include the shared-DB warning, and the note that the
  admin-key tab needs an existing service.
- **Transport:** the SDK (`redis-context-retriever==0.0.6`) is used **only** for `ContextModel` /
  `ContextField` / `ContextRelationship` / `export_data_model`, inside `cr_build_models`.
  **Everything else goes through `http_call`:** admin REST (create / describe / delete surface,
  create and list agent keys; take `mcp_url` from the list response, else `CTX_MCP_URL` or the
  default), import (`POST /api/v1/context-surfaces/{id}/data`, one entity per call, `fail_fast`),
  and MCP JSON-RPC `tools/list` and `tools/call` against `mcp_url` with `X-API-Key`. Check
  `result.isError` explicitly. Show `error.code` and `error.message` verbatim. Use
  `urllib.parse.unquote` on URL userinfo for `connection_config`.
- **Dataset:** the validated 3-entity, 20-record set (`research/context-retriever.md` §9),
  rebranded as Cairn, with **no `__acl_field_mappings__`** and key templates
  `f"{CR_PREFIX}customer:{{customer_id}}"` etc. (asserted). Always pass `entities=[...]`.
- **Core cells:**
  1. *Data model → tools (offline)*: pick a field, choose its index type (text / tag / numeric /
     none) and toggle `allowed_values`. The export JSON and a **predicted** tools table (labelled
     "prediction, classic naming") update live. Include a reset-to-default button.
  2. *Create + load (consent)*:
     - Take an `FT._LIST` snapshot.
     - Create the surface from the chosen model, and an agent key with expiry.
     - Show the **actual** tools, diffed against the prediction.
     - Import the data, then EXPIRE the keys by name.
     - **"Where did my data go"**: `JSON.GET` one doc, and `FT.INFO` on the new index (read-only).
  3. *Paging and provenance*: `limit`/`offset` sliders on one filter tool, showing `has_more`,
     `total_count` and the `_retrieval` stamp if present.
- **Wow: MCP tool explorer.**
  - A tool dropdown drives a form generated from `inputSchema`: enum → dropdown, bool → checkbox,
    number → number, array or object → JSON text area with a generated example.
  - **Run** renders the results table (or a count or ids), plus the real JSON-RPC request and
    response from `HTTP_TRACE`.
  - **Break it:** send an out-of-enum value found anywhere in the schema; else an unknown argument
    on a tool with `additionalProperties:false`; else hide the preset. The rejection shows in a red
    callout.
  - **Never hardcode tool or argument names.** The AUTORUN script calls the first `get_*` tool with
    `C001`, then Break it.
- **Deep dive:** the tool catalogue table (name, description, arguments). The access-tags
  experiment is **cut**.
- **Cleanup:** as in §3.6.

### Column 3 · Agent Memory (`am_`)

- **Title:** short-term session events plus long-term memories recalled by meaning across
  sessions. Docs https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/ . Mermaid:
  events → background extraction (minutes) → long-term memories (vector index in your Redis) →
  search.
- **Config:** endpoint, store ID, API key (secret). **Test connection** runs `GET /v1/stores/{id}/health`.
  Show the run's scope (`ib-{RUN}`, `iris-basics-notebook`) and why every call is filtered. The
  orphans panel goes here.
- **Transport:** raw `http_call`. Show **424 `type`/`detail`**; never turn it into "0 results". A
  collapsed text-only snippet shows the same call with the official SDK.
- **Core cells:**
  1. *Write long-term memories (consent)*: bulk-create 4–5 facts about a Cairn customer, with ids
     `ib-{RUN}-m{n}`, topics and the namespace. Ledger them.
  2. *Search + similarity lens*: **Sweep** runs one on-click sweep of 11 thresholds (limit 100,
     10 s timeout, progress bar, abort on 429). It caches a band table per memory, labelled
     "derived; assumes a hard cut". A threshold slider then filters the **cached** table
     client-side, with a **Re-query live** button. Phase 5 checks monotonicity and hides the band
     view if it fails.
  3. *Scope lens*: the same query under this owner, a fake owner, and a wrong namespace, which
     gives N / 0 / 0.
- **Wow: memory inspector (DIY chat).**
  - Each Send appends a USER event to session `ib-{RUN}-s{n}`. The first event carries `actorId`
    and the namespace.
  - It then searches long-term memory **rank-based**: very low or omitted threshold, `limit=3`.
    Output looks like "#1 of 3 · 0 words in common" (a local stemmed-overlap counter), plus the
    session event list.
  - **New session** (plain `btn.value`) rotates the session to show long-term memory survives while
    the session resets.
  - A **Prompt assembly** accordion shows the exact context string an agent would receive, with a
    toggle per tier.
  - Three zero-overlap presets; Phase 5 keeps only those that rank the target #1.
  - The AUTORUN script sends preset 1.
- **Deep dive:**
  - *Session memory*: events plus `summary`.
  - *Auto-extraction*: post a short conversation, then **Check for extracted memories**. The
    kick-off never autoruns, and the notes tell the presenter to press it before presenting the
    other columns.
  - *Under the hood*: read-only `FT.INFO memory:{storeId}:ltm` and `HMGET`/`HSTRLEN` of our own
    memory, over its own `connect_redis(redis_url_from_env())` connection. Fail soft if it's not the
    same DB.
- **Cleanup + Re-sweep:** as in §3.6.

---

## 6. Phases (Sonnet implementers · a Sonnet reviewer per implementer · Opus orchestrates, reviews setup before the freeze, and runs live verification)

Each phase goes **implement → independent review (read-only, structured findings) → fix → confirm**.
Reviewers check against this plan, the research corrections, §1, §3.3, §3.6 and "reads like the
surrounding code". **No live service calls before Phase 5.**

| Phase | Scope | Done when |
|---|---|---|
| **1 · Scaffold** | PEP 723 header; app + `setup(hide_code=True)` (§3.4 in full); the intro cell (`ib_notes`, AUTORUN banner, anchors, width override); **all four title cells** (§5); `.env.example`, `.gitignore`, `tests/conftest.py`, `test_shared.py` (env parser incl. CRLF/quotes/"" unset; allowlist; Secret repr; scrub forms; recursive redaction incl. a fake create-agent-key response; every http_call choke point; guard: no-PREFIX / empty / foreign FT.CREATE, DD, multi-key, pipeline, `owned_index` with bytes, empty, missing and `memory:X:ltm`; ledger round-trip; `pressed()` semantics; `@app.function` co_names rule); `verify.py` offline + `--preflight` skeleton; README skeleton | offline verify passes; Opus reviews the setup block |
| **2 · Column 0 Redis Search** (exemplar) | §5 column 0 + `test_redis_search.py` (argv shapes, RESP2 parsing, calibration, cleanup-independence test) | offline verify passes · reviewer signs off · **setup frozen** · `iris_basics.py` copied to `.agents/build/base.py` |
| **3 · Columns 1–3 in parallel** | each writes `.agents/build/col{N}_{key}.py`: base's header + setup (AST-equal) + **only its column's items after the title**, marked `column=0`; plus `tests/test_{key}.py` and `.agents/build/{key}-README.md`. **Never save fragments from the marimo editor** | each fragment passes `check --strict`, its tests (`IRIS_NB_MODULE`) and a no-creds export · reviewer signs off |
| **4 · Integrate** | `assemble.py` (Opus-written, deterministic): AST-compares setup, parses on decorators, rewrites `column=0` → N, splices each fragment right after its column-N title, strips preambles and footers → `iris_basics.py`. Merge the README, cross-column review, Playwright screenshots | offline verify passes on the assembled file · integration reviewer signs off · `.agents/build/` archived |
| **5 · Live verification** (approved: scoped + cleaned up, `IRIS_BASICS_ENV_FILE=redis-iris-demos/.env`) | `verify.py --live`; a fix loop for every discrepancy (fixer → reviewer → re-run); record real numbers; fill in the `recorded_live` panels; keep only presets that behave | live exports exit 0 · footprint empty after the extraction wait · secret scan clean · README "Verified live on <date>" lists what was and wasn't exercised |

---

## 7. Live-verification checklist

- **LangCache:** can the per-request threshold go below the default (the floor)? Is `maxResults`
  honoured? `ttlMillis` timing; attributes present?; real paraphrase similarities.
- **Context Retriever:** classic vs unified surface; import wire shape; `mcp_url` source; indexes
  left behind after delete; real index names; JSON keys.
- **Agent Memory:** threshold monotonicity; empty-text listing; session delete; extraction latency;
  that events' `ownerId` equals the first `actorId`; zero-overlap preset ranks.
- **Search:** DB version → `FT.HYBRID` / `SVS-VAMANA`; `BM25STD` accepted; lexical trap misses in
  BM25.

## 8. Harvested from the legacy plans (still valid)

- **Cell mechanics:** underscore locals; `run_button` + `mo.stop` gating for writes; a consent cell
  before the first write; TTL on every key; teardown that reports counts.
- **Vectors:** `DIM` is derived, never typed; `decode_responses=False` for vectors.
- **Tracing:** a redis-cli renderer with binary elision; latency is one incidental column.
- **Launch:** `uvx --sandbox` is required, since without a TTY marimo silently skips the sandbox;
  Windows-first tooling.
- **Rejected ideas:** no performance charts, data-types tour, keyspace profiler, or WASM/mock mode.
  The "Recorded live" panel is a labelled capture, not a mock.

## 9. Rev 2 changelog (from `research/plan-critique.json`)

*Rev 2.1 (Phase 1 review):* module-client pipelines and `batch_indexer` denied (§3.4 Redis);
`/flush` judged as httpx sends it and only four HTTP verbs allowed (§3.4 HTTP); empty-username
Redis URLs masked (§3.4 secrets); every dependency pinned exactly (header); the LangCache flush
test is scoped to column 1 (§5).

*Rev 2.2 (Phase 1 fix):* AUTORUN is refused in `marimo edit` unless `IRIS_BASICS_HEADLESS=1`, so
`verify.py`'s exports (which marimo also calls `edit`) can still autorun (§3.4, §3.7); the `.env`
name check also covers the `.env` beside the nearest `pyproject.toml` above the notebook (§3.4).

- **Blockers fixed:**
  - Cleanup depended on gated cells, so it silently never ran (§3.6).
  - `owned_index` came out vacuously true on bytes and empty prefix lists, which could have dropped
    the demo's `memory:*` index (§3.4).
  - Agent Memory cleanup trusted the server's filter (§3.6).
  - Secrets leaked via request and response bodies (§3.4 redaction and scrub).
  - AUTORUN could delete other people's surfaces (§3.4, `pressed` stages).
- **Majors fixed:**
  - FT.CREATE without a PREFIX clause; shared prefix across columns.
  - verify.py couldn't know RUN or the ledger; Python 3.14 in uv; offline runs weren't credential-free.
  - Extraction leftovers; LangCache Teach poisoning the shared cache; foreign cache entries shown.
  - Prefilled secrets in the Variables panel; `__marimo__/` session leaks.
  - SDK calls bypassing the trace; Context Retriever naming; Context Retriever cells were
    single-shot.
  - No reset, orphan path or presenter kit; the no-creds experience.
  - Debounce and fetch/render split; layout vs presenting mode; scope (core + deep dive).
- **Cut:**
  - M / EF_* sliders.
  - LangCache threshold slider cell and attributes cell (merged into the lab / moved to the config
    note).
  - "LLM would have taken" stat; Agent Memory bisection.
  - Access-tags experiment; altair.

---

## 10. As built (2026-09-30)

**Status: done and verified live.** `verify.py` offline: `marimo check --strict`, 1593 tests, and a
no-credentials headless export all pass. `verify.py --live` against the `redis-iris-demos`
services (run `95109ff5`) passed 28 of 28 steps. All 4 columns were exercised, the footprint
was empty, the TTL sentinel expired, and the secret scan was clean. Real numbers are in README
"Verified live".

Deviations from rev 2, all deliberate:
- **Column helpers live in the setup block**, grouped per column, instead of in
  `@app.function`. With 300 items, `marimo edit` took about 42 s to Run all and about 20 s per
  control change. With 112 cells it takes about 10 s and about 0.5 s (Phase 4 browser check).
  The `@app.function` global-name rule no longer applies. The frozen-setup / fragment / assemble
  build (Phases 3–4) served its purpose and is archived in `.agents/build/`.
- **Presenter notes** always render with a CSS class, and one `<style>` cell toggles them.
  Before, flipping `ib_notes` re-ran gated cells and raised NameErrors.
- **Live facts that overrode research:**
  - Redis 8.6 words a missing index `SEARCH_INDEX_NOT_FOUND Index not found`.
  - LangCache answers an expired entry's delete with `404 /errors/resource-not-found`.
  - This account's Context Retriever serves the **unified** tool surface, and `filter_*`
    needs a condition. The prediction now uses unified naming, and paging sends a
    schema-derived condition.
  - Agent Memory bands for a zero-overlap paraphrase are about 0.3–0.4, so the lens slider
    defaults to 0.3.
  - The service drops its own indexes together with the surface.
- `verify.py` gained `--notebook` / `--extra-test` (build-time only) and a full `--live`; the
  pytest step limit is 30 min, since the suite takes about 10.
- `RECORDED_LIVE` panels were filled from the first live run with every account identifier
  replaced. A test enforces this.

Still not exercised live (README lists them): the orphan deletes, Reset, Agent Memory's
extraction kick-off, New session and under-the-hood cells, SVS-VAMANA index creation, a
LangCache scope attribute, the classic Context Retriever surface, and a restricted Redis user.
