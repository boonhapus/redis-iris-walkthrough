"""Support code for the Redis Iris basics notebook: what a reader does not need to read to learn Redis.

- sample_data: the Cairn Outfitters articles, answers, and records the columns write. No protocol.
- embedder: the potion-base-8M model that embeds text on this machine.
- formatting: redis_cli, which prints a command the way redis-cli takes it, and shared_stems.
- llm: stream_llm, the OpenAI or Anthropic call behind the chat turns.
- run_tag: RUN_TAG and KEY_PREFIX, the tag in the name of the keys the Search and Context Retriever columns write.
- sessions: json_session and json_request, JSON over HTTP with niquests, and recording, which collects the requests a block sends.
- types: the type names in the notebook's signatures.

Nothing here imports marimo, so all of it works in an agent outside the notebook, and
sample_data imports nothing. Tests hold the package to both.
"""
