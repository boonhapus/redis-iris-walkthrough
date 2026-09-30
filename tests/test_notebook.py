"""Notebook-level tests: app.run() with no credentials, marimo check --strict, and the layout and
budget rules from the plan.

The file under test is iris_basics.py unless IRIS_BASICS_NOTEBOOK names another file (a column
builder's assembled preview). The three budget and layout tests apply to the assembled notebook
only, so they skip while the file still carries a `# @@ COLUMN` marker or the variable is set.
"""

import ast
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = Path(os.environ.get("IRIS_BASICS_NOTEBOOK", ROOT / "iris_basics.py"))
if not NOTEBOOK_PATH.is_absolute():
    NOTEBOOK_PATH = ROOT / NOTEBOOK_PATH
SOURCE = NOTEBOOK_PATH.read_text(encoding="utf-8")

# The ten names the notebook reads. Deleting them is what keeps every test offline: app.run()
# loads no .env, and a configure cell whose names are missing stops on its not-configured note.
CONFIG_NAMES = (
    "REDIS_URL",
    "LANGCACHE_HOST", "LANGCACHE_CACHE_ID", "LANGCACHE_API_KEY",
    "CTX_ADMIN_KEY", "CTX_API_URL", "CTX_MCP_URL",
    "MEMORY_API_BASE_URL", "MEMORY_STORE_ID", "MEMORY_API_KEY",
)

# Configure cell -> the client or session it binds only once configured.
CONFIGURE_CELLS = {
    "rs_connect": "rs_client",
    "lc_configure": "lc_service",
    "cr_configure": "cr_admin_service",
    "am_configure": "am_service",
}

SETUP_NAMES = {"RUN_TAG", "KEY_PREFIX", "KEY_TTL_SECONDS", "MASKED_HEADERS", "call_service", "connect_redis"}

NOT_ASSEMBLED = "# @@ COLUMN" in SOURCE or "IRIS_BASICS_NOTEBOOK" in os.environ
assembled_only = pytest.mark.skipif(
    NOT_ASSEMBLED,
    reason=(
        "budget and layout rules apply to the assembled iris_basics.py only: the file under test "
        "still carries a '# @@ COLUMN' marker or IRIS_BASICS_NOTEBOOK is set"
    ),
)


def _load_notebook():
    spec = importlib.util.spec_from_file_location("iris_basics_under_test", NOTEBOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _key_templates(node):
    """Every `redis_key_template` value anywhere inside the export_data_model output."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "redis_key_template":
                yield value
            else:
                yield from _key_templates(value)
    elif isinstance(node, list):
        for item in node:
            yield from _key_templates(item)


def test_notebook_runs_without_credentials(monkeypatch):
    for name in CONFIG_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    notebook = _load_notebook()
    outputs, defs = notebook.app.run()  # a raising cell propagates its own exception here
    assert "RUN_TAG" in defs
    for cell, client in CONFIGURE_CELLS.items():
        if f"def {cell}(" in SOURCE:
            assert client not in defs, f"{cell} ran on without credentials and bound {client}"
    if "def cr_data_model(" in SOURCE:
        assert defs["cr_data_model"]["entity_count"] == 3
        assert len(defs["cr_record_keys"]) == 20
        templates = list(_key_templates(defs["cr_data_model"]))
        assert templates, "export_data_model output carries no redis_key_template"
        assert all(template.startswith(defs["KEY_PREFIX"]) for template in templates)


def test_marimo_check_strict():
    result = subprocess.run(
        [sys.executable, "-m", "marimo", "check", "--strict", str(NOTEBOOK_PATH)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@assembled_only
def test_columns_are_complete_and_contiguous():
    decorators = re.findall(r"^@app\.cell\((.*)\)\s*$", SOURCE, flags=re.M)
    assert decorators, "no @app.cell decorator found"
    columns = []
    for arguments in decorators:
        match = re.search(r"column=(\d)", arguments)
        assert match, f"@app.cell({arguments}) carries no column="
        columns.append(int(match.group(1)))
    assert set(columns) == {0, 1, 2, 3}
    assert columns == sorted(columns), "cells of one column are not contiguous in file order (marimo issue #3543)"


def test_no_pep723_header():
    assert "# /// script" not in SOURCE


@assembled_only
def test_cell_count_budget():
    count = SOURCE.count("@app.cell(")
    assert 42 <= count <= 46, f"{count} cells; 45 planned, 42-46 allowed"


@assembled_only
def test_line_budget():
    lines = len(SOURCE.splitlines())
    assert lines <= 1500, f"{lines} lines; about 1,450 planned, 1,500 is the ceiling"


def test_setup_block_is_minimal():
    tree = ast.parse(SOURCE, filename=str(NOTEBOOK_PATH))
    setup_blocks = [
        node for node in tree.body
        if isinstance(node, ast.With) and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "setup"
            for item in node.items
        )
    ]
    assert len(setup_blocks) == 1, "expected exactly one `with app.setup(...)` block"
    bound = set()
    for statement in setup_blocks[0].body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(statement, (ast.FunctionDef, ast.ClassDef)):
            bound.add(statement.name)
        elif isinstance(statement, ast.Assign):
            for target in statement.targets:
                bound.update(name.id for name in ast.walk(target) if isinstance(name, ast.Name))
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            bound.add(statement.target.id)
        else:
            pytest.fail(f"unexpected statement in the setup block: {type(statement).__name__}")
    assert bound == SETUP_NAMES
