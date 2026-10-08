# Agent Memory column redesign: marimo-mechanics critique (2026-10-07)

Reviewed against marimo 0.24.2 source in `.venv/Lib/site-packages/marimo` and the live kernel at http://127.0.0.1:2718/ (read-only scratchpad). Python 3.13.14. Nothing in the notebook was changed. Ranked most severe first.

## 1. `@mo.cache` on `ams_sweep` in the same cell as `ams_recall` raises TypeError on the first call

Claim: "`ams_sweep`, decorated with `@mo.cache` ... `am_recall_fns` (code): `ams_query`, `ams_recall`, `ams_sweep`, `ams_bars`."

Verdict: REFUTED for the proposed layout. CONFIRMED only when `ams_sweep` lives in a different cell from `ams_recall`.

Evidence:
- `_save/save.py:242`: defs of the cell holding the cached function are added to `scoped_refs`.
- `_save/hash.py:1211`: at call time `refs = scoped_refs & previous_block.visitor.refs`, so `ams_recall` is re-collected as a content ref.
- `_runtime/primitives.py:231-253`: `is_pure_function` walks the real graph; `ams_recall` refs `ams_client`, which is not callable, so `ams_recall` is impure. Live: `is_pure_function("ams_recall", ..., real graph) -> False`, `callable(ams_client) -> False`.
- `_save/hash.py:835-836`: an impure `__main__` object stays in `refs`.
- `_save/hash.py:1083-1097`: execution-path hashing covers only ancestors of the cell; a cell is not its own ancestor, so a same-cell `ams_recall` is never execution-hashed.
- `_save/hash.py:473-527`: leftover scoped refs fall back to pickling their transitive closure, which includes `ams_client`.
- Live emulation (base block rebuilt against the real graph with cell id `Wvet`, the cell that defines `ams_recall`):
  ```
  == SAME CELL as ams_recall Wvet
    cache_type Deferred | content_refs ['asyncio'] | execution_refs [] | scoped ['*owner','*query','ams_build_system','ams_query','ams_recall','ams_remember_turn']
    RAISED TypeError: Content addressed hash could not be utilized. Try defining the dependent sections in a separate cell. The unhashable arguments/ references are: ams_client: <class 'redis_agent_memory.sdk.AgentMemory'> (cannot pickle '_thread.RLock' object)
  == SEPARATE downstream cell yUGJ
    cache_type Deferred | content_refs ['asyncio'] | execution_refs ['ams_recall'] | scoped ['*owner','*query']
    first 0.824s second 0.001s hits 1 misses 1 {'I have a cat named Miso': 0.4, "I'm allergic to peanuts and tree nuts": 0.2, "I'm planning a canoe trip in Maine in June": 0.05}
  ```
  A plain scratchpad probe (no graph) also worked, first 0.707 s, second 0.001 s, which is why a naive test misses this.

Fix: give `ams_sweep` its own cell downstream of `ams_recall` (14 cells, not 13), or move `ams_recall` into `am_prompt_fns` and keep `ams_sweep` in `am_recall_fns`. Do not define `ams_sweep` inside `am_recall_panel`; that cell reruns on every slider move.

Second defect in the same claim: the cache key is the code of the defining cells (`hash.py:945-990`, `:1083`), not the store. After Clean up, Seed, or background extraction, `ams_sweep(query, owner)` returns the old band dict until the kernel restarts or `am_recall_fns` is edited. Fix: pass a store fingerprint as an argument, e.g. `tuple(sorted(m.id for m in ams_store[owner]))`, so the key changes when the store does.

## 2. The chat cell does not rerun on an owner change, so session and history are shared across customers

Claim (code comment in the proposed `am_chat`): "`ams_session_id = secrets.token_hex(8)  # this cell reruns on a customer switch, so each customer gets a new session`."

Verdict: REFUTED. It contradicts the proposal's own mechanism paragraph.

