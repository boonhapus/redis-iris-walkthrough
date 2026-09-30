# Redis Iris basics: final MVP build plan

Date 2026-09-30. Baseline commit `37a6e0f` on `main`. This is the contract for one scaffold agent,
four column builders working in parallel, and one assembly step. It is plan B
(`.agents/plans/candidates/mvp-plan-b-teaching-first.md`) with the grafts from plan A that the
three judges directed, and with every blocker the judges named resolved against the six research
briefs in `.agents/research/*-2026-09-30.md`. Where this plan and a brief disagree on an API
fact, the brief wins; where they disagree on scope, this plan wins. No network call was made
while writing it, and no `.env` value was read.

Revision 2 (same day): the critics' one blocker, eight majors and the minors are folded in.
Section 1b lists what changed and where this revision disagrees, with evidence.
Revision 3 (same day): orchestrator amendments, in the section right below; they override the rest.

## Revision 3 (orchestrator amendments, 2026-09-30)

Read this section first. Where it conflicts with sections 1-20, this section wins. Everything
else in the plan stands.

**R3.1 The run tag is stable per machine and user, not per kernel.** In the setup cell replace
`RUN_TAG = uuid.uuid4().hex[:8]` with:

```python
# One tag for everything this notebook writes from this machine. It is derived from the user
# and host names, so a fresh kernel finds what an earlier one wrote and Clean up needs no ledger.
RUN_TAG = hashlib.sha256(f"{getpass.getuser()}@{platform.node()}".encode()).hexdigest()[:8]
```

Imports: add `getpass`, `hashlib`, `platform`; drop `uuid`. The six setup names in
`test_setup_block_is_minimal` are unchanged. Consequences, which the copy must state:

- Every deterministic id (Redis keys and index, surface name, agent-key name, owner id, memory
  ids) is the same in every kernel on one machine. Pressing Load or Write again overwrites
  (`HSET`; bulk create by id is an update). The intro's Safety paragraph (section 6) gains one
  sentence: "The tag is the same every time you open the notebook on this machine, so Clean up in
  a fresh kernel still finds what an earlier kernel wrote." The README FAQ answer about closing the
  laptop says the same and keeps the LangCache exception (entries cannot be listed; the 15-minute
  `ttlMillis` is the net).
- `cr_cleanup` (section 10 cell 11, section 12 row 2) no longer relies on `cr_surface_id` alone.
  It lists the account's surfaces (`GET /context-surfaces?page_size=50`, paging as the listing
  offers, at most 10 pages; the response shape the old notebook parsed is in `cr_list_surfaces`,
  found with `git show 37a6e0f:iris_basics.py | grep -n "def cr_list_surfaces"`), deletes every
  surface whose `name == cr_surface_name` plus `cr_surface_id` if it is not in the listing (200,
  204 and 404 all count as gone), then unlinks `cr_record_keys`, then drops any index in
  `FT._LIST` whose name contains one of the deleted surface ids and whose `FT.INFO` prefixes all
  start with `KEY_PREFIX` (no `DD`). `cr_indexes_before` stays, for `cr_peek` only. Depends on
  `cr_admin_service`, `cr_redis`, `cr_surface_name`, `cr_surface_id`, `cr_record_keys`,
  `cr_cleanup_button`. Body budget 30 (was 24); the six lines come out of the 50-line margin.
  `cr_cleanup_button` lead-in: "Clean up deletes every surface named `cairn-<run>` on this
  account, which also revokes their agent keys, then unlinks the 20 keys by name and drops any
  index those surfaces left under the run prefix, without `DD`. A surface has no TTL and holds
  your password, so this button matters more than the other three:". The listing body is never
  rendered (it names other surfaces); only the count of matches is.
- `am_session_id` is per kernel, because a session is one conversation:
  `am_session_id = f"{am_owner_id}-s{datetime.now(timezone.utc):%Y%m%d%H%M%S}"`. `am_cleanup`
  already lists sessions by owner, so it deletes sessions from earlier kernels too.
- The section 20 risk "Without a ledger, a LangCache Clean up after a kernel restart finds
  nothing" stays; the equivalent risks for Redis, Context Retriever and Agent Memory are gone.

**R3.2 Python floor is 3.12.** `requires-python = ">=3.12,<3.14"`, one pin `numpy==2.5.3`, no
marker split and no numpy 2.4.6 comment. Delete the `uv sync --python 3.11` run from section 17
step 0.7, decision 1 of section 18, and the Python 3.11 risk in section 20. README prerequisites:
"Python 3.12 or 3.13 (uv installs it)". Reason: fewer moving parts for the same audience.

**R3.3 Builders never write `iris_basics.py`.** `assemble.py` takes `--output PATH` (default:
the notebook in place; with `--output` the scaffold is read, the fragments inserted, and the
result written to PATH, leaving the notebook untouched). A builder checks its fragment with:

```
uv run python .agents/fragments/assemble.py --output .agents/fragments/preview_columnN.py
uv run marimo check --strict .agents/fragments/preview_columnN.py
IRIS_BASICS_NOTEBOOK=.agents/fragments/preview_columnN.py uv run pytest tests/test_safety.py tests/test_voice.py tests/test_notebook.py -k "not budget and not columns_are_complete"
```

