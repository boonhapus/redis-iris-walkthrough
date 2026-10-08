# Agent Memory column: polish spec (2026-10-07, evening)

Nick approved the findings of the read-only teaching review (40 verified findings) and asked for cheap implementers plus one cheap reviewer. This spec turns the findings into exact edits. Three implementers: **code** (wave 1), **docs** (wave 1, in parallel), **prose** (wave 2, after code reports done). Then one **reviewer**.

Facts verified live on 2026-10-07 before this spec was written:

- A `bulk_create_long_term_memories_async` call with an id that already exists answers `created: [id]` and replaces the text in place (checked with a throwaway owner, then deleted). Stable ids therefore make Seed idempotent.
- `get_session_memory_async` on a deleted or unknown session raises `redis_agent_memory.errors.NotFoundErrorResponseContent` (404).
- `list_sessions_async(filter_owner_id=owner).items` is a list of session id strings. Tom had 19 leftover sessions, Priya 3.
- Bands at the default threshold 0.30, N 1, for the current store (Tom and Priya, three seeds each): foods question gives Priya's allergy 0.35 and nothing for Tom (0.05 each). Pets gives dogs 0.40 and cat 0.40. Summer gives the Dolomites 0.40 and the canoe 0.40. "Any packing tips?" alone gives 0.15 for both trips, and paired with the summer question 0.35 for both. The marathon line gives 0.30 for both trips.
- The OpenAI key in `.env` is currently invalid (401 `token_invalidated`). A real chat turn fails at the model call. The code change in `am_turn_fn` makes that failure visible in the chat and lets the workbench rerun.
- `marimo run` sends no code unless started with `--include-code` (marimo 0.24.2).

## Rules for every implementer

**The live kernel is the source of truth.** The notebook runs at `http://127.0.0.1:2718/`. Never edit `C:\Users\NickCooper\projects\demo\redis-iris-basics\iris_basics.py` on disk. The kernel saves the file after each code-mode edit. The docs implementer edits only `README.md` and `.env.example`, which are plain files.

**How to run code in the kernel.** From the Bash tool (Git Bash):

```bash
export PATH="$(cygpath "$LOCALAPPDATA")/Microsoft/WinGet/Packages/jqlang.jq_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
bash "C:/Users/NickCooper/projects/demo/.agents/skills/marimo-pair/scripts/execute-code.sh" --url http://127.0.0.1:2718/ -c "import marimo._code_mode as cm; help(cm)"
```

The `help(cm)` call must be your first kernel call. After it succeeds, run scripts by file path:

```bash
bash "C:/Users/NickCooper/projects/demo/.agents/skills/marimo-pair/scripts/execute-code.sh" --url http://127.0.0.1:2718/ "C:/Users/NickCooper/AppData/Local/Temp/am_polish_<role>_<n>.py"
```

Write each script with the Write tool to `C:\Users\NickCooper\AppData\Local\Temp\am_polish_<role>_<n>.py`. Do not paste scripts into a heredoc: the Bash tool turns `\n` inside a heredoc into a real newline, which breaks cell code that must contain a literal `\n`. Scripts must be ASCII only (no em dashes, no curly quotes). Put each cell body in a raw triple-single-quoted string, `r'''...'''`, so backslashes survive. Plain `python` on PATH is the Microsoft Store alias; use `uv run python` from the project folder when you need Python outside the kernel.

**Read before you edit, inside the same context.** `edit_cell` raises `StaleCellError` unless you read `ctx.cells[name].code` inside the same `get_context()` block first. Pattern:

```python
import marimo._code_mode as cm

NEW_X = r'''...'''

async with cm.get_context() as ctx:
    for name in ["am_connect", "am_store_fns"]:
        _ = ctx.cells[name].code          # read every cell you will edit
    ctx.edit_cell("am_connect", code=NEW_X)
    ...
    ctx.run_cell("am_connect")             # run in dependency order, see each wave
```

Submit cell bodies, not `@app.cell` wrappers. Stored cell code has no trailing newline. All edits of one wave go in ONE `get_context()` block so validation sees the final graph (two names move between cells in wave 1). If validation fails, the whole block is discarded: read the error, fix the body, and resubmit the whole block.

**New cells.** `create_cell(code, after=...)` lands the cell at the end of column 0 in this `width="columns"` notebook. After the context exits, open a second context and `move_cell(<new id>, after="am_turn_fn")`, then confirm the order with `[c.name for c in ctx.cells]`. `create_cell` defaults to `hide_code=True`, which is what we want. Pass `name="am_next_md"`.

**Do not** open a browser against the notebook, click "Take over", press buttons through the UI, or edit `tests/`. Do not run `pytest` beyond `tests/test_voice.py`. Do not call the model yourself. The store is Nick's demo store: the code implementer migrates it once (Clean up then Seed, through the new functions) and nobody else writes to it.

**Report.** Write `C:\Users\NickCooper\projects\demo\redis-iris-basics\.agents\reviews\am-polish-<role>.report.md`: what you changed (cell, id, one line), the verification output, deviations from this spec with the reason, and anything you noticed but did not touch. Reply with a five-line summary.

