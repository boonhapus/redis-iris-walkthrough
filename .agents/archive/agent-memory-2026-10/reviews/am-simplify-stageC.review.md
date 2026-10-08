# Agent Memory simplification, Stage C (code moves) independent review (2026-10-07)

**Verdict: PASS WITH NOTES.** Findings: 0 high, 0 medium, 2 low. No cell differs from the spec, both probes behave as designed, `marimo check --strict` exits 0, `tests/test_voice.py` passes (8). The implementer's one deviation (comment indent 4, not 8) was right.

## Findings

### 1. Low: the comment above `ams_sweep` now describes the three lines below it, not the function it sits on

`am_turn_sweep` (live, and `iris_basics.py:573-574`):

```python
# The search text behind the last turn (the first starter prompt before any chat), and how high a threshold
# each memory clears for it, for every customer. Depends on the chat, N, and the store, not on the threshold.
async def ams_sweep(query: str, store: ...) -> ...:
```

The comment talks about "the last turn" and "depends on the chat, N, and the store". `ams_sweep` takes `query` and `store` as arguments and depends on nothing in the notebook. The comment was written for `ams_turn_live`, `ams_turn_query`, `ams_turn_bands`, which now sit 20 lines lower. The spec dictates this placement (C2), and the implementer flagged it, so it is not a deviation. It still misleads a reader who opens the hidden cell.

Why it matters: it is the only comment in the cell, and it is attached to the wrong statement.

Fix (cell body, move the two comment lines down, nothing else changes):

```python
async def ams_sweep(...):   # unchanged
    ...

# The search text behind the last turn (the first starter prompt before any chat), and how high a threshold
# each memory clears for it, for every customer. Depends on the chat, N, and the store, not on the threshold.
ams_turn_live = any(m.role == "user" for m in ams_chatbot.value)
```

### 2. Low: `ams_memories` silently truncates at 100, and `ams_seed` and `ams_forget` trust it

`am_store_fns`:

```python
request={"filter": {"ownerId": {"eq": owner}}, "limit": 100}
```

The page loop is gone, as specified. Past 100 memories for one owner, `ams_seed` would not see already-stored seed texts and would write duplicates, and `ams_forget` would delete only the first 100. The demo holds 9 memories (Tom 6, Priya 3), and extraction adds a handful per chat, so this is theoretical. I did not find the server's maximum `limit` in the docs (developer guide and Python quickstart only use `limit: 5`), so I cannot say whether 100 is accepted or capped. It worked in the kernel: `await ams_memories("Tom")` returned 6.

Why it matters: a reader copying `ams_memories` as the "list everything for an owner" recipe inherits a silent cap.

Fix, one comment line in the docstring area (no behavior change):

```python
    """Every long-term memory whose `ownerId` is `owner`, up to 100. The demo store holds a dozen."""
```

## Evidence by check

