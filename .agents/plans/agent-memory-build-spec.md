# Agent Memory column: build spec (2026-10-07)

This is the cell-by-cell spec for rebuilding column 4 of `iris_basics.py` in the live kernel at http://127.0.0.1:2718/ through `marimo._code_mode` (`cm`). It implements `.agents/plans/agent-memory-column-redesign.md` (v2) with Nick's two rulings from 2026-10-07: keep the "last N user messages" slider (one or several turns as the long-term memory search context) and keep every function that explains the Iris interface visible; hide only rendering code.

Builders apply one stage each, exactly as written (whitespace and comments may differ; names, signatures, logic, and prose may not). Reviewers compare the live cells against this file.

## Ground rules for every builder

- The kernel is the source of truth. Do not edit `iris_basics.py` on disk. Do not touch cells outside column 4 except the setup cell `Hbol` in stage 1.
- Read every cell you will edit inside the same `cm.get_context()` block before calling `edit_cell` (stale-cell check). Stored cell code has no trailing newline.
- Queue all of a stage's `edit_cell`, `create_cell`, `delete_cell`, and `run_cell` calls in one context block, so marimo validates the final graph once. If validation fails, nothing is applied: read the error, fix, retry.
- `create_cell` lands at the end of column 0; `move_cell` it afterwards (stage 3 does the ordering for everything).
- Run from Git Bash. Prepend jq to PATH, write scripts to `$LOCALAPPDATA/Temp`, ASCII only inside scripts (the notebook prose below is ASCII already):

```bash
export PATH="$(cygpath "$LOCALAPPDATA")/Microsoft/WinGet/Packages/jqlang.jq_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
bash "C:/Users/NickCooper/projects/demo/.claude/skills/marimo-pair/scripts/execute-code.sh" --url http://127.0.0.1:2718/ -c "import marimo._code_mode as cm; help(cm)"
bash "C:/Users/NickCooper/projects/demo/.claude/skills/marimo-pair/scripts/execute-code.sh" --url http://127.0.0.1:2718/ "$(cygpath -w "$LOCALAPPDATA/Temp/stage.py")"
```

- Put each cell's code in the script as a plain triple-quoted string and pass it to `ctx.edit_cell(name, code=..., hide_code=..., name=...)`. Where a cell is reused under a new name, pass `name="<new name>"` in the same `edit_cell` call.
- After the context block exits, run a second script that prints, for every column-4 cell, `name`, `status`, `errors`, and the first 300 characters of `output.data` (HTML-unescaped, tags stripped). Fix any `exception` status before reporting.
- Do not send chat messages, press Clean up, or delete memories unless the stage says so. Pressing Seed is safe (it skips texts already stored).

## Final cell order (16 cells, column 4)

| # | Cell | hide_code | Reused from |
|---|---|---|---|
| 1 | `am_title` | True | `am_title` |
| 2 | `am_connect` | False | `am_connect` (unchanged) |
| 3 | `am_store_md` | True | `am_try_it` |
| 4 | `am_store_button` | True | `am_seed` |
| 5 | `am_store` | True | `am_cleanup` |
| 6 | `am_store_fns` | False | `am_amnesia` |
| 7 | `am_probe` | True | `am_read_code` |
| 8 | `am_sweep` | True | new |
| 9 | `am_probe_panel` | True | new |
| 10 | `am_recall_fns` | False | `am_steps` |
| 11 | `am_owner` | True | `am_controls` |
| 12 | `am_chat` | False | `am_chat` |
| 13 | `am_turn_sweep` | True | `am_state` |
| 14 | `am_prompt_panel` | True | `am_trace` |
| 15 | `am_prompt_fns` | False | `am_chat_fn` |
| 16 | `am_render` | True | `am_memories` |

Deleted: `am_seed_fn`. The kernel ids today: `am_title` Xref, `am_connect` SFPL, `am_try_it` IMKF, `am_controls` Shga, `am_seed` Kclp, `am_cleanup` pDtD, `am_memories` HaRf, `am_state` RGSE, `am_chat` LNbO, `am_trace` yUGJ, `am_read_code` Xfup, `am_seed_fn` xTVY, `am_steps` Wvet, `am_chat_fn` BYtC, `am_amnesia` emfo, setup `Hbol`. Use names, and confirm ids inside the script before editing.

## Public names after the build

`ams_client`, `AgentMemory`, `models`, `dt`, `secrets` (unchanged, `am_connect`); `ams_seeds`, `ams_memories`, `ams_seed`, `ams_forget`; `ams_seed_button`, `ams_refresh_button`, `ams_cleanup_button`; `ams_store`; `ams_probe`; `ams_probe_bands`; `ams_request`, `ams_recall`, `ams_sweep`; `ams_owner`; `ams_threshold`, `ams_turns`, `ams_session_id`, `ams_turn`, `ams_chatbot`; `ams_turn_query`, `ams_turn_bands`; `ams_query`, `ams_remember`, `ams_build_system`; `ams_bars`, `ams_memory_table`.

Gone: `AMS_SEEDS`, `amnesia`, `ams_chat`, `ChatFn`, `ams_remember_turn`, `chatbot`, `turns`, `similarity_threshold`, `ams_turn_log`, `ams_sweep_cache`, `get_threshold`, `set_threshold`, `get_turns`, `set_turns`, `get_memories_version`, `set_memories_version`.

---

## Stage 1: setup imports, store section, owner, render helpers

### `Hbol` (setup, column 0): add two imports

Append `import html` and `import json` to the stdlib group, keeping the existing order. Final code:

```python
from collections.abc import AsyncGenerator, Callable
import asyncio
import html
import itertools as it
import json
import os
import uuid

import redis

from marimo import ai
import marimo as mo
```

### `am_amnesia` -> `am_store_fns` (hide_code=False)

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
    """Every long-term memory whose `ownerId` is `owner`, across pages."""
    found: list[models.MemoryRecord] = []
    page_token: str | None = None

    while True:
        r = await ams_client.search_long_term_memory_async(
            request={
                "filter": {"ownerId": {"eq": owner}},
                "limit": 100,
                **({"pageToken": page_token} if page_token else {}),
            }
        )
        found += r.items
        page_token = r.next_page_token

        if not page_token:
            return found


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

    await asyncio.gather(
        *(
            ams_client.bulk_delete_long_term_memories_async(memory_ids=memory_ids[i : i + 100])
            for i in range(0, len(memory_ids), 100)
        )
    )

    return memory_ids
