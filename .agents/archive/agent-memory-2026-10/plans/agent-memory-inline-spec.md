# Agent Memory column: inline spec (2026-10-07, afternoon)

Nick approved two inlines in column 4 of `iris_basics.py` (the Redis Agent Memory column): `ams_recall` and `ams_remember` go away, the turn calls the SDK itself, the search cell and the prompt cell merge into one cell of three pure functions, and the code section is retitled. One implementer applies the whole move in one code-mode context. Two reviewers follow.

Why: `ams_recall` wrapped one SDK method behind a notebook name and added nothing. `ams_remember` did the same for the session write. With both gone the turn shows all three service calls itself. `ams_request` stays because the workbench renders the live request body from it, and inlining it would mean a second copy that drifts. `ams_settings` stays because it is the rerun guard (a slider getter read inside the turn would rerun the turn cell on every move and wipe the chat).

## Rules for the implementer

**The live kernel is the source of truth.** The notebook runs at `http://127.0.0.1:2718/`. Never edit `C:\Users\NickCooper\projects\demo\redis-iris-basics\iris_basics.py` on disk. The kernel saves the file after each code-mode edit. A backup of the file as it was before this move is at `C:\Users\NickCooper\AppData\Local\Temp\iris_basics.before-inline.py`; do not touch it.

**How to run code in the kernel.** From the Bash tool (Git Bash):

```bash
export PATH="$(cygpath "$LOCALAPPDATA")/Microsoft/WinGet/Packages/jqlang.jq_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
bash "C:/Users/NickCooper/projects/demo/.claude/skills/marimo-pair/scripts/execute-code.sh" --url http://127.0.0.1:2718/ -c "import marimo._code_mode as cm; help(cm)"
```

The `help(cm)` call must be your first kernel call. After it succeeds, run scripts by file path:

```bash
bash "C:/Users/NickCooper/projects/demo/.claude/skills/marimo-pair/scripts/execute-code.sh" --url http://127.0.0.1:2718/ "C:/Users/NickCooper/AppData/Local/Temp/am_inline_<n>.py"
```

Write each script with the Write tool to `C:\Users\NickCooper\AppData\Local\Temp\am_inline_<n>.py`. Do not paste scripts into a heredoc: the Bash tool turns `\n` inside a heredoc into a real newline, which breaks cell code that must contain a literal `\n` (two of these cell bodies do). Scripts must be ASCII only (no em dashes, no curly quotes). Each cell body sits in a raw triple-single-quoted string, `r'''...'''`, so backslashes survive. Read `C:\Users\NickCooper\projects\demo\.claude\skills\marimo-pair\SKILL.md` for the `marimo._code_mode` API before you start.

**One context for the whole move.** Read all five cells, then delete one and edit four, then queue the runs, all inside one `async with cm.get_context() as ctx:` block. Code mode validates the batch as a whole and evicts deleted cells before it checks for multiply-defined names, so defining `ams_request` in `am_prompt_fns` while deleting `am_recall_fns` validates. If the context raises anyway, do not split the move into two contexts and do not pass `skip_staleness_check` or `skip_validation`. Stop, put the exact error in your report, and return the outcome `failed`. The kernel discards the whole queue when the block raises, so nothing has changed in that case.

- Submit cell bodies, not `@app.cell` wrappers or `def` lines. marimo generates the function signature from the body's references.
- Touch only the five cells listed below. No `create_cell`, no `move_cell`, no `hide_code` changes.
- `ctx.run_cell` every edited cell so its output refreshes.
- The chat holds four messages right now (two user turns). Editing `am_turn_fn` redefines `ams_turn`, so `am_chat` reruns and the chat empties. That is expected and already recorded at `C:\Users\NickCooper\AppData\Local\Temp\am_inline_prestate.txt`. Print the transcript once more before you edit and put it in your report.
- Verify in a separate kernel call after the context closes. The scratchpad namespace is a copy taken before your edits; read `ctx.globals[...]` or use a later call.

