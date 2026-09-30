"""A stand-in for the hosted Context Retriever service and for the Redis it writes into.

One object holds both, because the service is what fills the database: create a surface and it
builds an index over each entity (under a name it chooses), import records and it writes JSON
documents, delete the surface and the data stays (the docs say so). The indexes stay too unless
`drops_indexes` is set, which is what the live service did on 2026-09-30. Every reply has the
shape the research recorded: the admin REST API answers in snake_case, the MCP endpoint speaks
JSON-RPC 2.0, and Redis answers as RESP2 does (bytes, flat lists). The default tool surface is
the unified one the live service served; style="classic" gives the older per-field tools.

test_cr.py uses it in-process, and in a child process that runs the real `marimo export`
(TestRealRuntime), which is why it is picklable: plain dicts, lists and strings only.
"""

import fnmatch
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import redis
from fake_search import missing_index

ADMIN_KEY = "cs_admin_FAKEADMINKEY0123456789"
API = "https://cloud.redis.io/context-surfaces"
MCP = "https://gcp-us-east4.context-surfaces.redis.io/mcp"
LIMIT = {"type": "integer", "default": 10, "minimum": 1, "maximum": 100}
OFFSET = {"type": "integer", "default": 0, "minimum": 0}
PAGING = {"limit": LIMIT, "offset": OFFSET}


def txt(value):
    return value.decode() if isinstance(value, bytes) else str(value)


def string(**more):
    return {"type": "string", **more}


def tool(name, description, properties, required=(), strict=False):
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    if strict:
        schema["additionalProperties"] = False
    return {"name": name, "description": description, "inputSchema": schema}


def indexed(entity):
    """[(field name, index type)] for the fields of an exported entity that have an index."""
    return [(f["name"], i["type"]) for f in entity["fields"] for i in f["redis_indices"]]


def classic_tools(data_model):
    """Per-field tools named after the entity: what the workshop's recorded output shows."""
    tools = []
    for entity in data_model["entities"]:
        name, title = entity["name"].lower(), entity["name"]
        key = next(f for f in entity["fields"] if f["is_key_component"])
        by_id = {"id": string(description=key["description"])}
        tools.append(
            tool(f"get_{name}_by_id", f"Get one {title} by id", by_id, ["id"], strict=True)
        )
        if any(kind == "text" for _, kind in indexed(entity)):
            words = {"query": string(description="Words to look for"), **PAGING}
            tools.append(tool(f"search_{name}_by_text", f"Search {title} text", words, ["query"]))
        for field in entity["fields"]:
            for index in field["redis_indices"]:
                if index["type"] == "tag":
                    value = string(description=field["description"])
                    if field.get("allowed_values"):
                        value["enum"] = list(field["allowed_values"])
                    filter_name = f"filter_{name}_by_{field['name']}"
                    filter_title = f"Filter {title} by {field['name']}"
                    tools.append(
                        tool(filter_name, filter_title, {"value": value, **PAGING}, ["value"])
                    )
                elif index["type"] == "numeric":
                    span = {"min_value": {"type": "number"}, "max_value": {"type": "number"}}
                    find_name = f"find_{name}_by_{field['name']}_range"
                    tools.append(tool(find_name, f"Find {title} by range", {**span, **PAGING}))
    return tools


