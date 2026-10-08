# Agent Memory polish: review (2026-10-07)

**Verdict: PASS WITH NOTES.** All 19 rewritten cells match the spec byte for byte, the probes behave as specified, the store is clean, and the controls are restored. The notes are one misleading README sentence, README text outside the spec's edit list that now contradicts the notebook, and two sentences over the voice limit.

## Findings, most severe first

1. **README Step 7 misdescribes the second customer's bars** (`README.md:277`). It says the other customer's bars show "the same search without the `ownerId` filter's effect". The workbench labels them "ownerId Priya, the same search for comparison" and computes them with the other customer's own filter. The spec text was used verbatim, so this is a spec flaw, not an implementer slip.
   Fix: "The second customer's bars show the same search run against that customer's memories, for comparison, and the agent never sends it."
2. **README keeps stale statements about Claude and the old notebook.** The grep the spec expects to be empty finds `README.md:386` ("Dana is allergic...", in How it works). `ANTHROPIC_API_KEY`, `ant auth`, and "two session events" are gone. Other lines contradict the new notebook: line 32 ("one client per service, Claude included"), 330-340 (`ask_llm`, `claude-opus-5-5`), 442 ("Claude receives it on later turns"), 492 (FAQ says the chats call Claude through `ask_llm`), and 530-568 (the "Verified live" section). The docs implementer reported this and the spec scoped it out.
   Fix: one more docs pass over How it works, the FAQ, and Verified live, or a "describes the earlier notebook" banner.
3. **Two column-4 sentences are over 25 words with code stripped.** `am_title` principle 1, "Which foods should I avoid?" sentence, 28 words. `am_machinery_md` "Four hidden cells follow: ...", 26 words. Both are verbatim from the spec.
   Fix: split principle 1 after the quoted pair. In `am_machinery_md`, trim to "Four hidden cells follow: slider state, chat object, threshold sweep, and renderers for the bars and tables."
4. **`cr_title` (Context Retriever) is `stale`.** It is outside this task and probably stale because `mo` reran. Run it once so the column does not show a stale marker.
5. **Reports.** The code report says the unnamed `grep` for `USER_ID:` found nothing but did not grep `USER_ID` alone. That grep is empty now. The code report says "eleven edits" against the spec's "twelve", and the prose report says nine cells against "eight". Both counts are right, and the spec was miscounted. Nothing else disagrees with what I read.

## Evidence per check

1. **Code equals spec: PASS.** I extracted the fences by section heading and stripped trailing whitespace per line and the trailing newline. All 19 cells match with zero differences: `_`, `redis_client`, `llm_client`, `am_connect`, `am_store_fns`, `am_prompt_fns`, `am_turn_fn`, `am_settings`, `am_chat`, `am_turn_sweep`, `am_render`, `am_store`, `am_next_md`, `am_title`, `am_store_button`, `am_owner`, `am_workbench`, `am_build_md`, `am_machinery_md`. `rs_title` and `lc_title` have no `ANTHROPIC`, and `LLM_API_KEY` appears once in each.
2. **Disk mirrors kernel: PASS.** Every non-blank live line of the 21 rewritten cells (the 19 above plus `rs_title` and `lc_title`) is on disk with four spaces prepended. `itertools`, `secrets`, `health_async`, `USER_ID`, `ANTHROPIC_API_KEY`, `str(uuid.uuid4())`, and `sorted(bands` all give no hits. On-disk order of the named `am_*` defs has `am_next_md` after `am_turn_fn`.
3. **Health: PASS.** 22 cells in the kernel. `am_*`, `rs_title`, `lc_title`, `redis_client`, `llm_client`, and `_` are all `idle` with no errors. `ctx.graph.cycles == set()`. The only non-idle cell is `cr_title` (finding 4).
4. **Store: PASS.** Tom has `tom-seed-1..3` and Priya has `priya-seed-1..3`, all `semantic`. Both owners have no sessions.
5. **Threshold probe: PASS.** I set 0.45 and read in the next call. Session id, chat id, and turn id were unchanged. The line was at `left:45%`, all six bars were `ams-bar-below`, and the request JSON showed 0.45. The system prompt had no memories (Tom's foods question recalls nothing). Restored to 0.3 and confirmed: line at 30%, Priya's allergy dark again.
6. **N probe: PASS.** I set N to 2. Session id, chat id, and turn id were unchanged, and the bars were identical to the baseline. Restored to 1 and confirmed.
7. **Owner probe: PASS.** The switch to Priya gave session `priya-427e59c4717f`, and the chat and turn ids changed. Labels read "ownerId Priya, what the" before "ownerId Tom, the same search for comparison", and the system prompt carried "I'm allergic to peanuts and tree nuts". No `ReadTimeout` appeared. The restore to Tom swapped the labels back and gave a new session (`tom-cb6854794e6a`).
8. **Model-failure path: PASS.** A direct `ams_turn` returned "The model call failed (AuthenticationError). The session write and the search still ran." The session had 1 event. I deleted it, and both owners show no sessions afterwards.
9. **Empty store: PASS.** `ams_bars({}, 0.3)` has no `ams-ticks` row and contains "No memories yet. Select Seed to write them."
10. **Voice: PASS WITH NOTES.** `tests/test_voice.py`: 8 passed. A scan of the column-4 `mo.md` strings found no semicolons outside the CSS and two over-length sentences (finding 3). I eyeballed serial commas in the strings and found none missing. The workbench output contains all eight strings the spec lists, with `data-html` decoded and tags stripped.
11. **README and `.env.example`: PASS WITH NOTES.** The grep finds only the Dana line (finding 2). `.env.example` is clean. I read Step 7 against the live column. The seed and rewrite behaviour, the three-call turn, session switching, the extraction note, and the Step 8 bullet all match the notebook. The exception is finding 1.
12. **Reports: PASS.** See finding 5.

## Final state

- Owner: Tom
- Threshold: 0.3
- N: 1
- Session id: `tom-cb6854794e6a`. I deleted this session in check 8, so the store has no copy and the workbench shows "no messages written yet".
- Store counts per owner: Tom 3, Priya 3 (the six seeds)
- Sessions per owner: Tom 0, Priya 0

Scratch scripts are `am_polish_review_*.py` in `AppData\Local\Temp`. No cell was edited, and the only store write was the check 8 session event, which I deleted.
