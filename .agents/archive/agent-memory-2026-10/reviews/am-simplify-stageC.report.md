# Stage C report: code moves (2026-10-07)

All four edits went through one `cm.get_context()` block (script `am_simplify_C_2.py`): read all four cells first, then `edit_cell` x4, then `run_cell` in the order am_store_fns, am_recall_fns, am_turn_fn, am_turn_sweep. The context exited without a validation error. First kernel call was `help(cm)`. `iris_basics.py` and `tests/` were not edited by hand.

## Cells changed

| Cell | Id | Change |
|---|---|---|
| `am_recall_fns` | ZHCJ | Body replaced verbatim from spec C1: `ams_sweep` removed, `ams_request` and `ams_recall` unchanged. |
| `am_turn_sweep` | Vxnm | Body replaced verbatim from spec C2: `ams_sweep` plus its comment now sit above the three existing `ams_turn_*` lines. |
| `am_store_fns` | emfo | Body replaced verbatim from spec C3: `ams_memories` is one search with `"limit": 100`; `ams_forget` is one bulk delete guarded by `if memory_ids`. |
| `am_turn_fn` | RJQd | One comment line replaced via string replace on the current body (asserted to occur exactly once); every other line untouched. |

`hide_code` unchanged: am_recall_fns False, am_turn_sweep True, am_store_fns False, am_turn_fn False.

## Verification (separate kernel call, `am_simplify_C_3.py`)

- Status and errors: all four cells `idle`, `errors == []`. `am_workbench` also idle, `[]`.
- Graph defs:
  - am_recall_fns: `{ams_request, ams_recall}`
  - am_turn_sweep: `{ams_turn_live, ams_turn_query, ams_sweep, ams_turn_bands}`
  - am_store_fns: `{ams_seeds, ams_memories, ams_seed, ams_forget}`
- `ctx.graph.cycles`: `set()`
- Counts, before edit: `ams_store` Tom 6 / Priya 3; `ams_turn_bands` Tom 6 / Priya 3; `ams_memories("Tom")` 6.
- Counts, after edit: identical (`ams_store` Tom 6 / Priya 3, `ams_turn_bands` Tom 6 / Priya 3, `ams_memories("Tom")` 6). The single-search `limit: 100` version returns the same count as the old page loop.
- `am_workbench` output: contains "ownerId Tom" and "Same search, ownerId Priya"; 11 `ams-row` divs = 6 + 3 bar rows + 2 axis-tick rows, so both owners' bars render.
- `uv run python -m marimo check --strict iris_basics.py`: exit 0, no output.
- Disk greps on `iris_basics.py`:
  - `page_token\|asyncio.gather(\s*\*(\s*ams_client`: no matches.
  - `async def ams_sweep`: one line, 576, after `async def am_turn_sweep(` (564); `def am_recall_fns(` is at 432.
  - New strings present: line 393 `"limit": 100`, line 422 `if memory_ids:`, line 507 the new comment.
- I did not call `ams_seed` or `ams_forget`, send chat messages, or touch any button. The only store calls were the read-only `ams_memories("Tom")` check and the sweep the cell reruns.

## Deviations

- Indentation of the C4 comment. The spec's code block shows the comment indented 8 spaces, but the real line in `ams_turn` is indented 4 (matching the surrounding lines). I replaced only the comment text and kept the existing 4-space indent, as the spec's own instruction ("keep the indentation of the surrounding lines") requires.
- Verification item 4 (bars for both owners) was checked by output HTML row count rather than a screenshot.

## Noticed, not touched

- `async def ams_sweep` is indented in the saved file because marimo wraps the cell body in `async def am_turn_sweep(...)`. A grep anchored at column 0 would miss it.
- `am_turn_sweep` has a comment block directly above `ams_sweep` that describes the three `ams_turn_*` variables. It is placed as the spec dictates.
