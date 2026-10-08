# Agent Memory polish: prose implementer report (2026-10-07)

## What changed (kernel, scripts `am_polish_prose_1..6.py` in Temp)

One `get_context()` block, every cell read first, nine cells edited:

| Cell | Id | Change |
|---|---|---|
| `redis_client` | MJUe | Step 1: `USER_ID` removed, `AI_MODEL` kept. |
| `am_title` | Xref | Step 2 text. |
| `am_store_button` | SFPL | Step 3 text. |
| `am_owner` | RGSE | Step 4 text. |
| `am_workbench` | Kclp | Step 5 body (reran through `am_owner`). |
| `am_build_md` | emfo | Step 6 text. |
| `am_machinery_md` | ROlb | Step 7 text. |
| `rs_title` | bkHC | Single replace `ANTHROPIC_API_KEY` to `LLM_API_KEY`, asserted exactly one occurrence. |
| `lc_title` | lEQa | Same, asserted one occurrence. |

Run order followed step 9.

## Verification

- `redis_client`, `am_title`, `am_store_button`, `am_owner`, `am_build_md`, `am_machinery_md`, `rs_title`, `lc_title`, `am_workbench`, `am_turn_sweep`, `am_store`: all `idle`, `errors == []`. `ctx.graph.cycles == set()`.
- `am_workbench` output, with `data-html` decoded and tags and markdown marks stripped, contains all eight expected strings. (A first pass failed on four because bold tags split the labels; stripping tags without a space fixed that, so it was my search, not the output.)
- Disk: `grep USER_ID|ANTHROPIC_API_KEY|in preview on Redis Cloud` finds only line 156 (`cr_title`, Context Retriever keeps its wording).
- `marimo check --strict iris_basics.py`: exit 0. `tests/test_voice.py`: 8 passed.
- Column order, final: `am_title, am_store_button, am_store, am_owner, am_workbench, am_build_md, am_connect, am_store_fns, am_prompt_fns, am_turn_fn, am_next_md, am_machinery_md, am_settings, am_chat, am_turn_sweep, am_render` (16). `am_next_md` `hide_code` True, `idle`, no errors. On disk `def am_next_md` (line 527) follows `def am_turn_fn` (line 475).

## Deviations

- The spec says "eight" cells; it is nine. All nine edited.
- `am_next_md` (IHVC) was back at the end of column 0, after `llm_client`, when I started, despite the code wave's move. I moved it with `move_cell("am_next_md", after="am_turn_fn")` in a separate context after the run_cells finished. That alone worked: the order read back correctly and the disk order matches. The `edit_cell(..., column=4)` fallback was not needed. Lesson for later edits: a later edit batch can drop the cell back, so re-check its position after any further batch.
- Rejected interrupt: my first move attempt was interrupted by the user, then re-run on the coordinator's instruction. No state was affected.

## Noticed, not touched

- `am_workbench` references `ams_turns`, which is a name defined in `am_chat` as `ams_turns`; resolved fine (no errors).
- The store, sessions, and model were not touched.
