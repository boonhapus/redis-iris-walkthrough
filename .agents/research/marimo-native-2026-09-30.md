# marimo 0.25.0 native features for the MVP notebook

Research brief, 2026-09-30. Scope: what the rebuilt `iris_basics.py` should use natively instead of
the hand-rolled env/secret layer, ledger, choke points and helpers.

Method and tags:
- **VERIFIED (source)**: read in the installed package `marimo==0.25.0`
  (`...\uv\cache\archive-v0\BeolOrIrZ0G3BjlI\Lib\site-packages\marimo`, Python 3.13.14).
- **VERIFIED (run)**: executed today on this Windows 11 machine with
  `uv run --python 3.13 --no-project --with marimo==0.25.0 ...` in `%TEMP%\motest\...`; nothing under
  `projects/demo` was touched, no Redis or Iris service was called.
- **VERIFIED (docs)**: raw markdown from `github.com/marimo-team/marimo/main/docs/...` fetched today.
- **VERIFIED (GitHub API)**: issue/PR state fetched today.
- **VERIFIED (prior brief)**: browser/Playwright checks recorded in
  `.agents/plans/research/marimo.md` on 0.25.0 that I did not repeat (no browser in this workflow).
- **UNVERIFIED**: inferred or not checked.

Latest PyPI marimo is still 0.25.0 (uploaded 2026-09-23); the GitHub release list shows 0.25.0, 0.24.2,
0.24.1. VERIFIED (PyPI JSON + GitHub API).

---

## 1. `.env` loading, the Secrets panel, and password inputs

### 1.1 Which `.env` is loaded, and when

- **Default file.** With no `dotenv` key anywhere, marimo emits a default of exactly one file:
  `<dir of nearest pyproject.toml>/.env`. The nearest `pyproject.toml` is found by walking up from
  the notebook path (`find_nearest_pyproject_toml`, `_config/reader.py:195`). With no `pyproject.toml`
  the anchor is the notebook's own directory; the default is skipped when that directory is `~`.
  VERIFIED (source `_config/manager.py:255-290`, `ProjectConfigManager.get_defaults`,
  `ScriptConfigManager.get_defaults`) and VERIFIED (run): for `%TEMP%\motest\proj\nb_ok.py` with a
  `pyproject.toml` beside it, the resolved config was
  `runtime.dotenv = ['C:\...\motest\proj\.env']`.
- **Explicit list.** `[tool.marimo.runtime] dotenv = [".env", ".env.testing"]`; relative paths
  resolve against the `pyproject.toml` directory (or the notebook dir without a project); a missing
  file is silently ignored; `dotenv = []` loads nothing. The same key is allowed in PEP 723 script
  metadata, which outranks `pyproject.toml`. VERIFIED (docs `runtime_configuration.md`, source
  `_resolve_dotenv`, `config.py:164-168`).
- **Values land in `os.environ`.** `load_to_environ` does `os.environ[key] = value` for each binding,
  **skipping keys already present in `os.environ`** (python-dotenv `override=False` semantics) and
  skipping `None` values. VERIFIED (source `_secrets/load_dotenv.py:load_to_environ`) and VERIFIED
  (run): `TESTVAR_PRESET=from_shell marimo export html nb_ok.py` printed `PRESET=from_shell`
  while `.env` said `from_dotenv_should_lose`; without the shell value it printed the `.env` value.
- **When.** `Kernel.instantiate()` calls `self.load_dotenv()` before any cell runs
  (`_runtime/runtime.py:2281`, `2393-2407`). The Secrets panel's refresh re-runs the same method.
  VERIFIED (source).
- **Parser.** If `python-dotenv` is importable marimo uses `dotenv.parser.parse_stream` (handles
  `export KEY=...`, inline `# comments`, quoting); otherwise a built-in fallback splits on the first
  `=` and strips matching quotes only, so `KEY=abc # note` loads as `abc # note` and `export KEY=x`
  is skipped. `${VAR:-default}` interpolation is applied in both paths. VERIFIED (source
  `_read_dotenv_bindings`, `_parse_dotenv_bindings`, `_interpolate_dotenv_values`). python-dotenv is
  not a marimo dependency (prior brief). Latest python-dotenv is 1.2.3 (PyPI, 2026-08-16).

### 1.2 Per entry point

| Entry point | Reads `[tool.marimo.*]`? | Loads `.env`? | Applies `runtime.pythonpath`? |
|---|---|---|---|
| `uv run marimo edit iris_basics.py` | yes, anchored on the path given to `marimo edit` (`_server/start.py:261` -> `get_default_config_manager(current_path=start_path)`) | yes, at kernel instantiate | yes (`runtime.py:571-576`, `sys.path.insert(0, path)`) |
| `uv run marimo run iris_basics.py` | yes | yes | yes |
| `marimo export html iris_basics.py` | yes | yes | yes |
| `python iris_basics.py` / `uv run iris_basics.py` | **no** | **no** | **no** |
| `app.run()` from pytest or another script | **no** | **no** | **no** |

