# Agent Memory column redesign: teaching critique (2026-10-07)

Reviewed: `.agents/plans/agent-memory-column-redesign.md` against the live kernel on :2718 (column 4, `am_title` through `am_amnesia`, 15 cells), the disk copy `iris_basics.py` (`column=4` at line 178), `.agents/VOICE.md`, the two standing rulings (rule-of-three, "you already X"), Nick's three demo criteria, and marimo's best-practices page. Nothing in the notebook was changed.

Findings are ranked by how much they change the design. Letters in brackets map to the questions asked.

## 1. The owner dropdown inside the chat cell breaks the owner switch [A, B]

The proposal moves `ams_owner` into `am_chat` and comments the session line "this cell reruns on a customer switch, so each customer gets a new session". By the mechanism the proposal itself cites (`runtime.py:1942`, the defining cell does not rerun on a UI change), that comment is false. Today the dropdown lives in `am_controls` (`iris_basics.py:241`), so a switch reruns `am_chat`, builds a fresh closure with a new `session_id`, and clears the history. `am_try_it` says so: "Switching customers starts a new conversation."

Under the proposal a switch does three wrong things:

- The session id stays. Priya's messages are written to Tom's session, and panel 3's `get_session_memory_async` read-back shows one session with mixed turns.
- The chat history stays. When the reader asks "What pets do I have?" as Priya, the model has Tom's "two dogs" answer in `history`. The model can repeat it from history, and the reader sees the owner filter apparently fail. That is goal 2's demonstration, broken by the layout chosen to prove it.
- `test_chat_cells_own_their_controls` passes, because the owner is now a local control. The test encodes "no foreign control", but the owner is the one control whose change must wipe history.

Alternative: keep `ams_owner` in a cell above the chat (its own one-line cell, "Chat as", or beside the probe in finding 2), let the switch rerun `am_chat` on purpose, and keep the sentence "Switching customers starts a new conversation." Change the test to allow exactly one foreign control per chat cell, named `*_owner`, rather than bending the design to the test. Do not key sessions by owner inside the chat cell; that fixes the session and not the history.

## 2. Chat first makes every panel a placeholder until the reader pays for a model call [B, G]

Reader's path under the proposal: Seed, read two tables, scroll past a code cell, reach the chat, send a message (one LLM call), then scroll down through panel 1, a 50-line code cell, panel 2, panel 3. Until that message is sent, panels 1, 2, and 3 are three one-line placeholders with code cells between them. A first-time reader sees a page of "send a message to see" and the only knob that teaches the threshold is unreachable without a key for `LLM_API_KEY`.

Goals 1 and 2 are about the search API. The model adds nothing to them and can subtract from them (finding 1, and any answer the model gives from its own knowledge). Only goal 3 needs a model. Under Nick's second criterion, the sweep is the one measured series in the column and the chat is a single-shot command with a read-back; the proposal puts the read-back in front of the series.

Alternative, probe first:

| # | Cell | Shows | What the reader sees |
|---|---|---|---|
| 1 | `am_title` | prose | as finding 9 |
| 2 | `am_client` | code | unchanged |
| 3 | `am_store_md`, `am_store_button`, `am_store`, `am_store_fns` | as proposed | two owner tables |
| 4 | `am_probe` | hidden | "## Search long-term memory", one `mo.ui.text` with the first starter prompt as its default |
| 5 | `am_probe_panel` | hidden | bars for Tom and for Priya side by side, same text, threshold line from the chat's slider, request body under each. This is panels 1 and 2 in one view and it renders on load with no model call. Defines `ams_bands` |
| 6 | `am_recall_fns` | code | `ams_recall`, `ams_sweep` only |
| 7 | `am_chat_md` | hidden | "## Chat as Tom or Priya", one sentence, the owner dropdown (finding 1) |
| 8 | `am_chat` | code | `ams_threshold` slider rendered above the chat box, then `ams_chatbot`. The slider sits visually between the bars and the chat, and both read it |
| 9 | `am_prompt_panel` | hidden | the events the service stored for this session, the recall set for the last chat query at the slider's value (from the cached sweep, no new search), the system prompt as built |
| 10 | `am_prompt_fns` | code | `ams_query`, `ams_remember`, `ams_build_system`. `ams_turn` is already visible in cell 8 |

