"""A small in-memory stand-in for Redis 8 with Search, for tests that must not need a server.

It speaks RESP2 (bytes, flat lists) for exactly the commands the Redis Search column sends.
test_redis_search.py uses it in-process, and in a child process that runs the real
`marimo export` (TestRealRuntime), which is why it has a module of its own.
"""

import fnmatch
import math
import re

import redis

# What RediSearch ignores by default, and a crude stemmer: enough to decide whether two
# texts share a word once the server has stemmed them.
STOPWORDS = set(
    "a is the an and are as at be but by for if in into it no not of on or such that their "
    "then there these they this to was will with".split()
)


def stem(word):
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def stems(text):
    return [stem(w) for w in re.findall(r"[^\W_]+", text.lower()) if w not in STOPWORDS]


def within_one_edit(a, b):
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) <= 1
    a, b = sorted((a, b), key=len)
    return any(b[:i] + b[i + 1 :] == a for i in range(len(b)))


# How Redis words an index that does not exist. Redis 8.6.2 (seen live on 2026-09-30) answers
# FT.INFO and FT.DROPINDEX with the first; Search before 8.6 said the second.
MISSING_INDEX = {
    "8.6": "SEARCH_INDEX_NOT_FOUND Index not found: {name}",
    "older": "Unknown index name",
}


def missing_index(name, wording="8.6"):
    """The ResponseError a Redis of that vintage raises for an index it does not have."""
    return redis.ResponseError(MISSING_INDEX[wording].format(name=txt(name)))


def txt(value):
    return value.decode() if isinstance(value, bytes) else str(value)


