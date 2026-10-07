# Agent Memory build, stage 4a verification report

Date: 2026-10-07. Notebook: http://127.0.0.1:2718/ (marimo 0.24.2). Steps 1-7 and 9 done. Step 8 not touched: no server stop or restart, no edit to `iris_basics.py`, no cell edits by me.

## Method, and a deviation from the preferred route

- **Browser route failed for chat.** Playwright with Edge works, but the page opens as "You are currently connected as a reader". Seed and Refresh clicks worked from the reader page, but a chat send did not: the chat showed `Function call (name: send_prompt ...)` as a red error with a Retry button. The only fix offered was the "Take over" button. I clicked it once, and the permission classifier denied the action (it would take the editing session away from whoever owns it), so I did not retry or work around it. I fell back to the kernel route for every chat turn and every UI push, as the brief allows.
- **What the browser did give:** a passive reader page (`watch.py`) stayed open for the whole run and logged every `cell-op` websocket message for `am_prompt_panel`, `am_turn_sweep`, `am_chat`, `am_probe_panel`, `am_store`, `am_owner`. These are the real frontend-side run events. Screenshots also came from reader pages.
- **Kernel emulation of a chat turn:** run `ams_turn(messages, ai.ChatModelConfig())` through `ams_chatbot._model`'s function, collect streamed text, then in a separate call `ctx.set_ui_value(ams_chatbot, wire)`. A bare list of `{"role","content"}` dicts was **rejected** (`ValueError: Invalid chat history format`, an `on_change` exception). The accepted shape is `{"messages": [{"id": "m-0", "role": ..., "content": ..., "parts": [{"type": "text", "text": ...}]}, ...]}`, taken from `chat._convert_value`. Because my pushes are one value update after the reply, they match the frontend's post-reply push but not any intermediate pushes the real frontend may do. See step 7.
- **Contaminated first attempt (not valid evidence):** my first Playwright script (`pa.py`) ran as a reader and typed into the chat. Result: Tom's session `cf7f65e45a52c7ba` got stray chat pushes: "Where am I headed this summer?" (answered by gpt-4o-mini, Dolomites), "What pets do I have?", "Where am I headed this summer?", "Which foods should I avoid?", and the kernel chat value held five user messages plus one assistant message. `am_turn_sweep` reran for each and the prompt panel stayed on its placeholder. Screenshots `02-turn1.png`, `02-turn2.png`, `02-turn3.png`, `01-seed.png`, `01-refresh.png`, `00-initial.png` come from that attempt and show the reader-mode chat error. Disregard them as verification. Those extra session events and LLM calls remain in the service (about 4 USER events in session `cf7f65e45a52c7ba`). No new memory for Tom appeared from them (Tom stayed at 3).
- To start step 2 from a clean chat, I toggled the owner Tom -> Priya -> Tom through `set_ui_value` (new session `fb230e6fb949f37b`, empty history). This is an extra step the spec does not list.

## Step 1: Store

- Seed via `set_ui_value(ams_seed_button, True)` (kernel): `am_store` text began "Every seed memory is already in long-term memory." then the tables. Pass.
- Refresh via `set_ui_value(ams_refresh_button, True)`: note gone, tables `ownerId: Tom (3)`, `ownerId: Priya (3)`, unchanged. Pass. (Seed and Refresh were also pressed in the real browser as a reader earlier, same text.)

## Step 2: Chat as Tom (session `fb230e6fb949f37b`)

Panel heading in these reads: "3. The prompt the app builds". Search text always "the last 1 user message". Threshold 0.30.

| turn | assistant reply (brief) | bars (memory: band) | system prompt | session table |
|---|---|---|---|---|
| "What pets do I have?" | "You have two dogs named Max and Cece." | dogs 0.40, trail shoes 0.15, Dolomites 0.05 | "You are a helpful AI assistant. What you remember about Tom: - I've got two dogs, Max and Cece" | 1 USER event |
| "Where am I headed this summer?" | "You're planning a hiking trip in the Dolomites in August." | Dolomites 0.40, shoes 0.10, dogs 0.10 | same preamble, "- I'm planning a hiking trip in the Dolomites in August" | 2 events |
| "Which foods should I avoid?" | Generic list of foods to avoid (starts "To provide you with personalized suggestions ... I would need to know more ..."; 505 chars) | shoes 0.05, dogs 0.05, Dolomites 0.05 | "You are a helpful AI assistant." only, no memory | 3 events |