Thirteen cells counting the store group, two panels, one slider, one placeholder (cell 9 before the first turn). The chat-history constraint is respected: the slider is defined in `am_chat`, the probe panel only references it, so a slider move redraws the bars and leaves history alone. The probe's text box is in its own cell and the chat never reads it. A customer with Agent Memory credentials and no LLM key learns goals 1 and 2 before the chat exists.

## 3. The 0 to 0.6 cap hard-codes today's store into the instrument [E, C]

The sweep stops at 0.60 and the slider shrinks to 0.60 because "nothing in this store clears 0.55". That is a measurement of six seed sentences against one embedding model, written into the lesson's axis. Two failure modes:

- A customer who seeds their own text, or whose store has a different model, gets memories that clear 0.65 and the panel draws them as full bars with no way to tell them apart.
- The 0 to 1 scale is itself the lesson for anyone arriving from LangCache, where thresholds like 0.65 to 0.85 are normal. Setting 0.8 here by habit and getting nothing back is the thing a customer needs to see once. A slider that stops at 0.6 hides that failure.

The proposal's own cost figures argue against the cap: 13 concurrent searches cost about two searches of latency. 21 concurrent searches (0.00 to 1.00 at 0.05) cost about the same wall time, and `mo.cache` makes a repeat free.

Alternative: sweep 0.00 to 1.00 at 0.05, slider 0.0 to 1.0, axis 0 to 1. Keep the default at 0.3. The empty right half of the chart is not wasted space, it is the point. If the title no longer says "cosine similarity", it does not need to; "similarity" plus the 0 to 1 axis carries it. Verify the API accepts `similarityThreshold: 1.0` before building.

## 4. The bars read as a score, and the API returns none [C]

A horizontal bar with a number at its end is a score to any reader. Each bar here is a lower bound quantized to 0.05: the memory's similarity is somewhere in [band, band + 0.05). Ties at the same band are common (Tom's three at 0.05 for the foods question), and the order inside a tie means nothing. Nothing in the proposal's panel text says any of this.

Alternative wording, exact:

- Panel heading: `### 1. Which memories clear the threshold`
- Axis label: `Highest similarityThreshold the memory still clears`
- Bar end label: the band value, two decimals (`0.35`), never a word like "score" or "similarity".
- Threshold line label: `slider: 0.30`
- Footnote under the bars, four sentences: "The search API returns no similarity score. The notebook ran the same search at 21 thresholds, 0.00 to 1.00 in steps of 0.05. Each bar ends at the highest one that still returned the memory, so its similarity lies between that value and the next step up. Memories at or above the line come back at the slider's setting."
- Sort bars by band descending, then by store order. Render tied bars with the same length and no rank cue.

## 5. The title claims extraction and nothing on screen shows it [F, open question 3]

Sentence two says long-term memory is "written by the app or extracted from session events". The wire table has the row "session memory -> long-term memory, background extraction, when enabled on the store". The Refresh button exists only for this. No panel, prose, or starter prompt exercises it, and the proposal's open question 3 asks whether to drop Refresh.

Extraction is the thing that separates Agent Memory from a vector index with an `ownerId` field, and it is the most valuable thing the column could show a customer. The proposal leaves it as a claim.

Alternative, one of two:

- If extraction is enabled on Nick's store: add a fourth starter prompt that states a new fact ("I just adopted a parrot named Rio"), keep Refresh, and write one sentence under the store tables: "Tell the agent a new fact, wait a minute, then select Refresh. Extracted memories show their type." Then goal 3's panel has something to point at in the events it already shows.
- If extraction is not enabled: cut "or extracted from session events" from the title, cut the Refresh button, cut the wire-table row, and cut the sentence under the tables. Do not keep a button whose effect the demo cannot produce.

## 6. The "last N user messages" slider is a swept parameter with no measurement [F, cut candidate]

Moving N changes the search text, which changes the `(query, owner)` cache key, which triggers a fresh 13-call sweep and moves every bar. The reader moved a slider labelled "messages" and watched the threshold chart change for a reason that is only visible in the request body. With the three starter prompts on three unrelated topics, N above 1 blends pets, travel, and food into one query and lowers every band, which looks like the threshold lesson failing.

