import marimo

__generated_with = "0.25.0"
app = marimo.App(width="columns", app_title="Redis Iris basics")

with app.setup(hide_code=True):
    import getpass
    import hashlib
    import json
    import os
    import platform
    import re
    import sys
    import time
    from datetime import datetime, timedelta, timezone

    import marimo as mo
    import niquests
    import numpy as np
    import redis
    from redis.backoff import ExponentialBackoff
    from redis.retry import Retry

    from cairn_support import sample_data as cairn
    from cairn_support.embedder import EMBEDDING_MODEL_NAME, embed_texts, load_embedding_model
    from cairn_support.formatting import redis_cli, shared_stems

    # One tag for everything this notebook writes from this machine. It is derived from the user
    # and host names, so a fresh kernel finds what an earlier one wrote and Clean up needs no ledger.
    RUN_TAG = hashlib.sha256(f"{getpass.getuser()}@{platform.node()}".encode()).hexdigest()[:8]
    KEY_PREFIX = f"cairn:{RUN_TAG}:"   # every Redis key written by this notebook or by Context Retriever
    KEY_TTL_SECONDS = 3600             # Redis keys and the agent key expire on their own; Clean up only shortens that

    MASKED_HEADERS = {"authorization", "x-api-key"}

    def call_service(service, method, path, *, json_body=None, params=None):
        """One request to a hosted Iris service: (status, parsed body or None, wire).

        `wire` is what went on the wire with the credential header masked, so it is safe to show.
        Nothing is raised on a 4xx or 5xx: the JSON body (type, title, detail) is the teaching
        content, so each cell branches on the status itself. Nothing is retried: every call is
        exactly one request, so the wire view and the request counts in the copy are exact.
        """
        response = service.request(method, path, json=json_body, params=params)
        sent = response.request
        body = None
        if response.content:
            try:
                body = response.json()
            except ValueError:
                body = response.text
        headers = {k: "***" if k.lower() in MASKED_HEADERS else v for k, v in sent.headers.items()}
        wire = {
            "method": sent.method, "url": sent.url, "headers": headers,
            "body": json.loads(sent.body) if sent.body else None,
            "status": response.status_code, "ms": round(response.elapsed.total_seconds() * 1000),
        }
        return response.status_code, body, wire

    def connect_redis(url):
        """One redis-py client with the production checklist applied (redis-py docs, produsage)."""
        return redis.Redis.from_url(
            url,
            protocol=2,                # flat FT.* replies; every reply shape here was verified live under RESP2
            decode_responses=True,     # strings back; the vector field is never read through a core command
            socket_connect_timeout=5,
            socket_timeout=5,
            retry=Retry(ExponentialBackoff(), 3),  # three retries on ConnectionError and TimeoutError
            health_check_interval=30,  # PING before reuse after 30 s idle; a notebook idles a lot
        )


@app.cell(column=0, hide_code=True)
def intro():
    mo.md(rf"""
    # Redis Iris basics

    Agents do not have an intelligence problem. They have a context problem: the right facts, at the
    right moment, from data you already hold. [Redis Iris](https://redis.io/docs/latest/develop/ai/context-engine/)
    is the real-time context engine that supplies them: Redis Search underneath, then LangCache,
    Context Retriever, and Agent Memory as hosted services on Redis Cloud. This notebook takes them
    apart, one column per component, on Cairn Outfitters, a fictional outdoor-gear shop. Every step
    shows the exact `redis-cli` command or JSON request behind it. There is no LLM anywhere in it.

    | Component, left to right | Its one job here |
    |---|---|
    | Redis Search | Answer a question over 36 help articles by keyword, by meaning, and by both |
    | Redis LangCache | Return a stored answer for a question asked in other words |
    | Redis Context Retriever | Turn three Python classes into MCP tools an agent calls over your Redis |
    | Redis Agent Memory | Recall a customer fact with no word in common with the question |

    **Configuration.** Copy `.env.example` to `.env` next to `pyproject.toml`. marimo loads it
    when the kernel starts. A column whose variables are missing says so and stops.

    **Safety.** Everything this kernel writes carries the run tag `{RUN_TAG}`: Redis keys under
    `{KEY_PREFIX}` with a one-hour `EXPIRE`, LangCache entries with `ttlMillis`, a surface named
    `cairn-{RUN_TAG}`, memories owned by `cairn-{RUN_TAG}`. The tag is the same every time you open
    the notebook on this machine, so Clean up in a fresh kernel still finds what an earlier kernel
    wrote. Each column ends with one Clean up button whose label says what it deletes. Nothing is
    written until you press a button that says what it writes.
    """)
    return


