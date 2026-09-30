# Context Retriever MVP research brief (column 2), 2026-09-30

Scope: what column 2 of the rebuilt `iris_basics.py` needs to know to create a Context Retriever
surface over its own Redis Cloud database, call one generated tool over MCP, break one call on
purpose, look at the data in Redis, and clean up. SDK `redis-context-retriever==0.0.6` for the
data model only; admin REST and MCP JSON-RPC through `niquests`.

Tags. **VERIFIED** = observed today by running code or reading the source, or recorded by the
live run of 2026-09-30 (README "Verified live", `.agents/build/live/summary-2.json`, plan
section 10). **UNVERIFIED** = from docs, SDK models or the docs' interactive-demo JS, not seen
against the hosted service. Where the prior brief
(`.agents/plans/research/context-retriever.md`) conflicts with the live run, the live run wins.

No network call was made to any Redis or Iris service for this brief. Docs were read from
`raw.githubusercontent.com/redis/docs`; the SDK, redis-py 8.1.0 and niquests 3.21.2 were
introspected offline with `uv run --python 3.13 --no-project`.

## 0. The short version

- This account's hosted service serves the **unified** tool surface: 21 tools for the Cairn
  three-entity model (VERIFIED live). Tool names and argument names are known; build displays
  from `tools/list`, never from a hardcoded list.
- Everything except the data model is five plain HTTPS calls (create surface, create agent key,
  list agent keys for `mcp_url`, import data once per entity, delete surface) plus two JSON-RPC
  POSTs (`tools/list`, `tools/call`). No SDK client is needed, and none of it is async.
- The create body carries the Redis password. Mask it in any displayed request, and never let a
  create-agent-key reply be a cell's last expression (marimo would render the key).
- Deleting the surface revokes its agent keys and, on this service, drops the indexes it built.
  The 20 JSON documents stay until you `UNLINK` them or their TTL fires (VERIFIED live).
- `ContextModel` breaks on Python 3.14 (reproduced today). Pin `requires-python = ">=3.11,<3.14"`.
- Cut the offline field editor and the tool predictor. Show `export_data_model` once, then the
  real `tools/list`. The lesson survives in one markdown table.

## 1. Endpoints and settings

| Setting | Default | Status |
|---|---|---|
| `CTX_API_URL` | `https://cloud.redis.io/context-surfaces` | VERIFIED: `context_surfaces.constants.DEFAULT_API_URL`; the live probe listed surfaces at `cloud.redis.io` |
| `CTX_MCP_URL` | `https://gcp-us-east4.context-surfaces.redis.io/mcp` | VERIFIED as the SDK default. The live run took the address from the agent-key listing's `mcp_url` instead, which is what the notebook should do (fall back to `CTX_MCP_URL`, then the default) |
| `CTX_ADMIN_KEY` | none | Account-wide admin key. Console: Context Retriever, Admin keys tab, New admin key (shown once). The tab appears only after a first service exists; the console-free route is `ctxctl auth login -u <email>` then `ctxctl --output json admin create --name X` (docs quickstart, VERIFIED text) |
| `REDIS_URL` | none | `redis://` or `rediss://` one-liner. The hosted service must reach this host over the internet, and the database needs Search and JSON (Redis 7.4 or later for `INDEXMISSING`; not Flex, not Active-Active). Prefer a scratch database: the service is given its password |

Admin paths are `<CTX_API_URL>/api/v1/...`. Every admin call carries `X-API-Key: <admin key>`
and `Content-Type: application/json`. The SDK's `Iris tutorial` env name `CTX_ADMIN_API_URL` is
not read by anything; use `CTX_API_URL`.

No new secrets are needed for column 2 beyond `CTX_ADMIN_KEY` and `REDIS_URL`. The names the
orchestrator fixed (`CTX_ADMIN_KEY`, optional `CTX_API_URL`, `CTX_MCP_URL`) match the SDK's.

## 2. Admin REST, as used live

All bodies are snake_case JSON. Statuses marked "live" were observed on 2026-09-30.

### 2.1 Create the surface: `POST /api/v1/context-surfaces` -> 201 (live)

```json
{
  "name": "iris-basics-<run>",
  "description": "Made by the Redis Iris basics notebook. Safe to delete.",
  "metadata": {"created_by": "iris-basics-notebook", "run": "<run>"},
  "data_model": { "...": "export_data_model(...) output, section 5.2" },
  "data_source": {
    "type": "redis",
    "connection_config": {
      "addr": "<host>:<port>",
      "username": "default",
      "password": "****",
      "db": 0,
      "tls_enabled": true,
      "pool_size": 2,
      "min_idle_conns": 0
    }
  }
}
```

