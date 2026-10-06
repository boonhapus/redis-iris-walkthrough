import marimo

__generated_with = "0.24.2"
app = marimo.App(width="columns", app_title="Redis Iris basics")


@app.cell(column=0)
def _():
    from collections.abc import AsyncGenerator, Callable
    import asyncio
    import itertools as it
    import os
    import uuid

    import redis

    from marimo import ai
    import marimo as mo

    return AsyncGenerator, Callable, ai, asyncio, it, mo, os, redis, uuid


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

    An agent that forgets a customer's nut allergy between conversations can suggest the wrong trail snack next time.
    Redis Agent Memory keeps each conversation in session memory and durable facts in long-term memory, written by the app or extracted from session events.
    Write five memories about Dana, a made-up Cairn customer, then chat as Dana and change what the agent recalls to see three principles:

    1. **Long-term memory recalls facts by meaning.** Questions that share no word with the fact they are after still find it, ranked best first with no score in the reply.
    2. **The owner and namespace are the tenant boundary.** The API key opens the whole store, and Agent Memory returns only memories matching the `ownerId` and `namespace` the app sends.
    3. **An agent turn reads both tiers.** The app fetches the session by id in event order, searches long-term memory with the latest message, and joins both into one prompt.

    Agent Memory is in public preview on Redis Cloud. Settings: `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY`, plus `ANTHROPIC_API_KEY`, which the chat and the cell under it need.
    Docs: [Agent Memory](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/),
    [developer guide](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/developer-guide/), and
    [REST quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/rest-api-quickstart/).

    Each line reads source, target, and what passes between them:

    ```text
    App            -> long-term memory  POST /long-term-memory, five memories with ids the app chooses
    App            -> session memory    POST /session-memory/events, the customer's message
    App            -> long-term memory  POST /long-term-memory/search, the message, filtered by owner and namespace
    App            -> session memory    GET /session-memory/{sessionId}, events in order
    App            -> Claude            the session events and the recalled memories, one call
    App            -> session memory    POST /session-memory/events, the agent's reply
    session memory -> long-term memory  background extraction, every 5 minutes by default
    ```
    """)
    return


@app.cell
async def _(os):
    import datetime as dt
    import secrets
 
    from redis_agent_memory import AgentMemory, models


    ams_client = AgentMemory(
        os.environ["MEMORY_API_BASE_URL"],
        store_id=os.environ["MEMORY_STORE_ID"],
        api_key=os.environ["MEMORY_API_KEY"],
    )

    await ams_client.health_async()
    return ams_client, dt, models, secrets


@app.cell
def _(
    AsyncGenerator,
    Callable,
    ai,
    ams_client,
    dt,
    it,
    models,
    secrets,
    stream_llm,
):
    ChatFn = Callable[[list[ai.ChatMessage], ai.ChatModelConfig], AsyncGenerator[str, None]]
 
 
    def ams_chat(
        user_id: str,
        threshold: Callable[[], float],
        turns: Callable[[], int],
        session_id: str | None = None,
    ) -> ChatFn:
        """Marimo Chat, with Agent Memory injected in."""
        session_id = session_id or secrets.token_hex(8)

        async def chat(
            messages: list[ai.ChatMessage], config: ai.ChatModelConfig
        ) -> AsyncGenerator[str, None]:
            """
            Redis-backed chat.
 
            1. Store the current USER turn in STM.
            2. Fetch the user's LTM based on the most recent turn.
            3. Interface with language model.
            """
            latest_user_text = str(messages[-1].content)
 
            # 1. Store the current USER turn in STM.
            _ = await ams_client.add_session_event_async(
                session_id=session_id,
                actor_id=user_id,
                role=models.MessageRole.USER,
                content=[{"text": latest_user_text}],
                # namespace=...,
                # metadata=...,
                created_at=dt.datetime.now(dt.timezone.utc),
            )
 
            # 2. Fetch the user's LTM based on the most recent turns.
            recent_user_turns = it.islice((m for m in reversed(messages) if m.role == "user"), turns())

            ltm = await ams_client.search_long_term_memory_async(
                request={
                    "text": "\n".join(str(m.content) for m in recent_user_turns),
                    "similarityThreshold": threshold(),
                    "filter": {"ownerId": {"eq": user_id}},
                    "limit": 10,
                }
            )
 
            # 3. Interface with language model.
            system = "You are a helpful AI assistant."

            if ltm.items:
                facts = "\n".join(f"- {m.text}" for m in ltm.items)
                system += f"\n\nWhat you remember about {user_id}:\n{facts}"
        
            history = [
                {"role": m.role, "content": str(m.content)}
                for m in messages
                if m.role in ("user", "assistant")
            ]
 
            async for text in stream_llm(system, history, config):
                yield text
 
        return chat

    return (ams_chat,)


@app.cell
def _(ams_chat, mo):
    get_threshold, set_threshold = mo.state(0.5)

    similarity_threshold = mo.ui.slider(
        start=0.0, stop=1.0, step=0.05, value=get_threshold(),
        label="Memory similarity threshold", show_value=True,
        on_change=set_threshold,
    )

    get_turns, set_turns = mo.state(1)

    turns = mo.ui.slider(
        start=1, stop=10, step=1, value=get_turns(),
        label="LTM user turns", show_value=True,
        on_change=set_turns,
    )

    chatbot = mo.ui.chat(ams_chat(user_id="Tom", threshold=get_threshold, turns=get_turns))

    mo.vstack([chatbot, similarity_threshold, turns])
    return


@app.cell
async def _(USER_ID, ams_client, mo, uuid):
    mo.stop(predicate=True)

    _fact = "I've got two dogs, Max and Cece"

    await ams_client.bulk_create_long_term_memories_async(
      memories=[
          {
              "id": str(uuid.uuid4()),
              "text": _fact,
              "ownerId": USER_ID,
          },
      ],
    )
    return


@app.cell
def _(ams_client, asyncio, it):
    async def amnesia(user_id: str) -> None:
        """Lose all memories about `user_id`."""
        memory_ids: list[str] = []
        page_token: str | None = ""

        # FETCH ALL MEMZ
        while page_token is not None:
            r = await ams_client.search_long_term_memory_async(
                request={
                    "filter": {"ownerId": {"eq": user_id}},
                    "limit": 100,
                    **({"pageToken": page_token} if page_token else {}),
                }
            )

            memory_ids += [m.id for m in r.items]
            page_token = r.next_page_token

        # DELETE ALL MEMZ
        r = await asyncio.gather(
            *(
                ams_client.bulk_delete_long_term_memories_async(memory_ids=memory_ids[i : i + 100])
                for i in range(0, len(memory_ids), 100)
            )
        )

        return list(it.chain.from_iterable(r))


    # await amnesia("Tom")
    return


if __name__ == "__main__":
    app.run()
