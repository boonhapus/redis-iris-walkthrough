# Research brief: Redis Agent Memory for column 3 (MVP, raw HTTP with niquests)

Written 2026-09-30 for the iris-basics MVP rewrite. Read-only research: no call was made to any Redis or Iris service.

Tags. **VERIFIED** = read in the published OpenAPI 1.0.0 (`openapi-agent-memory.json`, fetched today from redis/docs main), in the current docs, or shown by the live run of 2026-09-30 recorded in README "Verified live" and plan section 10, or tested offline today (niquests 3.21.2, marimo 0.25.0 under uv, Python 3.13). **UNVERIFIED** = plausible, not confirmed against the live service. Where this brief and the older `.agents/plans/research/agent-memory.md` disagree, this brief wins; that file's "Verifier corrections" still hold.

What changed in the docs since the older brief (VERIFIED, fetched today):
- The REST, Python and TypeScript quickstarts were rewritten around one travel-planning scenario: session memory, extraction at a 1-minute cadence, summarization at 6/2, a `trip_preference` custom type, and a semantic exclusion prompt. `/develop/ai/context-engine/agent-memory/api-examples/` is now an alias of `rest-api-quickstart`.
- New sections in the developer guide and create-service: sensitive-data exclusions (built-in detectors, custom RE2 detectors, semantic exclusions; "early-stage feature, enabled for selected accounts"). They apply to extraction only, never to directly created memories.
- The `api-reference` folder holds one file, `openapi-agent-memory.json` (still version 1.0.0, no `servers`, no `securitySchemes`, no examples). Same 14 operations as before. SDK 0.4.1 still has extra operations (namespaces, move) that are not in the spec.
- The landing page banner: "Redis Agent Memory is currently available in preview."

---

## 1. Connection and credentials

| Our .env name (keep) | Docs name(s) | Where in the console | Notes |
|---|---|---|---|
| `MEMORY_API_BASE_URL` | `AGENT_MEMORY_URL` (REST quickstart), `AGENT_MEMORY_SERVER_URL` (LangGraph tutorial), `AGENT_MEMORY_ENDPOINT` (Iris Colab), positional `server_url` in the SDK | Agent Memory > your service > Configuration > General settings > **Endpoint** | Must include `https://`. VERIFIED (quickstart, view-service). |
| `MEMORY_STORE_ID` | `STORE_ID` (REST quickstart), `AGENT_MEMORY_STORE_ID` (tutorial and SDK env var) | Same screen > **Store ID** | Goes in every path: `/v1/stores/{storeId}/...`. Pattern `^[a-zA-Z0-9-]+$`, max 64. VERIFIED. |
| `MEMORY_API_KEY` | `API_KEY` (quickstarts), `AGENT_MEMORY_API_KEY` (tutorial and SDK env var), `AGENT_MEMORY_KEY` (Colab) | Shown once at creation; else **API keys** tab > New API key | `Authorization: Bearer <key>`. VERIFIED. |

Recommended `.env.example` block for column 3 (comments only; the loader reads the three names on the left):

```
# ---- Column 3 . Agent Memory ----
# Console: Agent Memory > your service > Configuration > General settings shows the
# Endpoint (include https://) and the Store ID. API keys tab > New API key makes a key,
# shown once. The Redis docs call these AGENT_MEMORY_URL (or AGENT_MEMORY_SERVER_URL),
# AGENT_MEMORY_STORE_ID and AGENT_MEMORY_API_KEY; this notebook keeps the MEMORY_* names
# the sibling redis-iris-demos project already uses.
# MEMORY_API_BASE_URL=https://<endpoint>
# MEMORY_STORE_ID=
# MEMORY_API_KEY=
```