- Kernel rows: VERIFIED (run) with `marimo export html` launched from a different cwd (`/tmp`):
  `DOTENV=from_dotenv ... SRC_ON_PATH=True`, and `from demo_lib import helper` succeeded via
  `pythonpath = ["src"]`.
- Script rows: VERIFIED (run): `python nb_ok.py` printed `DOTENV=None PRESET=None SRC_ON_PATH=False`
  and the `src` import failed; `App.run()` from `python -c` behaved the same. VERIFIED (source):
  `App.run()` goes to `AppScriptRunner` (`_ast/app.py:791`), which never reads the config, and the
  only `sys.path.insert` calls in the package are in `_runtime/runtime.py` (kernel init).
  **This contradicts `importing_local_code.md`, which says `pythonpath` applies "when running it as a
  script".** Treat the docs as wrong for 0.25.0.
- Workarounds for script mode, both VERIFIED (run) inside the `uv init --lib` scaffold:
  `uv run --env-file .env nb_ok.py` loaded the `.env` (uv feature, `UV_ENV_FILE`), and an editable
  install of the `src/` package made the import work without `pythonpath`.

### 1.3 The Secrets panel

- It exists: developer panel tab **Secrets**, "Manage environment secrets" (docs `panels.md`). Its
  endpoints require the `edit` permission, so it is an editor-only feature. VERIFIED (docs, source
  `_server/api/endpoints/secrets.py` `@requires("edit")`).
- Providers: `Environment variables` (a snapshot of `os.environ` taken when the kernel started, so
  dotenv-loaded keys can be told apart from inherited ones) plus one provider per configured dotenv
  file, named after the file (`.env`). VERIFIED (source `_runtime/callbacks/secrets.py`,
  `_secrets/secrets.py:_get_providers`, `_secrets/env_provider.py`).
- **It lists key names only.** `get_secret_keys` returns `keys: list[str]` per provider, de-duplicated
  in provider order; no value ever leaves the kernel through this path. VERIFIED (source
  `_secrets/models.py:SecretKeysWithProvider`, `get_secret_keys`).
- **Create** appends `KEY="escaped value"` to the chosen dotenv file (creates it if it is named
  `.env`), then sends `RefreshSecretsCommand`, which re-runs `load_dotenv()` so the new key is in
  `os.environ` at once (existing keys are still not overridden). **Delete** returns HTTP 501 "Not
  implemented". VERIFIED (source `DotEnvSecretsProvider.write_key`, endpoints `create`/`delete`).
- `runtime.dotenv` is itself treated as a secret path in the config: it is masked to `[]` when the
  config is sent to the frontend, and a persisted empty list is dropped on read so a stale
  `.marimo.toml` cannot suppress the default. VERIFIED (source `_config/secrets.py`,
  `_drop_hollow_dotenv`; run: `cm.get_config()['runtime']['dotenv'] == []` masked vs the real path
  with `hide_secrets=False`).

### 1.4 `mo.ui.text(kind="password")`

Signature VERIFIED (inspect):
`mo.ui.text(value='', placeholder='', kind: Literal['text','password','email','url']='text', max_length=None, disabled=False, debounce: bool|int=True, *, label='', on_change=None, full_width=False)`

- Renders `<input type="password">`. If constructed with a non-empty `value`, the element is
  "masked": `_frontend_initial_value` returns `""` so the prefilled value never reaches the browser;
  a `password-has-value` flag tells the frontend to show dots; empty updates from the frontend are
  ignored while masked so the backend value survives. VERIFIED (source
  `_plugins/ui/_impl/input.py:760-795`).
- **The Variables panel does receive the value.** `_format_variable_value` resolves any `UIElement`
  to `element.value` and truncates strings to 50 characters (`MAX_STR_LEN = 50`); values are broadcast
  after each cell run when `ctx.should_broadcast_data` (edit mode). So a global bound to a password
  input, or any `str` global holding a secret, shows its first 50 characters in the Variables panel.
  VERIFIED (source `_messaging/variables.py:178-244`, `_runtime/runner/hooks_post_execution.py:125-143`).
  Whether `_underscore` (cell-local) names are excluded from that broadcast: UNVERIFIED.
- Consequence for the MVP: the orchestrator's "no typed-in secret fields" decision is the right one.
  Keep secrets in `os.environ` and read them inside the function that builds a client
  (`redis.Redis.from_url(os.environ["REDIS_URL"])`); never assign a secret to a notebook global.