## Column 0, for reference

Lines 7-84 of the file. Cell `_` (unnamed, the first cell) holds the imports. `redis_client` defines `REDIS_CLIENT`, `AI_MODEL`, `USER_ID`. `llm_client` defines `stream_llm`. Column 4 starts at `am_title` (line 180). Column 4 order today: `am_title`, `am_store_button`, `am_store`, `am_owner`, `am_workbench`, `am_build_md`, `am_connect`, `am_store_fns`, `am_prompt_fns`, `am_turn_fn`, `am_machinery_md`, `am_settings`, `am_chat`, `am_turn_sweep`, `am_render`.

---

## Wave 1, implementer "code"

One `get_context()` block editing twelve cells, then a second block that creates and moves the closing cell, then the store migration, then verification.

### 1. Column 0 imports cell

Target: the first cell of the notebook, `ctx.cells[0]` (its name is `_`; address it by id from `ctx.cells[0].id`). It must currently contain `import itertools as it`; assert that before editing. New body:

```python
from collections.abc import AsyncGenerator, Callable
import asyncio
import datetime as dt
import html
import json
import os
import uuid

import redis

from marimo import ai
import marimo as mo
```

### 2. `llm_client`

New body (two typo fixes, and the key read no longer raises at kernel start):

```python
if AI_MODEL.startswith("gpt"):
    from openai import AsyncOpenAI

    llm = AsyncOpenAI(api_key=os.environ.get("LLM_API_KEY", "missing"))  # a missing key fails at the first chat turn, not here

    async def stream_llm(
        system_prompt: str, messages: list[dict[str, str]], config: ai.ChatModelConfig
    ) -> AsyncGenerator[str, None]:
        """Stream the response from OpenAI."""
        stream = await llm.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "system", "content": system_prompt}, *messages],
            max_tokens=config.max_tokens or 4096,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


if AI_MODEL.startswith("claude"):
    from anthropic import AsyncAnthropic

    llm = AsyncAnthropic(api_key=os.environ.get("LLM_API_KEY"))  # None lets the SDK look for its own credentials

    async def stream_llm(
        system_prompt: str, messages: list[dict[str, str]], config: ai.ChatModelConfig
    ) -> AsyncGenerator[str, None]:
        """Stream the response from Anthropic."""
        async with llm.messages.stream(
            model=AI_MODEL,
            system=system_prompt,
            messages=messages,
            max_tokens=config.max_tokens or 4096,
        ) as stream:
            async for text in stream.text_stream:
                yield text
```

### 3. `am_connect`

```python
# Client. Three settings from .env, and one object for every Agent Memory call below.
from redis_agent_memory import AgentMemory, errors, models


ams_client = AgentMemory(
    os.environ["MEMORY_API_BASE_URL"],
    store_id=os.environ["MEMORY_STORE_ID"],
    api_key=os.environ["MEMORY_API_KEY"],
)
```

### 4. `am_store_fns`

```python
# Store. Seed, list, and delete the long-term memories of two customers.
ams_seeds = {
    "Tom": [
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
    """Every long-term memory whose `ownerId` is `owner`, up to 100. With no `text`, the search endpoint lists by filter alone."""
    r = await ams_client.search_long_term_memory_async(
        request={"filter": {"ownerId": {"eq": owner}}, "limit": 100}
    )
    return r.items


async def ams_seed() -> list[str]:
    """Write memories straight to long-term memory, each tagged with the `ownerId` the app chooses (the write side of principle 2).
    The ids are the app's too, so a retry or a second Seed rewrites the same records and creates nothing new."""
    created: list[str] = []

    for owner, texts in ams_seeds.items():
        before = {m.id for m in await ams_memories(owner)}
        memories = [
            {"id": f"{owner.lower()}-seed-{i}", "text": text, "ownerId": owner}  # no memoryType, so the store files it as "semantic"
            for i, text in enumerate(texts, 1)
        ]
        await ams_client.bulk_create_long_term_memories_async(memories=memories)
        created += [m["id"] for m in memories if m["id"] not in before]

    return created


async def ams_forget(owner: str) -> tuple[list[str], list[str]]:
    """Delete every session and every long-term memory about `owner`. Returns the session ids and the memory ids deleted."""
    sessions = await ams_client.list_sessions_async(filter_owner_id=owner)
    await asyncio.gather(*(ams_client.delete_session_memory_async(session_id=s) for s in sessions.items))

    memory_ids = [m.id for m in await ams_memories(owner)]
    if memory_ids:
        await ams_client.bulk_delete_long_term_memories_async(memory_ids=memory_ids)

    return sessions.items, memory_ids
```

### 5. `am_prompt_fns`