```

### `am_seed_fn`: delete

### `am_try_it` -> `am_store_md` (hide_code=True)

```python
mo.md(r"""
## What long-term memory holds

Seed writes three memories per customer with `POST /long-term-memory`, each tagged with an `ownerId` the app chooses.
Clean up deletes every memory whose `ownerId` is Tom or Priya. Chat sessions are not deleted; the store expires them after an hour by default.
""")
```

### `am_seed` -> `am_store_button` (hide_code=True)

```python
ams_seed_button = mo.ui.run_button(label="Seed memories")
ams_refresh_button = mo.ui.run_button(label="Refresh")
ams_cleanup_button = mo.ui.run_button(label="Clean up", kind="danger")

mo.hstack([ams_seed_button, ams_refresh_button, ams_cleanup_button], justify="start")
```

### `am_cleanup` -> `am_store` (hide_code=True)

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
_ = ams_refresh_button.value  # Refresh re-lists, for memories extracted in the background

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
        ),
        mo.md(
            "Agent Memory also extracts memories from session events in the background, "
            "when extraction is enabled on the store. Select Refresh to look for new ones."
        ),
    ]
)
```

### `am_controls` -> `am_owner` (hide_code=True)

```python
ams_owner = mo.ui.dropdown(options=list(ams_seeds), value=USER_ID, label="Chat as")

mo.vstack(
    [
        mo.md("## Chat as Tom or Priya"),
        mo.md(
            "Ask the three starter questions as Tom, then switch to Priya and ask them again. "
            "Switching customers starts a new conversation."
        ),
        ams_owner,
    ]
)
```

### `am_memories` -> `am_render` (hide_code=True)

Rendering helpers only. The bar spec follows the dataviz skill: bars 12px thick with a 4px rounded data-end and a square baseline, a neutral hairline track, one accent hue (blue `#2a78d6`) for memories at or above the line and the same hue's light step (`#9ec5f4`) below it, a 2px ink line for the slider, text in ink tokens with tabular numerals, no legend (one series), values at the bar tip (three bars, so every bar is labelled).

```python
def ams_bars(bands: dict[str, float], threshold: float) -> mo.Html:
    """One row per memory: a bar to the highest threshold it still clears, and the slider as a line."""
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
    body = "".join(rows) or '<div class="ams-empty">No memories for this owner.</div>'
    css = """
<style>
.ams-bars { font: 13px system-ui, -apple-system, "Segoe UI", sans-serif; color: #52514e; }
.ams-row { display: grid; grid-template-columns: minmax(0, 1fr) 2fr 3.2em; gap: 0 8px; align-items: center; margin: 6px 0; }
.ams-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ams-track { position: relative; height: 12px; background: #e1e0d9; border-radius: 0 4px 4px 0; }
.ams-bar { position: absolute; left: 0; top: 0; height: 100%; background: #2a78d6; border-radius: 0 4px 4px 0; }
.ams-bar-below { background: #9ec5f4; }
.ams-line { position: absolute; top: -4px; height: 20px; width: 2px; background: #0b0b0b; }
.ams-value { color: #0b0b0b; font-variant-numeric: tabular-nums; text-align: right; }
.ams-ticks { display: flex; justify-content: space-between; color: #898781; font-size: 11px; border-top: 1px solid #c3c2b7; padding-top: 2px; }
.ams-caption { color: #898781; font-size: 11px; margin-top: 4px; }
.ams-empty { color: #898781; }
</style>
"""
    return mo.Html(
        '<div class="ams-bars">'
        + body
        + '<div class="ams-row"><div></div><div class="ams-ticks"><span>0</span><span>0.5</span><span>1</span></div><div></div></div>'
        + f'<div class="ams-caption">Highest similarityThreshold the memory still clears. Line: slider at {threshold:.2f}.</div>'
        + "</div>"
        + css
    )


def ams_memory_table(owner: str, found: list[models.MemoryRecord]) -> mo.Html:
    """One owner's long-term memories as a table, headed with the `ownerId`."""
    rows = [
        {"memory": m.text, "type": m.memory_type, "created (UTC)": m.created_at.strftime("%H:%M:%S")}
        for m in found
    ]
    table = (
        mo.ui.table(rows, selection=None, pagination=False, show_search=False, show_download=False)
        if rows
        else mo.md("_No memories yet._")
    )
    return mo.vstack([mo.md(f"**ownerId: {owner}** ({len(found)})"), table])
```

### Stage 1 run list

`run_cell` for `Hbol`, `am_store_fns`, `am_render`, `am_store_md`, `am_store_button`, `am_owner`, `am_store`. The untouched `am_chat`, `am_trace`, `am_state`, `am_chat_fn`, `am_steps` keep working: `ams_owner` still exists (now from `am_owner`), and nothing else they read moved.

### Stage 1 acceptance

- `am_store` output shows two tables, `ownerId: Tom (3)` and `ownerId: Priya (3)`, side by side, and the extraction sentence.
- No cell in the notebook has status `exception`. `ctx.graph` shows `AMS_SEEDS`, `amnesia`, `get_memories_version`, `set_memories_version` defined nowhere.
- `ams_bars({"a": 0.35, "b": 0.05}, 0.3)` evaluated in the scratchpad returns `mo.Html` whose text contains `0.35`, `0.05`, and `slider at 0.30`.

---

## Stage 2: recall and prompt functions, chat, turn sweep, prompt panel

### `am_steps` -> `am_recall_fns` (hide_code=False)

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


async def ams_sweep(query: str, store: dict[str, list[models.MemoryRecord]]) -> dict[str, dict[str, float]]:
    """For each owner in `store`, the highest threshold each memory still clears. The API returns no score."""
    grid = [round(0.05 * i, 2) for i in range(21)]  # 0.00 to 1.00
    pairs = [(owner, t) for owner in store for t in grid]
    hits = await asyncio.gather(*(ams_recall(query, owner, t) for owner, t in pairs))

    bands = {owner: {m.text: 0.0 for m in records} for owner, records in store.items()}
    for (owner, t), found in zip(pairs, hits):
        for m in found:
            bands[owner][m.text] = max(bands[owner].get(m.text, 0.0), t)

    return bands
