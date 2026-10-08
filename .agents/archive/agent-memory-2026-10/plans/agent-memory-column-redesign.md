# Agent Memory column: a readable rebuild (proposal v2, 2026-10-07)

Scope: column 4 of `iris_basics.py` as it runs on http://127.0.0.1:2718/ today (21-cell working copy, 15 cells in the column, 498 lines on disk, marimo 0.24.2, redis-agent-memory 0.4.1). Nothing in the notebook or the store has been changed. Every number below was measured in the live kernel through the scratchpad, and three independent reviews (`.agents/reviews/am-redesign-critique-*.md`) checked v1; the review log at the end says what they changed.

**Rulings from Nick, 2026-10-07, after reading v2.** Keep the "last N user messages" slider: one or several turns as the long-term memory search context is part of the lesson, so the prompt panel now shows the search text and the bars for it, which gives the slider the readout v2 said it lacked. Keep every function that explains the Iris interface visible and hide only rendering code. The notebook's purpose is to teach how to integrate Redis Iris into an agent workflow by taking each component apart and showing it is Redis underneath. Extraction stays in the title and Refresh stays until the five-minute test in the build decides it. The build follows `agent-memory-build-spec.md` (16 cells), not the 14-cell table below.

## Verdict

The column teaches the right three things and the code is correct. What makes it hard to read is that the explanation lives in machinery the reader cannot see: three `mo.state` pairs, two `on_change` handlers, a turn log and a sweep cache mutated from inside the chat closure, and a trace cell that guesses whether that log is stale by comparing strings. The goals in the title point at nothing on the screen. The heaviest UI block, a table keyed by threshold, asks the reader to invert it in their head to get the per-memory lesson. And the only control that teaches the threshold sits under a chat that needs an LLM key before anything renders.

I would reorder the column around the reader's path: store, then a search probe that draws one bar per memory for both customers and needs no model, then the chat with the panel that shows what the model received. Every panel is a pure function of visible controls. No `mo.state`, no `on_change`, no log, no cache dict, no staleness check, no `mo.cache`. Same cell count (15 becomes 14), 30 public names with four naming schemes become about 20 with one prefix, and goals 1 and 2 render on load.

I would not add anywidget. Reasons at the end.

## What is on screen today, and what hurts

| Cell | Role | What hurts |
|---|---|---|
| `am_title` | goals | The three goals name no panel or control. The reader meets one chat, two sliders, and a table and has to work out which demonstrates what. The six-line wire table is the densest block in the column and sits above the fold. |
| `am_connect` | client | Fine. 8 lines, one health line. |
| `am_try_it`, `am_read_code` | prose | Two cells for two sentences each. The second exists because the code is parked at the bottom, away from the UI it drives. |
| `am_controls` | admin row | The owner dropdown (a lesson control) shares a row with Seed, Refresh, and Clean up (store admin). |
| `am_seed`, `am_cleanup`, `am_memories` | store | Three cells and a `mo.state` version counter exist only to make the table re-list after a press. `run_button.value` is True for the run a click triggers and resets to False afterwards (`run_button.py:19-20`), so one cell can act on the pressed button and then list. |
| `am_state` | plumbing | `mo.state` for threshold and turns, passed into the chat as getter callables. marimo's best-practices page says "Don't use state and `on_change` handlers", and `tests/test_safety.py:33` bans `mo.state`. It is here to dodge the chat-rerun trap, which does not need it (see "Why the chat can own the slider"). |
| `am_chat` | input | Sliders beside the chat is right. `ams_turn_log` and `ams_sweep_cache` are module-level mutables written by the closure and read by another cell; marimo does not track mutations, so `am_trace` re-derives staleness from `chatbot.value`. |
| `am_trace` | evidence | 65 lines in the kernel. Shows the recall list, then a table with one row per threshold and the memories joined into one string. The lesson is per memory (how close did each one get), so the reader inverts the table. The cache key includes the slider value, so a slider move adds a row rather than moving a line. Nothing here reruns after Seed or Clean up. |
| `am_seed_fn`, `am_steps`, `am_chat_fn`, `am_amnesia` | code | Good functions with "Principle N" docstrings, all below "Read the code". Names: `ams_*` functions, but `amnesia`, `ChatFn`, `chatbot`, `turns`, `similarity_threshold`, and six `get_*`/`set_*` break the scheme. |

