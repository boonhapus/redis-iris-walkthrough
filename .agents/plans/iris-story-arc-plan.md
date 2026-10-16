# Plan: rebuild `iris_basics.py` as one story in four chapters

Created 2026-10-16. This plan supersedes `iris-basics-plan.md`, which stays for history only: its rulings no longer apply, and the facts still worth having are copied into "Facts carried forward" below. Read this file first in a new session. While a marimo session runs, the live kernel is the source of truth; otherwise the saved `iris_basics.py` is.

Work one iteration at a time. Each iteration ends with the notebook running. At the end of each, tick its box, add a line to the progress log, and record any number you measured under "Facts carried forward".

## Status

- [ ] Iteration 0. Baseline and branch
- [ ] Iteration 1. De-risk Context Retriever and tool calling (no notebook changes)
- [ ] Iteration 2. One shared world: the data
- [ ] Iteration 3. Prologue and Chapter 1, Search
- [ ] Iteration 4. Chapter 2, Semantic caching
- [ ] Iteration 5. Chapter 3, Context engineering and retrieval
- [ ] Iteration 6. Chapter 4, Agent memory, and the epilogue
- [ ] Iteration 7. Teaching pass, README, final counts

## Goals

Nick's brief, 2026-10-16:

> The notebook should walk readers through a complete story or narrative, separated into 4 chapters of increasing complexity which map to Search -> Semantic Caching -> Context Engineering and Retrieval -> Agent Memory. Write a complete story arc for this notebook such that we maximize reader learning and understanding, and minimize the amount of code and cells needed to support the goal.

The three goals:

1. **One complete story** in four chapters of increasing complexity: Search, Semantic Caching, Context Engineering and Retrieval, Agent Memory.
2. **Maximize reader learning and understanding.**
3. **Minimize the code and cells** needed to support the first two.

What meeting them looks like:

- **One story.** One customer (Priya), one help center, one support assistant, and the same names and ids in every chapter. Each chapter opens on something Priya needs that the assistant can't do yet, and closes on the next gap.
- **Complexity that builds.** Each chapter's code calls the previous chapter's: `lcs_answer` calls `rss_search`, `crs_answer` calls `rss_search` and extends Chapter 2's prompt, and `ams_turn` calls `crs_answer`.
- **Understanding.** Every command and request appears once in the prose with "reading it" bullets, and Chapters 2 to 4 each show exactly what the model saw.
- **Minimal.** About 39 cells (from 67), about 13 with code shown (from 16), at most 1,500 non-blank lines (from 2,919), one `mo.state` pair (from 7), one chat (from 2), and no threshold sweeps. Full table under "Metrics".

## Decisions, 2026-10-16

1. This plan supersedes the old one. Its rulings (the N slider stays, every Iris function stays visible, no control upstream of the LangCache chat, and the rest) don't bind. Anything worth keeping is restated here.
2. Hide code that only creates or cleans up resources whenever that serves the goals. Everything a turn calls stays visible.
3. LangCache's chat becomes an Ask form.
4. In Chapter 3 the model chooses which tools to call. The tool-calling loop for OpenAI, Anthropic, and Gemini lives in `cairn_support`, hidden like `stream_llm`.

## Where we start (2026-10-16, commit `15a9d54`)

- Five columns: setup, then Search, LangCache, Context Retriever, and Agent Memory, each a standalone tour. All four titles open with "As Cairn Outfitters, you store…" and bring their own data and people: 36 help articles searched by unnamed users, 5 gear-care answers that repeat the help center's care articles almost word for word, 6 customers named after computer scientists, and Tom and Priya, who only appear in Agent Memory. Nothing built in one chapter is used in the next.
- Complexity doesn't climb. LangCache has the heaviest UI in the notebook for a simple idea. Context Retriever never involves a model (surface admin plus a manual tool console), so context engineering is never shown.
- 67 cells, 51 hidden, 2,919 non-blank lines, 26 controls, 7 `mo.state` pairs, 2 chats. Two sweeps exist only to draw thresholds: 7 extra LangCache searches and 42 Agent Memory searches per turn.
- Worth keeping: Search's three searches written the way `redis-cli` takes them, the "reading it piece by piece" bullets, LangCache's paraphrase-versus-swapped-item contrast, the "index kinds predict the tools" table, Agent Memory's app-chosen ids and `ownerId` filter, and `env_check` with its Restart button.

## The story

Cairn Outfitters, a made-up outdoor-gear shop, is launching an AI support assistant. Priya leaves for a canoe trip in Maine in three weeks and has a tent on order. Tom is a second customer, used wherever a lesson needs two people. Each has one customer id, used as the record key in Chapter 3 and as the `ownerId` in Chapter 4.

### Chapter 1. Search: find the right article

