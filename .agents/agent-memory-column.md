# The Agent Memory column of `iris_basics.py`: current state

Last verified 2026-10-07, evening, after the polish pass, which a separate reviewer passed with notes (`archive/agent-memory-2026-10/reviews/am-polish.review.md`): every rewritten cell matched its spec, the threshold, N, owner, and model-failure probes behaved, and the notes (one README sentence, one plumbing lead-in) were applied afterwards. This is the one document that describes column 4 as it is. Everything under `archive/agent-memory-2026-10/` is history: proposals, specs, build reports, and reviews from 2026-10-06 and 2026-10-07 that led here. Read them only for the reasoning behind a ruling; the notebook on disk and the live kernel are the source of truth for what the column does.

## Purpose and reader

The notebook teaches a prospective Redis Iris developer how to integrate each Iris component into an agent workflow by taking the component apart and showing it is Redis underneath. Column 4 does that for Redis Agent Memory with two fictional Cairn Outfitters customers, Tom and Priya. Three principles, stated in `am_title` and demonstrated on screen: long-term memory recalls by meaning, the `ownerId` filter scopes recall to one customer, and the app builds the prompt.

## Layout, in notebook order

| Cell | Code shown | What the reader sees or what it defines |
|---|---|---|
| `am_title` | hidden | Problem sentence, three numbered principles, "public preview" plus the Settings line, Docs line, and the source-target call map the other three titles also carry |
| `am_store_button` | hidden | Heading "Seed and list two customers' long-term memories", four sentences (Seed, Refresh plus the extracted-type note, Clean up), the three `run_button`s |
| `am_store` | hidden | Seed, Clean up, and Refresh actions, then two markdown tables (memory, type) per `ownerId`. Also reruns on every chat turn so the tables agree with the bars. Defines `ams_store` |
| `am_owner` | hidden | The `Chat as` dropdown (defined here, displayed in the workbench) and the walkthrough prose with one payoff per step. Defines `ams_owner` |
| `am_workbench` | hidden | Left pane: dropdown and chat. Right pane: N slider, search text, the bar caption, threshold slider, this customer's bars ("what the `filter` returns"), the other customer's bars ("the same search for comparison. The agent never sends it."), a rule, "Moving a slider redraws this pane without a new turn.", Search request, System prompt, Session heading with the user messages written so far |
| `am_build_md` | hidden | Heading "The Agent Memory code: client, store, prompt, turn" and the rule "Every call on `ams_client` goes to Agent Memory, and the rest is the app." |
| `am_connect` | visible | `AgentMemory(...)` from the three `MEMORY_*` settings. No output. Defines `ams_client`, `errors`, `models`, `AgentMemory` |
| `am_store_fns` | visible | `ams_seeds` (both keys literals), `ams_memories` (filter-only search, limit 100), `ams_seed` (stable ids `<owner>-seed-<i>`, so a second Seed upserts and reports nothing new), `ams_forget` (deletes the owner's sessions, then memories) |
| `am_prompt_fns` | visible | `ams_query` (last N user messages, in order), `ams_request` (the search body), `ams_build_system` (the Cairn Outfitters brief plus "What you remember about {owner}") |
| `am_turn_fn` | visible | `ams_session_id` (new on every owner switch) and `ams_turn`: notebook inputs, session write, long-term search, prompt, model stream, with a `try` that yields "The model call failed (...)" so the workbench still reruns |
| `am_next_md` | hidden | "Take `ams_turn` into your agent": what to copy, what to replace, the LangGraph tutorial link |
| `am_machinery_md` | hidden | "Notebook plumbing: slider state, chat object, threshold sweep, renderers", naming the four cells below |
| `am_settings` | hidden | `mo.state` for threshold (0.3) and N (1), and `ams_settings()` that the turn reads |
| `am_chat` | hidden | The two debounced sliders with `on_change` setters, the five starter prompts, `ams_chatbot = mo.ui.chat(ams_turn, ...)` |
| `am_turn_sweep` | hidden | `ams_sweep` (21 thresholds times 2 owners, 12 in flight, limit 100), `ams_session_texts` (returns `[]` on `NotFoundErrorResponseContent`), and `ams_turn_live`, `ams_turn_query`, `ams_turn_bands`, `ams_turn_events` |
| `am_render` | hidden | `ams_bars` (rows in store order, ticks only when there are rows, grid 2fr/3fr) and `ams_memory_table` |

Column 0 (shared): the imports cell (`dt`, `uuid`, `asyncio`, `html`, `json`, `os`, `ai`, `mo`), `redis_client` (`REDIS_CLIENT`, `AI_MODEL`), `llm_client` (`stream_llm` for OpenAI or Anthropic by `AI_MODEL`, key read with `os.environ.get` so a missing key fails at the first turn). Columns 1 to 3 are title cells only in this working copy.

Starter prompts, in walkthrough order: "Which foods should I avoid?", "What pets do I have?", "Where am I headed this summer?", "Any packing tips?", "I'm training for a marathon in October."

## Rulings (Nick's), do not undo without asking

- Keep the "Search with the last N user messages" slider. The fourth starter prompt exists to give it a payoff.
- Every function that explains the Iris interface stays visible. Only rendering and notebook plumbing is hidden.
- The owner dropdown stays upstream of the chat, so a switch gives a fresh session id and a fresh chat.
- `mo.state` plus `ams_settings()` is the rerun guard: a `mo.ui.chat` model function runs in the chat cell's context and cannot read `.value` of sliders defined there, and a cell that references a state getter reruns on every setter call. The sliders live in `am_chat` with `on_change` setters, the state lives in `am_settings`, the turn calls `ams_settings()`.
- `ams_request` stays a function because the workbench renders the live request body from it.
- Markdown tables through `mo.md`, not `mo.ui.table` (0.24.2 hard-codes the column explorer, chart builder, and page-size selector).
- Redis Cloud is assumed. True globals are UPPERCASE and live in column 0.
- Agent Memory is "in public preview" (Redis Cloud create-service page and the Python quickstart, read 2026-10-07). Context Retriever keeps "in preview".
- Voice: `C:/Users/NickCooper/projects/demo/.agents/VOICE.md`, enforced by `tests/test_voice.py` (25-word ceiling with inline code stripped, serial comma, no semicolons, no exclamation marks, never "developers").
- Delegated work runs as paseo agents (Sonnet 5.5 for cheap implementers and reviewers), one report file each under `.agents/reviews/`, unless Nick invokes a Workflow himself.

## Service and kernel facts, verified

- The search API returns no similarity score. `MemoryRecord` has id, text, timestamps, memory_type, session_id, owner_id, namespace_ref, topics, attributes. The bars come from running the same search at 21 thresholds, 0.00 to 1.00.
- Bands on this store, N 1 (2026-10-07): foods question gives Priya's allergy 0.35 and 0.05 for each of Tom's; pets gives dogs 0.40 and cat 0.40; summer gives the Dolomites 0.40 and the canoe 0.40; "Any packing tips?" alone 0.15 for both trips, with the summer question 0.35 for both; the marathon line 0.30 for both trips. Nothing in this store clears 0.55.
- One search is about 300 ms; the 42-search sweep 1.8 to 2 s. The SDK client has a 5 s httpx timeout and no retries by default; one `ReadTimeout` puts `am_turn_sweep` in `exception` until something reruns it (an owner re-select does). `retry_config` on `AgentMemory(...)` is the candidate fix, not applied.
- A `bulk_create` with an existing id answers `created: [id]` and replaces the text in place. A memory created without `memoryType` is filed as `semantic`.
- Extraction is enabled on Nick's store: `episodic` memories appeared about two minutes after a factual chat message (default cadence 5 minutes, range 60 to 600 s). Short-term TTL default 1 hour, long-term 365 days.
- A session's `ownerId` is set from the first event's `actorId`, and memories extracted from the session carry it. The turn writes only the user message, never the reply, and the prompt never reads session events.
- `get_session_memory_async` on a deleted session raises `redis_agent_memory.errors.NotFoundErrorResponseContent`. `list_sessions_async(filter_owner_id=...).items` is a list of id strings.
- `run_button.value` is True only for the run a click triggers, then resets. `mo.ui.chat` pushes its value only when the model function completes with text, so a failed turn that raises triggers no reruns; the `try` in `ams_turn` is what keeps the workbench live.
- An `on_change` setter is attributed to the control's own cell, which marimo excludes from the rerun, so `am_chat` may read the getters for start values.
- `marimo run` sends no code unless started with `--include-code`; the README says so.
- `create_cell` lands a new cell at the end of column 0 in this `width="columns"` notebook, and any later edit batch drops it back there while its `column` config is None. Fix seen to hold on 2026-10-07: `edit_cell(name, column=4)` and `move_cell(name, after=...)` in one context, which also stamps `column=4` on the cell in the file. Check the file afterwards. A connected tab re-saves the file with `column=` only on the first cell of each column.

## How to edit the column

The live kernel at `http://127.0.0.1:2718/` is the source of truth while a session runs. Use the marimo-pair skill at `C:/Users/NickCooper/projects/demo/.agents/skills/marimo-pair/` by hand (the Skill tool does not list it): first kernel call `help(cm)`, scripts written with the Write tool to `%LOCALAPPDATA%\Temp` (ASCII only, cell bodies in raw `r'''...'''` strings), run through `execute-code.sh` with the jq PATH prefix from the skill's Windows notes. Read every cell inside the same `get_context()` before `edit_cell`, and put all edits that move a name between cells in one context. After edits: all `am_*` cells `idle` with no errors, `ctx.graph.cycles == set()`, `uv run python -m marimo check --strict iris_basics.py` exit 0, `uv run pytest tests/test_voice.py -q` 8 passed.

Probes that prove the guards still hold: `set_ui_value(ams_threshold, 0.45)` must leave `ams_session_id`, `id(ams_chatbot)`, and `id(ams_turn)` unchanged and move the line to `left:45%`; `set_ui_value(ams_owner, ["Priya"])` must give a `priya-` session id, new chat and turn objects, and swap the bar labels. Each `set_ui_value` goes in its own kernel call.

## Store and environment state

- Long-term memory: Tom `tom-seed-1..3`, Priya `priya-seed-1..3`, all `semantic`. No sessions for either owner after the polish pass. Clean up deletes both sessions and memories; Seed rewrites the same six ids.
- `.env` holds the three `MEMORY_*` values and `LLM_API_KEY`. The OpenAI key was invalid on 2026-10-07 evening (401 `token_invalidated`), so chat turns end with "The model call failed (AuthenticationError)" until it is replaced.

## Open items

- README Steps 4 to 6 and "How it works" still describe the older notebook (Claude, `cairn-<run>`, `call_service`). Step 7, Step 8, Step 2, and the key name are current.
- `tests/` beyond `test_voice.py` encode the v2 notebook (no `intro`, no `app.setup`, `mo.state` forbidden, column budgets) and fail for those reasons. Nick plans to rewrite them once the columns settle.
- `column=` stamps are first-cell-only; the test suite expects every cell stamped.
- Not applied: `retry_config` on the client, memoizing the sweep by search text and memory ids, a real browser chat turn since the mo.state hotfix (the model-failure path was verified with a direct `ams_turn` call).

## History, one paragraph

2026-10-06: a play-first rebuild of the column with calibrated thresholds found the state-getter rerun trap. 2026-10-07 morning: a probe-first redesign proposal and three critiques, then a six-stage build by paseo agents with a reviewer per stage, a hotfix moving the slider state into `mo.state`, the workbench consolidation (chat beside the data), and a regroup into demo, Iris code, and plumbing. Afternoon: a simplification pass (fewer words, markdown tables, headings that name their controls) and an inline pass (`ams_recall` and `ams_remember` folded into the turn). Evening: a read-only teaching review (six lenses, 40 verified findings) and this polish pass implementing them. The dated documents are in `archive/agent-memory-2026-10/`.
