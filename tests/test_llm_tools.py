import asyncio
import json
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock, ToolUseBlock
from google.genai import types
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageFunctionToolCall

from cairn_support import llm

SCHEMA = {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]}
TOOLS = [
    {"name": "get_customer_by_id", "description": "Look up a customer.", "inputSchema": SCHEMA},
    {"name": "filter_order", "inputSchema": {"type": "object", "properties": {}}},  # tools/list may leave the description out
]
MESSAGES = [
    {"role": "user", "content": "Hi"},
    SimpleNamespace(role="assistant", content="Hello, how can I help?"),  # chat messages work as well as dicts
    {"role": "user", "content": "Who is C001?"},
]
CALL = ("call_1", "get_customer_by_id", {"customer_id": "C001"})  # id, tool name, arguments
PROVIDERS = {"openai": "gpt-4o-mini", "anthropic": "claude-sonnet-5-5", "gemini": "gemini-2.5-flash"}


def fake_openai(*rounds):
    """An AsyncOpenAI that answers each request with the next round: a list of tool calls, or the final text."""
    replies, requests = list(rounds), []

    async def create(**kwargs):
        requests.append({**kwargs, "messages": list(kwargs["messages"])})
        round_ = replies.pop(0)
        calls = [
            ChatCompletionMessageFunctionToolCall(
                id=id_,
                type="function",
                function={"name": name, "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)},
            )
            for id_, name, arguments in (round_ if isinstance(round_, list) else [])
        ]
        message = ChatCompletionMessage(role="assistant", content=None if calls else round_, tool_calls=calls or None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), requests=requests)


def fake_anthropic(*rounds):
    """An AsyncAnthropic that answers each request with the next round: a list of tool calls, or the final text."""
    replies, requests = list(rounds), []

    async def create(**kwargs):
        requests.append({**kwargs, "messages": list(kwargs["messages"])})
        round_ = replies.pop(0)
        if isinstance(round_, list):
            content = [TextBlock(type="text", text="Let me look.")] + [
                ToolUseBlock(type="tool_use", id=id_, name=name, input=arguments) for id_, name, arguments in round_
            ]
            return SimpleNamespace(content=content, stop_reason="tool_use")
        content = [] if round_ is None else [TextBlock(type="text", text=round_)]  # None is a reply with no text in it
        return SimpleNamespace(content=content, stop_reason="end_turn")

    return SimpleNamespace(messages=SimpleNamespace(create=create), requests=requests)


def fake_gemini(*rounds):
    """A genai.Client that answers each request with the next round: a list of tool calls, or the final text."""
    replies, requests = list(rounds), []

    async def generate_content(*, model, contents, config):
        requests.append({"model": model, "contents": list(contents), "config": config})
        round_ = replies.pop(0)
        if isinstance(round_, list):
            parts = [
                types.Part(function_call=types.FunctionCall(id=id_, name=name, args=arguments))
                for id_, name, arguments in round_
            ]
        else:
            parts = [types.Part(text=round_)]
        return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))])

    return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), requests=requests)


FAKES = {"openai": fake_openai, "anthropic": fake_anthropic, "gemini": fake_gemini}


def answer(monkeypatch, provider, client, call_tool, max_rounds=5):
    """The pieces answer_with_tools yields for MESSAGES, with `client` standing in for the provider's SDK client."""
    monkeypatch.setattr(llm, "_client", lambda model: client)

    async def collect():
        return [
            text
            async for text in llm.answer_with_tools("You help.", MESSAGES, PROVIDERS[provider], TOOLS, call_tool, max_rounds)
        ]

    return asyncio.run(collect())


def recording_tool(calls, asynchronous=False, text="C001 is Ada Lovelace."):
    """A call_tool that records (name, arguments) in `calls` and answers `text`, as a function or a coroutine function."""

    def call_tool(name, arguments):
        calls.append((name, arguments))
        return text

    async def async_call_tool(name, arguments):
        return call_tool(name, arguments)

    return async_call_tool if asynchronous else call_tool