```python
# Prompt. The search text, the search request, and the system prompt the app builds.
def ams_query(messages: list[ai.ChatMessage], n: int) -> str:
    """The text to search with: the last `n` user messages, in order."""
    user_messages = [str(m.content) for m in messages if m.role == "user"]
    return "\n".join(user_messages[-n:])


def ams_request(query: str, owner: str, threshold: float) -> dict:
    """The search request. `text` and `similarityThreshold` match by meaning (principle 1), `filter` scopes recall to one `ownerId` (principle 2), and `limit` caps the results at ten."""
    return {
        "text": query,
        "similarityThreshold": threshold,
        "filter": {"ownerId": {"eq": owner}},
        "limit": 10,
    }


def ams_build_system(owner: str, facts: list[str]) -> str:
    """The system prompt. The app, not Agent Memory, decides what the model sees (principle 3)."""
    system = "You are the Cairn Outfitters support agent. Answer the customer in two or three sentences."

    if facts:
        system += f"\n\nWhat you remember about {owner}:\n" + "\n".join(f"- {fact}" for fact in facts)

    return system
```

### 6. `am_turn_fn`

```python
# Turn. One chat turn: the session write, the long-term search, and the prompt.
ams_session_id = f"{ams_owner.value.lower()}-{uuid.uuid4().hex[:12]}"  # a new session each time the customer changes


async def ams_turn(messages: list[ai.ChatMessage], config: ai.ChatModelConfig) -> AsyncGenerator[str, None]:
    """One agent turn: write the message to session memory, search long-term memory, and put what it recalls in the system prompt."""
    # Notebook inputs: the customer from the dropdown, and the slider values through ams_settings() (plumbing, not an Agent Memory call).
    owner = ams_owner.value
    threshold, turns = ams_settings()

    # Session memory: the user's message, as an event in this session.
    # The first event's actorId becomes the session's ownerId, and the memories the store extracts from the session carry it.
    # Extraction runs a few minutes later. The prompt below never reads these events.
    await ams_client.add_session_event_async(
        session_id=ams_session_id,
        actor_id=owner,
        role=models.MessageRole.USER,
        content=[{"text": str(messages[-1].content)}],
        created_at=dt.datetime.now(dt.timezone.utc),
    )

    # Long-term memory: this owner's memories, by meaning, searched with the last N user messages.
    found = await ams_client.search_long_term_memory_async(request=ams_request(ams_query(messages, turns), owner, threshold))

    # The prompt: the app puts the recalled memories in front of the model.
    system = ams_build_system(owner, [m.text for m in found.items])
    history = [{"role": m.role, "content": str(m.content)} for m in messages if m.role in ("user", "assistant")]

    try:
        async for text in stream_llm(system, history, config):
            yield text
    except Exception as e:  # a model error must not hide the two Agent Memory calls that already ran
        yield f"The model call failed ({type(e).__name__}). The session write and the search still ran."
```

### 7. `am_settings`

```python
# Slider state. The sliders live in am_chat, so a turn cannot read their .value from inside the chat cell.
# A cell that references these getters reruns on every move, so the turn reads them through ams_settings() instead.
# am_chat reads the getters for the sliders' start values, and marimo exempts the cell that owns a control.
get_threshold, set_threshold = mo.state(0.3)
get_turns, set_turns = mo.state(1)


def ams_settings() -> tuple[float, int]:
    """The sliders' current values, read at call time. A cell that calls this does not rerun when a slider moves."""
    return round(get_threshold(), 2), get_turns()
```

### 8. `am_chat`

```python
# debounce=True: the setter fires when the slider is released, not on every step.
ams_threshold = mo.ui.slider(
    start=0.0, stop=1.0, step=0.05, value=get_threshold(), show_value=True, debounce=True,
    label="similarityThreshold", on_change=set_threshold,
)
ams_turns = mo.ui.slider(
    start=1, stop=10, step=1, value=get_turns(), show_value=True, debounce=True,
    label="Search with the last N user messages", on_change=set_turns,
)
ams_prompts = [
    "Which foods should I avoid?",
    "What pets do I have?",
    "Where am I headed this summer?",
    "Any packing tips?",
    "I'm training for a marathon in October.",
]

# Displayed beside the data in am_workbench. ams_turn is the Iris turn above.
ams_chatbot = mo.ui.chat(ams_turn, prompts=ams_prompts, max_height=520)
```

### 9. `am_turn_sweep`

```python
async def ams_sweep(query: str, store: dict[str, list[models.MemoryRecord]]) -> dict[str, dict[str, float]]:
    """For each owner in `store`, the highest threshold that returns each memory. The API returns no score."""
    grid = [round(0.05 * i, 2) for i in range(21)]  # 0.00 to 1.00
    pairs = [(owner, t) for owner in store for t in grid]
    gate = asyncio.Semaphore(12)  # 42 searches per sweep; keep a dozen in flight

    async def recall(owner: str, t: float) -> list[models.MemoryRecord]:
        async with gate:
            r = await ams_client.search_long_term_memory_async(request={**ams_request(query, owner, t), "limit": 100})
            return r.items

    hits = await asyncio.gather(*(recall(owner, t) for owner, t in pairs))

    bands = {owner: {m.text: 0.0 for m in records} for owner, records in store.items()}
    for (owner, t), found in zip(pairs, hits):
        for m in found:
            bands[owner][m.text] = max(bands[owner].get(m.text, 0.0), t)

    return bands


async def ams_session_texts(session_id: str) -> list[str]:
    """The user messages the app wrote to this session, or none when the session is gone (Clean up deletes it)."""
    try:
        session = await ams_client.get_session_memory_async(session_id=session_id)
    except errors.NotFoundErrorResponseContent:
        return []
    return [e.content[0].text for e in session.events]


# The search text behind the last turn (the first starter prompt before any chat), how high a threshold each
# memory clears for it, for every customer, and the session so far. Depends on the chat, N, and the store, not on the threshold.
ams_turn_live = any(m.role == "user" for m in ams_chatbot.value)
ams_turn_query = ams_query(ams_chatbot.value, get_turns()) if ams_turn_live else ams_prompts[0]
ams_turn_bands = await ams_sweep(ams_turn_query, ams_store)
ams_turn_events = await ams_session_texts(ams_session_id) if ams_turn_live else []
```

