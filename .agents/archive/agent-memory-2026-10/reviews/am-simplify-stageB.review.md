# Stage B review: workbench, bars, tables

**Verdict: PASS WITH NOTES.** No cell differs from the spec, all cells are healthy, `marimo check --strict` and `tests/test_voice.py` pass. Notes are low severity and none is a Stage B defect.

Deviation from the brief: I did not run the threshold or owner probes (the Stage C reviewer shares the kernel). Threshold behavior is verified by inspection of `am_workbench` and `am_render` plus the live output, below.

## Findings (most severe first)

### 1. Low: the session table's `role` column can only ever say "user"
- Observed: `ams_remember` (iris_basics.py:460-468) writes only `models.MessageRole.USER` events, and nothing writes the assistant reply to session memory. `am_workbench` builds the table with `{"role": e.role.value, "text": ...}` (line 323), so every row reads `user`.
- Why it matters: a constant column is noise, and it looks like the table is showing the whole conversation when it shows only what the app chose to write. The spec dictates this body, so the implementer was right to copy it. This is a spec-level call.
- Fix (optional, spec change): drop the `role` key and show `[{"message": e.content[0].text} ...]`, or rename the heading to `**Session `{id}`**, user messages ({n})`. I would leave it unless Nick wants the table to teach that session memory holds the user's events only.

### 2. Low: the `marimo-table` has the column explorer and chart builder switched on
- Observed, decoded from `data-*` attributes of both tables in the `am_store` output: `data-show-column-explorer = True`, `data-show-chart-builder = True`, and `data-show-page-size-selector = True` on the 6-row Tom table but False on the 3-row Priya table.
- Why it matters: for a 6-row, 2-column table these are chrome the spec set out to remove. The page-size selector appearing on one table and not the other makes the side-by-side pair look uneven. I did not verify how this renders without a browser.
- Fix (optional): I did not check whether the installed `mo.ui.table` exposes parameters for the explorer, chart builder, or page-size selector. Run `help(mo.ui.table)`; if they exist, set them False on both tables, otherwise leave it.

### 3. Info: at 0.30 the system prompt carries a fact that answers the lesson backwards
- Observed: at the live threshold 0.3, Tom's `User has no known allergies` (0.30, an extracted memory, not a seed) clears the line, so the prompt for "Which foods should I avoid?" reads `What you remember about Tom: - User has no known allergies`.
- Why it matters: principle 1 says the query recalls Priya's peanut allergy. Priya's memory is the 0.35 bar, which is also solid at 0.3, so the principle still shows. This is store content, not Stage B code. The implementer noted the extracted Tom memories already.

## Evidence by check

1. **Code equals spec.** I parsed the three Stage B code blocks out of the spec file and compared with `ctx.cells[name].code` after stripping line-end whitespace: `am_store`, `am_render`, `am_workbench` all `code==spec True`.
2. **Disk equals kernel.** The notebook indents cell bodies four spaces inside each `def`. After dedenting the disk file, every non-blank live line is present, and each cell's whole body is a contiguous block on disk, for all three cells. (My first pass compared without dedenting and reported false misses; the second pass corrected that.)
3. **Cell health.** `am_store`, `am_render`, `am_workbench`: status `idle`, `errors == []`, `hide_code` True. Re-read in a later call: same.
4. **End state.** Column 4 order from `ctx.cells`: `am_title, am_store_button, am_store, am_owner, am_workbench, am_build_md, am_connect, am_store_fns, am_recall_fns, am_prompt_fns, am_turn_fn, am_machinery_md, am_settings, am_chat, am_turn_sweep, am_render`, matching the spec. `am_store_md` is gone from the file. Store `{'Tom': 6, 'Priya': 3}`, `ams_turn_bands` the same, chat length 0. Graph cycles: `set()`. I did not check Stage C/A `hide_code` flags beyond Stage B.
5. **Whole file.** `uv run python -m marimo check --strict iris_basics.py`: exit 0. `uv run pytest tests/test_voice.py -q`: `8 passed`.
6. **Cross-stage seams.**
   - "five minutes" appears once (line 213, store prose). "extracts" appears once, the same line.
   - "created (UTC)": 0 hits. "ams-caption", "How the bars are measured", "Request as sent", "Highest similarityThreshold", "Notebook machinery", "What long-term memory holds": 0 hits. "public preview": one hit, line 130, which is the LangCache column, not Agent Memory.
   - `am_build_md` heading reads `## The Agent Memory code: client, store, search, prompt, turn`; the lead-in promises no sweep.
   - `am_store_button` (lines 203-205) is the only place with `run_button`.
