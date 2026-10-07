# Agent Memory build, stage 4 final review (2026-10-07)

**Verdict: PASS WITH NOTES**

No blockers. The file, the live kernel, and the 12 post-review fixes agree. Every column-4 cell ran with status `idle` and no errors. Real findings: the prose hedge dropped from the extraction sentences, an empty-state message that never says "Seed", the owner switch silently resetting both sliders, 42 unthrottled concurrent searches, and a layout that no one has looked at since fixes 10 and 11.

Method: read-only. I ran `ctx.run_cell` for `_`, `redis_client`, `llm_client`, then the 16 column-4 cells in order, in one block. I read the cells back, listed the store, and ran `ams_sweep` and `ams_bars` in the scratchpad (searches only). I pressed no button and sent no chat message. I edited nothing and opened no browser. The kernel was reachable. Scripts are `rv4_*.py` in `$LOCALAPPDATA/Temp`.

## Findings, most severe first

### 1. (Medium, prose and accuracy) The extraction sentences lost their hedge and now promise a result

Evidence, live and file:

- `am_store`: "Agent Memory also extracts memories from session events in the background, on the store's extraction cadence (5 minutes by default). Tell the agent a new fact, wait a few minutes, then select Refresh."
- `am_owner`: "The fourth prompt tells the agent a new fact, and Refresh shows the memory extracted from it a few minutes later."

The first version said "when extraction is enabled on the store". Fix 12 removed it, because extraction works on Nick's store. A customer's store may have extraction off. Whether an LLM extractor writes a memory at all is also the extractor's call. VOICE.md says to hedge what the model decides. Both sentences are flat promises.

Fix, `am_owner`: "The fourth prompt tells the agent a new fact, and Refresh can show the memory extracted from it a few minutes later." Fix, `am_store`, restore the condition: "Agent Memory also extracts memories from session events in the background, when extraction is enabled on the store (every 5 minutes by default). Tell the agent a new fact, wait a few minutes, then select Refresh."

I did not independently verify the "5 minutes by default" figure against the docs. It comes from spec fix 1. The live test (116 s) is consistent with it.

### 2. (Medium, design) Empty store: the panels say "No memories for this owner." and never say what to do

Evidence: `ams_bars({}, 0.3)` renders `<div class="ams-empty">No memories for this owner.</div>` plus ticks and the caption. A fresh store shows this under both owners in the probe panel. The tables above say `_No memories yet._`, and nothing in either place says "Select Seed". `ams_sweep` over an empty store still fires 42 searches that return nothing. It does not crash, since `ams_memory_table` and `ams_bars` both handle empty input. I could not run the true empty case without deleting memories, so this is by construction plus the `ams_bars({})` output.

Fix, one string in `am_render`: `'<div class="ams-empty">No memories yet. Select Seed above.</div>'`. This also covers the 0-memory owner after Clean up. It also drops "owner", which is the one place the notebook says "owner" where it says "customer" everywhere else.

### 3. (Medium, verification gap) The vertical probe layout and the `min-width: 0` fix have never been looked at

The screenshots in `am-build-shots/` are stamped 10:37. `iris_basics.py` was last written at 10:50, after fixes 10 and 11 landed. `04-probe-panel.png` is the side-by-side layout whose label truncation and value-label clipping motivated fixes 10 and 11. Live HTML confirms the structure only: the probe panel's flex directions are all `column`, and the order is Tom then Priya. The CSS lines are present (`min-width: 0`, `white-space: nowrap`). Nobody has seen the result. The store tables are still a two-up `hstack` in the same 740px column (flex dirs `column, row, column, column`). Memory text is wider than the 3-column table allows at half width. Take one screenshot of `am_probe_panel` and `am_store` before Nick demos this. I am forbidden from opening a browser.

### 4. (Medium, verification gap) No real chat turn has ever run through the frontend in this build