## Target layout: 14 cells, probe first

Reading order top to bottom. "code" means `hide_code=False`.

| # | Cell | Shows | Goal | What the reader sees |
|---|---|---|---|---|
| 1 | `am_title` | prose | all | Problem, mechanism, three goals that each say what on screen proves them. No wire table. |
| 2 | `am_client` | code | setup | Connect and one health line. Unchanged from `am_connect`. |
| 3 | `am_store_md` | prose | 2 | "## What long-term memory holds". One sentence on Seed, one on Clean up. |
| 4 | `am_store_button` | hidden | 2 | Seed and Clean up in one row (Refresh only if extraction is confirmed, see open question 1). |
| 5 | `am_store` | hidden | 2 | Acts on whichever button was pressed, then lists Tom's and Priya's memories side by side, each table headed with its `ownerId`. Defines `ams_store`. |
| 6 | `am_store_fns` | code | 2 | `ams_seeds`, `ams_memories`, `ams_seed`, `ams_forget` (was `amnesia`; now also deletes the owner's sessions). |
| 7 | `am_probe` | hidden | 1, 2 | "## Search long-term memory". One text box, default "Which foods should I avoid?", `debounce=True` so it fires on Enter or blur. |
| 8 | `am_sweep` | hidden | 1, 2 | Runs the sweep for the probe text and every owner. Depends on the text and `ams_store` only, not on the slider. Defines `ams_bands`. |
| 9 | `am_probe_panel` | hidden | 1, 2 | "### 1 and 2. Which memories clear the threshold". Bars for Tom and for Priya side by side, same text, the chat's slider value drawn as a line, the request body under each, and the four-sentence footnote on what a bar is. |
| 10 | `am_recall_fns` | code | 1, 2 | `ams_recall`, `ams_sweep`. |
| 11 | `am_owner` | hidden | 3 | "## Chat as Tom or Priya", one sentence, the `ams_owner` dropdown. Upstream of the chat on purpose: a switch reruns the chat, starts a new session, and clears the history. |
| 12 | `am_chat` | code | 3 | The `similarityThreshold` slider above the chat box, the session id, the turn function `ams_turn`, and `ams_chatbot`. The turn function is the goal-3 code, so this cell shows it. |
| 13 | `am_prompt_panel` | hidden | 3 | "### 3. What the model received". After a completed turn: the session's events read back from the service, the memories recalled for the last message at the slider's value, the system prompt as built, and the messages list as sent. |
| 14 | `am_prompt_fns` | code | 3 | `ams_remember`, `ams_build_system`. |

Cell 13 renders a one-line placeholder until the chat has a completed turn. Cells 5, 9, and 13 use `if`/`else`, not `mo.stop`, so they never remove their defs from downstream cells. Rendering helpers (`_bars`, `_memory_table`) are private `_` functions inside the hidden cells that use them, not lesson code.

Removed cells: `am_try_it`, `am_read_code`, `am_controls`, `am_seed`, `am_cleanup`, `am_memories`, `am_state`, `am_trace`, `am_steps`, `am_chat_fn`, `am_amnesia` (contents move; nothing is lost). Removed names: `get_*`/`set_*` (6), `ams_turn_log`, `ams_sweep_cache`, `ChatFn`, `chatbot`, `turns`, `similarity_threshold`, `amnesia`, `ams_query`, `ams_chat`, `AMS_SEEDS`.

## The four changes that matter

### 1. Probe first: goals 1 and 2 without a model call

Goals 1 and 2 are search-API lessons. The model adds nothing to them and can subtract from them: an answer from the model's own knowledge, or from chat history, looks like the owner filter failing. Only goal 3 needs a model. Under Nick's second criterion, the sweep is the one measured series in the column and the chat is a single-shot command with a read-back; v1 put the read-back in front of the series and left goals 1 and 2 as placeholders until the reader had an `LLM_API_KEY`.

So the probe is a text box, and the bars draw on load for both customers at once. "Which foods should I avoid?" gives Priya one bar at 0.35 and Tom three stubs at 0.05 before anyone has typed anything. The chat then carries goal 3 alone.

### 2. One slider, pure panels

The chat cell defines the threshold slider; the probe panel and the prompt panel reference it. marimo reruns the cells that reference a UI element by name and never the one that defines it (`_runtime/runtime.py:1942` and `:2082-2086`, "never rerun the cell that created the name"). So a slider move redraws both panels and leaves the chat history alone, which is what `tests/test_notebook.py::test_chat_cells_own_their_controls` encodes. `mo.state` was never needed for this.

Every panel is a function of visible things: the probe text, `ams_store`, `ams_owner.value`, `ams_threshold.value`, and `ams_chatbot.value`. Nothing is logged from inside the chat closure. The prompt panel does not show "what the agent recalled"; it shows "what the agent recalls for the last message at the current slider value", which is the same set unless the slider moved after sending, and then it is the set the reader asked about. The label says so.

### 3. Per-memory bars from a 0 to 1 sweep, upstream of the slider

The search API returns no score. `MemoryRecord` has id, text, created_at, updated_at, memory_type, session_id, owner_id, namespace, namespace_ref, topics, attributes; the request has text, similarity_threshold, filter_, filter_op, limit, page_token. The raw endpoint ignores `includeScores` and its variants (checked over httpx), and no SDK method returns one. So the only way to show how close each memory came is to search at several thresholds and record the highest one each memory still clears.

Measured today with `asyncio.gather`, live store, three seed memories per customer:

| Search text | Owner | Highest threshold cleared |
|---|---|---|
| Which foods should I avoid? | Priya | allergy 0.35; cat Miso 0.05; Maine canoe 0.05 |
| Which foods should I avoid? | Tom | all three 0.05 |
| What pets do I have? | Tom | two dogs 0.40; trail shoes 0.15; Dolomites 0.05 |
| What pets do I have? | Priya | cat Miso 0.40; allergy 0.20; Maine canoe 0.05 |
| Where am I headed this summer? | Tom | Dolomites 0.40; shoes 0.10; dogs 0.10 |
| do i have any dogs | Tom | two dogs 0.50; shoes 0.15; Dolomites 0.10 |

The bands reproduced exactly on a second run. One search is about 300 ms; 13 concurrent searches took 0.4 to 1.1 s; 42 concurrent searches (21 thresholds, both owners) took 1.8 s. Threshold 0.0 and 0.05 return the same memories; 0.95 and 1.0 return none and raise nothing.

The sweep runs 0.00 to 1.00 in steps of 0.05, and the slider runs 0 to 1. v1 capped both at 0.6 because nothing in this store clears 0.55; that hard-codes six seed sentences into the instrument, breaks on a customer's own seeds, and hides the lesson a LangCache user needs most, which is that 0.8 by habit returns nothing here. The empty right half of the chart is the point.

The sweep depends on (probe text, store contents) and not on the slider, so it lives in its own cell and the panel redraws on a slider move without a network call. The slider has `step=0.05`, aligned to the grid, so the recall set at any slider position is exactly the memories whose band is at or above it. Compare with `round(..., 2)` on both sides; slider values can arrive as 0.15000000000000002.

```python
async def ams_sweep(query: str, owners: list[str]) -> dict[str, dict[str, float]]:
    """Principles 1 and 2: the highest threshold each memory still clears, per owner. The API returns no score."""
    grid = [round(0.05 * i, 2) for i in range(21)]  # 0.00 to 1.00
    hits = await asyncio.gather(*(ams_recall(query, owner, t) for owner in owners for t in grid))
    bands: dict[str, dict[str, float]] = {owner: {} for owner in owners}
    for (owner, t), found in zip(((o, t) for o in owners for t in grid), hits):
        for m in found:
            bands[owner][m.text] = max(bands[owner].get(m.text, 0.0), t)
    return bands
```

No `mo.cache`. v1 memoized the sweep on (query, owner). Two defects: the key is the defining cells' code, not the store, so bands go stale after Seed, Clean up, or extraction; and with `ams_sweep` in the same cell as `ams_recall`, marimo's hasher falls back to pickling `ams_client` and raises `TypeError: cannot pickle '_thread.RLock'` on the first call (`_save/hash.py:473-527`, emulated against the live graph; it works only from a separate downstream cell, 0.82 s then 0.001 s). The sweep cell reruns only when the text or the store changes, which is the same effect with no cache.

The bars, exact wording:

- Panel heading: `### 1 and 2. Which memories clear the threshold`
- One row per memory in `ams_store[owner]`, so a memory that never matched shows an empty track. Sorted by band descending, then store order; tied bars get the same length and no rank cue.
- Axis label: `Highest similarityThreshold the memory still clears`. Bar end label: the band, two decimals. Never the word "score".
- Threshold line label: `slider: 0.30`.
- Footnote, four sentences: "The search API returns no similarity score. The notebook ran the same search at 21 thresholds, 0.00 to 1.00 in steps of 0.05. Each bar ends at the highest one that still returned the memory, so its similarity lies between that value and the next step up. Memories at or above the line come back at the slider's setting."
- The `ams_recall` request body, as sent for this owner, under each set of bars, so `text`, `similarityThreshold`, `filter`, and `limit` are read together. The `filter` line is goal 2.

The bars are a private 15-line `mo.Html` helper in the panel cell: a filled bar scaled to 1.0, a vertical line at the slider value, filled rows at or above the line and muted rows below. No chart library (altair is not installed). One accent colour plus grey, per the dataviz skill's palette rule, in inline CSS.

### 4. Code under the panel it drives, and the turn function in the open

The functions already carry "Principle N" docstrings. Each goes under the panel that exercises it, and only functions that call `ams_client` or build the prompt are shown; HTML helpers are private inside hidden cells. The chat cell shows its code because the turn function is the goal-3 lesson:

```python
ams_threshold = mo.ui.slider(
    0.0, 1.0, step=0.05, value=0.3, show_value=True, debounce=True,
    label="similarityThreshold, for the bars above and the chat below",
)
ams_session_id = secrets.token_hex(8)  # am_owner is upstream, so a customer switch reruns this cell: new session, empty history


async def ams_turn(messages: list[ai.ChatMessage], config: ai.ChatModelConfig) -> AsyncGenerator[str, None]:
    """One agent turn. Principle 3: the app, not the service, decides what the model sees."""
    owner, latest = ams_owner.value, str(messages[-1].content)
    await ams_remember(ams_session_id, owner, latest)                                  # session memory
    recalled = await ams_recall(latest, owner, round(ams_threshold.value, 2))          # long-term memory, this owner only
    system = ams_build_system(owner, [m.text for m in recalled])
    history = [{"role": m.role, "content": str(m.content)} for m in messages if m.role in ("user", "assistant")]
    async for text in stream_llm(system, history, config):
        yield text


ams_chatbot = mo.ui.chat(ams_turn, prompts=["What pets do I have?", "Where am I headed this summer?", "Which foods should I avoid?"])
mo.vstack([ams_threshold, ams_chatbot])
```

No callables passed in, no `ChatFn`, no log parameter, no "last N user messages" slider. That slider was a swept parameter with no readout: moving it changed the search text, which moved every bar for a reason only visible in the request body, and with three starter prompts on unrelated topics N above 1 blends pets, travel, and food into one query and lowers every band. If the query-construction lesson matters, it needs its own measured effect (bands at N=1 against N=3 for the same turn), not a knob. The search text is the latest message, and the prompt panel shows it.

`ams_build_system` takes texts instead of records so the prompt panel can rebuild the exact prompt. The code says `owner` where the prose says customer; `user_id` goes away (one term per concept).

## The store section

One cell replaces three plus the version counter:

```python
_note = ""
if ams_seed_button.value:
    _note = f"Wrote {len(await ams_seed())} new memories to long-term memory."
if ams_cleanup_button.value:
    _deleted = 0
    for _owner in ams_seeds:
        _deleted += len(await ams_forget(_owner))
    _note = f"Deleted {_deleted} memories and every session for {' and '.join(ams_seeds)}."

ams_store = {}
for _owner in ams_seeds:
    ams_store[_owner] = await ams_memories(_owner)

mo.vstack([
    mo.md(_note) if _note else mo.md(""),
    mo.hstack([_memory_table(owner, found) for owner, found in ams_store.items()], widths="equal"),
])
```

Two side-by-side tables headed `ownerId: Tom` and `ownerId: Priya` put goal 2's boundary on screen before any search runs. `ams_store` is a real dependency for the sweep (it needs the full memory list to draw empty bars), and it makes the probe panel rerun after Seed or Clean up, which the current column does not do.

`ams_forget` should delete the owner's sessions as well as memories. `list_sessions_async(filter_owner_id=owner)` exists (checked; it takes keyword arguments, not a request dict), then `delete_session_memory_async` per session. Today `amnesia` deletes memories only, so chat sessions linger until the store's default one-hour expiry, and if extraction is enabled they can re-create memories after Clean up. Never send `include_all` (`tests/test_safety.py::test_no_include_all`).

## Title rewrite (draft v2)

Keeps the `N. **Title.** sentence` form the tests parse. Each goal says what on screen proves it, in a different shape each time.

```markdown
# Redis Agent Memory

An agent that forgets a customer's two dogs between conversations asks about them again every time.
Redis Agent Memory keeps each conversation in session memory and durable facts about the customer in long-term memory.
Seed memories for two made-up customers, Tom and Priya, then search them and chat as either to see three principles:

1. **Long-term memory recalls by meaning.** "Which foods should I avoid?" shares no word with "allergic to peanuts and tree nuts", and the search returns it for Priya. The bars under the search box show the highest `similarityThreshold` each memory clears, so raising the slider drops the weakest matches first.
2. **The `ownerId` filter scopes recall to one customer.** The app sends `filter: {ownerId: ...}` with every search, so Tom's agent does not see Priya's allergy. The same search runs as Tom and as Priya, side by side.
3. **The app builds the prompt.** Each turn writes the message to session memory and searches long-term memory with it. The hits go into the system prompt, so the model sees a memory only because the app put it there. The panel under the chat shows the events the service stored and the prompt the model received.

Agent Memory is in preview on Redis Cloud. Settings: `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY`, plus `LLM_API_KEY`, which the chat needs and which must match the provider of `AI_MODEL`.
Docs: [overview](...), [Python SDK quickstart](...), and [developer guide](...).
```

Voice check against VOICE.md: every sentence is under 25 words with inline code stripped, serial commas, no semicolons or exclamation marks, "you" or imperative, Redis prefix at the H1 and bare after, no idiom ("take apart" from v1 is gone), no staged reveal ("and still finds it" is gone), no "score" (the panel footnote says there is none), sentence shapes vary across the three goals. The v1 clause "or extracted from session events" is cut until extraction is shown on screen (open question 1). "Agent Memory is in preview" matches the docs as read on 2026-10-06; check on publish day. One deviation to name: VOICE.md's notebook-cell rule wants a colon-terminated lead-in before each cell; the panel headings do that job and no panel gets an extra lead-in sentence.

The wire table goes rather than collapses: every panel shows its own request body, so a `/// details` block would repeat them. The other columns keep theirs because their panels do not show requests.

## anywidget: when I would and would not use it

Not for this column, and not yet.

- anywidget and traitlets are not installed in the kernel's venv (checked). Adding them is a dependency for a demo meant to need near-zero setup.
- The bars are static HTML redrawn by the kernel on a slider move with no network call, and the slider is debounced.
- A widget would not remove the chat-rerun trap. `mo.ui.anywidget` reruns referencing cells on a synced-trait change exactly as a slider does.
- The only thing a widget buys is dragging the threshold line and watching the recall set change client-side, with no round trip, because the band data is already in the browser. That is a nicer feel, not a clearer lesson.

When it would be worth it: LangCache's column has the same lesson shape (a similarity threshold that trades wrong answers for misses), and Redis Search's column has a KNN score. If all three columns want one threshold meter with a draggable line, one anywidget class shared across them is the right home, and the private `_bars` helper is what it replaces. Build the helper first, promote it if two columns want it.

## Traps the design respects

- **Chat history.** The chat cell defines the only control the turn function reads at call time (the slider). The owner is upstream on purpose: switching customers must wipe the history, or the model answers Priya from Tom's turns and goal 2 looks broken on screen.
- **`mo.stop` removes defs.** A stopped cell's descendants do not run and lose their defs (`_runtime/control_flow.py:32-34`). The panels use `if`/`else` placeholders, never `mo.stop`, so `ams_store` and `ams_bands` always exist.
- **Shared store.** Every search carries `filter: {ownerId: ...}` (`tests/test_safety.py:223-241`). I deliberately did not add an unfiltered variant to the probe, even though it would make goal 2 loudest: the store may hold other people's memories and an unscoped search would show them.
- **Mutation.** `bands` in `ams_sweep` and `_note`, `ams_store` in `am_store` are built and mutated in the same cell or function. No cell mutates another cell's object.
- **Session read-back.** `get_session_memory_async(session_id=...)` returns session_id, owner_id, events (role, content[0].text, created_at), namespace, namespace_ref, summary; verified on today's live session. For a session id with no events it raises `NotFoundErrorResponseContent` (404), it does not return empty. The prompt panel reads the session only when the last chat message is an assistant reply, so the user event was written before the read, and it never reads a session that does not exist.
- **Value pushes.** `ams_chatbot.value` is pushed twice per completed turn and never mid-stream: once by Python at stream end (`_plugins/ui/_impl/chat/chat.py:387-390`, `:445-468`) and once by the frontend's onFinish; chunks travel as `stream_chunk` messages, not value updates. So the prompt panel reruns about twice per turn, each run one search and one session read. Confirm the count during the build.
- **Both branches assign.** marimo deletes a cell's defs before rerunning it (`_runtime/runtime.py:1516-1518`), and a name assigned in one branch counts as a def. Every public name a hidden cell defines (`ams_store`, `ams_bands`) is assigned in the placeholder branch too.
- **Float grid.** Slider values and grid values are both rounded to two decimals before comparison.

## What it breaks, corrected

- `tests/test_notebook.py::test_column_budgets` caps column 4 at 9 cells and 268 lines. Today the suite fails earlier (no `app.setup` block, `test_notebook.py:89`), and only `am_title` carries `column=4` on disk, so the cap has never been measured against this column. 14 cells and about 420 lines would fail it. Nick said the tests get rewritten once the columns settle.
- `test_chat_cells_own_their_controls` matches the model function with `ast.FunctionDef` (`test_notebook.py:148`); `async def ams_turn` is an `ast.AsyncFunctionDef`, so the test needs that case added. It also requires `rs_chat` and `lc_chat` to exist. And it forbids a foreign control in the chat cell, which the owner dropdown must be; the rule needs one exception, a control named `*_owner`.
- `test_uppercase_names_live_in_column_0` forbids `AMS_SEEDS` in column 4, hence `ams_seeds`.
- `test_columns_start_with_a_title_and_hide_title_and_button_code` wants the run_button cell named `*_button`, hence `am_store_button`.
- `test_safety.py::test_import_ownership` wants `datetime`, `secrets`, and `redis_agent_memory` imported in setup. The working copy imports them in `am_connect`; I would leave that where Nick put it.
- `test_mo_stop_gates_on_a_button_or_a_form_only` fails today on `am_trace`; the `if`/`else` placeholders fix it. `mo.cache` is not in `FORBIDDEN_CALLS`, but the design no longer uses it.
- The README still describes the v2 column (Dana, five memories, a last-turn panel). Out of scope here, same rewrite needed.

## Open questions for Nick

1. **Is extraction enabled on this store?** The title, the wire table, and the Refresh button all claim it, and nothing on screen shows it. Today's store has six `semantic` memories with no `session_id`, and Tom's session from this morning holds four questions, so there was nothing to extract and the check is inconclusive. Test during the build: send one factual message as Tom ("I just adopted a parrot named Rio"), wait five minutes, list Tom's memories. If a memory with a `session_id` appears, keep Refresh, add that sentence as a fourth starter prompt, restore "or extracted from session events" to the title, and say under the tables "Tell the agent a new fact, wait a minute, then select Refresh." If nothing appears, cut Refresh and every mention. Do not keep a button whose effect the demo cannot produce.
2. Show the chat cell's code (my recommendation; the turn function is the goal-3 lesson) or hide it and keep the turn function in its own visible cell above the chat?
3. Drop the "last N user messages" slider, as proposed, or keep it with a measured readout?

## Build plan through `cm`

Order matters because every public name has one owner.

1. Read every column-4 cell in one `get_context()` (the stale-cell check needs it).
2. Edit `am_steps`, `am_chat_fn`, `am_amnesia`, `am_seed_fn` into `am_recall_fns`, `am_prompt_fns`, `am_store_fns`: rename `amnesia` to `ams_forget` (add session deletion), `ams_remember_turn` to `ams_remember`, `AMS_SEEDS` to `ams_seeds`, `user_id` to `owner`; change `ams_build_system` to take texts; add `ams_sweep`; delete `ams_chat`, `ChatFn`, `ams_query`.
3. Edit `am_controls` into `am_owner` (dropdown only) and `am_chat` to the version above. Delete `am_state`.
4. Reuse `am_seed`, `am_cleanup`, `am_memories` as `am_store_button`, `am_store`, `am_probe`; reuse `am_trace` as `am_sweep`; create `am_probe_panel` and `am_prompt_panel`; fold `am_try_it` and `am_read_code` into `am_store_md` and `am_owner`.
5. Edit `am_title`. Run the voice pass (redis-voice-edit) on the title, the panel headings, and the footnote.
6. `move_cell` into the order in the table. Verify in the browser: bars on load for both customers; type a new question; Seed and Clean up re-list and redraw; three questions as Tom; switch to Priya (history clears, new session id in the panel); the foods question; slider 0.1 to 0.8; the extraction test from open question 1.
7. Stop the server, restamp `column=4` on every column-4 cell in the file (a live `cm` session re-saves with `column=` on the first cell only; `.agents/memories/iris-basics-live-edit-columns.md`), run `uv run python -m marimo check --strict iris_basics.py` and `uv run pytest tests -q --ignore=tests/live_smoke.py`, relaunch.

Store state to leave behind: Tom and Priya with three seed memories each, every session deleted.

## Review log (v1 to v2)

Three reviews of v1 ran as paseo agents on 2026-10-07; their reports are in `.agents/reviews/`.

- **Owner dropdown inside the chat cell** (teaching review 1, refutation 2): a correctness bug. The defining cell never reruns on its own element, so a customer switch kept Tom's session and history. Owner moved upstream.
- **Chat first** (teaching 2): goals 1 and 2 were placeholders until an LLM key was present. Probe first, panels 1 and 2 merged into one two-owner view.
- **0 to 0.6 cap** (teaching 3): hard-coded today's seeds into the axis. Sweep and slider now 0 to 1; API accepts 1.0 (checked).
- **Bars read as scores** (teaching 4): exact labels and footnote added.
- **Extraction claimed, not shown** (teaching 5): claim cut pending the five-minute test; open question 1.
- **N slider** (teaching 6): cut; reasoning above.
- **HTML helpers as lesson code** (teaching 7): now private in hidden cells.
- **Three restating layers and a "Panel N shows" triplet** (teaching 8, 9): title rewritten, headings name what is on screen, no lead-ins.
- **`mo.cache` staleness and hashing** (teaching 10, refutation "unverified"): dropped; the sweep cell's dependencies do the same job.
- **Wire table** (teaching 11): dropped, not collapsed.
- **Facts corrected** (refutation): 30 public names, not 26; `test_column_budgets` fails today for a different reason; `AsyncFunctionDef` vs the test's `FunctionDef`; `AMS_SEEDS` fails the uppercase rule; the unused-session read raises 404.
- **marimo mechanics** (marimo review): `mo.cache` in the same cell as `ams_recall` raises `TypeError` (confirms dropping it); two value pushes per chat turn and none mid-stream; `mo.ui.chat` accepts the two-argument async generator defined in the cell; top-level `await` in a comprehension compiles as a coroutine on 3.13; slider values arrive rounded to the step's decimals, and Python's grid still needs `round`; `run_button` resets after referencing cells run (not on lazy kernels; this one is autorun); no cycle in the proposed graph.
- Everything else held: the measured bands, the no-score finding (SDK, raw endpoint, and hidden flags), the package checks, every quoted marimo and test line.
