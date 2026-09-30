# Research: context-retriever

> Generated 2026-09-29 by the iris-basics-research workflow (researcher + adversarial verifier).
> **The Verifier corrections section OVERRIDES the report body where they conflict.** Treat `verified: false` facts as hypotheses.

## Summary

Context Retriever turns Pydantic-style ContextModel classes into a Redis-hosted MCP server. You POST the exported data model plus your Redis connection_config to the admin REST API (POST /api/v1/context-surfaces, 201). You then push records through POST /api/v1/context-surfaces/{id}/data (UnifiedClient.import_data). The service, not the caller, writes RedisJSON documents and creates the RediSearch indexes. Agents then call JSON-RPC 2.0 tools/list and tools/call over HTTP with an agent key in X-API-Key. The SDK is now `redis-context-retriever` 0.0.6 (import name still `context_surfaces`); PyPI `context-surfaces` is a deprecated shim. Three things will bite the implementer. (1) ContextModel classes crash on Python 3.14 (verified by running), so the PEP 723 header must cap requires-python at <3.14. (2) The live tool surface is in flux. The workshop's recorded output shows classic per-field tools (filter_x_by_field taking `value`, search_x_by_text taking `query`). The Redis docs' interactive demo, committed 2026-09-28 and described as mirroring the live server, shows a unified surface: get_x_by_id{id}, filter_x{tag_conditions,numeric_conditions,text_query,...}, count_x, intersect/union/except_results, expand_results. The wow cell must therefore build its form from each tool's inputSchema, not from hardcoded names. (3) Deleting a surface does not delete your data or (probably) its indexes, so the notebook must namespace keys per session, set TTLs, and clean up with redis-py. Setup is heavier than the other columns: a Redis Cloud DB with Search+JSON that the hosted service can reach, plus a Context Retriever admin key. The plan below keeps it to two inputs (REDIS_URL, CTX_ADMIN_KEY), runs the first three cells with no credentials, and includes a validated 20-record, 3-entity inline dataset and a tested schema-driven tool-explorer prototype. No live service was called.

## Verifier corrections (authoritative)

- **Claim:** 'The SDK raises MCPError on either' (a JSON-RPC error or a tool-level result.isError:true)
  - **Correction:** MCPClient.call_tool raises MCPError only when the JSON-RPC response has an 'error' key or on an HTTP error. A tool-level failure ({result:{content:[...],isError:true}}) is returned as a normal dict from query_tool. The notebook must check res.get('isError') explicitly and must not rely on try/except. The repo's ContextSurfaceService.call_tool likewise only JSON-decodes content[0].text and falls back to {'raw_text': text}.
  - **Source:** context_surfaces/mcp_client.py:143-194 (0.0.6 installed); redis-iris-demos/notebooks/workshop_helpers.py:213-229
- **Claim:** Citations constants.py:215-217, config.py:122-126 / 122-144, and mcp_client.py:444-531
  - **Correction:** The values are right but the line numbers are wrong. constants.py has 54 lines (URLs at 6-7). config.py has 47 lines (_CONFIG_MAP and __getattr__ at 24-46). mcp_client.py has 236 lines (list_tools at 107, call_tool at 143, initialize at 196).
  - **Source:** wc -l on installed context_surfaces 0.0.6 files; cat -n
- **Claim:** Safety note: 'There is no ACL user scoping on Redis Cloud, so the service gets the default user's full rights on that DB.'
  - **Correction:** Redis Cloud supports role-based access control: data-access roles with custom Redis ACL rules, including key patterns such as '+@all ~app:*', configured in the console. Only ACL SETUSER and similar commands are unsupported. You could pass a restricted user in connection_config.username/password instead of 'default'. Whether the Context Retriever service works under a restricted role (it needs FT.CREATE, JSON.*, SCAN and so on) is unverified.
  - **Source:** https://redis.io/docs/latest/operate/rc/security/access-control/data-access-control/configure-acls/ ; https://redis.io/docs/latest/operate/rc/databases/connect/ ('If you've turned on Role-based access control ... use the username and password for your data access role')
- **Claim:** Dead-man switch 'EXPIRE ... about 2 h, matching the existing notebook's TTL pattern' (TTL = 7200)
  - **Correction:** The existing notebook uses TTL = 3600 (1 h) and PREFIX = f'demo:{RUN}:'. 7200 does not match it. The existing notebook also wraps redis-py in a command allowlist that lacks JSON.GET, which the proposed 'teaching peek' would need.
  - **Source:** C:/Users/NickCooper/projects/demo/redis-iris-basics/notebook.py:41-58
- **Claim:** Section 10 plumbing is 'validated offline': conn = {..., 'username': u.username or 'default', 'password': u.password or ''} from urlparse(REDIS_URL)
  - **Correction:** urllib.parse.urlparse does not percent-decode userinfo, so 'p%40ss%2Fw' is forwarded to the service as-is. redis-py's from_url, which the same notebook uses for the peek and cleanup, decodes it to 'p@ss/w'. This gives a confusing split failure. Use urllib.parse.unquote(u.password) and unquote(u.username).
  - **Source:** ran urlparse('rediss://default:p%40ss%2Fw@host.example:12345').password -> 'p%40ss%2Fw' vs redis.Redis.from_url(...).connection_pool.connection_kwargs['password'] -> 'p@ss/w' (redis 8.1.0)
- **Claim:** Search+JSON requirement for the target DB sourced to the self-managed page's 'Redis database' section
  - **Correction:** That section ('Redis database (bring your own) ... must have the Search and JSON capabilities') describes the database the Helm-installed service itself is deployed against (redis.addr / redis.tlsEnabled). It is not the per-surface data_source on Redis Cloud. For Redis Cloud data DBs the requirement is inferred, though strongly: the demo JS emits FT.CREATE ... ON JSON and the commit message says Context Retriever only reads JSON keys. The self-managed product is also 'private preview' and needs a CS-LICENSE key.
  - **Source:** https://redis.io/docs/latest/operate/iris/context-retriever/self-managed (What you need; Production notes); redis/docs commit 9cad0b4d message
