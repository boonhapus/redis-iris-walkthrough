"""Safety rules over the notebook source, one function per rule, checked with `ast`.

These are the invariants the old notebook enforced at runtime with wrappers and allowlists. Here
they are static: nothing flushes, nothing drops documents with an index, every LangCache entry
carries a TTL, every Agent Memory body is owner-scoped, no secret is ever a notebook global, and
the one reply that could echo a password is never rendered.

The file under test is iris_basics.py unless IRIS_BASICS_NOTEBOOK names another file. Rules
about a specific cell are vacuous while that column is not assembled (its marker is still in
the file), and fail if the column is assembled without the cell.
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

# Unprefixed names each column may import (section 17 of the plan). Columns 1 and 3 import nothing.
OWNED_IMPORTS = {
    0: {"NumericField", "TagField", "TextField", "VectorField", "IndexDefinition", "IndexType", "Query", "ResponseError"},
    2: {"unquote", "urlsplit", "Any", "ContextField", "ContextModel", "ContextRelationship", "export_data_model"},
}
NON_SECRET_READS = {"CTX_API_URL", "CTX_MCP_URL"}
FORBIDDEN_CALLS = {"mo.ui.chat", "mo.state", "mo.ui.form"}


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


def _calls():
    return [node for node in ast.walk(TREE) if isinstance(node, ast.Call)]


def _dicts():
    return [node for node in ast.walk(TREE) if isinstance(node, ast.Dict)]


def _string_constants():
    return [node.value for node in ast.walk(TREE) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def _constant_keys(dictionary):
    return {key.value for key in dictionary.keys if isinstance(key, ast.Constant)}


def _cells():
    """(function node, column) for every module-level @app.cell function. A bare `@app.cell` (marimo's
    own serialization of a cell with no config) yields column None, so the import test can fail on it."""
    for node in TREE.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and _dotted(decorator.func) == "app.cell":
                column = next(
                    (keyword.value.value for keyword in decorator.keywords
                     if keyword.arg == "column" and isinstance(keyword.value, ast.Constant)),
                    None,
                )
                yield node, column
            elif isinstance(decorator, ast.Attribute) and _dotted(decorator) == "app.cell":
                yield node, None


def _column_assembled(column):
    return f"# @@ COLUMN {column} CELLS @@" not in SOURCE


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


def _target_names(node):
    return [name.id for name in ast.walk(node) if isinstance(name, ast.Name)]


# ---- Redis ----


def test_no_flush():
    for text in _string_constants():
        assert not re.search(r"\bFLUSH\w*", text), f"FLUSH token in a string: {text!r}"
        assert "/flush" not in text.lower(), f"/flush in a string: {text!r}"
    for call in _calls():
        assert _dotted(call.func).split(".")[-1] not in {"flushall", "flushdb"}


def test_no_dd_and_no_delete_documents():
    assert "DD" not in _string_constants(), "an FT.DROPINDEX with DD deletes the documents too"
    for call in _calls():
        for keyword in call.keywords:
            if keyword.arg == "delete_documents":
                assert not (isinstance(keyword.value, ast.Constant) and keyword.value.value is True)
        if _dotted(call.func).split(".")[-1] == "dropindex":   # redis-py also takes delete_documents positionally
            assert not any(isinstance(argument, ast.Constant) and argument.value is True for argument in call.args), (
                "dropindex(True) deletes the documents too"
            )


def test_key_templates_start_with_prefix():
    templates = [
        node for node in ast.walk(TREE)
        if isinstance(node, ast.Assign) and "__redis_key_template__" in _target_names(node)
    ]
    if _column_assembled(2):
        assert len(templates) == 3, "column 2 is assembled but does not declare three key templates"
    for node in templates:
        value = node.value
        assert isinstance(value, ast.JoinedStr) and value.values, "a key template must be an f-string"
        first = value.values[0]
        assert isinstance(first, ast.FormattedValue) and isinstance(first.value, ast.Name)
        assert first.value.id == "KEY_PREFIX", "every key template starts with {KEY_PREFIX}"


# ---- LangCache ----


def test_no_include_all():
    assert not any("includeAll" in text for text in _string_constants())
    assert not any(keyword.arg == "includeAll" for call in _calls() for keyword in call.keywords)


def test_no_bodied_delete_on_entries():
    for call in _calls():
        if _dotted(call.func) != "call_service":
            continue
        constants = {argument.value for argument in call.args if isinstance(argument, ast.Constant)}
        if "DELETE" in constants and "/entries" in constants:
            for keyword in call.keywords:
                if keyword.arg == "json_body":
                    assert isinstance(keyword.value, ast.Constant) and keyword.value.value is None, (
                        "DELETE /entries with a body deletes by attribute across the whole cache"
                    )


def test_every_stored_entry_carries_ttl():
    for dictionary in _dicts():
        keys = _constant_keys(dictionary)
        if {"prompt", "response"} <= keys:
            assert "ttlMillis" in keys, "a stored LangCache entry must carry ttlMillis"


# ---- Agent Memory ----


def test_memory_bodies_carry_owner_id():
    for dictionary in _dicts():
        keys = _constant_keys(dictionary)
        if "memories" in keys or "filter" in keys:
            nested = {node.value for node in ast.walk(dictionary) if isinstance(node, ast.Constant)}
            assert "ownerId" in nested, "every memories or filter body is scoped by ownerId"


# ---- marimo ----


def test_no_forbidden_marimo_calls():
    for call in _calls():
        name = _dotted(call.func)
        assert name not in FORBIDDEN_CALLS, f"{name} is not used in this notebook"
        if name == "mo.ui.text":
            for keyword in call.keywords:
                if keyword.arg == "kind":
                    assert not (isinstance(keyword.value, ast.Constant) and keyword.value.value == "password")


def test_no_print():
    assert not any(_dotted(call.func) == "print" for call in _calls())


# ---- secrets ----


# The two calls a secret may flow into on the way to a global: the client and the session keep it in
# their own connection state, and nothing else in the notebook is allowed to.
SECRET_SINKS = {"connect_redis", "niquests.Session"}


def test_no_secret_read_bound_to_a_global():
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Assign):
            continue
        # Every read anywhere inside the value: a bare read, `.strip()` on one, an f-string
        # (`f"Bearer {os.environ[...]}"`), or a wrapper (`urlsplit(os.environ[...])`).
        reads = {_environ_read(sub) for sub in ast.walk(node.value) if isinstance(sub, (ast.Subscript, ast.Call))}
        secrets = {variable for variable in reads if variable is not None and variable not in NON_SECRET_READS}
        if not secrets:
            continue
        if isinstance(node.value, ast.Call) and _dotted(node.value.func) in SECRET_SINKS:
            continue
        for name in [name for target in node.targets for name in _target_names(target)]:
            assert name.startswith("_"), (
                f"{name} reads os.environ[{sorted(secrets)}]: a secret read must stay in the expression that uses it "
                f"({sorted(SECRET_SINKS)}), or in a cell-local name starting with _"
            )


def test_sessions_are_pinned():
    assert not any(keyword.arg == "retries" for call in _calls() for keyword in call.keywords), (
        "no session passes retries=: every call is exactly one request"
    )
    for call in _calls():
        if _dotted(call.func) != "niquests.Session":
            continue
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        http3 = keywords.get("disable_http3")
        assert isinstance(http3, ast.Constant) and http3.value is True, "every session passes disable_http3=True"
        headers = keywords.get("headers")
        assert isinstance(headers, ast.Dict), "every session passes a literal headers dict"
        header_values = {key.value: value for key, value in zip(headers.keys, headers.values) if isinstance(key, ast.Constant)}
        content_type = header_values.get("Content-Type")
        assert isinstance(content_type, ast.Constant) and content_type.value == "application/json"


def test_mcp_session_sends_the_verified_accept_header():
    sessions = [
        node for node in ast.walk(TREE)
        if isinstance(node, ast.Assign)
        and "cr_mcp_service" in _target_names(node)
        and isinstance(node.value, ast.Call)
        and _dotted(node.value.func) == "niquests.Session"
    ]
    if _column_assembled(2):
        assert sessions, "column 2 is assembled but no niquests.Session is assigned to cr_mcp_service"
    for node in sessions:
        headers = next((keyword.value for keyword in node.value.keywords if keyword.arg == "headers"), None)
        assert isinstance(headers, ast.Dict)
        accept = next((value for key, value in zip(headers.keys, headers.values) if isinstance(key, ast.Constant) and key.value == "Accept"), None)
        # niquests.Session(headers=) replaces the default headers, so the live-verified Accept must be set explicitly
        assert isinstance(accept, ast.Constant) and accept.value == "*/*", "the MCP session sends Accept: */* (the live-verified value), never application/json alone"


def test_create_reply_is_never_rendered():
    cells = {node.name: node for node, _ in _cells()}
    if "cr_create" not in cells:
        assert not _column_assembled(2), "column 2 is assembled without a cr_create cell"
        return
    cell = cells["cr_create"]
    bound, body_and_wire = set(), set()
    for node in ast.walk(cell):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        if _dotted(node.value.func) != "call_service":
            continue
        constants = {argument.value for argument in node.value.args if isinstance(argument, ast.Constant)}
        if "POST" in constants and "/context-surfaces" in constants:
            names = [name for target in node.targets for name in _target_names(target)]
            for name in names:
                assert name.startswith("_"), f"{name}: the create reply is bound only to cell-local names"
                bound.add(name)
            body_and_wire.update(names[1:])   # the status is a number; the body and the raw wire are what could echo the request
    assert bound, "cr_create makes no POST /context-surfaces call"
    for node in ast.walk(cell):
        if not isinstance(node, ast.Call) or not _dotted(node.func).startswith("mo."):
            continue
        if _dotted(node.func) == "mo.json":
            assert node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "cr_create_wire", (
                "the only mo.json argument in cr_create is cr_create_wire"
            )
        rendered = {name.id for name in ast.walk(node) if isinstance(name, ast.Name)}
        assert not rendered & body_and_wire, (
            f"{sorted(rendered & body_and_wire)} reach a mo.* call in cr_create: the create reply is rendered only through its cut type, title and detail"
        )
    expressions = [statement for statement in cell.body if isinstance(statement, ast.Expr)]
    if expressions:
        last_names = {name.id for name in ast.walk(expressions[-1]) if isinstance(name, ast.Name)}
        assert not last_names & bound, "the create reply is never the cell's last expression"


# ---- imports ----


def test_import_ownership():
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
