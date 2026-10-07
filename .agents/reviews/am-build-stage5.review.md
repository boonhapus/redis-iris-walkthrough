# Agent Memory build, stage 5 (workbench) independent review (2026-10-07)

**Verdict: PASS WITH NOTES**

No blocker. The four stage 5 cells match the spec, the graph is acyclic, both probes behave, and the file agrees with the kernel. The real defects are design: the chat now sits downstream of a 42-search sweep, the request body hides in an accordion that re-collapses on every rerun, and the bar labels truncate in the 391 px right pane. Two browser-only claims (accordion collapse, label truncation) are by construction and arithmetic, because I may not open a browser.

Method: read-only. `cm.get_context()` reads of cells, graph, and outputs, the two allowed probes (each restored), one timed scratchpad `ams_sweep`, marimo 0.24.2 source and the bundled frontend JS, and the file on disk. No edit, run, chat message, button press, or delete. Final state: owner Tom, threshold 0.3, N 1, chat empty, `ams_session_id` `d03bcafb4c4b732e` (the owner round trip changed it twice, as designed).

## Findings, most severe first

### 1. (Medium, robustness) One failed search sweep now takes the whole chat down

`am_workbench` refs `ams_turn_bands`, `ams_turn_query`, `ams_turn_live`, all defined by `am_turn_sweep`, which awaits `ams_sweep(..., ams_store)`. `ams_sweep` is an unthrottled `asyncio.gather` over 2 customers x 21 thresholds (`am_recall_fns`, `iris_basics.py:535`). If that cell raises (a 429 on a rate-limited preview store, a timeout, the store down), marimo does not run its descendants, so the workbench shows an ancestor error. The chat, the owner dropdown, and both sliders all vanish, because they are only displayed there. Before stage 5 the same failure hid one panel and the chat stayed usable. `am_store` is the same upstream (`am_store` -> `am_turn_sweep` -> `am_workbench`). The stage 4 review flagged the missing semaphore as low priority because the cost was a panel. The cost is now the chat. On Nick's store it never fired: sweeps took 0.77 s and 0.78 s (two timed runs), no errors.

Fix, in `ams_sweep` (`am_recall_fns`):

```python
    sem = asyncio.Semaphore(12)

    async def limited(owner: str, t: float) -> list[models.MemoryRecord]:
        async with sem:
            return await ams_recall(query, owner, t)

    hits = await asyncio.gather(*(limited(owner, t) for owner, t in pairs))
```

I did not time it with a limit, so check that the sweep stays near 1 to 2 s. The spec's fallback (keep `ams_chatbot` displayed in `am_chat`, data only in the cell below) removes the coupling entirely if Nick prefers.

### 2. (Medium, design) "Request as sent" is collapsed on open and re-collapses on every rerun

The accordion renders `data-multiple='false'` with both entries closed. The request body is the evidence for goal 2 (`filter: {ownerId: ...}` with every search) and for the slider's effect (`"similarityThreshold": 0.3` becomes `0.45`; I confirmed that number in the output after the probe, inside the accordion). The accordion is part of the `am_workbench` output, which reruns on every threshold move, N change, chat push, and owner switch (finding in the rerun table below). A rerun replaces the output HTML, so the new accordion mounts closed. A customer who opens it to watch the number change while dragging the slider sees it close on every release. This is by construction from the marimo output model, not observed in a browser. `multiple=False` also means the request and the footnote cannot be open together.

Fix, in `am_workbench`: show the request outside the accordion and keep the footnote in it.

```python
_data += [
    mo.md("**Request as sent**"),
    mo.md(f"```json\n{json.dumps(ams_request(ams_turn_query, _owner, _t), indent=2)}\n```"),
]
_data.append(mo.accordion({"How the bars are measured": mo.md("...unchanged footnote...")}))
```

If Nick wants to keep the accordion, say so in `am_owner` ("Open Request as sent to see the filter"). The spec's acceptance string "Request as sent" survives either way.

### 3. (Medium, design, arithmetic) The bar labels truncate in the right pane

The app is `width="columns"`, and the column default is `contentWidth` = 740 px (`--content-width:740px`; `getColumnWidth` falls back to `contentWidth`). `widths=[5, 6]` renders `flex: 5` and `flex: 6` with `gap: 1.5rem` (live HTML), so the panes are about 325 px and 391 px. In the right pane `.ams-row` is `minmax(0, 1fr) 2fr 3.2em` with an 8 px gap: about 333 px after the value column, so the label gets about 111 px, roughly 17 characters at 13 px. Every seed memory is 26 to 53 characters, so all six labels show as "I wear size 11…", "I'm allergic to p…". This is the defect stage 3 fix 10 removed by stacking the owners at full width; the workbench puts it back, and the full text is only in the `title` tooltip. The chat at 325 px is workable (chips wrap).

