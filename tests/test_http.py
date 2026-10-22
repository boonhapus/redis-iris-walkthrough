from datetime import timedelta

from cairn_support.http import JsonSession, recording


class _Response:
    def __init__(self, status: int, content: bytes, content_type: str, parsed: object) -> None:
        self.status_code = status
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.elapsed = timedelta(milliseconds=12)
        self._parsed = parsed

    def json(self) -> object:
        return self._parsed


def _patch(monkeypatch, session: JsonSession, response: _Response, seen: list[tuple[str, str, object]]) -> None:
    def fake(method: str, path: str, json: object = None) -> _Response:
        seen.append((method, path, json))
        return response

    monkeypatch.setattr(session._session, "request", fake)


def test_json_object_round_trip(monkeypatch) -> None:
    session = JsonSession("https://example.test", {"Authorization": "Bearer secret"})
    seen: list[tuple[str, str, object]] = []
    _patch(monkeypatch, session, _Response(200, b'{"data": []}', "application/json", {"data": []}), seen)

    reply = session.request("POST", "/entries/search", {"prompt": "hi"})

    assert seen == [("POST", "/entries/search", {"prompt": "hi"})]
    assert reply == (200, {"data": []}, 12)
    assert session._session.headers["Content-Type"] == "application/json"
    assert session._session.headers["Authorization"] == "Bearer secret"
    assert session._session.headers["User-Agent"] == "redis-iris-basics"


def test_empty_and_non_json_bodies_stay_empty(monkeypatch) -> None:
    session = JsonSession()
    seen: list[tuple[str, str, object]] = []
    _patch(monkeypatch, session, _Response(204, b"", "application/json", None), seen)
    assert session.request("DELETE", "/entries/1").payload is None

    _patch(
        monkeypatch,
        session,
        _Response(403, b"<html>no</html>", "text/html", {"type": "should-not-parse"}),
        seen,
    )
    refused = session.request("POST", "/entries/search", {})
    assert refused.status == 403
    assert refused.payload is None


def test_json_content_type_ignores_case(monkeypatch) -> None:
    session = JsonSession()
    _patch(monkeypatch, session, _Response(200, b'{"ok": 1}', "Application/JSON", {"ok": 1}), [])
    assert session.request("GET", "/health").payload == {"ok": 1}


def test_json_object_without_a_json_content_type(monkeypatch) -> None:
    session = JsonSession()
    _patch(monkeypatch, session, _Response(200, b'{"ok": true}', "text/plain", {"ok": True}), [])
    assert session.request("GET", "/health").payload == {"ok": True}


def test_json_array_is_not_treated_as_an_object(monkeypatch) -> None:
    session = JsonSession()
    _patch(monkeypatch, session, _Response(200, b"[1]", "application/json", [1]), [])
    assert session.request("GET", "/list").payload is None


def test_recording_snapshots_the_body_and_nests(monkeypatch) -> None:
    outer = JsonSession()
    inner = JsonSession()
    _patch(monkeypatch, outer, _Response(200, b"{}", "application/json", {}), [])
    _patch(monkeypatch, inner, _Response(201, b"{}", "application/json", {"id": "1"}), [])

    body = {"prompt": "hi"}
    with recording() as first:
        outer.request("POST", "/outer", body)
        with recording() as second:
            inner.request("POST", "/inner", {"n": 1})
        assert [item.path for item in second] == ["/inner"]
    body["prompt"] = "changed"

    assert [(item.method, item.path, item.body, item.status) for item in first] == [
        ("POST", "/outer", {"prompt": "hi"}, 200)
    ]
    with recording() as third:
        pass
    assert third == []