(PowerShell: `$env:IRIS_BASICS_NOTEBOOK='...'; uv run pytest ...`.) The tests locate the notebook
through `NOTEBOOK_PATH = Path(os.environ.get("IRIS_BASICS_NOTEBOOK", ROOT / "iris_basics.py"))`
and import it with `importlib.util.spec_from_file_location("iris_basics_under_test", NOTEBOOK_PATH)`;
`test_marimo_check_strict` checks `str(NOTEBOOK_PATH)`. In `test_notebook_runs_without_credentials`
the `cr_data_model` and `cr_record_keys` assertions run only when the file under test contains
`def cr_data_model`; the not-configured assertions apply to whichever configure cells the file
contains. `.gitignore` gains `.agents/fragments/preview_*.py`. The rule "`git checkout
iris_basics.py` afterwards" in section 17 is void: a builder has no reason to touch the notebook.

**R3.4 A live smoke script, written at assembly, run by the orchestrator.** The assembly agent
adds `tests/live_smoke.py` (no `test_` prefix, so pytest never collects it). It runs the cells of
every configured column in file order through marimo's `Cell.run()` (verify the signature and the
semantics on the installed package first: `inspect.signature(marimo._ast.cell.Cell.run)`; a
cell's unspecified references are computed by running its ancestors, so the script threads a
dict of definitions from cell to cell and passes each cell's references explicitly), substituting
`types.SimpleNamespace(value=True)` for every reference whose name ends in `_button`. It prints
one line per cell: cell name, seconds, and either `stopped`, `error: <ExceptionType>` or the
first 120 characters of the output's plain text. It never prints a header, an environment value,
or a body that could carry one; configuration comes from the process environment only
(`App.run()` and `Cell.run()` load no `.env`). Section 18's first-live-run checklist is what the
orchestrator reads against its output.

**R3.5 Voice tests.** A `tests/test_voice.py` failure is fixed in the copy, not the test. The
one exception is a demonstrable false positive (a URL, a backticked command or a mermaid token
counted as words), which the assembly agent may fix in the test with a one-line comment.

Section map: 1 decisions that settle the judges' points; 1b critique response (revision 2);
2 file tree; 3 `pyproject.toml` and the small files; 4 the setup cell; 5 `src/cairn_support`;
6 the intro cell; 7 shared cell patterns every builder copies; 8-11 the four columns, cell by
cell; 12 the Clean up contract; 13 tests; 14 README outline; 15 dropped list; 16 budgets;
17 build order and parallelism; 18 what Nick must provide and the first-live-run checklist;
19 rules for builders; 20 risks.

## 1. Decisions that settle the judges' points

| # | Judges' item | Decision | Source |
|---|---|---|---|
| 1 | `display.mask_connection_config` and `display.request_and_reply` put Iris field names and native marimo calls into `src/` | `display.py` is gone. `src/` imports neither `redis`, `niquests` nor `marimo`. The password is masked with one line in `cr_create` on the wire dict, before it is bound to any global. Accordions and `mo.json` are written in the cells. The not-configured callout is literal text in each configure cell (section 7.1), byte-identical across columns. | plan A section 4 rule; marimo brief 1.4 |
| 2 | `len(outputs) >= 45` fails on B's own drop order | Replaced by `test_cell_count_budget` (`@app.cell(` count between 42 and 46; 45 planned) and by asserting that the four configure cells stopped (`rs_client`, `lc_service`, `cr_admin_service`, `am_service` absent from `defs`) while `cr_data_model` ran. | plan A section 9 |
| 3 | Rule 3 said seed cells never `mo.stop`, but `rs_load` and `am_write` did | Rule restated (section 7.3). Columns 0 and 3 compute every id Clean up needs in the configure cell, so their seed cells use the native pattern: `mo.stop(not <button>.value, <info callout>)` first, then the writes, then `<state> = True`. Columns 1 and 2 must hand service-chosen ids (`lc_stored_entries`, `cr_surface_id`) to Clean up, so their seed cells use the two-branch shape and never `mo.stop`. Example cells `mo.stop` on the falsy or missing state. Clean up depends only on setup names, configure-cell names, the seed cell's always-defined ids (columns 1 and 2) and its own button. | marimo brief 5.1 (a stopped cell's descendants never run); `marimo/_plugins/ui/_impl/run_button.py` (`_on_update_completion` sets `_value = False` directly, so a completed press does not re-run dependents); verified 2026-09-30 that names assigned before `mo.stop` reach `defs` |
| 4 | Column 2 budget (396 vs 360) and `cr_model_variant` | `cr_model_variant` is dropped outright; its consequences were inferred, not observed. Column 2 is 11 cells with a 285-line body budget (section 16), with the two `tools/call`s as separate cells (graft). | conformance review section 7; Context Retriever brief section 4 |
| 5 | `retries=2` (packaging brief A3) vs `retries=0` (Agent Memory brief line 321, Context Retriever brief line 95, LangCache brief line 434) | No session passes `retries=`; niquests' default 0 stays. Each call is exactly one request on the wire, so the wire view and the request counts in the copy are exact, and a replayed `POST /context-surfaces` can never make a second surface. The Redis client keeps redis-py's `Retry(ExponentialBackoff(), 3)` for connection errors, which is the production checklist. Stated once in `call_service`'s docstring and once beside the create call. `tests/test_safety.py` asserts `retries=` never appears. | three of four service briefs |
| 6 | Owner id grammar | `cairn-<run>` everywhere: Redis keys `cairn:<run>:...`, index `idx:cairn:<run>:help`, surface name `cairn-<run>`, agent key `cairn-<run>-agent`, owner id `cairn-<run>`, memory ids `cairn-<run>-m1..m5`, session `cairn-<run>-s1`. Namespace stays `iris-basics-notebook`. All match `^[a-zA-Z0-9-]+$` (Agent Memory id rule). | plan A section 14; Agent Memory brief section 2 |
| 7 | B 7.6 hid the pre-filter behind a multiselect whose default showed no filter; NUMERIC never queried | Fixed cell `rs_prefiltered` with no control: the same KNN 5 for *where is my parcel* over everything and over `(@category:{returns\|shipping} @year:[2024 2026])`, two redis-cli lines, two tables. `rs_search_controls` is the question box alone (the radius is the literal 0.5 since revision 2); `k` is the literal 3. | Redis Search brief 4.4 (captured wire) |
| 8 | Separate full-text cell | Restored as `rs_full_text`: one fixed `FT.SEARCH ... "hiking boots" SCORER BM25STD WITHSCORES` before any vector query. | Redis Search brief 4.1 |
| 9 | Column 0 cell count with two grafts | The embedder loads inside `rs_connect` (configure = database plus the local model). The model loads once per kernel, not per seed press. Revision 2: `rs_connect` and `lc_configure` also define and render the seed button under their status callout, so columns 0 and 1 land at 11 cells (section 1b, item 9). | marimo brief recommendation 4 made unnecessary |
| 10 | Button labels | Every button names the commands and counts (section 7.4). | plan A |
| 11 | Intro table | Column "Its one job here", and each title cell repeats the one job in its first paragraph. | plan A rule 1 |
| 12 | `SESSION_TURNS` need facts for extraction and the kitchen question last | Four events: USER fact (50 km race in April), ASSISTANT asks a concrete question, USER fact (left knee, trekking poles), USER kitchen question. The last turn drives the assembled prompt. | Agent Memory brief 4(d) |
| 13 | Wording | "at 0.85, the console default for a new service" (never "the cache's default"); 401/404 sentences print the body's `type` as received; `cr_rejected_call` leads with the live-observed `isError` reply and prints whatever the second, schema-violating call returns (`error.code` and `error.message`, or `isError` text, or an accepted body) without asserting which. | LangCache brief 2.3; Context Retriever brief 3.3 and open questions 1-2 |
| 14 | `cr_cleanup` running `FT.INFO` on every index of a shared database | The straggler check looks only at `(FT._LIST now) - cr_indexes_before` plus any name containing `cr_surface_id`, and drops an index only when every `prefixes` entry starts with `KEY_PREFIX`. | Context Retriever brief 7.3 and 8 |
| 15 | `Field.redis_args()` / `IndexDefinition.args` | Verified by the judges on redis-py 8.1.0; the hand-built fallback is removed. | judges' introspection |
| 16 | `.env.example` placeholders | Every name is commented out; no `<placeholder>` value can be loaded as a literal. | marimo brief 1.2 |
| 17 | Python 3.11 | Kept per the orchestrator (`>=3.11,<3.14`, numpy marker split). The scaffold agent runs `uv sync --python 3.11 && uv run pytest` once; if it fails, the floor moves to 3.12 and numpy collapses to `2.5.3`. Flagged in section 18. | packaging brief C1 |
| 18 | `SEARCH_PRESETS["lesson"]` text in `src/` | Removed from the data; the three lessons are the gloss under `rs_engine_results`' table. Copy about engines is Redis teaching and stays in the notebook. | packaging brief B5 boundary |

## 1b. Critique response (revision 2)

What changed, in the critics' order. Every item is applied in the section named; the notes
marked *disagree* say why and cite the evidence.

| # | Finding | Resolution | Where |
|---|---|---|---|
| B1 | `cr_create` could render the database password through `mo.json(body)` on a non-201 | On any non-2xx from `POST /context-surfaces` the cell renders only the status and the string values of `type`, `title` and `detail`, each cut at 200 characters, after asserting the password is absent from `json.dumps(body)`. `mo.json` of that reply is forbidden in the cell; `test_safety.py::test_create_reply_is_never_rendered` enforces it. Section 7.2 now separates "replies to requests that carried no secret" (the 7.2 pattern) from "replies to the create call" (this rule). | 7.2, 10 cell 5, 13, 19 rule 4 |
| M1 | `cr_mcp_service` sent `Accept: application/json`, a header set nobody verified | The MCP session sends no `Accept` header, which is the live-verified shape (httpx and niquests both default to `*/*`; Context Retriever brief section 3). The docs' `application/json, text/event-stream` is the fallback if a live run answers 406. `Accept: application/json` stays on the three REST sessions. On the first-live-run checklist. | 10 cell 5, 13, 18 |
| M2 | `FT.HYBRID ... LOAD 2 @__key @title` labelled verified while the live run sent `LOAD 1 @__key` | `LOAD 1 @__key` is sent, exactly as `rs_hybrid_args` did live (iris_basics.py 2214-2224); the title comes from `rs_article_titles`, a dict built in `rs_connect` from `cairn.HELP_ARTICLES` (zero extra commands). The brief's 4.5 label is wrong and is noted as such. | 8 cells 2 and 9, 14 |
| M3 | "Every global carries the prefix" contradicted the unprefixed imports the plan itself needs; MB002 collisions between builders | Fragment rules list the unprefixed names each column owns. Column 0: `NumericField`, `TagField`, `TextField`, `VectorField`, `IndexDefinition`, `IndexType`, `Query`, `ResponseError`. Column 2: `unquote`, `urlsplit`, `Any`, `ContextField`, `ContextModel`, `ContextRelationship`, `export_data_model`, plus `Customer`, `Product`, `Order`. Columns 1 and 3: none. Every other column reaches redis-py exceptions as `redis.ResponseError` / `redis.RedisError` through the setup's `redis` module. `from typing import Any` is in the `cr_data_model` cell text. `test_safety.py::test_import_ownership` enforces it. | 10 cell 3, 17 |
| M4 | `cr_rejected_call` asserted an unobserved `-32602` as fact | The live-observed rejection leads: `filter_customer` with no condition returns HTTP 200 with `result.isError` and `Error executing tool: at least one condition is required` (brief 3.3, README "Verified live"). The schema-violating call (a tag condition on `lifetime_value`) is the second request, worded "the cell prints what arrives", with three branches. The lead-in is true in every branch. | 10 cell 8, 18, 20 |
| M5 | Serial commas missing in the literal blocks builders copy | Fixed in every literal in sections 6-11 and 7.1; the VOICE checklist pass is a scaffold-step task as well as an assembly-step task; `test_voice.py::test_serial_comma_in_three_item_lists` is a cheap regex guard. | 6, 7.1, 7.6, 8-11, 17 |
| M6 | Lead-ins of 60-115 words, semicolons everywhere | Every lead-in is at most two sentences of at most 25 words and ends with a colon. Mechanism detail moved to the gloss under the output or into the wire accordion. Semicolons removed from every `mo.md` string. Builder rule 8 and `test_voice.py::test_no_sentence_over_30_words` enforce a 30-word ceiling. | 7.6, 8-11, 13, 19 |
| M7 | Trial CTA in `rs_title`; console click paths in customer-facing copy | The try-free link is gone from the notebook; "A Redis 8 database with Search and JSON, either local or Redis Cloud" is a README prerequisite and the FAQ says "No." Console paths survive only in `.env.example` and section 18, cut to the setting's name and the console page. | 3, 8 cell 1, 14, 18 |
| M8 | No size margin; test ceiling conceded the brief; serialization overhead unbudgeted | Section 16 rebuilt: 45 cells, 8 lines of saved-format overhead per cell (360) budgeted explicitly, body budgets re-derived per cell, total about 1,450 with the test ceiling at 1,500 lines and 46 cells. Columns 0 and 1 are 11 cells each because the seed button moves into the configure cell (the section 16 cut order removes lines, not cells, so it could not have done this on its own). The radius is the literal 0.5 and `lc_strategies` is 2 x 2, applied up front. | 7.3, 8, 9, 13, 16 |
| m1-m3 | `cr_create` globals unassigned after a mid-sequence failure; `lc_configure` did not stop on non-200; `am_configure` `.get` on a non-dict | Every `cr_create` global is initialised before the `try` in both branches. `lc_configure` stops on non-200 like the other two. `am_configure` guards with `isinstance(am_health_body, dict)`. | 9 cell 2, 10 cell 5, 11 cell 2 |
| m4 | The `os.environ` safety rule was ambiguous | Restated: the rule fires only when an `Assign` node's value is itself `os.environ[...]` or `os.environ.get(...)` and a target does not start with `_`, with `CTX_API_URL` and `CTX_MCP_URL` excepted. | 13 |
| m5 | LangCache `DELETE` carries the session `Content-Type` with no body | *Disagree on the code change.* Threading a per-call `headers=` through `call_service` for one request adds setup-block surface for a deviation HTTP permits (RFC 9110 section 8.3 constrains `Content-Type` only when content is present). Noted beside `lc_cleanup` and on the first-live-run checklist; if a live 4xx appears, the fix is `headers={"Content-Type": None}` on that call. | 9 cell 11, 18 |
| m6 | Custom `User-Agent` differs from the live run | Kept; on the first-live-run checklist with the fallback "drop the header". | 18 |
| m7 | `cr_tools_list` read but never declared | `cr_tools` declares `cr_tools_list` as a global. | 10 cell 6 |
| m8-m10 | Verified facts about pytest 9.1.1, `python -m marimo check`, MF007 scope, `defs` after `mo.stop`, `run_button` reset, niquests `base_url` and redis-py argv | Hedges removed: `test_marimo_check_strict` uses `[sys.executable, "-m", "marimo", ...]` only; rule 19.1 narrowed to markdown-only cells; decision 3 cites `run_button.py`. *Disagree with the separate minor that calls pytest 9.1.1 unverified*: the same review's "verified today" item records `pytest 9.1.1 with marimo 0.25.0 ... 2 passed`, so the pin stays and section 20 drops the flag. | 1 row 3, 13, 19, 20 |
| m11 | `hash_indexing_failures` gloss overstated | Reworded to "no hash failed to index". | 8 cell 3 |
| m12 | Abbreviations and single-letter names | `lc_entry_ttl_milliseconds`; comprehension variables `article`, `preset`, `entry`. | 8, 9 |
| m13 | `Any` never imported | Covered by M3. | 10 cell 3 |
| m14 | "#1 of 3" versus `limit` 5 | `am_search` sends `limit` 5 and the hedged line says "#1 of 5"; the live run's "#1 of 3" was at `limit` 3 (iris_basics.py 5260-5275). | 11 cell 6 |
| m15 | The 0.894 cache-aside hit was measured on a prefixed prompt | The stored prompt and both searches carry the live run's prefix `At Cairn Outfitters: ` (iris_basics.py 3202-3206), which also keeps the taught entry away from other apps' generic questions on a shared cache. The number is dropped until the first live run re-measures it. | 5, 9 cell 9 |
| m16 | Uppercase HIT and MISS in prose | Lowercase "hit" and "miss" in prose; uppercase only in table cells and callout titles. | 8, 9 |
| m17 | Headings numbered from zero | Title cells are `# Redis Search`, `# Redis LangCache`, `# Redis Context Retriever`, `# Redis Agent Memory`; the intro table lists the four in column order without numbers; `column=0..3` stays in code. | 6, 8-11 |
| m18 | Five identical "Load the help centre first." callouts | Only the first example cell of a column carries the callout; the rest use a bare `mo.stop`. | 7.3 |
| m19 | Native `run_button` + `mo.stop` for seed cells where it is enough | Adopted for columns 0 and 3 (decision 3). | 7.3 |
| m20 | `redis_cli` docstring overclaims | "redis-cli syntax, long values elided". | 5 |
| m21 | `rs_connect` lets the model loader's `RuntimeError` escape | Wrapped in `try/except RuntimeError` with a danger callout naming the model and the offline cause. | 8 cell 2 |
| m22 | `lc_title`'s SDK accordion shows uninstalled code in an unverified shape | Labelled "not installed here" and matched to the brief's shape (`server_url=`, `cache_id=`, `api_key=`, `delete_by_id(entry_id=)`). | 9 cell 1 |
| m23 | "No new Redis secret" while handing the default password to a hosted service | Section 18 recommends a dedicated Redis Cloud data-access user for Context Retriever (optional) and names the ACL scope as an open question from the Context Retriever brief. | 18 |
| m24 | "ten minutes" is a bare number | The store's extraction cadence (default 5 minutes, configurable 60-600 s; Agent Memory brief 3.4) is stated once in `am_session_button` and once in the README. | 11 cells 8 and 10, 14 |

## 2. File tree

```
redis-iris-basics/
  pyproject.toml              section 3; the only dependency list
  uv.lock                     committed; produced once by the scaffold agent with `uv lock`
  .python-version             "3.13"
  .env.example                ten names, every line commented (section 3)
  .gitignore                  section 3
  .gitattributes              unchanged
  README.md                   rewritten to the tutorial template (section 14)
  iris_basics.py              the notebook; no PEP 723 header (project mode)
  src/cairn_support/
    __init__.py               docstring only
    sample_data.py            Cairn Outfitters content as Python literals
    embedder.py               potion-base-8M loader and encoder
    formatting.py             redis_cli() and shared_stems(); imports nothing but the standard library
  tests/
    test_notebook.py          app.run() with no credentials; marimo check --strict; layout and budget rules
    test_safety.py            AST rules over iris_basics.py
    test_voice.py             sentence-length and serial-comma rules over every mo.md string
    test_cairn_support.py     unit tests for the package
  .agents/plans/              kept (this file, the 4-column plan, candidates/, research/, archive/)
  .agents/research/           kept
  .agents/reviews/            kept
  .agents/fragments/          build-time only (section 17); deleted in the assembly commit
```

Deleted by the scaffold agent in its first commit: `legacy/` (5 files), `tests/` (16 files,
about 16,800 lines, all coupled to the setup block), `verify.py`, `.agents/build/` (build
artefacts and screenshots), `__marimo__/` (untracked). The PEP 723 header goes with the rewrite:
in a uv project `uv run iris_basics.py` would prefer inline metadata over the project, and
`marimo edit` would offer to sandbox (packaging brief B4).

## 3. `pyproject.toml`, `.python-version`, `.gitignore`, `.env.example`

### `pyproject.toml` (full text)

```toml
[project]
name = "redis-iris-basics"
version = "0.2.0"
description = "Redis Iris basics: one marimo notebook that takes Redis Search, LangCache, Context Retriever, and Agent Memory apart on Cairn Outfitters sample data."
readme = "README.md"
# <3.14: redis-context-retriever 0.0.6's ContextModel metaclass reads namespace["__annotations__"],
#        which Python 3.14's deferred annotations (PEP 649) leave empty inside marimo cells
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

Facts behind the pins: packaging brief part B. `redis-agent-memory`, `langcache` and `anywidget`
are deliberately absent: the two services are called over REST so every JSON body is visible,
and no custom widget is needed. httpx stays in `uv.lock` as a transitive dependency of the
Context Retriever SDK and huggingface-hub; the README must not claim it is gone.

### `.python-version`

`3.13`

### `.gitignore`

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

### `.env.example` (every name commented out; comments on their own lines)

```
# Redis Iris basics: settings. Copy this file to .env next to pyproject.toml, uncomment the
# names for the columns you want to run, and fill them in. marimo loads .env when the kernel
# starts; a variable already set in your shell wins over the file. A column whose variables are
# missing shows one "not configured" note and stops. Console: https://cloud.redis.io

# ---- Redis Search (the same database is where Context Retriever writes) ----
# Any Redis 8 with Search and JSON, local or Redis Cloud. Console: the database's Configuration
# page (Public endpoint) and its Security page (Default user password). No TLS means redis://.
# REDIS_URL=redis://default:<password>@<host>:<port>

# ---- LangCache ----
# Console: the service's Connectivity page (URL and Cache ID). The API key is shown once, when
# the service is created. Use a service of your own: a cache is shared by everything that
# points at it.
# LANGCACHE_HOST=https://<region>.langcache.redis.io
# LANGCACHE_CACHE_ID=
# LANGCACHE_API_KEY=

# ---- Context Retriever (also needs REDIS_URL above) ----
# Console: the Context Retriever Admin keys page; a new key is shown once. The database must have
# Search and JSON and be reachable by the hosted service, which is given its password.
# CTX_ADMIN_KEY=
# Optional. The defaults suit Redis Cloud; the MCP address is read from the agent-key listing.
# CTX_API_URL=https://cloud.redis.io/context-surfaces
# CTX_MCP_URL=https://gcp-us-east4.context-surfaces.redis.io/mcp

# ---- Agent Memory ----
# Console: the service's General settings page (Endpoint, with https://, and Store ID) and its
# API keys page; a new key is shown once.
# MEMORY_API_BASE_URL=https://<endpoint>
# MEMORY_STORE_ID=
# MEMORY_API_KEY=
```

## 4. The setup cell (exact contents)

`with app.setup(hide_code=True):` holds imports, the run tag and its two derived constants, one
niquests call helper and one Redis connect helper. Nothing else, and `tests/test_setup_block_is_minimal`
enforces it. Per-column sessions, TTLs and index names live in the column's configure cell.

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

    from cairn_support import sample_data as cairn
    from cairn_support.embedder import EMBEDDING_MODEL_NAME, embed_texts, load_embedding_model
    from cairn_support.formatting import redis_cli, shared_stems

    # One tag for everything this kernel writes. Every Redis key, surface name, owner id and
    # memory id carries it, so each column's Clean up can find its own work without a ledger.
    RUN_TAG = uuid.uuid4().hex[:8]
    KEY_PREFIX = f"cairn:{RUN_TAG}:"   # every Redis key written by this notebook or by Context Retriever
    KEY_TTL_SECONDS = 3600             # Redis keys and the agent key expire on their own; Clean up only shortens that

    MASKED_HEADERS = {"authorization", "x-api-key"}

    def call_service(service, method, path, *, json_body=None, params=None):
        """One request to a hosted Iris service: (status, parsed body or None, wire).

        `wire` is what went on the wire with the credential header masked, so it is safe to show.
        Nothing is raised on a 4xx or 5xx: the JSON body (type, title, detail) is the teaching
        content, so each cell branches on the status itself. Nothing is retried: every call is
        exactly one request, so the wire view and the request counts in the copy are exact.
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

Rules the setup enforces by omission: no secret is ever bound to a global (URLs and keys are read
from `os.environ` inside the expression that builds a client or session, because the Variables
panel broadcasts the first 50 characters of any string global, marimo brief 1.4); no
`mo.ui.text(kind="password")`; no `mo.state`; no `mo.ui.chat` (broken on 0.25.0); no `retries=`.

The setup imports `redis` as a module so that columns 1-3 can write `redis.RedisError` and
`redis.ResponseError` without importing them: only column 0 owns the bare name `ResponseError`
(section 17, import ownership). Binding `rs_client`, `lc_service`, `am_service` and
`cr_mcp_service` as globals is safe: redis-py 8.1.0 redacts the username and password in
`repr(client)`, and `niquests.Session.__repr__` shows adapters only, never headers (both checked
2026-09-30).

## 5. `src/cairn_support`: modules and signatures

Boundary rule (packaging brief B5): if a line would appear on a Redis docs page for one of the
four products (a command, a request body, an index schema, a key template, a `ContextModel`, a
sentence about an engine), it stays in the notebook. The package imports nothing from `redis`,
`niquests` or `marimo`; `tests/test_cairn_support.py::test_package_has_no_iris_imports` asserts it.

### `sample_data.py` (Python literals only)

| Name | Shape | Source in the current notebook |
|---|---|---|
| `HELP_ARTICLES: list[dict]` | 36 rows, keys `id, title, body, category, year`; six categories (shipping, returns, care, sizing, orders, warranty) times six | `rs_articles` 1912-2029, verbatim |
| `SEARCH_PRESETS: list[dict]` | 3 rows, keys `label, query, target`: "Same meaning, different words" / `trail shoes feel cramped` / `size-boots`; "An exact name" / `Trailhead` / `warranty-resole`; "A typo" / `waterproofng` / `care-waterproof` | `rs_wow_presets` 2031-2058 minus `lesson` |
| `GEAR_CARE_ANSWERS: list[dict]` | 5 rows, keys `key, prompt, response` (`boots, bag, tent, shell, stove`) | `lc_seeds` 2656-2680 |
| `THRESHOLD_PROBES: list[dict]` | 6 rows, keys `text, kind, target`: goose-down launder (paraphrase, `bag`); tent away after a trip (paraphrase, `tent`); mud off Trailhead boots (paraphrase, `boots`); synthetic sleeping bag (swapped item, `None`); avoid washing the down bag (negation, `None`); capital of Australia (unrelated, `None`) | six of `lc_probes` 2682-2704 |
| `STRATEGY_VARIANTS: list[dict]` | 2 rows, keys `label, text`: shouting `HOW DO I WASH MY DOWN SLEEPING BAG?`; punctuation `How do I wash my down sleeping bag` (the paraphrase variant is dropped: `lc_strategies` is 2 x 2, section 16) | `lc_variants` 2706-2716 minus the third row |
| `CACHE_ASIDE_SCRIPT: dict` | keys `prefix, ask, answer, again`; `prefix` is `"At Cairn Outfitters: "`, prepended to the stored prompt and to both searches, exactly as the live run's `lc_taught_prompt` did (3202-3206). The prefix keeps the taught entry close only to questions that name the shop, so on a shared cache it is never served for another app's generic question. | `lc_script` 2718-2725 plus `lc_taught_prompt` |
| `SHOP_RECORDS: dict[str, list[dict]]` | `Customer` 6, `Product` 6, `Order` 8; ids are letters plus digits, no hyphen | `cr_records` 3378-3411, verbatim |
| `DANA_FACTS: list[dict]` | 5 rows, keys `text, topics` (no id, owner or namespace: those are Iris fields the notebook adds) | `am_memories` 4789-4817 |
| `MEMORY_QUESTIONS: list[dict]` | 3 rows, keys `text, target` (1-based index into `DANA_FACTS`): kitchen / 1; toes squashed / 2; somewhere freezing / 3 | `am_wow_presets` 4836-4865 minus `lesson` |
| `SESSION_TURNS: list[dict]` | 4 rows, keys `role, text`: USER "I am training for a 50 kilometre trail race in April."; ASSISTANT "Noted: a 50 km race in April. Do steep descents give you any trouble?"; USER "My left knee complains on steep downhills, so I always carry trekking poles."; USER "What should the kitchen avoid serving me?" | new; the last turn equals `MEMORY_QUESTIONS[0]["text"]` |

Module docstring states the constraints the tests enforce: no article says "cramped" or "shoes",
exactly one says "Trailhead", none says "waterproofng"; every shop id matches `^[A-Za-z]+[0-9]+$`;
every `THRESHOLD_PROBES` target is a `GEAR_CARE_ANSWERS` key or `None`; each memory question
shares no stem with its target fact; `SESSION_TURNS[-1]["text"] == MEMORY_QUESTIONS[0]["text"]`.

### `embedder.py`

```python
EMBEDDING_MODEL_NAME = "minishlab/potion-base-8M"
EMBEDDING_MODEL_FILES = ["config.json", "model.safetensors", "tokenizer.json"]

def load_embedding_model() -> "StaticModel":
    """Load potion-base-8M from the Hugging Face cache, downloading about 30 MB the first time.

    Tries snapshot_download(..., local_files_only=True) with allow_patterns=EMBEDDING_MODEL_FILES
    first, so a warm machine never touches the network, then the online call, then
    StaticModel.from_pretrained(<local path>). Raises RuntimeError with a one-sentence cause when
    neither works. There is no degraded fallback: a word-hashing embedder would make the
    paraphrase examples lie.
    """

def embed_texts(model, texts: list[str]) -> "np.ndarray":
    """Encode texts to a (len(texts), model.dim) float32 array of unit-length vectors."""
```

`os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")` at import (Windows). The
`.astype(np.float32).tobytes()` step stays in the notebook: "a vector is DIM x 4 raw bytes in a
hash field" is a Redis lesson.

### `formatting.py`

```python
def redis_cli(argv: list) -> str:
    """One command in redis-cli syntax, long values elided.

    Tokens are str()'d; a token with a space or a quote is double-quoted; bytes longer than 64
    are shown as "<N bytes>"; text longer than 120 characters is cut with a "<N chars>" marker.
    A line with an elided value is for reading, not pasting.
    """

def shared_stems(question: str, memory: str) -> list[str]:
    """The word stems two texts share, sorted; [] is the 'recalled by meaning' evidence.

    Lower-cases, drops a short stopword list, cuts -ing, -ed, -es or -s when at least three
    letters remain. Crude on purpose.
    """
```

`redis_cli` ported from 1153-1166 minus the scrub call; `shared_stems` from `am_stems` /
`am_overlap` 4880-4904.

## 6. The intro cell

`@app.cell(column=0, hide_code=True)`, `def intro():`, one `mo.md(rf"""...""")`, about 32 lines:

```
# Redis Iris basics

Agents do not have an intelligence problem. They have a context problem: the right facts, at the
right moment, from data you already hold. [Redis Iris](https://redis.io/docs/latest/develop/ai/context-engine/)
is the real-time context engine that supplies them: Redis Search underneath, then LangCache,
Context Retriever, and Agent Memory as hosted services on Redis Cloud. This notebook takes them
apart, one column per component, on Cairn Outfitters, a fictional outdoor-gear shop. Every step
shows the exact `redis-cli` command or JSON request behind it. There is no LLM anywhere in it.

| Component, left to right | Its one job here |
|---|---|
| Redis Search | Answer a question over 36 help articles by keyword, by meaning, and by both |
| Redis LangCache | Return a stored answer for a question asked in other words |
| Redis Context Retriever | Turn three Python classes into MCP tools an agent calls over your Redis |
| Redis Agent Memory | Recall a customer fact with no word in common with the question |

**Configuration.** Copy `.env.example` to `.env` next to `pyproject.toml`; marimo loads it when
the kernel starts. A column whose variables are missing says so and stops.

**Safety.** Everything this kernel writes carries the run tag `{RUN_TAG}`: Redis keys under
`{KEY_PREFIX}` with a one-hour `EXPIRE`, LangCache entries with `ttlMillis`, a surface named
`cairn-{RUN_TAG}`, memories owned by `cairn-{RUN_TAG}`. Each column ends with one Clean up button
whose label says what it deletes. Nothing is written until you press a button that says what it
writes.
```

## 7. Shared cell patterns (every builder copies these literally)

### 7.1 The not-configured stop

First statement of every configure cell. Only the column name and the variable list change:

```python
mo.stop(
    not all(os.environ.get(name) for name in ("LANGCACHE_HOST", "LANGCACHE_CACHE_ID", "LANGCACHE_API_KEY")),
    mo.callout(mo.md("LangCache is not configured. Set `LANGCACHE_HOST`, `LANGCACHE_CACHE_ID`, and `LANGCACHE_API_KEY` in the `.env` next to `pyproject.toml` (see `.env.example`), then restart the kernel."), kind="warn"),
)
```

Column 0: "Redis Search is not configured. Set `REDIS_URL` in ...". Column 2: "Context Retriever
is not configured. Set `CTX_ADMIN_KEY` and `REDIS_URL` in ...". Column 3: "Agent Memory is not
configured. Set `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY` in ...". Three-item
lists take the serial comma (VOICE.md, "Serial comma always"); `tests/test_voice.py` checks every
`mo.md` string for the pattern `` `X`, `Y` and `Z` `` and fails on it.

### 7.2 Connection failure and service errors

Redis: `except redis.RedisError as exc: mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while connecting. Check `REDIS_URL`."), kind="danger"))`. Never `str(exc)`, which can echo the URL.

HTTP: `except niquests.RequestException as exc: mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while reaching <Service>. Check `<HOST VARIABLE>`."), kind="danger"))`.

A non-2xx reply to a request that carried no secret is decoded in one sentence that prints the
body's `type` as received, plus the body: `_type = body.get("type", "") if isinstance(body, dict) else ""`,
then `mo.callout(mo.vstack([mo.md(f"<Service> answered `{status}` `{_type}`: {hint}."), mo.json(body if isinstance(body, dict) else {"body": body})]), kind="danger")`,
where `hint` is `{401: "check `<KEY VARIABLE>`", 404: "check `<ID VARIABLE>`"}.get(status, "read the body below")`.
`call_service` returns `None` for an empty body and a string for a non-JSON one, so every
`.get` on a body sits behind an `isinstance(body, dict)` check. Every configure cell stops on a
non-2xx check (`mo.stop(status != 200, <that callout>)`); none continues with a session that
just failed.

**The one request that carries a secret in its body**, `POST /context-surfaces` in `cr_create`,
has its own rule: on any non-2xx the cell renders the status and the string values of `type`,
`title` and `detail`, each cut at 200 characters, and nothing else. The body is never passed to
`mo.json`, never bound to a global, and never the cell's last expression, because no 400 or 422
validation body from the admin API has ever been observed (Context Retriever brief 2.7 documents
only 401 and 404) and validation frameworks commonly echo the offending input, which here would
include `data_source.connection_config.password`. The cell asserts the password is absent from
`json.dumps(body)` before rendering even those three strings. Section 10 cell 5 has the code and
`tests/test_safety.py::test_create_reply_is_never_rendered` enforces it.

### 7.3 Button, seed cell, example cell, Clean up

- A `run_button` is defined in one cell and read in the next. No cell reads its own element.
  Clean up buttons and `cr_create_button`, `am_write_button`, `am_session_button`,
  `lc_cache_aside_button` live in a markdown-plus-button cell. `rs_load_button` and
  `lc_store_button` are defined at the end of `rs_connect` and `lc_configure` and rendered under
  the status callout, so the seed button appears only once the column is configured and each of
  those columns stays at 11 cells.
- A `run_button` resets to `False` after the run it triggered without re-running dependents
  (`marimo/_plugins/ui/_impl/run_button.py`, `_on_update_completion` sets `_value = False`
  directly), so a seed cell's outputs persist until the cell re-runs for another reason.
- Seed cell, columns 0 and 3 (every id Clean up needs is computed in the configure cell): the
  native shape.
  ```python
  mo.stop(not rs_load_button.value, mo.callout(mo.md("Press *Load the help centre: 1 FT.CREATE, 36 HSET, 36 EXPIRE* above."), kind="info"))
  ...  # the commands
  rs_help_centre_loaded = True
  mo.vstack([...])
  ```
  While the button is unpressed the cell stops, `rs_help_centre_loaded` does not exist, and no
  example cell runs (a stopped cell's descendants never run, marimo brief 5.1).
- Seed or create cell, columns 1 and 2 (Clean up needs ids the service chose): the two-branch
  shape, never `mo.stop`.
  ```python
  if not lc_store_button.value:
      lc_stored_entries = []
      _output = mo.callout(mo.md("Press *Store 5 gear-care answers with ttlMillis 900000* above."), kind="info")
  else:
      lc_stored_entries = []
      ...  # the calls
      _output = mo.vstack([...])
  _output
  ```
  Names read by later cells are assigned in both branches.
- The first example cell of a column starts with `mo.stop(not <state name>, mo.callout(mo.md("Load the help centre first."), kind="info"))`
  (or "Store the answers first." / "Create the surface first." / "Write the memories first.").
  Every later example cell starts with the bare `mo.stop(not <state name>)`, so a column shows
  the note once, not four or five times. In columns 0 and 3 the line is also the dependency edge
  that keeps the example from running before the seed.
- A Clean up cell depends only on setup names, its column's configure-cell names (and, in column
  2, the offline `cr_data_model` cell's `cr_record_keys`), the seed cell's always-defined ids
  (columns 1 and 2) and its own button, and starts with `mo.stop(not <cleanup_button>.value)`.

### 7.4 Button labels (exact)

| Button | Label | `kind` |
|---|---|---|
| `rs_load_button` | `Load the help centre: 1 FT.CREATE, 36 HSET, 36 EXPIRE` | `success` |
| `rs_cleanup_button` | `Clean up: FT.DROPINDEX and UNLINK 36 keys` | `danger` |
| `lc_store_button` | `Store 5 gear-care answers with ttlMillis 900000` | `success` |
| `lc_cache_aside_button` | `Run the cache-aside loop: miss, store for 10 s, hit, expire, 404` | `success` |
| `lc_cleanup_button` | `Clean up: DELETE 5 entries by id` | `danger` |
| `cr_create_button` | `Create the surface, an agent key, and load 20 records (sends your database password)` | `success` |
| `cr_cleanup_button` | `Clean up: DELETE the surface and UNLINK 20 keys` | `danger` |
| `am_write_button` | `Write 5 memories about Dana in one POST` | `success` |
| `am_session_button` | `Send 4 session events and assemble the prompt` | `success` |
| `am_cleanup_button` | `Clean up: delete this owner's sessions and memories` | `danger` |

### 7.5 Wire views

HTTP: `mo.accordion({"Request as sent (credential masked)": mo.json(_wire), "Reply": mo.json(_body or {})})`, written in the cell. Redis: a fenced block from `redis_cli(argv)` where `argv` is rebuilt from redis-py (`Query.get_args()` plus `client.ft(index).get_params_args(params)`; `IndexDefinition.args` plus `Field.redis_args()`), never typed by hand.

### 7.6 Markdown and voice

Every markdown-only cell is `mo.md(r"""...""")` (never one-line `mo.md("...")`, MF007; MF007
inspects only cells whose whole body is one markdown call, so the one-line `mo.callout(mo.md("..."), kind=...)`
expressions inside code cells pass `check --strict`, checked 2026-09-30). Lead-ins end with a
colon and name actor and effect ("The cell below ... :"). Redis, LangCache, Context Retriever or
Agent Memory is the actor of the return-value sentence ("Redis returns `OK`"). Commands
uppercase and backticked. Every number from the live run is hedged ("in a live run on
2026-09-30"; "scores vary by model and phrasing"); every limit is flat ("a surface has no TTL").
Preview status stated flat once per title cell. No exclamation marks, no "developers" in prose,
sentence-case headings, serial commas.

Length, from VOICE.md's tutorial mechanics (10-25 words a sentence; two-sentence paragraphs;
one-sentence glosses; semicolons absent): a lead-in is at most two sentences of at most 25 words
each. The gloss under an output is one sentence. A title cell is two paragraphs of at most three
sentences each. Mechanism detail (which field types the index declares, what each admin call
does) goes into the gloss under the output or into the wire accordion, not into the lead-in. No
semicolon in any `mo.md` string; use a full stop. `tests/test_voice.py::test_no_sentence_over_30_words`
is the ceiling every string must clear, and the VOICE checklist pass happens at the scaffold step
(title cells, intro, `.env.example`, README skeleton) and again at assembly.

"cache hit" and "cache miss" are lowercase in prose (VOICE.md, semantic-caching terms); `HIT`,
`MISS` and `WRONG ANSWER` in capitals appear only as table-cell values and callout titles.

## 8. Column 0, Redis Search (11 cells, body budget 250 lines)

All cells `@app.cell(column=0)`; markdown-only cells add `hide_code=True`. Names: index
`idx:cairn:<run>:help`, key prefix `cairn:<run>:help:`. Connecting on load is read-only. Column 0
owns the unprefixed imports `NumericField`, `TagField`, `TextField`, `VectorField`,
`IndexDefinition`, `IndexType`, `Query` and `ResponseError` (section 17); no other column may
import those names.

**1. `rs_title`** (markdown + mermaid, hide_code, body 20 lines). Copy:

> # Redis Search
>
> Users ask in their own words, and a keyword index only knows yours. Redis Search indexes the
> hashes under a key prefix once, then answers by keyword (`BM25STD`), by meaning (a vector
> field), or by both fused in one command (`FT.HYBRID`, Redis 8.4 and later). Its one job in this
> column: answer a question over 36 Cairn Outfitters help articles all three ways.
>
> The other three columns are built on this engine. The embedding model, `potion-base-8M`, runs
> on your machine, so this column needs one setting, `REDIS_URL`. Docs:
> [Search and query](https://redis.io/docs/latest/develop/ai/search-and-query/).

No trial link anywhere in the notebook (VOICE.md: tutorials carry no Redis Cloud CTA; Redis
Cloud is a README prerequisite option and the FAQ answer "No.").

Mermaid: `flowchart LR; A[App] -->|embed locally| V[Vector]; V -->|HSET| H[(Hash under cairn:run:help:)]; H -.->|index watches the prefix| I[FT index]; A -->|FT.SEARCH / FT.HYBRID| I`.

**2. `rs_connect`** (configure: database, local model, and the load button; body 36 lines).
Globals: `rs_client`, `rs_redis_version`, `rs_has_hybrid`, `rs_index_name`, `rs_key_prefix`,
`rs_article_keys`, `rs_article_titles`, `rs_embedding_model`, `rs_article_vectors`,
`rs_embedding_dimension`, `rs_load_button`, plus the eight owned imports at the top of the cell
so the reader sees where they come from. Code:

```python
from redis.commands.search.field import NumericField, TagField, TextField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from redis.exceptions import ResponseError

mo.stop(not os.environ.get("REDIS_URL"), mo.callout(mo.md("Redis Search is not configured. Set `REDIS_URL` in the `.env` next to `pyproject.toml` (see `.env.example`), then restart the kernel."), kind="warn"))
try:
    rs_client = connect_redis(os.environ["REDIS_URL"])      # the URL is never a notebook global
    rs_client.ping()                                        # PING -> PONG
    rs_redis_version = rs_client.info("server")["redis_version"]   # INFO server
except redis.RedisError as exc:
    mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while connecting. Check `REDIS_URL`."), kind="danger"))
rs_has_hybrid = tuple(int(part) for part in rs_redis_version.split(".")[:2]) >= (8, 4)
rs_index_name = f"idx:cairn:{RUN_TAG}:help"
rs_key_prefix = f"{KEY_PREFIX}help:"
rs_article_keys = [f"{rs_key_prefix}{article['id']}" for article in cairn.HELP_ARTICLES]   # Clean up never scans
rs_article_titles = dict(zip(rs_article_keys, (article["title"] for article in cairn.HELP_ARTICLES)))   # key -> title, for FT.HYBRID rows
try:
    with mo.status.spinner(title=f"Loading {EMBEDDING_MODEL_NAME}", subtitle="about 30 MB the first time"):
        rs_embedding_model = load_embedding_model()
except RuntimeError as exc:
    mo.stop(True, mo.callout(mo.md(f"`{EMBEDDING_MODEL_NAME}` is not in the Hugging Face cache and could not be downloaded ({exc}). Connect once, or copy the model files in, then restart the kernel."), kind="danger"))
rs_article_vectors = embed_texts(rs_embedding_model, [f"{article['title']}. {article['body']}" for article in cairn.HELP_ARTICLES])
rs_embedding_dimension = int(rs_article_vectors.shape[1])
rs_load_button = mo.ui.run_button(label="Load the help centre: 1 FT.CREATE, 36 HSET, 36 EXPIRE", kind="success")
mo.vstack([
    mo.callout(mo.md(f"Connected to Redis {rs_redis_version}. `FT.HYBRID` {'is available' if rs_has_hybrid else 'needs Redis 8.4 or later, so the hybrid rank below becomes a note'}. {EMBEDDING_MODEL_NAME}: {rs_embedding_dimension} dimensions, {rs_embedding_dimension * 4} bytes per vector, computed on this machine."), kind="success"),
    mo.md(r"""The button below writes one index and 36 hashes to your database, each hash with a one-hour `EXPIRE`. The index watches the prefix `cairn:<run>:help:`, so every `HSET` under it is indexed on write:"""),
    rs_load_button,
])
```

The `RuntimeError` message from `load_embedding_model` is one sentence about the cache or the
network and never contains a path with a credential, so it may be printed. Wire: `PING`,
`INFO server`. The connection status and the seed button share a cell so the button appears only
once the column is configured.

**3. `rs_load`** (seed, native shape, body 36 lines). Globals: `rs_help_centre_loaded` (`True`,
assigned after the writes), `rs_documents_indexed`, `rs_schema`, `rs_index_definition`. Starts with
`mo.stop(not rs_load_button.value, mo.callout(mo.md("Press *Load the help centre: 1 FT.CREATE, 36 HSET, 36 EXPIRE* above."), kind="info"))`.
Then: schema `TextField("title", weight=2.0), TextField("body"), TagField("category"),
NumericField("year", sortable=True), VectorField("embedding", "HNSW", {"TYPE": "FLOAT32", "DIM":
rs_embedding_dimension, "DISTANCE_METRIC": "COSINE"})`; `rs_client.ft(rs_index_name).create_index(rs_schema,
definition=IndexDefinition(prefix=[rs_key_prefix], index_type=IndexType.HASH))` in
`try/except ResponseError` tolerated when `"already exists" in str(exc).lower()`; three
`rs_client.pipeline(transaction=False)` chunks of 12 articles, each `pipe.hset(key, mapping={"title",
"body", "category", "year", "embedding": vector.astype(np.float32).tobytes()})` then
`pipe.expire(key, KEY_TTL_SECONDS)`, `pipe.execute()`, `time.sleep(0.25)` (under the free tier's
100 ops/s); `_info = rs_client.ft(rs_index_name).info()`; `rs_help_centre_loaded = True`.
Wire, exact (captured argv, redis-py 8.1.0): `FT.CREATE idx:cairn:<run>:help ON HASH PREFIX 1
cairn:<run>:help: SCORE 1.0 SCHEMA title TEXT WEIGHT 2.0 body TEXT WEIGHT 1.0 category TAG
SEPARATOR , year NUMERIC SORTABLE embedding VECTOR HNSW 6 TYPE FLOAT32 DIM 256 DISTANCE_METRIC
COSINE`; `HSET cairn:<run>:help:ship-times title "How long does delivery take?" body "..."
category shipping year 2025 embedding "<1024 bytes>"`; `EXPIRE cairn:<run>:help:ship-times 3600`;
`FT.INFO`. Renders `mo.vstack([mo.hstack([mo.stat(num_docs, label="num_docs"), mo.stat(
hash_indexing_failures, label="hash_indexing_failures")]), mo.md(f"```\n{redis_cli(['FT.CREATE',
rs_index_name, *rs_index_definition.args, 'SCHEMA', *[arg for field in rs_schema for arg in
field.redis_args()]])}\n{redis_cli(first_hset_argv)}\n{redis_cli(['EXPIRE', first_key,
KEY_TTL_SECONDS])}\n```"), mo.md(gloss)])`. Gloss, two sentences: "Redis returns `OK` for the
`FT.CREATE` and indexes each hash as it lands. `FT.INFO` reports `num_docs` 36 and
`hash_indexing_failures` 0, so no hash failed to index." The schema detail the old lead-in
carried (`TEXT` with title weight 2, `TAG`, `NUMERIC`, a 256-dimension HNSW `VECTOR` with cosine
distance) is what the `FT.CREATE` line in the output shows, so the copy does not repeat it.

**4. `rs_full_text`** (fixed, no control, body 18 lines). The first example cell, so it carries
the note: `mo.stop(not rs_help_centre_loaded, mo.callout(mo.md("Load the help centre first."), kind="info"))`.
`Query("hiking boots").scorer("BM25STD").with_scores().return_fields("title", "category").paging(0, 5)`;
`rs_full_text_result = rs_client.ft(rs_index_name).search(query)`. Wire: `FT.SEARCH
idx:cairn:<run>:help "hiking boots" SCORER BM25STD WITHSCORES RETURN 2 title category DIALECT 2
LIMIT 0 5`, rendered with `redis_cli(["FT.SEARCH", rs_index_name, *query.get_args()])`. Renders the
lead-in, the redis-cli line and `mo.ui.table([{"title", "category", "score": round(doc.score, 3)}],
selection=None, show_download=False, pagination=False)`. Lead-in (two sentences): "The cell below
runs one keyword search for *hiking boots*, scored with `BM25STD`. Redis returns the match count
and the top five rows with their scores:". Gloss: "A title match counts double because the schema
gave `title` weight 2." No score range in the copy (the BM25STD range is an open question).

**5. `rs_search_controls`** (markdown + one input, body 10 lines). `mo.stop(not rs_help_centre_loaded)`
so it appears after the load. Global: `rs_question = mo.ui.text(value="will it fit my feet",
label="Ask the help centre", full_width=True)`. The radius is the literal `0.5` in the next cell
(section 16 cut applied up front; the slider is gone). Lead-in: "Redis runs the same query vector
two ways. `KNN 3` returns the three nearest articles whatever their distance, and `VECTOR_RANGE`
returns only what lies within a radius, which can be nothing:". Then one line under the box: "Ask
*best pizza near me* for a miss."

**6. `rs_knn_versus_range`** (body 34 lines). `mo.stop(not rs_help_centre_loaded)`. Globals:
`rs_query_vector_bytes`, `rs_knn_result`, `rs_range_result`. `rs_query_vector_bytes = embed_texts(rs_embedding_model, [rs_question.value])[0].astype(np.float32).tobytes()`;
`_knn = Query("*=>[KNN 3 @embedding $vector AS vector_score]").sort_by("vector_score").return_fields("title", "category", "vector_score").paging(0, 3)`;
`_range = Query("@embedding:[VECTOR_RANGE $radius $vector]=>{$YIELD_DISTANCE_AS: vector_score}").sort_by("vector_score").return_fields("title", "category", "vector_score").paging(0, 10)`;
`rs_client.ft(rs_index_name).search(_knn, {"vector": rs_query_vector_bytes})` and `search(_range, {"radius": 0.5, "vector": rs_query_vector_bytes})`.
Wire: `FT.SEARCH idx:cairn:<run>:help "*=>[KNN 3 @embedding $vector AS vector_score]" RETURN 3 title category vector_score SORTBY vector_score ASC DIALECT 2 LIMIT 0 3 PARAMS 2 vector "<1024 bytes>"` and `FT.SEARCH ... "@embedding:[VECTOR_RANGE $radius $vector]=>{$YIELD_DISTANCE_AS: vector_score}" RETURN 3 title category vector_score SORTBY vector_score ASC DIALECT 2 LIMIT 0 10 PARAMS 4 radius 0.5 vector "<1024 bytes>"`, both via `redis_cli(["FT.SEARCH", rs_index_name, *q.get_args(), *rs_client.ft(rs_index_name).get_params_args(params)])`.
Renders: KNN `mo.ui.table` (title, category, vector_score rounded to 3); for the range either a
table or `mo.callout(mo.md("0 articles within radius 0.5. KNN could never say this, and this
empty answer is what LangCache turns into a cache miss in the next column."), kind="warn",
title="MISS")`; both redis-cli lines in `mo.accordion({"redis-cli": ...})`. Gloss: "KNN always
fills three rows. The range answers with what is close enough, or with nothing." To change the
radius, edit the literal `0.5` in the cell (the README troubleshooting entry says so).

**7. `rs_prefiltered`** (fixed, no control, body 26 lines). `mo.stop(not rs_help_centre_loaded)`.
`_vector = embed_texts(rs_embedding_model, ["where is my parcel"])[0].astype(np.float32).tobytes()`;
`_unfiltered = Query("*=>[KNN 5 @embedding $vector AS vector_score]")`, `_filtered = Query("(@category:{returns|shipping} @year:[2024 2026])=>[KNN 5 @embedding $vector AS vector_score]")`, both `.sort_by("vector_score").return_fields("title", "category", "year", "vector_score").paging(0, 5)`.
Wire: `FT.SEARCH idx:cairn:<run>:help "(@category:{returns|shipping} @year:[2024 2026])=>[KNN 5 @embedding $vector AS vector_score]" RETURN 4 title category year vector_score SORTBY vector_score ASC DIALECT 2 LIMIT 0 5 PARAMS 2 vector "<1024 bytes>"` (Redis Search brief 4.4) and the `*=>[KNN 5 ...]` twin. Renders the lead-in, two redis-cli lines and two stacked `mo.ui.table`s (title, category, year, vector_score). Lead-in (two sentences): "The cell below runs the same `KNN 5` twice for *where is my parcel*, once over every article and once only over returns and shipping articles from 2024 on. The filtered query string is the whole lesson:". Gloss: "The filter runs inside the vector search, so all five rows qualify, instead of a top five with the wrong ones thrown away."

**8. `rs_engine_preset`** (markdown + dropdown, body 10 lines). `mo.stop(not rs_help_centre_loaded)`.
`rs_preset = mo.ui.dropdown(options={preset["label"]: preset["query"] for preset in cairn.SEARCH_PRESETS}, value="Same meaning, different words", label="Question")`. Lead-in (two sentences): "Keyword search matches words and vector search matches meaning, and each fails where the other wins. The cell below sends one question through `BM25STD`, through KNN, and through `FT.HYBRID`, which fuses both rankings on the server:". The three per-preset lessons and the live-run claim move to the gloss under the table in the next cell.

**9. `rs_engine_results`** (the wow, body 44 lines). `mo.stop(not rs_help_centre_loaded)`. Globals:
`rs_engine_question`, `rs_engine_target`, `rs_engine_rows`. `rs_engine_question = rs_preset.value`;
target from `cairn.SEARCH_PRESETS`. BM25: `Query(question).scorer("BM25STD").return_fields("title").paging(0, 10)`;
KNN: `Query("*=>[KNN 10 @embedding $vector AS vector_score]").sort_by("vector_score").return_fields("title").paging(0, 10)`;
hybrid, only when `rs_has_hybrid`, as an explicit argv in `try/except ResponseError` (backstop; the
version gate runs first): `rs_hybrid_argv = ["FT.HYBRID", rs_index_name, "SEARCH", question,
"SCORER", "BM25STD", "VSIM", "@embedding", "$vector", "KNN", 2, "K", 10, "COMBINE", "RRF", 4,
"WINDOW", 20, "CONSTANT", 60, "LOAD", 1, "@__key", "LIMIT", 0, 10, "PARAMS", 2, "vector",
_vector]`; `_reply = rs_client.execute_command(*rs_hybrid_argv)`; RESP2 flat pairs:
`_fields = dict(zip(_reply[::2], _reply[1::2])); _hybrid_keys = [dict(zip(row[::2], row[1::2]))["__key"] for row in _fields["results"]]`;
titles for the table come from `rs_article_titles[key]` (built in `rs_connect` from
`cairn.HELP_ARTICLES`, zero extra commands). `LOAD 1 @__key` and this row parse are exactly what
the live run sent and read (`rs_hybrid_args` and `rs_parse_hybrid`, iris_basics.py 2214-2258, on
Redis 8.6.2); the Redis Search brief's 4.5 shows `LOAD 2 @__key @title` and calls it "this exact
argv ran", which is wrong, and the multi-field `LOAD` is not used here. redis-py's
`hybrid_search()` is not used (experimental, warns on every call). Renders
`mo.ui.table(selection=None, pagination=False)` with columns article, BM25 rank, vector rank,
hybrid rank over the union of the three top-10 lists, `-` where an engine did not rank it, the
target title prefixed with a star; the three redis-cli lines in an accordion; when `rs_has_hybrid`
is False, `mo.callout(mo.md(f"`FT.HYBRID` needs Redis 8.4 or later. This database is {rs_redis_version}."), kind="info")`
in place of the hybrid column. Gloss under the table, one short paragraph chosen by the preset:
"Same meaning, different words": "*Trail shoes feel cramped* shares no word with the boot-sizing
guide, so `BM25STD` returns nothing and the vector search ranks it first."; "An exact name":
"*Trailhead* is a product name the model has never seen, so the keyword engine wins."; "A typo":
"*waterproofng* matches no word, yet its vector still lands on waterproofing." Then one hedged
line for all three: "In a live run on 2026-09-30, `FT.HYBRID` kept the right article first in all
three cases." Wire: `FT.HYBRID idx:cairn:<run>:help SEARCH "trail shoes feel cramped" SCORER
BM25STD VSIM @embedding $vector KNN 2 K 10 COMBINE RRF 4 WINDOW 20 CONSTANT 60 LOAD 1 @__key
LIMIT 0 10 PARAMS 2 vector "<1024 bytes>"`.

**10. `rs_cleanup_button`** (markdown + button, body 6 lines). Lead-in (two sentences): "Clean up
drops the index with `FT.DROPINDEX` and no `DD`, then unlinks the 36 keys by name. Nothing is
scanned, and every key would expire within the hour in any case:". The reason for no `DD` (it
deletes every key under the prefix, and this notebook never trusts a prefix over a list it holds)
is the gloss under the Clean up output.

**11. `rs_cleanup`** (body 14 lines). Depends on `rs_client`, `rs_index_name`, `rs_article_keys`,
`rs_cleanup_button`. `mo.stop(not rs_cleanup_button.value)`; `rs_client.ft(rs_index_name).dropindex()`
with `except ResponseError` tolerated when `re.search(r"index[_ ]not[_ ]found|unknown index|no such index", str(exc), re.I)`
(Redis 8.6 says `SEARCH_INDEX_NOT_FOUND Index not found`); `rs_keys_removed = rs_client.unlink(*rs_article_keys)`.
Wire: `FT.DROPINDEX idx:cairn:<run>:help`; `UNLINK cairn:<run>:help:ship-times ... (36 keys)`.
Renders `mo.callout(kind="success")`: "Dropped 1 index (or: the index was already gone), unlinked
36 of 36 keys. Redis returns the count of keys that existed." plus the `DD` gloss.

Requests to Redis in a full run: about 85 commands, all under the free tier's 100 ops/s.

## 9. Column 1, LangCache (11 cells, body budget 225 lines)

All cells `@app.cell(column=1)`. Every search sends `maxResults` 5 and passes through
`lc_own_hits`, so another app's entry on a shared cache is shown as `{"hidden": "another app's
entry", "similarity": s}` and never its prompt or response. Every store sends `ttlMillis`.
Nothing calls `/flush` or `DELETE /entries` with a body. Column 1 owns no unprefixed import.

**1. `lc_title`** (markdown + mermaid + one accordion, hide_code, body 22 lines). Copy:

> # Redis LangCache
>
> Users rarely ask the same question the same way twice, and an exact-key cache misses every
> rewording. Redis LangCache matches prompts by meaning: it embeds the prompt, searches the cache,
> and returns the stored answer when the similarity clears a threshold. Its one job in this
> column: return a stored answer for a question asked in other words.
>
> A match is a score above a line, so a wrong answer for a question that only sounds similar is
> possible. The threshold is the dial, and this column turns it. LangCache is in public preview on
> Redis Cloud. Settings: `LANGCACHE_HOST`, `LANGCACHE_CACHE_ID`, and `LANGCACHE_API_KEY`. Docs:
> [LangCache](https://redis.io/docs/latest/develop/ai/context-engine/langcache/),
> [API reference](https://redis.io/docs/latest/develop/ai/context-engine/langcache/api-reference/).

Mermaid: `flowchart LR; P[Prompt] --> S[POST /entries/search]; S -->|similarity >= threshold| H[Stored answer]; S -->|data: empty| L[Call the LLM]; L --> W[POST /entries with ttlMillis]; W -.-> S`.
Accordion "The same calls with the `langcache` SDK (not installed here)": a fenced block in the
brief's verified shape, `LangCache(server_url=..., cache_id=..., api_key=...)`,
`.search(prompt=..., similarity_threshold=0.85)`, `.set(prompt=..., response=..., ttl_millis=...)`,
`.delete_by_id(entry_id=...)`, and one line: "This notebook sends the JSON itself so you can
read it."

**2. `lc_configure`** (configure plus the store button, body 36 lines). Globals: `lc_service`,
`lc_entry_ttl_milliseconds`, `lc_own_hits`, `lc_check_status`, `lc_check_wire`, `lc_store_button`.
Code:

```python
mo.stop(not all(os.environ.get(name) for name in ("LANGCACHE_HOST", "LANGCACHE_CACHE_ID", "LANGCACHE_API_KEY")), mo.callout(mo.md("LangCache is not configured. Set `LANGCACHE_HOST`, `LANGCACHE_CACHE_ID`, and `LANGCACHE_API_KEY` in the `.env` next to `pyproject.toml` (see `.env.example`), then restart the kernel."), kind="warn"))
_host = os.environ["LANGCACHE_HOST"].strip().rstrip("/")
if "://" not in _host:
    _host = "https://" + _host
lc_service = niquests.Session(
    base_url=f"{_host}/v1/caches/{os.environ['LANGCACHE_CACHE_ID'].strip()}",
    headers={
        "Authorization": f"Bearer {os.environ['LANGCACHE_API_KEY'].strip()}",   # the key lives here and nowhere else
        "Accept": "application/json",
        "Content-Type": "application/json",   # pinned; niquests' default adds ;charset=utf-8, untested against the services
        "User-Agent": "redis-iris-basics",
    },
    timeout=(5, 30),        # seconds: connect, read
    disable_http3=True,     # HTTP/1.1 or HTTP/2 only, so the wire view reads the same on every machine
)
lc_entry_ttl_milliseconds = 900_000   # 15 minutes: every entry this column stores expires on its own

def lc_own_hits(reply, own_ids):
    """This kernel's hits, and a copy of every hit that is safe to show."""
    _hits = reply.get("data", []) if isinstance(reply, dict) else []
    mine = [hit for hit in _hits if hit.get("id") in own_ids]
    shown = [hit if hit.get("id") in own_ids else {"hidden": "another app's entry", "similarity": hit.get("similarity")} for hit in _hits]
    return mine, shown

try:
    lc_check_status, _body, lc_check_wire = call_service(lc_service, "POST", "/entries/search", json_body={"prompt": "Cairn Outfitters connection check", "maxResults": 1})
except niquests.RequestException as exc:
    mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while reaching LangCache. Check `LANGCACHE_HOST`."), kind="danger"))
_type = _body.get("type", "") if isinstance(_body, dict) else ""
_hint = {401: "check `LANGCACHE_API_KEY`", 404: "check `LANGCACHE_CACHE_ID`"}.get(lc_check_status, "read the body below")
mo.stop(lc_check_status != 200, mo.callout(mo.vstack([mo.md(f"LangCache answered `{lc_check_status}` `{_type}`: {_hint}."), mo.json(_body if isinstance(_body, dict) else {"body": _body})]), kind="danger"))
lc_store_button = mo.ui.run_button(label="Store 5 gear-care answers with ttlMillis 900000", kind="success")
mo.vstack([
    mo.callout(mo.md(f"Connected: LangCache answered 200 in {lc_check_wire['ms']} ms."), kind="success"),
    mo.accordion({"Request as sent (credential masked)": mo.json(lc_check_wire)}),
    mo.md(r"""The button below stores five gear-care answers, each with `ttlMillis` 900000 so it expires in 15 minutes whether or not you press Clean up. LangCache answers `201` with an `entryId`:"""),
    lc_store_button,
])
```

The cell stops on a non-200 check like `cr_configure` and `am_configure`, so `lc_service` and the
Store button exist only behind a working key and cache id. The 200 body is never shown (it may
hold other apps' entries). `lc_own_hits` is the one function defined in a column-1 cell; it is
the whole client-side filter and stays visible.

**3. `lc_store`** (seed, two-branch shape, body 24 lines). Globals: `lc_stored_entries` (list of
`{"key", "prompt", "entryId"}`; `[]` when unpressed), `lc_store_wire`. Pressed branch: for each
row of `cairn.GEAR_CARE_ANSWERS`: `call_service(lc_service, "POST", "/entries", json_body={"prompt":
row["prompt"], "response": row["response"], "ttlMillis": lc_entry_ttl_milliseconds})` -> 201
`{"entryId": "..."}`; any other status appends `{"key", "status", "type"}` to a problems list and
skips the row (already-stored ids survive). Renders `mo.ui.table(selection=None)` of key, prompt,
entryId; `mo.accordion({"First request as sent (credential masked)": mo.json(lc_store_wire), "Reply": mo.json(first body)})`; a danger callout with the problems if any. Gloss (two sentences): "LangCache embeds each prompt as it is stored and answers `201` with the `entryId` you will delete later. Those ids are the only handle on the entries, because a cache cannot be listed."

**4. `lc_anatomy_of_a_hit`** (body 20 lines). The first example cell:
`mo.stop(not lc_stored_entries, mo.callout(mo.md("Store the answers first."), kind="info"))`. Body
`{"prompt": "My rain jacket stopped beading water. How do I fix that?", "similarityThreshold": 0.85, "searchStrategies": ["semantic"], "maxResults": 5}` -> 200 `{"data": [{"id", "prompt", "response", "attributes", "similarity", "searchStrategy"}]}`; `_mine, _shown = lc_own_hits(_body, {entry["entryId"] for entry in lc_stored_entries})`. Renders the lead-in, `mo.accordion({...request..., "Reply (other apps' entries hidden)": mo.json({"data": _shown})}, multiple=True)` expanded, then the gloss. Lead-in (two sentences): "The cell below asks a question none of the five prompts contains, at `similarityThreshold` 0.85, the console default for a new service. LangCache returns every entry whose similarity clears that line:". Gloss (three short sentences): "`similarity` is the cosine similarity of the two prompt embeddings. In a live run on 2026-09-30 the rain-shell answer came back at 0.896 with no shared noun but *rain*. A miss is `200` with an empty `data`, never a `404`."

**5. `lc_probes_and_slider`** (auto, read-only, six requests, body 22 lines). `mo.stop(not lc_stored_entries)`.
Globals: `lc_probe_scores` (list of `{"text", "kind", "target", "best_key", "similarity"}`),
`lc_threshold = mo.ui.slider(0.5, 1.0, step=0.01, value=0.85, debounce=True, show_value=True, label="similarityThreshold")`.
For each of `cairn.THRESHOLD_PROBES`: `POST /entries/search {"prompt": text, "similarityThreshold": 0.5, "searchStrategies": ["semantic"], "maxResults": 5}`; keep the best own hit's key (by entryId lookup) and similarity, or `None`. Renders the lead-in and the slider. Lead-in (two sentences): "The cell below searches six labelled questions once each at `similarityThreshold` 0.50, low enough to see every score the server computed. The slider re-applies the cut on your machine, the same cut the server applies when you send the value:".

**6. `lc_threshold_table`** (no requests, body 22 lines). Verdict per probe at `t = lc_threshold.value`: `HIT` when `best_key == target and similarity >= t`; `WRONG ANSWER` when `similarity >= t` and `best_key != target` (including `target is None`); `MISS` otherwise. Renders `mo.ui.table(rows, selection=None, pagination=False, style_cell=...)` with columns question, kind, best match, similarity, verdict; `style_cell` colours the verdict cell green, red or grey. Lead-in: "Raise the slider until the red rows go, and watch the grey ones multiply:". Gloss (two sentences): "In a live run on 2026-09-30 the worst wrong answer scored 0.950, the synthetic bag served the down-bag answer, so nothing wrong was left from 0.96 up and most paraphrases missed too. Scores vary by model and phrasing, and the trade-off does not."

**7. `lc_strategies`** (auto, four requests, body 22 lines). `mo.stop(not lc_stored_entries)`.
For each of the two `cairn.STRATEGY_VARIANTS` and each of `["exact"]`, `["semantic"]` (2 x 2, the section 16 cut applied up front): `POST /entries/search {"prompt": text, "similarityThreshold": 0.85, "searchStrategies": strategies, "maxResults": 5}`; cell text `HIT 1.00`, `HIT 0.91`, `HIT (wrong entry)`, or `MISS`. Renders `mo.ui.table(selection=None, pagination=False)`: variant, prompt sent, exact, semantic. Lead-in (two sentences): "`searchStrategies` picks the matcher: `exact` is a case-insensitive string compare that reports `similarity` 1, and `semantic` is the embedding. The cell below sends two spellings of a stored prompt through each:". Gloss (hedged, two sentences): "The default is `semantic` alone. In a live run on 2026-09-30, *HOW DO I WASH MY DOWN SLEEPING BAG?* hit under `exact` and missed under `semantic`, and dropping the question mark did the reverse."

**8. `lc_cache_aside_button`** (markdown + button, body 6 lines). Lead-in (two sentences): "The button below plays the cache-aside loop with you as the model: a miss, a store with `ttlMillis` 10000, a hit on a reworded question, a wait, a second miss, then a `DELETE` that finds nothing. Every prompt is prefixed *At Cairn Outfitters:* so the entry stays clear of other apps' questions on a shared cache:".

**9. `lc_cache_aside`** (body 34 lines). Nothing later depends on it, so the native shape: `mo.stop(not lc_cache_aside_button.value, mo.callout(mo.md("Press *Run the cache-aside loop: miss, store for 10 s, hit, expire, 404* above."), kind="info"))` first; then, in order, with `_prefix = cairn.CACHE_ASIDE_SCRIPT["prefix"]` prepended to every prompt sent (stored and searched), exactly as the live run's `lc_taught_prompt` did: search `ask` at 0.85 (own hits empty: MISS); `POST /entries {"prompt": _prefix + ask, "response": answer, "ttlMillis": 10000}` -> 201, `_taught_id` (cell-local); search `again` -> HIT, own ids include `_taught_id`; `with mo.status.spinner(title="Waiting 12 s for ttlMillis to fire"): time.sleep(12)`; search `again` -> MISS; `DELETE /entries/{_taught_id}` -> `404 {"type": "/errors/resource-not-found", ...}` (live wording; `/errors/cache/entry/not-found` in the spec; a `204` means the delete won the race and is reported as "not expired yet: deleted now"). Renders `mo.ui.table(selection=None, pagination=False)` with columns step, request, status, similarity, ms, and the gloss (two sentences): "The miss at step 1 is where your app would call the model, and step 2 is you storing its answer. The `404` at the end is why this notebook can rely on TTL alone." No similarity number in the copy: the live 0.894 was measured on the prefixed text and is re-measured on the first live run (section 18). Prompts from `cairn.CACHE_ASIDE_SCRIPT`.

**10. `lc_cleanup_button`** (markdown + button, body 6 lines). Lead-in (two sentences): "Clean up sends one `DELETE /entries/{entryId}` per id this kernel stored. A `204` is a deletion, and a `404` means the TTL got there first:". The rest (a cache cannot be listed, so an entry left by a crashed kernel is found only by its TTL, and every entry here carried one) is the gloss under the Clean up output.

**11. `lc_cleanup`** (body 16 lines). Depends on `lc_service`, `lc_stored_entries`, `lc_cleanup_button`. `mo.stop(not lc_cleanup_button.value)`; per entry `call_service(lc_service, "DELETE", f"/entries/{entryId}")`; 204 counted deleted; 404 with `re.search(r"/(entry/not-found|resource[-/]not-found)$", body.get("type", ""))` counted expired; otherwise appended to problems. Renders "Deleted 5, already expired 0." or a danger callout with `mo.json(problems)`; "Nothing stored in this run" when the list is empty. Note for the builder: the session pins `Content-Type: application/json`, so this bodiless `DELETE` carries that header, while the verified request (LangCache brief 2.3) carried neither body nor `Content-Type`. HTTP permits it; it is on the first-live-run checklist, and the fallback is `headers={"Content-Type": None}` on that one call (which would need a `headers=` pass-through on `call_service`).

Requests in a full run: 1 + 5 + 1 + 6 + 4 + 5 + 5 = 27.

## 10. Column 2, Context Retriever (11 cells, body budget 285 lines)

All cells `@app.cell(column=2)`. The three `ContextModel` classes are named `Customer`, `Product`,
`Order` without the `cr_` prefix: the service names its tools after the classes
(`get_customer_by_id`), and a comment in the cell says so. Column 2 also owns the unprefixed
imports `unquote`, `urlsplit` (cell 2) and `Any`, `ContextField`, `ContextModel`,
`ContextRelationship`, `export_data_model` (cell 3). Every other global carries `cr_`. Redis
exceptions are written `redis.RedisError` and `redis.ResponseError` (column 0 owns the bare name).

**1. `cr_title`** (markdown + mermaid, hide_code, body 22 lines). Copy:

> # Redis Context Retriever
>
> An agent that writes its own queries against your database is a governance problem. Redis
> Context Retriever takes a data model you declare in Python, provisions indexes in your own
> Redis, and generates MCP tools whose arguments are constrained by that model. Its one job in
> this column: turn three Python classes into MCP tools an agent can call over your Redis.
>
> The agent gets `get_customer_by_id` and `filter_customer`, never a raw command. Context
> Retriever is in preview. This column needs an admin key (`CTX_ADMIN_KEY`) and, in `REDIS_URL`, a
> scratch database with Search and JSON that the hosted service can reach, because the service is
> given that database's password. Docs:
> [Context Retriever](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/).

Mermaid: `flowchart LR; M[ContextModel classes] -->|export_data_model| J[data_model JSON]; J -->|POST /context-surfaces| S[Surface + agent key]; S -->|writes JSON, builds indexes| R[(Your Redis)]; A[Agent] -->|tools/call over MCP| S`.

**2. `cr_configure`** (body 34 lines). Globals: `cr_admin_service`, `cr_redis`, `cr_api_url`,
`cr_mcp_url_default`, `cr_surface_name`, `cr_data_source`, `cr_check_status`, `cr_check_wire`, plus
the owned imports `unquote`, `urlsplit`. Code:

```python
from urllib.parse import unquote, urlsplit

mo.stop(not (os.environ.get("CTX_ADMIN_KEY") and os.environ.get("REDIS_URL")), mo.callout(mo.md("Context Retriever is not configured. Set `CTX_ADMIN_KEY` and `REDIS_URL` in the `.env` next to `pyproject.toml` (see `.env.example`), then restart the kernel."), kind="warn"))
cr_api_url = os.environ.get("CTX_API_URL", "https://cloud.redis.io/context-surfaces").rstrip("/")
cr_mcp_url_default = os.environ.get("CTX_MCP_URL", "https://gcp-us-east4.context-surfaces.redis.io/mcp")   # fallback only; the live address comes from the agent-key listing
cr_surface_name = f"cairn-{RUN_TAG}"
cr_admin_service = niquests.Session(
    base_url=f"{cr_api_url}/api/v1",
    headers={"X-API-Key": os.environ["CTX_ADMIN_KEY"].strip(), "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "redis-iris-basics"},
    timeout=(5, 60),        # surface creation and import do real work
    disable_http3=True,
)

def cr_data_source():
    """The data_source block of the create request, built from REDIS_URL at call time so the password is never a global."""
    _url = urlsplit(os.environ["REDIS_URL"])
    return {"type": "redis", "connection_config": {
        "addr": f"{_url.hostname}:{_url.port or 6379}",
        "username": unquote(_url.username or "default"),
        "password": unquote(_url.password or ""),
        "db": int(_url.path.lstrip("/") or 0),
        "tls_enabled": _url.scheme == "rediss",   # TLS is the scheme; redis-py's connection_kwargs never carry an ssl key
        "pool_size": 2, "min_idle_conns": 0,      # a free database allows 30 connections
    }}

try:
    cr_check_status, _body, cr_check_wire = call_service(cr_admin_service, "GET", "/context-surfaces", params={"page_size": 1})   # read-only key check
    cr_redis = connect_redis(os.environ["REDIS_URL"])
    cr_redis.ping()
except niquests.RequestException as exc:
    mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while reaching the admin API. Check `CTX_API_URL`."), kind="danger"))
except redis.RedisError as exc:
    mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while connecting. Check `REDIS_URL`."), kind="danger"))
mo.stop(cr_check_status != 200, mo.callout(mo.vstack([mo.md(f"The admin API answered `{cr_check_status}` `{_body.get('type', '') if isinstance(_body, dict) else ''}`: {'check `CTX_ADMIN_KEY`' if cr_check_status == 401 else 'check `CTX_API_URL`' if cr_check_status == 404 else 'read the body below'}."), mo.json(_body or {})]), kind="danger"))
mo.vstack([mo.callout(mo.md("Admin API reachable with this key; Redis answered `PONG`."), kind="success"), mo.accordion({"Request as sent (credential masked)": mo.json(cr_check_wire)})])
```

The listing body is never shown (it names other surfaces on the account). Lead-in lives in the title.

**3. `cr_data_model`** (offline; the pytest target; body 60 lines). Globals: `Customer`, `Product`,
`Order`, `cr_entity_classes`, `cr_data_model`, `cr_record_keys`, plus the owned imports. The cell
opens with exactly these two lines, so `orders: Any = ...` resolves:

```python
from typing import Any
from context_surfaces import ContextField, ContextModel, ContextRelationship, export_data_model
```

then `mo.stop(sys.version_info >= (3, 14), mo.callout(mo.md("`ContextModel` needs Python 3.13 or older. Run `uv run marimo edit iris_basics.py`."), kind="warn"))`. The three classes exactly as at 3462-3496 with `field(...)` replaced by literal `ContextField(...)` calls:

- `Customer`: `__redis_key_template__ = f"{KEY_PREFIX}customer:{{customer_id}}"`; `customer_id: str = ContextField(description="Customer ID such as C001", is_key_component=True)`; `name: str = ContextField(description="Full name", index="text", weight=2.0)`; `tier: str = ContextField(description="Loyalty tier", index="tag", allowed_values=["bronze", "silver", "gold"])`; `region: str = ContextField(description="Sales region: emea or apac", index="tag", allowed_values=["emea", "apac"])`; `lifetime_value: float = ContextField(description="Lifetime spend in USD", index="numeric", sortable=True)`; `orders: Any = ContextRelationship(description="Orders this customer placed", target="Order", source_field="customer_id")`.
- `Product`: template `f"{KEY_PREFIX}product:{{sku}}"`; `sku` key component; `title` text weight 2.0; `category` tag; `price` numeric sortable.
- `Order`: template `f"{KEY_PREFIX}order:{{order_id}}"`; `order_id` key component; `customer_id` tag; `sku` tag; `region` tag with allowed values; `status` tag with `["placed", "shipped", "delivered", "returned"]`; `total` numeric sortable; relationships `customer` (target Customer, source_field customer_id) and `product` (target Product, source_field sku).

`assert all(cls.__redis_key_template__.startswith(KEY_PREFIX) for cls in (Customer, Product, Order))`;
`cr_data_model = export_data_model("Cairn Outfitters", "Customers, products and orders of a fictional outdoor-gear shop", entities=[Customer, Product, Order])` (always `entities=`, or the SDK registry exports every class ever defined in the kernel);
`cr_record_keys = [cls.__redis_key_template__.format(**row) for cls, entity in ((Customer, "Customer"), (Product, "Product"), (Order, "Order")) for row in cairn.SHOP_RECORDS[entity]]` (20). Renders `mo.vstack`: the lead-in, a six-row `mo.md` table "what you declare -> what the service generates" (`is_key_component` -> `get_<entity>_by_id`; any entity -> `list_`, `count_`; a `numeric` field -> `summarize_`; a `tag` or `numeric` field -> `filter_` with that field in its menu; a `text` field -> `search_<entity>_by_text`; relationships -> `expand_results`, plus the three set tools), and `mo.accordion({"export_data_model output (3 entities)": mo.json(cr_data_model)})`. Lead-in (two sentences): "The cell below declares the whole data model as three Python classes, and nothing has been sent yet. Each field's `index` kind decides which tools the service generates, and `__redis_key_template__` is where each record will be written in your database:". This cell has no dependency on `cr_configure`, so it runs with no credentials.

**4. `cr_create_button`** (markdown + button, body 6 lines). Lead-in (two sentences): "The button below makes five admin calls: one `POST /context-surfaces` with the data model and your database password, one agent key with a one-hour `expires_at`, one `GET` for the surface's `mcp_url`, and one `POST .../data` per entity. The service keeps the password until you delete the surface:". The `EXPIRE` detail (the notebook sets a one-hour `EXPIRE` on the 20 keys because the service sets none) is the gloss under the create output.

**5. `cr_create`** (create, two-branch shape, body 52 lines). Globals: `cr_surface_id` (`None` when unpressed or when the create failed), `cr_surface_status`, `cr_mcp_service`, `cr_mcp_url`, `cr_agent_key_expires`, `cr_indexes_before`, `cr_import_counts`, `cr_create_wire`, `cr_create_problem`. **Every one of these is assigned before the first request in both branches** (`None`, `None`, `None`, `None`, `None`, `set()`, `{}`, `None`, `None`), so a failure at any step leaves every name defined and `cr_tools` / `cr_call_tool` can `mo.stop` on `cr_mcp_service is None` instead of raising `NameError`. Pressed branch, in order:

```python
cr_indexes_before = set(cr_redis.execute_command("FT._LIST"))
_status, _body, _wire = call_service(cr_admin_service, "POST", "/context-surfaces", json_body={
    "name": cr_surface_name,
    "description": "Made by the Redis Iris basics notebook. Safe to delete.",
    "metadata": {"created_by": "iris-basics-notebook", "run": RUN_TAG},
    "data_model": cr_data_model,
    "data_source": cr_data_source(),
})  # never retried (niquests default retries=0): a replayed create would make a second surface holding your password
_wire["body"]["data_source"]["connection_config"]["password"] = "***"   # mask before this dict is bound to anything else
cr_create_wire = _wire
if _status == 201:
    cr_surface_id, cr_surface_status = _body["id"], _body["status"]     # provisioning_indices live
else:
    # The request carried the database password. No 400/422 body from this API has ever been
    # observed and validation errors commonly echo their input, so the reply is never rendered:
    # only the status and three short strings, after checking the password is not in the body.
    _password = cr_data_source()["connection_config"]["password"]
    _body_text = json.dumps(_body) if _body is not None else ""
    _safe = isinstance(_body, dict) and (not _password or _password not in _body_text)
    _fields = {name: str(_body.get(name, ""))[:200] for name in ("type", "title", "detail")} if _safe else {}
    _problem = mo.callout(mo.md(f"The admin API answered `{_status}` `{_fields.get('type', '')}`. {_fields.get('title', '')} {_fields.get('detail', '')}".strip() + (" The reply is not shown because it may echo the request." if not _safe else "")), kind="danger")
```

The `else` branch leaves `cr_surface_id` as `None` and skips the rest. `cr_data_source()` is called a second time only to obtain the comparison string inside the cell; `_password` is a cell-local that the next statement overwrites, and `_fields`, not `_body`, is what reaches the callout. `mo.json` is never applied to `_body` in this cell, and `tests/test_safety.py::test_create_reply_is_never_rendered` asserts that the only `mo.json(` argument in the `cr_create` cell is `cr_create_wire`.

Then, on 201, inside one `try/except Exception as exc` that records `cr_create_problem = f"{type(exc).__name__} at step {step}"` instead of raising, so `cr_surface_id` survives for Clean up: `POST /context-surfaces/{id}/agent-keys {"name": f"{cr_surface_name}-agent", "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=KEY_TTL_SECONDS)).isoformat(timespec="seconds")}` -> 201 `{"id", "key", ...}`, and the key goes straight into `cr_mcp_service = niquests.Session(headers={"X-API-Key": _key_body["key"], "Content-Type": "application/json", "User-Agent": "redis-iris-basics"}, timeout=(5, 30), disable_http3=True)` and nowhere else (never a global, never the cell's last expression). **No `Accept` header on the MCP session**: the live run sent only `X-API-Key` plus httpx's default `Accept: */*` and got JSON back (Context Retriever brief section 3; `cr_rpc`, iris_basics.py 4217-4228), and niquests' default is the same `*/*`. If a live run answers `406`, the documented alternative is `"Accept": "application/json, text/event-stream"` (the docs' curl); `Accept: application/json` alone was never sent to this endpoint and is not used. `GET /context-surfaces/{id}/agent-keys?page_size=50` -> `cr_mcp_url` from the item whose `id` matches, else `cr_mcp_url_default`; for each entity `POST /context-surfaces/{id}/data {"entity": "Customer", "records": [Customer(**row).model_dump() for row in cairn.SHOP_RECORDS["Customer"]], "options": {"on_conflict": "overwrite", "on_error": "fail_fast"}}` -> any 2xx `{"imported": 6, "failed": 0, "errors": []}`; `cr_redis.expire(key, KEY_TTL_SECONDS)` for each of `cr_record_keys`. Renders `mo.callout(kind="success")`: "Surface `<id>` (status `provisioning_indices`), agent key expires at <time>, imported 6 + 6 + 8 records, 20 keys set to expire in 1 h." (or a warn callout naming `cr_create_problem` and "press Clean up"), and `mo.accordion({"Create request as sent (password masked)": mo.json(cr_create_wire)})`. The create reply carries `data_model` and `tools` but no `data_source`; it is still never rendered whole. Gloss (two sentences): "The service answers `201` with the surface id and a status, and the import lands as JSON documents at the key templates you declared. The notebook sets a one-hour `EXPIRE` on those 20 keys because the service sets none."

**6. `cr_tools`** (body 20 lines). The first example cell: `mo.stop(cr_mcp_service is None, mo.callout(mo.md("Create the surface first."), kind="info"))`. Globals: `cr_tools_list` (the `result.tools` list from the reply, read by `cr_call_tool`), `cr_tools_wire`. `call_service(cr_mcp_service, "POST", cr_mcp_url, json_body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})` -> 200 `{"result": {"tools": [{"name", "description", "inputSchema"}]}}`. Renders `mo.ui.table(rows, selection=None, pagination=False, label=f"{n} tools from 3 entities")` with columns tool, description, arguments (from `inputSchema.properties`, required starred), built from the reply and never from a hardcoded list; the request in an accordion. Lead-in (two sentences): "The cell below asks the surface what it can do, with one JSON-RPC `tools/list` over MCP authenticated by the agent key. Every row is a tool the service generated from the data model, and nothing here was hand-written:". Gloss (hedged): "In a live run on 2026-09-30 this account's surface listed 21 tools (the unified tool surface)."

**7. `cr_call_tool`** (body 20 lines). `mo.stop(cr_mcp_service is None)`. `_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_customer_by_id", "arguments": {"id": "C001"}}}` (the argument is `id`, as the live run sent it; the cell asserts `"id" in next(tool for tool in cr_tools_list if tool["name"] == "get_customer_by_id")["inputSchema"]["required"]` and shows a warn callout with the actual `required` list if not, so a renamed argument surfaces as a sentence instead of a wrong call); `cr_customer_record = json.loads(_body["result"]["content"][0]["text"])`, or a danger callout with the text when `_body["result"].get("isError")`. Renders `mo.hstack([mo.json(_request, label="JSON-RPC request"), mo.json(cr_customer_record, label="result.content[0].text, parsed")], widths="equal")` and the gloss (two sentences): "The `_retrieval` stamp names the surface that served it and when. The agent sent an argument, not a key or a query." Lead-in (two sentences): "The cell below calls one generated tool the way an agent would: `get_customer_by_id` with `{"id": "C001"}`. The service reads the JSON document from your Redis and returns the record plus a `_retrieval` stamp:".

**8. `cr_rejected_call`** (two requests, body 26 lines). `mo.stop(cr_mcp_service is None)`.

- Request A, the observed rejection (VERIFIED live, Context Retriever brief 3.3; README "Verified live"): `{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "filter_customer", "arguments": {"limit": 5, "offset": 0}}}`, a `filter_customer` call with no condition. Live reply: HTTP 200, `result.isError` true, `result.content[0].text` = `Error executing tool: at least one condition is required`. Rendered as `mo.callout(mo.md(f"`isError` true: {text}"), kind="danger", title="Request A: no condition")`, or, if the reply differs, the same callout shape with whatever arrived (`error.code` and `error.message` when a top-level `error` is present; otherwise `mo.json(_body)` under the title "Request A: the service accepted it").
- Request B, the schema violation (VERIFIED by inference only; brief 9(d) and open questions 1-2): `{"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "filter_customer", "arguments": {"tag_conditions": [{"field": "lifetime_value", "value": "4200"}]}}}`, a tag condition on the numeric field `lifetime_value`. Three branches, printed as received: `"error" in _body` -> `mo.callout(mo.md(f"`{code}` {message}"), kind="danger", title="Request B: rejected before anything reached Redis")`; `result.isError` -> danger callout with `content[0].text`, title "Request B: the tool refused it"; otherwise `mo.callout(mo.json(_body), kind="warn", title="Request B: accepted; the schema was not enforced here")`.

Renders the lead-in, the two requests in an accordion, and the two callouts. Lead-in (two sentences, true in every branch): "The cell below sends two calls an agent should not get away with: `filter_customer` with no condition, then a tag condition on the numeric field `lifetime_value`. The service's own answer to each is printed as it arrives:". Gloss (hedged): "In a live run on 2026-09-30 the first call came back as a tool error, `at least one condition is required`. The second is the schema's job to refuse, with a JSON-RPC `-32602` or a tool error, and what it says here is what this service does." The old lead-in ("Governance is the schema, and it is enforced at the door") asserted an unobserved outcome and is gone; the README "Verified live" records whichever wording the first live run produces.

**9. `cr_peek`** (read-only, your own Redis, body 20 lines). `mo.stop(cr_surface_id is None)`. `cr_first_document = cr_redis.json().get(cr_record_keys[0])`; `cr_new_indexes = sorted(set(cr_redis.execute_command("FT._LIST")) - cr_indexes_before)`; for each: `_info = cr_redis.ft(name).info()`, `num_docs`, and prefixes from the RESP2 flat list `dict(zip(d[::2], d[1::2]))["prefixes"]` with `d = _info["index_definition"]`. Wire: `JSON.GET cairn:<run>:customer:C001`, `FT._LIST`, `FT.INFO idx:<surface-id>:<hash>:customer`. Renders `mo.json(cr_first_document, label=cr_record_keys[0])` and `mo.ui.table(selection=None)` index, documents, prefix. Lead-in: "Where did the data go: the cell below reads your own database with `JSON.GET` and `FT.INFO`:". Gloss (two sentences): "The agent never saw Redis. The service wrote these JSON documents into your database, built one index per entity, and the tool call above read them back through the service."

**10. `cr_cleanup_button`** (markdown + button, body 6 lines). Lead-in (two sentences): "Clean up deletes the surface, which also revokes its agent key, then unlinks the 20 keys by name and drops any index it saw appear under the run prefix, without `DD`. A surface has no TTL and holds your password, so this button matters more than the other three:". The docs' note that documents in your database stay after a surface is deleted is the gloss under the Clean up output.

**11. `cr_cleanup`** (body 24 lines). Depends on `cr_admin_service`, `cr_redis`, `cr_record_keys`, `cr_surface_id`, `cr_indexes_before`, `cr_cleanup_button`. `mo.stop(not cr_cleanup_button.value)`; if `cr_surface_id`: `DELETE /context-surfaces/{id}` (200, 204 and 404 all count as gone; anything else shows the body through the 7.2 pattern, since a DELETE carries no secret); `cr_keys_removed = cr_redis.unlink(*cr_record_keys)`; candidates = `(set(FT._LIST) - cr_indexes_before) | {name for name in FT._LIST if cr_surface_id and cr_surface_id in name}`; for each candidate whose `FT.INFO` prefixes all start with `KEY_PREFIX`: `cr_redis.ft(name).dropindex()` (never `delete_documents=True`), `except redis.ResponseError` tolerated on the not-found wording (same regex as `rs_cleanup`). Renders "Deleted 1 surface (`<status>`), unlinked 20 of 20 keys, 0 indexes left under cairn:<run>:" or "No surface was created in this run. Unlinked 0 keys." `cr_indexes_before` is `set()` in the unpressed branch of `cr_create`.

## 11. Column 3, Agent Memory (11 cells, body budget 222 lines)

All cells `@app.cell(column=3)`. Every request carries `ownerId` `cairn-<run>`, and every teaching
search also `namespace` `iris-basics-notebook`; Clean up lists by owner alone. Column 3 owns no
unprefixed import.

**1. `am_title`** (markdown + mermaid, hide_code, body 20 lines). Copy:

> # Redis Agent Memory
>
> An agent that forgets a user's allergy between sessions is not unintelligent. It has no memory.
> Redis Agent Memory gives it two tiers: session memory, the ordered events of the conversation in
> progress, and long-term memory, facts recalled by meaning across sessions. Its one job in this
> column: recall a customer fact with no word in common with the question.
>
> Facts reach long-term memory two ways: your app writes them directly, or the service extracts
> them from sessions in the background, minutes later. Every call carries an owner id and a
> namespace, because the store is shared by every app that points at it. Agent Memory is in public
> preview. Settings: `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY`. Docs:
> [Agent Memory](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/),
> [REST quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/rest-api-quickstart/).

Mermaid: `flowchart LR; A[App] -->|POST /session-memory/events| S[Session memory]; S -.->|extraction, minutes later| L[Long-term memory]; A -->|POST /long-term-memory| L; A -->|POST /long-term-memory/search| L`.

**2. `am_configure`** (body 34 lines). Globals: `am_service`, `am_owner_id`, `am_namespace`, `am_memory_ids`, `am_session_id`, `am_health_status`, `am_health_body`, `am_health_wire`. Code:

```python
mo.stop(not all(os.environ.get(name) for name in ("MEMORY_API_BASE_URL", "MEMORY_STORE_ID", "MEMORY_API_KEY")), mo.callout(mo.md("Agent Memory is not configured. Set `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY` in the `.env` next to `pyproject.toml` (see `.env.example`), then restart the kernel."), kind="warn"))
am_owner_id = f"cairn-{RUN_TAG}"                 # the tenant boundary on a shared store
am_namespace = "iris-basics-notebook"
am_memory_ids = [f"{am_owner_id}-m{n}" for n in range(1, len(cairn.DANA_FACTS) + 1)]   # chosen here, not by the service
am_session_id = f"{am_owner_id}-s1"
am_service = niquests.Session(
    base_url=f"{os.environ['MEMORY_API_BASE_URL'].strip().rstrip('/')}/v1/stores/{os.environ['MEMORY_STORE_ID'].strip()}",
    headers={"Authorization": f"Bearer {os.environ['MEMORY_API_KEY'].strip()}", "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "redis-iris-basics"},
    timeout=(5, 30),
    disable_http3=True,
)
try:
    am_health_status, am_health_body, am_health_wire = call_service(am_service, "GET", "/health")
except niquests.RequestException as exc:
    mo.stop(True, mo.callout(mo.md(f"`{type(exc).__name__}` while reaching Agent Memory. Check `MEMORY_API_BASE_URL`."), kind="danger"))
mo.stop(not isinstance(am_health_body, dict), mo.callout(mo.md("The endpoint answered with a page or an empty body, not JSON. `MEMORY_API_BASE_URL` is the Endpoint on the service's General settings, not a console page."), kind="danger"))
mo.stop(am_health_status != 200, mo.callout(mo.vstack([mo.md(f"Agent Memory answered `{am_health_status}` `{am_health_body.get('type', '')}`: {'check `MEMORY_API_KEY`' if am_health_status == 401 else 'check `MEMORY_STORE_ID`' if am_health_status == 404 else 'read the body below'}."), mo.json(am_health_body)]), kind="danger"))
mo.vstack([mo.callout(mo.md(f"Store `{am_health_body.get('status')}`. Every call in this column carries `ownerId` `{am_owner_id}` and `namespace` `{am_namespace}`. On a store other apps share, that filter is the tenant boundary."), kind="success"), mo.accordion({"Request as sent (credential masked)": mo.json(am_health_wire), "Reply": mo.json(am_health_body)})])
```

The `isinstance(am_health_body, dict)` guard covers both a non-JSON page (a string) and an empty
200 (`call_service` returns `None` for an empty body), so no `.get` runs on a non-dict. Wire:
`GET {base}/v1/stores/{storeId}/health` -> 200 `{"status": "healthy", "features": {...}}`.

**3. `am_write_button`** (markdown + button, body 6 lines). Lead-in (two sentences): "The button below writes five facts about Dana, a made-up Cairn customer, in one bulk `POST /long-term-memory`. The ids, the owner, and the namespace are chosen here rather than by the service:". The consequence (deterministic ids make an idempotent write and a Clean up without a ledger possible) is the gloss under the write output.

**4. `am_write`** (seed, native shape, body 24 lines). Globals: `am_memories_written` (`True`, assigned after the request), `am_write_wire`. Starts with `mo.stop(not am_write_button.value, mo.callout(mo.md("Press *Write 5 memories about Dana in one POST* above."), kind="info"))`. Then `call_service(am_service, "POST", "/long-term-memory", json_body={"memories": [{"id": memory_id, "text": fact["text"], "ownerId": am_owner_id, "namespace": am_namespace, "memoryType": "semantic", "topics": fact["topics"]} for memory_id, fact in zip(am_memory_ids, cairn.DANA_FACTS)]})` -> 201 `{"created": [...]}`; 424 -> `mo.stop(True, <danger callout>)` "`424 <type>`: <detail>. This is a failure of the store's database or model, not 0 results."; other non-201 -> `mo.stop(True, <the 7.2 callout>)`; then `am_memories_written = True`. Renders `mo.ui.table(selection=None, pagination=False)` id, text, topics and the request/reply accordion. Gloss (two sentences): "Agent Memory answers `201` with `created`, and in a live run the memories were searchable at once. Because the ids are yours, the same request twice is an update, and Clean up can name them without a ledger."

**5. `am_search_controls`** (markdown + two inputs, body 10 lines). The first example cell: `mo.stop(not am_memories_written, mo.callout(mo.md("Write the memories first."), kind="info"))`. Globals: `am_question = mo.ui.text(value=cairn.MEMORY_QUESTIONS[0]["text"], label="Ask", full_width=True)`; `am_threshold = mo.ui.slider(0.0, 1.0, step=0.05, value=0.3, debounce=True, show_value=True, label="similarityThreshold")`. Lead-in (two sentences): "The reply of `POST /long-term-memory/search` carries no score, so `similarityThreshold` is the only knob. Everything at or above it comes back in rank order, and everything below is cut:". One line under the inputs: "Two more questions with no word in common with their memory: *My toes feel squashed on long descents.* and *Somewhere freezing to visit next year?*"

**6. `am_search`** (one request per change; the swept parameter, body 30 lines). `mo.stop(not am_memories_written)`. Globals: `am_search_body`, `am_recalled_memories`, `am_search_wire`. `am_search_body = {"text": am_question.value, "filter": {"ownerId": {"eq": am_owner_id}, "namespace": {"eq": am_namespace}}, "filterOp": "all", "similarityThreshold": round(am_threshold.value, 2), "limit": 5}` -> 200 `{"items": [{"id", "text", "memoryType", "ownerId", "namespace", "topics", "createdAt", "updatedAt"}]}`; 424 shown as a failure. Renders `mo.ui.table(selection=None, pagination=False)` rank, memory, topics, words in common (`len(shared_stems(am_question.value, item["text"]))`), or `mo.callout(kind="warn")` "0 memories at or above <t>."; `mo.json(am_search_body, label="Request body")`; the gloss and one hedged line. Gloss: "Agent Memory answers with the memories at or above the threshold, best first, and says nothing about how close they were." Hedged line (two sentences): "In a live run on 2026-09-30 the allergy memory was #1 of 5 at 0.0 with 0 words in common, alone at 0.3, and absent at 0.5. Another store or model may place the cut elsewhere, so start around 0.3 and move it." (`limit` is 5 here; the old notebook's "#1 of 3" came from its `limit` 3, iris_basics.py 5260-5275.)

**7. `am_scope_lens`** (read-only, three requests per question change, body 22 lines). `mo.stop(not am_memories_written)`. Same `text`, `"similarityThreshold": 0.0`, `"limit": 100`, under `{"ownerId": {"eq": am_owner_id}, "namespace": {"eq": am_namespace}}`; `{"ownerId": {"eq": "nobody-owns-this-id"}, "namespace": {"eq": am_namespace}}`; `{"ownerId": {"eq": am_owner_id}, "namespace": {"eq": "no-such-namespace"}}`. Renders `mo.ui.table(selection=None, pagination=False)` scope, filter (JSON text), memories returned, expected 5 / 0 / 0, and the gloss: "`namespace eq` never matches a record that has no namespace at all, which is why Clean up below lists by owner alone." Lead-in (two sentences): "The same question runs under three filters. The owner and the namespace, both sent on every call, are what scope a shared store:".

**8. `am_session_button`** (markdown + button, body 8 lines). Lead-in (two sentences): "The button below posts four session events, two of them facts worth remembering, then searches long-term memory with the last turn and assembles the prompt an agent would receive. A session's owner and namespace come from its first event only:". One line under it, the cadence stated once: "Extraction runs in the background on the store's cadence, every 5 minutes by default and configurable from 60 to 600 seconds, and can turn the two facts into long-term memories with ids the service chooses." (Agent Memory brief 3.4.)

**9. `am_session`** (the second half of the wow; six requests, body 36 lines). `if not am_session_button.value:` info callout; else for each turn of `cairn.SESSION_TURNS`: `POST /session-memory/events {"sessionId": am_session_id, "actorId": am_owner_id if role == "USER" else "cairn-agent", "role": role, "content": [{"text": text}], "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), "namespace": am_namespace, "metadata": {"source": "iris-basics"}}` -> 201; `GET /session-memory/{am_session_id}` -> 200 `{"sessionId", "ownerId", "namespace", "events", "summary"?}`; `POST /long-term-memory/search {"text": last USER text, "filter": {owner, namespace}, "filterOp": "all", "similarityThreshold": 0.0, "limit": 3}`. Nothing later depends on this cell, so it uses the native shape: `mo.stop(not am_session_button.value, mo.callout(mo.md("Press *Send 4 session events and assemble the prompt* above."), kind="info"))` first (the same applies to `lc_cache_aside`). Builds the literal string:

```
Retrieved memory context:
Session summary:
(none yet: this store summarizes only when that is switched on and the session is long enough)
Short-term memory:
USER: I am training for a 50 kilometre trail race in April.
ASSISTANT: Noted: a 50 km race in April. Do steep descents give you any trouble?
USER: My left knee complains on steep downhills, so I always carry trekking poles.
Long-term memory:
- Dana is allergic to peanuts and tree nuts, so trail snacks and camp meals must not contain them.
- ...
```

Renders `mo.vstack`: one line "Session `cairn-<run>-s1`: `ownerId` <first actor>, `namespace` <from the first event>."; `mo.ui.table(selection=None, pagination=False)` role, actor, text, createdAt; the heading "What the agent receives for the last turn" and `mo.plain_text(am_prompt_context)`; one line "#1 of 3 · 0 words in common" for the top recalled memory. Gloss (two sentences): "The session read-back shows the owner and namespace the first event fixed. The assembled prompt is what the two tiers are for."

**10. `am_cleanup_button`** (markdown + button, body 8 lines). Lead-in (two sentences): "Clean up deletes the session, lists every memory under the owner with no namespace filter, checks each item's `ownerId` on this side, deletes by id, and lists again. If you sent session events, press it again after the extraction cadence has passed, ten minutes to be safe, because extracted memories otherwise last a year:". The cadence itself (5 minutes by default, 60-600 s configurable) is stated once, in `am_session_button`, so "ten minutes" here reads as twice the default.

**11. `am_cleanup`** (body 28 lines). Depends on `am_service`, `am_owner_id`, `am_memory_ids`, `am_session_id`, `am_cleanup_button`. `mo.stop(not am_cleanup_button.value)`; `GET /session-memory?filterOwnerId=<owner>&limit=1000` -> `{"items": [...]}` (never `includeAll`), then `DELETE /session-memory/{id}` per item (204 and 404 both gone), falling back to `am_session_id` when the listing fails; `POST /long-term-memory/search {"text": "", "filter": {"ownerId": {"eq": am_owner_id}}, "limit": 100}` paged on `nextPageToken` (cap 50 pages); abort with a danger callout if any item's `ownerId` differs; `DELETE /long-term-memory {"memoryIds": [...]}` in chunks of 100 over the union of listed ids and `am_memory_ids` -> 200 `{"deleted": [...]}`; list again. Renders "Deleted 1 session and 5 memories (0 with ids the service chose); 0 left under cairn-<run>." If the second listing is not empty with no `errors`, the fallback (a comment in the cell) is to send `"similarityThreshold": 0.0` on the listing call.

Requests in a full run: 1 + 1 + 1 per slider or question change + 3 + 6 + about 6.

## 12. Per-column Clean up, stated exactly

| Column | Deletes | How it knows what to delete | Tolerates | Never |
|---|---|---|---|---|
| 0 | `FT.DROPINDEX idx:cairn:<run>:help`; `UNLINK` the 36 keys | `rs_index_name` and `rs_article_keys` from `rs_connect` | index already gone; `UNLINK` returning fewer than 36 | `DD`, `FLUSH*`, `SCAN`-and-delete, any key outside `cairn:<run>:` |
| 1 | `DELETE /entries/{id}` per stored id | `lc_stored_entries`, defined (possibly `[]`) by `lc_store`; the taught entry deletes itself in `lc_cache_aside` | `404` of type `resource-not-found` or `entry/not-found` | `/flush`, `DELETE /entries` with a body, Generate key, Delete service |
| 2 | `DELETE /context-surfaces/{id}`; `UNLINK` the 20 keys; `FT.DROPINDEX` any new or surface-named index whose every prefix starts with `cairn:<run>:` | `cr_surface_id` (always defined, `None` if never created); `cr_record_keys`; `cr_indexes_before` | `200`, `204` or `404` on the DELETE; index already gone | `DD`, `PUT`, deleting a surface not created in this run, `FLUSH*` |
| 3 | `DELETE /session-memory/{id}` for sessions listed under the owner; `DELETE /long-term-memory {"memoryIds"}` for memories listed under the owner plus the five deterministic ids | `filterOwnerId=cairn-<run>`; owner-only `text: ""` listing; client-side `ownerId` check | `404` on a session; a listing that lags a write | `includeAll`, deleting an id whose `ownerId` was not verified, Flush, Delete service |

If any cell between seed and Clean up fails, Clean up still runs.

## 13. Test suite

Runs with `uv run pytest`. Credentials are absent by construction: `app.run()` does not load
`.env` (marimo brief 1.2), and the notebook test deletes the ten configuration names with
`monkeypatch.delenv`, so no test can reach a Redis or Iris service.

**`tests/test_notebook.py`**

- `test_notebook_runs_without_credentials`: deletes the ten names, sets `HF_HUB_OFFLINE=1`, imports `iris_basics`, calls `outputs, defs = iris_basics.app.run()`; asserts it returned (a raising cell propagates), `"RUN_TAG" in defs`, `defs["cr_data_model"]["entity_count"] == 3`, `len(defs["cr_record_keys"]) == 20`, every `redis_key_template` in the export starts with `defs["KEY_PREFIX"]`, and none of `rs_client`, `lc_service`, `cr_admin_service`, `am_service` is in `defs` (each configure cell stopped on its not-configured note).
- `test_marimo_check_strict`: `subprocess.run([sys.executable, "-m", "marimo", "check", "--strict", "iris_basics.py"], capture_output=True, text=True)` exits 0. Verified 2026-09-30 under `uv run pytest` with pytest 9.1.1 and marimo 0.25.0 (`shutil.which("marimo")` also resolves to the venv script, so no fallback is written).
- `test_columns_are_complete_and_contiguous`: regex `column=(\d)` over the file; the set is `{0, 1, 2, 3}`, every `@app.cell(` carries `column=`, and the sequence is non-decreasing in file order (marimo issue #3543).
- `test_no_pep723_header`: no `# /// script`.
- `test_cell_count_budget`: `42 <= file.count("@app.cell(") <= 46` (45 planned, section 16).
- `test_line_budget`: the file has at most 1,500 lines (section 16 plans about 1,450).
- `test_setup_block_is_minimal`: `ast` over the `with app.setup(...)` block; the names it assigns or defines are exactly `{"RUN_TAG", "KEY_PREFIX", "KEY_TTL_SECONDS", "MASKED_HEADERS", "call_service", "connect_redis"}` plus imports.

**`tests/test_safety.py`** (`ast` over `iris_basics.py`; one function per rule)

- no `FLUSH` token, no `/flush` string, no `flushall`/`flushdb` call;
- no `"DD"` string argument and no `delete_documents=True`;
- no `includeAll`;
- no `call_service(..., "DELETE", "/entries", json_body=...)` (a DELETE whose path is exactly `/entries` with a body);
- every dict literal containing both `"prompt"` and `"response"` keys also contains `"ttlMillis"`;
- every dict literal containing a `"memories"` key or a `"filter"` key contains `"ownerId"` (directly or nested);
- every `__redis_key_template__` assignment is an f-string starting with `{KEY_PREFIX}`;
- no `mo.ui.text(kind="password")`, no `mo.ui.chat`, no `mo.state`, no `mo.ui.form`;
- `test_no_secret_read_bound_to_a_global`: for every `ast.Assign` whose `value` node is itself a `Subscript` or `Call` on `os.environ` (`os.environ[...]`, `os.environ.get(...)`), every target name starts with `_`, except when the string constant read is `CTX_API_URL` or `CTX_MCP_URL` (the two non-secret reads). An `os.environ` read nested inside a larger expression (`connect_redis(os.environ["REDIS_URL"])`, an f-string in `niquests.Session(base_url=...)`) is not an `Assign` value and does not fire;
- no `retries=` keyword anywhere; every `niquests.Session(` call passes `disable_http3=True` and a `"Content-Type": "application/json"` header;
- `test_mcp_session_sends_the_verified_accept_header`: the `niquests.Session(` call assigned to `cr_mcp_service` carries `"Accept": "*/*"` in its `headers` dict (the value httpx sent live; `niquests.Session(headers=)` replaces the default header set, so without the key niquests sends no `Accept` at all; section 18 row 6, review round 1);
- `test_create_reply_is_never_rendered`: in the cell function named `cr_create`, every `mo.json(` call's first argument is the `Name` `cr_create_wire`, and no name bound from the `call_service(..., "POST", "/context-surfaces", ...)` tuple is a global (every target starts with `_`) or the cell's last expression;
- `test_import_ownership`: the bare names `NumericField`, `TagField`, `TextField`, `VectorField`, `IndexDefinition`, `IndexType`, `Query`, `ResponseError` are imported only inside `column=0` cells; `unquote`, `urlsplit`, `Any`, `ContextField`, `ContextModel`, `ContextRelationship`, `export_data_model` only inside `column=2` cells; no other `ast.Import`/`ast.ImportFrom` appears in any column cell (section 17);
- no `print(`.

**`tests/test_voice.py`** (regex over every string constant inside a `mo.md(` call in `iris_basics.py`, after stripping fenced code, inline code and URLs)

- `test_no_sentence_over_30_words`: split on `.`, `:`, `?`; no sentence has more than 30 words (the plan's own ceiling is 25; 30 leaves room for a backticked command counted as words);
- `test_serial_comma_in_three_item_lists`: no match for `` `[^`]+`, `[^`]+` and `[^`]+` `` (three backticked items joined without the serial comma) and no match for `, [A-Za-z ]+ and [A-Za-z ]+[.:]` following a second comma-separated item, the shape the critics found;
- `test_no_semicolon_in_prose`: no `;` outside inline code or a mermaid block;
- `test_no_exclamation_marks`; `test_no_developers_in_prose`; `test_no_try_free_link` (no `redis.io/try-free`);
- `test_hit_miss_lowercase_in_prose`: the tokens `HIT` and `MISS` appear only inside a `title=` keyword or a table-cell string, never in a `mo.md` prose string (a `mo.md` string that begins with `|` is a table and exempt).

**`tests/test_cairn_support.py`**

- `test_package_has_no_iris_imports`: no module under `src/cairn_support` imports `redis`, `niquests` or `marimo` (walk `ast.Import`/`ast.ImportFrom`);
- `HELP_ARTICLES`: 36 rows, 6 categories x 6, unique ids, no title or body contains "cramped" or "shoes", exactly one contains "Trailhead", none "waterproofng"; every `SEARCH_PRESETS` target is an article id; the article categories include `returns` and `shipping` with at least one row in 2024-2026;
- `GEAR_CARE_ANSWERS`: 5 unique keys; `THRESHOLD_PROBES` has 6 rows whose targets are keys or `None`; `STRATEGY_VARIANTS` has 2 rows; `CACHE_ASIDE_SCRIPT` has `prefix`, `ask`, `answer`, `again`, and `prefix` ends with a space;
- `SHOP_RECORDS`: 6 / 6 / 8, every id matches `^[A-Za-z]+[0-9]+$`, every `Order.customer_id` and `Order.sku` exists;
- `DANA_FACTS` has 5 rows; each `MEMORY_QUESTIONS` entry shares zero stems with its target fact; `SESSION_TURNS` has 4 rows, `SESSION_TURNS[-1]["text"] == MEMORY_QUESTIONS[0]["text"]`, and the two USER facts share at least one stem with nothing in `DANA_FACTS` (so extraction, if it happens, creates new records the owner-only cleanup must catch);
- `formatting.redis_cli` renders a 1024-byte value as `<1024 bytes>`, quotes an argument with a space, truncates a 200-character string with a `<200 chars>` marker; `shared_stems("wash the boots", "washing boots") == ["boot", "wash"]`;
- `embedder.load_embedding_model`: skipped unless the snapshot is already in the Hugging Face cache (`local_files_only=True` succeeds); when it runs, `model.dim == 256` and `embed_texts` returns unit-norm float32.

## 14. README outline (VOICE.md tutorial template)

- H1: `How to take the Redis Iris services apart with one marimo notebook`
- `> **TL;DR:**` two sentences: what Redis Iris is (docs framing) and "In this tutorial, you will run a notebook that lets you index, cache, retrieve, and remember with the four Redis services behind an agent's context."
- `> **Note:** This tutorial uses the code from the following git repository:` + URL.
- Intro, problem-first: "When your agent needs context, you need a search index, a semantic cache, governed access to your data, and a memory that outlives the session. Redis gives you all four in one data layer, and this notebook shows the exact command or request behind each."
- `## What you'll learn` (four bullets, one per column, mechanism not benefit)
- `## What you'll build` (one notebook, four columns, one `.env`)
- `## Prerequisites`: uv; Python 3.11-3.13 (uv installs it); a Redis 8 database with Search and JSON, either local or Redis Cloud; Iris credentials flat ("A LangCache service (API URL, cache ID, and API key)", "A Context Retriever admin key", "An Agent Memory service (endpoint, store ID, and API key)"); `> **Note:** The notebook does not start a local Redis instance; the only thing that runs on your machine is a 30 MB embedding model.`
- `## Step 1. Clone the repository`
- `## Step 2. Configure the environment`: "Copy the sample file:" + `cp .env.example .env` + gloss "Open `.env`, uncomment the variables for the columns you want to run, and fill them in." + Variable | Default | Purpose table for the ten names. The Purpose cell names the console page the value comes from ("the service's Connectivity page") and links https://cloud.redis.io once, with no click-through steps (VOICE.md: no console click paths inside a step; the paths live in `.env.example` only).
- `## Step 3. Start the notebook`: `uv run marimo edit iris_basics.py`; "Or present it as an app:" `uv run marimo run iris_basics.py`; gloss: marimo loads `.env`, each column connects read-only.
- `## Step 4. Search the help centre` (column 0), `## Step 5. Turn the similarity threshold` (column 1), `## Step 6. Generate tools from a data model` (column 2), `## Step 7. Recall a memory by meaning` (column 3): each names the button to press and the literal output to expect ("Redis returns `num_docs` 36."), no contingencies.
- `## Step 8. Clean up`: one button per column and what each deletes; "If you sent session events, press Agent Memory's button again once the store's extraction cadence has passed (5 minutes by default, configurable from 60 to 600 seconds), because extraction adds memories with ids the service chooses."
- `## How it works`: Key | Type | Purpose table (`cairn:<run>:help:<id>` HASH; `idx:cairn:<run>:help` index; `cairn:<run>:customer:<id>` JSON written by the service; `idx:<surface>:<hash>:<entity>`; LangCache entry ids; `cairn-<run>-m<n>` memory ids; `cairn-<run>-s1` session); the raw `FT.CREATE` and the four request bodies after the client code; one mermaid of the four columns; the safety table (scope, expires, Clean up, never). Own the downside flat: "A LangCache entry this kernel has forgotten is reached only by its 15-minute TTL. A Context Retriever surface has no TTL and keeps your database password until you delete it."
- `## Troubleshooting`: `### A column says "not configured"` (Verify that `.env` sits next to `pyproject.toml` and the names are uncommented); `### Context Retriever fails to define the model` (Verify that `uv run python --version` reports 3.13 or older); `### The hybrid column shows a note` (Verify the Redis version is 8.4 or later); `### The range query returns nothing for a sensible question` (raise the literal `0.5` radius in the `rs_knn_versus_range` cell; 0.5 is a starting point for this model); `### Agent Memory answers 424` (a dependency failed, not zero results); `### LangCache answers 401` (Verify the key; a regenerated key invalidates the old one for every app); `### A run_button cell went back to "press the button"` (marimo reset it because a cell above re-ran; press it again); `### Clean up cannot find the surface` (the create cell did not finish; the surface, if created, is named `cairn-<run>` in the console).
- `## FAQ`: "Do I need Redis Cloud?" (No. Column 0 runs on any Redis 8 with Search; the three hosted services are Redis Cloud.); "Does the notebook call an LLM?" (No.); "What does the notebook leave behind if I close the laptop?" (Redis keys and the agent key expire in an hour, LangCache entries in 15 minutes; a Context Retriever surface and Agent Memory records do not, so press Clean up.); "Can I point it at a shared LangCache?" (Yes, with the TTL caveat; a service of your own is better.); "Where do the numbers in the copy come from?" (one live run on 2026-09-30; yours will differ).
- `## Next steps`: imperatives with linked titles (semantic caching tutorial; Agent Memory with LangGraph; Context Retriever quickstart; vector search guide).
- `## Additional resources`: the four docs anchors and the redis-ai-resources notebook collection.
- `## Verified live`: a short dated section carrying forward the 2026-09-30 facts the copy relies on, hedged as "in a live run", and only facts somebody observed: Redis 8.6.2; `FT.HYBRID ... LOAD 1 @__key` with the RESP2 pair-list reply; BM25STD returning no row for *trail shoes feel cramped* while KNN and hybrid ranked the boot guide first; LangCache 0.896 for the rain-shell question, 0.950 worst wrong answer, expiry within about a second of `ttlMillis`, `404 /errors/resource-not-found` on a deleted entry; Context Retriever 21 tools on the unified surface, `get_customer_by_id {"id": "C001"}`, and `filter_customer` with no condition answering `isError` `at least one condition is required`; Agent Memory search with no score, the allergy memory #1 with 0 words in common. The schema-violation reply, the cache-aside hit score, and the `Accept`-less MCP session under niquests are added after the first live run of the new notebook (section 18), never before.

No sales CTA, no "developers" in body prose, sentence-case headings, serial commas, no exclamation
marks, one dash style, no "Presenter notes", "Before the meeting" or `verify.py` sections.

## 15. Deliberately dropped from the current notebook

| Dropped | Lines today | Reason |
|---|---|---|
| `SecretRegistry`, `scrub`, `Secret`, `SecretText`, `validate_secret`, `resolve_secret` | 136-280, 373-419 | Nothing is typed in, so nothing needs scrubbing; secrets live in `os.environ` only. |
| Hand-rolled env parser (`ENV_ALLOWLIST`, `parse_env`, `env`, `redis_url_from_env`, split `REDIS_*` fields) | 281-372 | marimo loads `.env` natively; `REDIS_URL` is the one Redis setting. |
| AUTORUN / HEADLESS / `pressed()` and `.env` name sniffing | 61-134 | Test hooks visible in every consent cell; the tests call `app.run()` instead. |
| Recursive redaction and runtime secret learning | 441-531 | Two headers and one body field ever carry a secret; `call_service` masks the headers and `cr_create` masks the field. |
| HTTP choke points, route allowlist, `_dispatch` | 606-865 | A second implementation of each API written to refuse the notebook's own code; the invariants become `tests/test_safety.py`. |
| `http_call`, `HTTP_TRACE`, `as_curl`, `wire_view`, httpx | 533-560, 866-956 | `call_service` returns the wire dict; `mo.json` renders it. |
| `GuardedTracedRedis`, `_ft_create_prefixes`, `_confirm_index`, allowlists | 999-1275 | A newcomer cannot tell the wrapper from redis-py; the run prefix, `EXPIRE` and no-`DD` carry the safety. |
| Grants, orphans, typed confirmations, `owned_index`, `claim_index` | 1384-1520, 5940-5988, 7283-7325, 7970-8014 | Deleting other runs' leftovers is not an MVP concern; TTL bounds it. |
| Disk ledger | 1522-1589 | Every id is deterministic from the run tag, or kept in the cell that created it. |
| `RECORDED_LIVE` panels | 1683-1909 | Removed by decision; live numbers become hedged one-liners in the copy. |
| Presenter notes, `notes()`, the CSS toggle, CSS hacks on marimo internals | 1655-1664, 5717-5734, 5814-5822 | Talk track for the presenter, not teaching for the audience; CSS keyed on marimo class names is fragile. |
| Reset buttons and `mo.state` generation tokens | 5906, 6555, 7209, 7940 | A marimo re-run is the reset. |
| Test connection buttons and the extra probes (`FT._LIST`, `DBSIZE`, `SCAN irisnb_*`, `INFO clients`) | 5901-5937, 2411-2459 | `PING` and `INFO server` on load are enough and read-only. |
| `rs_hash_embed` word-hashing fallback | 2094-2108 | A degraded embedder makes the paraphrase demo lie; fail with a sentence instead. |
| `rs_tokens`/`rs_text_query` rewriting and the prefix/typo switches | 2152-2164 | Redis drops stopwords itself; the full-text cell is one fixed query. |
| KNN `k` slider, category multiselect, year range slider | 6143-6276 | `k` 3 is a literal; the pre-filter is a fixed query string shown as redis-cli, which is the lesson. |
| "Type your own" text on the three-engine cell | 6279-6341 | The dropdown of three presets is the one control. |
| Deep dive: second index FLAT/HNSW/SVS-VAMANA, `rs_compare_indexes`, `rs_has_svs` | 6386-6446, 2603-2640 | Doubles memory for a lesson that reads "same results at this scale". |
| LangCache floor probe, scope attribute detection, `LANGCACHE_SCOPE_ATTR` | 2887-2970 | The floor is verified live; the MVP sends no attributes. |
| LangCache 14-probe lab, precision/recall `mo.stat` tiles, scoreboard | 3008-3160 | Six probes and a verdict column carry the trade-off. |
| Cache-first chat, Teach field, vocabulary gate, shared-cache red note, `mo.ui.chat` | 6856-6988, 3202-3271 | `mo.ui.chat` is broken on 0.25.0 and free-text teaching risks leaking to other apps; a scripted loop teaches the same thing. |
| LangCache TTL deep dive as a separate cell | 7044-7100 | Folded into the cache-aside wow (`ttlMillis` 10000, then the `404`). |
| `cr_unified_tools`, `cr_predict_tools`, `cr_tool_diff` | 3571-3715 | Predicts what `tools/list` then says; the live list is the proof. |
| Field editor with `cr_edits` state, and plan B's `cr_model_variant` | 7328-7442 | Consequences inferred from the generation rule, not observed; the six-row table plus the live `tools/list` carry the lesson. |
| Schema-driven form generator and Break it planner | 4395-4621 | Two fixed `tools/call`s teach what the generic MCP client did, in a tenth of the code. |
| Paging and provenance sliders | 7577-7638 | `has_more` and `_retrieval` appear in the two fixed calls. |
| Surface adoption, settle polling, `cr_confirm_gone`, surface listing | 3952-3977, 4044-4095, 4677-4697, 3769 | Create once; Clean up deletes by the id you kept. |
| Tool catalogue deep dive | 7803-7829 | The `tools/list` table is the catalogue. |
| Agent Memory 11-threshold sweep, band table, monotonicity check, Re-query live | 5060-5164, 8137-8271 | A debounced live re-query per slider move teaches the score-less API. |
| Memory inspector chat, presets dropdown, New session, tier toggles | 8322-8465 | One session cell that assembles the prompt once. |
| Start extraction now, Check for extracted memories, Clean up again | 8017-8066, 8608-8651, 8471 | The session cell posts facts; the same Clean up button pressed later catches extraction. |
| Under the hood (`am_hood`, service `REDIS_URL` field) | 5547-5640, 8654-8709 | Needs the service's backing database URL, which customers do not have. |
| `ib-<run>` id shape | throughout | Replaced by `cairn-<run>` so every column's tag reads the same. |
| `verify.py`, `tests/` (16 files), `legacy/`, `.agents/build/` | repo | Coupled to the setup block being removed; replaced by three small test files. |
| PEP 723 header and `--sandbox` launch | 1-15 | Project mode: `pyproject.toml` and `uv run marimo edit iris_basics.py`. |

## 16. Line and cell budget

Two budgets, kept apart. **Body lines** are the statements inside a cell function, which is what
sections 8-11 estimate per cell. **Serialization overhead** is what marimo's saved format adds
around each body: the `@app.cell(...)` decorator, the `def name(...)` line, the `return (...)`
line, and the two blank lines between cells, plus the signature wrapping one parameter per line
once a cell depends on more than about six names (the old notebook's 8-dependency cell became a
10-line signature, iris_basics.py 7700-7710). Most cells here depend on 5-10 column globals, so
the overhead is budgeted at a flat 8 lines per cell.

| Region | Cells | Body lines | Overhead (8 x cells) | Lines |
|---|---|---|---|---|
| Header, `App(...)`, setup block | setup | 75 | | 75 |
| Intro | 1 | 30 | 8 | 38 |
| Column 0, Redis Search | 11 | 250 | 88 | 338 |
| Column 1, LangCache | 11 | 225 | 88 | 313 |
| Column 2, Context Retriever | 11 | 285 | 88 | 373 |
| Column 3, Agent Memory | 11 | 222 | 88 | 310 |
| `if __name__ == "__main__": app.run()` | | 3 | | 3 |
| **Total** | **45 `@app.cell`** | **1,090** | **360** | **about 1,450** |

Ceilings: `test_line_budget` 1,500 lines, `test_cell_count_budget` 42-46 cells. The 50-line
margin is the plan's, not the builders': a column that lands over its body budget is over.

Per-cell body budgets (ceilings), which add up to the column figures above:

| Column 0 | | Column 1 | | Column 2 | | Column 3 | |
|---|---|---|---|---|---|---|---|
| `rs_title` | 20 | `lc_title` | 22 | `cr_title` | 22 | `am_title` | 20 |
| `rs_connect` | 36 | `lc_configure` | 36 | `cr_configure` | 34 | `am_configure` | 34 |
| `rs_load` | 36 | `lc_store` | 24 | `cr_data_model` | 60 | `am_write_button` | 6 |
| `rs_full_text` | 18 | `lc_anatomy_of_a_hit` | 20 | `cr_create_button` | 6 | `am_write` | 24 |
| `rs_search_controls` | 10 | `lc_probes_and_slider` | 22 | `cr_create` | 52 | `am_search_controls` | 10 |
| `rs_knn_versus_range` | 34 | `lc_threshold_table` | 22 | `cr_tools` | 20 | `am_search` | 30 |
| `rs_prefiltered` | 26 | `lc_strategies` | 22 | `cr_call_tool` | 20 | `am_scope_lens` | 22 |
| `rs_engine_preset` | 10 | `lc_cache_aside_button` | 6 | `cr_rejected_call` | 26 | `am_session_button` | 8 |
| `rs_engine_results` | 44 | `lc_cache_aside` | 34 | `cr_peek` | 20 | `am_session` | 36 |
| `rs_cleanup_button` | 6 | `lc_cleanup_button` | 6 | `cr_cleanup_button` | 6 | `am_cleanup_button` | 8 |
| `rs_cleanup` | 14 | `lc_cleanup` | 16 | `cr_cleanup` | 24 | `am_cleanup` | 28 |
| **250** | | **225** | | **285** | | **222** | |

The cuts the previous revision listed as a fallback are applied up front and are already in the
figures: the radius is the literal 0.5 (no slider), `lc_strategies` is two variants x two
strategies (4 requests), and `rs_load_button` / `lc_store_button` live in the configure cells.
If a column still runs over, the builder cuts in this order, never anything on the conformance
review's keep list: column 0, the `rs_prefiltered` unfiltered twin (show the filtered query and
its table alone, and say "the same KNN with no filter returns a top five that ignores category
and year"); column 1, the `lc_threshold_table` `style_cell` colouring; column 2, the six-row
markdown table in `cr_data_model` to three rows; column 3, the scope lens to two scopes (this
owner, a nonexistent owner).

`src/cairn_support`: about 300 lines (`sample_data.py` 200, `embedder.py` 40, `formatting.py` 40,
`__init__.py` 3). Tests: about 260 lines.

## 17. Build order and parallelism

### Step 0. Scaffold (one agent, one commit, sequential)

1. Delete `legacy/`, `tests/`, `verify.py`, `.agents/build/`, `__marimo__/`. Drop the
   `.agents/build/` line from `.gitignore` and write the new `.gitignore`.
2. Write `pyproject.toml`, `.python-version`, `.env.example` (section 3).
3. Write `src/cairn_support/` (section 5) by porting the literals from the line ranges named.
4. Write `iris_basics.py` as the scaffold: header and setup cell (section 4), the intro cell
   (section 6), the four title cells (`rs_title`, `lc_title`, `cr_title`, `am_title`, sections
   8-11 cell 1), a marker line after each title cell, and the footer:

   ```python
   @app.cell(column=0, hide_code=True)
   def rs_title(): ...

   # @@ COLUMN 0 CELLS @@

   @app.cell(column=1, hide_code=True)
   def lc_title(): ...

   # @@ COLUMN 1 CELLS @@

   @app.cell(column=2, hide_code=True)
   def cr_title(): ...

   # @@ COLUMN 2 CELLS @@

   @app.cell(column=3, hide_code=True)
   def am_title(): ...

   # @@ COLUMN 3 CELLS @@


   if __name__ == "__main__":
       app.run()
   ```
5. Write `tests/` (section 13) in full and the README skeleton (section 14 headings, Steps 1-3,
   Step 8, How it works, Troubleshooting, FAQ, Next steps; Steps 4-7 left as `<!-- column N -->`
   markers). Then run the VOICE.md pre-publish checklist over everything the scaffold owns (the
   intro cell, the four title cells, `.env.example`, the README skeleton) and `uv run pytest
   tests/test_voice.py`, so the literals the builders copy are already right: serial commas in
   every three-item list, no sentence over 25 words, no semicolons, no trial link, no console
   click path outside `.env.example`.
6. Write `.agents/fragments/assemble.py` (below) and `.agents/fragments/README.md` (the fragment
   rules from this section, verbatim).
7. `uv lock`, `uv sync`, `uv run pytest` (the layout, safety, budget and package tests pass on the
   scaffold; `test_columns_are_complete_and_contiguous` passes because the four title cells carry
   `column=`), `uv run marimo check --strict iris_basics.py`, `uv sync --python 3.11 && uv run pytest`
   once (decision 17), then commit "Scaffold the MVP rebuild".

### Step 1. Four column fragments (four builders, in parallel, on the scaffold commit)

Fragment files, one per builder:

| Column | File | Cells (section) |
|---|---|---|
| 0 | `.agents/fragments/column0_redis_search.py` | `rs_connect` .. `rs_cleanup` (section 8, cells 2-12) |
| 1 | `.agents/fragments/column1_langcache.py` | `lc_configure` .. `lc_cleanup` (section 9, cells 2-12) |
| 2 | `.agents/fragments/column2_context_retriever.py` | `cr_configure` .. `cr_cleanup` (section 10, cells 2-11) |
| 3 | `.agents/fragments/column3_agent_memory.py` | `am_configure` .. `am_cleanup` (section 11, cells 2-11) |

Each builder also writes `.agents/fragments/readme_columnN.md`: the README Step section for
the column (heading, button to press, literal output), in the voice guide's tutorial register.

Fragment rules:

- A fragment is UTF-8, LF, and contains only `@app.cell(column=N ...)`-decorated functions in
  marimo's saved format (`def name(dep, ...):` body `return (defs,)`), separated by two blank
  lines. No module-level code, no imports outside a cell body, no `app`, no setup block, no title
  cell (the scaffold owns it), no footer.
- Every cell in the fragment carries the same `column=N`; markdown-only cells add `hide_code=True`.
- Every global the fragment defines carries the column prefix (`rs_`, `lc_`, `cr_`, `am_`), except
  the unprefixed names each column **owns** below. Cell-local names start with `_`. Two fragments
  defining the same global is MB002 (marimo lint `MultipleDefinitionsRule`), which
  `marimo check --strict` rejects and which stops both cells from running, so ownership is
  exclusive:
  - column 0 owns `NumericField`, `TagField`, `TextField`, `VectorField`, `IndexDefinition`,
    `IndexType`, `Query`, `ResponseError` (imported at the top of `rs_connect`);
  - column 2 owns `unquote`, `urlsplit` (imported in `cr_configure`), `Any`, `ContextField`,
    `ContextModel`, `ContextRelationship`, `export_data_model` (imported in `cr_data_model`), and
    the classes `Customer`, `Product`, `Order`;
  - columns 1 and 3 own no unprefixed name and contain no `import` statement.
  No fragment imports a name another column owns. Every column other than 0 reaches redis-py
  exceptions as `redis.ResponseError` and `redis.RedisError` through the setup's `redis` module,
  and no column re-imports anything the setup block already provides (`json`, `os`, `re`, `sys`,
  `time`, `uuid`, `datetime`, `timedelta`, `timezone`, `mo`, `niquests`, `np`, `redis`).
  `tests/test_safety.py::test_import_ownership` enforces all of this.
- The fragment reads only setup names (section 4), `cairn.*`, `redis_cli`, `shared_stems`,
  `EMBEDDING_MODEL_NAME`, `embed_texts`, `load_embedding_model`, and its own column's globals. It
  never reads another column's globals.
- Every `mo.md` string in the fragment passes `tests/test_voice.py`: lead-ins of at most two
  sentences of at most 25 words, colon-terminated; no semicolon; serial commas; lowercase "hit"
  and "miss" in prose.
- The builder checks the fragment by running `uv run python .agents/fragments/assemble.py`
  (which inserts whatever fragments exist and leaves the other markers in place), then
  `uv run marimo check --strict iris_basics.py` and `uv run pytest`. `git checkout iris_basics.py`
  afterwards; the builder commits only the fragment and its README piece.
- A builder never edits the scaffold, `src/`, `tests/` or another fragment. A needed change there
  is a note in the fragment's commit message for the assembly step.

`.agents/fragments/assemble.py` (about 25 lines, stdlib only):

```python
"""Insert the column fragments into the scaffold, in column order. Idempotent on the scaffold."""
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRAGMENTS = {0: "column0_redis_search.py", 1: "column1_langcache.py", 2: "column2_context_retriever.py", 3: "column3_agent_memory.py"}
notebook = ROOT / "iris_basics.py"
text = notebook.read_text(encoding="utf-8")
for column, name in FRAGMENTS.items():
    marker = f"# @@ COLUMN {column} CELLS @@"
    if marker not in text:
        sys.exit(f"marker missing: {marker} (run on the scaffold, not on an assembled notebook)")
    path = ROOT / ".agents" / "fragments" / name
    if path.exists():
        fragment = path.read_text(encoding="utf-8").strip("\n")
        cells = re.findall(r"^@app\.cell\(column=(\d)", fragment, flags=re.M)
        assert cells and set(cells) == {str(column)}, f"{name}: every cell must carry column={column}"
        text = text.replace(marker, fragment)
notebook.write_text(text, encoding="utf-8", newline="\n")
print("assembled", [n for c, n in FRAGMENTS.items() if (ROOT / ".agents" / "fragments" / n).exists()])
```

The concatenation rule in one sentence: the assembled notebook is the scaffold with each
`# @@ COLUMN N CELLS @@` line replaced by the whole content of fragment N, so every column's
cells follow that column's title cell and the file order is column 0, 1, 2, 3.

### Step 2. Assembly (one agent, sequential)

1. On the scaffold commit with the four fragment commits merged: `uv run python .agents/fragments/assemble.py`.
2. `uv run marimo check --strict iris_basics.py`; `uv run pytest`; `wc -l iris_basics.py` against
   section 16; `uv run marimo export html iris_basics.py -o /tmp/iris_basics.html` with no `.env`
   present (exit 0 proves no cell raised without credentials).
3. Paste the four `readme_columnN.md` pieces into the README's Steps 4-7 markers; run the
   pre-publish checklist from VOICE.md over the README and every `mo.md` string.
4. Open `uv run marimo edit iris_basics.py` once with no `.env` and save, so marimo normalises
   cell signatures and `return` tuples; re-run step 2.
5. Delete `.agents/fragments/`, commit "Assemble the MVP notebook".
6. Nick runs the notebook live once per column against the section 18 checklist and adjusts
   literals, never design: the radius literal, the Agent Memory slider default, the request B
   wording in the README's "Verified live", the cache-aside score, the `id` argument name of
   `get_customer_by_id`, and the `Accept`-less MCP session.

## 18. What Nick must provide, and the first-live-run checklist

`.env` next to `pyproject.toml`, per column (names only; nothing else is read). Console paths
here are the setting's name and the console page, and this section and `.env.example` are the
only places they appear.

| Column | Variables | Console page | Notes |
|---|---|---|---|
| Redis Search | `REDIS_URL` | The database's Configuration page (Public endpoint) and Security page (Default user password). Form `redis://default:<password>@<host>:<port>`; `rediss://` on a TLS database | A free 30 MB database with Search is enough. The same database serves the Context Retriever column, so it needs JSON as well and must be reachable from the internet with no CIDR allow list. Use a scratch database: the Context Retriever column hands its password to the hosted service. |
| LangCache | `LANGCACHE_HOST`, `LANGCACHE_CACHE_ID`, `LANGCACHE_API_KEY` | The service's Connectivity page (URL, Cache ID); the key is shown once at creation | **Recommendation: a dedicated service for this notebook**, created as a custom service named `iris-basics-notebook` with TTL "No expiration" (the notebook sets `ttlMillis` itself), the Redis embedding provider, threshold 0.85 and no attributes (LangCache brief 4.3). A cache is shared by everything that points at it, and the live Iris demo searches the same kind of cache unscoped, so anything stored on a shared one can be served to somebody else's app until it expires. Open point: whether a second service can use the same free database as the demo's service; if not, pick another database. |
| Context Retriever | `CTX_ADMIN_KEY`; optional `CTX_API_URL`, `CTX_MCP_URL` | The Context Retriever Admin keys page; a new key is shown once | Plus `REDIS_URL` above. Surfaces have no TTL; Clean up matters here. |
| Agent Memory | `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, `MEMORY_API_KEY` | The service's General settings page (Endpoint with `https://`, Store ID) and its API keys page | The default store uses Redis's own models, so no LLM key. |

**Redis secrets, as the brief asked.** No new Redis secret is *required*: `REDIS_URL` with the
default user runs columns 0 and 2. One is *recommended*: a dedicated Redis Cloud data-access user
for Context Retriever (Redis Cloud database > Security > add a user with its own password), used
only in the `REDIS_URL` the notebook hands to the hosted service, so that deleting the surface
and that user revokes the hosted service's access without rotating the default password. Open
question carried from the Context Retriever brief: the ACL scope the service needs (it creates
indexes with `FT.CREATE`, writes JSON with `JSON.SET`, and reads with `FT.SEARCH`/`JSON.GET`; a
user restricted to `~cairn:*` keys and the `@json @search @read @write` command categories is
the starting guess and must be checked live, because a too-narrow ACL surfaces as a failed data
import, not as a clear error). Until that is settled, the default user on a scratch database is
the documented path.

Decisions only Nick can take:

1. Python 3.11: keep the marker split if `uv sync --python 3.11 && uv run pytest` passes in
   step 0; otherwise raise the floor to 3.12.
2. Whether the console's URL List shows the LangCache base URL with or without `https://`; the
   notebook normalises both, but `.env.example`'s placeholder should match what the console shows.
3. Whether to create the dedicated Redis user above, and what ACL it gets.

**First-live-run checklist** (one pass per column, after assembly; literals may change, design
may not). Each item names the fallback if it fails.

| # | Column | Check | Fallback |
|---|---|---|---|
| 1 | 0 | `FT.HYBRID` with `LOAD 1 @__key` returns the RESP2 pair list `rs_engine_results` parses (already verified on 8.6.2; confirm on this database's version) | none needed; the `try/except ResponseError` shows the error as a sentence |
| 2 | 0 | The literal radius 0.5 gives a miss for *best pizza near me* and rows for *will it fit my feet* | change the literal and the README troubleshooting sentence |
| 3 | 1 | The custom `User-Agent: redis-iris-basics` is accepted by all three hosted services (the live run sent `python-httpx/x.y`) | drop the `User-Agent` header from all sessions |
| 4 | 1 | The bodiless `DELETE /entries/{id}` with the session's `Content-Type: application/json` answers `204` (the verified request carried no `Content-Type`) | add a `headers=` pass-through to `call_service` and send `{"Content-Type": None}` on that call |
| 5 | 1 | The cache-aside hit on the prefixed *What should I do about a broken tent pole?* clears 0.85; record the score for the README | lower the `similarityThreshold` sent in that one search and say so in the copy |
| 6 | 2 | The MCP session's explicit `Accept: */*` gets JSON from `tools/list` under niquests (httpx's `*/*` was the verified shape; `niquests.Session(headers=)` replaces the default headers, so the value is set by hand, review round 1) | change it to `"Accept": "application/json, text/event-stream"` (the docs' curl) in `cr_mcp_service` and update the safety test |
| 7 | 2 | `get_customer_by_id`'s `inputSchema.required` contains `id` (the cell checks and warns) | rename the argument in `cr_call_tool` |
| 8 | 2 | Request B in `cr_rejected_call` (tag condition on `lifetime_value`): record which branch fired and the exact text for the README "Verified live" | none; the cell prints what arrives in every branch |
| 9 | 2 | The tools count on this account (21 on 2026-09-30) | update the hedged gloss |
| 10 | 2 | If a dedicated Redis user is used: the data import reports `imported` 6/6/8 and `FT._LIST` shows three new indexes | widen the ACL, or fall back to the default user |
| 11 | 3 | The slider default 0.3 returns the allergy memory alone for the kitchen question | change the default and the hedged line |
| 12 | 3 | After the session cell, wait past the cadence and press Clean up again; the owner-only listing finds and deletes any extracted memories | if extracted memories carry no `ownerId` match, note it in section 20 and the README |
| 13 | 2 | The reply shape of `DELETE /context-surfaces/{id}`: which of 200/204 arrives and whether a 200 body echoes the resource (`cr_cleanup` shows the status alone on a gone reply, review round 1) | none needed; record the shape in the Context Retriever brief 2.6 |
| 14 | 2 | The sort order of `GET /context-surfaces` (oldest or newest first) against the 20-page cap in `cr_cleanup`; the summary says so when `has_next` is still true | raise the cap, or delete the leftover `cairn-<run>` surface in the console |
| 15 | 3 | Press Write twice in one kernel and record the status and body of the second reply (Agent Memory brief 2.2: re-creating an existing id is UNVERIFIED, upsert or per-item error; R3.1 makes the second press likely, since the ids are stable per machine) | if it is a per-item error, `am_write` needs a branch that reads it as "already written" instead of rendering the `errors` callout |

## 19. Rules for the builders

1. Every markdown-only cell is `mo.md(r"""...""")`; no one-line `mo.md("...")` as a cell's whole
   body (MF007 inspects only markdown-only cells, so one-line `mo.callout(mo.md("..."), kind=...)`
   inside a code cell is fine); no cell contains only `pass` (MF004).
2. A `run_button` is defined in one cell and read in the next; no cell reads its own element.
   `rs_load_button` and `lc_store_button` are defined in the configure cell (section 7.3).
3. Seed cells in columns 0 and 3, and every non-seed button cell (`lc_cache_aside`, `am_session`),
   use the native shape: `mo.stop(not <button>.value, <info callout naming the button>)` first.
   Seed cells in columns 1 and 2 (`lc_store`, `cr_create`) never `mo.stop` and define every state
   name in both branches, initialised before the first request. The first example cell of a
   column `mo.stop`s with the one-line info callout; later example cells use the bare `mo.stop`.
   Clean up depends only on setup names, configure-cell names, always-defined ids and its button.
4. Never assign a secret (`REDIS_URL`, any key, the agent key) to a global; read `os.environ`
   inside the expression that builds the client or session. Never let a reply that may carry a key
   or another tenant's data be a cell's last expression or a `mo.json` argument. Mask the create
   request's password on the wire dict before binding it. The reply to the create request is
   never rendered: on a non-2xx, only the status and the cut `type`, `title`, `detail` strings,
   after checking the password is absent from the serialized body (section 10 cell 5).
5. No `retries=` on any session; `disable_http3=True` and `"Content-Type": "application/json"` on
   every session; `"Accept": "application/json"` on the three REST sessions and `"Accept": "*/*"`
   on `cr_mcp_service` (the value httpx sent live; niquests sends none unless set); timeouts
   `(5, 30)`, or `(5, 60)` for the admin API.
6. Show the wire with `redis_cli(argv)` rebuilt from redis-py, or with `call_service`'s `wire` in
   `mo.json`; never `print`, never `repr(session)`.
7. Only two functions may be defined in column cells: `lc_own_hits` and `cr_data_source`. Every
   other cell is straight-line code a reader can retype into a script.
8. Copy: a lead-in is at most two sentences of at most 25 words each and ends with a colon; a
   gloss is one or two sentences; no semicolon in any `mo.md` string; serial commas; lowercase
   "hit" and "miss" in prose. Hedge every number from the live run; state limits flat; Redis or
   the service is the actor of the return-value sentence; commands uppercase and backticked; no
   exclamation marks; no "developers" in prose; no trial link; no console click path.
   `tests/test_voice.py` is the gate.
9. Imports: only the names your column owns (section 17), at the top of the cell that first uses
   them; everything else comes from the setup block, and redis-py exceptions outside column 0 are
   `redis.ResponseError` / `redis.RedisError`.
10. Names are full words: `lc_entry_ttl_milliseconds`, not `lc_entry_ttl_ms`; comprehension
    variables are `article`, `preset`, `entry`, `hit`, `row`, never one letter.
11. Do not change the notebook width in the editor (marimo #3543); edit the `.py` directly.
12. Body budgets in section 16 are ceilings; the cut order there is the only allowed way over.

## 20. Risks flagged (not relitigated)

- Request B in `cr_rejected_call` (a tag condition on the numeric field `lifetime_value`) has an
  outcome inferred from the schema shape, not observed live; the cell prints whatever arrives,
  has an accepted branch, and the copy asserts nothing about it. Request A is the observed one.
- The 0.5 radius and the 0.3 Agent Memory default rest on one live run and one offline
  calibration; the README's troubleshooting entry and the slider cover a different store or model.
- `time.sleep(12)` in `lc_cache_aside` blocks the kernel for twelve seconds; a slow expiry could
  still answer the `DELETE` with `204`, reported as such; a `429` surfaces as text.
- Python 3.11 is locked by marker but never run before step 0.
- The body budgets in section 16 are estimates; the 8-line overhead per cell is an average, and a
  cell that depends on ten globals costs about 14. The 50-line margin to the 1,500 ceiling absorbs
  about four such cells; past that the cut order applies.
- Without a ledger, a LangCache Clean up after a kernel restart finds nothing; every entry carries
  `ttlMillis`, and the README says the cache self-heals in 15 minutes.
- `mo.ui.table` with `style_cell` colouring and 36-row tables in a 740 px run-mode column are
  unverified without a browser; `max_height=` is the fallback.
- Whether `_underscore` cell-locals are excluded from the Variables-panel broadcast is UNVERIFIED;
  no cell binds a secret even to a local for longer than the expression that uses it, except the
  `cr_data_source` frame, which returns before the cell continues, and the `_password` comparison
  local in `cr_create`'s failure branch, which the next statement overwrites.
- Three header deviations from the live run are untested against the hosted endpoints and sit on
  the section 18 checklist with fallbacks: the custom `User-Agent`, the pinned `Content-Type` on
  a bodiless `DELETE`, and `Accept: */*` set explicitly on the MCP session (the value httpx sent by
  default; `niquests.Session(headers=)` replaces the defaults, so an unset header would go out
  absent, review round 1).
- `cr_create`'s failure branch calls `cr_data_source()` a second time to obtain the password for
  the "is it in the body" check; that string lives in a cell-local for one statement. The
  alternative, skipping the check, would let a validation body that echoes the request reach the
  screen through `title` or `detail`, which is the worse risk.
- `SESSION_TURNS` carry two facts, so extraction may create owner-scoped records with
  service-chosen ids minutes after the session; the owner-only listing in `am_cleanup` is designed
  to catch them, and the copy tells the reader to press Clean up again after the cadence.
- The dedicated Redis user for Context Retriever (section 18) is a recommendation with an
  unverified ACL scope; a too-narrow ACL shows up as a failed data import, and the fallback is the
  default user on a scratch database.