---

## 2. `pythonpath = ["src"]` vs an installed local package

### 2.1 What marimo recommends

- Docs: "When possible, it's preferred to avoid path manipulation ... we recommend creating a
  package and including marimo as a project dependency", with `uv init --lib`, `uv add --dev marimo`,
  `uv run marimo edit notebook.py`. `pythonpath` is offered for non-package layouts. VERIFIED
  (docs `runtime_configuration.md` "Python path", `importing_local_code.md`, `projects.md`).
- Given 1.2 (pythonpath is ignored by `python nb.py`, `app.run()` and therefore by pytest), the
  editable install is the only option that works in every mode. Recommendation: **editable package,
  no `pythonpath`.**

### 2.2 Minimal `pyproject.toml` (notebook at repo root, package under `src/iris_demo/`)

`uv init --lib --name iris-demo-lib --python 3.13 .` with uv 0.11.25 generated exactly this
build-system block, `src/iris_demo_lib/__init__.py`, `src/iris_demo_lib/py.typed`, `.python-version`
= `3.13`. VERIFIED (run). Package name is normalised to the module name (`iris-demo-lib` ->
`iris_demo_lib`; uv docs `build-backend.md`).

```toml
[project]
name = "redis-iris-basics"            # module defaults to src/redis_iris_basics/
version = "0.1.0"
requires-python = ">=3.11,<3.14"
dependencies = [
    "marimo==0.25.0",
    "redis==8.1.0",
    "niquests==3.21.2",
    "redis-context-retriever==0.0.6",
    "model2vec==0.9.0",
    "numpy==2.5.3",
    "python-dotenv==1.2.3",           # optional: standard .env parsing (comments, export)
]

[dependency-groups]
dev = ["pytest==9.1.1"]

[build-system]
requires = ["uv_build>=0.11.25,<0.12.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "iris_demo"             # only if the module name differs from the project name

[tool.marimo.runtime]
dotenv = [".env"]                     # explicit; equals the default
auto_reload = "lazy"                  # pick up src/ edits; "autorun" re-runs affected cells

[tool.marimo.display]
cell_output = "above"
```

- `uv sync` builds and installs the project editable because a `[build-system]` is present
  (`[tool.uv] package = true` is only needed to force it without one; `--no-editable` opts out).
  VERIFIED (uv docs `projects/config.md` "Build systems", "Editable mode") and VERIFIED (run): after
  `uv add marimo==0.25.0`, `.venv/Lib/site-packages/iris_demo_lib.pth` contains the `src` path and
  `iris_demo_lib.__file__` is `...\uvlib\src\iris_demo_lib\__init__.py`.
- `uv run marimo export html nb_ok.py` and `uv run marimo check --strict nb_ok.py` ran inside that
  scaffold with exit 0. VERIFIED (run). `uv run marimo edit` itself was not launched (server), so the
  editor experience is UNVERIFIED here; the config anchor is the same code path (source).
- Cache-signing note: `strip_untrusted_config` removes cache-trust keys from `pyproject.toml`; nothing
  the MVP needs. VERIFIED (source `manager.py:305`).
- Keep the PEP 723 header out of the notebook once the project exists: `marimo edit/run/export` offer
  or require sandbox handling when a header is present (`--no-sandbox` "never run in a sandbox, and
  never prompt to"; prior brief: export needed `--no-sandbox` with a header). VERIFIED (CLI help,
  prior brief). Running `uv run iris_basics.py` with a header makes uv use the header instead of the
  project (docs `scripts.md`).
- `mo.notebook_dir()` for data files: kernel cwd is the launch cwd, not the notebook dir (prior brief).

### 2.3 Module autoreload picks up `src/` edits

- Setting: `[tool.marimo.runtime] auto_reload = "off" | "lazy" | "autorun"`, default `"off"`.
  VERIFIED (source `config.py`, docs "On module change").
