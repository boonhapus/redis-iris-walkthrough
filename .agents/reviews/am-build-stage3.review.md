# Agent Memory build, stage 3 review (2026-10-07)

**Verdict: PASS WITH NOTES.** No blockers. The four stage 3 cells match the spec plus the five follow-up fixes, the column order and `hide_code` flags match the table, the graph is acyclic, and the bars flip at the right threshold. Two stage 4 and customer traps exist (empty probe text, Enter needed to search). The rest is title wording.

Method: read-only dump of the live kernel (`r3_dump.py`), `difflib` of live code against the spec blocks (`r3_chk.py`), scratchpad evaluation of `ams_bars` and the panel output (`r3_chk2.py`), SDK and marimo 0.24.2 source, `curl` of the three docs URLs. No cell edited, no widget set, no chat, no write. The stage 4 verifier was live at the same time, so outputs are a snapshot (probe "Which foods should I avoid?", slider 0.30).

## Findings, ranked

### 1. (Medium) Empty probe text renders "No memories for this owner." under both owners

Evidence: `am_sweep` returns `{owner: {} for owner in ams_store}` when `ams_probe.value.strip()` is empty. `am_probe_panel` then calls `ams_bars({}, 0.3)` per owner, and `ams_bars({}, ...)` renders `No memories for this owner.` (verified). Both owners have three memories. The JSON block under each pane also shows `"text": ""` (verified with `ams_request("", "Tom", 0.3)`), a request the notebook never sent. A customer who clears the box and presses Enter sees a false statement and a fake request.

Fix, in `am_probe_panel`, before the `mo.vstack`:

```python
_t = round(ams_threshold.value, 2)
_empty = not ams_probe.value.strip()
```

and replace the `mo.hstack([...])` argument with `mo.md("_Enter a search above to see the bars._")` when `_empty`, else the existing hstack. Cheaper alternative with one cell change: leave the panel and change nothing in `am_render` but accept the false message. Do the first.

### 2. (Medium, stage 4) `mo.ui.text(debounce=True)` emits only on Enter or blur, and nothing on screen says so

Evidence: docstring in `.venv/Lib/site-packages/marimo/_plugins/ui/_impl/input.py` (class `text`, line 735): "If True, then value is only emitted on Enter or when the input loses focus." Typing alone does not rerun `am_sweep`. `debounce=True` is also the class default, so the spec's explicit argument is redundant but harmless. `ctx.set_ui_value` bypasses the frontend, so the stage 3 acceptance test could not see this.

Impact: a customer types a new question, sees the old bars, and concludes the demo is stuck. The stage 4 browser verifier must press Enter or click away after typing, or it will read stale bars.

Fix: add one sentence to the `am_probe` prose: "Press Enter to run the search." Fits the voice (imperative, 6 words). Keep `debounce=True`: without it every keystroke fires 42 searches.

### 3. (Low-medium, voice) Prefix rule: "Redis Agent Memory" in the first body sentence after the H1

`am_title` sentence 2: "Redis Agent Memory keeps each conversation in session memory and durable facts in long-term memory, written by the app or extracted from session events." The standing rule is the Redis prefix at the H1 and bare after. Rewrite:

> Agent Memory keeps each conversation in session memory and durable facts in long-term memory, written by the app or extracted from session events.

(23 words.) Every other mention in the notebook is already bare.

### 4. (Low-medium, accuracy) Title and panel heading say the model "received" a prompt the panel may have rebuilt

Evidence: `am_prompt_panel` rebuilds the prompt from `ams_turn_bands` at the current slider (`_recalled`, `_t`). After a slider move the displayed prompt differs from what `ams_turn` sent. Fix 5 relabelled the bold line correctly ("System prompt the app built for this recall"), but two other strings still claim receipt:

- Heading `### 3. What the model received`
- `am_title` goal 3, last sentence: "...and the prompt the model received."

Rewrites:

- Heading: `### 3. The prompt the app builds`
- Goal 3 last sentence: "The panel under the chat shows the search text, the bars for it, and the prompt the app builds from the hits."

Also in the same panel: the session label says "as the service holds it". Every other string says "Agent Memory" or "the store". Rewrite: ``**Session `{ams_session_id}` in Agent Memory** ({len(_session.events)} events)``. The placeholder lists "the search text, the recall, and the prompt" while the title says "bars". Rewrite: "_Send a message to see what the last turn searched for and the prompt the app built._"

### 5. (Low, accuracy and voice) Goal 1 wording

`am_title` goal 1: `"Which foods should I avoid?" shares no word with "allergic to peanuts and tree nuts", and the search returns it for Priya.`

- The stored memory is "I'm allergic to peanuts and tree nuts". The query has the word "I". Whether "I'm" shares it is a tokenizer question, and a customer can see the quote is clipped. Quote the whole memory and say "keyword".
- The search returns it only at slider values up to 0.35 (verified: allergy band 0.35). At the default 0.30 it does.
- "raising the slider drops the weakest matches first" is not what the bars do. Bars stay and turn light blue below the line.