- `connection_config` is built from `urllib.parse.urlsplit(REDIS_URL)` with `unquote()` on the
  username and password (VERIFIED: `urlsplit` leaves `%40` encoded while redis-py's `from_url`
  decodes it; the notebook's `cr_connection_config` does this). `tls_enabled = scheme == "rediss"`.
  `db` from the URL path, else 0.
- `pool_size` 2 and `min_idle_conns` 0 worked live against a Redis Cloud database (VERIFIED). SDK
  defaults are 10 and 2, too many for a free 30-connection database.
- `data_source` is required when `data_model` is given (VERIFIED: SDK `CreateContextSurfaceRequest`
  description). `name` 1-255 chars, `description` up to 1000, `metadata` is `dict[str, str]`.
- Reply, 201: `{"id", "name", "description", "owner", "status", "data_model", "tools", "created_at", "updated_at", "metadata"}`.
  VERIFIED live: `id` and `status: "provisioning_indices"`. `tools` (a list of names) is in the
  SDK's `ContextSurface` model; the live notebook read tool names from `tools/list` instead, so
  the presence of `tools` in the live create reply is UNVERIFIED.
- The live run imported data straight after create without waiting for `status == "active"`, and
  all 20 records landed and were indexed (VERIFIED, one run). `ctxctl surface update` polls
  `GET .../{id}` every 2 s up to 30 times until `status == "active"` after a data-model change
  (VERIFIED in `cli/main.py:1852-1874`). The MVP needs no poll; if an import returns a non-2xx,
  show `status` from `GET .../{id}` as the diagnostic.
- Never retry this POST automatically: a replay makes a second surface holding your password.
  niquests defaults to `retries=0`; keep it.

### 2.2 Create an agent key: `POST /api/v1/context-surfaces/{id}/agent-keys` -> 201 (live)

```json
{"name": "iris-basics-<run>-agent", "expires_at": "2026-09-30T13:00:00+00:00"}
```

`expires_at` as `datetime.now(timezone.utc) + timedelta(seconds=TTL)` formatted with
`isoformat(timespec="seconds")` was accepted live (VERIFIED). Optional fields: `description`,
`metadata`, `access_tags: {"region": ["emea"]}` (UNVERIFIED end to end; leave out).

Reply, 201: `{"id", "key", "name", "context_surface_id", "key_type": "agent", "owner", "created_at", "updated_at", "expires_at", "metadata"}`.
VERIFIED live: `id` and `key`. **`key` is returned only here.** Keep it in a Python variable; show
at most its last four characters.

### 2.3 Find the MCP address: `GET /api/v1/context-surfaces/{id}/agent-keys?page_size=50` -> 200 (live)

Reply: `{"agent_keys": [{"id", "masked_key", "name", "context_surface_id", "access_tags", "mcp_url", "expires_at", ...}], "pagination": {...}}`.
VERIFIED live that the item for the new key carried `mcp_url`. Match on `id` from 2.2.

### 2.4 Import data: `POST /api/v1/context-surfaces/{id}/data` -> 2xx (live)

One entity per call:

```json
{
  "entity": "Customer",
  "records": [{"customer_id": "C001", "name": "Ada Lovelace", "tier": "gold", "region": "emea", "lifetime_value": 4200.0}, "..."],
  "options": {"on_conflict": "overwrite", "on_error": "fail_fast"}
}
```

Each record is `Customer(**row).model_dump()`, so a field the model does not know is dropped
before it reaches the wire. Reply: `{"imported": 6, "failed": 0, "errors": []}` (VERIFIED live:
6, 6 and 8 imported). The exact status code (200 vs 201) is UNVERIFIED; accept any 2xx. The
service writes the records as JSON documents at the key-template names and builds one index per
entity; the notebook writes nothing itself (VERIFIED live: `JSON.GET` of the first key returned the
document, 20 of 20 keys existed for `EXPIRE`).

The docs quickstart loads sample data with `redis-cli HSET` (hashes). The hosted service reads
and writes JSON; do not copy the HSET step into the notebook's copy.

### 2.5 Read and list

- `GET /api/v1/context-surfaces/{id}` -> 200 with `status`, `data_model`, `tools`; **404 once
  deleted** (VERIFIED live, used by `verify.py` as the footprint check).
- `GET /api/v1/context-surfaces?page=1&page_size=50` -> `{"context_surfaces": [...], "pagination": {"page", "page_size", "total_count", "total_pages", "has_next", "has_prev"}}`
  (VERIFIED live; this account has 500 or more surfaces, so a full listing is 10 pages). The MVP
  does not need this call.

### 2.6 Delete the surface: `DELETE /api/v1/context-surfaces/{id}` -> 200 or 204; 404 if already gone

The live notebook accepted 200, 204 and 404; which of 200/204 the service sends is UNVERIFIED
(SDK unit mocks say 200 `{"message": ...}`). What it removes and leaves is in section 7.3. There
is no need to delete agent keys separately: the docs say deletion "immediately terminates all
API keys associated with the service" (VERIFIED text, view-service.md).

### 2.7 Error replies

401 for a bad admin key, 404 for a wrong `CTX_API_URL` (it answers 404 to everything) or a deleted
surface (VERIFIED live behaviour described in the notebook's `cr_failed`). Bodies are JSON; show
`status_code` plus the body's `detail`/`message` if present, never the full request.

## 3. MCP JSON-RPC, as used live

`POST <mcp_url>` with headers `X-API-Key: <agent key>` and `Content-Type: application/json`. No
`Accept` header is needed: the live notebook sent httpx's default `Accept: */*` and got JSON back
(VERIFIED live). niquests sends the same default (UNVERIFIED against this endpoint, same value).
The docs' curl example adds `Accept: application/json, text/event-stream`; harmless to add.
No `initialize` handshake is needed (VERIFIED: the SDK never sends one; the live run did not).

### 3.1 `tools/list`

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
```
-> 200 `{"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "...", "description": "...", "inputSchema": {...}}]}}`
(VERIFIED live: 21 tools).

### 3.2 `tools/call`, the happy path (VERIFIED live, recorded verbatim with identifiers replaced)

```json
{"jsonrpc": "2.0", "id": 2, "method": "tools/call",
 "params": {"name": "get_customer_by_id", "arguments": {"id": "C001"}}}
```
-> 200
```json
{"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text",
  "text": "{\"_retrieval\":{\"source\":\"<surface-id>\",\"retrieved_at\":\"2026-09-30T09:34:54.655663572Z\",\"source_version\":1},\"customer_id\":\"C001\",\"lifetime_value\":4200,\"name\":\"Ada Lovelace\",\"region\":\"emea\",\"tier\":\"gold\"}"}]}}
```

`result.content[0].text` is a JSON string: `json.loads` it. `get_*_by_id` takes the key component
(`C001`), not the full Redis key, and returns the bare record plus a `_retrieval` stamp. Filter
results are an envelope `{"count", "has_more", "limit", "offset", "total_count", "results": [...]}`
and each result also carries `"id": "<full Redis key>"` (VERIFIED live, two rows recorded).

### 3.3 Two kinds of failure, and they look different

1. **Tool error**: HTTP 200, `result.isError: true`, `result.content[0].text` starts with
   `Error executing tool: `. VERIFIED live: `filter_customer` called with only `limit` and `offset`
   returned `Error executing tool: at least one condition is required`. Check `isError` explicitly;
   nothing raises.
2. **Schema rejection**: a top-level `error` object instead of `result`:
   `{"jsonrpc": "2.0", "id": 2, "error": {"code": -32602, "message": "invalid params: validating \"arguments\": validating <path>: enum: <value> does not equal any of: [<choices>]"}}`.
   The code -32602 is JSON-RPC's "Invalid params". The wording above is the docs demo JS
   (`static/js/context-retriever-demo.js:320,394`), which says it mirrors a live server; an
   unknown property reads `validating root: unexpected additional properties ["x"]`. The
   implementer's docstring in `cr_ruled` reports live rejections worded as the schema rule
   (`validating /then: not: ...`) for `count_*`, so the live service does word rejections this way,
   but no live rejection text was captured in the logs: **PLAUSIBLE, not VERIFIED**. The HTTP status
   that accompanies a JSON-RPC error is UNVERIFIED (the notebook accepted 200 and 400). Show
   `error.code` and `error.message` verbatim, whatever they are.

## 4. What the data model changes on the unified surface

Names and argument lists below are the 21 tools the hosted service listed for the default Cairn
model on 2026-09-30 (VERIFIED live, README table). Entity names are lower-cased. `*` = required.

| Field choice | Tool it creates or changes | Arguments |
|---|---|---|
| `is_key_component=True` (one per entity) | `get_<e>_by_id` | `id*` |
| any entity | `list_<e>` | `limit`, `offset`, and `sort_by` (enum of numeric fields) + `sort_order` when the entity has a numeric field |
| any entity | `count_<e>` | `function*` (`count`, `count_distinct`), `field` (enum of tag + numeric fields), `group_by` (enum of tag fields), `limit`, `offset`, `timeout_ms` |
| a `numeric` field | `summarize_<e>` | `field*` (enum of numeric fields), `function*` (`sum`, `avg`, `min`, `max`), `group_by`, `limit`, `offset`, `timeout_ms` |
| a `tag` or `numeric` field | `filter_<e>` | `tag_conditions` `[{field* (enum of tag fields), value*, exclude}]`, `numeric_conditions` `[{field* (enum of numeric fields), min_value, max_value}]`, `sort_by`, `sort_order`, `limit`, `offset`. At least one condition is required |
| a `text` field | `search_<e>_by_text` | `query*`, `match_mode`, `limit`, `offset`, `sort_by`, `sort_order` |
| always, once | `intersect_results`, `union_results`, `except_results` | `sets*` (2-8 of `{entity, tag_conditions, numeric_conditions, ...}`), `limit`, `offset` |
| always, once | `expand_results` | `set*`, `relationship*` (a `ContextRelationship` name), `limit`, `offset` |

For Cairn: 3 x (`get_`, `list_`, `count_`, `summarize_`, `filter_`) = 15, plus
`search_customer_by_text` and `search_product_by_text` (Order has no text field), plus 4 set
tools = 21. The condition items' `field` enum is VERIFIED by inference: the live paging cell built
its condition from `items.properties.field.enum` and got rows back.

The three README talk-track claims, explained:

- **`Order.status` not indexed** removes `status` from the `field` enum of
  `filter_order.tag_conditions` and from `count_order.field`/`group_by`. No tool disappears,
  because Order keeps other tag fields. (Live: the default model only; the variant is UNVERIFIED.)
- **`Customer.name` as `tag` instead of `text`** leaves Customer with no text field, so
  `search_customer_by_text` disappears (20 tools), and `name` joins the tag `field` enums of
  `filter_customer` and `count_customer`. (UNVERIFIED live; follows the rule that made the 21.)
- **`allowed_values` on a tag** is exported as `"allowed_values": [...]` on the field. The SDK README
  says it "is exported as an enum constraint on the generated TAG-filter tool parameter"
  (VERIFIED text). On the unified surface `tag_conditions.value` is shared by every tag field, so
  where the enum lands, if anywhere, is **UNVERIFIED**; the fake service that mirrors the live
  schemas types `value` as a plain string. Do not promise the audience that `tier: "platinum"` is
  rejected with -32602. See 9(d) for a Break it that is certain to violate the schema.

Other rules the SDK enforces at class definition (VERIFIED by running):
- `int`/`float` field with `index="tag"` -> `ValueError: field 'n' is int-typed and cannot use a 'tag' index: RediSearch maps JSON numbers to NUMERIC only ...`. Ids such as `C001` must be `str`.
- `allowed_values` on a non-tag field -> `ValueError: s: allowed_values requires a tag index`.
  Also: 1-128 unique, non-blank strings, each up to 256 bytes.

Relationship names (`orders`, `customer`, `product`) are what `expand_results.relationship` should
accept; UNVERIFIED live, and not needed for the MVP.

## 5. The SDK, 0.0.6, for the data model only

### 5.1 Signatures (VERIFIED by `inspect.signature`)

```python
from context_surfaces import ContextField, ContextModel, ContextRelationship, export_data_model

ContextField(*, description: str, default=..., default_factory=None,
             index: str | list[str] | None = None,      # "text" | "tag" | "numeric" | "vector"; None = not indexed
             is_key_component: bool = False,
             allowed_values: list[str] | None = None,   # tag only
             vector_dim: int | None = None, distance_metric: str = "cosine",
             embeds: str | None = None, embedding_model: str | None = None,
             weight: float = 1.0, no_stem: bool = False,  # text
             sortable: bool | None = None,               # numeric; None lets the server decide
             **kwargs) -> Any
ContextRelationship(*, description: str, target: str | None = None, source_field: str | None = None)
export_data_model(title: str, description: str, entities: list[type[ContextModel]] | None = None) -> dict
```

- **Always pass `entities=[...]`.** With `None` the export includes every `ContextModel` subclass
  ever defined in the process (`ContextModelMeta._registry`, keyed by class name). In a marimo
  kernel that re-runs a cell, that registry is exactly the trap. VERIFIED: registry lists
  `Customer, Order, Product` after defining them.
- `__redis_key_template__` defaults to `f"{cls.__name__.lower()}:{{id}}"` (`context_model.py:431`).
  Set it explicitly to `f"{KEY_PREFIX}customer:{{customer_id}}"`. The service indexes the text
  before the first `{` as the index `PREFIX` (docs demo JS line 281; VERIFIED live by inference:
  the Customer index covered exactly 6 documents, so its prefix was the entity's, not the run's).
- The class docstring becomes the entity `description`. `ContextRelationship` fields are stripped
  from the Pydantic model (excluded from `model_dump()`) and exported under `relationships`.
- `__acl_field_mappings__` exports `acl_field_mappings` only when non-empty. Leave it out.

### 5.2 `export_data_model` output for Cairn (VERIFIED by running; 3,096 bytes)

```json
{"title": "Cairn Outfitters", "description": "...", "entity_count": 3, "entities": [
  {"name": "Customer", "description": "A shopper at Cairn Outfitters, an outdoor-gear shop.",
   "redis_key_template": "<prefix>customer:{customer_id}",
   "fields": [
     {"name": "customer_id", "type": "str", "description": "Customer ID such as C001", "is_key_component": true, "redis_indices": []},
     {"name": "name", "type": "str", "description": "Full name", "is_key_component": false, "redis_indices": [{"type": "text", "weight": 2.0}]},
     {"name": "tier", "type": "str", "description": "Loyalty tier", "is_key_component": false, "redis_indices": [{"type": "tag"}], "allowed_values": ["bronze", "silver", "gold"]},
     {"name": "region", "type": "str", "description": "Sales region: emea or apac", "is_key_component": false, "redis_indices": [{"type": "tag"}], "allowed_values": ["emea", "apac"]},
     {"name": "lifetime_value", "type": "float", "description": "Lifetime spend in USD", "is_key_component": false, "redis_indices": [{"type": "numeric", "sortable": true}]}],
   "relationships": [{"name": "orders", "target": "Order", "description": "Orders this customer placed", "source_field": "customer_id"}]},
  {"name": "Product", "...": "..."},
  {"name": "Order", "...": "...", "relationships": [
     {"name": "customer", "target": "Customer", "description": "Buyer", "source_field": "customer_id"},
     {"name": "product", "target": "Product", "description": "Item bought", "source_field": "sku"}]}]}
```

`type` is `str`, `int`, `float` or `list[float]`. `Customer(...).model_dump()` gives exactly the
five data fields, and `Customer.__redis_key_template__.format(**row)` gives the key the service
writes (VERIFIED: `<prefix>customer:C001`).

### 5.3 The SDK's clients: do not use them in the notebook

`ContextSurfacesClient`, `UnifiedClient` and `MCPClient` are async-only (`async with`, top-level
`await` in cells). `ContextSurfacesClient._request` retries every exception three times with
1-10 s backoff, including 400/401/404, so a wrong key takes about 3 s to fail and a create POST can
be replayed. `UnifiedClient.create_context_surface` has no `data_source` parameter, so it cannot
create a usable surface; `create_agent_key` cannot set `expires_at`. `MCPClient.call_tool` raises
`MCPError` only for JSON-RPC `error` or HTTP errors; `isError` results come back as plain dicts.
(All VERIFIED in the installed source and the prior brief's verifier notes.)

If the notebook wants to acknowledge the clients, one collapsed note is enough: "The SDK also
ships `ctxctl` and async clients that wrap these same calls; this notebook sends them itself so
you can read the wire." That is optional; the MVP can skip it.

### 5.4 Footprint and side effects (VERIFIED)

`redis-context-retriever==0.0.6` installs 79 packages (about 73 MB) including fastmcp, mcp,
httpx, tenacity, typer, keyring, cryptography, pyjwt; `import context_surfaces` takes about 2 s
and emits about 14 `PydanticDeprecatedSince20` warnings from the SDK's own models. Two
consequences for the rebuild: httpx stays in the lock as a transitive dependency even after the
notebook's own code moves to niquests (say so in pyproject with a comment), and pytest should
`filterwarnings` the SDK's deprecation warnings.

## 6. Python 3.14 breaks `ContextModel` (VERIFIED today)

On CPython 3.14.6 with pydantic 2.13, this fails at class-definition time:

```
class Customer(ContextModel):
    customer_id: str = ContextField(description="id", is_key_component=True)
pydantic.errors.PydanticUserError: Field 'customer_id' requires a type annotation
```

Cause: `ContextModelMeta.__new__` reads `namespace.get("__annotations__", {})`
(`context_model.py:248`), which is empty under PEP 649 deferred annotations, then rewrites
`namespace["__annotations__"]` before handing the class to Pydantic. A file-level
`from __future__ import annotations` fixes it in a plain script (VERIFIED today on 3.14.6) but
not inside a marimo notebook, where each cell is compiled on its own (VERIFIED by the prior
brief's verifier). Works on 3.11, 3.12.13 and 3.13.14.

For `pyproject.toml`:

```toml
# redis-context-retriever 0.0.6: ContextModel's metaclass reads namespace["__annotations__"],
# which Python 3.14's deferred annotations (PEP 649) leave empty inside marimo cells ->
# PydanticUserError "Field 'customer_id' requires a type annotation". Checked 2026-09-30 on 3.14.6.
requires-python = ">=3.11,<3.14"
```

plus a `.python-version` containing `3.13`. `uv run marimo edit iris_basics.py` then resolves 3.13
even though 3.14.6 is the newest interpreter on this machine (VERIFIED: `uv python list`). An
optional one-line guard in the model cell, `mo.stop(sys.version_info >= (3, 14), mo.callout(...))`,
covers a hand-started kernel.

## 7. Safety for the MVP

### 7.1 The password on the wire

The create body forwards the database password in `data_source.connection_config.password`,
which the hosted service stores (encrypted at rest per the self-managed docs, `SECRET_ENCRYPTION_KEY`)
and uses for the surface's lifetime. Rules:

- Show the create request only through a masking step: copy the body and replace every
  `connection_config` value except `addr` with `****` (the old notebook's `redact()` rule).
- Prefer a scratch database. The service holds the default user's full rights until the surface is
  deleted. A Redis Cloud data-access role could narrow that, but whether the service works under a
  restricted user is UNVERIFIED (it needs at least `FT.CREATE`, `FT.SEARCH`, `FT.DROPINDEX`,
  `JSON.SET`, `JSON.GET`, `SCAN`).
- The consent copy on the Create button should say what happens in one sentence: "Create sends
  your database password to the hosted Context Retriever service, which keeps it to reach your
  database until you delete the surface."

### 7.2 Keys and tags

- Surface name `iris-basics-<run>` and `metadata: {"created_by": "iris-basics-notebook", "run": "<run>"}`.
- Key templates start with the shared run prefix and end in `:{<pk>}`, for example
  `<KEY_PREFIX>cr:customer:{customer_id}`. Assert every template starts with the prefix before
  sending. Ids without hyphens or spaces (TAG syntax treats `-` specially; UNVERIFIED but cheap).
- After import, `EXPIRE` each of the 20 keys by name with the shared TTL (the service does not set
  one). VERIFIED live: 20 of 20 keys existed to expire. When a document expires, it leaves the index.
- Agent key: `expires_at` = now + the same TTL. The live run used 7200 s for both.
- Surfaces have no TTL. A forgotten surface keeps your password and its indexes. The only
  automatic backstop is the expired agent key, so Clean up matters; say so in the column.
- Agent-key reply: never a cell's last expression, never in `mo.md`. Show `key[-4:]` at most.

### 7.3 What "delete surface" removes and leaves

| | After `DELETE /api/v1/context-surfaces/{id}` |
|---|---|
| Surface, entity model, generated tools | removed (VERIFIED docs; live `GET .../{id}` -> 404) |
| Agent keys of the surface | terminated (VERIFIED docs, view-service.md) |
| RediSearch indexes `idx:<surface-id>:<hash>:<entity>` | dropped by the service together with the surface (VERIFIED live, three of three, both runs) |
| JSON documents at the key-template names | **stay** ("Data stored in the underlying Redis Cloud database remains unaffected", VERIFIED docs and live) |

So Clean up is: `DELETE` the surface, `UNLINK` the 20 keys by name (computed from the templates,
never a `SCAN`-and-delete), then `FT._LIST` and, for any index still present whose every
`index_definition.prefixes` entry starts with the run prefix, `FT.DROPINDEX <name>` without `DD`.
The last step is a belt-and-braces check that should find nothing. Never `FLUSHDB`, never `DD`,
never delete by name pattern outside the run prefix.

### 7.4 Key/index hygiene on a shared database

Other apps on the same database (Iris demo domains `radish_bank_*`, `reddash_*`, `electrohub_*`,
`workshop_*`, `healthcare_*`, Agent Memory's `memory:*`) are untouched as long as the run prefix
is unique. `redis-iris-demos` `make setup`/`make reset` flush everything but `memory:*`; do not run
them during a session.

## 8. Redis client patterns for the peek and the cleanup

Only the notebook's own reads and the cleanup touch Redis; the service does the writes and the
indexing. redis-py 8.1.0 facts (VERIFIED by introspection) and the docs' production checklist
(`develop/clients/redis-py/produsage.md`, VERIFIED text):

```python
from redis import Redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

redis_client = Redis.from_url(
    REDIS_URL,                      # rediss:// -> TLS; userinfo is percent-decoded by redis-py
    decode_responses=True,
    socket_connect_timeout=5, socket_timeout=5,   # docs: set both; default is 10 s
    health_check_interval=30,                     # docs: PING after idle
    retry=Retry(ExponentialBackoff(), 3),         # docs: retries with jittered backoff
)
redis_client.json().get(key)                      # JSON.GET, parsed
redis_client.execute_command("FT._LIST")          # no wrapper in redis-py 8.1.0 (only FT.ALIASLIST)
redis_client.ft(index_name).info()                # dict; index_definition is a flat list on RESP2:
                                                  #   d = info["index_definition"]; dict(zip(d[::2], d[1::2]))["prefixes"]
redis_client.ft(index_name).dropindex(delete_documents=False)   # never True
redis_client.expire(key, TTL); redis_client.unlink(*keys)
```

`decode_responses=True` matters for the peek: `FT._LIST`, `FT.INFO` and `JSON.GET` come back as
`str`, not `bytes`. The live run saw Redis 8.6.2, where a missing index reads
`SEARCH_INDEX_NOT_FOUND Index not found: <name>`; treat that `ResponseError` as "already gone" in
the cleanup, not as a failure.

## 9. MVP teaching sequence and what to drop

Keep six beats, in this order. Each is one or two cells.

**(a) Define the model, offline.** Three `ContextModel` classes in the notebook (they are the
Iris API the audience came for), the 20 Cairn records imported from `src/` (sample data may move),
`export_data_model(..., entities=[Customer, Product, Order])` shown with `mo.json` inside an
accordion, and a six-row markdown table "index type -> tool" from section 4. Runs with no
credentials, so the pytest `app.run()` without secrets exercises it.

**(b) Create the surface and an agent key, one consent button.** `mo.ui.run_button` whose label
and the sentence above it say the password is sent. The action cell: `FT._LIST` snapshot, POST
surface (201), POST agent key with `expires_at` (201), GET agent keys for `mcp_url`, POST data
three times, `EXPIRE` 20 keys. Output: one success callout (surface id, status, records imported,
key expiry) and the masked create request in an accordion. Then `tools/list` into `mo.ui.table`
with columns tool, description, arguments (required starred), built from `inputSchema`.

**(c) Call one tool.** `tools/call get_customer_by_id {"id": "C001"}`. Show the JSON-RPC request
and the parsed `result.content[0].text` side by side (`mo.hstack` of two `mo.json`), and one gloss:
the `_retrieval` stamp says which surface served it and when. Read-only, so no button.

**(d) Break it.** Send a call the schema forbids and show `error.code` and `error.message`
verbatim in a red callout. Pick the violation that is certain from the live schema shape: a tag
condition on a numeric field,
`filter_customer {"tag_conditions": [{"field": "lifetime_value", "value": "4200"}]}` (`field` is an
enum of tag fields, VERIFIED by inference). The `allowed_values` violation (`tier: "platinum"`) is
the one the audience expects but its outcome is UNVERIFIED; if the implementer wants it, send it
second and word the copy as "watch what the service does". If the call is accepted, show that as
a surprise callout rather than crashing. Read-only, so no button.

**(e) Peek at your own Redis.** `JSON.GET <first key>` pretty-printed, and the `FT._LIST` diff
against the snapshot from (b): one new index per entity, named `idx:<surface-id>:<hash>:<entity>`,
with `num_docs` from `FT.INFO` (6, 6, 8). One sentence: the agent never saw Redis; the service
wrote here and reads here.

**(f) Clean up.** One `mo.ui.run_button(kind="danger")`: DELETE surface, UNLINK 20 keys, `FT._LIST`
again to show the indexes are gone (drop any run-prefixed straggler without `DD`). Report counts.

**Drop:** the field editor with kept edits and reset (7 helpers, 4 cells), `cr_unified_tools` /
`cr_predict_tools` / `cr_tool_diff` prediction machinery, the tool explorer form generator
(`cr_widget`, `cr_arguments`, `cr_example`, `cr_enums`, `cr_ruled`, `cr_break_plan`), paging
sliders, orphans panel and typed confirmation, the surface listing and `cr_adopt` reuse path,
the ledger, `Reset`, presenter notes, recorded-live panel, the deep-dive catalogue, endpoint text
fields (env only), and the connection probe as a separate step (fold a `PING` into settings).
The prediction cannot be one cell without the predictor, so it goes; the live `tools/list` table
is the proof and the markdown table is the lesson.

## 10. Open questions

1. Where `allowed_values` surfaces on the unified tool schema, and whether an out-of-menu tag
   value is rejected (-32602), returns zero rows, or comes back as `isError`. Only a live call
   settles it. The MVP's primary Break it does not depend on it.
2. The exact live text and HTTP status of a -32602 rejection. Design the cell to print
   `error.code` and `error.message` as received.
3. Whether the live create reply's `tools` list is populated (would save the `tools/list` call
   for the names, but not for `inputSchema`, so the MVP calls `tools/list` anyway).
4. Whether the MCP endpoint accepts niquests' default headers exactly as it did httpx's; the
   header values are identical (`Accept: */*`, JSON body).
5. Which of 200/204 `DELETE` returns and which of 200/201 import returns. Accept any 2xx.
6. Whether a Redis Cloud data-access role scoped to the run prefix lets the service build and
   query its indexes. If yes, `REDIS_URL` could carry a narrowed user instead of `default`.
7. Whether an account outside `gcp-us-east4` gets a different `mcp_url`; the listing call covers it.
8. Whether `status` ever stays at `provisioning_indices` long enough for the first import to
   fail. One live run says no; `ctxctl` polls after updates, so the service allows for it.

## 11. Recommended MVP cell list for column 2 (11 cells, all `column=2`)

Names use the `cr_` prefix and full words. Buttons are read in the cell after the one that
defines them (marimo rule). Copy follows the voice guide: sentence case, "you", Redis in the
third person, flat verbs for what the service does, no exclamation marks, preview status stated
flat once.

| # | Cell (defines) | What it does | Notes |
|---|---|---|---|
| 1 | `cr_title` | Markdown: "Context Retriever: a data model becomes governed MCP tools over your own Redis data. Context Retriever is in preview." Docs link. Small mermaid: `ContextModel -> export_data_model -> POST /context-surfaces -> surface (tools)`; `agent key -> POST /mcp tools/call -> service -> your Redis (JSON + index)` | hide_code |
| 2 | `cr_admin_key, cr_redis_url, cr_api_url, cr_mcp_url_default, cr_redis` | Read `os.environ` (marimo loaded `.env` next to `pyproject.toml`). `mo.stop(missing, callout("Not configured: set CTX_ADMIN_KEY and REDIS_URL in .env"))`. Build the redis-py client from section 8 and `PING`. | one "not configured" note and stop |
| 3 | `Customer, Product, Order, cr_data_model, cr_record_keys` | The three classes with `__redis_key_template__ = f"{KEY_PREFIX}cr:<entity>:{{<pk>}}"`; records from `src`; `export_data_model(..., entities=[...])`; assert templates start with the prefix. Output: the index-to-tool table and `mo.json(cr_data_model)` in an accordion. Optional 3.14 guard. | runs offline; the unit test target |
| 4 | `cr_create_button` | Markdown listing the five calls and the consent sentence; `mo.ui.run_button(label="Create the surface and load 20 records")` | |
| 5 | `cr_surface_id, cr_agent_key, cr_mcp_url, cr_indexes_before` | `mo.stop(not cr_create_button.value)`. `FT._LIST` snapshot; POST surface; POST agent key with `expires_at`; GET agent keys for `mcp_url` (fallback `CTX_MCP_URL`, then default); POST data x3 via `Model(**row).model_dump()`; `EXPIRE` each key. Output: success callout + masked request accordion. | never returns the raw reply as the last expression |
| 6 | `cr_tools` | `tools/list` via `niquests.post(cr_mcp_url, json=..., headers={"X-API-Key": cr_agent_key}, timeout=30)`; `mo.ui.table` of name, description, arguments (from `inputSchema.properties`, required starred) with a count in the caption ("21 tools from 3 entities") | schema-driven, no hardcoded names |
| 7 | `cr_customer_reply` | `tools/call get_customer_by_id {"id": "C001"}`; `mo.hstack([mo.json(request), mo.json(parsed_text)])`; gloss on `_retrieval` and on "the agent never sends a query" | read-only; also handle `isError` |
| 8 | `cr_broken_reply` | `tools/call filter_customer {"tag_conditions": [{"field": "lifetime_value", "value": "4200"}]}`; red callout with `error.code` and `error.message` verbatim; surprise callout if accepted | read-only; optional second call with `tier: "platinum"` |
| 9 | `cr_peek` | `JSON.GET` first key -> code block; `FT._LIST` now minus snapshot -> list with `FT.INFO num_docs`; one gloss | read-only |
| 10 | `cr_cleanup_button` | Markdown: what Clean up deletes and what stays (the docs sentence), then `mo.ui.run_button(kind="danger", label="Clean up")` | |
| 11 | (cleanup action) | `mo.stop(not cr_cleanup_button.value)`. DELETE surface (200/204/404 all fine); `UNLINK *cr_record_keys`; `FT._LIST` and drop run-prefixed stragglers without `DD`; report "Deleted 1 surface, unlinked 20 keys, 0 indexes left" | depends only on cells 2, 3 and 5's ids so it works when a middle cell failed |

Helpers that stay in the notebook because they are the Iris lesson: `cr_connection_config(url)`
(8 lines), `cr_admin_call(method, path, json=None)` (5 lines around `niquests.request`),
`cr_mcp_call(method, params)` (6 lines returning the parsed JSON-RPC reply), and the masking
one-liner for the create body. Candidates for `src/` without hiding Iris details: the Cairn
records, a `pretty_json()`/`mask_connection_config()` display helper, and the redis-py client
factory from section 8 (shared with column 0).

Test hooks: `app.run()` with no credentials must show cell 2's "not configured" note and still
run cells 1 and 3; a unit test asserts `cr_data_model["entity_count"] == 3`, that every
`redis_key_template` starts with the prefix, and that `len(cr_record_keys) == 20`.
