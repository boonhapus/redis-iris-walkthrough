# Agent Memory simplification: reviewer brief (2026-10-07)

One reviewer per implementation stage. You review the stage named in your prompt against `C:\Users\NickCooper\projects\demo\redis-iris-basics\.agents\plans\agent-memory-simplify-spec.md` (the spec) and the implementer's report at `C:\Users\NickCooper\projects\demo\redis-iris-basics\.agents\reviews\am-simplify-stage<X>.report.md`. All three stages have finished when you start, so the whole-file checks are meaningful.

## Read-only, with two allowed probes

You do not change the notebook. No `edit_cell`, `create_cell`, `delete_cell`, `move_cell`, no edits to `iris_basics.py`, no edits under `tests/`. The only file you write is your review.

Kernel access follows the spec's "How to run code in the kernel" recipe: jq on PATH, `help(cm)` first, scripts written with the Write tool to `C:\Users\NickCooper\AppData\Local\Temp\am_review_<X>_<n>.py`, ASCII only. Read `C:\Users\NickCooper\projects\demo\.claude\skills\marimo-pair\SKILL.md` for `marimo._code_mode`.

Allowed kernel actions: reading `ctx.cells`, `ctx.graph`, `ctx.globals`, cell `output.data`; `await ams_memories("Tom")` (read-only search); and these two probes, each step in its own kernel call because a scratchpad script blocks the kernel until it ends and dependents rerun only afterwards:

- **Threshold probe.** Record `ams_session_id`, `len(ams_chatbot.value)`, `id(ams_chatbot)`. In a new call, `ctx.set_ui_value(ams_threshold, 0.45)` inside a `cm.get_context()` block. In a new call, read the three values again and the `am_workbench` output text: the session id and chat object must be unchanged, and the output must reflect 0.45. In a new call, set it back to 0.3. In a new call, confirm 0.3 is live.
- **Owner probe.** `ctx.set_ui_value(ams_owner, ["Priya"])` (a list). In a new call, confirm `am_workbench` says "ownerId Priya" and "Same search, ownerId Tom" and that `ams_session_id` starts with "priya-". Set back to `["Tom"]`. Confirm. The session id changing on an owner switch is by design.

Forbidden: sending a chat message, pressing or `set_ui_value` on Seed, Refresh, or Clean up, calling `ams_seed` or `ams_forget`, creating or deleting memories or sessions, installing packages, opening a browser, clicking "Take over" anywhere.

## Checks for every reviewer

1. **Code equals spec.** For each cell in your stage, compare `ctx.cells[name].code` with the spec body. Whitespace at line ends and a trailing newline do not count; any other difference is a finding.
2. **Disk equals kernel.** Every line of each cell's live code appears in `C:\Users\NickCooper\projects\demo\redis-iris-basics\iris_basics.py` (the kernel saves after each edit).
3. **Cell health.** Each cell in your stage is `idle` (static markdown may read `stale`, that is a known cosmetic status) with `errors == []`.
4. **End state.** Column 4 order and `hide_code` flags match the spec's "Expected end state" section. Store counts unchanged (Tom 6, Priya 3 at the time of the spec). Chat empty.
5. **Whole file.** From `C:\Users\NickCooper\projects\demo\redis-iris-basics`: `uv run python -m marimo check --strict iris_basics.py` exits 0, and `uv run pytest tests/test_voice.py -q` passes. Do not run the full suite; it encodes an older notebook and 17 failures there are known and unrelated.
6. **Cross-stage seams.** The extraction note ("extracts" or "five minutes") appears once in the column, in the store prose. "created (UTC)" appears nowhere. No cell still says "What long-term memory holds", "Notebook machinery", "How the bars are measured", "Highest similarityThreshold", "Request as sent", or "public preview". `am_build_md`'s heading names the five code cells and the lead-in does not promise a sweep in the search cell.
7. **The implementer's report.** Does it match what you observe? Did it record deviations? Anything it missed?

## Stage-specific checks

