# Agent Memory inline move: probe review (2026-10-07, 14:35 to 14:45 CDT)

Lens: probe. Spec: `.agents/plans/agent-memory-inline-spec.md`. Brief: `.agents/plans/agent-memory-inline-review-brief.md`. Implementer's report: `.agents/reviews/am-inline.report.md`. Kernel: `http://127.0.0.1:2718/`. Scripts: `C:\Users\NickCooper\AppData\Local\Temp\am_inline_review_probe_{extract,compare,1..10}.py` (extract and compare are local stdlib scripts run with the venv `python.exe`; 1 through 10 ran in the kernel). Workbench dumps: `am_inline_review_workbench_*.html` in the same folder.

## Verdict: PASS WITH NOTES

The four cells are byte-identical to the spec's fences (`diff` exit 0 on each, not just normalized-equal), every live line is on disk with the wrapper indent, the graph is exactly what the spec asks for, and all three probes (threshold, N, owner) behaved as the brief specifies. Nothing in the move changes behavior or meaning.

Two things keep it from a plain PASS, neither caused by the move: a service `ReadTimeout` inside the sweep during my owner restore knocked `am_turn_sweep` into `exception` and `am_workbench` into `cancelled` until a second restore reran them (pre-existing fragility, finding 1); and Tom's long-term store holds two extracted memories the Seed never wrote, so "Tom returns nothing at 0.3" (brief check 7) is false for this store, exactly as the implementer's anomaly says (finding 2).

## Findings, most severe first

### 1. Medium: one slow search takes down the whole sweep and the workbench with it (`am_turn_sweep`; pre-existing)

**Observed.** My first `set_ui_value(ams_owner, ["Tom"])` (probe_8) returned exit 1 with this console output from cell Vxnm:

```
  File "C:\Users\NickCooper\AppData\Local\Temp\marimo_28120\__marimo__cell_Vxnm_.py", line 26, in <module>
    ams_turn_bands = await ams_sweep(ams_turn_query, ams_store)
  File "...\__marimo__cell_Vxnm_.py", line 12, in ams_sweep
    hits = await asyncio.gather(*(recall(owner, t) for owner, t in pairs))
  File "...\__marimo__cell_Vxnm_.py", line 9, in recall
    r = await ams_client.search_long_term_memory_async(request=ams_request(query, owner, t))
  ...
httpx.ReadTimeout
```

The next read showed `am_turn_sweep status=exception errors=[CellError(kind='runtime', msg='ReadTimeout: ')]`, `am_workbench status=cancelled`, `ams_turn_bands` absent from globals, and a 154-character workbench output. Owner was already Tom and `ams_session_id` was `tom-a803bae12a52`, so the dropdown move itself had landed; only the sweep had failed. A second identical `set_ui_value(ams_owner, ["Tom"])` reran the turn, chat and sweep (new session `tom-467d05ff321b`), and everything came back `idle` with `errors=[]`.

**Why it matters.** Every owner switch and every N move fires 42 searches with 12 in flight. One slow response from the service replaces the workbench with a traceback until something retriggers the sweep. In a live demo that is the moment Nick switches from Tom to Priya. The move did not cause this: the old `recall` awaited `ams_recall`, which made the same call with no retry. It did not fix it either, and the spec's claim that the turn "shows all three service calls itself" now puts this unguarded call in the visible code.

**Fix.** The SDK retries `httpx.TimeoutException` when `retry_connection_errors` is true (`redis_agent_memory/utils/retries.py` line 119), and `AgentMemory(...)` takes a `retry_config` that applies to every method (`sdk.py` line 34). One change in `am_connect`, which covers the turn's two calls and the store functions as well:

```python
from redis_agent_memory import AgentMemory, models
from redis_agent_memory.utils import BackoffStrategy, RetryConfig


ams_client = AgentMemory(
    os.environ["MEMORY_API_BASE_URL"],
    store_id=os.environ["MEMORY_STORE_ID"],
    api_key=os.environ["MEMORY_API_KEY"],
    retry_config=RetryConfig("backoff", BackoffStrategy(500, 4000, 1.5, 20000), True),
)
```