- marimo loads the `.env` next to `pyproject.toml` by default (`[tool.marimo.runtime] dotenv = [".env"]` to be explicit; relative paths resolve against the pyproject directory). VERIFIED in marimo docs `guides/configuration/runtime_configuration.md`. The column reads `os.environ.get(...)`.
- No new secret is needed for column 3. No LLM key: the default store uses "Redis in-built keys" for extraction, summarization and embeddings. VERIFIED (create-service "AI model and credentials").
- The sibling `redis-iris-demos/.env` defines exactly `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, `MEMORY_API_KEY` (names listed, values never read). VERIFIED.
- The store is shared with the live Iris demo. Every call this column makes must be scoped by `ownerId` (this run) and, for searches that teach, `namespace`. VERIFIED reasoning from the older brief; the live run confirmed the scope lens (5 / 0 / 0).

Auth surface: the spec has no `securitySchemes`, so Bearer auth is documented only in the quickstarts and SDK. VERIFIED. Send `Accept: application/json` on every call and let niquests set `Content-Type` on JSON bodies.

---

## 2. Exact request and response JSON

All bodies are camelCase on the wire. Identifiers (`ownerId`, `actorId`, `sessionId`, memory `id`, `namespace`, `storeId`) match `^[a-zA-Z0-9-]+$`, 1-64 chars: letters, digits, hyphen, **no underscore**. `memoryType` matches `^[a-zA-Z][a-zA-Z0-9_-]*$`. VERIFIED (spec).

Below, `ib-ab12cd34` stands for this run's owner id (`ib-` plus 8 hex chars of the run tag) and `iris-basics-notebook` for the fixed namespace.

### 2.1 Health

```
GET {MEMORY_API_BASE_URL}/health
Authorization: Bearer <key>
-> 200 {"status": "healthy"}                       # HealthStatus enum has only "healthy"
```
VERIFIED shape (spec `HealthResponseContent`). Whether `/health` needs the Bearer header is UNVERIFIED (the spec lists only a 200 for it; the quickstart sends the header anyway). Prefer the store health below: it also validates the store id and the key.

```
GET {MEMORY_API_BASE_URL}/v1/stores/{storeId}/health
-> 200 {"status": "healthy", "features": {"<feature>": {"status": "healthy"}, ...}}
      status: "healthy" | "degraded" | "unavailable"; features: map of name -> {status}
-> 401 {"title": "...", "status": 401, "detail": "...", "type": "/errors/authentication-failed"}
-> 404 {"title": "...", "status": 404, "detail": "...", "type": "/errors/resource-not-found"}   # wrong store id
```
VERIFIED shape (spec `StoreHealthResponseContent`); live on 2026-09-30 "the store reported healthy". Feature names are not documented: print the JSON, do not switch on names. UNVERIFIED which features appear.

An Endpoint copied from the wrong place (a console page) answers 200 with HTML. Guard: if `.json()` raises `niquests.JSONDecodeError`, say "MEMORY_API_BASE_URL is the Endpoint on the service's General settings". The old notebook needed this check (VERIFIED in the current `am_health`).

### 2.2 Bulk create long-term memories

```
POST /v1/stores/{storeId}/long-term-memory
{
  "memories": [
    {
      "id": "ib-ab12cd34-m1",                       # REQUIRED, client-chosen, "for idempotent creation"
      "text": "Dana is allergic to peanuts and tree nuts, so trail snacks and camp meals must not contain them.",
      "ownerId": "ib-ab12cd34",                      # REQUIRED
      "namespace": "iris-basics-notebook",           # optional label; taken as given on direct writes
      "memoryType": "semantic",                      # optional: semantic | episodic | message | session_summary_view | <custom>
      "topics": ["food", "allergy"]                  # optional, <= 50 topics, each 1-100 chars
    },
    ... up to 100 records ...
  ]
}
-> 201 {"created": ["ib-ab12cd34-m1", "ib-ab12cd34-m2", "ib-ab12cd34-m3", "ib-ab12cd34-m4", "ib-ab12cd34-m5"]}
       "errors": [{"id": "...", "error": "..."}] appears only when some records failed (partial success is possible)