Evidence: `_runtime/runtime.py:2082-2086` subtracts `get_defining_cells(name)` from the referring set ("never rerun the cell that created the name"). `ams_owner` is defined in `am_chat`, so a dropdown change reruns the panels only. `ams_session_id`, `ams_chatbot` and its history survive. Consequences:
- Tom's turns stay in `messages`; `ams_turn` sends them to the LLM as Priya's history. "Switch to Priya and ask again" then answers from the transcript even though recall is scoped, which defeats the goal-2 demo.
- Both customers write to one session id; panel 3 shows a mixed session.
- `tests/test_notebook.py:391-404` (`test_chat_cells_own_their_controls`) forbids the chat cell from reading a UI element defined elsewhere, so the test forces this placement. The test and "new session per customer" cannot both hold.

Fix: pick one. (a) Keep `ams_owner` in a separate cell so a switch reruns `am_chat` (today's behaviour) and amend the test to allow the owner dropdown as the one foreign control. (b) Keep it in `am_chat`, derive the session id per owner inside `ams_turn` (`f"{ams_session_base}-{ams_owner.value}"`), and say in the title that the transcript is shared. (a) is the one that keeps goal 2 visible. Remove the comment either way.

## 3. `if`/`else` placeholders keep defs only if both branches assign

Claim: "They use `if`/`else`, not `mo.stop`, so they never remove their defs from downstream cells."

Verdict: CONFIRMED for the mechanism, UNVERIFIED for the sketches (no panel code is given).

Evidence: `_runtime/runtime.py:1516-1518` deletes the defs of every cell in `cells_to_run` before the run; `_runtime/runner/cell_runner.py:920-929` marks descendants of a stopped cell cancelled so they never re-assign; `hooks_on_finish.py:52-59` emits the "ancestor was stopped" error. A cell that runs to completion keeps its defs. But marimo counts a name assigned in one branch as a def; if the placeholder branch does not assign `ams_recalled`, panel 3 gets NameError. Fix: assign `ams_recalled = []` in the placeholder branch.

## 4. Two value pushes per chat turn, none mid-stream

Claim: "panels referencing `ams_chatbot.value` rerun when a chat completes"; mid-stream push frequency "still unverified".

Verdict: CONFIRMED for completion; frontend dedupe UNVERIFIED.

Evidence: the Python side pushes once, at stream end: `_plugins/ui/_impl/chat/chat.py:387-390` -> `:564-565` -> `:445-468` enqueues an `UpdateUIElementCommand`. Mid-stream chunks go out as `stream_chunk` messages (`chat.py:298-314`), not value updates. The frontend bundle (`_static/assets/chat-ui-DGiVDzNh.js`) also calls `e.setValue(t)` in `onFinish`, and on message delete and clear, not on submit. `runtime.py:2039-2050` does not dedupe equal values. Expect two panel reruns per completed turn unless the frontend drops the second. Panel 2 is uncached, so budget 2 searches x 2 reruns per turn plus two session reads. Count runs of `am_recall_panel` during the build, as the proposal says.

## 5. Slider values are on the 0.05 grid, but Python's grid needs the `round`

Claim: "The slider is `step=0.05`, aligned to the grid, so the recall set at any slider position is exactly the memories whose band is at or above it."

Verdict: CONFIRMED from source; live movement UNVERIFIED (set_ui_value was off limits and the live slider sat at its default 0.3).

Evidence: Python converts with `float(value)` and no rounding (`_plugins/ui/_impl/input.py:359-362`). The frontend bundle (`index-4n22QjCD.js`) contains Radix's decimal-count rounding, `(String(e).split('.')[1]||'').length` and `Math.round(e*n)/n`, so a step of 0.05 yields two-decimal doubles. On the Python side `0.05*i` is off-grid for i = 3, 6, 7, 12 (`0.15000000000000002`, `0.30000000000000004`, `0.35000000000000003`, `0.6000000000000001`); the proposal's `round(0.05*i, 2)` is required and present. Cheap insurance: compare `round(band, 2) >= round(ams_threshold.value, 2)`, or compare in integer hundredths.

## 6. run_button resets after the referencing cells run

Claim: "A `run_button` resets to False after its referencing cells run, so one cell can act on the pressed button and then list."

Verdict: CONFIRMED.

Evidence: `_plugins/ui/_impl/run_button.py:91-97` converts the frontend counter to True; `:99-120` `_on_update_completion` sets `_value = False`; `_runtime/runtime.py:2111-2123` calls it after `_run_cells` returns, exception or not. Live: `ams_seed_button.value` is `False` while `_value_frontend` is `1` (pressed earlier this session). Any rerun of `am_store` for another reason sees False for all three buttons. Caveat: lazy kernels skip the reset (`run_button.py:109-117`); this notebook is autorun.

## 7. UI changes rerun referring cells and their descendants, never the defining cell

Claim 1 of the brief.

Verdict: CONFIRMED.

Evidence: `_runtime/runtime.py:2084-2086` (referring minus defining), `:2111-2112` runs them; `_runtime/runner/cell_runner.py:238-241` expands roots with `transitive_closure`. Finding 2 is the direct cost of this rule.

## 8. `mo.ui.chat(ams_turn, ...)` with a two-argument async generator

Claim 5 of the brief.

Verdict: CONFIRMED.

Evidence: `chat.py:521-528` calls `self._model(messages, args.config)` when the signature has more than one parameter; `:530-535` and `:350-354` stream an async generator; `:387-390` appends the assistant message. A cell-level `async def` is an ordinary def to marimo. The test helper `_chat_model` (`tests/test_notebook.py:145-148`) requires `mo.ui.chat(<Name>, ...)` with a `FunctionDef` of that name in the cell body; the proposal's shape satisfies it. The current `ams_chat(...)` call shape does not (`chat.args[0].id` raises AttributeError).

## 9. Top-level `await` inside a dict comprehension and an f-string

Claim 6 of the brief.

Verdict: CONFIRMED.

Evidence: cells compile with `ast.PyCF_ALLOW_TOP_LEVEL_AWAIT` (`_ast/compiler.py:357-363`); `_ast/cell.py:138-141, 300-301` marks the cell coroutine from `CO_COROUTINE`. Venv Python 3.13.14:
```
dictcomp_await compiles; CO_COROUTINE = True
fstring_await compiles; CO_COROUTINE = True
```

## 10. Proposed dependency graph

Claim 9 of the brief.

Verdict: CONFIRMED, no cycle.

Trace (defs -> consumers): `am_store_button` {3 buttons} -> `am_store`; `am_store_fns` {AMS_SEEDS, ams_memories, ams_seed, ams_forget} -> `am_store`, `am_chat`, `am_owner_panel`; `am_store` {ams_store} -> `am_recall_panel`; `am_recall_fns` {ams_query, ams_recall, ams_sweep, ams_bars} -> `am_chat`, panels; `am_prompt_fns` {ams_remember, ams_build_system} -> `am_chat`, `am_prompt_panel`; `am_chat` {ams_owner, ams_threshold, ams_turns, ams_session_id, ams_turn, ams_chatbot} -> panels; `am_recall_panel` {ams_recalled} -> `am_prompt_panel`. No panel defines anything a fns cell or `am_chat` reads. `AsyncGenerator`, `ai`, `secrets`, `stream_llm`, `USER_ID` exist in setup or `am_connect`. Loose ends: `ams_memory_table` is not assigned to any cell in the layout table (put it in `am_store_fns`); after finding 1, `ams_sweep` needs its own cell.

## Not checked

- Whether a `mo.cache` object survives a rerun of its defining cell with the same name (`save.py:258` `create_or_reconfigure(name)` suggests yes). Irrelevant once `ams_sweep` sits in a cell that reruns only on edit.
- Column budget and voice tests; out of scope here.
