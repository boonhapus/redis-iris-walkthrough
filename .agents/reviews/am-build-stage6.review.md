# Agent Memory build, stage 6 (demo / Iris calls / machinery) independent review (2026-10-07)

**Verdict: PASS WITH NOTES**

No blocker. The 17 cells are in table order with the right flags, the `ams_settings()` fix holds in the graph and in both probes, the guard analysis is correct, and the file equals the kernel. The defects are prose that overclaims in the two lead-ins, one stale comment, and a regression that nothing locks. Nothing here changes behavior.

Method: read-only. `cm.get_context()` reads, a graph walk of defs and refs, the two allowed probes (each restored, reads in separate calls), marimo 0.24.2 source, the file on disk. No edit, run, chat message, button press, or delete. Final state: owner Tom, threshold 0.3, N 1, chat empty, `ams_session_id` `tom-d108b315a38c` (the owner round trip changed it twice, as designed).

## Findings, most severe first

### 1. (Medium, prose accuracy) Both lead-ins say things the column does not do

`am_build_md` (live and `iris_basics.py:365-371`, equal to the spec verbatim, so the builder is faithful and the spec prose is wrong):

| Claim | Fact |
|---|---|
| "in the order a turn uses them: client, store, search, prompt, turn" | A turn never touches the store cell. `ams_seeds`, `ams_seed`, `ams_forget`, `ams_memories` serve Seed, Clean up, and the tables. A turn runs `ams_remember` (prompt cell), `ams_query` (prompt cell), `ams_recall` (search cell), then `ams_build_system` (prompt cell), so the prompt cell is used on both sides of the search. |
| "Each function names the principle it serves" | Five of the ten functions do (`ams_seed`, `ams_request`, `ams_remember`, `ams_build_system`, `ams_turn`). Five do not: `ams_memories`, `ams_forget`, `ams_recall`, `ams_sweep`, `ams_query`. |
| "The controls, state, and layout are at the end of the column" | The layout is `am_workbench`, cell 6, in the demo section. The tail holds no layout cell. The sliders, state, sweep, and rendering helpers are what sits there. |

`am_machinery_md` repeats "the layout" ("Controls, state, the threshold sweep, the layout, and the rendering helpers"). Controls are also split: the Seed buttons and the owner dropdown live in the demo section, so "Controls" overstates what is collapsed. "Threshold sweep" is a name the reader has not met; the demo says "bars".

Fix, `am_build_md` (also drops bare "Iris", see finding 4):

```python
mo.md(r"""
## The Agent Memory calls behind the demo

Five cells: client, store, search, prompt, turn. Seed and Clean up call the store functions, and each chat turn calls `ams_turn`.
A function that serves one of the three principles names it in its docstring. The sliders, slider state, bar sweep, and rendering helpers are at the end of the column with their code hidden.
""")
```

Fix, `am_machinery_md`:

```python
mo.md(r"""
## Notebook machinery

The sliders, the chat object, the slider state, the sweep behind the bars, and the rendering helpers. Their code is hidden. In edit mode, open a cell to read it.
""")
```

Voice check on both: every sentence is under 25 words with inline code stripped (the long one in `am_build_md` is 21), serial commas present, no semicolon, no exclamation mark, no banned or empty word, no rhetorical triple (the lists name real cells).

### 2. (Low-Medium, accuracy) The `am_settings` comment describes the design the fix replaced

Live comment: "The chat reads the latest slider values through these getters. A slider's .value cannot be read in the cell that defines it, and defining the sliders elsewhere would rerun the chat on every move." After the fix the chat does not read these getters: `ams_turn` calls `ams_settings()`, and the getters are read by `am_chat` (initial values), `am_workbench`, and `am_turn_sweep`. The comment also omits the one fact a future editor needs: a cell that references the getters directly reruns on a slider move, which is the getter trap the builder hit. The docstring on `ams_settings` states it, but the docstring is below the comment, and the spec's "replace the two-line comment" text was never updated.

Fix, replace the comment (`iris_basics.py:589`):

```python
# The chat reads the sliders through ams_settings(). A cell that references these getters directly reruns on every slider move, and a rerun of am_turn_fn would replace the chat and wipe its history. A slider's .value cannot be read in the cell that defines it, and defining the sliders elsewhere would rerun the chat on every move.
```

### 3. (Low, test debt) Nothing locks the fix, and the failure is silent

The regression was a history wipe on slider moves. It passes `marimo check`, raises no error, and only a probe catches it. One edit to `am_turn_fn` (adding `get_threshold()` back) reintroduces it. Add a static test: parse `iris_basics.py`, find `def am_turn_fn(`, assert its parameter names contain neither `get_threshold` nor `get_turns` and do contain `ams_settings`. Five lines in `tests/`, no kernel needed.

### 4. (Low, voice) "The Iris calls" is bare "Iris" on the first mention in the column, and the term drifts

