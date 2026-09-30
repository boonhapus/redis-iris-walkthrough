"""A small in-memory stand-in for a LangCache service, for tests that must not need a real one.

It speaks the REST API the LangCache column sends: search, set, delete by id, delete by
attributes and flush, with the error bodies the real API documents. test_lc.py uses it
in-process through httpx.MockTransport, and in a child process that runs the real
`marimo export` (TestRealRuntime), which is why it has a module of its own.

A DELETE of an id it no longer holds answers 404 with the type the live service used on
2026-09-30, "/errors/resource-not-found" (the API docs say "/errors/cache/entry/not-found",
which `missing_entry` can be set to). A wrong cache id answers "/errors/cache/not-found".

Similarity is not computed from meaning. `scores` maps a (question, stored prompt) pair, both
lower-cased, to the number a test wants the "model" to return; any other pair scores a low
lexical overlap, and identical text scores 1.0. score_plan() fills the table for the
notebook's own questions.
"""

import json
import re
import time

import httpx


def words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def nearest(text, prompts):
    """The prompt that shares the most words with text (the first, on a tie)."""
    return max(prompts, key=lambda prompt: len(words(text) & words(prompt)))


def score_plan(seeds, probes, variants, script):
    """{(question, stored prompt): similarity} for the notebook's own questions.

    Paraphrases get a spread from 1.0 down to 0.80 against their seed; questions that a cached
    answer would get wrong get 0.87 and down against the seed they resemble most; unrelated
    ones get nothing, so they sit below any floor. Every number is chosen here, so a test can
    say exactly what a threshold should do.
    """
    prompts = [seed["prompt"] for seed in seeds.values()]
    right, wrong = (
        iter([1.0, 0.93, 0.90, 0.88, 0.84, 0.80]),
        iter([0.87, 0.84, 0.82, 0.79, 0.76]),
    )
    plan = {}
    for probe in probes:
        if probe["target"]:
            plan[probe["text"], seeds[probe["target"]]["prompt"]] = next(right)
        elif probe["kind"] != "unrelated":
            plan[probe["text"], nearest(probe["text"], prompts)] = next(wrong)
    bag = seeds["bag"]["prompt"]
    # A change of case is the same text once lower-cased, and identical text scores 1.0 by itself.
    for variant, score in zip(variants, [None, None, 0.98, 0.96, 0.90, 0.78], strict=True):
        if score:
            plan[variant["text"], bag] = score
    plan["How can I get my rain jacket to bead water again?", seeds["shell"]["prompt"]] = 0.91
    # the floor probe's loose question sits between three of the entries
    for key, score in (("tent", 0.71), ("boots", 0.66), ("stove", 0.62)):
        plan["How do I look after my camping gear?", seeds[key]["prompt"]] = score
    plan[script["again"], "At Cairn Outfitters: " + script["ask"]] = 0.91
    return {(q.lower(), p.lower()): score for (q, p), score in plan.items()}