- **Claim:** Iris demo app key prefixes are radish_bank_*, reddash_*, electrohub_*, workshop_*
  - **Correction:** The list is incomplete. The repo has 7 domains, which add healthcare_*, rmobile_*, sports_betting_* and finance_researcher_* (plus checkpoint prefixes such as 'electrohub:checkpoint'). The conclusion that irisnb_<run>_ does not overlap still holds.
  - **Source:** grep redis_key_template in redis-iris-demos/domains/*/schema.py; domains/electrohub/domain.py:108-109
- **Claim:** Cleanup pseudo-code `FT.INFO(idx).index_definition.prefixes`
  - **Correction:** This is not a redis-py API. r.ft(idx).info() returns a dict. info['index_definition'] is not normalised by redis-py 8.1 and on RESP2 is a flat list [key_type, JSON, prefixes, [...], ...], so parse it with pairs, for example dict(zip(d[::2], d[1::2]))['prefixes'].
  - **Source:** redis 8.1.0 redis/commands/search/commands.py:274-276 (_parse_info) and 413-424 (_parse_info_unified only normalises 'attributes')

Verifier overall reliability: **high**

### Verifier: additional facts

- The admin-keys docs say the tab is usable 'After you have created your first Context Retriever service', so a customer with no service may not see it. The quickstart gives a console-free route: `ctxctl auth login -u <email>` (prompts for the Redis Cloud password and stores credentials in the OS keyring), then `ctxctl --output json admin create --name X`, which returns a cs_admin_ key. SSO-only accounts are unverified.  _(src: https://redis.io/docs/latest/operate/iris/context-retriever/view-admin-keys ; https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/quickstart/ ; `ctxctl auth login --help` and cli/main.py:553-676)_
- MCPError is a bare string exception with no code or data fields. call_tool also re-wraps its own MCPError, giving messages like 'Failed to call tool X: MCP error calling X: {...}'. A 'break it' cell that wants to show code -32602 and the message cleanly should POST the JSON-RPC itself with httpx.  _(src: context_surfaces/exceptions.py:55-56; mcp_client.py:185-194)_
- ContextSurfacesClient(max_retries=...) is stored but ignored; the retry count is hardcoded to 3 in the decorator. UnifiedClient.import_data also goes through _request, so a failed import POST is replayed up to 3 times. This is idempotent with on_conflict='overwrite' but not with 'error'. validate_key sends the key only in the body, with no X-API-Key header.  _(src: context_surfaces/client.py:109-127,172-176,406-428; unified_client.py:327-334)_
- SDK 0.0.6 added ctxcli --sort-by/--sort-order flags, forwarded only when a tool's inputSchema declares sort_by/sort_order (along with 'by'). Newer tool schemas may therefore carry sort args that the report's classic argument list omits. This is another reason to build forms from inputSchema.  _(src: cli/ctxcli/dynamic.py:192-211 (0.0.6) vs 0.0.5 diff)_
- The Iris tutorial's sample .env uses CTX_ADMIN_API_URL, but the SDK reads CTX_API_URL. That variable is silently ignored; this is harmless on Redis Cloud because the defaults are identical. redis-iris-demos .env/.env.example define only REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_SSL, REDIS_USERNAME, REDIS_DB, CTX_ADMIN_KEY, MCP_AGENT_KEY and CTX_SURFACE_ID (names only), with no CTX_MCP_URL.  _(src: tutorial HTML at https://redis.io/tutorials/redis-iris-call-agent/ ; context_surfaces/config.py:24-28; grep -o '^[A-Z_]*=' on .env and .env.example)_
- In a plain Python script (not marimo) on 3.14.6, a file-level `from __future__ import annotations` does fix ContextModel. The failure is specific to marimo's per-cell compilation. The PEP 723 cap only protects `--sandbox` or `uv run` launches, so add a runtime guard (sys.version_info < (3,14)) with a clear message in the setup cell.  _(src: ran /tmp/fc/s/models_future.py on p3.14 (success) vs mo314d.py (failure))_
- Design risk: the report says to put the dataset in 'verbatim' with __acl_field_mappings__ on Customer and Order, yet the main-path agent key is created with no access_tags. How acl mappings treat unscoped keys is unknown, and the core demo could return zero rows. Keep ACL mappings opt-in (a separate surface or toggle) until verified live.  _(src: report section 9 vs unknowns; no repo or doc example of the unscoped behaviour)_
- In the unified demo, count_{e} is generated only for entities with tag or numeric fields, and filter_{e} only if the entity has any tag, numeric or text field. filter_* and set tools type limit/offset as 'number', while count_* limit is 'integer' (1-100, default 10). The set tools and count_* declare additionalProperties:false; filter_* does not.  _(src: redis/docs static/js/context-retriever-demo.js:173-266)_
- The demo JS commit states Context Retriever reads only JSON documents (a key template pointing at a hash matches nothing). This backs using import_data over the quickstart's HSET step.  _(src: redis/docs commit 9cad0b4d message ('Count only JSON keys in the demo's key-template badge'); context-retriever-demo.js rowsFor())_
- The healthcare example's 43-tool list and its query/value calls were produced against a local dev stack (http://localhost:8081/mcp), not the hosted service. Only the radish_bank workshop outputs plausibly come from the hosted service.  _(src: sdist examples/healthcare_context_surface_example/healthcare_guide.ipynb cell 26)_

### Verifier: unverifiable claims

- Which tool surface the hosted service returns today, classic or unified. The docs prose (quickstart, Iris tutorial) and the SDK 0.0.6 CLI transformer (patterns filter_/get_/search_/find_ only, cli/ctxcli/transformer.py:51-58) are all classic. Only the demo JS (2026-09-28) claims the unified surface is live.
- That the hosted create-surface response includes a populated `tools` list. It is seen only in the SDK model and in the healthcare example, which ran against http://localhost:8080/8081 dev servers (healthcare_guide.ipynb cell 26), not cloud.redis.io. I did not check the TS-SDK api-types claim.
- Console deep links https://cloud.redis.io/#/context-retriever and /#/context-retriever/admin-keys. They appear in the Iris tutorial HTML, but SPA hash routes cannot be checked without login.
- Live status codes for DELETE surface (200), DELETE agent key (204) and import (200). These come only from SDK mocks.
- That the default httpx `Accept: */*` works against the hosted MCP endpoint. This is only implied by the workshop's recorded outputs, which went through UnifiedClient.
- Access-tag semantics end to end, including what an unscoped agent key sees on entities that declare __acl_field_mappings__.
- Whether surface deletion drops the RediSearch indexes, and the real index names.
- Whether a free Essentials DB works (public endpoint reachability from the hosted service, TLS on free endpoints, CIDR allow rules). The owner's README claim that 'Search and query' must be ticked at creation is in README.md:33-36 but I did not confirm it against Redis docs.
- Whether pool_size=5/min_idle_conns=1 is sufficient (this is inferred).
- Whether PUT with a changed data_model rebuilds indexes over existing data.
- The report's own scratch prototypes (nb_wow.py, nb_wow2.py, final_models.py). I re-ran equivalent code for the dataset, the dynamic dictionary form and top-level await, but not the full widget_for/coerce/example_for prototype.

## Docs URLs

- [Redis Context Retriever (overview)](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/) — verified: True
- [Context Retriever concepts](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/concepts/) — verified: True
- [Context Retriever quickstart (ctxctl walkthrough)](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/quickstart/) — verified: True
- [Context Retriever interactive demo (zero-credential fallback)](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/interactive-demo/) — verified: True
- [Create a Context Retriever service (Redis Cloud console)](https://redis.io/docs/latest/operate/iris/context-retriever/create-service) — verified: True
- [View and manage service (delete semantics)](https://redis.io/docs/latest/operate/iris/context-retriever/view-service) — verified: True
- [Manage admin keys (where to get CTX_ADMIN_KEY)](https://redis.io/docs/latest/operate/iris/context-retriever/view-admin-keys) — verified: True
- [Self-managed install (DB requirements, env vars)](https://redis.io/docs/latest/operate/iris/context-retriever/self-managed) — verified: True
- [PyPI: redis-context-retriever](https://pypi.org/project/redis-context-retriever/) — verified: True
- [Redis Cloud console: Context Retriever services](https://cloud.redis.io/#/context-retriever) — verified: False
- [Redis Cloud console: Context Retriever admin keys](https://cloud.redis.io/#/context-retriever/admin-keys) — verified: False
- [Connect to a Redis Cloud database (host/port/password location)](https://redis.io/docs/latest/operate/rc/databases/connect/) — verified: True
- [Redis Cloud Essentials plans (free tier limits)](https://redis.io/docs/latest/operate/rc/subscriptions/view-essentials-subscription/essentials-plan-details/) — verified: True
- [Iris tutorial (tool naming, index-to-tool mapping, deep links)](https://redis.io/tutorials/redis-iris-call-agent/) — verified: True
- [redis/docs source of the interactive demo (unified tool schemas)](https://github.com/redis/docs/blob/main/static/js/context-retriever-demo.js) — verified: True

## Secrets

- **CTX_ADMIN_KEY** — Sign in at cloud.redis.io -> left-hand menu 'Context Retriever' -> 'Admin keys' tab -> 'New admin key' -> enter a name -> 'Generate key' -> 'Copy'. The value is shown only once. It starts with cs_admin_ per the quickstart. It authorises creating and deleting any surface in the account, so use mo.ui.text(kind='password') and never print or persist it. (https://cloud.redis.io/#/context-retriever/admin-keys)
- **REDIS_URL (host, port, default-user password; rediss:// if TLS is enabled)** — Redis Cloud console -> Databases -> pick the database -> 'Configuration' tab -> 'Access' section (Essentials) for the public endpoint host:port. For the password use Default user -> Configure -> eye icon. The database must have Search and JSON enabled (tick 'Search and query' at creation on Essentials), be Redis >= 7.4, not be Flex or Active-Active, and have a public endpoint the hosted service can reach. The password is forwarded to the Context Retriever service inside connection_config. Prefer a throwaway demo DB. (https://cloud.redis.io/#/databases)
- **CTX_API_URL / CTX_MCP_URL (optional)** — Not needed on Redis Cloud (defaults https://cloud.redis.io/context-surfaces and https://gcp-us-east4.context-surfaces.redis.io/mcp). Only for self-managed installs (ingress URLs from your Helm deployment) or a different MCP region (use `mcp_url` from the list-agent-keys response). Set via os.environ before constructing UnifiedClient, or pass api_url=/mcp_url= explicitly. (https://redis.io/docs/latest/operate/iris/context-retriever/self-managed)

## API surface

## 1. Packages / versions (verified from PyPI JSON + installed source)
- `redis-context-retriever` **0.0.6** (2026-07-27; 0.0.5 = 2026-05-19). Python >=3.11 declared. Deps: fastmcp, httpx, pydantic>=2.6, tenacity, typer, keyring, cryptography, rich, pyjwt, pyyaml... (about 79 packages, 73 MB site-packages on Windows; `import context_surfaces` takes about 2 s).
- `context-surfaces` 0.0.6 / 0.0.5 = **compat shim** ("Please depend on redis-context-retriever directly"); the redis-iris-demos workshop pins it (uv.lock:326-333 -> 0.0.5). Import name is unchanged: `context_surfaces`.
- 0.0.5 -> 0.0.6 added: `ContextField(allowed_values=[...], embeds=..., embedding_model=...)`, a ValueError for int/float fields with `index="tag"`, and `sortable` default None (server decides).
- **Python 3.14 breaks ContextModel** (PEP 649 lazy annotations vs the metaclass reading `namespace["__annotations__"]`): `PydanticUserError: Field 'customer_id' requires a type annotation`. Verified on 3.14.6, even with a file-level `from __future__ import annotations` inside a marimo app. Works on 3.12.13 and 3.13.14. Use:
```
# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["marimo==0.24.2", "redis-context-retriever==0.0.6", "httpx>=0.28,<1", "redis==8.1.0"]
# ///
```
(marimo 0.24.2 and redis 8.1.0 match the existing notebook.py; marimo latest is 0.25.0.)

## 2. URLs / env (context_surfaces/constants.py:215-217, config.py:122-126; env read lazily at attribute access)
- Admin API default `https://cloud.redis.io/context-surfaces` (env `CTX_API_URL`). SDK sends to `<base>/api/v1/...` (trailing slash normalised).
- MCP default `https://gcp-us-east4.context-surfaces.redis.io/mcp` (env `CTX_MCP_URL`). Docs say the endpoint is `https://<region>.context-surfaces.redis.io/mcp`. `AgentKeyListItem.mcp_url` ("public MCP URL for this key") exists in the list-keys response.
- `from context_surfaces import config as cs_config; str(cs_config.api_url).rstrip('/')` is what redis-iris-demos uses.