### 10. `am_render`

```python
def ams_bars(bands: dict[str, float], threshold: float) -> mo.Html:
    """One row per memory, in store order."""
    threshold = round(threshold, 2)
    rows = []
    for text, band in bands.items():
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
    ticks = '<div class="ams-row"><div></div><div class="ams-ticks"><span>0</span><span>0.5</span><span>1</span></div><div></div></div>'
    body = "".join(rows) + ticks if rows else '<div class="ams-empty">No memories yet. Select Seed to write them.</div>'
    css = """
<style>
.ams-bars { font: 13px system-ui, -apple-system, "Segoe UI", sans-serif; color: #52514e; }
.ams-row { display: grid; grid-template-columns: minmax(0, 2fr) 3fr 3.2em; gap: 0 8px; align-items: center; margin: 6px 0; }
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
    return mo.Html('<div class="ams-bars">' + body + "</div>" + css)


def ams_memory_table(owner: str, found: list[models.MemoryRecord]) -> mo.Html:
    """One owner's long-term memories as a markdown table, headed with the `ownerId`."""
    head = f"**ownerId {owner}** ({len(found)})"
    if not found:
        return mo.md(head + "\n\n_No memories yet._")
    rows = [f"| {m.text.replace('|', '&#124;')} | {m.memory_type} |" for m in found]
    return mo.md("\n".join([head, "", "| memory | type |", "|---|---|", *rows]))
```

### 11. `am_store`

```python
_note = ""
if ams_seed_button.value:
    _ids = await ams_seed()
    _note = f"Wrote {len(_ids)} new memories to long-term memory." if _ids else "Every seed memory is already in long-term memory."
if ams_cleanup_button.value:
    _sessions, _memories = 0, 0
    for _owner in ams_seeds:
        _deleted_sessions, _deleted_memories = await ams_forget(_owner)
        _sessions += len(_deleted_sessions)
        _memories += len(_deleted_memories)
    _note = f"Deleted {_memories} memories and {_sessions} sessions for {' and '.join(ams_seeds)}."
_ = ams_refresh_button.value  # Refresh lists again, for memories extracted in the background
_ = ams_chatbot.value  # and so does each chat turn, so the tables agree with the bars

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

### 12. Run order inside the wave 1 context

After the twelve `edit_cell` calls, queue `run_cell` in this order: the imports cell (by id), `llm_client`, `am_connect`, `am_store_fns`, `am_prompt_fns`, `am_settings`, `am_turn_fn`, `am_chat`, `am_render`, `am_store`, `am_turn_sweep`. marimo reruns dependents (`am_workbench`) itself. `am_workbench` will show a NameError on `ams_turn_events` until wave 2 lands; that is expected, note it in the report.

### 13. Second context: the closing cell

```python
async with cm.get_context() as ctx:
    cid = ctx.create_cell(NEW_NEXT, name="am_next_md")
    ctx.run_cell(cid)
```

with

```python
mo.md(r"""
## Take `ams_turn` into your agent

Copy `ams_turn`, the client, and the three helpers above it. Replace `ams_owner.value` with the signed-in customer, `ams_settings()` with your settings, and `stream_llm` with your model call. Give a LangGraph agent memory that outlives the session: [Redis Agent Memory with LangGraph](https://redis.io/tutorials/redis-agent-memory-with-langgraph/).
""")
```

Then a third context: `ctx.move_cell("am_next_md", after="am_turn_fn")`. Confirm `[c.name for c in ctx.cells]` shows `am_next_md` between `am_turn_fn` and `am_machinery_md`, and that `ctx.cells["am_next_md"].config.hide_code` is True.

### 14. Store migration (once)

The store holds the six seeds under random ids. In a scratchpad call after the edits:

```python
g = ctx.globals  # from cm.get_context(), outside any async with
for owner in ("Tom", "Priya"):
    sessions, memories = await g["ams_forget"](owner)
    print(owner, "deleted sessions", len(sessions), "memories", len(memories))