- Which modules are watched: everything whose `__file__` is outside stdlib/`purelib`/`platlib`
  (`is_user_module`, docstring: "Editable installs ... point `__file__` at the source tree, so they
  are correctly classified as user code"); `_is_third_party_module` excludes paths containing a
  `site-packages` part. An editable `src/` package therefore qualifies. VERIFIED (source
  `_runtime/reload/autoreload.py:78-125`, `module_watcher.py:80-84`). The actual re-run in a browser
  session: UNVERIFIED here (docs describe it; prior brief did not test it).

---

## 3. Native building blocks (signatures from `inspect.signature`, VERIFIED unless noted)

### 3.1 Gating and inputs

- `mo.ui.run_button(kind='neutral'|'success'|'warn'|'danger', disabled=False, tooltip=None, *, label='click to run', on_change=None, full_width=False, keyboard_shortcut=None)`
  `.value` is `False` until clicked, `True` for the run its click triggers, then reset to `False`
  "as long as automatic execution is enabled" (docstring).
  Pattern (docs `control_flow.md`):
  ```python
  rs_seed_button = mo.ui.run_button(label="Seed help articles", kind="success")
  # next cell
  mo.stop(not rs_seed_button.value, mo.callout("Press Seed to load the 36 articles.", kind="info"))
  ```
  `mo.stop(predicate, output=None)` raises `MarimoStopError`; the cell's output becomes `output`,
  descendants do not run and their defs are removed. VERIFIED (docstring) and VERIFIED (run): in
  `App.run()` the stopped cell returned the callout and `after_stop` was absent from `defs`.
- `mo.ui.slider(start=None, stop=None, step=None, value=None, debounce=False, disabled=False, orientation='horizontal', show_value=False, include_input=False, steps=None, *, label='', on_change=None, full_width=False)`
  e.g. `lc_similarity_threshold = mo.ui.slider(0.5, 1.0, step=0.01, value=0.9, debounce=True, show_value=True, label="Similarity threshold")`.
- `mo.ui.text(...)` see 1.4; `mo.ui.text_area(value='', placeholder='', max_length=None, disabled=False, debounce=True, rows=None, *, label='', ...)`.
- `mo.ui.dropdown(options: Sequence | dict[str, Any], value=None, allow_select_none=None, searchable=False, *, label='', on_change=None, full_width=False, disabled=False)`
  dict options map label -> value; `value` is a label.
- `mo.ui.number(start=None, stop=None, step=None, value=None, debounce=False, *, label='', ...)`,
  `mo.ui.switch(value=False, *, label='', ...)`, `mo.ui.radio(options, value=None, inline=False, *, label='', ...)`.
- `mo.ui.form(element, *, bordered=True, loading=False, submit_button_label='Submit', ..., clear_on_submit=False, show_clear_button=False, ..., validate=None, label='', on_change=None)`
  and `mo.ui.batch(html, elements, on_change=None)` / `mo.md("{q}").batch(q=mo.ui.text())`.
  Gotcha (prior brief, browser): `clear_on_submit` clears the inputs but `form.value` keeps the last
  submitted value, so submitting the **same** text twice does not re-run dependents. For "ask the
  same question twice to show a LangCache hit" use `mo.ui.text` + `mo.ui.run_button`, which re-ran
  on every click (prior brief: n=1,2,3).
- `mo.ui.chat(model, *, prompts=None, on_message=None, show_configuration_controls=False, config={...}, allow_attachments=False, max_height=None, disabled=False)`
  **Broken in 0.25.0; confirmed today without a browser:**
  1. The installed frontend bundle `_static/assets/index-CgQsZfww.js` contains
     `metadata:vn().nullable()` (the minified `z.any().nullable()`); the source at tag 0.25.0 and on
     `main` is `metadata: z.any().nullable()` in `frontend/src/plugins/impl/chat/ChatPlugin.tsx`,
     used as `send_prompt: rpc.input(z.object({request_id, messages: messageSchema, config}))`.
     VERIFIED (grep of installed bundle; raw GitHub fetch).
  2. `frontend/package.json` pins `"zod": "^4.5.4"` at 0.25.0 and on `main`. VERIFIED (GitHub).
  3. node v24 + zod 4.5.4 on that exact schema: a message **without** a `metadata` key fails with
     `Invalid input: expected nonoptional, received undefined at 0.metadata`; `metadata: undefined`
     and `metadata: null` pass. VERIFIED (run, `%TEMP%\motest\zod`).
  4. `chat-ui.tsx` at 0.25.0 builds the payload as `body.messages.map(m => ({...m, content: ...}))`
     from `sendMessage({ role: "user", parts: [...] })`, which sets no `metadata` key. VERIFIED (GitHub).
  5. GitHub search for issues/PRs on this since 2026-09-20: none. VERIFIED (GitHub API).
  The in-browser red error was reproduced by the prior brief (Playwright, run and edit mode). No
  Python-side workaround exists (the key is missing before Python is called). Do not use
  `mo.ui.chat` on 0.25.0; the plan has no LLM anyway.

### 3.2 Showing data

- `mo.ui.table(data, pagination: bool|None=None, selection='multi'|'single'|'single-cell'|'multi-cell'|None='multi', initial_selection=None, page_size: int|None=None, show_column_summaries=None, show_data_types=True, format_mapping=None, freeze_columns_left=None, freeze_columns_right=None, text_justify_columns=None, wrapped_columns=None, column_widths=None, hidden_columns=None, visible_columns=None, header_tooltip=None, show_download=True, max_columns='inherit', *, show_search=True, label='', on_change=None, style_cell=None, hover_template=None, max_height=None)`
  A `list[dict]` is the first documented input form. `page_size` defaults to
  `display.default_table_page_size` = 10; with `pagination=None` marimo paginates **iff rows >
  page_size**, so 36 rows would show 10 per page. To show all 36: `pagination=False` (then
  `page_size = total_rows`) or `page_size=36`. Display-only table:
  `mo.ui.table(rs_help_articles, pagination=False, selection=None, show_download=False, show_column_summaries=False, show_data_types=False, label="36 help articles")`.
  VERIFIED (source `_plugins/ui/_impl/table.py:570-571, 799-806`, docstring). Rendering 36 rows in
  a 740 px column: UNVERIFIED (no browser); `max_height=` and `wrapped_columns=` exist for that.
- `mo.json(data: str | dict[str, Any] | list[Any], label: str | None = None) -> Html`
  "Render a JSON with tree." Accepts a Python dict directly:
  `mo.json({"request": {"method": "POST", "url": url, "body": body}, "response": resp_json}, label="LangCache /entries")`.
  VERIFIED (signature, docstring, docs `api/layouts/json.md`). Collapsible-tree behaviour in the
  browser: UNVERIFIED here (that is the documented purpose). Sibling: `mo.tree(items, label=None)`.
- `mo.callout(value, kind: 'neutral'|'warn'|'success'|'info'|'danger'='neutral', title: str|None=None)`
  VERIFIED (source `CalloutKind`, signature). e.g.
  `mo.callout(mo.md("Set REDIS_URL in .env to run this column."), kind="warn", title="Not configured")`.
- `mo.accordion(items: Mapping[str, object], multiple=False, lazy=False, *, expanded: bool | Sequence[str] = False)`
  strings render as markdown; `lazy=True` wraps each item in `mo.lazy`; `expanded=True` opens the
  first item. e.g. `mo.accordion({"Raw response": mo.json(resp)})`.
- `mo.stat(value: str|float, label=None, caption=None, direction: 'increase'|'decrease'|None=None, bordered=False, target_direction='increase', slot: Html|None=None) -> Html`
  e.g. `mo.hstack([mo.stat(hit_count, label="Cache hits", bordered=True), mo.stat(f"{latency_ms:.0f} ms", label="Latency")])`.
- `mo.mermaid(diagram: str, theme: str|None=None, theme_variables: dict[str,str]|None=None) -> Html`
  follows the app light/dark theme by default; `theme_variables` requires `theme="base"` (or
  `theme=None`, which switches to base). VERIFIED (docs `api/diagrams.md`).
- `mo.hstack(items, *, justify='space-between', align=None, wrap=False, gap=0.5, widths='equal'|Sequence[float]|None=None)`,
  `mo.vstack(items, *, align=None, justify='start', gap=0.5, heights=None)`, `mo.tabs(tabs)` and
  `mo.ui.tabs(tabs, value=None, lazy=False, *, orientation='horizontal', label='', on_change=None)`.
- `mo.md(text) -> Html`, `mo.plain_text`, `mo.as_html`, `mo.output.replace/append/clear`.

### 3.3 Status and caching

- `mo.status.spinner(title=None, subtitle=None, remove_on_exit=True)` context manager with
  `_spinner.update(subtitle=...)`; `mo.status.progress_bar(collection=None, *, title=None, subtitle=None, completion_title=None, completion_subtitle=None, total=None, show_rate=True, show_eta=True, remove_on_exit=False, disabled=False)`
  e.g. `for article in mo.status.progress_bar(rs_help_articles, title="Indexing"): ...`.
- `mo.cache(name_or_fn=None, *args, pin_modules=False, loader=None, ...)`,
  `mo.lru_cache(name_or_fn=None, maxsize=128, *args, pin_modules=False, ...)`,
  `mo.persistent_cache(name_or_fn=None, save_path=None, method='pickle', store=None, fn=None, *args, pin_modules=False, ...)`
  Decorator or `with` block; keys on arguments + closed-over values + defining code; cache survives
  re-running the defining cell if the code did not change; persistent store defaults to
  `__marimo__/cache/` next to the notebook (add `**/__marimo__/cache/` to `.gitignore`).
  VERIFIED (signatures, docstrings, docs `api/caching.md`). For the local embedding model:
  `@mo.cache def load_embedding_model(model_name: str): return StaticModel.from_pretrained(model_name)`
  keeps one instance per kernel across cell re-runs; `mo.persistent_cache` would pickle the model
  object to disk, which for model2vec is UNVERIFIED and unnecessary (the HF cache already holds the
  weights). Cache the embeddings (numpy arrays) with `mo.persistent_cache` if seed time matters.

### 3.4 Cell options and widgets

- `App.cell(func=None, *, column: int|None=None, disabled=False, hide_code=False, expand_output=False, **kwargs)`; same kwargs on `App.function` and `App.class_definition`.
  `with app.setup(hide_code=True):` is the documented way to hide setup code. VERIFIED (signatures,
  docstring; `nb_struct.py` with `app.setup(hide_code=True)` passed `check --strict`).
- `marimo run` never sends code to the browser unless `--include-code`; `hide_code` matters only in
  `marimo edit` and `marimo export html` (default `--include-code`). VERIFIED (CLI help).
- `mo.ui.anywidget(widget: AnyWidget)` exists for custom widgets. **marimo-pair is not a widget
  library**: it is an agent skill (`npx skills add marimo-team/marimo-pair`) that lets a coding agent
  drive a running notebook. VERIFIED (README fetched). No custom widget is needed for the MVP.

---

## 4. Structure: setup, top-level functions, sharing helpers, `check --strict`

### 4.1 Setup cell rules

- Runs before every other cell, in `marimo edit`, `marimo run` and `app.run()`. May contain imports,
  constants and plain function definitions; VERIFIED (run): `def tag_key(...)` defined inside
  `with app.setup(hide_code=True):` passed `check --strict`, was callable from an `@app.function`, and
  appeared in `app.run()` defs.
- Restrictions: it cannot reference names defined in other cells (`SetupRootError: The setup cell
  cannot reference any additional variables: {...}`), cannot redefine `app`, and `App.run(defs=...)`
  may not override setup names (`TypeError`). VERIFIED (source `_ast/app.py:130-137, 786`, docs
  `understanding_errors/setup.md`).
- Docs framing: "imports and constants defined in an `app.setup` with block" are what top-level
  functions may close over (docs `watching.md`).

### 4.2 `@app.function` / `@app.class_definition`

- One function or class per cell; may reference only setup names, other top-level functions/classes
  and builtins; not variables from regular cells; no cycles; `_private` names cannot be top-level.
  Importable: `from iris_basics import rs_search_help_articles`. VERIFIED (docs
  `reusing_functions.md`; run: `from nb_struct import doubled, tagged_double` worked and
  `tagged_double(4) == "demo-run:8"` used a setup constant and another top-level function).
- Each one is a cell in the graph with `column=`/`hide_code=` config. VERIFIED (signature).

### 4.3 Why 300 `@app.function` items were slow

- Plan section 10 records: with about 300 `@app.function` items `marimo edit` took about 42 s to Run
  all and about 20 s per control change; after moving the helpers into the setup block (112 cells)
  about 10 s and about 0.5 s. VERIFIED (plan text, `iris-basics-4-column-plan.md:576-579`).
- Mechanism: every top-level function is its own cell, so the frontend renders and tracks 300 more
  cell editors and the kernel's per-interaction bookkeeping (staleness, variable broadcasts, cell
  status messages) scales with cell count. UNVERIFIED (inferred; not profiled).
