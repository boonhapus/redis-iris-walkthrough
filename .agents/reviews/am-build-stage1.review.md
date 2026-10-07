# Stage 1 review (2026-10-07)

**Verdict: PASS WITH NOTES**

No blockers. All seven stage 1 cells match the spec. One prose sentence breaks the voice rules, and three smaller items could bite later stages. Reviewed read-only against the live kernel at http://127.0.0.1:2718/. A stage 2 builder was editing other cells while I worked, so I ignored them.

## Findings, most severe first

### 1. `am_store_md` has a semicolon in prose (voice failure, spec text itself is at fault)

Live and spec are identical, so the builder is not at fault. The spec prose breaks the house rule of no semicolons:

> Chat sessions are not deleted; the store expires them after an hour by default.

Fix, in the cell and in the spec:

> Chat sessions are not deleted. The store expires them after an hour by default.

### 2. `am_store_md` asserts two facts the build never checked (unverified, not wrong)

- "the store expires them after an hour by default": no evidence in the spec, the builder report, or the SDK. Check it in the Agent Memory docs on the day, or cut it. If unverifiable, replace the sentence with "Chat sessions are not deleted."
- `POST /long-term-memory` is shorthand. The SDK path is `/v1/stores/{storeId}/long-term-memory` (from the source of `bulk_create_long_term_memories_async`). The shorthand is defensible for a demo, but it is not the literal route.

### 3. Term drift: "Seed" in prose, "Seed memories" on the button

`am_store_md` says "Seed writes three memories per customer" and the button label is `Seed memories`. The sibling sentences say "Clean up" and "Refresh", which match their labels exactly. Fix: write "**Seed memories** writes three memories per customer ..." or rename the button to `Seed`. Related smaller drift: the chat prompts are "starter questions" in `am_owner`, "starter prompts" in spec stage 4, and `prompts=` in code. Pick one term before stage 3's `am_title` lands.

### 4. Eventual-consistency risk in `am_store` after Seed or Clean up (cannot verify without writes)

`am_store` lists memories immediately after `ams_seed()` and `ams_forget()`. If the search index lags the write, the note says "Wrote 6 new memories" while the tables show fewer, or show deleted rows. Stage 4 step 1 presses Seed only when everything is already stored, so it will not catch this. If Nick presses Clean up then Seed on stage, watch for it. Mitigation if it shows up: the Refresh button already exists as the recovery path, and the prose says so only for extraction. No change needed now.

### 5. `ams_bars` compares `round(band, 2) >= threshold` with an unrounded `threshold`

I confirmed `threshold=0.3` and `0.35` classify correctly. The spec's callers always pass `round(ams_threshold.value, 2)`, so this is safe as specced. If a later stage passes a raw slider value (for example `0.1 + 0.2`), a bar at 0.30 would render as below the line. Hardening, not required: `state = ... if round(band, 2) >= round(threshold, 2)`. Spec deviation if applied, so only do it with Nick's sign-off.

### 6. The builder's deviation 1 explanation is unconfirmed and now moot

The report blames `mo.stop` for `am_trace` reporting `exception` with an empty error list. That was never confirmed. `am_trace` no longer exists in the kernel (stage 2 replaced it) and `am_prompt_panel` is `idle` with no errors, so nothing further is needed for stage 1. Treat the `mo.stop` theory as unproven.

## Checks, with evidence

### 1. Live code vs spec

I extracted every stage 1 code block from the spec and compared it to the live cell code, stripped of leading and trailing whitespace:

```
_ IDENTICAL            (Hbol)
am_store_fns IDENTICAL
am_store_button IDENTICAL
am_store IDENTICAL
am_owner IDENTICAL
am_render IDENTICAL
am_store_md: same text, checked by eye (spec block parses as prose, not code)
```

Zero differences in names, signatures, logic, or prose. The `am_store_md` text read from the kernel matches the spec character for character.

### 2. Flags, names, leftovers

| Cell | id | `hide_code` live | Spec | Match |
|---|---|---|---|---|
| `am_store_md` | IMKF | True | True | yes |
| `am_store_button` | Kclp | True | True | yes |
| `am_store` | pDtD | True | True | yes |
| `am_store_fns` | emfo | False | False | yes |
| `am_owner` | Shga | True | True | yes |
| `am_render` | HaRf | True | True | yes |
| `am_connect` | SFPL | False | False | yes |
| `am_title` | Xref | True | True | yes |

- `am_seed_fn` is gone from the cell list.
- Defs scan over all cells: `AMS_SEEDS`, `amnesia`, `get_memories_version`, `set_memories_version` are defined nowhere. `ams_seed`, `ams_seeds`, `ams_memories`, `ams_forget`, `ams_store`, `ams_owner`, `ams_bars`, `ams_memory_table` each have exactly one owner (`multi-def: {}`).
- `Hbol` has `html` and `json` in the spec order.

