"""Notebook rules: it runs and checks, its layout, the three goals per column, the simplicity
budgets, no guards or status branching, the Claude client and the chat cells, and where globals
live. One function per rule.

The rules come from .agents/staging/design/SYNTHESIS.md (Global), .agents/staging/BUILD.md (Code
rules), and .agents/staging/INTERACTIVE.md (v2: chats, a console, and inputs). A failure here is fixed in the notebook, not in the test: cut the code, do not add
an exception or raise a cap.

The file under test is iris_basics.py unless IRIS_BASICS_NOTEBOOK names another file.
"""

import ast
import importlib.util
import math
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = Path(os.environ.get("IRIS_BASICS_NOTEBOOK", ROOT / "iris_basics.py"))
if not NOTEBOOK_PATH.is_absolute():
    NOTEBOOK_PATH = ROOT / NOTEBOOK_PATH
SOURCE = NOTEBOOK_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(NOTEBOOK_PATH))

COLUMN_0 = [
    "intro", "redis_client", "embedding_model", "llm_client", "langcache_client",
    "context_retriever_client", "agent_memory_client", "langcache_search_helper",
]
PREFIXES = {1: "rs", 2: "lc", 3: "cr", 4: "am"}
SETUP_NAMES = {"RUN_TAG", "KEY_PREFIX", "KEY_TTL_SECONDS", "iris_session", "call_service"}
CHAT_CELLS = {"rs_chat", "lc_chat", "am_chat"}   # Context Retriever has a tool console instead

# Measured on the staged v2 notebook on 2026-10-05, (cells, lines). Each cap is the measured value
# plus 10%, rounded up. Column 0's lines include the setup block.
MEASURED = {0: (8, 158), 1: (10, 259), 2: (10, 199), 3: (11, 301), 4: (8, 243)}
BUDGETS = {column: (math.ceil(cells * 1.1), math.ceil(lines * 1.1)) for column, (cells, lines) in MEASURED.items()}
FILE_BUDGET = (math.ceil(47 * 1.1), math.ceil(1263 * 1.1))
CUT = "cut code to fit; the cap is not raised"

# The only try blocks a lesson needs: "already exists" on a second Load, "not found" on a second
# Clean up, and the finally that sets a TTL on what Context Retriever writes.
ALLOWED_TRY = {"rs_seed": "redis.ResponseError", "rs_cleanup": "redis.ResponseError", "cr_create": None}
GOAL = re.compile(r"^(\d)\. \*\*([^*]+\.)\*\* \S.*$")


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


def _decorator(node):
    return next((d for d in node.decorator_list if _dotted(d.func if isinstance(d, ast.Call) else d) == "app.cell"), None)


def _config(node):
    """The @app.cell(...) keywords of a cell as {name: value}; {} for a bare @app.cell."""
    decorator = _decorator(node)
    if not isinstance(decorator, ast.Call):
        return {}
    return {keyword.arg: keyword.value.value for keyword in decorator.keywords if isinstance(keyword.value, ast.Constant)}


CELLS = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and _decorator(node)]
BY_NAME = {cell.name: cell for cell in CELLS}


def _column(column):
    return [cell for cell in CELLS if _config(cell).get("column") == column]


def _setup_block():
    blocks = [
        node for node in TREE.body
        if isinstance(node, ast.With) and any(_dotted(getattr(item.context_expr, "func", None)) == "app.setup" for item in node.items)
    ]
    assert len(blocks) == 1, "expected exactly one `with app.setup(...)` block"
    return blocks[0]


def _owners():
    """(name, node) for every top-level statement: a cell's name, '<setup>', or '<module>'."""
    for node in TREE.body:
        if node in CELLS:
            yield node.name, node
        elif isinstance(node, ast.With):
            yield "<setup>", node
        else:
            yield "<module>", node


def _md_text(cell):
    """The text of the first mo.md call in a cell, with each f-string interpolation as {}."""
    call = next(node for node in ast.walk(cell) if isinstance(node, ast.Call) and _dotted(node.func) == "mo.md")
    argument = call.args[0]
    if isinstance(argument, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in argument.values)
    return argument.value