- Recommendation for about 45 cells: imports, constants (`RUN_TAG`, key prefix, TTL) and at most a
  handful of one-line helpers in `app.setup`; `@app.function` only for the few pure helpers that unit
  tests should import (say five or fewer); everything not about Redis Iris (sample content, the
  embedding model wrapper, display glue) in the `src/` package. Cells that make Iris calls stay
  visible cells, since that is the teaching content.

### 4.4 `marimo check --strict` behaviour

`marimo check [--fix] [--strict] [--unsafe-fixes] [--select ...] [--ignore MF004,MF007] [--format full|json] FILES...`
VERIFIED (CLI help). Results VERIFIED (run):
- One-line `mo.md("# one line title")` -> `warning[markdown-indentation]` (MF007). Plain `check`
  exits 0; `check --strict` exits 1. `check --fix` rewrites it. The triple-quoted form passes:
  ```python
  mo.md(r"""
  # 1. Redis Search
  ...
  """)
  ```
- A cell that defines nothing but does something (`print("defines nothing")`) -> no finding.
- A cell containing only `pass` / comments / whitespace -> `warning[empty-cells]` (MF004),
  exit 1 with `--strict`; removal needs `--fix --unsafe-fixes`.
- A notebook with setup functions, `@app.function(column=0, hide_code=True)`, `mo.stop`, and a
  `test_` cell passed `--strict` (exit 0).