4a's browser route failed in reader mode ("Take over" denied). Every turn was a kernel-pushed value in the shape from `chat._convert_value`. The "two value pushes per turn" question is unmeasured: 4a logged 1 sweep and 1 panel run per turn, with the caveat that the frontend may push twice. The panel logic is idempotent under either count, since only the push whose last role is `assistant` reads the session. Worst case is 21 extra searches and a placeholder flash while the next turn streams. Acceptable. The real defect is that the first customer-style turn will be Nick's. Also not run: Clean up then Seed. The Seed-over-existing-memories path ran (note text confirmed). `ams_forget` and the write path of `ams_seed` were not exercised by 4a, and the stage 1 eventual-consistency risk (tables listing before the index catches up) is still unobserved.

### 5. (Low-medium, design) The owner switch resets both sliders, and nothing says so

Evidence: `ams_threshold`, `ams_turns`, and `ams_chatbot` are all defined by the same cell (`qnkX`, `am_chat`). `ams_owner` is defined upstream by `ROlb` (`am_owner`). A switch reruns `am_chat`, so threshold goes back to 0.30, N back to 1, and the session id changes. The probe panel's line jumps back too. This is real but cannot be fixed by moving the sliders to `am_owner`: `am_chat` reads both sliders inside `ams_turn`, so any slider move would then rerun `am_chat` and wipe the conversation. Defining them in the cell that reads them is what keeps moves from resetting the chat. Acceptable, one-line mitigation. In `am_owner`: "Switching customers starts a new conversation and resets both sliders."

### 6. (Low-medium, robustness, unverified at customer scale) 42 concurrent searches with no limit

`ams_sweep` calls `asyncio.gather` over `2 owners x 21 thresholds` with no semaphore. It runs on open, on every probe change, on Seed, Refresh, and Clean up (42 searches), and 21 per chat push. Measured on Nick's store: 0.88 s, no errors. A Redis Cloud preview store with a request rate limit would return 429s, and then `am_sweep` raises and `am_probe_panel` shows an ancestor-error output on open. Fix in `ams_sweep`: `sem = asyncio.Semaphore(8)` and `async with sem:` around each `ams_recall`. Defer unless a customer store errors.

### 7. (Low) Footnote shows under the empty-probe placeholder, and the placeholder repeats "Enter"

`am_probe_panel` builds `_body` as `mo.md("_Enter a search above and press Enter._")` when the text is empty, but the footnote ("Each bar ends at the highest one...") still renders below it, describing bars that are not there. Fix: wrap the footnote `mo.md` in the same `else` branch, or `[] if empty`. Rewrite the placeholder: "_Type a search above and press Enter._"

### 8. (Low) A 0.00 bar is ambiguous

Priya's two episodic memories show `0.00` for "Which foods should I avoid?". The sweep (`ams_sweep(q, {"Tom": [], "Priya": []})` returned them at 0.0) shows they came back at threshold 0.00 and not at 0.05, so 0.00 is a true band. A memory beyond `limit: 10`, or one never returned, also draws 0.00 with an empty bar, and the footnote does not distinguish them. Only matters past 10 memories per owner. No change now.

### 9. (Low) Duplicate extracted text collapses to one bar

Still true (bands are keyed by `m.text`). This run's extraction produced two distinct texts, so nothing collapsed. Leave.

### 10. (Low, accepted) Clean up has no confirmation and deletes extracted memories too

`kind="danger"` only. It deletes every memory whose `ownerId` is Tom or Priya, including the two `episodic` ones. Nick's call. If a customer's store already holds real memories under those two names, they are gone.

## Check A: file

