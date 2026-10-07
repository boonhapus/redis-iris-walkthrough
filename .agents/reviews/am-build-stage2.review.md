# Agent Memory build, stage 2 review (2026-10-07)

**Verdict: PASS WITH NOTES.** No blockers. The five cells match the spec byte for byte. The notes are things stage 3 and 4 will hit, plus two panel-wording and robustness issues in the completed-turn branch, which nobody can run until a chat turn happens.

Method: dumped the live kernel (`rv_dump.py`), diffed each cell against the spec's code block with `difflib` (`rv_diff.py`), ran read-only behaviour checks (`rv_beh.py`, `rv_more.py`), and read the SDK and marimo 0.24.2 source. No cell edited, no chat turn, no write.

## 1. Spec diff

All five cells: `code.strip() == spec_code.strip()`, zero diff lines. `hide_code` live: `am_recall_fns` False, `am_prompt_fns` False, `am_chat` False, `am_turn_sweep` True, `am_prompt_panel` True. All match. Names, signatures, logic, and prose are identical to the spec. Nothing to list.

## 2. Statuses and names

- All five cells and every other am_* cell: status `idle`, errors `[]`. Only `rs_title`, `lc_title`, `cr_title` are `stale`, and those are other columns.
- `am_prompt_panel` output text: "3. What the model received  Send a message to see the search text, the recall, and the prompt for the last turn." Heading and placeholder present.
- Gone list: none of the 16 names is defined by any cell (`ctx.graph` defs scan). No am_* cell code mentions `ams_chat`, `ams_trace`, or `ams_state`.
- Owned public names, each defined exactly once: `ams_request`, `ams_recall`, `ams_sweep` (`am_recall_fns`); `ams_query`, `ams_remember`, `ams_build_system` (`am_prompt_fns`); `ams_threshold`, `ams_turns`, `ams_session_id`, `ams_turn`, `ams_chatbot` (`am_chat`); `ams_turn_query`, `ams_turn_bands` (`am_turn_sweep`). No name has two definers anywhere in the graph.

## 3. Graph

- `am_chat` refs: `AsyncGenerator, ai, ams_build_system, ams_owner, ams_query, ams_recall, ams_remember, list, mo, round, secrets, str, stream_llm`. The only UI element from another cell is `ams_owner`. As intended.
- `am_turn_sweep` refs: `ams_chatbot, ams_owner, ams_query, ams_store, ams_sweep, ams_turns`. No `ams_threshold`. No re-sweep on a slider move.
- `am_prompt_panel` refs include `ams_threshold`, `ams_turns`, `ams_turn_bands`, `ams_turn_query`, `ams_chatbot`, `ams_session_id`, `ams_client`. Correct.
- Cycles: none. Stage 1 and 2 edges run `am_connect` -> `am_store_fns`/`am_render` -> `am_store`; `am_store_fns` -> `am_owner`; `am_recall_fns` and `am_prompt_fns` -> `am_chat`; `am_chat` + `am_store` -> `am_turn_sweep` -> `am_prompt_panel`. All acyclic, and marimo accepted the graph.

## 4. Behaviour without writes

- `ams_query` on [user "first", assistant "a", user "second"]: n=1 -> `'second'`; n=2 -> `'second\nfirst'`; n=5 -> `'second\nfirst'`. Correct.
- `ams_build_system("Tom", [])` -> `'You are a helpful AI assistant.'`. With two facts -> `'You are a helpful AI assistant.\n\nWhat you remember about Tom:\n- two dogs\n- size 11'`. Correct.
- `ams_request("q", "Tom", 0.3)` equals the spec dict: True.
- `await ams_sweep("What pets do I have?", ams_store)`, wall time **1.0 s**: Tom `{shoes 0.15, two dogs 0.40, Dolomites 0.05}`, Priya `{cat 0.40, allergy 0.20, canoe 0.05}`. Dogs and cat at 0.40 as expected.
- `ams_turn` is an async generator function: `inspect.isasyncgenfunction` True, signature `(messages: list[ChatMessage], config: ChatModelConfig) -> AsyncGenerator[str, None]`. Not called.

