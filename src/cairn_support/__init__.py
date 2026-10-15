"""Support code for the Redis Iris basics notebook: what a reader does not need to read to learn Redis.

- sample_data: the Cairn Outfitters articles, answers, and records the columns write. No protocol.
- embedder: the potion-base-8M model that embeds text on this machine.
- formatting: redis_cli, shared_stems, and CellLog for colored [E]/[W]/[I] lines in a cell.
- llm: stream_llm, the OpenAI, Anthropic, or Gemini call behind the chat turns.
- sessions: json_session and json_request, JSON over HTTP with niquests, and recording, which collects the requests a block sends.
- types: the type names in the notebook's signatures.

Nothing here imports marimo, so all of it works in an agent outside the notebook, and
sample_data imports nothing. Tests hold the package to both.
"""
