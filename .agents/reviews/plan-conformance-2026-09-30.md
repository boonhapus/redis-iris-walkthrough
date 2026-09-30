# Plan conformance: `iris_basics.py` against `iris-basics-4-column-plan.md`

**Date:** 2026-09-30 · **Baseline:** commit 37a6e0f, branch main · **Method:** static read of the
notebook (8,713 lines, 112 cells), the plan (rev 2 + §10 "As built"), README "Verified live",
`.env.example` and the test tree. The notebook was not run. Every claim below cites a line range
in `iris_basics.py` unless another file is named.

## Short version

The notebook implements the plan almost item for item. Of the roughly 95 concrete items in §3.2,
§3.4, §3.6 and §5, I found 90 implemented as written, 4 partial, 1 missing, and 5 deliberately
deviated as §10 records. I also found 11 deviations §10 does not record; all are minor and most
are stricter than the plan. So the conformance question has a boring answer: yes. The interesting
finding is what conformance cost. The plan asked for a 1,880-line safety harness (lines 33-1911)
and got one, then 3,769 lines of column helpers (1912-5680) to feed 3,028 lines of cells
(5684-8711). Sixty-five percent of the file runs before the first column cell, and a developer
who has never seen Redis Iris meets `SecretRegistry`, `_ft_create_prefixes`, an HTTP route
refusal table and a disk ledger before meeting `FT.CREATE`. The MVP brief is not a bug-fix on this
notebook. It is a rejection of §3.4 and most of §3.6, and this review's job is to say which
teaching moments survive that.

Size facts used below:

| Region | Lines | Share |
|---|---|---|
| Setup: shared infrastructure (identity, secrets, env parser, HTTP, guard, grants, ledger, UI, `RECORDED_LIVE`) | 33-1911 (1,879) | 22% |
| Setup: column helpers `rs_` `lc_` `cr_` `am_` | 1912-5680 (3,769) | 43% |
| Column cells (28 / 25 / 28 / 31) | 5684-8711 (3,028) | 35% |

---

## 1. Shared contract (§3.1-3.4, §3.6)

Status key: **impl** implemented as written · **partial** present but not as specified ·
**missing** · **dev (§10)** deliberate deviation recorded in §10 · **dev (undoc)** deviation §10
does not record.