### 3. Status and output

`am_store_md`, `am_store_button`, `am_store`, `am_store_fns`, `am_owner`, `am_render`, `Hbol`: all `idle`, `errors=[]`. No stage 1 cell is `exception`. `am_store` output contains two side-by-side flex tables and the extraction sentence. `ams_store` sizes: `{'Tom': 3, 'Priya': 3}`.

### 4. Behaviour without writes

`ams_bars({"a<b & c": 0.35, "low": 0.05, "mid": 0.30}, 0.3)`:

- Row order is band descending: `a&lt;b`, `mid`, `low` at string offsets 73, 318, 541.
- Classes and widths: `ams-bar` 35%, `ams-bar` 30%, `ams-bar ams-bar-below` 5%. The 0.30 bar sits on the line and is not below, as specced.
- Line: `style="left:30%"` on every row.
- Caption: `Line: slider at 0.30.`
- HTML escaping: the label renders `a&lt;b &amp; c` in both the text and the `title` attribute. No raw `<` in a label.
- `ams_bars({}, 0.3)` contains `No memories for this owner.`
- Threshold 0.35 with bands 0.35 and 0.30: first is `ams-bar`, second is `ams-bar-below`.

`ams_memory_table("Tom", ams_store["Tom"])`: a flex vstack with `**ownerId: Tom** (3)` and a `marimo-table` with columns `memory`, `type`, `created (UTC)`. The empty owner case renders `ownerId: Z (0)` and the `_No memories yet._` text.

### 5. marimo rules

- `am_store` refs: `ams_cleanup_button`, `ams_forget`, `ams_memories`, `ams_memory_table`, `ams_refresh_button`, `ams_seed`, `ams_seed_button`, `ams_seeds`. Defs: `ams_store`. This matches the spec.
- No `_`-prefixed name is defined by any cell (`private defs: {}`), so none can be read across cells.
- No cycle: `am_render` reads only `html`, `mo`, `models`. `am_store_fns` reads `USER_ID`, `ams_client`, `models`. `am_owner` reads `USER_ID`, `ams_seeds`. Nothing stage 1 defines is read by a cell it also reads.
- `USER_ID` is defined in `redis_client` (value `'Tom'`), `ams_client`, `models`, `dt`, `secrets` in `am_connect`, `stream_llm` in `llm_client`.

### 6. Voice

| Cell | Sentence | Verdict |
|---|---|---|
| `am_store_md` | "Seed writes three memories per customer with `POST /long-term-memory`, each tagged with an `ownerId` the app chooses." | Passes (about 14 words with code stripped). See findings 2 and 3. |
| `am_store_md` | "Clean up deletes every memory whose `ownerId` is Tom or Priya." | Passes. |
| `am_store_md` | "Chat sessions are not deleted; the store expires them after an hour by default." | **Fails**, semicolon. Rewrite in finding 1. |
| `am_owner` | "Ask the three starter questions as Tom, then switch to Priya and ask them again." | Passes (15 words). No rule-of-three, no "you already". |
| `am_owner` | "Switching customers starts a new conversation." | Passes. |
| `am_store` | "Agent Memory also extracts memories from session events in the background, when extraction is enabled on the store." | Passes (18 words). The hedge "when extraction is enabled" is on voice. |
| `am_store` | "Select Refresh to look for new ones." | Passes. |

Headings `What long-term memory holds` and `Chat as Tom or Priya` are sentence case with no terminal period. No exclamation marks, banned words, or empty words in any of the checked text. The notes `Wrote N new memories ...`, `Every seed memory is already in long-term memory.`, `Deleted N memories for Tom and Priya.` also pass.

### 7. Later stages

- Every name stages 2 to 4 expect from stage 1 exists: `ams_store`, `ams_owner`, `ams_seed_button`, `ams_refresh_button`, `ams_cleanup_button`, `ams_bars`, `ams_memory_table`, `ams_seeds`, `ams_memories`, `ams_seed`, `ams_forget`.
- Types are safe for `ams_memory_table`. `m.memory_type` is `Optional[str]` (value `'semantic'` today), so a `None` from an extracted memory still renders. `m.created_at` is a non-optional timezone-aware `datetime` in UTC, so `.strftime` and the `created (UTC)` label are correct. `m.owner_id` is populated (`Tom`, `Priya`).
- `ams_sweep` keys bands by `m.text`, so two memories with identical text collapse into one bar. Extraction in stage 4 could produce a near duplicate of a seed, which would stay separate (different text), but an exact duplicate would hide one row.