(`BackoffStrategy(initial_interval_ms, max_interval_ms, exponent, max_elapsed_time_ms)`.) Not applied; outside the five cells the spec names.

### 2. Low: Tom's store carries two extracted memories, so the Tom preview and the Tom search at 0.3 are not empty (store data, not a cell)

**Observed.** `await ams_memories("Tom")` returns 5, not 3: `['I wear size 11 trail shoes', 'User is interested in knowing which foods to avoid.', 'User asked how many cats they have.', "I've got two dogs, Max and Cece", "I'm planning a hiking trip in the Dolomites in August"]`. Bands for the starter query: `'User is interested in knowing which foods to avoid.': 0.65`, `'User asked how many cats they have.': 0.0`. At the default 0.3 the Tom system prompt block reads `You are a helpful AI assistant. What you remember about Tom: - User is interested in knowing which foods to avoid.`, and `search Tom at 0.3` returns `['User is interested in knowing which foods to avoid.']` where the brief's check 7 expects `[]`. `ams_store` still lists 3 for Tom because Refresh has not been pressed since the service extracted them.

**Why it matters.** The column opens on Tom with a fact in the system prompt that the Seed never wrote and the store table does not show, which muddles the "app decides what the model sees" point before anyone types. The code path is correct (the request body printed is `{'text': 'Which foods should I avoid?', 'similarityThreshold': 0.3, 'filter': {'ownerId': {'eq': 'Tom'}}, 'limit': 10}`); this is state left behind by the earlier chat turns, as the spec's caveat and the implementer's note both say.

**Fix.** Before the next demo run, press Clean up, then Seed (or at least Refresh, so `ams_store` lists what the store holds). Not pressed, per the brief.

### 3. Low: the report's "store counts: Tom 3, Priya 3" is the cached `ams_store`, not the store

**Observed.** The report's verification block prints `store counts: {'Tom': 3, 'Priya': 3}` from `g["ams_store"]`, and the spec's expected end state says "store Tom 3 / Priya 3". The live listing is Tom 5 / Priya 3 (finding 2). The report's "Noticed, not touched" bullet does say the two extra memories exist, so the report is internally consistent, but check 11 tells reviewers the live counts "must match the implementer's report", and the headline figure does not.

**Fix.** In the report and the spec's end state, label the figure: "`ams_store` (cached listing): Tom 3 / Priya 3; live `ams_memories`: Tom 5 / Priya 3".

### Notes, not findings

- A same-value `set_ui_value(ams_owner, ["Tom"])` reruns `am_turn_fn` and its descendants (new session id, new `ams_chatbot`). Useful to know: re-setting the dropdown is a legitimate way to rerun a failed sweep without `run_cell`.
- `am_settings` on disk initializes `get_turns, set_turns = mo.state(1)` while the live N is 3, so a fresh open starts at N=1 and the spec's "N 3" end state holds only for this kernel. The implementer flagged it; the spec said leave the cell alone.
- The two sweep comment lines were already above `ams_turn_live` in the backup (`iris_basics.before-inline.py` lines 587-588), and the backup had a blank line (592) before the sweep's `return` that the serializer dropped. Both of the implementer's side notes check out.

## Evidence by check

**1. Code equals spec.** Spec fences extracted by regex from the spec file (6 python fences; the first four are the bodies) to `am_inline_review_spec_<cell>.txt`; live `ctx.cells[name].code` written from the kernel to `am_inline_review_live_<cell>.txt`; both with `newline="\n"`. Result: `am_prompt_fns byte-equal=True normalized-equal=True spec_lines=24 live_lines=24`; `am_turn_fn ... True True 27 27`; `am_turn_sweep ... True True 26 26`; `am_build_md ... True True 5 5`. GNU `diff` exit 0 on all four. Live code lengths 918 / 1401 / 1421 / 205 chars, no trailing newline, ASCII only, matching the report's figures.

**2. Disk equals kernel.** For every non-blank live line, `"    " + line.rstrip()` is in `iris_basics.py` (rstripped): `am_prompt_fns 18/18`, `am_turn_fn 21/21`, `am_turn_sweep 20/20`, `am_build_md 4/4`, zero missing. Regex scan for `am_recall_fns|ams_recall|ams_remember` over the whole file: no hits. File: 636 lines, 27095 bytes, mtime 2026-10-07 14:29:40.12 CDT. `def am_prompt_fns(ai, it):` at line 425; heading at 340; `found.items` at 491; `search_long_term_memory_async` at 385, 488, 561.