**1. Code equals spec.** I dumped `ctx.cells[...].code` for the four cells to a file and compared with the fenced blocks in spec C1, C2, C3 after stripping line-end whitespace and the trailing newline: `am_recall_fns EQUAL`, `am_turn_sweep EQUAL`, `am_store_fns EQUAL`. `am_turn_fn`: `git diff -U0 iris_basics.py` against HEAD shows exactly one changed line in that cell (`@@ -563 +507 @@`), `-        # ams_settings reads the two sliders (notebook machinery, at the end of the column).` to `+        # ams_settings returns the slider values. It is a notebook helper in the plumbing cells below, not an Agent Memory call.` The signature's parameter list is untouched. The comment sits at 4 spaces in the cell body (8 on disk), the same indent as the line above it (`# Long-term memory: this owner's memories, ...`) and the line below (`threshold, turns = ams_settings()`). The spec's own instruction ("matching the line above it") agrees with the implementer; the 8-space code block in the spec was the error.

**2. Disk equals kernel.** For each of the four cells, every non-blank live line appears in `iris_basics.py` as the same line with four spaces added (marimo's function-wrapper indent). No missing lines. `def am_store_fns(USER_ID, ams_client, models, uuid):` at 375, `def am_recall_fns(ams_client, models):` at 432, `am_turn_fn` comment at 507, `async def am_turn_sweep(` at 564, `async def ams_sweep` at 576.

**3. Cell health.** `am_recall_fns`, `am_turn_sweep`, `am_store_fns`, `am_turn_fn`, `am_workbench`, `am_store`: all `idle`, `errors == []` (read at the start and again at the end). `hide_code`: `am_recall_fns` False, `am_store_fns` False, `am_turn_fn` False, `am_turn_sweep` True. Unchanged.

**4. End state.** Column 4 order from `am_title` to the end: `am_title`, `am_store_button`, `am_store`, `am_owner`, `am_workbench`, `am_build_md`, `am_connect`, `am_store_fns`, `am_recall_fns`, `am_prompt_fns`, `am_turn_fn`, `am_machinery_md`, `am_settings`, `am_chat`, `am_turn_sweep`, `am_render` (16, matches the spec). Visible code on exactly `am_connect`, `am_store_fns`, `am_recall_fns`, `am_prompt_fns`, `am_turn_fn`; every other `am_*` cell is `hide_code=True`. Store counts: `ams_store` Tom 6 / Priya 3, `ams_turn_bands` Tom 6 / Priya 3, `await ams_memories("Tom")` 6, `await ams_memories("Priya")` 3. Chat length 0.

**5. Whole file.** `uv run python -m marimo check --strict iris_basics.py`: exit 0, no output. `uv run pytest tests/test_voice.py -q`: `8 passed in 0.02s`.

**6. Cross-stage seams.**
- "extracts" / "five minutes": one hit, `iris_basics.py:213`, in the store prose (`am_store_button`). The `am_store` output text contains neither.
- "created (UTC)": no hits in the file; absent from the `am_store` output.
- "What long-term memory holds", "Notebook machinery", "How the bars are measured", "Highest similarityThreshold", "Request as sent", `am_store_md`: no hits.
- "public preview": one hit at line 130, which is the LangCache column ("LangCache is in public preview on Redis Cloud"), not the Agent Memory column. Not a defect here.
- `am_build_md`: heading "The Agent Memory code: client, store, search, prompt, turn" (five names for the five code cells `am_connect`, `am_store_fns`, `am_recall_fns`, `am_prompt_fns`, `am_turn_fn`). The lead-in says "One cell per step, code open in edit mode. Seed and Clean up call the store functions, and each chat turn calls `ams_turn`." No sweep promise.
- `run_button`: only `am_store_button` (lines 203-205).

**7. The implementer's report.** It matches what I read: ids, defs, counts, line numbers (576 / 564 / 432), disk-grep results and the "noticed" items all reproduce. It records the indent deviation and the by-HTML bar check. It missed nothing that changes behavior; it flagged the misplaced comment (my finding 1) without proposing a fix.

**Stage C specifics.**
- `ctx.graph.cells[am_recall_fns].defs` = `['ams_recall', 'ams_request']` (exact).
- `am_turn_sweep` defs = `['ams_sweep', 'ams_turn_bands', 'ams_turn_live', 'ams_turn_query']`.
- `am_store_fns` defs = `['ams_forget', 'ams_memories', 'ams_seed', 'ams_seeds']`.
- `ctx.graph.cycles` = `set()`.
- `am_turn_fn` refs = `AsyncGenerator, ai, ams_build_system, ams_owner, ams_query, ams_recall, ams_remember, ams_settings, list, secrets, str, stream_llm`. Includes `ams_settings`; excludes `get_threshold`, `get_turns`, `ams_threshold`, `ams_turns`, `ams_chatbot`.
- `am_turn_sweep` refs = `ams_chatbot, ams_prompts, ams_query, ams_recall, ams_store, asyncio, get_turns, models` (+ builtins). It references `get_turns` and not `get_threshold`, which is why a threshold move does not rerun it.
- `ams_forget`: `memory_ids = [m.id for m in await ams_memories(owner)]`, then `if memory_ids:` guarding one `bulk_delete_long_term_memories_async(memory_ids=memory_ids)`, then `return memory_ids`. Guard present. I did not call it.
- `ams_memories`: one `search_long_term_memory_async` with `"limit": 100`, no `page_token`, no loop.
- `asyncio`: imported once, inside the column-0 cell `_` (the notebook's first cell, which returns `asyncio`), and referenced by `am_turn_sweep` (parameter, `asyncio.Semaphore(12)`, `asyncio.gather`). `am_store_fns` and `am_recall_fns` no longer take it.
- `grep "page_token\|asyncio.gather(\s*\*(\s*ams_client"`: no matches.

### Threshold probe (each step in its own kernel call)

| Step | session | chat len | `id(ams_chatbot)` | `id(ams_turn)` | threshold |
|---|---|---|---|---|---|
| before | `tom-c8cb339d501c` | 0 | 2682515773776 | 2682573196800 | 0.3 |
| after set 0.45 | `tom-c8cb339d501c` | 0 | 2682515773776 | 2682573196800 | 0.45 |
| after set back 0.3 | `tom-c8cb339d501c` | 0 | 2682515773776 | 2682573196800 | 0.3 |

- Session id, chat object and `ams_turn` object are unchanged, so `am_turn_fn` and `am_chat` did not rerun on the slider move. Status of both stayed `idle`. (`am_turn_fn` and `am_chat` have empty output, so I used object identity, not output text.)
- At 0.45 `am_workbench` shows `"similarityThreshold": 0.45` in the request, the line at `left:45%` on 9 bar rows (`left:30%` on none), every bar in the lighter "below" state (11 `ams-bar-below` matches = 9 bars + 2 CSS mentions, 0 plain `ams-bar` bars), and a system prompt with no facts.
- At 0.3 the request shows `0.3` and the system prompt carries `What you remember about Tom:` / `- User has no known allergies`.
- `ams_turn_bands` (does not depend on the threshold, so identical at both values): Tom `User has no known allergies` 0.30, `User does not have any cats` 0.15, shoes / dogs / hiking / "User's dogs, Max and Cece, are active and enjoy hiking" 0.05 each; Priya `I'm allergic to peanuts and tree nuts` 0.35, `I have a cat named Miso` 0.05, canoe trip 0.05. The 0.45 line clears none of them, which matches the brief's expectation.

### Workbench at 0.45

Full `am_workbench` output text at threshold 0.45, owner Tom, empty chat (tags stripped; the JSON block was tokenised by the highlighter, shown here joined):

```text
Search text, the first starter message until you send one:
Which foods should I avoid?

ownerId Tom
  User has no known allergies                                  0.30
  User does not have any cats                                  0.15
  I wear size 11 trail shoes                                   0.05
  I've got two dogs, Max and Cece                              0.05
  I'm planning a hiking trip in the Dolomites in August        0.05
  User's dogs, Max and Cece, are active and enjoy hiking       0.05
  (axis) 0  0.5  1

Same search, ownerId Priya
  I'm allergic to peanuts and tree nuts                        0.35
  I have a cat named Miso                                      0.05
  I'm planning a canoe trip in Maine in June                   0.05
  (axis) 0  0.5  1

System prompt
You are a helpful AI assistant.

Search request
{
  "text": "Which foods should I avoid?",
  "similarityThreshold": 0.45,
  "filter": {
    "ownerId": {
      "eq": "Tom"
    }
  },
  "limit": 10
}
```

No memory is at or above the line for either owner (highest is Priya's allergy at 0.35), and the system prompt has no facts. For Stage B: every bar renders in the "below" style, the line sits at `left:45%`, and the labels "ownerId Tom", "Same search, ownerId Priya", "System prompt", "Search request" are all present. The raw HTML was 10204 characters at 0.45.

### Owner probe

- Set `ams_owner` to `["Priya"]`: session `priya-cd9fd5724aa7`, chat length 0, `id(ams_chatbot)` 2682523622992 and `id(ams_turn)` 2682573204480 (both new, by design on an owner switch), threshold still 0.3. `am_workbench` shows "ownerId Priya" (line 11) and "Same search, ownerId Tom" (line 21), and the system prompt reads `What you remember about Priya:` / `- I'm allergic to peanuts and tree nuts`.
- Set back to `["Tom"]`: owner Tom, session `tom-cbd5bda67dfe`.

## Final state

- Owner: Tom. Threshold 0.3. N (`ams_turns`) 1. Chat length 0.
- `ams_session_id`: `tom-cbd5bda67dfe`. It differs from the starting `tom-c8cb339d501c` because the owner round trip creates a new session by design. Nothing was written to either session (chat empty).
- Store counts: Tom 6, Priya 3 (`ams_store`, `ams_turn_bands`, and fresh `ams_memories` calls agree).
- All four Stage C cells and `am_workbench` / `am_store` idle with no errors.
- Restored what I touched: threshold 0.45 to 0.3, owner Priya to Tom. I did not edit the notebook or tests, send chat, use buttons, call `ams_seed` or `ams_forget`, or open a browser. Scripts are at `C:\Users\NickCooper\AppData\Local\Temp\am_review_C_*.py`.