def test_openai_tools():
    assert llm._openai_tools(TOOLS) == [
        {
            "type": "function",
            "function": {"name": "get_customer_by_id", "description": "Look up a customer.", "parameters": SCHEMA},
        },
        {"type": "function", "function": {"name": "filter_order", "description": "", "parameters": TOOLS[1]["inputSchema"]}},
    ]


def test_anthropic_tools():
    assert llm._anthropic_tools(TOOLS) == [
        {"name": "get_customer_by_id", "description": "Look up a customer.", "input_schema": SCHEMA},
        {"name": "filter_order", "description": "", "input_schema": TOOLS[1]["inputSchema"]},
    ]


def test_gemini_tools():
    (tool,) = llm._gemini_tools(TOOLS)
    declarations = tool.function_declarations
    assert [declaration.name for declaration in declarations] == ["get_customer_by_id", "filter_order"]
    assert declarations[0].description == "Look up a customer."
    assert declarations[0].parameters_json_schema == SCHEMA  # raw JSON Schema, no translation
    assert declarations[0].parameters is None


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("provider", PROVIDERS)
def test_one_tool_round_then_the_answer(monkeypatch, provider, asynchronous):
    calls = []
    client = FAKES[provider]([CALL], "C001 is Ada Lovelace.")
    pieces = answer(monkeypatch, provider, client, recording_tool(calls, asynchronous))
    assert pieces == ["C001 is Ada Lovelace."]  # one piece: nothing streams during tool rounds
    assert calls == [("get_customer_by_id", {"customer_id": "C001"})]
    assert len(client.requests) == 2


def test_openai_request_shapes(monkeypatch):
    other = ("call_2", "filter_order", {})
    client = fake_openai([CALL, other], "Done.")
    answer(monkeypatch, "openai", client, recording_tool([], text="a result"))
    first, second = client.requests
    history = [
        {"role": "system", "content": "You help."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello, how can I help?"},
        {"role": "user", "content": "Who is C001?"},
    ]
    assert first["model"] == "gpt-4o-mini"
    assert first["tools"] == llm._openai_tools(TOOLS)
    assert first["messages"] == history
    assert second["messages"] == [
        *history,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_customer_by_id", "arguments": '{"customer_id": "C001"}'},
                },
                {"id": "call_2", "type": "function", "function": {"name": "filter_order", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "a result"},
        {"role": "tool", "tool_call_id": "call_2", "content": "a result"},
    ]


def test_anthropic_request_shapes(monkeypatch):
    other = ("toolu_2", "filter_order", {})
    client = fake_anthropic([("toolu_1", "get_customer_by_id", {"customer_id": "C001"}), other], "Done.")
    answer(monkeypatch, "anthropic", client, recording_tool([], text="a result"))
    first, second = client.requests
    history = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello, how can I help?"},
        {"role": "user", "content": "Who is C001?"},
    ]
    assert first["model"] == "claude-sonnet-5-5"
    assert first["system"] == "You help."
    assert first["tools"] == llm._anthropic_tools(TOOLS)
    assert first["messages"] == history
    assistant, results = second["messages"][len(history) :]
    assert [block.type for block in assistant["content"]] == [
        "text",
        "tool_use",
        "tool_use",
    ]  # the model's own turn, as it sent it
    assert results == {  # every result in one user message
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "a result"},
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "a result"},
        ],
    }