def _goal_titles(column):
    text = _md_text(BY_NAME[f"{PREFIXES[column]}_title"])
    return [GOAL.match(line.strip()).group(2) for line in text.splitlines() if re.match(r"\s*\d+\. ", line)]


def _key_templates(node):
    """Every `redis_key_template` value anywhere inside the export_data_model output."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from [value] if key == "redis_key_template" else _key_templates(value)
    elif isinstance(node, list):
        for item in node:
            yield from _key_templates(item)


def _calls(node, name):
    return [sub for sub in ast.walk(node) if isinstance(sub, ast.Call) and _dotted(sub.func) == name]


def _ui_names(cell):
    """The globals a cell binds to a UI element: mo.ui.*, or a .batch() or .form() chain."""
    def makes_ui(value):
        return any(
            isinstance(node, ast.Call) and (_dotted(node.func).startswith("mo.ui.") or getattr(node.func, "attr", None) in ("batch", "form"))
            for node in ast.walk(value)
        )
    return {
        target.id for statement in cell.body if isinstance(statement, ast.Assign) and makes_ui(statement.value)
        for target in statement.targets if isinstance(target, ast.Name) and not target.id.startswith("_")
    }


def _chat_model(cell):
    """The function a chat cell passes to mo.ui.chat."""
    [chat] = _calls(cell, "mo.ui.chat")
    return next(node for node in cell.body if isinstance(node, ast.FunctionDef) and node.name == chat.args[0].id)


def _gate(condition):
    """The element a mo.stop condition gates on: `not <x>_button.value` or `<x>_form.value is None`."""
    if isinstance(condition, ast.UnaryOp) and isinstance(condition.op, ast.Not):
        element, suffix = condition.operand, "_button"
    elif (
        isinstance(condition, ast.Compare) and len(condition.ops) == 1 and isinstance(condition.ops[0], ast.Is)
        and isinstance(condition.comparators[0], ast.Constant) and condition.comparators[0].value is None
    ):
        element, suffix = condition.left, "_form"
    else:
        return None
    if isinstance(element, ast.Attribute) and element.attr == "value" and _dotted(element.value).endswith(suffix):
        return element.value.id
    return None


def _is_status(node):
    """A name, attribute, or key that holds an HTTP status, or an int literal in the status range."""
    if isinstance(node, ast.Name):
        return "status" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "status" in node.attr.lower()
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return "status" in str(node.slice.value).lower()
    return isinstance(node, ast.Constant) and type(node.value) is int and 100 <= node.value <= 599


# ---- 1. runs and checks ----


def test_marimo_check_strict():
    """marimo check --strict passes."""
    result = subprocess.run(
        [sys.executable, "-m", "marimo", "check", "--strict", str(NOTEBOOK_PATH)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_credential_free_cells_run_offline(monkeypatch):
    """intro and cr_data_model run through Cell.run() with no config in the environment. cr_data_model
    gives 3 entities and 20 record keys, and every redis_key_template starts with KEY_PREFIX."""
    for name in set(re.findall(r"os\.environ(?:\.get\(|\[)['\"](\w+)['\"]", SOURCE)):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    spec = importlib.util.spec_from_file_location("iris_basics_under_test", NOTEBOOK_PATH)
    notebook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notebook)
    notebook.intro.run()
    _, defs = notebook.cr_data_model.run()   # a raising cell propagates its own exception here
    assert defs["cr_data_model"]["entity_count"] == 3
    assert len(defs["cr_record_keys"]) == 20
    templates = list(_key_templates(defs["cr_data_model"]))
    assert len(templates) == 3, "export_data_model output carries one redis_key_template per entity"
    assert all(template.startswith(notebook.KEY_PREFIX) for template in templates)


def test_no_pep723_header():
    """The notebook runs from the uv project, not as a PEP 723 script."""
    assert "# /// script" not in SOURCE


# ---- 2. layout ----


def test_every_cell_has_a_column_and_columns_are_contiguous():
    """Every cell carries column=, the columns are exactly 0-4, and each column is contiguous in file
    order (marimo issue #3543)."""
    functions = [node for node in TREE.body if isinstance(node, ast.FunctionDef)]
    assert functions == CELLS, "every top-level function is an @app.cell"
    columns = [_config(cell).get("column") for cell in CELLS]
    assert None not in columns, f"{CELLS[columns.index(None)].name} carries no column="
    assert set(columns) == {0, 1, 2, 3, 4}
    assert columns == sorted(columns), "cells of one column are not contiguous in file order"


def test_column_0_is_the_fixed_global_setup():
    """Column 0 holds exactly the introduction and the six client and helper cells, in order."""
    names = [cell.name for cell in _column(0)]
    assert names == COLUMN_0, f"column 0 holds {names}; global setup is fixed"


def test_columns_start_with_a_title_and_hide_title_and_button_code():
    """Each column 1-4 starts with its title cell. The title cell and every run_button cell are
    hide_code=True, and a cell is named *_button exactly when it makes a run_button."""
    for column, prefix in PREFIXES.items():
        assert _column(column)[0].name == f"{prefix}_title", f"column {column} does not start with {prefix}_title"
        assert _config(BY_NAME[f"{prefix}_title"]).get("hide_code") is True, f"{prefix}_title shows its code"
    for cell in CELLS:
        makes_button = any(isinstance(node, ast.Call) and _dotted(node.func) == "mo.ui.run_button" for node in ast.walk(cell))
        assert makes_button == cell.name.endswith("_button"), f"{cell.name}: only *_button cells make a run_button"
        if makes_button:
            assert _config(cell).get("hide_code") is True, f"{cell.name} shows its code"


# ---- 3. three goals ----


def test_each_title_has_three_numbered_goals():
    """Each title cell lists exactly three goals, numbered 1-3, as `N. **Title.** sentence`."""
    for column, prefix in PREFIXES.items():
        lines = [line.strip() for line in _md_text(BY_NAME[f"{prefix}_title"]).splitlines() if re.match(r"\s*\d+\. ", line)]
        for line in lines:
            assert GOAL.match(line), f"{prefix}_title: not `N. **Title.** sentence`: {line}"
        assert [GOAL.match(line).group(1) for line in lines] == ["1", "2", "3"], f"{prefix}_title has {len(lines)} goals, not three"


def test_intro_table_quotes_each_columns_goal_titles():
    """The intro table row for each column lists that column's three goal titles, in order."""
    rows = {}
    for line in _md_text(BY_NAME["intro"]).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and len(cells) == 2:
            rows[cells[0]] = cells[1]
    for column, prefix in PREFIXES.items():
        heading = re.search(r"^\s*# (.+)$", _md_text(BY_NAME[f"{prefix}_title"]), flags=re.M).group(1)
        assert rows.get(heading) == " ".join(_goal_titles(column)), f"the intro row for {heading} does not quote its three goal titles"


# ---- 4. simplicity budgets ----


def test_column_budgets():
    """Each column stays within its cell and line caps (column 0 counts the setup block)."""
    for column, (max_cells, max_lines) in BUDGETS.items():
        cells = _column(column)
        lines = sum(cell.end_lineno - _decorator(cell).lineno + 1 for cell in cells)
        if column == 0:
            setup = _setup_block()
            lines += setup.end_lineno - setup.lineno + 1
        assert len(cells) <= max_cells, f"column {column} has {len(cells)} cells, cap {max_cells}: {CUT}"
        assert lines <= max_lines, f"column {column} has {lines} lines, cap {max_lines}: {CUT}"


def test_file_budget():
    """The whole notebook stays within its cell and line caps."""
    max_cells, max_lines = FILE_BUDGET
    assert len(CELLS) <= max_cells, f"{len(CELLS)} cells, cap {max_cells}: {CUT}"
    assert len(SOURCE.splitlines()) <= max_lines, f"{len(SOURCE.splitlines())} lines, cap {max_lines}: {CUT}"


# ---- 5. no guards or status branching ----


def test_no_isinstance():
    """No isinstance guard anywhere: the reply shapes are known."""
    assert not any(isinstance(node, ast.Name) and node.id == "isinstance" for node in ast.walk(TREE)), (
        "an isinstance guard: trust the verified reply shape, or let it raise"
    )


def test_try_only_where_a_lesson_needs_it():
    """try appears only in rs_load and rs_cleanup (except redis.ResponseError) and cr_create (finally)."""
    seen = set()
    for owner, top in _owners():
        for node in ast.walk(top):
            if not isinstance(node, (ast.Try, ast.TryStar)):
                continue
            assert owner in ALLOWED_TRY and owner not in seen, f"{owner}: try is allowed only once in {sorted(ALLOWED_TRY)}"
            seen.add(owner)
            if ALLOWED_TRY[owner]:
                assert [_dotted(handler.type) for handler in node.handlers] == [ALLOWED_TRY[owner]] and not node.finalbody, (
                    f"{owner}: the one try catches {ALLOWED_TRY[owner]} only"
                )
            else:
                assert not node.handlers and node.finalbody, f"{owner}: the one try is a try/finally with no except"
    assert seen == set(ALLOWED_TRY), f"{sorted(set(ALLOWED_TRY) - seen)} lost its try: drop it from ALLOWED_TRY"


def test_mo_stop_gates_on_a_button_or_a_form_only():
    """Every mo.stop takes one argument, `not <name>_button.value` or `<name>_form.value is None`,
    on an element the cell takes. Inputs and chats need no mo.stop."""
    for cell in CELLS:
        for node in _calls(cell, "mo.stop"):
            element = _gate(node.args[0]) if len(node.args) == 1 and not node.keywords else None
            assert element, f"{cell.name}: mo.stop takes one argument, `not <name>_button.value` or `<name>_form.value is None`"
            assert element in {arg.arg for arg in cell.args.args}, f"{cell.name}: mo.stop gates on {element}, which it does not take"


def test_no_callout_as_a_stop_output():
    """No mo.stop renders a callout: the button cell's lead-in says what pressing it does."""
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and _dotted(node.func) == "mo.stop":
            assert not any(isinstance(sub, ast.Call) and _dotted(sub.func) == "mo.callout" for sub in ast.walk(node)), (
                f"line {node.lineno}: mo.stop renders a callout"
            )


def test_no_status_comparisons():
    """Nothing compares an HTTP status: call_service raises on any status outside ok=."""
    call_service = next(node for node in ast.walk(_setup_block()) if isinstance(node, ast.FunctionDef) and node.name == "call_service")
    allowed = set(ast.walk(call_service))
    for owner, top in _owners():
        for node in ast.walk(top):
            if isinstance(node, ast.Compare) and node not in allowed:
                assert not any(_is_status(operand) for operand in [node.left, *node.comparators]), (
                    f"{owner}, line {node.lineno}: a status check; pass ok= to call_service instead"
                )


def test_no_status_hint_dicts():
    """No dictionary is keyed by HTTP status codes."""
    for node in ast.walk(TREE):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                assert not (isinstance(key, ast.Constant) and re.fullmatch(r"[1-5]\d\d", str(key.value))), (
                    f"line {node.lineno}: a status-hint dict; call_service already raises with the problem title"
                )


# ---- 6. Claude and the chats ----


def test_llm_client_contract():
    """llm_client defines LLM_MODEL == "claude-opus-5-5" and ask_llm(system, messages). ask_llm makes
    one beta messages.create on LLM_MODEL with output_config={"effort": "low"}, the
    server-side-fallback-2026-07-01 beta, and fallbacks="default", and raises on a refusal."""
    cell = BY_NAME["llm_client"]
    models = [statement.value for statement in cell.body if isinstance(statement, ast.Assign) and _dotted(statement.targets[0]) == "LLM_MODEL"]
    assert len(models) == 1 and getattr(models[0], "value", None) == "claude-opus-5-5", 'LLM_MODEL is "claude-opus-5-5"'
    ask = next((node for node in cell.body if isinstance(node, ast.FunctionDef) and node.name == "ask_llm"), None)
    assert ask and [arg.arg for arg in ask.args.args] == ["system", "messages"], "llm_client defines ask_llm(system, messages)"
    creates = [node for node in ast.walk(ask) if isinstance(node, ast.Call) and _dotted(node.func).endswith(".beta.messages.create")]
    assert len(creates) == 1, "ask_llm makes exactly one beta messages.create call"
    keywords = {keyword.arg: keyword.value for keyword in creates[0].keywords}
    literals = {name: ast.literal_eval(keywords[name]) for name in ("output_config", "betas", "fallbacks") if name in keywords}
    assert _dotted(keywords.get("model")) == "LLM_MODEL", "ask_llm calls model=LLM_MODEL"
    assert literals.get("output_config") == {"effort": "low"}, "ask_llm runs at low effort"
    assert "server-side-fallback-2026-07-01" in literals.get("betas", []), "ask_llm sends the server-side-fallback beta"
    assert literals.get("fallbacks") == "default", 'ask_llm passes fallbacks="default"'
    assert any(
        isinstance(node, ast.If) and "refusal" in [sub.value for sub in ast.walk(node.test) if isinstance(sub, ast.Constant)]
        and "stop_reason" in [getattr(sub, "attr", None) for sub in ast.walk(node.test)]
        and any(isinstance(statement, ast.Raise) for statement in node.body)
        for node in ast.walk(ask)
    ), "ask_llm raises when stop_reason is refusal, so no chat stores a refusal as an answer"
    returned = cell.body[-1].value
    assert {_dotted(name) for name in getattr(returned, "elts", [returned])} == {"LLM_MODEL", "ask_llm"}


def test_chat_cells_own_their_controls():
    """Search, LangCache, and Agent Memory each have one chat cell. It defines its controls beside the
    mo.ui.chat, its model function reads them at call time, and neither the cell nor the model reads
    a UI element another cell defines: a change to one would re-run the cell and wipe the history."""
    chats = {cell.name: cell for cell in CELLS if _calls(cell, "mo.ui.chat")}
    assert set(chats) == CHAT_CELLS, f"the chat cells are {sorted(chats)}"
    for name, cell in chats.items():
        assert len(_calls(cell, "mo.ui.chat")) == 1, f"{name} makes more than one chat"
        model = _chat_model(cell)
        reads = {node.id for node in ast.walk(model) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
        assert (_ui_names(cell) - {name}) & reads, f"{name}: the model function reads no control defined in this cell"
        foreign = {element for other in CELLS if other is not cell for element in _ui_names(other)}
        leaked = foreign & (reads | {arg.arg for arg in cell.args.args})
        assert not leaked, f"{name} reads {sorted(leaked)}, defined in another cell: move the control into {name}"


def test_claude_is_called_once_per_chat_turn():
    """ask_llm is called only from a chat model function, at most once per turn, and never in a loop."""
    models = {name: _chat_model(BY_NAME[name]) for name in sorted(CHAT_CELLS)}
    for name, model in models.items():
        assert len(_calls(model, "ask_llm")) <= 1, f"{name}: more than one ask_llm call per turn"
    models = list(models.values())
    inside = {id(call) for model in models for call in _calls(model, "ask_llm")}
    for owner, top in _owners():
        for call in _calls(top, "ask_llm"):
            assert id(call) in inside, f"{owner}, line {call.lineno}: ask_llm outside a chat model function"
        for loop in ast.walk(top):
            if isinstance(loop, (ast.For, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                assert not _calls(loop, "ask_llm"), f"{owner}: ask_llm inside a loop"


# ---- 7. globals ----


def test_uppercase_names_live_in_column_0():
    """UPPERCASE names are defined only in column 0 or the setup block."""
    for cell in CELLS:
        if _config(cell).get("column") == 0:
            continue
        for node in ast.walk(cell):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                name = node.id
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node is not cell:
                name = node.name
            elif isinstance(node, ast.alias):
                name = node.asname or node.name
            else:
                continue
            assert not re.fullmatch(r"[A-Z][A-Z0-9_]+", name), f"{cell.name} defines {name}: UPPERCASE is for column 0"


def test_setup_block_defines_exactly_the_shared_names():
    """The setup block defines RUN_TAG, KEY_PREFIX, KEY_TTL_SECONDS, iris_session, and call_service."""
    bound = set()
    for statement in _setup_block().body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            continue
        assert isinstance(statement, (ast.FunctionDef, ast.Assign)), f"unexpected {type(statement).__name__} in the setup block"
        if isinstance(statement, ast.FunctionDef):
            bound.add(statement.name)
        else:
            bound.update(name.id for target in statement.targets for name in ast.walk(target) if isinstance(name, ast.Name))
    assert bound == SETUP_NAMES, f"the setup block adds {sorted(bound - SETUP_NAMES)} and lacks {sorted(SETUP_NAMES - bound)}"