**3. Cell health.** At my first read (14:37) and at the end (14:44), all fifteen `am_*` cells `idle` with `errors=[]`, including the seven the brief lists. Between 14:41 and 14:43, after my first owner restore, `am_turn_sweep` read `exception [CellError(kind='runtime', msg='ReadTimeout: ')]` and `am_workbench` read `cancelled` (finding 1); every other cell stayed `idle`.

**4. Graph.** `am_prompt_fns defs=['ams_build_system', 'ams_query', 'ams_request']`, refs `['ai', 'dict', 'float', 'int', 'it', 'list', 'reversed', 'str']`. `am_turn_fn refs=['AsyncGenerator', 'ai', 'ams_build_system', 'ams_client', 'ams_owner', 'ams_query', 'ams_request', 'ams_settings', 'dt', 'list', 'models', 'secrets', 'str', 'stream_llm']`: all ten required names present, none of `ams_recall`, `ams_remember`, `get_threshold`, `get_turns`, `ams_threshold`, `ams_turns`, `ams_chatbot`. `am_turn_sweep refs` include `ams_client` and `ams_request`, not `ams_recall`. `ams_recall def=[] refs=[]`, `ams_remember def=[] refs=[]`, `am_recall_fns def=[] refs=[]`; a substring scan of every cell's code for the three names found nothing; `"ams_recall" in globals: False`, `"ams_remember" in globals: False`. `ams_request def=['am_prompt_fns'] refs=['am_workbench', 'am_turn_fn', 'am_turn_sweep']`. `cycles: set()`.

**5. End state.** `ORDER: ['am_title', 'am_store_button', 'am_store', 'am_owner', 'am_workbench', 'am_build_md', 'am_connect', 'am_store_fns', 'am_prompt_fns', 'am_turn_fn', 'am_machinery_md', 'am_settings', 'am_chat', 'am_turn_sweep', 'am_render']`, COUNT 15, `am_recall_fns` absent, `am_title` carries `column=4`. `hide_code=False` on exactly `am_connect` (SFPL), `am_store_fns` (emfo), `am_prompt_fns` (ulZA), `am_turn_fn` (RJQd); `True` on the other eleven. Owner Tom, threshold 0.3, N 3, chat empty, `ams_store` Tom 3 / Priya 3 at start and finish.

**6. The implementer's report.** Every value I could re-read matches: ORDER, ids, statuses, defs/refs, cycles, chat len 0, `(0.3, 3)`, owner Tom, session `tom-a2c65cf13b70` at start, bands Tom 5 / Priya 3, Priya search result, Tom search result, bind names, `isasyncgenfunction True`, heading text, workbench needles, `ams-row` count 12 (8 memory rows + 2 tick rows + 2 CSS rules), the four disk grep lines, the visibility set, the pre-edit transcript against `am_inline_prestate.txt`, and both backup side notes (lines 587-588 and 592 of the backup). Deviations: none in the move, correctly reported. Discrepancy: the "store counts" label (finding 3). Missed: nothing material; the sweep's timeout fragility (finding 1) is not something the implementer's steps would have surfaced.

**7. Live search path.** Requests built by the turn's own functions: Priya `{'text': 'Which foods should I avoid?', 'similarityThreshold': 0.3, 'filter': {'ownerId': {'eq': 'Priya'}}, 'limit': 10}`, Tom the same with `'Tom'`. `search Priya at 0.3: ["I'm allergic to peanuts and tree nuts"]` as expected. `search Tom at 0.3: ['User is interested in knowing which foods to avoid.']`, where the brief expects nothing (finding 2; same result the implementer reported). `inspect.signature(ams_client.add_session_event_async)` is keyword-only with `actor_id`, `role`, `content`, `created_at` required and `session_id` optional; `bind` with the turn's five keyword arguments succeeded: `['actor_id', 'content', 'created_at', 'role', 'session_id']`. `inspect.isasyncgenfunction(ams_turn): True`.

