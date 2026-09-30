# Research: marimo

> Generated 2026-09-29 by the iris-basics-research workflow (researcher + adversarial verifier).
> **The Verifier corrections section OVERRIDES the report body where they conflict.** Treat `verified: false` facts as hypotheses.

## Summary

Latest marimo on PyPI today is 0.25.0 (uploaded 2026-09-23); 0.24.2 does exist (2026-09-11), not yanked, so the old notebook's pin is valid. The columns API is confirmed and works in 0.25.0: marimo.App(width="columns"), @app.cell(column=N, hide_code=True), `with app.setup:` coexists, and cells in any column can depend on names from any other column, because columns are layout only. I confirmed this in a real browser through Playwright on Edge. `marimo run` renders columns side by side, one fixed-width column each (740px by default). `marimo edit` also shows them side by side, but wider, with drag-resizable columns. There are no column names. Width can only be changed in run mode with a custom CSS file that overrides --content-width. TWO 0.25.0 FINDINGS MATTER MOST. (1) mo.ui.chat is broken in 0.25.0: sending any message fails in the browser with "Invalid input: expected nonoptional, received undefined at messages[0].metadata". I reproduced it in run and edit mode, and it works in 0.24.2. Root cause: the frontend upgraded zod from 4.3.6 to 4.5.4, and 4.5.4 rejects a missing key for `z.any().nullable()`. I reproduced that in node. I found no upstream issue, and main still has zod ^4.5.4. So either pin marimo==0.24.2 (launcher AND header) or use the browser-verified DIY chat below. (2) .env autoloading changed: 0.24.2 loads .env only when a pyproject.toml is found walking upward from the notebook; 0.25.0 also loads the .env next to the notebook when there is no pyproject.toml. That makes it work with --sandbox and needs no setup. I verified both versions empirically. Other checks in 0.25.0: prefilling mo.ui.text(kind="password") from os.environ is safe, because marimo masks it and the secret never reaches the DOM or the exported HTML. Top-level await works, and asyncio.run() inside a cell fails. `marimo export html` exits 1 when any cell errors while still writing the file, and `--no-sandbox` is required when the notebook has a PEP 723 header, unless you pass the global -y. Exit codes for `marimo check` are documented in the cheat-sheet. All the proof commands passed on Windows 11 with uv 0.11.25. Everything was run in temp directories, and nothing under projects/demo was touched.

## Verifier corrections (authoritative)

- **Claim:** DIY chat: "form.value is None until submit AND again after clear_on_submit" (so the history-append cell handles repeated submits correctly).
  - **Correction:** clear_on_submit only clears the DOM inputs. FormPlugin's onSubmit calls setValue(newValue) and then clear() → clearInputs(formDiv); it never sets the value back to None. After a submit, form.value stays at the submitted text (browser: 'DBG value=\'first q\' n=2', with the input box empty). Submitting the SAME text again does not re-run dependent cells: the third send of 'second q' left n=4. So the report's DIY chat silently drops a repeated identical question, which is exactly the 'ask twice to show a LangCache hit' demo. Verified alternative: `q = mo.ui.text(...)` plus `send = mo.ui.run_button(label='Send')`, and in the consumer `if send.value: history.append(q.value)`. Three identical sends gave n=1, 2, 3.
  - **Source:** frontend/src/plugins/impl/FormPlugin.tsx at tag 0.25.0, lines 109-138; Playwright runs of %TEMP%\mofc\diy\diy.py (port 27127) and %TEMP%\mofc\diy2\d2.py (port 27128) on marimo 0.25.0
- **Claim:** "Do NOT put `mo.stop(not ask.value)` before the append when using clear_on_submit=True. The clear resets the value to None and re-runs the cell, which wipes the output."
  - **Correction:** The value is not reset to None, so this failure mode doesn't happen. A cell doing `mo.stop(not ask.value, ...)` followed by `hist.append(ask.value)` worked across turns (n=1 for 'one', n=2 for 'two'), and the output was not wiped. The real limitation is the dropped repeat described above, in both variants.
  - **Source:** Playwright run of %TEMP%\mofc\diy2\d2.py cells DBGB, marimo 0.25.0
- **Claim:** File skeleton: the title cell `mo.md("# 1. Redis Search ...")` under @app.cell(column=0, hide_code=True). The report says `check`, `export html` and the browser all passed on 0.25.0 with this shape, and recommends `check --strict` in CI.
  - **Correction:** A one-line, pure-markdown cell like that triggers `warning[markdown-indentation]` (code MF007, severity formatting, fixable). Plain `marimo check` exits 0, but `marimo check --strict` exits 1, on both 0.24.2 and 0.25.0. The triple-quoted form `mo.md(r"""\n    # 1. Redis Search\n    ...\n    """)` and f-string cells pass --strict. `marimo check --fix` rewrites it.
  - **Source:** `marimo check --strict md_a.py` exit 1 vs md_b.py exit 0 (both versions); `marimo check --format json md_a.py` shows code MF007; files in %TEMP%\mofc\chk
- **Claim:** GitHub issue #9885 "says columns are lost when switching to app mode" (framed as a possible bug conflicting with the run-mode test).
  - **Correction:** #9885 ('Display width columns for notebooks to render also as single column notebooks when switching to app mode', open since 2026-06-15) is a feature request. The author WANTS app mode to optionally show a columns notebook as one full-width column ('i'd like to see it as if it was displaying in the full width mode and then the columns are lost'). It is not a bug report. It agrees with the observation that `marimo run` renders columns side by side and has no toggle to collapse them.
  - **Source:** https://api.github.com/repos/marimo-team/marimo/issues/9885 (body)
- **Claim:** `mo.notebook_dir() -> pathlib.Path (verified)`, used unguarded in the fallback loader as `_load_env(mo.notebook_dir() / ".env")`.
  - **Correction:** The signature is `notebook_dir() -> pathlib.Path | None`. The docstring says it returns None 'if the notebook's directory cannot be determined' (for example, an unsaved notebook with no filename), and it returns Path.cwd() outside a kernel context. Inside a saved notebook it returns the directory. Guard it, e.g. `(mo.notebook_dir() or Path.cwd()) / '.env'`.
  - **Source:** marimo/_runtime/runtime.py:349-379 (0.25.0); inspect.signature output 'notebook_dir () -> pathlib.Path | None'

Verifier overall reliability: **high**

### Verifier: additional facts

- The kernel's working directory is the process launch cwd, not the notebook's directory, on both 0.24.2 and 0.25.0. Launched from ...\envt\other, os.getcwd() inside the notebook was ...\envt\other. Resolve data files (e.g. domain JSON copied from redis-iris-demos) with mo.notebook_dir(), not relative paths.  _(src: `marimo export html --no-sandbox --no-include-code ../nb/n.py` run from %TEMP%\mofc\envt\other; output 'CWD=C:\Users\NickCooper\AppData\Local\Temp\mofc\envt\other')_
- Token defaults differ. `marimo run` defaults to --no-token and `marimo edit` defaults to --token. Both default to --host 127.0.0.1.  _(src: `marimo run --help` ('[default: no-token]') and `marimo edit --help` ('[default: token]'), marimo 0.25.0)_
- python-dotenv is not a marimo dependency; the base 0.25.0 venv lacks it. marimo then uses its fallback parser `_parse_dotenv_bindings`, which splits on the first '=' and strips surrounding quotes. It does NOT strip inline '# comments' or an 'export ' prefix, so `KEY=abc # note` loads as 'abc # note'. Add python-dotenv to the PEP 723 deps, or keep .env lines plain. This comes from reading the source; the inline-comment case was not run.  _(src: marimo/_secrets/load_dotenv.py (_read_dotenv_bindings uses DependencyManager.dotenv.has(), else _parse_dotenv_bindings); `uv pip list` on the v25 venv shows no python-dotenv)_
- The PEP 723 sandbox prompt only appears when uv is on PATH. Without uv, `marimo export html pep.py` prints 'Consider installing uv so that marimo can create a temporary venv...' and runs in the current environment with exit 0.  _(src: %TEMP%\mofc\chk: `../v25/Scripts/marimo.exe export html pep.py` with ~/.local/bin not on PATH → exit 0; with it on PATH → 'Aborted!' exit 1)_
- Sparse and negative column indices are resolved in the source. Run mode groups cells into a Map keyed by column index and sorts by key, so 0 and 2 render as two adjacent columns and file order across columns doesn't matter. Edit mode allocates max(column)+1 columns, so a gap leaves an empty column. A negative index falls into the previous column.  _(src: vertical-layout.tsx groupCellsByColumn (lines 45-61) and frontend/src/utils/id-tree.tsx:659-682 at tag 0.25.0)_
- zod 4.5.4 accepts `metadata: undefined` when the key is present and rejects only a MISSING key. The frontend message builder omits metadata, so no Python-side workaround exists for mo.ui.chat on 0.25.0. Pin 0.24.2 or avoid mo.ui.chat.  _(src: node repro %TEMP%\mofc\zod\z4.5.4\t.mjs: missing key → false, explicit undefined → true)_
- mo.state re-runs cells that READ/reference the getter variable, not only cells that call it. The setter's own cell is excluded unless allow_self_loops=True. The signature is `state(value, allow_self_loops=False)`.  _(src: marimo/_runtime/state.py:189-211 docstring (0.25.0))_
- Don't switch the notebook width away from 'columns' in the editor and save. Issue #3543 (column metadata lost when switching views) is open and both fix PRs (#10797, #10991) are unmerged, so the column=N assignments would be lost.  _(src: https://api.github.com/repos/marimo-team/marimo/issues/3543 and /pulls/10797, /pulls/10991 (state open, merged_at null))_
- In the redis-iris-basics project, no pyproject.toml exists in redis-iris-basics, projects/demo or projects, and no .env exists there yet. With a 0.24.2 pin, a customer's .env next to the notebook will NOT autoload unless a pyproject.toml is added or the fallback loader is used. With 0.25.0 it autoloads.  _(src: `ls -a` of C:\Users\NickCooper\projects\demo\redis-iris-basics, ...\demo, ...\projects (names only, no .env read))_
- mo.ui.chat also has `on_message`, `allow_attachments: bool | list[str]`, `max_height` and `disabled`. mo.download has a `disabled` parameter, and mo.status.progress_bar has completion_title/completion_subtitle/show_rate/show_eta/remove_on_exit/disabled. The report's cheat-sheet elides some of these.  _(src: inspect.signature dump %TEMP%\mofc\sig_v25.txt)_
- Password masking also holds when the password text is nested in mo.ui.array or mo.ui.dictionary. The initial values serialize as {"0":""} and {"k":""}, and the exported HTML had 0 hits for the fake secret.  _(src: %TEMP%\mofc\leak\l.py exported with `marimo export html --no-sandbox`; grep -o fakevalue123 l.html → 0)_