```

### `am_chat_fn` -> `am_prompt_fns` (hide_code=False)

```python
def ams_query(messages: list[ai.ChatMessage], n: int) -> str:
    """The text to search with: the last `n` user messages, newest first."""
    recent = it.islice((m for m in reversed(messages) if m.role == "user"), n)
    return "\n".join(str(m.content) for m in recent)


async def ams_remember(session_id: str, owner: str, text: str) -> None:
    """Principle 3: write the user's message to session memory."""
    await ams_client.add_session_event_async(
        session_id=session_id,
        actor_id=owner,
        role=models.MessageRole.USER,
        content=[{"text": text}],
        created_at=dt.datetime.now(dt.timezone.utc),
    )


def ams_build_system(owner: str, facts: list[str]) -> str:
    """Principle 3: the app, not Agent Memory, decides what the model sees."""
    system = "You are a helpful AI assistant."

    if facts:
        system += f"\n\nWhat you remember about {owner}:\n" + "\n".join(f"- {fact}" for fact in facts)

    return system
```

### `am_chat` (hide_code=False)

```python
ams_threshold = mo.ui.slider(
    start=0.0, stop=1.0, step=0.05, value=0.3, show_value=True, debounce=True,
    label="similarityThreshold, for the bars above and the chat",
)
ams_turns = mo.ui.slider(
    start=1, stop=10, step=1, value=1, show_value=True, debounce=True,
    label="Search with the last N user messages",
)
ams_session_id = secrets.token_hex(8)  # am_owner is upstream, so a customer switch reruns this cell: new session, empty history


async def ams_turn(messages: list[ai.ChatMessage], config: ai.ChatModelConfig) -> AsyncGenerator[str, None]:
    """One agent turn. Principle 3: the app, not the service, decides what the model sees."""
    owner = ams_owner.value

    # Session memory: the user's message, as an event in this session.
    await ams_remember(ams_session_id, owner, str(messages[-1].content))

    # Long-term memory: this owner's memories, by meaning, searched with the last N user messages.
    query = ams_query(messages, ams_turns.value)
    recalled = await ams_recall(query, owner, round(ams_threshold.value, 2))

    # The prompt: the app puts the recalled facts in front of the model.
    system = ams_build_system(owner, [m.text for m in recalled])
    history = [{"role": m.role, "content": str(m.content)} for m in messages if m.role in ("user", "assistant")]

    async for text in stream_llm(system, history, config):
        yield text


ams_chatbot = mo.ui.chat(
    ams_turn,
    prompts=["What pets do I have?", "Where am I headed this summer?", "Which foods should I avoid?"],
)

mo.vstack([mo.hstack([ams_threshold, ams_turns], justify="start"), ams_chatbot])
```

### `am_state` -> `am_turn_sweep` (hide_code=True)

```python
# The search text the last turn used, and how high a threshold each of this owner's memories clears for it.
_user_messages = [m for m in ams_chatbot.value if m.role == "user"]
ams_turn_query = ams_query(ams_chatbot.value, ams_turns.value) if _user_messages else ""

if ams_turn_query:
    ams_turn_bands = (await ams_sweep(ams_turn_query, {ams_owner.value: ams_store[ams_owner.value]}))[ams_owner.value]
else:
    ams_turn_bands = {}
```

### `am_trace` -> `am_prompt_panel` (hide_code=True)

```python
_turn_done = bool(ams_chatbot.value) and ams_chatbot.value[-1].role == "assistant"
_t = round(ams_threshold.value, 2)

if not _turn_done:
    _view = mo.md("_Send a message to see the search text, the recall, and the prompt for the last turn._")
else:
    _recalled = [text for text, band in ams_turn_bands.items() if round(band, 2) >= _t]
    _session = await ams_client.get_session_memory_async(session_id=ams_session_id)
    _n = ams_turns.value
    _view = mo.vstack(
        [
            mo.md(f"**Search text**, the last {_n} user message{'s' if _n > 1 else ''}:"),
            mo.md(f"```text\n{ams_turn_query}\n```"),
            mo.md(f"**Recall for ownerId {ams_owner.value} at {_t:.2f}.** The recall and the prompt follow the slider."),
            ams_bars(ams_turn_bands, _t),
            mo.md("**System prompt sent to the model**"),
            mo.md(f"```text\n{ams_build_system(ams_owner.value, _recalled)}\n```"),
            mo.md(f"**Session `{ams_session_id}` as the service holds it** ({len(_session.events)} events)"),
            mo.ui.table(
                [
                    {"role": e.role.value, "text": e.content[0].text, "created (UTC)": e.created_at.strftime("%H:%M:%S")}
                    for e in _session.events
                ],
                selection=None,
                pagination=False,
                show_search=False,
                show_download=False,
            ),
        ]
    )

mo.vstack([mo.md("### 3. What the model received"), _view])
```

### Stage 2 run list

`run_cell` for `am_recall_fns`, `am_prompt_fns`, `am_chat`, `am_turn_sweep`, `am_prompt_panel`. `am_read_code` and `am_title` are untouched until stage 3.

### Stage 2 acceptance

- No cell has status `exception`. `ams_chat`, `ChatFn`, `ams_remember_turn`, `get_threshold`, `set_threshold`, `get_turns`, `set_turns`, `ams_turn_log`, `ams_sweep_cache`, `chatbot`, `turns`, `similarity_threshold` are defined nowhere.
- `am_prompt_panel` shows the heading and the placeholder.
- Scratchpad: `await ams_sweep("Which foods should I avoid?", ams_store)` returns Priya's allergy at 0.35 and Tom's three memories at 0.05, in about one to two seconds.
- Scratchpad: `ams_request("q", "Tom", 0.3)` equals `{"text": "q", "similarityThreshold": 0.3, "filter": {"ownerId": {"eq": "Tom"}}, "limit": 10}`.
- Do not send a chat message in this stage.

---

## Stage 3: probe, probe panel, title, order

### `am_read_code` -> `am_probe` (hide_code=True)

```python
ams_probe = mo.ui.text(
    value="Which foods should I avoid?",
    label="Search long-term memory with",
    full_width=True,
    debounce=True,
)

