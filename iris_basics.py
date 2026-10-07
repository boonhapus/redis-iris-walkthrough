import marimo

__generated_with = "0.24.2"
app = marimo.App(width="columns", app_title="Redis Iris basics")


@app.cell(column=0)
def _():
    from collections.abc import AsyncGenerator, Callable
    import asyncio
    import html
    import itertools as it
    import json
    import os
    import uuid

    import redis

    from marimo import ai
    import marimo as mo

    return AsyncGenerator, ai, asyncio, html, it, json, mo, os, redis, uuid


@app.cell
def redis_client(os, redis):
    REDIS_CLIENT = redis.Redis.from_url(
        os.environ["REDIS_URL"],
        protocol=2,               # flat FT.* replies: every reply shape here was verified live under RESP2
        decode_responses=True,    # strings back; the vector field is never read through a core command
        health_check_interval=30,
    )

    # AI_CLIENT = load_llm_client(model="gpt-4o-mini")
    # AI_CLIENT = load_llm_client(model="claude_haiku-4-5")

    AI_MODEL = "gpt-4o-mini"
    # AI_MODEL = "calude_hauiku-4-5"

    USER_ID = "Tom"
    return AI_MODEL, USER_ID


@app.cell
def llm_client(AI_MODEL, AsyncGenerator, ai, os):
    if AI_MODEL.startswith("gpt"):
        from openai import AsyncOpenAI

        llm = AsyncOpenAI(api_key=os.environ["LLM_API_KEY"])
 
        async def stream_llm(
            system_prompt: str, messages: list[dict[str, str]], config: ai.ChatModelConfig
        ) -> AsyncGenerator[str, None]:
            """Stream the repsonse from OpenAI."""
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

        llm = AsyncAnthropic(api_key=os.environ["LLM_API_KEY"])

        async def stream_llm(
            system_prompt: str, messages: list[dict[str, str]], config: ai.ChatModelConfig
        ) -> AsyncGenerator[str, None]:
            """Stream the repsonse from Anthropic."""
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

    Redis Search is available in Redis Open Source, Redis Software, and Redis Cloud, and `FT.HYBRID` needs Redis 8.4 or later. Settings: `REDIS_URL`, plus `ANTHROPIC_API_KEY`, which the chat and the cell under it need.
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

    LangCache is in public preview on Redis Cloud. Settings: `LANGCACHE_HOST` (with `https://`), `LANGCACHE_CACHE_ID`, and `LANGCACHE_API_KEY`, plus `ANTHROPIC_API_KEY`, which the chat needs on a miss. Docs:
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
    Select Seed to write memories for two made-up customers, Tom and Priya, then chat as either to see three principles:

    1. **Long-term memory recalls by meaning.** "Which foods should I avoid?" shares no keyword with "I'm allergic to peanuts and tree nuts", yet the search returns it for Priya. The bars beside the chat show the highest `similarityThreshold` that returns each memory, so raising the slider moves the line past the weakest memories first.
    2. **The `ownerId` filter scopes recall to one customer.** The app sends `filter: {ownerId: ...}` with every search, so Tom's agent does not see Priya's allergy. The bars beside the chat show the same search for both customers.
    3. **The app builds the prompt.** Each turn writes the message to session memory and searches long-term memory with the last N user messages. The memories it recalls go into the system prompt, so the model sees a memory only because the app put it there. Beside the chat you see the search text, the bars for it, and the prompt the app builds.

    Agent Memory is in public preview on Redis Cloud. Settings: `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY`, plus `LLM_API_KEY`, which the chat needs and which must match the provider of `AI_MODEL`.
    Docs: [overview](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/overview/),
    [Python SDK quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/), and
    [developer guide](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/developer-guide/).
    """)
    return


@app.cell(hide_code=True)
def am_store_md(mo):
    mo.md(r"""
    ## What long-term memory holds

    Seed writes three memories per customer with `POST /long-term-memory`, each tagged with an `ownerId` the app chooses.
    Clean up deletes every memory whose `ownerId` is Tom or Priya. Chat sessions are not deleted. The store expires them after an hour by default.
    """)
    return


@app.cell(hide_code=True)
def am_store_button(mo):
    ams_seed_button = mo.ui.run_button(label="Seed")
    ams_refresh_button = mo.ui.run_button(label="Refresh")
    ams_cleanup_button = mo.ui.run_button(label="Clean up", kind="danger")

    mo.hstack([ams_seed_button, ams_refresh_button, ams_cleanup_button], justify="start")
    return ams_cleanup_button, ams_refresh_button, ams_seed_button


@app.cell(hide_code=True)
async def am_store(
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
        _deleted = 0
        for _owner in ams_seeds:
            _deleted += len(await ams_forget(_owner))
        _note = f"Deleted {_deleted} memories for {' and '.join(ams_seeds)}."
    _ = ams_refresh_button.value  # Refresh re-lists, for memories extracted in the background

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
            ),
            mo.md(
                "Agent Memory also extracts memories from session events in the background, "
                "on the store's extraction cadence (5 minutes by default). "
                "Tell the agent a new fact, wait a few minutes, then select Refresh to look for what it extracted."
            ),
        ]
    )
    return (ams_store,)


@app.cell(hide_code=True)
def am_owner(USER_ID, ams_seeds, mo):
    ams_owner = mo.ui.dropdown(options=list(ams_seeds), value=USER_ID, label="Chat as")

    mo.md(r"""
    ## Chat and the data behind each turn

    Send the first three starter messages as Tom, then switch to Priya and send them again. Switching customers starts a new conversation.
    The fourth message tells the agent a new fact, and Refresh can show the memory extracted from it a few minutes later.
    Until you send a message, the panel beside the chat previews the first starter message.
    """)
    return (ams_owner,)


@app.cell(hide_code=True)
async def am_workbench(
    ams_bars,
    ams_build_system,
    ams_chatbot,
    ams_client,
    ams_owner,
    ams_request,
    ams_session_id,
    ams_threshold,
    ams_turn_bands,
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
    _turn_done = bool(ams_chatbot.value) and ams_chatbot.value[-1].role == "assistant"
    _recalled = [text for text, band in ams_turn_bands[_owner].items() if round(band, 2) >= _t]

    _data = [
        mo.md(
            ("**Search text**, the last user message:" if _n == 1 else f"**Search text**, the last {_n} user messages:")
            if ams_turn_live
            else "**Search text**, a preview of the first starter message until you send one:"
        ),
        mo.md(f"```text\n{ams_turn_query}\n```"),
        mo.md(f"**Recall for ownerId {_owner} at {_t:.2f}.** Memories above the line go into the prompt."),
        ams_bars(ams_turn_bands[_owner], _t),
    ]
    for _other in (o for o in ams_turn_bands if o != _owner):
        _data += [mo.md(f"**The same search as ownerId {_other}**"), ams_bars(ams_turn_bands[_other], _t)]
    _data += [
        mo.md("**System prompt the app built for this recall**"),
        mo.md(f"```text\n{ams_build_system(_owner, _recalled)}\n```"),
        mo.md(f"**Request as sent for ownerId {_owner}**"),
        mo.md(f"```json\n{json.dumps(ams_request(ams_turn_query, _owner, _t), indent=2)}\n```"),
    ]
    if _turn_done:
        _session = await ams_client.get_session_memory_async(session_id=ams_session_id)
        _data += [
            mo.md(f"**Session `{ams_session_id}` in Agent Memory** ({len(_session.events)} events)"),
            mo.ui.table(
                [
                    {"role": e.role.value, "text": e.content[0].text, "created (UTC)": e.created_at.strftime("%H:%M:%S")}
                    for e in _session.events
                ],
                selection=None,
                pagination=False,
                show_search=False,
                show_download=False,
            ),
        ]
    _data.append(
        mo.accordion(
            {
                "How the bars are measured": mo.md(
                    "The search API returns no similarity score. The notebook ran the same search at 21 thresholds, "
                    "0.00 to 1.00 in steps of 0.05. Each bar ends at the highest threshold that returned the memory, "
                    "so its similarity lies between that value and the next step up. "
                    "Memories at or above the line are returned at the slider's setting."
                ),
            }
        )
    )

    mo.hstack(
        [
            mo.vstack([ams_owner, ams_threshold, ams_turns, ams_chatbot]),
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
    ## The Agent Memory calls behind the demo

    Five cells of SDK calls. The client connects. The store cell seeds, lists, and deletes memories.
    The search cell runs one search and the sweep behind the bars. The prompt cell writes the session event and builds the system prompt.
    The turn calls the search and prompt cells in order. In edit mode their code is open, and the notebook machinery is collapsed at the end of the column.
    """)
    return