## 5. Completed-turn branch of `am_prompt_panel` (read, not run)

- `e.role.value`: `MessageRole` is `class MessageRole(str, Enum, metaclass=OpenEnumMeta)` with `USER`, `ASSISTANT`, `SYSTEM`. A known value parses to the enum, so `.value` works. The open-enum metaclass keeps an unknown role as a bare `str`, where `.value` would raise `AttributeError`. The notebook writes only `USER`, so this cannot fire today. See finding 4.
- `e.content[0].text`: `SessionEvent.content: List[Content]`, `Content = Text`, `Text.text: str`. Exists. `IndexError` only if an event has empty content, which `ams_remember` never writes.
- `e.created_at.strftime`: `created_at: datetime`. Exists.
- `_session.events`: `GetSessionMemoryResponseContent.events: List[SessionEvent]`. Exists. `len(_session.events)` fine.
- `_n > 1` plural: correct for int N. `round(band, 2) >= _t`: bands are already multiples of 0.05 and `_t` is rounded, so equality on the boundary is exact. Matches what the turn's own search at `round(threshold, 2)` returned.
- 404 gate: `get_session_memory_async` on an unused id raises `NotFoundErrorResponseContent` (confirmed live, both a made-up id and the current `ams_session_id`: "Session Not Found", 404). The read sits in the `else` branch behind `_turn_done = bool(value) and value[-1].role == "assistant"`. The assistant message can only exist after `ams_turn` ran, and `ams_turn` awaits `ams_remember` first, so the session exists by then. On an owner switch `am_chat` redefines `ams_session_id` and `ams_chatbot` together, so the new empty chatbot keeps the gate shut. Gate holds.
- `ams_chatbot.value[-1].role` is a plain `str`: `marimo.ai.ChatMessage.role: Literal["user","assistant","system"]` on marimo 0.24.2, and `_convert_value` builds it from the frontend dict. The comparison to `"assistant"` is right. Same for `m.role == "user"` in `ams_query` and `ams_turn_sweep`.

## 6. Prose (reader-visible output)

The completed branch is the only rendered prose that is new. No exclamation marks, no semicolons, sentences under 25 words, serial commas present. Two nits:

- Placeholder: "the search text, the recall, and the prompt" is a literal inventory of the three things the panel shows, so I let it stand. If Nick's rule-of-three ruling is read strictly, cut to "Send a message to see what the last turn searched for and what the model received." Optional.
- Term drift: heading says the model "received" and a later label says "sent" (`System prompt sent to the model`). The session label says "as the service holds it" while the rest of the notebook says "Agent Memory" and "the store". Pick one verb and name the product. See finding 2.

## Findings, ranked

### 1. (Medium, stage 4) Panel and sweep rerun more than the spec's "twice" note suggests, and the cost is invisible until measured

Evidence: marimo's streaming chat (`chat.py` `_run_prompt`) returns after the stream without appending the assistant message ("The frontend will add the accumulated message"), then the frontend pushes the history through `UpdateUIElementCommand`. A turn therefore produces one or two value pushes (the user message at send, the assistant message at finish), and every push reruns `am_turn_sweep` (21 searches, about 1 s) and `am_prompt_panel`. Only the push where the last role is `assistant` does the session read. Slider moves also rerun the panel, and each one with a finished turn does a `get_session_memory_async`.

Impact: roughly 21 or 42 sweep searches plus 1 recall plus 1 or more session reads per turn, and one session read per slider move. Works, but a flashing placeholder is likely between pushes (last role `user` -> placeholder, then full panel), and stage 4 step 7 should record the real count.

Fix: none required. In stage 4 record the observed push count and sweep count per turn. If the placeholder flash is visible, switch the gate to `bool(ams_chatbot.value) and any(m.role == "assistant" for m in ams_chatbot.value)` so it holds the last finished turn's panel while the next turn streams. That change would also need the sweep to keep the old bands, so leave it unless the flash bothers Nick.

