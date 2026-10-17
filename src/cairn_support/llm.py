"""The model calls behind the chat turns.

This is not Redis or Iris. Each turn builds its own system prompt, and stream_llm only sends
that prompt and the conversation to the model and streams the reply back.
answer_with_tools does the same and also offers the model a list of tools: it runs the calls the
model asks for through a function the caller supplies, sends the results back, and yields the
final text once.
A model name that starts with "claude" goes to Anthropic, one that starts with "gemini" to Google,
and any other name to OpenAI.
LLM_API_KEY is read at the first call, not at import, so a missing key fails at the first chat
turn with the SDK's own error.
"""

import inspect
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable

from cairn_support.types import ChatMessage

_MAX_TOKENS = 4096  # the reply cap on each model call in a tool loop, stream_llm's default
_CallTool = Callable[[str, dict], str | Awaitable[str]]  # (tool name, arguments) -> the text for the model
_clients: dict[tuple[str, str | None], object] = {}  # one client per provider and key, so turns reuse a connection


def _client(model: str):
    """The SDK client for `model`'s provider: Anthropic, Google, or OpenAI."""
    key = os.environ.get("LLM_API_KEY") or None
    provider = "anthropic" if model.startswith("claude") else "gemini" if model.startswith("gemini") else "openai"
    if (provider, key) not in _clients:
        if provider == "anthropic":
            from anthropic import AsyncAnthropic

            _clients[provider, key] = AsyncAnthropic(api_key=key)  # None lets the SDK look for its own credentials
        elif provider == "gemini":
            from google import genai

            _clients[provider, key] = genai.Client(api_key=key)  # None lets the SDK look for GOOGLE_API_KEY or GEMINI_API_KEY
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


def _gemini_contents(history: list[dict[str, str]]) -> list[dict]:
    """`history` as Gemini contents: its assistant role is "model"."""
    return [
        {"role": "model" if message["role"] == "assistant" else "user", "parts": [{"text": message["content"]}]}
        for message in history
    ]


async def stream_llm(
    system_prompt: str, messages: Iterable[ChatMessage | dict], model: str, max_tokens: int = _MAX_TOKENS
) -> AsyncIterator[str]:
    """Stream the model's reply to `messages` under `system_prompt`, a piece of text at a time.

    `messages` is the chat so far, oldest first: chat messages with `role` and `content`, or dicts
    with those two keys. Only the user and assistant messages are sent.
    """
    history = _history(messages)
    if model.startswith("claude"):
        async with _client(model).messages.stream(
            model=model, system=system_prompt, messages=history, max_tokens=max_tokens
        ) as stream:
            async for text in stream.text_stream:
                yield text
    elif model.startswith("gemini"):
        stream = await _client(model).aio.models.generate_content_stream(
            model=model,
            contents=_gemini_contents(history),
            config={"system_instruction": system_prompt, "max_output_tokens": max_tokens},
        )
        async for chunk in stream:
            if chunk.text:
                yield chunk.text
    else:
        stream = await _client(model).chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system_prompt}, *history],
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


def _openai_tools(tools: list[dict]) -> list[dict]:
    """`tools` as chat completions takes them: each one a function whose parameters are the inputSchema."""
    return [
        {
            "type": "function",
            "function": {"name": tool["name"], "description": tool.get("description", ""), "parameters": tool["inputSchema"]},
        }
        for tool in tools
    ]


def _anthropic_tools(tools: list[dict]) -> list[dict]:
    """`tools` as the Messages API takes them: the inputSchema is called input_schema."""
    return [
        {"name": tool["name"], "description": tool.get("description", ""), "input_schema": tool["inputSchema"]}
        for tool in tools
    ]


def _gemini_tools(tools: list[dict]) -> list:
    """`tools` as one Gemini Tool of function declarations. parameters_json_schema takes the inputSchema as it is."""
    from google.genai import types

    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=tool["name"], description=tool.get("description", ""), parameters_json_schema=tool["inputSchema"]
                )
                for tool in tools
            ]
        )
    ]


async def _result(call_tool: _CallTool, name: str, arguments: dict) -> str:
    """The text `call_tool` returns for this call, awaited when call_tool is a coroutine function."""
    result = call_tool(name, arguments)
    return await result if inspect.isawaitable(result) else result


def _stopped(max_rounds: int) -> str:
    """What the answer says when the model is still asking for tools after `max_rounds` rounds."""
    return f"The assistant stopped after {max_rounds} rounds of tool calls without an answer."


