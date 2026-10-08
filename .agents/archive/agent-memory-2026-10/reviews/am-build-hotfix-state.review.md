# Agent Memory hotfix (item 13, `mo.state` for the sliders), independent review (2026-10-07)

**Verdict: PASS WITH NOTES**

No blocker. The hotfix does what it claims. The one thing I could not exercise is a real chat turn, so the original failure is verified by source reading and graph inspection, not by a live send. Nick should send one message.

## Findings, most severe first

### 1. (Medium, test debt, expected) `test_no_forbidden_marimo_calls` now fails on `mo.state`
`uv run pytest tests -q --ignore=tests/live_smoke.py`: 20 failed, 43 passed. `tests/test_safety.py::test_no_forbidden_marimo_calls` fails with `mo.state is not used in this notebook` (`FORBIDDEN_CALLS = {"mo.state"}`, test_safety.py:33). That is the only failure this hotfix causes; `grep` finds no other test that mentions `mo.state`, `get_threshold`, or `ams_*` sliders. I cannot separate the other 19 from the pre-hotfix baseline (no baseline run was available), but the ones I read (`test_chat_cells_own_their_controls` expects `lc_chat`/`rs_chat`; `test_column_budgets` etc.) fail on the old v1 layout, not on this change.
Fix: Nick's planned test rewrite should drop `mo.state` from `FORBIDDEN_CALLS`, or scope the ban to columns 1 to 3.

### 2. (Low, file) 18 of 23 `@app.cell` decorators lack `column=`
`iris_basics.py:483-484` shows the new cell as `@app.cell(hide_code=True)` / `def am_settings(mo):` with no `column=4`. Only five decorators carry `column=` (lines 7, 87, 117, 149, 180: the first cell of each column). Marimo itself loads this layout correctly (live: `am_title` is column 4 and the 17 following cells belong to it until the file's end), so the column is right in the kernel. If the spec's step 8 restamp is still wanted: `@app.cell` to `@app.cell(column=4)` and `@app.cell(hide_code=True)` to `@app.cell(column=4, hide_code=True)` on the 16 non-first column-4 cells (`am_settings` included). `uv run python -m marimo check --strict iris_basics.py` exits 0 as is.

### 3. (Low, drift) Probe placeholder wording differs from spec item 7
`iris_basics.py:405` and the live `am_probe_panel` render `_Type a search above and press Enter._`. Spec post-review item 7 says `_Enter a search above and press Enter._`. Not caused by the hotfix. Fix: change one of them; "Enter a search ... press Enter" repeats the word, so I would amend the spec to match the code.

### 4. (Info) `rs_title`, `lc_title`, `cr_title` show status `stale`
Other columns' title cells, not touched by this change; zero errors. They are `hide_code` markdown and were already stale-looking after the server relaunch. No action unless Nick sees them missing in the browser.

## Evidence by check

**1. Live code matches item 13.** `am_settings` is cell 11 of the 17 in the column, directly after `am_owner` (10) and before `am_chat` (12), `hide_code=True`, code is exactly the two `mo.state` lines plus a comment. `am_chat` takes `value=get_threshold()` / `on_change=set_threshold` and `value=get_turns()` / `on_change=set_turns`. `ams_turn` calls `ams_query(messages, get_turns())` and `round(get_threshold(), 2)`. `am_turn_sweep` uses `get_turns()`; `am_probe_panel` and `am_prompt_panel` use `get_threshold()`; `am_prompt_panel` also uses `get_turns()`. Regex over all 17 column cells for `ams_(threshold|turns)`: the only hits are the definitions and the `.value`-free references inside `am_chat`. No cell reads `.value` of either slider. `am_owner` prose contains no "resets the sliders". `am_title` contains no "hits". Count: 17 column cells (grouped by the column cfg boundaries at `am_title`; `ctx.cells` reports `column=None` for non-first cells, so I bounded the column by position).

**2. Graph.**
- `am_chat` refs: `AsyncGenerator, ai, ams_build_system, ams_owner, ams_query, ams_recall, ams_remember, get_threshold, get_turns, list, mo, round, secrets, set_threshold, set_turns, str, stream_llm`. `ams_owner` is the only UI element from another cell.
- `am_turn_sweep` refs `get_turns`, not `get_threshold`. `am_probe_panel` refs `get_threshold`. `am_prompt_panel` refs both.
- `ctx.graph.cycles` is the empty set. Every column cell has `errs 0`.
- Rerun semantics: `runtime.py:2025-2028` wraps `component._update(value)` (which fires `on_change`) in `_install_execution_context(ui_element_registry.get_cell(object_id), setting_element_value=True)`, i.e. the slider's defining cell, `am_chat`. `SetFunctor.__call__` (`_runtime/state.py:175-185`) calls `register_state_update`, which records `ctx.execution_context.cell_id` as the setter cell (`runtime.py:1576-1578`). `resolve_state_updates` (`cell_runner.py:322-336`) skips `cid == setter_cell_id` unless `allow_self_loops` (it is `False`, confirmed live). So a threshold move reruns `am_probe_panel` and `am_prompt_panel`; a turns move reruns `am_turn_sweep` and `am_prompt_panel` (and `am_prompt_panel` again after `am_turn_sweep` redefines its refs; the "about two" in the spec's stage 4 step 7). `am_chat` is excluded in both cases. A cell is matched via `self.glbls[ref] is state`, and `get_threshold` is the `State` object (`State.__call__` is the getter), so refs to `set_*` (a `SetFunctor`) trigger nothing.