### Verifier: unverifiable claims

- Edit-mode visuals I did not test in a browser: the setup cell at the top of column 0 labelled 'setup cell', hide_code honored in edit mode, and column 1 overflowing an 1800px viewport in edit mode.
- The transient 'Failed to fetch dynamically imported module .../edit-page-*.js' on the first sandboxed edit load, the '~40s cold start' and 'Installed 42 packages in 8s' (marimo[lsp]) for `uvx marimo@0.25.0 edit --sandbox`. I did not run edit --sandbox.
- The CellNotInitializedError seen in `marimo edit` with an existing __marimo__ session cache until run-all.
- The report's exact run-mode figures (769px pitch, x=101/870/1638/2407, 769px→489px with 460px). I measured 772px pitch (x=112/884/1656/2428) with the defaults and a 412px pitch with 380px. The mechanism is the same.
- mo.ui.chat failing in EDIT mode on 0.25.0. Only run mode was reproduced here; edit mode is very likely the same because the same ChatPlugin.tsx and zod bundle validate before any RPC (inferred).
- `marimo export script` not wrapping mo.stop: my flat-script export had no mo.stop cell to check.
- `marimo check` flagging cycle-dependencies with exit 1, not flagging unused imports, and 'check --fix did not rewrite column' were not re-tested.
- Cancel semantics (CancelledError for async, GeneratorExit for sync generators). Only the task.cancel() path in chat.py _cancel_prompt was seen.
- A table row click in the browser producing [{'name':..,'score':..}]. Only the headless and initial_selection values were verified.
- Windows PowerShell redirected-stdout banner behaviour, and anything on macOS or Linux.
- Why 0.24.2 ignores PEP 723 runtime.dotenv even when cwd equals the notebook dir. One hypothesis is secret masking or blanking of runtime.dotenv on read (0.25.0 adds _drop_hollow_dotenv, manager.py:668-678). Not proven.

## Docs URLs

