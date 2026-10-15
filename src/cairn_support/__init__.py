"""Support code for the Redis Iris basics notebook: what a reader does not need to read to learn Redis.

- sample_data: the Cairn Outfitters articles, answers, and records the columns write. No protocol.
- embedder: the potion-base-8M model that embeds text on this machine.
- formatting: log, colored [E]/[W]/[I] lines in a cell.
- llm: stream_llm, the OpenAI, Anthropic, or Gemini call behind the chat turns.
- redis_search_replies: FT.SEARCH reply tables for the Search column.
- sessions: json_session and json_request, JSON over HTTP with niquests, and recording, which collects the requests a block sends.
- types: the type names in the notebook's signatures.

Nothing here imports marimo, so all of it works in an agent outside the notebook, and
sample_data imports nothing.
"""