print("seeded", await g["ams_seed"]())
```

Expected: Tom deletes about 19 sessions and 3 memories, Priya 3 and 3, and `ams_seed` returns six ids `tom-seed-1..3`, `priya-seed-1..3`. Then `ctx.run_cell("am_store")` in a context so the tables and the sweep refresh. Confirm `await g["ams_memories"]("Tom")` lists three records with those ids and `memory_type == "semantic"`, and `list_sessions_async(filter_owner_id="Tom").items` is empty.

### 15. Verification (code)

- Every edited cell plus `am_next_md`: `status == idle`, `errors == []`. `am_workbench` is allowed to be in error only on `ams_turn_events` until wave 2.
- `ctx.graph.cycles == set()`. Defs: `am_connect` {AgentMemory, errors, models, ams_client}; `am_store_fns` {ams_seeds, ams_memories, ams_seed, ams_forget}; `am_prompt_fns` {ams_query, ams_request, ams_build_system}; `am_turn_fn` {ams_session_id, ams_turn}; `am_turn_sweep` {ams_sweep, ams_session_texts, ams_turn_live, ams_turn_query, ams_turn_bands, ams_turn_events}; the imports cell defines `dt` and no longer `it`.
- Disk: `grep -n "itertools\|secrets\|health_async\|USER_ID:" iris_basics.py` finds nothing except `USER_ID` in `redis_client` and `am_owner` (wave 2 removes those). `grep -n "am_next_md\|ams_session_texts\|Any packing tips" iris_basics.py` finds each.
- `uv run python -m marimo check --strict iris_basics.py` exits 0.
- A direct turn, to prove the model-failure path and the session write: `out = [t async for t in g["ams_turn"]([ai.ChatMessage(role="user", content="Which foods should I avoid?")], ai.ChatModelConfig())]` with `from marimo import ai`. Expect the joined text to start with "The model call failed (AuthenticationError)" while the key is invalid (or a real answer if Nick has fixed the key). Then `len((await g["ams_client"].get_session_memory_async(session_id=g["ams_session_id"])).events) == 1`. Afterwards delete that session with `delete_session_memory_async` so the store is left with no sessions.

---

## Wave 1, implementer "docs"

Edits `C:\Users\NickCooper\projects\demo\redis-iris-basics\README.md` and `C:\Users\NickCooper\projects\demo\redis-iris-basics\.env.example` with the Edit tool. Read each file first. Line numbers are from the file as of this spec; match on text, not line numbers. Do not touch the kernel.

### README

1. Line 34, replace the sentence "The Search, LangCache, and Agent Memory chats call Claude (`claude-opus-5-5`)." with "The Search, LangCache, and Agent Memory chats call the model named by `AI_MODEL` in column 0, `gpt-4o-mini` at first."
2. Line 36, replace "A LangCache miss stores Claude's answer, and each Agent Memory message writes two session events: the message and the agent's reply." with "A LangCache miss stores the model's answer, and each Agent Memory message writes one session event, the message itself."
3. Line 53, replace "- A Claude API key, or an `ant auth login` profile, for the three chats" with "- An LLM API key for the three chats: an OpenAI key when `AI_MODEL` starts with `gpt`, an Anthropic key when it starts with `claude`"
4. Line 57, replace "Without a Claude credential the Claude client still builds, and each chat message that reaches Claude raises an error in the chat." with "Without an LLM key the client still builds, and each chat turn reports the failed model call in the chat."
5. Line 89, replace the table row that starts "| `ANTHROPIC_API_KEY` |" with "| `LLM_API_KEY` | none | The Search, LangCache, and Agent Memory chats. An OpenAI key when `AI_MODEL` starts with `gpt`, or an Anthropic key when it starts with `claude` |"
6. Line 93, delete the paragraph "The Anthropic SDK also accepts a profile from `ant auth login`, so you can leave `ANTHROPIC_API_KEY` unset. A set key wins over the profile." and the blank line after it.
7. Line 104, replace "uv run marimo run iris_basics.py" with "uv run marimo run --include-code iris_basics.py", and after that code block's closing fence add a blank line and "The code stays folded until you pick **Show code** from the app menu."
8. Line 106, replace "It sends nothing to Redis, the hosted services, or Claude." with "It sends nothing to Redis, the hosted services, or the LLM."
9. Replace everything from the line "## Step 7. Recall a memory by meaning" up to, not including, the line "## Step 8. Clean up" with:

```markdown
## Step 7. Recall a memory by meaning

In the Redis Agent Memory column, press **Seed**.

The cell writes three memories for each of two Cairn Outfitters customers, Tom and Priya, with one `POST /long-term-memory` per customer. Each memory carries an `ownerId` the notebook chooses and an id the notebook chooses, so a second press rewrites the same six records and reports nothing new. Two tables list what the store holds for each `ownerId`, with the type the store filed each memory under.

Under the tables, the workbench puts the chat beside the data behind each turn. Pick a customer in **Chat as**, then type `/` in the chat box to pick a starter message. The right pane shows the search text, one bar per memory for both customers, the request the app sends, the system prompt it builds, and the messages it wrote to the session.

