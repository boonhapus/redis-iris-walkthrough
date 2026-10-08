# Stage 4b report: stamp column= decorators and relaunch (2026-10-07)

## 1. Server process (recorded before stopping)

| PID | Parent | Executable | Command line |
|---|---|---|---|
| 21640 | 7688 (powershell.exe) | `C:\Users\NickCooper\.local\bin\uv.exe` | `"C:\Users\NickCooper\.local\bin\uv.exe" run --env-file .env marimo edit --host 0.0.0.0 --port 2718 --no-token --watch iris_basics.py` |
| 11032 | 21640 | `.venv\Scripts\marimo.exe` | `marimo.exe edit --host 0.0.0.0 --port 2718 --no-token --watch iris_basics.py` |
| 7172 | 11032 | `.venv\Scripts\python.exe` | same marimo.exe args |
| 2384 | 7172 | uv-managed cpython-3.13 `python.exe` | same marimo.exe args |

Relaunch command (the uv line), working directory `C:\Users\NickCooper\projects\demo\redis-iris-basics`. Launched with `uv.exe` as a full path, same arguments. The old window's working directory was inferred from the project folder (Win32_Process does not expose it). msedge processes were running; I could not tell whether they were a headless verifier or Nick's browser, so I left them alone.

## 2. Disk file had the live cells
`grep` listed am_title, am_connect, am_store_md, am_store_button, am_store, am_store_fns, am_probe, am_sweep, am_probe_panel, am_recall_fns, am_owner, am_chat, am_turn_sweep, am_prompt_panel, am_prompt_fns, am_render in that order. The title contains "public preview" (line 193). The chat prompts list contains "I'm training for a marathon in October." (line 531).

## 3. Server stopped
Stopped PIDs 21640, 11032, 7172, 2384. http://127.0.0.1:2718/ then returned "Unable to connect to the remote server".

## 4. Decorators
File is CRLF before and after: 699 CRLF, 0 bare LF. 22 cells; 0 `@app.cell` lines lack `column=`. Only decorators changed (14 lines: redis_client, llm_client, and 12 column-4 cells).

Before:
```
7 | @app.cell(column=0) | _
25 | @app.cell | redis_client
44 | @app.cell | llm_client
87 | @app.cell(column=1, hide_code=True) | rs_title
117 | @app.cell(column=2, hide_code=True) | lc_title
149 | @app.cell(column=3, hide_code=True) | cr_title
180 | @app.cell(column=4, hide_code=True) | am_title
201 | @app.cell | am_connect
221 | @app.cell(hide_code=True) | am_store_md
232 | @app.cell(hide_code=True) | am_store_button
242 | @app.cell(hide_code=True) | am_store
287 | @app.cell | am_store_fns
359 | @app.cell(hide_code=True) | am_probe
381 | @app.cell(hide_code=True) | am_sweep
391 | @app.cell(hide_code=True) | am_probe_panel
434 | @app.cell | am_recall_fns
468 | @app.cell(hide_code=True) | am_owner
486 | @app.cell | am_chat
538 | @app.cell(hide_code=True) | am_turn_sweep
558 | @app.cell(hide_code=True) | am_prompt_panel
607 | @app.cell | am_prompt_fns
638 | @app.cell(hide_code=True) | am_render
```

After:
```
7 | @app.cell(column=0) | _
25 | @app.cell(column=0) | redis_client
44 | @app.cell(column=0) | llm_client
87 | @app.cell(column=1, hide_code=True) | rs_title
117 | @app.cell(column=2, hide_code=True) | lc_title
149 | @app.cell(column=3, hide_code=True) | cr_title
180 | @app.cell(column=4, hide_code=True) | am_title
201 | @app.cell(column=4) | am_connect
221 | @app.cell(column=4, hide_code=True) | am_store_md
232 | @app.cell(column=4, hide_code=True) | am_store_button
242 | @app.cell(column=4, hide_code=True) | am_store
287 | @app.cell(column=4) | am_store_fns
359 | @app.cell(column=4, hide_code=True) | am_probe
381 | @app.cell(column=4, hide_code=True) | am_sweep
391 | @app.cell(column=4, hide_code=True) | am_probe_panel
434 | @app.cell(column=4) | am_recall_fns
468 | @app.cell(column=4, hide_code=True) | am_owner
486 | @app.cell(column=4) | am_chat
538 | @app.cell(column=4, hide_code=True) | am_turn_sweep
558 | @app.cell(column=4, hide_code=True) | am_prompt_panel
607 | @app.cell(column=4) | am_prompt_fns
638 | @app.cell(column=4, hide_code=True) | am_render
```