async def _ask_openai(
    system_prompt: str, history: list[dict[str, str]], model: str, tools: list[dict], call_tool: _CallTool, max_rounds: int
) -> str:
    """The OpenAI tool loop: the final text, or the stopped sentence."""
    client = _client(model)
    specs = _openai_tools(tools)
    messages = [{"role": "system", "content": system_prompt}, *history]
    for round_number in range(max_rounds + 1):
        reply = await client.chat.completions.create(model=model, messages=messages, tools=specs, max_tokens=_MAX_TOKENS)
        message = reply.choices[0].message
        if not message.tool_calls:
            return message.content or ""
        if round_number == max_rounds:
            break
        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.function.name, "arguments": call.function.arguments},
                    }
                    for call in message.tool_calls
                ],
            }
        )
        for call in message.tool_calls:
            name = call.function.name
            try:
                arguments = json.loads(call.function.arguments or "{}")  # the arguments arrive as a JSON string
                if not isinstance(arguments, dict):
                    raise ValueError("not a JSON object")
            except ValueError as error:  # json.JSONDecodeError is one
                content = f"Error: the arguments for {name} are not valid JSON ({error}). Call it again with a JSON object."
            else:
                content = await _result(call_tool, name, arguments)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
    return _stopped(max_rounds)


async def _ask_anthropic(
    system_prompt: str, history: list[dict[str, str]], model: str, tools: list[dict], call_tool: _CallTool, max_rounds: int
) -> str:
    """The Anthropic tool loop: the final text, or the stopped sentence."""
    client = _client(model)
    specs = _anthropic_tools(tools)
    messages = list(history)
    for round_number in range(max_rounds + 1):
        reply = await client.messages.create(
            model=model, system=system_prompt, messages=messages, tools=specs, max_tokens=_MAX_TOKENS
        )
        if reply.stop_reason != "tool_use":
            return "".join(block.text for block in reply.content if block.type == "text")
        if round_number == max_rounds:
            break
        messages.append({"role": "assistant", "content": reply.content})
        results = []
        for block in reply.content:
            if block.type == "tool_use":
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": await _result(call_tool, block.name, block.input),
                    }
                )
        messages.append({"role": "user", "content": results})
    return _stopped(max_rounds)


async def _ask_gemini(
    system_prompt: str, history: list[dict[str, str]], model: str, tools: list[dict], call_tool: _CallTool, max_rounds: int
) -> str:
    """The Gemini tool loop: the final text, or the stopped sentence."""
    from google.genai import types

    client = _client(model)
    contents = _gemini_contents(history)
    config = {
        "system_instruction": system_prompt,
        "max_output_tokens": _MAX_TOKENS,
        "tools": _gemini_tools(tools),
        "automatic_function_calling": {"disable": True},  # the loop is here, so the SDK must not run one of its own
    }
    for round_number in range(max_rounds + 1):
        reply = await client.aio.models.generate_content(model=model, contents=contents, config=config)
        if not reply.function_calls:
            return reply.text or ""
        if round_number == max_rounds:
            break
        contents.append(reply.candidates[0].content)  # the model's own turn, so its thought signatures go back with it
        parts = []
        for call in reply.function_calls:
            text = await _result(call_tool, call.name, call.args or {})
            parts.append(
                types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name, response={"result": text}))
            )
        contents.append(types.Content(role="user", parts=parts))
    return _stopped(max_rounds)


async def answer_with_tools(
    system_prompt: str,
    messages: Iterable[ChatMessage | dict],
    model: str,
    tools: list[dict],
    call_tool: _CallTool,
    max_rounds: int = 5,
) -> AsyncIterator[str]:
    """Answer `messages` under `system_prompt`, letting the model call `tools` first, and yield the final text once.

    `tools` are the MCP tool dicts from tools/list, with `name`, `description`, and `inputSchema`.
    `call_tool(name, arguments)` is a function or a coroutine function that returns the text to send
    back to the model, data or an error alike, so the model can recover from a failed call. If it
    raises, the exception is not caught. OpenAI sends arguments as a JSON string: one that is not a
    JSON object is answered with an error text, and `call_tool` is not called.

    The model gets up to `max_rounds` rounds of tool calls. If it still asks for more after the
    last one, the answer is one sentence saying the assistant stopped. Nothing streams during tool
    rounds. With no `tools`, this is stream_llm.
    """
    if not tools:
        async for text in stream_llm(system_prompt, messages, model):
            yield text
        return
    ask = _ask_anthropic if model.startswith("claude") else _ask_gemini if model.startswith("gemini") else _ask_openai
    yield await ask(system_prompt, _history(messages), model, tools, call_tool, max_rounds)