Each bar ends at the highest `similarityThreshold` that still returns the memory. The notebook finds it by running the same search at 21 thresholds, because the search API returns no score. Move the `similarityThreshold` slider, and the dark bars, at or above the line, are the memories that go into the system prompt. The second customer's bars show the same search without the `ownerId` filter's effect, and the agent never sends that search.

Each message sends two Agent Memory requests and one model call:

1. `POST /session-memory/events` with your message
2. `POST /long-term-memory/search` with the last N user messages, the customer's `ownerId` filter, and your threshold
3. One model call with the recalled memories in the system prompt and the chat history as messages

Switching customers starts a new session. The reply is not written to session memory. A few minutes after a turn, the store extracts long-term memories from the session, and **Refresh** lists them with their type.

```

(Keep one blank line before "## Step 8. Clean up".)

10. In Step 8, replace the bullet that starts "- Redis Agent Memory: **Clean up: DELETE this owner's sessions and memories**" with "- Redis Agent Memory: **Clean up** deletes every session and every long-term memory tagged Tom or Priya, extracted memories included. The note above the tables reports both counts."
11. Replace the paragraph "After chatting with the Cairn agent, press Agent Memory's button again ten minutes later. Extraction runs every 5 minutes by default, configurable from 60 to 600 seconds, and adds memories with ids the service chooses. This owner-scoped Clean up is what deletes them." with "After chatting, press **Refresh** a few minutes later. Extraction runs every 5 minutes by default, configurable from 60 to 600 seconds, and adds memories with ids the service chooses. Clean up deletes those too."
12. Line 354, replace the table row that starts "| `llm_client` |" with "| `llm_client` | `stream_llm`, which streams from OpenAI or Anthropic according to `AI_MODEL` | `LLM_API_KEY` |"
13. Line 482, replace "Verify that `ANTHROPIC_API_KEY` is set and valid in `.env`, or that `ant auth login` has stored a profile, then restart the kernel. A set key wins over the profile, and an invalid one raises `anthropic.AuthenticationError`." with "Verify that `LLM_API_KEY` is set and valid in `.env` and matches the provider of `AI_MODEL`, then restart the kernel. With an invalid key, each chat turn ends with \"The model call failed\" and the error's name."
14. Line 629, replace "The first real chat turns need `ANTHROPIC_API_KEY` or an `ant auth login` profile." with "The first real chat turns need `LLM_API_KEY`."

After the edits: `grep -n "ANTHROPIC_API_KEY\|ant auth\|Dana\|two session events" README.md` must find nothing. Note in the report that the README's Steps 4 to 6 and "How it works" still describe the older notebook (Claude, `cairn-<run>`, `call_service`), which is outside this task.

### .env.example

Replace the three-line Claude section at the end:

```
# ---- Claude (the chats in the Search, LangCache, and Agent Memory columns) ----
# Console: https://platform.claude.com, API keys. The SDK also accepts a profile from `ant auth login`.
# ANTHROPIC_API_KEY=
```

with

```
# ---- LLM (the chats in the Search, LangCache, and Agent Memory columns) ----
# An OpenAI key when AI_MODEL in column 0 starts with gpt, or an Anthropic key when it starts with claude.
# LLM_API_KEY=
```

---

## Wave 2, implementer "prose"

Start only after the code implementer's report exists at `.agents/reviews/am-polish-code.report.md`. One `get_context()` block editing eight cells, then verification.

### 1. `redis_client` (column 0)

```python
REDIS_CLIENT = redis.Redis.from_url(
    os.environ["REDIS_URL"],
    protocol=2,               # flat FT.* replies: every reply shape here was verified live under RESP2
    decode_responses=True,    # strings back; the vector field is never read through a core command
    health_check_interval=30,
)

AI_MODEL = "gpt-4o-mini"
```

### 2. `am_title`

```python
mo.md(r"""
# Redis Agent Memory

An agent that forgets a customer's two dogs between conversations asks about them again every time.
Redis Agent Memory keeps each conversation in session memory and durable facts in long-term memory, written by the app or extracted from session events.
Select Seed to write memories for two Cairn Outfitters customers, Tom and Priya, then chat as either to see three principles:

1. **Long-term memory recalls by meaning.** "Which foods should I avoid?" shares no keyword with "I'm allergic to peanuts and tree nuts", yet the search returns it for Priya. The search is a vector search, and `similarityThreshold` is its cosine similarity floor, the same name the LangCache column tunes.
2. **The `ownerId` filter scopes recall to one customer.** The app sends `filter: {ownerId: ...}` with every search, so Tom's agent does not see Priya's allergy.
3. **The app builds the prompt.** Each turn writes the message to session memory, searches long-term memory, and puts what it recalls in the system prompt.

Agent Memory is in public preview on Redis Cloud. Settings: `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY`, plus `LLM_API_KEY` for the chat.
Docs: [Agent Memory](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/overview/),
[Python SDK quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/), and
[developer guide](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/developer-guide/).

Each line reads source, target, and what passes between them:

```text
App        -> long-term   POST /long-term-memory, the Seed memories, each with an ownerId
App        -> session     POST /session-memory/events, the user's message, every turn
App        -> long-term   POST /long-term-memory/search: text, similarityThreshold, ownerId
long-term  -> App         the memories at or above the threshold, best first, no score
App        -> LLM         the system prompt with the recalled memories, and the history
session    -> long-term   extraction in the background, a few minutes after a turn
```
""")
```

### 3. `am_store_button`

```python
ams_seed_button = mo.ui.run_button(label="Seed")
ams_refresh_button = mo.ui.run_button(label="Refresh")
ams_cleanup_button = mo.ui.run_button(label="Clean up", kind="danger")