def unified_tools(data_model):
    """The surface the live service served on 2026-09-30: 21 tools for the default model.

    Per entity, named after it in lower case: get_<e>_by_id, list_<e>, count_<e>, summarize_<e>
    (needs a numeric field), filter_<e> (needs a tag or a numeric field) and search_<e>_by_text
    (needs a text field). Then four set tools. filter_<e> takes tag_conditions and
    numeric_conditions, and answers isError "at least one condition is required" when it is given
    neither. The schemas below are those the live service sent, less what the tests do not need.
    """
    tools = []
    for entity in data_model["entities"]:
        name, title = entity["name"].lower(), entity["name"]
        key = next(f for f in entity["fields"] if f["is_key_component"])
        kinds = indexed(entity)
        tags = [n for n, kind in kinds if kind == "tag"]
        numbers = [n for n, kind in kinds if kind == "numeric"]
        texts = [n for n, kind in kinds if kind == "text"]
        by_id = {"id": string(description=f"The ID of the {title} to retrieve. {key['description']}")}
        tools.append(
            tool(f"get_{name}_by_id", f"Get {title} by ID", by_id, ["id"], strict=True)
        )
        sort = {"sort_by": string(enum=numbers), "sort_order": string(enum=["asc", "desc"])}
        paging = {"limit": {"type": "number"}, "offset": {"type": "number"}}
        tools.append(tool(f"list_{name}", f"List {title}", {**paging, **(sort if numbers else {})}))
        countable = {"function": string(enum=["count", "count_distinct"]), "limit": LIMIT}
        if tags or numbers:
            countable["field"] = string(enum=sorted(tags + numbers))
        if tags:
            countable["group_by"] = string(enum=sorted(tags))
        tools.append(
            tool(f"count_{name}", f"Count {title}", countable, ["function"], strict=True)
        )
        if numbers:
            summed = {
                "field": string(enum=sorted(numbers)),
                "function": string(enum=["sum", "avg", "min", "max"]),
                "limit": LIMIT,
            }
            tools.append(
                tool(f"summarize_{name}", f"Summarize {title}", summed, ["field", "function"], True)
            )
        conditions = {}
        if tags:
            tag_item = {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "field": string(enum=tags),
                    "value": string(),
                    "exclude": {"type": "boolean"},
                },
                "required": ["field", "value"],
            }
            conditions["tag_conditions"] = {"type": "array", "items": tag_item, "maxItems": 16}
        if numbers:
            number_item = {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "field": string(enum=numbers),
                    "min_value": {"type": "number"},
                    "max_value": {"type": "number"},
                },
                "required": ["field"],
            }
            conditions["numeric_conditions"] = {"type": "array", "items": number_item, "maxItems": 16}
        if conditions:
            filters = {**paging, **conditions, **(sort if numbers else {})}
            tools.append(tool(f"filter_{name}", f"Filter {title}", filters))
        if texts:
            words = {"query": string(description="Words to look for"), **paging}
            tools.append(tool(f"search_{name}_by_text", f"Search {title} text", words, ["query"]))
    names = [e["name"] for e in data_model["entities"]]
    sets = {
        "type": "array",
        "minItems": 2,
        "maxItems": 8,
        "items": {"type": "object", "properties": {"entity": string(enum=names)}},
    }
    for verb in ("intersect", "union", "except"):
        tools.append(
            tool(f"{verb}_results", f"{verb} sets", {"sets": sets, **PAGING}, ["sets"], True)
        )
    follow = {"set": {"type": "object"}, "relationship": string(), **PAGING}
    tools.append(
        tool("expand_results", "Follow a relationship", follow, ["set", "relationship"], True)
    )
    return tools


def violation(schema, value, path="root"):
    """Why value does not fit schema, in the words of the service's validator; None if it does."""
    if "enum" in schema and value not in schema["enum"]:
        options = " ".join(str(c) for c in schema["enum"])
        return f"validating {path}: enum: {value} does not equal any of: [{options}]"
    kind = schema.get("type")
    python = {"string": str, "boolean": bool, "array": list, "object": dict}
    python |= {"integer": int, "number": (int, float)}
    wrong = kind in python and not isinstance(value, python[kind])
    if kind in ("integer", "number") and isinstance(value, bool):
        wrong = True
    if wrong:
        return f"validating {path}: type: {value!r} has type {type(value).__name__}, want {kind}"
    if kind == "object":
        return check_object(schema, value, path)
    if kind == "array":
        for i, item in enumerate(value):
            problem = violation(schema.get("items") or {}, item, f"{path}/items[{i}]")
            if problem:
                return problem
    return None


def check_object(schema, args, path="root"):
    properties = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        extra = [k for k in args if k not in properties]
        if extra:
            listed = ", ".join(json.dumps(k) for k in extra)
            return f"validating {path}: unexpected additional properties [{listed}]"
    missing = [k for k in schema.get("required") or [] if k not in args]
    if missing:
        return f"validating {path}: missing properties: {missing}"
    for name, value in args.items():
        if name in properties:
            problem = violation(properties[name], value, f"/properties/{name}")
            if problem:
                return problem
    return None


