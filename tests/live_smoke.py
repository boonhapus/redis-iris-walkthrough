"""Live smoke run of iris_basics.py: every cell, in file order, through marimo's Cell.run().

Run by hand (the file has no test_ prefix, so pytest never collects it), with the configuration
in the process environment: Cell.run() and App.run() load no .env, so export the names first or
prefix the command with them.

    uv run python tests/live_smoke.py

One line per cell: the cell name, the seconds it took, and one of `stopped` (the cell called
mo.stop), `stopped: an ancestor stopped` (the cell reads a name a stopped cell defines, so the
notebook would never run it), `error: <ExceptionType>`, `skipped: an ancestor errored` (a cell
that reads a name an errored cell should have defined; without either guard marimo would re-run
that ancestor live and misreport the cell), or the first 120 characters of the output's plain
text. Definitions thread from cell to cell in file order, so a cell sees what the cells above it
defined, and every reference whose name ends in `_button` is replaced with an object whose
`.value` is True, so every button counts as pressed, Clean up included. A stopped cell's partial
definitions (the names it assigned before mo.stop) are never threaded on, which is what keeps a
seed cell from sending requests to a service whose configure cell failed its check. Nothing here
prints a header, a request body, or an environment value: only the visible text of an output,
redacted of any configured value as a second guard, then cut to 120 characters. The exit code is
1 if any cell raised, otherwise 0.

Verified on marimo 0.25.0: `Cell.run(**refs)` accepts only names in the cell's references, runs
the ancestors that define whatever was not passed, injects the setup block's names itself, and
turns a mo.stop in the cell or an ancestor into a normal return of the stop's output. The stop is
told apart from an ordinary output through marimo's `by_refs._classify`, which sees every
MarimoStopError before the runner swallows it.
"""

import html
import importlib.util
import os
import re
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = ROOT / "iris_basics.py"

# The ten names the notebook reads. Their values are only ever compared against the printed line.
CONFIG_NAMES = (
    "REDIS_URL",
    "LANGCACHE_HOST", "LANGCACHE_CACHE_ID", "LANGCACHE_API_KEY",
    "CTX_ADMIN_KEY", "CTX_API_URL", "CTX_MCP_URL",
    "MEMORY_API_BASE_URL", "MEMORY_STORE_ID", "MEMORY_API_KEY",
)


def _plain_text(output):
    """The visible text of a cell output: tags (and everything inside them) gone, entities decoded."""
    if output is None:
        return ""
    text = getattr(output, "text", None)
    if not isinstance(text, str):
        text = str(output)
    text = re.sub(r"<[^>]*>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _redact(line):
    for name in CONFIG_NAMES:
        value = os.environ.get(name, "")
        if len(value) >= 4:
            line = line.replace(value, "***")
    return line


def main():
    spec = importlib.util.spec_from_file_location("iris_basics_live", NOTEBOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = module.app

    from marimo._runtime.runner import by_refs

    stops = []
    classify = by_refs._classify

    def classify_and_record(result):
        stop = classify(result)
        if stop is not None:
            stops.append(stop)
        return stop

    by_refs._classify = classify_and_record

    definitions = {}
    errored = set()   # names an errored cell should have defined; a cell that reads one is skipped, not re-run through its ancestors
    stopped = set()   # names a stopped cell defines; a cell that reads one never runs in the notebook, so it never runs here
    exit_code = 0
    for data in app._cell_manager.cell_data():
        cell = getattr(data, "cell", None)
        if cell is None or data.cell_id == app._cell_manager.setup_cell_id:   # the setup block is not a cell
            continue
        if set(cell._cell.refs) & errored:
            errored |= set(cell._cell.defs)   # its own defs are missing too, so its descendants are skipped, not re-run
            print(f"{data.name:<22}    0.0s  skipped: an ancestor errored", flush=True)
            continue
        if set(cell._cell.refs) & stopped:
            stopped |= set(cell._cell.defs)
            print(f"{data.name:<22}    0.0s  stopped: an ancestor stopped", flush=True)
            continue
        refs = {}
        for name in cell._cell.refs:
            if name.endswith("_button"):
                refs[name] = types.SimpleNamespace(value=True)
            elif name in definitions:
                refs[name] = definitions[name]
        stops.clear()
        started = time.perf_counter()
        try:
            output, defs = cell.run(**refs)
        except Exception as exc:   # a real error; mo.stop never reaches here
            status = f"error: {type(exc).__name__}"
            errored |= set(cell._cell.defs)
            exit_code = 1
        else:
            if stops:   # run_cell_sync returns the names assigned before the stop as defs; the notebook never hands them on
                stopped |= set(cell._cell.defs)
                status = "stopped"
            else:
                definitions.update(defs)
                status = _redact(_plain_text(output))[:120]
        print(_redact(f"{data.name:<22} {time.perf_counter() - started:6.1f}s  {status}"), flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