mo.vstack(
    [
        mo.md(r"""
## Seed and list two customers' long-term memories

Seed writes three memories per customer with `POST /long-term-memory`, each tagged with the `ownerId` the app chooses.
Refresh lists them again, including anything the store has extracted from a session a few minutes after a turn.
Seeds carry type `semantic`. An extracted memory carries the type the extractor chooses, such as `episodic`.
Clean up deletes every session and every long-term memory tagged Tom or Priya.
"""),
        mo.hstack([ams_seed_button, ams_refresh_button, ams_cleanup_button], justify="start"),
    ]
)
```

### 4. `am_owner`

```python
ams_owner = mo.ui.dropdown(options=list(ams_seeds), value=next(iter(ams_seeds)), label="Chat as")  # displayed in the workbench, upstream of the chat so a switch starts a new session

mo.md(r"""
## Chat with the Cairn agent as Tom or Priya and watch the recall

Type `/` in the chat box to pick a starter message. Send the first one as Tom. The agent has nothing on food, and the Priya bars show the allergy the `filter` kept out.
Send the next two as Tom, and his dogs and his Dolomites trip appear in the system prompt.

Switch to Priya, which starts a new conversation, and send the same three. The foods question now recalls the allergy, with no keyword shared.
Move `similarityThreshold` and watch memories enter and leave the system prompt.

Send the packing question next. Alone it is a weak match, so set N to 2 and the trip question rides along in the search text.
The last message tells the agent something new. A few minutes later, Refresh can list a memory the store extracted from it.
""")
```

### 5. `am_workbench`

```python
_t = round(get_threshold(), 2)
_n = get_turns()
_owner = ams_owner.value
_shown = min(_n, sum(m.role == "user" for m in ams_chatbot.value))
_recalled = [text for text, band in ams_turn_bands[_owner].items() if round(band, 2) >= _t]

_data = [
    ams_turns,
    mo.md(
        ("**Search text**, the last user message:" if _shown == 1 else f"**Search text**, the last {_shown} user messages, oldest first:")
        if ams_turn_live
        else "**Search text**, the first starter message until you send one:"
    ),
    mo.md(f"```text\n{ams_turn_query}\n```"),
    mo.md(
        "Each bar is the highest `similarityThreshold` that returns the memory, found by running this search at 21 thresholds. "
        "The API returns no score. Dark bars are at or above the slider's line, and those memories go into the system prompt."
    ),
    ams_threshold,
    mo.md(f"**ownerId {_owner}**, what the `filter` returns"),
    ams_bars(ams_turn_bands[_owner], _t),
]
for _other in (o for o in ams_turn_bands if o != _owner):
    _data += [mo.md(f"**ownerId {_other}**, the same search for comparison. The agent never sends it."), ams_bars(ams_turn_bands[_other], _t)]
_data += [
    mo.md("---"),
    mo.md("Moving a slider redraws this pane without a new turn."),
    mo.md("**Search request**, from `ams_request`"),
    mo.md(f"```json\n{json.dumps(ams_request(ams_turn_query, _owner, _t), indent=2)}\n```"),
    mo.md("**System prompt**, from `ams_build_system`"),
    mo.md(f"```text\n{ams_build_system(_owner, _recalled)}\n```"),
    mo.md(
        f"**Session `{ams_session_id}`**, the user messages the app wrote ({len(ams_turn_events)})"
        if ams_turn_events
        else f"**Session `{ams_session_id}`**, no messages written yet"
    ),
]
if ams_turn_events:
    _data.append(mo.md("\n".join(f"- {text}" for text in ams_turn_events)))

