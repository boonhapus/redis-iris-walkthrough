# Stage B report: workbench, bars, tables

## Cells changed (all via one `cm` context, bodies verbatim from spec; hide_code untouched)
- `am_store` (id Kclp): B1 body. Trailing extraction paragraph removed.
- `am_render` (id ecfG): B2 body. `.ams-caption` caption and CSS gone; memory table drops "created (UTC)" and passes `show_data_types=False`.
- `am_workbench` (id Evqy): B3 body. Shorter labels, "How the bars are measured" accordion gone, session table drops time column and passes `show_data_types=False`.

Scripts: `%LOCALAPPDATA%\Temp\am_simplify_B_1.py` (edit + run_cell x3), `am_simplify_B_2.py` (verify). First kernel call was `help(cm)` with jq on PATH (it printed a harmless `jq: error: writing output failed` because I piped to `head`).

## Verification (separate kernel call)
- am_store, am_render, am_workbench: status `idle`, errors `[]`.
- am_workbench output has "ownerId Tom", "Same search, ownerId Priya", "System prompt", "Search request"; lacks "How the bars are measured", "Highest similarityThreshold", "Memories above the line", "Request as sent".
- am_store output has "ownerId: Tom (6)" and "ownerId: Priya (3)"; lacks "extracts memories"; raw HTML lacks "created (UTC)".
- `ams_store` = {Tom: 6, Priya: 3}; `ams_turn_bands` = {Tom: 6, Priya: 3}.
- Workbench excerpt: `Search text, the first starter message until you send one: Which foods should I avoid? ownerId Tom ... User has no known allergies 0.30 ...`
- Disk grep `ams-caption|How the bars are measured|Request as sent|created (UTC)` in iris_basics.py: no matches. `show_data_types=False`: 2 matches (lines 328, 649).

## Deviations
None.

## Noticed, not touched
- The Tom bars list "User has no known allergies" and "User does not have any cats" at the top (0.30, 0.15). These are extracted memories already in the store (Tom has 6, three seeded); not a Stage B concern.
- am_render has no output (function defs only), as expected.
