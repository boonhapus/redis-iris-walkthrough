# Agent Memory column: simplification spec (2026-10-07)

Nick approved these cuts to column 4 of `iris_basics.py` (the Redis Agent Memory column). Three implementers work in parallel on the live kernel, each on a disjoint set of cells. One reviewer per implementer follows.

Goal: fewer words, fewer widgets, same lessons. Prose that restates what a chart, table, or code block already shows is cut. Headers name their controls and sections.

## Rules for every implementer

**The live kernel is the source of truth.** The notebook runs at `http://127.0.0.1:2718/`. Never edit `C:\Users\NickCooper\projects\demo\redis-iris-basics\iris_basics.py` on disk. The kernel saves the file after each code-mode edit.

**How to run code in the kernel.** From the Bash tool (Git Bash):

```bash
export PATH="$(cygpath "$LOCALAPPDATA")/Microsoft/WinGet/Packages/jqlang.jq_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
bash "C:/Users/NickCooper/projects/demo/.claude/skills/marimo-pair/scripts/execute-code.sh" --url http://127.0.0.1:2718/ -c "import marimo._code_mode as cm; help(cm)"
```

The `help(cm)` call must be your first kernel call. After it succeeds, run scripts by file path:

```bash
bash "C:/Users/NickCooper/projects/demo/.claude/skills/marimo-pair/scripts/execute-code.sh" --url http://127.0.0.1:2718/ "C:/Users/NickCooper/AppData/Local/Temp/am_simplify_<stage>_<n>.py"
```

Write each script with the Write tool to `C:\Users\NickCooper\AppData\Local\Temp\am_simplify_<stage>_<n>.py`. Do not paste long scripts into a heredoc: the Bash tool turns `\n` inside a heredoc into a real newline, which breaks cell code that must contain a literal `\n`. Scripts must be ASCII only (no em dashes, no curly quotes). Put each cell body in a raw triple-single-quoted string, `r'''...'''`, so backslashes survive.

**Read before you edit, inside the same context.** Pattern:

```python
import marimo._code_mode as cm

NEW = r'''...cell body...'''

async with cm.get_context() as ctx:
    _ = ctx.cells["am_title"].code      # read at this version, or edit_cell raises StaleCellError
    ctx.edit_cell("am_title", code=NEW)
    ctx.run_cell("am_title")
```

- Submit cell bodies, not `@app.cell` wrappers or `def` lines. marimo generates the function signature.
- Never pass `skip_staleness_check` or `skip_validation`.
- Touch only the cells your stage lists. Two other implementers are editing other cells of the same kernel at the same time.
- Do not `create_cell` or `move_cell`. Stage A deletes one cell; no stage creates one.
- `ctx.run_cell` every cell you edit so its output refreshes. The chat is empty, so dependent reruns are harmless.
- Verify in a separate kernel call after the context closes: `ctx.cells[name].code`, `.status`, `.errors`, and the output text (`ctx.cells[name].output.data`, strip tags). The scratchpad namespace is a copy taken before your edits, so read `ctx.globals[...]` or use a later call.
- Then confirm the file on disk carries your new strings: `grep -n "<new string>" C:/Users/NickCooper/projects/demo/redis-iris-basics/iris_basics.py`.

**Forbidden.** Sending a chat message. Pressing or `set_ui_value` on Seed, Refresh, or Clean up. Deleting or creating memories or sessions in the store. Installing packages. Launching a browser. Editing files under `tests/`. Editing `iris_basics.py` directly. Editing any cell outside your stage. Changing `hide_code` on any cell.

**Report.** Write `C:\Users\NickCooper\projects\demo\redis-iris-basics\.agents\reviews\am-simplify-stage<X>.report.md` with: each cell changed and how, the verification output (status, errors, output text excerpt, disk grep), any deviation from this spec and why, and anything you noticed but did not touch. Then reply with a summary of at most eight lines.

## Stage A: prose and headers