mo.vstack(
    [
        mo.md("## Search long-term memory"),
        mo.md(
            "The bars below come from the search API alone, before any chat. "
            "The line is the `similarityThreshold` slider under the chat."
        ),
        ams_probe,
    ]
)
```

### new `am_sweep` (hide_code=True)

```python
# The probe's search at 21 thresholds for every owner. Depends on the text and the store, not on the slider.
if ams_probe.value.strip():
    ams_probe_bands = await ams_sweep(ams_probe.value, ams_store)
else:
    ams_probe_bands = {owner: {} for owner in ams_store}
```

### new `am_probe_panel` (hide_code=True)

```python
_t = round(ams_threshold.value, 2)

mo.vstack(
    [
        mo.md("### 1 and 2. Which memories clear the threshold"),
        mo.hstack(
            [
                mo.vstack(
                    [
                        mo.md(f"**ownerId: {_owner}**"),
                        ams_bars(_bands, _t),
                        mo.md(f"```json\n{json.dumps(ams_request(ams_probe.value, _owner, _t), indent=2)}\n```"),
                    ]
                )
                for _owner, _bands in ams_probe_bands.items()
            ],
            widths="equal",
            align="start",
        ),
        mo.md(
            "The search API returns no similarity score. The notebook ran the same search at 21 thresholds, "
            "0.00 to 1.00 in steps of 0.05. Each bar ends at the highest one that still returned the memory, "
            "so its similarity lies between that value and the next step up. "
            "Memories at or above the line come back at the slider's setting."
        ),
    ]
)
```

### `am_title` (hide_code=True)

```python
mo.md(r"""
# Redis Agent Memory

An agent that forgets a customer's two dogs between conversations asks about them again every time.
Redis Agent Memory keeps each conversation in session memory and durable facts in long-term memory, written by the app or extracted from session events.
Seed memories for two made-up customers, Tom and Priya, then search them and chat as either to see three principles:

1. **Long-term memory recalls by meaning.** "Which foods should I avoid?" shares no word with "allergic to peanuts and tree nuts", and the search returns it for Priya. The bars under the search box show the highest `similarityThreshold` each memory clears, so raising the slider drops the weakest matches first.
2. **The `ownerId` filter scopes recall to one customer.** The app sends `filter: {ownerId: ...}` with every search, so Tom's agent does not see Priya's allergy. The same search runs as Tom and as Priya, side by side.
3. **The app builds the prompt.** Each turn writes the message to session memory and searches long-term memory with the last N user messages. The hits go into the system prompt, so the model sees a memory only because the app put it there. The panel under the chat shows the search text, the bars for it, and the prompt the model received.

Agent Memory is in preview on Redis Cloud. Settings: `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY`, plus `LLM_API_KEY`, which the chat needs and which must match the provider of `AI_MODEL`.
Docs: [overview](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/overview/),
[Python SDK quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/), and
[developer guide](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/developer-guide/).
""")
```

The three docs URLs are the ones in today's title; copy them from the live cell if they differ from the above.

### Ordering and flags

After the edits, `move_cell` so the column reads in the "Final cell order" table above: `am_title`, `am_connect`, `am_store_md`, `am_store_button`, `am_store`, `am_store_fns`, `am_probe`, `am_sweep`, `am_probe_panel`, `am_recall_fns`, `am_owner`, `am_chat`, `am_turn_sweep`, `am_prompt_panel`, `am_prompt_fns`, `am_render`. Move the two new cells out of column 0 first (`move_cell(new, after="am_probe")`, then the next). Then set `hide_code` per the table with `edit_cell(name, hide_code=...)` for any cell that differs. Confirm with `ctx.cells` order and `config.hide_code` for every column-4 cell, and confirm no column-4 cell is left in column 0.

### Stage 3 run list

`run_cell` for `am_probe`, `am_sweep`, `am_probe_panel`, `am_title`.

### Stage 3 acceptance

- `am_probe_panel` output text contains `ownerId: Tom`, `ownerId: Priya`, `0.35` (Priya's allergy), `slider at 0.30`, `"similarityThreshold": 0.3`, and the footnote.
- Setting the probe text to "What pets do I have?" through `ctx.set_ui_value(ams_probe, "What pets do I have?")` in one call, then reading the output in a second call, shows `0.40` for both Tom's dogs and Priya's cat.
- Setting `ams_threshold` to 0.45 the same way moves the line label to `slider at 0.45` and the request body to `0.45`, and `am_chat` does not rerun (its `ams_session_id` is unchanged). Set it back to 0.3.
- No cell has status `exception`; column order and `hide_code` flags match the table.

---

## Stage 4: live verification, extraction test, file, tests

1. **Store.** Press Seed via `ctx.set_ui_value(ams_seed_button, True)`; the note says every seed memory is already stored. Press Refresh the same way; tables unchanged.
2. **Chat as Tom.** Drive three turns through the chat model: `ams_chatbot._model` is `ams_turn`; the established way is to build `ai.ChatMessage` objects and call the model, or use `ctx.set_ui_value(ams_chatbot, ...)` if the chat accepts a messages list. Use the three starter prompts in order. After each turn confirm `am_prompt_panel` shows the search text, bars, the system prompt with the recalled memory ("two dogs" for the pets question, "Dolomites" for the summer question, nothing for the foods question), and the session table with one more USER event. The LLM is `gpt-4o-mini` through `LLM_API_KEY`; each turn costs one call.
3. **N slider.** Set `ams_turns` to 3. The search text in the panel shows three lines and the bars move; note the new bands. Set it back to 1.
4. **Switch to Priya.** `ctx.set_ui_value(ams_owner, ["Priya"])` (a dropdown takes a list). `am_chat` reruns: `ams_session_id` changes and `ams_chatbot.value` is empty; `am_prompt_panel` shows the placeholder. Ask the foods question as Priya; the system prompt carries the allergy.
5. **Extraction test.** As Priya, send "I just adopted a parrot named Rio and I am moving to Denver in May." Then wait 6 minutes (`sleep 360` in the shell, not in the kernel), press Refresh, and read `ams_store["Priya"]`. Report whether any memory with a `session_id` or a text about Rio or Denver appeared. Do not delete it; Nick decides.
6. **Clean up is not pressed.** Leave Tom and Priya with their seed memories plus whatever extraction added.
7. **Count panel reruns.** Note how many times `am_prompt_panel` ran for one turn, by watching `ctx.cells["am_prompt_panel"].status` or by comparing `output.data` timestamps if available. Expected: about two.
8. **File.** Find the marimo server's command line with PowerShell: `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'marimo' } | Select-Object ProcessId, CommandLine`. Record it. Confirm `iris_basics.py` on disk already contains the new cells (marimo saves on every cm edit). Stop the server process (and its `uv` parent if there is one). Then in the file, add `column=4` to every column-4 cell's decorator that lacks it (`@app.cell` -> `@app.cell(column=4)`, `@app.cell(hide_code=True)` -> `@app.cell(column=4, hide_code=True)`), keeping CRLF line endings (`Path.read_text`/`write_text` keep them). Also `column=0` on `redis_client` and `llm_client` if they lack it. Run `uv run python -m marimo check --strict iris_basics.py` (must exit 0) and `uv run pytest tests -q --ignore=tests/live_smoke.py` (expect the stale failures listed in the proposal; report the exact list). Relaunch the server with the recorded command line from the same directory in a new PowerShell window (`Start-Process powershell -ArgumentList '-NoExit', '-Command', '<command>'`) and confirm http://127.0.0.1:2718/ answers.
9. **Screenshots (optional).** If `uv run --with playwright python x.py` with `chromium.launch(channel="msedge", headless=True)` works, save PNGs of the probe panel and the prompt panel to `.agents/reviews/am-build-shots/`. marimo elements sit in open shadow roots; Playwright locators pierce them.

Report every number observed, every deviation from the spec, and the pytest failure list.

Stage 4 ran as two agents: 4a (steps 1 to 7 and 9, live, no server restart) and 4b (step 8 only, after every reviewer finished).

---

## Stage 5: one workbench for the chat and the data behind it (Nick, 2026-10-07 afternoon)

Nick: "consolidate the chat and diagram interfaces: chat, change the thresholds, and see the data behind the chat as we interact with the AI." Design: the chat, the two sliders, and the owner dropdown are still *defined* in their own cells (`am_owner`, `am_chat`, with `am_chat`'s code visible as the lesson), but they are *displayed* in one new hidden cell, `am_workbench`, side by side with the data pane. marimo renders a UI element wherever it appears in an output, and a cell may display elements it did not define; it may only not read `.value` of elements it defines. The workbench reruns on every chat value push, owner switch, and slider move (through the state getters), so the data pane follows the conversation. Before the first message the pane previews the first starter prompt, so the bars render on load with no LLM call. The separate search box (`am_probe`, `am_sweep`, `am_probe_panel`) goes; `am_turn_sweep` now sweeps every customer for the current search text, which keeps goal 2 (the other customer's bars) on screen beside the chat.

Final order, 14 cells: `am_title`, `am_connect`, `am_store_md`, `am_store_button`, `am_store`, `am_store_fns`, `am_owner`, `am_settings`, `am_chat`, `am_turn_sweep`, `am_workbench`, `am_recall_fns`, `am_prompt_fns`, `am_render`. Code visible on `am_connect`, `am_store_fns`, `am_chat`, `am_recall_fns`, `am_prompt_fns`.

Known trade-off to watch in the browser: the chat component re-renders each time the workbench reruns (after each reply and after each slider move). Its history is its value and survives; a typed-but-unsent draft may not. If that is visible and annoying, the fallback is to keep `ams_chatbot` displayed in `am_chat` and put only the data in the cell below.

### `am_owner` (hide_code=True): defines the dropdown, shows prose only

```python
ams_owner = mo.ui.dropdown(options=list(ams_seeds), value=USER_ID, label="Chat as")