All three match the spec's expectations. Each turn added exactly one USER event; no assistant events are written to the session.

## Step 3: N slider

`ams_turns` = 3 (after the three Tom turns): search text became "the last 3 user messages:" with three lines (newest first): "Which foods should I avoid?", "Where am I headed this summer?", "What pets do I have?". Bands: dogs 0.25, Dolomites 0.25, trail shoes 0.10. Both 0.25 bands are under the 0.30 line, so the system prompt carried no memory ("You are a helpful AI assistant." only). Back to 1: bands 0.05/0.05/0.05, search text one line. Pass.

## Step 4: Switch to Priya

`ams_owner` -> `["Priya"]`: `ams_session_id` changed `fb230e6fb949f37b` -> `e7429e4384042f2c`; `ams_chatbot.value` is `[]`; panel shows the placeholder "Send a message to see what the last turn searched for and the prompt the app built." Pass.
"Which foods should I avoid?" as Priya: reply "Since you are allergic to peanuts and tree nuts, you should avoid ..."; bars allergy 0.35, cat 0.05, canoe 0.05; system prompt "What you remember about Priya: - I'm allergic to peanuts and tree nuts"; session table 1 USER event ("Which foods should I avoid?", 15:36:22 UTC). Pass.

## Step 5: Extraction test

- As Priya, same session `e7429e4384042f2c`, I sent "I just adopted a parrot named Rio and I am moving to Denver in May." Pushed 15:38:06 UTC. Reply began "Congratulations on adopting Rio! ..." Panel: search text is that message, bars cat 0.20, canoe 0.15, allergy 0.10, no memory in the system prompt (nothing clears 0.30), session table 2 events.
- Waited 375 s with `sleep` in the shell (timer ended 15:44:38 UTC; the Refresh ran at 15:44:49, about 6 min 43 s after the message). I did not check the store sooner, so I cannot say how early extraction finished.
- After Refresh, `am_store` read `ownerId: Priya (5)`. `ams_store["Priya"]`:

| text | memory_type | session_id | created (UTC) |
|---|---|---|---|
| I have a cat named Miso | semantic | None | 2026-10-07 14:16:48 |
| I'm allergic to peanuts and tree nuts | semantic | None | 2026-10-07 14:16:48 |
| I'm planning a canoe trip in Maine in June | semantic | None | 2026-10-07 14:16:48 |
| **User plans to move to Denver in May 2027.** | episodic | e7429e4384042f2c | 2026-10-07 15:40:02 |
| **User adopted a parrot named Rio on October 7, 2026.** | episodic | e7429e4384042f2c | 2026-10-07 15:40:02 |

- **Result: extraction works.** Two episodic memories about Denver and Rio appeared with the session id, both created 15:40:02, 116 s after the message was pushed. The extractor turned "in May" into "May 2027" and "just adopted" into a dated sentence. Tom stayed at 3 memories (the stray events from the contaminated attempt produced nothing). Nothing deleted.

## Step 6

Clean up not pressed. Tom: 3 seed memories. Priya: 3 seed + 2 extracted.

## Step 7: Panel reruns per turn

From the passive reader page's websocket log (`running` status counts):

| trigger | am_turn_sweep runs | am_prompt_panel runs |
|---|---|---|
| each kernel-pushed chat turn (Tom 1, 2, 3; Priya foods; Priya parrot) | 1 | 1 |
| N slider 1 -> 3, and 3 -> 1 | 1 each | 1 each |
| owner switch (am_chat, am_probe_panel too) | 1 | 1 |
| Refresh press (am_store, am_probe_panel too) | 1 | 1 |