**8. Threshold probe.** Record (probe_2, 14:37:58): session `tom-a2c65cf13b70`, chat len 0, `id(ams_chatbot) 2682471911248`, `id(ams_turn) 2682571915648`, `ams_settings() (0.3, 3)`, line `left:30%`. Set (probe_3): `updated 1 UI element(s)`. Read (14:38:30): `ams_settings() (0.45, 3)`, slider 0.45, session and both ids unchanged, chat len 0, `ams-line styles: ['left:45%']`, JSON request `similarityThreshold: 0.45`. Bars at 0.45: `User is interested in knowing which foods to avoid. | ams-bar | width:65%`; the six seeded memories and `User asked how many cats they have.` all `ams-bar ams-bar-below` (widths 5%, 5%, 5%, 0%, 35%, 5%, 5%). System prompt block: `You are a helpful AI assistant. What you remember about Tom: - User is interested in knowing which foods to avoid.`, the one extracted memory the brief allows for; no seeded fact. Restore (probe_4): `updated 1 UI element(s)`. Confirm (14:39:07): `(0.3, 3)`, `left:30%`, JSON 0.3, ids and session unchanged.

**9. N probe.** Record: the 14:39:07 read above. Set (probe_5): `updated 1 UI element(s)`. Read (14:39:48): `ams_settings() (0.3, 1)`, `get_turns() 1`, session and both ids unchanged, `am_turn_sweep idle errors=[]`, bands Tom 5 / Priya 3 (chat empty, so the query is still the starter prompt). Restore (probe_6): `updated 1 UI element(s)`. Confirm (14:40:24): `(0.3, 3)`, ids unchanged, all cells idle.

**10. Owner probe.** Set (probe_7, `["Priya"]`): `updated 1 UI element(s)`. Read (14:40:52): owner Priya, `session_id priya-4b5070dcfbc5`, `id(ams_chatbot) 2682491564112` (was 2682471911248), `id(ams_turn) 2682571924128` (was 2682571915648), `has 'ownerId Priya': True`, `has 'Same search, ownerId Tom': True`, `has 'Same search, ownerId Priya': False`, sliders new objects with values preserved at 0.3 / 3, system prompt `What you remember about Priya: - I'm allergic to peanuts and tree nuts`, all cells idle. Restore (probe_8, `["Tom"]`): `updated 1 UI element(s)` plus the `httpx.ReadTimeout` traceback from Vxnm; read: owner Tom, `session tom-a803bae12a52`, `am_turn_sweep exception`, `am_workbench cancelled`, `ams_turn_bands` not in globals. Second restore (probe_8 again): no traceback. Confirm (14:43:18): owner Tom, `session_id tom-467d05ff321b`, `has 'ownerId Tom': True`, `has 'Same search, ownerId Priya': True`, `left:30%`, `(0.3, 3)`, all fifteen cells idle with `errors=[]`.

**11. Store counts.** Before the first probe: `ams_memories("Tom")` 5, `ams_memories("Priya")` 3. After the last: 5 and 3, same texts in the same order. `ams_store` (cached) 3 / 3 both times. Nothing was written: no chat message, no Seed/Refresh/Clean up, no `add_session_event_async` call (only `inspect.signature(...).bind`).

## Final state (14:44 CDT)

- Owner: Tom
- Threshold: 0.3 (`ams_threshold.value 0.3`, `get_threshold() 0.3`)
- N: 3 (`ams_turns.value 3`, `get_turns() 3`)
- `ams_settings()`: `(0.3, 3)`
- Chat length: 0
- `ams_session_id`: `tom-467d05ff321b` (new by design: the owner probe and the second restore each started a session; it was `tom-a2c65cf13b70` when I began)
- Store: live `ams_memories` Tom 5 / Priya 3; cached `ams_store` Tom 3 / Priya 3; both unchanged from before my first probe
- Bands: Tom 5 / Priya 3; turn query `'Which foods should I avoid?'`, live False
- All fifteen `am_*` cells `idle`, `errors=[]`

Every control I moved (threshold 0.3 to 0.45 to 0.3; N 3 to 1 to 3; owner Tom to Priya to Tom) is back where it started and confirmed in a separate kernel call.
