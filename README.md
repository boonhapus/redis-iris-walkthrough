# Redis Iris basics

One [marimo](https://marimo.io) notebook, one story in four chapters. Cairn Outfitters, a made-up outdoor-gear shop, is launching an AI support assistant, and you build it with [Redis Iris](https://redis.io/iris/). Priya leaves for a canoe trip in Maine in three weeks, and her tent is still on order. Tom is the second customer, here whenever a lesson needs two people.

Each chapter adds one Redis Iris service, shows the exact command or request behind every step, and closes on the gap the next chapter fixes. For more Iris demos, see [redis/redis-iris-demos](https://github.com/redis/redis-iris-demos).

## The four chapters

### 1. Search (Redis Search)

Customers search the help center in their own words. You create an index over the help articles and query it three ways, each written as the `FT.SEARCH` text redis-cli takes: by meaning (`KNN`), within a similarity floor (`VECTOR_RANGE`), and with a filter that runs before ranking. The idea is that the schema decides what you can search. It closes on a gap: Priya gets a list of shipping articles, and she wanted an answer.

### 2. Semantic caching (Redis LangCache)

A model writes the answer from those articles, and LangCache stores it under the question, so a reworded question skips the model. You build cache-aside: search the cache, answer on a miss, store the answer with a TTL. The idea is that similar isn't the same, so a threshold only trades wrong answers for misses. It closes on "Where's my tent?", which gets a generic answer that the cache serves to everyone.

### 3. Context engineering and retrieval (Redis Context Retriever)

You declare customers, products, and orders as three Python classes, and Context Retriever turns them into MCP tools that read your Redis. The model chooses which tools to call, and you see each call and the prompt the app built. The idea is that the data model decides the tools, and the app decides what the model sees. It closes a week later, when a new chat has forgotten what Priya told it.

### 4. Agent memory (Redis Agent Memory)

What customers say is kept as long-term memories, one set per customer. Each turn writes the message to session memory, recalls that customer's memories by meaning, and adds them to Chapter 3's context, in the notebook's only chat. The idea is that a filter before ranking keeps Tom's memories out of Priya's chat. An epilogue follows one turn through every layer.

## Get started

You need:

- A Redis 8 database with Search and JSON. A free [Redis Cloud](https://cloud.redis.io) database works. Chapter 3 gives the database's address and password to the hosted Context Retriever service, so the service must be able to reach it. Context Retriever does not support Redis Flex or Active-Active databases.
- A key for one model provider: OpenAI, Anthropic, or Google AI Studio. Chapters 2 to 4 call the model, and `AI_MODEL` in column 0 picks it (`gpt-4o-mini` to start). Chapter 1 never calls the model (its embeddings are made on your machine), but the settings panel wants every setting filled in, `LLM_API_KEY` included, before any chapter runs.

[Redis Search](https://redis.io/docs/latest/develop/ai/search-and-query/) runs in your database. You'll also set up three services, each with its own key in `.env`:

- [Redis LangCache](https://redis.io/docs/latest/develop/ai/context-engine/langcache/): for semantic caching. Use a service of your own, because a cache is shared by everything that points at it.
- [Redis Context Retriever](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/): for centralized, fresh context from Redis
- [Redis Agent Memory](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/): an LLM-agnostic short-term and long-term memory layer

1. Open the folder:

   ```sh
   cd redis-iris-basics
   ```

2. Copy the sample settings file, then open `.env`, uncomment every variable, and fill each one in. The comments in the file say where to find each value in the Redis Cloud console.

   ```sh
   cp .env.example .env
   ```

3. Start the notebook with [uv](https://docs.astral.sh/uv/), which installs Python and the dependencies on the first run. That run also downloads the embedding model, about 30 MB.

   ```sh
   uv run marimo edit iris_basics.py
   ```

   No uv? Use pip with Python 3.12 or 3.13 (3.14 is not supported yet). Create a virtual environment, install the project into it, and start marimo from there:

   ```sh
   python -m venv .venv
   source .venv/bin/activate   # on Windows: .venv\Scripts\activate
   pip install -e .
   marimo edit iris_basics.py
   ```

## Run it

The notebook puts one chapter in each column: column 0 is the setup, and Chapters 1 to 4 follow, left to right. Each chapter builds on the one before, so run it column by column:

1. Chapter 1: press **Create**, because Chapters 2 to 4 search its articles. Then try the three searches.
2. Chapter 2: ask the four questions in order.
3. Chapter 3: press **Create**, then **Ask**. Chapter 4 also runs without this surface, but then the assistant has no tools.
4. Chapter 4: press **Seed**, then chat as Priya and as Tom.
5. End of Chapter 4: press **Clean up everything**.

Every command and request is written out above its button. Cells that only run one, or draw a table, hide their code, and the cells that define each chapter's functions show it. Each "Try it" says what to look for, because a model's wording changes from run to run.

The settings:

- A panel in column 0 lists the nine settings with a green check or a red cross, and while any is missing it stops every cell below it.
- Fill in `.env`, then press **Restart kernel**. The button ends the kernel, and marimo then offers to restart it. Accept, and the new kernel reads `.env` again.
- A changed `.env` needs a server restart if you launched with `uv run --env-file .env marimo edit iris_basics.py`. The server's environment then holds the old values, and marimo never overrides a variable that is already set. A missing value is picked up on a kernel restart. Stop the server and start it again to load a changed one.

## What it writes, and how it cleans up

Every name carries the tag `20261021` (keys under `cairn:20261021:`, a surface named `cairn-20261021`). Cleanup deletes by name or id, and it never flushes a database or a cache.

- **Your Redis database.**
  - Chapter 1's **Create** writes 37 article hashes under `cairn:20261021:help:` and the index `idx:cairn:20261021:help`.
  - Chapter 3's **Create** imports 22 JSON documents (6 customers, 8 products, 8 orders), and the service builds an index for each entity.
  - Every key expires in an hour. The indexes don't, so Clean up drops them by name.
- **LangCache.**
  - Each question the cache misses stores one entry, which expires in 15 minutes.
  - Entries can't be listed, so the notebook keeps the ids it stored. **Clear** in Chapter 2 and Clean up everything delete those by id.
- **Context Retriever.**
  - **Create** makes a surface named `cairn-20261021` and an agent key, and sends the password in your `REDIS_URL` to the hosted service so it can read and write your database.
  - The surface holds that password until **Clean up** in Chapter 3, or Clean up everything, deletes it. Deleting the surface also ends its agent key.
- **Agent Memory.**
  - **Seed** writes seven long-term memories: four for Priya (`C001`) and three for Tom (`C002`).
  - Each chat turn writes the message to session memory, which lasts an hour.
  - Long-term memories last a year, including any the service extracts from your chats, until Clean up everything deletes them.

**Clean up everything** removes all of it: the surface, Chapter 3's records and indexes, Chapter 1's index and articles, the cache entries the notebook stored, and both customers' memories and sessions. Press it when you're done, above all for the surface that holds your password.

## Troubleshooting

- **A model call fails with 401.** The key in `LLM_API_KEY` is revoked, expired, or from another provider than `AI_MODEL` expects: `gpt` takes an OpenAI key, `claude` an Anthropic key, and `gemini` a Google AI Studio key. Put a working key in `.env`, then restart the server if you launched with `--env-file`.
- **A search comes back empty, or an Ask says the help center has no articles.** The articles expire an hour after **Create**, so press **Create** in Chapter 1 again. Chapter 3's records expire the same way: press **Clean up**, then **Create** there.
- **A new memory doesn't appear.** The service extracts long-term memories from a session in the background, up to five minutes after a turn. Wait, then press **Refresh** in Chapter 4's step 1. Seeded memories are there at once.
- **Chapter 3's Create stops with an error.** A failed Create can leave a surface behind. Press **Clean up**, which deletes every surface named `cairn-20261021`, then press **Create** again.