## 3. Modelling API (context_surfaces/context_model.py)
```python
from context_surfaces import ContextField, ContextModel, ContextRelationship, export_data_model
ContextField(*, description: str, default=..., default_factory=None,
    index: str | list[str] | None = None,            # "text" | "tag" | "numeric" | "vector"
    is_key_component: bool = False,                   # exactly the PK field; no index by default
    allowed_values: list[str] | None = None,         # tag only; 1-128 unique non-blank strs <=256 B; becomes enum on the tag-filter param
    vector_dim: int | None = None, distance_metric: str = "cosine",   # cosine | euclidean | dot_product
    embeds: str | None = None, embedding_model: str | None = None,    # vector only; server-side text->semantic tool
    weight: float = 1.0, no_stem: bool = False,       # text
    sortable: bool | None = None)                     # numeric
ContextRelationship(*, description: str, target: str | None = None, source_field: str | None = None)   # read-only descriptor, excluded from model_dump()
class X(ContextModel):
    __redis_key_template__ = "prefix:{key_field_name}"        # default f"{cls.__name__.lower()}:{{id}}"
    __acl_field_mappings__ = [{"document_field": "$.region", "access_tag": "region"}]   # optional, see section 8
export_data_model(title: str, description: str, entities: list[type[ContextModel]] | None = None) -> dict
```
- ALWAYS pass `entities=[...]`; with None it exports every ContextModel ever defined in the process (registry keyed by class name).
- Export shape (snake_case): `{"title","description","entity_count","entities":[{"name","description"(=class docstring),"redis_key_template","fields":[{"name","type":"str|int|float|list[float]","description","is_key_component","redis_indices":[{"type":"tag"}|{"type":"text","weight":2.0}|{"type":"numeric","sortable":true}|{"type":"vector","vector_dim":1536,"distance_metric":"cosine","source_field"?,"embedding_model"?}],"allowed_values"?}],"relationships":[{"name","target","description","source_field"}],"acl_field_mappings"?}]}`.
- Tag-indexed IDs must be `str`. Use `str` for ids like C001. Wrong `int`+tag raises at class-definition time.

## 4. Clients (all async-only; no sync client; use top-level `await` in marimo cells, which works in 0.24.2 and in `python nb.py` script mode)
```python
UnifiedClient(api_url: str|None=None, mcp_url: str|None=None, timeout: float=30.0)   # async with ... as client
  await client.list_tools(agent_key: str) -> list[dict]            # raw MCP tools: {"name","description","inputSchema"}
  await client.query_tool(agent_key: str, tool_name: str, arguments: dict|None=None) -> dict   # MCP result: {"content":[{"type":"text","text":"<JSON string>"}], "isError"?: bool}
  await client.import_data(admin_key, context_surface_id, records: list[ContextModel],
        on_conflict: "overwrite"|"skip"|"error"="overwrite", on_error: "continue"|"fail_fast"="continue") -> DataImportResponse(imported:int, failed:int, errors:[{index,record_id,error}])
  await client.create_context_surface(admin_key, name, data_model=None, description="")   # NO data_source param -> unusable for real surfaces
  await client.create_agent_key(admin_key, surface_id, name, description="")               # hardcodes access_tags=None
  await client.create_admin_key(name, owner, description="")
ContextSurfacesClient(base_url=None, timeout=30.0, max_retries=3)   # REST; every method: (..., admin_key=str)
  health(), readiness(), liveness(), validate_key(key), list_context_surfaces(admin_key=, page=1, page_size=20),
  get_context_surface(id), create_context_surface(CreateContextSurfaceRequest), update_context_surface(id, UpdateContextSurfaceRequest),
  delete_context_surface(id)->dict, create_agent_key(surface_id, CreateAgentKeyRequest), list_agent_keys(surface_id,page,page_size), delete_agent_key(surface_id, agent_key_id)->None
MCPClient(mcp_url=None, agent_key=None, timeout=30.0)   # plain httpx JSON-RPC: list_tools(), call_tool(name, arguments), initialize(); raises MCPError
```
- `ContextSurfacesClient._request` is decorated with tenacity `stop_after_attempt(3)` + exponential wait 1-10 s with NO retry predicate. It retries every exception, including 400/401/404. A bad admin key therefore takes about 3 s to fail, and a POST may be replayed after a transient error.
- `CreateContextSurfaceRequest` parses the response into `ContextSurface`, which requires `owner`, `created_at`, `updated_at`. The workshop deliberately uses raw httpx and reads `["id"]`, `["key"]`. Do the same for create.
- The SDK never sends `initialize`, and the plain `Accept: */*` default works. The docs' curl example uses `Accept: application/json, text/event-stream` if you go SDK-free.

## 5. Admin REST (header `X-API-Key: <admin key>`, `Content-Type: application/json`; JSON is snake_case)
| Op | Request | Success |
|---|---|---|
| create surface | `POST /api/v1/context-surfaces` body `{"name"(1-255),"description"(<=1000),"metadata":{str:str},"data_model":{...export...},"data_source":{"type":"redis","connection_config":{"addr":"host:port","username":"default","password":"...","db":0,"tls_enabled":true,"pool_size":10,"min_idle_conns":2}}}` (data_source REQUIRED when data_model given) | **201** `{id,name,description,owner,tools:[names],data_model,created_at,updated_at,metadata}` (scripts/setup_surface.py:104) |
| describe | `GET /api/v1/context-surfaces/{id}` | 200 (setup_surface.py:140); has `tools[]` |
| list | `GET /api/v1/context-surfaces?page=1&page_size=20` | `{context_surfaces:[...],pagination:{page,page_size,total_count,total_pages,has_next,has_prev}}` |
| update | `PUT /api/v1/context-surfaces/{id}` `{name?,description?,metadata?,data_model?,identity_provider?}` (no data_source) | **200** (workshop_helpers.py:662) |
| delete | `DELETE /api/v1/context-surfaces/{id}` | 200 `{"message":...}` (SDK unit-test mock only) |
| create agent key | `POST /api/v1/context-surfaces/{id}/agent-keys` `{"name","description"?,"expires_at"?,"metadata"?,"access_tags"?: {"region":["emea"]}}` | **201** `{id,key,name,context_surface_id,key_type:"agent",owner,created_at,updated_at}`; `key` is only returned here |
| list agent keys | `GET .../agent-keys?page=&page_size=` | `{agent_keys:[{id,masked_key,name,context_surface_id,access_tags,mcp_url,expires_at,...}],pagination}` |
| delete agent key | `DELETE .../agent-keys/{agent_key_id}` | 204 (SDK mock only) |
| import data | `POST /api/v1/context-surfaces/{id}/data` `{"entity":"Customer","records":[...model_dump()...],"options":{"on_conflict":"overwrite","on_error":"fail_fast"}}` (one entity type per call; the demos repo batches 25) | 200 `{imported,failed,errors:[]}` (SDK mock) |
| key check (unverified live) | `POST /api/v1/keys/validate {"key":...}`; `GET /health` | `{"valid":true,"key_type":"admin"}` (SDK mock) / `{"status":"UP"}` |
The SDK mocks put the API on `http://localhost:8080`; the live host is `https://cloud.redis.io/context-surfaces`.

## 6. MCP (JSON-RPC 2.0 over HTTP POST, header `X-API-Key: <agent key>`)
- `{"jsonrpc":"2.0","id":1,"method":"tools/list"}` -> `result.tools[]`
- `{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":...,"arguments":{...}}}` -> `result.content[0].text` (JSON string). Bad arguments -> JSON-RPC `error` (code -32602, e.g. `invalid params: validating "arguments": validating root: unexpected additional properties ["x"]` or `enum: ... does not equal any of: [...]`). A tool-level failure -> `result.isError: true` with `text: "Error executing tool: ..."`.
- The SDK raises `MCPError` on either.

## 7. Generated tools
**A. Classic surface (recorded in radish_bank_workshop.ipynb cell 16; my offline predictor below reproduces all 16 names exactly):** entity name is LOWERCASED with no underscores (`fixeddepositplan`, `bankdocument`).
- key component -> `get_{e}_by_id`
- tag field -> `filter_{e}_by_{field}` (arg `value`)
- numeric field -> `find_{e}_by_{field}_range` (`min_value`/`max_value`, per SDK ctxcli)
- entity with >=1 text field -> `search_{e}_by_text` (arg `query`)
- vector field -> `search_{e}_by_{field}_similarity` (raw-vector arg `vector`; with 0.0.6 `embeds=` a text-input variant)
- Untagged, non-key fields make no tool.
- Common args: `limit` (default 10), `offset`.
- Response envelope: `{"count":2,"has_more":false,"limit":10,"offset":0,"total_count":2,"results":[{"account_id":"ACC001",...,"id":"workshop_account:ACC001"}]}` (`id` = full Redis key).
```python
def predict_classic(dm):
    t=[]
    for e in dm["entities"]:
        n=e["name"].lower()
        if any(f["is_key_component"] for f in e["fields"]): t.append(f"get_{n}_by_id")
        if any(i["type"]=="text" for f in e["fields"] for i in f["redis_indices"]): t.append(f"search_{n}_by_text")
        for f in e["fields"]:
            for i in f["redis_indices"]:
                if i["type"]=="tag": t.append(f"filter_{n}_by_{f['name']}")
                elif i["type"]=="numeric": t.append(f"find_{n}_by_{f['name']}_range")
                elif i["type"]=="vector": t.append(f"search_{n}_by_{f['name']}_similarity")
    return sorted(t)
```
**B. Unified surface (redis/docs static/js/context-retriever-demo.js, commit 2026-09-28; "mirrors what a live MCP server returned Sep 2026", only relationship names and text/numeric conditions on per-entity filter are 'modelled'):** entity name is snake_cased.
- `get_{e}_by_id` `{id: string}` (key component only, NOT the full Redis key) -> bare record; missing -> isError "get by ID failed: failed to get document: key ... not found".
- `filter_{e}` `{tag_conditions:[{field(enum),value,exclude?}], numeric_conditions:[{field(enum),min_value?,max_value?}], text_query, text_match_mode: any|all|phrase, limit(default 10,max 100), offset}` (all conditions ANDed) -> `{count,has_more,limit,offset,total_count,results:[{_retrieval:{source,retrieved_at,source_version},...fields}]}`.
- `count_{e}` `{function: count|count_distinct (required), field?, group_by?, limit}` -> `{function:"count",value:N}` or `{function,group_by,groups:[{value,group_value}],has_more}`.
- `intersect_results` / `union_results` / `except_results` `{sets:[{entity,tag_conditions,numeric_conditions,text_query,expand_to?}] (2-8), limit, offset}` -> `{complete,count,entity,has_more,ids:[full keys],limit,offset}`.
- `expand_results` `{set, relationship, limit, offset}`.
- Index DDL the service issues (docs note): `FT.CREATE idx:<surface>:<hash8>:<entity> ON JSON PREFIX 1 <text before {id}> SCHEMA '$.f' AS f TAG INDEXMISSING | NUMERIC SORTABLE | TEXT` (names 'illustrative'; INDEXMISSING => Redis >= 7.4; access-control tag fields also get INDEXEMPTY).
- **Rule for the notebook: never hardcode tool names or arg names. Read `inputSchema` at runtime. `surface["tools"]` in the create response already lists names without any MCP call.**

