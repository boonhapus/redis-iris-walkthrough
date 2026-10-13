# Redis Iris basics

One [marimo](https://marimo.io) notebook that takes the four components of [Redis Iris](https://redis.io/iris/) apart, one column each, on Cairn Outfitters, a fictional outdoor-gear shop. Each column shows the exact command or request behind every step.

- **Redis Search**: find help articles by meaning, within a similarity floor and a filter
- **Redis LangCache**: return a stored answer for a question asked in other words
- **Redis Context Retriever**: turn three Python classes into MCP tools an agent calls over your Redis
- **Redis Agent Memory**: recall a customer fact that shares no word with the question

For more Iris demos, see [redis/redis-iris-demos](https://github.com/redis/redis-iris-demos).

## Get started

You need:

- A free [Redis Cloud](https://cloud.redis.io) account (or Redis 8+ since Iris builds on `FT.SEARCH` and JSON the data type)
- An OpenAI or Anthropic API key, for the LangCache and Agent Memory chats

You'll also set up three services, each with its own key in `.env`:

- [Redis LangCache](https://redis.io/docs/latest/develop/ai/context-engine/langcache/): for semantic caching
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

3. Start the notebook with [uv](https://docs.astral.sh/uv/), which installs Python and the dependencies on the first run.

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