Note: the first cell (line 7, name `_`) is the intro cell with no name in the file; the spec's `intro` name is not used in the file.

## 5. `uv run python -m marimo check --strict iris_basics.py`
No output, exit code 0.

## 6. `uv run pytest tests -q --ignore=tests/live_smoke.py`
`17 failed, 46 passed in 2.97s`

Failing:
- test_notebook.py: test_credential_free_cells_run_offline (no `intro`), test_column_0_is_the_fixed_global_setup (column 0 holds `_`, redis_client, llm_client), test_intro_table_quotes_each_columns_goal_titles (KeyError `intro`), test_column_budgets, test_try_only_where_a_lesson_needs_it (no `app.setup`... cr_create, rs_cleanup, rs_seed lost try), test_no_status_comparisons (no app.setup block), test_llm_client_contract (LLM_MODEL is "claude-opus-5-5"), test_chat_cells_own_their_controls (chat cells are ['am_chat']), test_claude_is_called_once_per_chat_turn (rs_chat/lc_chat missing), test_setup_block_defines_exactly_the_shared_names (no app.setup block)
- test_safety.py: test_key_templates_and_prefixes_start_with_key_prefix (cr_data_model/rs_key_prefix missing), test_cleanup_deletes_by_name_and_never_scans (each column ends with Clean up cell), test_no_secret_read_bound_to_a_global, test_sessions_are_pinned, test_mcp_session_sends_the_verified_accept_header (cr_* cell missing), test_create_password_is_masked_and_the_reply_never_rendered (cr_create missing), test_import_ownership (`_` in column 0 imports AsyncGenerator)

Stale-suite causes: no intro/app.setup, imports in column 0, rs_chat/lc_chat gone, column budgets, cr_* cells gone.

Possibly caused by this build (not by the stale suite alone), flag for Nick:
- test_no_secret_read_bound_to_a_global: `ams_client` (am_connect) reads `os.environ["MEMORY_API_BASE_URL"|"MEMORY_API_KEY"|"MEMORY_STORE_ID"]` into a global-shaped name. The test only allows reads inside `iris_session`, `niquests.Session`, or `redis.Redis.from_url`.
- test_sessions_are_pinned: the test says `iris_session` is the only place a `niquests.Session` is built; the failure may be the missing `iris_session` rather than am code. Not diagnosed further.
- No voice test failed (8 voice-related tests collected, all passed).

## 7. Relaunch
`Start-Process powershell -WorkingDirectory ... -ArgumentList '-NoExit','-Command','& "C:\Users\NickCooper\.local\bin\uv.exe" run --env-file .env marimo edit --host 0.0.0.0 --port 2718 --no-token --watch iris_basics.py'`. http://127.0.0.1:2718/ answered 200 after 5 s. `execute-code.sh` reached the kernel with no browser step needed (a tab apparently reconnected). `help(cm)` printed.

Cells after relaunch (index, name, status, column, hide_code); all `stale`, none forced to run:
```
0 _ stale 0 False
1 redis_client stale 0 False
2 llm_client stale 0 False
3 rs_title stale 1 True
4 lc_title stale 2 True
5 cr_title stale 3 True
6 am_title stale 4 True
7 am_connect stale 4 False
8 am_store_md stale 4 True
9 am_store_button stale 4 True
10 am_store stale 4 True
11 am_store_fns stale 4 False
12 am_probe stale 4 True
13 am_sweep stale 4 True
14 am_probe_panel stale 4 True
15 am_recall_fns stale 4 False
16 am_owner stale 4 True
17 am_chat stale 4 False
18 am_turn_sweep stale 4 True
19 am_prompt_panel stale 4 True
20 am_prompt_fns stale 4 False
21 am_render stale 4 True
```

## 8. File after reload
Re-checked about 20 s after the cells listing: the decorator listing is identical to section 4 "After" (diff empty), CRLF intact (699/0), file mtime 10:50:00. marimo did not re-save. The known re-save may still occur once a tab edits, moves, or reorders cells; re-check after Nick's first edit.