VOICE.md Do and don't: "Redis" prefix at each cold-entry boundary (H1, TL;DR, H2 opener); no bare "Iris" on first mention. The column's H1 says "Redis Agent Memory", and no demo cell says "Iris", so this H2 is the first. The calls are Agent Memory calls, and the title and prose call the product "Agent Memory" everywhere, so "Iris calls" is a second term for one concept. The fix text in finding 1 uses "The Agent Memory calls behind the demo". The spec table's "Iris" section label is internal and can stay.

### 5. (Low, design) The Iris turn function shows a notebook device

The visible `am_turn_fn` has `threshold, turns = ams_settings()` under the comment "ams_settings reads the two sliders (notebook machinery, at the end of the column)." That is the only line in the five Iris cells that is not an Agent Memory call, and it is labelled. I see no cleaner option: the indirection is what keeps the cell from rerunning. Tighten the comment so the reader knows to skip it:

```python
    # Slider values come from ams_settings, a notebook helper in the machinery cells below. It is not an Agent Memory call.
```

"At the end of the column" is also loose: `ams_settings` is cell 14 of 17.

### 6. (Low, mode) In `marimo run` the machinery lead-in promises something that cannot happen

README Step 3 offers `uv run marimo run iris_basics.py`. In app mode code stays hidden, so the Iris section shows a heading and "Agent Memory is healthy." and nothing else, and the machinery section shows a heading over four empty hidden cells. "Open a cell to read it" is only true in edit mode. The fix text in finding 1 says "In edit mode". Browser behavior is from marimo's hide-code model, not observed; I may not open a browser.

### 7. (Info) The health line belongs where it is

"Agent Memory is healthy." is the output of the client cell (`am_connect`, cell 8), directly under its visible code. It is the first output in the Iris section, and it comes from the client cell, so the split stays clean. No change.

### 8. (Info, pre-existing) `am_store_md` is `stale` and absent from `ctx.graph.cells`

Same class as `rs_title`, `lc_title`, `cr_title` (static markdown, `refs mo`). Its code and its rendered text match ("Chat sessions are not deleted. The store expires them after an hour by default."), no errors. Graph holds 19 of 23 cells. Not caused by stage 6.

## Evidence by check

**1. Order and flags.** `list(ctx.cells)` from `am_title` onward gives 17 cells in this order, all `hide_code` as the table says: `am_title`, `am_store_md`, `am_store_button`, `am_store`, `am_owner`, `am_workbench`, `am_build_md` (True), `am_connect`, `am_store_fns`, `am_recall_fns`, `am_prompt_fns`, `am_turn_fn` (False), `am_machinery_md`, `am_settings`, `am_chat`, `am_turn_sweep`, `am_render` (True). Code visible on exactly the five named cells. Every cell `idle` with 0 errors except `am_store_md` (`stale`, finding 8). No column-4 cell sits among the other columns' cells.

Code against spec:
- `am_turn_fn`: differs from the spec's original only where the as-built fix says: comment line 2 reads "ams_settings reads the two sliders (notebook machinery, at the end of the column)." and the body gains `threshold, turns = ams_settings()` then `ams_query(messages, turns)` and `ams_recall(query, owner, threshold)`. Refs: `AsyncGenerator, ai, ams_build_system, ams_owner, ams_query, ams_recall, ams_remember, ams_settings, list, secrets, str, stream_llm`. Matches the as-built refs list. Defs `ams_session_id`, `ams_turn`.
- `am_chat`: identical to the spec, character for character. Defs `ams_chatbot, ams_prompts, ams_threshold, ams_turns`. No `ams_turn` or `ams_session_id` def. Output empty.
- `am_settings`: the spec's comment verbatim, both `mo.state` lines, plus the `ams_settings()` function with its docstring, exactly the as-built text. Defs `ams_settings, get_threshold, get_turns, set_threshold, set_turns`.
- `am_build_md`, `am_machinery_md`: identical to the spec. The spec text is the problem (finding 1).
- Disk (`iris_basics.py:365-371, 539-615`) equals live for all five.

**2. Reruns, from `ctx.graph` defs and refs plus state identity (`self.globals[ref] is state`):**

| Trigger | State-matched cells | Minus setter cell | Reruns (with descendants) |
|---|---|---|---|
| (a) threshold | `am_workbench`, `am_chat` | `am_chat` | `am_workbench` only |
| (b) N | `am_workbench`, `am_chat`, `am_turn_sweep` | `am_chat` | `am_turn_sweep`, `am_workbench` |
| (c) owner | n/a, `am_owner` redefines `ams_owner` | n/a | `am_owner`, `am_turn_fn`, `am_chat`, `am_turn_sweep`, `am_workbench` |
| (d) chat push | cells referencing `ams_chatbot` | n/a | `am_turn_sweep`, `am_workbench` |

