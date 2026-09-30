"""Tests for verify.py: a verifier that cannot say no, or that reads the wrong environment,
is worse than none. Skipped when verify.py is not next to the module under test (a column
fragment in .agents/build has no verify.py).

--live is tested without a service: the processes it starts are replaced by fakes, and the code
its children run is called in this process against the notebook and the same stand-ins the
column tests use.
"""

import base64
import builtins
import html
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import namedtuple
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote, quote_plus

import httpx
import pytest
import redis
from fake_am import BASE as AM_BASE
from fake_am import KEY as AM_KEY
from fake_am import STORE as AM_STORE
from fake_am import FakeMemory
from fake_cr import ADMIN_KEY, FakeWorld
from fake_cr import API as CR_API
from fake_lc import FakeLangCache


@pytest.fixture(scope="module")
def verify(nb_file):
    path = nb_file.parent / "verify.py"
    if not path.exists():
        pytest.skip("no verify.py next to this module")
    spec = importlib.util.spec_from_file_location("iris_basics_verify", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestScrubbedEnv:
    def test_settings_and_test_switches_are_removed_but_the_rest_is_kept(self, verify, monkeypatch):
        for name in ("REDIS_URL", "LANGCACHE_API_KEY", "IRIS_BASICS_RUN", "IRIS_NB_MODULE"):
            monkeypatch.setenv(name, "leftover-from-the-shell")
        monkeypatch.setenv("IRIS_NB_PATH", "elsewhere")  # would make pytest import another copy
        env = verify.scrubbed_env(EXTRA="1")
        for name in ("REDIS_URL", "LANGCACHE_API_KEY", "IRIS_BASICS_RUN", "IRIS_NB_MODULE"):
            assert name not in env, name
        assert "IRIS_NB_PATH" not in env
        assert env["IRIS_BASICS_NO_DOTENV"] == "1" and env["EXTRA"] == "1"
        assert any(name.upper() == "PATH" for name in env)


class TestVerdict:
    def test_preflight_cannot_go_when_no_column_was_tested(self, verify):
        rows = [
            verify.Row("settings file exists", "PASS"),
            verify.Row("read settings, run connection tests", "PASS"),
            verify.Row("column 0 Redis Search", "SKIP"),
            verify.Row("column 1 LangCache", "WARN"),
        ]
        line, success = verify.verdict("preflight", rows)
        assert not success
        assert "NOT TESTED" in line and "0 of 4 columns tested" in line

    def test_preflight_goes_once_a_column_really_answered(self, verify):
        rows = [
            verify.Row("settings file exists", "PASS"),
            verify.Row("column 0 Redis Search", "PASS", tested=True),
            verify.Row("column 1 LangCache", "SKIP"),
        ]
        line, success = verify.verdict("preflight", rows)
        assert success and "GO" in line and "1 of 4 columns tested" in line

    def test_a_failure_is_no_go_even_with_a_tested_column(self, verify):
        rows = [
            verify.Row("column 0 Redis Search", "PASS", tested=True),
            verify.Row("column 1 LangCache", "FAIL", tested=True),
        ]
        line, success = verify.verdict("preflight", rows)
        assert not success and "NO-GO" in line and "1 failed of 2 steps" in line

    def test_live_says_how_many_columns_it_exercised(self, verify):
        rows = [
            verify.Row("column 0 Redis Search", "SKIP"),
            verify.Row("demo cells: 1 LangCache", "PASS", tested=True),
            verify.Row("cleanup cells: 1 LangCache", "PASS"),
        ]
        line, success = verify.verdict("live", rows)
        assert success and line == "live: PASS (1 of 4 columns exercised, 0 failed of 3 steps)"

    def test_a_live_failure_still_says_how_many_columns_it_exercised(self, verify):
        rows = [verify.Row("demo cells: 1 LangCache", "FAIL", tested=True)]
        line, success = verify.verdict("live", rows)
        assert not success and line == "live: FAIL (1 of 4 columns exercised, 1 failed of 1 steps)"

    def test_offline_passes_or_fails_on_failures_alone(self, verify):
        assert verify.verdict("offline", [verify.Row("pytest", "PASS")]) == (
            "offline: PASS (0 failed of 1 steps)",
            True,
        )
        line, success = verify.verdict("offline", [verify.Row("pytest", "FAIL")])
        assert not success and line.startswith("offline: FAIL")


class TestColumnRows:
    def test_only_a_column_that_ran_its_own_test_counts_as_tested(self, verify):
        def entry(*, missing=(), has_test=True, ok=None, detail=""):
            return {"missing": list(missing), "has_test": has_test, "ok": ok, "detail": detail}

        report = {
            "columns": {
                "rs": entry(ok=True, detail="PONG"),
                "lc": entry(missing=["LANGCACHE_HOST"], has_test=False),
                "cr": entry(has_test=False),
                "am": entry(ok=False, detail="401 unauthorised"),
            }
        }
        rows = verify.column_rows(report)
        assert [(r.status, r.tested) for r in rows] == [
            ("PASS", True),
            ("SKIP", False),
            ("WARN", False),
            ("FAIL", True),
        ]
        assert rows[1].detail == "offline only; missing LANGCACHE_HOST"
        assert rows[3].detail == "401 unauthorised"

    def test_a_report_with_no_columns_gives_no_rows(self, verify):
        assert verify.column_rows({}) == []


class TestPytestRow:
    def test_a_clean_run_is_a_pass_with_its_summary(self, verify):
        status, detail, _ = verify.pytest_outcome(0, "....\n== 601 passed in 41.0s ==\n")
        assert (status, detail) == ("PASS", "601 passed in 41.0s")

    def test_skipped_tests_are_a_warning_because_those_checks_never_ran(self, verify):
        status, detail, _ = verify.pytest_outcome(0, "....\n== 595 passed, 6 skipped in 40s ==\n")
        assert status == "WARN" and "6 skipped" in detail and "--preflight" in detail

    def test_a_failure_stays_a_failure_and_keeps_the_output_to_show(self, verify):
        status, detail, output = verify.pytest_outcome(1, "FAILED x\n== 1 failed, 5 skipped ==\n")
        assert status == "FAIL" and detail == "exit 1" and "FAILED x" in output

    def test_the_verdict_counts_warnings_but_only_failures_fail_it(self, verify):
        rows = [verify.Row("pytest", "WARN"), verify.Row("export", "PASS")]
        line, success = verify.verdict("offline", rows)
        assert success and line == "offline: PASS (0 failed of 2 steps, 1 warned)"


# ---- --live ---------------------------------------------------------------------------------
# Nothing below reaches a service. The processes a live run starts are replaced by fakes, and
# the code the children run is called in this process against the notebook and the same
# stand-ins the column tests use.
SECRET = 'S3cr"et<v>&/+é'  # every encoder writes this differently
REDIS_PASSWORD = "p@ss/w0rd:9"
CR_URL = "redis://default:fake-pw-9f3a@db.example:6379"


@pytest.fixture(scope="module")
def cells(verify, nb_file):
    return verify.notebook_cells(nb_file.read_text(encoding="utf-8"))


def at(cells, column, nth=0, kind="cell"):
    """The session position of the nth cell (or function) of a column: 0 is the setup block."""
    return [i for i, c in enumerate(cells) if c.column == column and c.kind == kind][nth] + 1


def cleanup_at(cells, column):
    """The session position of a column's Clean up cell."""
    return next(i for i, c in enumerate(cells, start=1) if c.column == column and c.cleanup)


def make_session(cells, outputs=None):
    """A session file as marimo writes it: the setup block, then one entry per cell."""
    entries = [{"id": "setup", "code_hash": "h", "outputs": [], "console": []}]
    entries += [
        {"id": f"c{n:03d}", "code_hash": "h", "outputs": [], "console": []}
        for n in range(len(cells))
    ]
    for position, items in (outputs or {}).items():
        entries[position]["outputs"] += items
    return {"version": "1", "metadata": {"marimo_version": "0.25.0"}, "cells": entries}


def stopped():
    text = "This cell wasn't run because an ancestor was stopped with `mo.stop`: "
    return {"type": "error", "ename": "ancestor-stopped", "evalue": text, "traceback": []}


def crashed(text="ValueError: bad"):
    return {"type": "error", "ename": "exception", "evalue": text, "traceback": ["Traceback"]}


def shows(markup):
    return {"type": "data", "data": {"text/html": markup}}


def callout_markup(nb, kind, text):
    return nb.status_callout(kind, text).text


def banner_markup(nb, phase="demo"):
    banner = f"**AUTORUN is on (`{phase}`).** This notebook is pressing its own buttons."
    return nb.mo.callout(nb.mo.md(banner), kind="danger").text


def judged(verify, nb, cells, outputs, phase="demo"):
    """judge_session over a session with these outputs and the AUTORUN banner in the intro."""
    outputs = {1: [shows(banner_markup(nb, phase))], **outputs}
    return verify.judge_session(make_session(cells, outputs), cells, nb.scrub, phase)


class TestNotebookCells:
    def test_the_cells_are_read_in_file_order_with_their_columns(self, verify, nb_file):
        source = nb_file.read_text(encoding="utf-8")
        cells = verify.notebook_cells(source)
        # every decorated cell and function, however the notebook splits them between the two
        decorated = re.findall(r"^@app\.(?:cell|function)(?:\(|$)", source, re.M)
        assert len(cells) == len(decorated) > 50
        assert cells[0].column == 0 and cells[0].kind == "cell"  # the intro cell
        assert {c.column for c in cells} == {0, 1, 2, 3}
        assert [c.column for c in cells] == sorted(c.column for c in cells)
        assert all(c.kind == "function" and c.name != "_" for c in cells if c.kind == "function")

    def test_they_agree_with_the_structure_tests_reader(self, verify, nb_file):
        import notebook_ast as nbast

        source = nb_file.read_text(encoding="utf-8")
        ours = [(c.kind, c.column, c.name) for c in verify.notebook_cells(source)]
        theirs = [(i.kind, i.column, i.node.name) for i in nbast.items(source)]
        assert ours == theirs

    def test_a_column_key_is_the_notebooks_column_number(self, verify):
        assert verify.KEYS == ["rs", "lc", "cr", "am"]

    def test_each_column_has_one_clean_up_cell(self, verify, cells):
        assert [(c.kind, c.column) for c in cells if c.cleanup] == [("cell", k) for k in range(4)]

    def test_the_cleanup_stage_is_read_from_the_call_not_from_the_word(self, verify):
        source = (
            "@app.cell(column=1)\n"
            "def _(btn):\n"
            '    mo.stop(not pressed(btn, stage="cleanup"), None)\n'
            "@app.cell(column=1)\n"
            "def _(btn):\n"
            '    mo.stop(not pressed(btn, stage="demo"), None)  # never stage="cleanup"\n'
            "@app.cell(column=1)\n"
            "def _(btn):\n"
            '    mo.stop(not other(btn, stage="cleanup"), None)\n'
        )
        assert [c.cleanup for c in verify.notebook_cells(source)] == [True, False, False]


class TestCallouts:
    def test_an_error_callout_is_read_back_as_danger_with_its_message(self, verify, nb):
        found = verify.callouts(callout_markup(nb, "error", "**Boom** it's `bad`"))
        assert [(kind, text) for _, kind, text in found] == [("danger", "Boom it's bad")]

    def test_every_kind_is_told_apart(self, verify, nb):
        markup = "".join(callout_markup(nb, kind, kind) for kind in ("ok", "info", "warn", "error"))
        assert [kind for _, kind, _ in verify.callouts(markup)] == [
            "success", "info", "warn", "danger",
        ]  # fmt: skip

    def test_a_red_button_is_not_a_callout(self, verify, nb):
        button = nb.mo.ui.run_button(label="Clean up", kind="danger")
        assert verify.callouts(button.text) == []

    def test_callouts_inside_a_stack_and_an_accordion_are_found(self, verify, nb):
        inner = nb.status_callout("error", "deep")
        markup = nb.mo.vstack([nb.mo.accordion({"x": inner}), nb.mo.md("hi")]).text
        assert [text for _, _, text in verify.callouts(markup)] == ["deep"]

    def test_the_break_it_rejection_is_expected_only_after_its_heading(self, verify):
        assert verify.expected_red("Rejected, code -32602: no", after_break=True)
        assert not verify.expected_red("Rejected, code -32602: no", after_break=False)
        assert verify.expected_red("AUTORUN is on (demo).", after_break=False)
        assert verify.expected_red("Shared cache: taught answers", after_break=False)
        assert not verify.expected_red("ServiceError: cr HTTP 500", after_break=True)


class TestJudgeSession:
    def test_a_clean_session_has_nothing_wrong_in_any_column(self, verify, nb, cells):
        outputs = {p: [stopped()] for p in (at(cells, 0, 3), at(cells, 1, 3), at(cells, 3, 5))}
        found = judged(verify, nb, cells, outputs)
        assert found["mapped"] and found["banner"]
        assert all(not g["errors"] and not g["unexpected"] for g in found["groups"].values())
        assert found["groups"]["rs"]["stopped"] == 1 and found["groups"]["am"]["stopped"] == 1

    def test_a_cell_waiting_on_a_button_is_not_an_error(self, verify, nb, cells):
        found = judged(verify, nb, cells, {at(cells, 2, 4): [stopped(), stopped()]})
        assert found["groups"]["cr"]["errors"] == [] and found["groups"]["cr"]["stopped"] == 2

    def test_an_error_output_is_charged_to_its_column_with_the_cell(self, verify, nb, cells):
        position = at(cells, 1, 5)
        found = judged(verify, nb, cells, {position: [crashed("KeyError: 'similarity'")]})
        (error,) = found["groups"]["lc"]["errors"]
        assert error["error"] == "exception: KeyError: 'similarity'"
        assert f"line {cells[position - 1].line}" in error["cell"]
        assert all(not found["groups"][k]["errors"] for k in ("rs", "cr", "am"))

    def test_an_error_callout_is_a_failure_too(self, verify, nb, cells):
        bad = shows(callout_markup(nb, "error", "**lc** HTTP 401: the API key was refused"))
        found = judged(verify, nb, cells, {at(cells, 1, 2): [bad]})
        (unexpected,) = found["groups"]["lc"]["unexpected"]
        assert "401" in unexpected["callout"] and found["groups"]["lc"]["callouts"] == {"danger": 1}

    def test_warn_and_info_callouts_are_counted_but_do_not_fail(self, verify, nb, cells):
        many = shows(callout_markup(nb, "warn", "MISS") + callout_markup(nb, "ok", "HIT"))
        found = judged(verify, nb, cells, {at(cells, 1, 2): [many]})
        assert found["groups"]["lc"]["unexpected"] == []
        assert found["groups"]["lc"]["callouts"] == {"warn": 1, "success": 1}

    def test_only_the_clean_up_cells_warns_are_an_unfinished_cleanup(self, verify, nb, cells):
        note = shows(callout_markup(nb, "warn", "**Never on a shared cache.** Flush is not used."))
        left = shows(callout_markup(nb, "warn", "**2** are still listed: press Clean up again"))
        outputs = {at(cells, 1, 0): [note], at(cells, 1, 3): [note], cleanup_at(cells, 1): [left]}
        found = judged(verify, nb, cells, outputs, phase="cleanup")
        assert found["groups"]["lc"]["callouts"] == {"warn": 3}
        assert found["groups"]["lc"]["unfinished"] == ["2 are still listed: press Clean up again"]
        assert all(not found["groups"][k]["unfinished"] for k in ("rs", "cr", "am"))

    def test_a_session_that_does_not_line_up_cannot_tell_which_cell_cleans_up(
        self, verify, nb, cells
    ):
        left = shows(callout_markup(nb, "warn", "still listed"))
        session = make_session(cells[:-3], {cleanup_at(cells, 1): [left]})
        found = verify.judge_session(session, cells, nb.scrub, "cleanup")
        assert found["groups"][verify.UNATTRIBUTED]["unfinished"] == []

    def test_the_banner_and_the_shared_cache_note_are_the_demo_not_a_failure(
        self, verify, nb, cells
    ):
        note = shows(callout_markup(nb, "error", "**Shared cache:** taught answers are visible"))
        found = judged(verify, nb, cells, {at(cells, 1, 2): [note]})
        assert found["groups"]["lc"]["unexpected"] == [] and found["groups"]["lc"]["expected"] == 1
        assert found["groups"][verify.SHARED]["expected"] == 1  # the banner

    def test_break_it_shows_a_red_rejection_after_its_heading_and_that_is_fine(
        self, verify, nb, cells
    ):
        rejected = callout_markup(nb, "error", "**Rejected**, code `-32602`:\n\nbad params")
        stack = nb.mo.vstack(
            [nb.mo.md("`tools/call x`"), nb.mo.md("**Break it**"), nb.mo.Html(rejected)]
        )
        found = judged(verify, nb, cells, {at(cells, 2, 20): [shows(stack.text)]})
        assert found["groups"]["cr"]["unexpected"] == [] and found["groups"]["cr"]["expected"] == 1

    def test_a_rejection_of_the_plain_run_is_not_excused_by_a_break_it_below_it(
        self, verify, nb, cells
    ):
        rejected = callout_markup(nb, "error", "**Rejected**, code `-32602`:\n\nbad params")
        stack = nb.mo.vstack([nb.mo.Html(rejected), nb.mo.md("**Break it**"), nb.mo.Html(rejected)])
        found = judged(verify, nb, cells, {at(cells, 2, 20): [shows(stack.text)]})
        assert len(found["groups"]["cr"]["unexpected"]) == 1
        assert found["groups"]["cr"]["expected"] == 1

    def test_no_banner_means_autorun_was_not_honoured(self, verify, nb, cells):
        session = make_session(cells)
        assert verify.judge_session(session, cells, nb.scrub, "demo")["banner"] is False
        wrong = make_session(cells, {1: [shows(banner_markup(nb, "cleanup"))]})
        assert verify.judge_session(wrong, cells, nb.scrub, "demo")["banner"] is False

    def test_a_session_that_does_not_line_up_with_the_notebook_is_unattributed(
        self, verify, nb, cells
    ):
        session = make_session(cells[:-3], {5: [crashed()]})
        found = verify.judge_session(session, cells, nb.scrub, "demo")
        assert not found["mapped"] and list(found["groups"]) == [verify.UNATTRIBUTED]
        assert len(found["groups"][verify.UNATTRIBUTED]["errors"]) == 1

    def test_snippets_are_scrubbed_before_they_leave(self, verify, nb, cells, monkeypatch):
        key = "lc-key-abcdef-123456"
        monkeypatch.setenv("LANGCACHE_API_KEY", key)
        nb.env("LANGCACHE_API_KEY")  # the notebook learns it, as it does from the settings file
        red = shows(nb.mo.callout(nb.mo.md(f"token {key}"), kind="danger").text)
        crash = crashed(f"HTTPError: Bearer {key} was refused")
        found = judged(verify, nb, cells, {at(cells, 1, 1): [crash, red]})
        text = json.dumps(found)
        assert key not in text and "****" in text

    def test_setup_block_errors_are_shared_not_a_columns(self, verify, nb, cells):
        found = judged(verify, nb, cells, {0: [crashed("NameError: x")]})
        assert len(found["groups"][verify.SHARED]["errors"]) == 1
        assert found["groups"][verify.SHARED]["errors"][0]["cell"] == "the setup block"

    def test_the_warnings_of_an_unconfigured_notebook_are_nothing_to_charge(
        self, verify, nb, cells
    ):
        warn = shows(callout_markup(nb, "warn", "not configured"))
        found = judged(verify, nb, cells, {at(cells, k, 2): [warn] for k in range(4)})
        assert all(not g["errors"] and not g["unexpected"] for g in found["groups"].values())


class TestLiveNumbers:
    def test_a_standing_note_is_left_out_of_the_warnings_and_a_finding_is_kept(
        self, verify, nb, cells
    ):
        note = "**Never on a shared cache.** Flush is not used."
        finding = "**The service does not behave like a hard cut.**"
        outputs = {
            at(cells, 1, 0): [shows(callout_markup(nb, "warn", note))],
            at(cells, 1, 4): [shows(callout_markup(nb, "warn", finding))],
        }
        session = make_session(cells, outputs)
        both = verify.live_numbers(session, cells, nb.scrub)
        assert len(both["warnings"]["lc"]) == 2
        standing = verify.warn_texts(
            make_session(cells, {at(cells, 1, 0): outputs[at(cells, 1, 0)]})
        )
        assert standing == {"Never on a shared cache. Flush is not used."}
        found = verify.live_numbers(session, cells, nb.scrub, standing)
        (kept,) = found["warnings"]["lc"]
        assert kept.startswith("warn: The service does not behave like a hard cut")

    def test_only_warnings_are_left_out_by_their_text(self, verify, nb, cells):
        hit = shows(callout_markup(nb, "ok", "**HIT** · similarity **0.912**"))
        session = make_session(cells, {at(cells, 1, 4): [hit]})
        found = verify.live_numbers(session, cells, nb.scrub, {"HIT · similarity 0.912"})
        assert found["similarities"] == [0.912]

    def test_no_session_has_no_warnings(self, verify):
        assert verify.warn_texts(None) == set() == verify.warn_texts({})


class TestSecretScan:
    ENCODINGS: ClassVar[dict] = {
        "raw": lambda s: s,
        "json": lambda s: json.dumps(s)[1:-1],
        "json, utf-8": lambda s: json.dumps(s, ensure_ascii=False)[1:-1],
        "html": html.escape,
        "html, quotes kept": lambda s: html.escape(s, quote=False),
        "percent": lambda s: quote(s, safe=""),
        "percent, plus for space": quote_plus,
        "percent, slash kept": quote,
        "html of json": lambda s: html.escape(json.dumps(s)[1:-1]),
        "json of html": lambda s: json.dumps(html.escape(s))[1:-1],
        "percent, lower-case hex": lambda s: re.sub(
            r"%[0-9A-F]{2}", lambda m: m[0].lower(), quote(s, safe="")
        ),
    }  # fmt: skip

    @pytest.fixture
    def forms(self, verify):
        return {"LANGCACHE_API_KEY": verify.spellings(SECRET)}

    def write(self, tmp_path, kind, *pieces):
        """An export html or session json whose only content is these strings."""
        cell = {"id": "c1", "outputs": [shows("<p>" + "".join(pieces) + "</p>")], "console": []}
        session = {"version": "1", "cells": [cell]}
        if kind == "session":
            path = tmp_path / "session.json"
            path.write_text(json.dumps(session), encoding="utf-8")
        else:
            payload = json.dumps({"filename": "iris_basics.py", "session": session})
            body = f"Object.defineProperty(window, 'x', {{value: Object.freeze({payload})}});"
            path = tmp_path / "out.html"
            path.write_text(
                f'<html><script data-marimo="true">{body}</script></html>', encoding="utf-8"
            )
        return path

    @pytest.mark.parametrize("kind", ["session", "html"])
    @pytest.mark.parametrize("how", list(ENCODINGS))
    def test_a_secret_is_found_in_every_spelling_in_both_files(
        self, verify, forms, tmp_path, kind, how
    ):
        path = self.write(tmp_path, kind, "before ", self.ENCODINGS[how](SECRET), " after")
        found = verify.scan_file(kind, path, forms, "")
        assert {name for name, _ in found["secrets"]} == {"LANGCACHE_API_KEY"}, how
        assert found["decoded"]

    @pytest.mark.parametrize("kind", ["session", "html"])
    def test_a_clean_file_has_no_hits(self, verify, forms, tmp_path, kind):
        found = verify.scan_file(kind, self.write(tmp_path, kind, "nothing here"), forms, "")
        assert found["secrets"] == [] and found["shapes"] == [] and found["decoded"]

    def test_a_secret_inside_a_table_is_found_although_marimo_encodes_it_three_times(
        self, verify, nb, forms, tmp_path
    ):
        table = nb.mo.ui.table([{"note": f"has {SECRET} in it"}], selection=None).text
        assert SECRET not in table and html.escape(SECRET) not in table
        found = verify.scan_file("session", self.write(tmp_path, "session", table), forms, "")
        assert [n for n, _ in found["secrets"]] == ["LANGCACHE_API_KEY"]

    def test_a_secret_inside_a_base64_data_url_is_found(self, verify, forms, tmp_path):
        rows = json.dumps([{"note": f"has {SECRET} in it"}]).encode("utf-8")
        url = "data:application/json;base64," + base64.b64encode(rows).decode("ascii")
        found = verify.scan_file("session", self.write(tmp_path, "session", url), forms, "")
        assert {n for n, _ in found["secrets"]} == {"LANGCACHE_API_KEY"}
        clean = base64.b64encode(json.dumps([{"note": "nothing here"}]).encode()).decode("ascii")
        page = self.write(tmp_path, "session", "data:application/json;base64," + clean)
        assert verify.scan_file("session", page, forms, "")["secrets"] == []

    def test_a_secret_inside_a_callout_is_found(self, verify, nb, forms, tmp_path):
        callout = nb.mo.callout(nb.mo.md(f"key {SECRET}"), kind="danger").text
        found = verify.scan_file("html", self.write(tmp_path, "html", callout), forms, "")
        assert [n for n, _ in found["secrets"]] == ["LANGCACHE_API_KEY"]

    def test_the_hit_names_the_variable_and_the_spelling_never_the_value(
        self, verify, forms, tmp_path
    ):
        path = self.write(tmp_path, "session", quote(SECRET, safe=""))
        found = verify.scan_file("session", path, forms, "")
        assert found["secrets"] == [("LANGCACHE_API_KEY", "percent")]
        text = repr(found)
        assert not any(form in text for form in verify.spellings(SECRET))

    def test_a_secret_in_a_log_counts_and_so_does_one_in_the_raw_text(
        self, verify, forms, tmp_path
    ):
        log = tmp_path / "export.log"
        log.write_text(f"Traceback ...\nValueError: {SECRET}\n", encoding="utf-8")
        found = verify.scan_file("log", log, forms, "")
        assert [n for n, _ in found["secrets"]] == ["LANGCACHE_API_KEY"] and found["decoded"]

    def test_marimos_own_escape_of_a_backslash_is_undone_before_looking(self, verify, tmp_path):
        forms = {"MEMORY_API_KEY": verify.spellings("back\\slash-secret-1")}
        log = tmp_path / "export.log"
        log.write_text("token=back&#92;slash-secret-1\n", encoding="utf-8")  # as data-html has it
        found = verify.scan_file("log", log, forms, "")
        assert [n for n, _ in found["secrets"]] == ["MEMORY_API_KEY"]

    def test_a_html_export_whose_payload_will_not_decode_is_reported_as_not_decoded(
        self, verify, forms, tmp_path
    ):
        broken = tmp_path / "out.html"
        broken.write_text(
            '<script data-marimo="true">Object.freeze({oops})</script>', encoding="utf-8"
        )
        assert verify.scan_file("html", broken, forms, "")["decoded"] is False

    def test_short_values_are_left_out_and_named(self, verify, nb, monkeypatch):
        monkeypatch.setenv("MEMORY_API_KEY", "abc")
        monkeypatch.setenv("CTX_ADMIN_KEY", "long-enough-admin-key")
        found, short = verify.collect_secrets(nb)
        assert short == ["MEMORY_API_KEY"] and list(found) == ["CTX_ADMIN_KEY"]

    def test_a_redis_url_gives_its_password_raw_and_decoded(self, verify, nb, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://default:p%40ss%2Fw0rd@db.example:6379")
        found, _ = verify.collect_secrets(nb)
        assert found["REDIS_URL password"] == "p%40ss%2Fw0rd"
        assert found["REDIS_URL password, decoded"] == "p@ss/w0rd"
        assert "composed Redis URL" not in found  # the URL as given is the one the notebook uses

    def test_a_url_composed_from_host_and_password_is_checked_with_its_password(
        self, verify, nb, monkeypatch
    ):
        for name, value in (("REDIS_HOST", "db.example"), ("REDIS_PASSWORD", REDIS_PASSWORD)):
            monkeypatch.setenv(name, value)
        found, _ = verify.collect_secrets(nb)
        assert found["REDIS_PASSWORD"] == REDIS_PASSWORD
        assert found["composed Redis URL"].startswith("redis://default:p%40ss%2Fw0rd%3A9@")
        assert found["composed Redis URL password"] == "p%40ss%2Fw0rd%3A9"
        assert found["composed Redis URL password, decoded"] == REDIS_PASSWORD

    def test_every_secret_the_notebook_knows_is_collected(self, verify, nb, monkeypatch):
        for name in nb.SECRET_ENV:
            monkeypatch.setenv(name, f"value-of-{name.lower()}")
        found, short = verify.collect_secrets(nb)
        assert set(nb.SECRET_ENV) <= set(found) and short == []


class TestSecretShapes:
    def test_they_are_the_notebooks_own(self, verify, nb):
        ours = [pattern.pattern for _, pattern, _ in verify.SHAPES]
        assert ours == [pattern.pattern for pattern, _ in nb._SECRET_SHAPES]

    @pytest.mark.parametrize(
        "leak, label, masked",
        [
            ("here: cs_agent_9x8Y7z-w", "agent or admin key", "cs_agent_****"),
            ("an ce_admin_AbCdEf", "agent or admin key", "ce_admin_****"),
            ("Authorization: Bearer abc.def-123", "bearer token", "Bearer ****"),
            (
                "redis://default:hunter2@db.example:6379",
                "redis url with a password",
                "redis://****@",
            ),
            ("rediss://:hunter2@db.example", "redis url with a password", "rediss://****@"),
        ],
    )
    def test_a_credential_shaped_string_is_a_hit_and_shown_masked(
        self, verify, leak, label, masked
    ):
        assert verify.shape_hits([leak], "") == [(label, masked)]

    @pytest.mark.parametrize(
        "text",
        [
            "Bearer ****",
            "Authorization: Bearer $LANGCACHE_API_KEY",
            "redis://:****@db.example:6379",
            "redis://default:****@db.example",
            "cs_agent_",
        ],
    )
    def test_a_masked_or_placeholder_credential_is_not(self, verify, text):
        assert verify.shape_hits([text], "") == []

    def test_a_placeholder_written_in_the_notebook_itself_is_not(self, verify):
        source = "Put them together as redis://default:<password>@<host>:<port> and Bearer <token>"
        texts = [
            "as redis://default:&lt;password&gt;@&lt;host&gt;",
            "Authorization: Bearer <token>",
        ]
        assert verify.shape_hits(texts, source) == []
        assert verify.shape_hits(["redis://default:hunter2@host"], source) != []

    def test_the_real_notebook_export_text_has_no_hits(self, verify, nb, nb_file):
        source = nb_file.read_text(encoding="utf-8")
        texts = [nb.wire_view("x", "GET https://h/\nAuthorization: Bearer ****").text]
        assert verify.shape_hits(texts, source) == []


class TestSpellings:
    def test_the_value_and_each_encoding_are_there(self, verify):
        forms = verify.spellings(SECRET)
        for how, encode in TestSecretScan.ENCODINGS.items():
            assert encode(SECRET) in forms, how
        assert forms[SECRET] == "raw" and "" not in forms

    def test_a_plain_value_has_one_spelling(self, verify):
        assert verify.spellings("abcdef123456") == {"abcdef123456": "raw"}


class TestLedger:
    def test_the_last_state_of_each_thing_wins_and_torn_lines_are_skipped(self, verify, tmp_path):
        lines = [
            {"column": "rs", "kind": "key", "ident": "k1", "state": "intent"},
            {"column": "rs", "kind": "key", "ident": "k1", "state": "created"},
            {"column": "rs", "kind": "key", "ident": "k2", "state": "created"},
            {"column": "rs", "kind": "key", "ident": "k2", "state": "deleted"},
            {"column": "lc", "kind": "entry", "ident": "e1", "state": "intent"},
        ]
        text = "\n".join(json.dumps(x) for x in lines) + '\n{"column": "am", "ki'
        (tmp_path / "deadbeef.jsonl").write_text(text, encoding="utf-8")
        totals = verify.ledger_totals(verify.read_ledger(tmp_path, "deadbeef"))
        assert totals["rs"] == {"recorded": 2, "live": 1, "sample": ["key k1"]}
        assert totals["lc"]["live"] == 1 and "am" not in totals

    def test_no_file_is_an_empty_ledger(self, verify, tmp_path):
        assert verify.read_ledger(tmp_path, "deadbeef") == {}
        assert verify.ledger_totals({}) == {}
        assert verify.live_by_kind({}) == {}

    def test_what_is_live_is_listed_by_column_and_kind(self, verify, tmp_path):
        lines = [
            {"column": "am", "kind": "session", "ident": "s1", "state": "created"},
            {"column": "am", "kind": "session", "ident": "s2", "state": "deleted"},
            {"column": "am", "kind": "memory", "ident": "m1", "state": "intent"},
            {"column": "am", "kind": "memory", "ident": "m2", "state": "deleted"},
            {"column": "rs", "kind": "key", "ident": "k1", "state": "deleted"},
        ]
        (tmp_path / "deadbeef.jsonl").write_text(
            "\n".join(json.dumps(x) for x in lines), encoding="utf-8"
        )
        live = verify.live_by_kind(verify.read_ledger(tmp_path, "deadbeef"))
        assert live == {"am": {"session": ["s1"], "memory": ["m1"]}}

    def test_it_reads_what_the_notebook_writes(self, verify, nb, tmp_path):
        nb.ledger_add("rs", "key", "irisnb_x_rs:doc:1")
        nb.ledger_add("cr", "surface", "cs_0001", "intent")
        nb.ledger_add("rs", "key", "irisnb_x_rs:doc:1", "deleted")
        totals = verify.ledger_totals(verify.read_ledger(nb.ledger_dir(), nb.RUN))
        assert totals["rs"]["live"] == 0 and totals["rs"]["recorded"] == 1
        assert totals["cr"]["live"] == 1


# ---- the children: the code that runs inside the notebook's environment ----------------------
LC_ENV = {
    "LANGCACHE_HOST": "https://lc.example.com",
    "LANGCACHE_CACHE_ID": "cache-abc123",
    "LANGCACHE_API_KEY": "fake-lc-key-9f3a",
}
AM_ENV = {
    "MEMORY_API_BASE_URL": AM_BASE,
    "MEMORY_STORE_ID": AM_STORE,
    "MEMORY_API_KEY": AM_KEY,
}
CR_ENV = {"CTX_ADMIN_KEY": ADMIN_KEY, "REDIS_URL": CR_URL, "CTX_API_URL": CR_API}


class Clock:
    """The fake cache's seconds; sleeping moves it, so an expiry check takes no time."""

    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def configure(monkeypatch, **groups):
    for env in groups.values():
        for name, value in env.items():
            monkeypatch.setenv(name, value)


def answer_redis(monkeypatch, nb, replies):
    """Answer Redis commands from a table (a reply, or a function of the arguments)."""
    sent = []

    def execute(self, *args, **options):
        sent.append(tuple(nb._txt(a) for a in args))
        reply = replies[nb._txt(args[0]).upper()]
        return reply(*args[1:]) if callable(reply) else reply

    monkeypatch.setattr(redis.Redis, "execute_command", execute)
    return sent


class TestProbe:
    def test_it_names_the_settings_present_and_the_run_it_is_running_as(
        self, verify, nb, monkeypatch
    ):
        configure(monkeypatch, lc=LC_ENV)
        found = verify.child_probe(nb, {})
        assert found["present"] == sorted(LC_ENV) and found["run"] == nb.RUN
        assert found["pinned"] is nb.RUN_PINNED and found["ledger"] == str(nb.ledger_dir())
        assert LC_ENV["LANGCACHE_API_KEY"] not in json.dumps(found)
        assert "indexes" not in found and "indexes_error" not in found  # no database, no list

    def test_with_a_database_it_lists_the_indexes_there_are_before_anything_is_written(
        self, verify, nb, monkeypatch
    ):
        monkeypatch.setenv("REDIS_URL", CR_URL)
        sent = answer_redis(monkeypatch, nb, {"PING": True, "FT._LIST": [b"zeta", b"alpha"]})
        found = verify.child_probe(nb, {})
        assert found["indexes"] == ["alpha", "zeta"] and "indexes_error" not in found
        assert {c[0] for c in sent} == {"PING", "FT._LIST"}  # reads only

    def test_a_database_that_cannot_list_its_indexes_is_a_note_not_a_failed_probe(
        self, verify, nb, monkeypatch
    ):
        monkeypatch.setenv("REDIS_URL", CR_URL)

        def no_search():
            raise redis.ResponseError("unknown command 'FT._LIST'")

        answer_redis(monkeypatch, nb, {"PING": True, "FT._LIST": no_search})
        found = verify.child_probe(nb, {})
        assert "indexes" not in found and "FT._LIST" in found["indexes_error"]
        assert found["pinned"] is nb.RUN_PINNED


class TestSweeps:
    def test_langcache_deletes_this_runs_ledgered_entries_and_nobody_elses(
        self, verify, nb, monkeypatch, mock_http
    ):
        configure(monkeypatch, lc=LC_ENV)
        cache = FakeLangCache("cache-abc123", LC_ENV["LANGCACHE_API_KEY"])
        mock_http(handler=cache.handle)
        conn, _ = nb.lc_connection("", "", None, "")
        mine = [nb.lc_store(conn, f"Cairn Outfitters {n}", "answer", 60_000) for n in range(2)]
        theirs = cache.add_foreign("What the live demo cached", "an answer")
        (nb.ledger_dir() / "deadbeef.jsonl").write_text(
            json.dumps({"column": "lc", "kind": "entry", "ident": theirs, "state": "created"})
            + "\n",
            encoding="utf-8",
        )  # another run's ledger names another entry
        found = verify.child_sweep(nb, {"columns": ["lc"]})
        assert found["lc"]["done"] == {"entries": 2, "deleted": 2, "expired": 0}
        assert theirs in cache.entries and not set(mine) & set(cache.entries)
        assert nb.ledger_live("lc", "entry", pending=True) == []

    def test_agent_memory_sweeps_the_owner_including_what_extraction_added(
        self, verify, nb, monkeypatch, mock_http
    ):
        configure(monkeypatch, am=AM_ENV)
        store = FakeMemory()
        mock_http(handler=store.handle)
        conn, _ = nb.am_settings("", "", None)
        nb.am_write_memories(conn)
        nb.am_add_event(conn, nb.am_session_id(1), "I run before sunrise.")
        store.add_memory("late-1", "extracted later", nb.AM_OWNER, nb.AM_NAMESPACE)
        store.add_memory("demo-1", "Priya prefers aisle seats.", "CUST-DEMO-001", "radish")
        found = verify.child_sweep(nb, {"columns": ["am"]})
        assert found["am"]["done"]["memories"] == 6 and found["am"]["done"]["extracted"] == 1
        assert list(store.memories) == ["demo-1"] and store.sessions == {}

    def test_agent_memory_asks_the_service_nothing_when_the_run_wrote_nothing(
        self, verify, nb, monkeypatch, mock_http
    ):
        configure(monkeypatch, am=AM_ENV)
        store = FakeMemory()
        mock_http(handler=store.handle)
        assert verify.child_sweep(nb, {"columns": ["am"]})["am"]["done"]["ran"] is False
        assert store.log == []

    def test_context_retriever_deletes_the_surface_and_what_it_built(self, verify, nb, monkeypatch):
        configure(monkeypatch, cr=CR_ENV)
        world, urls = FakeWorld(), []

        def send(self, request, **kwargs):
            urls.append(str(request.url))
            return world.http(request)

        monkeypatch.setattr(redis.Redis, "execute_command", lambda self, *a, **k: world.handle(a))
        monkeypatch.setattr(httpx.Client, "send", send)
        other = world.add_surface("iris-basics-feedcafe")
        conn = nb.cr_probe(nb.Secret(ADMIN_KEY), nb.Secret(CR_URL), "", "")
        nb.cr_launch(conn, nb.cr_build_models(nb.CR_PREFIX))
        assert len(world.surfaces) == 2 and world.indexes
        before = len(urls)
        found = verify.child_sweep(nb, {"columns": ["cr"]})
        assert len(urls) > before and all(u.startswith(CR_API) for u in urls[before:])
        done = found["cr"]["done"]
        assert (done["surfaces"], done["deleted"], done["left"], done["stopped"]) == (1, 1, [], "")
        assert list(world.surfaces) == [other] and world.docs == {} and world.indexes == {}

    def test_redis_drops_the_ledgered_index_without_dd_and_unlinks_the_ledgered_keys(
        self, verify, nb, monkeypatch
    ):
        monkeypatch.setenv("REDIS_URL", CR_URL)
        sent = answer_redis(monkeypatch, nb, {"PING": True, "FT.DROPINDEX": b"OK", "UNLINK": 2})
        index, keys = nb.RS_PREFIX + "idx", [nb.RS_PREFIX + "doc:1", nb.RS_PREFIX + "doc:2"]
        nb.ledger_add("rs", "index", index)
        for key in keys:
            nb.ledger_add("rs", "key", key)
        found = verify.child_sweep(nb, {"columns": ["rs"]})
        assert found["rs"]["done"] == {
            "indexes": 1, "dropped": 1, "gone": 0, "keys": 2, "removed": 2,
        }  # fmt: skip
        assert ("FT.DROPINDEX", index) in sent and ("UNLINK", *keys) in sent
        assert not any("DD" in command for command in sent)
        assert nb.ledger_live("rs", "key", pending=True) == []

    def test_a_column_that_cannot_be_swept_is_reported_and_the_others_still_are(
        self, verify, nb, monkeypatch, mock_http
    ):
        configure(monkeypatch, am=AM_ENV)  # LangCache is not configured
        mock_http(handler=FakeMemory().handle)
        found = verify.child_sweep(nb, {"columns": ["lc", "am"]})
        assert found["lc"]["error"].startswith("ValueError: missing LANGCACHE_HOST")
        assert "done" in found["am"]

    def test_a_sweep_calls_no_service_when_the_ledger_is_empty(self, verify, nb, monkeypatch):
        configure(monkeypatch, cr=CR_ENV)
        world = FakeWorld()
        monkeypatch.setattr(httpx.Client, "send", lambda self, request, **k: world.http(request))
        found = verify.child_sweep(nb, {"columns": ["cr"]})
        assert found["cr"]["done"]["surfaces"] == 0 and world.requests == []


class TestSentinel:
    @pytest.fixture
    def cache(self, nb, monkeypatch, mock_http):
        configure(monkeypatch, lc=LC_ENV)
        clock = Clock()
        cache = FakeLangCache("cache-abc123", LC_ENV["LANGCACHE_API_KEY"], clock=clock)
        mock_http(handler=cache.handle)
        cache.clock_object = clock
        return cache

    def test_an_entry_that_expires_passes_and_is_deleted_by_id(self, verify, nb, cache):
        found = verify.child_sentinel(nb, {}, sleep=cache.clock_object.sleep)
        assert found["seen_alive"] and found["gone"] and found["final"] == "expired"
        assert 4 <= found["waited"] <= 8 and found["ttl_ms"] == 5000
        (stored,) = [
            body for method, path, body in cache.calls if (method, path) == ("POST", "/entries")
        ]
        assert stored["ttlMillis"] == 5000 and stored["prompt"].startswith("Cairn Outfitters")
        assert stored["prompt"].endswith(nb.RUN)
        assert cache.calls[-1][:2] == ("DELETE", f"/entries/{found['entry']}")
        assert verify.sentinel_verdict(found)[0] == "PASS"

    def test_the_sentinel_is_not_in_the_ledger_but_the_choke_point_was_told_about_it(
        self, verify, nb, cache
    ):
        found = verify.child_sentinel(nb, {}, sleep=cache.clock_object.sleep)
        lines = (nb.ledger_dir() / f"{nb.RUN}.jsonl").read_text(encoding="utf-8").splitlines()
        # Only the delete by id writes to the ledger: the entry was never recorded as created.
        assert [json.loads(line)["state"] for line in lines] == ["deleted"]
        assert found["entry"] in nb.APPROVED["lc_entry"]

    def test_an_entry_that_never_expires_fails_after_a_minute_and_is_still_deleted(
        self, verify, nb, cache
    ):
        put = cache.put
        cache.put = lambda prompt, response, attributes, ttl_ms: put(
            prompt, response, attributes, None
        )
        found = verify.child_sentinel(nb, {}, sleep=cache.clock_object.sleep)
        assert found["gone"] is False and found["waited"] == verify.SENTINEL_WAIT
        assert found["final"] == "deleted" and found["entry"] not in cache.entries
        status, detail, _ = verify.sentinel_verdict(found)
        assert status == "FAIL" and "not honoured" in detail

    def test_it_polls_by_search_and_stops_asking_once_the_entry_is_gone(self, verify, nb, cache):
        found = verify.child_sentinel(nb, {}, sleep=cache.clock_object.sleep)
        searches = [
            c
            for c in cache.calls
            if c[:2] == ("POST", "/entries/search") and c[2]["prompt"].endswith(nb.RUN)
        ]  # the cache is probed once first, with a prompt of its own
        assert len(searches) == found["waited"] // verify.SENTINEL_POLL + 1

    def test_a_scoped_cache_gets_the_runs_tag_on_the_entry_and_the_search(
        self, verify, nb, monkeypatch, mock_http
    ):
        configure(monkeypatch, lc={**LC_ENV, "LANGCACHE_SCOPE_ATTR": "scope"})
        clock = Clock()
        cache = FakeLangCache(
            "cache-abc123", LC_ENV["LANGCACHE_API_KEY"], attributes=("scope",), clock=clock
        )
        mock_http(handler=cache.handle)
        found = verify.child_sentinel(nb, {}, sleep=clock.sleep)
        bodies = [body for _, _, body in cache.calls if body]
        assert all(body["attributes"] == {"scope": nb.RUN_TAG} for body in bodies)
        assert found["seen_alive"] and found["final"] == "expired"

    def test_a_cache_without_the_scope_attribute_is_used_unscoped_as_the_notebook_does(
        self, verify, nb, monkeypatch, mock_http
    ):
        configure(monkeypatch, lc={**LC_ENV, "LANGCACHE_SCOPE_ATTR": "scope"})
        clock = Clock()
        cache = FakeLangCache("cache-abc123", LC_ENV["LANGCACHE_API_KEY"], clock=clock)
        mock_http(handler=cache.handle)
        found = verify.child_sentinel(nb, {}, sleep=clock.sleep)
        mine = [body for _, _, body in cache.calls if body and body["prompt"].endswith(nb.RUN)]
        assert len(mine) > 2 and all("attributes" not in body for body in mine)
        assert found["seen_alive"] and found["gone"] and found["final"] == "expired"

    def test_a_cache_that_is_not_configured_is_an_error_naming_the_settings(self, verify, nb):
        with pytest.raises(ValueError, match="missing LANGCACHE_HOST"):
            verify.child_sentinel(nb, {})

    def hide(self, cache, until=None):
        """Make searches miss an entry until `until` seconds after it was stored (None: always)."""
        real = cache.search

        def search(body):
            hits = json.loads(real(body).content)["data"]
            entries = cache.entries

            def age(hit):
                return cache.clock() - (entries[hit["id"]]["expires"] - 5)

            keep = [h for h in hits if until is not None and age(h) >= until]
            return httpx.Response(200, json={"data": keep})

        cache.search = search

    def test_a_search_that_is_slow_to_find_a_new_entry_is_given_time_not_a_pass(
        self, verify, nb, cache
    ):
        self.hide(cache, until=3)
        found = verify.child_sentinel(nb, {}, sleep=cache.clock_object.sleep)
        assert found["seen_alive"] and found["gone"] and found["final"] == "expired"
        assert verify.sentinel_verdict(found)[0] == "PASS"

    def test_an_entry_the_search_never_finds_that_has_expired_is_a_warning(self, verify, nb, cache):
        self.hide(cache)
        found = verify.child_sentinel(nb, {}, sleep=cache.clock_object.sleep)
        assert found["seen_alive"] is False and found["gone"] and found["final"] == "expired"
        assert found["waited"] == verify.SENTINEL_GRACE  # it waited past the expiry to be sure
        assert verify.sentinel_verdict(found)[0] == "WARN"

    def test_an_entry_the_search_never_finds_that_is_still_there_after_its_expiry_fails(
        self, verify, nb, cache
    ):
        self.hide(cache)
        put = cache.put
        cache.put = lambda prompt, response, attributes, ttl_ms: put(
            prompt, response, attributes, None
        )
        found = verify.child_sentinel(nb, {}, sleep=cache.clock_object.sleep)
        assert found["seen_alive"] is False and found["final"] == "deleted"
        status, detail, _ = verify.sentinel_verdict(found)
        assert status == "FAIL" and "never listed" in detail

    def test_the_verdicts(self, verify):
        base = {"ttl_ms": 5000, "seen_alive": True, "gone": True, "waited": 6, "final": "expired"}
        assert verify.sentinel_verdict(base)[0] == "PASS"
        assert verify.sentinel_verdict({**base, "seen_alive": False})[0] == "WARN"
        assert verify.sentinel_verdict({**base, "final": "deleted"})[0] == "WARN"
        assert (
            verify.sentinel_verdict({**base, "seen_alive": False, "final": "deleted"})[0] == "FAIL"
        )
        assert verify.sentinel_verdict({**base, "gone": False, "waited": 60})[0] == "FAIL"


class TestFootprintChildren:
    def test_agent_memory_counts_this_owners_memories_and_sessions_and_no_others(
        self, verify, nb, monkeypatch, mock_http
    ):
        configure(monkeypatch, am=AM_ENV)
        store = FakeMemory()
        mock_http(handler=store.handle)
        store.add_memory("demo-1", "not ours", "CUST-DEMO-001", "radish")
        assert verify.footprint_memory(nb) == {
            "count": 0,
            "sample": [],
            "strangers": 0,
            "sessions": 0,
        }
        conn, _ = nb.am_settings("", "", None)
        nb.am_write_memories(conn)
        nb.am_add_event(conn, nb.am_session_id(1), "hello")
        found = verify.footprint_memory(nb)
        assert found["count"] == 5 and found["sessions"] == 1 and found["strangers"] == 0
        assert all(i.startswith(nb.AM_OWNER) for i in found["sample"]) and len(found["sample"]) == 5

    def test_a_service_that_ignores_the_owner_filter_is_caught(
        self, verify, nb, monkeypatch, mock_http
    ):
        configure(monkeypatch, am=AM_ENV)
        store = FakeMemory()
        store.ignore_filters = True
        mock_http(handler=store.handle)
        store.add_memory("demo-1", "not ours", "CUST-DEMO-001", "radish")
        found = verify.footprint_memory(nb)
        assert found["count"] == 0 and found["strangers"] == 1

    def test_a_failing_session_list_is_a_note_and_the_memories_still_count(
        self, verify, nb, monkeypatch, mock_http
    ):
        configure(monkeypatch, am=AM_ENV)
        store = FakeMemory()

        def handler(request):
            if request.url.path.endswith("/session-memory"):
                return httpx.Response(500, json={"title": "oops"})
            return store.handle(request)

        mock_http(handler=handler)
        found = verify.footprint_memory(nb)
        assert found["count"] == 0 and "sessions_error" in found and "sessions" not in found

    def test_context_retriever_counts_surfaces_named_for_this_run_only(
        self, verify, nb, monkeypatch
    ):
        configure(monkeypatch, cr=CR_ENV)
        world = FakeWorld()
        monkeypatch.setattr(redis.Redis, "execute_command", lambda self, *a, **k: world.handle(a))
        monkeypatch.setattr(httpx.Client, "send", lambda self, request, **k: world.http(request))
        world.add_surface("iris-basics-feedcafe")
        assert verify.footprint_surfaces(nb) == {"count": 0, "sample": []}
        conn = nb.cr_probe(nb.Secret(ADMIN_KEY), nb.Secret(CR_URL), "", "")
        nb.cr_launch(conn, nb.cr_build_models(nb.CR_PREFIX))
        found = verify.footprint_surfaces(nb)
        assert found["count"] == 1 and len(found["sample"]) == 1

    def test_redis_scans_this_runs_prefix_and_lists_indexes_that_cover_it(
        self, verify, nb, monkeypatch
    ):
        monkeypatch.setenv("REDIS_URL", CR_URL)
        prefixes = {
            "mine": [(nb.CR_PREFIX + "order:").encode()],
            "both": [b"other:", (nb.RS_PREFIX + "doc:").encode()],
            "theirs": [b"irisnb_deadbeef_rs:"],
            "gone": None,
        }

        def info(name):
            if prefixes[name] is None:  # dropped since it was listed; Redis 8.6.2's wording
                raise redis.ResponseError(f"SEARCH_INDEX_NOT_FOUND Index not found: {name}")
            definition = [b"key_type", b"HASH", b"prefixes", prefixes[name]]
            return [b"index_name", name.encode(), b"index_definition", definition]

        keys = [(nb.RS_PREFIX + "doc:1").encode(), (nb.CR_PREFIX + "order:1").encode()]
        sent = answer_redis(
            monkeypatch, nb,
            {
                "PING": True, "SCAN": (b"0", keys), "FT.INFO": info,
                "FT._LIST": [n.encode() for n in prefixes],
            },
        )  # fmt: skip
        found = verify.footprint_redis(nb, ["theirs"])
        assert found["redis_keys"]["count"] == 2
        assert sorted(found["indexes"]["sample"]) == ["both", "mine"]
        assert found["indexes_new"] == {"count": 0, "sample": []}  # "theirs" was there before
        scan = next(c for c in sent if c[0] == "SCAN")
        assert scan[scan.index("MATCH") + 1] == nb.PREFIX + "*" and scan[-1] == 1000
        assert {c[0] for c in sent} == {"PING", "SCAN", "FT._LIST", "FT.INFO"}  # reads only

    def test_a_database_without_search_reports_the_index_check_as_failed_not_empty(
        self, verify, nb, monkeypatch
    ):
        monkeypatch.setenv("REDIS_URL", CR_URL)

        def no_search():
            raise redis.ResponseError("unknown command 'FT._LIST'")

        answer_redis(monkeypatch, nb, {"PING": True, "SCAN": (b"0", []), "FT._LIST": no_search})
        found = verify.footprint_redis(nb, [])
        assert found["redis_keys"]["count"] == 0 and "error" in found["indexes"]
        assert found["indexes_new"] == found["indexes"]

    def indexes(self, monkeypatch, nb, prefixes, info=None):
        """A database whose indexes cover these prefixes (bytes), one index each."""
        monkeypatch.setenv("REDIS_URL", CR_URL)

        def describe(name):
            definition = [b"key_type", b"HASH", b"prefixes", prefixes[name]]
            return [b"index_name", name.encode(), b"index_definition", definition]

        answer_redis(
            monkeypatch, nb,
            {
                "PING": True, "SCAN": (b"0", []), "FT.INFO": info or describe,
                "FT._LIST": [name.encode() for name in prefixes],
            },
        )  # fmt: skip

    def test_an_index_that_is_new_and_not_under_the_prefix_is_reported_apart(
        self, verify, nb, monkeypatch
    ):
        prefixes = {
            "old": [b"app:"], "new-theirs": [b"app2:"],
            "mine": [(nb.CR_PREFIX + "order:").encode()],
        }  # fmt: skip
        self.indexes(monkeypatch, nb, prefixes)
        found = verify.footprint_redis(nb, ["old"])
        assert found["indexes"] == {"count": 1, "sample": ["mine"]}
        assert found["indexes_new"] == {"count": 1, "sample": ["new-theirs"]}
        assert verify.footprint_redis(nb, ["old", "new-theirs"])["indexes_new"]["count"] == 0

    def test_without_the_list_from_before_the_exports_new_indexes_cannot_be_judged(
        self, verify, nb, monkeypatch
    ):
        self.indexes(monkeypatch, nb, {"old": [b"app:"]})
        found = verify.footprint_redis(nb, None)
        assert found["indexes"]["count"] == 0
        assert "before the exports" in found["indexes_new"]["error"]

    def test_an_index_that_will_not_answer_is_an_error_not_an_index_that_is_gone(
        self, verify, nb, monkeypatch
    ):
        def loading(name):
            raise redis.ResponseError("Index is loading, try again")

        prefixes = {"mine": [(nb.CR_PREFIX + "order:").encode()]}
        self.indexes(monkeypatch, nb, prefixes, info=loading)
        found = verify.footprint_redis(nb, [])
        assert "Index is loading" in found["indexes"]["error"]
        assert found["indexes_new"] == found["indexes"] and found["redis_keys"]["count"] == 0

    def test_only_configured_columns_are_asked(self, verify, nb, monkeypatch):
        seen = []
        monkeypatch.setattr(
            verify, "footprint_redis", lambda nb, before: seen.append("rs") or {"redis_keys": {}}
        )
        monkeypatch.setattr(verify, "footprint_memory", lambda nb: seen.append("am") or {})
        monkeypatch.setattr(verify, "footprint_surfaces", lambda nb: seen.append("cr") or {})
        verify.child_footprint(nb, {"columns": ["lc", "am"]})
        assert seen == ["am"]

    def test_a_column_that_fails_to_answer_is_an_error_entry_and_the_rest_still_answer(
        self, verify, nb, monkeypatch
    ):
        def down(nb, before):
            raise ValueError("Redis is down")

        monkeypatch.setattr(verify, "footprint_redis", down)
        monkeypatch.setattr(verify, "footprint_surfaces", lambda nb: {"count": 0, "sample": []})
        found = verify.child_footprint(nb, {"columns": ["rs", "cr"]})
        assert found["redis_keys"] == {"error": "ValueError: Redis is down"} == found["indexes"]
        assert found["indexes_new"] == found["indexes"]
        assert found["surfaces"] == {"count": 0, "sample": []}

    def test_the_indexes_from_before_the_exports_reach_the_redis_check(
        self, verify, nb, monkeypatch
    ):
        seen = []
        monkeypatch.setattr(
            verify, "footprint_redis", lambda nb, before: seen.append(before) or {"redis_keys": {}}
        )
        verify.child_footprint(nb, {"columns": ["rs"], "indexes_before": ["a"]})
        verify.child_footprint(nb, {"columns": ["rs"]})
        assert seen == [["a"], None]


class TestChildMain:
    def run(self, verify, monkeypatch, capsys, action, spec=None):
        Path(verify.CHILD_SPEC).write_text(json.dumps(spec or {}), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["-c", action])
        try:
            verify.child_main()
            code = 0
        except SystemExit as exit_:
            code = exit_.code
        return code, capsys.readouterr().out

    def test_a_result_is_one_line_of_json(self, verify, monkeypatch, capsys):
        monkeypatch.setenv("LANGCACHE_HOST", "lc.example.com")
        code, out = self.run(verify, monkeypatch, capsys, "probe")
        assert code == 0 and out.count("\n") == 1 and out.startswith(verify.RESULT)
        assert json.loads(out[len(verify.RESULT) :])["present"] == ["LANGCACHE_HOST"]

    def test_every_string_in_the_result_is_scrubbed_with_the_notebooks_own_scrub(
        self, verify, nb, monkeypatch, capsys
    ):
        monkeypatch.setenv("LANGCACHE_API_KEY", "lc-key-abcdef-123456")
        nb.env("LANGCACHE_API_KEY")

        def leaky(nb, spec):
            return {"a": ["Bearer lc-key-abcdef-123456"], "b": {"lc-key-abcdef-123456": 1}}

        monkeypatch.setitem(verify.CHILD_ACTIONS, "probe", leaky)
        code, out = self.run(verify, monkeypatch, capsys, "probe")
        assert code == 0 and "lc-key-abcdef-123456" not in out and "****" in out

    def test_a_failure_prints_one_scrubbed_line_and_exits_3_with_no_traceback(
        self, verify, nb, monkeypatch, capsys
    ):
        monkeypatch.setenv("LANGCACHE_API_KEY", "lc-key-abcdef-123456")
        nb.env("LANGCACHE_API_KEY")

        def boom(nb, spec):
            raise ValueError("bad key lc-key-abcdef-123456")

        monkeypatch.setitem(verify.CHILD_ACTIONS, "probe", boom)
        code, out = self.run(verify, monkeypatch, capsys, "probe")
        assert code == 3 and out == f"{verify.FAILURE}ValueError: bad key ****\n"

    def test_an_unknown_action_is_a_failure(self, verify, monkeypatch, capsys):
        code, out = self.run(verify, monkeypatch, capsys, "bogus")
        assert code == 3 and out.startswith(verify.FAILURE + "KeyError")


class TestRunProcess:
    def test_it_returns_the_exit_code_and_both_streams_together(self, verify, tmp_path):
        code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
        rc, out = verify.run_process([sys.executable, "-c", code], tmp_path, os.environ.copy(), 60)
        assert rc == 3 and "out" in out and "err" in out

    def heartbeat_tree(self, tmp_path):
        """(the heartbeat file, the source of a command): the command starts a child that writes
        the heartbeat, and stops by itself after a minute if nothing kills it."""
        beat = tmp_path / "beat"
        child = (
            "import pathlib, time\n"
            "for _ in range(600):\n"
            f" pathlib.Path({str(beat)!r}).write_text(str(time.time()))\n time.sleep(0.1)\n"
        )
        parent = (
            "import pathlib, subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
            f"while not pathlib.Path({str(beat)!r}).exists():\n time.sleep(0.05)\n"
            "print('started', flush=True)\ntime.sleep(120)\n"
        )
        return beat, parent

    def test_an_interrupt_kills_the_command_and_the_children_it_started_and_goes_on_up(
        self, verify, tmp_path, monkeypatch
    ):
        beat, parent = self.heartbeat_tree(tmp_path)

        class Interrupted(subprocess.Popen):
            def communicate(self, input=None, timeout=None):
                if timeout != 60:  # subprocess.run's own wait, for taskkill, is left alone
                    return super().communicate(input, timeout)
                while not beat.exists():
                    time.sleep(0.05)
                raise KeyboardInterrupt  # Ctrl-C, once the grandchild is running

        monkeypatch.setattr(subprocess, "Popen", Interrupted)
        with pytest.raises(KeyboardInterrupt):
            verify.run_process([sys.executable, "-c", parent], tmp_path, os.environ.copy(), 60)
        stamp = beat.read_text()
        time.sleep(1.5)
        assert beat.read_text() == stamp, "the grandchild is still running"

    def test_a_timeout_kills_the_command_and_the_children_it_started(self, verify, tmp_path):
        beat, parent = self.heartbeat_tree(tmp_path)
        started = time.monotonic()
        rc, out = verify.run_process([sys.executable, "-c", parent], tmp_path, os.environ.copy(), 8)
        assert rc is None and "started" in out and time.monotonic() - started < 60
        stamp = beat.read_text()
        time.sleep(1.5)
        assert beat.read_text() == stamp, "the grandchild is still running"


class TestSweepVerdict:
    def test_each_column_reads_its_own_counts(self, verify):
        rs = {"done": {"indexes": 1, "dropped": 1, "gone": 0, "keys": 37, "removed": 37}}
        assert verify.sweep_verdict("rs", rs) == (
            "PASS",
            "dropped 1 of 1 indexes (0 already gone), unlinked 37 of 37 keys",
        )
        lc = {"done": {"entries": 12, "deleted": 10, "expired": 2}}
        assert verify.sweep_verdict("lc", lc)[1] == "deleted 10 and 2 had expired, of 12 entries"

    def test_a_context_retriever_sweep_that_left_or_stopped_is_a_failure(self, verify):
        done = {"surfaces": 1, "deleted": 1, "keys": 3, "removed": 3, "indexes": 3, "dropped": 3,
                "gone": 0}  # fmt: skip
        assert (
            verify.sweep_verdict("cr", {"done": {**done, "left": [], "stopped": ""}})[0] == "PASS"
        )
        left = verify.sweep_verdict("cr", {"done": {**done, "left": ["idx"], "stopped": ""}})
        assert left[0] == "FAIL" and "left behind: ['idx']" in left[1]
        stopped = verify.sweep_verdict(
            "cr", {"done": {**done, "left": [], "stopped": "Redis down"}}
        )
        assert stopped[0] == "FAIL" and "Redis down" in stopped[1]

    def test_indexes_the_service_dropped_with_the_surface_are_a_pass_and_are_counted(self, verify):
        done = {"surfaces": 1, "deleted": 1, "keys": 20, "removed": 20, "indexes": 3, "dropped": 0,
                "gone": 3, "left": [], "stopped": ""}  # fmt: skip
        status, detail = verify.sweep_verdict("cr", {"done": done})
        assert status == "PASS" and "dropped 0 of 3 indexes (3 already gone)" in detail
        rs = {"indexes": 1, "dropped": 0, "gone": 1, "keys": 36, "removed": 36}
        assert verify.sweep_verdict("rs", {"done": rs}) == (
            "PASS",
            "dropped 0 of 1 indexes (1 already gone), unlinked 36 of 36 keys",
        )

    def test_agent_memory_that_is_still_listed_is_a_warning_and_the_footprint_decides(self, verify):
        done = {"ran": True, "sessions": 1, "memories": 6, "extracted": 1, "left": 0, "refused": 0,
                "sessions_left": 0}  # fmt: skip
        assert verify.sweep_verdict("am", {"done": done})[0] == "PASS"
        assert verify.sweep_verdict("am", {"done": {**done, "left": 2}})[0] == "WARN"
        assert verify.sweep_verdict("am", {"done": {**done, "sessions_left": 1}})[0] == "WARN"
        assert "wrote nothing" in verify.sweep_verdict("am", {"done": {"ran": False}})[1]

    def test_an_error_is_a_failure_with_its_message(self, verify):
        assert verify.sweep_verdict("lc", {"error": "ServiceError: HTTP 401"}) == (
            "FAIL",
            "ServiceError: HTTP 401",
        )


class TestFootprintRows:
    CLEAN: ClassVar[dict] = {
        "redis_keys": {"count": 0, "sample": []},
        "indexes": {"count": 0, "sample": []},
        "indexes_new": {"count": 0, "sample": []},
        "memory": {"count": 0, "sample": [], "strangers": 0, "sessions": 0},
        "surfaces": {"count": 0, "sample": []},
    }
    ALL: ClassVar[list] = ["rs", "lc", "cr", "am"]

    def rows(self, verify, found=None, totals=None, configured=ALL):
        found = self.CLEAN if found is None else found
        rows = verify.footprint_rows(found, totals or {}, configured, "0123abcd")
        return {row.name.removeprefix("footprint: "): row for row in rows}

    def test_everything_empty_is_a_pass_in_every_place(self, verify):
        rows = self.rows(verify)
        assert {r.status for r in rows.values()} == {"PASS"} and len(rows) == 6

    def test_the_places_are_named_for_this_run(self, verify):
        assert set(self.rows(verify)) == {
            "keys irisnb_0123abcd_*", "indexes under irisnb_0123abcd_", "ledger",
            "new indexes outside irisnb_0123abcd_", "Agent Memory ib-0123abcd",
            "surfaces iris-basics-0123abcd",
        }  # fmt: skip

    def test_a_new_index_outside_the_prefix_is_a_warning_that_names_it(self, verify):
        found = {**self.CLEAN, "indexes_new": {"count": 2, "sample": ["app-idx", "other-idx"]}}
        row = self.rows(verify, found)["new indexes outside irisnb_0123abcd_"]
        assert row.status == "WARN"
        assert "2 indexes appeared" in row.detail and "app-idx, other-idx" in row.detail

    def test_new_indexes_that_could_not_be_compared_are_a_failure_not_an_empty(self, verify):
        error = {"error": "the probe listed no indexes from before the exports"}
        row = self.rows(verify, {**self.CLEAN, "indexes_new": error})[
            "new indexes outside irisnb_0123abcd_"
        ]
        assert row.status == "FAIL" and "could not check: the probe" in row.detail

    def test_a_key_or_an_index_left_behind_fails_and_says_which(self, verify):
        found = {
            **self.CLEAN, "redis_keys": {"count": 3, "sample": ["irisnb_0123abcd_rs:doc:1"]},
            "indexes": {"count": 1, "sample": ["irisnb_0123abcd_rs:idx"]},
        }  # fmt: skip
        rows = self.rows(verify, found)
        assert rows["keys irisnb_0123abcd_*"].status == "FAIL"
        assert "3 keys left, e.g. irisnb_0123abcd_rs:doc:1" in rows["keys irisnb_0123abcd_*"].detail
        assert rows["indexes under irisnb_0123abcd_"].status == "FAIL"

    def test_something_live_in_the_ledger_fails_and_names_the_column(self, verify):
        totals = {
            "lc": {"recorded": 5, "live": 2, "sample": []},
            "rs": {"recorded": 3, "live": 0, "sample": []},
        }
        row = self.rows(verify, totals=totals)["ledger"]
        assert row.status == "FAIL" and row.detail == "still live: lc 2"

    def test_agent_memory_memories_fail_and_sessions_only_warn(self, verify):
        left = {"count": 2, "sample": ["a", "b"], "strangers": 0, "sessions": 0}
        assert (
            self.rows(verify, {**self.CLEAN, "memory": left})["Agent Memory ib-0123abcd"].status
            == "FAIL"
        )
        sessions = {"count": 0, "sample": [], "strangers": 0, "sessions": 1}
        row = self.rows(verify, {**self.CLEAN, "memory": sessions})["Agent Memory ib-0123abcd"]
        assert row.status == "WARN" and "expire on their own" in row.detail

    def test_a_store_that_lists_other_owners_memories_cannot_be_judged_empty(self, verify):
        found = {**self.CLEAN, "memory": {"count": 0, "sample": [], "strangers": 2, "sessions": 0}}
        row = self.rows(verify, found)["Agent Memory ib-0123abcd"]
        assert row.status == "FAIL" and "filter ignored" in row.detail

    def test_a_context_retriever_surface_left_fails(self, verify):
        found = {**self.CLEAN, "surfaces": {"count": 1, "sample": ["cs_0001"]}}
        assert self.rows(verify, found)["surfaces iris-basics-0123abcd"].status == "FAIL"

    def test_a_place_that_could_not_be_checked_is_a_failure_not_an_empty(self, verify):
        found = {**self.CLEAN, "redis_keys": {"error": "ConnectionError: refused"}}
        row = self.rows(verify, found)["keys irisnb_0123abcd_*"]
        assert row.status == "FAIL" and "could not check: ConnectionError" in row.detail

    def test_a_column_that_is_not_configured_is_skipped(self, verify):
        rows = self.rows(verify, {}, configured=["lc"])
        assert rows["keys irisnb_0123abcd_*"].status == "SKIP"
        assert rows["new indexes outside irisnb_0123abcd_"].status == "SKIP"
        assert rows["Agent Memory ib-0123abcd"].status == "SKIP"
        assert rows["surfaces iris-basics-0123abcd"].status == "SKIP"
        assert rows["ledger"].status == "PASS"


class TestCellRows:
    def judge(self, verify, nb, cells, outputs, phase="demo"):
        return {"judge": judged(verify, nb, cells, outputs, phase)}

    def totals(self):
        return {key: {"recorded": 3, "live": 0, "sample": []} for key in ("rs", "lc", "cr", "am")}

    def test_a_clean_session_gives_a_pass_for_shared_and_each_configured_column(
        self, verify, nb, cells
    ):
        entry = self.judge(verify, nb, cells, {at(cells, 1, 3): [stopped()]})
        rows = verify.cell_rows("demo", entry, ["rs", "lc"], self.totals())
        assert [(r.name, r.status) for r in rows] == [
            ("demo cells: shared", "PASS"), ("demo cells: 0 Redis Search", "PASS"),
            ("demo cells: 1 LangCache", "PASS"),
        ]  # fmt: skip
        assert "1 waiting on a button" in rows[2].detail

    def test_the_demo_rows_of_the_configured_columns_are_the_columns_exercised(
        self, verify, nb, cells
    ):
        demo = self.judge(verify, nb, cells, {})
        rows = verify.cell_rows("demo", demo, ["rs", "lc"], self.totals())
        assert [(r.name.removeprefix("demo cells: "), r.tested) for r in rows] == [
            ("shared", False), ("0 Redis Search", True), ("1 LangCache", True),
        ]  # fmt: skip
        failed = self.judge(verify, nb, cells, {at(cells, 1, 2): [crashed()]})
        rows = verify.cell_rows("demo", failed, ["lc"], self.totals())
        assert [r.tested for r in rows] == [False, True]  # a column that fails was exercised
        cleanup = self.judge(verify, nb, cells, {}, phase="cleanup")
        assert not any(r.tested for r in verify.cell_rows("cleanup", cleanup, ["lc"], {}))

    def test_an_unconfigured_column_is_not_judged(self, verify, nb, cells):
        entry = self.judge(verify, nb, cells, {at(cells, 3, 2): [crashed()]})
        rows = verify.cell_rows("demo", entry, ["rs"], self.totals())
        assert {r.status for r in rows} == {"PASS"}

    def test_an_error_fails_its_column_with_the_cell_in_the_detail_and_all_of_them_in_the_output(
        self, verify, nb, cells
    ):
        outputs = {
            at(cells, 1, 2): [crashed("KeyError: 'x'")],
            at(cells, 1, 9): [crashed("OSError: y")],
        }
        rows = verify.cell_rows(
            "demo", self.judge(verify, nb, cells, outputs), ["lc"], self.totals()
        )
        bad = next(r for r in rows if r.name.endswith("LangCache"))
        assert bad.status == "FAIL" and "KeyError: 'x'" in bad.detail and "(+1 more)" in bad.detail
        assert "OSError: y" in bad.output and bad.output.count("\n") == 1

    def test_the_demo_must_have_written_something_to_each_configured_column(
        self, verify, nb, cells
    ):
        entry = self.judge(verify, nb, cells, {})
        totals = {**self.totals(), "lc": {"recorded": 0, "live": 0, "sample": []}}
        rows = {r.name: r for r in verify.cell_rows("demo", entry, ["rs", "lc"], totals)}
        assert rows["demo cells: 1 LangCache"].status == "FAIL"
        assert "wrote nothing" in rows["demo cells: 1 LangCache"].detail
        assert rows["demo cells: 0 Redis Search"].status == "PASS"

    def test_the_cleanup_export_is_not_asked_to_have_written(self, verify, nb, cells):
        entry = self.judge(verify, nb, cells, {}, phase="cleanup")
        rows = verify.cell_rows("cleanup", entry, ["lc"], {})
        assert {r.status for r in rows} == {"PASS"}

    def test_a_clean_up_cell_that_warns_is_a_warning_and_a_demo_that_warns_is_not(
        self, verify, nb, cells
    ):
        warn = shows(callout_markup(nb, "warn", "Deleted 3 of 4 entries; 1 left"))
        outputs = {cleanup_at(cells, 1): [warn]}
        cleanup = self.judge(verify, nb, cells, outputs, phase="cleanup")
        (lc,) = [
            r for r in verify.cell_rows("cleanup", cleanup, ["lc"], {}) if "LangCache" in r.name
        ]
        assert lc.status == "WARN"
        assert lc.detail.endswith("the Clean up cell warns: Deleted 3 of 4 entries; 1 left")
        demo = self.judge(verify, nb, cells, outputs)
        (lc,) = [
            r
            for r in verify.cell_rows("demo", demo, ["lc"], self.totals())
            if "LangCache" in r.name
        ]
        assert lc.status == "PASS"

    def test_a_standing_note_in_a_column_is_not_a_cleanup_that_could_not_finish(
        self, verify, nb, cells
    ):
        note = shows(callout_markup(nb, "warn", "**Never on a shared cache.** Flush is not used."))
        ok = shows(callout_markup(nb, "ok", "Deleted **3** entries."))
        outputs = {at(cells, 1, 0): [note], at(cells, 1, 5): [note], cleanup_at(cells, 1): [ok]}
        entry = self.judge(verify, nb, cells, outputs, phase="cleanup")
        rows = verify.cell_rows("cleanup", entry, ["lc"], {})
        assert {r.status for r in rows} == {"PASS"}
        assert "2 warn" in rows[1].detail  # counted, as information

    def test_a_missing_banner_fails_the_shared_row(self, verify, nb, cells):
        entry = {"judge": verify.judge_session(make_session(cells), cells, nb.scrub, "demo")}
        rows = verify.cell_rows("demo", entry, [], {})
        assert rows[0].status == "FAIL" and "was not honoured" in rows[0].detail

    def test_a_session_that_does_not_line_up_fails(self, verify, nb, cells):
        entry = {"judge": verify.judge_session(make_session(cells[:-1]), cells, nb.scrub, "demo")}
        rows = verify.cell_rows("demo", entry, ["rs"], self.totals())
        assert any(r.status == "FAIL" and "line up" in r.detail for r in rows)

    def test_no_session_file_is_a_failure(self, verify):
        (row,) = verify.cell_rows("cleanup", {"files": {}}, ["rs"], {})
        assert row.status == "FAIL" and row.name == "cleanup cells"


@pytest.fixture(scope="module")
def cleanup_session(nb_file, tmp_path_factory):
    """The session file of a real `marimo export` with AUTORUN=cleanup and no settings at all.

    Every column shows its standing notes (warn callouts in its title and config cells) and its
    Clean up cell says the column is not configured. Nothing here reaches a service.
    """
    work = tmp_path_factory.mktemp("real_cleanup")
    shutil.copy2(nb_file, work / nb_file.name)
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    env.update(
        PYTHONIOENCODING="utf-8", PYTHONUTF8="1", IRIS_BASICS_RUN="0123abcd",
        IRIS_BASICS_AUTORUN="cleanup", IRIS_BASICS_HEADLESS="1",
    )  # fmt: skip
    done = subprocess.run(
        [sys.executable, "-m", "marimo", "-y", "export", "html", "--no-sandbox",
         nb_file.name, "-o", "out.html", "-f"],
        cwd=work, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    session = work / "__marimo__" / "session" / f"{nb_file.name}.json"
    return json.loads(session.read_text(encoding="utf-8"))


class TestRealCleanupSession:
    """judge_session and cell_rows on what marimo really writes, not on a made-up session."""

    def healthy(self, nb, cells, cleanup_session):
        """The real session, with every Clean up cell showing the report of a finished cleanup."""
        session = json.loads(json.dumps(cleanup_session))
        done = shows(callout_markup(nb, "ok", "Deleted **1** entries."))
        for position, cell in enumerate(cells, start=1):
            if cell.cleanup:
                session["cells"][position]["outputs"] = [done]
        return session

    def test_every_column_has_standing_warn_notes_although_nothing_is_wrong(
        self, verify, nb, cells, cleanup_session
    ):
        session = self.healthy(nb, cells, cleanup_session)
        found = verify.judge_session(session, cells, nb.scrub, "cleanup")
        assert found["mapped"] and found["banner"]
        for key in verify.KEYS:
            group = found["groups"][key]
            assert group["callouts"].get("warn", 0) >= 1, key
            assert group["unfinished"] == [] and group["errors"] == [] and group["unexpected"] == []

    def test_a_finished_cleanup_is_a_pass_in_every_column_whatever_the_notes_say(
        self, verify, nb, cells, cleanup_session
    ):
        session = self.healthy(nb, cells, cleanup_session)
        found = verify.judge_session(session, cells, nb.scrub, "cleanup")
        rows = verify.cell_rows("cleanup", {"judge": found}, verify.KEYS, {})
        assert [(r.name, r.status) for r in rows] == [
            ("cleanup cells: shared", "PASS"), ("cleanup cells: 0 Redis Search", "PASS"),
            ("cleanup cells: 1 LangCache", "PASS"), ("cleanup cells: 2 Context Retriever", "PASS"),
            ("cleanup cells: 3 Agent Memory", "PASS"),
        ]  # fmt: skip

    def test_what_the_clean_up_cell_itself_shows_is_what_counts(
        self, verify, nb, cells, cleanup_session
    ):
        found = verify.judge_session(cleanup_session, cells, nb.scrub, "cleanup")
        for key in verify.KEYS:
            (unfinished,) = found["groups"][key]["unfinished"]
            assert "is not configured" in unfinished, key
        rows = verify.cell_rows("cleanup", {"judge": found}, verify.KEYS, {})
        assert {r.status for r in rows[1:]} == {"WARN"}
        assert all("the Clean up cell warns: " in r.detail for r in rows[1:])

    def test_the_notes_are_not_findings_in_the_numbers(self, verify, nb, cells, cleanup_session):
        standing = verify.warn_texts(cleanup_session)
        assert len(standing) >= 8  # a note or two for each of the four columns
        found = verify.live_numbers(cleanup_session, cells, nb.scrub, standing)
        assert found["warnings"] == {}
        assert verify.live_numbers(cleanup_session, cells, nb.scrub)["warnings"]


class TestAnalyse:
    def test_a_secret_the_scan_cannot_find_when_planted_is_reported_blind(
        self, verify, nb, monkeypatch
    ):
        configure(monkeypatch, lc=LC_ENV)
        found = verify.child_analyse(nb, {"phases": []})
        assert "LANGCACHE_API_KEY" in found["secrets"]["checked"]
        assert found["secrets"]["blind"] == []
        monkeypatch.setattr(verify, "spellings", lambda value: {"not the value": "raw"})
        blind = verify.child_analyse(nb, {"phases": []})["secrets"]["blind"]
        assert "LANGCACHE_API_KEY" in blind
        row = verify.scan_rows(verify.child_analyse(nb, {"phases": []}))
        assert row.status == "FAIL" and "could not find a planted copy" in row.output


class TestScanRows:
    def scanned(self, secrets=(), shapes=(), decoded=True):
        files = {
            "html": {"secrets": list(secrets), "shapes": list(shapes), "decoded": decoded},
            "session": {"secrets": [], "shapes": [], "decoded": True},
        }
        return {
            "secrets": {
                "checked": ["LANGCACHE_API_KEY", "MEMORY_API_KEY"], "too_short": [], "blind": [],
            },
            "phases": {"demo": {"files": files}, "cleanup": {"files": {"log": files["session"]}}},
        }  # fmt: skip

    def test_clean_files_pass_and_the_row_says_what_was_checked(self, verify):
        row = verify.scan_rows(self.scanned())
        assert (
            row.status == "PASS" and "3 files" in row.detail and "LANGCACHE_API_KEY" in row.detail
        )

    def test_a_hit_fails_and_names_the_variable_the_spelling_and_the_file(self, verify):
        row = verify.scan_rows(self.scanned(secrets=[("MEMORY_API_KEY", "json")]))
        assert row.status == "FAIL"
        assert row.output == "MEMORY_API_KEY (json) in the demo html"

    def test_a_credential_shaped_string_fails_and_is_shown_masked(self, verify):
        row = verify.scan_rows(self.scanned(shapes=[("bearer token", "Bearer ****")]))
        assert row.status == "FAIL" and "bearer token Bearer **** in the demo html" in row.output

    def test_a_payload_that_would_not_decode_fails(self, verify):
        row = verify.scan_rows(self.scanned(decoded=False))
        assert row.status == "FAIL" and "could not be decoded" in row.output

    def test_a_scan_that_cannot_find_a_planted_secret_fails(self, verify):
        found = self.scanned()
        found["secrets"]["blind"] = ["MEMORY_API_KEY"]
        assert verify.scan_rows(found).status == "FAIL"

    def test_a_settings_file_with_no_secret_in_it_fails(self, verify):
        found = self.scanned()
        found["secrets"]["checked"] = []
        assert verify.scan_rows(found).status == "FAIL"

    def test_a_secret_too_short_to_check_is_a_warning(self, verify):
        found = self.scanned()
        found["secrets"]["too_short"] = ["REDIS_PASSWORD"]
        row = verify.scan_rows(found)
        assert row.status == "WARN" and "too short to check: REDIS_PASSWORD" in row.detail


# ---- the whole run, with every process faked ---------------------------------------------------
SECRETS = [LC_ENV["LANGCACHE_API_KEY"], AM_KEY, ADMIN_KEY, "fake-pw-9f3a"]
PRESENT = [
    "REDIS_URL", "LANGCACHE_HOST", "LANGCACHE_CACHE_ID", "LANGCACHE_API_KEY", "CTX_ADMIN_KEY",
    "MEMORY_API_BASE_URL", "MEMORY_STORE_ID", "MEMORY_API_KEY",
]  # fmt: skip
ZERO = {
    "rs": {"indexes": 0, "dropped": 0, "gone": 0, "keys": 0, "removed": 0},
    "lc": {"entries": 0, "deleted": 0, "expired": 0},
    "cr": {"surfaces": 0, "deleted": 0, "keys": 0, "removed": 0, "indexes": 0, "dropped": 0,
           "gone": 0, "left": [], "stopped": ""},
    "am": {"ran": True, "sessions": 0, "sessions_left": 0, "memories": 0, "extracted": 0, "left": 0,
           "refused": 0},
}  # fmt: skip
WRITES = {
    "rs": ("key", "k1"),
    "lc": ("entry", "e1"),
    "cr": ("surface", "s1"),
    "am": ("session", "ib-x-s1"),
}
Call = namedtuple("Call", "kind name env cwd timeout spec files cmd")


def clean_footprint(columns=("rs", "lc", "cr", "am"), **found):
    """What child_footprint returns when the services hold nothing, with some places replaced."""
    empty = {
        "rs": {
            "redis_keys": {"count": 0, "sample": []},
            "indexes": {"count": 0, "sample": []},
            "indexes_new": {"count": 0, "sample": []},
        },
        "am": {"memory": {"count": 0, "sample": [], "strangers": 0, "sessions": 0}},
        "cr": {"surfaces": {"count": 0, "sample": []}},
    }
    return {**{k: v for key in columns for k, v in empty.get(key, {}).items()}, **found}


def forbid_reading(monkeypatch, path):
    """Fail the test if this process opens `path`: the parent must never read the settings."""
    real = builtins.open

    def guarded(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).resolve() == path.resolve():
            raise AssertionError("verify.py opened the settings file")
        return real(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    monkeypatch.setattr(io, "open", guarded)


class World:
    """A live run whose processes are fakes. It records every call and writes what a real export
    would: a session file and an export html, a ledger, and a log.

    Set the attributes before calling run(). results maps a child action to the dict it returns
    (or a function of its spec); the analyse child is the real thing, run in this process.
    """

    def __init__(self, verify, nb, cells, path, summary):
        self.verify, self.nb, self.cells, self.path, self.summary = verify, nb, cells, path, summary
        self.calls, self.sleeps = [], []
        self.present = list(PRESENT)
        self.export_rc, self.export_error = {}, {}
        self.outputs, self.leak, self.logs = {}, {}, {}
        self.results, self.child_exit = {}, {}
        self.left = set()  # columns the cleanup export leaves live in the ledger
        self.silent = set()  # columns the demo export writes nothing for
        self.banner = True

    # ---- the fakes ----
    def configured(self):
        present = set(self.present)
        return [
            key for key, (_, groups) in self.verify.COLUMNS.items()
            if all(any(name in present for name in group) for group in groups)
        ]  # fmt: skip

    def run_process(self, cmd, cwd, env, timeout):
        cwd = Path(cwd)
        if "export" in cmd:
            phase = env["IRIS_BASICS_AUTORUN"]
            files = sorted(p.name for p in cwd.iterdir())
            self.calls.append(Call("export", phase, env, cwd, timeout, None, files, cmd))
            return self.export(phase, cwd, env)
        action = cmd[-1]
        spec = json.loads((cwd / self.verify.CHILD_SPEC).read_text(encoding="utf-8"))
        self.calls.append(Call("child", action, env, cwd, timeout, spec, None, cmd))
        if action in self.child_exit:
            return self.child_exit[action]
        result = self.child(action, spec, env)
        return 0, "noise the parent must ignore\n" + self.verify.RESULT + json.dumps(result) + "\n"

    def ledger(self, env, columns, state):
        """Append a ledger line for each column's write, as the notebook would."""
        ledger = Path(env["IRIS_BASICS_LEDGER_DIR"]) / f"{env['IRIS_BASICS_RUN']}.jsonl"
        ledger.parent.mkdir(exist_ok=True)
        with ledger.open("a", encoding="utf-8") as fh:
            for key in columns:
                kind, ident = WRITES[key]
                line = {"column": key, "kind": kind, "ident": ident, "state": state}
                fh.write(json.dumps(line) + "\n")

    def export(self, phase, cwd, env):
        wrote = [key for key in self.configured() if key not in self.silent]
        if phase == "demo":
            self.ledger(env, wrote, "created")
        else:
            self.ledger(env, [k for k in wrote if k in self.left], "created")
            self.ledger(env, [k for k in wrote if k not in self.left], "deleted")
        if phase in self.export_error:
            raise self.export_error[phase]
        rc = self.export_rc.get(phase, 0)
        log = self.logs.get(phase, "marimo finished\n")
        if rc != 0:
            return rc, log
        outputs = {
            1: [shows(banner_markup(self.nb, phase))] if self.banner else [],
            **self.outputs.get(phase, {}),
        }
        session = make_session(self.cells, outputs)
        target = cwd / self.verify.SESSION
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(session), encoding="utf-8")
        payload = json.dumps({"filename": "iris_basics.py", "session": session})
        script = f'<script data-marimo="true">Object.freeze({payload})</script>'
        page = f"<html>{script}{self.leak.get(phase, '')}</html>"
        (cwd / "out.html").write_text(page, encoding="utf-8")
        return 0, log

    def child(self, action, spec, env):
        if action in self.results:
            found = self.results[action]
            return found(spec) if callable(found) else found
        columns = spec.get("columns", [])
        if action == "probe":
            return {"present": self.present, "run": env["IRIS_BASICS_RUN"], "pinned": True,
                    "ledger": env["IRIS_BASICS_LEDGER_DIR"], "python": "3.13.9",
                    "indexes": ["preexisting"]}  # fmt: skip
        if action == "sweep":
            self.ledger(env, columns, "deleted")
            return {key: {"done": ZERO[key], "report": ""} for key in columns}
        if action == "sentinel":
            return {"entry": "e-sentinel", "ttl_ms": 5000, "seen_alive": True, "gone": True,
                    "waited": 6, "final": "expired"}  # fmt: skip
        if action == "footprint":
            return clean_footprint(columns)
        analysed = self.verify.child_analyse(self.nb, spec)
        return self.verify.map_strings(self.nb.scrub, analysed)

    # ---- driving it ----
    def run(self, *args):
        return self.verify.main(
            ["--live", "--env-file", str(self.path), "--summary", str(self.summary), *args]
        )

    def actions(self):
        return [(c.kind, c.name) for c in self.calls]

    def written(self):
        return json.loads(self.summary.read_text(encoding="utf-8"))


@pytest.fixture
def world(verify, nb, cells, tmp_path, monkeypatch):
    configure(monkeypatch, lc=LC_ENV, am=AM_ENV, cr=CR_ENV)
    settings = tmp_path / "live.env"
    settings.write_text(
        "".join(f"{k}={v}\n" for env in (LC_ENV, AM_ENV, CR_ENV) for k, v in env.items()),
        encoding="utf-8",
    )
    forbid_reading(monkeypatch, settings)
    fake = World(verify, nb, cells, settings, tmp_path / "summary.json")
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    monkeypatch.setattr(verify, "UV", "uv")
    monkeypatch.setattr(verify, "run_process", fake.run_process)
    monkeypatch.setattr(verify, "sh", lambda cmd, cwd, env, timeout: (0, "3.13.9\n"))
    monkeypatch.setattr(verify.time, "sleep", fake.sleeps.append)
    fake.temp = tmp_path / "tmp"
    return fake


def table(capsys):
    """{step: (result, detail)} from the printed table, plus the whole text."""
    out = capsys.readouterr().out
    rows = {}
    for line in out.splitlines():
        parts = re.split(r"\s{2,}", line.strip(), maxsplit=3)
        if len(parts) >= 3 and parts[1] in ("PASS", "FAIL", "SKIP", "WARN"):
            rows[parts[0]] = (parts[1], parts[3] if len(parts) > 3 else "")
    return rows, out


def statuses(rows):
    return {name: status for name, (status, _) in rows.items()}


class TestLiveArguments:
    @pytest.mark.parametrize(
        "args",
        [
            ["--live"],
            ["--live", "--preflight", "--env-file", "p"],
            ["--extraction-wait", "5"],
            ["--summary", "s.json"],
            ["--live", "--env-file", "p", "--extraction-wait", "-1"],
            ["--live", "--env-file", "p", "--extraction-wait", "soon"],
            ["--live", "--env-file", "p", "--notebook", "iris_basics.py"],
            ["--preflight", "--env-file", "p", "--extraction-wait", "5"],
        ],
    )
    def test_a_bad_command_line_is_refused_before_anything_starts(
        self, verify, monkeypatch, capsys, args
    ):
        monkeypatch.setattr(verify, "UV", "uv")
        monkeypatch.setattr(verify, "run_process", lambda *a: pytest.fail("a process was started"))
        with pytest.raises(SystemExit) as refused:
            verify.main(args)
        assert refused.value.code == 2 and capsys.readouterr().err

    def test_a_missing_settings_file_is_a_failed_step_and_starts_nothing(
        self, verify, monkeypatch, capsys, tmp_path
    ):
        monkeypatch.setattr(verify, "UV", "uv")
        monkeypatch.setattr(verify, "run_process", lambda *a: pytest.fail("a process was started"))
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        code = verify.main(
            [
                "--live",
                "--env-file",
                str(tmp_path / "nope.env"),
                "--summary",
                str(tmp_path / "s.json"),
            ]
        )
        rows, out = table(capsys)
        assert code == 1 and rows["settings file exists"] == ("FAIL", "nope.env")
        assert "live: FAIL (0 of 4 columns exercised, 1 failed of 2 steps)" in out
        assert [p.name for p in tmp_path.iterdir()] == ["s.json"]  # no temp folder was made


class TestLiveEnvironment:
    def test_children_get_the_pinned_run_the_settings_path_the_ledger_and_headless(
        self, verify, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("REDIS_URL", "redis://:leftover@shell")
        monkeypatch.setenv("IRIS_BASICS_AUTORUN", "cleanup")
        env = verify.live_env(tmp_path / "p.env", "0123abcd", tmp_path / "ledger")
        assert env["IRIS_BASICS_ENV_FILE"] == str(tmp_path / "p.env")
        assert env["IRIS_BASICS_RUN"] == "0123abcd" and env["IRIS_BASICS_HEADLESS"] == "1"
        assert env["IRIS_BASICS_LEDGER_DIR"] == str(tmp_path / "ledger")
        assert "REDIS_URL" not in env and "IRIS_BASICS_AUTORUN" not in env
        assert env["PYTHONIOENCODING"] == "utf-8"

    def test_a_run_gets_a_fresh_eight_digit_hex_id_each_time(self, verify, tmp_path):
        ids = {verify.Live(tmp_path / "p.env").run_id for _ in range(20)}
        assert len(ids) == 20 and all(re.fullmatch(r"[0-9a-f]{8}", i) for i in ids)


class TestLiveRun:
    def test_a_clean_run_passes_and_does_its_steps_in_order(self, world, capsys):
        assert world.run() == 0
        rows, out = table(capsys)
        assert world.actions() == [
            ("child", "probe"), ("export", "demo"), ("export", "cleanup"), ("child", "analyse"),
            ("child", "sentinel"), ("child", "sweep"), ("child", "footprint"),
        ]  # fmt: skip
        assert "live: PASS (4 of 4 columns exercised, 0 failed of" in out
        assert {s for s in statuses(rows).values()} == {"PASS"}
        assert world.sleeps == [60] * 11

    def test_every_process_has_the_same_run_the_absolute_settings_path_and_no_secret(self, world):
        world.run()
        (run,) = {c.env["IRIS_BASICS_RUN"] for c in world.calls}
        assert re.fullmatch(r"[0-9a-f]{8}", run)
        (_,) = {c.env["IRIS_BASICS_LEDGER_DIR"] for c in world.calls}
        for call in world.calls:
            assert call.env["IRIS_BASICS_HEADLESS"] == "1"
            assert call.env["IRIS_BASICS_ENV_FILE"] == str(world.path.resolve())
            assert not any(s in value for s in SECRETS for value in call.env.values())
            assert not any(
                name.startswith(("REDIS_", "LANGCACHE_", "CTX_", "MEMORY_")) for name in call.env
            )

    def test_a_relative_settings_path_reaches_the_children_as_an_absolute_one(self, world):
        world.path = Path(world.path.name)  # the children run in other folders: it must not be
        world.run()
        expected = str(Path(world.path.name).resolve())
        assert {c.env["IRIS_BASICS_ENV_FILE"] for c in world.calls} == {expected}
        assert Path(expected).is_absolute() and Path(expected).exists()

    def test_autorun_is_demo_then_cleanup_on_the_exports_and_absent_elsewhere(self, world):
        world.run()
        exports = [c for c in world.calls if c.kind == "export"]
        assert [(c.name, c.env.get("IRIS_BASICS_AUTORUN")) for c in exports] == [
            ("demo", "demo"), ("cleanup", "cleanup"),
        ]  # fmt: skip
        assert all("IRIS_BASICS_AUTORUN" not in c.env for c in world.calls if c.kind == "child")

    def test_each_export_runs_marimo_in_its_own_empty_folder_with_only_the_notebook(self, world):
        world.run()
        exports = [c for c in world.calls if c.kind == "export"]
        assert len({c.cwd for c in exports}) == 2
        assert all(c.files == ["iris_basics.py"] for c in exports)

    def test_the_exports_are_marimos_own_headless_html_export_of_the_notebook(self, world):
        world.run()
        exports = [c for c in world.calls if c.kind == "export"]
        assert len(exports) == 2
        for call in exports:
            assert call.cmd[call.cmd.index("python") :] == [
                "python", "-m", "marimo", "-y", "export", "html", "--no-sandbox", "iris_basics.py",
                "-o", "out.html", "-f",
            ]  # fmt: skip

    def test_every_process_runs_in_the_notebooks_dependency_set_on_the_pinned_python(
        self, world, verify
    ):
        world.run()
        assert len(world.calls) == 7
        for call in world.calls:
            found = call.cmd[: call.cmd.index("--with-requirements") + 2]
            assert found == [
                "uv", "run", "--no-project", "--python", "3.13", "--with-requirements",
                "iris_basics.py",
            ]  # fmt: skip
        for call in (c for c in world.calls if c.kind == "child"):
            assert call.cmd[call.cmd.index("python") :] == [
                "python", "-c", verify.CHILD_CODE, call.name,
            ]  # fmt: skip
        assert verify.uv_command("python")[1:5] == ["run", "--no-project", "--python", "3.13"]

    def test_the_children_run_in_a_folder_with_the_notebook_and_this_script(self, world):
        world.run()
        (folder,) = {c.cwd for c in world.calls if c.kind == "child"}
        assert folder.name == "child" and folder not in {
            c.cwd for c in world.calls if c.kind == "export"
        }

    def test_the_settings_file_is_never_opened_here_and_its_values_never_printed(
        self, world, capsys
    ):
        world.run()  # the fixture makes opening the file an error
        out = capsys.readouterr().out
        assert not any(s in out for s in SECRETS)
        assert not any(s in world.summary.read_text(encoding="utf-8") for s in SECRETS)

    def test_two_runs_do_not_share_a_run_id(self, world):
        world.run()
        first = world.calls[0].env["IRIS_BASICS_RUN"]
        world.calls.clear()
        world.run()
        assert world.calls[0].env["IRIS_BASICS_RUN"] != first

    def test_the_summary_is_written_without_secrets_and_says_what_happened(self, world, capsys):
        world.run()
        summary = world.written()
        assert summary["success"] is True and summary["configured"] == ["rs", "lc", "cr", "am"]
        assert (
            summary["env_file"] == "live.env"
            and summary["run"] == world.calls[0].env["IRIS_BASICS_RUN"]
        )
        assert {"probe", "exports", "analysis", "footprint", "sentinel"} <= set(summary["data"])
        assert any(r["step"] == "secret scan" and r["result"] == "PASS" for r in summary["rows"])
        assert "indexes" not in summary["data"]["probe"]  # other apps' index names stay out of it
        out = capsys.readouterr().out
        assert str(world.summary) in out
        assert f"the summary will be written to {world.summary}" in out

    def test_the_summary_holds_the_verdict_and_the_steps_that_are_printed(self, world, capsys):
        world.left = {"lc"}
        assert world.run() == 1
        summary = world.written()
        out = capsys.readouterr().out
        assert summary["success"] is False and summary["verdict"] in out.splitlines()
        assert f"of {len(summary['rows'])} steps" in summary["verdict"]
        assert summary["rows"][-1]["step"] == "summary written"

    def test_a_summary_that_cannot_be_written_is_a_failed_step_and_fails_the_run(
        self, world, tmp_path, capsys
    ):
        (tmp_path / "blocker").write_text("a file, not a folder", encoding="utf-8")
        world.summary = tmp_path / "blocker" / "summary.json"
        assert world.run() == 1
        assert table(capsys)[0]["summary written"][0] == "FAIL"

    def test_the_indexes_from_before_the_exports_go_to_the_footprint_check(self, world):
        world.run()
        footprint = next(c for c in world.calls if c.name == "footprint")
        assert footprint.spec == {
            "columns": ["rs", "lc", "cr", "am"], "indexes_before": ["preexisting"],
        }  # fmt: skip

    def test_a_clean_run_removes_its_temp_folder_and_keep_keeps_it(self, world, capsys):
        world.run()
        assert list(world.temp.iterdir()) == []
        world.run("--keep")
        (kept,) = world.temp.iterdir()
        assert {p.name for p in kept.iterdir()} >= {"ledger", "logs", "child", "export-demo"}
        assert f"kept {kept}" in capsys.readouterr().out

    def test_a_warning_the_demo_shows_is_kept_for_the_summary_but_does_not_fail_the_run(
        self, world, nb
    ):
        text = "**The service does not behave like a hard cut.** A memory came back higher."
        world.outputs["demo"] = {at(world.cells, 3, 8): [shows(callout_markup(nb, "warn", text))]}
        assert world.run() == 0
        summary = world.written()
        (warning,) = summary["columns"]["am"]["warnings"]
        assert warning.startswith("warn: The service does not behave like a hard cut.")
        assert summary["columns"]["lc"]["warnings"] == []
        assert "1 warn" in summary["columns"]["am"]["demo"]["detail"]

    def test_a_note_every_export_shows_is_not_a_warning_of_the_demo(self, world, nb, capsys):
        note = shows(callout_markup(nb, "warn", "**Never on a shared service.** Flush is unused."))
        found = shows(
            callout_markup(nb, "warn", "**The service does not behave like a hard cut.**")
        )
        world.outputs["demo"] = {at(world.cells, 3, 1): [note], at(world.cells, 3, 8): [found]}
        world.outputs["cleanup"] = {at(world.cells, 3, 1): [note]}
        assert world.run() == 0
        (warning,) = world.written()["columns"]["am"]["warnings"]
        assert "hard cut" in warning
        rows, out = table(capsys)
        assert rows["cleanup cells: 3 Agent Memory"][0] == "PASS" and "warned" not in out

    def test_a_clean_up_cell_that_warns_is_a_warned_run_not_a_failed_one(self, world, nb, capsys):
        left = shows(callout_markup(nb, "warn", "**1** are still listed: press Clean up again"))
        world.outputs["cleanup"] = {cleanup_at(world.cells, 3): [left]}
        assert world.run() == 0
        rows, out = table(capsys)
        status, detail = rows["cleanup cells: 3 Agent Memory"]
        assert status == "WARN" and "1 are still listed" in detail and "1 warned" in out

    def test_the_live_numbers_come_from_the_demo_session(self, world, nb):
        hit = shows(
            callout_markup(nb, "ok", "**HIT** · similarity **0.912** · matched *x* · entry `e`")
        )
        world.outputs["demo"] = {at(world.cells, 1, 6): [hit]}
        world.run()
        assert world.written()["data"]["analysis"]["numbers"]["similarities"] == [0.912]


class TestLiveSettings:
    def test_a_column_whose_settings_are_absent_is_skipped_and_never_touched(self, world, capsys):
        world.present = [n for n in PRESENT if not n.startswith("MEMORY_")]
        assert world.run() == 0
        rows, out = table(capsys)
        assert (
            rows["column 3 Agent Memory"][0] == "SKIP"
            and "MEMORY_API_BASE_URL" in rows["column 3 Agent Memory"][1]
        )
        assert ("child", "sweep") not in world.actions()  # nothing to wait for or sweep
        footprint = next(c for c in world.calls if c.name == "footprint")
        assert footprint.spec["columns"] == ["rs", "lc", "cr"] and world.sleeps == []
        assert rows["wait for Agent Memory extraction"][0] == "SKIP"
        assert "live: PASS (3 of 4 columns exercised" in out

    def test_a_run_that_exercised_one_column_of_four_says_so_and_skips_what_it_did_not_do(
        self, world, capsys
    ):
        world.present = [n for n in PRESENT if n.startswith("MEMORY_")]
        assert world.run("--extraction-wait", "0") == 0
        rows, out = table(capsys)
        assert "live: PASS (1 of 4 columns exercised, 0 failed of" in out
        for label in ("0 Redis Search", "1 LangCache", "2 Context Retriever"):
            assert rows[f"column {label}"][0] == "SKIP"
        assert rows["LangCache TTL sentinel (5 s)"] == ("SKIP", "LangCache is not configured")
        assert rows["demo cells: 3 Agent Memory"][0] == "PASS"

    def test_agent_memory_that_is_not_configured_skips_the_wait_and_says_why(self, world, capsys):
        world.present = [n for n in PRESENT if not n.startswith("MEMORY_")]
        world.run()
        rows, _ = table(capsys)
        assert rows["wait for Agent Memory extraction"] == (
            "SKIP",
            "Agent Memory is not configured",
        )

    def test_an_error_in_a_column_that_is_not_configured_is_not_judged(self, world, capsys):
        world.present = [n for n in PRESENT if not n.startswith("MEMORY_")]
        world.outputs["demo"] = {at(world.cells, 3, 4): [crashed("KeyError: 'x'")]}
        assert world.run() == 0

    def test_nothing_configured_is_a_failure_and_no_export_is_run(self, world, capsys):
        world.present = []
        assert world.run() == 1
        rows, _ = table(capsys)
        assert rows["read setting names, pin RUN"][0] == "FAIL"
        assert [c.kind for c in world.calls] == ["child"]
        assert all(
            rows[f"column {label}"][0] == "SKIP" for label, _ in world.verify.COLUMNS.values()
        )

    def test_a_notebook_that_did_not_take_the_pinned_run_stops_the_run(self, world, capsys):
        world.results["probe"] = lambda spec: {
            "present": PRESENT,
            "run": "ffffffff",
            "pinned": False,
            "ledger": "x",
        }
        assert world.run() == 1
        rows, _ = table(capsys)
        assert "did not take the pinned RUN" in rows["read setting names, pin RUN"][1]
        assert [c.kind for c in world.calls] == ["child"]

    def test_a_notebook_that_keeps_its_ledger_elsewhere_stops_the_run(self, world, capsys):
        world.results["probe"] = lambda spec: {
            "present": PRESENT, "run": world.calls[0].env["IRIS_BASICS_RUN"], "pinned": True,
            "ledger": "/elsewhere",
        }  # fmt: skip
        assert world.run() == 1
        assert "not using the run's ledger" in table(capsys)[0]["read setting names, pin RUN"][1]


class TestLiveJudging:
    def test_an_error_in_a_configured_column_fails_the_run_with_the_cell_and_a_scrubbed_snippet(
        self, world, capsys
    ):
        key = LC_ENV["LANGCACHE_API_KEY"]
        position = at(world.cells, 1, 4)
        world.outputs["demo"] = {position: [crashed(f"ServiceError: lc HTTP 401 Bearer {key}")]}
        assert world.run() == 1
        rows, out = table(capsys)
        status, detail = rows["demo cells: 1 LangCache"]
        assert status == "FAIL" and f"line {world.cells[position - 1].line}" in detail
        assert key not in out and "****" in out
        assert rows["demo cells: 0 Redis Search"][0] == "PASS"

    def test_an_error_callout_fails_the_run(self, world, capsys):
        red = shows(
            callout_markup(world.nb, "error", "**am** HTTP 424: the store could not do the work")
        )
        world.outputs["cleanup"] = {at(world.cells, 3, 6): [red]}
        assert world.run() == 1
        status, detail = table(capsys)[0]["cleanup cells: 3 Agent Memory"]
        assert status == "FAIL" and "424" in detail

    def test_an_autorun_that_was_not_honoured_fails_because_nothing_ran(self, world, capsys):
        world.banner = False
        assert world.run() == 1
        assert "was not honoured" in table(capsys)[0]["demo cells: shared"][1]

    def test_a_column_the_demo_wrote_nothing_to_fails(self, world, capsys):
        world.silent = {"lc"}
        assert world.run() == 1
        assert "wrote nothing" in table(capsys)[0]["demo cells: 1 LangCache"][1]


class TestLiveSweeps:
    def test_a_failed_demo_export_still_gets_cleaned_up_and_the_cleanup_export_still_runs(
        self, world, capsys
    ):
        world.export_rc["demo"] = 1
        world.left = {"rs", "lc"}
        world.logs["demo"] = "Traceback ...\nboom\n"
        assert world.run() == 1
        rows, out = table(capsys)
        assert rows["export html, AUTORUN=demo"] == ("FAIL", "exit 1")
        assert world.actions()[:5] == [
            ("child", "probe"), ("export", "demo"), ("export", "cleanup"), ("child", "sweep"),
            ("child", "analyse"),
        ]  # fmt: skip
        sweep = world.calls[3]
        assert sweep.spec["columns"] == ["rs", "lc"]
        assert rows["safety-net sweep: 0 Redis Search"][0] == "PASS"
        assert "Traceback ..." in out  # the failing export's scrubbed tail is shown

    def test_a_crash_of_the_export_step_itself_still_sweeps(self, world, capsys):
        world.export_error["demo"] = OSError("disk full")
        world.left = {"cr"}
        assert world.run() == 1
        rows, _ = table(capsys)
        assert rows["export html, AUTORUN=demo"] == ("FAIL", "OSError: disk full")
        assert ("child", "sweep") in world.actions()
        assert next(c for c in world.calls if c.name == "sweep").spec["columns"] == ["cr"]

    def test_a_timed_out_export_is_a_failure_and_the_sweep_still_runs(self, world, capsys):
        world.export_rc["demo"] = None
        world.left = {"lc"}
        assert world.run() == 1
        assert "timed out after 1800 s" in table(capsys)[0]["export html, AUTORUN=demo"][1]
        assert ("child", "sweep") in world.actions()

    def test_ctrl_c_during_an_export_sweeps_reports_and_keeps_the_ledger(self, world, capsys):
        world.export_error["demo"] = KeyboardInterrupt()
        assert world.run() == 1
        rows, out = table(capsys)
        assert rows["run"] == ("FAIL", "interrupted") and ("child", "sweep") in world.actions()
        (kept,) = world.temp.iterdir()
        assert [p.name for p in kept.iterdir()] == ["ledger"] and "kept the ledger" in out

    def test_a_cleanup_that_left_things_live_fails_although_the_sweep_puts_it_right(
        self, world, capsys
    ):
        world.left = {"rs", "lc", "am"}
        assert world.run() == 1
        rows, out = table(capsys)
        assert rows["cleanup export left live: 0 Redis Search"] == (
            "FAIL",
            "1 still in the ledger after it, e.g. key k1",
        )
        assert rows["cleanup export left live: 1 LangCache"][0] == "FAIL"
        assert rows["cleanup export left live: 3 Agent Memory"][0] == "WARN"  # sessions expire
        assert not any(name.startswith("cleanup export left live: 2") for name in rows)
        # The net caught it, and that is all its PASS says.
        assert rows["safety-net sweep: 1 LangCache"][0] == "PASS"
        assert rows["footprint: ledger"] == ("PASS", "nothing live")
        assert "live: FAIL (4 of 4 columns exercised, 2 failed" in out

    def test_agent_memory_sessions_left_are_only_a_warning_but_a_memory_left_fails(
        self, world, capsys, monkeypatch
    ):
        world.left = {"am"}
        assert world.run() == 0
        rows, out = table(capsys)
        assert rows["cleanup export left live: 3 Agent Memory"][0] == "WARN"
        assert "1 warned" in out
        monkeypatch.setitem(WRITES, "am", ("memory", "m1"))
        assert world.run() == 1
        rows, _ = table(capsys)
        status, detail = rows["cleanup export left live: 3 Agent Memory"]
        assert status == "FAIL" and detail.endswith("e.g. memory m1")

    def test_a_cleanup_export_that_failed_has_its_own_row_and_no_audit_row(self, world, capsys):
        world.export_rc["cleanup"] = 1
        world.left = {"lc"}
        assert world.run() == 1
        rows, _ = table(capsys)
        assert rows["export html, AUTORUN=cleanup"] == ("FAIL", "exit 1")
        assert not any(name.startswith("cleanup export left") for name in rows)
        assert rows["safety-net sweep: 1 LangCache"][0] == "PASS"

    def test_only_columns_the_ledger_still_shows_live_are_swept(self, world):
        world.left = {"lc", "am"}
        world.run()
        sweeps = [c.spec["columns"] for c in world.calls if c.name == "sweep"]
        assert sweeps == [["lc", "am"], ["am"]]  # the finally, then after the extraction wait

    def test_nothing_live_means_the_finally_sweeps_nothing(self, world, capsys):
        world.run()
        rows, _ = table(capsys)
        assert rows["safety-net sweep"] == ("PASS", "nothing live")
        assert not any(name.startswith("cleanup export left") for name in rows)

    def test_a_sweep_that_fails_is_a_failed_row_naming_the_column_and_the_error(
        self, world, capsys
    ):
        world.left = {"lc"}
        world.results["sweep"] = lambda spec: {
            k: {"error": "ServiceError: lc HTTP 401"} for k in spec["columns"]
        }
        assert world.run() == 1
        assert table(capsys)[0]["safety-net sweep: 1 LangCache"] == (
            "FAIL",
            "ServiceError: lc HTTP 401",
        )

    def test_a_child_that_dies_is_a_failed_sweep_with_its_scrubbed_message(self, world, capsys):
        world.left = {"lc"}
        world.child_exit["sweep"] = (3, f"{world.verify.FAILURE}ValueError: bad\n")
        assert world.run() == 1
        assert (
            "sweep exited 3. ValueError: bad"
            in table(capsys)[0]["safety-net sweep: 1 LangCache"][1]
        )

    def test_the_ledger_is_kept_when_the_footprint_is_not_empty(self, world, capsys):
        keys = {"count": 3, "sample": ["irisnb_x_rs:doc:1"]}
        world.results["footprint"] = clean_footprint(redis_keys=keys)
        assert world.run() == 1
        (kept,) = world.temp.iterdir()
        assert [p.name for p in kept.iterdir()] == ["ledger"]
        assert "3 keys left" in next(
            v[1] for k, v in table(capsys)[0].items() if k.startswith("footprint: keys")
        )


class TestLiveExtraction:
    def test_it_waits_the_default_660_seconds_in_minutes_then_sweeps_agent_memory_again(
        self, world
    ):
        world.run()
        assert sum(world.sleeps) == 660 and max(world.sleeps) <= 60
        names = world.actions()
        assert names.index(("child", "sweep")) > names.index(("child", "sentinel"))
        assert names.index(("child", "footprint")) > names.index(("child", "sweep"))
        assert next(c for c in world.calls if c.name == "sweep").spec == {"columns": ["am"]}

    def test_the_wait_can_be_shortened(self, world):
        world.run("--extraction-wait", "100")
        assert sum(world.sleeps) == 100

    def test_zero_skips_the_wait_with_a_warning_and_still_sweeps_once(self, world, capsys):
        assert world.run("--extraction-wait", "0") == 0  # a warning does not fail the run
        rows, out = table(capsys)
        status, detail = rows["wait for Agent Memory extraction"]
        assert status == "WARN" and "--extraction-wait 0" in detail
        assert world.sleeps == [] and ("child", "sweep") in world.actions()
        assert "1 warned" in out

    def test_a_run_that_wrote_nothing_to_agent_memory_does_not_wait(self, world, capsys):
        world.silent = {"am"}
        assert world.run() == 1  # the demo cells row fails: it wrote nothing
        assert world.sleeps == []
        assert table(capsys)[0]["wait for Agent Memory extraction"][0] == "SKIP"

    def test_ctrl_c_during_the_wait_still_sweeps_agent_memory_before_stopping(
        self, world, capsys, monkeypatch
    ):
        def interrupt(seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(world.verify.time, "sleep", interrupt)
        assert world.run() == 1
        assert table(capsys)[0]["run"] == ("FAIL", "interrupted")
        assert world.actions()[-1] == ("child", "sweep")

    @pytest.mark.parametrize("action", ["analyse", "sentinel"])
    def test_ctrl_c_before_the_wait_still_sweeps_agent_memory_and_does_not_wait(
        self, world, capsys, action
    ):
        def interrupt(spec):
            raise KeyboardInterrupt

        world.results[action] = interrupt
        assert world.run() == 1
        assert table(capsys)[0]["run"] == ("FAIL", "interrupted")
        assert world.actions()[-1] == ("child", "sweep") and world.calls[-1].spec == {
            "columns": ["am"]
        }
        assert world.sleeps == [] and ("child", "footprint") not in world.actions()

    def test_sigterm_is_an_interrupt_too_and_the_handler_is_put_back(self, world, capsys):
        before = signal.getsignal(signal.SIGTERM)

        def terminate(spec):
            # Raising a signal nobody handles would end pytest itself: look first.
            assert callable(signal.getsignal(signal.SIGTERM))
            signal.raise_signal(signal.SIGTERM)

        world.results["sentinel"] = terminate
        assert world.run() == 1
        assert table(capsys)[0]["run"] == ("FAIL", "interrupted")
        assert world.actions()[-1] == ("child", "sweep")
        assert signal.getsignal(signal.SIGTERM) == before

    def test_the_hand_sweep_is_told_at_the_start_and_at_the_end_with_the_extraction_wait(
        self, world, capsys
    ):
        world.export_error["demo"] = KeyboardInterrupt()
        world.run()
        out = capsys.readouterr().out
        assert out.count("IRIS_BASICS_AUTORUN=cleanup") == 2
        assert out.index("if this run is killed, sweep by hand") < out.index("kept the ledger")
        assert out.count("once more about 11 minutes after the run ended") == 2


class TestLiveSentinelAndFootprint:
    def test_a_sentinel_that_survives_fails_the_run(self, world, capsys):
        world.results["sentinel"] = {
            "entry": "e",
            "ttl_ms": 5000,
            "seen_alive": True,
            "gone": False,
            "waited": 60,
            "final": "deleted",
        }
        assert world.run() == 1
        assert "not honoured" in table(capsys)[0]["LangCache TTL sentinel (5 s)"][1]

    def test_no_sentinel_without_langcache(self, world, capsys):
        world.present = [n for n in PRESENT if not n.startswith("LANGCACHE_")]
        world.run()
        assert ("child", "sentinel") not in world.actions()
        assert table(capsys)[0]["LangCache TTL sentinel (5 s)"][0] == "SKIP"

    def test_agent_memory_left_behind_fails_the_run(self, world, capsys):
        left = {"count": 2, "sample": ["ib-x-m1"], "strangers": 0, "sessions": 0}
        world.results["footprint"] = clean_footprint(memory=left)
        assert world.run() == 1
        assert "2 memories left" in next(
            v[1] for k, v in table(capsys)[0].items() if k.startswith("footprint: Agent")
        )

    def test_a_footprint_child_that_dies_fails_every_place_it_could_not_check(self, world, capsys):
        world.child_exit["footprint"] = (3, f"{world.verify.FAILURE}ConnectError: refused\n")
        assert world.run() == 1
        rows, _ = table(capsys)
        assert all(
            status == "FAIL"
            for name, (status, _) in rows.items()
            if name.startswith("footprint: ") and "ledger" not in name
        )

    def test_the_footprint_asks_only_about_the_configured_columns(self, world):
        world.present = [n for n in PRESENT if n.startswith(("LANGCACHE_", "REDIS_"))]
        world.run()
        assert next(c for c in world.calls if c.name == "footprint").spec == {
            "columns": ["rs", "lc"], "indexes_before": ["preexisting"],
        }  # fmt: skip

    def test_new_indexes_outside_the_prefix_are_a_warning_not_a_failure(self, world, capsys):
        new = {"count": 1, "sample": ["someone-elses-idx"]}
        world.results["footprint"] = clean_footprint(indexes_new=new)
        assert world.run() == 0
        rows, out = table(capsys)
        (status, detail) = next(v for k, v in rows.items() if k.startswith("footprint: new"))
        assert status == "WARN" and "someone-elses-idx" in detail and "1 warned" in out


class TestLiveSecrets:
    def test_a_secret_in_the_export_fails_the_run_naming_the_variable_and_never_printing_it(
        self, world, capsys
    ):
        key = LC_ENV["LANGCACHE_API_KEY"]
        world.leak["demo"] = f"<p>oops {quote(key, safe='')}</p>"
        assert world.run() == 1
        rows, out = table(capsys)
        assert rows["secret scan"][0] == "FAIL" and "LANGCACHE_API_KEY" in out
        assert key not in out and quote(key, safe="") not in out
        assert key not in world.summary.read_text(encoding="utf-8")

    def test_a_secret_in_a_failing_exports_log_is_neither_shown_nor_excerpted(self, world, capsys):
        world.export_rc["demo"] = 1
        world.logs["demo"] = f"Traceback\nValueError: token {AM_KEY} refused\n"
        assert world.run() == 1
        rows, out = table(capsys)
        assert AM_KEY not in out and "MEMORY_API_KEY" in out
        assert "Traceback" not in out and rows["secret scan"][0] == "FAIL"

    def test_the_excerpt_is_masked_before_its_lines_are_cut_short(self, world, capsys, nb):
        minted = "m1nted-key-" + "k" * 30  # learned by the notebook, never in the settings file
        nb.register_secret(minted)
        world.export_rc["demo"] = 1
        world.logs["demo"] = "a" * 190 + minted + " refused\n"
        assert world.run() == 1
        out = capsys.readouterr().out
        assert "a" * 190 + "****" in out and "m1nted" not in out

    def test_the_tail_of_a_failing_log_that_holds_no_secret_is_shown(self, world, capsys):
        world.export_rc["cleanup"] = 2
        world.logs["cleanup"] = "\n".join(f"line {n}" for n in range(100))
        assert world.run() == 1
        out = capsys.readouterr().out
        assert "line 99" in out and "line 60" not in out  # the last 30 lines

    def test_an_unmasked_credential_shape_in_the_export_fails_it(self, world, capsys):
        world.outputs["demo"] = {at(world.cells, 2, 8): [shows("<p>cs_agent_1234abcd5678</p>")]}
        assert world.run() == 1
        _, out = table(capsys)
        assert "cs_agent_****" in out and "1234abcd5678" not in out


class TestLiveSafety:
    def test_a_dotenv_that_marimo_would_load_is_found_by_its_pyproject(self, verify, tmp_path):
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        (tmp_path / ".env").write_text("REDIS_URL=redis://elsewhere\n", encoding="utf-8")
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        assert verify.stray_dotenv(deep) == tmp_path / ".env"

    def test_without_a_pyproject_only_the_notebooks_own_folder_counts(self, verify, tmp_path):
        (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
        deep = tmp_path / "a"
        deep.mkdir()
        assert verify.stray_dotenv(deep) is None  # marimo does not look above the notebook
        (deep / ".env").write_text("X=1\n", encoding="utf-8")
        assert verify.stray_dotenv(deep) == deep / ".env"

    def test_a_run_whose_temp_folder_would_load_another_dotenv_stops_before_any_process(
        self, world, capsys, tmp_path
    ):
        (world.temp / "pyproject.toml").write_text("", encoding="utf-8")
        (world.temp / ".env").write_text("REDIS_URL=redis://elsewhere\n", encoding="utf-8")
        assert world.run() == 1
        rows, _ = table(capsys)
        assert rows["no other .env can leak in"][0] == "FAIL"
        assert str(world.temp / ".env") in rows["no other .env can leak in"][1]
        assert world.calls == []
        assert [p.name for p in world.temp.iterdir() if p.is_dir()] == []

    def test_the_summary_has_a_section_per_column(self, world):
        world.present = [n for n in PRESENT if not n.startswith("MEMORY_")]
        world.run()
        columns = world.written()["columns"]
        assert list(columns) == ["rs", "lc", "cr", "am"]
        assert columns["lc"]["configured"] and columns["lc"]["demo"]["result"] == "PASS"
        assert columns["lc"]["cleanup"]["result"] == "PASS"
        assert columns["lc"]["ledger"] == {"recorded": 1, "live": 0, "sample": []}
        assert columns["am"]["configured"] is False and columns["am"]["missing"]
        assert "demo" not in columns["am"] and columns["am"]["ledger"] is None

    def test_a_ledger_that_cannot_be_read_means_every_configured_column_is_swept(
        self, world, capsys, monkeypatch
    ):
        monkeypatch.setattr(
            world.verify, "read_ledger",
            lambda *a: (_ for _ in ()).throw(PermissionError("locked")),
        )  # fmt: skip
        world.run()
        assert next(c for c in world.calls if c.name == "sweep").spec["columns"] == [
            "rs", "lc", "cr", "am",
        ]  # fmt: skip

    def test_a_bug_in_a_step_is_a_failed_row_after_the_sweep_not_a_crash(
        self, world, capsys, monkeypatch
    ):
        stage = world.verify.stage_live

        def broken(tmp, name, with_verify=False):
            if name == "export-cleanup":
                raise OSError("disk full")
            return stage(tmp, name, with_verify)

        monkeypatch.setattr(world.verify, "stage_live", broken)
        world.left = {"lc"}
        assert world.run() == 1
        rows, _ = table(capsys)
        assert rows["run"] == ("FAIL", "OSError: disk full")
        assert ("child", "sweep") in world.actions()  # the finally still ran
        (kept,) = world.temp.iterdir()
        assert [p.name for p in kept.iterdir()] == ["ledger"]

    def test_the_summary_folder_is_made_if_it_is_missing(self, world, tmp_path):
        world.summary = tmp_path / "not" / "yet" / "there.json"
        assert world.run() == 0 and world.summary.exists()


class TestInterruptOnTermination:
    def test_sigterm_and_ctrl_break_raise_keyboard_interrupt_and_the_handlers_come_back(
        self, verify
    ):
        names = [n for n in ("SIGTERM", "SIGBREAK") if hasattr(signal, n)]
        assert "SIGTERM" in names
        before = {n: signal.getsignal(getattr(signal, n)) for n in names}
        for name in names:
            with verify.interrupt_on_termination(), pytest.raises(KeyboardInterrupt):
                assert callable(signal.getsignal(getattr(signal, name)))  # else pytest would die
                signal.raise_signal(getattr(signal, name))
        assert {n: signal.getsignal(getattr(signal, n)) for n in names} == before


class TestLiveChildren:
    def test_a_child_that_runs_out_of_time_is_a_timed_out_row_not_a_hang(self, world, capsys):
        world.child_exit["probe"] = (None, "")
        assert world.run() == 1
        assert "timed out after 900 s" in table(capsys)[0]["read setting names, pin RUN"][1]
        assert [c.name for c in world.calls] == ["probe"]

    def test_a_child_that_exits_without_a_result_is_a_failure_with_its_scrubbed_line_only(
        self, world, capsys
    ):
        world.child_exit["probe"] = (
            1,
            "Traceback (most recent call last):\nboom fake-lc-key-9f3a\n",
        )
        assert world.run() == 1
        rows, out = table(capsys)
        assert rows["read setting names, pin RUN"] == ("FAIL", "ChildFailed: probe exited 1.")
        assert "boom" not in out and "fake-lc-key-9f3a" not in out

    def test_the_children_get_generous_but_bounded_time(self, world):
        world.run()
        timeouts = {(c.kind, c.name): c.timeout for c in world.calls}
        assert timeouts[("export", "demo")] == 1800 and timeouts[("export", "cleanup")] == 1200
        assert timeouts[("child", "sentinel")] == 300 and timeouts[("child", "probe")] == 900