- Every `@app.cell` carries `column=`. `grep ^@app` lists 22 decorators: `_`, `redis_client`, `llm_client` at 0, `rs_title` 1, `lc_title` 2, `cr_title` 3, and the 16 am cells at 4.
- The 16 column-4 cells are contiguous, at lines 180 to 638, and in the spec's order: `am_title`, `am_connect`, `am_store_md`, `am_store_button`, `am_store`, `am_store_fns`, `am_probe`, `am_sweep`, `am_probe_panel`, `am_recall_fns`, `am_owner`, `am_chat`, `am_turn_sweep`, `am_prompt_panel`, `am_prompt_fns`, `am_render`.
- CRLF: 699 CRLF, 0 bare LF (counted myself). The 4b claim holds.
- `uv run python -m marimo check --strict iris_basics.py`: exit 0, no output (run myself).
- Live code against file: for each of the 16 cells, every non-blank live line is present in the file. 0 missing in all 16.
- `git log -p --follow -S "ams_client = AgentMemory"` shows the line introduced in commit `705c6a0` ("Prototype Agent Memory chat", 2026-10-06). `git show HEAD:iris_basics.py` still has it (the `AgentMemory(... os.environ[...])` block). The working-tree diff touches only the `am_connect` decorator. So `ams_client = AgentMemory(os.environ[...])` predates this build.
- `git diff HEAD` also shows `Callable` dropped from `_`'s return tuple, `html`, `json` added, and `column=0` on the two cells. `check --strict` passes.

### Pytest: `17 failed, 46 passed` (run myself, `--ignore=tests/live_smoke.py`)

| Test | Failure | Class |
|---|---|---|
| `test_notebook::test_credential_free_cells_run_offline` | no attribute `intro` | stale suite (intro cell) |
| `test_notebook::test_column_0_is_the_fixed_global_setup` | column 0 holds `_`, `redis_client`, `llm_client` | stale suite (column 0 contents) |
| `test_notebook::test_intro_table_quotes_each_columns_goal_titles` | `KeyError: 'intro'` | stale suite (intro cell) |
| `test_notebook::test_column_budgets` | budgets for the old columns | stale suite (column budgets) |
| `test_notebook::test_try_only_where_a_lesson_needs_it` | `cr_create`, `rs_cleanup`, `rs_seed` lost their `try` | stale suite (cr_*/rs_* cells) |
| `test_notebook::test_no_status_comparisons` | expected one `app.setup` block | stale suite (app.setup) |
| `test_notebook::test_llm_client_contract` | `LLM_MODEL is "claude-opus-5-5"` | stale suite (the code now reads `AI_MODEL`) |
| `test_notebook::test_chat_cells_own_their_controls` | chat cells are `['am_chat']`, expected `rs_chat`, `lc_chat` too | stale suite (rs_chat/lc_chat) |
| `test_notebook::test_claude_is_called_once_per_chat_turn` | `StopIteration` | stale suite (rs_chat/lc_chat) |
| `test_notebook::test_setup_block_defines_exactly_the_shared_names` | expected one `app.setup` block | stale suite (app.setup) |
| `test_safety::test_key_templates_and_prefixes_start_with_key_prefix` | `cr_data_model`, `rs_key_prefix` missing | stale suite (cr_*/rs_* cells) |
| `test_safety::test_cleanup_deletes_by_name_and_never_scans` | expected a Clean up cell in each of 4 columns | stale suite (column structure) |
| `test_safety::test_no_secret_read_bound_to_a_global` | `ams_client` reads `os.environ[...]` into a global-shaped name | stale suite. `am_connect` predates this build (`705c6a0`, unchanged except the decorator). The test allowlist knows only `iris_session`, `niquests.Session`, `redis.Redis.from_url` and `_`-prefixed names. Not caused by this build |
| `test_safety::test_sessions_are_pinned` | `iris_session` is the only place a `niquests.Session` is built: `0 == 1` | stale suite. No `iris_session` cell exists, so the count is 0. Unrelated to `am_connect` |
| `test_safety::test_mcp_session_sends_the_verified_accept_header` | `StopIteration` | stale suite (cr_* cell) |
| `test_safety::test_create_password_is_masked_and_the_reply_never_rendered` | `StopIteration` | stale suite (cr_create) |
| `test_safety::test_import_ownership` | `_` (column 0) imports `AsyncGenerator` | stale suite (import ownership) |