**Forbidden.** Sending a chat message. Pressing or `set_ui_value` on Seed, Refresh, or Clean up. Moving a slider or the owner dropdown (the reviewers do that). Calling `ams_seed`, `ams_forget`, `add_session_event_async`, or any other write to the store, even as a test. Installing packages. Launching a browser. Clicking "Take over" anywhere. Editing files under `tests/`. Editing `iris_basics.py` directly. Editing any cell not listed here.

## The move

Five cells. Current ids: `am_recall_fns` ZHCJ, `am_prompt_fns` ulZA, `am_turn_fn` RJQd, `am_turn_sweep` Vxnm, `am_build_md` qxJz. Use names, not ids, in the script.

### 1. Delete `am_recall_fns`

It defines `ams_request` and `ams_recall`. `ams_request` moves to `am_prompt_fns` below. `ams_recall` is gone: its two callers (`am_turn_fn`, `am_turn_sweep`) call the SDK method directly.

### 2. `am_prompt_fns`: three pure functions

Replace the body with:

```python
def ams_query(messages: list[ai.ChatMessage], n: int) -> str:
    """The text to search with: the last `n` user messages, newest first."""
    recent = it.islice((m for m in reversed(messages) if m.role == "user"), n)
    return "\n".join(str(m.content) for m in recent)


def ams_request(query: str, owner: str, threshold: float) -> dict:
    """The search request. Principle 1 is `text` and `similarityThreshold`; Principle 2 is `filter`."""
    return {
        "text": query,
        "similarityThreshold": threshold,
        "filter": {"ownerId": {"eq": owner}},
        "limit": 10,
    }


def ams_build_system(owner: str, facts: list[str]) -> str:
    """Principle 3: the app, not Agent Memory, decides what the model sees."""
    system = "You are a helpful AI assistant."

    if facts:
        system += f"\n\nWhat you remember about {owner}:\n" + "\n".join(f"- {fact}" for fact in facts)

    return system
```

`ams_query` and `ams_build_system` are unchanged. `ams_request` is unchanged and moves in from the deleted cell. `ams_remember` is gone. The cell no longer references `ams_client`, `dt`, or `models`, so marimo will write its signature as `def am_prompt_fns(ai, it):`.

### 3. `am_turn_fn`: the turn makes the three service calls itself

Replace the body with:

```python
ams_session_id = f"{ams_owner.value.lower()}-{secrets.token_hex(6)}"  # a new session each time the customer changes


async def ams_turn(messages: list[ai.ChatMessage], config: ai.ChatModelConfig) -> AsyncGenerator[str, None]:
    """One agent turn. Principle 3: the app, not the service, decides what the model sees."""
    owner = ams_owner.value

    # Session memory: the user's message, as an event in this session.
    await ams_client.add_session_event_async(
        session_id=ams_session_id,
        actor_id=owner,
        role=models.MessageRole.USER,
        content=[{"text": str(messages[-1].content)}],
        created_at=dt.datetime.now(dt.timezone.utc),
    )

    # Long-term memory: this owner's memories, by meaning, searched with the last N user messages.
    # ams_settings returns the slider values. It is a notebook helper in the plumbing cells below, not an Agent Memory call.
    threshold, turns = ams_settings()
    found = await ams_client.search_long_term_memory_async(request=ams_request(ams_query(messages, turns), owner, threshold))

    # The prompt: the app puts the recalled facts in front of the model.
    system = ams_build_system(owner, [m.text for m in found.items])
    history = [{"role": m.role, "content": str(m.content)} for m in messages if m.role in ("user", "assistant")]

    async for text in stream_llm(system, history, config):
        yield text
```

What changed: the `ams_remember(...)` line became the `add_session_event_async` call with the same five keyword arguments the helper used. The `query = ...` and `recalled = await ams_recall(...)` lines became one `search_long_term_memory_async` call, and `recalled` became `found.items`. Everything else, including both comment lines and the docstring, is unchanged. The cell now references `ams_client`, `dt`, `models`, and `ams_request`, and no longer references `ams_recall` or `ams_remember`. It must not reference `get_threshold`, `get_turns`, `ams_threshold`, `ams_turns`, or `ams_chatbot`.

