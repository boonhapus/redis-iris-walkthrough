# Redis LangCache for column 1: MVP brief, raw HTTP with niquests

> Written 2026-09-30 for the iris_basics.py rewrite. Every fact is tagged **VERIFIED** (read in
> the docs or spec today, run on a loopback mock today, or observed live on 2026-09-30 per the
> README and plan section 10) or **UNVERIFIED** (inferred, or read only in third-party code).
> No network call was made to any Redis or Iris service while writing this. Secret values were
> never read; only variable names.
>
> Sources read today: `content/develop/ai/context-engine/langcache/{_index,api-examples,concepts,api-reference}.md`
> and `api-reference/api.yaml` (796 lines, OpenAPI 3.0.2, info.version "1.0");
> `content/operate/iris/langcache/{_index,create-service,use-langcache,view-edit-cache,monitor-cache}.md`;
> `operate/iris/langcache/self-managed/{reference,api-examples}.md`;
> https://redis.io/tutorials/semantic-caching-with-redis-langcache/ (curl, Chrome UA);
> https://pypi.org/pypi/langcache/json (0.14.0); niquests 3.21.2 and marimo 0.25.0 introspected
> under `uv run --python 3.13`; the prior brief `.agents/plans/research/langcache.md` (its
> Verifier corrections taken as authoritative); README "Verified live"; plan section 5 column 1
> and section 10.

## 0. What changed since the prior brief

- **VERIFIED live (README):** a per-request `similarityThreshold` of 0.50 was honoured under
  the cache's 0.85 default (least similar entry returned: 0.659); `maxResults` 5 returned five
  entries, best first; `exact` reports `similarity` 1; DELETE of an expired entry answers 404
  with `type` `/errors/resource-not-found`, not the spec's `/errors/cache/entry/not-found`.
  These close four of the prior brief's "unknowns".
