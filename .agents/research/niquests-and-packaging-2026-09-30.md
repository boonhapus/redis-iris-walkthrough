# niquests 3.21.2 and packaging the notebook as a uv project

Research brief for the iris-basics rewrite. Written 2026-09-30 on Windows 11 with uv 0.11.25,
CPython 3.13.14. Every fact is tagged **VERIFIED** (introspected, run, or read in the primary
source today) or **UNVERIFIED** (inferred, or read only in a secondary source). Nothing here
contacted a Redis or Iris service: the only network calls were PyPI JSON, GitHub raw docs, and a
local `http.server` on 127.0.0.1 used to prove the niquests helper.

Scratch evidence lives in `%TEMP%\research\` (`lockproj/`, `lock311/`, `hatchproj/`,
`niq_loopback.py`); it is disposable.

---

## 0. Headline findings (read these before touching pyproject.toml)

1. **`huggingface-hub==2.0.0` cannot be installed with `model2vec==0.9.0`.** model2vec needs
   `tokenizers>=0.20`, and every tokenizers release through 0.23.2 (latest, 2026-09-03) declares
   `huggingface-hub<2.0`. uv's resolver says so verbatim. Pin **`huggingface-hub==1.33.0`**
   (last 1.x, uploaded 2026-09-24, three hours before 2.0.0). **VERIFIED** (uv lock error +
   PyPI `requires_dist` of tokenizers 0.23.2 and model2vec 0.9.0).
2. **`numpy==2.5.3` requires Python >=3.12**, so a flat pin cannot lock under
   `requires-python = ">=3.11,<3.14"`. numpy 2.4.6 (2026-05-18) is the last release for 3.11.
   Use a marker split (section B4) or accept `>=3.12,<3.14`. **VERIFIED** (uv lock error; PyPI
   `requires_python` per release).
3. **uv_build needs `module-name`.** The distribution is `redis-iris-basics` but the module is
   not `redis_iris_basics`, so `[tool.uv.build-backend] module-name = "<package>"` is
   mandatory; hatchling likewise needs `packages = ["src/<package>"]`. **VERIFIED** (hatchling
   sync failed without it; uv_build synced with it).
4. **`redis-context-retriever` imports as `context_surfaces`**, not `redis_context_retriever`,
   and it drags `httpx`, `fastmcp`, `keyring`, `cryptography`, `typer`, `rich` and `pydantic`
   into the environment. "Replace httpx with niquests" is therefore about notebook code; httpx
   stays in `uv.lock` as a transitive dependency. **VERIFIED** (import test; `uv.lock`).
5. **niquests**: `Session(timeout=(connect, read), retries=N, headers={...}, base_url=...,
   disable_http3=True)` sets everything once; `session.request("DELETE", path, json=...)`
   sends a JSON body; `response.request` is the `PreparedRequest` that went on the wire;
   `retries=N` retries connection failures only (no 5xx retries); `raise_for_status()` drops
   the error body. **VERIFIED** (introspection + loopback run, section A10).
6. **marimo's "native secret storage" is exactly two providers**: the process environment and
   the `.env` files listed in `[tool.marimo.runtime] dotenv`. The editor's Secrets panel writes
   `KEY="value"` to that `.env`; nothing else is stored anywhere. `.env` next to
   `pyproject.toml` is loaded into `os.environ` at kernel start and never overrides an existing
   variable. `app.run()` from plain `python`/pytest does **not** load `.env`. `app.run()`
   returns normally when a cell hits `mo.stop()` and **raises** the cell's exception when a cell
   errors. **VERIFIED** (marimo 0.25.0 source; export/pytest runs).
7. The embedding model cache on Windows is `C:\Users\<you>\.cache\huggingface\hub`
   (`HF_HUB_CACHE`), outside the repo; nothing to gitignore. **VERIFIED** (huggingface_hub
   1.33.0 constants).

---

## Part A: niquests 3.21.2

### A1. Signatures (VERIFIED by `inspect.signature` on 3.21.2)

`niquests.Session.__init__` (keyword-only) — the arguments that matter here:

| kwarg | type | default | use |
|---|---|---|---|
| `timeout` | `int \| float \| (connect, read) \| (connect, read, x) \| urllib3.Timeout` | `None` | session-wide default when a call omits `timeout=` |
| `retries` | `bool \| int \| niquests.RetryConfiguration` | `0` | built into the adapter as `max_retries` |
| `headers` | mapping | `None` | merged into every request |
| `auth` | `(user, pass) \| str \| AuthBase \| callable` | `None` | `niquests.auth.BearerTokenAuth` exists; a plain header is simpler and shows on the wire view |
| `base_url` | `str` | `None` | relative paths are merged; `response.request.url` is absolute |
| `disable_http1/2/3` | `bool` | `False` | protocol negotiation switches |
| `verify` | `bool \| path \| PEM \| fingerprint` | `None` (= True) | leave default |
| `revocation_configuration` | `RevocationConfiguration \| None` | `PREFER_OCSP, strict_mode=False` | `None` disables OCSP/CRL lookups |
| `pool_connections`, `pool_maxsize` | `int` | 10, 10 | fine |
| `happy_eyeballs`, `resolver`, `multiplexed`, `quic_cache_layer`, `tls_configuration`, `json_encoder` | | | not needed |

`niquests.Session.request(method, url, params=None, data=None, headers=None, cookies=None,
files=None, auth=None, timeout=None, allow_redirects=True, proxies=None, hooks=None,
stream=None, verify=None, cert=None, json=None, override_scheme=None) -> Response`.
`Session.get/post/delete(url, *, ..., **kwargs)` forward `**kwargs`, so `session.delete(url,
json=...)` also works.

`niquests.RetryConfiguration` **is** `urllib3.util.retry.Retry` from `urllib3-future` (same
object). Its `__init__` defaults: `total=10, connect=None, read=None, redirect=None,
status=None, other=None, allowed_methods={GET, HEAD, PUT, DELETE, OPTIONS, TRACE},
status_forcelist=None, backoff_factor=0, backoff_max=120, raise_on_status=True,
respect_retry_after_header=True, backoff_jitter=0.0`.

Session default headers: `User-Agent: niquests/3.21.2`, `Accept-Encoding: gzip, deflate`,
`Accept: */*`, `Connection: keep-alive`.

`Response` instance attributes (set in `__init__`): `status_code`, `headers`
(CaseInsensitiveDict), `url`, `reason`, `elapsed` (timedelta), `request` (PreparedRequest),
`history`, `http_version` (int: 11, 20, 30), `conn_info`, `ocsp_verified`; methods `json()`,
`text`, `content`, `ok`, `raise_for_status()`.

`PreparedRequest` instance attributes: `method`, `url`, `headers`, `body` (bytes when built
from `json=`), `conn_info`, `path_url`.

### A2. Timeouts

- `Session(timeout=(5, 30))` is stored on `session.timeout` and used whenever a call omits
  `timeout=`. Source line in `Session.request`: `"timeout": timeout or self.timeout`; if both
  are `None`, the method default applies. **VERIFIED** (source + loopback: `s.timeout` printed
  `(5, 30)`).
- Method defaults when nothing is set: 30 s for GET/HEAD/OPTIONS, 120 s for write methods
  (`READ_DEFAULT_TIMEOUT=30`, `WRITE_DEFAULT_TIMEOUT=120` in `niquests._constant`).
  **VERIFIED**.
- A scalar is a per-operation socket timeout, not a wall clock for the whole download; the
  tuple is `(connect, read)`. The docs warn that a host with several addresses can take
  `n * connect`. **VERIFIED** (quickstart "Timeouts").
- A connect failure raises `niquests.ConnectTimeout` (subclass of `Timeout`, of
  `RequestException`). On this Windows box, a closed loopback port with `timeout=(1, 1)` raised
  `ConnectTimeout` after 1.01 s rather than a refused-connection error. **VERIFIED** (loopback).
- Recommendation for the notebook: `timeout=(5, 30)` for LangCache and Agent Memory,
  `(5, 60)` for Context Retriever (surface creation and data import do real work). One value
  per session, nothing per call.

### A3. Retries: what `retries=` actually retries

`Session(retries=N)` becomes `Retry(total=N)` on the adapter (`Retry.from_int`). Reading
`Retry.increment` and `Retry.is_retry` in urllib3-future 2.25.901 (**VERIFIED**):

| failure | retried with `retries=2`? | why |
|---|---|---|
| connection error before anything was sent (DNS, refused, connect timeout, TLS) | yes, any method | the connect branch has no method check |
| read error after the request started (read timeout, protocol error) | only GET/HEAD/PUT/DELETE/OPTIONS/TRACE | `_is_method_retryable`; POST is not idempotent |
| HTTP 5xx / 4xx | **no** | `status_forcelist` is empty |
| 413/429/503 **with** a `Retry-After` header | yes, idempotent methods only | `respect_retry_after_header=True` |
| redirects | follow up to 30 (`allow_redirects`) | separate counter |

Loopback proof: `Session(retries=3).get("/500")` made exactly one call. **VERIFIED**.

`backoff_factor` defaults to 0 (immediate retries). To add backoff or status retries build a
`RetryConfiguration`, for example
`niquests.RetryConfiguration(total=2, backoff_factor=0.5, status_forcelist=[502, 503, 504],
allowed_methods=["GET", "DELETE"], raise_on_status=False)`. `raise_on_status=False` matters:
with the default `True`, exhausting status retries raises `niquests.RetryError` instead of
returning the last response and its body (standard urllib3 behaviour; **UNVERIFIED** by test).

Recommendation for an MVP teaching notebook: `retries=2` on every session and a one-line
comment ("connection failures are retried twice; nothing that reached the server is re-sent
unless the method is idempotent"). Keep 5xx handling visible in the cell instead of hiding it
in a retry policy.

### A4. Headers and auth per service

| service | auth header | base URL for the session | source |
|---|---|---|---|
| LangCache | `Authorization: Bearer <LANGCACHE_API_KEY>` | `{LANGCACHE_HOST}/v1/caches/{LANGCACHE_CACHE_ID}` | research/langcache.md (verified there against the SDK wire format) |
| Agent Memory | `Authorization: Bearer <MEMORY_API_KEY>` | `{MEMORY_API_BASE_URL}/v1/stores/{MEMORY_STORE_ID}` | research/agent-memory.md (verified there) |
| Context Retriever admin REST | `X-API-Key: <CTX_ADMIN_KEY>` | `{CTX_API_URL}/api/v1` | research/context-retriever.md section 5 |
| Context Retriever MCP | `X-API-Key: <agent key>` | `{CTX_MCP_URL}` | research/context-retriever.md section 6 |

So the header names to mask are exactly `Authorization` and `X-API-Key`. Set them on the
session (`headers=`), not per call: the wire view then shows them on every request, masked.

Also set `Accept: application/json` and, deliberately, `Content-Type: application/json` on the
session. niquests' `json=` path stamps `Content-Type: application/json;charset=utf-8` unless a
Content-Type header is already present ("Add content-type if it wasn't explicitly provided",
`PreparedRequest.prepare_body`). httpx sent plain `application/json`, and every Redis docs curl
example uses plain `application/json`. Whether the three services accept the `;charset=utf-8`
variant is **UNVERIFIED** (no live calls), so pin the header and remove the question.
**VERIFIED** (source; loopback showed `application/json;charset=utf-8` without the override).

### A5. Status handling, `.json()`, error bodies

- `raise_for_status()` raises `niquests.HTTPError("500 Server Error: Internal Server Error for
  url: ...")` and the message does **not** include the body. The Iris services answer errors
  with a JSON problem body (`type`, `title`, `detail`; LangCache's expired-entry delete is
  `404 /errors/resource-not-found`, per the live run). Never call `raise_for_status()`; branch
  on `response.status_code` and show the parsed body. **VERIFIED** (loopback: `has body? False`).
- `.json()` on an empty body (204 from a DELETE) raises `niquests.JSONDecodeError`, which is a
  `ValueError`. Guard with `if response.content:`. **VERIFIED** (loopback returned `None` for
  204 with the guard).
- All network failures subclass `niquests.RequestException`: `ConnectionError`,
  `ConnectTimeout`, `ReadTimeout`, `SSLError`, `RetryError`, `TooManyRedirects`. One
  `except niquests.RequestException as exc:` in the cell is enough. **VERIFIED** (names in
  `niquests.exceptions`).
- A `Response` is truthy on 2xx via `response.ok` (`status_code < 400`); prefer the explicit
  status check so 404-as-expected paths (expired entry) read clearly.

### A6. HTTP/2 by default, HTTP/3 off for determinism

- HTTP/1.1, HTTP/2 (`jh2`) and HTTP/3 (`qh3`) are all enabled by default; the negotiated
  version is `response.http_version` (11 / 20 / 30). **VERIFIED** (`python -m niquests.help`
  printed `http3.enabled: true, qh3 2.0.3`).
- HTTP/3 is reached only after the server advertises `Alt-Svc: h3` or an HTTPS DNS record; the
  docs say repeated calls "do not guarantee a particular HTTP/2-to-HTTP/3 sequence". Pass
  `disable_http3=True` so the wire view is stable across runs and no QUIC/UDP path is involved
  behind corporate firewalls. **VERIFIED** (quickstart "HTTP/3 over QUIC"; adapter flag
  `_disable_http3=True` after construction).
- Whether Redis Cloud's LangCache / Agent Memory / Context Retriever endpoints negotiate h2 at
  all is **UNVERIFIED**; leaving HTTP/2 on is harmless either way.

### A7. DELETE with a JSON body

`session.request("DELETE", "/entries", json={"attributes": {...}})` sends the body with
`Content-Length` and the JSON content type; `prepare_body` does not look at the method.
`session.delete(path, json=...)` is equivalent (`**kwargs` forwarded). The loopback server
received `DELETE /v1/entries` with body `{"attributes": {"run": "abc"}}`. **VERIFIED**.

This covers LangCache `DELETE /entries` (by attributes) and Agent Memory
`DELETE /long-term-memory` with `{"memoryIds": [...]}`.

### A8. Wire view: `response.request`, `response.elapsed`, masking

- `response.request` is the exact `PreparedRequest` sent: `.method` (str), `.url` (absolute,
  base_url already merged, query string included), `.headers` (CaseInsensitiveDict including
  the session defaults and `Content-Length`), `.body` (bytes for `json=`; `json.loads` gives
  the dict back). **VERIFIED** (loopback wire dump).
- `response.elapsed` is a `timedelta` "between sending the first byte of the request and
  finishing parsing the headers", so it excludes body download. `round(elapsed.total_seconds()
  * 1000, 1)` gives milliseconds. **VERIFIED** (docstring).
- `response.request.conn_info` (TLS version, cipher, resolved IP, negotiated protocol) exists if
  a deeper "what happened" panel is ever wanted; skip for MVP.
- Masking: rebuild the headers dict with a case-insensitive check against
  `{"authorization", "x-api-key"}`. Do not print `dict(headers)` or the `PreparedRequest`
  itself. Body masking is the caller's job: the only secret that travels in a body is the Redis
  password inside Context Retriever's `connection_config` on surface creation; mask
  `password` there before showing it (research/context-retriever.md already flags this).

### A9. Windows and dependency gotchas

Installed with `niquests==3.21.2` on Windows x86_64 / CPython 3.13 (**VERIFIED**,
`importlib.metadata` in the temp env): `urllib3-future 2.25.901`, `charset-normalizer 3.5.2`,
`wassima 2.1.4`, `qh3 2.0.3`, `jh2 5.0.15`, `h11 0.16.0`. **Not** installed: `certifi`,
`idna`, `urllib3` (classic), `h2`.

- **`qh3` is not optional on this platform.** urllib3-future's own `Requires-Dist` pulls
  `qh3` on Windows/macOS/Linux for mainstream CPUs even without the `http3` extra; niquests'
  `http3`/`ocsp` extras only re-declare it. It ships as a prebuilt wheel (2.2 MiB download, no
  compiler). You cannot leave it out; you can only switch HTTP/3 off per session
  (`disable_http3=True`). **VERIFIED** (metadata; uv "Downloading qh3 (2.2MiB)").
- **Do not add extras.** `niquests[full]`, `[speedups]` (orjson), `[socks]`, `[ws]`, `[zstd]`,
  `[brotli]`, `[rtls]`, `[utls]` all add binary wheels the notebook does not use. Pin plain
  `niquests==3.21.2`. **VERIFIED** (extras list in metadata).
- **CA trust comes from `wassima`, not certifi.** wassima reads the operating-system trust store
  (132 roots found on this machine from the Windows store) and falls back to its embedded CCADB
  bundle only if the OS returns nothing. Consequence: a corporate root CA installed in Windows
  is trusted without `REQUESTS_CA_BUNDLE` (**UNVERIFIED** in practice, follows from the
  mechanism). `REQUESTS_CA_BUNDLE`/`CURL_CA_BUNDLE` are still honoured when set, requests-style
  (**VERIFIED** docs mention). Nothing to configure for the notebook.
- **OCSP is on by default, soft-fail.** With `verify=True` niquests adds an OCSP (or CRL) check
  after the TLS handshake on first connect, ignores responder failures, and caches results.
  It is one extra outbound call per host; if it ever slows the first request behind a proxy,
  pass `revocation_configuration=None`. **VERIFIED** (signature default, advanced "OCSP and
  certificate revocation").
- **Content-Type charset** (A4): set the header on the session.
- **Loopback behaviour**: a closed local port surfaced as `ConnectTimeout`, not a refusal; do
  not write tests that expect `ConnectionError` specifically.
- `python -m niquests.help` prints the whole stack as JSON; useful in a README
  "Troubleshooting" section.

### A10. The helper (copy-ready; plain Python; lives in the notebook's setup cell)

The wire view is what teaches the REST shape, so this stays in the notebook, not in `src/`.

```python
import json
import niquests

MASKED_HEADERS = {"authorization", "x-api-key"}

def call_service(session, method, path, *, json_body=None, params=None):
    """One request to an Iris service. Returns (status, body, wire); wire is safe to show."""
    response = session.request(method, path, json=json_body, params=params)
    sent = response.request  # the PreparedRequest that actually went on the wire
    body = None
    if response.content:
        try:
            body = response.json()
        except ValueError:
            body = response.text
    headers = {k: "***" if k.lower() in MASKED_HEADERS else v for k, v in sent.headers.items()}
    wire = {"method": sent.method, "url": sent.url, "headers": headers,
            "body": json.loads(sent.body) if sent.body else None,
            "status": response.status_code, "ms": round(response.elapsed.total_seconds() * 1000, 1)}
    return response.status_code, body, wire
```

One session per service, built once in that column's configure cell:

```python
langcache = niquests.Session(
    base_url=f"{LANGCACHE_HOST.rstrip('/')}/v1/caches/{LANGCACHE_CACHE_ID}",
    headers={
        "Authorization": f"Bearer {LANGCACHE_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "redis-iris-basics",
    },
    timeout=(5, 30),      # seconds: connect, read
    retries=2,            # connection failures only; nothing that reached the server is re-sent
    disable_http3=True,   # keep the wire view identical run to run
)
```

Agent Memory is the same with `MEMORY_*` and base `f"{MEMORY_API_BASE_URL.rstrip('/')}/v1/stores/{MEMORY_STORE_ID}"`.
Context Retriever uses `{"X-API-Key": CTX_ADMIN_KEY, ...}` and `timeout=(5, 60)`.

Using it in a cell, with `mo.json` for the wire:

```python
status, body, wire = call_service(langcache, "POST", "/entries/search",
                                  json_body={"prompt": question, "similarityThreshold": 0.85})
mo.stop(status != 200, mo.callout(mo.json(body or {}), kind="danger", title=f"LangCache answered {status}"))
mo.vstack([mo.json(wire, label="What went on the wire"), mo.json(body, label="Response")])
```

Wrap network failures once per cell:

```python
try:
    status, body, wire = call_service(langcache, "POST", "/entries/search", json_body=...)
except niquests.RequestException as exc:
    mo.stop(True, mo.callout(f"{type(exc).__name__}: {exc}", kind="danger", title="LangCache is unreachable"))
```

`mo.json(data: str | dict | list, label: str | None = None)` **VERIFIED** (0.25.0 signature).

**Loopback evidence** (`%TEMP%\research\niq_loopback.py`, `uv run --python 3.13 --no-project
--with niquests==3.21.2`), all **VERIFIED**:

```
session.timeout (5, 30) adapter retries Retry(total=2, connect=None, read=None, redirect=None, status=None)
DELETE status 200
server saw {'method': 'DELETE', 'path': '/v1/entries', 'body': '{"attributes": {"run": "abc"}}',
            'auth': 'Bearer <fake token>', 'ctype': 'application/json;charset=utf-8', 'ua': 'iris-basics-notebook'}
wire {'method': 'DELETE', 'url': 'http://127.0.0.1:55185/v1/entries',
      'headers': {'Authorization': '***', 'User-Agent': 'iris-basics-notebook', 'Content-Length': '30',
                  'Content-Type': 'application/json;charset=utf-8'},
      'body': {'attributes': {'run': 'abc'}}, 'status': 200, 'ms': 43.7, 'http_version': 11}
204 -> 204 None None
500 -> 500 {'type': '/errors/boom', 'detail': 'body kept'}
404 -> 404 {'type': '/errors/resource-not-found', 'title': 'Not Found'}
raise_for_status message: 500 Server Error: Internal Server Error for url: ... | has body? False
calls for one GET /500 with forcelist [502,503,504]: 1
retries=3 (int) calls for GET /500: 1
x-api-key wire headers {'X-API-Key': '***', ...} | server saw key? True
closed port -> ConnectTimeout 1.01 s
```

The fake token never appeared in the serialised wire dict (asserted in the script).

---

## Part B: packaging as a uv project with a `src/` layout

### B4. Files, ready to copy

Everything below was exercised in `%TEMP%\research\lockproj` (Python 3.12-3.14 variant: lock,
sync, imports, `marimo check --strict`, pytest with `app.run()`, HTML export) and
`%TEMP%\research\lock311` (3.11 marker variant: lock only). The support package was called
`cairn_demo_support` in the scratch project; the files below use the recommended name
`cairn_support` (section B5). Only the name differs.

#### `pyproject.toml`

```toml
[project]
name = "redis-iris-basics"
version = "0.1.0"
description = "Redis Iris basics: a marimo notebook that teaches Redis Search, LangCache, Context Retriever and Agent Memory with Cairn Outfitters sample data."
readme = "README.md"
# <3.14: redis-context-retriever 0.0.6's ContextModel crashes on Python 3.14 (verified 2026-09-29).
# >=3.11: redis-context-retriever itself requires Python >=3.11.
requires-python = ">=3.11,<3.14"
dependencies = [
  "marimo==0.25.0",
  "redis==8.1.0",
  "niquests==3.21.2",
  "redis-context-retriever==0.0.6",  # import name: context_surfaces
  "model2vec==0.9.0",
  # numpy 2.5.x needs Python >=3.12; 2.4.6 is the last release that supports 3.11.
  "numpy==2.5.3; python_version >= '3.12'",
  "numpy==2.4.6; python_version < '3.12'",
  # tokenizers (pulled in by model2vec) caps huggingface-hub at <2.0; 1.33.0 is the last 1.x.
  "huggingface-hub==1.33.0",
  # marimo parses .env with python-dotenv when it is installed. Without it, marimo's fallback
  # parser keeps an inline "# comment" as part of the value.
  "python-dotenv==1.2.3",
]

[dependency-groups]
dev = ["pytest==9.1.1"]

[build-system]
requires = ["uv_build>=0.11.25,<0.12.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
# The distribution is redis-iris-basics; the importable package is cairn_support.
module-name = "cairn_support"

[tool.marimo.runtime]
# The default is already [".env"]; stating it makes the Secrets panel list .env as the place
# it writes new keys, and it documents where configuration comes from.
dotenv = [".env"]

[tool.marimo.package_management]
manager = "uv"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

Facts behind each line:

- **requires-python and the numpy split.** `uv lock` with the split resolved 124 packages and
  recorded numpy 2.4.6 for `<3.12` and 2.5.3 for `>=3.12`. **VERIFIED** (lock311). The 3.11
  path was never synced or run, only locked: **UNVERIFIED** at runtime. If the orchestrator
  accepts `">=3.12,<3.14"`, replace the two numpy lines with `"numpy==2.5.3"`; that variant
  locked (119 packages), synced and passed every smoke test on 3.13.14. **VERIFIED**.
- **huggingface-hub 1.33.0** (section 0). The old pin (1.33.0) was right; 2.0.0 was not.
- **model2vec 0.9.0 runtime deps** (PyPI): `jinja2, joblib, numpy, safetensors,
  tokenizers>=0.20, tqdm`. No torch unless the `distill`/`train`/`onnx` extras are requested;
  the synced env has no torch. **VERIFIED**. Resolved: tokenizers 0.23.2, safetensors 0.8.0,
  hf-xet 1.6.0 (a Rust wheel huggingface-hub pulls on AMD64 for Xet downloads).
- **redis 8.1.0**: no required deps on 3.11+ (`async-timeout` only below 3.11.3). No
  `hiredis` extra needed. **VERIFIED**.
- **redis-context-retriever 0.0.6** requires `>=3.11` and pulls `cryptography, fastmcp, httpx,
  keyring, pydantic, pyjwt, python-dateutil, pyyaml, rich, tenacity, typer` (resolved: fastmcp
  4.0.10, pydantic 2.13.5, httpx 0.28.1, keyring 25.7.0, cryptography 50.0.2). Heavy, but it is
  the Iris SDK that defines `ContextModel`, which is Iris protocol and belongs in the notebook.
  **VERIFIED**. `from context_surfaces import ContextModel` imports on 3.13.14. **VERIFIED**.
- **redis-agent-memory 0.4.1** and **anywidget 0.11.0** are deliberately absent. Agent Memory
  is called over REST with niquests, so the SDK (httpx + pydantic) adds nothing. anywidget
  pulls `ipywidgets` and its Jupyter tree; add `anywidget==0.11.0` only if a custom widget
  becomes necessary. **VERIFIED** (metadata).
- **python-dotenv 1.2.3**: marimo checks `DependencyManager.dotenv.has()` and uses
  `dotenv.parser.parse_stream` when available, else its own `_parse_dotenv_bindings`
  (**VERIFIED** source `marimo/_secrets/load_dotenv.py`). The prior marimo brief documents the
  inline-comment difference.
- **uv_build bound.** `uv init --lib` on this machine (uv 0.11.25) templated
  `uv_build>=0.11.25,<0.12.0`. The uv executable bundles a copy of the backend and uses it when
  the version matches; otherwise it downloads a compatible `uv_build` from PyPI, which still
  works. The current docs template is `>=0.12.21,<0.13` because upstream moved on; someone on a
  newer uv will simply trigger the PyPI fallback. **VERIFIED** (docs "Bundled build backend";
  local template). Widening the bound is not recommended: uv's versioning policy allows
  breaking changes across minor versions.
- **Why uv_build over hatchling.** Both installed the package editable via a `.pth` file with no
  `*.egg-info` in `src/` (**VERIFIED** listing of `site-packages` and `src/`). uv_build is
  bundled, validates the layout, and needs zero configuration beyond `module-name`. hatchling
  1.32.4 works too but must be told `[tool.hatch.build.targets.wheel] packages =
  ["src/cairn_support"]`, and without that line `uv sync` fails with a "packages = [...]" hint.
  **VERIFIED** (hatchproj). Recommend **uv_build**.
- **Data files in the package.** uv_build's wheel includes everything under the module root
  except `__pycache__`, `*.pyc`, `*.pyo`, so a `sample_data.json` next to `__init__.py` ships
  without configuration. **VERIFIED** (build-backend docs "File inclusion and exclusion").
  Python literals in `sample_data.py` are simpler still.
- **`[tool.marimo.runtime] dotenv`**: key exists in 0.25.0's `RuntimeConfig`
  (`auto_instantiate, auto_reload, reactive_tests, on_cell_change, watcher_on_save,
  output_max_bytes, serve_cached_sessions_in_apps, std_stream_max_bytes, pythonpath, dotenv,
  default_sql_output, default_auto_download, default_csv_encoding, show_tracebacks`).
  Relative paths resolve against the directory holding `pyproject.toml`. **VERIFIED** (source,
  runtime_configuration.md).
- **`pythonpath` is not set on purpose.** With the editable install, `import cairn_support`
  works under marimo, `python`, and pytest alike. `[tool.marimo.runtime] pythonpath = ["src"]`
  would work only inside marimo (editor, app, script), not under pytest, so it would be a second
  mechanism to explain. **VERIFIED** (docs; pytest run without it).
- **`[tool.marimo.package_management] manager = "uv"`**: the key accepts `pip | rye | uv |
  poetry | pixi` (**VERIFIED** `PackageManagementConfig`). Whether marimo auto-detects uv from
  `uv.lock` is **UNVERIFIED**; setting it is harmless and makes the editor's package panel run
  `uv add`, keeping `pyproject.toml` the single source of truth.
- **`[tool.pytest.ini_options] pythonpath = ["."]`** lets `tests/` do `import iris_basics`
  without a `conftest.py` path hack. **VERIFIED** (2 passed).

#### `.python-version`

```
3.13
```

`uv sync` picked CPython 3.13.14 from it. **VERIFIED**. (3.12 also satisfies the range; 3.13
is the version everything was tested on.)

#### Layout

```
redis-iris-basics/
  pyproject.toml
  uv.lock                  commit it (uv docs: "This file should be checked into version control")
  .python-version          commit it
  .env.example             commit it; comments on their own lines
  .env                     never committed; marimo loads it at kernel start
  iris_basics.py           the notebook (no PEP 723 header: project mode)
  README.md
  src/cairn_support/
    __init__.py
    sample_data.py         Cairn Outfitters help articles, products, conversation turns
    embedder.py            potion-base-8M loader (huggingface_hub cache first, then online)
    display.py             tiny marimo glue only (see B5 for what is banned)
  tests/
    test_notebook.py       app.run() with no credentials; marimo check --strict
    test_cairn_support.py  unit tests for the package
```

Remove the PEP 723 `# /// script` block from `iris_basics.py`. In a project, `uv run
iris_basics.py` would prefer inline metadata over the project (**VERIFIED** marimo scripts.md:
"If the notebook contains inline script metadata, uv uses that metadata instead of the
project's dependencies"), and `marimo edit` offers to sandbox a notebook that carries one;
`--no-sandbox` is the flag that "never runs in a sandbox, and never prompts to" (**VERIFIED**
`marimo edit --help`). Dropping the header removes both surprises.

#### Run commands

All commands are identical in Git Bash, cmd and PowerShell; uv handles the venv, so nothing
needs activating. `uv run` locks and syncs on its own, so `uv sync` is optional but makes the
first start predictable.

| purpose | command | verified today |
|---|---|---|
| create or refresh the environment (installs Python 3.13 if missing) | `uv sync` | yes (temp project) |
| edit the notebook | `uv run marimo edit iris_basics.py` | command shape yes; not launched (headless session) |
| present as an app | `uv run marimo run iris_basics.py` | not launched |
| tests | `uv run pytest` | yes, 2 passed |
| lint the notebook | `uv run marimo check --strict iris_basics.py` | yes, exit 0 on a `width="columns"` notebook |
| static HTML export | `uv run marimo export html iris_basics.py -o iris_basics.html` | yes; `.env` value and package import both present in the output |
| run as a script | `uv run python iris_basics.py` | `app.run()` path yes |

PowerShell notes: if you do activate, it is `.venv\Scripts\Activate.ps1`; environment
overrides are `$env:UV_PROJECT_ENVIRONMENT = "..."`; there is no `python` on this machine's
PATH, `uv run python` is the way to reach the interpreter. `--strict` makes warnings exit
non-zero (**VERIFIED** help text), so CI can rely on the exit code.

A README "Run it" block in the voice guide's register (sentence case, "you", no exclamation):

```markdown
## Run it

You need [uv](https://docs.astral.sh/uv/). The first run installs Python 3.13 and every pinned
package into `.venv/` next to the notebook.

    uv run marimo edit iris_basics.py

`uv run marimo run iris_basics.py` opens the same notebook as an app with the code hidden. Both
commands read `.env` from this folder: copy `.env.example` to `.env` and fill in the variables
for the columns you want to show. A column whose variables are missing says so and stops.
```

#### What the smoke tests proved about marimo (all **VERIFIED** in lockproj, marimo 0.25.0)

- `marimo export html nb.py --no-sandbox` rendered `FAKE=<the .env value>` and the greeting
  from the editable package: `.env` autoload from the `pyproject.toml` directory and the
  `src/` import both work inside the marimo kernel.
- `python -c "import nb; nb.app.run()"` saw `FAKE_KEY = None`: **`app.run()` outside marimo
  does not load `.env`.** The pytest "no credentials" test therefore only has to clear the ten
  configuration variables from the shell environment (`monkeypatch.delenv`).
- `app.run()` with a cell that calls `mo.stop(cond, mo.callout(...))` returned normally; the
  stopped cell's later names and its descendants are absent from `defs`, unrelated cells still
  ran, and `outputs[0]` is the callout.
- `app.run()` with a cell that raises returned the exception to the caller
  (`ZeroDivisionError`). "No cell errored" is simply "app.run() did not raise".
- marimo writes `__marimo__/session/<notebook>.py.json` next to the notebook on export;
  pytest writes `.pytest_cache/`; uv writes `.venv/` (with its own `.gitignore` containing
  `*`) and `uv.lock`.

#### Test suite sketch (small, matches the decisions)

```python
# tests/test_notebook.py
import shutil
import subprocess

CONFIG_VARS = [
    "REDIS_URL", "LANGCACHE_HOST", "LANGCACHE_CACHE_ID", "LANGCACHE_API_KEY",
    "CTX_ADMIN_KEY", "CTX_API_URL", "CTX_MCP_URL",
    "MEMORY_API_BASE_URL", "MEMORY_STORE_ID", "MEMORY_API_KEY",
]

def test_notebook_runs_with_no_credentials(monkeypatch):
    for name in CONFIG_VARS:
        monkeypatch.delenv(name, raising=False)
    import iris_basics
    outputs, defs = iris_basics.app.run()  # raises if any cell errors; mo.stop() is not an error
    assert outputs  # every column rendered at least its "not configured" note

def test_marimo_check_strict():
    marimo = shutil.which("marimo")  # resolves to .venv/Scripts/marimo.exe under uv run
    result = subprocess.run([marimo, "check", "--strict", "iris_basics.py"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
```

Two cautions: (1) if the setup block loads the embedding model eagerly, `app.run()` in tests
will download 30 MB from Hugging Face on a clean machine; load it lazily inside column 0 (behind
the seed button) or set `HF_HUB_OFFLINE=1` in the test and let the loader report the fallback.
(2) `shutil.which("marimo")` under `uv run pytest` is expected to find the venv script, but that
resolution is **UNVERIFIED**; `python -m marimo` is an alternative that is also **UNVERIFIED**.

### B5. Package naming and boundary

The notebook module is `iris_basics.py` at the repo root, so the package cannot be
`iris_basics`. Candidates:

| name | for | against |
|---|---|---|
| **`cairn_support`** (recommended) | Named after the fictional customer, so nothing in it can be mistaken for Redis Iris code; short; reads well in cells (`from cairn_support import HELP_ARTICLES, load_embedder`) | "support" is generic |
| `cairn_outfitters` | Reads as "the customer's own data and tooling", which is exactly what it holds | Long; `cairn_outfitters.load_embedder` mixes the company and a model loader |
| `iris_basics_support` | Obviously tied to the notebook | "iris" in the name invites people to look there for Iris logic, the one thing rule 6 forbids |

Recommendation: `cairn_support`, distribution name `redis-iris-basics`, with
`[tool.uv.build-backend] module-name = "cairn_support"`.

What goes in (`sample_data.py`, `embedder.py`, `display.py`):

- Cairn Outfitters content: help articles, product rows, the conversation turns, the questions
  used in examples, as plain Python literals (or one JSON file next to `__init__.py`).
- The embedder: `load_embedder()` that tries `huggingface_hub.snapshot_download(
  "minishlab/potion-base-8M", local_files_only=True, allow_patterns=[...])` first, then online,
  then `StaticModel.from_pretrained(path)`, and returns `(name, embed, note)` with a hashing
  fallback exactly as the current `rs_load_embedder` does. model2vec 0.9.0 itself resolves a
  repo id through `_resolve_folder`: a local path is used as is; otherwise `force_download=True`
  (the default) always calls `snapshot_download`, and `force_download=False` checks
  `maybe_get_cached_model_path` first. **VERIFIED** (source). Passing the resolved local path
  keeps the model download out of model2vec and makes the offline path explicit.
- Display glue with no Iris knowledge: `mask_headers(dict)`, a `not_configured(column, vars)`
  callout builder, a `score_bar` or similar formatting helper if the columns share one.

What must **not** go in, because it is what the notebook teaches:

- Any HTTP call to LangCache, Agent Memory or Context Retriever, the niquests sessions, their
  base URLs, auth headers, and the `call_service` helper.
- Any Redis command: `FT.CREATE`, `HSET`, `JSON.SET`, `FT.SEARCH`, `FT.HYBRID`, `EXPIRE`,
  `SCAN`, `DEL`, connection construction from `REDIS_URL`.
- Index schemas, vector field definitions (dimensions, distance metric, algorithm), key
  prefixes, the run tag and TTL policy.
- Request and response bodies: `ttlMillis`, `similarityThreshold`, `attributes`, `ownerId`,
  `namespace`, `memoryIds`, `sessionId`, `connection_config`, `context-surfaces` payloads.
- The `ContextModel` classes and MCP JSON-RPC envelopes.
- Anything that reads environment variables for configuration.

Rule of thumb: if a line would appear in a Redis docs page for one of the four products, it
belongs in the notebook cell where the reader can see it.

### B6. `.gitignore`, caches, and the model on disk

```gitignore
# Secrets and per-machine settings (marimo loads .env from this folder)
.env
.env.*
!.env.example

# uv-managed environment and Python caches
.venv/
__pycache__/
.pytest_cache/
*.egg-info/
dist/
build/

# marimo's per-notebook session cache and HTML exports
__marimo__/
*.html

# Editor and OS noise
.idea/
.vscode/
.DS_Store
Thumbs.db
```

- Commit `uv.lock`, `.python-version`, `pyproject.toml`, `.env.example`. uv's `.venv/` already
  contains a `.gitignore` with `*` (**VERIFIED**), the top-level line is belt and braces.
- Drop the old `.agents/build/` ignore line once that folder is deleted.
- `.gitattributes` already normalises to LF; keep it.
- **Model cache location on Windows**: `HF_HOME = C:\Users\<you>\.cache\huggingface`,
  `HF_HUB_CACHE = C:\Users\<you>\.cache\huggingface\hub` (huggingface_hub 1.33.0
  `constants`). **VERIFIED**. The potion-base-8M snapshot (about 30 MB with the four
  `allow_patterns` files) lands there, outside the repo, so there is nothing to ignore. If
  anyone sets `HF_HOME` to a folder inside the repo, add that folder. `HF_HUB_OFFLINE=1`
  forces cache-only loads for demos on a plane.
- No `*.pth` or `*.egg-info` appear in `src/` with either backend (**VERIFIED**), so the
  `*.egg-info/` line only guards against someone running `pip install -e .` by hand.

---

## C. Open questions and risks for the orchestrator

1. **Python 3.11 is locked but untested.** Either accept `">=3.12,<3.14"` (everything verified)
   or keep 3.11 and have someone run `uv sync --python 3.11 && uv run pytest` once.
2. **Content-Type charset** (A4): pin `Content-Type: application/json` on each session; the
   three services' tolerance of `;charset=utf-8` was not tested.
3. **HTTP/2 negotiation** by the hosted endpoints is unknown; `http_version` in the wire view
   will tell on the first live run. Nothing depends on it.
4. **OCSP soft-fail** adds one outbound call per host on first connect; disable per session
   only if a customer network makes the first request slow.
5. **The embedder in `app.run()`** (test caution above): decide where the model loads before
   writing the "no credentials" test.
6. **`marimo run` and `.env`**: verified today through `export html` and previously through
   `edit`/`export` in the marimo brief; `marimo run` uses the same config manager but was not
   launched in this headless session.
7. **`uv_build` bound vs. a customer's newer uv**: works via the PyPI fallback; expect one extra
   small download, not a failure.
8. **httpx remains in the environment** through redis-context-retriever. If "no httpx" is a hard
   requirement, the Context Retriever column would have to hand-write the surface JSON instead
   of exporting a `ContextModel`, which hides Iris detail rather than showing it. Not
   recommended.

## D. Sources

- niquests 3.21.2: `inspect` on `niquests.Session`, `niquests.adapters.HTTPAdapter`,
  `niquests.models`, `niquests._constant`, `niquests._typing`; `python -m niquests.help`;
  https://raw.githubusercontent.com/jawah/niquests/main/docs/user/quickstart.rst ("Timeouts",
  "Errors and exceptions", "HTTP/3 over QUIC"); .../docs/user/advanced.rst ("Session
  Objects" kwarg list, "Request and Response objects", "Prepared requests", "CA certificates",
  "OCSP and certificate revocation", "Disable HTTP/1.1, HTTP/2, and/or HTTP/3", "Example:
  automatic retries").
- urllib3-future 2.25.901: `inspect` on `urllib3.util.retry.Retry.increment`, `is_retry`,
  `_is_method_retryable`; distribution metadata (qh3 platform marker).
- wassima 2.1.4: `root_der_certificates` source and a local call (132 roots).
- uv docs (main): concepts/projects/{init,layout,config,dependencies}.md,
  concepts/build-backend.md; `uv init --lib` and `uv init --lib --build-backend hatchling`
  templates from uv 0.11.25.
- marimo 0.25.0: source of `marimo/_secrets/{secrets,env_provider,models,load_dotenv}.py`,
  `marimo/_config/config.py` (`RuntimeConfig`, `PackageManagementConfig`); docs
  guides/package_management/{projects,importing_local_code,index}.md,
  guides/configuration/runtime_configuration.md, guides/scripts.md; `marimo edit --help`,
  `marimo run --help`, `marimo check --help`.
- PyPI JSON for marimo 0.25.0, redis 8.1.0, niquests 3.21.2, redis-context-retriever 0.0.6,
  redis-agent-memory 0.4.1, model2vec 0.9.0, numpy (all 2.x releases), huggingface-hub (2.0.0
  and release list), tokenizers 0.23.2, python-dotenv 1.2.3, anywidget 0.11.0, hatchling
  1.32.4, pytest 9.1.1.
- Prior briefs in `.agents/plans/research/` for the per-service auth headers and base URLs;
  `.agents/plans/iris-basics-4-column-plan.md` section 10 for live facts.