### 4. `am_turn_sweep`: the sweep calls the SDK method

Replace the body with:

```python
async def ams_sweep(query: str, store: dict[str, list[models.MemoryRecord]]) -> dict[str, dict[str, float]]:
    """For each owner in `store`, the highest threshold that returns each memory. The API returns no score."""
    grid = [round(0.05 * i, 2) for i in range(21)]  # 0.00 to 1.00
    pairs = [(owner, t) for owner in store for t in grid]
    gate = asyncio.Semaphore(12)  # 42 searches per sweep; keep a dozen in flight

    async def recall(owner: str, t: float) -> list[models.MemoryRecord]:
        async with gate:
            r = await ams_client.search_long_term_memory_async(request=ams_request(query, owner, t))
            return r.items

    hits = await asyncio.gather(*(recall(owner, t) for owner, t in pairs))

    bands = {owner: {m.text: 0.0 for m in records} for owner, records in store.items()}
    for (owner, t), found in zip(pairs, hits):
        for m in found:
            bands[owner][m.text] = max(bands[owner].get(m.text, 0.0), t)

    return bands


# The search text behind the last turn (the first starter prompt before any chat), and how high a threshold
# each memory clears for it, for every customer. Depends on the chat, N, and the store, not on the threshold.
ams_turn_live = any(m.role == "user" for m in ams_chatbot.value)
ams_turn_query = ams_query(ams_chatbot.value, get_turns()) if ams_turn_live else ams_prompts[0]
ams_turn_bands = await ams_sweep(ams_turn_query, ams_store)
```

What changed: the inner `recall` returns `r.items` from a direct `search_long_term_memory_async` call instead of `await ams_recall(...)`. Nothing else changed. The cell now references `ams_client` and `ams_request` and no longer references `ams_recall`.