| Rule | Status | Where | Notes |
|---|---|---|---|
| §3.1 `App(width="columns", app_title=...)`, indices 0-3 in order, every cell carries `column=N`, contiguous | impl | 31; 5684-8711 | 28/25/28/31 cells. |
| §3.1 order title → config → core → wow → cleanup → deep dive → helpers at column bottom | dev (§10) | helpers at 1912-5680 | Helpers moved into setup for edit-mode speed (300 items → 112 cells). Column order otherwise holds in all four. |
| §3.1 `with app.setup(hide_code=True)` | impl | 33 | |
| §3.1 run-mode `--content-width` ~560 px via inline `<style>` | impl | 5719-5721 | Plus three undocumented CSS hacks on marimo internals (5717, 5726-5734): hides the floating outline, lifts the 610 px output cap, and rewrites the "stopped cell" placeholder text. Fragile: keyed on `div.fixed.right-8.z-10000` and `.marimo-cell.stopped`. |
| §3.1 wide content stacks or uses `mo.ui.tabs`; no side-by-side tables | impl | no `mo.ui.tabs` anywhere; 20 `mo.hstack` all for controls | |
| §3.1 intro: what Iris is, four anchors, launch + safety notes, `ib_notes`, AUTORUN banner | impl | 5684-5811 | Adds "Settings detected" (names only, 5802-5805). |
| §3.2.1 title cells: `# N · Component`, 3-6 sentences, docs link, quick-create link, small mermaid | impl ×4 | 5825, 6449, 7103, 7832 | Mermaid direction flips TB in run mode. |
| §3.2.2 config: non-secret fields prefilled from env; secrets never prefilled, masked placeholder, resolved at call time | impl ×4 | `SecretText` 402-419; 5901, 6533-6551, 7192-7206, 7923-7937 | `SecretText._convert_value` wraps typed text in `Secret` so the Variables panel shows `••••1234`. |
| §3.2.2 "Where do I get these?" + "Never on a shared service" callout + read-only Test connection | impl ×4 | 5860-5898, 6486-6530, 7138-7189, 7866-7920 | |
| §3.2.2 Orphans sub-panel | impl ×3, n/a ×1 | rs 5940-5988, cr 7283-7325, am 7970-8014; lc says it cannot enumerate at 6616-6618 | Matches §3.6 "cannot be enumerated; say so". |
| §3.2.3 core cells: `###` heading, controls cell + result cell, real parameter, collapsed wire view | impl | every core cell; 21 `wire_view(` calls in cells | Fetch/render split is real: e.g. 6106-6116 fetch, 6119-6140 render. |
| §3.2.4 one wow per column | impl ×4 | 6279-6341, 6863-6988, 7641-7744, 8322-8465 | |
| §3.2.5 cleanup always available | impl ×4 | 6344-6383, 6991-7041, 7747-7800, 8468-8528 | |
| §3.2.6 deep dive behind `*_deep` switch with `mo.stop(not deep.value, None)` | impl ×4 | 6386-6446, 7044-7100, 7803-7829, 8531-8709 | Switch default is `AUTORUN == "demo"` in all four (6389, 7047, 7806, 8535): a test hook decides a presentation control's default. Undocumented. |
| §3.2 (a) controls feeding calls are debounced or behind a run_button | partial | sliders all `debounce=True`; `mo.ui.text` debounces by default in marimo | Switches (6089-6090), the multiselect (6215) and dropdowns (6283, 7577, 7645) have no debounce parameter in marimo and fire one call per click. Acceptable, but not what the rule literally says. |
| §3.2 (b) index builds and all writes behind a run_button | impl | 6017/6042, 6595/6628, 7445/7477, 8069/8097, 6394/6420, 7052/7073, 6863/6898, 8017/8044 | Every write path checked. |
| §3.2 (c) fetch split from render | impl | see rows above; also 8159/8205, 7608/7625 | |
| §3.2 (d) re-score client-side ("sweep once, slide locally") | impl | lc lab 6762/6772 + 3008-3160; lc strategies 6822/6834; am lens 8159/8184-8243 | |
| §3.2 (e) only the config cell renders `not_configured`; downstream `mo.stop(..., None)` | impl | 5916-5919, 6577, 7251-7261, 7952; every downstream cell stops on `None` | |
| §3.2 (f) `Say/Expect` under each core cell when `ib_notes` is on | dev (§10) | `notes()` 1655-1664; one `<style>` cell 5814-5822; 17 `notes(` calls | Always rendered with class `ib-note`, toggled by CSS, to stop NameErrors on re-run. |
| §3.3 prefixes `rs_ lc_ cr_ am_ ib_`, locals `_` | impl | throughout | Entity classes `Customer/Product/Order` inside `cr_build_models` (3413-3510) as the plan's exception. |
| §3.3 cells `import context_surfaces as cr_cs` | dev (§10, implied) | import is local to `cr_build_models` | Moot once helpers moved into setup. |
| §3.3 setup frozen after Phase 2; helpers reference only setup names | dev (§10) | | §10 says the global-name rule "no longer applies". |
| §3.4 `RUN` from `IRIS_BASICS_RUN` if `^[0-9a-f]{8}$` else `uuid4().hex[:8]` | impl | 89-91 | |
| §3.4 `PREFIX`, `RS_PREFIX`, `CR_PREFIX`, `KEY_TTL_S`, `LC_TTL_MS`, `LC_TEACH_TTL_MS` | impl | 93-102 | Adds `RUN_TAG`, `AM_OWNER`, `AM_NAMESPACE`, `CR_CREATED_BY`, `CR_KEY_TTL_S=7200`. |
| §3.4 AUTORUN only with a pinned RUN, real env only, refused in `edit` unless `IRIS_BASICS_HEADLESS=1` | impl | 104-120; `_names_in_dotenv` 67-83 | 50 lines exist to distrust a `.env` that marimo already loaded. |
| §3.4 `pressed(btn, stage)`; never-autorun buttons use plain `.value`; every text input has a non-empty preset | impl / partial | 122-134; plain `.value` at 5980, 7317, 8004, 8047, 8386, 8632, 8693, and all Reset/Keep buttons | Orphan confirm fields (5942, 7285, 7972) and optional form arguments (4489-4517) are intentionally empty; `lc_host`, `am_base` etc. default to `env(...)` which may be "". |
| §3.4 `ENV_ALLOWLIST` exactly as listed; never `CTX_SURFACE_ID`, `MCP_AGENT_KEY`, `MEMORY_OWNER_ID`, `OPENAI_*` | impl | 281-289; `env()` raises `KeyError` on any other name (346-347) | |
| §3.4 `env()` reads `os.environ` first, then a private dict from `IRIS_BASICS_ENV_FILE` or `.env`; strips quotes/CR; `""` unset; never writes `os.environ` | impl | 294-353 | This is the hand-rolled layer the brief replaces with marimo's native loading. |
| §3.4 `redis_url_from_env()` composes from split fields with percent-encoded userinfo | impl | 359-372 | |
| §3.4 `Secret` with masked repr and `.reveal()`; `validate_secret` | impl | 228-279 | |
| §3.4 `SECRETS` registry; `scrub()` in raw, percent, JSON and HTML forms plus the three regexes | impl | 136-227 | |
| §3.4 `HTTP_CLIENT` monkeypatchable `httpx.Client`; `http_call` returns `(status, body)`, catches everything, raises scrubbed `ServiceError` | impl | 535-537, 866-910 | |
| §3.4 recursive redaction by key name, incl. `connection_config` except `addr` | impl | 441-476 | Adds `_learn_secrets`/`_seal` (477-518) that register credential-shaped reply fields at runtime. |
| §3.4 `HTTP_TRACE`, `as_curl` with `$ENV_VAR` placeholders | impl | 538, 912-930 | |
| §3.4 choke points: only GET/POST/DELETE/PUT; refuse `/flush`; LC delete-by-attributes only `{scope: run tag}`; AM deletes only ledgered/verified; CR delete/PUT only ledgered/confirmed | dev (undoc), stricter | `HTTP_VERBS = ("GET","POST","DELETE")` 534; route table 767-788; rules 622-765 | PUT is never sent at all. The plan's four named rules became a 14-route allowlist where anything unlisted is refused (790-794). README says "never sends a PUT"; §10 does not. |
| §3.4 timeouts ~10 s reads, ~30 s create/import; `with_spinner` | impl | 539-540, 990-997 | |
| §3.4 `connect_redis`: `protocol=2, decode_responses=False, socket_timeout=10`, registers password, scrubs errors | impl | 1277-1300 | Also asserts the URL did not force another protocol (1293). |
| §3.4 `GuardedTracedRedis`: writes under `PREFIX`, multi-key checked, `FT.CREATE` needs `PREFIX n p...` under `PREFIX`, `DD` always denied, `FT.DROPINDEX` outside `PREFIX` only for `OWNED_INDEXES`, pipelines/module pipelines/`batch_indexer` denied, reads allowlist, `REDIS_TRACE` | impl, plus extra | 999-1275 | Extra, undocumented: `_confirm_index` (1246-1259) runs `FT.INFO` after every `FT.CREATE` and drops the index if it reaches beyond `PREFIX`. `HGET` added to reads (1001). `FT.DROPINDEX` also allowed for `ORPHANS["index"]` grants (1137). |
| §3.4 `owned_index` decodes bytes, needs non-empty prefix list all under `sub_prefix`, fails closed; only it feeds `OWNED_INDEXES` | impl | 1074-1088; `claim_index` 1406 | Also refuses any `sub_prefix` not starting `irisnb_` (1084). |
| §3.4 `_cli(argv)` with `<N bytes>` elision; `render_redis_trace(n)` | impl | `redis_cli` 1153-1166, 1329 | |
| §3.4 writes paced ≤ 20 ops/s; `SCAN COUNT 1000`; delete by name from ledger, `SCAN` only for reclaim/footprint | impl | 1009, 1020-1025, 1302-1309; `rs_cleanup` 2562 uses the ledger only | |
| §3.4 ledger: `%LOCALAPPDATA%/iris-basics/{RUN}.jsonl`, `intent/created/deleted`, `ledger_live`, `ledger_runs`, no secrets | impl | 1522-1589 | |
| §3.4 UI helpers `wire_view`, `status_callout`, `not_configured`, `notes`, `recorded_live` | impl | 948, 1593, 1638, 1655, 1889 | `RECORDED_LIVE` is 207 lines of captured JSON (1683-1889). |
| §3.6 cleanup depends only on config, ledger at click time and its own button; `pressed(btn, "cleanup")`; a test proves it | impl ×4 | 6365-6383, 7013-7041, 7771-7800, 8492-8528; `tests/test_shared.py:3543` | Each cleanup cell takes the config UI elements and buttons only, re-resolves secrets, and reads `ledger_live` inside the helper. |
| §3.6 per-column Reset: runs cleanup, clears state, bumps `*_gen` | impl ×4 | `mo.state(0)` at 5906, 6555, 7209, 7940; bumped at 6380, 7038, 7797, 8525; read at 5914, 6572, 7247, 7948 | Not exercised live (README). |
| §3.6 Search: `RS_PREFIX`, EXPIRE every key, cleanup from ledger (`UNLINK`, `FT.DROPINDEX` no DD, tolerate unknown index), orphans by `SCAN irisnb_*` + `owned_index` for other RUNs, never FLUSH/CONFIG/`memory:*` | impl | 2520-2541, 2552-2581, 2461-2506, allowlist 999-1008 | `is_unknown_index` (1311-1318) knows both Redis wordings (§10 live fact). |
| §3.6 LangCache: `ttlMillis` on every set; delete-by-id from ledger, 404 = expired; orphans cannot be enumerated; never flush or delete-by-attributes except run tag | impl | `_lc_set` refuses a missing/oversized TTL 622-626; `lc_delete` 2853; `lc_cleanup` 3355-3365; 6616-6618 | Choke point makes a TTL-less write impossible, not just avoided. |
| §3.6 Agent Memory: `ownerId ib-{RUN}` + namespace on every call; first event USER with actorId + namespace; cleanup = delete sessions → list by owner only → client-side owner check, abort on stranger → bulk delete; extraction note + Re-sweep; namespace orphan sweep for other `ib-` owners after typed confirm; never `includeAll` or un-ledgered ids | impl | `am_filter` 4948, `_am_search` 661-677, `am_event_body` 5191-5202, `am_add_event` 5204-5226, `am_cleanup` 5421-5479 (stranger abort at 5451-5459), `am_orphan_sweep` 5365-5402, `_am_memories` 694-705 | "Re-sweep" is the button "Clean up again" (8471). |
| §3.6 Context Retriever: surface `iris-basics-{RUN}` + `metadata {created_by, run}`, key templates asserted under `CR_PREFIX`, `pool_size 2, min_idle_conns 0`, agent key expires now + 2 h, EXPIRE CR keys by name after import, cleanup = DELETE surface → UNLINK keys → drop indexes new since the `FT._LIST` snapshot and `owned_index(..., CR_PREFIX)` → report leftovers; orphans `metadata.run != RUN` older than 2 h after typed confirm; never PUT | impl | `_cr_create` 714-743, `cr_launch` 4097-4163 (assert 4109-4110, snapshot 4113), `cr_connection_config` 3923-3938, `cr_mint_key` 3978-3990, `cr_expire_keys` 4036, `cr_cleanup` 4699-4744, `cr_drop_indexes` 4642-4675, `cr_orphans` 3870-3906 | `cr_confirm_gone` (4677-4697) is an extra guard: a 404 on DELETE is trusted only after the listing proves the address works. |
| §3.6 consent: first write is a run_button whose label says what and where; CR consent says the DB password is sent; shared-DB warning for `default` user | impl / partial | labels 6019, 6598, 7449, 8072; CR password text 7457-7459 and callout 7176-7181; `shared_db_warning` 1623-1636 used at 2449, 3856 | Labels say *what* ("Write 36 articles and 1 index"); *where* is in the markdown above, not the label. |
| Header pins exactly seven deps; `test_header_pins_the_stack` asserts them | partial | 1-15 pins match the plan | **No test asserts the pins.** `grep` of `tests/` for `model2vec==`, `redis==8.1.0`, `httpx==0.28` finds nothing; the named test does not exist. Undocumented. |
| §1.1 no `mo.ui.chat`, no `.form(clear_on_submit=True)`; §1.6 no `asyncio.run` | impl | comment at 8372; no matches for any of the three | |

