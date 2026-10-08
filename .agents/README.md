# What is under `.agents/`

The notebook `iris_basics.py` on disk, and the live kernel while a marimo session runs, are the source of truth. Nothing in this folder describes the current notebook except `agent-memory-column.md`. Everything else is history or tooling. Dates in file names and headers say which notebook a document was written against.

| Path | Status | What it is |
|---|---|---|
| `agent-memory-column.md` | current | Column 4 (Redis Agent Memory) as it is: layout, Nick's rulings, verified service and kernel facts, how to edit, store state, open items |
| `skills/marimo-pair/` | tooling | A copy of the marimo-pair skill. The Skill tool does not list it; read `SKILL.md` and run `scripts/execute-code.sh` by hand with the jq PATH prefix. The maintained copy is `../../.agents/skills/marimo-pair/` |
| `archive/agent-memory-2026-10/` | history | The 2026-10-06 and 2026-10-07 work on column 4: redesign proposal and critiques, build spec and six stage reports and reviews with screenshots, simplify and inline specs, briefs, reports, reviews, the teaching-review polish spec and its reports and review. Read for the reasoning behind a ruling, not for what the column does |
| `research/*-2026-09-30.md` | reference | Six docs-and-SDK briefs written for the MVP plan (Redis Search, LangCache, Context Retriever, Agent Memory, marimo, niquests and packaging). API facts in them were checked on 2026-09-30; re-check before relying on one |
| `plans/iris-basics-mvp-plan.md`, `plans/candidates/`, `plans/research/`, `plans/iris-basics-4-column-plan.md`, `plans/archive/` | superseded | Plans for the September notebooks (the 8.7k-line original, the 4-column plan, the MVP rewrite). The current working copy is Nick's 2026-10-06 trim, which these do not describe |
| `staging/` | superseded | The 2026-10-05 v2 build: scaffold, column fragments, `assemble.py`, build and review notes. Do not run `assemble.py` over the root notebook |
| `reviews/plan-conformance-2026-09-30.md` | superseded | The MVP plan conformance review |

Columns 1 to 3 (Redis Search, LangCache, Context Retriever) are title cells only in the current working copy. Column 4 is the exemplar the others will follow.