**3. Live probe** (each step its own call; Nick's chat was empty throughout, so the stronger non-empty check was not available):

| State | `ams_session_id` | `len(ams_chatbot.value)` | `get_threshold()` | `get_turns()` | probe panel |
|---|---|---|---|---|---|
| before | `bdefffcf061d628d` | 0 | 0.3 | 1 | "slider at 0.30" |
| `set_ui_value(ams_threshold, 0.45)` | `bdefffcf061d628d` | 0 | 0.45 | 1 | "slider at 0.45" |
| restored 0.3 | `bdefffcf061d628d` | 0 | 0.3 | 1 | "slider at 0.30" |

`am_chat` stayed `idle` throughout and the session id did not change, so it did not rerun. Probe text was "How many pets do I have", owner Tom. Final state equals the initial state.
Not verified: that a non-empty history survives. By the graph argument above it does, but only a send, then a slider move, then a read would show it.

**4. The guard.** `_plugins/ui/_core/ui_element.py:322-336`: `UIElement.value` raises only when `execution_context.cell_id == ui_element_registry.get_cell(self._id)` and `not setting_element_value`. `State.__call__` (`state.py:165-166`) is `return self._value` with no context check, so `get_threshold()` inside the chat RPC (cell `am_chat` installed, `runtime.py:2229`) is legal. `ams_owner.value` in `ams_turn` is also legal: `ams_owner` belongs to `am_owner`, not `am_chat`. Initial values: live `get_threshold()` 0.3 and `get_turns()` 1.

**5. Prose and regressions.** `am_owner`: "Switching customers starts a new conversation." is accurate (owner is upstream of `am_chat`, so a switch reruns it: new `ams_session_id`, empty history; sliders are rebuilt from the getters and keep their positions, which the prose no longer denies). The `am_settings` comment is accurate: reading `.value` in the defining cell is the guard above, and defining the sliders in another cell would make `am_chat` ref them and rerun on every move. Spot-checked post-review items 1 to 12 against live/disk code: 1, 2 (`Seed`), 3 (`threshold = round(threshold, 2)`), 4, 5 (all five strings), 8 (goal 1, goal 3 wording), 10 (`mo.vstack` per owner), 11 (`min-width: 0`, `white-space: nowrap`), 12 (fourth starter prompt present) match. Item 6 and 7: prose ends "Press Enter to run the search." not rechecked beyond the placeholder, which differs (finding 3). Item 9 untouched.

**6. File.** `iris_basics.py:483-484` has the new cell on disk and its body matches the live code. Decorator: `@app.cell(hide_code=True)`, no `column=4` (finding 2). `@app.cell` lines without `column=`: 18 of 23. `uv run python -m marimo check --strict iris_basics.py`: exit 0.

**7. Other bites.**
- `value=get_threshold()` does make `am_chat` a reader of the state, but only a setter call from a different cell reruns it, and no other cell calls either setter. Verified live (session id unchanged).
- `am_prompt_panel` still gates the session read: `_turn_done = bool(ams_chatbot.value) and ams_chatbot.value[-1].role == "assistant"`, and `get_session_memory_async` is only in the `else` branch.
- `mo.state` survives a rerun of `am_chat` but not of `am_settings`: editing or rerunning `am_settings` resets both sliders to 0.3 and 1, and since `am_chat` refs the getters it reruns and clears the history. Normal marimo behaviour; only matters if Nick edits that cell.
- The state is process-global to the kernel, so two browser tabs on the same notebook would share slider values. Irrelevant for this demo.
- Dragging a slider mid-turn does not change that turn: `ams_turn` reads the getters once at the start.

## Not done
No chat message was sent, no cell edited, no browser opened. I did not run `tests/live_smoke.py`.