`am_turn_fn` reruns only in (c). It refs `ams_settings` (a plain function, `type` `function`, `is get_threshold` False) and no state object: `am_turn_fn state refs: []`. `am_workbench` refs `get_threshold`, `get_turns`, `ams_session_id`, `ams_owner`, `ams_chatbot`, so it is the only cell on every path. `ctx.graph.cycles` is the empty set. Chain, no cycle: `am_owner` -> `am_turn_fn` -> `am_chat` -> `am_turn_sweep` -> `am_workbench`.

`_runtime/runtime.py:1605-1624` (`_find_cells_for_state`): skips `cid == setter_cell_id` unless `allow_self_loops`, then for each ref tests `ref in self.globals and self.globals[ref] is state`. A cell whose only relevant ref is `ams_settings` (a function object) does not match. A ref to `set_*` (a `SetFunctor`) does not match either.

**3. Probes.** Each step was its own call.

| State | `ams_session_id` | `len(ams_chatbot.value)` | id of `ams_chatbot` / `ams_turn` | statuses | workbench |
|---|---|---|---|---|---|
| baseline | `tom-7450c3c3c3b2` | 0 | ...651088 / ...962336 | all idle | "at 0.30" x3, "Recall for ownerId Tom" |
| threshold 0.45 | `tom-7450c3c3c3b2` | 0 | unchanged | `am_turn_fn` idle, `am_chat` idle | "at 0.45" x3 |
| threshold 0.3 | `tom-7450c3c3c3b2` | 0 | unchanged | idle | "at 0.30" x3 |
| owner Priya | `priya-200a6c4c8acc` | 0 | both changed | idle | "Recall for ownerId Priya", "The same search as ownerId Tom" |
| owner Tom | `tom-d108b315a38c` | 0 | both changed | idle | "Recall for ownerId Tom", "The same search as ownerId Priya" |

The builder's failed threshold probe now passes: session, chat object, and turn function are untouched by a slider move. Not verified: that a non-empty history survives, because no chat turn may run. The graph argument covers it.

**4. The guard.** `_plugins/ui/_core/ui_element.py:322-336`: `.value` raises only if `execution_context.cell_id == ui_element_registry.get_cell(self._id)` and `setting_element_value` is false. The chat RPC installs `am_chat`'s context (`runtime.py:2229`). `ams_owner` is defined in `am_owner`, so `ams_owner.value` is allowed. `State.__call__` (`state.py:165-166`) is `return self._value`, so `ams_settings()` has no guard. A regex over `am_turn_fn` finds two `.value` reads, both `ams_owner.value`, and no mention of `ams_threshold`, `ams_turns`, or `ams_chatbot`.

**5. Reader's view, top to bottom** (edit mode, as Nick sees it):
1. `am_title`: H1 "Redis Agent Memory", the three principles, status and settings line, docs links. Prose.
2. `am_store_md`: "What long-term memory holds", Seed and Clean up explained. Prose.
3. `am_store_button`: three buttons, no text.
4. `am_store`: two tables (Tom 3, Priya 3) and the extraction note.
5. `am_owner`: "Chat and the data behind each turn" prose, plus the owner dropdown.
6. `am_workbench`: the chat on the left, the search text, bars for Tom and Priya, prompt, session, and request on the right.
7. `am_build_md`: "The Iris calls behind the demo", two lines.
8. `am_connect`: visible client code, then "Agent Memory is healthy."
9. to 11. `am_store_fns`, `am_recall_fns`, `am_prompt_fns`: visible code, no output.
12. `am_turn_fn`: visible code, no output.
13. `am_machinery_md`: "Notebook machinery", one line.
14. to 17. `am_settings`, `am_chat`, `am_turn_sweep`, `am_render`: collapsed hidden cells, no output.

The split is visible: six demo cells, an H2, five code cells, an H2, four hidden cells. The Iris lead-in says what the section is, but misstates its order and its docstring claim (finding 1). The machinery lead-in names a layout that is not there. Mixing inside the Iris section is limited to the one `ams_settings()` line (finding 5). The health line is the client's own output and is correct there (finding 7).

**6. Prose.** Title unchanged; all three goals match `^(\d)\. \*\*([^*]+\.)\*\* \S.*$` (checked live). `am_build_md` and `am_machinery_md`: sentence lengths under 25, serial commas present, no semicolons or exclamation marks, no Slop-floor word, no rhetorical triple, no "you already". Failures are accuracy (finding 1) and term (finding 4). Comments: `am_settings` stale (finding 2). `am_turn_fn` comments are accurate: "a new session each time the customer changes" is true (owner redefines it), and the "Session memory", "Long-term memory", "The prompt" comments match the calls. The `am_chat` comment "ams_turn is the Iris turn above" is true (cell 12 sits above cell 15).

**7. File.** `grep -n "^def am_\|^async def am_" iris_basics.py` lists 17 cells in order (lines 181, 202, 213, 223, 268, 282, 365, 376, 396, 468, 508, 539, 578, 588, 602, 619, 636). `uv run python -m marimo check --strict iris_basics.py` exits 0. `uv run pytest tests/test_voice.py -q`: 8 passed. File not edited.
