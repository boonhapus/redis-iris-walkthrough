"""Voice rules over every `mo.md` string in the notebook: sentence length, serial commas, no
semicolons, no exclamation marks, no "developers", no trial link, lowercase hit and miss in prose.

Fenced code, inline code, link targets, and bare URLs are stripped before a rule runs, and table
rows (lines starting with `|`) are not sentences. A failure here is fixed in the copy, not in the
test, unless it is a demonstrable false positive (VOICE.md is the reference).

The file under test is iris_basics.py unless IRIS_BASICS_NOTEBOOK names another file.
"""

import ast
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = Path(os.environ.get("IRIS_BASICS_NOTEBOOK", ROOT / "iris_basics.py"))
if not NOTEBOOK_PATH.is_absolute():
    NOTEBOOK_PATH = ROOT / NOTEBOOK_PATH
SOURCE = NOTEBOOK_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(NOTEBOOK_PATH))

WORD_CEILING = 30  # the plan's own ceiling is 25; 30 leaves room for a backticked command counted as words

# `X`, `Y` and `Z`: three backticked items without the serial comma.
BACKTICKED_LIST = re.compile(r"`[^`\n]+`, `[^`\n]+` and `[^`\n]+`")
# "keys, entries and surfaces." : a short second item, then "and", then a short third item ending
# the sentence or lead-in. Conjunctions after the comma are clauses, not items.
PLAIN_LIST = re.compile(
    r"\b[\w-]+, (?!(?:and|or|but|so|then|because|which|not|while|where|though|since|as|if)\b)"
    r"[\w-]+(?: [\w-]+){0,3} and [\w-]+(?: [\w-]+){0,3}[.:]"
)


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _literal_text(node):
    """The text of a string constant or an f-string, with each interpolation replaced by a word."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            str(part.value) if isinstance(part, ast.Constant) else "value" for part in node.values
        )
    return None


def _md_strings():
    strings = []
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and _dotted(node.func) == "mo.md" and node.args:
            text = _literal_text(node.args[0])
            if text is not None:
                strings.append(text)
    return strings


def _prose(text):
    """Markdown with code, link targets, and URLs removed."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r"\]\([^)]*\)", "]", text)
    text = re.sub(r"https?://\S+", " ", text)
    return text


def _prose_lines(text):
    return [line.strip() for line in _prose(text).splitlines() if not line.strip().startswith("|")]


def _sentences(text):
    paragraphs, current = [], []
    for line in _prose_lines(text):
        if line:
            current.append(line)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    for paragraph in paragraphs:
        paragraph = re.sub(r"(?<=\d)\.(?=\d)", "<dot>", paragraph)   # 8.4 and 0.85 are one token, not a sentence end
        for sentence in re.split(r"[.:?]", paragraph):
            words = [token for token in sentence.split() if re.search(r"[A-Za-z0-9]", token)]
            if words:
                yield sentence.replace("<dot>", ".").strip(), len(words)


MD_STRINGS = _md_strings()


def test_notebook_has_markdown():
    assert MD_STRINGS, "no mo.md string found; the voice rules have nothing to check"


def test_no_sentence_over_30_words():
    for text in MD_STRINGS:
        for sentence, count in _sentences(text):
            assert count <= WORD_CEILING, f"{count} words: {sentence}"


def test_serial_comma_in_three_item_lists():
    for text in MD_STRINGS:
        match = BACKTICKED_LIST.search(text)
        assert not match, f"three backticked items without the serial comma: {match.group(0)}"
        for line in _prose_lines(text):
            match = PLAIN_LIST.search(line)
            assert not match, f"three items without the serial comma: {match.group(0)}"


def test_no_semicolon_in_prose():
    for text in MD_STRINGS:
        for line in _prose_lines(text):
            assert ";" not in line, f"semicolon in prose (use a full stop): {line}"


def test_no_exclamation_marks():
    for text in MD_STRINGS:
        assert "!" not in _prose(text), f"exclamation mark in: {text[:80]!r}"


def test_no_developers_in_prose():
    for text in MD_STRINGS:
        assert not re.search(r"\bdevelopers\b", _prose(text), flags=re.I), "say 'you', 'devs', or 'teams'"


def test_no_try_free_link():
    assert "redis.io/try-free" not in SOURCE, "a tutorial carries no Redis Cloud CTA"


def test_hit_miss_lowercase_in_prose():
    for text in MD_STRINGS:
        for line in _prose_lines(text):
            match = re.search(r"\b(HIT|MISS)\b", line)
            assert not match, f"uppercase {match.group(0) if match else ''} in prose (table cells and callout titles only): {line}"
