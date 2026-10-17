"""The type names in the notebook's signatures, so a signature says what goes in and what comes out.

ChatTurn and ChatMessage describe a chat turn; Reply and Exchange describe one HTTP request, which
sessions.json_request returns and sessions.recording collects.

Nothing here is checked at run time. ChatMessage is a Protocol, so marimo's chat messages fit it and
so do your own agent's: a turn copied out of the notebook does not bring marimo with it.
"""

from collections.abc import AsyncIterator
from typing import NamedTuple, Protocol

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
