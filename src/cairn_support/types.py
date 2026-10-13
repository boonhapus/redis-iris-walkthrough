"""The type names in the notebook's signatures, so a signature says what goes in and what comes out.

Nothing here is checked at run time. ChatMessage is a Protocol, so marimo's chat messages fit it and
so do your own agent's: a turn copied out of the notebook does not bring marimo with it.
"""

from collections.abc import AsyncIterator
from typing import NamedTuple, NotRequired, Protocol, TypedDict

# What a chat turn returns: the reply, streamed a piece of text at a time.
ChatTurn = AsyncIterator[str]


class ChatMessage(Protocol):
    """One message in a chat: who sent it and what it says. marimo's chat messages fit this."""

    role: str  # "user" or "assistant"
    content: object  # the text, or an object whose str() is the text


class Reply(NamedTuple):
    """One HTTP exchange, as sessions.json_request returns it."""

    status: int  # the HTTP status
    payload: dict | None  # the JSON reply, None for an empty body, {"type": reason} when it is not JSON
    ms: int  # the round trip in milliseconds


class Exchange(NamedTuple):
    """One request and its reply, as sessions.recording collects them."""

    method: str
    path: str
    body: dict | None  # the JSON the request sent
    status: int
    payload: dict | None
    ms: int


class Article(TypedDict):
    """One help-center article, as sample_data.HELP_ARTICLES holds it and the Search column writes it."""

    id: str
    title: str
    body: str
    category: str
    year: int


class Surface(TypedDict):
    """The context surface the Context Retriever column created, as it keeps it after Create."""

    id: str
    name: str
    status: str  # the status in the create reply
    http: int  # the HTTP status of the create POST
    agent_key: str  # the key an agent sends to mcp_url. Show at most its last four characters.
    key_expires_at: str
    mcp_url: str
    imports: dict[str, int]  # the records imported, per entity
    expiring: int  # how many of the keys the service wrote now expire in an hour
    create_request: dict  # the create request as sent, with the password and the admin key masked


class ToolCall(TypedDict):
    """One tools/call and its reply, sorted into data, a tool error, or a JSON-RPC error."""

    tool: str
    arguments: dict | str  # a str when the form held no valid JSON, so nothing was sent
    http: int | str  # the HTTP status, or "" when nothing was sent or no reply came
    kind: str  # "data", "tool error", "JSON-RPC error <code>", "HTTP <status>", "not JSON", or the console's "not sent, ..." and "no reply, <error name>"
    text: str  # the reply text, JSON indented when it parses
    at: NotRequired[str]  # the time of the Send, added by the console