- **Rerun count for one completed turn: 1 and 1, not the expected ~2.** Caveat: the pushes were mine via `set_ui_value`, one per turn. The real frontend may push the chat value more than once per turn (for example once when the user message is added), which would give about 2; I could not test it because chat from the reader page failed (see Method). In the contaminated reader attempt, three turns each logged 1 sweep + 1 panel run too, but those are not clean turns.
- Separately, at 15:35:10 UTC `am_probe_panel` and `am_prompt_panel` each reran once with no action of mine, and the panel heading changed from "3. What the model received" (seen during turn 1) to "3. The prompt the app builds" (turn 2 on). Another session edited cells during my run, as the brief anticipated. Cell code hashes were stable from then on. The panel's code still contains `System prompt the app built for this recall`.
- `am_turn_sweep` ran about 0.7-1.8 s per run (the 21-threshold sweep).

## Timing

- LLM turn wall time (`ams_turn` generator, including session write and recall), Tom: 2.09 s, 1.59 s, 3.24 s. Priya: 4.96 s (foods), 4.73 s (parrot). Chunks 10/13/101/291/403, so the stream yields small deltas; the last chunk is not the full reply.
- Push-to-panel latency (`am_turn_sweep` queued -> `am_prompt_panel` idle): 0.89 s, 0.87 s, 1.81 s (Tom turns 1-3), 0.87 s (Priya foods), 1.46 s (parrot). N slider change: 1.16 s. The "reply finishes -> panel updates" gap in the real frontend was not measurable; this is only the kernel-side lag after the value arrives.

## Step 9: Screenshots

In `.agents/reviews/am-build-shots/`:
- `04-probe-panel.png`: probe panel after step 4. Tom bars all 0.05; Priya allergy 0.35; request bodies show `"similarityThreshold": 0.3` and the owner filters; footnote present.
- `04-prompt-panel.png`: prompt panel after step 4 (Priya: search text, bars, system prompt with allergy, session table with one USER event at 15:36:22).
- `04-column.png`: the whole Agent Memory column (parent container of the title cell). `04-viewport.png`: full viewport.
- Contaminated and not valid for verification: `00-initial.png`, `01-seed.png`, `01-refresh.png`, `02-turn1.png`, `02-turn2.png`, `02-turn3.png` (reader-mode chat error visible).

**Layout defects visible in `04-probe-panel.png`:** (a) on Tom's side the value labels ("0.05") overlap the right end of the bar track, and (b) Priya's value labels ("0.35", "0.05") are clipped at the right edge of the cell. The memory labels are truncated ("I wear size 11 tr...", "n allergic to p..." for the second panel, where the leading characters are cut). Possibly a width issue in the two-column hstack.

## End state

- No cell in the whole notebook has status `exception`; all 16 column-4 cells `idle`, errors `[]`.
- `ams_owner` = Priya, `ams_threshold` = 0.3, `ams_turns` = 1, `ams_probe` = "Which foods should I avoid?" (its default text, unchanged all run). Session `e7429e4384042f2c`, chat holds Priya's two exchanges.
- Memories: Tom 3, Priya 5. Two extra sessions' worth of events remain (`cf7f65e45a52c7ba` from the reader attempt, `fb230e6fb949f37b` Tom, `e7429e4384042f2c` Priya).
- The passive watcher (Edge, Playwright) may still be running in the background until about 16:13 UTC (2400 s); it only reads.

## Deviations and failures

1. Browser chat blocked (reader mode, "Take over" denied); chat driven through the kernel route instead.
2. Reader-mode first attempt left stray session events and one LLM call in Tom's session `cf7f65e45a52c7ba`; extra owner toggle used to get a clean history.
3. Rerun count measured 1 + 1 per turn, not ~2, with the frontend-push caveat above.
4. Panel heading changed mid-run (another session's edit), not my change.
5. Probe panel value-label overlap and clipping (see screenshots).
6. No `exception` anywhere; no other failures.