---

## 2. Column 0 · Redis Search

| §5 item | Status | Where | Notes |
|---|---|---|---|
| Title: Query Engine, docs link, mermaid app → embed → HSET → index watching a prefix → FT.SEARCH | impl | 5825-5857 | |
| Config: `REDIS_URL` secret-style field; Connect runs PING, `INFO server`, `FT._LIST`, `INFO clients`, sets `rs_has_hybrid`/`rs_has_svs`; shows `DBSIZE` and existing `irisnb_*` counts | impl | 5901-5937; `redis_facts` 1352-1382; `rs_probe` 2411-2435; `rs_describe` 2437-2459 | Hybrid ≥ 8.4, SVS ≥ 8.2 by version tuple (2431-2432). Connect also runs a `SCAN irisnb_*` and a `TTL` per other run (2424-2427). |
| Embedder potion-base-8M via `snapshot_download(allow_patterns=[4 files])`, `local_files_only=True` first, spinner "first run: ~30 MB", `DIM` derived, loud hashing fallback | impl | 5991-6014; `rs_load_embedder` 2110-2142 | |
| Seed (consent): ~36 articles (`title` TEXT WEIGHT 2, `body` TEXT, `category` TAG, one NUMERIC), HASH under `RS_PREFIX+"doc:"`, paced, EXPIRE each, ledgered; index `RS_PREFIX+"idx"` HNSW COSINE; show the `FT.CREATE` | impl | 6017-6082; `rs_articles` 1912 (36 rows, 6 per category); `rs_create_args` 2166-2172; `rs_seed_corpus` 2520-2541 | Wire view shows the `FT.CREATE` and one `HSET` with the vector as `<1024 bytes>` (6068-6074). |
| Core 1 full-text: query box + prefix/fuzzy toggles, `SCORER BM25STD WITHSCORES` | impl | 6085-6140; `rs_text_query` 2152-2164; `rs_text_args` 2181-2186 | |
| Core 2 KNN vs `VECTOR_RANGE`: k slider + radius slider, both debounced; range can return 0 = cache MISS bridge | impl | 6143-6208; `rs_knn_args` 2188-2197; `rs_range_args` 2199-2205 | The MISS callout says "KNN could never say this" (6194). |
| Core 3 hybrid pre-filter: category multiselect + numeric range → pre-filtered KNN | impl | 6211-6276; `rs_filter` 2207-2212 | Shows filtered and unfiltered side by side (stacked). |
| Wow "one query, three engines": presets (lexical trap, exact keyword, typo), one rank table BM25 / vector / hybrid (`FT.HYBRID` RRF else client-side RRF, labelled), target highlighted, command trace; offline tests on the trap | impl | 6279-6341; `rs_wow_presets` 2031-2058; `rs_wow_search` 2321-2350; `rs_hybrid_args` 2214-2224; `rs_rrf` 2313; tests `test_redis_search.py:1145,1154` | Source label at 6326-6328. |
| Deep dive index anatomy: FLAT/HNSW (+SVS-VAMANA if available), metric, second index over the same prefix, compare `FT.INFO`, "same results at this scale; ANN matters at 10⁵+" | impl | 6386-6446; `rs_build_second` 2603-2617; `rs_compare_indexes` 2619 | Optional `FT.PROFILE` not built (plan said optional). |
| Cleanup: drop ledgered indexes (no DD), UNLINK ledgered keys | impl | 6344-6383; `rs_cleanup` 2562-2581 | |