7. **Implementer's report.** It matches what I observed: bodies verbatim, idle/no errors, `show_data_types=False` at two lines (328, 649), `{Tom: 6, Priya: 3}`, no deviations. Nothing missed that affects behavior. One imprecision: it says "first kernel call was `help(cm)`" and reports a jq write error from piping to `head`; harmless.

### Stage B specific
- **`am_workbench` output HTML** (decoded and counted): `<details` 0, `accordion` 0, `ams-caption` 0; `ownerId Tom` 1, `Same search, ownerId Priya` 1, `System prompt` 1, `Search request` 1. Both owners' bars present: Tom 6 rows, Priya 3 rows. Absent: "How the bars are measured", "Highest similarityThreshold", "Memories above the line", "Request as sent".
- **`am_store` output HTML:** two `marimo-table` elements. After `html.unescape` and `json.loads` on `data-*`: both show `data-show-data-types = False`, `data-pagination = False`, `data-show-search = False`, `data-show-download = False`, `data-total-columns = 2`. Rows hold only `memory` and `type` keys. "created (UTC)" 0, "extracts memories" 0, "ownerId: Tom" 1, "ownerId: Priya" 1.
- **Threshold behavior by inspection.** `_recalled` and `ams_bars` both use `round(band, 2) >= round(threshold, 2)`, so the solid bars and the prompt facts cannot disagree. Live at 0.3: the line sits at `left:30%` (only position in the HTML), 2 solid bars (Tom `User has no known allergies` 0.30, Priya `I'm allergic to peanuts and tree nuts` 0.35) and 7 `ams-bar-below`. The system prompt carries exactly Tom's one 0.30 fact, and the request JSON shows `"similarityThreshold": 0.3`. At 0.45 the bands read from `ams_turn_bands` give Tom max 0.30 and Priya max 0.35, so no memory is at or above the line, `_recalled == []`, and `ams_build_system("Tom", [])` returns `'You are a helpful AI assistant.'` (I called this pure function directly: `'You are a helpful AI assistant.'`). `ams_turn_bands` does not depend on the threshold (`am_turn_sweep` refs `get_turns`, not `get_threshold`), so the sweep, session id, and chat object are untouched by a slider move; only `am_workbench` reruns. I did not move the slider to confirm this live.
- **Owner behavior by inspection.** `_owner = ams_owner.value`; the other owner loop is `for _other in (o for o in ams_turn_bands if o != _owner)`, so switching to Priya gives `ownerId Priya` and `Same search, ownerId Tom`. `ams_session_id` is rebuilt in `am_turn_fn` from `ams_owner.value.lower()` (prefix `priya-`), an existing by-design reset. Not probed live.
- **Session table (`_turn_done`).** Unreachable with an empty chat: `bool([])` is False, so the `and` short-circuits before `[-1]`. With a finished turn it fetches `get_session_memory_async(session_id=ams_session_id)` and shows `role` and `content[0].text` per event; `ams_remember` writes `content=[{"text": text}]` with role USER, so reading `.content[0].text` and `.role.value` is consistent. See finding 1. Not exercised live (needs a chat message).

## Final state
Owner Tom, threshold 0.3 (slider value and `get_threshold()` both 0.3), N = 1, chat length 0, `ams_session_id` `tom-c8cb339d501c`, store Tom 6 / Priya 3. I changed nothing: no probes, no edits, no store writes. The only scratch files are `am_review_B_1.py`, `_2.py`, `_3.py` and two HTML dumps in `%LOCALAPPDATA%\Temp`.