```
VERIFIED: spec `CreateMemoryRecord` (required `id`, `ownerId`, `text`; `text` 1-50000 chars), `BulkCreateLongTermMemoriesRequestContent` (1-100), response `created` always present. Live: "Five memories were written and listed at once." Re-creating an existing id: upsert or per-item error is UNVERIFIED (spec says only "idempotent creation").

### 2.3 Search

```
POST /v1/stores/{storeId}/long-term-memory/search
{
  "text": "What should the kitchen avoid serving me?",
  "filter": {
    "ownerId":   {"eq": "ib-ab12cd34"},              # case-sensitive
    "namespace": {"eq": "iris-basics-notebook"}      # case-insensitive; eq never matches a record with no namespace
  },
  "filterOp": "all",                                  # "all" (AND, default in iris-demos) | "any" (OR)
  "similarityThreshold": 0.3,                         # float 0-1, "minimum similarity threshold for vector search results (normalized cosine similarity)"
  "limit": 5                                          # 1-100, default 10
}
-> 200 {
  "items": [
    {
      "id": "ib-ab12cd34-m1",
      "text": "Dana is allergic to peanuts and tree nuts, so trail snacks and camp meals must not contain them.",
      "memoryType": "semantic",
      "ownerId": "ib-ab12cd34",
      "namespace": "iris-basics-notebook",
      "topics": ["food", "allergy"],
      "createdAt": "2026-09-30T12:00:00Z",
      "updatedAt": "2026-09-30T12:00:00Z"
    }
  ]
  # "nextPageToken": "..." only when more pages exist; "sessionId" and "attributes" only when set
}
```
- VERIFIED: no field in the request selects semantic/keyword/hybrid; `text` is not in the spec's `required` list (omitting it is UNVERIFIED; `""` is the documented listing form, used by the Iris Colab and verified live). `MemoryRecord` requires only `id`, `text`, `createdAt`, `updatedAt`.
- VERIFIED live: **the reply carries no score**, items come back in rank order at threshold 0.0 (the allergy memory was #1 of 3 for the question above with 0 words in common), the sweep placed it in band 0.3-0.4 and the other four in 0.0-0.3, at 0.5 nothing returned, and a live re-query agreed with the derived table. One run, one question: treat the numbers as "what one run showed", not as constants.
- Filter grammar (VERIFIED, spec): clause operators are `eq`, `ne`, `in` (array), `all` (array) on `ownerId`, `sessionId` (case-sensitive), `namespace`, `topics`, `memoryType` (case-insensitive); `createdAt` takes `gt|gte|lt|lte|eq` RFC 3339; `attributes` is for custom types. `any` is not a clause operator; it is a `filterOp` value. Paging: `limit` plus `pageToken` from `nextPageToken`.
- Server default of `similarityThreshold` when omitted: UNVERIFIED. iris-demos sends 0.7 by default; the Colab omits it for listing. Always send it explicitly on teaching searches; omit it on listing calls.

### 2.4 Bulk delete long-term memories (DELETE with a JSON body)

```
DELETE /v1/stores/{storeId}/long-term-memory
{"memoryIds": ["ib-ab12cd34-m1", "ib-ab12cd34-m2", "ib-ab12cd34-m3", "ib-ab12cd34-m4", "ib-ab12cd34-m5"]}   # 1-100 ids
-> 200 {"deleted": ["ib-ab12cd34-m1", "ib-ab12cd34-m2", "ib-ab12cd34-m3", "ib-ab12cd34-m4", "ib-ab12cd34-m5"]}
       "errors": [{"id": "...", "error": "..."}] only when some ids failed
```
VERIFIED (spec; live cleanup left "no memory or session for the run's owner"). What an unknown id produces (error entry or silent omission) is UNVERIFIED.

### 2.5 Session events

```
POST /v1/stores/{storeId}/session-memory/events
{
  "sessionId": "ib-ab12cd34-s1",                     # optional; omitted -> server makes a 32-char id
  "actorId": "ib-ab12cd34",                          # REQUIRED; the FIRST event's actorId becomes the session's ownerId
  "role": "USER",                                    # USER | ASSISTANT | SYSTEM (uppercase)
  "content": [{"text": "I am training for a 50 kilometre trail race in April."}],   # only content part type is {"text"}
  "createdAt": "2026-09-30T12:00:00Z",               # REQUIRED, RFC 3339 UTC, app time
  "namespace": "iris-basics-notebook",               # optional; read from the FIRST event only, ignored later; inherited by extracted memories
  "metadata": {"source": "iris-basics"}              # optional, any JSON
}
-> 201 {
  "event": {
    "eventId": "<32 alphanumerics>",
    "actorId": "ib-ab12cd34",
    "sessionId": "ib-ab12cd34-s1",
    "role": "USER",
    "content": [{"text": "..."}],
    "createdAt": "2026-09-30T12:00:00Z",
    "systemTimestamp": "2026-09-30T12:00:00.412Z",   # server ingest time
    "metadata": {"source": "iris-basics"}
  }
}
```
VERIFIED (spec `AddSessionEventRequestContent`, `SessionEvent`; live: the AUTORUN inspector sent a USER event and read the session back). The `actorId` description says "1-255 chars" but its schema says max 64: trust 64. For ASSISTANT events use a fixed actor such as `assistant` (iris-demos) or `cairn-agent`; it does not change the session owner. `createdAt` in Python: `datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")`.

### 2.6 Get and delete a session

```
GET /v1/stores/{storeId}/session-memory/ib-ab12cd34-s1            # optional ?includeSummarisedEvents=true (not for normal use)
-> 200 {
  "sessionId": "ib-ab12cd34-s1",
  "ownerId": "ib-ab12cd34",                          # "set from the actorId of the first event"
  "namespace": "iris-basics-notebook",               # absent when the first event carried none
  "events": [ SessionEvent, ... ],                   # summarised events excluded once a summary exists
  "summary": {                                       # present only after at least one summarization cycle
    "text": "...", "summarizedUpToEventId": "<32>", "createdAt": "...", "updatedAt": "...",
    "metadata": {}, "summarizedEvents": 6
  }
}
-> 404 {"title": "...", "status": 404, "detail": "...", "type": "/errors/resource-not-found"}

DELETE /v1/stores/{storeId}/session-memory/ib-ab12cd34-s1
-> 204 (no body)
-> 404 (same shape as above): treat as already gone
```
VERIFIED (spec). Summaries appear only if the store has automatic summarization on and the session passed "Summarize after"; on a default store the MVP will not see one, so the markdown must say "No summary yet: this store summarizes only when it is switched on and the session is long enough". Session TTL default 1 hour (create-service), so sessions expire on their own; memories do not (365 days).