@app.cell
async def am_connect(mo, os):
    import datetime as dt
    import secrets
 
    from redis_agent_memory import AgentMemory, models


    ams_client = AgentMemory(
        os.environ["MEMORY_API_BASE_URL"],
        store_id=os.environ["MEMORY_STORE_ID"],
        api_key=os.environ["MEMORY_API_KEY"],
    )

    _health = await ams_client.health_async()

    mo.md(f"Agent Memory is {_health.status.value}.")
    return ams_client, dt, models, secrets


@app.cell
def am_store_fns(USER_ID, ams_client, asyncio, models, uuid):
    ams_seeds = {
        USER_ID: [
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
        """Every long-term memory whose `ownerId` is `owner`, across pages."""
        found: list[models.MemoryRecord] = []
        page_token: str | None = None

        while True:
            r = await ams_client.search_long_term_memory_async(
                request={
                    "filter": {"ownerId": {"eq": owner}},
                    "limit": 100,
                    **({"pageToken": page_token} if page_token else {}),
                }
            )
            found += r.items
            page_token = r.next_page_token

            if not page_token:
                return found


    async def ams_seed() -> list[str]:
        """Principle 2: write memories straight to long-term memory, each tagged with an `ownerId`."""
        created: list[str] = []

        for owner, texts in ams_seeds.items():
            stored = {m.text for m in await ams_memories(owner)}
            new = [
                {"id": str(uuid.uuid4()), "text": text, "ownerId": owner}
                for text in texts
                if text not in stored
            ]

            if new:
                await ams_client.bulk_create_long_term_memories_async(memories=new)

            created += [m["id"] for m in new]

        return created


    async def ams_forget(owner: str) -> list[str]:
        """Delete every long-term memory about `owner`. Returns the ids deleted."""
        memory_ids = [m.id for m in await ams_memories(owner)]

        await asyncio.gather(
            *(
                ams_client.bulk_delete_long_term_memories_async(memory_ids=memory_ids[i : i + 100])
                for i in range(0, len(memory_ids), 100)
            )
        )

        return memory_ids

    return ams_forget, ams_memories, ams_seed, ams_seeds


@app.cell
def am_recall_fns(ams_client, asyncio, models):
    def ams_request(query: str, owner: str, threshold: float) -> dict:
        """The search request. Principle 1 is `text` and `similarityThreshold`; Principle 2 is `filter`."""
        return {
            "text": query,
            "similarityThreshold": threshold,
            "filter": {"ownerId": {"eq": owner}},
            "limit": 10,
        }


    async def ams_recall(query: str, owner: str, threshold: float) -> list[models.MemoryRecord]:
        """Search long-term memory by meaning, for one owner, cut off by `threshold`."""
        r = await ams_client.search_long_term_memory_async(request=ams_request(query, owner, threshold))
        return r.items


    async def ams_sweep(query: str, store: dict[str, list[models.MemoryRecord]]) -> dict[str, dict[str, float]]:
        """For each owner in `store`, the highest threshold that returns each memory. The API returns no score."""
        grid = [round(0.05 * i, 2) for i in range(21)]  # 0.00 to 1.00
        pairs = [(owner, t) for owner in store for t in grid]
        gate = asyncio.Semaphore(12)  # 42 searches per sweep; keep a dozen in flight

        async def recall(owner: str, t: float) -> list[models.MemoryRecord]:
            async with gate:
                return await ams_recall(query, owner, t)

        hits = await asyncio.gather(*(recall(owner, t) for owner, t in pairs))

        bands = {owner: {m.text: 0.0 for m in records} for owner, records in store.items()}
        for (owner, t), found in zip(pairs, hits):
            for m in found:
                bands[owner][m.text] = max(bands[owner].get(m.text, 0.0), t)

        return bands

    return ams_recall, ams_request, ams_sweep


@app.cell
def am_prompt_fns(ai, ams_client, dt, it, models):
    def ams_query(messages: list[ai.ChatMessage], n: int) -> str:
        """The text to search with: the last `n` user messages, newest first."""
        recent = it.islice((m for m in reversed(messages) if m.role == "user"), n)
        return "\n".join(str(m.content) for m in recent)


    async def ams_remember(session_id: str, owner: str, text: str) -> None:
        """Principle 3: write the user's message to session memory."""
        await ams_client.add_session_event_async(
            session_id=session_id,
            actor_id=owner,
            role=models.MessageRole.USER,
            content=[{"text": text}],
            created_at=dt.datetime.now(dt.timezone.utc),
        )


    def ams_build_system(owner: str, facts: list[str]) -> str:
        """Principle 3: the app, not Agent Memory, decides what the model sees."""
        system = "You are a helpful AI assistant."

        if facts:
            system += f"\n\nWhat you remember about {owner}:\n" + "\n".join(f"- {fact}" for fact in facts)

        return system

    return ams_build_system, ams_query, ams_remember


@app.cell
def am_turn_fn(
    AsyncGenerator,
    ai,
    ams_build_system,
    ams_owner,
    ams_query,
    ams_recall,
    ams_remember,
    ams_settings,
    secrets,
    stream_llm,
):
    ams_session_id = f"{ams_owner.value.lower()}-{secrets.token_hex(6)}"  # a new session each time the customer changes


    async def ams_turn(messages: list[ai.ChatMessage], config: ai.ChatModelConfig) -> AsyncGenerator[str, None]:
        """One agent turn. Principle 3: the app, not the service, decides what the model sees."""
        owner = ams_owner.value

        # Session memory: the user's message, as an event in this session.
        await ams_remember(ams_session_id, owner, str(messages[-1].content))

        # Long-term memory: this owner's memories, by meaning, searched with the last N user messages.
        # ams_settings reads the two sliders (notebook machinery, at the end of the column).
        threshold, turns = ams_settings()
        query = ams_query(messages, turns)
        recalled = await ams_recall(query, owner, threshold)

        # The prompt: the app puts the recalled facts in front of the model.
        system = ams_build_system(owner, [m.text for m in recalled])
        history = [{"role": m.role, "content": str(m.content)} for m in messages if m.role in ("user", "assistant")]

        async for text in stream_llm(system, history, config):
            yield text

    return ams_session_id, ams_turn


@app.cell(hide_code=True)
def am_machinery_md(mo):
    mo.md(r"""
    ## Notebook machinery

    These cells hold the sliders and the chat object, the state behind them, the sweep that feeds the bars, and the rendering helpers.
    In edit mode, open a cell to read its code.
    """)
    return


@app.cell(hide_code=True)
def am_settings(mo):
    # The turn reads the latest slider values through ams_settings() below. A slider's .value cannot be read in the cell that defines it, and a cell that references these getters reruns on every move.
    get_threshold, set_threshold = mo.state(0.3)
    get_turns, set_turns = mo.state(1)


    def ams_settings() -> tuple[float, int]:
        """The sliders' current values, read at call time. A cell that calls this does not rerun when a slider moves."""
        return round(get_threshold(), 2), get_turns()

    return ams_settings, get_threshold, get_turns, set_threshold, set_turns


@app.cell(hide_code=True)
def am_chat(ams_turn, get_threshold, get_turns, mo, set_threshold, set_turns):
    ams_threshold = mo.ui.slider(
        start=0.0, stop=1.0, step=0.05, value=get_threshold(), show_value=True, debounce=True,
        label="similarityThreshold", on_change=set_threshold,
    )
    ams_turns = mo.ui.slider(
        start=1, stop=10, step=1, value=get_turns(), show_value=True, debounce=True,
        label="Search with the last N user messages", on_change=set_turns,
    )
    ams_prompts = ["Which foods should I avoid?", "What pets do I have?", "Where am I headed this summer?", "I'm training for a marathon in October."]

    # Displayed beside the data in am_workbench. ams_turn is the Iris turn above.
    ams_chatbot = mo.ui.chat(ams_turn, prompts=ams_prompts, max_height=520)
    return ams_chatbot, ams_prompts, ams_threshold, ams_turns


@app.cell(hide_code=True)
async def am_turn_sweep(
    ams_chatbot,
    ams_prompts,
    ams_query,
    ams_store,
    ams_sweep,
    get_turns,
):
    # The search text behind the last turn (the first starter prompt before any chat), and how high a threshold
    # each memory clears for it, for every customer. Depends on the chat, N, and the store, not on the threshold.
    ams_turn_live = any(m.role == "user" for m in ams_chatbot.value)
    ams_turn_query = ams_query(ams_chatbot.value, get_turns()) if ams_turn_live else ams_prompts[0]
    ams_turn_bands = await ams_sweep(ams_turn_query, ams_store)
    return ams_turn_bands, ams_turn_live, ams_turn_query


@app.cell(hide_code=True)
def am_render(html, mo, models):
    def ams_bars(bands: dict[str, float], threshold: float) -> mo.Html:
        """One row per memory: a bar to the highest threshold that returns it, and the slider as a line."""
        threshold = round(threshold, 2)
        rows = []
        for text, band in sorted(bands.items(), key=lambda kv: -kv[1]):
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
        body = "".join(rows) or '<div class="ams-empty">No memories for this owner yet. Select Seed to write them.</div>'
        css = """
    <style>
    .ams-bars { font: 13px system-ui, -apple-system, "Segoe UI", sans-serif; color: #52514e; }
    .ams-row { display: grid; grid-template-columns: minmax(0, 3fr) 2fr 3.2em; gap: 0 8px; align-items: center; margin: 6px 0; }
    .ams-label { overflow-wrap: anywhere; }
    .ams-track { position: relative; min-width: 0; height: 12px; background: #e1e0d9; border-radius: 0 4px 4px 0; }
    .ams-bar { position: absolute; left: 0; top: 0; height: 100%; background: #2a78d6; border-radius: 0 4px 4px 0; }
    .ams-bar-below { background: #9ec5f4; }
    .ams-line { position: absolute; top: -4px; height: 20px; width: 2px; background: #0b0b0b; }
    .ams-value { color: #0b0b0b; font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
    .ams-ticks { display: flex; justify-content: space-between; color: #898781; font-size: 11px; border-top: 1px solid #c3c2b7; padding-top: 2px; }
    .ams-caption { color: #898781; font-size: 11px; margin-top: 4px; }
    .ams-empty { color: #898781; }
    </style>
    """
        return mo.Html(
            '<div class="ams-bars">'
            + body
            + '<div class="ams-row"><div></div><div class="ams-ticks"><span>0</span><span>0.5</span><span>1</span></div><div></div></div>'
            + f'<div class="ams-caption">Highest similarityThreshold at which the memory is returned. Line: slider at {threshold:.2f}.</div>'
            + "</div>"
            + css
        )


    def ams_memory_table(owner: str, found: list[models.MemoryRecord]) -> mo.Html:
        """One owner's long-term memories as a table, headed with the `ownerId`."""
        rows = [
            {"memory": m.text, "type": m.memory_type, "created (UTC)": m.created_at.strftime("%H:%M:%S")}
            for m in found
        ]
        table = (
            mo.ui.table(rows, selection=None, pagination=False, show_search=False, show_download=False)
            if rows
            else mo.md("_No memories yet._")
        )
        return mo.vstack([mo.md(f"**ownerId: {owner}** ({len(found)})"), table])

    return ams_bars, ams_memory_table


if __name__ == "__main__":
    app.run()
