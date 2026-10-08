# Agent Memory inline: reviewer brief (2026-10-07, afternoon)

Two reviewers, each with a different lens, review the move described in `C:\Users\NickCooper\projects\demo\redis-iris-basics\.agents\plans\agent-memory-inline-spec.md` (the spec) against the live kernel and the implementer's report at `C:\Users\NickCooper\projects\demo\redis-iris-basics\.agents\reviews\am-inline.report.md`. The implementer has finished when you start. Your prompt names your lens: **static** or **probe**. Read the spec in full first.

## Read-only, with the probes reserved for one reviewer

You do not change the notebook. No `edit_cell`, `create_cell`, `delete_cell`, `move_cell`, no edits to `iris_basics.py`, no edits under `tests/`. The only file you write is your review.

Kernel access follows the spec's "How to run code in the kernel" recipe: jq on PATH, `help(cm)` first, scripts written with the Write tool to `C:\Users\NickCooper\AppData\Local\Temp\am_inline_review_<lens>_<n>.py`, ASCII only. Read `C:\Users\NickCooper\projects\demo\.claude\skills\marimo-pair\SKILL.md` for `marimo._code_mode`.

Allowed kernel actions for both: reading `ctx.cells`, `ctx.graph`, `ctx.globals`, and cell `output.data`; `await ams_memories("Tom")` and `await ams_memories("Priya")` (read-only searches); the exact read-only search expression from the spec's verification script; `inspect.signature(...).bind(...)` on SDK methods without calling them.

**Only the probe reviewer may call `ctx.set_ui_value`.** The two reviewers run at the same time. Static reviewer: while you work, the probe reviewer may have the threshold at 0.45, N at 1, or the owner at Priya for a few seconds, and `ams_session_id` changes on an owner switch by design. Do not report a transient reading of `ams_settings()`, `ams_owner.value`, `ams_session_id`, or the workbench's owner labels as a finding; the final state of those is the probe reviewer's check 8.

Forbidden for both: sending a chat message, pressing or `set_ui_value` on Seed, Refresh, or Clean up, calling `ams_seed`, `ams_forget`, `add_session_event_async`, or any other store write, creating or deleting memories or sessions, installing packages, opening a browser, clicking "Take over" anywhere.

## Checks for both reviewers

1. **Code equals spec.** For `am_prompt_fns`, `am_turn_fn`, `am_turn_sweep`, `am_build_md`: compare `ctx.cells[name].code` with the spec's fenced body, line by line. Whitespace at line ends and a trailing newline do not count; any other difference is a finding. Write both texts to files under Temp and diff them rather than eyeballing.
2. **Disk equals kernel.** Every non-blank line of each of the four cells' live code appears in `C:\Users\NickCooper\projects\demo\redis-iris-basics\iris_basics.py` with four spaces of wrapper indent added. `am_recall_fns`, `ams_recall`, and `ams_remember` appear nowhere in the file.
3. **Cell health.** `am_prompt_fns`, `am_turn_fn`, `am_turn_sweep`, `am_build_md`, `am_workbench`, `am_chat`, `am_store` are `idle` (static markdown may read `stale`) with `errors == []`.
4. **Graph.** `ctx.graph.cells[<am_prompt_fns id>].defs == {"ams_build_system", "ams_query", "ams_request"}`. `am_turn_fn` refs include `ams_client`, `ams_request`, `ams_query`, `ams_build_system`, `ams_settings`, `dt`, `models`, `secrets`, `stream_llm`, `ams_owner` and exclude `ams_recall`, `ams_remember`, `get_threshold`, `get_turns`, `ams_threshold`, `ams_turns`, `ams_chatbot`. `am_turn_sweep` refs include `ams_client` and `ams_request` and exclude `ams_recall`. No cell defines or references `ams_recall` or `ams_remember`, and neither name is in `ctx.globals`. `ctx.graph.cycles` is empty.
5. **End state.** Column 4 order and `hide_code` flags match the spec's "Expected end state". Fifteen `am_*` cells.
6. **The implementer's report.** Does it match what you observe? Did it record deviations? Anything it missed?

## Static lens, in addition