- Rule set in the installed package: MF001 general formatting, MF002/MF003 stdout/stderr during load,
  MF004 empty cells, MF005 SQL parse, MF006 misc, MF007 markdown indentation (plus MB*/MR* breaking and
  runtime rules). VERIFIED (source `_lint/rules/formatting/__init__.py`).

---

## 5. Testing headless without credentials

### 5.1 `App.run()`

`App.run(self, defs: dict[str, Any] | None = None, **kwargs) -> tuple[Sequence[Any], Mapping[str, Any]]`
VERIFIED (signature; run):
- `outputs` is a tuple with one entry per non-disabled cell in file order (setup excluded):
  `(None, callout(), None)` for a 3-cell notebook. `defs` maps every global name, including setup
  names (`['dotenv_val', 'mo', 'os', 'preset_val', 'src_import', 'sys']`).
- A raising cell propagates its **original** exception type out of `app.run()`
  (`RuntimeError: boom from cell`), so "no cell errored" is simply "app.run() returned".
- `mo.stop` does not raise out of `app.run()`; the stopped cell's output is the stop output and its
  descendants' names are absent from `defs`.
- No `.env` and no `pythonpath` are applied (1.2), so `app.run()` in pytest is naturally the
  "no credentials" path; if a live test ever needs them, use `uv run --env-file .env pytest`.
