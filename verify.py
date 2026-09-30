# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Verify the Iris basics notebook. Pure Python, Windows first, standard library only.

    uv run --no-project --python 3.13 verify.py
        Offline (default). Works on a temp copy in an empty folder with every setting
        removed from the environment, so it needs no credentials and cannot touch a
        service: Python check, `marimo check --strict`, pytest, and a headless
        `marimo export html` that must exit 0. Skipped tests (the calibration ones, when
        the embedding model is not cached) make the pytest row WARN, not PASS.

    uv run --no-project --python 3.13 verify.py --preflight --env-file P
        Before a meeting. Warms the uv environment and the embedding-model cache, then
        runs each column's read-only connection test against the settings in P and
        prints a go / no-go table. GO needs at least one column that really answered
        its test; with none tested the verdict is NOT TESTED and the exit code is 1.
        Values in P are never read here or printed.

    uv run --no-project --python 3.13 verify.py --live --env-file P [--extraction-wait S]
        Full live run against the services in P (plan section 3.7). It exports the notebook
        twice under a fresh RUN, once with IRIS_BASICS_AUTORUN=demo and once with =cleanup, and
        judges each export by its session file, not its exit code: an error output, or a red
        error callout that is not part of the demo, in a configured column is a FAIL. The
        ledger is read the moment the cleanup export is over, and whatever it still shows live
        is a FAIL of the notebook's own cleanup; then, in a `finally`, a safety-net sweep with
        the notebook's own cleanup functions puts it right. It then sets a five-second
        LangCache sentinel and checks it expires, waits S seconds (default 660) for Agent
        Memory extraction and sweeps again, and prints a footprint report (Redis keys and
        indexes, ledger, Agent Memory, Context Retriever surfaces) that must all be empty.
        Last, it scans the export html and session json for every secret in P, in every
        spelling. Values in P are read only inside child processes and never printed; a
        summary json (no secrets) is written to --summary or the temp folder, and is kept.
        Columns whose settings P lacks are skipped, and the verdict says how many of the four
        were exercised. Run --preflight first. It writes to shared accounts, so it is meant
        to be run once, by hand.

Python is pinned to 3.13 everywhere: uv's default here is 3.14, which breaks Context
Retriever's ContextModel.
"""

import argparse
import ast
import base64
import html
import importlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, quote_plus, unquote, urlsplit

ROOT = Path(__file__).resolve().parent
NOTEBOOK = "iris_basics.py"
# `uv run` exports its own path as $UV, which is on PATH for nobody in a Git Bash session.
UV = os.environ.get("UV") or shutil.which("uv")
PYTHON = "3.13"
SETTING_PREFIXES = (
    "REDIS_", "LANGCACHE_", "CTX_", "MEMORY_", "MCP_", "OPENAI_", "DEMO_", "IRIS_BASICS_",
    "IRIS_NB_",  # tests/conftest.py imports whatever module these name, not the temp copy
)  # fmt: skip

# What each column needs before its live cells can run (plan section 3.5). Each inner
# list is "any one of"; every inner list must be satisfied.
COLUMNS = {
    "rs": ("0 Redis Search", [["REDIS_URL", "REDIS_HOST"]]),
    "lc": ("1 LangCache", [["LANGCACHE_HOST"], ["LANGCACHE_CACHE_ID"], ["LANGCACHE_API_KEY"]]),
    "cr": ("2 Context Retriever", [["CTX_ADMIN_KEY"], ["REDIS_URL", "REDIS_HOST"]]),
    "am": ("3 Agent Memory", [["MEMORY_API_BASE_URL"], ["MEMORY_STORE_ID"], ["MEMORY_API_KEY"]]),
}


@dataclass
class Row:
    name: str
    status: str  # PASS | FAIL | SKIP | WARN
    seconds: float = 0.0
    detail: str = ""
    output: str = ""  # captured output, shown only when the row failed
    tested: bool = False  # a column row whose connection test ran (live: whose demo was judged)


# ---- plumbing ------------------------------------------------------------------------
def scrubbed_env(**extra):
    """The parent environment minus every setting and test switch the notebook knows about.

    A run therefore sees only what it is handed, and can never pick up a credential that
    happens to be in the shell, or a leftover IRIS_NB_MODULE from a column phase.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(SETTING_PREFIXES)}
    for name in ("VIRTUAL_ENV", "PYTHONPATH"):
        env.pop(name, None)
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", IRIS_BASICS_NO_DOTENV="1")
    env.update(extra)
    return env


def sh(cmd, cwd, env, timeout):
    done = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )  # fmt: skip
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def uv_command(*command):
    """The argv that runs a command in the notebook's own dependency set, on the pinned Python."""
    return [UV, "run", "--no-project", "--python", PYTHON,
            "--with-requirements", NOTEBOOK, "--with", "pytest", *command]  # fmt: skip


def uv_run(work, env, *command, timeout=300):
    """Run a command in the notebook's own dependency set, on the pinned Python."""
    return sh(uv_command(*command), work, env, timeout)


def stage(tmp, with_tests, notebook=None, extra_tests=()):
    """Copy the notebook (and tests) into an empty folder, away from any .env or __marimo__.

    notebook replaces iris_basics.py in the copy, under that name, and extra_tests are added to
    the copied tests/: how a column phase verifies base.py plus its own fragment and tests
    (.agents/build/assemble.py --only N) without touching the shared tests/ folder.
    """
    work = tmp / "work"
    work.mkdir()
    shutil.copy2(notebook or ROOT / NOTEBOOK, work / NOTEBOOK)
    if with_tests:
        for name in (".env.example", ".gitignore", "verify.py"):  # the tests read these too
            shutil.copy2(ROOT / name, work / name)
        ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")
        shutil.copytree(ROOT / "tests", work / "tests", ignore=ignore)
        for extra in extra_tests:
            shutil.copy2(extra, work / "tests" / Path(extra).name)
    return work


def step(name, action):
    """Time action() -> (status, detail, output) and turn any exception into a FAIL row."""
    started = time.perf_counter()
    try:
        status, detail, output = action()
    except subprocess.TimeoutExpired as exc:
        status, detail, output = "FAIL", f"timed out after {exc.timeout:.0f} s", ""
    except Exception as exc:  # a verifier must report, not crash
        status, detail, output = "FAIL", f"{type(exc).__name__}: {exc}", ""
    return Row(name, status, time.perf_counter() - started, detail, output)


def outcome(rc, output, ok_detail=""):
    return ("PASS", ok_detail, "") if rc == 0 else ("FAIL", f"exit {rc}", output)


def print_table(rows):
    width = max(len(r.name) for r in rows)
    print(f"\n{'step'.ljust(width)}  result  seconds  detail")
    print(f"{'-' * width}  ------  -------  ------")
    for r in rows:
        print(f"{r.name.ljust(width)}  {r.status.ljust(6)}  {r.seconds:7.1f}  {r.detail}")
    for r in rows:
        if r.status == "FAIL" and r.output.strip():
            tail = "\n".join(r.output.strip().splitlines()[-40:])
            print(f"\n--- {r.name}: last output ---\n{tail}")
    print()


# ---- offline ------------------------------------------------------------------------
def python_check(work, env):
    code = (
        "import sys; assert sys.version_info < (3, 14), sys.version; print(sys.version.split()[0])"
    )
    rc, out = uv_run(work, env, "python", "-c", code, timeout=900)
    # First run resolves and installs the PEP 723 dependencies (~110 wheels).
    return outcome(rc, out, f"python {out.strip().splitlines()[-1]}" if rc == 0 else "")


def pytest_outcome(rc, out):
    """The pytest row. Skipped tests are checks that never ran, so they turn PASS into WARN.

    The calibration tests skip when the embedding model is not in the local cache (tests never
    touch the network), and would otherwise leave a fresh machine "verified" without them.
    """
    summary = next(
        (ln for ln in reversed(out.splitlines()) if " passed" in ln or " failed" in ln), ""
    ).strip("= ")
    status, detail, output = outcome(rc, out, summary)
    if status == "PASS" and " skipped" in summary:
        return "WARN", f"{summary}; skipped checks never ran (--preflight caches the model)", ""
    return status, detail, output


def offline_steps(work, env):
    """The three checks every phase must keep green. Returns rows."""
    rows = []

    def strict():
        return outcome(*uv_run(work, env, "python", "-m", "marimo", "check", "--strict", NOTEBOOK))

    def tests():
        # ~1250 tests, a dozen of them real `marimo export`s of the whole notebook: ~8 min.
        return pytest_outcome(
            *uv_run(work, env, "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests",
                    timeout=1800)
        )  # fmt: skip

    def export():
        target = work / "out.html"
        rc, out = uv_run(work, env, "python", "-m", "marimo", "-y", "export", "html",
                         "--no-sandbox", NOTEBOOK, "-o", str(target), "-f")  # fmt: skip
        if rc == 0 and not target.exists():
            return "FAIL", "exit 0 but no output file", out
        return outcome(rc, out, f"{target.stat().st_size // 1024} KB html" if rc == 0 else "")

    for name, action in (
        ("marimo check --strict", strict),
        ("pytest", tests),
        ("marimo export html", export),
    ):
        rows.append(step(name, action))
    return rows