Caused by this build: none. No `test_voice` test failed. No `test_safety` test failed on new text or new requests. Caveat: none of these tests inspect the am cells' new prose or the new request bodies, so a pass there is not evidence for the new text.

## Check B: live

- Status after running: all 22 cells listed. Column 0 and the 16 column-4 cells are `idle` with `errors=[]`. The three other-column titles are `stale` (they have no graph entries, as before). No `exception` anywhere.
- `am_store`: `ownerId: Tom (3)`, `ownerId: Priya (5)`, then "Agent Memory also extracts memories from session events in the background, on the store's extraction cadence (5 minutes by default). Tell the agent a new fact, wait a few minutes, then select Refresh."
- Priya's listing straight from `ams_memories("Priya")`:

| text | memory_type | session_id | created (UTC) |
|---|---|---|---|
| I have a cat named Miso | semantic | None | 14:16:48 |
| I'm allergic to peanuts and tree nuts | semantic | None | 14:16:48 |
| User plans to move to Denver in May 2027. | episodic | e7429e4384042f2c | 15:40:02 |
| I'm planning a canoe trip in Maine in June | semantic | None | 14:16:48 |
| User adopted a parrot named Rio on October 7, 2026. | episodic | e7429e4384042f2c | 15:40:02 |

  Tom: 3 `semantic`, no session id. Two of Priya's five are `episodic`.
- `am_probe_panel`: owner order Tom then Priya, every flex direction `column`. Each pane has bars (Tom 0.05 x3, Priya allergy 0.35, cat 0.05, canoe 0.05, parrot 0.00, Denver 0.00), a JSON request (`"similarityThreshold": 0.3`, `"ownerId": {"eq": ...}`, `"limit": 10`), the caption "Line: slider at 0.30.", and the footnote.
- `am_prompt_panel`: heading "3. The prompt the app builds" and the placeholder.
- `am_chat`: `data-prompts` is `["What pets do I have?","Where am I headed this summer?","Which foods should I avoid?","I'm training for a marathon in October."]`.
- Graph: none of the 16 "Gone" names is defined by any cell, and `multi-def` is empty.

## Check C: the 12 post-review fixes

Live and file, with file line numbers:

1. `am_store_md` (file 227): "Clean up deletes every memory whose `ownerId` is Tom or Priya. Chat sessions are not deleted. The store expires them after an hour by default." No semicolon.
2. `am_store_button` (file 234): `ams_seed_button = mo.ui.run_button(label="Seed")`.
3. `am_render` (file 642): `threshold = round(threshold, 2)`.
4. `am_title` (file 193): "Agent Memory is in public preview on Redis Cloud."
5. `am_prompt_panel`: line 587 `mo.md("**System prompt the app built for this recall**")`, line 603 `mo.md("### 3. The prompt the app builds")`, line 576 `_Send a message to see what the last turn searched for and the prompt the app built._`, line 589 ``**Session `{ams_session_id}` in Agent Memory** ({len(_session.events)} events)``.
6. `am_probe` (file 373): "The line is the `similarityThreshold` slider under the chat. Press Enter to run the search."
7. `am_probe_panel` (file 404): `_body = mo.md("_Enter a search above and press Enter._")` when `not ams_probe.value.strip()`.
8. `am_title` (file 187): "Select Seed to write memories for two made-up customers, Tom and Priya, then search them and chat as either to see three principles:". Line 189 goal 1 reads "shares no keyword with "I'm allergic to peanuts and tree nuts", yet the search returns it for Priya ... moves the line past the weakest matches first." Line 191 goal 3 ends "and the prompt the app builds from the hits."
9. `am_title` (file 186): "Redis Agent Memory keeps each conversation in session memory and durable facts in long-term memory, written by the app or extracted from session events." The kept deviation, present.
10. `am_probe_panel`: `_body = mo.vstack([mo.vstack([...]) for _owner, _bands in ams_probe_bands.items()])` (file 406 to 408). Title line 190 ends "The search box runs the same search as Tom and as Priya."
11. `am_render` (file 661, 662, 666): `.ams-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }`, `.ams-track { position: relative; min-width: 0; height: 12px; ...}`, `.ams-value { ...; white-space: nowrap; }`.
12. `am_chat` (file 531): `prompts=[..., "I'm training for a marathon in October."]`. `am_store` (file 279): "on the store's extraction cadence (5 minutes by default)." `am_owner` (file 477): "The fourth prompt tells the agent a new fact, ..." Refresh button present (file 235 area, `label="Refresh"`).