- **VERIFIED docs:** the docs now have a `concepts.md` page (default 0.85, recommended
  0.8 to 0.9, "a false-positive match can return a wrong answer", "Start with a conservative,
  tighter threshold"). `api.yaml` is unchanged in shape: still no `maxResults`, no
  `/conversations/search`, no securitySchemes. The self-managed docs add cache-level
  `defaultSearchThreshold` and `defaultTtlMillis` at create time (control plane only; not
  relevant to Redis Cloud users).
- **VERIFIED today:** LangCache is "currently available in preview" (develop index banner) and
  "public preview" on Redis Cloud (operate index banner). Say so flat on first mention.
- **VERIFIED (marimo brief, browser-tested):** `mo.ui.chat` is broken in marimo 0.25.0 (zod
  4.5.4 rejects a missing `messages[0].metadata`). With 0.25.0 pinned, column 1 must not use
  `mo.ui.chat`. The wow below is a scripted `run_button` sequence instead.

## 1. Auth and base URL

- **VERIFIED** Auth is one header on every request: `Authorization: Bearer <LANGCACHE_API_KEY>`.
  The key is called the "LangCache service API key" (docs) or "service key" (console dialog).
  No other auth mechanism exists; `api.yaml` has no `securitySchemes` block, so the header shape
  comes from the docs text, which is consistent across api-examples, use-langcache and the
  self-managed api-examples (`-H "Authorization: Bearer $LC_AGENT_KEY"`).
- **VERIFIED** Path template: `{base URL}/v1/caches/{cacheId}/entries` and below. The five
  operations in `api.yaml`:

  | operationId | Method and path | Success |
  |---|---|---|
  | `search` | `POST /v1/caches/{cacheId}/entries/search` | 200, `{"data":[...]}` |
  | `set` | `POST /v1/caches/{cacheId}/entries` | 201, `{"entryId": "..."}` |
  | `delete` | `DELETE /v1/caches/{cacheId}/entries/{entryId}` | 204, no body |
  | `deleteQuery` | `DELETE /v1/caches/{cacheId}/entries` with a JSON body | 200, `{"deletedEntriesCount": n}` |
  | `flush` | `POST /v1/caches/{cacheId}/flush` | 204 (never called by the notebook) |

- **VERIFIED** Where the three values live in the console (view-edit-cache.md): LangCache (left
  menu) -> your service -> **Configuration** tab -> **Connectivity** section, which lists
  **API Key** ("The Bearer token for your LangCache API requests"), **Cache ID** ("The unique
  ID of your LangCache service") and **URL List** ("A list of base URLs for LangCache API
  requests. Use the closest URL for the best performance"). Copy buttons sit next to the Cache
  ID and the API base URL. The API key value is shown only once, in the dialog right after
  creation (create-service.md: "This is the only time the value of the user key is
  available").
- **Scheme, console vs docs.** **VERIFIED** the docs' curl uses `https://$HOST/v1/...` and
  the docs' Python example builds `server_url=f"https://{HOST}"`, so the docs' `$HOST` carries
  **no** scheme. **VERIFIED** the redis.io tutorial names the same value `LANGCACHE_API_URL`
  ("LangCache API endpoint"), the RedisVL guide uses `https://aws-us-east-1.langcache.redis.io`
  as a full server URL, and the redis-iris-demos `.env` variable `LANGCACHE_HOST` is used as
  `f"{host}/v1/caches/{cache_id}"`, so there it must include `https://`. **UNVERIFIED** whether
  the console's URL List shows the scheme. Design consequence: keep the name `LANGCACHE_HOST`
  (orchestrator decision), document it as `https://<region>.langcache.redis.io`, and normalise
  both forms in one line: `host = host.strip().rstrip("/"); if "://" not in host: host = "https://" + host`.
- **VERIFIED** The other two names: `LANGCACHE_CACHE_ID` (the `{cacheId}` path parameter) and
  `LANGCACHE_API_KEY`. These are the names the redis-iris-demos `.env` uses (names listed, values
  never read) and the names the official Python SDK falls back to from the environment.
- **VERIFIED** Error body shape on every 4xx/5xx (RFC 7807 style):
  `{"title": str, "status": int, "detail": str, "type": "/errors/...", "instance"?: str}`.
  400 `/errors/invalid-data`; 401 `/errors/authentication-failed`; 403
  `/errors/insufficient-permissions`; 404 one of `/errors/resource/not-found`,
  `/errors/cache/not-found`, `/errors/cache/entry/not-found`, `/errors/token/not-found`; 413
  `/errors/payload-too-large`; 424 `/errors/database/resource-database-unavailable`,
  `/errors/database/out-of-memory`, `/errors/embeddings/unauthorized`,
  `/errors/embeddings/too-many-requests`, `/errors/cache/index-not-found`; 429
  `/errors/too-many-requests`; 500 `/errors/unexpected-error`, `/errors/cache/authentication`,
  `/errors/cache/unexpected-error`, `/errors/embeddings/unexpected-error`.
  **VERIFIED live** that the server also emits a `type` outside this enum
  (`/errors/resource-not-found`, hyphen), so match 404 types loosely (see section 2.3).

## 2. Exact request and response JSON

Prompt limits (**VERIFIED**, `api.yaml`): `prompt` is required, 1 to 1024 characters, on both
search and set. `response` is required on set with no stated maximum.

### 2.1 Search

**VERIFIED** request (`SearchEntriesRequest`, camelCase):

```json
POST {base}/v1/caches/{cacheId}/entries/search
Authorization: Bearer <LANGCACHE_API_KEY>
Content-Type: application/json

{
  "prompt": "My rain jacket stopped beading water. How do I fix that?",
  "similarityThreshold": 0.85,
  "searchStrategies": ["semantic"],
  "maxResults": 5,
  "attributes": {"scope": "iris-basics-3f9a1c2e"}
}
```

- `prompt` **VERIFIED** required.
- `similarityThreshold` **VERIFIED** optional float 0 to 1, "The minimum similarity threshold
  for the cache entry (normalized cosine similarity)". Omitted: the cache's own default applies
  (console default 0.85, console range 0.5 to 1.0, editable after creation). **VERIFIED live**
  that a per-request value below the cache default is honoured (0.50 under a 0.85 cache
  returned an entry at 0.659). A slider hands over `0.8500000000000001`: `round(value, 2)`
  before sending.
- `searchStrategies` **VERIFIED** optional array of `"exact"` and/or `"semantic"`, "ordered by
  priority". Default is `["semantic"]` only. `exact` "matches the query exactly (case
  insensitive)". **VERIFIED live** `exact` reports `similarity` 1, and a case flip (even all
  caps) hits under `exact`, while a dropped question mark or a paraphrase does not.
- `maxResults` **UNVERIFIED in the docs** (absent from `api.yaml`), **VERIFIED in the SDK**
  (0.12.0+, docstring "The maximum number of results to return. Defaults to 1 if not
  specified") and **VERIFIED live** on this account (5 asked, 5 returned, best first). The
  MVP sends 5 so it can hide other apps' entries and still find its own; see section 4.
- `attributes` **VERIFIED** optional map; "LangCache will only return responses that match the
  attributes you specify". Only usable on a cache that defined that attribute name at creation.
  The MVP does not send attributes (see 2.4 and 4).

**VERIFIED** response (`SearchEntriesResponse`):

```json
HTTP 200
{
  "data": [
    {
      "id": "5b84acef3ce360988d1b35adbaaaccb1",
      "prompt": "How do I restore the waterproofing on my 3-layer rain shell?",
      "response": "Wash the shell, then apply a spray-on or wash-in treatment. A short tumble in the dryer sets it.",
      "attributes": {},
      "similarity": 0.896,
      "searchStrategy": "semantic"
    }
  ]
}
```

- A miss is `HTTP 200 {"data": []}`, never a 404 (**VERIFIED**, spec: "If no entries are found,
  this endpoint returns an empty array").
- `id` pattern in the spec is `^[a-f0-9]{32}$`, but Redis's own tutorial output showed a
  36-character UUID from another deployment (**VERIFIED**, prior brief). Do not validate or
  format the id; treat it as an opaque string.
- There is no `distance` field, only `similarity` (**VERIFIED**).
- Results are best first (**VERIFIED live** with `maxResults` 5). Sorting client-side costs
  nothing and protects the table if that changes.

### 2.2 Set (store)

**VERIFIED** request (`SetEntryRequest`):

```json
POST {base}/v1/caches/{cacheId}/entries
Authorization: Bearer <LANGCACHE_API_KEY>
Content-Type: application/json

{
  "prompt": "How do I wash my down sleeping bag?",
  "response": "Use a front-loading machine and a down-specific soap, then dry it on low heat with clean tennis balls to break up the clumps.",
  "ttlMillis": 900000
}
```

- `ttlMillis` **VERIFIED** optional int64, 1 to 31556952000 (about one year), "If not set, the
  cache's default TTL is used". The console default TTL is "No expiration" (**VERIFIED**,
  create-service.md), so an entry stored without `ttlMillis` lives until something deletes it.
  The MVP always sends it. Note the docs disagree with themselves on the console unit
  (create-service: milliseconds; view-edit-cache: "The number of seconds"); the API field is
  unambiguous.
- `attributes` **VERIFIED** optional, at most 5 properties, names `^[a-zA-Z0-9_-]{1,32}$`,
  values 1 to 500 characters matching `^[^,]*$` (no commas), and "All attribute names that can
  be associated with an entry must be defined during cache creation".

**VERIFIED** response:

```json
HTTP 201
{"entryId": "5b84acef3ce360988d1b35adbaaaccb1"}
```

**UNVERIFIED** whether storing the same prompt twice de-duplicates or creates a second entry.
Track every returned `entryId`; never assume one prompt equals one entry.

**VERIFIED live** timing: an entry stored with a 5 s TTL was listed, then gone after about 6 s;
one stored for 10 s was gone when deleted more than 11 s later. Expiry is close to `ttlMillis`.

### 2.3 Delete by id

**VERIFIED** request: `DELETE {base}/v1/caches/{cacheId}/entries/{entryId}`, no body, no
Content-Type. Success is `HTTP 204` with an empty body (do not call `.json()` on it).

404 types, and what each means for Clean up:

| `type` in the 404 body | Seen where | Meaning | Clean up treats it as |
|---|---|---|---|
| `/errors/resource-not-found` | **VERIFIED live** on an expired entry (README, plan section 10) | the entry is already gone | expired, counted, not an error |
| `/errors/cache/entry/not-found` | **VERIFIED** in `api.yaml`; **UNVERIFIED live** | the docs' name for the same case | expired |
| `/errors/cache/not-found` | **VERIFIED** in `api.yaml`; **UNVERIFIED live** | the Cache ID is wrong | error: check `LANGCACHE_CACHE_ID` |
| `/errors/resource/not-found`, `/errors/token/not-found` | spec enum only | unclear | error, show the body |

Match with `re.search(r"/(entry/not-found|resource[-/]not-found)$", body.get("type", ""))`
for "gone", as the old notebook does after its live fix. **UNVERIFIED** the old notebook's
advice that a wrong Cache ID answers 404 to every endpoint; plausible, show the body either way.
A wrong key answers 401 `/errors/authentication-failed` (**VERIFIED** spec; **UNVERIFIED** live).

### 2.4 Delete by attributes, and why the MVP never calls it

**VERIFIED** request: `DELETE {base}/v1/caches/{cacheId}/entries` with a JSON body
`{"attributes": {"scope": "iris-basics-3f9a1c2e"}}`; response `HTTP 200 {"deletedEntriesCount": 2}`.
With several attributes it deletes entries "that contain all given attributes".

**VERIFIED** the docs' warning: "If you do not specify any `attributes`, all responses in the
cache will be deleted. This cannot be undone." The spec says the same ("If no attributes are
provided, all entries in the cache are deleted"). The SDK sends `{"attributes": {}}` for an empty
dict (**VERIFIED** on a mock, prior brief), and **UNVERIFIED** whether the server treats an empty
map as "none provided". A bug that drops the run tag from the dict is therefore a possible
full wipe of a cache the live demo depends on. That is why the orchestrator's rule stands:
never delete-by-attribute without the run tag, and the MVP goes one step further and does not
call `deleteQuery` at all. Delete-by-id over the list of ids this session stored is exact,
needs no attribute schema on the cache, and is what Clean up does.

**What happens on a cache with no attributes configured.** **VERIFIED** that a set or search
naming an attribute the cache did not define answers HTTP 400 `type` `/errors/invalid-data`.
**UNVERIFIED** exact wording: two Redis-authored codebases match the substring
`"no attributes are configured"` in the 400 body (redis-iris-demos
`backend/app/langcache_service.py:91` on `exc.response.text`; redisvl 0.27.2
`extensions/cache/llm/langcache.py` on `str(e).lower()` for search, check and store), and the
RedisVL docs say "If you pass metadata to store or attributes to check but the cache has no
attributes configured, the LangCache API returns an error". A scoped search on such a cache
400s; it does not silently return everything. Since the MVP sends no attributes it never meets
this error; mention it in one sentence in the title cell's "attributes" note so a reader who
adds `attributes` knows what the 400 means.

### 2.5 Wire details that show up in the wire view

- **VERIFIED** (mock, niquests 3.21.2) the request headers niquests sends with `json=`:
  `Authorization`, `Accept: application/json` (set on the Session), `Content-Type:
  application/json;charset=utf-8` (niquests adds the charset), `Content-Length`,
  `User-Agent: niquests/3.21.2`, `Accept-Encoding: gzip, deflate`, `Connection: keep-alive`.
  DELETE by id carries no Content-Type and no body.
- The docs' curl (**VERIFIED**) is the equivalent to show under the JSON:
  `curl -s -X POST "https://$HOST/v1/caches/$CACHE_ID/entries/search" -H "accept: application/json" -H "Authorization: Bearer $API_KEY" -d '{ "prompt": "What is semantic caching" }'`.

## 3. Live facts turned into the MVP teaching sequence

### 3.1 The numbers to design around (all VERIFIED live on 2026-09-30, this account)

| Fact | Value |
|---|---|
| Per-request threshold below the cache default | honoured: 0.50 under a 0.85 cache; least similar hit 0.659 |
| `maxResults` | honoured: 5 asked, 5 returned, best first |
| `exact` strategy | `similarity` 1 |
| Rain-jacket paraphrase vs the shell entry | 0.896 |
| Taught tent-pole paraphrase vs the taught entry | 0.894 |
| Lab paraphrases (6) | 0.812 to 0.949 |
| Highest false hit (a swapped item, e.g. synthetic bag vs the down-bag entry) | 0.950 |
| Threshold with no false hit left | 0.96, where only 1 of 6 paraphrases still hits |
| Semantic on an all-caps copy of a stored prompt | missed at the lab threshold; `exact` hit it |
| Rate limits during 14 probes plus the strategies table | none met |
| Another app's entry near a test prompt | came back; the notebook showed its score only |

Two other anchors from Redis material (**VERIFIED** read; other caches, other content): the
radish-bank workshop saw 1.000 exact, 0.906 and 0.839 for paraphrases, a miss for an unrelated
question at 0.82; the redis.io tutorial saw 0.833 for "I forgot how to change my login
password." against "How do I reset my password?" with its threshold set to 0.65.

How Redis frames the threshold, for the copy (**VERIFIED**): concepts.md: "LangCache defaults
to a similarity threshold of 0.85, with a recommended starting range of 0.8 to 0.9 ... Start
with a conservative, tighter threshold. Loosen it gradually while monitoring hit rate and
spot-checking matches. Don't start loose and try to catch bad matches after the fact." The
tutorial: "Start around 0.65 for a support FAQ flow like this one ... Tune down if you miss too
many close matches, and tune up if you get false positives." The two disagree on the number,
not the method. The notebook should show the default (0.85) and let you slide.

### 3.2 The sequence, and what to keep

**(a) Store 5 Cairn gear-care entries with `ttlMillis`. KEEP.** Reuse the five existing
`lc_seeds()` rows (boots, down bag, tent, rain shell, stove): they are good, Cairn-only, and
far from every live-demo domain. Behind one `mo.ui.run_button`. `ttlMillis` 900000 (15 min).
Show the request JSON of the first store and the five `entryId`s. Keep the ids in one list,
`langcache_entry_ids`, which every later search filters on and Clean up walks.

**(b) One search showing a HIT with the full JSON. KEEP.** Fixed prompt "My rain jacket
stopped beading water. How do I fix that?" (0.896 live against the shell entry, comfortably
above 0.85 and a real paraphrase: no shared noun with the stored prompt except "rain").
Send `similarityThreshold` 0.85 explicitly so the JSON teaches the parameter, plus
`searchStrategies ["semantic"]` and `maxResults 5`. Display: request JSON, response JSON,
elapsed ms, and one line decoding it ("`similarity` 0.896 is the cosine similarity of the two
prompt embeddings; the stored answer comes back with no model call"). Any `data[]` item whose
`id` is not in `langcache_entry_ids` is replaced by `{"hidden": "another app's entry",
"similarity": ...}` before display.

**(c) Threshold: search once at a low floor, then re-score locally. RECOMMENDED over live
re-search.** Eight labelled probes, each searched **once** at `similarityThreshold` 0.50 with
`maxResults` 5 (8 requests, behind a `run_button`). Keep for each probe its best own match and
its `similarity`. Then a `mo.ui.slider(0.5, 1.0, 0.01, value=0.85, debounce=True,
show_value=True, label="similarityThreshold")` re-computes the verdict column with no network
call: HIT (right entry, above the line), WRONG ANSWER (an entry above the line for a question
it does not answer), MISS. Why this and not re-searching one paraphrase live per slider move:

- it teaches the true mechanic: the server computes one `similarity`, the threshold is a cut
  line over it; sliding shows the scores that sit below the line, which a live re-search hides;
- 8 requests total instead of one per drag stop; no rate-limit exposure (429 exists in the
  spec); no latency on the slider;
- it depends only on the floor being honoured, which is now VERIFIED live;
- it is fewer lines: a list comprehension, not a reactive network cell.

Add one sentence so nobody thinks the cut is fake: "The server applies the same cut when you
send `similarityThreshold`; the anatomy cell above did exactly that at 0.85."

Probe set (8, from the existing `lc_probes()`, chosen so the live numbers already say what
happens): "How do I wash my down sleeping bag?" (same words, bag); "What's the right way to
launder a goose-down sleeping bag?" (paraphrase, bag); "My rain jacket stopped beading water.
How do I fix that?" (paraphrase, shell); "how should i put my tent away after a trip"
(paraphrase, tent); "How do I wash my synthetic sleeping bag?" (swapped item, wrong answer);
"How should I store my kayak between trips?" (swapped item, wrong answer); "How do I avoid
washing my down sleeping bag?" (negation, wrong answer); "What is the capital of Australia?"
(unrelated). Expected reading at 0.85: paraphrases mostly HIT, at least one swapped item is a
WRONG ANSWER (0.950 live), unrelated MISS. Skip the precision/recall `mo.stat` tiles: the
verdict column carries the lesson.

**(d) `exact` vs `semantic`. KEEP, small.** Three variants of the stored down-bag prompt, all
caps "HOW DO I WASH MY DOWN SLEEPING BAG?", dropped question mark "How do I wash my down
sleeping bag", paraphrase "Can you tell me how to wash a down bag?", each searched with
`["exact"]`, `["semantic"]` and `["exact", "semantic"]` at the slider's threshold: 9 requests
behind a `run_button`, one table of `similarity` or "miss" per cell plus the `searchStrategy`
the response reports. Live reading: `exact` hits the case flip at 1 and misses the other two;
`semantic` catches the paraphrase and can miss the all-caps copy; `["exact", "semantic"]`
tries exact first. If the column runs over budget, cut this to 2 variants x 2 strategies.

**(e) Optional wow: miss, teach, paraphrase HIT, delete. KEEP ONLY AS ONE SCRIPTED CELL.**
Fixed script from `lc_script()`: ask "How do I fix a snapped tent pole?" (MISS: `data []`),
store the answer with `ttlMillis` 120000, ask "What should I do about a broken tent pole?"
(HIT, 0.894 live), DELETE the taught id (204). Four requests, one `run_button`, a four-row
timeline with status, `similarity` and ms. No free-text teaching, no Cairn-vocabulary filter,
no chat widget (`mo.ui.chat` is broken in 0.25.0 anyway). This is the cache-aside loop live
and is worth one cell. It is the first cell to drop if the column exceeds 11 cells.

**Drop:** the TTL deep dive (Clean up already shows 204 vs 404), the `mo.stat` scoreboard,
presenter notes, recorded-live panels, the floor probe (the floor is verified), scope-attribute
detection, the chat, Reset.

## 4. Shared-cache hazards and the dedicated service

### 4.1 What the live Iris demo does with the same cache (VERIFIED, prior brief, code read)

`redis-iris-demos/backend/app/langcache_service.py` searches with
`{"prompt", "similarityThreshold": 0.82, "searchStrategies": ["semantic"]}`, **no**
`attributes`, **no** `maxResults`, and serves `data[0]`. Its `make setup`, `make reset` and
`make seed-langcache` **flush the whole cache** before re-seeding one entry per domain (prompts
about a bank's FD rates, electronics returns, clinic appointments, late-delivery refunds, phone
trade-ins, bet settlement, and NVIDIA/AMD/Broadcom gross profit).

Consequences: (1) any notebook entry whose prompt scores at least 0.82 against a demo user's
question is served to that user as the answer, with no way for the demo to tell; (2) a demo
re-seed deletes the notebook's entries mid-session, so a later search shows MISS or "another
app's entry"; (3) a notebook search can return a demo entry.

### 4.2 What the MVP must still do on a shared cache

1. **`ttlMillis` on every set** (15 min for the five entries, 2 min for the taught entry, which
   is also deleted in the same cell). Leaked entries then self-expire even if Clean up is never
   pressed or the kernel dies.
2. **Cairn-only content.** Every prompt names gear (boots, down sleeping bag, tent, rain shell,
   camp stove, tent pole). Nothing about refunds, policies, accounts, appointments, opening
   hours or prices. Keep the two "unrelated" probes harmless: "What is the capital of
   Australia?" is a search, not a store, so it cannot leak; only stores matter here.
3. **Show only what this session stored.** Every search sends `maxResults` 5 and filters
   `data[]` to ids in `langcache_entry_ids`; any other item is shown as
   `{"hidden": "another app's entry", "similarity": 0.71}`, never its prompt or response. This
   also keeps the wire view clean: redact before rendering, not after.
4. **No `flush`, no `deleteQuery`, no Generate key, no Delete service.** `flush` and an
   attribute-less `deleteQuery` empty the cache the demo depends on; Generate key and Delete
   service kill the key the demo uses (**VERIFIED**, view-edit-cache.md: "calls to the LangCache
   API with the old key will fail"; delete "immediately terminates all API keys associated with
   the service"). A small test can walk column 1's AST and fail on any call path or string
   containing `/flush` or a bare `DELETE .../entries` with a body.
5. **Read-only connection check.** The configure cell's test is one search for
   "Cairn Outfitters connection check" with `maxResults` 1; it writes nothing.

### 4.3 What to tell Nick about a dedicated LangCache service (VERIFIED, create-service.md)

Why: a cache is shared by everything that points at it, and attributes cannot protect the demo
from the notebook because the demo searches unscoped. A service of its own is the only real
isolation, and it makes `Clean up` optional rather than load-bearing.

Click path: https://cloud.redis.io -> **LangCache** (left menu) -> **New service** (or the intro
page if none exists yet) -> either

- **Quick create**: default settings on your Free 30 MB database (Redis Cloud creates one if
  you have none). Defaults: Redis embedding provider, threshold 0.85, TTL "No expiration", no
  attributes. A dialog shows the **service key once**: copy it. Or
- **Create custom service**: Service name (e.g. `iris-basics-notebook`), Select database, TTL
  (leave "No expiration"; the notebook sets `ttlMillis` itself), User "default", Embedding
  Provider **Redis** (no OpenAI key needed), Model (the offered Redis model), Similarity
  threshold 0.85, and, optionally, **Attributes settings** -> Add attribute -> `scope` (this
  cannot be added later; the MVP does not need it, but a future scoped-delete cell would).
  **Create**, then copy the one-time key.

Then **Configuration -> Connectivity**: copy **Cache ID** and the closest entry of **URL List**.
Put in the `.env` next to `pyproject.toml`:

```
LANGCACHE_HOST=https://<the URL you copied, with or without https://>
LANGCACHE_CACHE_ID=<Cache ID>
LANGCACHE_API_KEY=<the key from the one-time dialog>
```

Constraints (**VERIFIED**): public preview; the database must not have a CIDR allow list, must
not be Active-Active, and must have the default user enabled; only the `default` user is
supported. **UNVERIFIED**: whether a second LangCache service can point at the same free
database (Quick create picks the free database; if the demo's service already uses it, pick
Create custom service and choose another database, or test Quick create and see). If the key
is lost, **Generate key** on the dedicated service is fine; on the shared one it breaks the
demo. The Metrics tab (hit ratio, search requests, latency; Hour/Day/Week) is worth one link in
the title cell.

## 5. niquests 3.21.2 for this API (all VERIFIED by introspection and a loopback mock today)

**Session with defaults.** `niquests.Session` takes `base_url`, `timeout` and `headers` in its
constructor; `Session.request` uses `timeout or self.timeout`, so a per-session timeout applies
to every call that passes none (a 1.0 s session timeout raised `niquests.exceptions.ReadTimeout`
at 1.0 s on the mock). Relative paths join onto `base_url` with or without a leading slash, and
a trailing slash on the base is harmless. Default `retries=0`: each call is exactly one wire
request, which is what a teaching wire view wants. HTTP/2 multiplexing is off by default.

```python
import json, os, re, niquests

LANGCACHE_ENTRY_TTL_MS = 15 * 60 * 1000

def langcache_session() -> niquests.Session | None:
    host = os.environ.get("LANGCACHE_HOST", "").strip().rstrip("/")
    cache_id = os.environ.get("LANGCACHE_CACHE_ID", "").strip()
    api_key = os.environ.get("LANGCACHE_API_KEY", "").strip()
    if not (host and cache_id and api_key):
        return None
    if "://" not in host:
        host = "https://" + host
    return niquests.Session(
        base_url=f"{host}/v1/caches/{cache_id}",
        timeout=15,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
```

**Calls.** `session.post(path, json=body)`, `session.request("DELETE", path)`. Do not set
Content-Type yourself; `json=` sets `application/json;charset=utf-8`. `Session.delete(url,
**kwargs)` also forwards `json=` (a body was sent on the mock), unlike httpx, but the MVP never
sends a body on DELETE.

**Reading the reply.** `response.status_code`, `response.ok` (status below 400),
`response.json()`, `response.elapsed` (a `datetime.timedelta`). A 204 has `response.content ==
b""` and `.json()` raises `niquests.exceptions.JSONDecodeError`, so guard with `if
response.content`. Prefer reading the body over `raise_for_status()`: the API's 4xx bodies carry
`title/status/detail/type`, which is the teaching content. `raise_for_status()` raises
`niquests.exceptions.HTTPError` whose `.response` still holds the body, and returns the response
itself on success (chainable), so it is usable, but a helper that returns `(status, payload,
response)` and lets each cell decide reads better in a notebook. Network failures are
`niquests.exceptions.ConnectionError` and `niquests.exceptions.Timeout`, both subclasses of
`niquests.exceptions.RequestException` (which subclasses `OSError`): catch `RequestException`
and show one line.

```python
def langcache_call(session, method, path, body=None):
    """One request to the cache: (status, parsed JSON or None, response)."""
    response = session.request(method, path, json=body)
    payload = response.json() if response.content else None
    return response.status_code, payload, response
```

**The wire view.** `response.request` is the `PreparedRequest` that went out: `.method`,
`.url` (absolute, after `base_url` joining), `.headers` (case-insensitive dict) and `.body`
(bytes for `json=`, `None` for a bodiless DELETE). Copy the headers to a plain dict and
overwrite `Authorization` before anything renders; never show any part of the key:

```python
def wire_view(response, shown_body):
    request = response.request
    headers = dict(request.headers)
    headers["Authorization"] = "Bearer <LANGCACHE_API_KEY>"
    return {
        "request": {
            "method": request.method,
            "url": request.url,
            "headers": headers,
            "body": json.loads(request.body) if request.body else None,
        },
        "response": {
            "status": response.status_code,
            "ms": round(response.elapsed.total_seconds() * 1000),
            "body": shown_body,   # data[] already redacted to this session's ids
        },
    }
```

Render with `mo.json(...)` (native, collapsible) or `mo.accordion({"Request": mo.json(...),
"Response": mo.json(...)})`. `shown_body` is the search reply after the id filter, so another
app's prompt and answer never reach the browser, the exported HTML or `__marimo__/session`.

**Filtering search results to this session's ids:**

```python
def own_hits(payload, entry_ids):
    shown, mine = [], []
    for item in payload.get("data", []):
        if item.get("id") in entry_ids:
            mine.append(item); shown.append(item)
        else:
            shown.append({"hidden": "another app's entry", "similarity": item.get("similarity")})
    mine.sort(key=lambda item: item["similarity"], reverse=True)
    return mine, {"data": shown}
```

**Not needed:** `niquests.Session(auth=...)` (a plain header is clearer to read), hooks,
`multiplexed=True`, retries, `override_scheme`.

## 6. Configuration and marimo pieces (VERIFIED on marimo 0.25.0 unless noted)

- **`.env` loading is native.** `runtime.dotenv` defaults to `[".env"]`; relative paths resolve
  against the directory holding `pyproject.toml` (or the notebook's directory when there is
  none). `load_to_environ` skips keys already in `os.environ` (no override), and the kernel
  loads the files at start. So `os.environ.get("LANGCACHE_HOST")` in a cell is the whole
  secret layer. Without `python-dotenv` installed marimo's fallback parser does not strip inline
  `# comments` (marimo brief), so pin `python-dotenv==1.2.3` in `pyproject.toml` and keep
  comments on their own lines in `.env.example`.
- **"Not configured" note.** In the configure cell:
  `mo.stop(langcache is None, mo.callout(mo.md("..."), kind="warn"))`. Every downstream cell
  depends on `langcache`, so they stop too, with no further output. Do not prefill or offer a
  typed-in key field.
- **Slider.** `mo.ui.slider(start, stop, step, value, debounce, disabled, orientation,
  show_value, include_input, steps, *, label, on_change, full_width)`: pass `show_value=True`
  by keyword (the sixth positional is `disabled`). `debounce=True` sends the value on drag end.
- **Buttons.** `mo.ui.run_button(label=...)`; a cell that does `mo.stop(not button.value)`
  runs only after a press. `mo.ui.button(on_click=...)` with a counter is the alternative for
  Clean up so it can be pressed twice.
- **Display.** `mo.json` for JSON, `mo.ui.table(rows, selection=None)` for the probe and
  strategy tables, `mo.mermaid` for the cache-aside diagram, `mo.accordion` for the collapsed
  SDK note and the wire view.
- **The collapsed SDK note.** Official Python SDK `langcache` 0.14.0 (PyPI, 2026-08-24, Python
  >= 3.10, depends on httpx and pydantic). Shape (**VERIFIED**, PyPI description):
  `with LangCache(server_url="https://<host>", cache_id=..., api_key=...) as lang_cache:`
  then `lang_cache.search(prompt=..., similarity_threshold=0.85)`, `lang_cache.set(prompt=...,
  response=..., ttl_millis=...)`, `lang_cache.delete_by_id(entry_id=...)`, plus `*_async`
  twins. It reads `LANGCACHE_CACHE_ID` and `LANGCACHE_API_KEY` from the environment when those
  arguments are `None`, the same names this notebook uses. The JavaScript SDK is
  `@redis-ai/langcache` on npm. The notebook stays on raw HTTP so each JSON body is visible.

## 7. Copy notes (VOICE.md)

- H1 and first mention: "Redis LangCache"; bare "LangCache" afterwards. State the status flat:
  "LangCache is in public preview on Redis Cloud."
- Open on the problem: "Users rarely ask the same question the same way twice, and an
  exact-key cache misses every rewording. LangCache matches on meaning, so a paraphrase gets
  the stored answer and no model is called." Then own the downside in the body: "A match is a
  similarity score above a threshold, so a wrong answer for a question that only sounds
  similar is possible. The threshold is the dial."
- Cell lead-ins end with a colon and name actor and effect: "The cell below stores five
  gear-care answers, each with `ttlMillis`:". Decode the return value in one sentence:
  "LangCache answers `201` with the `entryId` you will delete later."
- Sentence-case headings, no exclamation marks, no emoji, "you", Redis as third-person actor,
  serial commas. Docs links: overview
  https://redis.io/docs/latest/develop/ai/context-engine/langcache/ , concepts
  https://redis.io/docs/latest/develop/ai/context-engine/langcache/concepts/ , API reference
  https://redis.io/docs/latest/develop/ai/context-engine/langcache/api-reference/ , create a
  service https://redis.io/docs/latest/operate/iris/langcache/create-service/ .

## 8. Risks to flag to the orchestrator

- `mo.ui.chat` is broken in marimo 0.25.0 (marimo brief, browser-verified). Column 1's wow is
  a scripted `run_button` cell; do not plan a chat widget under this pin.
- `maxResults` is undocumented in the public spec. It worked live on this account. If a
  future service ignores it, the id filter still works but may hide the only result; the
  anatomy cell should then say "the best match belongs to another app" rather than "MISS".
- Shared cache: a demo re-seed flushes the notebook's entries. The Store cell should be
  re-pressable, and any "0 own results" state should say so.
- The 404 `type` for an expired entry is not in the spec enum. Match loosely, as above.
- `LANGCACHE_HOST` may be pasted with or without `https://`; normalise, do not validate.

## 9. Recommended MVP cell list for column 1 (10 cells, about 300 lines)

Names are full words with the `lc_` prefix. Content that is not about LangCache (the five Q&A
rows, the eight probes, the three variants, the wow script) lives in
`src/cairn_outfitters/gear_care.py` and is imported; every HTTP call, JSON body and status
decision stays in the notebook.

| # | Cell | Kind | Requests | What it shows |
|---|---|---|---|---|
| 1 | `lc_title` | markdown, `hide_code` | 0 | Problem, then "Redis LangCache" in one paragraph; preview status; docs links; `mo.mermaid` cache-aside flow (prompt -> search -> hit: stored answer / miss: LLM -> store). One collapsed `mo.accordion` note: the `langcache` SDK shape, and one line on `attributes` (defined at creation; a 400 `/errors/invalid-data` if the cache has none). |
| 2 | `lc_configure` | code | 1 | `langcache = langcache_session()` from `LANGCACHE_HOST`, `LANGCACHE_CACHE_ID`, `LANGCACHE_API_KEY`; `mo.stop` with one "not configured" callout naming the three variables and the console path; else one read-only search for "Cairn Outfitters connection check" (`maxResults` 1) and a line "Connected: 200 in N ms". 401 and 404 decoded in one sentence each. Defines `langcache_entry_ids = []` and `langcache_run_tag`. |
| 3 | `lc_store_button` + `lc_store` | run_button, code | 5 | Lead-in with a colon; `POST /entries` per Q&A with `ttlMillis` 900000; appends each `entryId`; shows the first request JSON and the five ids via `mo.json`. Re-pressable. |
| 4 | `lc_anatomy_of_a_hit` | code | 1 | Fixed rain-jacket paraphrase; `similarityThreshold` 0.85, `searchStrategies ["semantic"]`, `maxResults` 5; `wire_view` accordion (request, redacted response); one sentence decoding `similarity`, `searchStrategy` and the empty-`data` miss case. |
| 5 | `lc_probe_button` + `lc_probe_results` | run_button, code | 8 | Eight labelled probes searched once at 0.50; keeps best own match and score per probe in `lc_probe_scores`. |
| 6 | `lc_threshold_slider` + `lc_threshold_table` | slider (debounced), code | 0 | Re-scores `lc_probe_scores` at the slider value: HIT / WRONG ANSWER / MISS column; one line: at 0.96 no wrong answer is left and most paraphrases miss too. |
| 7 | `lc_strategies` | run_button, code | 9 | Three variants x `["exact"]`, `["semantic"]`, `["exact","semantic"]` at the slider's threshold; table of `similarity` or "miss" and the reported `searchStrategy`. |
| 8 | `lc_cache_aside_wow` (optional) | run_button, code | 4 | Scripted: MISS -> store (`ttlMillis` 120000) -> paraphrase HIT -> DELETE 204; four-row timeline. First to drop if over budget. |
| 9 | `lc_clean_up` | button, code | n | `DELETE /entries/{id}` for every id in `langcache_entry_ids`; counts 204 "deleted" and 404 gone-types "already expired"; any other status shows its body. Reminder line: every entry also carried a TTL. |
| 10 | `lc_helpers` | code, near the top of the column | 0 | `langcache_session`, `langcache_call`, `own_hits`, `wire_view` (about 40 lines), visible on purpose: this is the whole client. |

Total live requests for a full run: 28 (1 + 5 + 1 + 8 + 9 + 4) plus Clean up. All searches
send `maxResults` 5 and pass through `own_hits`; all stores send `ttlMillis`; nothing calls
`/flush` or a bodied `DELETE /entries`.