### 2. (Low-medium, wording) "System prompt sent to the model" is false after a slider move

Evidence: the panel rebuilds the prompt from `ams_turn_bands` at the current `ams_threshold` and the query at the current `ams_turns`. `ams_turn` read those values at send time. After the user moves either slider post-turn, the panel shows a prompt the model never received, under a bold label that says "sent". The line above the bars ("The recall and the prompt follow the slider.") hedges the recall but not the prompt label. The session table does show what was actually written.

Fix (voice-safe, one label): change the label to `**System prompt at this threshold**` or `**System prompt the app builds at this threshold**`, and change the session label to `**Session `{id}` in Agent Memory** ({n} events)`. This deviates from the spec's prose, so Nick or the stage 3 builder should decide.

### 3. (Low) Unhandled session read and fence breakage in the completed branch

- `await ams_client.get_session_memory_async(...)` has no `try`. A transient service error turns the whole panel into an error output, including the bars. The gate only protects against the 404 from an unwritten session. Fix: wrap the read, and on failure render the bars and prompt with `mo.md("_Session read failed._")` in place of the table. Defer unless stage 4 sees a failure.
- `mo.md(f"```text\n{ams_turn_query}\n```")` and the system prompt block break if a user message or a memory contains a triple backtick. Fix if it matters: use `mo.plain_text` or a four-backtick fence. Demo prompts do not contain one.

### 4. (Low) `e.role.value` is brittle against an unknown role

Open enum: an unrecognised role stays a `str` and `.value` raises. Cannot happen with the notebook's own USER-only writes, but extracted or externally written events could carry another role. Fix: `getattr(e.role, "value", e.role)`.

### 5. (Low) Bars collapse and mislead when an owner has more than 10 memories or duplicate texts

`ams_request` has `limit: 10` and `ams_sweep` keys bands by `m.text`. A memory beyond the tenth is never returned, stays at 0.0, and satisfies `0.0 >= _t` only when the slider is 0.0. Two memories with identical text (a background extraction that repeats a seed) collapse to one bar. Stage 4's extraction test could produce the duplicate. Fix: none now. Note it if the extraction run adds a repeat.

### 6. (Info) Rerun side effects to know before stage 3

- Any rerun of `am_chat` (owner switch, or editing `am_connect`, `am_recall_fns`, `am_prompt_fns`) calls `secrets.token_hex(8)` again, so the chat and its session reset. Stage 3 touches none of those cells, so it is safe. Stage 3's acceptance check "`am_chat` does not rerun on a threshold move" will pass, because `ams_threshold` is defined in `am_chat` and `am_chat` does not read it outside `ams_turn`.
- The `ams_threshold` slider is defined inside `am_chat`, so `am_probe_panel` (stage 3) will depend on `am_chat` and rerun on every owner switch. That is cheap. `am_sweep` does not depend on `am_chat`, so the 42-search probe sweep does not rerun on a switch. Keep it that way: do not let `am_sweep` read `ams_threshold`.
- `am_turn_sweep` also reruns on Refresh (it reads `ams_store`), 21 searches when a turn exists. Expected and cheap.
- Assistant replies are never written to session memory (`ams_remember` writes the user message only), so the session table shows USER events only. That matches the spec and stage 4 step 2 ("one more USER event"), but the "as the service holds it" label may read as incomplete to a customer. Nick's call.
- The report's note that only `am_title` shows `config.column == 4` is correct marimo behaviour, not a defect, as far as I can tell. I did not verify it in source.

## Not verified

- Whether the session read returns the just-written event immediately. The panel reads seconds after the write, so it is probably fine, but stage 4 is the first real test.
- The exact number of chat value pushes per streamed turn (source says up to two). Stage 4 step 7.
- The completed-turn render. Stage 4 must look at it.
