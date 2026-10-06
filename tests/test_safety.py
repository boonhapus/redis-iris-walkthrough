"""Safety rules over the notebook source, one function per rule, checked with `ast`.

These protect a customer's data and secrets: nothing flushes, nothing drops documents with an
index, every key sits under the run's prefix, every LangCache entry carries a TTL, every Agent
Memory body is owner-scoped, no secret is ever a notebook global, every session is pinned, the
reply that could echo a password is never rendered, and Clean up deletes by name, never by scan.

The file under test is iris_basics.py unless IRIS_BASICS_NOTEBOOK names another file.
"""

import ast
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = Path(os.environ.get("IRIS_BASICS_NOTEBOOK", ROOT / "iris_basics.py"))
if not NOTEBOOK_PATH.is_absolute():
    NOTEBOOK_PATH = ROOT / NOTEBOOK_PATH
SOURCE = NOTEBOOK_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(NOTEBOOK_PATH))

# Unprefixed names each column may import. Every other name comes from the setup block, and
# columns 0, 2, and 4 import nothing.
OWNED_IMPORTS = {
    1: {"NumericField", "TagField", "TextField", "VectorField", "IndexDefinition", "IndexType", "Query"},
    3: {"unquote", "urlsplit", "Any", "ContextField", "ContextModel", "ContextRelationship", "export_data_model"},
}
NON_SECRET_READS = {"CTX_API_URL"}
# The calls a secret may flow into on the way to a global: the client and the sessions keep it in
# their own connection state, and nothing else in the notebook is allowed to.
SECRET_SINKS = {"iris_session", "niquests.Session", "redis.Redis.from_url"}
FORBIDDEN_CALLS = {"mo.state"}   # v2 allows mo.ui.chat and .form() submits
SCAN_CALLS = {"scan", "scan_iter", "hscan", "sscan", "zscan", "keys"}
# Fields of the create reply that may render: scalars the service sets, never the echoed request.
CREATE_REPLY_FIELDS = {"id", "status"}


# ---- helpers ----