mo.hstack(
    [
        mo.vstack([ams_owner, ams_chatbot]),
        mo.vstack(_data),
    ],
    widths=[5, 6],
    gap=1.5,
    align="start",
)
```

### 6. `am_build_md`

```python
mo.md(r"""
## The Agent Memory code: client, store, prompt, turn

One cell per step, with the code visible in `marimo edit`. In `marimo run`, pick **Show code** from the app menu.
Every call on `ams_client` goes to Agent Memory, and the rest is the app. Seed and Clean up call the store functions, and each chat turn calls `ams_turn`.
""")
```

### 7. `am_machinery_md`

```python
mo.md(r"""
## Notebook plumbing: slider state, chat object, threshold sweep, renderers

Four hidden cells follow: the slider state, the chat object, the threshold sweep behind the bars, and the renderers for the bars and the store tables.
Read `am_settings`, `am_chat`, `am_turn_sweep`, and `am_render` in `iris_basics.py`, or expand them in `marimo edit`.
""")
```

### 8. `rs_title` and `lc_title`

In each, read the body, assert `body.count("ANTHROPIC_API_KEY") == 1`, and submit `body.replace("ANTHROPIC_API_KEY", "LLM_API_KEY")`. Nothing else changes in those cells.

### 9. Run order and verification (prose)

Queue `run_cell` for `redis_client`, `am_title`, `am_store_button`, `am_owner`, `am_build_md`, `am_machinery_md`, `rs_title`, `lc_title`. `am_owner` reruns `am_turn_fn`, `am_chat`, `am_store`, `am_turn_sweep`, and `am_workbench` on its own (a new session id is expected). Then check:

- All eight cells and `am_workbench`, `am_turn_sweep`, `am_store` are `idle` with `errors == []`.
- `am_workbench` output text contains "Each bar is the highest", "ownerId Tom, what the", "ownerId Priya, the same search for comparison", "Moving a slider redraws this pane", "Search request, from", "System prompt, from", "no messages written yet", and "Cairn Outfitters support agent". Decode `data-html` attributes before searching, as in the earlier reviews.
- `grep -n "USER_ID\|ANTHROPIC_API_KEY\|in preview on Redis Cloud" iris_basics.py` finds `in preview` only in `cr_title` (Context Retriever keeps its wording) and nothing else.
- `uv run python -m marimo check --strict iris_basics.py` exits 0 and `uv run pytest tests/test_voice.py -q` reports 8 passed.
- Column 4 order: `am_title`, `am_store_button`, `am_store`, `am_owner`, `am_workbench`, `am_build_md`, `am_connect`, `am_store_fns`, `am_prompt_fns`, `am_turn_fn`, `am_next_md`, `am_machinery_md`, `am_settings`, `am_chat`, `am_turn_sweep`, `am_render` (16 cells).

---

## Reviewer brief

Start after all three implementer reports exist. Read this spec and the three reports. Read-only on the store except where a probe says otherwise. First kernel call `help(cm)`.

1. **Code equals spec.** For every cell this spec rewrites, dump `ctx.cells[name].code` and compare with the fenced body here after stripping trailing whitespace per line and the trailing newline. Report any difference verbatim.
2. **Disk mirrors kernel.** Every non-blank live line of each rewritten cell appears in `iris_basics.py` with four spaces prepended. `grep` for `itertools`, `secrets`, `health_async`, `USER_ID`, `ANTHROPIC_API_KEY`, `str(uuid.uuid4())`, and `sorted(bands` must find nothing.
3. **Health.** All `am_*` cells, `rs_title`, `lc_title`, `redis_client`, `llm_client`, and the imports cell `idle` with `errors == []`. `ctx.graph.cycles == set()`.
4. **Store.** `ams_memories("Tom")` and `("Priya")` return exactly the three seeds each with ids `<owner>-seed-1..3` and type `semantic`. `list_sessions_async(filter_owner_id=...)` returns no sessions for either owner (the code implementer deleted the one its direct turn wrote).
5. **Threshold probe.** Record `ams_session_id`, `id(ams_chatbot)`, `id(ams_turn)`. `set_ui_value(ams_threshold, 0.45)` in one call, read in the next: all three unchanged, workbench line at `left:45%`, every bar in the `ams-bar-below` state, system prompt with no memories, request JSON at 0.45. Restore to 0.3 and confirm.
6. **N probe.** `set_ui_value(ams_turns, 2)`; the chat is empty so the search text stays the first starter prompt and the bands are unchanged, and `ams_session_id` and both ids are unchanged. Restore to 1.
7. **Owner probe.** `set_ui_value(ams_owner, ["Priya"])`: new session id starting `priya-`, new chat and turn ids, workbench shows "ownerId Priya, what the" before "ownerId Tom, the same search", system prompt carries the allergy. Restore `["Tom"]` and confirm the labels swap back. If a `ReadTimeout` traceback appears from `am_turn_sweep`, repeat the same `set_ui_value` once and say so.
8. **Model-failure path (writes one session event, then deletes it).** Run `ams_turn` directly as in the code implementer's step 15 and confirm the fallback text (or a real answer if the key works). Confirm the session has one event, then delete the session.
9. **Empty-store rendering.** Call `ams_bars({}, 0.3)` and confirm the HTML has no `ams-ticks` row and contains "No memories yet. Select Seed to write them."
10. **Voice.** `uv run pytest tests/test_voice.py -q` passes. Read every `mo.md` string in column 4 once more for a sentence over 25 words with code stripped, a missing serial comma, or a semicolon.
11. **README and .env.example.** The greps in the docs section find nothing. Read the new Step 7 against the live column and flag any sentence that does not match what the notebook does.
12. **Reports.** Check each implementer's report against what you read.

Verdict PASS, PASS WITH NOTES, or FAIL, findings most severe first with the fix, and a "Final state" block (owner, threshold, N, session id, store counts, sessions). Restore every control you moved. Write `.agents/reviews/am-polish.review.md`.
