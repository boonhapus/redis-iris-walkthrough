"""JSON over HTTP for the LangCache and Context Retriever columns, with niquests.

json_session builds a session that sends JSON, and json_request makes one request and returns the
status, the reply as a dict, and the milliseconds it took. Neither knows a LangCache or Context
Retriever path or header: the notebook passes the base URL and the key, so the cells show both.
recording collects the requests made inside a block, which is how the notebook shows what a chat
turn sent without any display code in the turn.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from http import HTTPStatus

import niquests

from cairn_support.types import Exchange, Reply

_recording: ContextVar[list[Exchange] | None] = ContextVar("recording", default=None)


def json_session(
    base_url: str | None = None, headers: dict[str, str] | None = None, timeout: float | tuple[float, float] = 15
) -> niquests.Session:
    """A session that sends JSON, to `base_url` when given, with `headers` on every request.

    Content-Type is pinned to application/json, or niquests appends ;charset=utf-8. HTTP/3 is off,
    and retries stay at 0, so a POST is never replayed. `timeout` is seconds, or (connect, read).
    """
    session = niquests.Session(base_url=base_url, timeout=timeout, disable_http3=True)
    session.headers.update({"Content-Type": "application/json", "User-Agent": "redis-iris-basics", **(headers or {})})
    return session


def json_request(session: niquests.Session, method: str, path: str, body: dict | None = None) -> Reply:
    """One request, with no retry: the HTTP status, the reply as a dict, and the milliseconds it took.

    The reply is None for an empty body, such as a 204, and {"type": reason} when the server answers
    without JSON, such as the 403 HTML page a gateway sends for a wrong key. A body that starts like
    JSON is read as JSON whatever its Content-Type says.
    """
    r = session.request(method, path, json=body)
    if not r.content:
        payload = None
    elif "json" in r.headers.get("Content-Type", "") or r.content.lstrip()[:1] in (b"{", b"["):
        payload = r.json()
    else:
        payload = {"type": r.reason or _phrase(r.status_code)}  # HTTP/2 carries no reason phrase
    reply = Reply(r.status_code, payload, round(r.elapsed.total_seconds() * 1000))
    log = _recording.get()
    if log is not None:
        log.append(Exchange(method, path, body, *reply))
    return reply


@contextmanager
def recording() -> Iterator[list[Exchange]]:
    """Collect every json_request made inside the block, in order, as Exchange records."""
    log: list[Exchange] = []
    token = _recording.set(log)
    try:
        yield log
    finally:
        try:
            _recording.reset(token)
        except ValueError:  # the block ended in another context, as a closed async generator can
            pass


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return f"HTTP {status}"
