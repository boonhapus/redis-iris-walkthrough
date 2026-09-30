# Column fragments (build-time only)

This folder exists between the scaffold commit and the assembly commit. Four builders each write
one fragment here, in parallel, on the scaffold commit; the assembly step inserts them into
`iris_basics.py` and deletes the folder. The contract is
`.agents/plans/iris-basics-mvp-plan.md`: Revision 3 first, then section 17, then your column's
section (8, 9, 10 or 11) and the shared patterns in section 7.

## Files

| Column | File | Cells (plan section) |
|---|---|---|
| 0 | `column0_redis_search.py` | `rs_connect` .. `rs_cleanup` (section 8, cells 2-12) |
| 1 | `column1_langcache.py` | `lc_configure` .. `lc_cleanup` (section 9, cells 2-12) |
| 2 | `column2_context_retriever.py` | `cr_configure` .. `cr_cleanup` (section 10, cells 2-11) |
| 3 | `column3_agent_memory.py` | `am_configure` .. `am_cleanup` (section 11, cells 2-11) |

Each builder also writes `readme_columnN.md`: the README Step section for the column (heading,
button to press, literal output), in the voice guide's tutorial register
(`../../../.agents/VOICE.md`). The assembly step pastes it over the `<!-- column N -->` marker in
`README.md`.

## Fragment rules (plan section 17, verbatim)

- A fragment is UTF-8, LF, and contains only `@app.cell(column=N ...)`-decorated functions in
  marimo's saved format (`def name(dep, ...):` body `return (defs,)`), separated by two blank
  lines. No module-level code, no imports outside a cell body, no `app`, no setup block, no title
  cell (the scaffold owns it), no footer.
- Every cell in the fragment carries the same `column=N`; markdown-only cells add `hide_code=True`.
- Every global the fragment defines carries the column prefix (`rs_`, `lc_`, `cr_`, `am_`), except
  the unprefixed names each column **owns** below. Cell-local names start with `_`. Two fragments
  defining the same global is MB002 (marimo lint `MultipleDefinitionsRule`), which
  `marimo check --strict` rejects and which stops both cells from running, so ownership is
  exclusive:
  - column 0 owns `NumericField`, `TagField`, `TextField`, `VectorField`, `IndexDefinition`,
    `IndexType`, `Query`, `ResponseError` (imported at the top of `rs_connect`);
  - column 2 owns `unquote`, `urlsplit` (imported in `cr_configure`), `Any`, `ContextField`,
    `ContextModel`, `ContextRelationship`, `export_data_model` (imported in `cr_data_model`), and
    the classes `Customer`, `Product`, `Order`;
  - columns 1 and 3 own no unprefixed name and contain no `import` statement.
  No fragment imports a name another column owns. Every column other than 0 reaches redis-py
  exceptions as `redis.ResponseError` and `redis.RedisError` through the setup's `redis` module,
  and no column re-imports anything the setup block already provides (`json`, `os`, `re`, `sys`,
  `time`, `hashlib`, `getpass`, `platform`, `datetime`, `timedelta`, `timezone`, `mo`, `niquests`,
  `np`, `redis`). Section 17 lists `uuid` here; R3.1 removed it from the setup block.
  `tests/test_safety.py::test_import_ownership` enforces all of this.
- The fragment reads only setup names (section 4), `cairn.*`, `redis_cli`, `shared_stems`,
  `EMBEDDING_MODEL_NAME`, `embed_texts`, `load_embedding_model`, and its own column's globals. It
  never reads another column's globals.
- Every `mo.md` string in the fragment passes `tests/test_voice.py`: lead-ins of at most two
  sentences of at most 25 words, colon-terminated; no semicolon; serial commas; lowercase "hit"
  and "miss" in prose.
- ~~The builder checks the fragment by running `uv run python .agents/fragments/assemble.py`
  (which inserts whatever fragments exist and leaves the other markers in place), then
  `uv run marimo check --strict iris_basics.py` and `uv run pytest`. `git checkout iris_basics.py`
  afterwards.~~ (void: R3.3 replaces this with the `--output` procedure below; a builder never
  writes `iris_basics.py`.) The builder commits only the fragment and its README piece.
- A builder never edits the scaffold, `src/`, `tests/` or another fragment. A needed change there
  is a note in the fragment's commit message for the assembly step.

Both strikes come from Revision 3: R3.1 derives `RUN_TAG` from `hashlib`, `getpass`, and
`platform` instead of `uuid`, and R3.3 replaces the in-place check with the procedure below.
Builders never write `iris_basics.py`.

## How a builder checks a fragment (Revision 3, R3.3, verbatim)

```
uv run python .agents/fragments/assemble.py --output .agents/fragments/preview_columnN.py
uv run marimo check --strict .agents/fragments/preview_columnN.py
IRIS_BASICS_NOTEBOOK=.agents/fragments/preview_columnN.py uv run pytest tests/test_safety.py tests/test_voice.py tests/test_notebook.py -k "not budget and not columns_are_complete"
```

(PowerShell: `$env:IRIS_BASICS_NOTEBOOK='...'; uv run pytest ...`.) The tests locate the notebook
through `NOTEBOOK_PATH = Path(os.environ.get("IRIS_BASICS_NOTEBOOK", ROOT / "iris_basics.py"))`
and import it with `importlib.util.spec_from_file_location("iris_basics_under_test", NOTEBOOK_PATH)`;
`test_marimo_check_strict` checks `str(NOTEBOOK_PATH)`. In `test_notebook_runs_without_credentials`
the `cr_data_model` and `cr_record_keys` assertions run only when the file under test contains
`def cr_data_model`; the not-configured assertions apply to whichever configure cells the file
contains. `.gitignore` gains `.agents/fragments/preview_*.py`. The rule "`git checkout
iris_basics.py` afterwards" in section 17 is void: a builder has no reason to touch the notebook.

## For the assembly step (plan section 17, step 2.3)

Besides pasting the four `readme_columnN.md` pieces over the `<!-- column N -->` markers, fill
the two `<repository URL>` placeholders in `README.md` (the Note under the TL;DR and the
`git clone` line in Step 1) with the real remote. The scaffold has no remote configured, so they
ship as visible placeholders until then.

## The concatenation rule in one sentence

The assembled notebook is the scaffold with each `# @@ COLUMN N CELLS @@` line replaced by the
whole content of fragment N, so every column's cells follow that column's title cell and the file
order is column 0, 1, 2, 3.