All twelve are present in both places. Finding 1 above is about the wording fix 12 left behind.

## Check D: the reports

- **4a store claims: reproduced.** Tom 3, Priya 5, the two episodic memories with session id `e7429e4384042f2c`, both created 15:40:02 (table above). Also reproduced: `ams_sweep` allergy 0.35, Tom 0.05 x3 for the foods question, 0.88 s for the sweep (4a: 0.7 to 1.8 s).
- Not reproducible by me (needs a chat turn or session reads): the five turn tables, LLM timings, N-slider bands, the session-event counts, the 116 s extraction delay. I did not send a message. I did not query the stray session `cf7f65e45a52c7ba`. 4a's own admission stands: the real-frontend rerun count is unmeasured.
- One 4a detail to correct in the record: its table shows Priya's extracted memories created "2026-10-07 15:40:02" and the parrot message pushed at 15:38:06. That is 116 s, as it says. Fine.
- **4b file claims: reproduced.** Decorator listing, 699 CRLF and 0 LF, `check --strict` exit 0, 17 failed and 46 passed, relaunch answering. The 4b failure list matches mine. Its cell listing after relaunch showed all `stale`. After my run all are `idle`.
- 4b's remark that `test_sessions_are_pinned` "may be the missing `iris_session`" is right. It is (count 0, not 1).
- One thing the reports do not say: the kernel ids changed on relaunch (spec ids like `Xref`/`SFPL` for the title and connect cells survive, but `am_chat` is now `qnkX`, `am_store_md` `BYtC`). Nothing depends on the old ids.
- Stale leftovers to know: 4a left extra session events (`cf7f65e45a52c7ba`, `fb230e6fb949f37b`) and one stray LLM call in the store. No long-term effect (Tom is still 3).

## Check E: prose, final pass

Rules applied: sentences under 25 words with inline code stripped, serial commas, no semicolons, no `!`, Slop floor, one term per concept, Nick's rulings. No semicolons or exclamation marks found in any reader-visible string. No Slop-floor banned word found. Longest sentences: title goal 1 sentence 2 at 24 words (25 with the code token), probe footnote sentence 3 at 24.