- **Story:** before any AI, customers search the help center in their own words. Tom types "my trail shoes feel cramped" and looks up his Trailhead boots. Priya mistypes "waterproofng", then searches "Where is my parcel?" because her tent hasn't arrived.
- **Teaches:** the schema decides what you can search; writing a document indexes it; KNN by meaning; a similarity floor with `VECTOR_RANGE` (an empty reply is an answer); filtering before ranking.
- **Code:** the three current searches, nearly as they are. The KNN query becomes `rss_search(question, k=3, where="*")`, still a raw `FT.SEARCH` string, which every later chapter calls.
- **Ends on:** Priya gets a list of shipping articles. She wanted an answer, and nothing in the help center knows where her tent is.

### Chapter 2. Semantic caching: answer once, serve many

- **Story:** Cairn puts a model on top of Chapter 1: search the help center, then let the model write the answer from the articles. Customers ask the same care questions in different words, and each one costs a model call and a couple of seconds.
- **Teaches:** cache-aside (search the cache before the model, store the answer after, with a TTL) and the threshold tradeoff.
- **Climax, in order:**
  1. Tom asks "How do I wash my down sleeping bag?". It misses, the assistant answers from the help center, and the answer is stored.
  2. Another customer asks "What's the right way to launder a goose-down sleeping bag?". It hits at 0.905, with no model call.
  3. Priya asks "How do I wash my synthetic sleeping bag?" and is served the down answer at 0.950, higher than the real rewording.
  4. At a threshold of 0.96, both miss. Similar isn't the same, so only cache answers that are right for anyone who asks.
- **Code:** the client and request bodies, the assistant's base prompt `lcs_system(articles)`, and `lcs_answer(question, threshold)`, which searches the cache and, on a miss, calls `rss_search` and the model, then stores. The cache fills itself from the first question, so there's no seed data.
- **Ends on:** Priya asks "Where's my tent?", gets a generic answer, and the cache stores it for everyone. A personal answer can't come from a shared cache or from the help center.

### Chapter 3. Context engineering and retrieval: answer about her

- **Story:** Cairn's customers, products, and orders are JSON documents in Redis. You declare a data model, Context Retriever turns it into MCP tools, and the assistant chooses which to call.
- **Teaches:** the data model decides the tools; agent keys versus the admin key; and context engineering itself: for each question the app decides what the model sees, here the customer's identity, Chapter 1's articles, and tool results.
- **Code:** the three model classes, a create function (surface, agent key, import), the tool list and tool call, and `crs_answer(customer_id, messages, extra="")`. The reader sees each tool call and reply, the assembled system prompt, and an answer about her actual order. A second question, Tom's "Has the refund for my trail shoes gone through?", shows the same assistant picking different tools.
- **Ends on:** a week later, in a new chat, Priya asks "Which sleeping bag should I get for the trip?". Her orders don't say she's allergic to down or paddling in Maine in June. She told the assistant last week, and it forgot.

### Chapter 4. Agent memory: remember her