mo.md(r"""
## Chat, and the data behind each turn

Ask the first three starter prompts as Tom, then switch to Priya and ask them again. Switching customers starts a new conversation.
The fourth prompt tells the agent a new fact, and Refresh can show the memory extracted from it a few minutes later.
Until you send a message, the panel beside the chat previews the first starter prompt.
""")
```

### `am_settings` (hide_code=True): unchanged

### `am_chat` (hide_code=False): defines, does not display

```python
ams_threshold = mo.ui.slider(
    start=0.0, stop=1.0, step=0.05, value=get_threshold(), show_value=True, debounce=True,
    label="similarityThreshold", on_change=set_threshold,
)
ams_turns = mo.ui.slider(
    start=1, stop=10, step=1, value=get_turns(), show_value=True, debounce=True,
    label="Search with the last N user messages", on_change=set_turns,
)
ams_session_id = secrets.token_hex(8)  # am_owner is upstream, so a customer switch reruns this cell: new session, empty history
ams_prompts = ["What pets do I have?", "Where am I headed this summer?", "Which foods should I avoid?", "I'm training for a marathon in October."]


async def ams_turn(messages: list[ai.ChatMessage], config: ai.ChatModelConfig) -> AsyncGenerator[str, None]:
    """One agent turn. Principle 3: the app, not the service, decides what the model sees."""
    owner = ams_owner.value

    # Session memory: the user's message, as an event in this session.
    await ams_remember(ams_session_id, owner, str(messages[-1].content))

    # Long-term memory: this owner's memories, by meaning, searched with the last N user messages.
    query = ams_query(messages, get_turns())
    recalled = await ams_recall(query, owner, round(get_threshold(), 2))

    # The prompt: the app puts the recalled facts in front of the model.
    system = ams_build_system(owner, [m.text for m in recalled])
    history = [{"role": m.role, "content": str(m.content)} for m in messages if m.role in ("user", "assistant")]

    async for text in stream_llm(system, history, config):
        yield text