## 8. Access tags (exist in SDK/API; unverified end-to-end, not used anywhere in redis-iris-demos)
- Entity: `__acl_field_mappings__ = [{"document_field": "$.region", "access_tag": "region"}]` -> exported as `acl_field_mappings` (format from SDK tests/test_context_model.py:419-445).
- Agent key: `access_tags: {"region": ["emea"]}` (multi-valued; `CreateAgentKeyRequest.access_tags`, `AgentKeyListItem.access_tags`). `UnifiedClient.create_agent_key` cannot pass it: use raw httpx or `ContextSurfacesClient`.
- Docs: "Each agent key has access tags that automatically filter what data it can see".
- OAuth alternative (`IdentityProviderConfig`, `access_tag_claims`) is out of scope.

## 9. Validated inline dataset (20 records, 3 entities; all instantiate; 15 classic tools predicted; put this in the notebook verbatim)
```python
RUN = uuid.uuid4().hex[:6]; PREFIX = f"irisnb_{RUN}_"; TTL = 7200
ACL = [{"document_field": "$.region", "access_tag": "region"}]
class Customer(ContextModel):
    """A shopper in the demo outdoor-gear store."""
    __redis_key_template__ = f"{PREFIX}customer:{{customer_id}}"; __acl_field_mappings__ = ACL
    customer_id: str = ContextField(description="Customer ID such as C001", is_key_component=True)
    name: str = ContextField(description="Full name", index="text", weight=2.0)
    tier: str = ContextField(description="Loyalty tier", index="tag", allowed_values=["bronze","silver","gold"])
    region: str = ContextField(description="Sales region: emea or apac", index="tag", allowed_values=["emea","apac"])
    lifetime_value: float = ContextField(description="Lifetime spend in USD", index="numeric", sortable=True)
    orders: Any = ContextRelationship(description="Orders this customer placed", target="Order", source_field="customer_id")
class Product(ContextModel):
    """An item in the catalogue."""
    __redis_key_template__ = f"{PREFIX}product:{{sku}}"
    sku: str = ContextField(description="Stock keeping unit such as P100", is_key_component=True)
    title: str = ContextField(description="Product title", index="text", weight=2.0)
    category: str = ContextField(description="Product category", index="tag")
    price: float = ContextField(description="List price in USD", index="numeric", sortable=True)
class Order(ContextModel):
    """A purchase of one product by one customer."""
    __redis_key_template__ = f"{PREFIX}order:{{order_id}}"; __acl_field_mappings__ = ACL
    order_id: str = ContextField(description="Order ID such as O1001", is_key_component=True)
    customer_id: str = ContextField(description="Customer who placed the order", index="tag")
    sku: str = ContextField(description="Product ordered", index="tag")
    region: str = ContextField(description="Sales region: emea or apac", index="tag", allowed_values=["emea","apac"])
    status: str = ContextField(description="placed, shipped, delivered or returned", index="tag", allowed_values=["placed","shipped","delivered","returned"])
    total: float = ContextField(description="Order total in USD", index="numeric", sortable=True)
    customer: Any = ContextRelationship(description="Buyer", target="Customer", source_field="customer_id")
    product: Any = ContextRelationship(description="Item bought", target="Product", source_field="sku")
DATA = {"Customer": [dict(customer_id="C001",name="Ada Lovelace",tier="gold",region="emea",lifetime_value=4200.0), dict(customer_id="C002",name="Grace Hopper",tier="gold",region="apac",lifetime_value=3900.0), dict(customer_id="C003",name="Alan Turing",tier="silver",region="emea",lifetime_value=1500.0), dict(customer_id="C004",name="Katherine Johnson",tier="silver",region="apac",lifetime_value=1750.0), dict(customer_id="C005",name="Linus Torvalds",tier="bronze",region="emea",lifetime_value=300.0), dict(customer_id="C006",name="Margaret Hamilton",tier="bronze",region="apac",lifetime_value=420.0)],
 "Product": [dict(sku="P100",title="Trail Runner Shoes",category="footwear",price=129.0), dict(sku="P101",title="Alpine Down Jacket",category="apparel",price=249.0), dict(sku="P102",title="Ultralight Tent 2P",category="camping",price=399.0), dict(sku="P103",title="Insulated Water Bottle",category="accessories",price=35.0), dict(sku="P104",title="Merino Base Layer",category="apparel",price=89.0), dict(sku="P105",title="Headlamp Pro",category="camping",price=59.0)],
 "Order": [dict(order_id="O1001",customer_id="C001",sku="P102",region="emea",status="delivered",total=399.0), dict(order_id="O1002",customer_id="C001",sku="P104",region="emea",status="shipped",total=89.0), dict(order_id="O1003",customer_id="C002",sku="P101",region="apac",status="delivered",total=249.0), dict(order_id="O1004",customer_id="C002",sku="P100",region="apac",status="returned",total=129.0), dict(order_id="O1005",customer_id="C003",sku="P105",region="emea",status="placed",total=59.0), dict(order_id="O1006",customer_id="C004",sku="P103",region="apac",status="delivered",total=35.0), dict(order_id="O1007",customer_id="C005",sku="P100",region="emea",status="placed",total=129.0), dict(order_id="O1008",customer_id="C006",sku="P104",region="apac",status="shipped",total=89.0)]}
```
- Ids avoid hyphens (TAG queries treat '-' specially; unverified).
- EMEA = 3 customers / 4 orders; APAC = 3 / 4.
- Predicted classic tools (15): filter_customer_by_{region,tier}, find_customer_by_lifetime_value_range, get_customer_by_id, search_customer_by_text, filter_product_by_category, find_product_by_price_range, get_product_by_id, search_product_by_text, filter_order_by_{customer_id,region,sku,status}, find_order_by_total_range, get_order_by_id.
- Unified surface would show 13 tools (3 get + 3 filter + 3 count + intersect/union/except/expand).
- The data model JSON is about 3.4 KB and the records about 2 KB.
- Assert `set(inst.model_dump()) == set(row)` on every record (pydantic silently drops unknown fields).

## 10. Notebook plumbing (each piece validated offline unless noted)
```python
u = urlparse(REDIS_URL)   # rediss://default:pw@host:port
conn = {"addr": f"{u.hostname}:{u.port}", "username": u.username or "default", "password": u.password or "",
        "db": 0, "tls_enabled": u.scheme == "rediss", "pool_size": 5, "min_idle_conns": 1}   # 5/1 inferred for the free 30-connection DB; workshop uses 10/2
HDR = {"Content-Type": "application/json", "X-API-Key": ADMIN_KEY}
API = str(cs_config.api_url).rstrip("/")
r = await http.post(f"{API}/api/v1/context-surfaces", headers=HDR, json={"name": f"iris-basics-{RUN}", "description": "...",
      "metadata": {"created_by": "iris-basics-notebook", "run": RUN}, "data_model": dm,
      "data_source": {"type": "redis", "connection_config": conn}});  assert r.status_code == 201, r.text
SID = r.json()["id"]; TOOLS = r.json().get("tools", [])
r = await http.post(f"{API}/api/v1/context-surfaces/{SID}/agent-keys", headers=HDR, json={"name": f"iris-basics-{RUN}-agent"})  # 201 -> ["key"]
async with UnifiedClient() as c:
    for name, rows in DATA.items():
        res = await c.import_data(admin_key=ADMIN_KEY, context_surface_id=SID, records=[ENT[name](**x) for x in rows], on_conflict="overwrite", on_error="fail_fast")
# dead-man switch + teaching peek (redis-py, same REDIS_URL): expire every key under PREFIX; JSON.GET one; FT._LIST + FT.INFO to show the index the service built
# two scoped agents (unverified live): POST agent-keys {"name": "...-emea", "access_tags": {"region": ["emea"]}} and {"...-apac": ["apac"]}
# cleanup: DELETE /api/v1/context-surfaces/{SID}  (also revokes agent keys), then r.scan_iter(match=PREFIX+"*") -> UNLINK; then for idx in FT._LIST: if all(p.startswith(PREFIX) for p in FT.INFO(idx).index_definition.prefixes): FT.DROPINDEX idx  (no DD)
```
Schema-driven tool explorer, prototyped and run headless in marimo 0.24.2 against both a classic tool (`value`,`limit`,`offset`) and a unified tool (array-of-object `tag_conditions`). The setup block holds `example_for(schema)`, `widget_for(name, prop, required)` (enum -> `mo.ui.dropdown`, boolean -> `mo.ui.checkbox`, integer/number -> `mo.ui.number`, array/object -> `mo.ui.text_area` prefilled with a JSON example, else `mo.ui.text`) and `coerce(schema, values) -> (args, missing_required)`.
- Cell A: `picker = mo.ui.dropdown({t["name"]: t for t in tools}, searchable=True)`.
- Cell B (depends on picker): `form = mo.ui.dictionary({k: widget_for(k, p, k in req) for k, p in schema["properties"].items()})` + `mo.ui.run_button`.
- Cell C: read `form.value` only in a later cell (marimo forbids reading it in the defining cell), `coerce`, `await client.query_tool(agent_key, name, args)`, `json.loads(res["content"][0]["text"])`.
- Render: `results` -> `mo.ui.table`; `groups` -> table; `ids` -> list; `{"function":"count","value":N}` -> metric; bare dict -> 1-row table. Raw JSON goes in `mo.accordion`. Show `isError` in `mo.callout(kind="danger")`.