Fix, in `am_render` CSS: `.ams-row { grid-template-columns: minmax(0, 3fr) 2fr 3.2em; ... }` and `.ams-label { overflow-wrap: anywhere; }` (drop `overflow: hidden; text-overflow: ellipsis; white-space: nowrap`). Labels then wrap to two lines instead of clipping.

### 4. (Low, design) What a workbench rerun does to the chat, and what it does not

History is safe in the same tab, and I read why from source:

- `ams_chatbot` is displayed in `am_workbench` but defined by `am_chat`. Its frontend object id is `TqIu-2` (live HTML), prefixed by the defining cell.
- A re-rendered `marimo-ui-element` reads its value from the frontend `UIElementRegistry` (`rh(e,t)`: `t.lookupValue(n)` before `data-initial-value`, in `index-4n22QjCD.js`). `removeInstance` only drops the DOM element. Entries are deleted by `removeElementsByCell(cellId)`, which matches the prefix of the cell that defined them, so a workbench rerun does not clear it and an `am_chat` rerun (owner switch) does, which is the intended reset.
- On mount the chat also calls `get_chat_history` and uses it only when the value is empty (`O=e.value.length>0?e.value:E`, `chat-ui-DGiVDzNh.js`). `ams_chatbot.value` on the kernel side is set by `_update` (`ui_element.py:460-467`), so the kernel object holds it too.

What can be lost on each rerun: an unsent draft in the input, focus (the cursor leaves the box after every reply), the chat's inner scroll position, any open accordion (finding 2), and any text selection in the right pane. A stream in flight when a slider is released is the worst case: the chat remounts mid-stream, and I cannot tell from source whether the old instance's `onFinish` still pushes the final value. Sliders are `debounce=True` and a user would have to release one during the 2 to 5 s reply, so I rate it low. The stage 4 note still stands: the kernel's `_chat_history` lacks the last streamed assistant reply (`chat.py` `_run_prompt`, "the frontend will add the accumulated message"), so only a full page reload restores history short of that reply. That is marimo behaviour, not new. The frontend sends one `setValue` at stream finish (`onFinish`), so I expect one chat push per turn.

Nothing here was observed live. No chat turn ran.

### 5. (Low, prose) Wording failures in the three cells

Counted with inline code stripped, no sentence is 25 words or over, no semicolon or exclamation mark appears, and no banned word does. The failures are term and grammar:

| Where | Text | Problem | Rewrite |
|---|---|---|---|
| `am_workbench` search label, N = 1 | "**Search text**, the last 1 user message:" | "the last 1" | `the last user message` when `_n == 1` |
| `am_owner`, `am_workbench` | "Ask the first three starter prompts", "The fourth prompt tells...", "previews the first starter prompt" | "prompt" also means the system prompt ("go into the prompt", title goal 3). One term per concept | "Send the first three starter messages as Tom, then switch to Priya and send them again." "The fourth message tells the agent a new fact, and Refresh can show..." "previews the first starter message." The spec's acceptance string "preview of the first starter prompt" changes with it |
| `am_owner` heading | "## Chat, and the data behind each turn" | stray comma | `## Chat and the data behind each turn` |
| `am_workbench` footnote | "...the highest one that still returned the memory..." and "...are recalled at the slider's setting." | Unchanged from the stage 4 review: "still" is an empty word, "one" has no antecedent, and "clears" (caption), "returned", and "recalled" name one event three ways | "Each bar ends at the highest threshold that returned the memory...", "Memories at or above the line are returned at the slider's setting.", caption "Highest similarityThreshold at which the memory is returned." |
| `ams_bars` caption in the workbench | "Highest similarityThreshold the memory still clears. Line: slider at 0.30." | Printed twice (Tom and Priya), the second a repeat | Leave, or pass `caption=False` for the second pane |
| preview state | "**System prompt the app built for this recall**" | "built" before anything was sent | Fine as is if Nick accepts the preview sentence above it. Otherwise `...the app would build...` while `not ams_turn_live` |

Passes: title three goals match `^(\d)\. \*\*([^*]+\.)\*\* \S.*$` (checked live, all three). Goal 1 sentence 1 is 23 words (the 28 my counter reported includes the bold lead). The longest sentence is the goal 1 second sentence at 23 words plus one code token. "Beside the chat you see..." and "The bars beside the chat show..." are true: the workbench is an `hstack` with the controls and chat on the left and the data on the right. The "search text, the bars for it, and the prompt the app builds" list is three real panel items and is not rhetorical. "Redis Agent Memory" in title sentence 2 is the known kept deviation. Sentence-case headings, no terminal periods, no "you already".