# Displayed beside the data in am_workbench.
ams_chatbot = mo.ui.chat(ams_turn, prompts=ams_prompts, max_height=520)
```

### `am_turn_sweep` (hide_code=True)

```python
# The search text behind the last turn (the first starter prompt before any chat), and how high a threshold
# each memory clears for it, for every customer. Depends on the chat, N, and the store, not on the threshold.
ams_turn_live = any(m.role == "user" for m in ams_chatbot.value)
ams_turn_query = ams_query(ams_chatbot.value, get_turns()) if ams_turn_live else ams_prompts[0]
ams_turn_bands = await ams_sweep(ams_turn_query, ams_store)
```

### new `am_workbench` (hide_code=True), after `am_turn_sweep`

```python
_t = round(get_threshold(), 2)
_n = get_turns()
_owner = ams_owner.value
_turn_done = bool(ams_chatbot.value) and ams_chatbot.value[-1].role == "assistant"
_recalled = [text for text, band in ams_turn_bands[_owner].items() if round(band, 2) >= _t]

_data = [
    mo.md(
        f"**Search text**, the last {_n} user message{'s' if _n > 1 else ''}:"
        if ams_turn_live
        else "**Search text**, a preview of the first starter prompt until you send a message:"
    ),
    mo.md(f"```text\n{ams_turn_query}\n```"),
    mo.md(f"**Recall for ownerId {_owner} at {_t:.2f}.** Memories above the line go into the prompt."),
    ams_bars(ams_turn_bands[_owner], _t),
]
for _other in (o for o in ams_turn_bands if o != _owner):
    _data += [mo.md(f"**The same search as ownerId {_other}**"), ams_bars(ams_turn_bands[_other], _t)]
_data += [
    mo.md("**System prompt the app built for this recall**"),
    mo.md(f"```text\n{ams_build_system(_owner, _recalled)}\n```"),
]
if _turn_done:
    _session = await ams_client.get_session_memory_async(session_id=ams_session_id)
    _data += [
        mo.md(f"**Session `{ams_session_id}` in Agent Memory** ({len(_session.events)} events)"),
        mo.ui.table(
            [
                {"role": e.role.value, "text": e.content[0].text, "created (UTC)": e.created_at.strftime("%H:%M:%S")}
                for e in _session.events
            ],
            selection=None,
            pagination=False,
            show_search=False,
            show_download=False,
        ),
    ]
_data.append(
    mo.accordion(
        {
            "Request as sent": mo.md(f"```json\n{json.dumps(ams_request(ams_turn_query, _owner, _t), indent=2)}\n```"),
            "How the bars are measured": mo.md(
                "The search API returns no similarity score. The notebook ran the same search at 21 thresholds, "
                "0.00 to 1.00 in steps of 0.05. Each bar ends at the highest one that still returned the memory, "
                "so its similarity lies between that value and the next step up. "
                "Memories at or above the line are recalled at the slider's setting."
            ),
        }
    )
)

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

### `am_title`: three sentence changes

- Intro line: "Select Seed to write memories for two made-up customers, Tom and Priya, then chat as either to see three principles:"
- Goal 1, second sentence: "The bars beside the chat show the highest `similarityThreshold` each memory clears, so raising the slider moves the line past the weakest memories first."
- Goal 2, last sentence: "The bars beside the chat show the same search for both customers."
- Goal 3, last sentence: "Beside the chat you see the search text, the bars for it, and the prompt the app builds."

### Deletes and moves

Delete `am_probe`, `am_sweep`, `am_probe_panel` (nothing else references `ams_probe` or `ams_probe_bands`; check `ctx.graph` first). Create `am_workbench` (it will land at the end of column 0), then `move_cell("am_workbench", after="am_turn_sweep")` and `move_cell("am_recall_fns", after="am_workbench")` in a second context block after the edits are applied. Confirm the final order above and that no column-4 cell sits among `_`, `redis_client`, `llm_client`, `rs_title`, `lc_title`, `cr_title`.

### Stage 5 as built (2026-10-07)

