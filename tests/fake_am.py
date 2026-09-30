"""A small in-memory stand-in for the hosted Agent Memory service, for tests that must not need one.

FakeMemory answers exactly the requests the Agent Memory column sends, as httpx responses,
in the wire shapes the published OpenAPI describes (camelCase, RFC 7807 errors, no score on a
search item). test_am.py puts it behind httpx.MockTransport, and a child process that runs the
real `marimo export` puts it behind httpx.Client.send (TestRealRuntime), which is why it has a
module of its own. FakeIndexServer is the Redis database behind the store, for the read-only
"under the hood" cell.

It is a stand-in, not a model of the service: "similarity" here is a bag of hand-made
concepts (words that mean the same thing share one), a hard cut on one number per memory. What
the live service does with a threshold, and how it words an error, is for the live run.
"""

import hashlib
import json
import math
import re
from collections import Counter, namedtuple
from datetime import datetime, timedelta, timezone

import httpx
import redis
from fake_search import missing_index

BASE = "https://am.example.test"
STORE = "store-test-1"
KEY = "am-key-test-9f3a"

Call = namedtuple("Call", "method path query body")

# What a real embedding model knows and this one is told: words that share a concept.
CONCEPTS = {
    "food": "food kitchen serve serving meal snack peanut nut allergic allergy eat dinner",
    "feet": "feet foot toe boot size wide squashed cramped descent shoe",
    "cold": "winter freezing cold trek trip visit travel patagonia february somewhere",
    "green": "recycled material sustainable eco gear",
    "post": "parcel shipping express packaging plastic delivery sent",
    "running": "race run running training kilometre marathon",
    "knee": "knee pole trekking downhill descent",
}


def stem(word):
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def concepts(text):
    words = {stem(w) for w in re.findall(r"[a-z]+", text.lower())}
    return Counter({name: len(words & {stem(w) for w in vocab.split()}) for name, vocab in CONCEPTS.items()})


def similarity(query, memory):
    """A number in [0.12, 1.0): the cosine of the concept counts, stretched, plus a fixed jitter."""
    a, b = concepts(query), concepts(memory)
    dot = sum(a[name] * b[name] for name in CONCEPTS)
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    jitter = int(hashlib.md5((query + memory).encode()).hexdigest()[:4], 16) / 65535 * 0.04
    return round(0.12 + 0.84 * (dot / norm if norm else 0) + jitter, 4)


