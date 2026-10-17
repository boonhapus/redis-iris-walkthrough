"""Support code for the Redis Iris basics notebook: what a reader does not need to read to learn Redis.

- sample_data: the cast, and the Cairn Outfitters articles and records the chapters write. No protocol.
- embedder: the potion-base-8M model that embeds text on this machine.
- llm: stream_llm, and answer_with_tools when the model picks tools, the OpenAI, Anthropic, or Gemini calls behind the chat turns.
- redis_search_replies: an FT.SEARCH reply as one dict per hit, and hits as a markdown table, for Chapter 1.
- sessions: json_session and json_request, JSON over HTTP with niquests, and recording, which collects the requests a block sends.
- types: the type names in the notebook's signatures.

Nothing here imports marimo, so all of it works in an agent outside the notebook, and
sample_data imports nothing.
"""