Cells: `am_title`, `am_store_button`, `am_store_md` (delete), `am_owner`, `am_build_md`, `am_machinery_md`. Every one is `hide_code=True` already; leave the flags alone.

Do the edits in one context: read all six cells, then edit five and delete one.

### A1. `am_title`

Replace the body with:

```python
mo.md(r"""
# Redis Agent Memory

An agent that forgets a customer's two dogs between conversations asks about them again every time.
Redis Agent Memory keeps each conversation in session memory and durable facts in long-term memory, written by the app or extracted from session events.
Select Seed to write memories for two made-up customers, Tom and Priya, then chat as either to see three principles:

1. **Long-term memory recalls by meaning.** "Which foods should I avoid?" shares no keyword with "I'm allergic to peanuts and tree nuts", yet the search returns it for Priya.
2. **The `ownerId` filter scopes recall to one customer.** The app sends `filter: {ownerId: ...}` with every search, so Tom's agent does not see Priya's allergy.
3. **The app builds the prompt.** Each turn writes the message to session memory, searches long-term memory, and puts what it recalls in the system prompt.

Agent Memory is in preview on Redis Cloud. Settings: `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY`, plus `LLM_API_KEY` for the chat.
Docs: [overview](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/overview/),
[Python SDK quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/), and
[developer guide](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/developer-guide/).
""")
```

Why: the three "the bars beside the chat show..." sentences described what is on screen. "public preview" became "preview" because the docs index (`redis/docs` main, 2026-10-07) says "currently available in preview". The three numbered lines must keep the shape `N. **Title.** sentence`, which `tests/test_notebook.py` parses.

### A2. `am_store_button`

Replace the body with (the heading and prose move in here from `am_store_md`, and the extraction note moves in from `am_store`, which stage B removes there):

```python
ams_seed_button = mo.ui.run_button(label="Seed")
ams_refresh_button = mo.ui.run_button(label="Refresh")
ams_cleanup_button = mo.ui.run_button(label="Clean up", kind="danger")

mo.vstack(
    [
        mo.md(r"""
## Seed and list two customers' long-term memories

Seed writes three memories per customer with `POST /long-term-memory`, each tagged with the `ownerId` the app chooses.
Refresh lists them again, which picks up memories the store extracts from chat sessions about five minutes after a turn.
Clean up deletes every memory tagged Tom or Priya.
"""),
        mo.hstack([ams_seed_button, ams_refresh_button, ams_cleanup_button], justify="start"),
    ]
)
```

The cell keeps its name, so the rule "only `*_button` cells make a `run_button`" still holds.

### A3. Delete `am_store_md`

It is static markdown with no dependents (it is not even in `ctx.graph.cells`). Confirm with `ctx.cells["am_store_md"].id not in ctx.graph.cells` or an empty `ctx.graph.descendants(...)`, then `ctx.delete_cell("am_store_md")` in the same context as A2.

### A4. `am_owner`

Replace the body with:

```python
ams_owner = mo.ui.dropdown(options=list(ams_seeds), value=USER_ID, label="Chat as")

mo.md(r"""
## Chat as Tom or Priya and watch the recall

Type / in the chat box to pick a starter message. Send the first three as Tom, then switch to Priya and send them again.
Switching customers starts a new conversation. The fourth message tells the agent a new fact, and Refresh shows the memory extracted from it a few minutes later.
""")
```

Why: the "Until you send a message, the panel beside the chat previews..." sentence duplicated the workbench label. The "/" sentence is new because `mo.ui.chat` hides the starter prompts behind "/" and nothing told the reader.

### A5. `am_build_md`

Replace the body with:

```python
mo.md(r"""
## The Agent Memory code: client, store, search, prompt, turn

One cell per step, code open in edit mode. Seed and Clean up call the store functions, and each chat turn calls `ams_turn`.
""")
```

### A6. `am_machinery_md`

Replace the body with:

```python
mo.md(r"""
## Notebook plumbing: sliders, chat object, sweep, bars

Their code is hidden. In edit mode, open a cell to read it.
""")
```

### Stage A verification

1. In a later kernel call: the six cells (five after the delete) show `status` idle or stale, `errors == []`, and output text containing the new headings. `am_store_md` is gone from `list(ctx.cells.keys())`. Column 4 still starts at `am_title`, and the order is `am_title`, `am_store_button`, `am_store`, `am_owner`, `am_workbench`, `am_build_md`, `am_connect`, `am_store_fns`, `am_recall_fns`, `am_prompt_fns`, `am_turn_fn`, `am_machinery_md`, `am_settings`, `am_chat`, `am_turn_sweep`, `am_render`.
2. `grep -n "Notebook plumbing\|Chat as Tom or Priya\|Seed and list two\|in preview on Redis Cloud" C:/Users/NickCooper/projects/demo/redis-iris-basics/iris_basics.py` finds all four. `grep -n "What long-term memory holds\|Notebook machinery\|public preview" ...` finds none.
3. From `C:\Users\NickCooper\projects\demo\redis-iris-basics`, run `uv run pytest tests/test_voice.py -q`. It must pass (8 tests). If a sentence trips the 25-word ceiling or the serial-comma rule, shorten it and record the change in the report. Do not edit the test.

## Stage B: workbench, bars, tables

Cells: `am_store`, `am_render`, `am_workbench`. All three are `hide_code=True`; leave the flags alone. One context for all three edits is fine.

### B1. `am_store`

Replace the body with (only the trailing extraction paragraph is removed; it now lives in the store prose, which stage A writes):

```python
_note = ""
if ams_seed_button.value:
    _ids = await ams_seed()
    _note = f"Wrote {len(_ids)} new memories to long-term memory." if _ids else "Every seed memory is already in long-term memory."
if ams_cleanup_button.value:
    _deleted = 0
    for _owner in ams_seeds:
        _deleted += len(await ams_forget(_owner))
    _note = f"Deleted {_deleted} memories for {' and '.join(ams_seeds)}."
_ = ams_refresh_button.value  # Refresh lists again, for memories extracted in the background

ams_store = {}
for _owner in ams_seeds:
    ams_store[_owner] = await ams_memories(_owner)

mo.vstack(
    ([mo.md(_note)] if _note else [])
    + [
        mo.hstack(
            [ams_memory_table(_owner, _found) for _owner, _found in ams_store.items()],
            widths="equal",
            align="start",
        )
    ]
)
```

Running this cell lists both owners again (two read-only searches). That is allowed. It writes nothing because no button value is set.

### B2. `am_render`

Replace the body with. Changes: the caption line under the bars and its `.ams-caption` CSS are gone; the memory table drops the "created (UTC)" column and passes `show_data_types=False`.