| Where | Sentence | Problem | Rewrite |
|---|---|---|---|
| `am_title` goal 1 | "...so raising the slider moves the line past the weakest matches first." | "matches" is a third word for the search result, next to "memories" and "hits" | "...so raising the slider moves the line past the weakest memories first." |
| `am_title` goal 3 | "The hits go into the system prompt, so the model sees a memory only because the app put it there." and "...the prompt the app builds from the hits." | "hits" again. One term per concept | "The recalled memories go into the system prompt, so the model sees one only because the app put it there." and "...the prompt the app builds from them." |
| `am_title` goal 3 | "shows the search text, the bars for it, and the prompt the app builds ..." | Three literal items, not rhetorical, and they match the panel. Leave. Cut to two only if Nick reads his ruling strictly | none |
| `am_title` sentence 2 | "Redis Agent Memory keeps each conversation ... durable facts ..." | Deliberate deviation (prefix after the H1, matches the other three column titles). "facts" is the only place the notebook says "facts" in prose, everywhere else it says "memories" | leave the prefix. Optional: "...and what it learns about a customer in long-term memory ..." |
| `am_store` | "...on the store's extraction cadence (5 minutes by default). Tell the agent a new fact, wait a few minutes, then select Refresh." | Hedge dropped (finding 1). The three-step imperative is a real procedure, not a rule-of-three flourish | see finding 1 |
| `am_owner` | "...and Refresh shows the memory extracted from it a few minutes later." | Flat promise about an LLM extractor | "...and Refresh can show the memory extracted from it a few minutes later." |
| `am_owner` | "Switching customers starts a new conversation." | Omits the slider reset | "Switching customers starts a new conversation and resets both sliders." |
| `am_probe_panel` placeholder | "_Enter a search above and press Enter._" | "Enter" twice | "_Type a search above and press Enter._" |
| `am_probe_panel` footnote | "Each bar ends at the highest one that still returned the memory, so its similarity lies between that value and the next step up." | "one" has "thresholds" two sentences back, "still" is an empty word. 24 words, so no length failure | "Each bar ends at the highest threshold that returned the memory, so its similarity lies between that value and the next step up." |
| `am_probe_panel` footnote and `ams_bars` caption | "come back", "clears", "returned", "recall" | four verbs for one event | choose "returns": "Memories at or above the line are returned at the slider's setting." and the caption "Highest similarityThreshold at which the memory is returned." (keep the caption under the hairline) |
| `ams_bars` empty state | "No memories for this owner." | "owner" where the notebook says "customer", and no next step | "No memories yet. Select Seed above." |
| `am_prompt_panel` | heading "The prompt the app builds", label "System prompt the app built" | tense drift builds/built | leave, or "System prompt the app builds for this recall" |
| `am_probe` | heading "Search long-term memory" and label "Search long-term memory with" | repeats the phrase | label: "Search text" |

Everything else passes: the title's paragraph 1 (17 words), goal 2, the status sentence and the settings sentence (serial comma present), `am_store_md` (all four sentences under 14 words, no semicolon, 'Seed' matches the button), the heading set (sentence case, no terminal period), button labels `Seed`, `Refresh`, `Clean up`, dropdown label `Chat as`, slider labels, note strings (`Wrote N new memories to long-term memory.`, `Every seed memory is already in long-term memory.`, `Deleted N memories for Tom and Priya.`), the `_No memories yet._` table placeholder, and "Search with the last N user messages". No "you already" framing anywhere.

## Check F: first-open behaviour, ranked

| # | Behaviour | Real defect or acceptable | One-line fix |
|---|---|---|---|
| 1 | Layout after fixes 10 and 11 unseen (finding 3) | Gap, not a defect yet | Screenshot the probe panel and the store tables once |
| 2 | Extraction promise flat (finding 1) | Real, prose | Restore the condition, add "can" |
| 3 | Empty store: bars say "No memories for this owner." with no Seed pointer (finding 2) | Real, low cost | Change the `ams_bars` empty string |
| 4 | Owner switch resets both sliders and the session (finding 5) | Acceptable, say so | One clause in `am_owner` |
| 5 | Zero-memory owner in the probe: empty bars plus the real request body | Acceptable. The request was sent, so showing it is honest | none |
| 6 | Empty probe text: placeholder shown, footnote still rendered under it (finding 7) | Real, trivial | Move the footnote inside the `else` |
| 7 | 42 concurrent searches on open, 21 per push (finding 6) | Acceptable on Nick's store (0.88 s). Unthrottled for a rate-limited customer store | `asyncio.Semaphore(8)` in `ams_sweep` |
| 8 | Two chat value pushes per turn (finding 4) | Acceptable. The `_turn_done` gate makes both pushes safe | measure once in a real tab |
| 9 | Autorun on open | Acceptable. Fires no LLM call and writes nothing | none |
| 10 | Clean up with no confirmation (finding 10) | Acceptable for a demo | none |