@app.cell(column=0, hide_code=True)
def rs_title():
    mo.vstack([
        mo.md(r"""
    # Redis Search

    Users ask in their own words, and a keyword index only knows yours. Redis Search indexes the
    hashes under a key prefix once, then answers by keyword (`BM25STD`), by meaning (a vector
    field), or by both fused in one command (`FT.HYBRID`, Redis 8.4 and later). Its one job in this
    column: answer a question over 36 Cairn Outfitters help articles all three ways.

    The other three columns are built on this engine. The embedding model, `potion-base-8M`, runs
    on your machine, so this column needs one setting, `REDIS_URL`. Docs:
    [Search and query](https://redis.io/docs/latest/develop/ai/search-and-query/).
    """),
        mo.mermaid("""flowchart LR
    A[App] -->|embed locally| V[Vector]
    V -->|HSET| H[("Hash under cairn:run:help:")]
    H -.->|index watches the prefix| I[FT index]
    A -->|"FT.SEARCH / FT.HYBRID"| I"""),
    ])
    return


# @@ COLUMN 0 CELLS @@


@app.cell(column=1, hide_code=True)
def lc_title():
    mo.vstack([
        mo.md(r"""
    # Redis LangCache

    Users rarely ask the same question the same way twice, and an exact-key cache misses every
    rewording. Redis LangCache matches prompts by meaning: it embeds the prompt, searches the cache,
    and returns the stored answer when the similarity clears a threshold. Its one job in this
    column: return a stored answer for a question asked in other words.

    A match is a score above a line, so a wrong answer for a question that only sounds similar is
    possible. The threshold is the dial, and this column turns it. LangCache is in public preview on
    Redis Cloud. Settings: `LANGCACHE_HOST`, `LANGCACHE_CACHE_ID`, and `LANGCACHE_API_KEY`. Docs:
    [LangCache](https://redis.io/docs/latest/develop/ai/context-engine/langcache/),
    [API reference](https://redis.io/docs/latest/develop/ai/context-engine/langcache/api-reference/).
    """),
        mo.mermaid("""flowchart LR
    P[Prompt] --> S["POST /entries/search"]
    S -->|"similarity >= threshold"| H[Stored answer]
    S -->|"data: empty"| L[Call the LLM]
    L --> W["POST /entries with ttlMillis"]
    W -.-> S"""),
        mo.accordion({
            "The same calls with the `langcache` SDK (not installed here)": mo.md(r"""
    ```python
    from langcache import LangCache

    with LangCache(server_url="https://<host>", cache_id="<cache id>", api_key="<api key>") as cache:
        cache.search(prompt="How do I wash a down sleeping bag?", similarity_threshold=0.85)
        cache.set(prompt="...", response="...", ttl_millis=900_000)
        cache.delete_by_id(entry_id="<entryId>")
    ```

    This notebook sends the JSON itself so you can read it.
    """),
        }),
    ])
    return


# @@ COLUMN 1 CELLS @@


@app.cell(column=2, hide_code=True)
def cr_title():
    mo.vstack([
        mo.md(r"""
    # Redis Context Retriever

    An agent that writes its own queries against your database is a governance problem. Redis
    Context Retriever takes a data model you declare in Python, provisions indexes in your own
    Redis, and generates MCP tools whose arguments are constrained by that model. Its one job in
    this column: turn three Python classes into MCP tools an agent can call over your Redis.

    The agent gets `get_customer_by_id` and `filter_customer`, never a raw command. Context
    Retriever is in preview. This column needs an admin key (`CTX_ADMIN_KEY`) and, in `REDIS_URL`, a
    scratch database with Search and JSON that the hosted service can reach, because the service is
    given that database's password. Docs:
    [Context Retriever](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/).
    """),
        mo.mermaid("""flowchart LR
    M[ContextModel classes] -->|export_data_model| J[data_model JSON]
    J -->|"POST /context-surfaces"| S["Surface + agent key"]
    S -->|"writes JSON, builds indexes"| R[(Your Redis)]
    A[Agent] -->|"tools/call over MCP"| S"""),
    ])
    return


# @@ COLUMN 2 CELLS @@


@app.cell(column=3, hide_code=True)
def am_title():
    mo.vstack([
        mo.md(r"""
    # Redis Agent Memory

    An agent that forgets a user's allergy between sessions is not unintelligent. It has no memory.
    Redis Agent Memory gives it two tiers: session memory, the ordered events of the conversation in
    progress, and long-term memory, facts recalled by meaning across sessions. Its one job in this
    column: recall a customer fact with no word in common with the question.

    Facts reach long-term memory two ways: your app writes them directly, or the service extracts
    them from sessions in the background, minutes later. Every call carries an owner id and a
    namespace, because the store is shared by every app that points at it. Agent Memory is in public
    preview. Settings: `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID`, and `MEMORY_API_KEY`. Docs:
    [Agent Memory](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/),
    [REST quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/rest-api-quickstart/).
    """),
        mo.mermaid("""flowchart LR
    A[App] -->|"POST /session-memory/events"| S[Session memory]
    S -.->|"extraction, minutes later"| L[Long-term memory]
    A -->|"POST /long-term-memory"| L
    A -->|"POST /long-term-memory/search"| L"""),
    ])
    return


# @@ COLUMN 3 CELLS @@


if __name__ == "__main__":
    app.run()