```python
def ams_bars(bands: dict[str, float], threshold: float) -> mo.Html:
    """One row per memory: a bar to the highest threshold that returns it, and the slider as a line."""
    threshold = round(threshold, 2)
    rows = []
    for text, band in sorted(bands.items(), key=lambda kv: -kv[1]):
        state = "ams-bar" if round(band, 2) >= threshold else "ams-bar ams-bar-below"
        rows.append(
            '<div class="ams-row">'
            f'<div class="ams-label" title="{html.escape(text)}">{html.escape(text)}</div>'
            '<div class="ams-track">'
            f'<div class="{state}" style="width:{band * 100:.0f}%"></div>'
            f'<div class="ams-line" style="left:{threshold * 100:.0f}%"></div>'
            "</div>"
            f'<div class="ams-value">{band:.2f}</div>'
            "</div>"
        )
    body = "".join(rows) or '<div class="ams-empty">No memories for this owner yet. Select Seed to write them.</div>'
    css = """
<style>
.ams-bars { font: 13px system-ui, -apple-system, "Segoe UI", sans-serif; color: #52514e; }
.ams-row { display: grid; grid-template-columns: minmax(0, 3fr) 2fr 3.2em; gap: 0 8px; align-items: center; margin: 6px 0; }
.ams-label { overflow-wrap: anywhere; }
.ams-track { position: relative; min-width: 0; height: 12px; background: #e1e0d9; border-radius: 0 4px 4px 0; }
.ams-bar { position: absolute; left: 0; top: 0; height: 100%; background: #2a78d6; border-radius: 0 4px 4px 0; }
.ams-bar-below { background: #9ec5f4; }
.ams-line { position: absolute; top: -4px; height: 20px; width: 2px; background: #0b0b0b; }
.ams-value { color: #0b0b0b; font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
.ams-ticks { display: flex; justify-content: space-between; color: #898781; font-size: 11px; border-top: 1px solid #c3c2b7; padding-top: 2px; }
.ams-empty { color: #898781; }
</style>
"""
    return mo.Html(
        '<div class="ams-bars">'
        + body
        + '<div class="ams-row"><div></div><div class="ams-ticks"><span>0</span><span>0.5</span><span>1</span></div><div></div></div>'
        + "</div>"
        + css
    )


def ams_memory_table(owner: str, found: list[models.MemoryRecord]) -> mo.Html:
    """One owner's long-term memories as a table, headed with the `ownerId`."""
    rows = [{"memory": m.text, "type": m.memory_type} for m in found]
    table = (
        mo.ui.table(rows, selection=None, pagination=False, show_search=False, show_download=False, show_data_types=False)
        if rows
        else mo.md("_No memories yet._")
    )
    return mo.vstack([mo.md(f"**ownerId: {owner}** ({len(found)})"), table])
```

### B3. `am_workbench`

Replace the body with. Changes: shorter labels, the "How the bars are measured" accordion is gone, the session table drops its time column and passes `show_data_types=False`.

```python
_t = round(get_threshold(), 2)
_n = get_turns()
_owner = ams_owner.value
_turn_done = bool(ams_chatbot.value) and ams_chatbot.value[-1].role == "assistant"
_recalled = [text for text, band in ams_turn_bands[_owner].items() if round(band, 2) >= _t]

_data = [
    mo.md(
        ("**Search text**, the last user message:" if _n == 1 else f"**Search text**, the last {_n} user messages:")
        if ams_turn_live
        else "**Search text**, the first starter message until you send one:"
    ),
    mo.md(f"```text\n{ams_turn_query}\n```"),
    mo.md(f"**ownerId {_owner}**"),
    ams_bars(ams_turn_bands[_owner], _t),
]
for _other in (o for o in ams_turn_bands if o != _owner):
    _data += [mo.md(f"**Same search, ownerId {_other}**"), ams_bars(ams_turn_bands[_other], _t)]
_data += [
    mo.md("**System prompt**"),
    mo.md(f"```text\n{ams_build_system(_owner, _recalled)}\n```"),
    mo.md("**Search request**"),
    mo.md(f"```json\n{json.dumps(ams_request(ams_turn_query, _owner, _t), indent=2)}\n```"),
]
if _turn_done:
    _session = await ams_client.get_session_memory_async(session_id=ams_session_id)
    _data += [
        mo.md(f"**Session `{ams_session_id}`** ({len(_session.events)} events)"),
        mo.ui.table(
            [{"role": e.role.value, "text": e.content[0].text} for e in _session.events],
            selection=None,
            pagination=False,
            show_search=False,
            show_download=False,
            show_data_types=False,
        ),
    ]

mo.hstack(
    [
        mo.vstack([ams_owner, ams_threshold, ams_turns, ams_chatbot]),
        mo.vstack(_data),
    ],
    widths=[5, 6],
    gap=1.5,
    align="start",
)
```

The `\n` sequences inside the f-strings must reach the kernel as the two characters backslash and n. That is why the script is written with the Write tool and the body sits in `r'''...'''`.

### Stage B verification