**Stage A (prose and headers).** Read `C:\Users\NickCooper\projects\demo\.agents\VOICE.md` in full and the Detect section of `C:\Users\NickCooper\projects\demo\.agents\skills\redis-voice-edit\SKILL.md`, including its Standing rulings. Review every changed `mo.md` string (title, store prose, owner prose, both lead-ins) and every heading against: Headlines and titles (sentence case, no terminal period, no gerund headings, "Redis" prefix at H1), the Slop floor (banned and empty words, synonym cycling, robotic rhythm, staged reveals), the Standing rulings (no invented triplets, no "already in your stack"), and the 25-word sentence ceiling that `tests/test_voice.py` enforces. Confirm the three numbered goals in `am_title` still match `^(\d)\. \*\*([^*]+\.)\*\* \S.*$` and are numbered 1, 2, 3. Confirm "in preview" (not "public preview") against the docs index: `curl -s -A "Mozilla/5.0" https://raw.githubusercontent.com/redis/docs/main/content/develop/ai/context-engine/agent-memory/_index.md | grep -i preview`. Confirm `am_store_md` is gone and that `am_store_button` is still the only column-4 cell that creates a `run_button`. Quote each voice hit with the rule and a fix in a few words. Do not restyle text the stage did not change.

**Stage B (workbench, bars, tables).** In the `am_workbench` output HTML: no accordion element, no "ams-caption" class, both owners' bars present, labels "ownerId Tom", "Same search, ownerId Priya", "System prompt", "Search request". In the `am_store` output HTML: two tables, the table component's data attributes show data types off (look for `show-data-types` with a false value in the `marimo-table` element's attributes, decoding `data-*` attributes rather than stripping tags), and no "created (UTC)" header. Run the threshold probe and confirm the bars' line and the "above the line" memories move with it (at 0.45 for the preview query "Which foods should I avoid?", no Tom memory and no Priya memory should be at or above the line, so the system prompt should carry no facts). Run the owner probe. Confirm the session table code path (`_turn_done`) is unreachable with an empty chat and reads correctly by inspection.

**Stage C (code moves).** `ctx.graph.cells[<am_recall_fns id>].defs == {"ams_recall", "ams_request"}`; `am_turn_sweep` defs include `ams_sweep`; `ctx.graph.cycles` empty; `am_turn_fn` refs include `ams_settings` and exclude `get_threshold`, `get_turns`, `ams_threshold`, `ams_turns`, `ams_chatbot`. Run the threshold probe (the point of the `ams_settings` indirection is that a slider move must not rerun `am_turn_fn`; confirm `am_turn_fn`'s and `am_chat`'s outputs or `id(ams_turn)` are unchanged). Run the owner probe. `await ams_memories("Tom")` returns 6 (or the current store count). Inspect `ams_forget` for the `if memory_ids` guard and `ams_memories` for `"limit": 100` with no page loop. Confirm the `am_turn_fn` comment line changed and nothing else in that cell did (diff against the spec's description; the rest of the body is in `iris_basics.py` git history if needed, or compare with the stage 6 review's description at `.agents/reviews/am-build-stage6.review.md`). Check `asyncio` is still imported in column 0 and still referenced by `am_turn_sweep`.

## Report

Write `C:\Users\NickCooper\projects\demo\redis-iris-basics\.agents\reviews\am-simplify-stage<X>.review.md`:

- **Verdict:** PASS, PASS WITH NOTES, or FAIL. FAIL means a cell differs from the spec in a way that changes behavior or meaning, a probe fails, `marimo check --strict` fails, or the voice test fails.
- **Findings, most severe first.** Each with: what you observed (quote it), why it matters, and a concrete fix (the exact replacement text or code). Prose findings quote the VOICE.md section.
- **Evidence by check**, numbered as above, with the actual values you read.
- **Final state**: owner, threshold, N, chat length, `ams_session_id`, store counts, confirming you restored what you touched.

Then reply with a summary of at most eight lines: verdict, finding count by severity, and the one thing the coordinator should fix first.
