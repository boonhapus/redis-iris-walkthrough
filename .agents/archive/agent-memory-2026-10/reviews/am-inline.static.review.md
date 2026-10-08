# Agent Memory inline: static review (2026-10-07, afternoon)

Lens: static. Spec: `.agents/plans/agent-memory-inline-spec.md`. Report: `.agents/reviews/am-inline.report.md`. Kernel `http://127.0.0.1:2718/`, read-only: no `set_ui_value`, no button, no chat message, no store write, no cell edits. Kernel calls: `help(cm)` first, then one script, `C:\Users\NickCooper\AppData\Local\Temp\am_inline_review_static_1.py`. Diff inputs in the same folder: `am_spec_<cell>.txt` (spec fences, cut by line number), `am_live_<cell>.txt` (written from `ctx.cells[<cell>].code`), `am_norm_*` (trailing whitespace stripped).

## Verdict: PASS WITH NOTES

The four cells are byte-identical to the spec, disk mirrors the kernel, the graph is exactly as specified, `marimo check --strict` exits 0, the voice test passes, and the implementer's report matches what I read. One low finding on the wording of the new heading.

## Findings

### Low. The heading's embedded "and" reads as a five-item list missing its serial comma

Observed, live `am_build_md` output and `iris_basics.py` line 340: `## The Agent Memory code: client, store, request and prompt, turn`.

Why: VOICE.md, Sentence and paragraph mechanics, Lists: "Serial comma always". The heading's list is comma-only except for the "and" inside "request and prompt", so the first reading is "client, store, request and prompt" as four items with a misplaced conjunction and ", turn" dangling, i.e. five steps. The lead-in beneath it says "One cell per step" and there are four visible code cells (`am_connect`, `am_store_fns`, `am_prompt_fns`, `am_turn_fn`), so the claim holds only when the reader groups "request and prompt" as one item. The sibling heading, "Notebook plumbing: sliders, chat object, sweep, bars", has one plain item per cell and reads without that pause.

Fix: `## The Agent Memory code: client, store, prompt, turn` (one item per cell, matching the cell names; the lead-in already says what the turn and store cells do). If Nick wants "request" in the title, the spec's text stands: nothing else in the heading or lead-in breaks a VOICE rule, and the spec's wording was approved, so this is a judgement call, not a defect.

## Evidence by check

### 1. Code equals spec

Spec fences cut by line number: `am_prompt_fns` 49-72, `am_turn_fn` 82-108, `am_turn_sweep` 118-143, `am_build_md` 153-157 (fence lines 48/73, 81/109, 117/144, 152/158 confirmed as the triple backticks). Live code dumped from the kernel: 918, 1401, 1421, 205 chars, none ending in a newline, all ASCII.

| Cell | spec lines/bytes | live lines/bytes (normalized) | `diff` | `cmp` spec-minus-trailing-LF vs live |
|---|---|---|---|---|
| `am_prompt_fns` | 24 / 919 | 24 / 919 | identical | byte-identical |
| `am_turn_fn` | 27 / 1402 | 27 / 1402 | identical | byte-identical |
| `am_turn_sweep` | 26 / 1422 | 26 / 1422 | identical | byte-identical |
| `am_build_md` | 5 / 206 | 5 / 206 | identical | byte-identical |

### 2. Disk equals kernel

Every non-blank live line, with four spaces prepended, found verbatim in `iris_basics.py` (`grep -Fxq`): `am_prompt_fns` 18/18, `am_turn_fn` 21/21, `am_turn_sweep` 20/20, `am_build_md` 4/4; 63 checked, 0 missing. `grep -n "ams_recall\|ams_remember\|am_recall_fns" iris_basics.py README.md tests/*.py` prints nothing (exit 1). A scan of every live cell's code for the same three names returns `[]`.

### 3. Cell health

All fifteen `am_*` cells `status=idle errors=[]`: `am_title` Xref, `am_store_button` RGSE, `am_store` Kclp, `am_owner` ROlb, `am_workbench` Evqy, `am_build_md` qxJz, `am_connect` SFPL, `am_store_fns` emfo, `am_prompt_fns` ulZA, `am_turn_fn` RJQd, `am_machinery_md` DhJD, `am_settings` qnkX, `am_chat` TqIu, `am_turn_sweep` Vxnm, `am_render` ecfG. `am_build_md` reads `idle`, not `stale`.

### 4. Graph