1. In a later kernel call: the three cells are idle with `errors == []`. The `am_workbench` output text contains "ownerId Tom", "Same search, ownerId Priya", "System prompt", and "Search request", and does not contain "How the bars are measured", "Highest similarityThreshold", "Memories above the line", or "Request as sent". The `am_store` output text contains "ownerId: Tom" and "ownerId: Priya" and does not contain "extracts memories". The `am_store` output HTML does not contain "created (UTC)".
2. `ctx.globals["ams_store"]` still has two owners, and the bars still show both owners (`ams_turn_bands` has keys Tom and Priya).
3. `grep -n "ams-caption\|How the bars are measured\|Request as sent\|created (UTC)" C:/Users/NickCooper/projects/demo/redis-iris-basics/iris_basics.py` finds nothing. `grep -n "show_data_types=False" ...` finds two.

## Stage C: code moves

Cells: `am_recall_fns` (visible), `am_turn_sweep` (hidden), `am_store_fns` (visible), `am_turn_fn` (visible). Leave every `hide_code` flag alone.

All four edits must go in ONE context. `ams_sweep` leaves `am_recall_fns` and enters `am_turn_sweep`; done in two contexts, the first would leave `ams_sweep` undefined or defined twice and validation would reject it.

### C1. `am_recall_fns`

Replace the body with (`ams_sweep` removed, nothing else changed):

```python
def ams_request(query: str, owner: str, threshold: float) -> dict:
    """The search request. Principle 1 is `text` and `similarityThreshold`; Principle 2 is `filter`."""
    return {
        "text": query,
        "similarityThreshold": threshold,
        "filter": {"ownerId": {"eq": owner}},
        "limit": 10,
    }


async def ams_recall(query: str, owner: str, threshold: float) -> list[models.MemoryRecord]:
    """Search long-term memory by meaning, for one owner, cut off by `threshold`."""
    r = await ams_client.search_long_term_memory_async(request=ams_request(query, owner, threshold))
    return r.items
```

### C2. `am_turn_sweep`

Replace the body with (`ams_sweep` arrives here unchanged, above the three lines that were already in the cell):

```python
# The search text behind the last turn (the first starter prompt before any chat), and how high a threshold
# each memory clears for it, for every customer. Depends on the chat, N, and the store, not on the threshold.
async def ams_sweep(query: str, store: dict[str, list[models.MemoryRecord]]) -> dict[str, dict[str, float]]:
    """For each owner in `store`, the highest threshold that returns each memory. The API returns no score."""
    grid = [round(0.05 * i, 2) for i in range(21)]  # 0.00 to 1.00
    pairs = [(owner, t) for owner in store for t in grid]
    gate = asyncio.Semaphore(12)  # 42 searches per sweep; keep a dozen in flight

    async def recall(owner: str, t: float) -> list[models.MemoryRecord]:
        async with gate:
            return await ams_recall(query, owner, t)

    hits = await asyncio.gather(*(recall(owner, t) for owner, t in pairs))

    bands = {owner: {m.text: 0.0 for m in records} for owner, records in store.items()}
    for (owner, t), found in zip(pairs, hits):
        for m in found:
            bands[owner][m.text] = max(bands[owner].get(m.text, 0.0), t)

    return bands


ams_turn_live = any(m.role == "user" for m in ams_chatbot.value)
ams_turn_query = ams_query(ams_chatbot.value, get_turns()) if ams_turn_live else ams_prompts[0]
ams_turn_bands = await ams_sweep(ams_turn_query, ams_store)
```

### C3. `am_store_fns`

Replace the body with. Changes: `ams_memories` is one search with `limit` 100 instead of a page loop, and `ams_forget` is one bulk delete guarded by `if memory_ids` instead of a chunked gather. `asyncio` is no longer used here.