Rewrite (regex-safe, the bold title is unchanged):

> 1. **Long-term memory recalls by meaning.** "Which foods should I avoid?" shares no keyword with "I'm allergic to peanuts and tree nuts", and at the default 0.30 the search returns it for Priya. The bars under the search box show the highest `similarityThreshold` each memory clears, so raising the slider moves the line past the weakest matches first.

### 6. (Low, voice) Term drift

- Title says "Seed memories for two made-up customers"; the button reads `Seed` and `am_store_md` says "Seed writes". Rewrite: "Select Seed to write memories for two made-up customers, Tom and Priya, then search them and chat as either to see three principles:" (23 words).
- Title says "durable facts" and "the hits"; the notebook everywhere else says "memories" (`ams_build_system` keeps "facts" as a variable name, which is code). Rewrite "durable facts in long-term memory" as "durable memories in long-term memory"? That reads badly. Better: "keeps each conversation in session memory and what it learns about a customer in long-term memory". Optional, Nick's call.
- "recall" (goal 1 title, panel heading "Recall for ownerId") and "search" (probe, goals' prose) both name the same operation. The API call is a search and the demo word is recall. Leave, but do not add a third.

## Check results

### 1. Live code against spec

`am_probe`, `am_sweep`, `am_probe_panel`: zero diff lines (stripped comparison). `am_title`: one diff line, the fix 4 sentence ("in public preview"). No other difference. Follow-up fixes verified live: fix 1 (`am_store_md` two sentences, no semicolon), fix 2 (`label="Seed"`), fix 3 (`threshold = round(threshold, 2)` first line of `ams_bars`, and the caption and line position now use the rounded value), fix 4, fix 5 (label string present in `am_prompt_panel`).

### 2. Order and flags

Kernel indices 6 to 21 are `am_title, am_connect, am_store_md, am_store_button, am_store, am_store_fns, am_probe, am_sweep, am_probe_panel, am_recall_fns, am_owner, am_chat, am_turn_sweep, am_prompt_panel, am_prompt_fns, am_render`: the table order exactly, 16 cells. `hide_code` False only on `am_connect`, `am_store_fns`, `am_recall_fns`, `am_chat`, `am_prompt_fns`; True on the other 11. Indices 0 to 5 are `_`, `redis_client`, `llm_client`, `rs_title`, `lc_title`, `cr_title`. No column-4 cell among them. All 16 cells `idle`, errors `[]`.

### 3. Graph

- `am_sweep` refs: `ams_probe`, `ams_store`, `ams_sweep` (function). No `ams_threshold`. Defs: `ams_probe_bands`.
- `am_probe_panel` refs: `ams_bars`, `ams_probe`, `ams_probe_bands`, `ams_request`, `ams_threshold`, `json`, `mo`, `round`. The spec list plus builtins.
- Cycle trace, edges by defining cell: `am_connect` (`ams_client`, `models`, `dt`, `secrets`) feeds `am_store_fns`, `am_render`, `am_recall_fns`, `am_prompt_fns`, `am_chat`, `am_prompt_panel`. `am_store_fns` (`ams_seeds`) feeds `am_store`, `am_owner`. `am_store_button` and `am_render` feed `am_store`. `am_probe` + `am_store` + `am_recall_fns` feed `am_sweep`. `am_sweep` + `am_probe` + `am_chat` (`ams_threshold`) + `am_recall_fns` + `am_render` feed `am_probe_panel`. `am_owner` + `am_recall_fns` + `am_prompt_fns` feed `am_chat`. `am_chat` + `am_owner` + `am_store` + `am_prompt_fns` + `am_recall_fns` feed `am_turn_sweep`, which feeds `am_prompt_panel`. Nothing returns upstream. Acyclic. marimo also accepted the graph.
- Every name in the spec's public list has exactly one defining cell. `multi-def: {}` over the whole graph. None of the 16 "Gone" names is defined by any cell. No private (`_x`) names are defined across cells.

### 4. Title and regex

All three goal lines match `^(\d)\. \*\*([^*]+\.)\*\* \S.*$`; numbers 1, 2, 3. One H1: `# Redis Agent Memory`. The three docs URLs return 200. Env var names in the title match `am_connect` (`MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, `MEMORY_API_KEY`). The public-preview wording is the stated fix 4.

On-screen claims:

- "bars under the search box": true. `am_sweep` has no output, so `am_probe_panel` renders directly below `am_probe`.
- "The same search runs as Tom and as Priya, side by side": true. Two equal-width panes, `ownerId: Tom` and `ownerId: Priya`.
- "The panel under the chat shows the search text, the bars for it, and the prompt": true after a turn, and `am_prompt_panel` is the next output below `am_chat` (`am_turn_sweep` has none). Before a turn it shows the placeholder only. See finding 4 for "received".
- Not on screen: the slider that moves the line sits under the chat, below the bars it changes. `am_probe` prose says so. A customer scrolls past the chat to change the line. Acceptable per the spec, but say it in the title ("the slider under the chat") if Nick wants fewer misses.

Voice, sentence by sentence (code stripped):

| Text | Words | Result |
|---|---|---|
| Title para 1 ("An agent that forgets ... every time.") | 17 | pass |
| Title para 2 | 23 | fails prefix rule, finding 3 |
| Title para 3 ("Seed memories for ...") | 20 | term drift, finding 6 |
| Goal 1 sentences | 18, 21 | accuracy, finding 5 |
| Goal 2 sentences | 20, 9 | pass |
| Goal 3 sentences | 19, 20, 19 | "received", finding 4. The "search text, the bars for it, and the prompt" list names three real things, so it stays |
| "Agent Memory is in public preview on Redis Cloud." | 9 | pass |
| Settings sentence | about 15 | pass, serial comma present |
| `am_probe`: two sentences | 12, 9 | pass, plus finding 2 |
| `am_store_md`: three sentences | 15, 10, 4 and 9 | pass. No semicolon. "Chat sessions" against the title's "session memory" is a mild drift, leave |
| `am_owner`: two sentences | 15, 5 | pass. "starter questions" matches the chat chips |
| Probe-panel footnote: four sentences | 7, 17, 24, 12 | pass. The third is at 24 words, one under the limit. "highest one" has "thresholds" as the antecedent two sentences up and reads slightly loose; if edited, say "the highest threshold that still returned the memory" (24 words, same count) |
| `am_prompt_panel` labels | | finding 4 for "service", "recall" and the heading; the other labels pass |

No exclamation marks, banned words, empty words ("still" and "also" are not on the list), idioms, or "you already" framing. Headings are sentence case with no terminal period.

### 5. Bars and request body

`ams_bars(ams_probe_bands["Priya"], t)` (allergy 0.35, cat 0.05, canoe 0.05):

- t=0.30: allergy `ams-bar`, others below.
- t=0.35: allergy `ams-bar` (line at 35%).
- t=0.40: allergy `ams-bar ams-bar-below`.
- t=0.45: allergy below.

Hand dict `{allergy 0.35, cat 0.40, x 0.30, y 0.05}`: at 0.30 allergy and cat above, x on the line counts as above, y below. At 0.35 x drops. At 0.40 allergy drops, cat stays. So band 0.35 clears 0.35 and not 0.40, as intended. A raw `0.1 + 0.2` threshold now classifies a 0.30 band as above (fix 3 works).

The probe panel JSON blocks (read from the live output, highlighting stripped):

- Tom: `{ "text": "Which foods should I avoid?", "similarityThreshold": 0.3, "filter": { "ownerId": { "eq": "Tom" } }, "limit": 10 }`
- Priya: the same with `"eq": "Priya"`.

The footnote text is present. The caption reads `slider at 0.30`.

### 6. Traps for stage 4 and customers

- **Empty probe**: finding 1.
- **Debounce**: finding 2.
- **`limit: 10`**: `ams_memories` pages at 100, `ams_request` caps at 10. With more than 10 memories per owner, `ams_sweep` seeds every listed memory at 0.0 and only the top 10 per search can rise above it. The 11th and later show a 0.00 bar, which the footnote ("ends at the highest one that still returned the memory") does not explain. Seeds are three per owner, so this only matters if extraction piles up. No change now.
- **Duplicate texts**: bands are keyed by `m.text`, so two memories with identical text give one bar while `am_store` lists both (for example `ownerId: Priya (4)` over three bars). A near-duplicate from extraction stays separate. No change now. Note it if stage 4 step 5 shows a repeat.
- **Bars are not limited to the store snapshot**: `ams_sweep` adds any text the API returns, even if absent from `store` (verified: `ams_sweep("dogs", {"Tom": []})` returned Tom's three memories). So a memory extracted in the background shows in the bars before Refresh shows it in the tables. Harmless and arguably right. Do not "fix" it.
- **Cost**: every probe change, Seed, Refresh, or Clean up reruns `am_sweep` (2 owners x 21 searches, about 1 s). A slider move reruns only `am_probe_panel`. An owner switch recreates `ams_threshold` (back to 0.3) and `ams_turns` (back to 1), because both sit in `am_chat`. That is expected, and a stage 4 verifier who sets 0.45 before switching to Priya will see 0.30 after.
- **Stale title cells**: `rs_title`, `lc_title`, `cr_title` are `stale` and absent from `ctx.graph.cells` (verified). Other columns, not touched by stage 3, and stale since before stage 2. Stage 4's file step adds `column=` to the file, not to these, so nothing breaks, but the stage 4 verifier should not count them as errors.
- `POST /long-term-memory` in `am_store_md` is the route suffix. The SDK path is `/v1/stores/{storeId}/long-term-memory` with method POST (`redis_agent_memory/sdk.py`, line 1058). Stage 1's concern is closed.

## Not verified

- The browser rendering of the bars and the two-pane layout (stage 4 owns it).
- Whether the API treats the threshold as `>=` or `>`. Bands only show what returns at 0.05 steps.
- `tests/test_notebook.py` against the new cells. The on-disk file already contains the new `am_title` text. Stage 4 runs the suite.