### 6. (Low, design) The session table is invisible until a reply completes, and nothing says so

`_turn_done` gates `get_session_memory_async`, so goal 3's "writes the message to session memory" has no on-screen evidence on open or while the first reply streams. The right pane just lacks the section. Fix: add `else: _data.append(mo.md("_Send a message to see the session as Agent Memory holds it._"))` after the `if _turn_done:` block.

### 7. (Info) An owner switch re-runs the 42-search sweep it does not need

See the rerun table (d). The sweep covers both customers and the preview text does not change, but `am_turn_sweep` refs `ams_chatbot`, which `am_chat` redefines. About 0.8 s and 42 requests per switch. Leave it.

### 8. (Info) `am_store_md` showed `stale`

Current status: `stale`, errors `[]`, `id BYtC`. Facts:

- Its live code equals post-review fix 1 exactly ("Chat sessions are not deleted. The store expires them after an hour by default.", no semicolon) and equals the file (`iris_basics.py:221-230`). No divergent edit reached the kernel.
- Its output is current: the markdown text contains the fixed sentence and no semicolon.
- It is not in `ctx.graph.cells`. The only other cells missing from the graph are `rs_title`, `lc_title`, `cr_title`, which also read `stale`. Everything else (16 cells) is registered. `am_title` is `idle` and registered because stage 5 ran it.
- `iris_basics.py` was last written at 11:48:30, eight minutes before this read.

So `stale` here means "not registered in the kernel graph, so the kernel does not know it has run", not "has an unapplied edit". I cannot tell from read-only access whether Nick's tab caused it. The cell references only `mo` and nothing depends on it, so it is cosmetic. Running the cell once (not allowed to me) clears it. The stage 5 report already recorded it as stale before the stage.

## Evidence by check

### 1. Live code against the spec

