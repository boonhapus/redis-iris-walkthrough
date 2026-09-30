**(a) Clean.** No chart of a duration, no throughput, no race. Only residue: Day 0 item 5's "first-embed time" (setup expectation, not Redis — keep, but label it install-time, not Redis-time) and Panel 3's "HNSW is the one-word change you make at scale," which invites a perf question you must not answer on screen. Otherwise obedient.

**Highest-value gaps**

1. **Act 1 has no seed cell.** §3.1's six cells are header / connect / preflight / bench / recall card / trace. But the recall card says "Recalled 3 of **12**" and §3.3 budgets **12 `HSET`**. The twelve seed facts are never written by any listed cell, and the ledger keeps §5.1 item 3 ("Seed/reset, run_button-gated") as a changed row with no home. Add cell 3.5: *Seed twelve facts*, `run_button`-gated, one pipeline, prints its own `redis-cli` equivalent — and make it the thing §8 item 3's consent gate guards.

2. **Panel 2's `SAMPLES 0` beat does not work.** `MEMORY USAGE` default is `SAMPLES 5`; the memory hash has ~5 fields (text, vector, owner_id, +2). With `nfields ≤ samples` Redis visits every field and multiplies the average by the field count, so the answer is *identical* to `SAMPLES 0`. The "what 'estimate' means" payoff silently prints the same number twice in front of the customer. Either move it to the session LIST (many elements) or cut it — the −0.25d trim already lists it.

3. **The preflight contradicts the consent gate.** §4.1 probes `OBJECT ENCODING` "on a scratch key" and renders *before any panel*; §8 item 3 requires `DBSIZE` + a zero-result `SCAN` + a button press *before the first write*. The probe is a write. Fix: the scratch key is `demo:{run_id}:_probe` created **inside** the consent cell, or fold consent into the probe cell and reorder.

4. **Four copies of the same command surface; Act 2/Act 3 boundary undefined.** Commands appear in Act 1's accordion, Act 2's slot 3, Act 3's gates, and the pinned trace. That is four renderers to keep in sync and the reason Act 2 risks reading as Act 3 twice. State the rule: **the trace is the only command renderer**; Act 1's accordion and Act 2's slot 3 are `TRACE` filtered by tag. Then declare the boundary in §1 — **Act 2 shows what the value *is*, Act 3 shows the call that made it.** Without that sentence the three acts are load-bearing in file order but not in content.

5. **(c) partly.** Good on A-A encoding degradation and the one-DB case. But "Search must be enabled at creation" is framed as A-A-only — it applies to **Standard Essentials** too, so the README's first line must be "create the database with Search and query enabled," not just "point at Standard." Three load-bearing claims are unverified and unmarked: Search auto-enabled on A-A Redis 8+, **vector KNN on A-A at all**, and Panel 3 obs-4 (expiry propagating to the index) under CRDT tombstone semantics. Mark them UNVERIFIED and add them to Day 0 item 2.

6. **Guard gaps.** `_check_allowed`'s key-position map must handle **subcommand offsets** (`OBJECT ENCODING k`, `MEMORY USAGE k`, key at argv[2]) — stated as "position map" with no mention. And teardown's `UNLINK` / `FT.DROPINDEX` are in neither the allowlist nor the ~15-command map, so §8 item 5 fails its own guard.

7. **(e) One credential, ~4 actions — but `uv` is never installed.** §9.1 opens with `uvx` and no prerequisite line. Add the Windows/macOS uv install one-liner as README step 0, or the "30-second taste" is an error message.

8. **Hardcoded vector geometry.** "1024 bytes · 256 dims" appears in prose, the recall card, the RESP example and `FT.CREATE`'s `DIM`. Derive all four from `len(embed(""))//4`; confirm potion-base-8M's dim on Day 0.

9. **Rot.** `seed_memories.py:113-116`, `memory_service.py:147-170`, `client.py:722/1648`, `variables.py:62-63`, `session_manager.py:449` are quoted by line number. Pin every one to a commit-SHA permalink or the walkthrough quotes the wrong lines in a month.

10. **(d) Correct** — Set/List/Stream/Hash/Zset/String all match, no original chain reproduced. One answer-key risk: prediction 3's only valid solution is *deleting the vector field* (hash encoding never converts back to listpack); say so.

11. **(g) Near-complete.** Missing: original §10's "marimo is a second unfamiliar tool," whose mitigation was app mode — the revision makes `marimo edit` primary and adds a molab account fork, so that risk **grew** and was dropped from §13. Add it back.

12. **(f) Honest in total, mis-distributed.** Day 2 carries the `redis-cli` renderer (which needs a shared binary-argv elision rule for the 1024-byte `PARAMS` value — unbudgeted), the embed layer *and* Act 1. Day 7's 0.75d covers README + SAFETY.md + repo + ipynb + name audit. Move SAFETY.md to Day 1 beside the guard.

13. **Missed by every stage:** no statement of room size — 30 connections / 100 ops-sec is one database per participant, so either say "presenter-driven, participants watch" or "one free DB each"; no `LICENSE` in §9.5 (counterpart is MIT) and no CI smoke check (`marimo export ipynb` + name-collision grep) despite two pre-commit promises; `--trusted` on a remote URL is a code-execution trust bypass that belongs in SAFETY.md, not just §9.1; and the deliverable list omits the **two-minute presenter script**, which is the actual insurance for "a second SE runs it cold."