- `am_prompt_panel` was deleted too (the workbench replaces it; the final order above never listed it).
- `ams_prompts` starts with "Which foods should I avoid?", so the on-load preview is the owner-contrast question the title cites (Priya's allergy at 0.35, Tom nothing). The builder's acceptance check for "0.35" assumed this order.
- Four title sentences changed, not three (the intro line plus one per goal).
- After the stage 5 review: `ams_sweep` runs its 42 searches behind `asyncio.Semaphore(12)` (1.3 s measured), so a burst cannot take the workbench, and with it the chat, down; the request body sits outside the accordion as "**Request as sent for ownerId X**" so the slider's effect on `similarityThreshold` is visible (the accordion keeps only "How the bars are measured"); bar labels wrap instead of truncating (`minmax(0, 3fr) 2fr 3.2em`, `overflow-wrap: anywhere`); one term for one event: a threshold "returns" a memory (caption "Highest similarityThreshold at which the memory is returned", footnote, docstrings, title goal 1); "starter messages" instead of "starter prompts" so "prompt" means only the system prompt; the search label reads "the last user message" when N is 1; the owner heading is "Chat and the data behind each turn".

### Stage 5 run list and acceptance

Run `am_owner`, `am_chat`, `am_turn_sweep`, `am_workbench`, `am_title`. Then:

- No column-4 cell has status `exception`. `ams_probe`, `ams_probe_bands` are defined nowhere; `ams_prompts`, `ams_turn_live` are defined once.
- `am_workbench` output text contains "Chat as", "similarityThreshold", "preview of the first starter prompt", "Which foods should I avoid?", "Recall for ownerId Tom at 0.30", "The same search as ownerId Priya", "0.35" (Priya's allergy), "You are a helpful AI assistant.", "Request as sent".
- `am_chat` has no output (its last statement is an assignment). `am_owner` shows the heading and prose only.
- Slider check through `ctx.set_ui_value(ams_threshold, 0.45)` in one call, read in the next: `ams_session_id` unchanged, `am_workbench` text says "at 0.45", then restore 0.3.
- Owner check: `ctx.set_ui_value(ams_owner, ["Priya"])`, read: `ams_session_id` changed, workbench says "Recall for ownerId Priya", "The same search as ownerId Tom"; restore `["Tom"]`.

## Stage 6: demo first, Iris calls second, machinery last (Nick, 2026-10-07 evening)

Nick: group the marimo machinery in one hidden area and the Iris implementation in another, so a reader separates "interactive demo" from "Redis implementation for agentic memory". Two changes make that real:

1. The turn function leaves `am_chat`. A new visible cell `am_turn_fn` holds `ams_session_id` and `ams_turn`. It reads `ams_owner.value`, so it reruns on a customer switch, which gives a new session id and, through `ams_turn`, a fresh `ams_chatbot`. `am_chat` keeps only the sliders, the starter messages, and the chat object, and its code is hidden.
2. The column is reordered: outputs first, then five visible Iris cells, then the hidden machinery. marimo renders a hidden cell's output where the cell sits, whatever the order of its inputs.

Final order, 17 cells:

| # | Cell | hide_code | Section |
|---|---|---|---|
| 1 | `am_title` | True | demo |
| 2 | `am_store_md` | True | demo |
| 3 | `am_store_button` | True | demo |
| 4 | `am_store` | True | demo |
| 5 | `am_owner` | True | demo |
| 6 | `am_workbench` | True | demo |
| 7 | `am_build_md` | True | Iris (lead-in) |
| 8 | `am_connect` | False | Iris |
| 9 | `am_store_fns` | False | Iris |
| 10 | `am_recall_fns` | False | Iris |
| 11 | `am_prompt_fns` | False | Iris |
| 12 | `am_turn_fn` | False | Iris |
| 13 | `am_machinery_md` | True | machinery (lead-in) |
| 14 | `am_settings` | True | machinery |
| 15 | `am_chat` | True | machinery |
| 16 | `am_turn_sweep` | True | machinery |
| 17 | `am_render` | True | machinery |

Graph check before building: `am_turn_fn` refs `ams_owner`, `secrets`, `ams_remember`, `ams_query`, `ams_recall`, `ams_build_system`, `get_threshold`, `get_turns`, `stream_llm`, `ai`, `AsyncGenerator`. `am_chat` refs `ams_turn`, `get_threshold`, `set_threshold`, `get_turns`, `set_turns`, `mo`. No cycle: `am_owner` -> `am_turn_fn` -> `am_chat` -> `am_turn_sweep` -> `am_workbench`. The RPC still runs in `am_chat`'s context; `ams_turn` reads `ams_owner.value` (defined in `am_owner`, allowed) and the state getters (no guard), never `ams_threshold.value`.

### new `am_turn_fn` (hide_code=False)

```python
ams_session_id = f"{ams_owner.value.lower()}-{secrets.token_hex(6)}"  # a new session each time the customer changes


async def ams_turn(messages: list[ai.ChatMessage], config: ai.ChatModelConfig) -> AsyncGenerator[str, None]:
    """One agent turn. Principle 3: the app, not the service, decides what the model sees."""
    owner = ams_owner.value

    # Session memory: the user's message, as an event in this session.
    await ams_remember(ams_session_id, owner, str(messages[-1].content))

    # Long-term memory: this owner's memories, by meaning, searched with the last N user messages.
    # get_turns and get_threshold read the two sliders (notebook machinery, at the end of the column).
    query = ams_query(messages, get_turns())
    recalled = await ams_recall(query, owner, round(get_threshold(), 2))

    # The prompt: the app puts the recalled facts in front of the model.
    system = ams_build_system(owner, [m.text for m in recalled])
    history = [{"role": m.role, "content": str(m.content)} for m in messages if m.role in ("user", "assistant")]

    async for text in stream_llm(system, history, config):
        yield text
```

### `am_chat` (hide_code=True): controls and the chat object only

```python
ams_threshold = mo.ui.slider(
    start=0.0, stop=1.0, step=0.05, value=get_threshold(), show_value=True, debounce=True,
    label="similarityThreshold", on_change=set_threshold,
)
ams_turns = mo.ui.slider(
    start=1, stop=10, step=1, value=get_turns(), show_value=True, debounce=True,
    label="Search with the last N user messages", on_change=set_turns,
)
ams_prompts = ["Which foods should I avoid?", "What pets do I have?", "Where am I headed this summer?", "I'm training for a marathon in October."]

# Displayed beside the data in am_workbench. ams_turn is the Iris turn above.
ams_chatbot = mo.ui.chat(ams_turn, prompts=ams_prompts, max_height=520)
```

### new `am_build_md` (hide_code=True)

```python
mo.md(r"""
## The Iris calls behind the demo

Five cells, in the order a turn uses them: client, store, search, prompt, turn. Each function names the principle it serves.
The controls, state, and layout are at the end of the column with their code hidden.
""")
```

### new `am_machinery_md` (hide_code=True)

```python
mo.md(r"""
## Notebook machinery

Controls, state, the threshold sweep, the layout, and the rendering helpers. Their code is hidden. Open a cell to read it.
""")
```

### `am_settings` comment

Replace the two-line comment with: `# The chat reads the latest slider values through these getters. A slider's .value cannot be read in the cell that defines it, and defining the sliders elsewhere would rerun the chat on every move.` (keep the two `mo.state` lines).

### Build order

Block 1 (one context): read `am_chat`, `am_settings`; `create_cell` for `am_turn_fn` (hide_code=False), `am_build_md`, `am_machinery_md` (hide_code=True), capture ids; `edit_cell("am_chat", code=..., hide_code=True)`; `edit_cell("am_settings", ...)`; `run_cell` for `am_turn_fn`, `am_chat`, `am_build_md`, `am_machinery_md`, `am_settings`. Everything that depends on them reruns on its own. Block 2 (new context): `move_cell` into the table order; the three new cells start at the end of column 0. Then confirm the order, the flags, that no column-4 cell sits among `_`, `redis_client`, `llm_client`, `rs_title`, `lc_title`, `cr_title`, and that every cell is `idle` with no errors.

### Stage 6 as built: the getter trap and its fix

As specified, `am_turn_fn` referenced `get_threshold` and `get_turns`. marimo reruns on a setter call every cell whose refs resolve to the State object, excluding only the setter's own cell (`_runtime/runtime.py:1605-1624`). With `ams_turn` in its own cell, a slider move reran `am_turn_fn`, which redefined `ams_turn` and `ams_session_id`, which reran `am_chat` and wiped the history. The builder caught it in the threshold probe. Fix: `am_settings` also defines

```python
def ams_settings() -> tuple[float, int]:
    """The sliders' current values, read at call time. A cell that calls this does not rerun when a slider moves."""
    return round(get_threshold(), 2), get_turns()
```

and `ams_turn` reads `threshold, turns = ams_settings()`. `am_turn_fn` now references the function, not the getters, so it does not rerun on a slider move. Verified: refs `ams_build_system, ams_owner, ams_query, ams_recall, ams_remember, ams_settings`; threshold to 0.45 left `ams_session_id` and the chat value unchanged while the workbench showed "at 0.45"; a kernel-side `send_prompt` as Tom returned ok.

After the stage 6 review: the two lead-ins were rewritten for accuracy (a turn never touches the store cell, only half the functions carry a principle docstring, the layout cell is in the demo section, and "Agent Memory" replaces bare "Iris"). `am_build_md` now reads: "Five cells of SDK calls. The client connects. The store cell seeds, lists, and deletes memories. The search cell runs one search and the sweep behind the bars. The prompt cell writes the session event and builds the system prompt. The turn calls the search and prompt cells in order. In edit mode their code is open, and the notebook machinery is collapsed at the end of the column." `am_machinery_md`: "These cells hold the sliders and the chat object, the state behind them, the sweep that feeds the bars, and the rendering helpers. In edit mode, open a cell to read its code." The `am_settings` comment now says the turn reads the sliders through `ams_settings()` and that a cell referencing the getters reruns on every move.

### Stage 6 acceptance

- `ams_session_id` starts with "tom-" while Tom is selected; `ams_turn` is defined only in `am_turn_fn`; `am_chat` has no output and does not define `ams_turn` or `ams_session_id`.
- Owner probe: `set_ui_value(ams_owner, ["Priya"])`, read: `ams_session_id` starts with "priya-", `ams_chatbot.value` is empty, workbench says "Recall for ownerId Priya"; restore `["Tom"]`.
- Threshold probe: 0.45 then read: `ams_session_id` unchanged, workbench says "at 0.45"; restore 0.3.
- Kernel `send_prompt` as Tom returns `code: ok` (the coordinator runs this).

## Post-review fixes applied by the coordinator (2026-10-07)

These supersede the spec text above; the live kernel holds them.

1. `am_store_md`: "Chat sessions are not deleted. The store expires them after an hour by default." (no semicolon; the one-hour default is on the Redis Cloud create-service docs page, which also gives long-term TTL 365 days and extraction cadence 5 minutes, 60 to 600 s).
2. `am_store_button`: the first button is labelled "Seed".
3. `am_render`: `ams_bars` starts with `threshold = round(threshold, 2)`.
4. `am_title`: "Agent Memory is in public preview on Redis Cloud." (the operate docs say "During public preview").
5. `am_prompt_panel`: bold label "**System prompt the app built for this recall**"; heading "### 3. The prompt the app builds"; placeholder "_Send a message to see what the last turn searched for and the prompt the app built._"; session label "**Session `{id}` in Agent Memory** ({n} events)".
6. `am_probe`: the prose ends "Press Enter to run the search." (`mo.ui.text(debounce=True)` sends its value on Enter or blur only).
7. `am_probe_panel`: when the probe text is empty it renders "_Type a search above and press Enter._" instead of empty bars and a request body that was never sent, and the footnote is hidden in that state.
8. `am_title` wording: "Select Seed to write memories for two made-up customers, Tom and Priya, then search them and chat as either to see three principles:"; goal 1 reads `"Which foods should I avoid?" shares no keyword with "I'm allergic to peanuts and tree nuts", yet the search returns it for Priya. The bars under the search box show the highest `similarityThreshold` each memory clears, so raising the slider moves the line past the weakest matches first.`; goal 3 ends "and the prompt the app builds from the hits."
9. Kept on purpose, flagged for Nick: sentence 2 of the title still opens "Redis Agent Memory", matching the other three column titles, although VOICE.md wants the prefix bare after the H1.
10. `am_probe_panel`: the two owners stack vertically (`mo.vstack`), not side by side. The verifier's screenshot showed that two panes in a 740px column truncate every memory label and push the value labels into the next pane; one pane per owner at full width reads like the prompt panel. Title goal 2 now ends "The search box runs the same search as Tom and as Priya."
11. `am_render`: `.ams-track` gets `min-width: 0` and `.ams-value` gets `white-space: nowrap`.
13. **`mo.state` is back, for the one thing it is for.** Nick's first real chat turn failed with "Accessing the value of a UIElement in the cell that created it is not allowed": the chat RPC runs with the chat cell's execution context installed (`_runtime/runtime.py:2229`), so `ams_turn` cannot read `ams_threshold.value` or `ams_turns.value`, which the same cell defines. Defining the sliders in another cell would rerun the chat on every move. The sanctioned escape is what Nick's original column had: a new hidden cell `am_settings` (after `am_owner`) holds `get_threshold, set_threshold = mo.state(0.3)` and `get_turns, set_turns = mo.state(1)`; the sliders in `am_chat` take `value=get_threshold()` / `on_change=set_threshold` (and the same for turns); `ams_turn`, `am_turn_sweep`, `am_probe_panel`, and `am_prompt_panel` read the getters. A slider's `on_change` runs in the slider's defining cell context (`runtime.py:2025`), so the setter excludes `am_chat` from the rerun (`cell_runner.py:322-336`) and the history survives; verified live (`set_ui_value` to 0.45 left `ams_session_id` and the chat value unchanged and redrew the probe panel). A side effect: an owner switch no longer resets the sliders, since they are rebuilt from the getters; the owner prose dropped "and resets the sliders". The kernel-side verification in stage 4a missed this because it called `ams_turn` from the scratchpad, outside the chat cell's context. 17 cells now.
12. Extraction is confirmed on Nick's store (the live test produced two `episodic` memories for Priya with a `session_id`, about two minutes after the message). So: `am_chat` has a fourth starter prompt, "I'm training for a marathon in October."; the sentence under the store tables reads "Agent Memory also extracts memories from session events in the background, on the store's extraction cadence (5 minutes by default). Tell the agent a new fact, wait a few minutes, then select Refresh."; `am_owner` adds "The fourth prompt tells the agent a new fact, and Refresh shows the memory extracted from it a few minutes later." Refresh stays. The title's "or extracted from session events" stays.