def offline(keep, notebook=None, extra_tests=()):
    """The offline checks. For the real notebook every column's tests must find their column
    (IRIS_NB_REQUIRE_ALL); a partial assembly deselects the columns it does not contain."""
    tmp = Path(tempfile.mkdtemp(prefix="iris_basics_verify_"))
    try:
        work = stage(tmp, with_tests=True, notebook=notebook, extra_tests=extra_tests)
        env = scrubbed_env() if notebook else scrubbed_env(IRIS_NB_REQUIRE_ALL="1")
        rows = [step("python < 3.14, dependencies resolve", lambda: python_check(work, env))]
        if rows[0].status == "PASS":
            rows += offline_steps(work, env)
        return rows
    finally:
        if keep:
            print(f"kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


# ---- preflight ----------------------------------------------------------------------
# Runs inside the notebook's environment. It prints setting NAMES and each column's
# scrubbed connection-test verdict, never a value. A column adds its test as an
# @app.function called <key>_test_connection() that returns (ok, detail) and is read-only.
CHILD = r"""
import importlib, json, sys
nb = None
try:
    nb = importlib.import_module("iris_basics")
    present = nb.env_present()
    report = {"present": present, "columns": {}}
    for key, groups in json.loads(sys.argv[1]).items():
        missing = [" or ".join(g) for g in groups if not any(n in present for n in g)]
        entry = {"missing": missing, "ok": None, "detail": ""}
        test = getattr(nb, f"{key}_test_connection", None)
        if not missing and test is not None:
            try:
                ok, detail = test()
            except Exception as exc:
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            entry.update(ok=bool(ok), detail=nb.scrub(detail)[:160])
        entry["has_test"] = test is not None
        report["columns"][key] = entry
    print("PREFLIGHT " + json.dumps(report))
except Exception as exc:
    # Never let a traceback (which can quote a setting) reach the console.
    text = nb.scrub(f"{type(exc).__name__}: {exc}") if nb else type(exc).__name__
    print("PREFLIGHT_ERROR " + text[:200])
    sys.exit(3)
"""

MODEL_WARMUP = r"""
from huggingface_hub import snapshot_download
files = dict(allow_patterns=["config.json", "model.safetensors", "tokenizer.json", "README.md"])
try:
    snapshot_download("minishlab/potion-base-8M", local_files_only=True, **files)
    print("cached")
except Exception:
    snapshot_download("minishlab/potion-base-8M", **files)
    print("downloaded")
"""


def column_rows(report):
    """One go / no-go row per column from the child's report.

    Only a column that ran its own connection test counts as tested; a column with missing
    settings is skipped and one with no test yet only warns.
    """
    rows = []
    for key, (label, _) in COLUMNS.items():
        found = report.get("columns", {}).get(key)
        if found is None:
            continue
        name = f"column {label}"
        if found["missing"]:
            detail = "offline only; missing " + ", ".join(found["missing"])
            rows.append(Row(name, "SKIP", detail=detail))
        elif not found["has_test"]:
            # TODO(phase 2-4): each column phase adds <key>_test_connection(); until then
            # this is a warning, not a failure.
            rows.append(Row(name, "WARN", detail=f"no {key}_test_connection() yet"))
        else:
            status = "PASS" if found["ok"] else "FAIL"
            rows.append(Row(name, status, detail=found["detail"], tested=True))
    return rows


def preflight(env_file, keep, skip_model):
    rows = []
    # Absolute, because the child runs in a temp folder where a relative path leads nowhere.
    path = Path(env_file).expanduser().resolve()
    rows.append(Row("settings file exists", "PASS" if path.is_file() else "FAIL", detail=path.name))
    if rows[-1].status == "FAIL":
        return rows
    tmp = Path(tempfile.mkdtemp(prefix="iris_basics_preflight_"))
    try:
        work = stage(tmp, with_tests=False)
        env = scrubbed_env(IRIS_BASICS_ENV_FILE=str(path))
        rows.append(step("warm uv environment", lambda: python_check(work, env)))
        if rows[-1].status != "PASS":
            return rows

        def model():
            rc, out = uv_run(work, env, "python", "-c", MODEL_WARMUP, timeout=600)
            return outcome(rc, out, out.strip().splitlines()[-1] if rc == 0 else "")

        rows.append(Row("warm embedding-model cache", "SKIP", detail="--skip-model") if skip_model
                    else step("warm embedding-model cache", model))  # fmt: skip

        report = {}

        def connections():
            rc, out = uv_run(
                work, env, "python", "-c", CHILD, json.dumps({k: v[1] for k, v in COLUMNS.items()})
            )
            line = next((ln for ln in out.splitlines() if ln.startswith("PREFLIGHT ")), None)
            if rc != 0 or line is None:
                # Raw output is withheld: a crash could quote a setting. The child reports
                # its own scrubbed error line instead.
                error = next(
                    (ln for ln in out.splitlines() if ln.startswith("PREFLIGHT_ERROR")), ""
                )
                return "FAIL", f"exit {rc} {error[len('PREFLIGHT_ERROR') :].strip()}", ""
            report.update(json.loads(line[len("PREFLIGHT ") :]))
            if not report["present"]:
                return "FAIL", f"no settings found in {path.name}: is it the right file?", ""
            return "PASS", f"{len(report['present'])} settings found", ""

        rows.append(step("read settings, run connection tests", connections))
        rows += column_rows(report)
        return rows
    finally:
        if keep:
            print(f"kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


# ---- live ---------------------------------------------------------------------------
# Plan section 3.7. Three rules keep a run safe on shared accounts:
#   1. This process never sees a secret. It hands the settings file's path to child processes,
#      which read it inside the notebook's dependency set and report only names and counts.
#      Nothing a child prints reaches the console unless the notebook's own scrub() has seen it.
#   2. Everything the run makes carries a fresh RUN, and only what that RUN's ledger names is
#      deleted, by the notebook's own cleanup functions and through its choke points.
#   3. The sweep is in a `finally`, so a failed, timed-out or interrupted export still gets one.
TIMEOUTS = {"demo": 1800, "cleanup": 1200, "child": 900, "sentinel": 300}  # seconds
EXTRACTION_WAIT = 660  # Agent Memory extraction runs about every 11 minutes
SENTINEL_TTL_MS = 5000
SENTINEL_WAIT = 60  # seconds the sentinel gets to expire
SENTINEL_POLL = 2
SENTINEL_GRACE = SENTINEL_TTL_MS // 1000 + 3  # a search may take a moment to find a new entry
MIN_SECRET = 4  # the notebook's own floor: a shorter string would match ordinary text
KEYS = list(COLUMNS)  # column N of the notebook is KEYS[N]
SHARED = "shared"  # the setup block and the intro cell, which belong to no column
UNATTRIBUTED = "unattributed"
PHASES = ("demo", "cleanup")
LIVE_STATES = ("created", "intent")  # a ledger item in either has not been deleted
SESSION = Path("__marimo__") / "session" / f"{NOTEBOOK}.json"
CHILD_SPEC = "child_spec.json"  # how a child is told what to do: JSON in its own folder
CHILD_CODE = "import verify; verify.child_main()"
RESULT, FAILURE = "LIVE_RESULT ", "LIVE_ERROR "
# Rows are shown in this order whatever order they were earned in.
GROUPS = ("settings", "warm", "probe", "demo", "demo cells", "cleanup", "cleanup cells", "sweep",
          "sentinel", "late", "footprint", "scan", "summary")  # fmt: skip


# ---- live: reading a session file ---------------------------------------------------
class Cell(NamedTuple):
    kind: str  # "cell" or "function"
    column: int | None
    line: int
    name: str
    cleanup: bool = False  # a column's Clean up cell: it asks pressed(..., stage="cleanup")


def presses_cleanup(node):
    """True for a cell that asks pressed(button, stage="cleanup"): the cell that cleans up."""
    return any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "pressed"
        and any(
            k.arg == "stage" and isinstance(k.value, ast.Constant) and k.value.value == "cleanup"
            for k in call.keywords
        )
        for call in ast.walk(node)
    )


def notebook_cells(source):
    """Every @app.cell and @app.function of the notebook, in file order (marimo's order)."""
    found = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            target = call.func if call else decorator
            if not (
                isinstance(target, ast.Attribute)
                and target.attr in ("cell", "function")
                and isinstance(target.value, ast.Name)
                and target.value.id == "app"
            ):
                continue
            column = next(
                (
                    k.value.value
                    for k in (call.keywords if call else [])
                    if k.arg == "column" and isinstance(k.value, ast.Constant)
                ),
                None,
            )
            found.append(Cell(target.attr, column, node.lineno, node.name, presses_cleanup(node)))
            break
    return found


_TAG = r"""((?:\s+[\w-]+=(?:'[^']*'|"[^"]*"))*)\s*/?>"""  # a tag's attributes, up to its >
_CALLOUT = re.compile(r"<marimo-callout-output" + _TAG)
_TABLE = re.compile(r"<marimo-table" + _TAG)
_ATTRIBUTE = re.compile(r"""([\w-]+)=(?:'([^']*)'|"([^"]*)")""")


def attributes(tag):
    """{name: raw value} for the attributes a tag regex captured."""
    return {name: single or double for name, single, double in _ATTRIBUTE.findall(tag)}


def attribute_value(raw):
    """What marimo put in a data-* attribute: HTML-escaped JSON, sometimes JSON inside JSON."""
    value = html.unescape(raw)
    try:
        value = json.loads(value)
    except ValueError:
        return value
    if isinstance(value, str) and value[:1] in ("[", "{"):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    return value


def plain_text(markup):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", markup)).split())


def callouts(markup):
    """[(position, kind, text)] for every callout in rendered html.

    status_callout(kind, ...) is mo.callout(..., kind=...), which renders as a
    <marimo-callout-output> whose data-kind holds "danger" for an error and whose data-html
    holds the message. A run_button of kind danger is a different tag and is not found.
    """
    found = []
    for tag in _CALLOUT.finditer(markup):
        attrs = attributes(tag.group(1))
        kind = str(attribute_value(attrs.get("data-kind", "")))
        found.append(
            (tag.start(), kind, plain_text(str(attribute_value(attrs.get("data-html", "")))))
        )
    return found


def expected_red(text, after_break):
    """True for a red callout that is part of the demo, not a failure.

    The AUTORUN banner is red on purpose; LangCache's "Shared cache" note is a standing
    warning; and Context Retriever's Break it exists to show a rejection, so a red rejection
    (or tool error) that comes after the Break it heading is what it should look like.
    """
    if text.startswith(("AUTORUN is on", "Shared cache:")):
        return True
    return after_break and text.startswith(("Rejected", "The tool ran and reported an error"))


def cell_group(position, cells, mapped):
    """The column key a session cell belongs to. position 0 is the setup block, 1 the intro."""
    if not mapped:
        return UNATTRIBUTED
    column = cells[position - 1].column if position else None
    return (
        KEYS[column] if position > 1 and column is not None and 0 <= column < len(KEYS) else SHARED
    )


def describe(position, cells, mapped):
    if not mapped:
        return f"session cell {position}"
    if position == 0:
        return "the setup block"
    cell = cells[position - 1]
    return (
        f"{cell.name}() at line {cell.line}"
        if cell.kind == "function"
        else f"cell at line {cell.line}"
    )


def new_group():
    return {
        "cells": 0, "stopped": 0, "errors": [], "callouts": {}, "unexpected": [], "expected": 0,
        "unfinished": [],
    }  # fmt: skip


def judge_session(session, cells, scrub, phase):
    """What one export's session file says, by column.

    session is the parsed __marimo__/session json and cells notebook_cells(source). It has one
    entry for the setup block and one per cell, in file order; if the counts do not agree the
    cells cannot be told apart and everything lands in "unattributed". Per group: how many
    cells there were and how many stopped upstream (ancestor-stopped is a cell waiting for a
    button, and normal), the error outputs, the red callouts that are not part of the demo,
    and the warn callouts of its Clean up cell, which is where a cleanup that could not finish
    says so ("unfinished"; every column also has standing warn notes in its title and config
    cells, which say nothing about the cleanup). snippets pass through scrub() first: this is
    the one place a secret could reach the console.
    """
    entries = session.get("cells") if isinstance(session, dict) else None
    entries = [e for e in entries or [] if isinstance(e, dict)]
    mapped = len(entries) == len(cells) + 1 and entries[0].get("id") == "setup"
    groups, banner = {}, False
    for position, entry in enumerate(entries):
        group = groups.setdefault(cell_group(position, cells, mapped), new_group())
        where = describe(position, cells, mapped)
        group["cells"] += 1
        cleaning = mapped and position > 0 and cells[position - 1].cleanup
        for output in entry.get("outputs") or []:
            if not isinstance(output, dict):
                continue
            if output.get("type") == "error":
                if output.get("ename") == "ancestor-stopped":
                    group["stopped"] += 1
                    continue
                message = scrub(f"{output.get('ename')}: {output.get('evalue')}")
                group["errors"].append({"cell": where, "error": message[:200]})
                continue
            markup = (output.get("data") or {}).get("text/html")
            if not isinstance(markup, str):
                continue
            heading = markup.rfind("<strong>Break it</strong>")
            for position_in_cell, kind, text in callouts(markup):
                group["callouts"][kind] = group["callouts"].get(kind, 0) + 1
                if kind == "warn" and cleaning:
                    group["unfinished"].append(scrub(text)[:200])
                if kind != "danger":
                    continue
                banner = banner or text.startswith(f"AUTORUN is on ({phase})")
                if expected_red(text, 0 <= heading < position_in_cell):
                    group["expected"] += 1
                else:
                    group["unexpected"].append({"cell": where, "callout": scrub(text)[:200]})
    return {"mapped": mapped, "banner": banner, "groups": groups}


SIMILARITY = re.compile(r"similarity\D{0,4}([01](?:\.\d+)?)", re.IGNORECASE)


def warn_texts(session):
    """The text of every warn callout in a session file."""
    found = set()
    entries = session.get("cells") if isinstance(session, dict) else None
    for entry in [e for e in entries or [] if isinstance(e, dict)]:
        for output in entry.get("outputs") or []:
            markup = (
                (output.get("data") or {}).get("text/html") if isinstance(output, dict) else None
            )
            if isinstance(markup, str):
                found.update(text for _, kind, text in callouts(markup) if kind == "warn")
    return found


def live_numbers(session, cells, scrub, standing=frozenset()):
    """The numbers worth writing in the README, taken from what the demo rendered.

    Per column: its callouts (a LangCache HIT says its similarity), its warnings (a sweep that is
    not monotonic says so in one) and a sample of its tables (tool names, thresholds, ranks).
    standing is the warn texts every export shows, such as "Never on a shared cache": notes,
    not findings, so they are left out. Cheap and generic: the caller picks what it needs.
    """
    entries = [e for e in (session.get("cells") or []) if isinstance(e, dict)]
    mapped = len(entries) == len(cells) + 1 and entries[0].get("id") == "setup"
    found = {"similarities": [], "callouts": {}, "warnings": {}, "tables": {}}
    for position, entry in enumerate(entries):
        group = cell_group(position, cells, mapped)
        for output in entry.get("outputs") or []:
            markup = (
                (output.get("data") or {}).get("text/html") if isinstance(output, dict) else None
            )
            if not isinstance(markup, str):
                continue
            for _, kind, text in callouts(markup):
                if kind == "warn" and text in standing:
                    continue
                # Warnings apart, so a wall of HIT and MISS lines cannot push them out.
                kept = found["warnings" if kind == "warn" else "callouts"].setdefault(group, [])
                if len(kept) < 30 and kind != "danger":
                    kept.append(f"{kind}: {scrub(text)[:200]}")
                found["similarities"] += [float(n) for n in SIMILARITY.findall(text)]
            for tag in _TABLE.finditer(markup):
                rows = attribute_value(attributes(tag.group(1)).get("data-data", ""))
                if not (isinstance(rows, list) and rows and isinstance(rows[0], dict)):
                    continue
                kept = found["tables"].setdefault(group, [])
                if len(kept) < 20:
                    sample = [
                        {str(k): scrub(str(v))[:80] for k, v in row.items()}
                        for row in rows[:8]
                        if isinstance(row, dict)
                    ]
                    kept.append(
                        {
                            "cell": describe(position, cells, mapped),
                            "rows": len(rows),
                            "sample": sample,
                        }
                    )
                for row in rows:
                    for name, value in row.items() if isinstance(row, dict) else ():
                        if "similar" in str(name).lower() and isinstance(value, (int, float)):
                            found["similarities"].append(float(value))
    found["similarities"] = sorted(set(found["similarities"]))
    return found


# ---- live: looking for secrets ------------------------------------------------------
_ENCODERS = (
    ("percent", lambda s: quote(s, safe="")),
    ("percent, slash kept", quote),
    ("percent, plus for space", quote_plus),
    ("json", lambda s: json.dumps(s)[1:-1]),
    ("json, utf-8", lambda s: json.dumps(s, ensure_ascii=False)[1:-1]),
    ("html", html.escape),
    ("html, quotes kept", lambda s: html.escape(s, quote=False)),
)  # fmt: skip


def spellings(value):
    """{text: how it is spelled}: the value, and what one or two layers of escaping make of it.

    Two layers because a value can be HTML-escaped by a callout and then JSON-escaped again by
    the session file.
    """
    forms = {value: "raw"}
    for label, encode in _ENCODERS:
        forms.setdefault(encode(value), label)
    for outer, encode_outer in _ENCODERS:
        for inner, encode_inner in _ENCODERS:
            forms.setdefault(encode_outer(encode_inner(value)), f"{inner}, then {outer}")
    for form, label in list(forms.items()):  # some encoders write %2f where quote writes %2F
        lowered = re.sub(r"%[0-9A-F]{2}", lambda m: m.group().lower(), form)
        forms.setdefault(lowered, f"{label}, lower-case hex")
    forms.pop("", None)
    return forms


# What the notebook's scrub() masks even when it never saw the secret (_SECRET_SHAPES).
SHAPES = (
    (
        "agent or admin key",
        re.compile(r"c[se]_(?:admin|agent)_[\w-]+"),
        lambda m: m[0][:9] + "****",
    ),
    ("bearer token", re.compile(r"(?i)\bBearer\s+(?!\$)[^\s\"']+"), lambda m: "Bearer ****"),
    (
        "redis url with a password",
        re.compile(r"(rediss?://)[^:@/\s]*:[^@\s]+@"),
        lambda m: m[1] + "****@",
    ),
)
_DATA_ATTRIBUTE = re.compile(r"""(data-[\w-]+)=(?:'([^']*)'|"([^"]*)")""")
_DATA_URL = re.compile(r"data:[\w/+.-]+;base64,([A-Za-z0-9+/=]{16,})")


def texts_in(doc, depth=3):
    """Every string in a decoded JSON document, unescaped as well, and what marimo keeps inside
    a rendered component: JSON in its data-* attributes (a table's rows, a callout's html) and
    base64 data: urls (the rows of a big table).
    """
    if isinstance(doc, str):
        yield doc
        plain = html.unescape(doc)
        if plain != doc:
            yield plain
        if depth:
            for _, single, double in _DATA_ATTRIBUTE.findall(doc):
                yield from texts_in(attribute_value(single or double), depth - 1)
            for encoded in _DATA_URL.findall(doc):
                try:
                    yield from texts_in(
                        base64.b64decode(encoded).decode("utf-8", "replace"), depth - 1
                    )
                except ValueError:
                    continue
    elif isinstance(doc, dict):
        for key, value in doc.items():
            yield from texts_in(key, depth)
            yield from texts_in(value, depth)
    elif isinstance(doc, list):
        for item in doc:
            yield from texts_in(item, depth)


def html_payloads(text):
    """The JSON documents an export html embeds in its <script data-marimo> blocks, decoded.

    marimo writes its mount config as Object.freeze({...json...}) and the notebook's source as
    notebookCode: "...". Anything that does not parse is skipped.
    """
    documents, decoder = [], json.JSONDecoder()
    for script in re.finditer(r'<script data-marimo="true">(.*?)</script>', text, re.DOTALL):
        body = script.group(1)
        for opener in re.finditer(r"Object\.freeze\(\s*|notebookCode:\s*", body):
            try:
                documents.append(decoder.raw_decode(body, opener.end())[0])
            except ValueError:
                continue
    return documents


def secret_hits(forms, texts):
    """[(name, how spelled)] for every secret that any of texts contains."""
    hits = set()
    for text in texts:
        for name, spelt in forms.items():
            hits.update((name, how) for form, how in spelt.items() if form in text)
    return sorted(hits)


def shape_hits(texts, source):
    """[(shape, masked match)] for text that looks like a credential.

    A match that is already masked (****), or that stands verbatim in the notebook's source
    (a documented placeholder such as redis://default:<password>@), is not a leak.
    """
    hits = set()
    for text in map(html.unescape, texts):
        for label, pattern, mask in SHAPES:
            for match in pattern.finditer(text):
                if "****" not in match[0] and match[0] not in source:
                    hits.add((label, mask(match)))
    return sorted(hits)


def scan_file(kind, path, forms, source):
    """What one file leaks: {"bytes", "decoded", "secrets": [[name, how]], "shapes": [[..]]}.

    Secrets are looked for in the raw text, unescaped, and in every string of the decoded json.
    Shapes only in decoded strings, because the raw text of a json file still has \\u003c in it.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if kind == "html":
        documents = html_payloads(text)
    elif kind == "session":
        try:
            documents = [json.loads(text)]
        except ValueError:
            documents = []
    else:
        documents = []
    decoded = [t for doc in documents for t in texts_in(doc)]
    plain = [text, html.unescape(text)]
    # An export's payload is the mount config, and the outputs are its "session"; if that did not
    # decode, only the raw text was scanned.
    payload = any(isinstance(d, dict) and "session" in d for d in documents)
    return {
        "bytes": len(text.encode("utf-8")),
        "decoded": payload if kind == "html" else kind != "session" or bool(documents),
        "secrets": secret_hits(forms, plain + decoded),
        "shapes": shape_hits(plain if kind == "log" else decoded, source),
    }


def collect_secrets(nb):
    """({name: value}, [names too short to check]) for every secret in the settings file.

    Read here, in the child, and never printed. The password part of a Redis URL counts too,
    raw and percent-decoded, whether the URL was given or composed from REDIS_HOST and friends.
    """
    found, short = {}, []

    def take(name, value):
        if not value:
            return
        if len(value) >= MIN_SECRET:
            found[name] = value
        else:
            short.append(name)

    for name in sorted(nb.SECRET_ENV):
        take(name, nb.env(name))
    urls = {"REDIS_URL": nb.env("REDIS_URL")}
    composed = nb.redis_url_from_env()
    if composed != urls["REDIS_URL"]:
        take("composed Redis URL", composed)
        urls["composed Redis URL"] = composed
    for label, url in urls.items():
        try:
            password = urlsplit(url).password if url else None
        except ValueError:
            password = None
        if password:
            take(f"{label} password", password)
            if unquote(password) != password:
                take(f"{label} password, decoded", unquote(password))
    return found, short


def map_strings(fn, obj):
    """A copy of a JSON-able object with fn applied to every string, keys included."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {map_strings(fn, k): map_strings(fn, v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [map_strings(fn, item) for item in obj]
    return obj


# ---- live: what the children do -----------------------------------------------------
# Each takes the imported notebook module and a spec, and returns a JSON-able dict that
# child_main() scrubs before printing. They are the only code that talks to a service.
def sweep_rs(nb):
    done = nb.rs_cleanup(nb.connect_redis(nb.redis_url_from_env()))
    return done, nb.rs_cleanup_report(done)


def sweep_lc(nb):
    conn, missing = nb.lc_connection("", "", None, "")
    if conn is None:
        raise ValueError("missing " + ", ".join(missing))
    done = nb.lc_cleanup(conn)
    return done, nb.lc_cleanup_report(done)


def sweep_cr(nb):
    admin, url = nb.resolve_secret("CTX_ADMIN_KEY"), nb.redis_url_secret()
    if admin is None or url is None:
        raise ValueError("CTX_ADMIN_KEY and REDIS_URL (or REDIS_HOST) are not both set")
    done = nb.cr_cleanup(admin, nb.cr_endpoints("")[0], url)
    return done, nb.cr_cleanup_report(done)


def sweep_am(nb):
    conn, missing = nb.am_settings("", "", None)
    if conn is None:
        raise ValueError("missing " + ", ".join(missing))
    done = nb.am_cleanup(conn)
    return done, nb.am_cleanup_report(done)


SWEEPS = {"rs": sweep_rs, "lc": sweep_lc, "cr": sweep_cr, "am": sweep_am}


def list_indexes(nb, client):
    """The name of every index the database has."""
    return sorted(nb.decode_reply(client.execute_command("FT._LIST")))


def child_probe(nb, spec):
    """Which settings P has (names only), whether this child really runs as our RUN, and, when
    there is a database, the indexes it has before anything is written (FT._LIST reads only)."""
    found = {
        "present": nb.env_present(), "run": nb.RUN, "pinned": nb.RUN_PINNED,
        "ledger": str(nb.ledger_dir()), "python": sys.version.split()[0],
    }  # fmt: skip
    if {"REDIS_URL", "REDIS_HOST"} & set(found["present"]):
        try:
            found["indexes"] = list_indexes(nb, nb.connect_redis(nb.redis_url_from_env()))
        except Exception as exc:  # the footprint then has nothing to compare with
            found["indexes_error"] = f"{type(exc).__name__}: {exc}"
    return found


def child_sweep(nb, spec):
    """The notebook's own cleanup for each column named: what it did, or why it could not."""
    results = {}
    for key in spec["columns"]:
        try:
            done, report = SWEEPS[key](nb)
            results[key] = {"done": done, "report": report}
        except Exception as exc:  # one column failing must not stop the others being swept
            results[key] = {"error": f"{type(exc).__name__}: {exc}"}
    return results


def child_sentinel(nb, spec, sleep=time.sleep):
    """LangCache TTL: store an entry with ttlMillis=5000, watch it go, delete it by id.

    The entry is a Cairn prompt with this run's scope attribute (if the cache has one), set
    through the notebook's own helpers but deliberately not ledgered, so it is also the proof
    that the choke point lets a granted id through: approve_lc_entry. The delete at the end
    doubles as the test: 404 means the cache let it expire, 204 means it was still there.
    """
    conn, missing = nb.lc_connection("", "", None, "")
    if conn is None:
        raise ValueError("missing " + ", ".join(missing))
    # A cache that lacks the scope attribute answers 400 to it. The notebook then goes on
    # unscoped, and so must the sentinel.
    scope = nb.lc_probe_cache(conn)["scope"]
    prompt = f"Cairn Outfitters verify.py expiry sentinel {nb.RUN}"
    body = nb.lc_set_body(
        prompt,
        "This answer expires five seconds after it is stored.",
        SENTINEL_TTL_MS,
        scope,
    )
    _, reply = nb.lc_call(conn, "POST", "/entries", body)
    entry = reply.get("entryId") if isinstance(reply, dict) else None
    if not (isinstance(entry, str) and re.fullmatch(r"[A-Za-z0-9_-]+", entry)):
        raise ValueError("the reply held no usable entryId")
    search = {
        "prompt": prompt, "similarityThreshold": 0.9, "searchStrategies": ["semantic"],
        "maxResults": 5,
    }  # fmt: skip
    if scope:
        search["attributes"] = {scope: nb.RUN_TAG}
    result = {
        "entry": entry,
        "ttl_ms": SENTINEL_TTL_MS,
        "seen_alive": False,
        "gone": False,
        "waited": 0,
    }
    try:
        nb.approve_lc_entry(entry)
        while True:
            _, found = nb.lc_call(conn, "POST", "/entries/search", search)
            data = found.get("data") if isinstance(found, dict) else None
            listed = any(isinstance(hit, dict) and hit.get("id") == entry for hit in data or [])
            result["seen_alive"] = result["seen_alive"] or listed
            # Not listed yet is not gone: until it has been seen, give the search its grace.
            if not listed and (result["seen_alive"] or result["waited"] >= SENTINEL_GRACE):
                result["gone"] = True
                break
            if result["waited"] >= SENTINEL_WAIT:
                break
            sleep(SENTINEL_POLL)
            result["waited"] += SENTINEL_POLL
    finally:
        result["final"] = nb.lc_delete(conn, entry)
    return result


def footprint_redis(nb, before):
    """Keys under this run's prefix (SCAN, COUNT 1000), indexes that cover any of it, and indexes
    that are new since `before` (the probe's list) and cover none of it: another app's, or one
    the service built under a prefix of its own."""
    client = nb.connect_redis(nb.redis_url_from_env())
    keys = sorted(nb.scan_keys(client, nb.PREFIX + "*", count=1000))
    found = {"redis_keys": {"count": len(keys), "sample": keys[:5]}}
    try:
        mine, others = [], []
        for name in list_indexes(nb, client):
            try:
                info = nb.reply_map(nb.decode_reply(client.execute_command("FT.INFO", name)))
            except nb.redis.ResponseError as exc:
                if not nb.is_unknown_index(exc):
                    raise  # an index that will not answer is not an index that is gone
                continue  # dropped since it was listed
            prefixes = nb.reply_map(info.get("index_definition")).get("prefixes")
            prefixes = [prefixes] if isinstance(prefixes, str) else prefixes or []
            covers = any(isinstance(p, str) and p.startswith(nb.PREFIX) for p in prefixes)
            (mine if covers else others).append(name)
        found["indexes"] = {"count": len(mine), "sample": mine[:5]}
        if before is None:
            found["indexes_new"] = {"error": "the probe listed no indexes from before the exports"}
        else:
            new = [name for name in others if name not in before]
            found["indexes_new"] = {"count": len(new), "sample": new[:5]}
    except nb.redis.RedisError as exc:
        found["indexes"] = found["indexes_new"] = {"error": f"{type(exc).__name__}: {exc}"}
    return found


def footprint_memory(nb):
    """Memories and sessions the store lists for this run's owner (never for anyone else)."""
    conn, missing = nb.am_settings("", "", None)
    if conn is None:
        raise ValueError("missing " + ", ".join(missing))
    listed = nb.am_list(conn, nb.am_filter(namespace=None))["items"]
    mine = [m for m in listed if isinstance(m, dict) and m.get("ownerId") == nb.AM_OWNER]
    found = {
        "count": len(mine), "sample": [str(m.get("id")) for m in mine[:5]],
        "strangers": len(listed) - len(mine),
    }  # fmt: skip
    try:
        sessions = nb.am_list_sessions(conn, nb.AM_OWNER)
        found["sessions"] = len([s for s in sessions if str(s).startswith(f"{nb.AM_OWNER}-s")])
    except Exception as exc:  # the memories are the footprint; a failing session list is a note
        found["sessions_error"] = f"{type(exc).__name__}: {exc}"
    return found


def footprint_surfaces(nb):
    """Context Retriever surfaces named iris-basics-<RUN>."""
    admin = nb.resolve_secret("CTX_ADMIN_KEY")
    if admin is None:
        raise ValueError("CTX_ADMIN_KEY is not set")
    api = nb.cr_endpoints("")[0]
    mine = [
        s
        for s in nb.cr_list_surfaces(api, admin)
        if isinstance(s, dict) and s.get("name") == nb.RUN_TAG
    ]
    return {"count": len(mine), "sample": [str(s.get("id")) for s in mine[:5]]}


def child_footprint(nb, spec):
    """Everything the services still hold for this RUN. Reads only."""
    columns, found = set(spec["columns"]), {}

    def attempt(key, action):
        try:
            found.update(action())
        except Exception as exc:
            found[key] = {"error": f"{type(exc).__name__}: {exc}"}

    if "rs" in columns:
        attempt("redis_keys", lambda: footprint_redis(nb, spec.get("indexes_before")))
        if "redis_keys" in found and "error" in found["redis_keys"]:
            found["indexes"] = found["indexes_new"] = found["redis_keys"]
    if "am" in columns:
        attempt("memory", lambda: {"memory": footprint_memory(nb)})
    if "cr" in columns:
        attempt("surfaces", lambda: {"surfaces": footprint_surfaces(nb)})
    return found


def child_analyse(nb, spec):
    """Judge each export's session file and scan the export for secrets. Offline: no service.

    spec["phases"] lists {"name", "html", "session", "log", "rc"}. The secrets are read here
    (from the settings file, through the notebook's own env()) and only their names are reported.
    """
    source = Path(nb.__file__).read_text(encoding="utf-8")
    cells = notebook_cells(source)
    secrets, short = collect_secrets(nb)
    forms = {name: spellings(value) for name, value in secrets.items()}
    # A scan that cannot say yes is worthless: plant each secret and make sure it is found.
    blind = [n for n, v in secrets.items() if not secret_hits({n: forms[n]}, ["x" + v + "y"])]
    result = {
        "secrets": {"checked": sorted(secrets), "too_short": short, "blind": blind},
        "phases": {}, "numbers": {},
    }  # fmt: skip
    documents = {}
    for phase in spec["phases"]:
        entry = result["phases"][phase["name"]] = {"files": {}}
        for kind in ("html", "session", "log"):
            path = Path(phase[kind]) if phase.get(kind) else None
            if path is not None and path.exists():
                entry["files"][kind] = scan_file(kind, path, forms, source)
        session = entry["files"].get("session")
        if session is not None and session["decoded"]:
            document = documents[phase["name"]] = json.loads(
                Path(phase["session"]).read_text(encoding="utf-8")
            )
            entry["judge"] = judge_session(document, cells, nb.scrub, phase["name"])
        log = entry["files"].get("log")
        if phase.get("rc") != 0 and log is not None and not log["secrets"]:
            tail = (
                Path(phase["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
            )
            masked = nb.scrub("\n".join(tail))  # before cutting the lines, which could cut a key
            entry["excerpt"] = "\n".join(line[:200] for line in masked.splitlines())
    if "demo" in documents:
        # What the cleanup export shows too is a standing note, not something the demo found.
        standing = warn_texts(documents.get("cleanup"))
        result["numbers"] = live_numbers(documents["demo"], cells, nb.scrub, standing)
    return result


CHILD_ACTIONS = {
    "probe": child_probe, "sweep": child_sweep, "sentinel": child_sentinel,
    "footprint": child_footprint, "analyse": child_analyse,
}  # fmt: skip


def child_main():
    """Entry point of a live child: python -c "import verify; verify.child_main()" <action>.

    Runs in the notebook's dependency set, in a folder holding the notebook, this script and
    child_spec.json. It prints one line: RESULT and a JSON document with every string scrubbed,
    or FAILURE and one scrubbed line. Nothing else it says is ever shown by the parent.
    """
    nb = None
    try:
        spec = json.loads(Path(CHILD_SPEC).read_text(encoding="utf-8"))
        nb = importlib.import_module(NOTEBOOK.removesuffix(".py"))
        result = CHILD_ACTIONS[sys.argv[1]](nb, spec)
        line = RESULT + json.dumps(map_strings(nb.scrub, result))
    except Exception as exc:
        # Never let a traceback (which can quote a setting) reach the console.
        text = nb.scrub(f"{type(exc).__name__}: {exc}") if nb else type(exc).__name__
        line = FAILURE + text[:300]
        print(line)
        sys.exit(3)
    print(line)


# ---- live: running things -----------------------------------------------------------
def kill_tree(proc):
    """Kill a process and everything it started. uv leaves python and marimo running when it
    is killed alone, and a stray export would go on writing to the services."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()


def run_process(cmd, cwd, env, timeout):
    """(returncode, output) of a command; returncode is None if it ran out of time.

    On a timeout, or an interrupt, the whole process tree is killed first. Output is stdout and
    stderr together, and is for a log file, not the console.
    """
    group = {} if sys.platform == "win32" else {"start_new_session": True}
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", **group,
    )  # fmt: skip
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out = ""
        return None, out or ""
    except BaseException:
        kill_tree(proc)
        raise
    return proc.returncode, out or ""


def live_env(path, run, ledger, **extra):
    """The environment for every live child: a pinned RUN and ledger, headless, the settings path.

    IRIS_BASICS_ENV_FILE is the only way a credential reaches a child, and it is a path: this
    process never opens the file.
    """
    return scrubbed_env(
        IRIS_BASICS_ENV_FILE=str(path), IRIS_BASICS_RUN=run, IRIS_BASICS_HEADLESS="1",
        IRIS_BASICS_LEDGER_DIR=str(ledger), **extra,
    )  # fmt: skip


def stage_live(tmp, name, with_verify=False):
    """A new, empty folder holding the notebook (and, for a child, this script)."""
    folder = tmp / name
    folder.mkdir()
    shutil.copy2(ROOT / NOTEBOOK, folder / NOTEBOOK)
    if with_verify:
        shutil.copy2(ROOT / "verify.py", folder / "verify.py")
    return folder


def stray_dotenv(folder):
    """The .env that marimo would load into an export run in `folder`, or None.

    marimo reads the .env in the folder of the nearest pyproject.toml at or above the notebook
    and, without one, the notebook's own folder, and puts it in os.environ, where the notebook
    looks before it looks in the settings file. A .env above the temp folder would therefore
    mix another project's credentials into a run against shared accounts.
    """
    anchor = next((d for d in (folder, *folder.parents) if (d / "pyproject.toml").exists()), folder)
    return next((d / ".env" for d in {folder, anchor} if (d / ".env").exists()), None)


def read_ledger(folder, run):
    """{(column, kind): {ident: last state}} from a run's ledger file; {} if there is none."""
    path = Path(folder) / f"{run}.jsonl"
    latest = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a line cut short by a crash
            if isinstance(entry, dict) and entry.get("column") and entry.get("kind"):
                key = (entry["column"], entry["kind"])
                latest.setdefault(key, {})[str(entry.get("ident"))] = entry.get("state")
    return latest


def ledger_totals(latest):
    """{column: {"recorded": n, "live": n, "sample": [...]}}. Live is created or only intended."""
    totals = {}
    for (column, kind), items in latest.items():
        entry = totals.setdefault(column, {"recorded": 0, "live": 0, "sample": []})
        entry["recorded"] += len(items)
        live = [ident for ident, state in items.items() if state in LIVE_STATES]
        entry["live"] += len(live)
        entry["sample"] += [f"{kind} {ident}" for ident in live[:3]]
    return totals


def live_by_kind(latest):
    """{column: {kind: [ident, ...]}} for what a run's ledger still shows live."""
    found = {}
    for (column, kind), items in latest.items():
        live = [ident for ident, state in items.items() if state in LIVE_STATES]
        if live:
            found.setdefault(column, {})[kind] = live
    return found


# ---- live: turning results into rows ------------------------------------------------
def sweep_verdict(key, result):
    """(status, detail) for one column's sweep, from what the notebook's cleanup returned."""
    if "error" in result:
        return "FAIL", result["error"]
    done = result.get("done") or {}
    if key == "rs":
        detail = (
            f"dropped {done.get('dropped')} of {done.get('indexes')} indexes "
            f"({done.get('gone')} already gone), "
            f"unlinked {done.get('removed')} of {done.get('keys')} keys"
        )
    elif key == "lc":
        detail = (
            f"deleted {done.get('deleted')} and {done.get('expired')} had expired, "
            f"of {done.get('entries')} entries"
        )
    elif key == "cr":
        detail = (
            f"deleted {done.get('deleted')} of {done.get('surfaces')} surfaces, unlinked "
            f"{done.get('removed')} of {done.get('keys')} keys, dropped {done.get('dropped')} "
            f"of {done.get('indexes')} indexes ({done.get('gone')} already gone)"
        )
        if done.get("left") or done.get("stopped"):
            detail += f"; left behind: {done.get('left')}; stopped: {done.get('stopped') or 'no'}"
            return "FAIL", detail
    else:
        if not done.get("ran"):
            return "PASS", "this run wrote nothing to Agent Memory"
        detail = (
            f"deleted {done.get('sessions')} sessions and {done.get('memories')} memories "
            f"({done.get('extracted')} made by extraction)"
        )
        if done.get("left") or done.get("refused") or done.get("sessions_left"):
            detail += (
                f"; still listed {done.get('left')}, refused {done.get('refused')}, sessions "
                f"left {done.get('sessions_left')}"
            )
            return "WARN", detail
    return "PASS", detail


def sentinel_verdict(found):
    """(status, detail, output) for the LangCache sentinel."""
    limit = f"ttlMillis {found['ttl_ms']}"
    if not found["gone"]:
        return "FAIL", f"still listed after {found['waited']} s: {limit} was not honoured", ""
    if found["final"] == "deleted" and found["seen_alive"]:
        # Lookups no longer find it, which is what a cache owes; a purge may just be lagging.
        detail = f"search stopped listing it after {found['waited']} s, but delete by id found it"
        return "WARN", detail, ""
    if found["final"] == "deleted":
        return "FAIL", f"never listed, and delete by id found it after {found['waited']} s", ""
    if not found["seen_alive"]:
        return (
            "WARN",
            "never listed, so nothing shows it expired (does search find a fresh entry?)",
            "",
        )
    return (
        "PASS",
        f"listed, then gone after about {found['waited']} s; delete by id: 404 (expired)",
        "",
    )


def memory_verdict(memory):
    """(status, detail) for what Agent Memory still lists for this run's owner."""
    if memory is None:
        return "FAIL", "the check reported nothing"
    if "error" in memory:
        return "FAIL", f"could not check: {memory['error']}"
    if memory["strangers"]:
        return (
            "FAIL",
            f"the service listed {memory['strangers']} memories of others: filter ignored",
        )
    if memory["count"]:
        return "FAIL", f"{memory['count']} memories left, e.g. {', '.join(memory['sample'][:3])}"
    if memory.get("sessions"):
        return "WARN", f"no memories; {memory['sessions']} sessions left (they expire on their own)"
    note = (
        f" (session list failed: {memory['sessions_error']})" if "sessions_error" in memory else ""
    )
    return "PASS", "no memories, no sessions" + note


def footprint_rows(found, totals, configured, run):
    """One row per place the run could have left something. PASS only when that place is empty."""
    prefix = f"irisnb_{run}_"
    rows = []

    def add(name, status, detail):
        rows.append(Row(f"footprint: {name}", status, detail=detail))

    def place(name, column, key, noun, absent, left="left", status="FAIL"):
        entry = found.get(key)
        if column not in configured:
            add(name, "SKIP", absent)
        elif entry is None:
            add(name, "FAIL", "the check reported nothing")
        elif "error" in entry:
            add(name, "FAIL", f"could not check: {entry['error']}")
        elif entry["count"]:
            sample = ", ".join(entry["sample"][:3])
            add(name, status, f"{entry['count']} {noun} {left}, e.g. {sample}")
        else:
            add(name, "PASS", f"no {noun}")

    place(f"keys {prefix}*", "rs", "redis_keys", "keys", "no Redis settings in P")
    place(f"indexes under {prefix}", "rs", "indexes", "indexes", "no Redis settings in P")
    # Whose they are cannot be told (a shared database has other apps), so this only warns.
    place(
        f"new indexes outside {prefix}", "rs", "indexes_new", "indexes", "no Redis settings in P",
        left="appeared since the probe and are not under it (not removed)", status="WARN",
    )  # fmt: skip
    live = ", ".join(f"{key} {entry['live']}" for key, entry in totals.items() if entry["live"])
    add("ledger", "FAIL" if live else "PASS", f"still live: {live}" if live else "nothing live")
    name = f"Agent Memory ib-{run}"
    if "am" in configured:
        add(name, *memory_verdict(found.get("memory")))
    else:
        add(name, "SKIP", "Agent Memory is not configured")
    absent = "Context Retriever is not configured"
    place(f"surfaces iris-basics-{run}", "cr", "surfaces", "surfaces", absent)
    return rows


def cell_rows(phase, entry, configured, totals):
    """The rows for one export's session: the shared cells, then each configured column.

    A configured column FAILs on an error output, on a red callout that is not part of the demo,
    and, for the demo, on writing nothing (its ledger is empty). In the cleanup export it WARNs
    when its Clean up cell shows a warn callout, which is a cleanup that could not finish; the
    standing notes of its title and config cells are no such thing. A column's row for the demo
    counts as that column exercised (Row.tested).
    """
    judge = entry.get("judge")
    if judge is None:
        return [Row(f"{phase} cells", "FAIL", detail="no session file to judge")]
    rows = []
    for key in [SHARED, UNATTRIBUTED, *configured]:
        group = judge["groups"].get(key)
        if group is None and key != SHARED:
            continue
        group = group or new_group()
        label = key if key in (SHARED, UNATTRIBUTED) else COLUMNS[key][0]
        problems = [f"{e['cell']}: {e['error']}" for e in group["errors"]]
        problems += [f"{u['cell']}: red callout: {u['callout']}" for u in group["unexpected"]]
        if key == UNATTRIBUTED:
            problems.insert(0, "the session's cells do not line up with the notebook's cells")
        if key == SHARED and not judge["banner"]:
            problems.insert(
                0, f"no AUTORUN banner: AUTORUN={phase} was not honoured, so nothing ran"
            )
        if phase == "demo" and key in configured and not totals.get(key, {}).get("recorded"):
            problems.insert(0, "the ledger recorded nothing: the demo wrote nothing to this column")
        name = f"{phase} cells: {label}"
        exercised = phase == "demo" and key in COLUMNS
        if problems:
            more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            rows.append(
                Row(
                    name,
                    "FAIL",
                    detail=problems[0][:150] + more,
                    output="\n".join(problems),
                    tested=exercised,
                )
            )
            continue
        seen = ", ".join(f"{n} {kind}" for kind, n in sorted(group["callouts"].items()))
        detail = (
            f"{group['cells']} cells, {group['stopped']} waiting on a button; callouts: "
            f"{seen or 'none'}"
            + (f" ({group['expected']} red ones expected)" if group["expected"] else "")
        )
        status = "PASS"
        if phase == "cleanup" and group["unfinished"]:
            status, detail = "WARN", detail + f"; the Clean up cell warns: {group['unfinished'][0]}"
        rows.append(Row(name, status, detail=detail, tested=exercised))
    return rows


def scan_rows(analysis):
    """The secret scan as one row: any hit fails it. Only names and spellings are ever listed."""
    secrets = analysis["secrets"]
    problems, files = [], 0
    for phase, entry in analysis["phases"].items():
        for kind, scanned in entry["files"].items():
            files += 1
            problems += [
                f"{name} ({how}) in the {phase} {kind}" for name, how in scanned["secrets"]
            ]
            problems += [
                f"{label} {masked} in the {phase} {kind}" for label, masked in scanned["shapes"]
            ]
            if not scanned["decoded"]:
                problems.append(f"the {phase} {kind} could not be decoded as json")
    if secrets["blind"]:
        problems.append("the scan could not find a planted copy of " + ", ".join(secrets["blind"]))
    if not secrets["checked"]:
        problems.append("the settings file holds no secret to look for: is it the right file?")
    if problems:
        detail = f"{len(problems)} problems: {problems[0]}" + (" ..." if len(problems) > 1 else "")
        return Row("secret scan", "FAIL", detail=detail, output="\n".join(problems))
    names = ", ".join(secrets["checked"])
    detail = f"{files} files clean of {len(secrets['checked'])} secrets in every spelling ({names})"
    if secrets["too_short"]:
        detail += f"; too short to check: {', '.join(secrets['too_short'])}"
        return Row("secret scan", "WARN", detail=detail)
    return Row("secret scan", "PASS", detail=detail)


# ---- live: the run ------------------------------------------------------------------
def say(message):
    print(message, flush=True)


class ChildFailed(Exception):
    """A child ended without a result. The message is already scrubbed."""


@contextmanager
def interrupt_on_termination():
    """Make SIGTERM and Windows' Ctrl-Break raise KeyboardInterrupt, as Ctrl-C does, so a run
    stopped that way still sweeps. (Closing the console window is not something Python can
    catch.) The handlers are put back afterwards."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = {}
    try:
        for name in ("SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                previous[name] = signal.signal(getattr(signal, name), interrupt)
    except ValueError:  # not the main thread: signals cannot be caught here
        pass
    try:
        yield
    finally:
        for name, handler in previous.items():  # None: one Python did not install
            signal.signal(getattr(signal, name), handler or signal.SIG_DFL)


class Live:
    """One live run: its RUN, folders and environment, and the rows it has earned so far."""

    def __init__(self, env_file, keep=False, extraction_wait=EXTRACTION_WAIT, summary=None):
        self.path = Path(env_file).expanduser().resolve()
        self.keep, self.wait = keep, extraction_wait
        self.run_id = uuid.uuid4().hex[:8]
        default = Path(tempfile.gettempdir()) / f"iris_basics_live_{self.run_id}.json"
        self.summary_path = Path(summary).expanduser().resolve() if summary else default
        self.groups = {name: [] for name in GROUPS}
        self.data = {"run": self.run_id, "exports": {}}
        self.configured, self.skipped = [], {}
        self.tmp = self.ledger = self.logs = self.kid = self.env = None
        self.indexes_before = None  # the database's indexes before anything was written
        self.dirty = False  # True while the ledger may name something still live
        self.calls = 0
        self.started = time.perf_counter()

    # ---- plumbing ----
    def rows(self):
        return [row for name in GROUPS for row in self.groups[name]]

    def task(self, group, name, action):
        row = step(name, action)
        self.groups[group].append(row)
        return row

    def totals(self):
        return ledger_totals(read_ledger(self.ledger, self.run_id))

    def child(self, action, spec=None, timeout=None):
        """Run one child action in the notebook's environment; return its parsed result."""
        self.calls += 1
        (self.kid / CHILD_SPEC).write_text(json.dumps(spec or {}), encoding="utf-8")
        timeout = timeout or TIMEOUTS["child"]
        cmd = uv_command("python", "-c", CHILD_CODE, action)
        rc, out = run_process(cmd, self.kid, self.env, timeout)
        (self.logs / f"{self.calls:02d}-{action}.log").write_text(out, encoding="utf-8")
        if rc is None:
            raise subprocess.TimeoutExpired(action, timeout)
        lines = out.splitlines()
        found = next((ln for ln in reversed(lines) if ln.startswith(RESULT)), None)
        if rc == 0 and found:
            return json.loads(found[len(RESULT) :])
        failure = next((ln for ln in reversed(lines) if ln.startswith(FAILURE)), "")
        raise ChildFailed(f"{action} exited {rc}. {failure[len(FAILURE) :]}".strip())

    # ---- steps ----
    def setup(self):
        exists = self.path.is_file()
        self.groups["settings"].append(
            Row("settings file exists", "PASS" if exists else "FAIL", detail=self.path.name)
        )
        if not exists:
            return False
        self.tmp = Path(tempfile.mkdtemp(prefix="iris_basics_live_"))
        self.ledger, self.logs = self.tmp / "ledger", self.tmp / "logs"
        self.ledger.mkdir()
        self.logs.mkdir()
        self.kid = stage_live(self.tmp, "child", with_verify=True)
        self.env = live_env(self.path, self.run_id, self.ledger)
        say(f"run {self.run_id}; ledger {self.ledger}; temp {self.tmp}")
        say(f"settings file {self.path.name}: its values are read only inside child processes")
        say(f"the summary will be written to {self.summary_path} and kept")
        say(f"if this run is killed, sweep by hand: {self.hand_sweep()}")
        stray = stray_dotenv(self.tmp / "export-demo")
        detail = f"marimo would load {stray} into the run" if stray else "nothing else is loaded"
        self.groups["settings"].append(
            Row("no other .env can leak in", "FAIL" if stray else "PASS", detail=detail)
        )
        return stray is None

    def warm(self):
        row = self.task(
            "warm", "python < 3.14, dependencies resolve", lambda: python_check(self.kid, self.env)
        )
        return row.status == "PASS"

    def probe(self):
        def action():
            found = self.child("probe")
            self.indexes_before = found.pop("indexes", None)
            self.data["probe"] = found
            if not found["pinned"] or found["run"] != self.run_id:
                detail = (
                    "the notebook did not take the pinned RUN: does a .env name IRIS_BASICS_RUN?"
                )
                return "FAIL", detail, ""
            if Path(found["ledger"]).resolve() != self.ledger.resolve():
                return "FAIL", "the notebook is not using the run's ledger folder", ""
            present = set(found["present"])
            for key, (label, groups) in COLUMNS.items():
                missing = [" or ".join(g) for g in groups if not any(n in present for n in g)]
                if missing:
                    self.skipped[key] = missing
                else:
                    self.configured.append(key)
            if not self.configured:
                return "FAIL", f"no column has all its settings in {self.path.name}", ""
            return (
                "PASS",
                f"{len(present)} settings; exercising columns " + ", ".join(self.configured),
                "",
            )

        row = self.task("probe", "read setting names, pin RUN", action)
        for key, missing in self.skipped.items():
            detail = "not exercised; missing " + ", ".join(missing)
            self.groups["probe"].append(Row(f"column {COLUMNS[key][0]}", "SKIP", detail=detail))
        return row.status == "PASS"

    def export(self, phase):
        folder = stage_live(self.tmp, f"export-{phase}")
        log = self.logs / f"export-{phase}.log"
        record = self.data["exports"][phase] = {
            "html": str(folder / "out.html"), "session": str(folder / SESSION), "log": str(log),
            "rc": None,
        }  # fmt: skip
        env = {**self.env, "IRIS_BASICS_AUTORUN": phase}
        cmd = uv_command("python", "-m", "marimo", "-y", "export", "html", "--no-sandbox",
                         NOTEBOOK, "-o", "out.html", "-f")  # fmt: skip
        limit = TIMEOUTS[phase]
        say(f"exporting with AUTORUN={phase} (gives up after {limit // 60} min) ...")

        def action():
            rc, out = run_process(cmd, folder, env, limit)
            log.write_text(out, encoding="utf-8")
            record["rc"] = rc
            if rc is None:
                return "FAIL", f"timed out after {limit} s; the whole process tree was killed", ""
            if rc != 0:
                return "FAIL", f"exit {rc}", ""
            if not (folder / "out.html").exists() or not (folder / SESSION).exists():
                return "FAIL", "exit 0 but no html or session file", ""
            return "PASS", f"exit 0, {(folder / 'out.html').stat().st_size // 1024} KB html", ""

        self.task(phase, f"export html, AUTORUN={phase}", action)

    def sweep(self, group, why, columns):
        """The notebook's own cleanup for these columns, in a child; one row per column."""
        started = time.perf_counter()
        try:
            results = self.child("sweep", {"columns": columns})
        except Exception as exc:
            results = {key: {"error": f"{type(exc).__name__}: {exc}"} for key in columns}
        seconds = time.perf_counter() - started
        self.data.setdefault("sweeps", []).append({"why": why, "results": results})
        for key in columns:
            status, detail = sweep_verdict(key, results.get(key, {"error": "no result"}))
            name = f"{why}: {COLUMNS[key][0]}"
            self.groups[group].append(
                Row(name, status, seconds if key == columns[0] else 0.0, detail)
            )

    def audit_cleanup(self):
        """Read the ledger the moment the cleanup export is over: what is still live is what the
        notebook's own cleanup left. The sweep that follows puts it right and passes, so it is
        only here that it can be said. Agent Memory sessions alone are a warning: they expire
        on their own, and the notebook's Clean up cell says so; a memory or anything else is not."""
        if self.data["exports"].get("cleanup", {}).get("rc") != 0:
            return  # a cleanup export that did not finish has its own row
        try:
            left = live_by_kind(read_ledger(self.ledger, self.run_id))
        except OSError:
            return  # sweep_live cannot read it either, and sweeps every column
        for key in self.configured:
            kinds = left.get(key)
            if not kinds:
                continue
            count = sum(len(idents) for idents in kinds.values())
            sample = ", ".join(f"{kind} {idents[0]}" for kind, idents in kinds.items())
            status = "WARN" if key == "am" and set(kinds) == {"session"} else "FAIL"
            self.groups["cleanup"].append(
                Row(
                    f"cleanup export left live: {COLUMNS[key][0]}",
                    status,
                    detail=f"{count} still in the ledger after it, e.g. {sample}",
                )
            )

    def sweep_live(self):
        """In a finally: sweep every configured column whose ledger still shows something live.

        This is the safety net, and its rows say so: a PASS here is not the notebook cleaning up
        after itself (audit_cleanup says whether it did).
        """
        if not self.configured:
            return
        try:
            totals = self.totals()
            columns = [key for key in self.configured if totals.get(key, {}).get("live")]
        except OSError:  # cannot read the ledger, so cannot say what is live: sweep the lot
            columns = list(self.configured)
        if columns:
            self.sweep("sweep", "safety-net sweep", columns)
        else:
            self.groups["sweep"].append(Row("safety-net sweep", "PASS", detail="nothing live"))

    def analyse(self):
        phases = [
            {
                "name": phase,
                **{k: v for k, v in record.items() if k in ("html", "session", "log", "rc")},
            }
            for phase, record in self.data["exports"].items()
        ]
        started = time.perf_counter()
        try:
            found = self.child("analyse", {"phases": phases})
        except Exception as exc:
            row = Row("judge exports, scan for secrets", "FAIL", time.perf_counter() - started,
                      f"{type(exc).__name__}: {exc}")  # fmt: skip
            self.groups["scan"].append(row)
            return
        self.data["analysis"] = found
        totals = self.totals()
        for phase, entry in found["phases"].items():
            self.groups[f"{phase} cells"] += cell_rows(phase, entry, self.configured, totals)
            if entry.get("excerpt"):
                for row in self.groups[phase]:
                    row.output = entry["excerpt"]
        row = scan_rows(found)
        row.seconds = time.perf_counter() - started
        self.groups["scan"].append(row)

    def sentinel(self):
        name = f"LangCache TTL sentinel ({SENTINEL_TTL_MS // 1000} s)"
        if "lc" not in self.configured:
            self.groups["sentinel"].append(Row(name, "SKIP", detail="LangCache is not configured"))
            return

        def action():
            found = self.data["sentinel"] = self.child("sentinel", timeout=TIMEOUTS["sentinel"])
            return sentinel_verdict(found)

        self.task("sentinel", name, action)

    def wrote_to_memory(self):
        try:
            return bool(self.totals().get("am", {}).get("recorded"))
        except OSError:  # cannot read the ledger: assume it did, and look
            return True

    def extraction(self):
        """Wait out Agent Memory extraction. sweep_late is the sweep that follows."""
        name = "wait for Agent Memory extraction"
        if "am" not in self.configured:
            self.groups["late"].append(Row(name, "SKIP", detail="Agent Memory is not configured"))
        elif not self.wrote_to_memory():
            self.groups["late"].append(Row(name, "SKIP", detail="this run wrote nothing there"))
        elif self.wait <= 0:
            detail = "skipped (--extraction-wait 0): memories extracted later are not swept"
            self.groups["late"].append(Row(name, "WARN", detail=detail))
        else:
            self.task("late", name, self.pause)

    def sweep_late(self):
        """In a finally: sweep Agent Memory's owner again, for what extraction made meanwhile.
        It runs even when the wait, or a step before it, is interrupted."""
        if "am" in self.configured and self.wrote_to_memory():
            self.sweep("late", "sweep again", ["am"])

    def pause(self):
        say(f"waiting {self.wait} s for Agent Memory extraction (about one cadence) ...")
        left = self.wait
        while left > 0:
            chunk = min(left, 60)
            time.sleep(chunk)
            left -= chunk
            if left:
                say(f"  {left} s to go")
        return "PASS", f"waited {self.wait} s", ""

    def footprint(self):
        started = time.perf_counter()
        try:
            spec = {"columns": self.configured, "indexes_before": self.indexes_before}
            found = self.child("footprint", spec)
        except Exception as exc:
            error = {"error": f"{type(exc).__name__}: {exc}"}
            places = ("redis_keys", "indexes", "indexes_new", "memory", "surfaces")
            found = {key: error for key in places}
        self.data["footprint"] = found
        rows = footprint_rows(found, self.totals(), self.configured, self.run_id)
        rows[0].seconds = time.perf_counter() - started
        self.groups["footprint"] += rows
        self.dirty = any(
            row.status == "FAIL"
            for name in ("sweep", "late", "footprint")
            for row in self.groups[name]
        )

    def steps(self):
        if not (self.setup() and self.warm() and self.probe()):
            return
        self.dirty = True  # from here the services may hold something of ours
        try:
            for phase in PHASES:
                self.export(phase)
            self.audit_cleanup()
        finally:
            self.sweep_live()
        try:
            self.analyse()
            self.sentinel()
            self.extraction()
        finally:
            self.sweep_late()
        self.footprint()

    def column_summary(self, rows):
        """Per column: its settings, how each export's cells fared, and what its ledger holds."""
        by_name = {row.name: row for row in rows}
        try:
            totals = self.totals() if self.ledger else {}
        except OSError:
            totals = {}
        columns = {}
        for key, (label, _) in COLUMNS.items():
            found = {"label": label, "configured": key in self.configured}
            found["missing"] = self.skipped.get(key, [])
            for phase in PHASES:
                if row := by_name.get(f"{phase} cells: {label}"):
                    found[phase] = {"result": row.status, "detail": row.detail}
            found["ledger"] = totals.get(key)
            numbers = self.data.get("analysis", {}).get("numbers", {})
            found["warnings"] = numbers.get("warnings", {}).get(key, [])
            columns[key] = found
        return columns

    def hand_sweep(self):
        """What to do if the run cannot clean up after itself."""
        return (
            f"export {NOTEBOOK} with IRIS_BASICS_RUN={self.run_id}, "
            f"IRIS_BASICS_LEDGER_DIR={self.ledger}, IRIS_BASICS_ENV_FILE=<your settings "
            "file>, IRIS_BASICS_HEADLESS=1 and IRIS_BASICS_AUTORUN=cleanup, and once more "
            "about 11 minutes after the run ended: Agent Memory extraction goes on that long "
            "after its last event"
        )

    def wrap_up(self):
        # The summary's own row goes in first, so that the verdict the file holds is the one
        # printed; if the write fails the file does not exist and the row is turned into a FAIL.
        written = Row("summary written", "PASS", detail=str(self.summary_path))
        self.groups["summary"].append(written)
        rows = self.rows()
        line, success = verdict("live", rows)
        table = [
            {"step": r.name, "result": r.status, "seconds": round(r.seconds, 1), "detail": r.detail}
            for r in rows
        ]
        summary = {
            "run": self.run_id, "verdict": line, "success": success,
            "env_file": self.path.name, "seconds": round(time.perf_counter() - self.started, 1),
            "configured": self.configured, "skipped": self.skipped,
            "columns": self.column_summary(rows),
            "rows": table,
            "data": self.data,
        }  # fmt: skip
        try:
            self.summary_path.parent.mkdir(parents=True, exist_ok=True)
            self.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        except OSError as exc:
            written.status, written.detail = "FAIL", f"{type(exc).__name__}: {exc}"
        if self.tmp is None:
            return
        if self.keep:
            say(f"kept {self.tmp}")
            say("its child logs are not secret-scanned: they hold what the children printed")
            if any(row.status == "FAIL" for row in self.groups["scan"]):
                say("the exports there failed the secret scan: the folder may hold a secret")
        elif self.dirty:
            for child in self.tmp.iterdir():
                if child != self.ledger:
                    shutil.rmtree(child, ignore_errors=True)
            say(f"kept the ledger {self.ledger}: it names what may still be live.")
            say(f"To sweep by hand, {self.hand_sweep()}.")
        else:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def run(self):
        with interrupt_on_termination():
            try:
                self.steps()
            except KeyboardInterrupt:
                self.groups["summary"].append(Row("run", "FAIL", detail="interrupted"))
            except Exception as exc:  # a verifier must report, not crash
                detail = f"{type(exc).__name__}: {exc}"
                self.groups["summary"].append(Row("run", "FAIL", detail=detail))
            finally:
                self.wrap_up()
        return self.rows()


def live(env_file, keep, extraction_wait=EXTRACTION_WAIT, summary=None):
    """The whole live run (plan section 3.7). Returns the rows for the table."""
    return Live(env_file, keep, extraction_wait, summary).run()


# ---- main ---------------------------------------------------------------------------
def verdict(mode, rows):
    """The table's last line, and whether the run succeeded."""
    failed = [r for r in rows if r.status == "FAIL"]
    warned = sum(r.status == "WARN" for r in rows)
    steps = f"{len(failed)} failed of {len(rows)} steps" + (f", {warned} warned" if warned else "")
    if mode == "preflight":
        tested = sum(r.tested for r in rows)
        # A go / no-go has to be able to say no. With no column actually tried (nothing
        # configured, or no connection test written yet) there is nothing to go on.
        label = "NO-GO" if failed else "GO" if tested else "NOT TESTED"
        line = f"preflight: {label} ({tested} of {len(COLUMNS)} columns tested, {steps})"
        return line, label == "GO"
    label = "FAIL" if failed else "PASS"
    if mode == "live":  # a run that exercised one column of four is not a run of the notebook
        exercised = f"{sum(r.tested for r in rows)} of {len(COLUMNS)} columns exercised"
        return f"live: {label} ({exercised}, {steps})", not failed
    return f"{mode}: {label} ({steps})", not failed


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify the Iris basics notebook.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true", help="warm caches and test connections")
    mode.add_argument("--live", action="store_true", help="full run against the services in P")
    parser.add_argument("--env-file", metavar="P", help="settings file for --preflight / --live")
    parser.add_argument(
        "--skip-model", action="store_true", help="--preflight: skip the model download"
    )
    parser.add_argument("--keep", action="store_true", help="keep the temp folder for inspection")
    parser.add_argument(
        "--extraction-wait", metavar="S", type=int,
        help=f"--live: seconds to wait for extraction (default {EXTRACTION_WAIT}; 0 skips it)",
    )  # fmt: skip
    parser.add_argument(
        "--summary", metavar="F", type=Path,
        help="--live: where to write the summary json (default: the temp folder)",
    )  # fmt: skip
    parser.add_argument(
        "--notebook", metavar="F", type=Path,
        help="offline: verify F instead of iris_basics.py (a partial assembly)",
    )  # fmt: skip
    parser.add_argument(
        "--extra-test", metavar="T", type=Path, action="append", default=[],
        help="offline: also run test file T (copied into tests/; repeatable)",
    )  # fmt: skip
    args = parser.parse_args(argv)
    if (args.notebook or args.extra_test) and (args.preflight or args.live):
        parser.error("--notebook and --extra-test only apply to the offline checks")
    for extra in args.extra_test:
        if not extra.is_file():
            parser.error(f"--extra-test {extra}: no such file")
    if args.notebook and not args.notebook.is_file():
        parser.error(f"--notebook {args.notebook}: no such file")
    if (args.preflight or args.live) and not args.env_file:
        parser.error("--preflight and --live need --env-file P")
    if args.env_file and not (args.preflight or args.live):
        parser.error("--env-file only applies to --preflight or --live")
    if (args.extraction_wait is not None or args.summary) and not args.live:
        parser.error("--extraction-wait and --summary only apply to --live")
    if args.extraction_wait is not None and args.extraction_wait < 0:
        parser.error("--extraction-wait is a number of seconds, 0 or more")
    if not UV:
        parser.error("uv is not on PATH: https://docs.astral.sh/uv/getting-started/installation/")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    mode_name = "live" if args.live else "preflight" if args.preflight else "offline"
    if args.live:
        wait = EXTRACTION_WAIT if args.extraction_wait is None else args.extraction_wait
        rows = live(args.env_file, args.keep, wait, args.summary)
    elif args.preflight:
        rows = preflight(args.env_file, args.keep, args.skip_model)
    else:
        rows = offline(
            args.keep, args.notebook and args.notebook.resolve(),
            [t.resolve() for t in args.extra_test],
        )  # fmt: skip

    print_table(rows)
    line, success = verdict(mode_name, rows)
    print(line)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