- `am_prompt_fns` defs `['ams_build_system', 'ams_query', 'ams_request']`; refs `['ai', 'dict', 'float', 'int', 'it', 'list', 'reversed', 'str']`.
- `am_turn_fn` defs `['ams_session_id', 'ams_turn']`; refs `['AsyncGenerator', 'ai', 'ams_build_system', 'ams_client', 'ams_owner', 'ams_query', 'ams_request', 'ams_settings', 'dt', 'list', 'models', 'secrets', 'str', 'stream_llm']`. All ten required names present; `ams_recall`, `ams_remember`, `get_threshold`, `get_turns`, `ams_threshold`, `ams_turns`, `ams_chatbot` absent.
- `am_turn_sweep` refs include `ams_client`, `ams_request`; `ams_recall` absent.
- `ams_recall def=[] refs=[]`, `ams_remember def=[] refs=[]`, `am_recall_fns def=[] refs=[]`; `ams_recall in globals: False`, `ams_remember in globals: False`.
- `ams_request def=['am_prompt_fns'] refs=['am_workbench', 'am_turn_fn', 'am_turn_sweep']`; `ams_client def=['am_connect'] refs=['am_workbench', 'am_store_fns', 'am_turn_fn', 'am_turn_sweep']`; `ams_turn def=['am_turn_fn'] refs=['am_chat']`.
- `cycles: set()`.
- Ancestors of `am_turn_fn` are unchanged in effect: its new refs (`ams_client`, `dt`, `models` from `am_connect`; `ams_request` from `am_prompt_fns`) were already upstream through the old `am_prompt_fns`, so the move adds no new rerun path into the chat.

### 5. End state