### 2.7 List sessions (cleanup only)

```
GET /v1/stores/{storeId}/session-memory?filterOwnerId=ib-ab12cd34&limit=1000
-> 200 {"items": ["ib-ab12cd34-s1"], "total": 1}   # "nextPageToken" when more
```
VERIFIED (spec). `includeAll=true` lists every session in the shared store: never send it. `filterOwnerId` and `includeAll` are mutually exclusive; one of them is required. Not exercised live (the old cleanup deleted ledgered ids).

### 2.8 Error shapes

RFC 7807-like body on every 4xx/5xx: `{"title", "status", "detail", "instance", "type"}`; 400 adds `"fields": [{"field", "rule", "message"}]`. VERIFIED (spec).

```
424 Failed Dependency  {"title": "Failed Dependency", "status": 424, "detail": "<why>", "type": "/errors/resource-unavailable" | "/errors/database-out-of-memory"}
429 Too Many Requests  {"title": "Too Many Requests", "status": 429, "detail": "<why>", "type": "/errors/too-many-requests"}
401                    type "/errors/authentication-failed"
404                    type "/errors/resource-not-found"
```
- Every store-scoped operation, including search, maps 424 to the same body; SDK 0.4.1 adds `/errors/invalid-model-credentials` and `/errors/model-quota-exceeded` for model-backed calls. Treat `type` as an open string and print `type` and `detail`. VERIFIED (spec + verifier correction).
- 424 means the store could not do the work (database full or unreachable, model key or quota). It is a failure, not "0 results"; iris-demos swallows it (`allow_424=True`), the notebook must not. VERIFIED.
- 429: no `Retry-After` header is documented and no limits are published. UNVERIFIED thresholds. The MVP makes a handful of calls; show the message and let the user press again.

---

## 3. Semantics that shape the teaching

1. **No score.** The search reply has no similarity, score or distance field (spec and live). The only knob is `similarityThreshold`, a minimum on normalized cosine similarity. "How close" can only be inferred by asking again at a different threshold. VERIFIED.
2. **Threshold behaves like a hard cut, as far as one run showed.** Raising it removes memories from the bottom of the ranking; the set at 0.3 was a subset of the set at 0.0, and a live re-query matched the derived band table. One run, so PLAUSIBLE rather than a guarantee; write "in a live run" in the copy.
3. **Zero-overlap recall is real.** "What should the kitchen avoid serving me?" shares no word (after crude stemming) with the peanut-allergy memory and ranked #1 of 3. VERIFIED live. Whether hosted search is pure vector or hybrid is UNVERIFIED; a 0-words-in-common hit shows keyword matching cannot be what found it.
4. **Extraction is background and slow.** Default cadence 5 minutes, configurable 60-600 s, "how often the extraction pipeline runs while a session is active". VERIFIED (create-service, view-service). The live run saw nothing 11 minutes after one event, but that event was a question with no fact in it (the kick-off conversation was not pressed), so it says little about cadence and a lot about content: post facts if you want extraction to have something to keep. Extraction reads the whole session and weighs new memories against existing ones with model judgment (overview). Extracted ids are server-chosen.
5. **A session's owner and namespace come from its first event only.** `ownerId` = first `actorId`; `namespace` = first event's `namespace`, later values ignored; extracted memories inherit both. So every session this notebook starts opens with a USER event from `ib-<run>` carrying the namespace. VERIFIED (spec descriptions).
6. **`namespace eq` never matches a record without a namespace**; `ne` does. Omit the namespace filter to search across all namespaces. This is why Clean up lists by owner alone. VERIFIED (spec `NamespaceFilter`).
7. **TTLs.** Short-term (session) default 1 hour; long-term default 365 days; both editable per service. VERIFIED (create-service). Leftover memories therefore outlive the demo by a year unless deleted.
8. **Summarization is opt-in per store** with "Summarize after" and "Keep most recent"; the quickstart uses 6/2, production defaults are higher. `summary` is absent until a cycle ran. VERIFIED.
9. **Namespace label vs namespace resources.** The published spec and all quickstarts use the string `namespace`. SDK 0.4.1 deprecates it in favour of `namespaceRef` and namespace resources, which are not in the published spec. Keep the string, say so in one comment, and re-check before shipping. VERIFIED (PyPI description, spec).
10. **Direct writes vs extraction.** Docs: use direct writes "for bulk imports or external knowledge sources: anything that didn't originate in a conversation"; extraction is for facts that emerge from session events. The MVP shows both, direct first because it is instant. VERIFIED (overview FAQ).
11. **Read-after-write.** Memories were searchable immediately after the bulk create in the live run and in the Colab. No documented guarantee. PLAUSIBLE.
12. **Exclusions do not apply to direct writes** and session content reaches the model provider: use only fictional facts (Dana). VERIFIED (developer guide warning).
13. **Two products with the same name.** The redis-ai-resources recipes (`python-recipes/agents/03_memory_agent.ipynb`, `llm-message-history/`) use RedisVL and the open-source agent-memory-server (`/v1/working-memory/...`, results carry `dist`), not the hosted service. Do not borrow their payloads or score handling. VERIFIED (grep of the recipe: RedisVL only).