- [marimo 0.25.0 release notes (GitHub)](https://github.com/marimo-team/marimo/releases/tag/0.25.0) — verified: True
- [marimo on PyPI](https://pypi.org/project/marimo/) — verified: True
- [mo.ui.chat API](https://docs.marimo.io/api/inputs/chat/) — verified: True
- [Runtime configuration (dotenv)](https://docs.marimo.io/guides/configuration/runtime_configuration/) — verified: True
- [Sandboxes guide](https://docs.marimo.io/guides/package_management/sandboxes/) — verified: True
- [Inlining dependencies (PEP 723)](https://docs.marimo.io/guides/package_management/inlining_dependencies/) — verified: True
- [Editor overview (multi-column layout)](https://docs.marimo.io/guides/editor_features/overview/) — verified: True
- [Run notebooks as apps](https://docs.marimo.io/guides/apps/) — verified: True
- [Exporting notebooks](https://docs.marimo.io/guides/exporting/) — verified: True
- [CLI reference](https://docs.marimo.io/cli/) — verified: True
- [Lint rules (marimo check)](https://docs.marimo.io/guides/lint_rules/) — verified: True
- [mo.ui.table API](https://docs.marimo.io/api/inputs/table/) — verified: True
- [mo.ui.run_button API](https://docs.marimo.io/api/inputs/run_button/) — verified: True
- [mo.ui.form API](https://docs.marimo.io/api/inputs/form/) — verified: True
- [Layout functions (hstack/vstack/tabs/accordion)](https://docs.marimo.io/api/layouts/) — verified: True
- [Status (spinner/progress bar)](https://docs.marimo.io/api/status/) — verified: True
- [Control flow (mo.stop)](https://docs.marimo.io/api/control_flow/) — verified: True
- [State (mo.state)](https://docs.marimo.io/api/state/) — verified: True
- [Issue #9885: columns lost in app mode](https://github.com/marimo-team/marimo/issues/9885) — verified: True
- [Issue #3543: column metadata lost switching views](https://github.com/marimo-team/marimo/issues/3543) — verified: True
- [Frontend source: vertical-layout.tsx (run-mode columns) at 0.25.0](https://github.com/marimo-team/marimo/blob/0.25.0/frontend/src/components/editor/renderers/vertical-layout/vertical-layout.tsx) — verified: True
- [Frontend source: ChatPlugin.tsx (messageSchema with metadata) at 0.25.0](https://github.com/marimo-team/marimo/blob/0.25.0/frontend/src/plugins/impl/chat/ChatPlugin.tsx) — verified: True

## Secrets


## API surface

## 0. VERSION / PINNING (verified)
- PyPI latest: **marimo 0.25.0** (2026-09-23), requires-python >=3.10. **0.24.2 exists** (2026-09-11). Neither is yanked.
- Recommended header pin. The launcher and the header must agree:
  - **If you need mo.ui.chat: pin 0.24.2.** Header `"marimo==0.24.2"`, launcher `uvx marimo@0.24.2 edit --sandbox notebook.py`.
  - **If you don't need mo.ui.chat: pin 0.25.0.** Header `"marimo==0.25.0"`, launcher `uvx marimo@0.25.0 edit --sandbox notebook.py`.
- The launcher's marimo version wins inside the sandbox:
  - Launcher 0.25.0 with header `marimo==0.24.2` ran marimo **0.25.0**.
  - Launcher 0.24.2 with header `marimo>=0.24.2` ran marimo **0.25.0**, because the `>=` resolved to the newest.
  - Never use `>=` for marimo.
- If you pin 0.24.2, .env autoload needs a `pyproject.toml` next to the notebook. Otherwise use the 0.24.2-safe loader in section 5.

## 1. FILE SKELETON (columns + setup + PEP 723). `check`, `export html` and the browser all passed on 0.25.0 with this shape.
```python
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "marimo==0.25.0",
#     # add: "redis==...", "httpx", "altair" (altair NOT in base marimo), "pandas"/"polars" only if you pass DataFrames
# ]
# ///
import marimo

__generated_with = "0.25.0"          # keep it: `marimo check` warns [general-formatting] if missing, and --strict then exits 1
app = marimo.App(width="columns", app_title="Redis Iris basics", css_file="custom.css")  # css_file optional, relative to the .py

with app.setup:                       # or: with app.setup(hide_code=True):
    import os
    import marimo as mo
    # names here are visible in every cell WITHOUT being cell parameters; do NOT redefine them in any cell (MB002 multiple-definitions)


@app.cell(column=0, hide_code=True)   # first cell of column 0
def _():
    mo.md("# 1. Redis Search ...")
    return


@app.cell(column=1)
def _(some_var_from_col0):            # cross-column dependency is fine (the graph ignores columns)
    ...
    return


if __name__ == "__main__":
    app.run()
```
Column rules (all verified in the browser):
- `column=N` is an int. Every cell may carry it, or only the first cell of a column.
- A cell with no `column` inherits the previous cell's column in FILE ORDER. This is how marimo itself serializes. `check --fix` did not rewrite it.
- Keep each column's cells contiguous in the file.
- Use contiguous indices 0..N-1. Sparse indices are untested.
- The setup cell has `column: null`. In edit mode it shows at the top of column 0, labelled "setup cell". In run mode it is not shown.
- `hide_code=True` is honored in edit mode. `marimo run` sends no code to the browser by default anyway (`--include-code` opts in).
- Other cell kwargs: `disabled=True`, and NEW in 0.25.0 `expand_output=True`.

RENDERING
- `marimo run`, `width="columns"`: a flex row, `gap-8` (32px). Each column is `w-(--content-width)`, **740px by default** (`--content-width-medium` is 1110px). Four columns need about 3100px and overflow a 1920px screen, so scroll horizontally, zoom out, or override the width.
- Override the width with `App(css_file="custom.css")` containing `:root { --content-width: 460px; }`. Column spacing changed 769px to 489px in my test.
- `marimo edit`: columns side by side, each at least 500px, drag-resizable. Width is stored in browser localStorage key `marimo:notebook-col-sizes`, NOT in the .py file. The column header has move-left, move-right, drag, "Delete column" and "Add column". **There is no column naming.**
- Width literals: `Literal["normal","compact","medium","full","columns"]` (normal == compact).
- Use `# Heading` markdown cells as visual column titles.

## 2. UI SIGNATURES, from inspect.signature on marimo 0.25.0. Only accordion differs from 0.24.2 (`expanded=` is new).
```
mo.ui.chat(model, *, prompts=None, on_message=None, show_configuration_controls=False, config={'max_tokens':4096,'temperature':0.5,'top_p':1,'top_k':40,'frequency_penalty':0,'presence_penalty':0}, allow_attachments=False, max_height=None, disabled=False)
mo.ui.table(data, pagination=None, selection='multi'|'single'|'single-cell'|'multi-cell'|None, initial_selection=None, page_size=None, show_column_summaries=None, show_data_types=True, format_mapping=None, freeze_columns_left=None, freeze_columns_right=None, text_justify_columns=None, wrapped_columns=None, column_widths=None, hidden_columns=None, visible_columns=None, header_tooltip=None, show_download=True, max_columns='inherit', *, show_search=True, label='', on_change=None, style_cell=None, hover_template=None, max_height=None)
mo.ui.slider(start=None, stop=None, step=None, value=None, debounce=False, disabled=False, orientation='horizontal', show_value=False, include_input=False, steps=None, *, label='', on_change=None, full_width=False)
mo.ui.range_slider(start,stop,step,value=[lo,hi], ..., show_value=False, label='')
mo.ui.dropdown(options: Sequence|dict[label->value], value=None, allow_select_none=None, searchable=False, *, label='', on_change=None, full_width=False, disabled=False)   # `value` is the option LABEL for dict options
mo.ui.radio(options, value=None, inline=False, *, label='', on_change=None, disabled=False)
mo.ui.switch(value=False, *, label='', disabled=False, on_change=None)
mo.ui.checkbox / mo.ui.multiselect(options, value=None, *, label, max_selections=None) / mo.ui.number(start,stop,step,value,...) / mo.ui.button(on_click=None, value=None, kind='neutral', ..., label='click here')
mo.ui.run_button(kind='neutral'|'success'|'warn'|'danger', disabled=False, tooltip=None, *, label='click to run', on_change=None, full_width=False, keyboard_shortcut=None)   # .value is False until clicked, True for the run that click triggers
mo.ui.text(value='', placeholder='', kind='text'|'password'|'email'|'url', max_length=None, disabled=False, debounce=True, *, label='', on_change=None, full_width=False)
mo.ui.text_area(value='', placeholder='', max_length=None, disabled=False, debounce=True, rows=None, *, label='', on_change=None, full_width=False)
mo.ui.array(elements: Sequence[UIElement]);  mo.ui.dictionary(elements: Mapping[str, UIElement])   # .value is list / dict of child values
<element>.form(label='', *, bordered=True, loading=False, submit_button_label='Submit', submit_button_tooltip=None, submit_button_disabled=False, clear_on_submit=False, show_clear_button=False, clear_button_label='Clear', clear_button_tooltip=None, validate=None, on_change=None)   # form.value is None until submitted
mo.ui.form(element, *, same kwargs)
mo.md("... {a} ... {b}").batch(a=ui1, b=ui2).form()    # one form, several inputs, value = {'a':..,'b':..} or None
mo.ui.tabs(tabs: Mapping[str, object], value=None, lazy=False, *, orientation='horizontal', label='')   # .value is the selected tab NAME
mo.accordion(items: Mapping[str, object], multiple=False, lazy=False, *, expanded: bool | Sequence[str] = False)   # `expanded` NEW in 0.25.0
mo.ui.altair_chart(chart, chart_selection=True, legend_selection=True, *, label='', on_change=None)   # REQUIRES altair (not in base marimo)
mo.stat(value, label=None, caption=None, direction='increase'|'decrease'|None, bordered=False, target_direction='increase', slot=None) -> Html
mo.callout(value, kind='neutral'|'warn'|'success'|'info'|'danger', title=None)   # kind literals inferred from CalloutKind; 'info', 'warn', 'neutral', 'success' used or checked
mo.hstack(items, *, justify='space-between', align=None, wrap=False, gap=0.5, widths='equal'|list|None)
mo.vstack(items, *, align=None, justify='start', gap=0.5, heights=None)
with mo.status.spinner(title=None, subtitle=None, remove_on_exit=True): ...
mo.status.progress_bar(collection=None, *, title=None, subtitle=None, total=None, ...)
mo.md(text) -> Html;  mo.stop(predicate: bool, output=None);  mo.json(data); mo.tree(items); mo.lazy(element); mo.download(data, filename=None, mimetype=None, label='Download')
mo.state(initial) -> (getter, setter);   get(); set(v) or set(lambda old: new). Cells that CALL getter() re-run when setter is called. A cell that calls both is not re-triggered by itself (allow_self_loops=False).
mo.output.append/replace/clear(value)   # stream several outputs from one cell
mo.notebook_dir() -> pathlib.Path (verified)
```
Observed values, before any user interaction:
- `mo.ui.table(list_of_dicts, selection='single')` gives `.value == []`. After a row click it is `[{'name':..,'score':..}]`. With `selection=None`, `.value` is `None`. `initial_selection=[1]` gives that row.
- `mo.ui.table` with a list of dicts works with NO pandas or polars installed. The base marimo env has only narwhals, no pandas, polars, altair or numpy.
- `mo.ui.array` gives `[3, 1]`. `mo.ui.dictionary` gives `{'k': 5, 't': 'x'}`. `text.form()` and `.batch().form()` give `None`.

## 3. mo.ui.chat CONTRACT (source: marimo/_plugins/ui/_impl/chat/chat.py lines 470-545, and _ai/_types.py)
- `model` is called as `model(messages, config)`. If the callable has EXACTLY ONE parameter, it is called as `model(messages)`.
- `messages` is `list[ChatMessage]`, where ChatMessage is a `msgspec.Struct` with `role: 'user'|'assistant'|'system'`, `content: Any` (`messages[-1].content` is the last user text), `id`, `parts`, `attachments`, `metadata`.
- `config` is a `ChatModelConfig` dataclass. Use attribute access: `config.temperature`, `config.max_tokens`, `top_p`, `top_k`, `frequency_penalty`, `presence_penalty`. `config.temperature` in a 0.24.2 run-mode chat returned a reply.
- Allowed model shapes: sync function, `async def` (awaited), sync generator, async generator.
  - Yield DELTA strings. marimo accumulates them.
  - A non-str return value is rendered with `as_html(...)`, so you can return a mo.* element, a chart or a table.
  - `async def` and async generators are the right choice for httpx.AsyncClient or async SDKs.
- A SYNC model runs directly on the kernel event loop (chat.py:526-528), so a blocking redis or requests call blocks that loop while it runs. Inferred from source, not measured. Prefer async.
- Cancel/Stop: async code gets `CancelledError`; sync generators get `GeneratorExit`.
- **0.25.0 BUG**: sending a message shows a red error box, "Invalid input: expected nonoptional, received undefined at messages[0].metadata", and the model is never called. Run and edit mode both fail; 0.24.2 works. Cause: frontend `messageSchema.metadata = z.any().nullable()` in ChatPlugin.tsx, combined with zod 4.5.4. Reproduced in node: zod 4.3.6 accepts a missing `metadata` key, 4.5.4 rejects it with the same message.

### DIY CHAT (browser-verified on 0.25.0: multi-turn, input clears, history persists)
```python
@app.cell(column=3)
def _():
    ask = mo.ui.text(placeholder="Ask something...", full_width=True).form(
        submit_button_label="Send", clear_on_submit=True, bordered=False)
    ask
    return (ask,)

@app.cell(column=3)
def _():
    history: list[tuple[str, str]] = []      # this cell never re-runs, so the list persists
    return (history,)

@app.cell(column=3)
def _(ask, history):
    if ask.value:                            # form.value is None until submit AND again after clear_on_submit
        history.append(("user", ask.value))
        history.append(("assistant", f"echo: {ask.value}"))   # replace with the real call
    mo.stop(not history, mo.md("_Ask a question above._"))
    mo.vstack([mo.callout(mo.md(t), kind="neutral" if r == "user" else "success") for r, t in history])
    return
```
Do NOT put `mo.stop(not ask.value)` before the append when using clear_on_submit=True. The clear resets the value to None and re-runs the cell, which wipes the output.

## 4. ASYNC (verified 0.24.2 and 0.25.0)
- Any cell may `await` at top level: `await asyncio.sleep(...)`, `async with httpx.AsyncClient() as c: r = await c.get(...)`, `await asyncio.gather(...)`.
- Do NOT call `asyncio.run(...)` in a cell. It raises `RuntimeError: asyncio.run() cannot be called from a running event loop`. Use `await`.
- Only `app.run()` differs between versions. Called from `__main__`, it runs async cells fine in both. NEW in 0.25.0 (#10505): `app.run()` also works when called inside an already-running loop. In 0.24.2 that raises the same RuntimeError.
- There is no `app.run_async` attribute in either version.

## 5. SECRETS / .env
Behaviour by version, all tested with a fake .env in a temp dir and exported through `marimo export html`:

| Setup | 0.24.2 | 0.25.0 |
|---|---|---|
| .env next to the notebook, no pyproject.toml, `--no-sandbox` | NOT loaded | loaded |
| same, with `--sandbox` (header pinned to the same version) | NOT loaded | loaded |
| pyproject.toml beside the notebook, `--sandbox` | loaded | loaded |
| notebook launched from a different cwd | n/a | still loaded (path is notebook-relative) |

- Default is `runtime.dotenv = [".env"]`. `dotenv = []` disables it.
- Override in `[tool.marimo.runtime]` in pyproject.toml.
- In 0.25.0, script-metadata `dotenv = [".env"]` (PEP 723) also worked from another cwd.
- In 0.24.2, `[tool.marimo.runtime] dotenv = [".env"]` in PEP 723 metadata did NOT load the file in my export test (cause unknown), so do not rely on it there.
- The file is loaded into `os.environ` at kernel start. `.env` values do not override variables already set in the process (standard python-dotenv semantics, not separately tested).

**0.24.2-safe fallback, works on both versions.** Put it in `with app.setup:` after the imports:
```python
    def _load_env(p):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    _load_env(mo.notebook_dir() / ".env")
```
`mo.notebook_dir()` and `__file__` both work (tested 0.24.2).

**Config cell pattern (verified in the browser on 0.25.0).** Password prefill from the environment is masked: the field shows the placeholder "••••••••", the secret never appears in the DOM, the exported HTML or the session-cache JSON (all grep-checked), and `.value` returns the real value until the user types. It also survives a form submit (browser-verified).
```python
@app.cell(column=0)
def _():
    cfg_form = (
        mo.md("""
        **Configuration** - get your key at [Redis Cloud console](https://cloud.redis.io)

        {url}

        {ns}
        """)
        .batch(
            url=mo.ui.text(value=os.environ.get("REDIS_URL", ""), kind="password", label="REDIS_URL", full_width=True),
            ns=mo.ui.text(value="demo", label="namespace"),
        )
        .form(submit_button_label="Save config", bordered=True)
    )
    cfg_form
    return (cfg_form,)

@app.cell(column=0)
def _(cfg_form):
    mo.stop(cfg_form.value is None, mo.callout("Fill in the form and press Save config.", kind="warn"))
    cfg = cfg_form.value          # {'url': '<real value, even if not retyped>', 'ns': 'demo'}
    return (cfg,)
```
Do not use `mo.stop(...)` on `cfg_form.value is None` unless you want the form step; drop `.form()` for a live, ungated input.

## 6. HEADLESS VERIFICATION (best to worst)
1. **`marimo export html` is the end-to-end proof.**
   - `uvx marimo@0.25.0 export html --no-sandbox --no-include-code notebook.py -o out.html -f`
   - It executes every cell. Exit code is **1 with "Export was successful, but some cells failed to execute."** if any cell raised, and the HTML is still written.
   - `mo.stop()` output is not a failure.
   - Non-interactive with a PEP 723 header it prompts "Run in a sandboxed venv...? [Y/n]" and prints `Aborted!` (exit 1). Fix it with **`--no-sandbox`**, or **`--sandbox`**, or the global `-y` placed BEFORE the subcommand: `uvx marimo@0.25.0 -y export html notebook.py -o out.html -f`. `-y` runs it in the uv sandbox.
   - The HTML embeds each cell's rendered output. I checked mo.md text, marimo-chatbot markup, run_button gating text and console prints.
   - `--include-code` is the default. Use `--no-include-code` when sharing.
2. `marimo export script --no-sandbox notebook.py [-o flat.py]` writes a flat topological script.
   - It does NOT wrap `mo.stop`, so it is a debugging aid, not a test.
3. In-process harness, no browser and no server. It needs a PEP 723 header on the harness file, or `uv run --with marimo==0.25.0`.
```python
import sys, importlib; sys.path.insert(0, ".")
nb = importlib.import_module("notebook")
outputs, defs = nb.app.run()          # outputs: one per non-setup cell, in topological order; defs: dict of all names
assert defs["go"].value is False      # run_button headless value
```
   - A raising cell makes app.run() raise the original exception (NameError etc.).
   - Run it with `uv run --script harness.py`.
   - `python notebook.py` (the `__main__` app.run() guard) also works and exits 0 or raises.
4. `marimo check` is static only.
   - It does NOT catch NameError or undefined names.

`marimo check [--strict] [--fix] [--format json] [--select MB,MR001] [--ignore MF004] [--ignore-scripts] file.py`
- Clean notebook: no output, exit 0.
- Errors, exit 1: `critical[multiple-definitions]` (MB002, includes shadowing a setup-cell name), `critical[cycle-dependencies]`.
- Non-marimo file: "Failed to parse ... (not a valid notebook)", exit 1.
- Warning only, e.g. missing `__generated_with` gives `warning[general-formatting]`: exit **0** normally, exit **1 with --strict**.
- Unused imports are not flagged.

## 7. LAUNCH / SANDBOX ON WINDOWS 11
- `uvx marimo@0.25.0 edit --sandbox notebook.py` worked headless (`--headless --no-token --port N`). Log:
  - "Running in a sandbox: uv.exe run --active --no-project --python ... --with marimo[lsp]==0.25.0 -- python -m marimo edit ... --sandbox=uv"
  - "Installed 42 packages in 8s". The server was up about 40s after a cold cache; later runs are fast.
- The first browser load once showed "Something went wrong: Failed to fetch dynamically imported module .../assets/edit-page-*.js". The asset returned 200 when fetched directly and a retry worked, so tell presenters to hit reload/"Try again".
- `uvx marimo@0.25.0 run notebook.py --sandbox` follows the same launch path.
- `uvx marimo@0.25.0 run notebook.py --headless --no-sandbox --port N` under Windows PowerShell with redirected stdout and no PYTHONIOENCODING started fine. marimo drops the emoji in its banner.
- 0.25.0 CLI changes: `--sandbox [uv|pixi]` replaces the `--sandbox/--no-sandbox` switch pair on edit and run, and `--no-sandbox` is now a separate flag meaning "never sandbox and never prompt". `export html/script` still have `--sandbox/--no-sandbox`.
- Sandbox internals were reworked in 0.25.0 (one env per notebook, editor starts before the kernel env is ready).
- marimo run/edit/export also write a `__marimo__/session/<nb>.py.json` cache folder next to the notebook. Add `__marimo__/` to .gitignore.
- `marimo run` flags: `--headless --port N --host 127.0.0.1 (default) --include-code --no-sandbox --sandbox --token --check/--no-check --show-tracebacks`.
- `marimo edit` flags: `--headless --no-token --port --sandbox --no-sandbox --skip-update-check --watch`.

## 8. PROOF (executed 2026-09-29, Windows 11, uv 0.11.25; files in %TEMP%\mo_proof)
Notebook `proof.py`. It uses PEP 723 marimo>=0.25.0, width="columns", `with app.setup:` (os, mo, GREETING), column 0 (md heading, password text prefilled from DEMO_API_KEY, run_button), and column 1 (a cell that `mo.stop(not go.value, ...)` and reads api_key and go from column 0, plus a mo.ui.chat cell with `echo_model(messages, config)` and prompts). The source is quoted below; the full file is `C:\Users\NickCooper\AppData\Local\Temp\mo_proof\proof.py`.
```
$ uvx marimo@latest --version                       -> 0.25.0
$ uvx marimo@latest check proof.py                  -> (no output)  exit=0
$ uvx marimo@latest check --strict proof.py         -> (no output)  exit=0
$ uvx marimo@latest export html proof.py -o proof.html -f
This notebook has inlined package dependencies.
Run in a sandboxed venv containing this notebook's dependencies? [Y/n]: Aborted!      exit=1   (no tty)
$ uvx marimo@latest export html --no-sandbox proof.py -o proof.html -f   -> exit=0, proof.html 30247 bytes
    (contains "Press the button in column 0 to run." = mo.stop output from the column-1 cell reading the column-0 run_button;
     a <marimo-chatbot> element with prompts ["hello","what is Redis Iris?"]; appConfig {"width":"columns"};
     cell config column 0/0/1/1, setup column null)
$ uvx marimo@latest -y export html proof.py -o y.html -f   -> sandboxed run "--with marimo==0.25.0", exit=0
$ uv run --script headless_check.py   (imports proof, calls app.run())
n outputs: 4
0 _md   <h1 id="column-0-config-run-button">Column 0: config + run button ...
1 _FlexContainerHtml <div style='display: flex; ...  (vstack of text + run_button)
2 _md   Press the button in column 0 to run.
3 chat  <marimo-ui-element ...><marimo-chatbot ...
defs: ['GREETING','api_key','chat','echo_model','go','mo','os']   run_button value: False
```
Browser confirmation (Playwright, Edge channel, `marimo run proof.py --headless --no-sandbox`, viewport 1800px):
- The column 0 title sits at x=160 and the column 1 content at x=929, two side-by-side columns.
- The setup cell is hidden.
- Typing a password and clicking the run_button in column 0 made the column 1 cell print "Ran with 'hello from app.setup'; key length = 10".
- In `marimo edit` both columns showed, the setup cell was on top of column 0, and column 1 overflowed off the 1800px viewport.
- Sending a chat message failed with the zod error on 0.25.0 and succeeded ("echo: ...") on 0.24.2.

## Explorable parameters

- **App width** — Overall layout of the notebook. Values: 'columns' for one column per Iris component; 'medium' or 'full' for single-column layouts _(src: marimo.App(width=...))_
- **column index** — Which column a cell renders in. Unmarked cells inherit the previous cell's column. Values: 0..3 for Search, LangCache, Context Retriever, Agent Memory _(src: @app.cell(column=N))_
- **--content-width (run mode column width)** — Width of each column in marimo run, default 740px. Lower it so 4 columns fit a projector. Values: 380px-460px for 4 columns at 1920px (measured spacing change only for 460px; 380px is an estimate) _(src: css_file=custom.css with :root { --content-width: 380px; })_
- **hide_code** — Hide a cell's code in edit mode; run mode already hides code by default. Values: True/False _(src: @app.cell(hide_code=True), app.setup(hide_code=True))_
- **launcher/header pin** — Which marimo version actually runs. The launcher version wins in the sandbox. Values: 0.24.2 (chat works, needs pyproject or fallback for .env) or 0.25.0 (.env autoload works, chat broken) _(src: uvx marimo@X + header marimo==X)_
- **--sandbox / --no-sandbox / -y** — Controls PEP 723 sandbox prompts. Needed for non-interactive export. Values: --sandbox (uv), --no-sandbox, global -y _(src: marimo CLI)_
- **mo.ui.chat config / prompts / show_configuration_controls** — Chat sampling config passed to your model as config.*, suggested prompts, and a UI panel to adjust sampling. Values: config keys max_tokens, temperature, top_p, top_k, frequency_penalty, presence_penalty _(src: mo.ui.chat(...))_
- **mo.ui.table selection / page_size / initial_selection** — How a hit list is selected, and how selection drives downstream cells. Values: 'single','multi','single-cell','multi-cell',None; .value is a list of rows _(src: mo.ui.table(...))_
- **mo.ui.form clear_on_submit / submit_button_label** — Gates expensive calls behind an explicit submit; clear_on_submit interacts with mo.stop. Values: True/False _(src: .form(...))_
- **mo.accordion expanded (new in 0.25.0)** — Which accordion items start open. Values: bool or list of item names _(src: mo.accordion(items, expanded=[...]))_

## Wow ideas

- Add a small `custom.css` (`:root { --content-width: 380px; }`) so all 4 Iris columns fit on a projector in `marimo run`, and give the notebook an `app_title`.
- Use mo.ui.tabs or mo.accordion(expanded=[...]) (new in 0.25.0) inside each column so a presenter can flip between 'How it works' and 'Try it' without leaving the column.
- Use mo.stat for live latency and hit-count tiles in each wow cell, and mo.status.spinner around every network call.
- Gate each column's wow cell behind its own mo.ui.run_button plus mo.stop, so the notebook exports cleanly headless with no live services (`marimo export html` exits 0) and only calls services when a presenter clicks.
- Ship a `check + export html --no-sandbox` script as the notebook's CI smoke test. It fails the build when any cell raises, and it works with no browser.
- If mo.ui.chat stays broken in the release you pin, use the verified DIY chat (text.form + persisted history list + mo.callout bubbles) for the LangCache and Agent Memory wow cells.
- mo.ui.table(selection='single') as a clickable hit list. The selected row drives a downstream detail cell in the same column, in the style of the 'explorer' UX.

## Safety concerns

- marimo itself touches no shared account, but the notebook will hold Redis Cloud, LangCache and Agent Memory credentials. Prefilled password inputs are masked and never reach the DOM, exported HTML or the session cache, but a cell that prints or mo.md's a secret does end up in exports and in __marimo__/session/*.json. Never echo secrets.
- `marimo export html` includes the code by default. Use --no-include-code when sharing exports, and remember non-password mo.ui.text values are written into the HTML as data-initial-value.
- Add `.env` and `__marimo__/` to .gitignore in the repo. marimo run/edit/export create the __marimo__/session cache folder beside the notebook.
- 0.25.0 now autoloads .env from the notebook's directory even without a pyproject.toml, so a customer .env sitting there is read into os.environ automatically. Say so in the README. It is skipped only when the notebook dir is the user's home directory.
- Do not start `marimo run/edit` with --host 0.0.0.0 or --no-token on shared networks. `marimo edit` gives full code execution, and edit mode uses a token by default. Keep 127.0.0.1.
- Everything here scopes by using namespaced keys and indexes (per-session prefix) as the old notebook does, and by cleaning up on a teardown cell or button. marimo has no built-in teardown, so add an explicit cleanup cell (run_button-gated) and never let a cell delete unprefixed keys.
- The 0.25.0 sandbox re-installs packages into a uv cache environment. It needs network access to PyPI on first run, which matters for customers behind proxies.

## Unknowns

- Whether upstream will fix the 0.25.0 mo.ui.chat zod regression soon. No issue or PR was found as of 2026-09-29; re-check PyPI for 0.25.1 before shipping.
- Why a fresh `marimo edit` session (with a __marimo__ session cache present) showed old cell outputs but the chat raised 'CellNotInitializedError: The cell containing this UI element has not been run yet' until I clicked the run-all button. Presenters using edit mode may need to run all cells first.
- Why PEP 723 `[tool.marimo.runtime] dotenv=[...]` did not load in my 0.24.2 export test (the pyproject.toml route did work there).
- Sparse or unusual column indices (e.g. 0 then 2, or negative) were not tested; use contiguous 0..N-1.
- Whether a cell in column 3 depending on column 0 behaves differently from column 1 depending on column 0. It should not, since the graph ignores columns, but only 1-from-0 was tested.
- Horizontal-scroll UX in run mode for 4 x 740px columns was measured by positions only, not visually judged. The 380px CSS estimate was not rendered.
- The one-time 'Failed to fetch dynamically imported module' on first load of a sandboxed edit session: cause not established.
- Behaviour on macOS or Linux (customers) was not tested.
- The `mo.callout` kind literal list beyond neutral/warn/success/info/danger comes from the CalloutKind type name rather than a printed enum.

## Facts (with sources)

- ✅ Latest marimo on PyPI is 0.25.0, uploaded 2026-09-23T21:48:29, requires-python >=3.10, not yanked. _(src: curl https://pypi.org/pypi/marimo/json -> info.version=0.25.0; releases['0.25.0'] yanked=False)_
- ✅ marimo 0.24.2 exists on PyPI (uploaded 2026-09-11T16:58:27), not yanked, requires-python >=3.10. _(src: same PyPI JSON: releases['0.24.2'] -> ['marimo-0.24.2-py3-none-any.whl','marimo-0.24.2.tar.gz'], yanked False)_
- ✅ The API is confirmed: marimo.App(width=...) accepts Literal['normal','compact','medium','full','columns'] and @app.cell(column: int|None, disabled, hide_code, expand_output). expand_output is new in 0.25.0. _(src: marimo/_config/config.py:123 WidthType; marimo/_ast/app.py:363-400 and _ast/cell.py:41-55 (diff of 0.24.2 vs 0.25.0 site-packages))_
- ✅ `with app.setup:` and `with app.setup(hide_code=True):` both exist. The setup cell coexists with width=columns, has config column=null, is shown at the top of column 0 in edit mode with a 'setup cell' label, and is not shown in run mode. Names defined in it are usable in all cells without parameters. _(src: marimo/_ast/app.py:547-580; Playwright screenshots shot_edit.png / shot_run.png in %TEMP%\mo_proof; `marimo check` passes with cells referencing os/mo without params)_
- ✅ A cell in column 1 can depend on variables defined in column 0 (columns are layout only). Tested column 1 reading api_key and go from column 0 in export, in headless app.run(), and interactively in run mode. Column 3 depending on column 0 was not tested directly; the graph ignores column indices. _(src: proof.py run via export html, app.run() harness and Playwright interaction ('key length = 10'))_
- ✅ In `marimo run` (0.25.0), width=columns renders side-by-side columns (flex-row, gap-8, each w-(--content-width) = 740px). Measured Col A/B/C/D at x=101/870/1638/2407 on a 1920px viewport, so 4 columns overflow. _(src: frontend/src/components/editor/renderers/vertical-layout/vertical-layout.tsx lines 120-131 at tag 0.25.0 (identical at 0.24.2); vertical-layout-wrapper.tsx (w-fit for columns); Playwright measurement on cols4.py; CSS grep --content-width:740px in marimo/_static/assets/*.css)_
- ✅ A CSS file set via App(css_file='custom.css') containing `:root { --content-width: 460px; }` shrinks run-mode column spacing (769px -> 489px). This is the only width control found for run mode. _(src: Playwright measurement, %TEMP%\mo_cols4 (cols4.py + custom.css); _ast/app_config.py css_file field)_
- ✅ Edit mode: columns are drag-resizable (min 500px), the width is stored in browser localStorage 'marimo:notebook-col-sizes' and not in the .py file. The header has move left/right, drag, 'Delete column', 'Add column'. There is no column naming. _(src: frontend/src/components/editor/columns/{cell-column.tsx,storage.ts,sortable-column.tsx} at tag 0.25.0; _AppConfig has no per-column fields (marimo/_ast/app_config.py))_
- ✅ Cells without an explicit column inherit the previous cell's column in file order (marimo writes column=N only where the column changes). Confirmed in run mode: an unmarked cell after column=1 rendered in column 1. _(src: Playwright test c2.py: AAA x=160, BBB x=160, CCC x=929, DDD x=929; export html config shows column null for the unmarked cells)_
- ✅ GitHub issue #9885 (open, 2026-06-15) is titled about column layouts and app mode and says columns are lost when switching to app mode. My test of 0.25.0 run mode rendered columns fine, so the issue's exact ask is ambiguous. #3543 (open) says column metadata is lost when switching away from columns view, with PRs #10797/#10991 open. _(src: https://github.com/marimo-team/marimo/issues/9885 and /issues/3543 via WebFetch; contradicted/qualified by the Playwright run-mode test)_
- ✅ UI signatures in 0.25.0 match the api_surface cheat-sheet; the only difference vs 0.24.2 is mo.accordion gaining keyword-only `expanded: bool | Sequence[str] = False`. _(src: inspect.signature dump of both versions, `diff sig24.txt sig25.txt`)_
- ✅ mo.ui.chat model contract: called as model(messages, config); if the callable has exactly one parameter it gets only messages. messages is list[ChatMessage] (msgspec Struct: role, content, id, parts, attachments, metadata). Sync fn, async fn, sync generator and async generator are all supported; non-str returns go through as_html. A sync model runs on the kernel event loop. _(src: marimo/_plugins/ui/_impl/chat/chat.py:470-545 and docstring lines 55-200; marimo/_ai/_types.py; https://docs.marimo.io/api/inputs/chat/)_
- ✅ REGRESSION: mo.ui.chat cannot send a message in 0.25.0. The browser shows 'Invalid input: expected nonoptional, received undefined at messages[0].metadata' (console error thrown in send_prompt), in both run and edit mode, and the model function is never called. The same notebook works in 0.24.2 run mode (reply 'echo: hello'). _(src: Playwright: chat_try.py (0.24.2 reply visible True, error False; 0.25.0 reply False, error True), chat_edit3.py (edit mode, error True))_
- ✅ Root cause of the chat regression: the 0.25.0 frontend bumped zod ^4.3.6 -> ^4.5.4 (and ai ^7.0.37 -> ^7.0.87). ChatPlugin.tsx (unchanged) has `metadata: z.any().nullable()`. In zod 4.5.4 a MISSING key fails with 'expected nonoptional, received undefined'; zod 4.3.6 accepts it. main branch still has zod ^4.5.4 and an identical ChatPlugin.tsx, and I found no upstream issue or fix PR as of 2026-09-29. _(src: raw.githubusercontent.com/marimo-team/marimo/{0.24.2,0.25.0,main}/frontend/package.json and frontend/src/plugins/impl/chat/ChatPlugin.tsx (diff empty 0.25.0 vs main); node repro %TEMP%\mo_zod (zod 4.3.6 -> true, 4.5.4 -> false 'Invalid input: expected nonoptional, received undefined'); GitHub issue search API returned 0 chat-related results)_
- ✅ A DIY chat (text.form(clear_on_submit=True) + a never-rerun `history` list cell + a display cell) works in 0.25.0 run mode over multiple turns. Using mo.stop(not ask.value) before appending breaks it, because clear_on_submit resets form.value to None and re-runs the cell. _(src: Playwright diyx3.py: both replies visible True, input cleared; earlier failed variant diyx.py)_
- ✅ Password inputs prefilled from os.environ are masked: the frontend gets initial value '' and password-has-value, shows placeholder '••••••••', and the secret never appears in the DOM, the exported HTML or the __marimo__ session JSON. Python `.value` returns the real secret until the user types (a frontend '' is ignored while masked). It survives a form submit. _(src: marimo/_plugins/ui/_impl/input.py:759-800 (identical in 0.24.2); Playwright pw_masked.py and cfgx.py (secret in DOM False, key length correct); grep of leak.html and session json = 0 hits)_
- ✅ .env autoload differs by version. 0.24.2 loads a .env only when a pyproject.toml is found walking upward from the notebook. 0.25.0 also loads <notebook dir>/.env when there is no pyproject.toml (skipped when the notebook dir is the home directory), and relative dotenv paths resolve against the pyproject or notebook dir. Confirmed empirically with a fake .env: 0.24.2 gives FAKE_KEY=None, 0.25.0 gives the value, with and without --sandbox, and from another cwd. _(src: marimo/_config/manager.py (0.24.2 lines 225-288 vs 0.25.0 lines 240-360, ScriptConfigManager.get_defaults ~509); marimo/_config/config.py:164-168; empirical runs in %TEMP%\mo_dotenv; https://docs.marimo.io/guides/configuration/runtime_configuration/)_
- ✅ In 0.24.2, a pyproject.toml next to the notebook makes .env load, also under --sandbox. PEP 723 `[tool.marimo.runtime] dotenv=['.env']` did NOT load it in my 0.24.2 export test (cause unknown), but did work in 0.25.0 from another cwd. _(src: %TEMP%\mo_dotenv/withpp and nb24m.py/nb25m.py runs)_
- ✅ Top-level await works in cells. asyncio.run() inside a cell raises 'asyncio.run() cannot be called from a running event loop'. Async generator chat models are accepted. app.run() inside an already-running loop works in 0.25.0 and raises RuntimeError in 0.24.2. _(src: %TEMP%\mo_async: anb.py export (gather result ['a','b'], asyncio.run FAILED message), lt25.py vs lt24.py; release notes '#10505 Run async notebooks from inside a running event loop')_
- ✅ `marimo export html` exits 1 with 'Export was successful, but some cells failed to execute.' when any cell raises (the HTML is still written). mo.stop() is not a failure. _(src: %TEMP%\mo_check/err.py run: exit=1 with NameError in the HTML; proof.py run exit=0)_
- ✅ With a PEP 723 header present, `marimo export html` without a flag prompts 'Run in a sandboxed venv... [Y/n]' and aborts (exit 1) when non-interactive. --no-sandbox, --sandbox or the global -y (before the subcommand) avoids it. _(src: command output in %TEMP%\mo_proof; `uvx marimo@latest -y export html proof.py -o y.html -f` exit 0)_
- ✅ marimo check: clean notebook exits 0 with no output; multiple-definitions (MB002, including shadowing a setup-cell name) and cycle-dependencies exit 1; a missing __generated_with gives a general-formatting warning that exits 0 but 1 under --strict; a non-notebook file is 'not a valid notebook' (exit 1); undefined names and unused imports are NOT flagged. _(src: runs of bad_multi.py, warn_only.py, cycle.py, notmarimo.py, undef.py in %TEMP%\mo_check; `marimo check --help` shows --fix --strict --format [full|json] --select --ignore --ignore-scripts)_
- ✅ In the 0.25.0 sandbox, the launcher's marimo version overrides the header pin. `uvx marimo@0.25.0 export html --sandbox` with header marimo==0.24.2 ran with `--with marimo==0.25.0` and reported marimo_version 0.25.0. With launcher 0.24.2 and header marimo>=0.24.2, uv resolved 0.25.0. Only launcher 0.24.2 + header ==0.24.2 gave 0.24.2. _(src: command logs and marimo_version in exported HTML, %TEMP%\mo_dotenv)_
- ✅ `uvx marimo@0.25.0 edit --sandbox --headless --no-token --port N notebook.py` works on Windows 11 (installs 42 packages, serves HTTP 200, cells render after a reload). The first page load showed a transient 'Failed to fetch dynamically imported module' error. _(src: %TEMP%\mo_proof sb_edit.log and Playwright sb_shot.py)_
- ✅ 0.25.0 CLI: `--sandbox [uv|pixi]` plus a separate `--no-sandbox` ('never run in a sandbox, and never prompt to') on edit and run; 0.24.2 had `--sandbox / --no-sandbox`. Pixi sandbox is new. `marimo run` does not send code to the browser unless --include-code. _(src: `marimo edit --help` and `marimo run --help` diff between the two venvs)_
- ✅ 0.25.0 release highlights: Pixi sandbox, reworked sandbox lifecycle (editor starts before kernel env), expand_output cell decorator, WASM single-file/offline export (`marimo export html-wasm`), accordion `expanded`, app.run() inside a running loop, doc fix for .env resolution without pyproject.toml. Notes state most users can upgrade without changes. _(src: https://api.github.com/repos/marimo-team/marimo/releases/tags/0.25.0 body; https://github.com/marimo-team/marimo/releases/tag/0.25.0)_
- ✅ The base marimo env has no pandas/polars/altair/numpy. mo.ui.table(list_of_dicts), slider, dropdown, radio, switch, forms, tabs, accordion, stat, callout, hstack/vstack, spinner, mo.state and mo.ui.button(on_click) all run headless in it. altair is absent, so mo.ui.altair_chart needs altair in the PEP 723 deps. _(src: ls marimo venv site-packages; comps.py exported with exit 0 (%TEMP%\mo_components))_
- ✅ Table row selection, radio and switch interact correctly in run mode on 0.25.0 (SEL=bravo after clicking the row checkbox; METRIC and STREAM update; no console errors). So the chat validation bug is not a generic breakage of UI RPC. _(src: Playwright smokex.py / smokex2.py on %TEMP%\mo_smoke\smoke.py)_
- ✅ `export html` and `run/edit` create a `__marimo__/session/<notebook>.py.json` cache folder next to the notebook. _(src: ls -R __marimo__ in %TEMP%\mo_proof)_
- ✅ Under Windows PowerShell with redirected stdout and no PYTHONIOENCODING, `uvx marimo@0.25.0 run proof.py --headless --no-sandbox` starts and serves HTTP 200 (banner printed without the emoji). _(src: PowerShell Start-Process run, ps_run.log in %TEMP%\mo_proof)_
- ✅ I did not test marimo on Linux/macOS, nor the same mo.ui.chat in edit mode on 0.24.2. The 'chat works on 0.24.2' finding is run-mode only. _(src: scope of testing)_

### Verifier: confirmed

- PyPI: the latest marimo is 0.25.0 (wheel uploaded 2026-09-23T21:48:29, requires_python >=3.10, yanked=False). 0.24.2 exists (whl 2026-09-11T16:58:27, tar.gz 16:58:30, yanked=False). There is no 0.25.1 or 0.24.3. Source: `curl https://pypi.org/pypi/marimo/json` parsed with uv's Python.
- WidthType = Literal['normal','compact','medium','full','columns'], with the comment 'normal == compact'. Source: marimo/_config/config.py:123 (0.25.0).
- 0.25.0 signature: `@app.cell(func=None, *, column: int|None=None, disabled=False, hide_code=False, expand_output=False)` (marimo/_ast/app.py:362-369). CellConfig has column/disabled/hide_code/expand_output (_ast/cell.py:41-53). In 0.24.2, CellConfig has only column/disabled/hide_code, and unknown keys are dropped with a LOGGER warning 'Invalid config keys' (0.24.2 _ast/cell.py:41-58).
- _AppConfig fields: width (default 'compact'), app_title, layout_file, css_file (relative to the app file), html_head_file, auto_download, sql_output. There are no per-column or column-name fields. Source: marimo/_ast/app_config.py (0.25.0).
- `with app.setup:` and `with app.setup(hide_code=True):` both exist (app.py:562-579). In the headless run and in export, the setup cell has config column=None. Setup names (mo, os, GREETING) are usable in cells without being parameters: app.run() defs listed them and `marimo check` passed.
- Cross-column dependencies work. In a browser (Playwright, Edge, `marimo run --headless --no-sandbox`, 0.25.0), a column-1 cell read a column-0 run_button and a column-0 password field and printed 'RAN_OK key length = 12' after the click.
- Run mode, width='columns': the source renders `<div className="flex flex-row gap-8 w-full">` with each column `flex-1 flex flex-col gap-2 w-(--content-width)` (frontend/src/components/editor/renderers/vertical-layout/vertical-layout.tsx at tag 0.25.0, renderCells). The bundled CSS sets --content-width:740px and --content-width-medium:1110px (grep of marimo/_static/assets/*.css).
- Four 740px columns overflow a 1920px viewport. Measured COL0..COL3 at x=112/884/1656/2428 (772px pitch). The horizontal scroll container is div#App (overflow-x:auto, scrollWidth 3152), which matches the report's 'about 3100px'.
- `App(css_file='custom.css')` containing `:root { --content-width: 380px; }` works. The computed --content-width was 380px, the columns sat at x=168/580/992/1404 (412px pitch), and all four fit a 1920px viewport. The report called 380px an unrendered estimate; it is now measured.
- Unmarked cells inherit the previous cell's column. Run mode: groupCellsByColumn uses `cell.config.column ?? lastSeenColumn` (vertical-layout.tsx:45-61). Edit mode: MultiColumn.fromIdsAndColumns uses prevColumn (frontend/src/utils/id-tree.tsx:659-682). Browser check: an unmarked cell after column=0 cells rendered at x=160, the same as the column-0 title, while column 1 was at x=932.
- Edit-mode column widths live in localStorage key 'marimo:notebook-col-sizes' (frontend/src/components/editor/columns/storage.ts:7). The column minimum is min-w-[500px] (cell-column.tsx:126). The header actions are 'Move column left', 'Move column right', 'Delete column' and 'Add column' (sortable-column.tsx:99,112,145,149). There is no column naming.
- UI signatures: inspect.signature dumps for 37 APIs on 0.24.2 and 0.25.0 match the report's cheat-sheet. The only diff is mo.accordion gaining keyword-only `expanded: bool | Sequence[str] = False` in 0.25.0. Source: `diff sig_v24.txt sig_v25.txt` in %TEMP%\mofc.
- mo.callout kinds are an explicit literal, not an inference: `CalloutKind = Literal['neutral','warn','success','info','danger']` (marimo/_plugins/stateless/callout.py:14). This settles the report's open question.
- mo.ui.chat contract: _run_prompt calls `self._model(messages)` when the callable has exactly one parameter, otherwise `self._model(messages, args.config)`. Sync and async generators are streamed, awaitables are awaited, and non-str results go through as_html(...).text. Source: marimo/_plugins/ui/_impl/chat/chat.py:513-545.
- ChatMessage is `msgspec.Struct(dict=True)` with role, content: Any, id='', parts=[], attachments=None, metadata=None. ChatModelConfig has max_tokens, temperature, top_p, top_k, frequency_penalty, presence_penalty. The default config is {'max_tokens':4096,'temperature':0.5,'top_p':1,'top_k':40,'frequency_penalty':0,'presence_penalty':0}. Source: marimo/_ai/_types.py:193-215 and 364-381, plus the inspect.signature output.
- The mo.ui.chat regression in 0.25.0 reproduces independently. In run mode, sending 'hello there' shows a red box 'Invalid input: expected nonoptional, received undefined → at messages[0].metadata' with a Retry button, and the console logs an error thrown from send_prompt. The same notebook on 0.24.2 replies 'echo: hello there t=0.5'. Source: Playwright screenshots %TEMP%\mofc\chat_27125.png (0.25.0) and chat_27124.png (0.24.2).
- Chat root cause confirmed. frontend/package.json: 0.24.2 has zod ^4.3.6 and ai ^7.0.37; 0.25.0 and main have zod ^4.5.4 and ai ^7.0.87. ChatPlugin.tsx is byte-identical across 0.24.2, 0.25.0 and main, with `metadata: z.any().nullable()` at line 30. The shipped bundles embed zod version major:4,minor:3,patch:6 (0.24.2) vs major:4,minor:5,patch:4 (0.25.0). Node repro: zod 4.3.6 accepts a missing metadata key; 4.5.4 rejects it with 'Invalid input: expected nonoptional, received undefined @ 0.metadata'.
- No upstream issue exists for the chat bug. GitHub search API `repo:marimo-team/marimo nonoptional` returned total_count 0 on 2026-09-29, and `chat expected nonoptional` also returned 0.
- Password masking: text.__init__ sets `_masked = kind=='password' and bool(value)`, `_frontend_initial_value` returns '' when masked, and _update/_update_value ignore '' while masked. Source: marimo/_plugins/ui/_impl/input.py:760-799; the text class is identical in 0.24.2.
- No secret leaks. With a fake FAKE_KEY, exported HTML contained 0 occurrences of the secret for a plain password field, the report's exact `.batch(...).form()` config pattern, mo.ui.array and mo.ui.dictionary. The batch's data-initial-value was {"url":"","ns":"demo"}. The __marimo__/session json also had 0 hits.
- Config-form pattern in the browser (0.25.0): with a fake REDIS_URL from .env, the secret was absent from the DOM before and after 'Save config'. After submit, `cfg['url'] == os.environ['REDIS_URL']` was True (len 46), with no retyping.
- .env autoload, measured with a fake .env via `export html --no-sandbox --no-include-code`. No pyproject, cwd = notebook dir: 0.24.2 gives None, 0.25.0 gives the value. No pyproject, cwd = another dir: 0.24.2 None, 0.25.0 value. pyproject.toml beside the notebook, other cwd: both versions load it.
- 0.25.0 .env code: ProjectConfigManager._dotenv_root falls back to the notebook's directory when there is no pyproject.toml, and get_defaults skips the fallback when that directory is the home directory (marimo/_config/manager.py:255-287). ScriptConfigManager.get_defaults delegates to it (manager.py:506-518). The docs say the same: https://docs.marimo.io/guides/configuration/runtime_configuration/ ('A notebook with no pyproject.toml, such as a sandboxed notebook, loads the .env next to the notebook instead. marimo skips this default when the notebook is in your home directory'; `dotenv = []` disables it).
- PEP 723 `[tool.marimo.runtime] dotenv = [".env"]` does not load on 0.24.2, even with cwd = notebook dir (FAKE_KEY=None). It works on 0.25.0 from any cwd. 0.24.2's ScriptConfigManager.get_config never calls _resolve_dotenv (0.24.2 manager.py:430-470); 0.25.0 does (manager.py:557-559).
- .env values never override variables already set. load_to_environ skips keys already in os.environ (marimo/_secrets/load_dotenv.py load_to_environ; identical file in 0.24.2 and 0.25.0).
- Async: top-level await and asyncio.gather work in a cell. asyncio.run() inside a cell raises 'asyncio.run() cannot be called from a running event loop'. Neither version has app.run_async. app.run() inside asyncio.run(main()) works on 0.25.0 and raises that RuntimeError on 0.24.2. Source: %TEMP%\mofc\asy\lt.py run with both venvs.
- `marimo export html` with a failing cell prints 'Error: Export was successful, but some cells failed to execute.', exits 1, and still writes the HTML (22833 bytes). A mo.stop output exits 0.
- With a PEP 723 header, uv on PATH and no TTY, `marimo export html pep.py` prints 'Run in a sandboxed venv containing this notebook's dependencies? [Y/n]: Aborted!' and exits 1. `--no-sandbox` exits 0. The global `-y` before the subcommand runs in a sandbox ('Running in a sandbox: uv run --active --no-project --python <env> --with marimo==0.25.0 -- python -m marimo -y export html ...') and exits 0.
- Launcher version wins in the 0.25.0 sandbox. With header marimo==0.24.2 and launcher 0.25.0, the log shows the env installing marimo==0.24.2 and then running `--with marimo==0.25.0`; the exported marimo_version is 0.25.0. Cause: marimo/_environments/overlay.py:125 `runtime = f"marimo{suffix}=={__version__}"`.
- 0.24.2 launcher pinning. With header `marimo>=0.24.2`, the 0.24.2 launcher (uv run --isolated --no-project --with-requirements <tmp>) ran 0.25.0. With header ==0.24.2 it ran 0.24.2.
- marimo check (0.25.0): a clean notebook gives no output and exit 0. Shadowing a setup-cell name gives critical[multiple-definitions] and exit 1. A missing __generated_with gives warning[general-formatting]: exit 0 normally, exit 1 with --strict. An undefined name is not flagged (exit 0, even with --strict). `check --help` lists --fix, --strict, -v/-q, --unsafe-fixes, --ignore-scripts, --format [full|json], --select and --ignore.
- CLI: 0.25.0 edit and run have `--sandbox [uv|pixi]` plus a separate `--no-sandbox` ('Never run in a sandbox, and never prompt to.'); 0.24.2 has `--sandbox / --no-sandbox`. export html keeps `--sandbox / --no-sandbox` and `--include-code / --no-include-code` (default include-code). run's --include-code help says code is not sent to the client by default. The global `-y, --yes` exists in both. Source: `marimo <cmd> --help` for both venvs.
- Export writes a __marimo__/session/<notebook>.py.json folder next to the notebook (seen after the export in %TEMP%\mofc\leak).
- Headless harness: `outputs, defs = app.run()` works, and a raising cell re-raises the original NameError. Values before interaction: run_button False; table(selection='single') []; selection=None gives None; initial_selection=[1] gives [{'name':'b'}]; text().form() None; md().batch().form() None; array [3, 1]; dictionary {'k': 5, 't': 'x'}; dropdown(dict, value='Label B') gives 2 (value is the label); tabs gives 'One' (the tab name).
- The base marimo 0.25.0 venv has no pandas, polars, altair or numpy; it has narwhals 2.26.0 and msgspec 0.22.0. mo.ui.table(list_of_dicts) ran headless there. Source: `uv pip list` on the v25 venv.
- 0.25.0 release notes (published 2026-09-23T21:57:18Z) cover: Pixi sandboxes (#10697), one env per notebook (#10728), async startup (#10821), expand_output (#10911), accordion expanded (#10824), run async notebooks inside a running loop (#10505), html-wasm --single-file/--offline, a docs entry 'Explain .env resolution when there is no pyproject.toml' (#10583), and 'We expect most users to be able to upgrade without making changes.' Source: https://api.github.com/repos/marimo-team/marimo/releases/tags/0.25.0.
- Issue #3543 ('Switching from columns view to another view loses column metadata', opened 2025-01-23) is open. PRs #10797 and #10991 are open and not merged. Source: GitHub API.
- All 22 docs/source URLs in the report return HTTP 200, and docs.marimo.io returns a real 404 for a bogus path, so these are not soft 404s. Also 200: https://docs.marimo.io/api/inputs/text/, https://docs.marimo.io/api/app/ and https://docs.marimo.io/api/cell/.
- The existing C:\Users\NickCooper\projects\demo\redis-iris-basics\notebook.py pins `marimo==0.24.2` in PEP 723, has `__generated_with = "0.24.2"` and `marimo.App(width="medium")`, and its docstring launcher is `uvx marimo@0.24.2 edit --sandbox notebook.py` (notebook.py:1-22).