"""The model call behind the Search, LangCache, and Agent Memory chat turns.

This is not Redis or Iris. Each turn builds its own system prompt, and stream_llm only sends
that prompt and the conversation to the model and streams the reply back.
A model name that starts with "claude" goes to Anthropic, and any other name to OpenAI.
LLM_API_KEY is read at the first call, not at import, so a missing key fails at the first chat
turn with the SDK's own error.
"""

import os
from collections.abc import AsyncIterator, Iterable

from cairn_support.types import ChatMessage

_clients: dict[tuple[str, str | None], object] = {}  # one client per provider and key, so turns reuse a connection


def _client(provider: str):
    key = os.environ.get("LLM_API_KEY") or None
    if (provider, key) not in _clients:
        if provider == "anthropic":
            from anthropic import AsyncAnthropic

            _clients[provider, key] = AsyncAnthropic(api_key=key)  # None lets the SDK look for its own credentials
        else:
            from openai import AsyncOpenAI

            _clients[provider, key] = AsyncOpenAI(api_key=key or "missing")  # a missing key fails at the request, not here
    return _clients[provider, key]


def _history(messages: Iterable[ChatMessage | dict]) -> list[dict[str, str]]:
    """The user and assistant messages as {"role", "content"} dicts, oldest first."""
    history = []
    for message in messages:
        role, content = (message["role"], message["content"]) if isinstance(message, dict) else (message.role, message.content)
        if role in ("user", "assistant"):
            history.append({"role": role, "content": str(content)})
    return history


async def stream_llm(
    system_prompt: str, messages: Iterable[ChatMessage | dict], model: str, max_tokens: int = 4096
) -> AsyncIterator[str]:
    """Stream the model's reply to `messages` under `system_prompt`, a piece of text at a time.

    `messages` is the chat so far, oldest first: chat messages with `role` and `content`, or dicts
    with those two keys. Only the user and assistant messages are sent.
    """
    history = _history(messages)
    if model.startswith("claude"):
        async with _client("anthropic").messages.stream(
            model=model, system=system_prompt, messages=history, max_tokens=max_tokens
        ) as stream:
            async for text in stream.text_stream:
                yield text
    else:
        stream = await _client("openai").chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system_prompt}, *history],
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