| Cell | Diff vs the spec's Stage 5 block |
|---|---|
| `am_owner` | none (0 diff lines) |
| `am_chat` | one line: `ams_prompts` starts with "Which foods should I avoid?" (the coordinator's intended change). Consequence: the on-load preview is the foods question, `0.35` and "You are a helpful AI assistant." appear, and the `am_owner` sentence "first three starter prompts" now means foods, pets, summer, still correct, with the marathon line fourth |
| `am_turn_sweep` | none |
| `am_workbench` | none |
| `am_title` | four sentences as the spec and post-review fixes 4 and 8, plus "The memories it recalls go into the system prompt" (the stage 4 "hits" fix). No difference that matters |

- `am_chat` output: `CellOutput` with `len 0` (text/plain). `am_owner` output: markdown only, 514 chars, heading plus prose.
- `am_prompt_panel`, `am_probe`, `am_sweep`, `am_probe_panel`: none exist (checked by cell name). `ams_probe`, `ams_probe_bands`: defined nowhere. `ams_prompts`: defined only in `am_chat`. `ams_turn_live`: only in `am_turn_sweep`. `AMS_SEEDS`: nowhere. `multi-def`: empty.

### 2. Order and flags

20 cells: `_`, `redis_client`, `llm_client`, `rs_title`, `lc_title`, `cr_title`, then the 14: `am_title`, `am_connect`, `am_store_md`, `am_store_button`, `am_store`, `am_store_fns`, `am_owner`, `am_settings`, `am_chat`, `am_turn_sweep`, `am_workbench`, `am_recall_fns`, `am_prompt_fns`, `am_render`. This is the spec order. `hide_code` is False on `am_connect`, `am_store_fns`, `am_chat`, `am_recall_fns`, `am_prompt_fns` and True on the other nine. All errors `[]`, none `exception`. `am_store_md` is the only stale column-4 cell (finding 8).

### 3. Graph

- `am_workbench` refs: `ams_bars`, `ams_build_system`, `ams_chatbot`, `ams_client`, `ams_owner`, `ams_request`, `ams_session_id`, `ams_threshold`, `ams_turn_bands`, `ams_turn_live`, `ams_turn_query`, `ams_turns`, `get_threshold`, `get_turns`, `json`, plus builtins `bool`, `len`, `mo`, `round`. Every name on the check list is present.
- `am_turn_sweep` refs: `ams_chatbot`, `ams_prompts`, `ams_query`, `ams_store`, `ams_sweep`, `get_turns`, `any`. It does not ref `get_threshold`.
- `graph.cycles` is `set()`.
- Displaying an element outside its defining cell is allowed, and it is live: the workbench output contains `marimo-ui-element object-id='ROlb-0'` (dropdown from `am_owner`) and `TqIu-0/1/2` (sliders, chat from `am_chat`). The guard is `ui_element.py:324-336`: `.value` raises only when `execution_context.cell_id == ui_element_registry.get_cell(self._id)` and not `setting_element_value`. Rendering does not call `.value`. No cell reads `.value` of `ams_threshold` or `ams_turns` (they use the getters); `ams_owner.value` is read in `am_chat` and `am_workbench`, and `ams_chatbot.value` in `am_turn_sweep` and `am_workbench`, none of which defined them.

### 4. Reruns, from refs and `graph.children`

`am_chat` children: `am_workbench`, `am_turn_sweep`. `am_owner` children: `am_chat`, `am_workbench`. `am_store` children: `am_turn_sweep`. `am_settings` children: `am_chat`, `am_turn_sweep`, `am_workbench`.

| Event | Cells that rerun | Sweep (42 searches, 0.77 s measured) |
|---|---|---|
| (a) chat value push | `am_turn_sweep`, `am_workbench` (workbench also reads the session after a completed turn) | yes |
| (b) threshold change | `am_workbench` only. The setter runs in `am_chat`'s context, which marimo excludes from the rerun | no |
| (c) N change | `am_turn_sweep`, `am_workbench` | yes |
| (d) owner switch | `am_chat` (new session id, empty chat, rebuilt sliders), `am_turn_sweep`, `am_workbench` | yes |

(b) was confirmed live: after the 0.45 probe `am_chat` and `am_turn_sweep` were `idle` and `ams_session_id` was unchanged. The wall time of the workbench rerun itself is not observable from `cm`: the context exit returned in 0.05 s with the update queued. Closest numbers: the sweep alone is 0.77 s and 0.78 s, so (a), (c), (d) cost about one second plus one session GET when a turn is done, and (b) costs the render only.

### 5. Probes

- Threshold 0.45, read in a separate call: `ams_session_id` `f2236ed0267336d6` unchanged, `get_threshold()` 0.45, workbench says "Recall for ownerId Tom at 0.45", "slider at 0.45", all six bar lines at `left:45%`, request body `"similarityThreshold": 0.45` (inside the accordion, syntax-highlighted), `am_chat` and `am_turn_sweep` `idle`. Restored 0.3: back to "at 0.30", lines at 30%, 5 below bars and 1 above for Tom.
- Owner `["Priya"]`: session id changed `f2236ed0267336d6` -> `17d2785393ca0dd5`, chat value length 0, threshold 0.3 and N 1 kept (getters), workbench says "Recall for ownerId Priya at 0.30" and "The same search as ownerId Tom". Priya's bars: allergy 0.35 above the line, cat 0.05 and canoe 0.05 below. System prompt: "You are a helpful AI assistant. What you remember about Priya: - I'm allergic to peanuts and tree nuts". Request body `"eq": "Priya"`. Restored `["Tom"]`: session id changed again to `d03bcafb4c4b732e`, "Recall for ownerId Tom at 0.30", "The same search as ownerId Priya".
- On-load content checks, raw `output.data` unescaped: "Chat as", "similarityThreshold", "preview of the first starter prompt", "Which foods should I avoid?", "Recall for ownerId Tom at 0.30", "The same search as ownerId Priya", "0.35", "You are a helpful AI assistant.", "Request as sent", "How the bars are measured", and the 4-chip `data-prompts` with `data-max-height='520'` are all present. The accordion body text also appears in the output because the body is part of the HTML.

### 7. File

`grep '^def am_\|^async def am_'` lists the 14 cells in the live order (lines 181 to 600). Every non-blank line of all 14 live cells is present in the file (0 missing in each). `uv run python -m marimo check --strict iris_basics.py` exits 0. `uv run pytest tests/test_voice.py -q`: 8 passed (I did not check whether it scans the `am_` cells).

### 8. First-open design, ranked

| Behaviour | Real defect? | One-line fix |
|---|---|---|
| Right pane 391 px, bar labels clipped to about 17 characters | Yes (finding 3) | Wrap labels, widen the label column |
| Request body collapsed, and re-collapsed by each rerun | Yes (finding 2) | Take it out of the accordion |
| Sweep failure blanks the chat | Latent (finding 1) | `Semaphore(12)` in `ams_sweep` |
| Chat at 325 px with `max_height=520` | No. It scrolls inside the component, and the left stack is about 670 px against a right pane that grows past it | none |
| Session table only after a completed reply | Minor (finding 6) | Add the placeholder |
| `am_chat`'s 33 lines of code sit between the prose and the chat | By design (the code is the lesson), but the chat is below the code on first open | none |
| Column width is a user setting (`marimo:notebook-col-sizes`) | A customer who narrows the column makes finding 3 worse | none |

## Not verified

No browser, so finding 2's collapse, finding 3's truncation, and the chat remount behaviour are from source and arithmetic. No chat turn ran, so the post-turn session table, the value push count, and a non-empty history surviving a slider move were not exercised.