## 11. marimo facts (0.24.2, verified by import/run)
`mo.ui.dictionary/form/table/dropdown/text_area/code_editor/run_button/tabs/array/number/checkbox`, `mo.status.spinner`, `mo.accordion`, `mo.callout`, `mo.mermaid`, `mo.json`, and `mo.ui.text(kind="password")` all exist. `marimo.App(width="columns")` and `@app.cell(column=2)` work. `from __future__ import annotations` must be file-level (before `import marimo`) and does not help on 3.14.

## 12. CLI (`ctxctl`, entry point context_surfaces.cli.entrypoint:cli_main; second CLI `ctxcli` with dynamic per-entity commands)
`config show|list|set`, `auth login|logout|status|list`, `admin create|list|delete`, `surface create|list|describe|update|delete` (+ `surface idp configure|show|disable|enable|test`), `agent create|list|delete`, `tools list|call`, `version` (`datamodel` group is registered but empty). `ctxctl surface create --name N --models ./models.py --redis-addr H:P --redis-password PW --redis-tls`; `ctxctl agent create --surface-id ID --name N --access-tags '{"tenant":["acme"]}'`; `ctxctl tools call TOOL --agent-key K --args '{"query":"jane","limit":5}'`; `ctxctl surface delete ID --confirm`.

## Explorable parameters

- **ContextField index type per field** — Decides which tools exist and which arguments they accept: tag->filter/exact match, numeric->range, text->full-text search, vector->similarity; a key component with no index gets only get_by_id. Values: text | tag | numeric | vector | none (str ids must be str for tag; int/float + tag raises ValueError in 0.0.6) _(src: context_model.py ContextField; workshop notebook cell 16; Iris tutorial note on redisIndices)_
- **is_key_component + __redis_key_template__** — Defines the Redis key (`prefix:{pk}`) and the get_by_id lookup; results show the namespaced key. Values: f'{PREFIX}customer:{{customer_id}}'; PREFIX = irisnb_<run>_ _(src: context_model.py:299-301; workshop output ('id': 'workshop_account:ACC001'))_
- **allowed_values** — Turns a tag filter parameter into an enum, so the server rejects invalid values; a good 'guardrails by construction' demo. Values: 1-128 unique non-blank strings <=256 bytes, tag index only _(src: README_0.0.6.md 'Data model example'; context_model.py:104-107,488-525)_
- **text weight / no_stem** — Relevance weight and stemming control for text search fields (name, title). Values: weight float (default 1.0, demo uses 2.0); no_stem bool _(src: context_model.py ContextField)_
- **numeric sortable** — Whether the numeric index supports sorting; the live service says NUMERIC SORTABLE. Values: True | False | None (server default) _(src: context_model.py:123-124; static/js/context-retriever-demo.js:273)_
- **vector_dim / distance_metric / embeds / embedding_model** — Vector search config; `embeds` names the text field a vector represents and enables a text-input semantic tool (server embeds the query). Skipped in the inline dataset because stored embeddings need an embeddings API. Values: vector_dim int; distance_metric cosine | euclidean | dot_product; embeds='<text field>'; embedding_model str (provenance) _(src: context_model.py:38-98)_
- **ContextRelationship(target, source_field)** — Declares join paths (Order.customer via customer_id); exported in the model and, on the unified surface, drives expand_results relationship names. Values: target entity name; source_field on this entity _(src: context_model.py:141-195; crdemo.js relationships())_
- **Tool arguments (classic)** — value (filter), query (text search), limit, offset, min_value, max_value; read the real names from inputSchema. Values: limit default 10 (max 100 on unified); offset default 0 _(src: healthcare_guide.ipynb cells 27-28; ctxcli/dynamic.py:_build_arguments)_
- **Tool arguments (unified)** — tag_conditions[{field,value,exclude}], numeric_conditions[{field,min_value,max_value}], text_query, text_match_mode (any|all|phrase), count function (count|count_distinct), group_by, sets[] for intersect/union/except, expand_to / relationship. Values: conditions ANDed; sets 2-8; limit max 100 _(src: static/js/context-retriever-demo.js:173-266)_
- **import options on_conflict / on_error** — Overwrite/skip/error on existing key; continue vs fail_fast on validation errors; try duplicate import to show idempotent upsert. Values: on_conflict: overwrite | skip | error; on_error: continue | fail_fast _(src: unified_client.py:239-334)_
- **Agent key access_tags** — Per-agent data scoping: two keys with {'region':['emea']} vs {'region':['apac']} should see different rows (unverified live). Values: dict[str, list[str]]; also expires_at, metadata, description _(src: models.py:183-186; cli/main.py:1030-1102)_
- **data_source.connection_config (pool_size, min_idle_conns, tls_enabled, db)** — How the hosted service connects to your Redis; pool settings matter on a 30-connection free DB. Values: pool_size>=1 (default 10), min_idle_conns>=0 (default 2), tls_enabled bool, db int (0 on Redis Cloud) _(src: models.py:189-198; setup_surface.py:85-97)_

## Wow ideas

- 'Pydantic in, MCP tools out' in three visible steps. (1) Offline, no credentials: dropdowns set the index type per field, `export_data_model` JSON renders live, and a 'predicted tools' table (predict_classic, labelled classic naming) updates. (2) One button creates the surface. (3) An 'actual tools' table from the create response's `tools` array appears, with a diff against the prediction. That diff honestly teaches that the tool set is a function of model plus server version.
- Interactive MCP tool explorer (the main wow): mo.ui.dropdown of tools -> auto-generated argument form built from each tool's inputSchema (enums -> dropdowns, arrays of objects -> JSON editor with a generated example) -> Run -> results as mo.ui.table, with the raw JSON-RPC request/response and an equivalent curl in an accordion. It works for both tool surfaces because nothing is hardcoded. Prototype code is in api_surface section 10.
- Guardrails by construction: a 'break it' button sends a call the model does not allow (tag filter on lifetime_value, an unknown tool, an out-of-enum tier). The server rejects it (JSON-RPC -32602 enum/unexpected-property error) and the notebook shows it in a red callout, driving home 'no open query path, so injected text cannot become arbitrary SQL' from the concepts page.
- Flip an index, watch the tools change: checkbox 'index Order.status as tag', press Apply -> PUT the data model -> re-list tools -> highlighted added/removed rows, then re-call. Mirrors the docs' interactive-demo lesson but against the user's own surface (index rebuild behaviour unverified).
- Two agents, one dataset: mint an EMEA-scoped and an APAC-scoped agent key (access_tags) and show the same call side by side (`mo.hstack` of two tables; expected 4 EMEA vs 4 APAC orders, 3 vs 3 customers). Verify live before shipping; hide the cell if both keys see everything.
- 'Where did my data go' reveal: after import, a redis-py peek shows the actual RedisJSON doc at irisnb_<run>_customer:C001 and the FT.INFO of the index the service created. It proves data stays in the customer's Redis and the agent never touches Redis directly, which is the architecture lesson. Then show `id` in results equals that key.
- LLM-free multi-hop trace: preset question chips (for example 'What did gold customers in EMEA buy?') run a schema-resolved chain of 3 calls (customers -> orders by customer -> product lookup) and render each call and result like an agent trace, with `mo.status.spinner` between steps. Optional bonus: if the presenter pastes OPENAI_API_KEY, hand the same inputSchemas to a 30-line function-calling loop (pattern in workshop_helpers._make_mcp_tool) so a real model picks the tools.
- Provenance and paging playground: sliders for limit/offset show has_more and total_count; on the unified surface show the `_retrieval` stamp (source, retrieved_at, source_version) on each record. Add count_* with group_by for a one-click aggregate bar chart (region x status) with no SQL.

## Safety concerns