class FakeSearch:
    """Redis 8 with Search, just enough of it. Every reply has the RESP2 shape.

    Hashes, TTLs and indexes are kept. FT.SEARCH understands the queries this column sends:
    a union of words (exact, `prefix*`, `%fuzzy%`), KNN with an optional TAG and NUMERIC
    pre-filter, and VECTOR_RANGE. Text scores are a small BM25-like sum and distances are
    exact. `sent` records every command that reached the server, blobs and all.
    """

    def __init__(self, version="8.4.0", search=True, hybrid=True, wording="8.6"):
        self.version, self.search, self.hybrid = version, search, hybrid
        self.wording = wording  # how a missing index is worded: a key of MISSING_INDEX
        self.hashes, self.ttls, self.indexes, self.sent = {}, {}, {}, []

    # ---- plumbing -------------------------------------------------------------------
    def handle(self, args):
        self.sent.append(tuple(args))
        name = txt(args[0]).upper()
        command = {
            "PING": lambda: True,
            "INFO": self.info,
            "DBSIZE": lambda: len(self.hashes),
            "SCAN": self.scan,
            "TTL": self.ttl,
            "HSET": self.hset,
            "EXPIRE": self.expire,
            "UNLINK": self.unlink,
            "FT._LIST": self.ft_list,
            "FT.CREATE": self.ft_create,
            "FT.INFO": self.ft_info,
            "FT.DROPINDEX": self.ft_dropindex,
            "FT.SEARCH": self.ft_search,
            "FT.HYBRID": self.ft_hybrid,
        }.get(name)
        if command is None:
            raise redis.ResponseError(f"unknown command '{name}'")
        return command(*args[1:])

    def names(self):
        """The upper-cased name of every command received."""
        return [txt(argv[0]).upper() for argv in self.sent]

    def writes(self):
        """(command, target) for every write that reached the server."""
        writes = {"HSET", "EXPIRE", "UNLINK", "FT.CREATE", "FT.DROPINDEX"}
        return [(txt(a[0]).upper(), txt(a[1])) for a in self.sent if txt(a[0]).upper() in writes]

    def index(self, name):
        if txt(name) not in self.indexes:
            raise missing_index(name, self.wording)
        return self.indexes[txt(name)]

    def add_index(self, name, prefix, **more):
        self.indexes[name] = {"prefixes": [prefix], **more}

    # ---- core commands (replies as redis-py hands them over, after its callbacks) -------
    def info(self, section):
        if txt(section) == "server":
            return {"redis_version": self.version}
        return {"connected_clients": 3}

    def scan(self, _cursor, _match, pattern, _count, _n):
        keys = [k for k in self.hashes if fnmatch.fnmatchcase(k, txt(pattern))]
        return 0, [k.encode() for k in keys]

    def ttl(self, key):
        key = txt(key)
        return -2 if key not in self.hashes else self.ttls.get(key, -1)

    def hset(self, key, *pairs):
        fields = self.hashes.setdefault(txt(key), {})
        added = 0
        for name, value in zip(pairs[::2], pairs[1::2], strict=True):
            added += txt(name) not in fields
            fields[txt(name)] = value if isinstance(value, bytes) else txt(value).encode()
        return added

    def expire(self, key, seconds):
        self.ttls[txt(key)] = int(seconds)
        return 1

    def unlink(self, *keys):
        gone = [txt(k) for k in keys if txt(k) in self.hashes]
        for key in gone:
            del self.hashes[key]
            self.ttls.pop(key, None)
        return len(gone)

    # ---- the search module -----------------------------------------------------------
    def ft_list(self):
        if not self.search:
            raise redis.ResponseError("unknown command 'FT._LIST'")
        return [name.encode() for name in self.indexes]

    def ft_create(self, name, *rest):
        tokens = [txt(t) for t in rest]
        if txt(name) in self.indexes:
            raise redis.ResponseError("Index already exists")
        at = tokens.index("PREFIX")
        vector = tokens.index("VECTOR")
        self.add_index(
            txt(name),
            *tokens[at + 2 : at + 2 + int(tokens[at + 1])],
            algorithm=tokens[vector + 1],
            metric=tokens[tokens.index("DISTANCE_METRIC") + 1],
            dim=int(tokens[tokens.index("DIM") + 1]),
        )
        return b"OK"

    def docs(self, index):
        return {
            key: fields
            for key, fields in self.hashes.items()
            if any(key.startswith(p) for p in index["prefixes"])
        }

    def ft_info(self, name):
        index = self.index(name)
        prefixes = [p.encode() for p in index["prefixes"]]
        algorithm = index.get("algorithm", "HNSW").encode()
        return [
            b"index_name", txt(name).encode(),
            b"index_definition", [b"key_type", b"HASH", b"prefixes", prefixes],
            b"attributes", [[b"identifier", b"vec", b"type", b"VECTOR", b"algorithm", algorithm]],
            b"num_docs", str(len(self.docs(index))).encode(),
            b"vector_index_sz_mb", b"0.045",
            b"total_index_memory_sz_mb", b"0.31",
            b"hash_indexing_failures", b"0",
            b"indexing", b"0",
            b"percent_indexed", b"1",
        ]  # fmt: skip

    def ft_dropindex(self, name, *rest):
        assert not [r for r in rest if txt(r).upper() == "DD"], "DD reached the server"
        self.index(name)
        del self.indexes[txt(name)]
        return b"OK"

    @staticmethod
    def options(rest):
        """The trailing FT.SEARCH options this column uses."""
        opts, i = {"params": {}, "limit": (0, 10), "return": None, "scored": False}, 0
        while i < len(rest):
            word = txt(rest[i]).upper()
            if word == "PARAMS":
                n = int(rest[i + 1])
                pairs = rest[i + 2 : i + 2 + n]
                opts["params"] = {txt(k): v for k, v in zip(pairs[::2], pairs[1::2], strict=True)}
                i += 2 + n
            elif word == "LIMIT":
                opts["limit"] = (int(rest[i + 1]), int(rest[i + 2]))
                i += 3
            elif word == "RETURN":
                n = int(rest[i + 1])
                opts["return"] = [txt(f) for f in rest[i + 2 : i + 2 + n]]
                i += 2 + n
            else:
                opts["scored"] |= word == "WITHSCORES"
                i += 1
        return opts

    @staticmethod
    def distance(index, blob, fields):
        import numpy as np

        a = np.frombuffer(blob, dtype=np.float32)
        b = np.frombuffer(fields["vec"], dtype=np.float32)
        assert len(a) == index["dim"] == len(b), "a vector of the wrong length"
        if index["metric"] == "L2":
            return float(((a - b) ** 2).sum())
        return float(1 - a @ b)

    @staticmethod
    def passes(prefilter, fields):
        categories = re.search(r"@category:\{([^}]*)\}", prefilter)
        if categories and txt(fields["category"]) not in categories[1].split("|"):
            return False
        years = re.search(r"@year:\[(\d+) (\d+)\]", prefilter)
        return not years or int(years[1]) <= int(txt(fields["year"])) <= int(years[2])

    def bm25(self, index, query):
        """(key, score) for the documents matching a '(a|b*|%c%)' union, best first."""
        terms = [t for t in query.strip("()").split("|") if t]

        def matches(term, token):
            if term.startswith("%"):
                return within_one_edit(stem(term.strip("%")), token)
            if term.endswith("*"):
                return token.startswith(term[:-1])
            return stem(term) == token

        def weight(term, fields):
            title, body = stems(txt(fields["title"])), stems(txt(fields["body"]))
            return 2 * sum(matches(term, t) for t in title) + sum(matches(term, t) for t in body)

        docs = self.docs(index)
        scores = {key: 0.0 for key in docs}
        for term in terms:
            found = {key: weight(term, fields) for key, fields in docs.items()}
            df = sum(1 for w in found.values() if w)
            for key, w in found.items():
                if w:
                    scores[key] += w * math.log(1 + len(docs) / df)
        return sorted(((k, s) for k, s in scores.items() if s), key=lambda ks: (-ks[1], ks[0]))

    def nearest(self, index, blob, prefilter="*"):
        return sorted(
            (self.distance(index, blob, fields), key)
            for key, fields in self.docs(index).items()
            if self.passes(prefilter, fields)
        )

    def ft_search(self, name, query, *rest):
        index, query, opts = self.index(name), txt(query), self.options(rest)
        offset, num = opts["limit"]
        if "=>[KNN" in query:
            prefilter, _, tail = query.partition("=>[KNN")
            k = int(tail.split()[0])
            near = self.nearest(index, opts["params"]["BLOB"], prefilter)
            hits = [(key, d) for d, key in near][:k]
        elif "VECTOR_RANGE" in query:
            radius = float(txt(opts["params"]["R"]))
            near = self.nearest(index, opts["params"]["BLOB"])
            hits = [(key, d) for d, key in near if d <= radius]
        else:
            hits = self.bm25(index, query)
        reply = [len(hits)]
        for key, value in hits[offset : offset + num]:
            shown = {"dist": f"{value:.6f}"}
            fields = [
                item.encode()
                for name in opts["return"] or []
                for item in (name, shown.get(name) or txt(self.hashes[key][name]))
            ]
            score = [f"{value:.6f}".encode()] if opts["scored"] else []
            reply += [key.encode(), *score, fields]
        return reply

    def ft_hybrid(self, name, *rest):
        if not self.hybrid:
            raise redis.ResponseError("unknown command 'FT.HYBRID'")
        index, tokens = self.index(name), list(rest)
        query = txt(tokens[tokens.index("SEARCH") + 1])
        k = int(tokens[tokens.index("K") + 1])
        limit = int(tokens[tokens.index("LIMIT") + 2])
        blob = tokens[tokens.index("PARAMS") + 3]
        text_ranking = [key for key, _ in self.bm25(index, query)][:20]
        vector_ranking = [key for _, key in self.nearest(index, blob)][:k]
        scores = {}
        for ranking in (text_ranking, vector_ranking):
            for rank, key in enumerate(ranking, 1):
                scores[key] = scores.get(key, 0) + 1 / (60 + rank)
        fused = sorted(scores, key=lambda key: (-scores[key], key))[:limit]
        return [
            b"total_results", len(fused),
            b"results", [[b"__key", key.encode()] for key in fused],
            b"warnings", [],
            b"execution_time", b"0.5",
        ]  # fmt: skip
