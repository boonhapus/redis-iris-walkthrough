# Agent Memory build, stage 5 report (2026-10-07)

## Changed (live kernel, one `cm` block, validated and applied)

- `am_owner` (ROlb): spec code verbatim; defines `ams_owner`, shows prose only. hide_code=True.
- `am_chat` (TqIu): spec code verbatim; defines `ams_threshold`, `ams_turns`, `ams_session_id`, `ams_prompts`, `ams_turn`, `ams_chatbot`; no output. hide_code=False.
- `am_turn_sweep` (Vxnm): spec code verbatim; defines `ams_turn_live`, `ams_turn_query`, `ams_turn_bands` (per owner).
- `am_title` (Xref): four sentence replacements by exact `str.replace` (each matched once), the rest byte for byte: intro line, goal 1 sentence 2, goal 2 last sentence, goal 3 last sentence.
- Created `am_workbench` (Evqy), hide_code=True, spec code verbatim.
- Deleted `am_probe` (Hstk), `am_sweep` (nWHF), `am_probe_panel` (iLit), and `am_prompt_panel` (DnEU; see deviation 1).
- Graph check before deleting: nothing outside the three probe cells referenced `ams_probe` or `ams_probe_bands`. `ams_sweep` is now defined by `am_recall_fns`, which `am_turn_sweep` uses.
- Moves (second block): `am_workbench` after `am_turn_sweep`, `am_recall_fns` after `am_workbench`.

## Final order (column 4, 14 cells; hide_code)

am_title T, am_connect F, am_store_md T, am_store_button T, am_store T, am_store_fns F, am_owner T, am_settings T, am_chat F, am_turn_sweep T, am_workbench T, am_recall_fns F, am_prompt_fns F, am_render T.
Matches the spec's 14-cell order and flags. Cells above them: `_`, `redis_client`, `llm_client`, `rs_title`, `lc_title`, `cr_title`; no column-4 cell sits among them.

## Cell status (all errors `[]`, none `exception`)

| cell | status | output, first 300 chars (stripped) |
|---|---|---|
| am_title | idle | "Redis Agent Memory An agent that forgets a customer's two dogs ... Select Seed to write memorie..." |
| am_connect | idle | Agent Memory is healthy. |
| am_store_md | stale | What long-term memory holds Seed writes three memories per customer with POST /long-term-memory ... The store expires them after an hour by default. |
| am_store_button | idle | (empty text; buttons) |
| am_store | idle | ownerId: Tom (3) ownerId: Priya (3) Agent Memory also extracts memories ... select Refresh to look for what it extracted. |
| am_store_fns | idle | (none) |
| am_owner | idle | Chat, and the data behind each turn Ask the first three starter prompts as Tom ... Until you send a message, the panel beside the chat previews the first starter prompt. |
| am_settings | idle | (none) |
| am_chat | idle | (none) |
| am_turn_sweep | idle | (none) |
| am_workbench | idle | Search text, a preview of the first starter prompt until you send a message: What pets do I have? Recall for ownerId Tom at 0.30. Memories above the line go into the prompt. I've got two dogs, Max and Cece 0.40 I wear size 11 trail shoes 0.15 I'm planning a hiking trip in the Dolomites in August 0. |
| am_recall_fns | idle | (none) |
| am_prompt_fns | idle | (none) |
| am_render | idle | (none) |

`am_store_md` is `stale` (static markdown, unchanged by this stage; stale before the stage too). `rs_title`, `lc_title`, `cr_title` were also stale before.

## Acceptance

- No column-4 `exception`. `ams_probe`, `ams_probe_bands` defined nowhere; `ams_prompts` is defined in `am_chat` only, `ams_turn_live` in `am_turn_sweep` only.
- `am_chat` output empty; `am_owner` shows heading and prose only.
- `am_workbench` text checks. Pass: "similarityThreshold", "preview of the first starter prompt", "Recall for ownerId Tom at 0.30", "The same search as ownerId Priya", "You are a helpful AI assistant." Pass in the raw HTML only (the dropdown, chat prompts, and accordion are custom elements, so their text sits in attributes, not text nodes): "Chat as", "Which foods should I avoid?" (a chat starter-prompt chip), "Request as sent". **Fail: "0.35".** See deviation 2.
- Slider: before `ams_session_id` acd389d75dd807ec, after `set_ui_value(ams_threshold, 0.45)` unchanged; workbench says "at 0.45". The `"similarityThreshold": 0.45` request body is inside the accordion, which the stripped text does not expose, so I did not confirm it. Restored to 0.3 (`ams_threshold.value` = 0.3, `ams_turns.value` = 1).
- Owner: `set_ui_value(ams_owner, ["Priya"])` changed `ams_session_id` (acd389... to 2d9e81...); workbench shows "Recall for ownerId Priya" and "The same search as ownerId Tom". Restored to `["Tom"]`: session id changed again (e4185bc322284bb4), `ams_owner.value` = Tom, text back to Tom with Priya as "the other".
- Preview bands (query "What pets do I have?"): Tom dogs 0.40, shoes 0.15, Dolomites 0.05; Priya cat 0.40, allergy 0.20, Maine 0.05.
- No chat message sent, no button pressed, no memory deleted.

## Deviations

1. **Deleted `am_prompt_panel` too.** The spec's delete list omits it, but its 14-cell final order excludes it, and it reads `ams_turn_bands` as a flat text-to-band dict. With the new per-owner shape it would raise. It is superseded by `am_workbench` (same content). Nothing referenced it.
2. **"0.35" cannot appear.** The spec's acceptance expects Priya's allergy at 0.35, which is the "Which foods should I avoid?" sweep. Before any chat the preview is `ams_prompts[0]`, "What pets do I have?", where the allergy is 0.20. The page is correct per the spec code; the acceptance line assumes the foods prompt. "Which foods should I avoid?" is on the page only as a prompt chip.
3. **`am_title`: four replacements, not three.** The spec lists four bullets (intro plus three goals) under "three sentence changes". I applied all four. The live intro line read "...then search them and chat as either..." (a post-review fix); it is now "...then chat as either...", per the spec.
4. `am_owner` got no hide_code change needed (already True). `am_settings` untouched.