class FakeWorld:
    """The hosted service and the database behind it.

    style: "unified" (what the live service served) or "classic", the tool surface a created
    surface gets.
    """

    def __init__(
        self, style="unified", version="8.4.0", search=True, json_module=True, mcp_url=MCP
    ):
        self.style, self.version, self.search, self.json_module = (
            style,
            version,
            search,
            json_module,
        )
        self.mcp_url = mcp_url  # what the agent-key listing says; None leaves it out
        self.wording = "8.6"  # how a missing index is worded: a key of fake_search.MISSING_INDEX
        self.drops_indexes = False  # deleting a surface drops its indexes too, as the live one did
        self.surfaces, self.agent_keys, self.key_records = {}, {}, {}
        self.docs, self.ttls, self.indexes = {}, {}, {}
        self.sent, self.requests, self.bodies, self.events = [], [], [], []
        self.config = None  # the connection_config the service was last given, password and all
        self.tools_override = None  # tools/list answers this and every call echoes: made-up tools
        self.forced = {}  # "list" | "create" | "delete" | "mcp" | "data": an HTTP status to answer
        self.create_reply_lost = False  # make the surface, then answer 504: the caller cannot know
        self.fail_entity = "Order"  # the import of this entity answers forced["data"]
        self.status = None  # a lifecycle status for a created surface to report; None leaves it out
        self.down = False  # Redis refuses every command
        self.appear_on_create = []  # [(index name, prefix)] another app builds as a surface is made
        self.counter = 0

    # ---- helpers for tests --------------------------------------------------------------------
    def add_surface(self, name, run="feedcafe", created_by="iris-basics-notebook", age_h=5.0):
        """A surface some other run (or app) left in the account; returns its id."""
        self.counter += 1
        when = datetime.now(timezone.utc) - timedelta(hours=age_h)
        sid = f"cs_{self.counter:04d}"
        self.surfaces[sid] = {
            "id": sid,
            "name": name,
            "metadata": {"created_by": created_by, "run": run} if run else {},
            "created_at": when.isoformat(timespec="seconds"),
            "tools": [],
        }
        return sid

    def add_index(self, name, prefix):
        self.indexes[name] = {"prefixes": [prefix], "fields": [("f", "TAG")]}

    def shown(self, sid):
        """A surface as the API sends it: not the tools kept for the MCP endpoint."""
        return {k: v for k, v in self.surfaces[sid].items() if k != "_tools"}

    def names(self):
        """The upper-cased name of every Redis command received."""
        return [txt(argv[0]).upper() for argv in self.sent]

    def writes(self):
        writes = {"EXPIRE", "UNLINK", "FT.DROPINDEX"}
        return [(txt(a[0]).upper(), txt(a[1])) for a in self.sent if txt(a[0]).upper() in writes]

    # ---- the hosted service -------------------------------------------------------------------
    def reply(self, request, status, payload):
        return httpx.Response(status, json=payload, request=request)

    def http(self, request):
        method, path = request.method, request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append((method, path))
        self.bodies.append((method, path, body))
        self.events.append(("http", method, path))
        if path.rstrip("/").endswith("/mcp"):
            return self.mcp(request, body)
        if request.headers.get("x-api-key") != ADMIN_KEY:
            return self.reply(request, 401, {"detail": "invalid admin key"})
        route = r".*/api/v1/context-surfaces(?:/([^/]+)(?:/(agent-keys|data))?)?"
        match = re.fullmatch(route, path)
        if not match:
            return self.reply(request, 404, {"detail": f"no route {path}"})
        sid, sub = match.groups()
        if sid is None and method == "GET":
            return self.list_surfaces(request, dict(request.url.params))
        if sid is None and method == "POST":
            return self.create(request, body)
        if sid not in self.surfaces:
            return self.reply(request, 404, {"detail": "surface not found"})
        if sub is None and method == "GET":
            return self.reply(request, 200, self.shown(sid))
        if sub is None and method == "DELETE":
            return self.delete(request, sid)
        if sub == "agent-keys" and method == "POST":
            return self.mint(request, sid, body)
        if sub == "agent-keys" and method == "GET":
            return self.list_keys(request, sid)
        if sub == "data" and method == "POST":
            return self.load(request, sid, body)
        return self.reply(request, 405, {"detail": "method not allowed"})

    def list_surfaces(self, request, params):
        if "list" in self.forced:
            return self.reply(request, self.forced["list"], {"detail": "forced"})
        page, size = int(params.get("page", 1)), int(params.get("page_size", 20))
        items = [self.shown(sid) for sid in self.surfaces]
        pagination = {
            "page": page,
            "page_size": size,
            "total_count": len(items),
            "total_pages": -(-len(items) // size),
            "has_next": page * size < len(items),
            "has_prev": page > 1,
        }
        chunk = items[(page - 1) * size : page * size]
        return self.reply(request, 200, {"context_surfaces": chunk, "pagination": pagination})

    def create(self, request, body):
        if "create" in self.forced:
            return self.reply(request, self.forced["create"], {"detail": "forced"})
        model = body["data_model"]
        self.config = body["data_source"]["connection_config"]
        self.counter += 1
        sid = f"cs_{self.counter:04d}"
        tools = (classic_tools if self.style == "classic" else unified_tools)(model)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.surfaces[sid] = {
            "id": sid,
            "name": body["name"],
            "description": body.get("description", ""),
            "owner": "someone",
            "metadata": body.get("metadata", {}),
            "data_model": model,
            "tools": [t["name"] for t in tools],
            "created_at": now,
            "updated_at": now,
            "_tools": tools,
        }
        if self.status:
            self.surfaces[sid]["status"] = self.status
        for entity in model["entities"]:  # the service picks index names of its own
            digest = hashlib.md5(entity["name"].encode()).hexdigest()[:8]
            name = f"idx:{sid}:{digest}:{entity['name'].lower()}"
            fields = [(n, kind.upper()) for n, kind in indexed(entity)]
            prefix = entity["redis_key_template"].split("{")[0]
            self.indexes[name] = {"prefixes": [prefix], "fields": fields}
        for name, prefix in self.appear_on_create:
            self.add_index(name, prefix)
        if self.create_reply_lost:
            return self.reply(request, 504, {"detail": "gateway timeout"})
        return self.reply(request, 201, self.shown(sid))

    def delete(self, request, sid):
        if "delete" in self.forced:
            return self.reply(request, self.forced["delete"], {"detail": "forced"})
        del self.surfaces[sid]  # the data stays, as the docs say
        if self.drops_indexes:
            for name in [n for n in self.indexes if n.startswith(f"idx:{sid}:")]:
                del self.indexes[name]
        for text in [k for k, rec in self.agent_keys.items() if rec["surface"] == sid]:
            del self.agent_keys[text]
        return self.reply(request, 200, {"message": "deleted"})

    def mint(self, request, sid, body):
        self.counter += 1
        text = f"cs_agent_{self.counter:04d}FAKEAGENTKEY9f3a"
        record = {
            "id": f"ak_{self.counter:04d}",
            "surface": sid,
            "expires_at": body.get("expires_at"),
            "name": body["name"],
        }
        self.agent_keys[text] = record
        reply = {"id": record["id"], "key": text, "name": body["name"], "context_surface_id": sid}
        return self.reply(request, 201, {**reply, "key_type": "agent", "owner": "someone"})

    def list_keys(self, request, sid):
        items = []
        for record in self.agent_keys.values():
            if record["surface"] == sid:
                item = {"id": record["id"], "masked_key": "cs_agent_****", "name": record["name"]}
                item["expires_at"] = record["expires_at"]
                if self.mcp_url:
                    item["mcp_url"] = self.mcp_url
                items.append(item)
        return self.reply(request, 200, {"agent_keys": items, "pagination": {"has_next": False}})

    def load(self, request, sid, body):
        if "data" in self.forced and body["entity"] == self.fail_entity:
            return self.reply(request, self.forced["data"], {"detail": "forced"})
        entities = self.surfaces[sid]["data_model"]["entities"]
        entity = next(e for e in entities if e["name"] == body["entity"])
        for record in body["records"]:
            key = entity["redis_key_template"].format(**record)
            self.docs[key] = dict(record)
            self.key_records[key] = {"surface": sid, "entity": entity["name"]}
        done = {"imported": len(body["records"]), "failed": 0, "errors": []}
        return self.reply(request, 200, done)

    # ---- MCP ----------------------------------------------------------------------------------
    def rpc(self, request, ident, result=None, error=None):
        payload = {"jsonrpc": "2.0", "id": ident}
        payload.update({"error": error} if error else {"result": result})
        return self.reply(request, 200, payload)

    def text(self, request, ident, payload):
        content = [{"type": "text", "text": json.dumps(payload)}]
        return self.rpc(request, ident, {"content": content})

    def mcp(self, request, body):
        if "mcp" in self.forced:
            return self.reply(request, self.forced["mcp"], {"detail": "forced"})
        record = self.agent_keys.get(request.headers.get("x-api-key", ""))
        if record is None or record["surface"] not in self.surfaces:
            return self.reply(request, 401, {"detail": "invalid agent key"})
        surface, ident = self.surfaces[record["surface"]], body.get("id")
        tools = surface["_tools"] if self.tools_override is None else self.tools_override
        if body["method"] == "tools/list":
            return self.rpc(request, ident, {"tools": tools})
        name, args = body["params"]["name"], body["params"].get("arguments") or {}
        chosen = next((t for t in tools if t["name"] == name), None)
        if chosen is None:
            error = {"code": -32602, "message": f"unknown tool {name}"}
            return self.rpc(request, ident, error=error)
        problem = check_object(chosen["inputSchema"], args)
        if problem:
            message = f'invalid params: validating "arguments": {problem}'
            return self.rpc(request, ident, error={"code": -32602, "message": message})
        if self.tools_override is not None:
            return self.text(request, ident, {"tool": name, "args": args})
        outcome, payload = self.run_tool(surface, name, args)
        if outcome == "error":
            content = [{"type": "text", "text": payload}]
            return self.rpc(request, ident, {"content": content, "isError": True})
        return self.text(request, ident, payload)

    def entity_docs(self, surface, entity):
        entities = surface["data_model"]["entities"]
        template = next(e["redis_key_template"] for e in entities if e["name"] == entity)
        prefix = template.split("{")[0]
        return [(k, v) for k, v in self.docs.items() if k.startswith(prefix)]

    def run_tool(self, surface, name, args):
        """("ok", JSON) or ("error", text) for a tool call that passed validation."""
        verb, _, rest = name.partition("_")
        if verb in ("intersect", "union", "except", "expand"):
            return "ok", {"complete": True, "count": 0, "ids": [], "has_more": False}
        wanted = rest.split("_")[0]
        entities = surface["data_model"]["entities"]
        entity = next(e["name"] for e in entities if e["name"].lower() == wanted)
        docs = self.entity_docs(surface, entity)
        stamp = {
            "source": surface["id"], "retrieved_at": "2026-09-30T09:34:54.655663572Z",
            "source_version": 1,
        }  # fmt: skip
        extra = {"_retrieval": stamp} if self.style == "unified" else {}
        if verb == "get":
            key = next((k for k, _ in docs if k.endswith(":" + str(args["id"]))), None)
            if key is None:
                failed = f"failed to get document: key {args['id']} not found"
                return "error", f"Error executing tool: get by ID failed: {failed}"
            return "ok", {**extra, **self.docs[key]}
        if verb == "count":
            return "ok", {"function": args["function"], "value": len(docs)}
        if verb == "summarize":
            total = sum(doc.get(args["field"], 0) for _, doc in docs)
            return "ok", {"function": args["function"], "field": args["field"], "value": total}
        if verb == "filter" and self.style == "unified":
            if not (args.get("tag_conditions") or args.get("numeric_conditions")):
                return "error", "Error executing tool: at least one condition is required"
        matched = [(key, doc) for key, doc in docs if self.matches(verb, name, args, doc)]
        limit, offset = args.get("limit", 10), args.get("offset", 0)
        page = matched[offset : offset + limit]
        return "ok", {
            "count": len(page),
            "has_more": offset + limit < len(matched),
            "limit": limit,
            "offset": offset,
            "total_count": len(matched),
            "results": [{**doc, **extra, "id": key} for key, doc in page],
        }

    def matches(self, verb, name, args, doc):
        if verb == "list":
            return True
        if verb == "search":
            return args["query"].lower() in json.dumps(doc).lower()
        if self.style == "unified":  # filter_<entity>: every condition must hold
            keep = True
            for cond in args.get("tag_conditions") or []:
                keep &= (str(doc.get(cond["field"])) == cond["value"]) != bool(cond.get("exclude"))
            for cond in args.get("numeric_conditions") or []:
                value = doc.get(cond["field"], 0)
                keep &= cond.get("min_value", value) <= value <= cond.get("max_value", value)
            return keep
        if verb == "filter":
            return str(doc.get(name.split("_by_")[1])) == args["value"]
        field = name.split("_by_")[1].removesuffix("_range")  # find_<entity>_by_<field>_range
        low, high = args.get("min_value", float("-inf")), args.get("max_value", float("inf"))
        return low <= doc[field] <= high

    # ---- Redis --------------------------------------------------------------------------------
    def handle(self, args):
        if self.down:
            raise redis.ConnectionError("Error 111 connecting to db.example:6379. Refused.")
        self.sent.append(tuple(args))
        name = txt(args[0]).upper()
        self.events.append(("redis", name))
        command = {
            "PING": lambda: True,
            "INFO": self.info,
            "DBSIZE": lambda: len(self.docs),
            "SCAN": self.scan,
            "FT._LIST": self.ft_list,
            "FT.INFO": self.ft_info,
            "FT.DROPINDEX": self.ft_dropindex,
            "JSON.GET": self.json_get,
            "EXPIRE": self.expire,
            "UNLINK": self.unlink,
        }.get(name)
        if command is None:
            raise redis.ResponseError(f"unknown command '{name}'")
        return command(*args[1:])

    def scan(self, _cursor, _match, pattern, _count, _n):
        keys = [key for key in self.docs if fnmatch.fnmatchcase(key, txt(pattern))]
        return 0, [key.encode() for key in keys]

    def info(self, section):
        if txt(section) == "server":
            return {"redis_version": self.version}
        return {"connected_clients": 3}

    def ft_list(self):
        if not self.search:
            raise redis.ResponseError("unknown command 'FT._LIST'")
        return [name.encode() for name in self.indexes]

    def index(self, name):
        if txt(name) not in self.indexes:
            raise missing_index(name, self.wording)
        return self.indexes[txt(name)]

    def ft_info(self, name):
        index = self.index(name)
        prefixes = [p.encode() for p in index["prefixes"]]
        docs = [k for k in self.docs if any(k.startswith(p) for p in index["prefixes"])]
        attributes = [
            [b"identifier", f"$.{n}".encode(), b"attribute", n.encode(), b"type", kind.encode()]
            + ([b"SORTABLE"] if kind == "NUMERIC" else [])  # a flag makes the list odd-length
            for n, kind in index["fields"]
        ]
        definition = [b"key_type", b"JSON", b"prefixes", prefixes, b"default_score", b"1"]
        return [
            b"index_name", txt(name).encode(),
            b"index_definition", definition,
            b"attributes", attributes,
            b"num_docs", str(len(docs)).encode(),
        ]  # fmt: skip

    def ft_dropindex(self, name, *rest):
        assert not [r for r in rest if txt(r).upper() == "DD"], "DD reached the server"
        self.index(name)
        del self.indexes[txt(name)]
        return b"OK"

    def json_get(self, key, *paths):
        if not self.json_module:
            raise redis.ResponseError("unknown command 'JSON.GET'")
        doc = self.docs.get(txt(key))
        return None if doc is None else json.dumps(doc).encode()

    def expire(self, key, seconds):
        if txt(key) not in self.docs:
            return 0
        self.ttls[txt(key)] = int(seconds)
        return 1

    def unlink(self, *keys):
        gone = [txt(k) for k in keys if txt(k) in self.docs]
        for key in gone:
            del self.docs[key]
            self.ttls.pop(key, None)
            self.key_records.pop(key, None)
        return len(gone)