def _dotted(node):
    """`mo.ui.text` for an Attribute chain on a Name, else ''."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _calls(node=TREE, name=None):
    return [sub for sub in ast.walk(node) if isinstance(sub, ast.Call) and name in (None, _dotted(sub.func))]


def _dicts():
    return [node for node in ast.walk(TREE) if isinstance(node, ast.Dict)]


def _string_constants(node=TREE):
    return [sub.value for sub in ast.walk(node) if isinstance(sub, ast.Constant) and isinstance(sub.value, str)]


def _constant_keys(dictionary):
    return {key.value for key in dictionary.keys if isinstance(key, ast.Constant)}


def _target_names(node):
    return [name.id for name in ast.walk(node) if isinstance(name, ast.Name)]


def _cells():
    """(function node, column) for every module-level @app.cell function; column None when the
    decorator carries no column= (a bare `@app.cell` included)."""
    for node in TREE.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            func = decorator.func if isinstance(decorator, ast.Call) else decorator
            if _dotted(func) == "app.cell":
                keywords = decorator.keywords if isinstance(decorator, ast.Call) else []
                yield node, next((k.value.value for k in keywords if k.arg == "column" and isinstance(k.value, ast.Constant)), None)


def _cell(name):
    return next(cell for cell, _ in _cells() if cell.name == name)


def _function(name):
    return next(node for node in ast.walk(TREE) if isinstance(node, ast.FunctionDef) and node.name == name)


def _service_call(call):
    """(method, path text) of a call_service call. An f-string path keeps its constant parts, and a
    path held in a name (the MCP URL) reads as ''."""
    method, path = call.args[1], call.args[2]
    if isinstance(path, ast.JoinedStr):
        return method.value, "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in path.values)
    return method.value, path.value if isinstance(path, ast.Constant) else ""


def _environ_read(node):
    """The variable name read when `node` is os.environ[...], os.environ.get(...) or os.getenv(...),
    possibly followed by string methods (`.strip()`, `.rstrip('/')`); None otherwise."""
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and _dotted(node.func) not in ("os.environ.get", "os.getenv"):
        node = node.func.value
    if isinstance(node, ast.Subscript) and _dotted(node.value) == "os.environ":
        return node.slice.value if isinstance(node.slice, ast.Constant) else ""
    if isinstance(node, ast.Call) and _dotted(node.func) in ("os.environ.get", "os.getenv"):
        first = node.args[0] if node.args else None
        return first.value if isinstance(first, ast.Constant) else ""
    return None


def _bound_values(cell, name):
    """(expression, cell) for every binding of `name`: an assignment's value or a loop's iterable in
    `cell`, or, for a name the cell takes as a parameter, in the cell that returns it."""
    if name in {arg.arg for arg in cell.args.args}:
        cell = next((other for other, _ in _cells() if name in _target_names(other.body[-1])), cell)
    for node in ast.walk(cell):
        if isinstance(node, ast.Assign) and name in [n for target in node.targets for n in _target_names(target)]:
            yield node.value, cell
        elif isinstance(node, (ast.For, ast.comprehension)) and name in _target_names(node.target):
            yield node.iter, cell


def _carries_owner(node, cell, seen=frozenset()):
    """True when `node`, or what a name in it is bound to (in this cell or the cell that defines
    it), holds an ownerId key."""
    if "ownerId" in _string_constants(node):
        return True
    names = {name.id for name in ast.walk(node) if isinstance(name, ast.Name)} - seen
    return any(_carries_owner(value, where, seen | names) for name in names for value, where in _bound_values(cell, name))


def _session_headers(call):
    """The headers dict of an iris_session(base_url, headers) or niquests.Session(headers=) call."""
    if len(call.args) > 1:
        return call.args[1]
    return next((keyword.value for keyword in call.keywords if keyword.arg == "headers"), None)


def _header(headers, name):
    return next((value for key, value in zip(headers.keys, headers.values) if isinstance(key, ast.Constant) and key.value == name), None)


# ---- Redis ----


def test_no_flush():
    """No FLUSHALL, FLUSHDB, or /flush anywhere."""
    for text in _string_constants():
        assert not re.search(r"\bFLUSH\w*", text), f"FLUSH token in a string: {text!r}"
        assert "/flush" not in text.lower(), f"/flush in a string: {text!r}"
    for call in _calls():
        assert _dotted(call.func).split(".")[-1] not in {"flushall", "flushdb"}


def test_no_dd_and_no_delete_documents():
    """No FT.DROPINDEX with DD and no delete_documents: dropping an index never deletes documents."""
    assert "DD" not in _string_constants(), "an FT.DROPINDEX with DD deletes the documents too"
    for call in _calls():
        for keyword in call.keywords:
            assert keyword.arg != "delete_documents", "dropindex(delete_documents=...) deletes the documents too"
        if _dotted(call.func).split(".")[-1] == "dropindex":   # redis-py also takes delete_documents positionally
            assert not call.args, "dropindex(True) deletes the documents too"


def test_key_templates_and_prefixes_start_with_key_prefix():
    """Every Context Retriever key template and every Redis key prefix is an f-string starting with
    {KEY_PREFIX}."""
    templates = [
        node for node in ast.walk(TREE)
        if isinstance(node, ast.Assign)
        and {"__redis_key_template__", "rs_key_prefix"} & {name for target in node.targets for name in _target_names(target)}
    ]
    assert len(templates) == 4, "expected three key templates in cr_data_model and rs_key_prefix in column 1"
    for node in templates:
        value = node.value
        assert isinstance(value, ast.JoinedStr) and value.values, "a key template must be an f-string"
        first = value.values[0]
        assert isinstance(first, ast.FormattedValue) and _dotted(first.value) == "KEY_PREFIX", (
            f"line {node.lineno}: every key template starts with {{KEY_PREFIX}}"
        )


# ---- LangCache ----


def test_no_include_all():
    """No request sends includeAll, which lists every session in a shared store. Markdown may name it."""
    markdown = {id(sub) for call in _calls(name="mo.md") for sub in ast.walk(call)}
    for node in ast.walk(TREE):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in markdown:
            assert "includeAll" not in node.value, f"line {node.lineno}: includeAll in a request"
    assert not any(keyword.arg == "includeAll" for call in _calls() for keyword in call.keywords)


def test_no_bodied_delete_on_entries():
    """No DELETE on /entries carries a body: a bodied DELETE /entries deletes by attribute across the cache."""
    for call in _calls(name="call_service"):
        method, path = _service_call(call)
        if method == "DELETE" and path.startswith("/entries"):
            for keyword in call.keywords:
                assert keyword.arg != "json_body" or (isinstance(keyword.value, ast.Constant) and keyword.value.value is None), (
                    f"line {call.lineno}: DELETE {path} carries a body"
                )


def test_every_stored_entry_carries_ttl():
    """Every stored LangCache entry (a dict with prompt and response) carries ttlMillis."""
    for dictionary in _dicts():
        keys = _constant_keys(dictionary)
        if {"prompt", "response"} <= keys:
            assert "ttlMillis" in keys, f"line {dictionary.lineno}: a stored LangCache entry must carry ttlMillis"


# ---- Agent Memory ----


def test_memory_bodies_carry_owner_id():
    """Every Agent Memory `memories` or `filter` body carries ownerId, inline or through the name it
    is bound to (a dropdown's options included), and every namespace filter clause names the owner
    beside it. Every Agent Memory request is made in column 4, so that is where the bodies are."""
    for cell, column in _cells():
        if column != 4:
            assert not any(_dotted(call.args[0]) == "AGENT_MEMORY" for call in _calls(cell, "call_service")), (
                f"{cell.name} calls Agent Memory outside column 4"
            )
            continue
        for dictionary in ast.walk(cell):
            if not isinstance(dictionary, ast.Dict):
                continue
            for key, value in zip(dictionary.keys, dictionary.values):
                if isinstance(key, ast.Constant) and key.value in ("memories", "filter"):
                    assert _carries_owner(value, cell), f"{cell.name}, line {dictionary.lineno}: a {key.value} body without ownerId"
            namespace = _header(dictionary, "namespace")
            if isinstance(namespace, ast.Dict):   # a filter clause such as {"namespace": {"eq": ...}}
                assert "ownerId" in _constant_keys(dictionary), f"{cell.name}, line {dictionary.lineno}: a namespace filter without ownerId"


def test_cleanup_deletes_by_name_and_never_scans():
    """No Clean up cell scans: no SCAN, KEYS, or *scan* call, so every delete names its keys or ids.
    The DD and includeAll rules above cover Clean up too."""
    cleanups = [cell for cell, _ in _cells() if cell.name.endswith("_cleanup")]
    assert len(cleanups) == 4, "each column ends with a Clean up cell"
    for cell in cleanups:
        for call in _calls(cell):
            assert _dotted(call.func).split(".")[-1] not in SCAN_CALLS, f"{cell.name}, line {call.lineno}: Clean up scans"
        for text in _string_constants(cell):
            assert not re.match(r"(SCAN|KEYS)\b", text, flags=re.I), f"{cell.name}: Clean up sends {text!r}"


# ---- marimo ----


def test_no_forbidden_marimo_calls():
    """No mo.state and no password text input. Chats and .form() submits are the v2 inputs."""
    for call in _calls():
        name = _dotted(call.func)
        assert name not in FORBIDDEN_CALLS, f"{name} is not used in this notebook"
        if name == "mo.ui.text":
            for keyword in call.keywords:
                assert not (keyword.arg == "kind" and isinstance(keyword.value, ast.Constant) and keyword.value.value == "password")


def test_no_print():
    """No print: every output is a cell output."""
    lines = [call.lineno for call in _calls(name="print")]
    assert not lines, f"print on lines {lines}"


# ---- secrets ----


def test_no_secret_read_bound_to_a_global():
    """A secret read stays inside the call that uses it (iris_session, niquests.Session, or
    redis.Redis.from_url), or in a cell-local name starting with _."""
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Assign):
            continue
        # Every read anywhere inside the value: a bare read, `.strip()` on one, an f-string
        # (`f"Bearer {os.environ[...]}"`), or a wrapper (`urlsplit(os.environ[...])`).
        reads = {_environ_read(sub) for sub in ast.walk(node.value) if isinstance(sub, (ast.Subscript, ast.Call))}
        secrets = {variable for variable in reads if variable is not None and variable not in NON_SECRET_READS}
        if not secrets or (isinstance(node.value, ast.Call) and _dotted(node.value.func) in SECRET_SINKS):
            continue
        for name in [name for target in node.targets for name in _target_names(target)]:
            assert name.startswith("_"), (
                f"{name} reads os.environ[{sorted(secrets)}]: a secret read must stay in the expression that uses it "
                f"({sorted(SECRET_SINKS)}), or in a cell-local name starting with _"
            )


def test_sessions_are_pinned():
    """No retries=. Every session is built by iris_session, which passes disable_http3=True and a
    pinned Content-Type: application/json that no caller overrides."""
    assert not any(keyword.arg == "retries" for call in _calls() for keyword in call.keywords), (
        "no session passes retries=: every call is exactly one request"
    )
    sessions = _calls(name="niquests.Session")
    assert len(sessions) == 1 and sessions == _calls(_function("iris_session"), "niquests.Session"), (
        "iris_session is the only place a niquests.Session is built"
    )
    keywords = {keyword.arg: keyword.value for keyword in sessions[0].keywords}
    http3 = keywords.get("disable_http3")
    assert isinstance(http3, ast.Constant) and http3.value is True, "iris_session passes disable_http3=True"
    headers = keywords.get("headers")
    assert isinstance(headers, ast.Dict), "iris_session passes a literal headers dict"
    content_type = _header(headers, "Content-Type")
    assert isinstance(content_type, ast.Constant) and content_type.value == "application/json"
    for call in _calls(name="iris_session"):
        assert not (isinstance(_session_headers(call), ast.Dict) and _header(_session_headers(call), "Content-Type")), (
            f"line {call.lineno}: a caller overrides the pinned Content-Type"
        )


def test_mcp_session_sends_the_verified_accept_header():
    """cr_mcp is an iris_session that sends Accept: */*, and iris_session lets the caller's headers win."""
    sessions = [
        node.value for node in ast.walk(_cell("cr_create"))
        if isinstance(node, ast.Assign) and _target_names(node.targets[0]) == ["cr_mcp"] and isinstance(node.value, ast.Call)
    ]
    assert len(sessions) == 1 and _dotted(sessions[0].func) == "iris_session", "cr_create builds cr_mcp with iris_session"
    headers = _session_headers(sessions[0])
    accept = _header(headers, "Accept") if isinstance(headers, ast.Dict) else None
    assert isinstance(accept, ast.Constant) and accept.value == "*/*", "the MCP session sends Accept: */* (the live-verified value)"
    pinned = _calls(_function("iris_session"), "niquests.Session")[0]
    defaults = next(keyword.value for keyword in pinned.keywords if keyword.arg == "headers")
    assert defaults.keys[-1] is None, "iris_session spreads **headers last, so the caller's Accept wins"


def test_create_password_is_masked_and_the_reply_never_rendered():
    """In cr_create, the statement after POST /context-surfaces masks the password in the wire.
    Nothing renders before it, and no output shows the create or agent-key replies: only the
    status, the masked wire through mo.json, and the reply's id and status fields."""
    cell = _cell("cr_create")
    create = [
        (index, statement) for index, statement in enumerate(cell.body)
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call)
        and _dotted(statement.value.func) == "call_service" and _service_call(statement.value) == ("POST", "/context-surfaces")
    ]
    assert len(create) == 1, "cr_create makes one POST /context-surfaces call, as a top-level statement"
    index, statement = create[0]
    status, body, wire = _target_names(statement.targets[0])
    assert body.startswith("_") and wire.startswith("_"), "the create reply is bound only to cell-local names"

    mask = cell.body[index + 1]
    path, node = [], mask.targets[0] if isinstance(mask, ast.Assign) else None
    while isinstance(node, ast.Subscript):
        path.insert(0, node.slice.value)
        node = node.value
    assert _dotted(node) == wire and path == ["body", "data_source", "connection_config", "password"] and mask.value.value == "***", (
        f"the statement after the create call must be {wire}['body']['data_source']['connection_config']['password'] = '***'"
    )

    agent_key_replies = {
        name for sub in ast.walk(cell)
        if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call) and _dotted(sub.value.func) == "call_service"
        and "agent-keys" in _service_call(sub.value)[1]
        for name in _target_names(sub.targets[0])[1:]
    }
    rendered = [call for call in _calls(cell) if _dotted(call.func).startswith("mo.") and _dotted(call.func) != "mo.stop"]
    expressions = [statement for statement in cell.body if isinstance(statement, ast.Expr)]
    if expressions:
        rendered.append(expressions[-1].value)   # the cell's output
    for output in rendered:
        assert output.lineno > mask.lineno, f"line {output.lineno} renders before the password is masked"
        allowed = {
            id(sub.value) for sub in ast.walk(output)
            if isinstance(sub, ast.Subscript) and _dotted(sub.value) == body and getattr(sub.slice, "value", None) in CREATE_REPLY_FIELDS
        } | {id(call.args[0]) for call in _calls(output, "mo.json") if call.args and _dotted(call.args[0]) == wire}
        for name in ast.walk(output):
            if isinstance(name, ast.Name) and name.id in {body, wire} | agent_key_replies:
                assert id(name) in allowed, f"line {name.lineno}: {name.id} renders in cr_create; the create and agent-key replies never render"


# ---- imports ----


def test_import_ownership():
    """Only columns 1 and 3 import, and only the names they own; everything else is from the setup block."""
    for cell, column in _cells():
        assert column is not None, f"{cell.name} carries no column="
        allowed = OWNED_IMPORTS.get(column, set())
        for node in ast.walk(cell):
            if isinstance(node, ast.Import):
                names = [(alias.asname or alias.name).split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [alias.asname or alias.name for alias in node.names]
            else:
                continue
            for name in names:
                assert name in allowed, (
                    f"{cell.name} (column {column}) imports {name}, which that column does not own; "
                    "everything else comes from the setup block"
                )