---

## 3. Column 1 · LangCache

| §5 item | Status | Where | Notes |
|---|---|---|---|
| Title: managed semantic cache, docs link, cache-aside mermaid | impl | 6449-6483 | |
| Config: host, cache ID, API key (secret), optional scope attribute with a one-line note; shared-cache and never-Generate-key warnings | impl | 6486-6567 | |
| Test connection: read-only sentinel search **and a floor probe** that records `lc_floor` for slider minimums | **dev (undoc)** | connect 6570-6592 runs only `lc_probe_cache` (2876-2899); the floor probe `lc_probe_floor` (2955-2970) runs in the seed-result cell 6628-6650 | Sensible (the probe needs the run's own entries to exist) but §5 puts it in Test connection, and as built `lc_floor` and every slider exist only after seeding. |
| Detect attribute support via the 400 error | impl | 2887-2892 | Drops to unscoped and says why (2921-2928). |
| Result filtering: `maxResults=5`, keep only ledgered ids, show others as "another app's entry (hidden)", never their prompt or response | impl | `lc_search` 2779-2829 | Also rewrites the trace entry so the wire view never carries a foreign prompt (2816-2818). |
| Seed (consent): 4-6 gear-care Q&A with `ttlMillis`, ledgered, request JSON shown | impl | 6595-6650; `lc_seeds` 2656-2680 (5 entries); `lc_set_body` 2831 | |
| Core 1 anatomy of a hit: one search with full request/response JSON | impl | 6653-6727 | |
| Core 2 threshold lab: ~15 labelled probes searched once at the floor; slider re-scores client-side; rows coloured hit / false-hit / missed / foreign; `mo.stat` tiles precision, recall, hit rate on own entries only | impl | 6730-6790; `lc_probes` 2682-2704 (14 probes: 1 same-words, 5 paraphrase, 2 swapped item, 2 near-miss, 1 negation, 3 unrelated); `lc_sweep` 3008; `lc_scoreboard` 3042; `lc_lab_tiles` 3062 | |
| Core 3 `searchStrategies` exact vs semantic vs both on case, punctuation, paraphrase variants | impl | 6793-6853; `lc_variants` 2706-2716; `lc_strategy_sweep` 3160 | |
| Wow cache-first chat (DIY): Send searches at slider threshold; HIT badge (similarity, matched prompt, id, ms); MISS → Teach; Teach prefixes "At Cairn Outfitters: ", refuses prompts with no Cairn word, stores with `LC_TEACH_TTL_MS`, deletes the taught entry after its paraphrase HIT; red shared-cache callout when unscoped; AUTORUN script ask → teach → paraphrase | impl | 6856-6988; `lc_taught_prompt` 3202; `lc_teach_refusal` 3215-3230; `lc_teach` 3251-3271; taught-delete cell 6972-6988; `lc_shared_warning` 3208; `lc_autorun_chat` 3273 | History list held in a cell only Reset re-runs (6856-6860), exactly the §1.1 recipe. |
| Deep dive TTL: store `ttlMillis=10000`, Check again turns HIT into MISS, delete-by-id then 404 | impl | 7044-7100; `lc_ttl_store` 3318; `lc_ttl_look` 3332-3347 | 404 type check per §10 live fact is in `lc_delete` 2853. |
| Cleanup: delete-by-id from ledger, report counts; AST test on column 1 for `flush` | impl | 6991-7041; `lc_cleanup` 3355-3365; `tests/test_lc.py:1051` walks column items only | |

---

## 4. Column 2 · Context Retriever

| §5 item | Status | Where | Notes |
|---|---|---|---|
| Title: data model → governed MCP tools; docs link; mermaid ContextModel → admin API → surface → tools; agent + key → MCP → your Redis | impl | 7103-7135 | |
| Config: admin key (secret), `REDIS_URL` (secret-style), optional API/MCP URLs; Test connection = `GET /api/v1/context-surfaces?page_size=…` + orphans panel; shared-DB warning; admin-key-tab note | impl, one dev (undoc) | 7138-7325; `cr_probe` 3812; `cr_list_surfaces` 3769 (50 × 10 pages); `cr_describe` 3842-3868 | **Undocumented:** the optional URLs are prefilled with hard-coded defaults `https://cloud.redis.io/context-surfaces` and `https://gcp-us-east4.context-surfaces.redis.io/mcp` (7199, 7204). A region-specific MCP host baked into the notebook is a trap for any account outside us-east4, softened only by the note that the agent-key listing wins (7223-7226; `cr_agent_endpoint` 3992-4006). |
| Transport: SDK only for `ContextModel`/`ContextField`/`ContextRelationship`/`export_data_model` inside `cr_build_models`; all else via `http_call`; `mcp_url` from the list response else setting; check `result.isError`; show `error.code`/`message` verbatim; `unquote` userinfo | impl | 3413-3510; `cr_rpc` 4217-4236; `cr_call_tool` 4252-4285; `cr_connection_config` 3923-3938 | |
| Dataset: 3 entities, 20 records, Cairn, no `__acl_field_mappings__`, key templates `f"{CR_PREFIX}customer:{{customer_id}}"` asserted, always `entities=[...]` | impl | `cr_records` 3378-3411 (6/6/8); templates 3480-3496; assert 4109-4110; `export_data_model(..., entities=...)` 3505-3509; `tests/test_cr.py:153` | |
| Core 1 data model → tools (offline): pick field, index type, toggle `allowed_values`; export JSON and predicted tools table labelled "prediction, classic naming" update live; reset-to-default | impl, dev (§10) | 7328-7442; `cr_predict_tools` 3661-3684; `cr_unified_tools` 3571-3659 | §10: prediction switched to **unified** naming after the live run; label at 7415-7419. Undocumented addition: a "Keep this change" step and `cr_edits` state (7363-7387) so edits accumulate. |
| Core 2 create + load (consent): `FT._LIST` snapshot; create surface + agent key with expiry; actual tools diffed against prediction; import; EXPIRE keys by name; "Where did my data go" `JSON.GET` one doc + `FT.INFO` new index | impl | 7445-7574; `cr_launch` 4097-4163; `cr_tool_diff` 3686-3715; `cr_look` 4367 | Reuses an existing run surface instead of making a second one (`cr_adopt` 3952, "stale" flag). Password consent text at 7457-7459. |
| Core 3 paging and provenance: `limit`/`offset` sliders on one filter tool; `has_more`, `total_count`, `_retrieval` | impl, dev (§10) | 7577-7638; `cr_pageable` 4423-4441; `cr_match_most` 4395 | §10: sends a schema-derived condition because unified `filter_*` needs one. Adds a tool dropdown over every pageable tool. |
| Wow MCP tool explorer: dropdown → form from `inputSchema` (enum → dropdown, bool → checkbox, number → number, array/object → JSON area with example); Run shows table + real JSON-RPC from `HTTP_TRACE`; Break it (out-of-enum, else unknown arg on `additionalProperties:false`, else hide); never hardcode names; AUTORUN first `get_*` with `C001` | impl | 7641-7744; `cr_widget` 4479-4517; `cr_break_plan` 4587-4621; `cr_outcome_view` 4334 | 7648 picks the first `get_` tool; `cr_example` (4450) supplies `C001` from the schema description. |
| Deep dive: tool catalogue (name, description, args); access-tags experiment cut | impl | 7803-7829; `cr_catalogue_rows` 4623 | |
| Cleanup as §3.6 | impl | 7747-7800; `cr_cleanup` 4699-4744 | §10 live fact: the service drops its own indexes with the surface; counted as "gone" (4653-4660, 4757-4760). |

---

## 5. Column 3 · Agent Memory

| §5 item | Status | Where | Notes |
|---|---|---|---|
| Title: session events + long-term memories recalled by meaning; docs; mermaid events → background extraction (minutes) → memories (vector index in your Redis) → search | impl | 7832-7863 | |
| Config: endpoint, store ID, API key (secret); Test connection `GET /v1/stores/{id}/health`; show run scope (`ib-{RUN}`, `iris-basics-notebook`) and why every call is filtered; orphans panel | impl | 7866-8014; scope text 7879-7882; "Why every call is filtered" accordion 7900-7908; `am_health` 4994; `am_orphan_sweep` 5365-5402 | |
| Transport raw `http_call`; show 424 `type`/`detail`, never "0 results"; collapsed SDK text snippet | impl | `am_call` 4930-4946; 424 hint 5034-5037; `am_sdk_snippet` 5641, shown at 8226-8231 | |
| Core 1 write memories (consent): bulk-create 4-5 facts, ids `ib-{RUN}-m{n}`, topics, namespace, ledgered | impl | 8069-8134; `am_memories` 4789-4817 (5 facts about "Dana"); `am_write_memories` 5041-5058 | Ready only when the listing shows all five (8107). |
| Core 2 search + similarity lens: Sweep = 11 thresholds, limit 100, 10 s timeout, progress bar, abort on 429; band table "derived; assumes a hard cut"; slider filters the cached table; Re-query live; monotonicity check hides the band view | impl | 8137-8271; `am_sweep` 5064-5102; `am_sweep_problem` 5104; `am_band_rows` 5133; `am_recheck` 5153 | §10: slider starts at 0.3 (8189-8191). |
| Core 3 scope lens: same query under this owner / fake owner / wrong namespace → N / 0 / 0 | impl | 8274-8319; `am_scope_search` 5168-5189 | |
| Wow memory inspector: Send appends USER event to `ib-{RUN}-s{n}`, first event carries actorId + namespace; rank-based search (low/omitted threshold, `limit=3`); "#1 of 3 · 0 words in common"; session event list; New session (plain `.value`); Prompt assembly accordion with per-tier toggles; three zero-overlap presets; AUTORUN sends preset 1 | impl | 8322-8465; `am_take_turn` 5236-5258 (threshold 0.0); `am_recall_rows` 5260-5275; `am_context` 5307-5328; `am_wow_presets` 4836-4865; New session at 8386 | The plan's "very low or omitted" became 0.0 with a note that omission is undocumented (8343-8345). |
| Deep dive session memory: events + `summary` | impl | 8541-8605; `am_get_session` 5228 | |
| Deep dive auto-extraction: post a short conversation, then Check for extracted memories; kick-off never autoruns; notes say press it early | **dev (undoc)** | kick-off promoted out of the deep dive to a core-position cell "Start extraction now" 8017-8066, before Write memories; Check stays in the deep dive 8608-8651 | Consistent with the plan's intent (press it early) but not its layout. Adds `am_posted` state (8376-8377) so the session-memory cell re-reads after a post. |
| Deep dive under the hood: read-only `FT.INFO memory:{storeId}:ltm`, `HMGET`/`HSTRLEN` of our memory, own `connect_redis(redis_url_from_env())`, fail soft | impl, dev (undoc) | 8654-8709; `am_hood` 5547-5601 | Adds its own `SecretText("REDIS_URL")` field (8657) rather than env only. Fails soft on a missing index (5567-5568). |
| Cleanup + Re-sweep as §3.6 | impl | 8468-8528; `am_cleanup` 5421-5479 | Three buttons: Clean up, Clean up again, Reset. |

---

## 6. Is §10 "As built" complete? No. Eleven undocumented deviations

Ordered by how much a reader of §10 would be misled.

1. **No test asserts the version pins.** The plan header names `test_header_pins_the_stack`
   and says it "asserts all seven". It does not exist and no test in `tests/` contains any of
   the pinned version strings. §10 claims "1593 tests ... pass" and says nothing about this.
2. **LangCache floor probe moved from Test connection to the seed cell** (§5 col 1 config vs
   6628-6650). `lc_floor` is undefined until the run has written entries; the plan had the config
   cell set slider minimums. Correct engineering, unrecorded.
3. **Agent Memory extraction kick-off promoted from the deep dive to a core-position cell**
   (8017-8066, before Write memories), with a new `am_posted` state token (8376-8377).
4. **PUT removed from the HTTP verb set** (534). Plan §3.4 allowed PUT under a ledger/confirm
   rule; as built it can never be sent. README says so; §10 does not. Stricter, fine, unrecorded.
5. **`GuardedTracedRedis._confirm_index`** (1246-1259): after every `FT.CREATE` the guard runs
   `FT.INFO` and drops the index if `owned_index` fails. Not in §3.4. A second safety layer nobody
   asked for and nobody documented.
6. **Hard-coded Context Retriever endpoint defaults**, including the region-specific
   `gcp-us-east4` MCP host (7199, 7204). Plan §3.5 lists `CTX_API_URL`/`CTX_MCP_URL` as optional
   with no default named.
7. **Deep-dive switches default to `AUTORUN == "demo"`** (6389, 7047, 7806, 8535). The plan never
   says a test hook should set a presentation control's initial value.
8. **Undocumented CSS on marimo internals** (5717, 5726-5734): hides the outline column, removes
   the 610 px output cap, and injects placeholder text into stopped cells. The plan mentions only
   the `--content-width` override.
9. **"Keep this change" button and `cr_edits` state** in the data-model cell (7363-7387). The plan
   described live updates plus a reset; as built the current pick is live but only kept edits
   persist across field changes.
10. **Under-the-hood cell has its own `REDIS_URL` field** (8657) rather than `redis_url_from_env()`
    alone.
11. Small ones: `HGET` added to the reads allowlist (1001); `FT.DROPINDEX` also honours
    `ORPHANS["index"]` grants (1137); the lab has 14 probes, not "about 15"; `CR_KEY_TTL_S=7200`
    is used for CR keys where §3.6 gave no number; `lc_search` rewrites the last `HTTP_TRACE`
    entry to hide foreign prompts (2816-2818).

The five deviations §10 does list (helpers in setup, notes via CSS, five live facts, verify.py
flags, `RECORDED_LIVE` filled) are all real and correctly described. The §10 "still not
exercised live" list matches README. So §10 is honest about what it records; it under-records.

---

## 7. Keep for the MVP: the teaching moments that earn their lines

Each entry names the exact example, the exact command or request the audience sees, and why it
teaches. These are the parts of the current notebook I would fight to keep. Everything not listed
here is negotiable.

### Column 0 · Redis Search

1. **The `FT.CREATE` shown as redis-cli, next to one `HSET` with the vector elided as
   `<1024 bytes>`** (6066-6075; `rs_create_args` 2166-2172):
   `FT.CREATE irisnb_<run>_rs:idx ON HASH PREFIX 1 irisnb_<run>_rs:doc: SCHEMA title TEXT WEIGHT 2 body TEXT category TAG year NUMERIC vec VECTOR HNSW 6 TYPE FLOAT32 DIM 256 DISTANCE_METRIC COSINE`.
   One line teaches the whole Query Engine model: an index watches a prefix, four field types,
   and the vector is just another hash field. The `<N bytes>` elision is what makes the wire view
   readable and must survive.
2. **KNN vs `VECTOR_RANGE` on "will it fit my feet" / "best pizza near me"** (6143-6208):
   `*=>[KNN 3 @vec $BLOB AS dist]` always returns 3 rows; `@vec:[VECTOR_RANGE $R $BLOB]=>{$YIELD_DISTANCE_AS: dist}`
   returns 0 for the pizza question. The callout "KNN could never say this" is the bridge to
   LangCache's threshold and the single best conceptual hand-off in the notebook.
3. **The wow: "trail shoes feel cramped" through BM25, KNN and `FT.HYBRID ... COMBINE RRF`**
   (6279-6341; `rs_hybrid_args` 2214-2224; live: BM25 0 rows, vector #1, hybrid #1). The
   preset's four words appear in no article. The rank table with the starred target is the
   clearest possible argument for vector search, and `FT.HYBRID` shows the server-side fusion the
   customer will actually use on 8.4+. Keep the three presets (lexical trap, "Trailhead" exact
   name, "waterproofng" typo); each one shows a different engine winning. Drop the sort radio.
4. **The pre-filter query string** `(@category:{returns|shipping} @year:[2024 2026])=>[KNN 5 @vec $BLOB AS dist]`
   (6245-6276; `rs_filter` 2207-2212). Filtering *inside* the KNN is the thing developers get wrong
   with other vector stores. One example, filtered vs unfiltered, is enough.

### Column 1 · LangCache

5. **Anatomy of a hit** (6653-6727): the body
   `{"prompt": ..., "similarityThreshold": 0.85, "searchStrategies": ["semantic"], "maxResults": 5}`
   and a reply row with `similarity: 0.896` for the rain-jacket paraphrase. This is the only place
   the audience sees the raw LangCache API. Keep the request and reply JSON exactly as shown.
6. **The threshold trade-off, but with a shorter list** (6730-6790; `lc_probes` 2682-2704).
   The live numbers are the lesson: the worst false hit ("How do I wash my *synthetic* sleeping
   bag?" hitting the down-bag answer) scored 0.950, and at 0.96 only 1 of 6 paraphrases still
   hit. A newcomer needs to see one false hit and one lost paraphrase side by side with the slider
   that trades them. Six probes (2 paraphrases, 2 swapped-item, 1 negation, 1 unrelated) and a
   HIT/false-hit colouring carry it; the precision/recall/hit-rate stat tiles (3042-3092) do not.
7. **`searchStrategies` exact vs semantic** (6793-6853; `lc_variants` 2706-2716): "HOW DO I WASH
   MY DOWN SLEEPING BAG?" hits under `exact` and (live) missed under `semantic`; dropping the
   question mark misses `exact` and hits `semantic`. Two variants and the two strategies teach
   why the default is `["exact","semantic"]`. MVP-sized already.
8. **The 10-second `ttlMillis` entry, then a search MISS, then `DELETE` → `404 /errors/resource-not-found`**
   (7052-7100; `lc_ttl_look` 3332-3347). It is the cheapest demonstration in the notebook of why
   the MVP safety model can rely on TTL alone, and the 404 is a real live fact. Promote it from the
   deep dive to a core cell.
9. **Teach-then-paraphrase as the cache-aside loop without an LLM** (6863-6988): MISS on "How do I
   fix a snapped tent pole?", teach, HIT at 0.894 on "What should I do about a broken tent pole?",
   then the taught entry is deleted (6972-6988). If the MVP keeps a "wow" in this column, it is
   this one, minus the vocabulary gate (3215-3230), the shared-cache red callout and the AUTORUN
   branch; with `ttlMillis` at 120 s it is safe enough.

### Column 2 · Context Retriever

10. **The three `ContextModel` classes with `__redis_key_template__` and `field(...)` index
    kinds, and the `export_data_model` JSON they produce** (3413-3510; shown at 7403-7442). This
    is the product. The classes must stay visible in the notebook, not in `src/`, because the
    index declarations are where "governed" comes from.
11. **The create-surface `POST /api/v1/context-surfaces` body with `data_model` and
    `data_source.connection_config` (password redacted), then `tools/list` over JSON-RPC**
    (4118-4137; 7498-7524). Seeing the real 21 unified tools come back from the MCP endpoint
    teaches more than the 146-line prediction table does. Keep the call; drop the prediction.
12. **"Where did my data go": `JSON.GET irisnb_<run>_cr:customer:C001` and `FT.INFO idx:<surface>:<hash>:customer`
    read from the customer's own database** (7538-7574; `cr_look` 4367). The single strongest
    Context Retriever moment: the hosted service wrote RedisJSON into *your* Redis and built the
    index, and the agent never touches either. It must survive.
13. **One real `tools/call`** `{"name": "get_customer_by_id", "arguments": {"customer_id": "C001"}}`
    returning the record plus the `_retrieval` stamp, and **one rejected call** (an out-of-enum
    `tier`, or a `filter_customer` with no condition, which the live run showed answers `isError`
    "at least one condition is required") shown in red with the service's own message
    (7700-7744; `cr_call_tool` 4252-4285). Two fixed calls teach what the schema-driven form
    generator (4479-4517) and break planner (4587-4621) teach, in a tenth of the code.

### Column 3 · Agent Memory

14. **The bulk `POST /long-term-memory` body** with `id: ib-<run>-m1`, `ownerId`, `namespace`,
    `memoryType: semantic`, `topics` (8069-8134; `am_memories` 4789-4817). It shows that the
    caller chooses ids and scope, which is what makes cleanup possible.
15. **"What should the kitchen avoid serving me?" recalling the peanut-allergy memory #1 of 3 with
    0 words in common** (8322-8465; `am_recall_rows` 5260-5275; live confirmed). The stemmed-overlap
    counter is 25 lines (4880-4904) and turns "recalled by meaning" from a claim into evidence.
    Keep this preset, the `#1 of 3 · 0 words in common` cell format, and the session event list.
16. **The Prompt assembly block** (8434-8451; `am_context` 5307-5328): the literal
    `Retrieved memory context: / Session summary: / Short-term memory: / Long-term memory:` string
    an agent would receive. The three tier toggles are optional; the string is not.
17. **The scope lens: same query, three filters, N / 0 / 0** (8274-8319; `am_scope_search`
    5168-5189). Three read-only searches teach `filter.ownerId.eq` and `filter.namespace.eq`, and
    the footnote that namespace `eq` never matches a record with no namespace is a real live gotcha.
18. **The 424 hint** (5034-5037): "This is a failure, not 0 results." One sentence, keep it.
19. **Session memory read-back `GET /session-memory/<id>`** showing `ownerId` = first actor and
    `namespace` from the first event (8583-8605). Small, cheap, and it explains the seeding rule
    in the safety model.

Not worth keeping as built: the 11-threshold sweep and band table (5060-5164, 8137-8271). It is
ingenious, it recovers a score the API withholds, and it is 200 lines to teach that
`similarityThreshold` exists. Two searches, at 0.3 and 0.5, showing 1 memory then 0, teach the
same thing. Likewise "Under the hood" (8654-8709, 5547-5640): a good "it is only Redis" moment,
but it needs the service's backing `REDIS_URL`, which most customers will not have to hand.

---

## 8. Where the notebook is needlessly complex for a developer new to Redis Iris

Mechanisms, line ranges, and what the MVP replaces them with. "Needless" means: it teaches
nothing about Redis Iris and exists to make the notebook safe on a shared account or drivable by
`verify.py`. The orchestrator has already decided to drop most of it; this list is the evidence
for that decision and a checklist for what must not leak back in.

| Mechanism | Lines | Size | Why it is in the way | MVP replacement |
|---|---|---|---|---|
| `.env` name sniffing to distrust `IRIS_BASICS_RUN`/`AUTORUN` that marimo loaded from a file | 61-89 | ~30 | The notebook already depends on marimo loading `.env` (comment at 61-63), then works around it. | Gone with AUTORUN. |
| AUTORUN / HEADLESS / `pressed()` and every `pressed(` in the cells (29) plus AUTORUN branches inside the chat cells (6913-6916, 7092-7093) | 104-134 | ~30 + 29 call sites | A test hook visible in every consent cell; a first-time reader asks what `pressed` does before asking what `FT.SEARCH` does. | `btn.value`. |
| `SecretRegistry`, `_spellings`, `scrub`, `Secret`, `validate_secret`, `SecretText`, `resolve_secret`, `secret_hint` | 136-280, 373-419 | ~190 | Exists because secrets were typed into fields and echoed by the Variables panel. | No typed secret fields; `os.environ[...]` read inside the connect cell; nothing to scrub. |
| Hand-rolled env parser: `ENV_ALLOWLIST`, `parse_env`, `env_file`, `_parse_env_file` (lru-cached on mtime), `env_file_values`, `env`, `env_present`, `redis_url_from_env` | 281-372 | ~90 | Duplicates marimo's native `.env` loading and python-dotenv. | marimo loads `.env` next to `pyproject.toml`; `os.environ.get`. |
| Recursive redaction and runtime credential learning: `REDACT_KEYS`, `redact`, `_is_credential`, `_learn_secrets`, `_seal`, `_clip`, `_summary` | 441-531 | ~90 | Needed only because traces were stored and shown. | Show the request body you built, with the key omitted by hand. |
| HTTP choke points: `HTTP_RULES`, `LC_SCOPE`, 14 `_lc_*`/`_am_*`/`_cr_*` rule functions, `_ROUTES`, `_route_refusal`, `http_refusal`, `_dispatch` | 606-865 | ~260 | A second implementation of every API's contract, written to refuse the notebook's own code. | Put the run tag, TTL, owner and namespace in the one helper that builds each request. |
| `http_call`, `HttpCall`, `as_curl`, `_wire_text`, `wire_view` | 533-560, 866-956 | ~120 | The trace deque and curl rendering are display glue. | `niquests` call in the cell; `mo.json(body)` for the wire view. |
| Redis guard: `REDIS_READS`/`REDIS_WRITES`, `pace`, `_ft_create_prefixes` (35 lines parsing `FT.CREATE` for a smuggled `SCHEMA`), `_authorise`, `GuardedTracedRedis` with `_confirm_index`, pipeline/pubsub/monitor denial, module-client patching | 999-1275 | ~280 | Sits between the reader and `redis-py`. A newcomer cannot tell which behaviour is Redis and which is the wrapper. | Plain `redis.Redis.from_url(..., protocol=2, decode_responses=False)`; key prefix + `EXPIRE` by convention. |
| Grants and orphans: `OWNED_INDEXES`, `APPROVED`, `ORPHANS`, `ORPHAN_CONFIRM`, `age_hours`, `is_stale`, `claim_index`, `approve_*` ×6 | 1384-1520 | ~140 | A capability system for deleting *other* runs' leftovers. | Not an MVP concern; TTL covers keys and cache entries. |
| Disk ledger | 1522-1589 | ~70 | Survives kernel restarts for `verify.py`. | In-memory list of what this kernel wrote. Say in the README that a crashed kernel leaves Agent Memory and Context Retriever artefacts to the console. |
| `RECORDED_LIVE` panels | 1683-1909 | ~230 | Static JSON shown when unconfigured. | Dropped by decision. |
| Per-column orphan panels (3 cells each) and Reset tokens (`mo.state`, `_gen()` reads, `set_gen` bumps) | 5940-5988, 7283-7325, 7970-8014; 5906/5914/6380 etc. | ~150 in cells | Six cells and four state tokens that a customer never presses. | One Clean up button per column. |
| Presenter notes: `notes()` + 17 call sites + the CSS toggle cell | 1655-1664, 5814-5822, 17 cells | ~120 | Talk track for Nick, not teaching for the audience. | Fold the one-line "Expect" into the cell's markdown where it adds meaning. |
| `cr_unified_tools` / `cr_predict_tools` / `cr_tool_diff` | 3571-3715 | ~145 | Predicts what the server will say, then diffs it. Only needed when the column had to teach offline. | Call `tools/list` and show it. |
| Schema-driven form generator and break planner: `cr_match_most`, `cr_pageable`, `cr_kind`, `cr_example`, `cr_widget`, `cr_arguments`, `cr_enums`, `cr_ruled`, `cr_example_args`, `cr_break_plan` | 4395-4621 | ~230 | A generic MCP client UI. Impressive, not educational. | Two hard-coded calls: one `get_customer_by_id`, one rejected `filter_customer`. |
| Context Retriever surface adoption, settle, index learning, confirm-gone | 3952-3977, 4044-4095, 4677-4697 | ~100 | Idempotency and 404-proofing for re-runs. | Create once; Clean up deletes by the id you kept. |
| Agent Memory threshold sweep, bands, monotonicity, re-query | 5060-5163, 8137-8271 | ~200 | See §7. | Two searches. |
| LangCache lab scoreboard and stat tiles | 3028-3160 | ~130 | Precision/recall maths on 14 probes. | Six probes, HIT/false-hit colour. |
| `rs_hash_embed` fallback | 2094-2108 | 15 | A second embedder so the column runs without the model; it produces results that contradict the lesson (no paraphrase match) and the cell has to shout about it (6003-6011). | Fail with a clear message; the model is 30 MB. |
| `am_hood` / `am_hood_view` | 5547-5640, 8654-8709 | ~150 | Needs the service's backing DB URL. | Drop or one optional accordion. |

Two things in the cells themselves add friction without a helper to blame: every result cell
wraps its output in `mo.vstack([...])` with a `notes(...)` tail, so the actual output is buried
in list syntax; and every connect cell has a three-way `if _url is None / elif not pressed /
else` with a `try/except (ValueError, ServiceError, redis.RedisError)` around it. The MVP's
"not configured → stop" rule collapses each of those to two lines.

Risks to the orchestrator's decisions, flagged not relitigated:

- Dropping the ledger means a LangCache Clean up after a kernel restart cannot find its ids
  (the cache cannot be listed). That is fine only if `ttlMillis` is on every write and the README
  says the cache self-heals in 15 minutes.
- Dropping the choke points moves the safety burden into the helpers. The three invariants that
  matter are small and should be asserted, not just written: every LangCache set has `ttlMillis`;
  every Agent Memory search/create/event carries `ownerId` and `namespace`; every Context
  Retriever key template starts with the run prefix. A ten-line pytest on the `src/` package covers
  them.
- With `RECORDED_LIVE` gone, the Context Retriever data-model cell no longer needs to work offline,
  so the prediction table has no remaining purpose. Delete it with the panels, or it will look
  like the column's core.
- `mo.ui.text` debounces by default and sliders take `debounce=True`; switches, dropdowns and
  multiselects do not. Reactivity rule (a) holds in spirit; state it as "one call per control
  change" in the MVP rather than "debounced".

---

## 9. Verdict

`iris_basics.py` **implements the plan with deviations**, and the deviations are the least of
its problems. Every §5 item exists at the lines cited above, every §3.6 rule has a mechanism
enforcing it rather than a comment promising it, and the four §10 deviations plus five live facts
are accurately recorded; I found eleven more that §10 omits, of which only two matter (no test
asserts the version pins the plan header promises, and the Context Retriever column ships a
hard-coded `gcp-us-east4` MCP default). The real finding is that the notebook is a faithful build
of a plan that optimised for the wrong thing: 1,879 lines of shared harness and 3,769 lines of
helpers exist so that four columns could run unattended against shared Redis accounts and be
verified by a 1,980-line script, and the result buries roughly nineteen genuinely strong teaching
moments (§7) under machinery a developer new to Redis Iris must read past to find `FT.CREATE`.
The MVP rewrite should be judged against §7, not against this plan: keep the redis-cli and HTTP
wire shapes exactly as they render today, keep the "trail shoes feel cramped", rain-jacket,
"where did my data go" and peanut-allergy examples with their live numbers, and let TTL, a run
tag and one Clean up button per column carry the safety story.