7. **Whole file.** From `C:\Users\NickCooper\projects\demo\redis-iris-basics`: `uv run python -m marimo check --strict iris_basics.py` exits 0, and `uv run pytest tests/test_voice.py -q` passes. Also run `uv run pytest tests/test_notebook.py -q -k "budget or hide_title or chat_cells_own or claude_is_called" 2>&1 | tail -15` and report what passes and fails; failures there may be pre-existing (the suite encodes an older notebook), so for each failure say whether its message names one of the four changed cells. Do not run the full suite.
8. **Voice.** Read the Headlines and titles section and the Slop floor of `C:\Users\NickCooper\projects\demo\.agents\VOICE.md`. Judge the new heading, "The Agent Memory code: client, store, request and prompt, turn", and the unchanged lead-in sentence beneath it against them, and against the 25-word sentence ceiling. Does "One cell per step" still hold with four code cells and a heading that lists four steps? Quote any hit with the rule and a fix in a few words. Do not restyle text the move did not change.
9. **Turn semantics.** Compare the new `ams_turn` body with the old one at `C:\Users\NickCooper\AppData\Local\Temp\iris_basics.before-inline.py` (lines 476 to 512 there, plus the old `ams_remember` at 453 to 461 and `ams_recall` at 436 to 439). Confirm the five keyword arguments of `add_session_event_async` are the ones the helper passed, that the search request is built from the same `ams_query(messages, turns)` text, and that `found.items` replaces `recalled`. Confirm the comments and docstring are byte-identical to before.
10. **The sweep's comment move.** Confirm the two comment lines now sit directly above `ams_turn_live` and that no comment sits above `async def ams_sweep`.
11. **Other columns and files.** `grep -n "ams_recall\|ams_remember\|am_recall_fns" iris_basics.py README.md tests/*.py` prints nothing.

## Probe lens, in addition

Each probe step runs in its own kernel call: a scratchpad script blocks the kernel until it ends, so cells triggered by `set_ui_value` rerun only afterwards. Record values in one call, set in the next, read in the next.

7. **Live search path.** Run the spec's read-only search expression for Priya and Tom at 0.3 with the message "Which foods should I avoid?" and `n=1`. Priya returns the peanut and tree nut memory; Tom returns nothing. Then run `inspect.signature(ams_client.add_session_event_async).bind(...)` with the turn's five keyword arguments and confirm it binds. Confirm `inspect.isasyncgenfunction(ams_turn)`.
8. **Threshold probe.** Record `ams_session_id`, `len(ams_chatbot.value)`, `id(ams_chatbot)`, `id(ams_turn)`, and `ams_settings()`. In a new call, `ctx.set_ui_value(ams_threshold, 0.45)` inside a `cm.get_context()` block. In a new call, read the five values again and the `am_workbench` output: the session id, `id(ams_chatbot)`, and `id(ams_turn)` must be unchanged, `ams_settings()` must read `(0.45, 3)`, and the workbench's `ams-line` style must read `left:45%`. At 0.45 for the preview query "Which foods should I avoid?", no seeded memory of either owner should be at or above the line, so the system prompt block should carry no facts (Tom may show two extracted memories at 0.5; those are pre-existing and above the line by design). In a new call, set it back to 0.3. In a new call, confirm `(0.3, 3)` and `left:30%`.
9. **N probe.** Same shape: record, `ctx.set_ui_value(ams_turns, 1)`, confirm `ams_settings() == (0.3, 1)` with the session id, `id(ams_chatbot)`, and `id(ams_turn)` unchanged, then set back to 3 and confirm `(0.3, 3)`. The sweep reruns on each move (42 searches); allow it to finish before reading.
10. **Owner probe.** `ctx.set_ui_value(ams_owner, ["Priya"])` (a list). In a new call, confirm the workbench says "ownerId Priya" and "Same search, ownerId Tom", that `ams_session_id` starts with "priya-", and that `id(ams_chatbot)` changed (the owner switch is meant to start a new session and a new chat). Set back to `["Tom"]`. Confirm "ownerId Tom" and a "tom-" session id.
11. **Store counts.** `len(await ams_memories("Tom"))` and `len(await ams_memories("Priya"))`, before your first probe and after your last. They must match each other and the implementer's report.

## Report

Write `C:\Users\NickCooper\projects\demo\redis-iris-basics\.agents\reviews\am-inline.<lens>.review.md`:

- **Verdict:** PASS, PASS WITH NOTES, or FAIL. FAIL means a cell differs from the spec in a way that changes behavior or meaning, a probe fails, `marimo check --strict` fails, or the voice test fails.
- **Findings, most severe first**, each tagged high, medium, or low. Each with: what you observed (quote it), why it matters, and a concrete fix (the exact replacement text or code). Prose findings quote the VOICE.md section.
- **Evidence by check**, numbered as above, with the actual values you read.
- **Final state** (probe lens): owner, threshold, N, chat length, `ams_session_id`, store counts, confirming you restored what you touched.

Then return the structured result the coordinator asked for.