Correction (after the fact, 2026-10-07): the first version of this paragraph said the two comment lines above `ams_turn_live` moved here from above `async def ams_sweep`. They were already above `ams_turn_live` before this move (the stage C review's first finding had been applied earlier), so the body above matches the old cell apart from the inner `recall`. The implementer and both reviewers caught this; the body they pasted was correct either way.

### 5. `am_build_md`: retitle the section

Replace the body with:

```python
mo.md(r"""
## The Agent Memory code: client, store, request and prompt, turn

One cell per step, with the code visible. Seed and Clean up call the store functions, and each chat turn calls `ams_turn`.
""")
```

Only the heading changed ("search, prompt" became "request and prompt").

## The edit script

Write this to `C:\Users\NickCooper\AppData\Local\Temp\am_inline_1.py` exactly, with the five bodies above pasted between the raw-string quotes. Run it once.

```python
import marimo._code_mode as cm

PROMPT = r'''<body of section 2>'''

TURN = r'''<body of section 3>'''

SWEEP = r'''<body of section 4>'''

BUILD_MD = r'''<body of section 5>'''

NAMES = ["am_recall_fns", "am_prompt_fns", "am_turn_fn", "am_turn_sweep", "am_build_md"]

for m in ams_chatbot.value:
    print("CHAT", repr(m.role), repr(str(m.content)[:200]))

async with cm.get_context() as ctx:
    before = {n: ctx.cells[n].code for n in NAMES}  # read at this version, or edit_cell raises StaleCellError
    assert "async def ams_recall" in before["am_recall_fns"], "am_recall_fns is not the cell this spec expects"
    assert "async def ams_remember" in before["am_prompt_fns"], "am_prompt_fns is not the cell this spec expects"
    assert "await ams_recall(" in before["am_turn_fn"] and "await ams_recall(" in before["am_turn_sweep"]
    assert "search, prompt, turn" in before["am_build_md"]

    ctx.delete_cell("am_recall_fns")
    ctx.edit_cell("am_prompt_fns", code=PROMPT)
    ctx.edit_cell("am_turn_fn", code=TURN)
    ctx.edit_cell("am_turn_sweep", code=SWEEP)
    ctx.edit_cell("am_build_md", code=BUILD_MD)

    ctx.run_cell("am_prompt_fns")
    ctx.run_cell("am_turn_fn")
    ctx.run_cell("am_turn_sweep")
    ctx.run_cell("am_build_md")

print("context closed without error")
```

Paste each body so that the raw string starts on the line after `r'''` only if you also strip that leading newline; the simplest correct form is `PROMPT = r'''def ams_query(...` with the body's first line on the same line as the opening quotes and the closing `'''` directly after the body's last character. Cell code in the kernel carries no trailing newline.

## The verification script

Write this to `C:\Users\NickCooper\AppData\Local\Temp\am_inline_2.py` and run it in a separate kernel call after the edit script has printed `context closed without error`. Wait for it to finish; the sweep it triggers makes 42 searches and can take ten to twenty seconds.

```python
import html as _html
import inspect
import re as _re
import types

import marimo._code_mode as cm

ctx = cm.get_context()
by_id = {c.id: c.name for c in ctx.cells}

order = [c.name for c in ctx.cells if (c.name or "").startswith("am_")]
print("ORDER:", order)
print("COUNT:", len(order), "| am_recall_fns present:", "am_recall_fns" in order)

for n in ["am_prompt_fns", "am_turn_fn", "am_turn_sweep", "am_build_md", "am_workbench", "am_chat", "am_store", "am_settings"]:
    c = ctx.cells[n]
    print(f"{n:14s} id={c.id} status={c.status} errors={c.errors} hide_code={getattr(c.config, 'hide_code', None)}")

print()
for target in ["ams_recall", "ams_remember", "ams_request", "ams_query", "ams_build_system", "ams_settings"]:
    owners = [by_id.get(cid, cid) for cid, impl in ctx.graph.cells.items() if target in impl.defs]
    users = [by_id.get(cid, cid) for cid, impl in ctx.graph.cells.items() if target in impl.refs]
    print(f"{target:18s} def={owners} refs={users}")

print()
for n in ["am_prompt_fns", "am_turn_fn", "am_turn_sweep"]:
    impl = ctx.graph.cells[ctx.cells[n].id]
    print(f"{n:14s} defs={sorted(impl.defs)}")
    print(f"{'':14s} refs={sorted(impl.refs)}")
print("cycles:", ctx.graph.cycles)

g = ctx.globals
print()
print("ams_recall in globals:", "ams_recall" in g, "| ams_remember in globals:", "ams_remember" in g)
print("session_id:", g.get("ams_session_id"))
print("chat len:", len(g["ams_chatbot"].value))
print("threshold/turns:", g["ams_settings"]())
print("owner:", g["ams_owner"].value)
print("store counts:", {k: len(v) for k, v in g["ams_store"].items()})
print("bands counts:", {k: len(v) for k, v in g["ams_turn_bands"].items()})
print("turn query:", repr(g["ams_turn_query"]), "| live:", g["ams_turn_live"])
print("isasyncgenfunction(ams_turn):", inspect.isasyncgenfunction(g["ams_turn"]))

# The search path exactly as the turn now spells it. Read-only.
msgs = [types.SimpleNamespace(role="user", content="Which foods should I avoid?")]
for owner in ["Priya", "Tom"]:
    r = await g["ams_client"].search_long_term_memory_async(request=g["ams_request"](g["ams_query"](msgs, 1), owner, 0.3))
    print(f"search {owner} at 0.3:", [m.text for m in r.items])

# The session-event call: bind the turn's five keyword arguments without calling anything.
sig = inspect.signature(g["ams_client"].add_session_event_async)
bound = sig.bind(
    session_id="probe", actor_id="Tom", role=g["models"].MessageRole.USER,
    content=[{"text": "probe"}], created_at=g["dt"].datetime.now(g["dt"].timezone.utc),
)
print("add_session_event_async kwargs bind:", sorted(bound.arguments))


def _text(cell):
    raw = str(cell.output.data) if cell.output else ""
    return raw, _re.sub(r"<[^>]+>", " ", _html.unescape(raw))


print()
raw, txt = _text(ctx.cells["am_build_md"])
print("am_build_md text:", " ".join(txt.split())[:160])
raw, txt = _text(ctx.cells["am_workbench"])
for needle in ["ownerId Tom", "Same search, ownerId Priya", "System prompt", "Search request", "similarityThreshold"]:
    print(f"workbench has {needle!r}:", (needle in txt) or (needle in raw))
print("workbench ams-row count:", raw.count("ams-row"))
```

Expected: 15 names in ORDER with `am_recall_fns` absent and `am_prompt_fns` directly after `am_store_fns`; every listed cell `idle` with `errors == []` (`am_build_md` may read `stale`, a known cosmetic status for static markdown); `ams_recall` and `ams_remember` with no owner and no referrers, and absent from globals; `ams_request` defined by `am_prompt_fns` and referenced by `am_workbench`, `am_turn_fn`, `am_turn_sweep`; `am_prompt_fns` defs exactly `ams_build_system`, `ams_query`, `ams_request`; `am_turn_fn` refs include `ams_client`, `ams_request`, `dt`, `models` and exclude `ams_recall`, `ams_remember`, `get_threshold`, `get_turns`, `ams_threshold`, `ams_turns`, `ams_chatbot`; `cycles` empty; chat len 0; threshold/turns `(0.3, 3)` (the state cell is untouched, so the N slider stays at 3); owner Tom; store counts Tom 3, Priya 3; a new `tom-...` session id; the Priya search returns the peanut and tree nut memory and the Tom search returns nothing at 0.3 (Tom's bands may include two extracted memories the store listing does not show yet; that is pre-existing); the bind prints the five argument names; `isasyncgenfunction` True; the heading text contains "request and prompt, turn"; every workbench needle True.

## Disk checks

From `C:\Users\NickCooper\projects\demo\redis-iris-basics` (Bash tool), after the kernel has saved:

```bash
grep -n "async def ams_recall\|async def ams_remember\|def am_recall_fns\|ams_recall(\|ams_remember(" iris_basics.py   # expect no output
grep -n "def am_prompt_fns(\|request and prompt, turn\|found.items\|search_long_term_memory_async" iris_basics.py
wc -l iris_basics.py
uv run python -m marimo check --strict iris_basics.py; echo "check exit $?"
uv run pytest tests/test_voice.py -q
```

Expected: the first grep prints nothing; the second shows `def am_prompt_fns(ai, it):`, the new heading, `found.items`, and three `search_long_term_memory_async` lines (store, turn, sweep); the file is shorter than 650 lines; `marimo check --strict` exits 0 with no output; the voice test reports 8 passed. Do not run the full test suite; it encodes an older notebook and its failures there are known and unrelated.

## Expected end state

Column 4, in order: `am_title`, `am_store_button`, `am_store`, `am_owner`, `am_workbench`, `am_build_md`, `am_connect`, `am_store_fns`, `am_prompt_fns`, `am_turn_fn`, `am_machinery_md`, `am_settings`, `am_chat`, `am_turn_sweep`, `am_render`. Fifteen cells. Visible code (`hide_code=False`) on exactly `am_connect`, `am_store_fns`, `am_prompt_fns`, `am_turn_fn`; every other `am_*` cell hidden. Owner Tom, threshold 0.3, N 3, chat empty, store Tom 3 / Priya 3.

## Report

Write `C:\Users\NickCooper\projects\demo\redis-iris-basics\.agents\reviews\am-inline.report.md` with: the chat transcript you printed before editing; each cell changed and how; whether the single context closed cleanly; the verification script's full output; the disk check output; any deviation from this spec and why; anything you noticed but did not touch. Then return the structured result the coordinator asked for.