def stamp(when):
    return when.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class FakeMemory:
    """The hosted service, for one store and one API key.

    memories: id -> record, as the service returns it. sessions: id -> {"ownerId", "namespace",
    "events", "summary", "read"}. log: every request received, in order (Call). Set
    `search_error=(424, "invalid-model-credentials")` to make every search fail like that,
    `rate_limit=n` to answer 429 to searches after the n-th, `status="degraded"` for the health
    check, `ignore_filters=True` to make a search return everything whatever its filter (a
    service that did not apply what it was sent), and `wobble=id` to make that memory come back
    at thresholds it should not (a service whose threshold is not a hard cut), and `unlisted=ids`
    to keep those memories out of every search (an index that lags behind the write).
    """

    def __init__(self, store=STORE, key=KEY):
        self.store, self.key = store, key
        self.memories, self.sessions, self.log = {}, {}, []
        self.status, self.search_error, self.rate_limit = "healthy", None, None
        self.ignore_filters, self.wobble, self.stubborn, self.unlisted = False, None, set(), set()
        self.searches = 0

    # ---- state that has to cross a process boundary (TestRealRuntime) ---------------------
    def state(self):
        return {"memories": self.memories, "sessions": self.sessions, "log": self.log,
                "searches": self.searches}  # fmt: skip

    def load(self, state):
        self.memories, self.sessions, self.log = state["memories"], state["sessions"], state["log"]
        self.searches = state["searches"]

    # ---- helpers for tests ------------------------------------------------------------------
    def add_memory(self, ident, text, owner, namespace=None, *, hours_old=0, topics=(), kind="semantic"):
        made = stamp(datetime.now(timezone.utc) - timedelta(hours=hours_old))
        record = {"id": ident, "text": text, "ownerId": owner, "memoryType": kind,
                  "topics": list(topics), "createdAt": made, "updatedAt": made}  # fmt: skip
        if namespace is not None:
            record["namespace"] = namespace
        self.memories[ident] = record
        return record

    def add_session(self, ident, owner, namespace=None):
        self.sessions[ident] = {"ownerId": owner, "namespace": namespace, "events": [],
                                "summary": None, "read": False}  # fmt: skip

    def extract(self):
        """Background extraction: one memory per session with events, owned as its first event says."""
        made = []
        for ident, session in self.sessions.items():
            if session["read"] or not session["events"]:
                continue
            session["read"] = True
            said = " ".join(e["content"][0]["text"] for e in session["events"])
            made.append(
                self.add_memory(
                    hashlib.md5(ident.encode()).hexdigest(), f"The customer said: {said}",
                    session["ownerId"], session["namespace"], topics=["conversation"], kind="episodic",
                )  # fmt: skip
            )
            made[-1]["sessionId"] = ident
        return made

    def requests(self, method=None, tail=None):
        return [c for c in self.log if (not method or c.method == method) and (not tail or c.path.endswith(tail))]

    def writes(self):
        """Every request that changes something: POST and DELETE, except a search."""
        return [c for c in self.log if c.method in ("POST", "DELETE") and not c.path.endswith("/search")]

    def deleted_memory_ids(self):
        return [i for c in self.requests("DELETE", "/long-term-memory") for i in c.body["memoryIds"]]

    # ---- the wire -----------------------------------------------------------------------------
    def problem(self, status, slug, detail):
        body = {"title": slug.replace("-", " ").title(), "status": status, "detail": detail,
                "instance": "/", "type": f"/errors/{slug}"}  # fmt: skip
        return httpx.Response(status, json=body)

    def handle(self, request):
        query = dict(request.url.params)
        body = json.loads(request.content) if request.content else None
        self.log.append(Call(request.method, request.url.path, query, body))
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return self.problem(401, "authentication-failed", "the API key is not valid")
        prefix = f"/v1/stores/{self.store}"
        if not request.url.path.startswith(prefix):
            return self.problem(404, "resource-not-found", "no such store")
        path, method = request.url.path[len(prefix) :], request.method
        if (method, path) == ("GET", "/health"):
            features = {"long-term-memory": {"status": self.status}, "session-memory": {"status": "healthy"}}
            return httpx.Response(200, json={"status": self.status, "features": features})
        if (method, path) == ("POST", "/session-memory/events"):
            return self.add_event(body)
        if (method, path) == ("GET", "/session-memory"):
            return self.list_sessions(query)
        if match := re.fullmatch(r"/session-memory/([^/]+)", path):
            if method == "GET":
                return self.get_session(match[1])
            if method == "DELETE":
                return self.delete_session(match[1])
        if (method, path) == ("POST", "/long-term-memory"):
            return self.create(body)
        if (method, path) == ("POST", "/long-term-memory/search"):
            return self.search(body)
        if (method, path) == ("DELETE", "/long-term-memory"):
            return self.delete(body)
        return self.problem(404, "resource-not-found", f"{method} {path}")

    # ---- sessions -----------------------------------------------------------------------------
    def add_event(self, body):
        for field in ("actorId", "role", "content", "createdAt"):
            if field not in body:
                return self.problem(400, "invalid-data", f"{field} is required")
        ident = body.get("sessionId") or hashlib.md5(str(len(self.sessions)).encode()).hexdigest()
        if ident not in self.sessions:  # the first event decides owner and namespace, for good
            self.add_session(ident, body["actorId"], body.get("namespace"))
        event = {"eventId": hashlib.md5(f"{ident}{len(self.sessions[ident]['events'])}".encode()).hexdigest(),
                 "actorId": body["actorId"], "sessionId": ident, "role": body["role"],
                 "content": body["content"], "createdAt": body["createdAt"],
                 "systemTimestamp": stamp(datetime.now(timezone.utc)),
                 "metadata": body.get("metadata")}  # fmt: skip
        self.sessions[ident]["events"].append(event)
        return httpx.Response(201, json={"event": event})

    def get_session(self, ident):
        session = self.sessions.get(ident)
        if session is None:
            return self.problem(404, "resource-not-found", "no such session")
        reply = {"sessionId": ident, "ownerId": session["ownerId"], "events": session["events"]}
        if session["namespace"]:
            reply["namespace"] = session["namespace"]
        if session["summary"]:
            reply["summary"] = {"text": session["summary"]}
        return httpx.Response(200, json=reply)

    def list_sessions(self, query):
        if "includeAll" in query or not query.get("filterOwnerId"):
            return self.problem(400, "invalid-data", "filterOwnerId is required without includeAll")
        ids = [i for i, s in self.sessions.items() if s["ownerId"] == query["filterOwnerId"]]
        return httpx.Response(200, json={"items": ids, "total": len(ids)})

    def delete_session(self, ident):
        if self.sessions.pop(ident, None) is None:
            return self.problem(404, "resource-not-found", "no such session")
        return httpx.Response(204)

    # ---- long-term memory ---------------------------------------------------------------------
    def create(self, body):
        created, errors = [], []
        for item in body["memories"]:
            if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", item.get("id", "")) or not item.get("text"):
                errors.append({"id": item.get("id", ""), "error": "invalid memory"})
                continue
            self.add_memory(item["id"], item["text"], item["ownerId"], item.get("namespace"),
                            topics=item.get("topics", ()), kind=item.get("memoryType", "semantic"))  # fmt: skip
            created.append(item["id"])
        return httpx.Response(201, json={"created": created, **({"errors": errors} if errors else {})})

    @staticmethod
    def clause_matches(clause, value, fold=False):
        """eq / ne / in / all on one field. eq, in and all never match a missing value; ne does."""
        norm = (lambda v: v.lower()) if fold else (lambda v: v)
        values = value if isinstance(value, list) else [value]
        have = {norm(v) for v in values if v is not None}
        if "ne" in clause and norm(clause["ne"]) in have:
            return False
        if "eq" in clause and norm(clause["eq"]) not in have:
            return False
        if "in" in clause and not have & {norm(v) for v in clause["in"]}:
            return False
        return not ("all" in clause and not {norm(v) for v in clause["all"]} <= have)

    def passes(self, filt, memory):
        if self.ignore_filters:
            return True
        fields = (("ownerId", False), ("namespace", True), ("sessionId", False),
                  ("memoryType", True), ("topics", True))  # fmt: skip
        return all(
            self.clause_matches(filt[name], memory.get(name), fold)
            for name, fold in fields
            if name in filt
        )

    def search(self, body):
        if self.search_error:
            return self.problem(self.search_error[0], self.search_error[1], "the store could not answer")
        self.searches += 1
        if self.rate_limit is not None and self.searches > self.rate_limit:
            return self.problem(429, "too-many-requests", "rate limit exceeded")
        threshold, limit, text = body.get("similarityThreshold"), body.get("limit", 10), body.get("text", "")
        kept = [
            m for m in self.memories.values()
            if m["id"] not in self.unlisted and self.passes(body.get("filter") or {}, m)
        ]  # fmt: skip
        if text:
            scored = [(similarity(text, m["text"]), m) for m in kept]
            if self.wobble and threshold is not None:
                scored = [(s if m["id"] != self.wobble else self.wobbled(s, threshold), m) for s, m in scored]
            scored = [(s, m) for s, m in scored if threshold is None or s >= threshold]
            kept = [m for _, m in sorted(scored, key=lambda sm: (-sm[0], sm[1]["id"]))]
        else:
            kept.sort(key=lambda m: (m["createdAt"], m["id"]))
        offset = int(body.get("pageToken") or 0)
        reply = {"items": kept[offset : offset + limit]}
        if offset + limit < len(kept):
            reply["nextPageToken"] = str(offset + limit)
        return httpx.Response(200, json=reply)

    @staticmethod
    def wobbled(score, threshold):
        """A memory that is missing at 0.3 and 0.4 but back at 0.5 and above: not a hard cut."""
        return 0.0 if 0.25 < threshold < 0.45 else 1.0

    def delete(self, body):
        deleted, errors = [], []
        for ident in body["memoryIds"]:
            if ident in self.memories and ident not in self.stubborn:
                del self.memories[ident]
                deleted.append(ident)
            elif ident not in self.memories:
                errors.append({"id": ident, "error": "not found"})
        return httpx.Response(200, json={"deleted": deleted, **({"errors": errors} if errors else {})})


