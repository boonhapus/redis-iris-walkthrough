import marimo

__generated_with = "0.24.2"
app = marimo.App(width="columns", app_title="Redis Iris basics")


@app.cell(column=0)
def _():
    from collections.abc import AsyncGenerator, Callable
    import asyncio
    import datetime as dt
    import html
    import json
    import os
    import uuid

    import redis

    from marimo import ai
    import marimo as mo

    return AsyncGenerator, ai, asyncio, dt, html, json, mo, os, redis, uuid


@app.cell
def redis_client(os, redis):
    REDIS_CLIENT = redis.Redis.from_url(
        os.environ["REDIS_URL"],
        protocol=2,               # flat FT.* replies: every reply shape here was verified live under RESP2
        decode_responses=True,    # strings back; the vector field is never read through a core command
        health_check_interval=30,
    )

    AI_MODEL = "gpt-4o-mini"
    return (AI_MODEL,)


@app.cell
def llm_client(AI_MODEL, AsyncGenerator, ai, os):
    if AI_MODEL.startswith("gpt"):
        from openai import AsyncOpenAI

        llm = AsyncOpenAI(api_key=os.environ.get("LLM_API_KEY", "missing"))  # a missing key fails at the first chat turn, not here

        async def stream_llm(
            system_prompt: str, messages: list[dict[str, str]], config: ai.ChatModelConfig
        ) -> AsyncGenerator[str, None]:
            """Stream the response from OpenAI."""
            stream = await llm.chat.completions.create(
                model=AI_MODEL,
                messages=[{"role": "system", "content": system_prompt}, *messages],
                max_tokens=config.max_tokens or 4096,
                stream=True,
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content


    if AI_MODEL.startswith("claude"):
        from anthropic import AsyncAnthropic

        llm = AsyncAnthropic(api_key=os.environ.get("LLM_API_KEY"))  # None lets the SDK look for its own credentials

        async def stream_llm(
            system_prompt: str, messages: list[dict[str, str]], config: ai.ChatModelConfig
        ) -> AsyncGenerator[str, None]:
            """Stream the response from Anthropic."""
            async with llm.messages.stream(
                model=AI_MODEL,
                system=system_prompt,
                messages=messages,
                max_tokens=config.max_tokens or 4096,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
    return (stream_llm,)


@app.cell(column=1, hide_code=True)
def rs_title(mo):
    mo.md(r"""
    # Redis Search

    Customers ask in their own words, and a keyword index finds only the words your articles use.
    Redis Search keeps text, tag, numeric, and vector fields in one index over your hashes.
    Load 36 Cairn Outfitters help articles, then ask the help center, filter it, and add an article to see three principles:

    1. **An index follows every write under its prefix.** `FT.CREATE` names a key prefix once, and Redis indexes each `HSET` under it as part of the write, so no sync job runs.
    2. **Redis fuses keyword and vector rankings in one command.** Keyword and vector search fail on different questions, and `FT.HYBRID` merges the two with Reciprocal Rank Fusion (RRF).
    3. **Filters run inside the vector search.** Redis picks the k nearest articles from those that pass a tag or numeric filter, so it returns fewer than k only when fewer pass.

    Redis Search is available in Redis Open Source, Redis Software, and Redis Cloud, and `FT.HYBRID` needs Redis 8.4 or later. Settings: `REDIS_URL`, plus `LLM_API_KEY`, which the chat and the cell under it need.
    Docs: [Redis Search](https://redis.io/docs/latest/develop/ai/search-and-query/), [vectors and filters](https://redis.io/docs/latest/develop/ai/search-and-query/vectors/), and [`FT.HYBRID`](https://redis.io/docs/latest/commands/ft.hybrid/).

    Each line reads source, target, and what passes between them:

    ```text
    App   -> FT index  FT.CREATE over the prefix cairn:<run>:help:
    App   -> vector    potion-base-8M embeds on this machine
    App   -> hash      HSET title, body, category, year, and embedding
    hash  -> FT index  indexed as part of the HSET
    App   -> FT index  FT.HYBRID: BM25STD and KNN, fused with RRF, the filter inside both
    App   -> Claude    the top 3 articles and the question, for the answer
    ```
    """)
    return


@app.cell(column=2, hide_code=True)
def lc_title(mo):
    mo.md(r"""
    # Redis LangCache

    Customers rarely ask the same question the same way twice, and an exact-match cache misses every rewording.
    Redis LangCache matches prompts by meaning, so a reworded question can get a stored answer without an LLM call.
    Store five Cairn Outfitters gear-care answers, then ask Cairn support below and move the threshold to see three principles:

    1. **Your app searches before the LLM and stores after it.** LangCache embeds every prompt itself, and your app calls the LLM only on a miss and then stores the answer.
    2. **The threshold trades wrong answers for misses.** LangCache serves any entry whose similarity clears `similarityThreshold`, so a question that only sounds similar can get a wrong answer.
    3. **A cached answer lives until its time to live (TTL) runs out.** Until `ttlMillis` passes or your app deletes it, LangCache serves the answer unchanged, so the TTL caps how stale it gets.

    LangCache is in public preview on Redis Cloud. Settings: `LANGCACHE_HOST` (with `https://`), `LANGCACHE_CACHE_ID`, and `LANGCACHE_API_KEY`, plus `LLM_API_KEY`, which the chat needs on a miss. Docs:
    [LangCache](https://redis.io/docs/latest/develop/ai/context-engine/langcache/),
    [concepts](https://redis.io/docs/latest/develop/ai/context-engine/langcache/concepts/),
    [API and SDK examples](https://redis.io/docs/latest/develop/ai/context-engine/langcache/api-examples/), and
    [API reference](https://redis.io/docs/latest/develop/ai/context-engine/langcache/api-reference/).

    Each line reads source, target, and what passes between them:

    ```text
    App       -> LangCache        POST /entries/search with the prompt text
    LangCache -> embedding model  the prompt, embedded on the service side
    LangCache -> App              data: [entries above the threshold], or data: []
    App       -> Claude           the question, only when data is empty
    App       -> LangCache        POST /entries with the answer and ttlMillis
    ```
    """)
    return


@app.cell(column=3, hide_code=True)
def cr_title(mo):
    mo.md(r"""
    # Redis Context Retriever

    An agent that writes its own queries can run any query your database allows, including one planted in a ticket it reads.
    Redis Context Retriever gives the agent a fixed set of MCP tools instead, generated from a data model you declare once.
    Create a surface from three Cairn classes, then call its tools from the console to see three principles:

    1. **The data model generates the tools.** Three Python classes become the MCP tools the surface lists, and each field's `index` kind decides which tools exist.
    2. **The agent can call only what the model defines.** A console sends the tool and JSON arguments you choose, with three presets that step outside the model, and a table shows each answer.
    3. **Context Retriever is a layer on top of your Redis.** It writes JSON documents and one index per entity into your database, and the documents stay after Clean up deletes the surface.

    Context Retriever is in preview on Redis Cloud, and it does not support Redis Flex or Active-Active databases.
    Settings: `CTX_ADMIN_KEY`, plus a `REDIS_URL` for a scratch database with Search and JSON that the hosted service can reach.
    Create sends that database's password to the service, which keeps it until Clean up deletes the surface.
    Docs: [Context Retriever](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/) and
    [concepts](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/concepts/).

    Each line reads source, target, and what passes between them:

    ```text
    Python classes  -> data_model JSON  export_data_model, in this kernel
    data_model JSON -> surface          POST /context-surfaces, with your database password
    surface         -> your Redis       JSON documents, one index per entity, a read per tool call
    notebook        -> surface          tools/list once, then one tools/call per console submit, with an agent key
    ```
    """)

    return


@app.cell(column=4, hide_code=True)
def am_title(mo):
    mo.md(r"""
    # Redis Agent Memory

    An agent that forgets a customer's two dogs between conversations asks about them again every time.
    Redis Agent Memory keeps each conversation in session memory and durable facts in long-term memory, written by the app or extracted from session events.
    Select Seed to write memories for two Cairn Outfitters customers, Tom and Priya, then chat as either to see three principles:

    1. **Long-term memory recalls by meaning.** "Which foods should I avoid?" shares no keyword with "I'm allergic to peanuts and tree nuts", yet the search returns it for Priya. The search is a vector search, and `similarityThreshold` is its cosine similarity floor, the same name the LangCache column tunes.
    2. **The `ownerId` filter scopes recall to one customer.** The app sends `filter: {ownerId: ...}` with every search, so Tom's agent does not see Priya's allergy.
    3. **The app builds the prompt.** Each turn writes the message to session memory, searches long-term memory, and puts what it recalls in the system prompt.

    Agent Memory is in public preview on Redis Cloud. Settings: `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY`, plus `LLM_API_KEY` for the chat.
    Docs: [Agent Memory](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/overview/),
    [Python SDK quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/), and
    [developer guide](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/developer-guide/).

    Each line reads source, target, and what passes between them:

    ```text
    App        -> long-term   POST /long-term-memory, the Seed memories, each with an ownerId
    App        -> session     POST /session-memory/events, the user's message, every turn
    App        -> long-term   POST /long-term-memory/search: text, similarityThreshold, ownerId
    long-term  -> App         the memories at or above the threshold, best first, no score
    App        -> LLM         the system prompt with the recalled memories, and the history
    session    -> long-term   extraction in the background, a few minutes after a turn
    ```
    """)
    return


@app.cell(hide_code=True)
def am_store_button(mo):
    ams_seed_button = mo.ui.run_button(label="Seed")
    ams_refresh_button = mo.ui.run_button(label="Refresh")
    ams_cleanup_button = mo.ui.run_button(label="Clean up", kind="danger")

    mo.vstack(
        [
            mo.md(r"""
    ## Seed and list two customers' long-term memories

    Seed writes three memories per customer with `POST /long-term-memory`, each tagged with the `ownerId` the app chooses.
    Refresh lists them again, including anything the store has extracted from a session a few minutes after a turn.
    Seeds carry type `semantic`. An extracted memory carries the type the extractor chooses, such as `episodic`.
    Clean up deletes every session and every long-term memory tagged Tom or Priya.
    """),
            mo.hstack([ams_seed_button, ams_refresh_button, ams_cleanup_button], justify="start"),
        ]
    )
    return ams_cleanup_button, ams_refresh_button, ams_seed_button


@app.cell(hide_code=True)
async def am_store(
    ams_chatbot,
    ams_cleanup_button,
    ams_forget,
    ams_memories,
    ams_memory_table,
    ams_refresh_button,
    ams_seed,
    ams_seed_button,
    ams_seeds,
    mo,
):
    _note = ""
    if ams_seed_button.value:
        _ids = await ams_seed()
        _note = f"Wrote {len(_ids)} new memories to long-term memory." if _ids else "Every seed memory is already in long-term memory."
    if ams_cleanup_button.value:
        _sessions, _memories = 0, 0
        for _owner in ams_seeds:
            _deleted_sessions, _deleted_memories = await ams_forget(_owner)
            _sessions += len(_deleted_sessions)
            _memories += len(_deleted_memories)
        _note = f"Deleted {_memories} memories and {_sessions} sessions for {' and '.join(ams_seeds)}."
    _ = ams_refresh_button.value  # Refresh lists again, for memories extracted in the background
    _ = ams_chatbot.value  # and so does each chat turn, so the tables agree with the bars

    ams_store = {}
    for _owner in ams_seeds:
        ams_store[_owner] = await ams_memories(_owner)

    mo.vstack(
        ([mo.md(_note)] if _note else [])
        + [
            mo.hstack(
                [ams_memory_table(_owner, _found) for _owner, _found in ams_store.items()],
                widths="equal",
                align="start",
            )
        ]
    )
    return (ams_store,)


@app.cell(hide_code=True)
def am_owner(ams_seeds, mo):
    ams_owner = mo.ui.dropdown(options=list(ams_seeds), value=next(iter(ams_seeds)), label="Chat as")  # displayed in the workbench, upstream of the chat so a switch starts a new session

    mo.md(r"""
    ## Chat with the Cairn agent as Tom or Priya and watch the recall

    Type `/` in the chat box to pick a starter message. Send the first one as Tom. The agent has nothing on food, and the Priya bars show the allergy the `filter` kept out.
    Send the next two as Tom, and his dogs and his Dolomites trip appear in the system prompt.

    Switch to Priya, which starts a new conversation, and send the same three. The foods question now recalls the allergy, with no keyword shared.
    Move `similarityThreshold` and watch memories enter and leave the system prompt.

    Send the packing question next. Alone it is a weak match, so set N to 2 and the trip question rides along in the search text.
    The last message tells the agent something new. A few minutes later, Refresh can list a memory the store extracted from it.
    """)
    return (ams_owner,)


@app.cell(hide_code=True)
def am_workbench(
    ams_bars,
    ams_build_system,
    ams_chatbot,
    ams_owner,
    ams_request,
    ams_session_id,
    ams_threshold,
    ams_turn_bands,
    ams_turn_events,
    ams_turn_live,
    ams_turn_query,
    ams_turns,
    get_threshold,
    get_turns,
    json,
    mo,
):
    _t = round(get_threshold(), 2)
    _n = get_turns()
    _owner = ams_owner.value
    _shown = min(_n, sum(m.role == "user" for m in ams_chatbot.value))
    _recalled = [text for text, band in ams_turn_bands[_owner].items() if round(band, 2) >= _t]

    _data = [
        ams_turns,
        mo.md(
            ("**Search text**, the last user message:" if _shown == 1 else f"**Search text**, the last {_shown} user messages, oldest first:")
            if ams_turn_live
            else "**Search text**, the first starter message until you send one:"
        ),
        mo.md(f"```text\n{ams_turn_query}\n```"),
        mo.md(
            "Each bar is the highest `similarityThreshold` that returns the memory, found by running this search at 21 thresholds. "
            "The API returns no score. Dark bars are at or above the slider's line, and those memories go into the system prompt."
        ),
        ams_threshold,
        mo.md(f"**ownerId {_owner}**, what the `filter` returns"),
        ams_bars(ams_turn_bands[_owner], _t),
    ]
    for _other in (o for o in ams_turn_bands if o != _owner):
        _data += [mo.md(f"**ownerId {_other}**, the same search for comparison. The agent never sends it."), ams_bars(ams_turn_bands[_other], _t)]
    _data += [
        mo.md("---"),
        mo.md("Moving a slider redraws this pane without a new turn."),
        mo.md("**Search request**, from `ams_request`"),
        mo.md(f"```json\n{json.dumps(ams_request(ams_turn_query, _owner, _t), indent=2)}\n```"),
        mo.md("**System prompt**, from `ams_build_system`"),
        mo.md(f"```text\n{ams_build_system(_owner, _recalled)}\n```"),
        mo.md(
            f"**Session `{ams_session_id}`**, the user messages the app wrote ({len(ams_turn_events)})"
            if ams_turn_events
            else f"**Session `{ams_session_id}`**, no messages written yet"
        ),
    ]
    if ams_turn_events:
        _data.append(mo.md("\n".join(f"- {text}" for text in ams_turn_events)))

    mo.hstack(
        [
            mo.vstack([ams_owner, ams_chatbot]),
            mo.vstack(_data),
        ],
        widths=[5, 6],
        gap=1.5,
        align="start",
    )
    return


@app.cell(hide_code=True)
def am_build_md(mo):
    mo.md(r"""
    ## The Agent Memory code: client, store, prompt, turn

    One cell per step, with the code visible in `marimo edit`. In `marimo run`, pick **Show code** from the app menu.
    Every call on `ams_client` goes to Agent Memory, and the rest is the app. Seed and Clean up call the store functions, and each chat turn calls `ams_turn`.
    """)
    return


@app.cell
def am_connect(os):
    # Client. Three settings from .env, and one object for every Agent Memory call below.
    from redis_agent_memory import AgentMemory, errors, models


    ams_client = AgentMemory(
        os.environ["MEMORY_API_BASE_URL"],
        store_id=os.environ["MEMORY_STORE_ID"],
        api_key=os.environ["MEMORY_API_KEY"],
    )
    return ams_client, errors, models


@app.cell
def am_store_fns(ams_client, asyncio, models):
    # Store. Seed, list, and delete the long-term memories of two customers.
    ams_seeds = {
        "Tom": [
            "I've got two dogs, Max and Cece",
            "I wear size 11 trail shoes",
            "I'm planning a hiking trip in the Dolomites in August",
        ],
        "Priya": [
            "I have a cat named Miso",
            "I'm allergic to peanuts and tree nuts",
            "I'm planning a canoe trip in Maine in June",
        ],
    }


    async def ams_memories(owner: str) -> list[models.MemoryRecord]:
        """Every long-term memory whose `ownerId` is `owner`, up to 100. With no `text`, the search endpoint lists by filter alone."""
        r = await ams_client.search_long_term_memory_async(
            request={"filter": {"ownerId": {"eq": owner}}, "limit": 100}
        )
        return r.items


    async def ams_seed() -> list[str]:
        """Write memories straight to long-term memory, each tagged with the `ownerId` the app chooses (the write side of principle 2).
        The ids are the app's too, so a retry or a second Seed rewrites the same records and creates nothing new."""
        created: list[str] = []

        for owner, texts in ams_seeds.items():
            before = {m.id for m in await ams_memories(owner)}
            memories = [
                {"id": f"{owner.lower()}-seed-{i}", "text": text, "ownerId": owner}  # no memoryType, so the store files it as "semantic"
                for i, text in enumerate(texts, 1)
            ]
            await ams_client.bulk_create_long_term_memories_async(memories=memories)
            created += [m["id"] for m in memories if m["id"] not in before]

        return created


    async def ams_forget(owner: str) -> tuple[list[str], list[str]]:
        """Delete every session and every long-term memory about `owner`. Returns the session ids and the memory ids deleted."""
        sessions = await ams_client.list_sessions_async(filter_owner_id=owner)
        await asyncio.gather(*(ams_client.delete_session_memory_async(session_id=s) for s in sessions.items))

        memory_ids = [m.id for m in await ams_memories(owner)]
        if memory_ids:
            await ams_client.bulk_delete_long_term_memories_async(memory_ids=memory_ids)

        return sessions.items, memory_ids

    return ams_forget, ams_memories, ams_seed, ams_seeds


@app.cell
def am_prompt_fns(ai):
    # Prompt. The search text, the search request, and the system prompt the app builds.
    def ams_query(messages: list[ai.ChatMessage], n: int) -> str:
        """The text to search with: the last `n` user messages, in order."""
        user_messages = [str(m.content) for m in messages if m.role == "user"]
        return "\n".join(user_messages[-n:])


    def ams_request(query: str, owner: str, threshold: float) -> dict:
        """The search request. `text` and `similarityThreshold` match by meaning (principle 1), `filter` scopes recall to one `ownerId` (principle 2), and `limit` caps the results at ten."""
        return {
            "text": query,
            "similarityThreshold": threshold,
            "filter": {"ownerId": {"eq": owner}},
            "limit": 10,
        }


    def ams_build_system(owner: str, facts: list[str]) -> str:
        """The system prompt. The app, not Agent Memory, decides what the model sees (principle 3)."""
        system = "You are the Cairn Outfitters support agent. Answer the customer in two or three sentences."

        if facts:
            system += f"\n\nWhat you remember about {owner}:\n" + "\n".join(f"- {fact}" for fact in facts)

        return system

    return ams_build_system, ams_query, ams_request


@app.cell
def am_turn_fn(
    AsyncGenerator,
    ai,
    ams_build_system,
    ams_client,
    ams_owner,
    ams_query,
    ams_request,
    ams_settings,
    dt,
    models,
    stream_llm,
    uuid,
):
    # Turn. One chat turn: the session write, the long-term search, and the prompt.
    ams_session_id = f"{ams_owner.value.lower()}-{uuid.uuid4().hex[:12]}"  # a new session each time the customer changes


    async def ams_turn(messages: list[ai.ChatMessage], config: ai.ChatModelConfig) -> AsyncGenerator[str, None]:
        """One agent turn: write the message to session memory, search long-term memory, and put what it recalls in the system prompt."""
        # Notebook inputs: the customer from the dropdown, and the slider values through ams_settings() (plumbing, not an Agent Memory call).
        owner = ams_owner.value
        threshold, turns = ams_settings()

        # Session memory: the user's message, as an event in this session.
        # The first event's actorId becomes the session's ownerId, and the memories the store extracts from the session carry it.
        # Extraction runs a few minutes later. The prompt below never reads these events.
        await ams_client.add_session_event_async(
            session_id=ams_session_id,
            actor_id=owner,
            role=models.MessageRole.USER,
            content=[{"text": str(messages[-1].content)}],
            created_at=dt.datetime.now(dt.timezone.utc),
        )

        # Long-term memory: this owner's memories, by meaning, searched with the last N user messages.
        found = await ams_client.search_long_term_memory_async(request=ams_request(ams_query(messages, turns), owner, threshold))

        # The prompt: the app puts the recalled memories in front of the model.
        system = ams_build_system(owner, [m.text for m in found.items])
        history = [{"role": m.role, "content": str(m.content)} for m in messages if m.role in ("user", "assistant")]

        try:
            async for text in stream_llm(system, history, config):
                yield text
        except Exception as e:  # a model error must not hide the two Agent Memory calls that already ran
            yield f"The model call failed ({type(e).__name__}). The session write and the search still ran."

    return ams_session_id, ams_turn


@app.cell(hide_code=True)
def am_machinery_md(mo):
    mo.md(r"""
    ## Notebook plumbing: slider state, chat object, threshold sweep, renderers

    Four hidden cells follow: slider state, chat object, the threshold sweep behind the bars, and the renderers for bars and tables.
    Read `am_settings`, `am_chat`, `am_turn_sweep`, and `am_render` in `iris_basics.py`, or expand them in `marimo edit`.
    """)
    return


@app.cell(hide_code=True)
def am_settings(mo):
    # Slider state. The sliders live in am_chat, so a turn cannot read their .value from inside the chat cell.
    # A cell that references these getters reruns on every move, so the turn reads them through ams_settings() instead.
    # am_chat reads the getters for the sliders' start values, and marimo exempts the cell that owns a control.
    get_threshold, set_threshold = mo.state(0.3)
    get_turns, set_turns = mo.state(1)


    def ams_settings() -> tuple[float, int]:
        """The sliders' current values, read at call time. A cell that calls this does not rerun when a slider moves."""
        return round(get_threshold(), 2), get_turns()

    return ams_settings, get_threshold, get_turns, set_threshold, set_turns


@app.cell(hide_code=True)
def am_chat(ams_turn, get_threshold, get_turns, mo, set_threshold, set_turns):
    # debounce=True: the setter fires when the slider is released, not on every step.
    ams_threshold = mo.ui.slider(
        start=0.0, stop=1.0, step=0.05, value=get_threshold(), show_value=True, debounce=True,
        label="similarityThreshold", on_change=set_threshold,
    )
    ams_turns = mo.ui.slider(
        start=1, stop=10, step=1, value=get_turns(), show_value=True, debounce=True,
        label="Search with the last N user messages", on_change=set_turns,
    )
    ams_prompts = [
        "Which foods should I avoid?",
        "What pets do I have?",
        "Where am I headed this summer?",
        "Any packing tips?",
        "I'm training for a marathon in October.",
    ]

    # Displayed beside the data in am_workbench. ams_turn is the Iris turn above.
    ams_chatbot = mo.ui.chat(ams_turn, prompts=ams_prompts, max_height=520)
    return ams_chatbot, ams_prompts, ams_threshold, ams_turns


@app.cell(hide_code=True)
async def am_turn_sweep(
    ams_chatbot,
    ams_client,
    ams_prompts,
    ams_query,
    ams_request,
    ams_session_id,
    ams_store,
    asyncio,
    errors,
    get_turns,
    models,
):
    async def ams_sweep(query: str, store: dict[str, list[models.MemoryRecord]]) -> dict[str, dict[str, float]]:
        """For each owner in `store`, the highest threshold that returns each memory. The API returns no score."""
        grid = [round(0.05 * i, 2) for i in range(21)]  # 0.00 to 1.00
        pairs = [(owner, t) for owner in store for t in grid]
        gate = asyncio.Semaphore(12)  # 42 searches per sweep; keep a dozen in flight

        async def recall(owner: str, t: float) -> list[models.MemoryRecord]:
            async with gate:
                r = await ams_client.search_long_term_memory_async(request={**ams_request(query, owner, t), "limit": 100})
                return r.items

        hits = await asyncio.gather(*(recall(owner, t) for owner, t in pairs))

        bands = {owner: {m.text: 0.0 for m in records} for owner, records in store.items()}
        for (owner, t), found in zip(pairs, hits):
            for m in found:
                bands[owner][m.text] = max(bands[owner].get(m.text, 0.0), t)

        return bands


    async def ams_session_texts(session_id: str) -> list[str]:
        """The user messages the app wrote to this session, or none when the session is gone (Clean up deletes it)."""
        try:
            session = await ams_client.get_session_memory_async(session_id=session_id)
        except errors.NotFoundErrorResponseContent:
            return []
        return [e.content[0].text for e in session.events]


    # The search text behind the last turn (the first starter prompt before any chat), how high a threshold each
    # memory clears for it, for every customer, and the session so far. Depends on the chat, N, and the store, not on the threshold.
    ams_turn_live = any(m.role == "user" for m in ams_chatbot.value)
    ams_turn_query = ams_query(ams_chatbot.value, get_turns()) if ams_turn_live else ams_prompts[0]
    ams_turn_bands = await ams_sweep(ams_turn_query, ams_store)
    ams_turn_events = await ams_session_texts(ams_session_id) if ams_turn_live else []
    return ams_turn_bands, ams_turn_events, ams_turn_live, ams_turn_query


@app.cell(hide_code=True)
def am_render(html, mo, models):
    def ams_bars(bands: dict[str, float], threshold: float) -> mo.Html:
        """One row per memory, in store order."""
        threshold = round(threshold, 2)
        rows = []
        for text, band in bands.items():
            state = "ams-bar" if round(band, 2) >= threshold else "ams-bar ams-bar-below"
            rows.append(
                '<div class="ams-row">'
                f'<div class="ams-label" title="{html.escape(text)}">{html.escape(text)}</div>'
                '<div class="ams-track">'
                f'<div class="{state}" style="width:{band * 100:.0f}%"></div>'
                f'<div class="ams-line" style="left:{threshold * 100:.0f}%"></div>'
                "</div>"
                f'<div class="ams-value">{band:.2f}</div>'
                "</div>"
            )
        ticks = '<div class="ams-row"><div></div><div class="ams-ticks"><span>0</span><span>0.5</span><span>1</span></div><div></div></div>'
        body = "".join(rows) + ticks if rows else '<div class="ams-empty">No memories yet. Select Seed to write them.</div>'
        css = """
    <style>
    .ams-bars { font: 13px system-ui, -apple-system, "Segoe UI", sans-serif; color: #52514e; }
    .ams-row { display: grid; grid-template-columns: minmax(0, 2fr) 3fr 3.2em; gap: 0 8px; align-items: center; margin: 6px 0; }
    .ams-label { overflow-wrap: anywhere; }
    .ams-track { position: relative; min-width: 0; height: 12px; background: #e1e0d9; border-radius: 0 4px 4px 0; }
    .ams-bar { position: absolute; left: 0; top: 0; height: 100%; background: #2a78d6; border-radius: 0 4px 4px 0; }
    .ams-bar-below { background: #9ec5f4; }
    .ams-line { position: absolute; top: -4px; height: 20px; width: 2px; background: #0b0b0b; }
    .ams-value { color: #0b0b0b; font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
    .ams-ticks { display: flex; justify-content: space-between; color: #898781; font-size: 11px; border-top: 1px solid #c3c2b7; padding-top: 2px; }
    .ams-empty { color: #898781; }
    </style>
    """
        return mo.Html('<div class="ams-bars">' + body + "</div>" + css)


    def ams_memory_table(owner: str, found: list[models.MemoryRecord]) -> mo.Html:
        """One owner's long-term memories as a markdown table, headed with the `ownerId`."""
        head = f"**ownerId {owner}** ({len(found)})"
        if not found:
            return mo.md(head + "\n\n_No memories yet._")
        rows = [f"| {m.text.replace('|', '&#124;')} | {m.memory_type} |" for m in found]
        return mo.md("\n".join([head, "", "| memory | type |", "|---|---|", *rows]))

    return ams_bars, ams_memory_table


if __name__ == "__main__":
    app.run()