- Prior brief: `app.run()` also works when called inside a running event loop (new in 0.25.0).

### 5.2 pytest integration

- A cell named `test_*` (`@app.cell def test_x(dep): ...`) or a cell containing **only** `test_`
  functions / `Test*` classes / `@pytest.fixture` functions is collected by `pytest notebook.py`;
  mixed cells are skipped; fixtures are visible across cells only if defined in setup or `conftest.py`.
  VERIFIED (docs `pytest.md`; run: `pytest nb_struct.py` -> 1 passed, `pytest test_cells_nb.py` -> 2
  passed with pytest 8.4.2). The notebook also runs these tests reactively in the editor
  (`runtime.reactive_tests`, default true). Latest pytest is 9.1.1 (PyPI, 2026-06-19); UNVERIFIED with
  9.1.1.
- Example (VERIFIED (run) against the temp notebooks; adapt names):
  ```python
  # tests/test_notebook_headless.py
  import sys
  from pathlib import Path

  import pytest

  sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root holds iris_basics.py

  CREDENTIAL_NAMES = ("REDIS_URL", "LANGCACHE_HOST", "LANGCACHE_CACHE_ID", "LANGCACHE_API_KEY",
                      "CTX_ADMIN_KEY", "MEMORY_API_BASE_URL", "MEMORY_STORE_ID", "MEMORY_API_KEY")

  @pytest.fixture
  def no_credentials(monkeypatch):
      for name in CREDENTIAL_NAMES:
          monkeypatch.delenv(name, raising=False)

  def test_notebook_runs_without_credentials(no_credentials):
      import iris_basics

      outputs, defs = iris_basics.app.run()      # raises if any cell raised
      assert len(outputs) > 0
      assert "rs_help_articles" in defs          # seed data still defined
      assert "rs_index_client" not in defs       # gated by mo.stop when REDIS_URL is missing
  ```
  Run: `uv run pytest -q` (dev group) or `uv run --python 3.13 --no-project --with marimo==0.25.0 --with pytest==9.1.1 pytest -q tests`.

### 5.3 CLI exit codes

- `marimo export html nb.py -o out.html [-f]`: a raising cell prints
  `MarimoExceptionRaisedError: ...` then `Error: Export was successful, but some cells failed to
  execute.` and exits **1**, while still writing the file (25 KB written in the test); no error ->
  exit 0. VERIFIED (run). Options: `--include-code/--no-include-code`, `--watch`, `--sandbox/--no-sandbox`,
  `-f/--force`. `--no-sandbox` is only needed when the notebook carries a PEP 723 header (prior brief).
- `marimo check`: exit 0 with warnings, 1 with `--strict` and any warning (4.4).
- `marimo edit --headless` exists (no browser launch); `marimo run` defaults to `--no-token`,
  `marimo edit` to `--token` (CLI help; prior brief).

---

## 6. `width="columns"`

- `marimo.App(width="columns", app_title=..., css_file=..., html_head_file=..., layout_file=..., auto_download=[], sql_output="auto")`;
  `WidthType = Literal["normal", "compact", "medium", "full", "columns"]`; `_AppConfig.width` default
  `"compact"`. VERIFIED (source `_ast/app_config.py`, `_config/config.py:123`).