def test_gemini_request_shapes(monkeypatch):
    other = ("call_2", "filter_order", {})
    client = fake_gemini([CALL, other], "Done.")
    answer(monkeypatch, "gemini", client, recording_tool([], text="a result"))
    first, second = client.requests
    history = [
        {"role": "user", "parts": [{"text": "Hi"}]},
        {"role": "model", "parts": [{"text": "Hello, how can I help?"}]},
        {"role": "user", "parts": [{"text": "Who is C001?"}]},
    ]
    assert first["model"] == "gemini-2.5-flash"
    assert first["contents"] == history
    assert first["config"]["system_instruction"] == "You help."
    assert first["config"]["automatic_function_calling"] == {"disable": True}
    assert first["config"]["tools"][0].function_declarations[0].parameters_json_schema == SCHEMA
    model_turn, results = second["contents"][len(history) :]
    assert [part.function_call.name for part in model_turn.parts] == ["get_customer_by_id", "filter_order"]
    assert results.role == "user"  # every result in one message
    assert [
        (part.function_response.id, part.function_response.name, part.function_response.response) for part in results.parts
    ] == [
        ("call_1", "get_customer_by_id", {"result": "a result"}),
        ("call_2", "filter_order", {"result": "a result"}),
    ]


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]", '"C001"'], ids=["not JSON", "a list", "a string"])
def test_openai_arguments_that_are_not_a_json_object(monkeypatch, arguments):
    calls = []
    client = fake_openai([("call_1", "get_customer_by_id", arguments)], "Sorry, I could not look that up.")
    pieces = answer(monkeypatch, "openai", client, recording_tool(calls))
    assert pieces == ["Sorry, I could not look that up."]
    assert calls == []  # the tool was not called
    tool_message = client.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_1"
    assert tool_message["content"].startswith("Error: the arguments for get_customer_by_id are not valid JSON")


def test_openai_empty_arguments_are_an_empty_object(monkeypatch):
    calls = []
    client = fake_openai([("call_1", "filter_order", "")], "Done.")
    answer(monkeypatch, "openai", client, recording_tool(calls))
    assert calls == [("filter_order", {})]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_stops_after_max_rounds(monkeypatch, provider):
    calls = []
    client = FAKES[provider](*[[CALL]] * 3)  # the model asks for tools on every request
    pieces = answer(monkeypatch, provider, client, recording_tool(calls), max_rounds=2)
    assert len(pieces) == 1
    assert "stopped after 2 rounds of tool calls" in pieces[0]
    assert len(calls) == 2  # two rounds ran, and the third request for tools was not run
    assert len(client.requests) == 3  # the model saw the results of the second round


@pytest.mark.parametrize("provider", PROVIDERS)
def test_no_rounds_allowed(monkeypatch, provider):
    calls = []
    client = FAKES[provider]([CALL])
    pieces = answer(monkeypatch, provider, client, recording_tool(calls), max_rounds=0)
    assert len(pieces) == 1
    assert "stopped after 0 rounds of tool calls" in pieces[0]
    assert calls == []
    assert len(client.requests) == 1


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_reply_with_no_text_is_an_empty_answer(monkeypatch, provider):
    client = FAKES[provider](None)
    assert answer(monkeypatch, provider, client, recording_tool([])) == [""]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_the_answer_after_the_last_round_still_counts(monkeypatch, provider):
    client = FAKES[provider]([CALL], [CALL], "Done.")
    assert answer(monkeypatch, provider, client, recording_tool([]), max_rounds=2) == ["Done."]


@pytest.mark.parametrize(
    "error", [RuntimeError("the service is down"), ValueError("bad id")], ids=["RuntimeError", "ValueError"]
)
@pytest.mark.parametrize("provider", PROVIDERS)
def test_an_exception_from_call_tool_propagates(monkeypatch, provider, error):
    def call_tool(name, arguments):
        raise error

    client = FAKES[provider]([CALL], "unreachable")
    with pytest.raises(type(error), match=str(error)):
        answer(monkeypatch, provider, client, call_tool)


def test_no_tools_streams_through_stream_llm(monkeypatch):
    seen = []

    async def fake_stream_llm(system_prompt, messages, model):
        seen.append((system_prompt, list(messages), model))
        yield "Hel"
        yield "lo"

    monkeypatch.setattr(llm, "stream_llm", fake_stream_llm)
    monkeypatch.setattr(llm, "_client", lambda model: pytest.fail("no tools, so no client is needed here"))

    async def collect():
        return [text async for text in llm.answer_with_tools("You help.", MESSAGES, "gpt-4o-mini", [], recording_tool([]))]

    assert asyncio.run(collect()) == ["Hel", "lo"]
    assert seen == [("You help.", MESSAGES, "gpt-4o-mini")]
