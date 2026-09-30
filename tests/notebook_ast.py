"""Read a notebook's cells from its source, without running it. For the structure tests.

marimo returns from a cell exactly the globals other cells use, and gives it those it needs
as parameters, so a cell's parameters are what it reads (its refs) and its return values are
what it offers (its defs).
"""

import ast
from typing import NamedTuple


# A column's helper functions carry its prefix; the column number is the position in this tuple.
COLUMN_PREFIXES = ("rs_", "lc_", "cr_", "am_")


class Item(NamedTuple):
    kind: str  # "cell" or "function"
    column: int | None
    hide_code: bool | None
    node: ast.FunctionDef


def items(source):
    """Every @app.cell and @app.function in the file, in order."""
    found = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            target = call.func if call else decorator
            if (
                isinstance(target, ast.Attribute)
                and target.attr in ("cell", "function")
                and isinstance(target.value, ast.Name)
                and target.value.id == "app"
            ):
                kwargs = {
                    k.arg: k.value.value
                    for k in (call.keywords if call else [])
                    if isinstance(k.value, ast.Constant)
                }
                found.append(Item(target.attr, kwargs.get("column"), kwargs.get("hide_code"), node))
    return found


def setup_body(source):
    """The statements of the `with app.setup(...)` block."""
    for node in ast.parse(source).body:
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            target = item.context_expr
            target = target.func if isinstance(target, ast.Call) else target
            if isinstance(target, ast.Attribute) and target.attr == "setup":
                return node.body
    return []


def helpers(source):
    """Each column's helper functions, in file order, as Items of kind "function".

    They are defined at the end of the setup block, not as @app.function items: marimo edit
    makes a cell of every item, and nearly 200 hidden helpers made it slow. A helper is a
    function there whose name starts with a column prefix, so the shared functions
    (`env`, `notes`, ...) are not among them.
    """
    found = []
    for node in setup_body(source):
        if isinstance(node, ast.FunctionDef):
            for column, prefix in enumerate(COLUMN_PREFIXES):
                if node.name.startswith(prefix):
                    found.append(Item("function", column, True, node))
    return found


def config_panel(cells, name):
    """The cell that defines `name` and the one before it: a column's settings.

    The prose (what to fill in, where to get it, the warnings) is a hidden cell just above the
    fields, so the visible code is only the fields, the button and the reset token.
    """
    at = next(i for i, c in enumerate(cells) if name in defs(c))
    return [cells[at - 1], cells[at]]


def all_items(source):
    """Every cell, then every column helper."""
    return [*items(source), *helpers(source)]


def column_items(source, column):
    """One column's cells in file order, then its helpers."""
    return [i for i in items(source) if i.column == column] + [
        h for h in helpers(source) if h.column == column
    ]


def refs(node):
    """The globals a cell reads (its parameters)."""
    return [arg.arg for arg in node.args.args]


def defs(node):
    """The globals a cell offers to others (what it returns)."""
    for statement in node.body:
        if isinstance(statement, ast.Return) and statement.value is not None:
            value = statement.value
            return [e.id for e in (value.elts if isinstance(value, ast.Tuple) else [value])]
    return []


def call_name(call):
    """A call's dotted name, 'pressed' or 'mo.ui.text'; '' if it is not a plain name."""
    parts, func = [], call.func
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    return ".".join([func.id, *reversed(parts)]) if isinstance(func, ast.Name) else ""


def calls(node, name):
    """Every call in node to the function with this dotted name."""
    return [c for c in ast.walk(node) if isinstance(c, ast.Call) and call_name(c) == name]


def keyword(call, name):
    """The expression given as name=... in a call, or None."""
    return next((k.value for k in call.keywords if k.arg == name), None)