- The admin key is account-wide: it can list, update and delete every surface in the Redis Cloud account, including the Iris demo app's `radish-bank-workshop` surface and other people's. The notebook must only DELETE the surface id it created this session (store SID, name it `iris-basics-<run>`, put metadata {'created_by':'iris-basics-notebook','run':RUN}). Any 'find orphans' helper must filter on that metadata or name prefix and require a typed confirmation. Never offer a list-and-delete-all button.
- Surface deletion leaves data behind ('Data stored in the underlying Redis Cloud database remains unaffected') and probably leaves the RediSearch indexes. Cleanup therefore has three parts. (1) DELETE the surface, which also revokes its agent keys. (2) SCAN with MATCH `irisnb_<run>_*` and UNLINK only those keys. (3) FT._LIST, then FT.INFO each index and FT.DROPINDEX (without DD) only where every index_definition prefix starts with `irisnb_<run>_`. Never use FLUSHDB/FLUSHALL, KEYS *, or a broad prefix. Redis-py cleanup shapes are unverified live.
- Key/index collisions: the service creates a JSON index per entity on PREFIX = text before the first `{` in the key template. Use `irisnb_<6-hex run id>_<entity>:{pk}` so no other index (Iris demo app radish_bank_*/reddash_*/electrohub_*/workshop_*, LangGraph checkpoints, or `memory:*` from Agent Memory) can match, and two concurrent sessions never share a prefix. Index names are service-chosen; show them via FT._LIST after creation and never assume `idx:<entity>`.
- The Iris demo app's `make setup` / `make reset` call scripts/flush_redis.py, which deletes every key except `memory:*`. Warn presenters not to run those against the same database while a notebook session is live (it silently empties the tools), and never point the notebook at the database the Iris app uses for a customer demo without a namespace.
- Dead-man switch: after import, EXPIRE every key under the session prefix (about 2 h, matching the existing notebook's TTL pattern) so a forgotten demo does not persist. Leftover surfaces and indexes still need explicit cleanup, so surface a visible 'session resources' panel (SID, key prefix, agent key names) and a red Delete button with mo.stop gating.
- Secrets: the Redis password is forwarded to the hosted Context Retriever service in connection_config (stored encrypted at rest per the self-managed docs), and agent keys are bearer credentials returned only once. Use password inputs, never echo keys or the request body (mask `password` when showing the create-surface JSON in a teaching accordion), don't write them to disk, and prefer a dedicated demo database. There is no ACL user scoping on Redis Cloud, so the service gets the default user's full rights on that DB.
- Connection budget: a free Essentials DB allows 30 connections and 100 ops/s. The service pools connections per surface (defaults pool_size 10, min_idle 2) plus the notebook's redis-py, and each stale surface holds idle connections. Use pool_size about 5 and min_idle 1, create one surface per session, delete it promptly, and keep imports to one call per entity (20 records total).
- Network/side-effect gating: every live cell (create, import, agent keys, delete) must sit behind mo.ui.run_button plus mo.stop so a reactive re-run cannot silently create duplicate surfaces or re-POST. ContextSurfacesClient also retries POSTs up to 3 times on any error, so a timeout can create a duplicate surface. Keep SID in session state and check for an existing surface named `iris-basics-<run>` before retrying.
- Python 3.14 is the default interpreter on the owner's machine (uv lists 3.14.6, 3.13.14, 3.12.13). Without `requires-python = '>=3.11,<3.14'` in the PEP 723 header, uv may resolve 3.14 and the ContextModel cell crashes on first run; test the cap under `uvx marimo edit --sandbox`.
- Setup burden is the weakest of the owner's three tests for this column: Redis Cloud account with the preview feature, an admin key, a Search+JSON DB reachable from a hosted service, and a 79-package SDK. Mitigate by (1) making the model, prediction and data cells run with zero credentials, (2) linking the docs' hosted interactive demo as the no-credential fallback, and (3) optionally replacing the SDK with about 30 lines of httpx (the MCP and REST calls are plain JSON) if install time matters, at the cost of losing ContextModel.

## Unknowns

- Which tool surface a given account returns today: classic (filter_x_by_field with `value`, search_x_by_text with `query`) or the unified one (filter_x with tag_conditions, count_x, set tools). The workshop's recorded output is classic; the docs' demo, committed 2026-09-28, claims to mirror the live server and is unified. Only a live tools/list settles it, so build the UI from inputSchema.
- Exact classic `get_*_by_id` argument name (`id` per unified docs; possibly `id` or `<pk>` in classic), and whether classic `limit`/`offset` are typed integer or number.
- Access-tag semantics end to end: what an agent key WITHOUT access_tags sees, how entities without acl_field_mappings behave (Product is left unmapped), whether get_*_by_id is filtered, whether the ACL field must be tag-indexed (docs note INDEXEMPTY for access-control tags), and the required JSONPath form of `document_field`. No live run or repo example exists.
- Whether deleting a surface drops the RediSearch indexes it created (docs only say DB data is unaffected), and the real index names (`idx:<surface>:<hash8>:<entity>` is labelled illustrative; the local dev example used bare `idx:patient`). Cleanup must match by prefix via FT.INFO, not by name.
- Whether the create-agent-key response includes `mcp_url` (only the list response model has it) and which regions exist besides gcp-us-east4.
- Whether a free Essentials DB works end to end: the docs exclude only Flex and Active-Active. The owner's README says Search must be enabled at creation, and the docs' demo note implies Redis >= 7.4. The service must be able to reach the public endpoint, and whether it needs TLS for free-tier endpoints is unknown (map `tls_enabled` from the rediss:// scheme).
- Whether Context Retriever needs account-level enablement or a waitlist during public preview, and any per-account limits on surfaces, entities, records per import call (repo batches 25), or rate.
- Docs conflict: the quickstart loads sample data with redis-cli HSET (hashes) while other docs and the SDK say JSON documents keyed by the template. Use import_data, not HSET.
- Behaviour of `embeds=` semantic search: the SDK comment says queries are embedded server-side, but stored embeddings must still be supplied (`embedding_model` is provenance). The inline dataset deliberately has no vector field because that would need an embeddings API key.
- Whether PUT /api/v1/context-surfaces/{id} with a changed data_model rebuilds indexes over existing data or requires re-import (relevant to a 'toggle an index, watch tools change' wow cell). The workshop's `update_existing=True` path suggests it works.
- Live response shapes of GET /health and POST /api/v1/keys/validate (only SDK mocks seen). GET /api/v1/context-surfaces?page_size=1 with the admin key is the safest read-only credential check and doubles as an orphan-surface finder.
- Whether TAG queries on values containing hyphens or spaces are escaped server-side (the dataset avoids them), and how text_query tokenises/stems (`jackets` vs `Jacket`).
- Agent-key prefix inconsistency: quickstart says cs_agent_, TS SDK examples and the healthcare notebook output show ce_agent_. Do not validate on prefix.
- Whether relationship names/`expand_results` behave as the docs' demo models them (the demo flags relationship names as modelled, not observed).

## Facts (with sources)

- ✅ PyPI package `context-surfaces` is a deprecated compatibility shim whose only dependency is `redis-context-retriever==<same version>`; latest of both is 0.0.6 (2026-07-27). The import name remains `context_surfaces`. The workshop pins the shim at 0.0.5. _(src: curl https://pypi.org/pypi/context-surfaces/json and https://pypi.org/pypi/redis-context-retriever/json; redis-iris-demos/uv.lock:326-333)_
- ✅ 0.0.5 -> 0.0.6 diff adds ContextField allowed_values / embeds / embedding_model, raises ValueError for int/float fields with index='tag', and changes sortable default to None (server default). _(src: diff of installed context_surfaces/context_model.py and constants.py (v005 vs v006 venvs in /tmp/cs))_
- ✅ ContextModel subclasses fail on Python 3.14 with PydanticUserError 'Field customer_id requires a type annotation' (metaclass reads namespace['__annotations__'], empty under PEP 649); a file-level `from __future__ import annotations` inside a marimo app does not fix it. Works on 3.12.13 and 3.13.14. _(src: Ran /tmp/cs/scratch/nb_test3.py and nb_test4.py under v006 (py3.14.6), mov (py3.12.13), v313 (py3.13))_
- ✅ Install footprint of redis-context-retriever 0.0.6 is about 79 packages / 73 MB (fastmcp 4.0.10, mcp 2.2.0, cryptography, keyring...); `import context_surfaces` takes about 2 s. _(src: `uv pip list --python v006/Scripts/python.exe`, `du -sh v006/Lib/site-packages`, `time python -c 'import context_surfaces'`)_
- ✅ Default admin API URL is https://cloud.redis.io/context-surfaces (env CTX_API_URL); default MCP URL is https://gcp-us-east4.context-surfaces.redis.io/mcp (env CTX_MCP_URL); config values are resolved lazily so setting os.environ before constructing the client works. _(src: context_surfaces/constants.py:215-217; config.py:122-144; python check printing config.api_url after setting os.environ)_
- ✅ UnifiedClient is async-only (async context manager), with list_tools(agent_key)->list[dict], query_tool(agent_key, tool_name, arguments=None)->raw MCP result dict, import_data(...). It has no sync variant and no list/get/delete surface methods. create_context_surface has no data_source parameter and create_agent_key hardcodes access_tags=None. _(src: context_surfaces/unified_client.py:62-334; inspect.signature output)_
- ✅ Neither the SDK nor the service caller writes to Redis directly. import_data POSTs {entity, records, options:{on_conflict,on_error}} to /api/v1/context-surfaces/{id}/data and the service stores the records and manages indexes. The SDK README says to avoid writing directly to Redis. Users of the older docs quickstart HSET hashes with redis-cli, which conflicts with other docs saying documents are JSON. _(src: unified_client.py:302-334; README_0.0.6.md 'Import data with UnifiedClient (recommended)'; docs quickstart 'Load sample data'; docs interactive-demo ('Each business object is a JSON document'))_
- ✅ The target Redis database must have Search and JSON (RediSearch + RedisJSON); the docs' demo note says TAG fields use INDEXMISSING so Redis >= 7.4 is needed. Flex and Active-Active databases are unsupported during public preview. _(src: https://redis.io/docs/latest/operate/iris/context-retriever/self-managed (Redis database section); static/js/context-retriever-demo.js:634; https://redis.io/docs/latest/operate/iris/context-retriever/create-service (Prerequisites and limitations))_
- ✅ MCP transport is plain JSON-RPC 2.0 over HTTP POST with X-API-Key: <agent key>; methods initialize, tools/list, tools/call. The SDK's MCPClient never sends initialize and returns result.tools / result directly; errors raise MCPError. _(src: context_surfaces/mcp_client.py:444-531; static/js/context-retriever-demo.js:822-823,889-892)_
- ✅ Recorded workshop output: 4 entities (Customer, Account, FixedDepositPlan, BankDocument) produced exactly 16 tools with names filter_{entity lowercased, no underscores}_by_{tag field}, find_..._by_{numeric field}_range, get_..._by_id, search_..._by_text, search_bankdocument_by_content_embedding_similarity. My predict_classic() reproduces all 16 from the exported data model. _(src: notebooks/radish_bank_workshop.ipynb cell 16 output; ran /tmp/cs/scratch/predict_tools.py -> '16 16 True')_
- ✅ Classic-surface call shapes: filter_* take {'value': str}, search_*_by_text take {'query': str}, both accept limit/offset; response envelope is {count, has_more, limit, offset, results[], total_count} and each result's `id` is the full Redis key (e.g. 'workshop_account:ACC001'). _(src: sdist examples/healthcare_context_surface_example/healthcare_guide.ipynb cells 27-28; workshop notebook cell 17 output; ctxcli/dynamic.py:_build_arguments param_map)_
- ✅ Conflict: redis-iris-demos prompts state 'All filter_* and search_* tools take a single value parameter' and use search_policy_by_text(value=...), while the SDK example notebook, PyPI README and docs quickstart call search_*_by_text with {'query': ...}. Use inputSchema, not either claim. _(src: redis-iris-demos/domains/healthcare/prompt.py:81-82; domains/sports-betting/prompt.py:96; healthcare_guide.ipynb cell 27; docs quickstart 'Call a tool')_
- ✅ The Redis docs interactive demo (committed 2026-09-28, described as mirroring the live MCP server as of Sep 2026) uses a different unified tool surface: get_{entity}_by_id{id}, filter_{entity}{tag_conditions,numeric_conditions,text_query,text_match_mode,limit,offset}, count_{entity}, intersect_results/union_results/except_results, expand_results; entity names are snake_cased; records carry a _retrieval provenance stamp. The tool surface is therefore version-dependent. _(src: https://raw.githubusercontent.com/redis/docs/main/static/js/context-retriever-demo.js lines 1-12,173-270,377-435; GitHub commits API for that path (9cad0b4d, 2026-09-28))_
- ✅ Create-surface response includes a `tools` array of tool names (so tool names can be shown without any MCP call); the healthcare SDK example prints 43 tools from it. _(src: models.py ContextSurface.tools; healthcare_guide.ipynb cell 17 output; ts-sdk api-types InternalContextsurfaceResponseDTO.tools example)_
- ✅ Create surface expects HTTP 201 and body {name, description, data_model, data_source:{type:'redis', connection_config:{addr, username, password, db, tls_enabled, pool_size, min_idle_conns}}}; agent-key create expects 201 and returns `key`; describe 200; PUT update 200 with {name, description, data_model}. _(src: redis-iris-demos/scripts/setup_surface.py:81-131,134-145; notebooks/workshop_helpers.py:618-680)_
- ❔ DELETE surface returns 200 {message}; DELETE agent key returns 204; import returns 200 {imported, failed, errors[]}; these are asserted only in the SDK's mocked unit tests, not observed live. _(src: sdist tests/test_client.py:331-339,460-468; tests/test_unified_client.py:198-340)_
- ✅ Deleting a Context Retriever service removes its configuration, entity model, tools and all its API keys, but 'Data stored in the underlying Redis Cloud database remains unaffected.' The quickstart also says surface delete revokes its agent keys. _(src: https://redis.io/docs/latest/operate/iris/context-retriever/view-service (Delete service); https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/quickstart/ (Clean up))_
- ✅ Access tags exist: CreateAgentKeyRequest.access_tags: dict[str, list[str]] (REST field access_tags), AgentKeyListItem.access_tags, entity-level __acl_field_mappings__ = [{'document_field': '$.tenant_id', 'access_tag': 'tenant'}] exported as acl_field_mappings; CLI --access-tags '{"tenant":["acme"]}'. Docs: 'Each agent key has access tags that automatically filter what data it can see.' _(src: models.py:105,183-186; context_model.py:305,441-442; sdist tests/test_context_model.py:419-445; cli/main.py:1038-1102; https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/)_
- ✅ No access-tag or ACL usage exists anywhere in redis-iris-demos (grep for access_tag/acl_field/tenant returned nothing), so a two-key scoping demo is untested territory. _(src: grep -rn -i -E 'access_tag|acl_field|tenant|access-tag' over redis-iris-demos excluding .venv/node_modules)_
- ✅ Docs URL https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/ resolves and describes Context Retriever ('Expose tools to Agents to query your Redis databases'); sub-pages concepts, quickstart, interactive-demo, and operate/iris/context-retriever/{create-service,view-service,view-admin-keys,self-managed} also load. _(src: WebFetch of each URL, 2026-09-29)_
- ✅ Admin key is created in the Redis Cloud console: left menu 'Context Retriever' -> 'Admin keys' tab -> 'New admin key' -> name -> 'Generate key' -> 'Copy'; the value is shown only once. Console deep links: https://cloud.redis.io/#/context-retriever and https://cloud.redis.io/#/context-retriever/admin-keys. Quickstart says keys start with cs_admin_. _(src: https://redis.io/docs/latest/operate/iris/context-retriever/view-admin-keys; https://redis.io/tutorials/redis-iris-call-agent/ (deep links))_
- ✅ The Redis DB host:port is on the database's Configuration tab (Access section for Essentials, General for Pro); Essentials default-user password via Default user > Configure > eye icon. _(src: https://redis.io/docs/latest/operate/rc/databases/connect/)_
- ✅ Free Essentials plan: 30 MB, 30 concurrent connections, 100 ops/sec, one free DB per account, public endpoint; the Context Retriever docs exclude only Flex and Active-Active. The owner's earlier notebook README states that on Standard Essentials the 'Search and query' capability must be ticked at creation and cannot be added later. _(src: https://redis.io/docs/latest/operate/rc/subscriptions/view-essentials-subscription/essentials-plan-details/; https://redis.io/docs/latest/operate/rc/databases/create-database/create-essentials-database/; C:/Users/NickCooper/projects/demo/redis-iris-basics/README.md (Run it callout))_
- ❔ The Context Retriever service (not the notebook) connects to the DB using connection_config, so the DB needs a publicly reachable endpoint; localhost Redis will not work with the cloud-hosted service. Connection pool defaults are pool_size=10, min_idle_conns=2 (workshop), which is large against a 30-connection free DB. _(src: models.py DataSourceConnectionConfig; workshop_helpers.py:626-634; essentials plan table)_
- ✅ scripts/flush_redis.py (called by `make setup` and `make reset`) deletes EVERY key except `memory:*`; the Agent Memory API keeps memory:* keys and its search index in the same DB. Running the Iris demo app's setup against the same DB destroys notebook data, and the notebook must never FLUSHDB or touch memory:*. _(src: redis-iris-demos/scripts/flush_redis.py:1-46; Makefile:46-75)_
- ✅ Iris demo app key prefixes are domain-specific (radish_bank_*, reddash_*, electrohub_*, workshop_*), with LangGraph checkpoints under '<domain-id>:checkpoints:'. The notebook prefix `irisnb_<run>_` does not overlap any of them. _(src: domains/radish-bank/schema.py; notebooks radish_bank_workshop.ipynb cell 9; .claude/skills/domain-pack-authoring.md NamespaceConfig)_
- ✅ ContextSurfacesClient._request retries every exception (tenacity stop_after_attempt(3), wait_exponential 1-10 s, no retry predicate), including 400/401/404, so a wrong admin key takes about 3 s to fail and a POST can be replayed. _(src: context_surfaces/client.py:172-246)_
- ✅ MCP endpoint is regional (https://<region>.context-surfaces.redis.io/mcp); list-agent-keys items carry `mcp_url`. Use it (or CTX_MCP_URL) if the account is not on gcp-us-east4. _(src: static/js/context-retriever-demo.js:536; models.py AgentKeyListItem.mcp_url)_
- ✅ The proposed 20-record dataset instantiates cleanly against the ContextModel classes (model_dump keys equal row keys), exports a 3.4 KB data model, and predict_classic yields 15 tools; numeric-with-tag raises ValueError on 0.0.6. _(src: ran /tmp/cs/scratch/final_models.py and check_models.py on py3.12)_
- ✅ A schema-driven form (enum->dropdown, bool->checkbox, number->number, array/object->JSON text_area with generated example, else text) plus coerce() ran headless in marimo 0.24.2 for both a classic tool and a unified array-of-object tool; mo.ui.dictionary built dynamically from the selected tool's inputSchema works. _(src: ran /tmp/cs/scratch/nb_wow.py and nb_wow2.py; marimo signature dumps)_
- ✅ marimo 0.24.2 supports top-level await in cells (UnifiedClient used with async cell in script mode), app.setup blocks, App(width='columns') and @app.cell(column=N). _(src: ran /tmp/cs/scratch/nb_await.py and nb_cols.py)_
- ✅ Docs recommend the ctxctl/Python workflow and mention a 'Get started' CLI wizard and 'Create custom service' UI; Context Retriever is in public preview, and the Iris tutorial lists 'Redis Cloud account with Redis Agent Memory and Redis Context Retriever enabled' as a prerequisite. _(src: https://redis.io/docs/latest/operate/iris/context-retriever/create-service; https://redis.io/tutorials/redis-iris-call-agent/ (Prerequisites))_
- ✅ Docs give a zero-credential hosted fallback: an in-browser interactive demo that simulates model -> tools -> agent questions with food-delivery sample data. _(src: https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/interactive-demo/)_

### Verifier: confirmed

- PyPI: redis-context-retriever 0.0.6 is latest (2026-07-27; 0.0.5 = 2026-05-19), requires_python >=3.11. context-surfaces 0.0.6 is a shim whose only dependency is redis-context-retriever==0.0.6 (summary: 'Compatibility shim - installs redis-context-retriever'). The workshop pins context-surfaces 0.0.5 at uv.lock:326-333. The import name is context_surfaces. Sources: https://pypi.org/pypi/redis-context-retriever/json, https://pypi.org/pypi/context-surfaces/json, context_surfaces/__init__.py:47.
- 0.0.5 to 0.0.6 diff (installed v5 vs v6 context_model.py): adds allowed_values, embeds and embedding_model, adds a ValueError for int/float fields with index='tag', and changes sortable from bool=False to bool|None=None.
- Python 3.14 breaks ContextModel: reproduced 'PydanticUserError: Field customer_id requires a type annotation' on 3.14.6 with pydantic 2.13.5. The metaclass reads namespace['__annotations__'] (context_model.py:248). The dataset works on 3.12.13 and 3.13.14. Inside a marimo 0.24.2 app a file-level `from __future__ import annotations` does not fix it, in either @app.cell or @app.class_definition (my runs of /tmp/fc/s/mo314.py and mo314d.py).
- The requires-python cap works. marimo sandbox passes requires-python as `uv run --python` (marimo/_cli/sandbox.py:295-303). On this machine `uv run --python '>=3.11'` picks 3.14.6 and '>=3.11,<3.14' picks 3.13.14. marimo latest is 0.25.0 and redis latest is 8.1.0. The existing notebook.py pins marimo==0.24.2 and redis==8.1.0.
- Footprint: 79 packages (uv pip list), 73 MB site-packages, fastmcp 4.0.10, mcp 2.2.0. `import context_surfaces` takes about 1.9 s (-X importtime).
- Default URLs are https://cloud.redis.io/context-surfaces (CTX_API_URL) and https://gcp-us-east4.context-surfaces.redis.io/mcp (CTX_MCP_URL). Env vars are resolved lazily via module __getattr__. The values are at constants.py:6-7 and config.py:24-46.
- ContextField signature matches the report exactly, plus a **kwargs passthrough (context_model.py:38-65). export_data_model(title, description, entities=None) with None exports the whole registry, keyed by class name (context_model.py:285,599-628). The default key template is f'{cls.__name__.lower()}:{{id}}' (line 431). acl_field_mappings is exported only when non-empty (441-442). no_stem is exported only when True.
- The report's 20-record dataset instantiates on 0.0.6 with model_dump keys equal to the row keys. The data model is 3353 bytes and the records 2159 bytes. predict_classic returns exactly the 15 listed names. Counts are EMEA 3/APAC 3 customers and 4/4 orders. It also runs inside a marimo cell on 3.12 (my runs of /tmp/fc/s/models.py and mo_models.py).
- predict_classic reproduces all 16 workshop tool names, run against the notebook's own cell-9 models, which match radish_bank_workshop.ipynb cell 16 output. The recorded response envelope {count,has_more,limit,offset,results[],total_count} with id='workshop_account:ACC001' is in cell 17 output.
- UnifiedClient is async-only with exactly create_admin_key, create_context_surface (no data_source param), create_agent_key (hardcodes access_tags=None), query_tool, list_tools and import_data (unified_client.py:104-334). import_data POSTs {entity, records:[model_dump()], options:{on_conflict,on_error}} to /api/v1/context-surfaces/{id}/data with defaults on_conflict='overwrite', on_error='continue'.
- MCPClient is plain httpx JSON-RPC with X-API-Key. connect() never sends initialize. list_tools posts {jsonrpc:'2.0',id:1,method:'tools/list',params:{}} and returns result.tools. call_tool returns data['result'] (mcp_client.py:75-194).
- ContextSurfacesClient._request uses @retry(stop_after_attempt(3), wait_exponential(multiplier=1,min=1,max=10), reraise=True) with no retry predicate, so it retries 401/404/400 too, sleeping 1 s then 2 s (client.py:172-176). All endpoint paths match: /health, /health/readiness, /health/liveness, /api/v1/keys, /api/v1/keys/validate, /api/v1/context-surfaces[/{id}[/agent-keys[/{kid}]]], list page_size default 20.
- models.py confirms: ContextSurface requires owner, created_at and updated_at and has tools: list[str]. CreateContextSurfaceRequest has name 1-255, description <=1000, metadata dict[str,str], data_model and data_source (required when data_model is given). DataSourceConnectionConfig defaults are pool_size=10, min_idle_conns=2, tls_enabled=False, db=0. UpdateContextSurfaceRequest has no data_source. CreateAgentKeyRequest.access_tags is dict[str,list[str]]. AgentKeyListItem has masked_key, access_tags and mcp_url. The pagination fields match. DataImportResponse is {imported, failed, errors[{index,record_id,error}]}.
- redis-iris-demos scripts/setup_surface.py:81-131 and notebooks/workshop_helpers.py:618-680 use raw httpx. Create surface expects 201 and reads ['id']. Agent-key create expects 201 and reads ['key']. Describe expects 200. PUT update sends {name,description,data_model} and expects 200. Both use str(cs_config.api_url).rstrip('/') and a connection_config with pool 10/2. The 25-record batching is in scripts/load_data.py:78. workshop_helpers.seed_workshop_context_data (712-724) imports each entity in a single call with on_error='fail_fast'.
- SDK mocks: DELETE surface returns 200 {message:'deleted'}, DELETE agent key returns 204, validate returns {valid:true,key_type:'admin'}, health returns {status:'UP'}, all on http://localhost:8080 (sdist tests/test_client.py:14,143-150,330-338,460-468). The acl_field_mappings format is at tests/test_context_model.py:419-445.
- The classic argument names in the sdist healthcare_guide.ipynb cells 27-28 are search_*_by_text {'query':...} and filter_* {'value':...}. ctxcli _build_arguments param_map is value/query/limit/offset/min_value/max_value (cli/ctxcli/dynamic.py:192-199).
- The prompt conflict is real. domains/healthcare/prompt.py:80-81 says all filter_* and search_* tools take 'value'. domains/sports-betting/prompt.py:96 has search_policy_by_text using value=. Workshop cell 15 says the same. The docs quickstart uses search_customer_by_text --args '{"query": "jane", "limit": 5}'.
- redis/docs static/js/context-retriever-demo.js at commit 9cad0b4d (2026-09-28) says the tool names, argument shapes, descriptions and envelopes mirror a live server (Sep 2026). Relationship names and text_query/numeric_conditions on filter_{e} are modelled. Unified tools: get_{snake}_by_id{id}, filter_{snake}{tag_conditions,numeric_conditions,text_query,text_match_mode,limit,offset}, count_{snake}{function(required),field,group_by,limit}, intersect/union/except_results{sets 2-8}, expand_results{set,relationship}. Also confirmed: the _retrieval stamp, the -32602 wording, the isError 'Error executing tool:' shape, INDEX_OPTIONS (TAG INDEXMISSING, NUMERIC SORTABLE, TEXT), the Redis 7.4 note (line 633), ENDPOINT https://<region>.context-surfaces.redis.io/mcp (line 536), and the curl Accept 'application/json, text/event-stream' (line 823). The unified count for the dataset is 13 by the JS logic.
- Docs: view-service says deleting removes the configuration, entity model and tools, 'immediately terminates all API keys', and 'Data stored in the underlying Redis Cloud database remains unaffected.' The quickstart Clean up says delete 'also revokes its agent keys'. The quickstart loads data with redis-cli HSET hashes. create-service excludes Flex and Active-Active during public preview and offers 'Get started' (ctxctl) and 'Create custom service'. The overview says 'Each agent key has access tags that automatically filter what data it can see'. The concepts page has the prompt-injection and 'no open query path' text. The interactive demo uses food-delivery data and calls no service.
- Admin-key console flow: Context Retriever -> Admin keys tab -> New admin key -> name -> Generate key -> Copy, and the value is shown only once (view-admin-keys). The quickstart says admin keys start with cs_admin_ and agent keys with cs_agent_. The healthcare notebook output shows ce_agent_. The Iris tutorial contains the deep links https://cloud.redis.io/#/context-retriever and .../admin-keys and lists 'A Redis Cloud account with Redis Agent Memory and Redis Context Retriever enabled' as a prerequisite.
- Redis Cloud connect docs: endpoints are on the Configuration tab (Access for Essentials, General for Pro); the Essentials password is under Default user > Configure > eye icon. The free Essentials plan has 30 MB, 30 concurrent connections, 100 ops/sec and 5 GB bandwidth, and 'You can only have one free database per account' (create-essentials-database page).
- scripts/flush_redis.py:1-46 deletes every key except memory:*. The Makefile setup target (line 53), reset target (line 67) and flush-redis target all call it.
- No access_tag, acl_field or access-tag usage exists in redis-iris-demos (grep across .py/.ipynb/.md/.ts, excluding .venv and node_modules). The CLI `ctxctl agent create --access-tags` exists, parsed at cli/main.py:977-1102.
- ctxctl command tree matches: config show|list|set, auth login|logout|status|list, admin create|list|delete, surface create|list|describe|update|delete|idp{configure,show,disable,enable,test}, agent create|list|delete, tools list|call (--agent-key, --args, --args-file), version, datamodel. Entry points: ctxctl = context_surfaces.cli.entrypoint:cli_main, ctxcli = context_surfaces.cli.ctxcli:cli_main.
- marimo 0.24.2 checked by import and a headless run. All listed mo.ui/mo.* names exist. mo.ui.text has kind='password'. The callout kinds include 'danger'. WidthType includes 'columns' and App.cell(column=) works. A top-level await cell runs under app.run(). A dropdown built from a dict of tool dicts works with searchable=True. A mo.ui.dictionary built from inputSchema works. Reading .value in the defining cell raises RuntimeError ('Accessing the value of a UIElement in the cell that created it is not allowed').
- All 12 listed docs and PyPI URLs return HTTP 200 (curl -sL, 2026-09-29).
- The combined dependency set (existing notebook pins numpy>=2.0 and model2vec==0.9.0, plus redis-context-retriever==0.0.6 and httpx>=0.28,<1) resolves to 106 packages on Python 3.13 (uv pip compile).