# Plan: rebuild `iris_basics.py` as one story in four chapters

Created 2026-10-16. This plan supersedes `iris-basics-plan.md`, which stays for history only: its rulings no longer apply, and the facts still worth having are copied into "Facts carried forward" below. Read this file first in a new session. While a marimo session runs, the live kernel is the source of truth; otherwise the saved `iris_basics.py` is.

Work one iteration at a time. Each iteration ends with the notebook running. At the end of each, tick its box, add a line to the progress log, and record any number you measured under "Facts carried forward".

## Status

- [x] Iteration 0. Baseline and branch
- [ ] Iteration 1. De-risk Context Retriever and tool calling (no notebook changes). Done except the live model round trip: the `.env` OpenAI key is revoked (see the progress log).
- [x] Iteration 2. One shared world: the data
- [x] Iteration 3. Prologue and Chapter 1, Search
- [x] Iteration 4. Chapter 2, Semantic caching (the model's own answers are unseen until a working key is in `.env`)
- [x] Iteration 5. Chapter 3, Context engineering and retrieval (the model's own tool choices and answers are unseen until a working key is in `.env`)
- [x] Iteration 6. Chapter 4, Agent memory, and the epilogue (the payoff answers themselves are unseen until a working key is in `.env`)
- [x] Iteration 7. Teaching pass, README, final counts

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
6. **One chat.** Only Chapter 4 uses `mo.ui.chat`. Chapters 2 and 3 batch their inputs (`mo.md(...).batch(...)`) and send on an **Ask** `run_button`, so a request is sent only on a press. (Changed in Iteration 4: in a browser, a `mo.ui.form` submitted again with unchanged values doesn't rerun anything, so asking the same question twice silently did nothing.)
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
| `am_chat_md` | hidden | `ams_owner` | As built: Step 2, the session-memory write (`POST /session-memory/events`, its body, Reading it bullets) with the extraction watch-out, then Step 3, recall. The customer picker is defined here and shown beside the chat (upstream of it, so a switch starts a new session). |
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

| Metric | Baseline 2026-10-16 | Target | Result, end of Iteration 7 |
|---|---|---|---|
| Cells | 67 | about 39 | 42 (the three hidden `*_ends` cells) |
| Hidden cells | 51 | about 26 | 29 (same three) |
| Cells with code shown | 16 | about 13 | 13 |
| Non-blank lines in the notebook | 2,919 | at most 1,500 | 1,495 (after the 2026-10-17 session-memory step) |
| `mo.state` pairs | 7 | 1 | 1 |
| `mo.ui.chat` | 2 | 1 | 1 |
| Extra service calls per turn, for display only | 7 (LangCache), 42 (Agent Memory) | at most 3 | Chapter 2: 1 (the nearest-prompt probe). Chapter 3: 0. Chapter 4: 3 (two recall searches and the session read), plus one `rss_search` on Redis to rebuild the prompt |

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
- 2026-10-16, about 16:00: the `.env` `LLM_API_KEY` (OpenAI, last four `ffMA`) is dead. OpenAI answered 401 three ways on three probes: `expired_secret_key`, `invalid_api_key` (models.list), and `token_invalidated` ("Your API key has been invalidated"). Every model call in Chapters 2 to 4 fails until it's replaced and the server restarted.
- Installed: google-genai 2.30.1, anthropic 1.11.0, openai 3.25.0. The anthropic and openai 3.x SDKs use `httpx2`, not `httpx`, so offline mock transports import `httpx2`.

**marimo 0.24.2**

- `mo.ui.chat` pushes its value only when the model function ends with text, so the chat wrapper must end a failed turn with a line of text.
- `run_button.value` is true only for the run its click triggers. A cell can't read the value of a UI element it defines.
- The cell that calls a state setter isn't rerun by it.
- `mo.ui.table` is avoided because 0.24.2 forces the column explorer, chart builder, and page-size selector.
- The saved cell config holds only `column`, `disabled`, and `hide_code`. On save, `column=` stays only on the first cell of each column. `marimo run` sends no code unless started with `--include-code`, which is why every command must also appear in the prose.

**Search**

Re-measured 2026-10-16 with the 37 articles (Iteration 2), every KNN cross-checked against a numpy brute force:

- KNN 3: "trail shoes feel cramped" gives `size-boots` 0.5394, `size-bags` 0.7251, `care-tent` 0.7453. "my trail shoes feel cramped" gives `size-boots` 0.5326, `size-bags` 0.7250, `care-tent` 0.7351. "waterproofng" gives `care-waterproof` 0.5219, `care-boots` 0.6361, `care-tent` 0.7378. "Trailhead" gives `ship-address` 0.8549, `size-boots` 0.8621, `warranty-resole` 0.8677 (third).
- `VECTOR_RANGE` with `$EPSILON: 0.5`: at floor 0.40, both cramped phrasings return `size-boots` alone and "waterproofng" returns `care-waterproof` alone; at floor 0.50 all three are empty. "Trailhead" is empty at 0.40 and at 0.30.
- `$EPSILON` depends on the index's history. On a fresh index right after its first load, the default (0.01) at floor 0.40 misses `size-boots` for both cramped phrasings, while 0.5 finds it. After a second load into the same index, or on an index with earlier loads, the default finds it too. So the prose may say a range search "can" stop early and miss, not that it does.
- "Where is my parcel?", KNN 5: `ship-international` 0.4841 (2025), `returns-refund` 0.5593 (2024), `ship-address` 0.5831 (2023), `ship-times` 0.6281 (2025), `ship-overdue` 0.6364 (2026). Post-filter kept of 5 at year floors 2022 to 2026: 5, 5, 4, 3, 1. The pre-filter always returns 5: at 2025 it adds `ship-free` and `order-backorder`; at 2026 it is `ship-overdue`, `ship-free`, `returns-damaged`, `order-gift`, and `size-packs` (a weak fifth at 0.8864). `care-synthetic` (2023) never appears.
- The articles later chapters put into prompts, KNN 3: "How do I wash my down sleeping bag?" `care-down` 0.3618, `care-pack` 0.4549, `care-synthetic` 0.4555. The goose-down rewording `care-down` 0.6848, `size-bags`, `care-pack`. "How do I wash my synthetic sleeping bag?" `care-synthetic` 0.3431, `care-down` 0.4514, `care-waterproof`. "Where's my tent?" `care-tent` 0.5638, `warranty-tent`, `ship-free` (no shipping article first; "Where is my parcel?" is the shipping question). "Has the refund for my trail shoes gone through?" `ship-overdue` 0.5769, `size-boots`, `order-cancel`; `returns-refund` is not in the three ("When will my refund reach my card?" puts it first at 0.3675). "Which sleeping bag should I get for the trip?" `size-bags` 0.5717, `ship-express`, `care-down`. "How do I wash my sleeping bag after the trip?" `care-down` 0.4739, `care-pack`, `care-synthetic` 0.5314 (both bag articles, so memory must steer). "I'm bringing my dog Juniper on the trip." nothing closer than 0.76.
- After a load: `num_docs` 37, `hash_indexing_failures` 0.
- Through the rebuilt notebook (Iteration 3, about 16:40) every number above came out the same. `num_docs` is 37 right after the pipeline, even on a fresh index. `num_records` grows with each reload (1041, 2082, 3123) while `num_docs` stays 37: stale records waiting for garbage collection. `FT.INFO` comes back as a dict under redis-py 8.1.0's default protocol (RESP3), not a flat list.
- `HSET` never removes a field. Keys written by an older loader keep its `id` field until they're unlinked or expire, so Create writes only `title`, `body`, `category`, `year`, `embedding`, and the `HSTRLEN` of `embedding` is 1024 (256 x 4).
- The `$EPSILON` miss reproduced on a throwaway index with the same schema: right after its first load, the default at floor 0.40 returns nothing for "my trail shoes feel cramped", while 0.5 finds `size-boots`. The notebook's own index always exists already, so a reader won't see the miss there; the prose says "can".
- potion-base-8M's WordPiece tokenizer splits "waterproofng" into `water`, `##proof`, `##ng`, and "Trailhead" into `trail`, `##head`. `FT.SEARCH <index> "Trailhead"` (full text) returns `warranty-resole` alone.
- A wrong-length embedding, or a non-numeric `year`, leaves the hash out of the index silently, counted in `hash_indexing_failures`.

**LangCache**

- Re-measured through the rebuilt Chapter 2 (Iteration 4): the goose-down rewording 0.904587, the synthetic question 0.95018625, and "Where's my tent?" 0.618 against "How do I wash my down sleeping bag?". With the other entries stored, the goose-down question scores 0.8838 against the synthetic entry, and "Where's my tent?" scores 0.6158 and 0.607 against the others. At 0.96 all three miss. Storing the same prompt again gives the same `entryId`.
- A search with no `maxResults` returns only the best entry. `similarityThreshold` 0 is accepted. Each entry carries `id`, `prompt`, `response`, `similarity`, `attributes`, and `searchStrategy`.
- Observed once, not investigated: a prompt containing a digit ("How do I wash my 20F down sleeping bag?", "Where's my tent? I leave for Maine on June 12.") came back with `data: []` even at threshold 0 with near matches stored.
- Timings: a hit takes 63 to 137 ms end to end; a store (`POST /entries`, 201) takes 67 to 179 ms. No real model answer was timed (key revoked).
- Against the stored prompt "How do I wash my down sleeping bag?": the goose-down rewording scores 0.905 and the synthetic question 0.950. At 0.96 both miss.
- A miss is HTTP 200 with `data: []`. A new cache never expires entries unless `ttlMillis` is sent, and expiry lands about a second after it.
- The `entryId` is derived from the prompt, so storing the same prompt again replaces the entry. Entries can't be listed, hence the ledger.
- `DELETE` on an expired id answers 404 with type `/errors/resource-not-found`. A bad key answers 403 with an HTML page. A search takes 68 to 294 ms, about 440 ms cold. `maxResults` is honored, though missing from the published `api.yaml`.

**Context Retriever** (live, 2026-10-16, Iteration 1, with the old data; data values change with the new records)

- Create through the notebook's button took about 4 s: `POST /context-surfaces` 201 with status `provisioning_indices`, which was already `active` at the first read about 4 s later. The service built one index per entity (`idx:<surface id>:<8 hex>:<entity>`) and 20 JSON keys, which the notebook expired at 3600 s. `GET /context-surfaces/{id}` carries `mcp_url` itself. The surface dict kept in the kernel never refreshes its `status`.
- `tools/list` (87 ms): 21 tools. Per entity `count_`, `filter_`, `get_..._by_id`, `list_`, `summarize_` (15), plus `search_customer_by_text` and `search_product_by_text`, plus four set tools: `except_results`, `expand_results`, `intersect_results`, `union_results`. About 33,000 characters of JSON, about 8,000 tokens, sent with every model request; the `inputSchema`s are most of it. Descriptions carry the data model's own text and enums (for example `filter_order` lists tag fields `customer_id`, `sku`, `region`, `status`).
- `tools/call` took 61 to 175 ms. A data reply is `result.content[0].text`, JSON: `get_*_by_id` gives the bare record plus `_retrieval` and no `id`; `filter_*` and `search_*` give `{count, has_more, limit, offset, results, total_count}` with each result's `id` the full Redis key. `get_*_by_id` wants the bare id: the full key gives "document not found" with the template doubled.
- Errors: a tool error is HTTP 200 with `isError` (no condition: "at least one condition is required"; unknown id: "get by ID failed: document not found: <key>"). A schema break (wrong type, enum miss, missing required field) and an unlisted tool both give HTTP 200 with JSON-RPC `-32602`, told apart only by the message. No key gives 401 `{"error": "API key required"}`, a made-up key 401 `{"error": "unauthorized: invalid agent token: ..."}` (a string, not a dict).
- Agent key: `cs_agent_` plus 43 characters, opaque. The service ignores `expires_at`: with `+00:00`, with `Z`, and with 2 hours, 1 day, 30 days, 200 days, or none, every key got created_at + 90 days (Iteration 5). The notebook no longer sends it. Deleting the surface ends its keys: the old key then answers `tools/list` with 401. The agent-key POST reply carries `mcp_url`, `expires_at`, `masked_key`, and the key itself, so no GET is needed for the `mcp_url`.
- Scoping a key to one customer: unverified. The SDK has `access_tags` on `CreateAgentKeyRequest` and `__acl_field_mappings__` on `ContextModel` (shape undocumented), and the docs say access tags filter what a key can see. Until it's tested, Chapter 3 says the key can carry access tags, but this data model declares no ACL mapping, so the identity line in the prompt is the only scope the demo shows.
- `json_request` on `json_session(headers={"X-API-Key": agent_key})`, which has no base URL, works with the absolute `mcp_url` (200, gzip JSON over HTTP/2). `recording()` captures MCP calls made inside `answer_with_tools` in order; `Exchange.path` is the full URL and holds no headers, so the agent key never lands in the record.
- The account holds 826 surfaces, so cleanup by name walks 17 listing pages. Delete plus the walk took 4.3 s. The service drops its indexes on `DELETE` but leaves the 20 documents.
- With the new data (Iteration 5): Create imports Customer 6, Product 8, Order 8 (region `amer` accepted) in about 1.8 s (the create POST 473 ms). `tools/list` is 21 tools and 34,684 characters. Priya's document is `{"customer_id": "C001", "lifetime_value": 4200.0, "name": "Priya Shah", "region": "amer", "tier": "gold"}`. `filter_order` on `customer_id` C001 returns O1001 (P102, shipped, 399) and O1002 (P104, delivered, 89); C002 returns O1003 (P101, delivered, 249) and O1004 (P100, returned, 129). Results carry only the `sku`, so naming a product takes `get_product_by_id`. `tools/call` took 206 to 360 ms with a new session per call (89 to 118 ms with a reused session).
- Tom's question is "When will the refund for my trail shoes reach my card?": `returns-refund` comes first (0.4639) on every index state tried. The plan's "Has the refund for my trail shoes gone through?" retrieves no refund article.
- KNN is approximate, and the index's history matters. After unlinking and reloading the articles into the same index, "Where's my tent? I leave for Maine on June 12." returned `care-tent`, `ship-express`, and `order-cancel`, losing `warranty-tent` (a brute force agrees with the fresh index). Chapter 1's stated numbers have matched on every check so far.

**Agent Memory**

- The SDK client has a 5-second timeout and no retries. A session's `ownerId` comes from its first event's `actorId`. A memory created without `memoryType` is filed as `semantic`, and creating an existing id replaces its text.
- Search returns no score, and one search takes about 300 ms. Extraction shows up two to five minutes after a turn. Session memory lives an hour, long-term memory 365 days.
- `get_session_memory_async` on a deleted session raises `errors.NotFoundErrorResponseContent`. `list_sessions_async(filter_owner_id=...).items` is a list of id strings.
- Recall threshold, measured through the notebook's `ams_request` (Iteration 6). At 0.3, Priya's "Which sleeping bag should I get for the trip?" recalls only "I always sleep cold", and "How do I wash my sleeping bag after the trip?" recalls nothing. The allergy appears for the first question below about 0.22 to 0.24 and for the second below about 0.21 to 0.22. The fixed value is now **0.15**, so both payoff questions recall cold, canoe, and the allergy, and the cat never comes back for either. Tom's memories never come back for Priya at any value tried. Agent Memory scores on its own scale, so 0.15 here isn't comparable to Chapter 1's 0.40 or Chapter 2's 0.85.
- Extraction runs on a five-minute tick (memories created at :x0:02 and :x5:02), so a fact appears anywhere from seconds to about five minutes after a turn. "I'm bringing my dog Juniper on the trip." became the episodic memory "User is bringing their dog Juniper on the canoe trip to Maine in June." It uses the seeded context and doesn't need the chat's model key.
- Timings: a session event write takes 247 ms, a recall search 294 to 329 ms, and a session read 82 ms. A turn spends about 0.8 s before the model call, and the pane adds about 0.4 s after it (two recall searches, one session read, one `rss_search`).
- marimo keeps a `mo.ui.chat`'s messages in the page when the cell that shows it reruns, even for a new chat object. So after a customer switch the old transcript stayed on screen and was sent as history. `am_pane` nests the chat one `mo.vstack` deeper per customer, which makes a switch mount a new chat. The `/` menu lists a chat's prompts alphabetically, not in the order given.

## How to work on the live kernel (this machine)

- Start the server from the project folder: `uv run --env-file .env marimo edit --host 0.0.0.0 --port 2718 --no-token --watch iris_basics.py`. The URL is `http://127.0.0.1:2718`.
- The skill scripts have CRLF line endings, and the `bash` first on PATH is WSL, which can't reach this machine's `127.0.0.1`. Use Git Bash, strip the carriage returns, and put jq on PATH. The first call, from PowerShell:

  ```powershell
  & "C:\Program Files\Git\bin\bash.exe" -lc 'export PATH="/c/projects/.vendor/jq:$PATH"; sed "s/\r$//" /c/work/redis/redis-iris-walkthrough/.agents/skills/marimo-pair/scripts/execute-code.sh | bash -s -- --url http://127.0.0.1:2718 -c "import marimo._code_mode as cm; help(cm)"'
  ```

  After that, write each script to `%LOCALAPPDATA%\Temp` and pass its Git Bash path (for example `/c/Users/Nick/AppData/Local/Temp/edit.py`) as the last argument instead of `-c "..."`.
- On Nick's machine (`C:\Users\NickCooper`, checked 2026-10-16) the paths above differ: the skill is vendored at `.agents/skills/marimo-pair/` with LF endings, Git Bash is the Bash tool's `bash`, and jq is at `$(cygpath "$LOCALAPPDATA")/Microsoft/WinGet/Packages/jqlang.jq_Microsoft.Winget.Source_8wekyb3d8bbwe`. The user config sets `auto_instantiate = false`, so a new session leaves every cell `stale` until something runs them. Helper scripts in `%LOCALAPPDATA%\Temp\iris-story\` wrap all of it: `nb_cycle.sh` (stop, start the server with `--headless`, hold one headless Edge tab open with Playwright, `help(cm)`, run every cell, print statuses), `kx.sh` (execute-code with jq on `PATH`), `status.py`, and `nbtext.py` (cell outputs as plain text). Launch PowerShell scripts from Git Bash with `-ExecutionPolicy Bypass`, use the absolute `uv.exe` path there, and send their output to a file, or the server inherits the caller's pipe and the call never returns.
- Never edit `iris_basics.py` on disk while a session runs. `--watch` and the kernel's saves overwrite it.
- Read a cell's code inside the same `cm.get_context()` before editing it, and keep edits that move a name between cells in one context. An exception inside the context discards every queued edit.
- `ctx.cells[name].code` is the cell body without its `def` wrapper, so match edits against that, not the file.
- Set one UI value per kernel call (`ctx.set_ui_value`) and read the result in the next. A chat's value can only come from the browser, so chat turns need a browser check.

## Progress log

- **2026-10-16.** Evaluated the notebook against the goals (baseline above) and proposed the story. Nick approved it with the four decisions above. Plan written; no notebook changes yet.
- **2026-10-16, Iteration 0.** Branch `story-arc` from `3ccf7f7`. Server started on Nick's machine with a headless Edge tab; after a run-all (the user config has `auto_instantiate = false`), all 67 cells reached `idle`. Store baseline at 15:30: `DBSIZE` 34, every key `memory:*` (the Agent Memory service's own); no keys under `cairn:20261021:`; no surfaces named `cairn-20261021`; no memories or sessions for owners `Tom`, `Priya`, `C001`, or `C002`; the LangCache ledger empty (entries can't be listed). `FT._LIST` also holds two empty hash indexes, `idx:cairn:20261021:help` and a leftover `idx:cairn:8ef77075:help` from an older run tag, plus the `langcache:*` and `memory:*` service indexes. Clean up everything should return to this, minus `idx:cairn:20261021:help`, which it drops.

- **2026-10-16, Iterations 1 and 2.** Done by a workflow of Sonnet agents (implementers, then one reviewer per checkpoint, a fixer, and a re-review), all PASS WITH NOTES. `answer_with_tools` is in `cairn_support/llm.py` with the three providers' loops; `tests/test_llm_tools.py` covers tool conversion and each loop against fake clients, and `uv run pytest -q` gives 40 passed. Gemini's request was checked offline at the wire; Anthropic and OpenAI over mocked transports. Context Retriever ran live through the notebook's own Create (facts above), then was cleaned up: no surface named `cairn-20261021`, no record keys, no entity indexes. The live model-chosen round trip did NOT run, because the `.env` OpenAI key is revoked. With a scripted stand-in model, `answer_with_tools` drove real tool calls against the live MCP endpoint and `recording()` captured them. The data now matches "Data changes", with three exceptions. `GEAR_CARE_ANSWERS`, `THRESHOLD_PROBES`, and `CACHE_ASIDE_SCRIPT` stay until Iteration 4, because the old LangCache column imports them and every iteration ends with the notebook running. The bags are "Down Sleeping Bag 20F" ($289) and "Synthetic Sleeping Bag 30F" ($189), so "I always sleep cold" pulls toward the down bag and only the allergy rules it out. `care-synthetic` is dated 2023, which keeps it out of the year-floor example. After a server restart, all 67 cells are `idle` on the new data. For later iterations: add `amer` to the region values on `Customer` and `Order`. Stop saying the agent key expires in an hour. Give Chapter 1's `$EPSILON` claim "can". Note that "Has the refund for my trail shoes gone through?" doesn't retrieve `returns-refund`.

- **2026-10-16, Iteration 3.** Four Sonnet drafters wrote every chapter offline as fragments, against a contract that fixes the cross-chapter names, signatures, prompt text, and rerun rules. A Sonnet integrator then applied column 0 and Chapter 1 with the server stopped, restarted it, and checked the done-when live: Create, the three presets, both sliders, a second Create, and the guards before Create. Every number matched. A Sonnet reviewer gave PASS WITH NOTES, all minor: start the year slider where the two sides differ, move the inline doc links into the chapter's accordion, keep "floor" for similarity only, say "embedding model" in Chapter 1, fix one code comment, and show a hint when the articles have expired. They are applied with Iteration 4. Deviations from the plan's table: column 1 has 11 cells, because `rs_ends` holds the Ends on and the docs accordion after the last table. `rs_create` sits above `rs_search_fn`, so the status line is under the button. `rs_search_fn` also exports `ft_search_rows`, and `rs_render` exports `ft_search_markdown_table`. The old `lc_chat` lost its `recording` import, which the imports cell now owns. Metrics: 64 cells, 49 hidden, 15 shown, 2,440 non-blank lines, 7 `mo.state`, 2 chats. Column 0 is 131 lines and column 1 is 382. The drafts put Chapter 2 at 258, Chapter 3 at 435, and Chapter 4 at 346, which totals 1,552. So Chapter 3 has to lose about 50 lines and Chapter 4 about 10 to reach 1,500.

- **2026-10-16, Iteration 4.** A Sonnet polisher applied the seven Iteration 3 notes to Chapter 1. A Sonnet integrator applied Chapter 2 and removed `GEAR_CARE_ANSWERS`, `THRESHOLD_PROBES`, and `CACHE_ASIDE_SCRIPT` from `sample_data.py`. With the key still revoked, it verified the cache with the real LangCache and Search and with a stand-in model. That run gave: question 1 misses and is stored; question 2 hits at 0.905 with no model call; question 3 is served the down answer at 0.950; at 0.96 both miss, each showing its nearest stored prompt; "Where's my tent?" misses at about 0.62 and is stored; Clear deletes every ledger id (204). The first review FAILED with three majors. (1) Asking the same question twice did nothing in a browser: an unchanged `mo.ui.form` resubmit doesn't rerun. (2) The answer rendered about 2,400 px below the button. (3) A miss on a digit prompt said "the cache has nothing stored yet" when entries were stored. A Sonnet fixer applied these changes. The inputs are now a `.batch` with an Ask `run_button` beside Clear. The cells run `lc_title`, `lc_ask`, `lc_connect`, `lc_answer_fn`, `lc_ends`, so the result sits under the buttons and the Ends on follows it. The miss line says only what is known. The fixer verified the whole sequence with real clicks through the headless tab. The re-review gave PASS WITH NOTES (minor: say "note the total time" before switching questions; a gentler first-miss line; a line when no articles came back; Chapter 1's floor and filter cells also need the expired-articles hint). `lcs_answer` also refuses to call the model when `rss_search` returns nothing, so an expired help center can't put an article-less answer in the shared cache. Metrics: 54 cells, 2,096 non-blank lines, 4 `mo.state`, 1 chat. Column 1 is 385 lines and column 2 is 258 in 5 cells. Chapter 3 may have 390 and Chapter 4 335.

- **2026-10-16, Iteration 5.** A Sonnet polisher applied the three Chapter 2 re-review notes. A Sonnet preparer moved Chapter 3 to the run-button pattern, with the cells `cr_title`, `cr_model_fns`, `cr_model`, `cr_create`, `cr_connect`, `cr_lifecycle`, `cr_answer_fn`, `cr_ask_md`, `cr_ask`, and a new `cr_ends`. It cut the chapter from 435 to 381 lines, and checked it with an offline harness of 69 checks against fake services. A Sonnet integrator applied both and verified Chapter 3 live. Create works on the new data. The system prompt shows on screen. With the key revoked, a scripted stand-in model drove `filter_order` calls against the live surface and `cr_ask` showed them. The Ask button reruns on real clicks, twice in a row too. Every stop line fires: no surface, no index, expired articles, a second Create. Clean up leaves no surface, record key, or entity index. Rerun safety was checked by identity: `crs_answer` and `crs_list_tools` are the same objects after Create and Clean up, so Chapter 4's chat won't reset. The Sonnet review gave PASS WITH NOTES, all minor; they go to Iteration 6's prep. Also: the expiry prose no longer promises an hour, Tom's question changed (facts above), and `$` in model answers is escaped. Metrics: 47 cells, 33 hidden, 14 shown, 1,738 non-blank lines, 3 `mo.state` (one is Chapter 3's, two are the old column 4's), 1 chat. Column 3 is 379 lines in 10 cells; columns 0 to 3 total 1,153, which leaves Chapter 4 at most 347.

- **2026-10-16, Iteration 6.** A Sonnet polisher applied the ten Chapter 3 notes, and Chapter 3 now reuses one MCP session per agent key. A Sonnet preparer reordered Chapter 4 so each result sits under its button and cut it to 309 lines. The epilogue's Clean up text now says the surface never expires and holds the password. A Sonnet integrator applied every remaining column, which retired the last old column. Nothing in column 0 could be pruned, because every import is still used. It verified live: Seed (4 and 3 memories, idempotent); a real typed chat turn as Priya, which writes the session event, recalls, and ends with the one-line error on the dead key; the pane's five blocks; the customer switch (new session id, empty chat, Tom recalls only Tom); background extraction; and Clean up everything back to the baseline (DBSIZE 34; only `idx:cairn:20261021:help` is gone, as intended). It changed the recall threshold to 0.15 and fixed the stale chat transcript after a switch (facts above). With a stand-in model, both payoff questions reached the model with the allergy in the prompt, and `recording()` captured the tool calls across the chat's yields. Rerun safety was checked by identity across every button in the notebook. The Sonnet review gave PASS WITH NOTES, all minor; Iteration 7 applies them. Metrics: **42 cells, 29 hidden, 13 shown, 1,471 non-blank lines, 1 `mo.state`, 1 `mo.ui.chat`**. Display calls per turn: two Agent Memory searches and a session read, plus one `rss_search` to rebuild the prompt (Redis, not a service call).

- **2026-10-16, Iteration 7.** It ran as a single workflow.
  - **Polish.** A Sonnet polisher applied the Iteration 6 notes:
    - Extraction timing reads "within about five minutes".
    - The three Chapter 1 searches and the Chapter 4 chat say "press **Create**" when the index is missing, instead of raising.
    - Every step heading is now "## Step N.", "the assistant" is used throughout, and `textwrap` is gone from the imports.
    - Dead package code is deleted: `formatting.py`, the `Surface` and `ToolCall` types, and four unused `redis_search_replies` helpers with their tests.
  - **Teaching pass.** Three Sonnet cold readers each used one lens: the story (19 findings), consistency (11), and the screen through the headless tab (9). One Sonnet fixer merged and applied them:
    - Redis commands are introduced as Redis commands.
    - The prologue says how to run the chapters, starting with **Create**.
    - Chapter 1 says "embedding model", so "the model" means the chat model everywhere else.
    - Chapter 3's picker adds the sleeping-bag question, so its closing gap is seen, not told.
    - The Try it notes that the `/` menu sorts the starter prompts alphabetically.
    - A new `iris_basics.css`, wired through `[tool.marimo.display] custom_css` in `pyproject.toml`, lifts marimo's 610 px output cap and wraps long command lines.
    - A small `rss_text` helper renders prompts without stray blank lines.
  - **README.** A Sonnet writer rewrote `README.md` for the story: 109 lines on the chapters, setup, what the notebook writes and how it cleans up, and troubleshooting.
  - **Final review.** A Sonnet whole-branch review gave PASS WITH NOTES. The coordinator applied the cheap ones: the README's model-key and Restart sentences, the `.env.example` LLM header, the `pyproject.toml` description, one docstring, and a pane line naming the missing index (checked live).
  - **End state.** `marimo check --strict` exits 0 and `uv run pytest -q` gives 38 passed. The stores are at the baseline after Clean up everything: DBSIZE 34, no `cairn:20261021:` keys, no surface, no memories or sessions for the cast, and an empty ledger. The final metrics are in the table above.
  - **Still open.** No real model answer has been seen in Chapters 2 to 4, and Iteration 1's live round trip hasn't run, because the `.env` OpenAI key is revoked. Before publishing, put a working key in `.env`, restart the server, and run each chapter's Try it once. That also checks that OpenAI accepts the 21 tool schemas; they carry top-level `if`/`then`/`else`. Also open: Chapter 4's prose never shows the session-event request, which appears only in the shown `ams_turn` code; the notebook has no line to spare for it.

- **2026-10-17, the session-memory step.** Nick asked for Chapter 4's session write to be shown like its other requests, making room by simplifying elsewhere if possible. Chapter 4 now has four steps. Step 2, "Write each message to session memory", shows the `POST /session-memory/events` body: `sessionId`, `actorId`, `role` `USER`, `content` `[{"text": ...}]`, and `createdAt` ending in `Z`, with the field names checked against the SDK's own serializer. Four Reading it bullets follow, and the extraction watch-out moved under the step; recall is Step 3 and the chat Step 4. The step costs 18 lines. Four Sonnet scouts proposed measured cuts, and the coordinator took four that lose no teaching point, saving 23 lines. In c1-4, `rs_title` defines `FT.CREATE` once as `rss_create_command` and `rs_create` sends it, so the command shown and the command sent can't drift apart. In c1-5, `rs_filter` names its two tables. In c3-1, `cr_create` ends with one `mo.stop` for the empty state. In c3-4 and c3-5, the surface setter comes straight from `mo.state`. Declined: 32 lines of callout reformatting, dropping type hints from shown code, edits to `env_check` and the imports comments, and comment-only cuts. A Sonnet integrator verified it live. The first Create built a fresh index from the shared command, and the shown and sent commands match character for character. Chapter 3's empty, held, and cleaned states show as before, and the turn functions keep their identity. A real typed chat turn stored an event matching the prose JSON, which also confirmed that a session takes its `ownerId` from its first event, and Clean up everything restored the baseline. The Sonnet review gave PASS WITH NOTES. The coordinator applied three minor notes: the learning list now follows the step order, Step 2's lead-in names Priya's question, and `_step` became `_recall`. Exception to contract rule 2: `rs_create` reads `rss_create_command` from `rs_title`, the cell that already defines its button, so no new rerun trigger is added. Metrics: 42 cells, 29 hidden, 13 shown, **1,495** non-blank lines, 1 `mo.state`, 1 chat; `marimo check --strict` exits 0 and 38 tests pass.

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
