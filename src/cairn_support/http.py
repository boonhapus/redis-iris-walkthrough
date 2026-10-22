"""JSON over HTTP for LangCache and Context Retriever.

JsonSession holds one connection pool. request() sends a JSON object and returns the
status code, the JSON object in the body, and the milliseconds the call took.
recording() lists those calls, in order, for every JsonSession used inside the block, so a
chat turn can show the LangCache or MCP requests it made. Reply and Exchange,
the objects those calls produce, are defined in schema.

The body is a JSON object, or None when it is empty or any other value, such as an HTML
error page. The status code says what happened.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

import niquests

from cairn_support.schema import Exchange, JsonObject, Reply

type Timeout = float | tuple[float, float]

_active_trace: ContextVar[list[Exchange] | None] = ContextVar("json_request_trace", default=None)


class JsonSession:
    """A connection pool that sends JSON and reads a JSON object back.

    Content-Type stays application/json. Left to itself, niquests appends
    ;charset=utf-8. HTTP/3 is off, and retries stay at 0, so a POST is never replayed.
    timeout is seconds, or (connect, read).
    """

    def __init__(
        self,
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: Timeout = 15,
    ) -> None:
        self._session = niquests.Session(
            base_url=base_url,
            timeout=timeout,
            disable_http3=True,
            retries=0,
        )
        self._session.headers.update(
            {
                "Content-Type": "application/json",
                "User-Agent": "redis-iris-basics",
                **dict(headers or {}),
            }
        )

    def request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> Reply:
        """Send one request and return its Reply. Does not retry.

        body is the JSON object to send, or None when the request has no body.
        A copy is what gets logged, so a later change to the caller's dict is not
        a change to the exchange.
        """
        sent = None if body is None else dict(body)
        response = self._session.request(method, path, json=sent)
        reply = Reply(response.status_code, _json_object(response), round(response.elapsed.total_seconds() * 1000))
        trace = _active_trace.get()
        if trace is not None:
            trace.append(Exchange(method, path, sent, reply.status, reply.payload, reply.ms))
        return reply


def _json_object(response: niquests.Response) -> JsonObject | None:
    """The body when it is a JSON object, and None when it is empty or any other value."""
    raw = response.content
    if not raw:
        return None
    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type.lower() and not raw.lstrip().startswith(b"{"):
        return None
    parsed = response.json()
    if isinstance(parsed, dict):
        return parsed
    return None


@contextmanager
def recording() -> Iterator[list[Exchange]]:
    """Collect every JsonSession.request made inside the block, in order."""
    log: list[Exchange] = []
    token: Token[list[Exchange] | None] = _active_trace.set(log)
    try:
        yield log
    finally:
        try:
            _active_trace.reset(token)
        except ValueError:  # the block ended in another context, as a closed async generator can
            pass