Alternative: fix N at 1 in `ams_query` and show the search text in the prompt panel. If the query-construction lesson matters, it needs its own measured effect (bands at N=1 against bands at N=3 for the same turn), not a knob with no readout. That is a second column's worth of lesson, not a slider.

## 7. Code cells between panels, and rendering helpers presented as lesson code [A]

The layout puts `am_recall_fns` (four functions) between panel 1 and panel 2, so the reader scrolls about 50 lines of code to get from one half of the goal-1-and-2 story to the other. Two of those functions are not lessons: `ams_bars` is 15 lines of inline CSS and `ams_memory_table` is a table wrapper. The proposal's rule "code under the panel it drives" is right for API-touching functions and wrong for HTML helpers. `ams_query` is prompt construction (goal 3) and sits in the recall cell (goal 1).

Also mixed: `am_chat` carries four controls, a session id, the turn function, and layout. That is acceptable because the turn function is the lesson, but the three controls above it dilute the eleven lines the reader is meant to read. Finding 2 reduces it to one slider plus the chat.

Alternative: visible cells carry only functions that call `ams_client` or build the prompt. `ams_bars` and `ams_memory_table` go in one `hide_code=True` cell at the bottom of the column, named `am_render`. `ams_query` moves to `am_prompt_fns`. With finding 2, panels 1 and 2 merge, so no code cell separates them.

## 8. Three layers say the same thing: goal sentence, panel heading, lead-in [A, D]

Each goal in the title ends with "Panel N shows ...". Each panel opens with a heading that restates the goal title ("### 1. Long-term memory recalls by meaning"). Each panel then has a one-sentence lead-in. The proposal argues the headings replace VOICE.md's colon lead-ins; they do, so the lead-ins are the redundant layer, and the "Panel N shows" sentences are a fourth restatement with the same shape three times in a row (VOICE.md "Robotic rhythm", and the standing ruling on triplets).

Alternative: the panel heading carries the number and names what is on screen, not the principle ("### 1. Which memories clear the threshold", "### 2. The same search as the other customer", "### 3. What the model received"). The title's goal sentences point at the panel in varied shapes (finding 9). No lead-in sentence under a heading unless it says something the heading and the panel do not.

Panels the reader will not understand without the code: "the history shape" in panel 3 means nothing to a customer. Show the `history` list as the JSON the app sent, labelled "messages sent to the model", or drop it. In panel 2, "the same search text at the same threshold for every owner" needs a visible recall set per owner, not a count; under finding 2 it is the second set of bars.

## 9. Title: sentences that fail, with rewrites [D]

Checked every sentence for length under 25 words after inline code is stripped, the slop floor, staged reveals, rhetorical triplets, and "you already". Seven fail or are at risk.

| Sentence (before) | Why | After |
|---|---|---|
| "Redis Agent Memory keeps each conversation in session memory and durable facts in long-term memory, written by the app or extracted from session events." | 24 words, at the ceiling; "extracted from session events" is unproven on screen (finding 5) | "Redis Agent Memory keeps each conversation in session memory and durable facts about the customer in long-term memory." |
| "The three panels under the chat take the last turn apart, one principle each:" | "take apart" is an idiom (VOICE.md: no idioms or quips); "three panels" repeats the rule-of-three cue | "The numbered panels under the chat show what the last turn did:" |
| Goal 1: "... shares no word with "allergic to peanuts and tree nuts" and still finds it." | the question does not find anything, the search does; "still" is doing a staged-reveal job | "... shares no word with "allergic to peanuts and tree nuts", and the search returns it as Priya." |
| Goal 1: "The slider sets `similarityThreshold`, the score a memory must reach to come back." | calls it a score, then the panel footnote says the API returns no score (finding 4) | "Panel 1 shows the highest `similarityThreshold` each memory clears, so raising the slider drops the weakest matches first." (replaces both "Panel 1 shows" and this sentence) |
| Goal 2: "Panel 2 runs the last search for both customers." | third "Panel N verb" sentence in a row | "In panel 2 the same search runs as Tom and as Priya, and only Priya gets the allergy back." |
| Goal 3: "Each turn writes the message to session memory, searches with the last N user messages, and puts the hits in the system prompt." | 23 words in a verb triplet; it is literal, and it still reads as the cadence Nick has ruled slop | "Each turn writes the message to session memory and searches long-term memory with it. The hits go into the system prompt, so the model sees a memory only because the app put it there." |
| Goal 3: "Panel 3 shows the events the service stored and the prompt the model received." | pointer shape repeated | "The events the service stored and the prompt the model received are in panel 3." |