ORDER `['am_title', 'am_store_button', 'am_store', 'am_owner', 'am_workbench', 'am_build_md', 'am_connect', 'am_store_fns', 'am_prompt_fns', 'am_turn_fn', 'am_machinery_md', 'am_settings', 'am_chat', 'am_turn_sweep', 'am_render']`, COUNT 15, `am_recall_fns` absent, 21 cells in the whole notebook. `hide_code=False` on exactly `am_connect`, `am_store_fns`, `am_prompt_fns`, `am_turn_fn`; `True` on the other eleven. Disk decorators agree: bare `@app.cell` at lines 347, 367, 424, 454; `hide_code=True` on every other column-4 cell. Transient readings at the time of my call (the probe reviewer's domain, not findings): owner Tom, `ams_settings() == (0.3, 3)`, chat len 0, `ams_session_id` `tom-a2c65cf13b70` (unchanged from the report, so the owner had not yet been switched), workbench `ams-line` styles all `left:30%` (8 rows).

### 6. The implementer's report

Every value I re-read matches the report: ORDER, statuses, hide_code flags, graph lines, globals, session id, `(0.3, 3)`, `ams_store` counts `{'Tom': 3, 'Priya': 3}`, bands `{'Tom': 5, 'Priya': 3}`, turn query `'Which foods should I avoid?'` with `live: False`, `isasyncgenfunction True`, the Priya search `["I'm allergic to peanuts and tree nuts"]`, the Tom search `['User is interested in knowing which foods to avoid.']`, the bind `['actor_id', 'content', 'created_at', 'role', 'session_id']`, all five workbench needles True, 12 `ams-row` (10 rows plus the `.ams-row` CSS rule in each of the two bar blocks). Disk: grep 2 lines 340, 385, 425, 488, 491, 561 as reported; 636 lines; file 27095 bytes, mtime 14:29:40.12 CDT, no save since; `diff -u` backup vs disk has the same nine hunk headers as the report's diff, 55 changed lines. Pre-edit transcript in the report matches `am_inline_prestate.txt` (four messages, two user turns). The report's three "noticed, not touched" items I could check are all accurate: the sweep comments were already above `ams_turn_live` in the backup (backup lines 587-589), the serializer dropped the blank line before `return` in `am_turn_sweep` only (disk 579-580; `am_prompt_fns` and `am_turn_fn` keep theirs), and `am_settings` on disk reads `mo.state(1)` for N.

One gloss for the probe reviewer's check 11: the report's "store counts" are the cached `ams_store` dict (Tom 3, Priya 3). A live `ams_memories("Tom")` returns 5: the three seeds plus `'User is interested in knowing which foods to avoid.'` (semantic) and `'User asked how many cats they have.'` (episodic); `ams_memories("Priya")` returns 3. The report says this in prose; its headline number is the cache. The verification anomaly (Tom search non-empty at 0.3) is this and nothing else; the band for that extracted memory is 0.65 now (0.5 in the prestate file), so it is above any threshold the probe reviewer uses.

### 7. Whole file

- `uv run python -m marimo check --strict iris_basics.py`: no output, exit 0.
- `uv run pytest tests/test_voice.py -q`: `8 passed in 0.02s`, exit 0.
- `uv run pytest tests/test_notebook.py -q -k "budget or hide_title or chat_cells_own or claude_is_called"`: 3 failed, 2 passed, 16 deselected.
  - PASSED `test_columns_start_with_a_title_and_hide_title_and_button_code`, `test_file_budget`.
  - FAILED `test_column_budgets`: `AssertionError: expected exactly one `with app.setup(...)` block` (raised in `_setup_block()` while measuring column 0). Names no changed cell.
  - FAILED `test_chat_cells_own_their_controls`: `AssertionError: the chat cells are ['am_chat']` (the suite expects `rs_chat` and `lc_chat` too). Names `am_chat`, not one of the four changed cells.
  - FAILED `test_claude_is_called_once_per_chat_turn`: `StopIteration` in `_chat_model` (the model passed to `mo.ui.chat` in `am_chat` is `ams_turn`, defined in `am_turn_fn`, and the suite expects it inside the chat cell). No message; traceback names only test lines 148 and 409.
  - The same five tests against the pre-move backup (`IRIS_BASICS_NOTEBOOK=...\iris_basics.before-inline.py`, `-p no:cacheprovider`) give the identical 3 failed / 2 passed with the same three messages, so all three failures pre-date the move.

### 8. Voice

Heading `The Agent Memory code: client, store, request and prompt, turn`: sentence case with the product name's capitals kept; colon after a label that names a list (Mechanics, "Labelled pivots and colons"); no terminal period; no "&" (Pre-publish checklist 2: "&" only in explainer headings); no gerund, no em dash, no banned or empty word. The one hit is the embedded "and" against "Serial comma always", the low finding above. Lead-in, unchanged: "One cell per step, with the code visible." (8 words) and "Seed and Clean up call the store functions, and each chat turn calls `ams_turn`." (15 words), both under the 25-word ceiling, no Slop floor hit. "One cell per step" holds: four visible code cells, four heading items, provided "request and prompt" is read as one item.

### 9. Turn semantics

Old `am_turn_fn` (backup 476-512) called `await ams_remember(ams_session_id, owner, str(messages[-1].content))`; old `ams_remember` (backup 453-461) passed `session_id=session_id, actor_id=owner, role=models.MessageRole.USER, content=[{"text": text}], created_at=dt.datetime.now(dt.timezone.utc)`. New body passes `session_id=ams_session_id, actor_id=owner, role=models.MessageRole.USER, content=[{"text": str(messages[-1].content)}], created_at=dt.datetime.now(dt.timezone.utc)`: the same five keywords with the helper's arguments substituted. `inspect.signature(ams_client.add_session_event_async)` is keyword-only with `actor_id`, `role`, `content`, `created_at` required and `session_id` optional; the bind yields exactly those five. Old search: `query = ams_query(messages, turns)`, `recalled = await ams_recall(query, owner, threshold)` where `ams_recall` (backup 436-439) did `search_long_term_memory_async(request=ams_request(query, owner, threshold))` and returned `r.items`. New: `found = await ams_client.search_long_term_memory_async(request=ams_request(ams_query(messages, turns), owner, threshold))` and `[m.text for m in found.items]`. Same request text, same owner and threshold, `found.items` in place of `recalled`. Comments and docstring: the six comment/docstring lines of the old cell and the six of the new cell diff as identical (`COMMENTS/DOCSTRINGS IDENTICAL`). `isasyncgenfunction(ams_turn)` True.

### 10. The sweep's comment move

Disk (cell at 541-580): `async def ams_sweep` at relative line 13 with no comment above it; the two comment lines at relative 34-35, directly above `ams_turn_live =` at 36. Live code equals the spec body, which has the same placement. The backup already had this placement (relative 32-34 of its cell), so the spec's "moved from above `async def ams_sweep`" describes an earlier change, as the report says; the only effective change to the sweep is the inner `recall`.

### 11. Other columns and files

`grep -n "ams_recall\|ams_remember\|am_recall_fns" iris_basics.py README.md tests/*.py`: no output, exit 1. README.md does not mention the old or new heading text. Files in the project newer than the backup: the brief, the spec, the report, `iris_basics.py`, `__marimo__/session/iris_basics.py.json`, and `.pytest_cache/v/cache/nodeids`.

## Notes outside the move

- `am_settings` on disk initializes N with `mo.state(1)`; the live value is 3. A fresh open starts at N=1. The spec left the cell untouched by design; recorded because the spec's "Expected end state" lists N 3 and that is true of the kernel only.
- Disk line 488 (the turn's search call) is 129 characters; the old code split it over two lines. Spec-mandated; `marimo check --strict` accepts it.

## Final state

Static lens: nothing to restore. Kernel calls were `help(cm)` plus one read-only script; no `set_ui_value`, button, chat message, cell edit, or store write. Last readings: owner Tom, `(0.3, 3)`, chat len 0, `ams_session_id` `tom-a2c65cf13b70`, `ams_store` Tom 3 / Priya 3, `ams_memories` Tom 5 / Priya 3. Side effects: Temp scratch files named above and this review; two of the three pytest runs refreshed `.pytest_cache` under the project (the third used `-p no:cacheprovider`).