- Column assignment: `@app.cell(column=N, ...)`, `@app.function(column=N, ...)`,
  `@app.class_definition(column=N, ...)`; the setup cell has `column=None`. Columns are layout only;
  cross-column references are ordinary graph edges. VERIFIED (signatures; prior brief browser). Every
  cell in the current notebook carries an explicit `column=` (28/25/28/31 for columns 0-3), which is
  the safe convention; a cell without `column=` inherits the previous cell's column in file order
  (prior brief, browser; not re-verified). Run mode groups cells by index and sorts; edit mode
  allocates `max(column)+1` columns, so gaps make empty columns (prior brief, frontend source).
- **Known bug, still open today:** issue #3543 "Switching from columns view to another view loses
  column metadata" (opened 2025-01-23, 20 comments, last activity 2026-09-11). Fix PRs #10797
  (updated 2026-09-16) and #10991 (updated 2026-09-25) are both **open, unmerged**. Latest analysis in
  the thread: `getCellConfigs()` in `frontend/src/core/cells/cells.ts` overwrites `column` with `null`
  whenever the editor is not in multi-column view, so saving after switching the width away from
  columns drops every `column=`. A 2025-10-13 comment adds that toggling to app view inside the
  editor shows a single vertical column until the browser is refreshed. VERIFIED (GitHub API).
  Rule for the MVP: never change the width in the editor's settings; keep `check --strict` in CI and
  a test that asserts the four `column=` groups exist in the file.
- Default column width in `marimo run`: the installed CSS sets `--content-width: 740px`
  (`--content-width-medium: 1110px`); each column is one content width, so four columns need about
  3100 px. VERIFIED (grep of `_static/assets/*.css`; prior brief measured 740 px in the browser). No
  `marimo run` flag sets width (CLI help). `display.default_width` only seeds new notebooks and
  pyodide (`_server/app_defaults.py`, `_pyodide/bootstrap.py`); `App(width=...)` wins. VERIFIED
  (source). The only 0.25.0 way to narrow columns is CSS: `App(width="columns", css_file="iris_basics.css")`
  with `:root { --content-width: 480px; }` (prior brief: spacing went from 769 px to 489 px), or the
  project-level `[tool.marimo.display] custom_css = ["iris_basics.css"]` (config field VERIFIED; its
  effect in run mode UNVERIFIED).
- New cell option `expand_output=True` (0.25.0). VERIFIED (signature).

---

## Recommendations for the MVP (summary)

1. Configuration: `pyproject.toml` at the repo root with `[tool.marimo.runtime] dotenv = [".env"]`;
   ship `.env.example`; read only `os.environ[...]` inside connect cells; one `mo.callout(kind="warn")`
   + `mo.stop` per column when names are missing. No `mo.ui.text(kind="password")` anywhere (the
   Variables panel would show `.value`).
2. Package: `src/<pkg>/` installed editable through `uv_build`; no `runtime.pythonpath`, so pytest and
   `python iris_basics.py` see the same modules as the editor. Enable `auto_reload = "lazy"`.
3. Tests: `marimo check --strict iris_basics.py`; a pytest that imports `iris_basics` and calls
   `app.run()` with the eight credential names deleted; `pytest iris_basics.py` picks up any `test_`
   cells; optional `marimo export html` smoke (exit 1 on any cell error).
4. UI: `mo.ui.run_button` + `mo.stop` for seed/clean-up; `mo.ui.table(..., pagination=False,
   selection=None, show_download=False)` for the 36 articles; `mo.json(...)` for request/response;
   `mo.accordion` to fold raw payloads; `mo.stat` for hit counts and latency; `mo.mermaid` per column;
   `mo.status.spinner` around network calls; `@mo.cache` on the embedding-model loader. Avoid
   `mo.ui.chat` (broken) and `mo.ui.form` for repeat-submits.
5. Markdown cells always triple-quoted (`mo.md(r"""...""")`) so `--strict` passes; no `pass`-only cells.
6. Never switch the notebook width in the editor (#3543). Add `iris_basics.css` with a narrower
   `--content-width` only if four 740 px columns are too wide for the presenting screen.

## Open questions

- Whether `_underscore` cell-local names are excluded from the Variables-panel broadcast (matters only
  if a secret is ever bound to a local).
- Module autoreload actually re-running cells on `src/` edits in the editor (classification verified,
  end-to-end not run).
- `mo.ui.table` with 36 rows and `pagination=False` inside a 740 px column: layout unverified without
  a browser; `max_height=` / `wrapped_columns=` are available.
- `[tool.marimo.display] custom_css` in `marimo run` as an alternative to `App(css_file=...)`.
- pytest 9.1.1 compatibility with marimo's collector (8.4.2 verified).
- Whether the docs' claim that `pythonpath` applies to script mode is a doc bug or a regression in
  0.25.0; either way the editable install sidesteps it.
