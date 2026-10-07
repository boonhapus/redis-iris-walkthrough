# Agent Memory build, stage 2 report (2026-10-07)

Applied "Stage 2" of `.agents/plans/agent-memory-build-spec.md` to the live kernel at http://127.0.0.1:2718/ through `marimo._code_mode`. `iris_basics.py` on disk was not edited. `help(cm)` ran first. The edit script (`$LOCALAPPDATA/Temp/s2_edit.py`, ASCII-only, written with the Write tool) asserted the expected ids (`Wvet`, `BYtC`, `LNbO`, `RGSE`, `yUGJ`), read all five cells inside the `cm.get_context()` block, then queued five `edit_cell` and five `run_cell` calls. Marimo validated and applied them on the first try. Code was copied from the spec verbatim (raw triple-quoted strings). `name=` and `hide_code=` went in the same `edit_cell` call for the reused cells.

## What changed, cell by cell

| Old | New | id | hide_code | Change |
|---|---|---|---|---|
| `am_steps` | `am_recall_fns` | Wvet | False | `ams_request`, `ams_recall`, `ams_sweep`. |
| `am_chat_fn` | `am_prompt_fns` | BYtC | False | `ams_query`, `ams_remember`, `ams_build_system`. |
| `am_chat` | `am_chat` | LNbO | False | Rewritten in place: `ams_threshold`, `ams_turns`, `ams_session_id`, `ams_turn`, `ams_chatbot`. |
| `am_state` | `am_turn_sweep` | RGSE | True | `ams_turn_query`, `ams_turn_bands`. |
| `am_trace` | `am_prompt_panel` | yUGJ | True | if/else panel, heading `### 3. What the model received`. |

Run list executed: `am_recall_fns`, `am_prompt_fns`, `am_chat`, `am_turn_sweep`, `am_prompt_panel`. No other cell was edited. No chat message sent, no button pressed, no memory deleted.

## Column-4 cells after the build

Order is the kernel's current order, mixed until stage 3. All statuses are `idle` and all `errors` are `[]`.

| Cell | id | hide_code | status | output (first 300 chars, tags stripped) |
|---|---|---|---|---|
| am_title | Xref | True | idle | Old title, untouched ("Redis Agent Memory An agent that forgets ...") |
| am_connect | SFPL | False | idle | Agent Memory is healthy. |
| am_store_md | IMKF | True | idle | What long-term memory holds Seed writes three memories per customer ... |
| am_owner | Shga | True | idle | Chat as Tom or Priya Ask the three starter questions as Tom ... |
| am_store_button | Kclp | True | idle | (buttons, no text) |
| am_store | pDtD | True | idle | ownerId: Tom (3) ownerId: Priya (3) Agent Memory also extracts memories ... Select Refresh to look for new ones. |
| am_render | HaRf | True | idle | (definitions only) |
| am_turn_sweep | RGSE | True | idle | (no output) |
| am_chat | LNbO | False | idle | (no text from the scan; the chat widget is not HTML text) |
| am_prompt_panel | yUGJ | True | idle | 3. What the model received Send a message to see the search text, the recall, and the prompt for the last turn. |
| am_read_code | Xfup | True | idle | Read the code ... ams_chat strings them into one turn. (untouched, stage 3 replaces it; the prose names the now-gone `ams_chat`) |
| am_recall_fns | Wvet | False | idle | (definitions only) |
| am_prompt_fns | BYtC | False | idle | (definitions only) |
| am_store_fns | emfo | False | idle | (definitions only) |

`am_prompt_panel` is `idle`, not `exception`, so the `mo.stop` artifact from stage 1 is gone.

## Graph defs and the "Gone" list

Defs after the build:

- `am_recall_fns`: `ams_recall`, `ams_request`, `ams_sweep`
- `am_prompt_fns`: `ams_build_system`, `ams_query`, `ams_remember`
- `am_chat`: `ams_chatbot`, `ams_session_id`, `ams_threshold`, `ams_turn`, `ams_turns`
- `am_turn_sweep`: `ams_turn_bands`, `ams_turn_query`
- `am_prompt_panel`: none
- Unchanged from stage 1: `Hbol`, `am_connect`, `am_owner`, `am_store_button`, `am_store`, `am_render`, `am_store_fns`.

All sixteen "Gone" names (`AMS_SEEDS`, `amnesia`, `ams_chat`, `ChatFn`, `ams_remember_turn`, `chatbot`, `turns`, `similarity_threshold`, `ams_turn_log`, `ams_sweep_cache`, `get_threshold`, `set_threshold`, `get_turns`, `set_turns`, `get_memories_version`, `set_memories_version`) are defined nowhere.

## Acceptance checks

- **No `exception` status: pass.** Every cell is idle.
- **Gone names defined nowhere: pass** (see above).
- **`am_prompt_panel` shows the heading and the placeholder: pass.**
- **`await ams_sweep("Which foods should I avoid?", ams_store)`: pass.** Wall time 1.47 s.
  - Tom: shoes 0.05, two dogs 0.05, Dolomites 0.05.
  - Priya: cat 0.05, allergy 0.35, canoe trip 0.05.
- **`ams_request("q", "Tom", 0.3)`: pass.** It equals `{"text": "q", "similarityThreshold": 0.3, "filter": {"ownerId": {"eq": "Tom"}}, "limit": 10}`.
- **No chat message sent: pass.**

## Deviations

None from the spec's code or flags. Notes only:

- The verification script printed `config.column` as `4` for `am_title` and `None` for the other am_* cells. I read that as marimo storing the column on the first cell of a column, but did not check the source. Cell order and column membership are stage 3's job.
- `am_read_code` is untouched, as instructed. Its prose still mentions `ams_chat`, which no longer exists; stage 3 replaces that cell.
