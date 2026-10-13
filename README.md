# Redis Iris basics

One [marimo](https://marimo.io) notebook that takes the four components of [Redis Iris](https://redis.io/iris/) apart, one column each, on Cairn Outfitters, a fictional outdoor-gear shop. Each column shows the exact command or request behind every step.

- **Redis Search**: find help articles by meaning, within a similarity floor and a filter
- **Redis LangCache**: return a stored answer for a question asked in other words
- **Redis Context Retriever**: turn three Python classes into MCP tools an agent calls over your Redis
- **Redis Agent Memory**: recall a customer fact that shares no word with the question

For more Iris demos, see [redis/redis-iris-demos](https://github.com/redis/redis-iris-demos).

## Get started

You need [uv](https://docs.astral.sh/uv/), which installs Python 3.12 or 3.13 for you, and these accounts and keys:

- A Redis 8 or later database with Search and JSON, local or on [Redis Cloud](https://cloud.redis.io)
- A LangCache service
- A Context Retriever admin key
- An Agent Memory service
- An OpenAI or Anthropic API key, for the LangCache and Agent Memory chats

1. Open the folder:

   ```sh
   cd redis-iris-basics
   ```

2. Copy the sample settings file, then open `.env`, uncomment every variable, and fill each one in. The comments in the file say where to find each value in the Redis Cloud console.

   ```sh
   cp .env.example .env
   ```

3. Start the notebook. `uv` installs the dependencies on the first run.

   ```sh
   uv run marimo edit iris_basics.py
   ```

Column 0 checks your settings and marks each missing one with a red cross. Until all nine are set, the other columns wait behind it. After you edit `.env`, press **Restart kernel** in column 0, then **Restart** in the banner that follows.

Nothing is written until you press a button such as **Load**, **Seed**, or **Create**, or send a chat message. Each column has a **Clean up** button that deletes what the column wrote.

`AI_MODEL` in column 0 picks the chat model, `gpt-4o-mini` at first. Put an OpenAI key in `LLM_API_KEY` for a `gpt` model and an Anthropic key for a `claude` model.