class FakeLangCache:
    """One cache. `entries` holds everything it stores; add_foreign() puts in what another app did.

    floor: the lowest similarity it will ever return, whatever the request asks for.
    attributes: the attribute names the cache defines; with none, any attribute is a 400.
    max_results: the most it returns; 1 stands for a service that ignores maxResults.
    clock: seconds, so a test can make an entry expire without waiting.
    `calls` records every request that reached it as (method, path, body).
    """

    MISSING_ENTRY = "/errors/resource-not-found"  # what a live DELETE of an expired id answered

    def __init__(self, cache_id="cache-abc123", api_key="fake-lc-key-9f3a", *, floor=0.5,
                 attributes=(), max_results=100, clock=time.time):  # fmt: skip
        self.cache_id, self.api_key, self.floor = cache_id, api_key, floor
        self.missing_entry = self.MISSING_ENTRY
        self.attributes, self.cap, self.clock = tuple(attributes), max_results, clock
        self.entries, self.counter, self.scores, self.calls = {}, 0, {}, []
        self.flushed = self.wiped = False

    # ---- state -------------------------------------------------------------------
    def snapshot(self):
        return self.entries, self.counter

    def restore(self, snapshot):
        self.entries, self.counter = snapshot

    def add_foreign(self, prompt, response, attributes=None, ttl_ms=None):
        """An entry that some other app stored; returns its id."""
        return self.put(prompt, response, attributes or {}, ttl_ms)

    def put(self, prompt, response, attributes, ttl_ms):
        self.counter += 1
        entry_id = f"{self.counter:032x}"
        expires = self.clock() + ttl_ms / 1000 if ttl_ms else None
        self.entries[entry_id] = {
            "prompt": prompt, "response": response, "attributes": dict(attributes),
            "ttl_ms": ttl_ms, "expires": expires,
        }  # fmt: skip
        return entry_id

    def live(self):
        now = self.clock()
        self.entries = {
            i: e for i, e in self.entries.items() if e["expires"] is None or e["expires"] > now
        }
        return self.entries

    def similarity(self, question, stored):
        key = (question.lower(), stored.lower())
        if key in self.scores:
            return self.scores[key]
        if key[0] == key[1]:
            return 1.0
        both, either = words(question) & words(stored), words(question) | words(stored)
        return 0.6 * len(both) / len(either) if either else 0.0

    # ---- HTTP --------------------------------------------------------------------
    @staticmethod
    def error(status, kind, title, detail=""):
        body = {"title": title, "status": status, "detail": detail or title, "type": kind}
        return httpx.Response(status, json=body)

    def handle(self, request):
        """httpx.MockTransport's handler: one request in, one Response out."""
        body = json.loads(request.content) if request.content else None
        route = re.fullmatch(r"/v1/caches/([^/]+)(/.*)", request.url.path)
        self.calls.append((request.method, route[2] if route else request.url.path, body))
        if request.headers.get("authorization") != f"Bearer {self.api_key}":
            return self.error(401, "/errors/authentication-failed", "Authentication failed")
        if not route or route[1] != self.cache_id:
            return self.error(404, "/errors/cache/not-found", "Cache not found")
        method, path = request.method, route[2]
        if (method, path) == ("POST", "/entries/search"):
            return self.search(body or {})
        if (method, path) == ("POST", "/entries"):
            return self.store(body or {})
        if (method, path) == ("DELETE", "/entries"):
            return self.delete_by_attributes(body or {})
        if (method, path) == ("POST", "/flush"):
            self.flushed, self.entries = True, {}
            return httpx.Response(204)
        if method == "DELETE" and path.startswith("/entries/"):
            if self.live().pop(path.removeprefix("/entries/"), None) is None:
                return self.error(404, self.missing_entry, "Entry not found")
            return httpx.Response(204)
        return self.error(404, "/errors/resource/not-found", "Not found")

    def bad_attributes(self, attributes):
        """The 400 for an attribute the cache does not define, or None if they are all fine."""
        if not self.attributes:
            return self.error(400, "/errors/invalid-data", "Invalid data",
                              "no attributes are configured for this cache")  # fmt: skip
        unknown = sorted(set(attributes) - set(self.attributes))
        if unknown:
            return self.error(400, "/errors/invalid-data", "Invalid data",
                              f"attribute {unknown[0]} is not defined")  # fmt: skip
        return None

    def search(self, body):
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not 1 <= len(prompt) <= 1024:
            return self.error(
                400, "/errors/invalid-data", "Invalid data", "prompt is 1-1024 characters"
            )
        attributes = body.get("attributes")
        if attributes and (bad := self.bad_attributes(attributes)):
            return bad
        threshold = max(float(body.get("similarityThreshold", 0.85)), self.floor)
        limit = min(int(body.get("maxResults", 1)), self.cap)
        hits = []
        for entry_id, entry in self.live().items():
            if any(entry["attributes"].get(k) != v for k, v in (attributes or {}).items()):
                continue
            for strategy in body.get("searchStrategies") or ["semantic"]:
                if strategy == "exact":
                    score = 1.0 if prompt.lower() == entry["prompt"].lower() else 0.0
                else:
                    score = self.similarity(prompt, entry["prompt"])
                if score and score >= threshold:
                    hits.append({
                        "id": entry_id, "prompt": entry["prompt"], "response": entry["response"],
                        "attributes": entry["attributes"], "similarity": score,
                        "searchStrategy": strategy,
                    })  # fmt: skip
                    break
        hits.sort(key=lambda hit: hit["similarity"], reverse=True)
        return httpx.Response(200, json={"data": hits[:limit]})

    def store(self, body):
        prompt, response, ttl = body.get("prompt"), body.get("response"), body.get("ttlMillis")
        if not (isinstance(prompt, str) and 1 <= len(prompt) <= 1024 and isinstance(response, str)):
            return self.error(400, "/errors/invalid-data", "Invalid data", "prompt and response")
        if ttl is not None and not (isinstance(ttl, int) and 1 <= ttl <= 31_556_952_000):
            return self.error(
                400, "/errors/invalid-data", "Invalid data", "ttlMillis is out of range"
            )
        attributes = body.get("attributes") or {}
        if attributes and (bad := self.bad_attributes(attributes)):
            return bad
        return httpx.Response(201, json={"entryId": self.put(prompt, response, attributes, ttl)})

    def delete_by_attributes(self, body):
        attributes = body.get("attributes") or {}
        if not attributes:  # what the real API does: no filter means every entry
            self.wiped = True
        gone = [
            i for i, e in self.live().items()
            if all(e["attributes"].get(k) == v for k, v in attributes.items())
        ]  # fmt: skip
        for entry_id in gone:
            del self.entries[entry_id]
        return httpx.Response(200, json={"deletedEntriesCount": len(gone)})
