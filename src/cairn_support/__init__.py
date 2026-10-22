"""Support code for the Redis Iris basics notebook: what a reader does not need to read to learn Redis.

- schema: the data objects. Cairn records (HelpArticle, Customer, Product, Order, ShopRecords), SearchResults, Reply and Exchange, ChatMessage and ChatTurn, and ToolSpec.
- sample_data: the cast, and the articles and records the chapters write. No protocol.
- embedder: the potion-base-8M model that embeds text on this machine.
- llm: stream_llm, and answer_with_tools when the model picks tools, the OpenAI, Anthropic, or Gemini calls behind the chat turns. ToolSpec is in schema.
- redis_search_replies: parse_search_reply reads an FT.SEARCH reply into SearchResults, and format_search_table renders the hits, for Chapter 1. SearchResults is in schema.
- http: JsonSession, one JSON HTTP connection, and recording(), which lists the calls made inside a block. Reply and Exchange are in schema.

Nothing here imports marimo, so all of it works in an agent outside the notebook.
schema imports only the standard library, and sample_data imports schema.
"""
