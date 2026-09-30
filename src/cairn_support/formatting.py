"""Two formatting helpers with no Redis or Iris knowledge: a redis-cli renderer for argument
lists, and a crude stemmer for showing which words two texts share.
"""

import re

_STOPWORDS = frozenset(
    "a an and are as at be but by for if in into is it no not of on or such that the their "
    "then there these they this to was will with".split()
)
_SUFFIXES = ("ing", "ed", "es", "s")


def redis_cli(argv: list) -> str:
    """One command in redis-cli syntax, long values elided.

    Tokens are str()'d; a token with a space or a quote is double-quoted; bytes longer than 64
    are shown as "<N bytes>"; text longer than 120 characters is cut with a "<N chars>" marker.
    A line with an elided value is for reading, not pasting.
    """
    tokens = []
    for argument in argv:
        if isinstance(argument, (bytes, bytearray)):
            if len(argument) > 64:
                tokens.append(f"<{len(argument)} bytes>")
                continue
            text = bytes(argument).decode("utf-8", "replace")
        else:
            text = str(argument)
        if len(text) > 120:
            text = text[:60] + f"...<{len(text)} chars>"
        needs_quotes = not text or " " in text or '"' in text
        tokens.append('"' + text.replace('"', '\\"') + '"' if needs_quotes else text)
    return " ".join(tokens)


def _stems(text: str) -> list[str]:
    stems = []
    for word in re.findall(r"[^\W_]+", text.lower()):
        if word in _STOPWORDS:
            continue
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)]
                break
        stems.append(word)
    return stems


def shared_stems(question: str, memory: str) -> list[str]:
    """The word stems two texts share, sorted; [] is the 'recalled by meaning' evidence.

    Lower-cases, drops a short stopword list, cuts -ing, -ed, -es or -s when at least three
    letters remain. Crude on purpose.
    """
    return sorted(set(_stems(question)) & set(_stems(memory)))