---

## 4. MVP teaching sequence for column 3

Keep the Cairn customer Dana and the five facts as they are (they are good and were live-tested):

| id | text | topics |
|---|---|---|
| `ib-<run>-m1` | Dana is allergic to peanuts and tree nuts, so trail snacks and camp meals must not contain them. | food, allergy |
| `ib-<run>-m2` | Dana has wide feet, so she needs boots half a size bigger than her usual size. | sizing, footwear |
| `ib-<run>-m3` | Dana is planning a winter trek across Patagonia in February and gets cold easily. | travel, trip |
| `ib-<run>-m4` | Dana prefers gear made from recycled materials and will pay extra for it. | preferences, sustainability |
| `ib-<run>-m5` | Dana wants parcels sent express, with no plastic packaging. | shipping, packaging |

Zero-overlap questions (VERIFIED no shared stem by the old test suite; only the first is live-verified as #1): "What should the kitchen avoid serving me?" (m1), "My toes feel squashed on long descents." (m2), "Somewhere freezing to visit next year?" (m3). Offer the first as the default text and the other two in the markdown; no preset dropdown.

Sample data and the shared-word counter are not Redis Iris and may live in `src/` (for example `iris_basics/sample_data.py: dana_memories(owner_id, namespace)` and `iris_basics/text.py: shared_words(a, b)`). The HTTP calls, request bodies, filters and cleanup logic stay in the notebook cells.

### (a) Write five facts (consent button)

One `mo.ui.run_button(label="Write 5 memories about Dana")`. The result cell builds the body, POSTs, and shows `created` plus the exact request (masked). Explain in one sentence why the ids are ours: idempotent creation and a cleanup that needs no ledger. One call.

### (b) Search by meaning with a threshold slider (debounced live re-query)

Controls: `mo.ui.text(value="What should the kitchen avoid serving me?", full_width=True)` and `mo.ui.slider(0, 1, 0.05, value=0.3, debounce=True, show_value=True, label="similarityThreshold")` (both signatures VERIFIED on marimo 0.25.0). The result cell runs one POST per change (marimo only reruns the dependent cell; `debounce=True` means on release), `limit=5`, filter owner + namespace, and shows a table: rank, memory, topics, words in common (0). Below it, the exact request JSON with the threshold that was sent.

Why live re-query beats the 11-threshold sweep for an MVP: one request per interaction instead of eleven; nothing derived, nothing to caveat ("assumes a hard cut", monotonicity check, cached band table, Re-query live button); the audience moves the slider from 0.0 (all five, in rank order) to 0.3 (the allergy memory alone in the live run) to 0.5 (none) and watches the request body change. That is the whole lesson about a score-less API in three slider moves. Recommendation: drop the sweep entirely; do not keep a tiny one. Put the live numbers in the markdown as "in a live run on 2026-09-30", hedged.

### (c) Scope lens

Same question, `similarityThreshold: 0.0`, `limit: 100`, three filters: this owner + this namespace; owner `nobody-owns-this-id` + this namespace; this owner + namespace `no-such-namespace`. Show a three-row table: scope, filter JSON, count. Expected 5 / 0 / 0 (VERIFIED live). Read-only, so it can run automatically once memories exist; three calls. One sentence: on a shared store the filter is the tenant boundary, which is why every call in this column carries both.

### (d) Session memory

One `mo.ui.run_button(label="Send 3 events and read the session back")`. The result cell POSTs three events to `ib-<run>-s1` (USER from `ib-<run>` with the namespace first, then ASSISTANT from `cairn-agent`, then USER), GETs the session and shows: owner and namespace from the reply (with the one-line rule about the first event), the events table (role, actor, text, createdAt), and "No summary yet" or the summary text. Give the USER events facts worth extracting, for example "I'm training for a 50 kilometre trail race in April." and "My left knee complains on steep downhills, so I always carry trekking poles." Four calls.

### (e) Optional: check for extracted memories

A `mo.ui.run_button(label="Check for extracted memories")` created in the same cell as the send-events button; its result cell lists by owner only (`text: ""`, no threshold, `limit: 100`) and shows the items whose ids are not `ib-<run>-m1..m5`, or "None yet. Extraction runs on the store's cadence (5 minutes by default, 1-10 minutes configurable) and only keeps what it judges worth keeping." Keep it only if the column stays within budget; it is the one cell that shows the service writing on its own, and it costs one call. Never poll or loop.

### (f) Clean up

One `mo.ui.run_button(kind="danger", label="Clean up")`. Order: delete sessions, then list-and-delete memories, then list again. Details in section 5. Show counts: sessions deleted, memories deleted (and how many had ids the service chose), memories left (expect 0).

### Drop from the old column

The 11-threshold sweep, band table and Re-query live; the mo.ui.chat memory inspector, New session and Prompt assembly accordion; the presets dropdown; the deep-dive switch; Under the hood (Redis peek); orphan sweep and typed confirmation; Start extraction now as a separate kick-off (the session cell now posts facts); Clean up again as a separate button (pressing Clean up again is the same button); recorded-live panel; presenter notes; the ledger; SecretText fields. The collapsed SDK snippet stays (section 7).

---

## 5. Cleanup correctness

Goal: after Clean up, the store holds no session and no memory for `ib-<run>`, including memories extraction created with its own ids.

1. **Sessions.** `GET /session-memory?filterOwnerId=ib-<run>&limit=1000` (page on `nextPageToken`), then `DELETE /session-memory/{id}` for each; 204 or 404 both count as gone. Fallback if the list call fails: delete the deterministic ids the MVP uses (`ib-<run>-s1`). Never `includeAll`. Deleting sessions first is also the best guess at stopping further extraction from them (UNVERIFIED that it does).
2. **List every memory under the owner.** `POST /long-term-memory/search` with `{"text": "", "filter": {"ownerId": {"eq": "ib-<run>"}}, "limit": 100}`, **no `namespace` filter** (an extracted memory from a session without namespace would be invisible to `eq`; ours carry the namespace, but owner-only is the superset and costs nothing) and **no `similarityThreshold`** (the Colab omits it; iris-demos sends 0.7 with empty text and nobody has shown that returns everything). Follow `nextPageToken` until absent, cap at 50 pages. VERIFIED live: the old notebook's `am_list` used exactly this call (text "", threshold omitted, limit 100, owner-only for cleanup) and the footprint after Clean up was empty; "five memories were written and listed at once".
3. **Client-side owner check.** Before deleting, assert every listed item has `ownerId == "ib-<run>"`; if one does not, delete nothing and say the filter was not applied as sent. Cheap, and it is the notebook's own guard on a shared store. VERIFIED pattern from the old `am_cleanup`.
4. **Delete.** `DELETE /long-term-memory` with `{"memoryIds": [...]}` in chunks of 100; union of listed ids and `ib-<run>-m1..m5` (in case a write succeeded but the listing lagged). Count `deleted`; report `errors` if any.
5. **List again** and show the count left (expect 0).
6. **Late extraction.** Extraction can add memories for minutes after the last event. The cleanup markdown says: if you sent events, press Clean up again in about ten minutes. The same button handles it. With the session deleted and only 3 events sent, the live run's experience (nothing extracted from one event in 11 minutes) suggests the leftover risk is small but not zero; long-term TTL is 365 days, so say it.

Empty-text search returned the five directly written memories in the live run. Whether it returns *every* memory regardless of embedding (that is, whether "" acts as a pure filter listing or as a vector query for the empty string with no cut) is UNVERIFIED in general; the observed behaviour and the Colab's use of it are the evidence. If the first cleanup ever shows `left > 0` with no `errors`, the fallback is `similarityThreshold: 0.0` explicitly on the listing call.

---

## 6. niquests 3.21.2 specifics (all VERIFIED offline today unless marked)

```python
import json, niquests

agent_memory_http = niquests.Session(
    base_url=f"{MEMORY_API_BASE_URL.rstrip('/')}/v1/stores/{MEMORY_STORE_ID}",
    headers={"Authorization": f"Bearer {MEMORY_API_KEY}", "Accept": "application/json"},
    timeout=(5, 30),            # (connect, read) seconds; a session timeout is the default for its requests
)
```
- `Session(base_url=...)` joins relative paths: `"/long-term-memory/search"` and `"long-term-memory/search"` both become `{base_url}/long-term-memory/search`; an absolute URL bypasses `base_url` (useful for `GET {endpoint}/health`). `session.timeout` holds the tuple.
- **DELETE with a JSON body:** `session.request("DELETE", "/long-term-memory", json={"memoryIds": ids})`, or `session.delete("/long-term-memory", json=...)` (`Session.delete` forwards `**kwargs`). The prepared body is `b'{"memoryIds": [...]}'` with `Content-Type: application/json;charset=utf-8`.
- `json=` is ignored if non-empty `data` or `files` is passed (docs). Query params: `params={"filterOwnerId": owner, "limit": 1000}` renders `?filterOwnerId=...&limit=1000`.
- Timeouts: a scalar applies to connect and to read separately, not wall-clock; a tuple is `(connect, read)`; without any timeout niquests defaults to 30 s for GET/HEAD/OPTIONS and 120 s for write methods (docs quickstart "Timeouts"). Exceptions: `niquests.Timeout`, `niquests.ConnectTimeout`, `niquests.ReadTimeout`, `niquests.ConnectionError`.
- Errors: `response.raise_for_status()` raises `niquests.HTTPError` with `.response`; the problem body is `err.response.json()`. Do not use it blindly: for 4xx read `response.json()` yourself and render `status`, `type`, `detail`. A non-JSON body raises `niquests.JSONDecodeError` (exported at top level).
- **Showing the exact sent request, masked:** `response.request` is the `PreparedRequest` with `.method`, `.url`, `.headers`, `.body`. Render:

```python
def sent_request(response):
    request = response.request
    headers = {k: ("Bearer ***" if k.lower() == "authorization" else v) for k, v in request.headers.items()}
    body = json.loads(request.body) if request.body else None
    return {"method": request.method, "url": request.url, "headers": headers, "json": body}
```
  Verified output for the delete above: `{"method": "DELETE", "url": ".../v1/stores/st1/long-term-memory", "headers": {"Authorization": "Bearer ***", "Accept": "application/json", "Content-Length": "26", "Content-Type": "application/json;charset=utf-8"}, "json": {"memoryIds": ["ib-x-m1"]}}`. Never print `session.headers`, `request.headers` or `response.request.headers` raw: unlike httpx, niquests does not mask `Authorization` in reprs. This helper is display glue and can live in `src/`.
- `response.elapsed` is a `timedelta`; handy for "took 180 ms" next to each call.
- Retries: `Session(retries=0)` is the default; `retries` takes an int or `niquests.adapters.Retry` (urllib3's `Retry`). Leave it at 0 in the MVP and show 429 as text. Rate limits are undocumented.
- User agent is `niquests/3.21.2`; HTTP/2 is negotiated by default and needs no setting.
- Pin `niquests==3.21.2` in `pyproject.toml`.

Suggested call helper for the column (kept in the notebook; it is the teaching seam):

```python
def agent_memory_call(method, path, json_body=None, params=None):
    response = agent_memory_http.request(method, path, json=json_body, params=params)
    if response.status_code == 204:
        return None, sent_request(response)
    try:
        payload = response.json()
    except niquests.JSONDecodeError:
        raise RuntimeError(f"{response.status_code} with a non-JSON reply; is MEMORY_API_BASE_URL the Endpoint?")
    if response.status_code >= 400:
        raise RuntimeError(f"{payload.get('status')} {payload.get('type')}: {payload.get('detail') or payload.get('title')}")
    return payload, sent_request(response)
```
(Delete-session should treat 404 as success; do that at the call site.)

---

## 7. The same call with the official SDK (collapsed note, text only)

`redis-agent-memory` 0.4.1 (PyPI, released 2026-09-16; Python >= 3.10; depends on httpx >= 0.28.1, pydantic >= 2.11.2; Speakeasy-generated). Not a dependency of the notebook.

```python
# uv add redis-agent-memory==0.4.1
from redis_agent_memory import AgentMemory

with AgentMemory(MEMORY_API_BASE_URL, store_id=MEMORY_STORE_ID, api_key=MEMORY_API_KEY) as agent_memory:
    found = agent_memory.search_long_term_memory(request={
        "text": "What should the kitchen avoid serving me?",
        "similarity_threshold": 0.3,
        "limit": 5,
        "filter_": {"owner_id": {"eq": owner_id}, "namespace": {"eq": "iris-basics-notebook"}},
    })
    for item in found.items:
        print(item.text)
```
Notes for the collapsed text: methods are snake_case and the SDK serializes to the same camelCase JSON shown above; every method has an `_async` twin; the SDK reads `AGENT_MEMORY_API_KEY` and `AGENT_MEMORY_STORE_ID` from the environment if not passed; errors are typed (`errors.MemoryFailedDependencyErrorResponseContent` for a 424 on search or create). Caveat worth one line: a misspelled filter key in a plain dict is dropped silently and the search runs unscoped across the shared store (verified offline in the older brief), so build filters from `models.LongTermMemoryFilter` or check the serialized body. The SDK marks the string `namespace` deprecated in favour of `namespace_ref`.

---

## 8. Production-friendly patterns to state (short, in the column's markdown)

From the docs and the LangGraph tutorial's "Production considerations" (VERIFIED sources):
- A stable `ownerId` per user and a `namespace` per app or environment; put both on every search. "Bad owner IDs or namespaces can mix users' memories."
- Retrieve session memory before a turn, search long-term memory with the user's current message, keep `limit` small (the tutorial uses 5), write both sides of the turn as events.
- Store durable preferences long-term; keep transient task details in the session; review or confirm before writing sensitive memories; avoid secrets in memories.
- Treat 424 as a failure of a dependency, not as an empty result; check readiness with the store health call.
- Delete by explicit ids; never Flush or Delete service on a shared store (both erase everything); never `includeAll`.
- One `niquests.Session` with a timeout, the key from the environment, never logged.

Voice (per `demo/.agents/VOICE.md`): "Redis Agent Memory" at the column's H1 and first mention, then "Agent Memory"; "session memory" and "long-term memory"; state "Agent Memory is in public preview." flat once; colon-terminated lead-ins that name actor and effect ("The cell below writes five memories in one request:"); decode the return value ("The service answers `201` with the ids it created."); hedge what the model decides ("can become long-term memory"); no exclamation marks, no "seamless", no console click-through inside a step (the console path belongs in `.env.example` and the not-configured note).

---

## 9. Risks and open questions

- UNVERIFIED: behaviour of `text: ""` with a threshold; the server default threshold; whether `text` can be omitted; upsert vs error on a repeated memory id; what bulk delete reports for an unknown id; whether deleting a session halts pending extraction; 429 limits; which store health features are listed; whether `/health` accepts unauthenticated calls.
- The store's configuration (cadence, TTLs, summarization, custom types, exclusions) is invisible to the API; a custom memory type on the shared store will also fire on the notebook's session and produce extra records under the owner, which the owner-only cleanup catches.
- marimo `run_button` value is True only for the run its click triggers; a result cell that also depends on a changing input would see False and `mo.stop`. Keep result cells depending on the button, the session object and constants only.
- One live data point for the threshold bands. If the slider default of 0.3 shows nothing on another store or model, the copy must not promise a number; say "start around 0.3 and move it".

---

## 10. Recommended MVP cell list for column 3 (11 cells, about 8 requests in the core path)

| # | Cell | Kind | Calls |
|---|---|---|---|
| 1 | Title: "Redis Agent Memory", two sentences on the two tiers, docs link, small mermaid (`app -> session events -> background extraction, minutes -> long-term memory <- direct write; search`) | markdown, hide_code | 0 |
| 2 | Configure and connect: read `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, `MEMORY_API_KEY` from `os.environ` (marimo loaded `.env`); `mo.stop` with one "not configured" callout if any is missing; `am_owner_id = f"ib-{run_tag}"`, `am_namespace = "iris-basics-notebook"`; build the `niquests.Session`; `GET /health` (store) and show status JSON plus the masked request | code, runs on load | 1 |
| 3 | Write memories: `mo.ui.run_button("Write 5 memories about Dana")` with a one-paragraph lead-in | button | 0 |
| 4 | Write result: bulk `POST /long-term-memory`, show `created` and the request body; sets `am_memories_written` | result | 1 |
| 5 | Search controls: question `mo.ui.text` and `similarityThreshold` `mo.ui.slider(0, 1, 0.05, value=0.3, debounce=True, show_value=True)`; lead-in says the API returns no score, so the slider is the only knob | controls | 0 |
| 6 | Search result: one `POST /long-term-memory/search` per change (`limit: 5`), table with rank, memory, topics, words in common; request body underneath; one hedged sentence with the live numbers | result | 1 per change |
| 7 | Scope lens: same question at 0.0 under this owner / a fake owner / a wrong namespace; table of filter and count (5 / 0 / 0) | result, automatic after 4 | 3 |
| 8 | Session memory: `mo.ui.run_button("Send 3 events and read the session back")` and, if kept, `mo.ui.run_button("Check for extracted memories")`; lead-in on owner and namespace coming from the first event | buttons | 0 |
| 9 | Session result: 3x `POST /session-memory/events`, `GET /session-memory/{id}`; owner, namespace, events table, summary or "No summary yet" | result | 4 |
| 10 | Extracted check result (optional): owner-only listing, items with ids not ours, or the cadence note | result | 1 |
| 11 | Clean up: `mo.ui.run_button(kind="danger")` and, in the next cell, the result (list sessions, delete each, list memories by owner, owner check, delete in chunks, list again, counts). That is 12 cells with the optional check kept | button + result | about 5 |

If the column must be 10 or 11 cells, drop cell 10 first (fold its sentence into cell 9's markdown), then merge cells 3 and 8 into one "buttons" cell only as a last resort, since separating direct writes from session events is the point of the column.