class FakeIndexServer:
    """The Redis database behind a store: the index the service keeps and its memories' hashes.

    Answers only what the "under the hood" cell reads, as RESP2 replies (bytes, flat lists),
    and records every command in `sent`. `dim` is the vector length the index was built with.
    """

    def __init__(self, store, memories, dim=3072):
        self.index, self.dim, self.sent = f"memory:{store}:ltm", dim, []
        self.wording = "8.6"  # how a missing index is worded: a key of fake_search.MISSING_INDEX
        self.hashes = {f"{self.index}:{m['id']}": m for m in memories}

    def handle(self, args):
        self.sent.append(tuple(args))
        name = (args[0].decode() if isinstance(args[0], bytes) else str(args[0])).upper()
        rest = [a.decode() if isinstance(a, bytes) else a for a in args[1:]]
        if name == "PING":
            return True
        if name in ("FT.INFO", "FT.SEARCH") and rest[0] != self.index:
            raise missing_index(rest[0], self.wording)
        if name == "FT.INFO":
            vector = [b"identifier", b"text_vector", b"attribute", b"text_vector", b"type", b"VECTOR",
                      b"algorithm", b"HNSW", b"data_type", b"FLOAT32", b"dim", self.dim,
                      b"distance_metric", b"COSINE"]  # fmt: skip
            return [b"index_name", self.index.encode(),
                    b"index_definition", [b"key_type", b"HASH", b"prefixes", [f"{self.index}:".encode()]],
                    b"attributes", [[b"identifier", b"id", b"attribute", b"id", b"type", b"TAG"], vector],
                    b"num_docs", str(len(self.hashes)).encode()]  # fmt: skip
        if name == "FT.SEARCH":
            wanted = re.fullmatch(r"@id:\{(.*)\}", rest[1])[1].replace("\\-", "-")
            keys = [k for k in self.hashes if k == f"{self.index}:{wanted}"]
            return [len(keys), *[k.encode() for k in keys[:1]]]
        if name == "HMGET":
            record = self.hashes[rest[0]]
            names = {"owner_id": "ownerId", "memory_type": "memoryType"}
            return [self.field(record, names.get(f, f)) for f in rest[1:]]
        if name == "HSTRLEN":
            return self.dim * 4 if rest[1] == "text_vector" else 0
        raise redis.ResponseError(f"unknown command '{name}'")

    @staticmethod
    def field(record, name):
        value = record.get(name)
        if isinstance(value, list):
            value = ",".join(value)
        return None if value is None else str(value).encode()

    def names(self):
        return [(a[0].decode() if isinstance(a[0], bytes) else str(a[0])).upper() for a in self.sent]
