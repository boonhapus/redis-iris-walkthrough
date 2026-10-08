# am-polish docs implementer report

Files edited: `README.md`, `.env.example`. Both are LF files (`git ls-files --eol` shows i/lf w/lf), and LF was preserved. Nothing else was touched.

## README edits (all 14 applied)

1. "The Search, LangCache, and Agent Memory chats call Claude (`claude-opus-5-5`)." -> "...call the model named by `AI_MODEL` in column 0, `gpt-4o-mini` at first."
2. "A LangCache miss stores Claude's answer, and each Agent Memory message writes two session events..." -> "...stores the model's answer, and each Agent Memory message writes one session event, the message itself."
3. "- A Claude API key, or an `ant auth login` profile..." -> "- An LLM API key for the three chats: an OpenAI key when `AI_MODEL` starts with `gpt`, an Anthropic key..."
4. "Without a Claude credential the Claude client still builds..." -> "Without an LLM key the client still builds, and each chat turn reports the failed model call in the chat."
5. "| `ANTHROPIC_API_KEY` | none | ..." -> "| `LLM_API_KEY` | none | ... An OpenAI key when `AI_MODEL` starts with `gpt`, or an Anthropic key..."
6. Deleted "The Anthropic SDK also accepts a profile from `ant auth login`..." and the blank line after it.
7. "uv run marimo run iris_basics.py" -> "uv run marimo run --include-code iris_basics.py", plus blank line and "The code stays folded until you pick **Show code** from the app menu." after the fence.
8. "...the hosted services, or Claude." -> "...the hosted services, or the LLM."
9. Step 7 body (from "In the Redis Agent Memory column, press **Write 5 memories about Dana..." to the end of Step 7) -> the new Seed/workbench text from the spec. The "## Step 7" heading and one blank line before "## Step 8. Clean up" are kept.
10. Step 8 bullet "- Redis Agent Memory: **Clean up: DELETE this owner's sessions and memories**..." -> "- Redis Agent Memory: **Clean up** deletes every session and every long-term memory tagged Tom or Priya..."
11. "After chatting with the Cairn agent, press Agent Memory's button again ten minutes later..." -> "After chatting, press **Refresh** a few minutes later... Clean up deletes those too."
12. "| `llm_client` | `LLM_MODEL` (`claude-opus-5-5`) and `ask_llm` | ..." -> "| `llm_client` | `stream_llm`, which streams from OpenAI or Anthropic according to `AI_MODEL` | `LLM_API_KEY` |"
13. Troubleshooting "Verify that `ANTHROPIC_API_KEY` is set and valid..." -> "Verify that `LLM_API_KEY` is set and valid in `.env` and matches the provider of `AI_MODEL`..."
14. "The first real chat turns need `ANTHROPIC_API_KEY` or an `ant auth login` profile." -> "The first real chat turns need `LLM_API_KEY`."

## .env.example

Replaced the three-line Claude section ("# ---- Claude (the chats...") with the "# ---- LLM (the chats ..." section and `# LLM_API_KEY=`, as in the spec.

## Grep

`grep -n "ANTHROPIC_API_KEY\|ant auth\|Dana\|two session events" README.md .env.example` found ONE match, not zero:

- `README.md:386`, in "How it works": the example JSON for "Five memories in one Agent Memory write", which contains "Dana is allergic to peanuts...".

`ANTHROPIC_API_KEY`, `ant auth`, and "two session events" are gone from both files.

## Deviations

- The Dana match above remains. It sits in the older-notebook "How it works" section, which none of the 14 edits cover, and the spec's grep expectation did not account for it. I left it rather than rewrite content outside my edit list. The fix is a rewrite of that example (and the related `cairn-<run>-m<n>` row and Agent Memory rows of the scope table) once someone decides how far to update "How it works".
- Edit 9: the spec's replacement text ends the paragraph about the second customer's bars with "without the `ownerId` filter's effect". I used it verbatim, though the phrasing is awkward. The reviewer may want to check it against the live column.
- `git diff --stat` also lists `iris_basics.py` (251 lines changed). That is the live kernel saving the notebook (code implementer's work), not me.

## Note

README Steps 4 to 6 and "How it works" still describe the older notebook (Claude, `cairn-<run>`, `call_service`, `ask_llm`, `ANTHROPIC` text such as "Claude gets the top three articles", the "Verified live" section, the mermaid diagram, the key table and the scope table's Agent Memory row). That is outside this task.