- **Story:** what customers said in earlier sessions is kept as long-term memories, one set per customer. Each turn writes the message to session memory, recalls memories by meaning, and adds them to Chapter 3's context.
- **Teaches:** ownership (the `ownerId` filter is Chapter 1's filter before ranking), recall by meaning with no shared words, session versus long-term memory with background extraction, and the complete turn.
- **Payoff:** "How do I wash my sleeping bag after the trip?", the question that fooled the cache, gets the synthetic answer because the assistant remembers the down allergy. "Which sleeping bag should I get for the trip?" combines her memories, the temperature-rating article, and the catalog. Tom's memories never show up in Priya's chat.
- **Code:** the seed list, and `ams_turn`, about ten lines around `crs_answer`. This is the notebook's only chat, because it's the only chapter where conversation history matters.

### Epilogue

One markdown cell follows a single turn through every layer and says what each added:

1. Agent Memory writes the message to session memory and recalls the customer's long-term memories.
2. Redis Search finds the help articles closest in meaning.
3. Context Retriever answers the tool calls the model makes for her orders and products.
4. The model gets the system prompt the app assembled, the chat, and the tool results.

LangCache sits in front of questions whose answer is the same for everyone. The same cell holds Clean up everything.

### Ideas that recur

| Idea | Introduced | Returns in |
|---|---|---|
| The schema decides what you can ask | Ch 1, field types in `FT.CREATE` | Ch 3, index kinds decide the tools |
| Matching by meaning, with a floor | Ch 1, `KNN` and `VECTOR_RANGE` | Ch 2 and Ch 4, `similarityThreshold` |
| Filter before ranking | Ch 1, `@year` before `=>` | Ch 3 filter tools, Ch 4 `ownerId` |
| The app decides what the model sees | Ch 2, articles in the prompt | Ch 3 identity and tool results, Ch 4 memories |

## Design rules

These replace the old plan's rulings.

1. **One world.** One Redis database, one help center, one cast with fixed ids. No chapter brings its own dataset.
2. **Composition, not copies.** A chapter reuses earlier functions by calling them. No function is written twice.
3. **What's shown.** Show the building blocks the story composes: the clients, the model classes, the seeds, and one function per chapter (`rss_search`, `lcs_answer`, `crs_answer`, `ams_turn`) with the few functions they call. A reader can follow the code's own arc from one to the next. Each command or request appears once in the prose with its "reading it" bullets, so cells that only execute it are hidden, as are rendering, wiring, and lifecycle code. A short delete-by-id function may stay beside its client when it teaches something (LangCache entries can't be listed, so the app keeps their ids).
4. **Redis-forward in Chapter 1.** Every Search command is text written the way `redis-cli` takes it, split by `shlex` into one `REDIS_CLIENT.execute_command`.
5. **Show what the model saw.** Chapters 2 to 4 display the system prompt and any tool calls behind each answer.
6. **One chat.** Only Chapter 4 uses `mo.ui.chat`. Chapters 2 and 3 use `mo.ui.form`, so a request is sent only on submit.
7. **No sweeps.** Displays use the service's own reply (LangCache's `similarity`) plus at most three extra calls per turn.
8. **Prose budget.** One markdown cell per step: a story line, the command or request, three to five "reading it" bullets, at most one callout, and a short "Try it". Doc links go in one accordion per chapter.
9. **Kept from the old plan, because they still serve the goals:**
   - Markdown tables through `mo.md` rather than `mo.ui.table`.
   - The settings check stops with `mo.stop`, and the Restart button calls `os._exit(0)`.
   - Redis Cloud is assumed.
   - True globals are UPPERCASE and live in column 0.
   - No preview or availability status in any title, and no `FT.HYBRID`.
   - The epilogue recaps the story rather than telling readers what to copy.
   - Console links only where a published deep link exists.

## Target structure

Keep `width="columns"`, one chapter per column. Cell names keep the `rs_`, `lc_`, `cr_`, and `am_` prefixes, and the names a chapter defines keep `rss_`, `lcs_`, `crs_`, and `ams_`. "Shown" means the code is visible; "hidden" means `hide_code=True`.

### Column 0. Prologue and setup (6 cells, 4 shown)

| Cell | Code | Defines | Role |
|---|---|---|---|
| `prologue` | hidden | — | Premise, cast, chapter map. Run column by column; each chapter builds on the last; Clean up everything is at the end of Chapter 4. |
| imports | shown | stdlib names, `mo`, `json_session`, `json_request`, the types, `CAST` | As now, plus `CAST`, minus anything unused. |
| `env_check` | hidden | `os` | As now. |
| `redis_client` | shown | `REDIS_CLIENT`, `CAIRN_SCOPE`, `redis`, `shlex` | As now. |
| `ai_model` | shown | `AI_MODEL`, `stream_llm`, `answer_with_tools` | Adds the tool-loop import. |
| embedding | shown | `EMBEDDING_MODEL`, `embed_texts` | As now. |

### Column 1. Chapter 1, Search (10 cells, 1 shown)

| Cell | Code | Defines | Role |
|---|---|---|---|
| `rs_title` | hidden | `rs_create_button` | Story opening, what you'll learn, the hash shape. Step 1: the schema (`FT.CREATE`), writing is indexing (`HSET`), the Create button. |
| `rs_search_fn` | shown | `RS_KEY_PREFIX`, `RS_IDX_NAME`, `HELP_ARTICLES`, `rss_vector`, `rss_search` | Constants, question to bytes, and the KNN query as a function. No UI references. |
| `rs_create` | hidden | `rss_index_info` | On Create: `FT.CREATE` and the `HSET` plus `EXPIRE` pipeline. Every run: `FT.INFO`, for the search cells' guards. |
| `rs_knn_md` | hidden | `rs_meaning_pick` | Step 2, by meaning, with the three presets attributed to Tom and Priya. |
| `rs_knn` | hidden | — | `rss_search(rs_meaning_pick.value)` and its table. |
| `rs_floor_md` | hidden | `rs_floor_slider` | Step 3, the similarity floor. `$EPSILON` goes in an accordion. |
| `rs_floor` | hidden | — | The `VECTOR_RANGE` query and its table. |
| `rs_filter_md` | hidden | `rs_year_slider` | Step 4, filter before ranking, on Priya's "Where is my parcel?". Ends on. Doc links. |
| `rs_filter` | hidden | — | Post-filter beside `rss_search(..., where="@year:[Y +inf]")`. |
| `rs_render` | hidden | `rss_table` | Renderer. |

`rss_search` returns hits with `title`, `body`, `category`, `year`, and `vector_score`, because Chapters 2 to 4 put the bodies into prompts. The table can leave `body` out.

### Column 2. Chapter 2, Semantic caching (4 cells, 2 shown)

| Cell | Code | Defines | Role |
|---|---|---|---|
| `lc_title` | hidden | `lcs_ask`, `lcs_clear` | Story, what you'll learn, the cache-aside path (accordion), both request bodies with "reading it" bullets, the scripted "Try it", the Ask form, and a Clear button for replaying the chapter. |
| `lc_connect` | shown | `lcs_session`, `lcs_call`, `lcs_request`, `lcs_entry`, `lcs_entries`, `lcs_forget` | Bearer session, the two request bodies, the ledger of entry ids this kernel stored, and delete by id. |
| `lc_answer_fn` | shown | `lcs_system`, `lcs_answer` | The assistant's base prompt, and cache-aside with retrieval on a miss. |
| `lc_ask` | hidden | — | On Clear: `lcs_forget()`, then stop. On submit: run `lcs_answer` inside `recording()` and show the search and its reply (similarity and the stored prompt it matched), hit or miss, on a miss the articles and the prompt, the store request, the answer, and timings. |

The Ask form batches a question picker (the four scripted questions in story order, labeled with who asks), a free-text question that overrides the picker when filled, and a `similarityThreshold` slider from 0.80 to 1.00, starting at 0.85.

### Column 3. Chapter 3, Context engineering and retrieval (9 cells, 3 shown)

| Cell | Code | Defines | Role |
|---|---|---|---|
| `cr_title` | hidden | — | Story ("Where's my tent?"), what context engineering is, what you'll learn, the customer JSON shape with Priya. |
| `cr_model_fns` | shown | `Customer`, `Product`, `Order`, `crs_data_model`, `crs_record_keys`, `crs_entity_prefixes`, `SHOP_RECORDS` | As now, with the new data. |
| `cr_model` | hidden | `crs_create_button`, `crs_cleanup_button` | The tools the index kinds predict, the key idea, Create and Clean up, the password watch-out. |
| `cr_connect` | shown | `crs_admin`, `crs_connection_config`, `crs_create_body`, `crs_create_surface` | Admin side: the admin session, the create body built from `REDIS_URL`, and one function that creates the surface, makes an agent key, and imports the records. |
| `cr_lifecycle` | hidden | `crs_surface`, `crs_set_surface`, `crs_cleanup`, `crs_shown` | The surface state (the notebook's one `mo.state`), key expiry, cleanup by name (surfaces, documents, leftover indexes), and masking for display. |
| `cr_create` | hidden | — | On Create or Clean up. Shows the surface, `tools/list`, and `JSON.GET` of Priya's document. |
| `cr_answer_fn` | shown | `crs_list_tools`, `crs_call`, `crs_build_system`, `crs_answer` | Agent side: the agent key's tool list and calls, the system prompt (Chapter 2's prompt plus identity and `extra`), and the answer. |
| `cr_ask_md` | hidden | `crs_ask` | "Try it", the Ask form (customer, question picker, free text), and Ends on. |
| `cr_ask` | hidden | — | On submit: run `crs_answer` inside `recording()`, then show each tool call and reply, the system prompt, and the answer. |

Scripted questions: Priya's "Where's my tent? I leave for Maine on June 12." and Tom's "Has the refund for my trail shoes gone through?". Asking Priya's question as Tom shows that identity changes the answer.

### Column 4. Chapter 4, Agent memory, and the epilogue (10 cells, 3 shown)

| Cell | Code | Defines | Role |
|---|---|---|---|
| `am_title` | hidden | `ams_seed_button`, `ams_refresh_button` | Story (a week later, the assistant forgot), what you'll learn, the memory shape, Seed and Refresh. |
| `am_connect` | shown | `ams_client`, `models` | As now. |
| `am_store_fns` | shown | `ams_seeds`, `ams_seed`, `ams_memories`, `ams_forget` | The seed list keyed by customer id, and seed, list, and delete. |
| `am_store` | hidden | — | On Seed or Refresh: one memory table per customer. |
| `am_chat_md` | hidden | `ams_owner` | "Try it" with the starter prompts in story order, the customer picker (upstream of the chat, so a switch starts a new session), and the extraction watch-out. |
| `am_turn_fn` | shown | `ams_session_id`, `ams_request`, `ams_memory_block`, `ams_turn` | Write the message to session memory, recall this customer's memories, then `crs_answer(owner, messages, extra=...)`. |
| `am_chat` | hidden | `ams_chatbot` | `mo.ui.chat` around `ams_turn`, inside `recording()`, ending with a line of text when a turn fails. |
| `am_pane` | hidden | — | The chat beside the data: what this turn recalled, the same search for the other customer, the system prompt, the tool calls, and the session's messages. |
| `am_epilogue` | hidden | `am_cleanup_button` | One turn through every layer, what each chapter added, where the cache fits, and Clean up everything. |
| `am_cleanup` | hidden | — | On click: drop the Search index and unlink the article keys, then `lcs_forget()`, `crs_cleanup()`, and `ams_forget()` for the cast. |

Starter prompts, in order: "Which sleeping bag should I get for the trip?", "How do I wash my sleeping bag after the trip?", "Where's my tent?", and "I'm bringing my dog Juniper on the trip." (a new fact, so Refresh can show an extracted memory a few minutes later).

Totals: 39 cells, 13 shown, 26 hidden.

## Implementation recommendations

### Functions and how they compose

```python
# Chapter 1
def rss_search(question: str, k: int = 3, where: str = "*") -> list[dict]:
    """KNN over the help center: the k articles closest in meaning, among those `where` lets in."""

# Chapter 2
def lcs_system(articles: list[dict]) -> str:
    """The assistant's base prompt: answer in two or three sentences from these help articles."""

async def lcs_answer(question: str, threshold: float) -> str:
    # POST /entries/search with lcs_request(...). A hit returns its response.
    # A miss: rss_search(question), the model with lcs_system(articles),
    # then POST /entries with lcs_entry(..., ttlMillis) and the entryId into lcs_entries.

# Chapter 3
def crs_build_system(customer_id: str, articles: list[dict], extra: str = "") -> str:
    """lcs_system(articles), plus who the customer is, plus `extra`."""

async def crs_answer(customer_id: str, messages, extra: str = "") -> ChatTurn:
    # tools from crs_list_tools(surface), or [] when no surface exists
    # system = crs_build_system(customer_id, rss_search(last user message), extra)
    # yield from answer_with_tools(system, messages, AI_MODEL, tools, call_tool)

# Chapter 4
async def ams_turn(messages) -> ChatTurn:
    # add_session_event, then search_long_term_memory with ams_request(...)
    # yield from crs_answer(owner, messages, extra=ams_memory_block(owner, memories))
```

- The prompt grows one section per chapter: articles (Chapter 2), then identity (Chapter 3), then memories (Chapter 4). That makes "the app decides what the model sees" concrete.
- `crs_answer` falls back to no tools when no surface exists, so Chapter 4 still runs (memory plus help articles) if Chapter 3 was skipped. Chapter 3's own Ask stops with "Press Create first".
- The search text for both articles and memories is the last user message. The N slider is gone.
- Chapter 4 recalls with a fixed `similarityThreshold`. Start at 0.3, confirm it in Iteration 6, and explain it in one sentence, since thresholds were taught twice already.

### The tool-calling loop (`cairn_support/llm.py`)

Add `answer_with_tools(system_prompt, messages, model, tools, call_tool, max_rounds=5)`, an async generator like `stream_llm`:

- `tools` are the MCP tool dicts from `tools/list` (`name`, `description`, `inputSchema`). `call_tool(name, arguments)` returns the text to send back, data or error alike, so the model can recover from a tool error.
- **OpenAI:** chat completions with `tools=[{"type": "function", "function": {"name", "description", "parameters": inputSchema}}]`, looping while the reply has `tool_calls`.
- **Anthropic:** `tools=[{"name", "description", "input_schema": inputSchema}]`, looping while `stop_reason == "tool_use"` and sending `tool_result` blocks back.
- **Gemini:** `FunctionDeclaration(name=..., description=..., parameters_json_schema=inputSchema)`. The pinned `google-genai` 2.30.1 accepts raw JSON Schema there, so no schema translation is needed.
- No streaming during tool rounds. Yield the final text. With an empty `tools` list, delegate to `stream_llm`.
- After `max_rounds`, stop with a sentence saying so.
- Unit-test the provider-neutral parts (tool conversion, the loop with a fake client) in `tests/`. Live-test the provider whose key is in `.env`, and record which providers have run live.

### Showing what happened, with no display code in the turns

- Route the MCP calls through `json_request`, so `recording()` captures them the way it already captures LangCache requests. The hidden cells wrap each turn in `recording()` and render the exchanges.
- The hidden cells rebuild the prompt with the same shown functions (`rss_search`, `lcs_system`, `crs_build_system`, `ams_memory_block`), the way `lcs_request` and `ams_request` feed the panes today.
- Agent Memory goes through its SDK, so `am_pane` repeats the recall search, runs it once for the other customer, and reads the session: three calls per turn, replacing the 42-call sweep.

### Reruns and state

- Function cells never reference UI elements, except `am_turn_fn`, which reads `ams_owner` on purpose. Otherwise a click in one chapter would redefine functions and reset later chapters, including the Chapter 4 chat.
- Action cells (`rs_create`, `lc_ask`, `cr_create`, `cr_ask`, `am_store`, `am_cleanup`) reference only their own buttons or form plus function cells, and check prerequisites at call time. A form keeps its value across reruns, so an action cell that reran for an unrelated reason would send the request again.
- A cell that consumes a form starts with `mo.stop(form.value is None, ...)`, so nothing is sent at startup.
- The one `mo.state` is the Context Retriever surface, which must outlive reruns of the cell that created it. LangCache's ledger is a plain dict in `lc_connect`.

### Cleanup

- Every Redis key the notebook writes gets `EXPIRE 3600`. LangCache entries get a fixed `ttlMillis` of 15 minutes. Agent keys expire in an hour.
- Clean up everything, in the epilogue, removes all of it. Chapter 2's Clear (to replay the cold-cache sequence) and Chapter 3's Clean up (the surface holds the database password) stay as local buttons that call the same functions.
- Delete by name or id only. No `FLUSHALL`, `FT.DROPINDEX ... DD`, `SCAN`, `KEYS`, bodied `DELETE /entries`, or `/flush`.

## Data changes

In `src/cairn_support/sample_data.py`, and the seed list in `am_store_fns`:

- **Cast:** `CAST = {"Priya": "C001", "Tom": "C002"}`, imported in column 0.
- **Customers:** C001 Priya Shah and C002 Tom Becker replace Ada Lovelace and Grace Hopper. Both are in a new `amer` region; add it to the region values on `Customer` and `Order`. Keep the other four customers, so the filter and summarize tools have something to group.
- **Products:** add a down sleeping bag (P106) and a synthetic sleeping bag (P107), with temperature ratings in the titles. The Alpine Down Jacket (P101) stays as a distractor.
- **Orders:** O1001, Priya's Ultralight Tent 2P, becomes `shipped`, and O1002 becomes `delivered`, so the tent is her only order in transit. O1004, Tom's returned Trail Runner Shoes, already fits. Neither sleeping bag appears in Priya's orders, so only memory can tell the assistant she avoids down.
- **Memories:**
  - Priya (C001): "I'm allergic to down feathers", "I'm planning a canoe trip in Maine in June", "I always sleep cold", and "I have a cat named Miso". The cat stays to show that recall is selective.
  - Tom (C002): "I wear size 11 trail shoes", "I'm planning a hiking trip in the Dolomites in August", and "I've got two dogs, Max and Cece".
- **Help center:** add `care-synthetic`, "Washing a synthetic sleeping bag", for example "Wash on a gentle cycle with mild detergent and no fabric softener, then tumble dry on low until completely dry. Synthetic fill doesn't clump, so skip the tennis balls." It makes Chapter 2's wrong answer visibly wrong, and lets Chapter 4's right answer come from the help center. Update the module docstring's article counts.
- **Remove** `GEAR_CARE_ANSWERS`, `THRESHOLD_PROBES`, and `CACHE_ASIDE_SCRIPT`.
- Chapter 2's first question must be exactly "How do I wash my down sleeping bag?", the stored prompt behind the measured 0.905 and 0.950.

## Iterations

Notebook changes go through the live kernel with `marimo._code_mode`. The package under `src/` is edited on disk.

### Iteration 0. Baseline and branch

- Start the server (see "How to work on the live kernel") and confirm every cell reaches `idle`.
- Work on a branch, for example `story-arc`, so `main` at `15a9d54` stays the fallback.
- Record what the stores hold: `DBSIZE`, keys under `cairn:20261021:`, surfaces named `cairn-20261021`, and memories and sessions for the owners `Tom` and `Priya`.
- **Done when:** the baseline is in the progress log.

### Iteration 1. De-risk Context Retriever and tool calling (scratchpad only)

- With the current data, run Create, then `tools/list` (record the tool count and one `inputSchema`), `get_customer_by_id`, and `filter_order` with a tag condition on `customer_id`. Then delete the surface and unlink the documents.
- Check whether an agent key can be limited to one customer. If not, Chapter 3 says so in one sentence: the identity line in the prompt is the only scope.
- Confirm `json_request` handles the absolute `mcp_url` with a session that has no base URL.
- Write `answer_with_tools` and its unit tests. In the scratchpad, ask "Where is order O1001?" with the real tools and the `.env` provider.
- **Done when:** a model-chosen tool call round-trips live and the facts are recorded. If Context Retriever doesn't work, stop and revisit Chapter 3 before changing the notebook.

### Iteration 2. One shared world

- Make the data changes. Keep `tests/test_redis_search_replies.py` passing.
- Re-measure Chapter 1's numbers with the new article: the three presets, the floor examples, and the year-floor counts.
- **Done when:** the data matches this plan and the new numbers are under "Facts carried forward".

### Iteration 3. Prologue and Chapter 1

- Add `prologue`. Rebuild column 1 to its table: merge the config, create, and load cells; extract `rss_search`; attribute the presets to Tom and Priya; cut the prose to the budget; remove `rs_build_md` and `rs_connect`.
- **Done when:** Create, all three searches, and both sliders behave as the prose says, with the re-measured numbers.

### Iteration 4. Chapter 2

- Rebuild column 2 to its table. Remove the chat, `lc_controls`, `lc_workbench`, `lc_settings`, `lc_turn_sweep`, `lc_render`, the TTL and strategy controls, and the seed data.
- **Done when:** from a cleared cache, the scripted sequence gives a miss, a hit at about 0.905, the down answer served for the synthetic question at about 0.950, both missing at 0.96, and a generic tent answer that gets stored. Clear empties the ledger.

### Iteration 5. Chapter 3

- Rebuild column 3 to its table. Remove `cr_controls`, `cr_workbench`, `cr_console`, `cr_send`, `cr_settings`, and `cr_surface`. Fold `cr_admin_fns`, `cr_redis_fns`, and `cr_call_fn` into `cr_connect`, `cr_lifecycle`, and `cr_answer_fn`.
- **Done when:** Priya's tent question and Tom's refund question each produce sensible tool calls and correct answers, the system prompt is on screen, and Clean up leaves no surface, document, or index.

### Iteration 6. Chapter 4 and the epilogue

- Rebuild column 4 to its table. Remove `am_settings`, `am_turn_sweep`, the bars in `am_render`, both sliders, and `ams_query`.
- From the scratchpad, delete leftover memories and sessions under the old owners `Tom` and `Priya` once, since the new seeds use customer ids.
- Confirm the fixed threshold recalls "I'm allergic to down feathers" for both payoff questions, and that Tom's memories never appear for Priya.
- **Done when:** both payoff answers behave as designed in a browser, the owner switch starts a new session, and Clean up everything returns the stores to the Iteration 0 baseline.

### Iteration 7. Teaching pass, README, final counts

- Read the notebook cold as a new reader. Does every chapter open on a need and close on the next gap? Do the recurring ideas use the same words? Is any prose over budget?
- Rewrite `README.md` for the story; today it describes four separate columns.
- Record the metrics, run `uv run python -m marimo check --strict iris_basics.py`, and commit.
- **Done when:** the metrics meet their targets, or the gaps are explained in the progress log.

## Metrics

| Metric | Baseline 2026-10-16 | Target |
|---|---|---|
| Cells | 67 | about 39 |
| Hidden cells | 51 | about 26 |
| Cells with code shown | 16 | about 13 |
| Non-blank lines in the notebook | 2,919 | at most 1,500 |
| `mo.state` pairs | 7 | 1 |
| `mo.ui.chat` | 2 | 1 |
| Extra service calls per turn, for display only | 7 (LangCache), 42 (Agent Memory) | at most 3 |

Measure with the script in the appendix after each iteration.

## Risks and open questions

- **Context Retriever** has the least live history. The old plan's last record is that the column never reached the service, and nothing since says otherwise. Iteration 1 exists for this.
- **Providers:** only one model key is in `.env` at a time, so two of the three tool-calling paths may stay untested live. Record which ones have run.
- **Chapters depend on each other.** Chapter 2 needs Chapter 1's index, Chapter 3's prompt uses it too, and Chapter 4 uses Chapter 3's surface when it exists. Each Ask says which earlier step is missing.
- **Model output varies.** Answers and tool choices change between runs, so the prose says what to look for, never the exact answer.
- **Unmeasured:** the new memories and records, until Iterations 2 and 6.

## Facts carried forward

Measured before 2026-10-16. Re-measure anything the data changes touch.

**Environment**

- marimo is pinned to 0.24.2, because 0.25.0 and 0.25.1 reject every `mo.ui.chat` message (marimo #11090, fixed on main by #11114). Python must be below 3.14 for `redis-context-retriever` 0.0.6.
- The server copies `.env` when it starts, and marimo's loader never overrides a variable that's already set, so a changed `.env` needs a server restart, not a kernel restart.
- `[tool.marimo.runtime] auto_reload = "lazy"` picks up edits under `src/` on the next run of a cell that imports them.
- Redis Cloud 8.6.2, Search 8.6.10, redis-py 8.1.0. The free tier allows about 100 operations a second.

**marimo 0.24.2**

- `mo.ui.chat` pushes its value only when the model function ends with text, so the chat wrapper must end a failed turn with a line of text.
- `run_button.value` is true only for the run its click triggers. A cell can't read the value of a UI element it defines.
- The cell that calls a state setter isn't rerun by it.
- `mo.ui.table` is avoided because 0.24.2 forces the column explorer, chart builder, and page-size selector.
- The saved cell config holds only `column`, `disabled`, and `hide_code`. On save, `column=` stays only on the first cell of each column. `marimo run` sends no code unless started with `--include-code`, which is why every command must also appear in the prose.

**Search**

- "trail shoes feel cramped": `size-boots` first at 0.5394. "waterproofng": `care-waterproof` first at 0.5219. "Trailhead": `warranty-resole` third under KNN.
- "Where is my parcel?": top three at 0.4841, 0.5593, and 0.5831. At year floor 2025 the post-filter keeps 3 of 5, and the pre-filter returns 5.
- The range query needs `$EPSILON: 0.5`. With the default 0.01, a floor of 0.40 misses `size-boots` at about 0.54.
- A wrong-length embedding, or a non-numeric `year`, leaves the hash out of the index silently, counted in `hash_indexing_failures`.

**LangCache**

- Against the stored prompt "How do I wash my down sleeping bag?": the goose-down rewording scores 0.905 and the synthetic question 0.950. At 0.96 both miss.
- A miss is HTTP 200 with `data: []`. A new cache never expires entries unless `ttlMillis` is sent, and expiry lands about a second after it.
- The `entryId` is derived from the prompt, so storing the same prompt again replaces the entry. Entries can't be listed, hence the ledger.
- `DELETE` on an expired id answers 404 with type `/errors/resource-not-found`. A bad key answers 403 with an HTML page. A search takes 68 to 294 ms, about 440 ms cold. `maxResults` is honored, though missing from the published `api.yaml`.

**Context Retriever** (from the 2026-09-30 notebook)

- Three entities produced 21 tools. `get_customer_by_id` returned the record plus a `_retrieval` stamp. `filter_customer` with no condition answered `isError` with "at least one condition is required".
- A bad key answers 401 `{"error": "Invalid API key"}` from both the admin API and the MCP endpoint.
- Unverified: a schema-breaking argument returning JSON-RPC -32602, the HTTP status that comes with a JSON-RPC error, and how long `provisioning_indices` lasts.

**Agent Memory**

- The SDK client has a 5-second timeout and no retries. A session's `ownerId` comes from its first event's `actorId`. A memory created without `memoryType` is filed as `semantic`, and creating an existing id replaces its text.
- Search returns no score, and one search takes about 300 ms. Extraction shows up two to five minutes after a turn. Session memory lives an hour, long-term memory 365 days.
- `get_session_memory_async` on a deleted session raises `errors.NotFoundErrorResponseContent`. `list_sessions_async(filter_owner_id=...).items` is a list of id strings.

## How to work on the live kernel (this machine)

- Start the server from the project folder: `uv run --env-file .env marimo edit --host 0.0.0.0 --port 2718 --no-token --watch iris_basics.py`. The URL is `http://127.0.0.1:2718`.
- The skill scripts have CRLF line endings, and the `bash` first on PATH is WSL, which can't reach this machine's `127.0.0.1`. Use Git Bash, strip the carriage returns, and put jq on PATH. The first call, from PowerShell:

  ```powershell
  & "C:\Program Files\Git\bin\bash.exe" -lc 'export PATH="/c/projects/.vendor/jq:$PATH"; sed "s/\r$//" /c/work/redis/redis-iris-walkthrough/.agents/skills/marimo-pair/scripts/execute-code.sh | bash -s -- --url http://127.0.0.1:2718 -c "import marimo._code_mode as cm; help(cm)"'
  ```

  After that, write each script to `%LOCALAPPDATA%\Temp` and pass its Git Bash path (for example `/c/Users/Nick/AppData/Local/Temp/edit.py`) as the last argument instead of `-c "..."`.
- Never edit `iris_basics.py` on disk while a session runs. `--watch` and the kernel's saves overwrite it.
- Read a cell's code inside the same `cm.get_context()` before editing it, and keep edits that move a name between cells in one context. An exception inside the context discards every queued edit.
- `ctx.cells[name].code` is the cell body without its `def` wrapper, so match edits against that, not the file.
- Set one UI value per kernel call (`ctx.set_ui_value`) and read the result in the next. A chat's value can only come from the browser, so chat turns need a browser check.

## Progress log

- **2026-10-16.** Evaluated the notebook against the goals (baseline above) and proposed the story. Nick approved it with the four decisions above. Plan written; no notebook changes yet.

## Appendix: measuring the notebook

Save this as a temp file and run it with the project's Python, for example `.venv\Scripts\python.exe notebook_metrics.py iris_basics.py`. On the baseline it prints `cells 67, hidden 51, shown 16, non-blank lines 2919, mo.state 7, mo.ui.chat 2`.

```python
import ast
import re
import sys

src = open(sys.argv[1], encoding="utf-8").read()
lines = src.splitlines()
cells = hidden = nonblank = 0
for node in ast.parse(src).body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
        deco = node.decorator_list[0]
        cells += 1
        hidden += any(kw.arg == "hide_code" and kw.value.value is True for kw in getattr(deco, "keywords", []))
        nonblank += sum(1 for line in lines[deco.lineno - 1 : node.end_lineno] if line.strip())
states = len(re.findall(r"mo\.state\(", src))
chats = len(re.findall(r"mo\.ui\.chat\(", src))
print(f"cells {cells}, hidden {hidden}, shown {cells - hidden}, non-blank lines {nonblank}, mo.state {states}, mo.ui.chat {chats}")
```