```python
ams_seeds = {
    USER_ID: [
        "I've got two dogs, Max and Cece",
        "I wear size 11 trail shoes",
        "I'm planning a hiking trip in the Dolomites in August",
    ],
    "Priya": [
        "I have a cat named Miso",
        "I'm allergic to peanuts and tree nuts",
        "I'm planning a canoe trip in Maine in June",
    ],
}


async def ams_memories(owner: str) -> list[models.MemoryRecord]:
    """Every long-term memory whose `ownerId` is `owner`."""
    r = await ams_client.search_long_term_memory_async(
        request={"filter": {"ownerId": {"eq": owner}}, "limit": 100}
    )
    return r.items


async def ams_seed() -> list[str]:
    """Principle 2: write memories straight to long-term memory, each tagged with an `ownerId`."""
    created: list[str] = []

    for owner, texts in ams_seeds.items():
        stored = {m.text for m in await ams_memories(owner)}
        new = [
            {"id": str(uuid.uuid4()), "text": text, "ownerId": owner}
            for text in texts
            if text not in stored
        ]

        if new:
            await ams_client.bulk_create_long_term_memories_async(memories=new)

        created += [m["id"] for m in new]

    return created


async def ams_forget(owner: str) -> list[str]:
    """Delete every long-term memory about `owner`. Returns the ids deleted."""
    memory_ids = [m.id for m in await ams_memories(owner)]

    if memory_ids:
        await ams_client.bulk_delete_long_term_memories_async(memory_ids=memory_ids)

    return memory_ids
```

The `if memory_ids` guard matters: the old chunked gather over an empty list never called the API, and this keeps that behavior.

### C4. `am_turn_fn`

Read the current body. Replace exactly this comment line:

```python
        # ams_settings reads the two sliders (notebook machinery, at the end of the column).
```

with:

```python
        # ams_settings returns the slider values. It is a notebook helper in the plumbing cells below, not an Agent Memory call.
```

Leave every other line as it is. Keep the indentation of the surrounding lines (the comment sits inside `ams_turn`, so it is indented four spaces relative to the `async def`, matching the line above it).

### Stage C verification

1. In a later kernel call: all four cells idle with `errors == []`. `ctx.graph.cells[<am_recall_fns id>].defs == {"ams_recall", "ams_request"}`. `ctx.graph.cells[<am_turn_sweep id>].defs` contains `ams_sweep`, `ams_turn_bands`, `ams_turn_live`, `ams_turn_query`. `ctx.graph.cycles` is empty.
2. `ctx.globals["ams_turn_bands"]` has keys Tom and Priya with the same memory counts as `ctx.globals["ams_store"]` (Tom 6, Priya 3 at the time of writing). `ams_store` counts are unchanged from before your edit.
3. Call `await ctx.globals["ams_memories"]("Tom")` in the scratchpad and confirm it returns the same count as before. Do not call `ams_forget` or `ams_seed`.
4. `am_workbench` output still shows bars for both owners (the sweep moved but still runs).
5. From `C:\Users\NickCooper\projects\demo\redis-iris-basics`: `uv run python -m marimo check --strict iris_basics.py` exits 0. If it fails on a cell outside your stage, record it and move on; the other implementers may be mid-edit.
6. `grep -n "page_token\|asyncio.gather(\s*\*(\s*ams_client" C:/Users/NickCooper/projects/demo/redis-iris-basics/iris_basics.py` finds nothing. `grep -n "async def ams_sweep" ...` finds exactly one line, and it sits after `def am_turn_sweep(` rather than after `def am_recall_fns(`.

## Expected end state (all stages)

16 cells in column 4, in this order: `am_title`, `am_store_button`, `am_store`, `am_owner`, `am_workbench`, `am_build_md`, `am_connect`, `am_store_fns`, `am_recall_fns`, `am_prompt_fns`, `am_turn_fn`, `am_machinery_md`, `am_settings`, `am_chat`, `am_turn_sweep`, `am_render`. Visible code on exactly `am_connect`, `am_store_fns`, `am_recall_fns`, `am_prompt_fns`, `am_turn_fn`. Store unchanged: Tom 6 memories, Priya 3. Chat empty.
