# Redis Iris basics

One [marimo](https://marimo.io) notebook. Cairn Outfitters, a made-up outdoor-gear shop, is launching an AI support assistant, and you build it with [Redis Iris](https://redis.io/iris/) in four chapters: Search, semantic caching, context retrieval, and agent memory. The notebook tells the story. This page is how to start it and what it writes. For more Iris demos, see [redis/redis-iris-demos](https://github.com/redis/redis-iris-demos).

## Before you start

You need:

- A Redis 8 database with Search and JSON. A free [Redis Cloud](https://cloud.redis.io) database works. Chapter 3 gives the database's address and password to the hosted Context Retriever service, so the service must be able to reach it. Context Retriever does not support Redis Flex or Active-Active databases.
- A key for one model provider: OpenAI, Anthropic, or Google AI Studio. Chapters 2 to 4 call the model, and `AI_MODEL` in column 0 picks it (`gpt-4o-mini` to start). Chapter 1 never calls the model (its embeddings are made on your machine), but the settings panel wants every setting filled in, `LLM_API_KEY` included, before any chapter runs.

[Redis Search](https://redis.io/docs/latest/develop/ai/search-and-query/) runs in your database. You'll also set up three services, each with its own key in `.env`:

- [Redis LangCache](https://redis.io/docs/latest/develop/ai/context-engine/langcache/): for semantic caching. Use a service of your own, because a cache is shared by everything that points at it.
- [Redis Context Retriever](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/): for centralized, fresh context from Redis
- [Redis Agent Memory](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/): an LLM-agnostic short-term and long-term memory layer

## Start

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
   uv run --env-file .env marimo edit --port 2718 --watch notebooks/learn_iris_101.py
   ```

   `--env-file .env` loads your settings into the server, `--port 2718` serves the notebook at `http://localhost:2718`, and `--watch` reloads the notebook when you save it in another editor. marimo opens it in your browser.

   No uv? Use pip with Python 3.12 or 3.13 (3.14 is not supported yet). Create a virtual environment, install the project into it, and start marimo from there:

   ```sh
   python -m venv .venv
   source .venv/bin/activate   # on Windows: .venv\Scripts\activate
   pip install -e .
   marimo edit notebooks/learn_iris_101.py
   ```

Press **Restart kernel** in column 0 after you fill in `.env`. The button ends the kernel, and marimo then offers to restart it. Accept, and the new kernel reads `.env` again. When you start with the uv command, `--env-file .env` puts the values in the server's environment, and marimo never overrides a variable that is already set. A missing value is picked up on a kernel restart. Stop the server and start it again to load a changed one.

Work left to right. Column 0 is the setup, and each chapter builds on the last. **Clean up everything** is at the end of Chapter 4.

## What it writes

Every name carries the tag `20261021` (keys under `cairn:20261021:`, a surface named `cairn-20261021`). Cleanup deletes by name or id, and it never flushes a database or a cache.

- **Your Redis database.** Chapter 1's **Create** writes the help-article hashes under `cairn:20261021:help:` and the index `idx:cairn:20261021:help`. Chapter 3's **Create** imports the customer, product, and order documents, and the service builds an index for each entity. Every key expires in an hour. The indexes don't, so Clean up drops them by name.
- **LangCache.** Each question the cache misses stores one entry, which expires in 15 minutes. Entries can't be listed, so the notebook keeps the ids it stored. **Clear** in Chapter 2 and Clean up everything delete those by id.
- **Context Retriever.** **Create** makes a surface named `cairn-20261021` and an agent key, and sends the password in your `REDIS_URL` to the hosted service so it can read and write your database. The surface holds that password until **Clean up** in Chapter 3, or Clean up everything, deletes it. Deleting the surface also ends its agent key.
- **Agent Memory.** **Seed** writes long-term memories for Priya (`C001`) and Tom (`C002`). Each chat turn writes the message to session memory, which lasts an hour. Long-term memories last a year, including any the service extracts from your chats, until Clean up everything deletes them.

**Clean up everything** removes all of it. Press it when you're done, above all for the surface that holds your password.

## If something fails

- **A model call fails with 401.** The key in `LLM_API_KEY` is revoked, expired, or from another provider than `AI_MODEL` expects: `gpt` takes an OpenAI key, `claude` an Anthropic key, and `gemini` a Google AI Studio key. Put a working key in `.env`, then restart the server if you launched with `--env-file`.
- **A search comes back empty, or an Ask says the help center has no articles.** The articles expire an hour after **Create**, so press **Create** in Chapter 1 again. Chapter 3's records expire the same way: press **Clean up**, then **Create** there.
- **A new memory doesn't appear.** The service extracts long-term memories from a session in the background, up to five minutes after a turn. Wait, then press **Refresh** in Chapter 4's step 1. Seeded memories are there at once.
- **Chapter 3's Create stops with an error.** A failed Create can leave a surface behind. Press **Clean up**, which deletes every surface named `cairn-20261021`, then press **Create** again.