Sentences that pass unchanged: the opening dogs sentence (16 words), "Seed memories for two made-up customers, Tom and Priya, then chat as either." (13), goal 2's filter sentence, the status and Settings lines, the Docs line.

Also: goal 2's title changes "one user" to "one customer". The code says `user_id`, the proposal's `ams_sweep` says `owner`, the prose says customer. One term per concept (VOICE.md "Synonym cycling"): use `owner` in code and "customer" in prose, and rename `user_id` to `owner` in `ams_recall`, `ams_remember`, and `ams_build_system`.

## 10. `mo.cache` on the sweep goes stale after Seed and Clean up, and mid-stream pushes re-read the session [A]

`ams_sweep` is keyed on `(query, owner)`. After Clean up the store is empty, the bands cache still holds the old memories, and panel 1 draws rows from `ams_store` (fresh) against bands from the cache (stale). Today that happens to work because the row list is empty. After a re-seed with changed text, or after background extraction adds a memory, the cached bands miss it until the kernel restarts. The proposal relies on `ams_store` to make the panels rerun after Seed, which they do, with stale data.

Panel 3 calls `get_session_memory_async` on every `chatbot.value` push, and the proposal says the push count during streaming is unverified.

Alternative: include a store fingerprint in the cache key, `ams_sweep(query, owner, store_key)` where `store_key = tuple(sorted(m.id for m in ams_store[owner]))`. The lookup hits when nothing changed and misses correctly when it did. For panel 3, gate the session read on the last user message text changing, not on every push: a small `@mo.cache` function `ams_session_events(session_id, last_user_text)`. Count panel runs across one streamed reply during the build, as the proposal already plans.

## 11. The wire table should go, not collapse [open question 2]

Every panel under the proposal shows its own request body. The `/// details` block then holds six rows that each duplicate a panel, and the reader who opens it sees the same `POST /long-term-memory/search` they just read above. The other three columns keep their wire tables because their panels do not show request bodies. Drop it here. If Nick wants consistency across columns, keep the row for the one request no panel shows, which under finding 5 is extraction or nothing.

## 12. Test fallout the proposal does not list

- `test_mo_stop_gates_on_a_button_or_a_form_only`: the current `am_trace` (`iris_basics.py:397`) fails it today; the proposal's `if`/`else` placeholders fix it. Worth saying, since it is the one test the design repairs.
- `test_intro_table_quotes_each_columns_goal_titles`: the working copy has no `intro` cell, so the test fails regardless, but the goal 2 title change ("one user" to "one customer") will need the intro row when it comes back.
- `test_chat_cells_own_their_controls`: see finding 1. The rule needs an owner exception or the design breaks the lesson.
- `mo.cache` is not in `FORBIDDEN_CALLS` (`test_safety.py:33` bans only `mo.state`), so the memoized sweep is allowed.

## 13. Scores against Nick's three criteria [G]

| Criterion | Proposal as written | With findings 1 to 3 applied | Justification |
|---|---|---|---|
| Hand to a customer as a notebook | 3 | 4 | As written, switching customers mid-chat mixes sessions and history (finding 1), and a 0.6 cap breaks on a customer's own seeds (finding 3). Fixed, it is four env vars and a key with nothing to install. |
| Simulations that teach components and architecture | 3 | 4 | The sweep is the one measured series and it is behind a model call; N is swept with no readout; latency is measured in the plan and shown nowhere. Probe first puts the series on screen on load. One point short of five because nothing measures the model-side effect, such as prompt tokens added by recall. |
| Near-zero customer setup | 4 | 5 | No new dependency, no anywidget, `/// details` and inline HTML only. As written a customer with Agent Memory credentials and no LLM key sees nothing but placeholders; probe first removes that gate. |
