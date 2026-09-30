# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = [
#     "marimo==0.25.0",
#     "redis==8.1.0",
#     "numpy==2.4.6",
#     "model2vec==0.9.0",
#     "huggingface-hub==1.33.0",
#     "httpx==0.28.1",
#     "redis-context-retriever==0.0.6",
# ]
#
# [tool.marimo.display]
# cell_output = "above"
# ///
"""Redis Iris basics: one marimo notebook, one column per Iris component.

Column 0 Redis Search, 1 LangCache, 2 Context Retriever, 3 Agent Memory. Each column
teaches its component with real calls and shows the wire behind every one.

Present (code visible):   uvx marimo@0.25.0 edit --sandbox iris_basics.py
                          marimo opens it with nothing run: press Ctrl+Shift+R (Run all
                          stale cells) and wait.
Hand over (code hidden):  uvx marimo@0.25.0 run --sandbox iris_basics.py
                          It runs by itself when opened.
"""

import marimo

__generated_with = "0.25.0"
app = marimo.App(width="columns", app_title="Redis Iris basics")

with app.setup(hide_code=True):
    # Setup: the imports, constants and helpers that every cell shares.
    #
    # Everything here is visible to every cell, so no cell may redefine a name from this
    # block. Names with a leading underscore are private to it: cells cannot see them, so
    # anything a column needs must be public.
    import functools
    import html
    import json
    import os
    import re
    import reprlib
    import sys
    import time
    import uuid
    from collections import deque
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from typing import NamedTuple
    from urllib.parse import quote, quote_plus, unquote, urlsplit

    import httpx
    import marimo as mo
    import redis

    # ---- identity -------------------------------------------------------------
    # RUN tags everything this session creates, so cleanup can find its own resources
    # and two presenters on one database or service never collide. verify.py pins it
    # (IRIS_BASICS_RUN) so it can find the ledger and the footprint afterwards.
    #
    # marimo loads a .env into os.environ before this block runs, so a variable that
    # came from that file looks just like one set on purpose. RUN, AUTORUN and HEADLESS
    # therefore count only when the file does not name them: a shared .env must not pin
    # two presenters to one run id, or press write buttons with nobody's consent.
    def _names_in_dotenv():
        """Setting names in the .env marimo loads for this notebook. Values are never kept."""
        here = (mo.notebook_dir() or Path.cwd()).absolute()
        # marimo loads the .env in the folder of the nearest pyproject.toml at or above the
        # notebook, and only without one, the notebook's own folder. Look in both, so that
        # neither can smuggle a name in.
        anchor = next((d for d in (here, *here.parents) if (d / "pyproject.toml").exists()), here)
        names = set()
        for folder in {here, anchor}:
            try:
                text = (folder / ".env").read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            names.update(re.findall(r"^\s*(?:export\s+)?(\w+)\s*=", text, re.M))
        return names

    _file_names = _names_in_dotenv()

    def _from_environment(name):
        """os.environ[name] as set on purpose: "" if unset or if a .env is where it came from."""
        return "" if name in _file_names else os.environ.get(name, "")

    _pinned = _from_environment("IRIS_BASICS_RUN")
    RUN_PINNED = re.fullmatch(r"[0-9a-f]{8}", _pinned) is not None
    RUN = _pinned if RUN_PINNED else uuid.uuid4().hex[:8]

    PREFIX = f"irisnb_{RUN}_"  # every Redis key and index name we write starts with this
    RS_PREFIX = PREFIX + "rs:"
    CR_PREFIX = PREFIX + "cr:"
    RUN_TAG = f"iris-basics-{RUN}"  # LangCache attribute value, Context Retriever surface name
    AM_OWNER = f"ib-{RUN}"  # Agent Memory ownerId / actorId
    AM_NAMESPACE = "iris-basics-notebook"
    CR_CREATED_BY = "iris-basics-notebook"  # Context Retriever surface metadata: who made it
    KEY_TTL_S = 3600
    LC_TTL_MS = 900_000  # 15 min
    LC_TEACH_TTL_MS = 120_000  # 2 min: a taught answer is visible to other apps sharing the cache
    CR_KEY_TTL_S = 7200  # an agent key expires this long after it is minted, cleanup or not
    CR_LIST_PAGE, CR_LIST_PAGES = 50, 10  # cr_list_surfaces reads at most this much of an account

    # AUTORUN lets verify.py drive the notebook headless. It is honoured only with a
    # pinned RUN, from the real environment, and never in `marimo edit`, the presenting
    # mode: a stray IRIS_BASICS_AUTORUN in someone's shell or .env can never make the
    # notebook press write or delete buttons on its own.
    #
    # marimo also reports mode "edit" for `marimo export`, which is how verify.py runs
    # the notebook, and offers nothing that tells the two apart. So the export says so
    # itself with IRIS_BASICS_HEADLESS=1: a test hook, which a presenter never sets.
    AUTORUN = _from_environment("IRIS_BASICS_AUTORUN")
    if (
        AUTORUN not in ("demo", "cleanup")
        or not RUN_PINNED
        or (mo.app_meta().mode == "edit" and _from_environment("IRIS_BASICS_HEADLESS") != "1")
    ):
        AUTORUN = ""

    def pressed(btn, stage="demo"):
        """True when the user pressed btn, or when AUTORUN is driving this stage.

        Buttons that must never autorun (orphan delete, reclaim, extraction kick-off,
        New session) read `btn.value` directly instead of calling this.
        """
        if stage not in ("demo", "cleanup"):
            raise ValueError(f"unknown stage {stage!r}")
        return bool(btn.value) or AUTORUN == stage

    # ---- secrets ----------------------------------------------------------------
    # SECRETS is the runtime registry of every credential we have seen: env secrets,
    # the (unquoted) password in a Redis URL, minted agent keys. scrub() masks all of
    # them, in every spelling they take after passing through a URL, JSON or HTML.
    def _spellings(secret):
        return {
            secret,
            quote(secret, safe=""),
            quote(secret),
            quote_plus(secret),
            json.dumps(secret)[1:-1],
            json.dumps(secret, ensure_ascii=False)[1:-1],
            html.escape(secret),
            html.escape(secret, quote=False),
        }

    class SecretRegistry:
        """The credentials scrub() masks. Deliberately not a `set`.

        In `marimo edit` every global's value is previewed in the Variables panel, and
        marimo prints the members of a set. This holds the secrets themselves, so its
        str() and repr() say only how many there are, and it cannot be iterated or
        searched: `list(SECRETS)` would print every credential.
        """

        __slots__ = ("_items", "_forms")

        def __init__(self):
            self._items = set()
            self._forms = None

        def add(self, value):
            if value not in self._items:
                self._items.add(value)
                self._forms = None

        def clear(self):
            self._items.clear()
            self._forms = None

        def forms(self):
            """Every spelling of every secret, longest first, cached until the registry changes."""
            if self._forms is None:
                spellings = {form for secret in self._items for form in _spellings(secret)}
                # Longest first, so a short spelling never leaves a fragment of a long one.
                self._forms = tuple(sorted(spellings, key=len, reverse=True))
            return self._forms

        def __len__(self):
            return len(self._items)

        def __repr__(self):
            return f"SecretRegistry({len(self._items)} secrets)"

    SECRETS = SecretRegistry()

    def register_secret(value):
        """Remember a credential so scrub() masks it wherever it surfaces; return it as a str."""
        if isinstance(value, Secret):
            value = value.reveal()
        elif not isinstance(value, str):
            # str(None) or str(6379) would register "None" or "6379" and mangle ordinary text.
            raise TypeError(f"a secret must be a str or a Secret, not {type(value).__name__}")
        if len(value) >= 4:  # shorter strings would mangle ordinary text
            SECRETS.add(value)
        return value

    def register_url_password(url):
        """Register the password embedded in a redis:// URL, decoded and as written."""
        try:
            password = urlsplit(url).password
        except ValueError:
            return
        if password:
            register_secret(password)
            register_secret(unquote(password))

    # Credentials that follow a known shape are masked even if never registered.
    _SECRET_SHAPES = (
        (re.compile(r"c[se]_(?:admin|agent)_[\w-]+"), "****"),
        # A $ENV_VAR placeholder (as_curl) is not a secret, and a quote ends the token, so the
        # curl in a wire view stays copy-pasteable.
        (re.compile(r"(?i)\bBearer\s+(?!\$)[^\s\"']+"), "Bearer ****"),
        # The username may be empty: redis://:password@host is the commonest spelling.
        (re.compile(r"(rediss?://)[^:@/\s]*:[^@\s]+@"), r"\1****@"),
    )

    def scrub(text):
        """Mask every known secret, and anything shaped like one, in text."""
        text = text if isinstance(text, str) else str(text)
        for form in SECRETS.forms():
            text = text.replace(form, "****")
        for shape, replacement in _SECRET_SHAPES:
            text = shape.sub(replacement, text)
        return text

    class Secret:
        """A credential that cannot leak by accident: str/repr are masked, reveal() is explicit.

        Deliberately not a `str`. marimo's Variables panel and tracebacks show repr(), so
        a secret held in a config object appears as ••••1234 instead of the real value.
        """

        __slots__ = ("_value",)

        def __init__(self, value):
            self._value = register_secret(value)

        def reveal(self):
            return self._value

        def masked(self):
            if not self._value:
                return ""
            # A short secret would give away half of itself, so it shows no tail at all.
            return "••••" + (self._value[-4:] if len(self._value) > 8 else "")

        def __bool__(self):
            return bool(self._value)

        def __repr__(self):
            return f"Secret({self.masked()})"

        def __str__(self):
            return self.masked()

    _UNSAFE_CHARS = re.compile(r"[\s\x00-\x1f\x7f\u200b-\u200f\u2060\ufeff]")

    def validate_secret(name, value):
        """Return value if it is a plausible credential; else raise without echoing it."""
        if not value:
            raise ValueError(f"{name} is empty")
        if _UNSAFE_CHARS.search(value):
            raise ValueError(
                f"{name} contains whitespace or a control character "
                "(a stray newline or space from copy-paste?). The value is not shown."
            )
        return value

    # ---- settings ---------------------------------------------------------------
    # Precedence: os.environ (marimo has already loaded .env into it), then the file
    # named by IRIS_BASICS_ENV_FILE, else .env next to the notebook unless
    # IRIS_BASICS_NO_DOTENV=1. Only the names below are ever served; everything else in
    # a settings file is skipped before its value is looked at. The file is never
    # written into os.environ.
    #
    # Deliberately NOT read: CTX_SURFACE_ID and MCP_AGENT_KEY (another app's surface and
    # key: we mint our own), MEMORY_OWNER_ID and MEMORY_NAMESPACE (scope is per RUN),
    # OPENAI_* and DEMO_* (no LLM here).
    ENV_ALLOWLIST = frozenset(
        {
            "REDIS_URL", "REDIS_HOST", "REDIS_PORT", "REDIS_USERNAME", "REDIS_PASSWORD",
            "REDIS_SSL", "REDIS_DB",
            "LANGCACHE_HOST", "LANGCACHE_CACHE_ID", "LANGCACHE_API_KEY", "LANGCACHE_SCOPE_ATTR",
            "CTX_ADMIN_KEY", "CTX_API_URL", "CTX_MCP_URL",
            "MEMORY_API_BASE_URL", "MEMORY_STORE_ID", "MEMORY_API_KEY",
        }
    )  # fmt: skip
    SECRET_ENV = frozenset(
        {"REDIS_URL", "REDIS_PASSWORD", "LANGCACHE_API_KEY", "CTX_ADMIN_KEY", "MEMORY_API_KEY"}
    )

    def _clean(value):
        """Strip whitespace, a stray \\r and one layer of matching quotes."""
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].strip()
        return value

    def parse_env(text):
        """Parse KEY=VALUE lines into a dict, keeping allowlisted names only.

        Tolerates CRLF, `export ` and quotes; `KEY=""` counts as unset. Comments must sit
        on their own lines. An inline `# note` stays part of the value here, but marimo's
        own .env loader drops it when python-dotenv is installed (it is, in the sandbox)
        and keeps it when not, so the loaders cannot be trusted to agree about it.
        """
        found = {}
        for line in re.split(r"\r\n|\r|\n", text):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.removeprefix("export ").partition("=")
            name = name.strip()
            if name in ENV_ALLOWLIST and (value := _clean(value)):
                found[name] = value
        return found

    def env_file():
        """The settings file to consult, or None if there is nothing to look for."""
        explicit = os.environ.get("IRIS_BASICS_ENV_FILE", "").strip()
        if explicit:
            return Path(explicit).expanduser()
        if os.environ.get("IRIS_BASICS_NO_DOTENV") == "1":
            return None
        return (mo.notebook_dir() or Path.cwd()) / ".env"

    @functools.lru_cache(maxsize=8)
    def _parse_env_file(path, mtime_ns, size):
        # Keyed on mtime and size, so editing the file takes effect on the next call.
        return parse_env(Path(path).read_text(encoding="utf-8-sig", errors="replace"))

    def env_file_values():
        path = env_file()
        if path is None:
            return {}
        try:
            stat = path.stat()
            return _parse_env_file(str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            return {}

    def env(name):
        """Setting NAME from os.environ, else the settings file; "" when unset."""
        if name not in ENV_ALLOWLIST:
            raise KeyError(f"{name} is not an allowlisted setting")
        value = _clean(os.environ.get(name, "")) or env_file_values().get(name, "")
        if value and name in SECRET_ENV:
            register_secret(value)
            if name == "REDIS_URL":
                register_url_password(value)
        return value

    def env_present():
        """Allowlisted settings that currently have a value. Names only, never values."""
        return sorted(name for name in ENV_ALLOWLIST if env(name))

    def redis_url_from_env():
        """REDIS_URL, else a URL composed from REDIS_HOST/PORT/USERNAME/PASSWORD/SSL/DB."""
        url = env("REDIS_URL")
        if url or not env("REDIS_HOST"):
            return url
        scheme = "rediss" if env("REDIS_SSL").lower() in ("1", "true", "yes", "on") else "redis"
        user, password = env("REDIS_USERNAME"), env("REDIS_PASSWORD")
        # Userinfo is percent-encoded so a password containing @ / : # survives the URL.
        userinfo = quote(user or "default", safe="") + (
            ":" + quote(password, safe="") if password else ""
        )
        db = f"/{env('REDIS_DB')}" if env("REDIS_DB") else ""
        return f"{scheme}://{userinfo}@{env('REDIS_HOST')}:{env('REDIS_PORT') or 6379}{db}"

    def resolve_secret(name, typed=None):
        """A validated Secret from a config field's value (a SecretText), else the environment.

        None if neither has one. A plain str is refused: a field that holds the typed key as
        text would show it in full in marimo's Variables panel.
        """
        if typed is not None and not isinstance(typed, Secret):
            raise TypeError("typed must be the value of a SecretText field, not a str")
        value = typed.reveal() if typed else env(name)
        return Secret(validate_secret(name, value)) if value else None

    def redis_url_secret(typed=None):
        """The database URL as a Secret: what was typed into a SecretText field, else the environment.

        The environment means REDIS_URL, or a URL composed from REDIS_HOST and friends. None when
        there is neither. Every column that talks to Redis resolves its URL this way.
        """
        if typed:
            return resolve_secret("REDIS_URL", typed)
        url = redis_url_from_env()
        return Secret(validate_secret("REDIS_URL", url)) if url else None

    def secret_hint(name):
        """Placeholder text for a secret field: names the env var and shows a masked tail."""
        value, source = env(name), f"${name}"
        if name == "REDIS_URL" and not value:  # a URL can be composed from the split fields
            value, source = redis_url_from_env(), "$REDIS_HOST, $REDIS_PASSWORD"
        return f"using {source} ({Secret(value).masked()})" if value else f"paste {name}"

    class SecretText(mo.ui.text):
        """A password field whose value is a Secret, not the text typed into it.

        In `marimo edit` the Variables panel previews a UI element's `.value`, which for a
        plain mo.ui.text is the key itself. Here it is None until something is typed, then a
        Secret that previews as ••••1234; pass it to resolve_secret(name, field.value).
        """

        def __init__(self, name, *, label=""):
            super().__init__(
                kind="password", placeholder=secret_hint(name), label=label, full_width=True
            )

        def _convert_value(self, value):
            value = value.strip()
            return Secret(value) if value else None

    # ---- errors -----------------------------------------------------------------
    class ServiceError(Exception):
        """A hosted-service or Redis call failed. The message is already scrubbed.

        kind: "refused" (a choke point stopped it; nothing was sent), "timeout",
        "network", "http" (status outside `expect`), "connect" or "config" (Redis).
        """

        def __init__(self, component, status, kind, detail):
            self.component, self.status, self.kind = component, status, kind
            self.detail = scrub(detail)
            where = f"{component} HTTP {status}" if status else component
            super().__init__(f"{where} {kind}: {self.detail}")

    class Denied(Exception):
        """Raised locally by the Redis guard: the command never reaches the server."""

    # ---- redaction ----------------------------------------------------------------
    # Anything stored in a trace or shown in the UI passes through redact() first. It is
    # recursive and works by key name, because a secret can sit in a body as easily as in
    # a header: the create-surface request carries the database password, and the
    # create-agent-key response carries the agent key.
    REDACT_KEYS = frozenset(
        {
            "password", "pass", "passwd", "key", "apikey", "xapikey", "token", "accesstoken",
            "secret", "clientsecret", "authorization", "proxyauthorization", "cookie",
            "setcookie",
        }
    )  # fmt: skip

    def _norm(key):
        return re.sub(r"[^a-z0-9]", "", str(key).lower())

    def redact(obj, in_config=False):
        """A copy of obj that is safe to store or display.

        Values under sensitive key names become ****, every string is scrubbed, and inside
        a `connection_config` everything except `addr` is masked.
        """
        if isinstance(obj, dict):
            out = {}
            for key, value in obj.items():
                name = _norm(key)
                if name in REDACT_KEYS or (in_config and key != "addr"):
                    out[key] = "****"
                elif name == "connectionconfig":
                    out[key] = redact(value, True) if isinstance(value, dict) else "****"
                else:
                    out[key] = redact(value, in_config)
            return out
        if isinstance(obj, (list, tuple)):
            return [redact(item, in_config) for item in obj]
        if isinstance(obj, str):
            return scrub(obj)
        if isinstance(obj, (bytes, bytearray)):
            return f"<{len(obj)} bytes>"
        return obj

    _LEARN_KEYS = REDACT_KEYS - {"key", "cookie", "setcookie"}

    def _is_credential(key, siblings, value):
        """True for a str under a credential's key name in a dict (siblings) of a request or reply.

        A bare "key" is only a credential in the create-agent-key reply, the one with a key_type.
        """
        name = _norm(key)
        return isinstance(value, str) and (
            name in _LEARN_KEYS or (name == "key" and "key_type" in siblings)
        )

    def _learn_secrets(obj):
        """Register credentials in a request or reply, so later errors and displays mask them."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if _is_credential(key, obj, value):
                    # "Bearer <token>": the token alone is the credential.
                    words = value.split()
                    register_secret(words[-1] if "authorization" in _norm(key) and words else value)
                else:
                    _learn_secrets(value)
        elif isinstance(obj, list):
            for item in obj:
                _learn_secrets(item)

    def _seal(obj):
        """A copy of a reply with every credential wrapped in a Secret.

        A cell that keeps a reply in a global (the create-agent-key reply, say) would show
        the key in full in marimo's Variables panel. A Secret previews as ••••1234, and
        the caller asks for .reveal() when it needs the real value.
        """
        if isinstance(obj, dict):
            return {
                key: Secret(value) if _is_credential(key, obj, value) else _seal(value)
                for key, value in obj.items()
            }
        if isinstance(obj, list):
            return [_seal(item) for item in obj]
        return obj

    def _clip(obj, limit=20_000):
        text = json.dumps(obj, default=str, ensure_ascii=False)
        return obj if len(text) <= limit else text[:limit] + f"... ({len(text)} chars)"

    def _summary(obj, limit=300):
        if not isinstance(obj, str):
            obj = json.dumps(redact(obj), default=str, ensure_ascii=False)
        text = scrub(obj)
        return text if len(text) <= limit else text[: limit - 3] + "..."

    # ---- HTTP: hosted Iris services -------------------------------------------------
    # component is the column prefix: "lc" LangCache, "cr" Context Retriever, "am" Agent
    # Memory. HTTP_CLIENT is a plain httpx.Client so tests can swap in a MockTransport.
    # Columns never touch it: everything goes through http_call.
    HTTP_COMPONENTS = ("lc", "cr", "am")
    HTTP_VERBS = ("GET", "POST", "DELETE")  # the only verbs this notebook ever sends
    HTTP_CLIENT = httpx.Client(
        headers={"User-Agent": "iris-basics-notebook"}, follow_redirects=False
    )
    HTTP_TRACE = deque(maxlen=200)
    T_READ = 10.0  # timeout class for reads and searches (seconds)
    T_WRITE = 30.0  # create surface, import: the hosted service does real work

    class HttpCall(NamedTuple):
        """One traced call. `request` and `response` are already redacted."""

        component: str
        method: str
        url: str
        request: dict
        status: int
        ms: float
        response: object

    def _parse_time(stamp):
        """An RFC 3339 timestamp as an aware datetime; None if it is not one."""
        try:
            when = datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            return None
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)

    def _int_in(value, low, high):
        """True for an int (not a bool) with low <= value <= high."""
        return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high

    # Choke points. http_refusal() vets every request before it leaves this process. The
    # hosted services write to the shared database over their own connections, where no
    # Redis guard can see them, so this is the only place a wrong id, a wrong owner or a
    # missing expiry can be stopped. Requests are judged as httpx will send them. GET passes;
    # a POST or DELETE that no route below allows is refused; there is no PUT.
    #
    #   any component   a verb outside HTTP_VERBS; a path with a segment named flush,
    #                   whatever the verb; an includeAll query parameter.
    #   lc  POST   .../entries              ttlMillis 1..LC_TTL_MS. The cache default is no
    #                                       expiry, and a TTL is the net if this process dies.
    #       POST   .../entries/search       any body.
    #       DELETE .../entries              attributes exactly {LC_SCOPE attribute: RUN_TAG};
    #                                       refused while no scope attribute is set.
    #       DELETE .../entries/{id}         a ledgered entry.
    #   am  POST   .../long-term-memory     every memory has an id starting f"{AM_OWNER}-",
    #                                       ownerId AM_OWNER and namespace AM_NAMESPACE:
    #                                       creating by id overwrites what has that id.
    #       POST   .../long-term-memory/search
    #                                       filter.ownerId.eq or filter.namespace.eq.
    #       POST   .../session-memory/events
    #                                       sessionId starting f"{AM_OWNER}-s" and actorId
    #                                       AM_OWNER: a session's owner is its first event's
    #                                       actor, and extraction writes into that owner's memory.
    #       DELETE .../long-term-memory     an explicit memoryIds list of ledgered ids (with this
    #                                       run's prefix) or granted ones.
    #       DELETE .../session-memory/{id}  a ledgered session, or a granted one.
    #   cr  POST   .../context-surfaces     name RUN_TAG, metadata run and created_by, every key
    #                                       template under CR_PREFIX, a small connection pool.
    #       POST   .../{id}/data            a surface this run created.
    #       POST   .../{id}/agent-keys      the same, and an expires_at within CR_KEY_TTL_S.
    #       POST   .../mcp                  the JSON-RPC endpoint; the agent key scopes it.
    #       DELETE .../context-surfaces/{id}, and its agent keys
    #                                       a surface this run ledgered, or a granted one.
    #
    # "Granted" ids come from the grants section below. Columns extend the choke points
    # without editing this block:
    #   HTTP_RULES   name -> fn(component, method, url, body, header_names) -> str | None.
    #                url is the address as it will be sent, query string included, and
    #                header_names the lower-case names of its headers. Return the reason to
    #                refuse, or None to abstain. Rules can only add refusals, never lift a
    #                built-in one. Naming a rule again replaces it, so a cell may re-run.
    HTTP_RULES = {}
    LC_SCOPE = {"attr": ""}  # the cache attribute that tags this run's entries: set_lc_scope
    _AM_IDS = AM_OWNER + "-"  # every Agent Memory id this run chooses starts with this

    def set_lc_scope(attr):
        """Name the LangCache attribute that tags this run's entries ("" if the cache has none).

        The config cell calls this with its scope-attribute field. Until it names one,
        delete by attributes stays refused: a filter on an attribute the cache does not
        define might be ignored by the server, and no filter at all deletes every entry.
        """
        attr = (attr or "").strip()
        if attr and not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", attr):
            raise ValueError("a LangCache attribute name is 1-32 letters, digits, - or _")
        LC_SCOPE["attr"] = attr

    def _lc_set(match, body):
        ttl = body.get("ttlMillis") if isinstance(body, dict) else None
        if _int_in(ttl, 1, LC_TTL_MS):
            return None
        return f"a cache entry needs ttlMillis from 1 to {LC_TTL_MS}: without one it never expires"

    def _lc_by_attributes(match, body):
        attributes = body.get("attributes") if isinstance(body, dict) else None
        if LC_SCOPE["attr"] and attributes == {LC_SCOPE["attr"]: RUN_TAG}:
            return None
        return (
            f"delete-by-attributes needs a scope attribute and exactly {{scope: {RUN_TAG!r}}}; "
            "an empty or wider filter deletes other apps' entries"
        )

    def _lc_by_id(match, body):
        entry = match["id"]
        if entry in ledger_live("lc", "entry", pending=True) or entry in APPROVED["lc_entry"]:
            return None
        return "that entry was not created by this run"

    def _am_create(match, body):
        memories = body.get("memories") if isinstance(body, dict) else None
        if not isinstance(memories, list) or not memories:
            return "bulk create needs a memories list"
        for memory in memories:
            memory = memory if isinstance(memory, dict) else {}
            if not (
                isinstance(memory.get("id"), str)
                and memory["id"].startswith(_AM_IDS)
                and memory.get("ownerId") == AM_OWNER
                and memory.get("namespace") == AM_NAMESPACE
            ):
                return (
                    f"every memory needs an id starting {_AM_IDS!r}, ownerId {AM_OWNER!r} and "
                    f"namespace {AM_NAMESPACE!r}: creating by id overwrites whatever has that id"
                )
        return None

    def _am_search(match, body):
        body = body if isinstance(body, dict) else {}
        clauses = body.get("filter") if isinstance(body.get("filter"), dict) else {}

        def equals(field):
            clause = clauses.get(field)
            return clause.get("eq") if isinstance(clause, dict) else None

        scopes = [equals("ownerId"), equals("namespace")]
        if not any(isinstance(scope, str) and scope for scope in scopes):
            return (
                "a search needs filter.ownerId.eq or filter.namespace.eq: "
                "unfiltered, it reads the whole shared store"
            )
        if body.get("filterOp", "all") != "all":
            return 'filterOp "any" widens a scoped search to whatever matches any one clause'
        return None

    def _am_event(match, body):
        body = body if isinstance(body, dict) else {}
        session = body.get("sessionId")
        if (
            isinstance(session, str)
            and session.startswith(_AM_IDS + "s")
            and body.get("actorId") == AM_OWNER
        ):
            return None
        return (
            f"an event needs a sessionId starting {_AM_IDS + 's'!r} and actorId {AM_OWNER!r}: "
            "the first event names the session's owner, and extraction writes into that "
            "owner's long-term memory"
        )

    def _am_memories(match, body):
        ids = body.get("memoryIds") if isinstance(body, dict) else None
        if not isinstance(ids, list) or not ids:
            return "bulk delete needs an explicit memoryIds list"
        # An id this run chose carries its prefix, so a ledger line naming any other id is
        # not trusted. Ids the server minted (extraction) count only once granted.
        ledgered = {i for i in ledger_live("am", "memory", pending=True) if i.startswith(_AM_IDS)}
        granted = ledgered | APPROVED["am_memory"] | ORPHANS["am_memory"]
        theirs = [i for i in ids if not (isinstance(i, str) and i in granted)]
        if theirs:
            return f"{len(theirs)} of {len(ids)} memory ids are neither ledgered nor verified ours"
        return None

    def _am_session(match, body):
        session = match["id"]
        ledgered = session in ledger_live("am", "session", pending=True)
        if (ledgered and session.startswith(_AM_IDS)) or session in ORPHANS["am_session"]:
            return None
        return "that session was not created by this run"

    def _cr_create(match, body):
        body = body if isinstance(body, dict) else {}
        meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        stamp = (body.get("name"), meta.get("run"), meta.get("created_by"))
        if stamp != (RUN_TAG, RUN, CR_CREATED_BY):
            return (
                f"a surface is named {RUN_TAG!r} and carries metadata "
                f"run={RUN!r} and created_by={CR_CREATED_BY!r}"
            )
        model = body.get("data_model")
        entities = model.get("entities") if isinstance(model, dict) else None
        if not isinstance(entities, list) or not entities:
            return "a surface needs a data_model with entities, so its key templates can be checked"
        for entity in entities:
            template = entity.get("redis_key_template") if isinstance(entity, dict) else None
            if not (isinstance(template, str) and template.startswith(CR_PREFIX)):
                return (
                    f"every redis_key_template must start with {CR_PREFIX!r}: "
                    "that is where the service writes"
                )
        source = body.get("data_source")
        config = source.get("connection_config") if isinstance(source, dict) else None
        config = config if isinstance(config, dict) else {}
        small_pool = _int_in(config.get("pool_size"), 1, 2)
        if not (small_pool and _int_in(config.get("min_idle_conns"), 0, 0)):
            return (
                "connection_config needs pool_size 1 or 2 and min_idle_conns 0: "
                "the free tier allows 30 connections"
            )
        return None

    def _cr_own_surface(match, body):
        if match["id"] in ledger_live("cr", "surface"):
            return None
        return "that surface was not created by this run; orphans can be deleted, never written to"

    def _cr_mint_key(match, body):
        if reason := _cr_own_surface(match, body):
            return reason
        expires = _parse_time(body.get("expires_at")) if isinstance(body, dict) else None
        now = datetime.now(timezone.utc)
        # A minute of slack covers the time it takes to build the request.
        if expires is None or not now < expires <= now + timedelta(seconds=CR_KEY_TTL_S + 60):
            return f"an agent key needs an expires_at within {CR_KEY_TTL_S / 3600:g} h from now"
        return None

    def _cr_surface(match, body):
        surface = match["id"]
        ledgered = surface in ledger_live("cr", "surface", pending=True)
        if ledgered or surface in ORPHANS["cr_surface"]:
            return None
        return "that surface is neither ledgered nor a user-confirmed orphan"

    _CACHE = r".*/v1/caches/[^/]+"
    _STORE = r".*/v1/stores/[^/]+"
    _SURFACES = r".*/api/v1/context-surfaces"
    # (component, method, path pattern, check(match, body) -> reason | None). The first
    # route that fits decides; a check of None allows the request as it is.
    _ROUTES = (
        ("lc", "POST", _CACHE + "/entries", _lc_set),
        ("lc", "POST", _CACHE + "/entries/search", None),
        ("lc", "DELETE", _CACHE + "/entries", _lc_by_attributes),
        ("lc", "DELETE", _CACHE + "/entries/(?P<id>[^/]+)", _lc_by_id),
        ("am", "POST", _STORE + "/long-term-memory", _am_create),
        ("am", "POST", _STORE + "/long-term-memory/search", _am_search),
        ("am", "POST", _STORE + "/session-memory/events", _am_event),
        ("am", "DELETE", _STORE + "/long-term-memory", _am_memories),
        ("am", "DELETE", _STORE + "/session-memory/(?P<id>[^/]+)", _am_session),
        ("cr", "POST", _SURFACES, _cr_create),
        ("cr", "POST", _SURFACES + "/(?P<id>[^/]+)/data", _cr_own_surface),
        ("cr", "POST", _SURFACES + "/(?P<id>[^/]+)/agent-keys", _cr_mint_key),
        ("cr", "POST", r".*/mcp", None),
        ("cr", "DELETE", _SURFACES + "/(?P<id>[^/]+)", _cr_surface),
        ("cr", "DELETE", _SURFACES + "/(?P<id>[^/]+)/agent-keys/[^/]+", _cr_surface),
    )

    def _route_refusal(component, method, path, body):
        for comp, verb, pattern, check in _ROUTES:
            if (comp, verb) == (component, method) and (m := re.fullmatch(pattern, path)):
                return check(m, body) if check else None
        return f"{method} {path} is not an operation this notebook performs"

    def http_refusal(component, method, url, body=None, header_names=()):
        """Why this request must not be sent, or None if it may go. See the notes above.

        url is the address as httpx will send it, query string included.
        """
        method = method.upper()
        if method not in HTTP_VERBS:
            return f"{method} is not a verb this notebook sends"
        # Judge the path httpx will send, not the one typed: URL.path is percent-decoded and
        # has its dot segments resolved, so /flush/. and /%66lush both read as /flush. A
        # ;parameter on a segment is ignored by many servers, so it is ignored here too.
        target = httpx.URL(url)
        if "flush" in {segment.partition(";")[0].lower() for segment in target.path.split("/")}:
            return "flush would erase a service other apps share"
        if any(name.lower() == "includeall" for name in target.params):
            return "includeAll would reach every owner's data in a shared store"
        path = target.path.rstrip("/")
        if method != "GET" and (reason := _route_refusal(component, method, path, body)):
            return reason
        for rule in HTTP_RULES.values():
            if reason := rule(component, method, url, body, header_names):
                return reason
        return None

    def _parse_body(response):
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def _problem(body):
        """One line describing an error reply. RFC 7807 fields (type, detail) stay verbatim."""
        if isinstance(body, dict):
            if isinstance(body.get("error"), dict):  # JSON-RPC style
                body = body["error"]
            fields = ("type", "title", "detail", "message", "code")
            parts = [str(body[k]) for k in fields if body.get(k)]
            if parts:
                return " | ".join(parts)
        return _summary(body)

    def _dispatch(component, method, url, headers, body, params, timeout):
        """Vet and send one request. Returns (response, failure, address).

        Exactly one of response and failure is set; failure is (kind, detail). address is
        the URL as it is sent, scrubbed for the trace. The request is built first, so what
        is vetted is what goes out, params merged into the query and all.
        """
        try:
            request = HTTP_CLIENT.build_request(
                method, url, headers=headers, json=body, params=params, timeout=timeout
            )
        except httpx.InvalidURL:
            # The parser's message quotes the text it choked on, which could be a credential
            # pasted into a host field, so none of it is kept.
            failure = ("config", "the URL is not valid; check the host and port settings")
            return None, failure, "<invalid URL>"
        address = str(request.url)
        shown = scrub(address)
        if reason := http_refusal(component, method, address, body, frozenset(request.headers)):
            return None, ("refused", reason), shown
        try:
            return HTTP_CLIENT.send(request), None, shown
        except httpx.TimeoutException:
            return None, ("timeout", f"no reply within {timeout:g} s"), shown
        except Exception as exc:  # DNS, TLS, reset ... anything must surface as ServiceError
            return None, ("network", f"{type(exc).__name__}: {exc}"), shown

    def http_call(component, method, url, *, headers=None, json=None, params=None,
                  timeout=T_READ, expect=(200, 201, 204)):  # fmt: skip
        """Send one request to a hosted Iris service; return (status, body).

        body is parsed JSON, else text, else None for an empty reply. Its credential fields,
        such as the key in a create-agent-key reply, come back wrapped in a Secret: the real
        value is there for a caller that needs it (`.reveal()`), and marimo's Variables panel
        cannot show it. The trace and the UI copies are redacted. The request is built and
        sent in two steps because httpx's delete() cannot carry a body.

        Every failure raises ServiceError with a scrubbed message. A 424 keeps the
        service's `type` and `detail` so it is never mistaken for "0 results".
        """
        method = method.upper()
        if component not in HTTP_COMPONENTS:
            raise ValueError(f"unknown component {component!r}; use one of {HTTP_COMPONENTS}")
        _learn_secrets(headers)
        _learn_secrets(json)
        request = {"headers": redact(headers), "json": redact(json)}
        request = {k: v for k, v in request.items() if v}

        started = time.perf_counter()
        response, failure, address = _dispatch(
            component, method, url, headers, json, params, timeout
        )
        ms = (time.perf_counter() - started) * 1000

        body = _parse_body(response) if response is not None else None
        # A failure's text can echo the request (httpx puts the URL in its messages), so it
        # is redacted like any body before it is stored.
        HTTP_TRACE.append(
            HttpCall(
                component, method, address, request,
                response.status_code if response is not None else 0, ms,
                _clip(redact(failure[1] if failure else body)),
            )
        )  # fmt: skip
        # Raise outside the except blocks so no exception chain can carry a secret.
        if failure:
            raise ServiceError(component, None, *failure)
        _learn_secrets(body)
        if response.status_code not in expect:
            raise ServiceError(component, response.status_code, "http", _problem(body))
        return response.status_code, _seal(body)

    # ---- HTTP: wire view ------------------------------------------------------------
    def _curl_placeholder(call, name):
        if name.lower() == "authorization":
            var = {"lc": "LANGCACHE_API_KEY", "am": "MEMORY_API_KEY"}.get(call.component, "API_KEY")
            return f"Bearer ${var}"
        # Context Retriever: admin calls use the admin key, MCP calls use an agent key.
        is_mcp = urlsplit(call.url).path.rstrip("/").endswith("/mcp")
        return "$CTX_AGENT_KEY" if is_mcp else "$CTX_ADMIN_KEY"

    def as_curl(call):
        """A copy-pasteable curl for a traced call. Secrets appear as $ENV_VAR placeholders."""
        lines = [f"curl -X {call.method} '{call.url}'"]
        for name, value in call.request.get("headers", {}).items():
            shown = _curl_placeholder(call, name) if value == "****" else value
            lines.append(f'-H "{name}: {shown}"')
        if "json" in call.request:
            body = json.dumps(call.request["json"], separators=(",", ":"), ensure_ascii=False)
            lines.append("-d '" + body.replace("'", "'\\''") + "'")
        return " \\\n  ".join(lines)

    def _wire_text(item):
        if isinstance(item, HttpCall):
            lines = [f"{item.method} {item.url}"]
            lines += [f"{k}: {v}" for k, v in item.request.get("headers", {}).items()]
            if "json" in item.request:
                lines += ["", json.dumps(item.request["json"], indent=2, ensure_ascii=False)]
            reply = (
                item.response
                if isinstance(item.response, str)
                else json.dumps(item.response, indent=2, ensure_ascii=False, default=str)
            )
            lines += ["", f"-> {item.status or 'not sent'} in {item.ms:.0f} ms", reply]
            return "\n".join(lines + ["", as_curl(item)])
        if isinstance(item, str):
            return item
        return json.dumps(redact(item), indent=2, ensure_ascii=False, default=str)

    def wire_view(title, payload):
        """A collapsed accordion showing exactly what went over the wire.

        payload: an HttpCall, a redis-cli line (str), any JSON-able object, or a list of those.
        """
        items = payload if isinstance(payload, list) else [payload]
        text = scrub("\n\n".join(_wire_text(item) for item in items))
        return mo.accordion({title: mo.md(code_block(text))})

    def plain_table(rows):
        """A table to read, not to work with: no selection, paging, download or column charts.

        marimo puts a band of charts above the header of a table with 11 or more rows, which
        is clutter in a column this narrow, so every table here is made by this.
        """
        return mo.ui.table(
            rows, selection=None, pagination=False, show_download=False, show_column_summaries=False
        )

    def code_block(text, lang=""):
        """Text in a fenced Markdown block, so Markdown cannot change a character of it.

        A fence inside the text is turned into quotes, or it would close the block early.
        """
        return f"```{lang}\n" + str(text).replace("```", "'''") + "\n```"

    def md_table(rows, code=()):
        """A short table of text as Markdown, so its cells wrap in a narrow column.

        plain_table keeps marimo's toolbar and clips a long cell, which is fine for numbers and
        wrong for a sentence in each cell. The columns named in `code` are set in code type.
        """
        head = list(rows[0])

        def cell(name, value):
            text = str(value).replace("|", "/").replace("`", "'")
            return f"`{text}`" if text and name in code else text

        lines = ["| " + " | ".join(head) + " |", "|" + ":---|" * len(head)]
        lines += ["| " + " | ".join(cell(name, row[name]) for name in head) + " |" for row in rows]
        return mo.md("\n".join(lines))

    def with_spinner(service, fn):
        """Run fn() under a spinner naming the service, so a slow call never looks like a hang."""
        with mo.status.spinner(
            title=f"Calling {service}...",
            subtitle=f"reads time out after {T_READ:g} s, writes after {T_WRITE:g} s",
        ):
            return fn()

    # ---- Redis --------------------------------------------------------------------
    REDIS_READS = frozenset(
        {
            "PING", "INFO", "DBSIZE", "SCAN", "TYPE", "TTL", "EXISTS", "HGETALL", "HGET",
            "HMGET", "HSTRLEN", "MEMORY", "OBJECT", "JSON.GET", "FT._LIST", "FT.INFO",
            "FT.SEARCH", "FT.AGGREGATE", "FT.HYBRID", "FT.PROFILE", "FT.EXPLAINCLI",
        }
    )  # fmt: skip
    REDIS_WRITES = frozenset(
        {"HSET", "EXPIRE", "UNLINK", "DEL", "JSON.SET", "FT.CREATE", "FT.DROPINDEX"}
    )
    WRITE_RATE = 20  # writes per second: the free tier allows 100 ops/s, shared with anything else
    REDIS_TRACE = deque(maxlen=300)

    class RedisCall(NamedTuple):
        cli: str
        ms: float
        reply: str
        status: str  # "ok" | "denied" | "error"

    _last_write = [0.0]

    def pace():
        """Sleep just long enough to hold writes to WRITE_RATE per second."""
        wait = _last_write[0] + 1 / WRITE_RATE - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_write[0] = time.monotonic()

    def _txt(value):
        """Bytes to str. The client is binary-safe because vectors are bytes."""
        return value.decode("utf-8", "replace") if isinstance(value, (bytes, bytearray)) else value

    def decode_reply(value):
        """A reply with every bytes value decoded to str, however deeply it is nested.

        Public because every column that reads Redis (FT.SEARCH, FT.INFO, JSON.GET ...)
        parses RESP2 replies, which are lists of bytes.
        """
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8", "replace")
        if isinstance(value, (list, tuple)):
            return [decode_reply(item) for item in value]
        if isinstance(value, dict):
            return {decode_reply(k): decode_reply(v) for k, v in value.items()}
        return value

    def reply_map(value):
        """A RESP2 flat [k, v, k, v ...] list, or a mapping, as a dict; {} if it is neither.

        Total: a list whose even-numbered items are not all text (a count followed by rows,
        say) is not a map, so it gives {} rather than an unhashable-key TypeError.
        """
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and len(value) % 2 == 0:
            names = value[::2]
            if all(isinstance(name, (str, bytes)) for name in names):
                return dict(zip(names, value[1::2], strict=True))
        return {}

    def _index_of(info):
        """(name, prefixes) from an FT.INFO reply, a RESP2 flat list of bytes or a dict.

        Anything unreadable gives ("", []), so every caller fails closed.
        """
        info = reply_map(decode_reply(info))
        definition = reply_map(info.get("index_definition"))
        prefixes = definition.get("prefixes")
        prefixes = [prefixes] if isinstance(prefixes, str) else prefixes
        name = info.get("index_name")
        return (
            name if isinstance(name, str) else "",
            prefixes if isinstance(prefixes, list) else [],
        )

    def owned_index(info, sub_prefix):
        """True only if the FT.INFO reply shows an index built entirely under sub_prefix.

        Fails closed: the prefix list must exist and be non-empty, and every prefix must
        start with sub_prefix; anything unparseable is False. sub_prefix must itself sit in
        our namespace, so a caller bug can never make the demo's memory:<store>:ltm index
        droppable. It only judges. Letting the guard drop an index outside PREFIX takes a
        grant: claim_index for this run's, approve_orphan_index for another run's.
        """
        if not (isinstance(sub_prefix, str) and sub_prefix.startswith("irisnb_")):
            return False
        _, prefixes = _index_of(info)
        return bool(prefixes) and all(
            isinstance(p, str) and p.startswith(sub_prefix) for p in prefixes
        )

    # FT.CREATE options that take a string, which a crafted command could spell SCHEMA.
    _FT_STRING_OPTIONS = frozenset({"FILTER", "LANGUAGE_FIELD", "SCORE_FIELD", "PAYLOAD_FIELD"})

    def _ft_create_prefixes(argv):
        """The prefixes in FT.CREATE's PREFIX clause, or None if it is missing or malformed.

        The clause is looked for up to the first SCHEMA. A SCHEMA that is really an option's
        value (LANGUAGE_FIELD SCHEMA, STOPWORDS 1 SCHEMA) would end that search early and
        hide a second PREFIX behind it, so such a command counts as malformed.
        """
        head = []
        for arg in argv[2:]:
            token = str(_txt(arg))
            if token.upper() == "SCHEMA":
                break
            head.append(token)
        upper = [t.upper() for t in head]
        if upper[-1:] and upper[-1] in _FT_STRING_OPTIONS:
            return None
        if "STOPWORDS" in upper:
            i = upper.index("STOPWORDS")
            try:
                if len(head) < i + 2 + int(head[i + 1]):
                    return None  # the list runs into SCHEMA: it is one of the stop words
            except (IndexError, ValueError):
                return None
        if upper.count("PREFIX") != 1:
            return None
        i = upper.index("PREFIX")
        try:
            count = int(head[i + 1])
        except (IndexError, ValueError):
            return None
        prefixes = head[i + 2 : i + 2 + count]
        return prefixes if count >= 1 and len(prefixes) == count else None

    def _authorise(argv):
        name = _txt(argv[0]).upper()
        if name in REDIS_READS:
            return  # reads may touch any key, including keys that are not ours
        if name not in REDIS_WRITES:
            raise Denied(f"{name} is not on this notebook's command allowlist")
        targets = [str(_txt(a)) for a in (argv[1:] if name in ("DEL", "UNLINK") else argv[1:2])]
        if not targets:
            raise Denied(f"{name} names no key")
        for target in targets:
            if target.startswith(PREFIX):
                continue
            if name == "FT.DROPINDEX" and (target in OWNED_INDEXES or target in ORPHANS["index"]):
                continue
            raise Denied(f"{name} target {target!r} is outside {PREFIX}*")
        if name == "FT.DROPINDEX":
            if any(str(_txt(a)).upper() == "DD" for a in argv[2:]):
                raise Denied("FT.DROPINDEX ... DD would delete the documents; it is never allowed")
            ORPHANS["index"].discard(targets[0])  # a confirmed orphan is good for one drop
        if name == "FT.CREATE":
            prefixes = _ft_create_prefixes(argv)
            if prefixes is None:
                raise Denied("FT.CREATE needs a PREFIX clause, or the index would cover every key")
            for prefix in prefixes:
                if not prefix or not prefix.startswith(PREFIX):
                    raise Denied(f"FT.CREATE PREFIX {prefix!r} is not under {PREFIX}")

    def redis_cli(argv):
        """One command as copy-pasteable redis-cli syntax, with vectors and long text elided."""
        out = []
        for arg in argv:
            if isinstance(arg, (bytes, bytearray)) and len(arg) > 64:
                out.append(f"<{len(arg)} bytes>")
                continue
            # Scrubbed before it is cut short, or the cut could split a secret in half.
            text = scrub(str(_txt(arg)))
            if len(text) > 120:
                text = text[:60] + f"...<{len(text)} chars>"
            quoted = not text or " " in text or '"' in text
            out.append('"' + text.replace('"', '\\"') + '"' if quoted else text)
        return " ".join(out)

    def _plain(value):
        """A reply with every string decoded and scrubbed, and long blobs elided.

        This runs on each leaf, before anything is cut short: reprlib and the length limit
        can split a secret in the middle, and repr() re-spells one that holds both kinds of
        quote, so scrubbing the finished text can miss it.
        """
        if isinstance(value, (bytes, bytearray)):
            if len(value) > 64:
                return f"<{len(value)} bytes>"
            value = value.decode("utf-8", "replace")
        if isinstance(value, str):
            return scrub(value)
        if isinstance(value, (list, tuple)):
            return [_plain(item) for item in value]
        if isinstance(value, dict):
            # Keys stay keys: only text is scrubbed, so an odd key type cannot break the trace.
            text_keys = (str, bytes, bytearray)
            return {
                (_plain(k) if isinstance(k, text_keys) else k): _plain(v) for k, v in value.items()
            }
        return value

    def _reply_text(reply, limit=70):
        # reprlib cuts long lists and strings as it goes, so a 100-hit FT.SEARCH reply full
        # of vectors is never rendered whole.
        text = reprlib.repr(_plain(reply))
        return text if len(text) <= limit else text[: limit - 3] + "..."

    def _deny_pipelines(*args, **kwargs):
        raise Denied("pipelines bypass the guard and the trace; issue single commands")

    class GuardedTracedRedis(redis.Redis):
        """A redis client that refuses out-of-scope commands, then times and records the rest.

        ACL SETUSER is unsupported on Redis Cloud, so scoping happens client-side. Two
        things a naive tracer gets wrong: a refusal must be recorded too, and a raising
        command must be recorded as an error rather than as a plausible latency.
        """

        def execute_command(self, *args, **options):
            def record(started, reply, status):
                ms = (time.perf_counter() - started) * 1000
                REDIS_TRACE.append(RedisCall(redis_cli(args), ms, reply, status))

            started = time.perf_counter()
            try:
                _authorise(args)
            except Denied as exc:  # shows ~0 ms: it never left this process
                record(started, str(exc), "denied")
                raise
            if _txt(args[0]).upper() in REDIS_WRITES:
                pace()
            started = time.perf_counter()
            try:
                reply = super().execute_command(*args, **options)
            except Exception as exc:
                record(started, scrub(f"{type(exc).__name__}: {exc}"), "error")
                raise
            record(started, _reply_text(reply), "ok")
            if _txt(args[0]).upper() == "FT.CREATE":
                self._confirm_index(str(_txt(args[1])))
            return reply

        def _confirm_index(self, index):
            """Ask the server what FT.CREATE built, and undo it if it reaches beyond PREFIX.

            _authorise reads the command, but the server has the last word on how it parses
            (an option value spelt SCHEMA, say), so the index itself is checked as well.
            """
            try:
                built = self.execute_command("FT.INFO", index)
            except redis.RedisError:
                built = None
            if owned_index(built, PREFIX):
                return
            self.execute_command("FT.DROPINDEX", index)  # no DD: the documents stay
            raise Denied(f"FT.CREATE built an index that reaches beyond {PREFIX}*; it was dropped")

        # Pipelines, pub/sub and MONITOR open their own connections and call their own
        # execute_command, so they would bypass everything above. transaction() opens a
        # pipeline, so it is denied along with them.
        pipeline = _deny_pipelines

        def pubsub(self, *args, **kwargs):
            raise Denied("pub/sub is outside this notebook's scope")

        def monitor(self, *args, **kwargs):
            raise Denied("MONITOR would show other apps' commands")

        # redis-py's module clients build pipelines of their own straight from the
        # connection pool, never calling Redis.pipeline, so each one is denied as well.
        # ft().batch_indexer() is denied by name: left to fail inside BatchIndexer.__init__
        # it would raise a second time from the half-built object's __del__.
        def ft(self, *args, **kwargs):
            index = super().ft(*args, **kwargs)
            index.pipeline = index.batch_indexer = _deny_pipelines
            return index

        def json(self, *args, **kwargs):
            module = super().json(*args, **kwargs)
            module.pipeline = _deny_pipelines
            return module

        def ts(self, *args, **kwargs):
            module = super().ts(*args, **kwargs)
            module.pipeline = _deny_pipelines
            return module

    def connect_redis(url):
        """A guarded, traced, RESP2 client for url.

        redis-py 8 defaults to RESP3, where FT.* replies are maps instead of the flat
        lists every parser here expects, and decode_responses stays off because vector
        fields are raw FLOAT32 bytes. Errors are scrubbed and never chained, since redis-py
        exceptions can echo the URL.
        """
        if not url:
            raise ServiceError("rs", None, "config", "no Redis URL: set REDIS_URL or REDIS_HOST")
        register_url_password(url)
        try:
            client = GuardedTracedRedis.from_url(
                url, protocol=2, decode_responses=False,
                socket_timeout=T_READ, socket_connect_timeout=T_READ,
            )  # fmt: skip
            client.ping()
            if client.connection_pool.connection_kwargs.get("protocol") != 2:
                raise ValueError("the URL forced a protocol other than RESP2")
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
        else:
            return client
        raise ServiceError("rs", None, "connect", failure)

    def scan_keys(client, match, count=1000):
        """Yield keys matching `match` as str. COUNT 1000 keeps the ops/s cost of a sweep low."""
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=match, count=count)
            yield from (_txt(key) for key in keys)
            if int(cursor) == 0:
                return

    def is_unknown_index(exc):
        """True if a ResponseError says the index does not exist.

        Redis words it two ways: "Unknown index name" (Search before 8.6) and, from Redis 8.6.2,
        "SEARCH_INDEX_NOT_FOUND Index not found: <name>". Either is "already gone", not a failure.
        """
        pattern = r"unknown.*index|no such index|index[_ ]not[_ ]found"
        return re.search(pattern, str(exc), re.I) is not None

    def redis_cli_since(mark):
        """The commands traced after `mark` (an entry of REDIS_TRACE, or None), oldest first.

        They are redis-cli lines, for a wire view.
        """
        trace = list(REDIS_TRACE)
        at = next((i for i, call in enumerate(trace) if call is mark), -1)
        return [call.cli for call in trace[at + 1 :]]

    def render_redis_trace(n=12):
        """The last n Redis commands, newest first."""
        # Scrubbed again here, so a secret registered after a row was stored is still masked.
        rows = [
            {
                "command": scrub(c.cli), "ms": round(c.ms, 1), "status": c.status,
                "reply": scrub(c.reply),
            }
            for c in list(REDIS_TRACE)[-n:][::-1]
        ]  # fmt: skip
        if not rows:
            return mo.md("_no commands yet_")
        return mo.vstack(
            [
                plain_table(rows),
                mo.md(
                    "<small>Latency is one incidental column, not the point. The first row of a "
                    "session includes connect and AUTH. A `denied` row shows about 0 ms because "
                    "the guard stopped it before it left this process.</small>"
                ),
            ]
        )

    def redis_facts(client):
        """Read-only facts about a database, for the Test connection of a column that uses one.

        "notebook_keys" are the keys that start `irisnb_` (this notebook's, from any run) and
        "shared" is true for the default user on a database that also holds anything else: keys
        that are not the notebook's, or an index it did not name. Raises redis.ResponseError
        when the database has no Search (FT._LIST fails).
        """
        server = client.execute_command("INFO", "server")
        version = tuple(int(n) for n in re.findall(r"\d+", str(server.get("redis_version")))[:3])
        indexes = decode_reply(client.execute_command("FT._LIST"))
        notebook_keys = list(scan_keys(client, "irisnb_*"))
        dbsize = client.execute_command("DBSIZE")
        connection = client.connection_pool.connection_kwargs
        return {
            "where": f"{connection.get('host', '?')}:{connection.get('port', '?')}",
            "version": ".".join(map(str, version)) or "unknown",
            "version_tuple": version,
            "indexes": indexes,
            "notebook_keys": notebook_keys,
            "dbsize": dbsize,
            "clients": client.execute_command("INFO", "clients").get("connected_clients"),
            # The default user on a database that also holds other people's data.
            "shared": connection.get("username") in (None, "", "default")
            and (dbsize > len(notebook_keys) or any(not n.startswith("irisnb_") for n in indexes)),
        }  # fmt: skip

    # ---- grants -------------------------------------------------------------------
    # Beyond what this run's ledger shows, the choke points accept the ids and index names
    # held here. Only the helpers below write to them, and each checks what the thing
    # really is first, so merely listing an object never lets the notebook touch it and a
    # column that misreads a field cannot approve a stranger's data by accident.
    OWNED_INDEXES = set()  # this run's indexes whose names are not under PREFIX
    APPROVED = {"lc_entry": set(), "am_memory": set()}  # this run's, though not in its ledger
    ORPHANS = {  # left behind by other runs; granted only after a typed confirmation
        "index": set(), "am_memory": set(), "am_session": set(), "cr_surface": set(),
    }  # fmt: skip
    ORPHAN_CONFIRM = "delete orphans"  # what the user types to confirm
    ORPHAN_AGE_H = 2  # memories and surfaces must be older: a young one may be a live colleague's

    def _confirmed(confirmation):
        if str(confirmation).strip() != ORPHAN_CONFIRM:
            raise ValueError(f"type {ORPHAN_CONFIRM!r} to confirm")

    def age_hours(stamp):
        """Hours since an RFC 3339 timestamp; None if it is not one."""
        when = _parse_time(stamp)
        return None if when is None else (datetime.now(timezone.utc) - when).total_seconds() / 3600

    def is_stale(stamp):
        """True if an RFC 3339 timestamp is over ORPHAN_AGE_H hours old; False if unreadable."""
        age = age_hours(stamp)
        return age is not None and age > ORPHAN_AGE_H

    def claim_index(info):
        """Let the guard drop an index of this run's that sits outside PREFIX; True if it may.

        Context Retriever's service names its own indexes. info is the index's FT.INFO reply,
        and the claim holds only if every prefix it covers is under RS_PREFIX or CR_PREFIX.
        """
        name, _ = _index_of(info)
        if name and (owned_index(info, RS_PREFIX) or owned_index(info, CR_PREFIX)):
            OWNED_INDEXES.add(name)
            return True
        return False

    def approve_orphan_index(info, confirmation):
        """Let the guard drop, once, an index that another run of this notebook left behind."""
        _confirmed(confirmation)
        name, prefixes = _index_of(info)
        first = prefixes[0] if prefixes and isinstance(prefixes[0], str) else ""
        run = re.match(r"irisnb_[0-9a-f]{8}_", first)
        if not (name and run and run.group() != PREFIX and owned_index(info, run.group())):
            raise ValueError("that is not an index another run of this notebook built")
        ORPHANS["index"].add(name)

    def approve_am_memories(items):
        """Let the choke point delete memories that are this run's but lack its id prefix.

        Extraction gives its memories ids of its own. items are memories as listed by the
        service, and each must name AM_OWNER as its ownerId; one that does not aborts the lot.
        """
        ids = []
        for item in items:
            mine = isinstance(item, dict) and item.get("ownerId") == AM_OWNER
            if not (mine and isinstance(item.get("id"), str) and item["id"]):
                raise ValueError(f"every memory needs an id and ownerId {AM_OWNER!r}")
            ids.append(item["id"])
        APPROVED["am_memory"].update(ids)

    def approve_am_orphans(items, confirmation):
        """Let the choke point delete other runs' memories.

        Each needs an ib-<run> owner that is not this run's, and must be older than
        ORPHAN_AGE_H.
        """
        _confirmed(confirmation)
        ids = []
        for item in items:
            item = item if isinstance(item, dict) else {}
            owner = item.get("ownerId")
            other_run = isinstance(owner, str) and re.fullmatch(r"ib-[0-9a-f]{8}", owner)
            if not (
                other_run
                and owner != AM_OWNER
                and is_stale(item.get("createdAt"))
                and isinstance(item.get("id"), str)
            ):
                raise ValueError(
                    "every orphan needs an id, an old createdAt and an ib-<run> owner "
                    "that is not this run's"
                )
            ids.append(item["id"])
        ORPHANS["am_memory"].update(ids)

    def approve_am_session_orphans(session_ids, confirmation):
        """Let the choke point delete other runs' sessions, named ib-<run>-s<n>."""
        _confirmed(confirmation)
        session_ids = list(session_ids)
        for ident in session_ids:
            named = isinstance(ident, str) and re.fullmatch(r"ib-([0-9a-f]{8})-s\d+", ident)
            if not (named and named[1] != RUN):
                raise ValueError("an orphan session is named ib-<run>-s<n> for another run")
        ORPHANS["am_session"].update(session_ids)

    def approve_cr_orphan(surface, confirmation):
        """Let the choke point delete a surface another run created, as listed by the service.

        It must be named iris-basics-<run>, carry that run and CR_CREATED_BY in its metadata,
        and be older than ORPHAN_AGE_H. A surface without that stamp is somebody else's.
        """
        _confirmed(confirmation)
        surface = surface if isinstance(surface, dict) else {}
        meta = surface.get("metadata") if isinstance(surface.get("metadata"), dict) else {}
        run = meta.get("run")
        other_run = isinstance(run, str) and re.fullmatch(r"[0-9a-f]{8}", run) and run != RUN
        if not (
            other_run
            and meta.get("created_by") == CR_CREATED_BY
            and surface.get("name") == f"iris-basics-{run}"
            and is_stale(surface.get("created_at"))
            and isinstance(surface.get("id"), str)
        ):
            raise ValueError("that is not a stale surface another run of this notebook created")
        ORPHANS["cr_surface"].add(surface["id"])

    def approve_lc_entry(entry_id):
        """Let the choke point delete one cache entry that the ledger does not know.

        That is verify.py's TTL sentinel, which this same process set a moment before.
        """
        if not (isinstance(entry_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", entry_id)):
            raise ValueError("a cache entry id is one word of letters, digits, - or _")
        APPROVED["lc_entry"].add(entry_id)

    # ---- ledger -------------------------------------------------------------------
    # An append-only file per RUN that survives kernel restarts. It is how cleanup, the
    # choke points and verify.py know what this run created. It never holds secrets.
    #
    #   column  kind         ident
    #   rs      key, index   Redis key / index name
    #   lc      entry        entry id
    #   cr      surface      surface id; agent_key id; key, index: Redis key / index name
    #   am      session, memory   ids we choose (ib-<RUN>-s1, ib-<RUN>-m1)
    #
    # state: "intent" is written BEFORE a create, so a create that timed out can still be
    # found (by the name we chose); "created" after it succeeds; "deleted" once removed.
    #
    # A kind outside this table is refused rather than stored: a typo would otherwise make
    # the item undeletable, and Agent Memory has no server-side expiry to fall back on.
    LEDGER_KINDS = {
        "rs": {"key", "index"},
        "lc": {"entry"},
        "cr": {"surface", "agent_key", "key", "index"},
        "am": {"session", "memory"},
    }
    LEDGER_STATES = ("intent", "created", "deleted")

    def _check_kind(column, kind):
        if kind not in LEDGER_KINDS.get(column, ()):
            raise ValueError(f"bad ledger kind: {column!r} has no {kind!r}")

    def ledger_dir():
        if os.environ.get("IRIS_BASICS_LEDGER_DIR"):
            return Path(os.environ["IRIS_BASICS_LEDGER_DIR"])
        if os.environ.get("LOCALAPPDATA"):
            return Path(os.environ["LOCALAPPDATA"]) / "iris-basics"
        return Path.home() / ".iris-basics"

    def ledger_add(column, kind, ident, state="created"):
        """Append one line to this run's ledger."""
        _check_kind(column, kind)
        if state not in LEDGER_STATES:
            raise ValueError(f"bad ledger state: {state!r}")
        ident = str(ident)
        if scrub(ident) != ident:
            raise ValueError("refusing to write a secret into the ledger")
        entry = {
            "t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "column": column, "kind": kind, "ident": ident, "state": state,
        }  # fmt: skip
        path = ledger_dir() / f"{RUN}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry) + "\n"
        with path.open("a+b") as fh:
            # A crash can cut the last line short. Start a fresh line after it, or the torn
            # text and this entry fuse into one unreadable line and the entry is lost.
            if fh.seek(0, os.SEEK_END):
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    line = "\n" + line
            fh.write(line.encode("utf-8"))

    def ledger_state(column, kind, run=None):
        """{ident: last state} for one column and kind, in first-seen order."""
        _check_kind(column, kind)
        path = ledger_dir() / f"{run or RUN}.jsonl"
        latest = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue  # a line cut short by a crash
                if isinstance(e, dict) and e.get("column") == column and e.get("kind") == kind:
                    latest[str(e.get("ident"))] = e.get("state")
        return latest

    def ledger_live(column, kind, *, run=None, pending=False):
        """Ids created and not yet deleted. pending=True also includes ones only marked intent."""
        wanted = ("created", "intent") if pending else ("created",)
        return [i for i, s in ledger_state(column, kind, run).items() if s in wanted]

    def ledger_runs():
        """RUN ids that have a ledger file, oldest first."""
        files = [p for p in ledger_dir().glob("*.jsonl") if re.fullmatch(r"[0-9a-f]{8}", p.stem)]
        return [p.stem for p in sorted(files, key=lambda p: p.stat().st_mtime)]

    # ---- UI helpers -----------------------------------------------------------------
    _CALLOUT_KINDS = {"ok": "success", "info": "info", "warn": "warn", "error": "danger"}

    def status_callout(kind, message):
        """A callout. kind: ok | info | warn | error. message is markdown, or an exception."""
        if isinstance(message, BaseException):
            message = f"{type(message).__name__}: {message}"
        return mo.callout(mo.md(scrub(message)), kind=_CALLOUT_KINDS[kind])

    # Advice for a failed call, by HTTP status, that holds for every hosted service. A column
    # says more about a status it can (service_failed's hints), and this is what is left.
    HTTP_HINTS = {
        403: "This key may not do that.",
        424: "**The service could not do the work (424 Failed Dependency).** This is a failure, "
        'not "0 results". The database behind it may be full or unreachable, or a model key '
        "or quota is at fault.",
        429: "Too many requests. Wait a minute and try again.",
    }

    def service_failed(exc, hints=None):
        """The callout for a call that failed, saying what to do about it.

        hints maps a status to a column's own advice and overrides HTTP_HINTS. A 424 keeps the
        service's own `type` and detail: it means the service could not do the work, which is
        not the same as having found nothing. The type is an open string, so it is printed as it
        came. Anything else, or a status with no advice, shows the error itself.
        """
        if isinstance(exc, ServiceError) and exc.status:
            hint = {**HTTP_HINTS, **(hints or {})}.get(exc.status)
            if hint:
                return status_callout("error", f"{hint}\n\n`{exc.detail}`")
        return status_callout("error", exc)

    def shared_db_warning(facts, consequence):
        """A warning callout if redis_facts found other data on the database, else an empty note.

        consequence says what the column does about it, in a sentence or two.
        """
        if not facts["shared"]:
            return mo.md("")
        others = facts["dbsize"] - len(facts["notebook_keys"])
        foreign = sum(not name.startswith("irisnb_") for name in facts["indexes"])
        return status_callout(
            "warn",
            f"You are the `default` user on a database that also holds **{others}** keys and "
            f"**{foreign}** indexes that are not this notebook's. {consequence}",
        )

    def not_configured(component, missing, *, offline=""):
        """The one message an unconfigured column shows. missing: setting names, never values.

        offline says what a column still offers without them; most offer nothing.
        """
        names = ", ".join(f"`{name}`" for name in missing)
        # `marimo run` has no cell to re-run: a new .env is read when the notebook starts.
        after = (
            "then restart the notebook" if mo.app_meta().mode == "run" else "then re-run this cell"
        )
        return status_callout(
            "warn",
            f"**{component} is not configured.** Missing: {names}. Fill in the fields above, or "
            "put them in `.env` next to this notebook (see `.env.example`) or in the file "
            f"`IRIS_BASICS_ENV_FILE` points at, {after}. {offline}".rstrip(),
        )

    def notes(say, expect):
        """A one-line Say / Expect talk track for the presenter.

        It is always in the page, marked `ib-note`, and one cell next to the intro shows or
        hides every one with the Presenter notes switch. No cell that renders a result may read
        that switch: flipping it re-runs whatever reads it without re-running the cells above,
        and a result that a stopped cell never made is then a NameError.
        """
        line = f"**Say:** {say} &nbsp;·&nbsp; **Expect:** {expect}"
        return mo.Html(f'<div class="ib-note">{mo.callout(mo.md(line), kind="neutral").text}</div>')

    # Component -> {"date": "...", "markdown": "..."}: real output captured from a live run of this
    # notebook and shown when a column has no settings. It is Cairn data only, and every identifier
    # of the account it ran against (cache, store and surface ids, entry and memory ids, hosts, the
    # run id) is a <placeholder>. test_shared fails if a hex id, a UUID or a redis.io host is in it.
    # Built from lists of lines: marimo strips four spaces from every line of this block, a
    # multi-line string included.

    def recorded_call(title, request, reply):
        """One recorded call as Markdown: what it was, then the request and the reply as JSON."""
        return "\n\n".join(
            [
                title,
                code_block(json.dumps(request, indent=2), "json"),
                code_block(json.dumps(reply, indent=2), "json"),
            ]
        )

    RECORDED_LIVE = {
        "lc": {
            "date": "2026-09-30",
            "markdown": "\n\n".join(
                [
                    recorded_call(
                        "**A HIT.** One search is one `POST`: a prompt and a threshold go up, and "
                        "every stored entry at least that similar comes back with its score. "
                        "`POST https://<host>/v1/caches/<cache-id>/entries/search`, 200 in 58 ms:",
                        {
                            "prompt": "How can I get my rain jacket to bead water again?",
                            "similarityThreshold": 0.85,
                            "searchStrategies": ["semantic"],
                            "maxResults": 5,
                        },
                        {
                            "data": [
                                {
                                    "attributes": {},
                                    "id": "<entry-id>",
                                    "prompt": "How do I restore the waterproofing on my 3-layer "
                                    "rain shell?",
                                    "response": "Wash the shell, then apply a spray-on or wash-in "
                                    "treatment. A short tumble in the dryer sets it.",
                                    "searchStrategy": "semantic",
                                    "similarity": 0.8961312,
                                }
                            ]
                        },
                    ),
                    "**Where a threshold bites.** Each question asked once at the floor (0.50), "
                    "then judged at 0.85:",
                    "\n".join(
                        [
                            "| question | kind | closest entry | similarity | at 0.85 |",
                            "|:---|:---|:---|---:|:---|",
                            "| How do I wash my down sleeping bag? "
                            "| same words | bag | 1.000 | hit |",
                            "| What's the right way to launder a goose-down sleeping bag? "
                            "| paraphrase | bag | 0.905 | hit |",
                            "| My rain jacket stopped beading water. How do I fix that? "
                            "| paraphrase | shell | 0.812 | missed paraphrase |",
                            "| How do I wash my synthetic sleeping bag? "
                            "| swapped item | bag | 0.950 | false hit |",
                            "| How do I avoid washing my down sleeping bag? "
                            "| negation | bag | 0.735 | miss |",
                            "| What is the capital of Australia? "
                            "| unrelated | - | under 0.50 | miss |",
                        ]
                    ),
                    "The highest false hit scored 0.950. From 0.96 up there are none, and 1 of the "
                    "6 paraphrases still hit.",
                    "**Cache-first chat.** A question that missed at 0.80 (`How do I fix a snapped "
                    "tent pole?`) was taught, then asked in other words:",
                    code_block(
                        "HIT · similarity 0.894 · matched At Cairn Outfitters: How do I fix a "
                        "snapped tent pole? · entry <entry-id> · 57 ms"
                    ),
                    "The taught entry was deleted right after it was served, so no other app on "
                    "the cache was.",
                    "**The floor.** A search at 0.50 was honoured: the least similar entry that "
                    "came back scored 0.659, under the 0.85 default. Entries of other apps on the "
                    "same cache came back as a score only, and were hidden.",
                ]
            ),
        },
        "cr": {
            "date": "2026-09-30",
            "markdown": "\n\n".join(
                [
                    "**The tools the service made** for the three Cairn entities (Customer, "
                    "Product, Order): 21, as `tools/list` returned them. Every entity gets the "
                    "first five below, Customer and Product have a text field and so also get "
                    "`search_<entity>_by_text`, and four set tools span the entities. `*` marks a "
                    "required argument.",
                    "\n".join(
                        [
                            "| tool | arguments |",
                            "|:---|:---|",
                            "| `get_customer_by_id` | `id`\\* |",
                            "| `list_customer` | `limit`, `offset`, `sort_by`, `sort_order` |",
                            "| `count_customer` | `field`, `function`\\*, `group_by`, `limit`, "
                            "`offset`, `timeout_ms` |",
                            "| `summarize_customer` | `field`\\*, `function`\\*, `group_by`, "
                            "`limit`, `offset`, `timeout_ms` |",
                            "| `filter_customer` | `limit`, `numeric_conditions`, `offset`, "
                            "`sort_by`, `sort_order`, `tag_conditions` |",
                            "| `search_customer_by_text` | `limit`, `match_mode`, `offset`, "
                            "`query`\\*, `sort_by`, `sort_order` |",
                            "| `intersect_results`, `union_results`, `except_results` "
                            "| `limit`, `offset`, `sets`\\* |",
                            "| `expand_results` | `limit`, `offset`, `relationship`\\*, `set`\\* |",
                        ]
                    ),
                    recorded_call(
                        "**One tool call.** An agent calls a tool over JSON-RPC and the service "
                        "reads your Redis data. `POST https://<mcp-host>/mcp`, "
                        "`tools/call get_customer_by_id`, 200 in 224 ms:",
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {"name": "get_customer_by_id", "arguments": {"id": "C001"}},
                        },
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": '{"_retrieval":{"source":"<surface-id>",'
                                        '"retrieved_at":"2026-09-30T09:34:54.655663572Z",'
                                        '"source_version":1},"customer_id":"C001",'
                                        '"lifetime_value":4200,"name":"Ada Lovelace",'
                                        '"region":"emea","tier":"gold"}',
                                    }
                                ]
                            },
                        },
                    ),
                    "**Where the data went.** 20 records (6 Customer, 6 Product, 8 Order) were "
                    "written as JSON under the prefix the notebook chose, with one index per "
                    "entity named `idx:<surface-id>:<hash>:<entity>`. The Customer index covered 6 "
                    "documents: `name TEXT, tier TAG, region TAG, lifetime_value NUMERIC`. "
                    "Deleting the surface dropped all three indexes with it; the keys stay until "
                    "Clean up unlinks them or they expire.",
                    "**A tool says no.** `filter_customer` sent only `limit` and `offset` came "
                    "back as a tool error (`isError`), with the words below. A call outside a "
                    "tool's schema is refused before it reaches Redis, as a JSON-RPC error "
                    "`-32602`.",
                    code_block("Error executing tool: at least one condition is required"),
                ]
            ),
        },
        "am": {
            "date": "2026-09-30",
            "markdown": "\n\n".join(
                [
                    "**Recall by meaning.** The question `What should the kitchen avoid serving "
                    "me?` shares no word with the memory it needs. Searched at "
                    "`similarityThreshold` 0.0 with a limit of 3, the memories come back in rank "
                    "order (`POST https://<host>/v1/stores/<store-id>/long-term-memory/search`):",
                    "\n".join(
                        [
                            "| answer | recalled | memory | topics |",
                            "|:---|:---|:---|:---|",
                            "| ★ | #1 of 3 · 0 words in common | Dana is allergic to peanuts and "
                            "tree nuts, so trail snacks and camp meals must not contain them. "
                            "| food, allergy |",
                            "| | #2 of 3 · 0 words in common "
                            "| Dana wants parcels sent express, with no plastic packaging. "
                            "| shipping, packaging |",
                            "| | #3 of 3 · 0 words in common | Dana prefers gear made from "
                            "recycled materials and will pay extra for it. "
                            "| preferences, sustainability |",
                        ]
                    ),
                    code_block(
                        json.dumps(
                            {
                                "text": "What should the kitchen avoid serving me?",
                                "filter": {
                                    "ownerId": {"eq": "ib-<run>"},
                                    "namespace": {"eq": "iris-basics-notebook"},
                                },
                                "filterOp": "all",
                                "limit": 3,
                                "similarityThreshold": 0.0,
                            },
                            indent=2,
                        ),
                        "json",
                    ),
                    "**What the threshold cuts.** The service returns no score, so the same "
                    "question was asked at 11 thresholds, and a memory's band is the highest one "
                    "that still returned it. At 0.3 only the allergy memory stays; at 0.5 nothing "
                    "came back.",
                    "\n".join(
                        [
                            "| similarity | memory | words in common |",
                            "|:---|:---|---:|",
                            "| 0.3 to 0.4 | Dana is allergic to peanuts and tree nuts, so trail "
                            "snacks and camp meals must not contain them. | 0 |",
                            "| 0.2 to 0.3 | Dana wants parcels sent express, with no plastic "
                            "packaging. | 0 |",
                            "| 0.1 to 0.2 | Dana prefers gear made from recycled materials and "
                            "will pay extra for it. | 0 |",
                            "| 0.0 to 0.1 | Dana has wide feet, so she needs boots half a size "
                            "bigger than her usual size. | 0 |",
                            "| 0.0 to 0.1 | Dana is planning a winter trek across Patagonia in "
                            "February and gets cold easily. | 0 |",
                        ]
                    ),
                    "**Scope.** The same question under three scopes found 5, 0 and 0 memories: "
                    "this run's owner, an owner that does not exist, and a namespace that does "
                    "not exist.",
                    "**Extraction.** The service also writes memories from sessions on its own. "
                    "In this run, at least 11 minutes after the last event (one question in one "
                    "session), the store held no memory that it had made itself, so start "
                    "extraction early if you want to show it.",
                ]
            ),
        },
    }

    def recorded_live(component):
        """The labelled 'Recorded live on <date>' panel for a column with no credentials."""
        capture = RECORDED_LIVE.get(component)
        if not capture:
            return mo.md("")
        note = (
            f"Real output from a run of this notebook on {capture['date']}, not a simulation. "
            "A name such as `<cache-id>` stands in for an identifier of the account it ran on."
        )
        return mo.accordion(
            {f"Recorded live on {capture['date']}": mo.md(note + "\n\n" + capture["markdown"])}
        )

    # ---- column helpers -------------------------------------------------------------
    # What each column's cells call, column by column and named with its prefix (rs_ lc_ cr_
    # am_). They are defined here and not as @app.function items because marimo edit makes a
    # cell of every item: with these as items the notebook had 300 and Run all took 40 s.
    # A function here may use only names this block defines (test_shared checks), and a
    # multi-line string here must be indented four spaces, since marimo strips four from
    # every line of the block.

    # ---- column 0 · Redis Search: helpers ----------------------------------------

    def rs_articles():
        """The Cairn Outfitters help centre: 36 short articles, six in each of six categories.

        Written so every engine has something to win. No article says "cramped" or "shoes", though
        the boot guide answers them; only one says "Trailhead"; nothing says "waterproofng".
        """
        fields = ("id", "title", "body", "category", "year")
        rows = (
            ("ship-times", "How long does delivery take?",
             "Standard orders leave our Denver warehouse within one business day and arrive in "
             "three to five. Express upgrades arrive in two.", "shipping", 2025),
            ("ship-express", "Upgrading to express or overnight delivery",
             "Choose express at checkout for two-day arrival, or overnight for orders placed before "
             "noon Mountain time. Rural addresses may need an extra day.", "shipping", 2024),
            ("ship-international", "Shipping outside the United States",
             "We ship to Canada, the UK and the EU. Duties and import taxes are collected at "
             "checkout so nothing is due when the courier arrives.", "shipping", 2025),
            ("ship-overdue", "Missing and overdue shipments",
             "If a tracking page has not moved for five days, the carrier has probably lost it. "
             "Contact us and we will reship at no charge or refund the order.", "shipping", 2026),
            ("ship-address", "Changing a delivery address",
             "Addresses can be edited until the order is packed. After that the carrier can "
             "redirect it, but only while it is still in the local depot.", "shipping", 2023),
            ("ship-free", "Free shipping threshold",
             "Orders over 75 dollars ship free within the contiguous states. Oversized items such "
             "as tents and kayaks always carry a freight surcharge.", "shipping", 2026),
            ("returns-window", "How long do I have to return something?",
             "Unworn, unwashed gear can go back within 60 days of delivery. Tags must be attached "
             "and the original packaging kept.", "returns", 2025),
            ("returns-refund", "When will the refund reach my card?",
             "Once the warehouse scans your parcel, the credit is released within two days and your "
             "bank usually posts it in five to seven.", "returns", 2024),
            ("returns-exchange", "Exchanging for a different size or color",
             "Start an exchange from your order page and we send the replacement first, so you are "
             "never left without gear before a trip.", "returns", 2025),
            ("returns-label", "Printing a prepaid return label",
             "Open the order, choose Return, and print the label. Drop the box at any carrier "
             "location; there is nothing to pay.", "returns", 2023),
            ("returns-final", "Final sale and clearance items",
             "Items marked final sale cannot be returned, but defective ones are always covered by "
             "the warranty regardless of the sale price.", "returns", 2022),
            ("returns-damaged", "Something arrived damaged",
             "Take a photo of the box and the item and send it to us within 14 days. We will "
             "replace it immediately and arrange a pickup.", "returns", 2026),
            ("care-waterproof", "Restoring waterproofing on shells and boots",
             "When rain stops beading and starts soaking in, wash the garment and apply a spray-on "
             "or wash-in treatment. A short tumble in the dryer sets it.", "care", 2024),
            ("care-down", "Washing a down sleeping bag",
             "Use a front-loading machine with a down-specific soap, then dry on low heat with "
             "clean tennis balls to break up the clumps.", "care", 2023),
            ("care-boots", "Cleaning and drying hiking boots",
             "Brush off the mud, rinse with lukewarm water and dry them at room temperature. Never "
             "put leather next to a heater or campfire.", "care", 2025),
            ("care-tent", "Storing a tent between trips",
             "Pitch it once at home until completely dry, then keep it loosely packed in a cool "
             "cupboard. Damp fabric grows mildew within days.", "care", 2022),
            ("care-stove", "Cleaning a camp stove",
             "Clear the jet with the pricking wire, wipe the burner and check the fuel hose for "
             "cracks before every season.", "care", 2024),
            ("care-pack", "Washing a backpack",
             "Empty every pocket, scrub the mesh with mild soap and a soft brush, and hang the "
             "pack upside down to air dry away from direct sun.", "care", 2026),
            ("size-boots", "Finding your boot size",
             "Boots run a half size small. Measure your foot at the end of the day, wear your "
             "hiking socks, and leave a thumb of room at the toe.", "sizing", 2025),
            ("size-jackets", "Jacket and shell fit guide",
             "Layer a midweight fleece underneath when you try a shell on. Sleeves should cover "
             "your wrists when you reach forward.", "sizing", 2024),
            ("size-packs", "Choosing a pack size by torso length",
             "Volume matters less than fit. Measure from the bone at the base of your neck down to "
             "the hip line, then match it to the frame size chart.", "sizing", 2026),
            ("size-bags", "Picking a sleeping bag length and temperature rating",
             "Long bags fit people over six feet tall. The comfort rating, not the limit rating, "
             "is the temperature you will actually sleep warm at.", "sizing", 2023),
            ("size-kids", "Sizing for children",
             "Kids grow quickly, so buy jackets a size up and pick boots with a removable insole "
             "you can swap for a thinner one later.", "sizing", 2022),
            ("size-gloves", "Glove and mitten sizing",
             "Measure around your palm below the knuckles. Mittens are warmer than gloves because "
             "your fingers share heat.", "sizing", 2025),
            ("order-cancel", "Canceling an order",
             "Orders can be canceled from your account until they are packed, which is usually "
             "within two hours of checkout.", "orders", 2025),
            ("order-change", "Adding an item to an order I already placed",
             "We cannot edit a paid order, but you can place a second one and we will combine the "
             "two into a single box while both are still unpacked.", "orders", 2024),
            ("order-status", "Checking the status of an order",
             "Sign in and open Orders to see whether yours is processing, packed or shipped. A "
             "tracking number appears the moment it ships.", "orders", 2023),
            ("order-gift", "Sending a gift with a message",
             "Tick the gift box at checkout to hide prices on the packing slip and add a note of "
             "up to 200 characters.", "orders", 2026),
            ("order-promo", "Using a promo code",
             "Enter the code in the box above the total. Only one code applies per order, and "
             "codes cannot be added once the order is placed.", "orders", 2022),
            ("order-backorder", "Backordered and out of stock items",
             "A backordered item ships as soon as it lands in our warehouse. Choose split shipping "
             "if you would rather have the rest first.", "orders", 2025),
            ("warranty-lifetime", "Lifetime warranty on Ridgeline packs",
             "Every Ridgeline pack is guaranteed against failed seams, buckles and fabric for as "
             "long as you own it. Wear and tear from use is not covered.", "warranty", 2026),
            ("warranty-zipper", "A zipper that no longer closes",
             "Most jammed or splitting zips can be fixed with a new slider. Send the item in and we "
             "repair it for free within the warranty.", "warranty", 2024),
            ("warranty-claim", "Making a warranty claim",
             "Fill in the claim form with photos of the fault and your order number. Our team "
             "answers within two business days.", "warranty", 2025),
            ("warranty-resole", "Resoling worn boots",
             "Trailhead boots use Vibram Megagrip outsoles that a cobbler can replace. Send them "
             "to our repair shop and they come back within three weeks.", "warranty", 2023),
            ("warranty-tent", "Tent poles and fabric coverage",
             "A snapped pole is replaced free for five years. Sun damage, and punctures caused by "
             "sharp rocks, are not covered.", "warranty", 2022),
            ("warranty-repair", "Paid repairs outside the warranty",
             "Not covered? Our repair shop patches, re-stitches and re-proofs gear for a flat fee, "
             "quoted before we start any work.", "warranty", 2024),
        )  # fmt: skip
        return [dict(zip(fields, row, strict=True)) for row in rows]

    def rs_wow_presets():
        """The wow cell's questions, each with the article it is really after and what to notice."""
        return [
            {
                "label": "Same meaning, different words",
                "query": "trail shoes feel cramped",
                "target": "size-boots",
                "lesson": "Not one article contains *trail*, *shoes*, *feel* or *cramped*, so "
                "keyword search has nothing to return. The vector search reads the meaning and "
                "ranks the boot guide first. Hybrid fuses the two lists, and with only one of "
                "them to go on, the vector order stands.",
            },
            {
                "label": "An exact name",
                "query": "Trailhead",
                "target": "warranty-resole",
                "lesson": "A product name the embedding model has never seen. Keyword search finds "
                "the one article that contains it; the vector search is guessing. Hybrid puts "
                "keyword search's answer first because the vector side also lists it.",
            },
            {
                "label": "A typo",
                "query": "waterproofng",
                "target": "care-waterproof",
                "lesson": "No article contains that misspelling, so keyword search returns nothing "
                "at all. The embedding still lands on *waterproofing*, and hybrid falls back on it.",
            },
        ]

    def rs_wow_preset(label, typed):
        """The wow question called `label`; any other label is the user's own, which is `typed`."""
        for preset in rs_wow_presets():
            if preset["label"] == label:
                return preset
        return {
            "label": label,
            "query": typed.strip(),
            "target": None,
            "lesson": "Your own words, so no article is starred: nobody said which one you were "
            "after. Look for rows where the columns disagree. Hybrid rewards an article that both "
            "lists rank, so a keyword hit that the vector search also likes can sit above the "
            "vector search's favourite.",
        }

    def rs_doc_key(article_id=""):
        """The hash key of an article; with no id, the prefix the index watches."""
        return f"{RS_PREFIX}doc:{article_id}"

    def rs_index():
        """This run's index name. The deep dive's second index is this plus "2"."""
        return RS_PREFIX + "idx"

    def rs_unit(matrix):
        """Scale every row to length 1, so a COSINE distance is 1 minus a dot product."""
        import numpy as np

        lengths = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.where(lengths == 0, 1, lengths)

    def rs_blob(vector):
        """The bytes a FLOAT32 VECTOR field stores: DIM x 4 of them, or indexing fails silently."""
        return vector.astype("float32").tobytes()

    def rs_hash_embed(texts, dim=256):
        """The zero-download fallback: every word is hashed into one of `dim` buckets.

        It matches words, not meaning, so "boots" can never find "shoes".
        """
        import hashlib

        import numpy as np

        matrix = np.zeros((len(texts), dim), dtype=np.float32)
        for row, text in zip(matrix, texts, strict=True):
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
                row[int.from_bytes(digest, "big") % dim] += 1
        return rs_unit(matrix)

    def rs_load_embedder():
        """potion-base-8M as (name, embed, problem); embed(texts) gives one unit-length row per text.

        model2vec's force_download=False does nothing, so the snapshot is fetched here: from the
        local cache first, then online, and only the four files it needs (~30 MB). If the model
        cannot be had the hashing fallback comes back instead, with a note on why.
        """
        import numpy as np

        try:
            from huggingface_hub import snapshot_download
            from model2vec import StaticModel

            files = {
                "allow_patterns": [
                    "config.json",
                    "model.safetensors",
                    "tokenizer.json",
                    "README.md",
                ]
            }
            try:
                path = snapshot_download("minishlab/potion-base-8M", local_files_only=True, **files)
            except Exception:  # not cached yet
                path = snapshot_download("minishlab/potion-base-8M", **files)
            model = StaticModel.from_pretrained(path)
        except Exception as exc:  # no network, no cache, a blocked host ...: degrade, loudly
            return "hashing fallback", rs_hash_embed, f"{type(exc).__name__}: {exc}"[:200]

        def embed(texts):
            return rs_unit(np.asarray(model.encode(list(texts)), dtype=np.float32))

        return "potion-base-8M", embed, ""

    def rs_tokens(text):
        """The words of a query, lower-cased, minus the ones RediSearch would ignore anyway."""
        stop = (
            "a is the an and are as at be but by for if in into it no not of on or such that their "
            "then there these they this to was will with"
        ).split()
        return [word for word in re.findall(r"[^\W_]+", text.lower()) if word not in stop]

    def rs_text_query(text, prefix=False, fuzzy=False):
        """A union of the query's words, '(hiking|boots)'; "" if it has none.

        Prefix adds `boots*` for every word and fuzzy adds `%boots%` (one edit away).
        """
        terms = []
        for word in rs_tokens(text):
            terms.append(word)
            if prefix and len(word) >= 2:  # RediSearch wants two characters before the *
                terms.append(word + "*")
            if fuzzy:
                terms.append(f"%{word}%")
        return f"({'|'.join(terms)})" if terms else ""

    def rs_create_args(name, dim, algorithm="HNSW", metric="COSINE"):
        """FT.CREATE for the help centre: the index watches every hash under rs_doc_key()."""
        return [
            "FT.CREATE", name, "ON", "HASH", "PREFIX", 1, rs_doc_key(), "SCHEMA",
            "title", "TEXT", "WEIGHT", 2, "body", "TEXT", "category", "TAG", "year", "NUMERIC",
            "vec", "VECTOR", algorithm, 6, "TYPE", "FLOAT32", "DIM", dim, "DISTANCE_METRIC", metric,
        ]  # fmt: skip

    def rs_doc_args(article, blob):
        """HSET for one article: text, tag and number fields, and the vector as raw bytes."""
        return [
            "HSET", rs_doc_key(article["id"]), "title", article["title"], "body", article["body"],
            "category", article["category"], "year", article["year"], "vec", blob,
        ]  # fmt: skip

    def rs_text_args(index, query):
        """FT.SEARCH by keyword: BM25 scores returned, ten best, query dialect 2."""
        return [
            "FT.SEARCH", index, query, "SCORER", "BM25STD", "WITHSCORES",
            "RETURN", 3, "title", "category", "year", "LIMIT", 0, 10, "DIALECT", 2,
        ]  # fmt: skip

    def rs_knn_args(index, blob, k, prefilter="*"):
        """FT.SEARCH for the k nearest vectors among the documents `prefilter` lets through.

        KNN needs DIALECT 2, and its results stop at 10 unless LIMIT says k.
        """
        return [
            "FT.SEARCH", index, f"{prefilter}=>[KNN {k} @vec $BLOB AS dist]",
            "PARAMS", 2, "BLOB", blob, "SORTBY", "dist", "ASC",
            "RETURN", 4, "title", "category", "year", "dist", "LIMIT", 0, k, "DIALECT", 2,
        ]  # fmt: skip

    def rs_range_args(index, blob, radius):
        """FT.SEARCH for every vector within `radius` (a cosine distance): possibly none."""
        return [
            "FT.SEARCH", index, "@vec:[VECTOR_RANGE $R $BLOB]=>{$YIELD_DISTANCE_AS: dist}",
            "PARAMS", 4, "R", radius, "BLOB", blob, "SORTBY", "dist", "ASC",
            "RETURN", 4, "title", "category", "year", "dist", "LIMIT", 0, 100, "DIALECT", 2,
        ]  # fmt: skip

    def rs_filter(categories, years):
        """A KNN pre-filter: any of the TAG categories (none means all) within a NUMERIC year range."""
        low, high = years
        parts = ["@category:{" + "|".join(categories) + "}"] if categories else []
        parts.append(f"@year:[{int(low)} {int(high)}]")
        return "(" + " ".join(parts) + ")"

    def rs_hybrid_args(index, query, blob, k):
        """FT.HYBRID (Redis 8.4+): BM25 and KNN run server-side and fuse by reciprocal rank (RRF).

        The counts after KNN and COMBINE include every token that follows them.
        """
        return [
            "FT.HYBRID", index, "SEARCH", query, "SCORER", "BM25STD",
            "VSIM", "@vec", "$BLOB", "KNN", 2, "K", k,
            "COMBINE", "RRF", 4, "WINDOW", 20, "CONSTANT", 60,
            "LOAD", 1, "@__key", "LIMIT", 0, k, "PARAMS", 2, "BLOB", blob,
        ]  # fmt: skip

    def rs_parse_search(reply, scored=False):
        """(total, rows) from a RESP2 FT.SEARCH reply.

        The reply is flat, [total, key, [field, value ...], key, ...], with a score before each
        field list under WITHSCORES. A row is {"key", "score" (if scored), the returned fields};
        dist, the distance a KNN or range query yields, is a float.
        """
        total, *rest = decode_reply(reply)
        step = 3 if scored else 2
        rows = []
        for i in range(0, len(rest), step):
            row = {"key": rest[i]}
            if scored:
                row["score"] = float(rest[i + 1])
            row |= reply_map(rest[i + step - 1])
            if "dist" in row:
                row["dist"] = float(row["dist"])
            rows.append(row)
        return int(total), rows

    def rs_parse_hybrid(reply):
        """The document keys, best first, from a RESP2 FT.HYBRID reply.

        redis-py reads the reply as key/value pairs whose `results` are field lists, and asks
        for @__key so each carries its key. Nothing else is guessed at: any other shape is a
        ValueError, so the caller can fall back to fusing the rankings itself.
        """
        rows = reply_map(decode_reply(reply)).get("results")
        if not isinstance(rows, list):
            raise ValueError("unrecognised FT.HYBRID reply")
        keys = [reply_map(row).get("__key") for row in rows]
        if not all(keys):
            raise ValueError("an FT.HYBRID result has no @__key")
        return keys

    def rs_parse_info(reply):
        """The numbers worth comparing from a RESP2 FT.INFO reply."""
        info = reply_map(decode_reply(reply))

        def number(name):
            try:
                return float(info[name])
            except (KeyError, TypeError, ValueError):
                return 0.0

        return {
            "docs": int(number("num_docs")),
            "failures": int(number("hash_indexing_failures")),
            "indexing": bool(number("indexing")),
            "vector_mb": number("vector_index_sz_mb"),
            "total_mb": number("total_index_memory_sz_mb"),
        }

    def rs_search_failed(exc):
        """The callout for a search that raised. Usually the index is gone: Cleanup dropped it."""
        if isinstance(exc, redis.ResponseError) and is_unknown_index(exc):
            return status_callout(
                "warn",
                "**The index is gone.** Cleanup dropped it, or this run's hour is up. Press the "
                "**Write** button above to build it again, or **Reset the column**.",
            )
        return status_callout("error", exc)

    def rs_search_text(client, text, prefix=False, fuzzy=False):
        """Keyword search: {"argv", "total", "rows"}; argv is None if no word is searchable."""
        query = rs_text_query(text, prefix, fuzzy)
        if not query:
            return {"argv": None, "total": 0, "rows": []}
        argv = rs_text_args(rs_index(), query)
        total, rows = rs_parse_search(client.execute_command(*argv), scored=True)
        return {"argv": argv, "total": total, "rows": rows}

    def rs_knn_search(client, embed, text, k, prefilter="*", index=None):
        """The k nearest articles to `text`: {"argv", "k", "rows"}."""
        argv = rs_knn_args(index or rs_index(), rs_blob(embed([text])[0]), k, prefilter)
        return {"argv": argv, "k": k, "rows": rs_parse_search(client.execute_command(*argv))[1]}

    def rs_range_search(client, embed, text, radius):
        """Every article within `radius` of `text`: {"argv", "radius", "rows"}."""
        radius = round(float(radius), 4)  # a slider hands over 0.15000000000000002
        argv = rs_range_args(rs_index(), rs_blob(embed([text])[0]), radius)
        return {
            "argv": argv,
            "radius": radius,
            "rows": rs_parse_search(client.execute_command(*argv))[1],
        }

    def rs_rrf(*rankings, constant=60):
        """Reciprocal rank fusion of rankings (lists of keys, best first): the fused list."""
        scores = {}
        for ranking in rankings:
            for rank, key in enumerate(ranking, 1):
                scores[key] = scores.get(key, 0) + 1 / (constant + rank)
        return sorted(scores, key=scores.get, reverse=True)

    def rs_wow_search(client, embed, preset, has_hybrid):
        """One question asked three ways: keyword, vector and hybrid, ten best from each.

        The question (see rs_wow_preset) must hold a word that keyword search can use. Returns
        the document keys each engine ranked, best first, and the commands sent. The hybrid list
        comes from FT.HYBRID when the server has it and answers as expected; if not, from fusing
        the other two here, and `source` and `note` say which and why.
        """
        query = preset["query"]
        bm25 = rs_search_text(client, query)
        knn = rs_knn_search(client, embed, query, 10)
        wow = {
            "preset": preset,
            "BM25": [row["key"] for row in bm25["rows"]],
            "vector": [row["key"] for row in knn["rows"]],
            "commands": [redis_cli(bm25["argv"]), redis_cli(knn["argv"])],
            "source": "client-side RRF",
            "note": "FT.HYBRID needs Redis 8.4 or later.",
        }
        if has_hybrid:
            argv = rs_hybrid_args(rs_index(), rs_text_query(query), rs_blob(embed([query])[0]), 10)
            wow["commands"].append(redis_cli(argv))
            try:
                wow["hybrid"] = rs_parse_hybrid(client.execute_command(*argv))
                wow["source"], wow["note"] = "FT.HYBRID", ""
            except (redis.ResponseError, ValueError) as exc:
                wow["note"] = f"FT.HYBRID did not work here ({exc})."
        if wow["source"] != "FT.HYBRID":
            wow["hybrid"] = rs_rrf(wow["BM25"], wow["vector"])
        return wow

    def rs_ranks(wow):
        """{engine: {document key: rank}}, 1 being best. A document outside the top ten is absent."""
        return {
            engine: {key: rank for rank, key in enumerate(wow[engine], 1)}
            for engine in ("BM25", "vector", "hybrid")
        }

    def rs_rank_rows(wow, sort_by):
        """The one rank table: a row per document any engine ranked, sorted by one engine's rank.

        A question with a known answer stars that article's row in an "answer" column.
        """
        ranks = rs_ranks(wow)
        articles = {rs_doc_key(a["id"]): a for a in rs_articles()}
        target = wow["preset"]["target"] and rs_doc_key(wow["preset"]["target"])
        engines = [sort_by] + [e for e in ("hybrid", "BM25", "vector") if e != sort_by]
        keys = sorted(
            set().union(*ranks.values()),
            key=lambda key: [ranks[engine].get(key, 99) for engine in engines],
        )
        return [
            ({"answer": "★" if key == target else ""} if target else {})
            | {
                "article": articles[key]["title"],
                "category": articles[key]["category"],
                "BM25 rank": ranks["BM25"].get(key),
                "vector rank": ranks["vector"].get(key),
                "hybrid rank": ranks["hybrid"].get(key),
            }
            for key in keys
        ]

    def rs_wow_summary(wow):
        """One line saying where each engine put the article the question was really after.

        "" for a question of the user's own, which has none.
        """
        if not wow["preset"]["target"]:
            return ""
        ranks = rs_ranks(wow)
        target = rs_doc_key(wow["preset"]["target"])
        title = next(a["title"] for a in rs_articles() if rs_doc_key(a["id"]) == target)
        where = [
            f"{engine} #{ranks[engine][target]}"
            if target in ranks[engine]
            else f"{engine} missed it"
            for engine in ("BM25", "vector", "hybrid")
        ]
        return f"The article it is really after, ★ **{title}**: " + " · ".join(where) + "."

    def rs_hit_rows(hits):
        """Search rows as table rows: rank, then the BM25 score or the distance, then the article."""
        return [
            {"#": rank}
            | {name: round(hit[name], 3) for name in ("score", "dist") if name in hit}
            | {name: hit.get(name, "") for name in ("title", "category", "year")}
            for rank, hit in enumerate(hits, 1)
        ]

    def rs_probe(client):
        """Read-only facts about the database Connect reached. Raises if it has no Search."""
        mark = REDIS_TRACE[-1] if REDIS_TRACE else None
        try:
            facts = redis_facts(client)
        except redis.ResponseError:
            detail = (
                "this database has no Search (FT._LIST failed). Redis Cloud includes it in every "
                "8.x database; a free one is at https://redis.io/try-free/"
            )
            raise ServiceError("rs", None, "config", detail) from None
        runs = {}
        for key in facts["notebook_keys"]:
            if match := re.match(r"irisnb_([0-9a-f]{8})_", key):
                runs.setdefault(match[1], {"keys": 0, "sample": key})["keys"] += 1
        for run in runs.values():
            run["ttl"] = client.execute_command("TTL", run.pop("sample"))
        version = facts["version_tuple"]
        return {
            **facts,
            "hybrid": version >= (8, 4),
            "svs": version >= (8, 2),
            "runs": runs,
            "commands": redis_cli_since(mark),
        }

    def rs_describe(facts):
        """What Connect found, as a callout (plus a warning if the database looks shared)."""
        here = facts["runs"].get(RUN, {}).get("keys", 0)
        others = sum(run != RUN for run in facts["runs"])
        found = [
            f"**Connected** to `{facts['where']}`: Redis **{facts['version']}** with Search · "
            f"**{facts['dbsize']}** keys · **{facts['clients']}** client connections (the free "
            "tier allows 30)",
            f"- `FT.HYBRID` (Redis 8.4+): **{'yes' if facts['hybrid'] else 'no'}** · "
            f"`SVS-VAMANA` (8.2+): **{'yes' if facts['svs'] else 'no'}**",
            f"- keys of this run (`{PREFIX}*`): **{here}** · other runs' leftovers: **{others}**",
        ]
        return mo.vstack(
            [
                status_callout("ok", "\n".join(found)),
                shared_db_warning(
                    facts,
                    f"This column only writes under `{PREFIX}`, but Redis Cloud cannot give it a "
                    "narrower user. If other apps depend on this database, use a scratch one.",
                ),
                wire_view("The read-only commands, as redis-cli", facts["commands"]),
            ]
        )

    def rs_leftovers(facts):
        """Other runs' leftovers from what Connect found.

        {"runs": table rows, "indexes": names to offer for dropping, "live": names left alone}.
        An index is offered once its run's keys are gone (or will never expire). While they are
        still counting down the run may be someone's live demo, and dropping its index would
        break their next search.
        """

        def expires(ttl):
            if ttl == -2:  # the key expired between the SCAN and the TTL
                return "gone"
            return "never (no TTL)" if ttl < 0 else f"in ~{max(1, (ttl + 59) // 60)} min"

        counting = {run for run, found in facts["runs"].items() if found["ttl"] >= 0}
        others = {}
        for name in facts["indexes"]:
            if (match := re.match(r"irisnb_([0-9a-f]{8})_", name)) and not name.startswith(PREFIX):
                others[name] = match[1]
        return {
            "runs": [
                {"run": run, "keys": found["keys"], "expires": expires(found["ttl"])}
                for run, found in facts["runs"].items()
                if run != RUN
            ],
            "indexes": [name for name, run in others.items() if run not in counting],
            "live": [name for name, run in others.items() if run in counting],
        }

    def rs_drop_orphans(client, names, confirmation):
        """Drop other runs' indexes, never with DD, once `confirmation` is typed. Returns the names.

        approve_orphan_index checks each one really is an index another run of this notebook
        built, and grants the guard a single drop of it.
        """
        dropped = []
        for name in names:
            try:
                approve_orphan_index(client.execute_command("FT.INFO", name), confirmation)
            except redis.ResponseError as exc:
                if not is_unknown_index(exc):  # already gone is fine
                    raise
                continue
            if rs_drop_index(client, name):  # False: someone else dropped it since the FT.INFO
                dropped.append(name)
        return dropped

    def rs_test_connection():
        """verify.py --preflight: connect and probe, read-only. Returns (ok, detail)."""
        url = redis_url_from_env()
        if not url:
            return False, "no REDIS_URL or REDIS_HOST is set"
        facts = rs_probe(connect_redis(url))
        hybrid, svs = ("yes" if facts[name] else "no" for name in ("hybrid", "svs"))
        return True, (
            f"Redis {facts['version']}, Search available, FT.HYBRID {hybrid}, SVS-VAMANA {svs}, "
            f"{facts['dbsize']} keys"
        )

    def rs_seed_corpus(client, embed, dim):
        """Create the index, then write and expire every article. Ledgered, so Cleanup finds them all.

        The index goes first, so each HSET is searchable the moment it lands. The client holds
        writes to WRITE_RATE per second. Pressing Seed twice just writes the same things again.
        """
        articles = rs_articles()
        vectors = embed([f"{a['title']}. {a['body']}" for a in articles])
        ledger_add("rs", "index", rs_index(), "intent")
        try:
            client.execute_command(*rs_create_args(rs_index(), dim))
        except redis.ResponseError as exc:
            if "already exists" not in str(exc).lower():
                raise
        ledger_add("rs", "index", rs_index())
        for article, vector in zip(articles, vectors, strict=True):
            key = rs_doc_key(article["id"])
            ledger_add("rs", "key", key, "intent")
            client.execute_command(*rs_doc_args(article, rs_blob(vector)))
            client.execute_command("EXPIRE", key, KEY_TTL_S)
            ledger_add("rs", "key", key)
        return len(articles)

    def rs_index_info(client, name):
        """FT.INFO's numbers for one index (see rs_parse_info), or None if there is no such index."""
        try:
            return rs_parse_info(client.execute_command("FT.INFO", name))
        except redis.ResponseError as exc:
            if is_unknown_index(exc):
                return None
            raise

    def rs_drop_index(client, name):
        """FT.DROPINDEX without DD, so the documents stay. False if the index was already gone."""
        try:
            client.execute_command("FT.DROPINDEX", name)
        except redis.ResponseError as exc:
            if is_unknown_index(exc):
                return False
            raise
        return True

    def rs_cleanup(client):
        """Drop this run's ledgered indexes and UNLINK its ledgered keys; return the counts.

        Only what the ledger names is touched, and never with a SCAN. The ledger is read from
        disk now, so a fresh kernel with this run id can clean up too. Indexes and keys that are
        already gone are counted ("gone" for the indexes), not errors.
        """
        names = ledger_live("rs", "index", pending=True)
        dropped = 0
        for name in names:
            dropped += rs_drop_index(client, name)
            ledger_add("rs", "index", name, "deleted")
        keys = ledger_live("rs", "key", pending=True)
        removed = client.execute_command("UNLINK", *keys) if keys else 0
        for key in keys:
            ledger_add("rs", "key", key, "deleted")
        return {
            "indexes": len(names), "dropped": dropped, "gone": len(names) - dropped,
            "keys": len(keys), "removed": removed,
        }  # fmt: skip

    def rs_cleanup_report(done):
        """The counts from rs_cleanup, in a sentence."""
        if not (done["indexes"] or done["keys"]):
            return "Nothing to clean up: this run has written nothing, or it is already cleaned up."
        report = (
            f"Dropped **{done['dropped']}** of {done['indexes']} indexes and unlinked "
            f"**{done['removed']}** of {done['keys']} keys."
        )
        gone = []
        if done["gone"]:
            gone.append(f"{done['gone']} of the indexes")
        if done["removed"] < done["keys"]:
            gone.append(
                f"{done['keys'] - done['removed']} of the keys (they expire on their own after "
                f"{KEY_TTL_S // 60} minutes)"
            )
        if gone:
            report += " Already gone: " + " and ".join(gone) + "."
        return report

    def rs_build_second(client, dim, algorithm, metric):
        """Build a second index over the same documents; returns its name.

        It is ledgered, so Cleanup drops it. Building again replaces the last one.
        """
        name = rs_index() + "2"
        ledger_add("rs", "index", name, "intent")
        rs_drop_index(client, name)
        client.execute_command(*rs_create_args(name, dim, algorithm, metric))
        ledger_add("rs", "index", name)
        for _ in range(50):  # the new index scans the existing hashes in the background
            if not rs_index_info(client, name)["indexing"]:
                break
            time.sleep(0.1)
        return name

    def rs_compare_indexes(client, embed, query, indexes):
        """FT.INFO's numbers and the same top-3 KNN for each index. `indexes` maps name to a label.

        Returns {"rows": table rows, "note": a sentence on whether the indexes agree}, worked out
        from what they returned rather than from what indexes are expected to do.
        """
        rows, tops = [], []
        for name, label in indexes.items():
            info = rs_index_info(client, name)
            top = rs_knn_search(client, embed, query, 3, index=name)["rows"]
            tops.append(top)
            rows.append(
                {
                    "index": label,
                    "documents": info["docs"],
                    "vector index MB": round(info["vector_mb"], 3),
                    "all of it MB": round(info["total_mb"], 3),
                    "failed to index": info["failures"],
                    "nearest 3": " · ".join(f"{hit['title']} ({hit['dist']:.3f})" for hit in top),
                }
            )
        if not all(tops):
            return {"rows": rows, "note": "An index returned nothing: its articles have expired."}
        same = len({tuple(hit["key"] for hit in top) for top in tops}) == 1
        agree = (
            "Every index returned the same three articles in the same order."
            if same
            else "The indexes disagree about the three nearest articles."
        )
        nearest = " and ".join(
            f"{top[0]['dist']:.3f} by {label}"
            for top, label in zip(tops, indexes.values(), strict=True)
        )
        return {"rows": rows, "note": f"{agree} The nearest article is {nearest}."}

    # ---- column 1 · LangCache: helpers -------------------------------------------

    def lc_seeds():
        """The gear-care answers the column stores: {key: {"prompt", "response"}}.

        Cairn Outfitters questions with gear in them. Nothing here is close to what the live Iris
        demo is about (a bank, electronics, a clinic, deliveries, phones, betting, shares), and it
        searches the same kind of cache with no filter, so it is never served one of them.
        """
        rows = (
            ("boots", "How do I clean my Trailhead boots after a muddy hike?",
             "Brush off the dry mud, rinse them with lukewarm water and let them dry at room "
             "temperature. Never put leather next to a heater or a campfire."),
            ("bag", "How do I wash my down sleeping bag?",
             "Use a front-loading machine and a down-specific soap, then dry it on low heat with "
             "clean tennis balls to break up the clumps."),
            ("tent", "How should I store my tent between trips?",
             "Pitch it once at home until it is completely dry, then keep it loosely packed in a "
             "cool cupboard. Damp fabric grows mildew within days."),
            ("shell", "How do I restore the waterproofing on my 3-layer rain shell?",
             "Wash the shell, then apply a spray-on or wash-in treatment. A short tumble in the "
             "dryer sets it."),
            ("stove", "How do I clean my camp stove?",
             "Clear the jet with the pricking wire, wipe the burner and check the fuel hose for "
             "cracks before every season."),
        )  # fmt: skip
        return {key: {"prompt": prompt, "response": answer} for key, prompt, answer in rows}

    def lc_probes():
        """The threshold lab's questions: {"text", "kind", "target"}, in the order they are shown.

        target is the seed (see lc_seeds) that a correct HIT returns, or None when the cached answer
        would be wrong for this question, which is what makes a hit on it a false hit.
        """
        rows = (
            ("How do I wash my down sleeping bag?", "same words", "bag"),
            ("What's the right way to launder a goose-down sleeping bag?", "paraphrase", "bag"),
            ("My rain jacket stopped beading water. How do I fix that?", "paraphrase", "shell"),
            ("Best way to get the mud off my Trailhead boots?", "paraphrase", "boots"),
            ("how should i put my tent away after a trip", "paraphrase", "tent"),
            ("How do I look after my camping stove?", "paraphrase", "stove"),
            ("How do I wash my synthetic sleeping bag?", "swapped item", None),
            ("How should I store my kayak between trips?", "swapped item", None),
            ("How do I clean my climbing harness?", "near-miss", None),
            ("How do I dry my hiking boots quickly?", "near-miss", None),
            ("How do I avoid washing my down sleeping bag?", "negation", None),
            ("What is the capital of Australia?", "unrelated", None),
            ("What are your store opening hours?", "unrelated", None),
            ("Which stocks should I buy this year?", "unrelated", None),
        )  # fmt: skip
        return [dict(zip(("text", "kind", "target"), row, strict=True)) for row in rows]

    def lc_variants():
        """The strategies cell's variants of one stored question, each {"label", "text"}."""
        rows = (
            ("case", "how do i wash my down sleeping bag?"),
            ("shouting", "HOW DO I WASH MY DOWN SLEEPING BAG?"),
            ("punctuation", "How do I wash my down sleeping bag"),
            ("spacing", "How do I wash  my down sleeping bag ?"),
            ("paraphrase", "Can you tell me how to wash a down bag?"),
            ("loose paraphrase", "Cleaning a down bag: where do I start?"),
        )
        return [dict(zip(("label", "text"), row, strict=True)) for row in rows]

    def lc_script():
        """The chat's presets: a question the cache misses, the answer to teach, and a paraphrase."""
        return {
            "ask": "How do I fix a snapped tent pole?",
            "answer": "Splint the break with the repair sleeve from your tent's kit and tape it "
            "tight. A snapped pole is replaced free within five years of purchase.",
            "again": "What should I do about a broken tent pole?",
        }

    def lc_vocabulary():
        """The words that make a question about Cairn gear, and so one Teach will store."""
        return frozenset(
            "cairn outfitters boot boots trailhead ridgeline tent tents pole poles sleeping "
            "backpack backpacks pack packs stove stoves jacket jackets shell shells hike hiking "
            "camp camping tarp waterproofing crampons gaiters harness headlamp kayak gear".split()
        )

    def lc_norm(text):
        """A prompt as an exact match reads it: case and runs of spaces do not count."""
        return " ".join(text.lower().split())

    def lc_seed_key(prompt):
        """The key of the seed that a stored prompt is, or the prompt itself if it is not a seed."""
        for key, seed in lc_seeds().items():
            if lc_norm(seed["prompt"]) == lc_norm(prompt):
                return key
        return prompt

    def lc_connection(host, cache_id, typed_key, scope):
        """(connection, missing) from the config fields; a blank one falls back on the environment.

        missing names the settings that are still absent, and the connection is None while there
        are any. The host may come without its scheme, or with a slash or a path: only its origin
        is used. The connection holds the API key as a Secret, so it previews masked.
        """
        host = host.strip() or env("LANGCACHE_HOST")
        cache_id = cache_id.strip() or env("LANGCACHE_CACHE_ID")
        key = resolve_secret("LANGCACHE_API_KEY", typed_key)
        absent = {"LANGCACHE_HOST": host, "LANGCACHE_CACHE_ID": cache_id, "LANGCACHE_API_KEY": key}
        if missing := [name for name, value in absent.items() if not value]:
            return None, missing
        try:
            origin = httpx.URL(host if "://" in host else "https://" + host)
        except httpx.InvalidURL:
            raise ValueError("the LangCache host is not a valid URL") from None
        netloc = origin.netloc.decode()
        if origin.scheme != "https" or not re.fullmatch(r"[a-z0-9._-]+(:\d+)?", netloc):
            raise ValueError("the LangCache host must be an https:// address")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cache_id):
            raise ValueError("a Cache ID is made of letters, digits, - and _")
        set_lc_scope(scope.strip() or env("LANGCACHE_SCOPE_ATTR"))
        base = f"https://{netloc}/v1/caches/{cache_id}"
        return {"base": base, "cache_id": cache_id, "key": key, "scope": LC_SCOPE["attr"]}, []

    def lc_call(conn, method, path, body=None, *, expect=(200, 201, 204)):
        """One request to the cache through http_call: (status, body). path sits below the cache."""
        headers = {"Authorization": f"Bearer {conn['key'].reveal()}", "Accept": "application/json"}
        return http_call(
            "lc", method, conn["base"] + path, headers=headers, json=body, expect=expect
        )

    def lc_search(conn, prompt, threshold, strategies=("semantic",)):
        """Search the cache once: {"prompt", "threshold", "mine", "foreign", "call"}.

        mine is the matches that are entries of this run, best first, each {"id", "prompt",
        "response", "similarity", "strategy"}. Every other match belongs to another app: only its
        similarity is kept, never its prompt or its answer. maxResults is 5 because the default is
        1, and the best match on a shared cache may not be ours. call is the request as traced.
        """
        threshold = round(float(threshold), 2)  # a slider hands over 0.8500000000000001
        body = {
            "prompt": prompt,
            "similarityThreshold": threshold,
            "searchStrategies": list(strategies),
            "maxResults": 5,
        }
        if conn["scope"]:
            body["attributes"] = {conn["scope"]: RUN_TAG}
        _, reply = lc_call(conn, "POST", "/entries/search", body)
        reply = reply if isinstance(reply, dict) else {}
        ours = set(ledger_live("lc", "entry"))
        found = reply.get("data") if isinstance(reply.get("data"), list) else []
        mine, foreign, shown = [], [], []
        for item in found:
            if not isinstance(item, dict):
                continue
            similarity = float(item.get("similarity") or 0)
            if item.get("id") in ours:
                mine.append(
                    {
                        "id": item["id"],
                        "prompt": str(item.get("prompt", "")),
                        "response": str(item.get("response", "")),
                        "similarity": similarity,
                        "strategy": str(item.get("searchStrategy", "")),
                    }
                )
                shown.append(item)
            else:
                foreign.append(similarity)
                shown.append({"hidden": "another app's entry", "similarity": similarity})
        mine.sort(key=lambda entry: entry["similarity"], reverse=True)
        # The trace keeps the reply as it came, and the wire view shows the trace. Swap in a copy
        # where another app's entry is only its score, so its prompt and answer are seen nowhere.
        HTTP_TRACE[-1] = call = HTTP_TRACE[-1]._replace(response=redact({**reply, "data": shown}))
        return {
            "prompt": prompt,
            "threshold": threshold,
            "mine": mine,
            "foreign": sorted(foreign, reverse=True),
            "call": call,
        }

    def lc_set_body(prompt, answer, ttl_ms, scope):
        """The JSON of one write. ttlMillis is always there: the cache's default is no expiry."""
        body = {"prompt": prompt, "response": answer, "ttlMillis": ttl_ms}
        if scope:
            body["attributes"] = {scope: RUN_TAG}
        return body

    def lc_store(conn, prompt, answer, ttl_ms):
        """Write one entry, and put the id it comes back with in the ledger at once.

        The cache chooses the id, so unlike the other columns there is no intent line to write
        first: the expiry is the net for a write whose reply never arrives.
        """
        _, reply = lc_call(
            conn, "POST", "/entries", lc_set_body(prompt, answer, ttl_ms, conn["scope"])
        )
        entry_id = reply.get("entryId") if isinstance(reply, dict) else None
        if not (isinstance(entry_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", entry_id)):
            raise ServiceError("lc", 201, "http", "the reply held no usable entryId")
        ledger_add("lc", "entry", entry_id)
        return entry_id

    def lc_delete(conn, entry_id):
        """Delete one entry by id: "deleted" (204), or "expired" (404: the cache let it go itself).

        The live service answers an id it no longer holds with a 404 of type
        "/errors/resource-not-found" (the API docs say "/errors/cache/entry/not-found"); either
        means the entry expired. A 404 of type "/errors/cache/not-found" says the cache itself is
        missing, which is a wrong Cache ID, and any other 404 says nothing about the entry: both
        deleted nothing, so they are errors and the id stays in the ledger for a retry.
        """
        status, reply = lc_call(conn, "DELETE", f"/entries/{entry_id}", expect=(204, 404))
        kind = str(reply.get("type", "")) if isinstance(reply, dict) else ""
        if status == 404 and kind.endswith("/cache/not-found"):
            detail = f"the cache was not found ({kind}): check the Cache ID"
            raise ServiceError("lc", 404, "http", detail)
        if status == 404 and not re.search(r"/(entry/not-found|resource[-/]not-found)$", kind):
            detail = (
                "the 404 said neither that the entry expired nor that the cache is missing "
                f"({kind or 'no type'})"
            )
            raise ServiceError("lc", 404, "http", detail)
        ledger_add("lc", "entry", entry_id, "deleted")
        return "deleted" if status == 204 else "expired"

    def lc_probe_cache(conn):
        """Read-only checks of a cache: does the key work, does it take this run's scope attribute?

        One search for a made-up prompt. If a scope attribute is set and the cache answers 400 (it
        has no attributes configured, or not this one), the run carries on unscoped. Returns
        {"scope": the attribute in force ("" for none), "note": why it was dropped, "ms", "others"}
        where others counts entries of other apps that matched.
        """
        prompt, note = "Cairn Outfitters connection check", ""
        try:
            found = lc_search(conn, prompt, 0.5)
        except ServiceError as exc:
            if not (conn["scope"] and exc.status == 400):
                raise
            set_lc_scope("")
            conn, note = {**conn, "scope": ""}, exc.detail
            found = lc_search(conn, prompt, 0.5)
        return {
            "scope": conn["scope"],
            "note": note,
            "ms": found["call"].ms,
            "others": len(found["foreign"]),
            "call": found["call"],
        }

    def lc_failed(exc):
        """service_failed with LangCache's own advice."""
        return service_failed(
            exc,
            {
                401: "The API key was refused. Paste `LANGCACHE_API_KEY` again. Do not press "
                "*Generate key* on a cache anyone else uses: it kills their key.",
                404: "Not found: check the host and the Cache ID. A wrong one answers 404 to "
                "everything.",
            },
        )

    def lc_describe(conn, facts):
        """What Test connection found, as callouts (plus a warning if the cache refused the scope)."""
        scope = (
            f"entries are tagged `{conn['scope']}: {RUN_TAG}` and every search filters on it"
            if conn["scope"]
            else "**no scope attribute**: entries carry no tag and every search reads the whole cache"
        )
        found = [
            f"**Connected** to cache `{conn['cache_id']}`: a search answered in "
            f"**{facts['ms']:.0f} ms**",
            f"- other apps' entries near the test prompt: **{facts['others']}** (never shown)",
            f"- {scope}",
        ]
        parts = [status_callout("ok", "\n".join(found))]
        if facts["note"]:
            parts.append(
                status_callout(
                    "warn",
                    f"The cache would not take the scope attribute, so this run is unscoped. It said: "
                    f"{facts['note']}",
                )
            )
        parts.append(wire_view("The test search, as HTTP", facts["call"]))
        return mo.vstack(parts)

    def lc_test_connection():
        """verify.py --preflight: reach the cache with a read-only search. Returns (ok, detail)."""
        conn, missing = lc_connection("", "", None, "")
        if conn is None:
            return False, "missing " + ", ".join(missing)
        facts = lc_probe_cache(conn)
        scope = f"scope attribute {facts['scope']}" if facts["scope"] else "no scope attribute"
        return True, f"cache {conn['cache_id']} answered in {facts['ms']:.0f} ms, {scope}"

    def lc_write_seeds(conn):
        """Write every gear-care answer, each with its expiry and its id in the ledger; the ids.

        Pressing the button twice writes them twice: the extra entries are ledgered and expire like
        the first, and a search simply finds two of each.
        """
        return [lc_store(conn, s["prompt"], s["response"], LC_TTL_MS) for s in lc_seeds().values()]

    def lc_probe_floor(conn):
        """Can a search go below the cache's default threshold? Search at 0.5 and see how low it goes.

        Two questions: a loose one that sits between several entries, and one entry's own prompt
        word for word, which any cache finds. Returns {"floor", "lowest"}: lowest is the least
        similar of this run's entries that came back from either, or None if none did (they have
        expired, or are not indexed yet). Under 0.85, the console's default, the server honoured
        the low threshold and the floor is 0.5. Otherwise it cannot be told apart from a cache that
        holds every search to its default, so the floor is 0.85 and the sliders start there.
        """
        loose = "How do I look after my camping gear?"
        scores = []
        for prompt in (loose, lc_seeds()["shell"]["prompt"]):
            scores += [entry["similarity"] for entry in lc_search(conn, prompt, 0.5)["mine"]]
        lowest = min(scores, default=None)
        return {"floor": 0.5 if lowest is not None and lowest < 0.85 else 0.85, "lowest": lowest}

    def lc_floor_message(probe):
        """What the floor probe found, for the callout under the write button."""
        if probe["floor"] < 0.85:
            return (
                "**Searchable.** A search at 0.50 was honoured: the least similar entry that came "
                f"back scored {probe['lowest']:.3f}, under the 0.85 default. The sliders below "
                "start at 0.85 and reach down to 0.50."
            )
        return (
            "**Searchable.** Nothing scored under 0.85 in a search at 0.50, so this cache may hold "
            "every search to its default threshold. The sliders below start at 0.85, and a "
            "question with no match shows as *below cache floor*."
        )

    def lc_hit_rows(mine, foreign):
        """Search matches as table rows, best first. Another app's entry is a similarity and no more.

        Three columns, for a column this narrow: the entry id and the strategy are in the wire view.
        """
        rows = [
            (
                entry["similarity"],
                {"similarity": round(entry["similarity"], 3), "matched prompt": entry["prompt"]},
            )
            for entry in mine
        ]
        rows += [
            (
                score,
                {"similarity": round(score, 3), "matched prompt": "another app's entry (hidden)"},
            )
            for score in foreign
        ]
        ranked = sorted(rows, key=lambda row: row[0], reverse=True)
        return [{"#": rank} | row for rank, (_, row) in enumerate(ranked, 1)]

    def lc_sweep(conn, probes, floor):
        """Search every probe once at the floor: [{"probe", "best", "foreign", "call"}].

        best is this run's closest entry (None if there was none) and foreign the similarity of
        the closest entry of another app (None likewise). The slider re-scores these, so nothing
        is searched twice.
        """
        swept = []
        for probe in probes:
            found = lc_search(conn, probe["text"], floor)
            swept.append(
                {
                    "probe": probe,
                    "best": found["mine"][0] if found["mine"] else None,
                    "foreign": found["foreign"][0] if found["foreign"] else None,
                    "call": found["call"],
                }
            )
        return swept

    def lc_verdict(row, threshold):
        """How one swept probe fares at a threshold, judged on this run's entries first.

        "hit": the right answer was served. "false hit": an answer was served that is wrong for the
        question. "foreign": nothing of ours qualified but another app's entry did. Otherwise a
        "missed paraphrase" if the question had a right answer, else a plain "miss".
        """
        probe, best = row["probe"], row["best"]
        if best and best["similarity"] >= threshold:
            return "hit" if lc_seed_key(best["prompt"]) == probe["target"] else "false hit"
        if row["foreign"] is not None and row["foreign"] >= threshold:
            return "foreign"
        return "missed paraphrase" if probe["target"] else "miss"

    def lc_scoreboard(sweep, threshold):
        """Precision, recall and hit rate at a threshold, on this run's entries only.

        Precision is the share of hits that were the right answer, recall the share of paraphrases
        that hit, hit rate the share of questions that got any answer of ours. A ratio with nothing
        to divide by is None. Another app's entries count as no hit.
        """
        verdicts = [lc_verdict(row, threshold) for row in sweep]
        right, wrong = verdicts.count("hit"), verdicts.count("false hit")
        should = sum(1 for row in sweep if row["probe"]["target"])
        return {
            "right": right,
            "hits": right + wrong,
            "should": should,
            "asked": len(sweep),
            "precision": right / (right + wrong) if right + wrong else None,
            "recall": right / should if should else None,
            "hit_rate": (right + wrong) / len(sweep),
        }

    def lc_lab_tiles(board):
        """The scoreboard as three stat tiles."""

        def percent(value):
            return "-" if value is None else f"{value:.0%}"

        return mo.hstack(
            [
                mo.stat(
                    percent(board["precision"]),
                    label="Precision",
                    bordered=True,
                    caption=f"{board['right']} of {board['hits']} hits were the right answer",
                ),
                mo.stat(
                    percent(board["recall"]),
                    label="Recall",
                    bordered=True,
                    caption=f"{board['right']} of {board['should']} paraphrases hit",
                ),
                mo.stat(
                    percent(board["hit_rate"]),
                    label="Hit rate",
                    bordered=True,
                    caption=f"{board['hits']} of {board['asked']} questions hit",
                ),
            ],
            justify="start",
            wrap=True,
        )

    def lc_lab_rows(sweep, threshold, floor):
        """The lab's table at a threshold: the question and its kind, the closest entry (by its key),
        its score, the verdict.
        """
        rows = []
        for row in sweep:
            verdict, best = lc_verdict(row, threshold), row["best"]
            if verdict == "foreign":
                closest, score = "another app", f"{row['foreign']:.3f}"
            elif best:
                closest, score = lc_seed_key(best["prompt"]), f"{best['similarity']:.3f}"
            else:
                closest, score = "-", "below cache floor" if floor > 0.5 else "under 0.50"
            rows.append(
                {
                    "question": row["probe"]["text"],
                    "kind": row["probe"]["kind"],
                    "closest entry": closest,
                    "similarity": score,
                    "verdict": verdict,
                }
            )
        return rows

    def lc_lab_table(rows):
        """The lab's rows as a table tinted by verdict. plain_table cannot tint a row, and the tint
        is the point here, so this one is HTML (every cell escaped, the verdict also spelt out).
        The kind of question sits under the question, to keep the table four columns wide.
        """
        columns = ("closest entry", "similarity", "verdict")
        tints = {
            "hit": "46, 160, 67",
            "false hit": "248, 81, 73",
            "missed paraphrase": "210, 153, 34",
            "foreign": "110, 118, 129",
        }
        head = "".join(f"<th>{html.escape(name)}</th>" for name in ("question", *columns))
        body = ""
        for row in rows:
            tint = tints.get(row["verdict"])
            style = f' style="background: rgba({tint}, 0.22)"' if tint else ""
            question = (
                f"{html.escape(row['question'])}<br><small>{html.escape(row['kind'])}</small>"
            )
            cells = "".join(f"<td>{html.escape(str(row[name]))}</td>" for name in columns)
            body += f"<tr{style}><td>{question}</td>{cells}</tr>"
        return mo.Html(
            "<style>.lc-lab { width: 100%; border-collapse: collapse; font-size: 0.85em }"
            ".lc-lab th, .lc-lab td { padding: 3px 6px; text-align: left; vertical-align: top }"
            f"</style><table class='lc-lab'><tr>{head}</tr>{body}</table>"
        )

    def lc_lab_summary(sweep):
        """One sentence on where false hits stop: how high the worst one scored, and the cost of
        a threshold above it.
        """
        wrong = [r["best"]["similarity"] for r in sweep if lc_verdict(r, 0.0) == "false hit"]
        if not wrong:
            return "No question was served a wrong answer at any threshold from the floor up."
        clear = min(1.0, round(max(wrong) + 0.01, 2))
        kept = sum(lc_verdict(row, clear) == "hit" for row in sweep)
        total = sum(1 for row in sweep if row["probe"]["target"])
        return (
            f"The highest false hit scored **{max(wrong):.3f}**. From **{clear:.2f}** up there are "
            f"none, and {kept} of the {total} paraphrases still hit."
        )

    def lc_strategy_sweep(conn, variants, floor):
        """Every variant searched under each strategy list, once, at the floor.

        [{"variant", "found": {label: the closest entry of this run, or None}, "call"}]; call is the
        request that asked for both strategies.
        """
        lists = {
            "semantic": ("semantic",),
            "exact": ("exact",),
            "exact + semantic": ("exact", "semantic"),
        }
        swept = []
        for variant in variants:
            found, call = {}, None
            for label, strategies in lists.items():
                reply = lc_search(conn, variant["text"], floor, strategies)
                found[label] = reply["mine"][0] if reply["mine"] else None
                call = reply["call"]  # the last list is the one with both
            swept.append({"variant": variant, "found": found, "call": call})
        return swept

    def lc_strategy_rows(sweep, threshold):
        """The strategies table at a threshold: what each strategy list answered for each variant.

        The slider is a semantic threshold. An exact match is a hit whatever score the service gives
        it, and shows none. Only the column that asks for both names the strategy that answered.
        """

        def cell(label, entry):
            if not entry or (entry["strategy"] != "exact" and entry["similarity"] < threshold):
                return "miss"
            score = "" if entry["strategy"] == "exact" else f" {entry['similarity']:.2f}"
            named = f" ({entry['strategy']})" if label == "exact + semantic" else ""
            wrong = "" if lc_seed_key(entry["prompt"]) == "bag" else " wrong entry"
            return f"HIT{score}{named}{wrong}"

        return [
            {"variant": row["variant"]["label"], "prompt sent": row["variant"]["text"]}
            | {label: cell(label, entry) for label, entry in row["found"].items()}
            for row in sweep
        ]

    def lc_taught_prompt(question):
        """The prompt Teach stores. The prefix names the shop, so a taught answer is only ever
        close to questions that mention it and never to another app's generic ones.
        """
        return "At Cairn Outfitters: " + question.strip()

    def lc_shared_warning():
        """The red note under Teach when the cache has no scope attribute to keep this run apart."""
        return (
            f"**Shared cache:** taught answers are visible to other apps for {LC_TEACH_TTL_MS // 1000} "
            "s. Name a scope attribute above, or use a cache of your own, to keep them apart."
        )

    def lc_teach_refusal(question, answer):
        """Why Teach will not store this, or "" if it will: no answer, or no word about Cairn gear.

        A stored answer is served to any app whose question is similar, so nothing that is not
        about the shop's gear goes in.
        """
        if not answer.strip():
            return "Type the answer to store first."
        if not set(re.findall(r"[a-z]+", question.lower())) & lc_vocabulary():
            return (
                "Not taught: that question is not about Cairn gear. A cache is shared, so Teach "
                "stores only questions that mention gear such as a boot, a tent, a stove or a pack."
            )
        if len(lc_taught_prompt(question)) > 1024:
            return "Not taught: LangCache prompts are at most 1024 characters."
        return ""

    def lc_ask(conn, history, question, threshold):
        """One chat turn: search at the threshold, and add the result (HIT or MISS) to history."""
        question = question.strip()
        if not question:
            history.append({"kind": "refused", "question": "", "why": "Type a question first."})
            return
        found = lc_search(conn, question, threshold)
        history.append(
            {
                "kind": "ask",
                "question": question,
                "threshold": found["threshold"],
                "hit": found["mine"][0] if found["mine"] else None,
                "hidden": len(found["foreign"]),
                "ms": found["call"].ms,
                "call": found["call"],
            }
        )

    def lc_teach(conn, history, taught, answer):
        """Store an answer for the question that just missed, for two minutes, and note the entry.

        Refused (with a reason in history) unless the last question was a MISS that is not taught
        yet and the question and answer pass lc_teach_refusal.
        """
        asked = next((turn for turn in reversed(history) if turn["kind"] == "ask"), None)
        if asked is None or asked["hit"] or asked.get("taught"):
            why = "Ask a question the cache misses first, and teach that one."
        else:
            why = lc_teach_refusal(asked["question"], answer)
        if why:
            history.append({"kind": "refused", "question": asked and asked["question"], "why": why})
            return
        stored = lc_taught_prompt(asked["question"])
        entry_id = lc_store(conn, stored, answer.strip(), LC_TEACH_TTL_MS)
        taught.add(entry_id)
        asked["taught"] = True
        history.append(
            {"kind": "teach", "question": asked["question"], "stored": stored, "entry_id": entry_id}
        )

    def lc_autorun_chat(conn, history, taught, threshold):
        """AUTORUN's chat: the preset question, teach it if it missed, then the paraphrase."""
        script = lc_script()
        lc_ask(conn, history, script["ask"], threshold)
        if not history[-1]["hit"]:
            lc_teach(conn, history, taught, script["answer"])
        lc_ask(conn, history, script["again"], threshold)

    def lc_turn_view(turn):
        """One chat turn as markup: the question, then a HIT badge with its answer, or a MISS."""
        asked = mo.md(f"**You:** {turn['question']}") if turn.get("question") else mo.md("")
        if turn["kind"] == "teach":
            script = lc_script()
            example = (
                f" For example: *{script['again']}*" if turn["question"] == script["ask"] else ""
            )
            return status_callout(
                "info",
                f"**Taught.** Stored as *{turn['stored']}* for {LC_TEACH_TTL_MS // 1000} s (entry "
                f"`{turn['entry_id']}`). Now ask it in other words.{example}",
            )
        if turn["kind"] == "refused":
            return mo.vstack([asked, status_callout("warn", turn["why"])])
        hit = turn["hit"]
        if hit:
            badge = status_callout(
                "ok",
                f"**HIT** · similarity **{hit['similarity']:.3f}** · matched *{hit['prompt']}* · "
                f"entry `{hit['id']}` · {turn['ms']:.0f} ms",
            )
            return mo.vstack(
                [asked, badge, mo.md("\n".join("> " + s for s in hit["response"].splitlines()))]
            )
        other = " Another app's entry matched too, and stays hidden." if turn["hidden"] else ""
        return mo.vstack(
            [
                asked,
                status_callout(
                    "warn",
                    f"**MISS** at {turn['threshold']:.2f} · {turn['ms']:.0f} ms. Nothing cached is "
                    f"that similar. A real assistant would call its LLM now.{other}",
                ),
            ]
        )

    def lc_ttl_store(conn):
        """Store a ten-second entry: {"id", "prompt", "stored" (monotonic), "checks", "deleted"}."""
        prompt = f"Cairn Outfitters ten-second expiry test {RUN}"
        entry_id = lc_store(
            conn, prompt, "This answer expires ten seconds after it is stored.", 10_000
        )
        return {
            "id": entry_id,
            "prompt": prompt,
            "stored": time.monotonic(),
            "checks": [],
            "deleted": False,
        }

    def lc_ttl_look(conn, entry):
        """Search for the entry now and add a row to its checks. The first miss also deletes it by
        id, and the 404 that comes back is the cache saying it has already expired.
        """
        found = lc_search(conn, entry["prompt"], 0.9)
        seen = next((hit for hit in found["mine"] if hit["id"] == entry["id"]), None)
        row = {
            "after": f"{time.monotonic() - entry['stored']:.0f} s",
            "search": f"HIT {seen['similarity']:.3f}" if seen else "MISS",
            "delete by id": "-",
        }
        if not seen and not entry["deleted"]:
            expired = lc_delete(conn, entry["id"]) == "expired"
            row["delete by id"] = "404: already expired" if expired else "204: it was still there"
            entry["deleted"] = True
        entry["checks"].append(row)

    def lc_ttl_script(conn, entry):
        """AUTORUN's Check again: look now, wait out the expiry, look once more."""
        lc_ttl_look(conn, entry)
        time.sleep(max(0.0, 11 - (time.monotonic() - entry["stored"])))
        lc_ttl_look(conn, entry)

    def lc_cleanup(conn):
        """Delete every entry the ledger names, by id; return the counts.

        The ledger is read from disk now, so a fresh kernel with this run id can clean up too. An
        id the cache no longer knows has expired, and counts as done.
        """
        ids = ledger_live("lc", "entry", pending=True)
        done = {"entries": len(ids), "deleted": 0, "expired": 0}
        for entry_id in ids:
            done[lc_delete(conn, entry_id)] += 1
        return done

    def lc_cleanup_report(done):
        """The counts from lc_cleanup, in a sentence."""
        if not done["entries"]:
            return "Nothing to clean up: this run has stored nothing, or it is already cleaned up."
        return (
            f"Deleted **{done['deleted']}** of {done['entries']} entries by id; **{done['expired']}** "
            "had already expired (the cache answered 404)."
        )

    # ---- column 2 · Context Retriever: helpers -----------------------------------

    def cr_records():
        """The Cairn Outfitters shop: 6 customers, 6 products and 8 orders, by entity name.

        Every id is a plain word plus digits, with no hyphen, which a TAG query would treat as an
        operator. EMEA and APAC each have 3 customers and 4 orders.
        """
        return {
            "Customer": [
                dict(customer_id="C001", name="Ada Lovelace", tier="gold", region="emea", lifetime_value=4200.0),
                dict(customer_id="C002", name="Grace Hopper", tier="gold", region="apac", lifetime_value=3900.0),
                dict(customer_id="C003", name="Alan Turing", tier="silver", region="emea", lifetime_value=1500.0),
                dict(customer_id="C004", name="Katherine Johnson", tier="silver", region="apac", lifetime_value=1750.0),
                dict(customer_id="C005", name="Linus Torvalds", tier="bronze", region="emea", lifetime_value=300.0),
                dict(customer_id="C006", name="Margaret Hamilton", tier="bronze", region="apac", lifetime_value=420.0),
            ],
            "Product": [
                dict(sku="P100", title="Trail Runner Shoes", category="footwear", price=129.0),
                dict(sku="P101", title="Alpine Down Jacket", category="apparel", price=249.0),
                dict(sku="P102", title="Ultralight Tent 2P", category="camping", price=399.0),
                dict(sku="P103", title="Insulated Water Bottle", category="accessories", price=35.0),
                dict(sku="P104", title="Merino Base Layer", category="apparel", price=89.0),
                dict(sku="P105", title="Headlamp Pro", category="camping", price=59.0),
            ],
            "Order": [
                dict(order_id="O1001", customer_id="C001", sku="P102", region="emea", status="delivered", total=399.0),
                dict(order_id="O1002", customer_id="C001", sku="P104", region="emea", status="shipped", total=89.0),
                dict(order_id="O1003", customer_id="C002", sku="P101", region="apac", status="delivered", total=249.0),
                dict(order_id="O1004", customer_id="C002", sku="P100", region="apac", status="returned", total=129.0),
                dict(order_id="O1005", customer_id="C003", sku="P105", region="emea", status="placed", total=59.0),
                dict(order_id="O1006", customer_id="C004", sku="P103", region="apac", status="delivered", total=35.0),
                dict(order_id="O1007", customer_id="C005", sku="P100", region="emea", status="placed", total=129.0),
                dict(order_id="O1008", customer_id="C006", sku="P104", region="apac", status="shipped", total=89.0),
            ],
        }  # fmt: skip

    def cr_build_models(key_prefix, index_overrides=None):
        """The Cairn data model as ContextModel classes, and what export_data_model makes of them.

        Returns {"classes": {"Customer": ..., "Product": ..., "Order": ...}, "data_model": {...}}.
        The service names its tools after the classes, so they are called exactly Customer, Product
        and Order, and are defined here, where context_surfaces is imported, not at the top of a
        cell where every name carries a column prefix. `index_overrides` maps "Entity.field" to
        {"index": "text" | "tag" | "numeric" | "none", "allowed": bool}; a field it does not name
        keeps the index written below. Every key template starts with key_prefix.
        """
        from typing import Any

        from context_surfaces import (
            ContextField,
            ContextModel,
            ContextRelationship,
            export_data_model,
        )

        picks = index_overrides or {}
        rows = cr_records()

        def field(field_id, description, index="none", allowed=None, **options):
            """A ContextField with the index and menu that `picks` asks for, if it names this field."""
            entity, name = field_id.split(".")
            pick = picks.get(field_id, {})
            index = pick.get("index", index)
            values = None
            if index == "tag" and pick.get("allowed", allowed is not None):
                values = allowed or sorted({row[name] for row in rows[entity]})
            return ContextField(
                description=description,
                index=None if index == "none" else index,
                allowed_values=values,
                **options,
            )

        class Customer(ContextModel):
            """A shopper at Cairn Outfitters, an outdoor-gear shop."""

            __redis_key_template__ = f"{key_prefix}customer:{{customer_id}}"
            customer_id: str = field(
                "Customer.customer_id", "Customer ID such as C001", is_key_component=True
            )
            name: str = field("Customer.name", "Full name", "text", weight=2.0)
            tier: str = field("Customer.tier", "Loyalty tier", "tag", ["bronze", "silver", "gold"])
            region: str = field(
                "Customer.region", "Sales region: emea or apac", "tag", ["emea", "apac"]
            )
            lifetime_value: float = field(
                "Customer.lifetime_value", "Lifetime spend in USD", "numeric", sortable=True
            )
            orders: Any = ContextRelationship(
                description="Orders this customer placed",
                target="Order",
                source_field="customer_id",
            )

        class Product(ContextModel):
            """An item in the Cairn catalogue."""

            __redis_key_template__ = f"{key_prefix}product:{{sku}}"
            sku: str = field(
                "Product.sku", "Stock keeping unit such as P100", is_key_component=True
            )
            title: str = field("Product.title", "Product title", "text", weight=2.0)
            category: str = field("Product.category", "Product category", "tag")
            price: float = field("Product.price", "List price in USD", "numeric", sortable=True)

        class Order(ContextModel):
            """A purchase of one product by one customer."""

            __redis_key_template__ = f"{key_prefix}order:{{order_id}}"
            order_id: str = field("Order.order_id", "Order ID such as O1001", is_key_component=True)
            customer_id: str = field("Order.customer_id", "Customer who placed the order", "tag")
            sku: str = field("Order.sku", "Product ordered", "tag")
            region: str = field(
                "Order.region", "Sales region: emea or apac", "tag", ["emea", "apac"]
            )
            status: str = field(
                "Order.status", "placed, shipped, delivered or returned", "tag",
                ["placed", "shipped", "delivered", "returned"],
            )  # fmt: skip
            total: float = field("Order.total", "Order total in USD", "numeric", sortable=True)
            customer: Any = ContextRelationship(
                description="Buyer", target="Customer", source_field="customer_id"
            )
            product: Any = ContextRelationship(
                description="Item bought", target="Product", source_field="sku"
            )

        classes = {"Customer": Customer, "Product": Product, "Order": Order}
        data_model = export_data_model(
            "Cairn Outfitters",
            "Customers, products and orders of a fictional outdoor-gear shop",
            entities=list(classes.values()),
        )
        return {"classes": classes, "data_model": data_model}

    def cr_model_fields():
        """The fields the picker can change, read off the default model: id, type, index, menu.

        A key field is left out: it is what the get-by-id tool is made from.
        """
        fields = []
        for entity in cr_build_models(CR_PREFIX)["data_model"]["entities"]:
            for found in entity["fields"]:
                if not found["is_key_component"]:
                    indices = [i["type"] for i in found["redis_indices"]]
                    fields.append(
                        {
                            "id": f"{entity['name']}.{found['name']}",
                            "type": found["type"],
                            "index": indices[0] if indices else "none",
                            "allowed": found.get("allowed_values"),
                        }
                    )
        return fields

    def cr_field_state(field_id, edits):
        """What the controls for one field offer and show: {"kinds", "index", "allowed"}.

        A str can be text or tag and a float only numeric: the SDK refuses a tag over a number.
        The controls start at the kept change if there is one, else at the default model.
        """
        field = next(f for f in cr_model_fields() if f["id"] == field_id)
        kept = edits.get(field_id, {})
        return {
            "kinds": ["text", "tag", "none"] if field["type"] == "str" else ["numeric", "none"],
            "index": kept.get("index", field["index"]),
            "allowed": kept.get("allowed", field["allowed"] is not None),
        }

    def cr_index_words(index, allowed):
        """How a field is indexed, in words: "tag with allowed_values", "text", "not indexed"."""
        if index == "none":
            return "not indexed"
        return f"{index} with allowed_values" if allowed and index == "tag" else index

    def cr_changes(picks):
        """What `picks` change in the default model, a line each. A pick that changes nothing is out."""
        lines = []
        for field in cr_model_fields():
            pick = picks.get(field["id"])
            if pick is None:
                continue
            was = cr_index_words(field["index"], field["allowed"] is not None)
            now = cr_index_words(pick["index"], pick["allowed"])
            if was != now:
                lines.append(f"`{field['id']}`: {was} → {now}")
        return lines

    def cr_field_json(data_model, field_id):
        """One field's entry in an exported data model."""
        entity, _, name = field_id.partition(".")
        fields = next(e["fields"] for e in data_model["entities"] if e["name"] == entity)
        return next(f for f in fields if f["name"] == name)

    def cr_unified_tools(data_model):
        """The tools a unified surface makes for a data model: [{"tool", "index", "accepts"}].

        From the surface the service served on 2026-09-30 (21 tools for the default model), not
        from a call. Each entity gives get_<entity>_by_id for its key, list_<entity> and
        count_<entity>; summarize_<entity> if a field is numeric, filter_<entity> if one is a tag
        or numeric and search_<entity>_by_text if one is text. Four set tools come with any
        model. "accepts" says which fields a tool takes, so an index choice shows in it. The
        entity name is lower-cased.
        """

        def join(*parts):
            return " · ".join(part for part in parts if part)

        tools = []
        for entity in data_model["entities"]:
            name, fields = entity["name"].lower(), entity["fields"]

            def indexed(kind):
                return [f for f in fields if any(i["type"] == kind for i in f["redis_indices"])]

            def menu(field):
                allowed = field.get("allowed_values")
                return f"{field['name']} ({', '.join(allowed)})" if allowed else field["name"]

            tags = sorted(f["name"] for f in indexed("tag"))
            numbers = [f["name"] for f in indexed("numeric")]
            texts = [f["name"] for f in indexed("text")]
            key = next(f["name"] for f in fields if f["is_key_component"])
            grouped = f"group by {', '.join(tags)}" if tags else ""
            tools.append({"tool": f"get_{name}_by_id", "index": "key", "accepts": f"id ({key})"})
            tools.append(
                {
                    "tool": f"list_{name}",
                    "index": "numeric" if numbers else "",
                    "accepts": f"sort by {', '.join(numbers)}" if numbers else "no sort field",
                }
            )
            counted = sorted(tags + numbers)
            tools.append(
                {
                    "tool": f"count_{name}",
                    "index": "tag, numeric",
                    "accepts": join(
                        f"count_distinct of {', '.join(counted)}" if counted else "", grouped
                    )
                    or "record counts only",
                }
            )
            if numbers:
                tools.append(
                    {
                        "tool": f"summarize_{name}",
                        "index": "numeric",
                        "accepts": join(f"numeric: {', '.join(numbers)}", grouped),
                    }
                )
            if tags or numbers:
                kinds = (("tag", tags), ("numeric", numbers))
                tagged = [menu(f) for f in indexed("tag")]
                tools.append(
                    {
                        "tool": f"filter_{name}",
                        "index": ", ".join(kind for kind, used in kinds if used),
                        "accepts": join(
                            f"tag: {', '.join(tagged)}" if tags else "",
                            f"numeric: {', '.join(numbers)}" if numbers else "",
                        ),
                    }
                )
            if texts:
                tools.append(
                    {
                        "tool": f"search_{name}_by_text",
                        "index": "text",
                        "accepts": f"text: {', '.join(texts)}",
                    }
                )
        sets = {
            "intersect_results": "the records in every set",
            "union_results": "the records in any set",
            "except_results": "the records of the first set that no other has",
            "expand_results": "a set and a relationship to follow to the related entity",
        }
        tools += [
            {"tool": tool, "index": "any", "accepts": f"tag, numeric or text conditions: {what}"}
            for tool, what in sets.items()
        ]
        return sorted(tools, key=lambda t: t["tool"])

    def cr_predict_tools(data_model):
        """cr_unified_tools with a "vs default" column, and the default's tools this model lost.

        The column says "new" for a tool the default model does not make, "changed" for one that
        accepts other fields or values than in the default, and "gone" on a row for a default tool
        that has disappeared.
        """
        default = {
            t["tool"]: t["accepts"]
            for t in cr_unified_tools(cr_build_models(CR_PREFIX)["data_model"])
        }
        rows = cr_unified_tools(data_model)
        marked = []
        for row in rows:
            was = default.get(row["tool"])
            change = "new" if was is None else "changed" if was != row["accepts"] else ""
            marked.append({**row, "vs default": change})
        now = {row["tool"] for row in rows}
        marked += [
            {"tool": tool, "index": "", "accepts": "", "vs default": "gone"}
            for tool in default
            if tool not in now
        ]
        return sorted(marked, key=lambda row: row["tool"])

    def cr_tool_diff(predicted, actual, stale=False):
        """The prediction against the tool names the service listed: {"rows", "note"}.

        A stale surface was made from an earlier model, which is why it differs, not the service.
        """
        expected = {row["tool"] for row in predicted if row["vs default"] != "gone"}
        real = set(actual)
        rows = [
            {
                "tool": tool,
                "predicted": "yes" if tool in expected else "",
                "made": "yes" if tool in real else "",
            }
            for tool in sorted(expected | real)
        ]
        if expected == real:
            note = f"All **{len(real)}** predicted tools exist, and the service made nothing else."
        elif stale:
            note = (
                f"**{len(expected & real)}** of the {len(expected)} predicted tools exist. The "
                "surface was made from an earlier model, so its tools follow that one."
            )
        else:
            note = (
                f"**{len(expected & real)}** of the {len(expected)} predicted tools exist, and "
                f"**{len(real - expected)}** were not predicted. The prediction uses the unified "
                "naming; an older service has classic tools such as `filter_customer_by_tier` "
                "instead. The tool set depends on your model *and* on the version of the service."
            )
        return {"rows": rows, "note": note}

    def cr_record_keys(data_model):
        """The Redis key of every record, from the key templates.

        These are the names the service writes to, and the ones this notebook expires and unlinks.
        """
        rows = cr_records()
        return [
            entity["redis_key_template"].format(**row)
            for entity in data_model["entities"]
            for row in rows[entity["name"]]
        ]

    def cr_endpoints(api="", mcp=""):
        """The admin API and MCP addresses, without a trailing slash.

        What was typed wins, then CTX_API_URL and CTX_MCP_URL, then Redis Cloud's own.
        """
        api = api.strip() or env("CTX_API_URL") or "https://cloud.redis.io/context-surfaces"
        mcp = (
            mcp.strip()
            or env("CTX_MCP_URL")
            or "https://gcp-us-east4.context-surfaces.redis.io/mcp"
        )
        return api.rstrip("/"), mcp.rstrip("/")

    def cr_headers(admin):
        """The header the admin API wants. The key is masked in every trace by http_call."""
        return {"X-API-Key": admin.reveal()}

    def cr_shown(call):
        """A traced call as it may be displayed: a listing of surfaces keeps only a count.

        The reply lists the whole account, other people's surfaces and data models included, and
        a wire view ends up in the page, the export and the session file.
        """
        listing = call.method == "GET" and urlsplit(call.url).path.endswith(
            "/api/v1/context-surfaces"
        )
        if not (listing and call.status == 200):
            return call
        found = call.response.get("context_surfaces") if isinstance(call.response, dict) else None
        count = len(found) if isinstance(found, list) else "some"
        return call._replace(
            response={"context_surfaces": f"{count} surfaces, not shown: they are your account's"}
        )

    def cr_trace_since(mark):
        """The calls traced after `mark` (an entry of HTTP_TRACE, or None), oldest first, as shown."""
        trace = list(HTTP_TRACE)
        at = next((i for i, call in enumerate(trace) if call is mark), -1)
        return [cr_shown(call) for call in trace[at + 1 :]]

    def cr_list_surfaces(api, admin):
        """Every surface the admin key can see, following the pagination. Read-only.

        It stops after CR_LIST_PAGES pages of CR_LIST_PAGE: an account with more (the live one
        had 500 or more) is listed in part, which cr_describe says.
        """
        found = []
        for page in range(1, CR_LIST_PAGES + 1):
            _, body = http_call(
                "cr", "GET", f"{api}/api/v1/context-surfaces",
                headers=cr_headers(admin), params={"page": page, "page_size": CR_LIST_PAGE},
            )  # fmt: skip
            body = body if isinstance(body, dict) else {}
            found += body.get("context_surfaces") or []
            if not (body.get("pagination") or {}).get("has_next"):
                break
        return found

    def cr_stamped(surfaces):
        """The surfaces this notebook made, cut to what the orphans panel and the guard read.

        Every other surface in the account is somebody else's, so none of it is kept.
        """
        return [
            {field: surface.get(field) for field in ("id", "name", "metadata", "created_at")}
            for surface in surfaces
            if (surface.get("metadata") or {}).get("created_by") == CR_CREATED_BY
        ]

    def cr_redis_facts(client):
        """Read-only facts about the database, or a ServiceError saying how to fix a missing module."""
        try:
            facts = redis_facts(client)
            client.execute_command("JSON.GET", CR_PREFIX + "probe")  # nil when JSON is there
        except redis.ResponseError:
            detail = (
                "this database has no Search or no JSON (FT._LIST or JSON.GET failed). Context "
                "Retriever needs both; Redis Cloud includes them in its 8.x databases, and a free "
                "one is at https://redis.io/try-free/"
            )
            raise ServiceError("cr", None, "config", detail) from None
        return facts

    def cr_probe(admin, url, api, mcp):
        """Test connection, read-only: list the surfaces the admin key sees, and reach the database.

        Returns what the cells below use: both secrets, the addresses, how many surfaces the account
        has and the ones this notebook stamped, the Redis client with its facts, and the requests
        made (for the wire view).
        """
        api, mcp = cr_endpoints(api, mcp)
        mark = HTTP_TRACE[-1] if HTTP_TRACE else None
        surfaces = cr_list_surfaces(api, admin)
        client = connect_redis(url.reveal())
        return {
            "admin": admin, "url": url, "api": api, "mcp": mcp, "listed": len(surfaces),
            "surfaces": cr_stamped(surfaces), "redis": client, "facts": cr_redis_facts(client),
            "calls": cr_trace_since(mark),
        }  # fmt: skip

    def cr_failed(exc):
        """service_failed with Context Retriever's own advice."""
        return service_failed(
            exc,
            {
                401: "The key was refused. Paste `CTX_ADMIN_KEY` again, or make a new admin key "
                f"on the *Admin keys* tab. An agent key expires {CR_KEY_TTL_S // 3600} h after "
                "Create.",
                404: "Not found: check the Admin API address, which answers 404 to everything when "
                "it is wrong, or the surface has been deleted.",
            },
        )

    def cr_describe(conn):
        """What Test connection found, as a callout (plus a warning if the database looks shared)."""
        facts = conn["facts"]
        listed = conn["listed"]
        seen = f"{listed} or more" if listed >= CR_LIST_PAGE * CR_LIST_PAGES else listed
        found = [
            f"**Connected.** The admin key sees **{seen}** surfaces at "
            f"`{urlsplit(conn['api']).netloc}`.",
            f"- database `{facts['where']}`: Redis **{facts['version']}** with Search and JSON · "
            f"**{facts['dbsize']}** keys · **{len(facts['indexes'])}** indexes · "
            f"**{facts['clients']}** client connections (the free tier allows 30)",
        ]
        return mo.vstack(
            [
                status_callout("ok", "\n".join(found)),
                shared_db_warning(
                    facts,
                    "The hosted service will be given this user's password, and with it full "
                    "rights on the whole database. This notebook has it write under "
                    f"`{CR_PREFIX}` and drop only its own surface, keys and indexes, but nothing "
                    "stops the service itself. If other apps use this database, point the column "
                    "at a scratch one. (A Redis Cloud data-access role could narrow the user; "
                    "whether Context Retriever works under one is not verified.)",
                ),
                wire_view("The request, as HTTP", conn["calls"]),
            ]
        )

    def cr_orphans(surfaces):
        """The surfaces other runs of this notebook left behind, from the account's list.

        {"rows": table rows for all of them, "stale": those to offer for deletion}. A surface is
        offered when it is old enough and its stamp is one approve_cr_orphan will accept (a run of
        8 hex digits, and the name iris-basics-<run>): approve_cr_orphan refuses a whole batch for
        one it does not accept. It checks again before the choke point lets any go; this only
        decides what to show. Surfaces this notebook did not stamp are not even listed.
        """
        rows, stale = [], []
        for surface in surfaces:
            meta = surface.get("metadata") or {}
            run = meta.get("run")
            if meta.get("created_by") != CR_CREATED_BY or run in (None, RUN):
                continue
            named = (
                isinstance(run, str)
                and re.fullmatch(r"[0-9a-f]{8}", run) is not None
                and surface.get("name") == f"iris-basics-{run}"
            )
            age = age_hours(surface.get("created_at"))
            if not named:
                offered = "no, the stamp is not recognised"
            elif age is None or age <= ORPHAN_AGE_H:
                offered = f"no, not yet {ORPHAN_AGE_H} h old"
            else:
                offered = "yes"
                stale.append(surface)
            rows.append(
                {
                    "surface": surface.get("id"),
                    "name": surface.get("name"),
                    "age": "unknown" if age is None else f"{age:.1f} h",
                    "offered": offered,
                }
            )
        return {"rows": rows, "stale": stale}

    def cr_delete_orphans(conn, stale, confirmation):
        """Delete other runs' surfaces once `confirmation` is typed; returns their ids.

        All are approved before any is deleted, so one that is not a stale surface of this
        notebook's stops the lot. 404 counts as gone.
        """
        for surface in stale:
            approve_cr_orphan(surface, confirmation)
        for surface in stale:
            http_call(
                "cr", "DELETE", f"{conn['api']}/api/v1/context-surfaces/{surface['id']}",
                headers=cr_headers(conn["admin"]), timeout=T_WRITE, expect=(200, 204, 404),
            )  # fmt: skip
        return [surface["id"] for surface in stale]

    def cr_connection_config(url):
        """data_source.connection_config for a redis:// or rediss:// URL.

        urlsplit leaves the userinfo percent-encoded, and the service must get what redis-py would
        send, so it is decoded. The pool is small because the free tier allows 30 connections.
        """
        parts = urlsplit(url)
        return {
            "addr": f"{parts.hostname}:{parts.port or 6379}",
            "username": unquote(parts.username or "") or "default",
            "password": unquote(parts.password or ""),
            "db": int(parts.path.lstrip("/") or 0),
            "tls_enabled": parts.scheme == "rediss",
            "pool_size": 2,
            "min_idle_conns": 0,
        }

    def cr_reply_value(body, name, what):
        """body[name] from a service reply, or a ServiceError saying what came back instead.

        The create, agent-key and import reply shapes were confirmed live on 2026-09-30. The check
        stays so that a service that changes them gives a message, not a KeyError.
        """
        if isinstance(body, dict) and body.get(name):
            return body[name]
        found = ", ".join(sorted(map(str, body))) if isinstance(body, dict) else type(body).__name__
        detail = f"the reply to {what} has no {name!r}; it has: {found}"
        raise ServiceError("cr", None, "shape", detail)

    def cr_adopt(api, admin):
        """This run's surfaces as the service lists them, learned into the ledger.

        A surface is this run's if it carries the whole stamp the create request gave it: the
        name, and the run and created_by in its metadata. Ledgering it is what makes it deletable.
        It is how a create whose reply never came back is found again, and how pressing Create
        twice reuses the surface instead of making another.
        """
        ours = [
            surface
            for surface in cr_list_surfaces(api, admin)
            if surface.get("name") == RUN_TAG
            and (surface.get("metadata") or {}).get("run") == RUN
            and (surface.get("metadata") or {}).get("created_by") == CR_CREATED_BY
        ]
        for surface in ours:
            ident = cr_reply_value(surface, "id", "the list of surfaces")
            if ident not in ledger_live("cr", "surface"):
                ledger_add("cr", "surface", ident)
        return ours

    def cr_settle():
        """Close the ledger's intent line, written under the surface's name before its id existed."""
        if RUN_TAG in ledger_live("cr", "surface", pending=True):
            ledger_add("cr", "surface", RUN_TAG, "deleted")

    def cr_mint_key(api, admin, surface):
        """An agent key for the surface that expires in CR_KEY_TTL_S seconds: (its id, the key)."""
        expires = datetime.now(timezone.utc) + timedelta(seconds=CR_KEY_TTL_S)
        _, body = http_call(
            "cr", "POST", f"{api}/api/v1/context-surfaces/{surface}/agent-keys",
            headers=cr_headers(admin),
            json={"name": f"{RUN_TAG}-agent", "expires_at": expires.isoformat(timespec="seconds")},
            timeout=T_WRITE, expect=(201,),
        )  # fmt: skip
        key_id = cr_reply_value(body, "id", "minting an agent key")
        ledger_add("cr", "agent_key", key_id)
        key = cr_reply_value(body, "key", "minting an agent key")
        return key_id, Secret(key)  # registered for scrubbing, whichever way it arrived

    def cr_agent_endpoint(api, admin, surface, key_id, fallback):
        """Where the agent key talks MCP: (address, where that came from).

        The service names it in its listing of the surface's agent keys; failing that, the MCP
        setting is used.
        """
        _, body = http_call(
            "cr", "GET", f"{api}/api/v1/context-surfaces/{surface}/agent-keys",
            headers=cr_headers(admin), params={"page_size": 50},
        )  # fmt: skip
        listed = body.get("agent_keys") if isinstance(body, dict) else None
        for item in listed or []:
            if item.get("id") == key_id and item.get("mcp_url"):
                return item["mcp_url"], "the service's list of agent keys"
        return fallback, "the MCP endpoint setting"

    def cr_import(api, admin, surface, classes):
        """POST the records, one entity per call, stopping at the first failure: {entity: imported}.

        Each record goes through its ContextModel class first, so a field the model does not know
        is dropped here and never reaches the wire unnoticed.
        """
        done = {}
        for entity, rows in cr_records().items():
            records = [classes[entity](**row).model_dump() for row in rows]
            _, body = http_call(
                "cr", "POST", f"{api}/api/v1/context-surfaces/{surface}/data",
                headers=cr_headers(admin),
                json={
                    "entity": entity,
                    "records": records,
                    "options": {"on_conflict": "overwrite", "on_error": "fail_fast"},
                },
                timeout=T_WRITE,
            )  # fmt: skip
            body = body if isinstance(body, dict) else {}
            if body.get("failed"):
                raise ValueError(
                    f"the service could not import {body['failed']} {entity} records: "
                    f"{body.get('errors')}"
                )
            done[entity] = body.get("imported", len(records))
        return done

    def cr_expire_keys(client, keys):
        """EXPIRE every key by name, so a forgotten demo does not linger. Returns how many existed."""
        expired = 0
        for key in keys:
            expired += client.execute_command("EXPIRE", key, CR_KEY_TTL_S)
            ledger_add("cr", "key", key)
        return expired

    def cr_claim(info):
        """True, and the guard is told, if an FT.INFO reply shows an index wholly under CR_PREFIX.

        CR_PREFIX carries this run's id. claim_index alone would also take column 0's indexes.
        """
        return owned_index(info, CR_PREFIX) and claim_index(info)

    def cr_learn_indexes(client):
        """Every index there is, this run's learned into the ledger: [{"name", "prefixes", "ours"}].

        The service names its own indexes, so the ledger cannot know them beforehand. One is
        this run's, and so ledgered for Clean up to drop, if every prefix it covers is under
        CR_PREFIX (cr_claim). Judged by what the database says, not by a snapshot from before
        the create, it is found again after a reused surface, a lost reply or a failed import.
        The caller reports any other that is new. None of those is touched.
        """
        known = set(ledger_live("cr", "index", pending=True))
        found = []
        for name in sorted(decode_reply(client.execute_command("FT._LIST"))):
            try:
                info = client.execute_command("FT.INFO", name)
            except redis.ResponseError as exc:
                if not is_unknown_index(exc):
                    raise
                continue  # dropped since it was listed
            ours = cr_claim(info)
            if ours and name not in known:
                ledger_add("cr", "index", name)
            definition = reply_map(reply_map(decode_reply(info)).get("index_definition"))
            prefixes = definition.get("prefixes") or []
            found.append(
                {
                    "name": name,
                    "prefixes": [prefixes] if isinstance(prefixes, str) else prefixes,
                    "ours": ours,
                }
            )
        return found

    def cr_signature(data_model):
        """What a data model says about indexing: {"Entity.field": (index types, allowed values)}.

        Only that is compared, so whatever else the service adds when it echoes a model is ignored.
        """
        return {
            f"{entity.get('name')}.{found.get('name')}": (
                sorted(str(i.get("type")).lower() for i in found.get("redis_indices") or []),
                sorted(found.get("allowed_values") or []),
            )
            for entity in (data_model or {}).get("entities") or []
            for found in entity.get("fields") or []
        }

    def cr_launch(conn, model):
        """Create the surface and an agent key, load the records, and set the keys to expire.

        The surface (by its name) and every key are ledgered before the request that makes them,
        the agent key and the indexes as soon as they exist, so Clean up finds them whatever fails
        below. What the import wrote is set to expire, and its indexes learned, even if it fails.
        If the service already has this run's surface it is reused, not made again, and "stale"
        says whether it was made from another model than this. Returns the session facts the cells
        below work from.
        """
        client, api, admin = conn["redis"], conn["api"], conn["admin"]
        data_model = model["data_model"]
        keys = cr_record_keys(data_model)
        if not all(key.startswith(CR_PREFIX) for key in keys):
            raise ValueError(f"every key must start with {CR_PREFIX}, and these do not")
        mark = HTTP_TRACE[-1] if HTTP_TRACE else None
        before = set(decode_reply(client.execute_command("FT._LIST")))
        ours = cr_adopt(api, admin)
        if ours:
            surface_id = ours[0]["id"]
            _, surface = http_call(
                "cr",
                "GET",
                f"{api}/api/v1/context-surfaces/{surface_id}",
                headers=cr_headers(admin),
            )
            surface = surface if isinstance(surface, dict) else {}
            made_from = cr_signature(surface.get("data_model"))  # empty if the reply omits it
            stale = bool(made_from) and made_from != cr_signature(data_model)
        else:
            ledger_add("cr", "surface", RUN_TAG, "intent")  # the name: the id does not exist yet
            _, surface = http_call(
                "cr", "POST", f"{api}/api/v1/context-surfaces",
                headers=cr_headers(admin),
                json={
                    "name": RUN_TAG,
                    "description": "Made by the Redis Iris basics notebook. Safe to delete.",
                    "metadata": {"created_by": CR_CREATED_BY, "run": RUN},
                    "data_model": data_model,
                    "data_source": {
                        "type": "redis",
                        "connection_config": cr_connection_config(conn["url"].reveal()),
                    },
                },
                timeout=T_WRITE, expect=(201,),
            )  # fmt: skip
            surface_id = cr_reply_value(surface, "id", "creating the surface")
            ledger_add("cr", "surface", surface_id)
            stale = False
        cr_settle()
        key_id, agent = cr_mint_key(api, admin, surface_id)
        mcp_url, mcp_from = cr_agent_endpoint(api, admin, surface_id, key_id, conn["mcp"])
        for key in keys:
            ledger_add("cr", "key", key, "intent")
        try:
            imported = cr_import(api, admin, surface_id, model["classes"])
        finally:  # a failed or timed-out import may have written some documents and built indexes
            expired = cr_expire_keys(client, keys)
            indexes = cr_learn_indexes(client)
        return {
            "surface": surface_id, "reused": bool(ours), "stale": stale,
            "status": surface.get("status"), "agent": agent, "agent_id": key_id,
            "mcp_url": mcp_url, "mcp_from": mcp_from, "keys": keys, "imported": imported,
            "expired": expired,
            "indexes": [row for row in indexes if row["ours"] or row["name"] not in before],
            "calls": [c for c in cr_trace_since(mark) if c.component == "cr"],
        }  # fmt: skip

    def cr_launched(session):
        """The callouts and the wire view for a finished cr_launch."""
        hours = CR_KEY_TTL_S // 3600
        ours = [i for i in session["indexes"] if i["ours"]]
        others = [i["name"] for i in session["indexes"] if not i["ours"]]
        loaded = ", ".join(f"{n} {entity}" for entity, n in session["imported"].items())
        status = f", status `{session['status']}`" if session["status"] else ""
        found = [
            f"**{'Picked up' if session['reused'] else 'Created'}** the surface `{RUN_TAG}` "
            f"(id `{session['surface']}`{status}) and an agent key {session['agent']} that expires "
            f"in {hours} h.",
            f"- loaded **{sum(session['imported'].values())}** records ({loaded})",
            f"- **{session['expired']}** of {len(session['keys'])} keys now expire in {hours} h",
            f"- MCP endpoint `{session['mcp_url']}`, from {session['mcp_from']}",
            f"- the service built **{len(ours)}** indexes: "
            + (", ".join(f"`{i['name']}`" for i in ours) or "none"),
        ]
        parts = [status_callout("ok", "\n".join(found))]
        if session["stale"]:
            parts.append(
                status_callout(
                    "warn",
                    "**This surface was made from an earlier model.** Create picks it up again, so "
                    "its tools follow that model, not the one above. **Clean up**, then Create, "
                    "to make one from this model.",
                )
            )
        if session["expired"] < len(session["keys"]):
            parts.append(
                status_callout(
                    "warn",
                    "Some keys were not there to expire, so the service does not key its documents "
                    "the way the template says. What is really there is shown below.",
                )
            )
        if others:
            parts.append(
                status_callout(
                    "warn",
                    f"An index appeared meanwhile that is not under `{CR_PREFIX}`: "
                    + ", ".join(f"`{name}`" for name in others)
                    + ". It is left alone.",
                )
            )
        parts.append(wire_view("The requests, as HTTP", session["calls"]))
        return mo.vstack(parts)

    def cr_error_text(error):
        """A JSON-RPC error object as one line: its code and its message, verbatim."""
        error = error if isinstance(error, dict) else {}
        return f"{error.get('code')}: {error.get('message')}"

    def cr_rpc(session, method, params, ident):
        """One JSON-RPC 2.0 call to the surface's MCP endpoint with the agent key: (reply, traced call).

        A tool-level failure is a normal reply with result.isError; only a protocol error has an
        error object. Either way the reply is handed back for the caller to read.
        """
        _, body = http_call(
            "cr", "POST", session["mcp_url"],
            headers={"X-API-Key": session["agent"].reveal()},
            json={"jsonrpc": "2.0", "id": ident, "method": method, "params": params},
            expect=(200, 400),
        )  # fmt: skip
        if not (
            isinstance(body, dict)
            and any(isinstance(body.get(part), dict) for part in ("result", "error"))
        ):
            raise ServiceError(
                "cr", None, "http", f"the MCP endpoint did not answer in JSON-RPC: {body!r:.200}"
            )
        return body, HTTP_TRACE[-1]

    def cr_list_tools(session):
        """The surface's tools as its MCP endpoint lists them, by name, and the traced call."""
        body, call = cr_rpc(session, "tools/list", {}, 1)
        if "error" in body:
            raise ServiceError("cr", None, "http", cr_error_text(body["error"]))
        tools = body["result"].get("tools") or []
        if not (
            isinstance(tools, list) and all(isinstance(t, dict) and t.get("name") for t in tools)
        ):
            raise ServiceError(
                "cr", None, "shape", "tools/list did not return a list of named tools"
            )
        return sorted(tools, key=lambda tool: tool["name"]), call

    def cr_call_tool(session, name, arguments):
        """Call a tool: {"tool", "arguments", "outcome", "code", "message", "data", "call"}.

        outcome is "ok", "tool_error" (the reply says isError) or "rejected" (a JSON-RPC error:
        the arguments broke the schema). The message is the service's own words. data is the
        reply's JSON, or {"raw_text": ...} when the text is not JSON.
        """
        body, call = cr_rpc(session, "tools/call", {"name": name, "arguments": arguments}, 2)
        found = {
            "tool": name,
            "arguments": arguments,
            "code": None,
            "message": "",
            "data": None,
            "call": call,
        }
        if isinstance(body.get("error"), dict):
            error = body["error"]
            return found | {
                "outcome": "rejected",
                "code": error.get("code"),
                "message": str(error.get("message")),
            }
        result = body.get("result") or {}
        text = "".join(
            p.get("text", "") for p in result.get("content") or [] if isinstance(p, dict)
        )
        if result.get("isError"):
            return found | {"outcome": "tool_error", "message": text}
        try:
            data = json.loads(text)
        except ValueError:
            data = {"raw_text": text}
        return found | {"outcome": "ok", "data": data}

    def cr_row(record):
        """A record as a table row: nested values become JSON text."""
        if not isinstance(record, dict):
            return {"value": record}
        plain = (str, int, float, bool, type(None))
        return {k: v if isinstance(v, plain) else json.dumps(v) for k, v in record.items()}

    def cr_data_view(data):
        """A tool's JSON reply as the most natural view.

        A table of its results, groups or ids (with total_count, has_more and the _retrieval stamp
        when it has them), a count as a sentence, or the reply itself as a one-row table.
        """
        if not isinstance(data, dict):
            return mo.md(code_block(json.dumps(data, indent=2)))
        if data.get("function") and "value" in data:
            return mo.md(f"`{data['function']}` = **{data['value']}**")
        rows = next((data[k] for k in ("results", "groups") if isinstance(data.get(k), list)), None)
        if rows is None:
            rows = [{"id": i} for i in data["ids"]] if isinstance(data.get("ids"), list) else [data]
        parts = []
        if "total_count" in data:
            parts.append(
                mo.hstack(
                    [
                        mo.stat(data["total_count"], label="total_count"),
                        mo.stat(data.get("count", len(rows)), label="returned"),
                        mo.stat(str(data.get("has_more")).lower(), label="has_more"),
                    ],
                    justify="start",
                    wrap=True,
                )
            )
        parts.append(plain_table([cr_row(row) for row in rows]) if rows else mo.md("_No rows._"))
        if isinstance(data.get("results"), list):
            stamp = next(
                (r["_retrieval"] for r in rows if isinstance(r, dict) and "_retrieval" in r), None
            )
            parts.append(
                mo.md(
                    f"`_retrieval`: `{json.dumps(stamp)}`"
                    if stamp
                    else "_These records carry no `_retrieval` stamp._"
                )
            )
        return mo.vstack(parts)

    def cr_outcome_view(result, broke=False):
        """A tool call's outcome with the JSON-RPC under it.

        A rejection (the service refused the arguments) and a tool error (isError) are red, with
        the service's code and message verbatim. For Break it, being accepted is the surprise.
        """
        head = mo.md(f"`tools/call {result['tool']}` with `{json.dumps(result['arguments'])}`")
        if result["outcome"] == "rejected":
            body = status_callout(
                "error",
                f"**Rejected**, code `{result['code']}`:\n\n" + code_block(result["message"]),
            )
        elif result["outcome"] == "tool_error":
            body = status_callout(
                "error",
                "**The tool ran and reported an error** (`isError`):\n\n"
                + code_block(result["message"]),
            )
        elif broke:
            body = mo.vstack(
                [
                    status_callout(
                        "warn", "**The service accepted it.** Nothing refused this call."
                    ),
                    cr_data_view(result["data"]),
                ]
            )
        else:
            body = cr_data_view(result["data"])
        return mo.vstack(
            [head, body, wire_view("The request and the reply, as JSON-RPC", result["call"])]
        )

    def cr_look(client, session):
        """Read-only look at what the service wrote: {"key", "doc", "indexes", "commands"}.

        doc is the first record's JSON, pretty-printed, or None if nothing is at its key; indexes
        are table rows for the ones this run owns; commands are the redis-cli lines that were sent.
        """
        key = session["keys"][0]
        text = decode_reply(client.execute_command("JSON.GET", key))
        doc = json.dumps(json.loads(text), indent=2) if text else None
        commands, rows = [redis_cli(["JSON.GET", key])], []
        for index in session["indexes"]:
            if not index["ours"]:
                continue
            info = reply_map(decode_reply(client.execute_command("FT.INFO", index["name"])))
            # An attribute is a flat list that may end in flags (SORTABLE), so only its first three
            # pairs are read: identifier, attribute (the field's name) and type.
            fields = [reply_map(a[:6]) for a in info.get("attributes") or []]
            rows.append(
                {
                    "index": index["name"],
                    "over": ", ".join(index["prefixes"]),
                    "documents": info.get("num_docs"),
                    "fields": ", ".join(f"{a.get('attribute')} {a.get('type')}" for a in fields),
                }
            )
            commands.append(redis_cli(["FT.INFO", index["name"]]))
        return {"key": key, "doc": doc, "indexes": rows, "commands": commands}

    def cr_match_most(schema):
        """A filter tool's conditions that nearly every record meets: {property: [condition]}.

        The unified filter tools refuse a call with no condition ("at least one condition is
        required"), so a paging demo must send one. It is read off the tool's inputSchema: an array
        property whose items pick a `field` from an enum is a list of conditions. A numeric one
        (its items take min_value) asks for 0 and up. A tag one asks for the first choice of the
        value's enum if it has one, else, if items take `exclude`, for anything but an example
        value, else for the example itself. A numeric condition wins, since it keeps every record.
        Empty if the tool takes no such property.
        """
        numeric, tagged = {}, {}
        for name, prop in (schema.get("properties") or {}).items():
            item = prop.get("items") or {}
            props = item.get("properties") or {}
            fields = (props.get("field") or {}).get("enum")
            if prop.get("type") != "array" or not fields:
                continue
            if "min_value" in props:
                numeric[name] = [{"field": fields[0], "min_value": 0}]
            elif "value" in props:
                value = props["value"]
                condition = {"field": fields[0], "value": cr_example(value)}
                if "enum" not in value and "exclude" in props:
                    condition["exclude"] = True
                tagged[name] = [condition]
        return numeric or tagged

    def cr_pageable(catalog):
        """The filter tools that page and can be called with nothing typed: {tool name: arguments}.

        A tool qualifies when it takes limit and offset and each argument it requires offers a menu
        (an enum) to take the first choice from. The arguments are those choices, and, for a tool
        that takes conditions, one that nearly every record meets (cr_match_most).
        """
        found = {}
        for tool in catalog:
            schema = tool.get("inputSchema") or {}
            props, required = schema.get("properties") or {}, schema.get("required") or []
            if (
                tool["name"].startswith("filter_")
                and {"limit", "offset"} <= props.keys()
                and all("enum" in (props.get(name) or {}) for name in required)
            ):
                menus = {name: props[name]["enum"][0] for name in required}
                found[tool["name"]] = {**menus, **cr_match_most(schema)}
        return found

    def cr_kind(schema):
        """The JSON Schema type of a property; ["integer", "null"] counts as integer."""
        kind = schema.get("type")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), None)
        return kind

    def cr_example(schema):
        """A plausible value for a JSON Schema fragment, for a form to start from.

        The default, else for an enum its first choice, else something of the right type. An array
        holds minItems copies (at least one) and an object a value for every property. A string is
        the id its description gives ("such as C001"), else C001.
        """
        default = schema.get("default")
        if "enum" in schema:
            return default if default in schema["enum"] else schema["enum"][0]
        if default not in (None, ""):
            return default
        kind = cr_kind(schema)
        if kind == "object":
            return {
                name: cr_example(prop) for name, prop in (schema.get("properties") or {}).items()
            }
        if kind == "array":
            return [
                cr_example(schema.get("items") or {})
                for _ in range(max(schema.get("minItems", 1), 1))
            ]
        if kind == "boolean":
            return False
        if kind in ("integer", "number"):
            return schema.get("minimum", 0)
        hint = re.search(r"such as (\w+)", schema.get("description") or "")
        return hint[1] if hint else "C001"

    def cr_widget(name, schema, required):
        """The form control for one tool argument, made from its JSON Schema.

        An enum becomes a dropdown, a boolean a checkbox, a number a number box, an array or an
        object a JSON box, and anything else a text box. A required argument, or one with a
        default, starts at an example, so a form can always be run as it stands. Any other starts
        empty (a text box shows the example as a hint): an empty one is left out of the call, so
        the service is never sent a value nobody chose.
        """
        label = f"{name} *" if required else name
        unset = not required and "default" not in schema
        example, kind = cr_example(schema), cr_kind(schema)
        if "enum" in schema:
            choices = {str(choice): choice for choice in schema["enum"]}
            return mo.ui.dropdown(choices, value=None if unset else str(example), label=label)
        if kind == "boolean":
            return mo.ui.checkbox(value=bool(example), label=label)
        if kind in ("integer", "number"):
            step = 1 if kind == "integer" else None
            if unset:  # a number box with a minimum cannot be empty, so this one has no bounds
                return mo.ui.number(step=step, value=None, label=label)
            return mo.ui.number(
                start=schema.get("minimum"),
                stop=schema.get("maximum"),
                step=step,
                value=example,
                label=label,
            )
        if kind in ("array", "object"):
            hint = json.dumps(example, indent=2)
            return mo.ui.text_area(
                value="" if unset else hint, placeholder=hint, label=label, full_width=True
            )
        return mo.ui.text(
            value="" if unset else str(example),
            placeholder=str(example),
            label=label,
            full_width=True,
        )

    def cr_arguments(schema, values):
        """The tool arguments a filled-in form stands for.

        Blank optional text, cleared numbers and menus left unchosen are left out. The JSON boxes
        are parsed, and a ValueError names the one that is not JSON.
        """
        required = schema.get("required") or []
        arguments = {}
        for name, prop in (schema.get("properties") or {}).items():
            value, kind = values.get(name), cr_kind(prop)
            if value is None or (
                isinstance(value, str) and not value.strip() and name not in required
            ):
                continue
            if kind in ("array", "object"):
                try:
                    value = json.loads(value)
                except ValueError as exc:
                    raise ValueError(f"{name} is not valid JSON: {exc}") from None
            elif kind == "integer":
                value = int(value)
            arguments[name] = value
        return arguments

    def cr_enums(schema, path=()):
        """Every (path, choices) where a schema limits a value to an enum, however deep.

        It looks through properties and array items. path locates the value inside an example of
        the schema: property names, and 0 for the first item of an array.
        """
        if path and "enum" in schema:
            yield path, schema["enum"]
        for name, prop in (schema.get("properties") or {}).items():
            yield from cr_enums(prop, (*path, name))
        if isinstance(schema.get("items"), dict):
            yield from cr_enums(schema["items"], (*path, 0))

    def cr_ruled(schema):
        """The argument names that the if/then/else rules of a schema mention.

        The live count_ tools say "if function is count, field must be absent". A value outside
        the menu of `field` breaks that rule first, and the service words the rejection as the
        rule ("validating /then: not: ..."), not as a menu. Break it prefers an argument that no
        rule mentions, so the rejection it shows is about the menu.
        """
        names = set()

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "required" and isinstance(value, list):
                        names.update(name for name in value if isinstance(name, str))
                    elif key == "properties" and isinstance(value, dict):
                        names.update(value)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk([schema.get(keyword) for keyword in ("if", "then", "else")])
        return names

    def cr_example_args(tool):
        """Example values for the arguments a tool requires."""
        schema = tool.get("inputSchema") or {}
        props = schema.get("properties") or {}
        return {name: cr_example(props.get(name) or {}) for name in schema.get("required") or []}

    def cr_break_plan(catalog, selected):
        """A call the surface should refuse, worked out from the schemas: {"tool", "arguments", "how"}.

        First choice is a value outside an enum, wherever a tool's inputSchema has one (in its
        properties or the items of an array), the selected tool first, and an argument that no
        if/then/else rule mentions before one that does (cr_ruled). Failing that, an argument
        the tool does not declare, sent to a tool whose schema says additionalProperties is false.
        None if the schemas offer neither, and the button is not shown.
        """
        ordered = sorted(catalog, key=lambda tool: tool["name"] != selected)  # selected first
        for tool in ordered:
            schema = tool.get("inputSchema") or {}
            ruled = cr_ruled(schema)
            for path, choices in sorted(cr_enums(schema), key=lambda found: found[0][0] in ruled):
                arguments = cr_example_args(tool)
                arguments.setdefault(path[0], cr_example(schema["properties"][path[0]]))
                target = arguments
                for step in path[:-1]:
                    target = target[step]
                target[path[-1]] = "not-a-choice"
                where = ".".join(str(step) for step in path)
                return {
                    "tool": tool["name"],
                    "arguments": arguments,
                    "how": f"send `not-a-choice` for `{where}`, which is limited to {len(choices)} "
                    f"choices, to `{tool['name']}`",
                }
        for tool in ordered:
            if (tool.get("inputSchema") or {}).get("additionalProperties") is False:
                return {
                    "tool": tool["name"],
                    "arguments": {**cr_example_args(tool), "not_an_argument": "x"},
                    "how": f"send an argument `not_an_argument` that `{tool['name']}` does not declare",
                }
        return None

    def cr_catalogue_rows(catalog):
        """One table row per tool: its name, description and arguments (required ones starred)."""
        rows = []
        for tool in catalog:
            schema = tool.get("inputSchema") or {}
            required = schema.get("required") or []
            arguments = [
                f"{name}{'*' if name in required else ''}"
                for name in schema.get("properties") or {}
            ]
            rows.append(
                {
                    "tool": tool["name"],
                    "description": tool.get("description") or "",
                    "arguments": ", ".join(arguments),
                }
            )
        return rows

    def cr_drop_indexes(client):
        """Drop this run's indexes, never with DD: {"indexes", "dropped", "left"}.

        The ledger's are joined by any the service built that it does not know yet
        (cr_learn_indexes). Each is checked with FT.INFO first and dropped only if every prefix
        it covers is under CR_PREFIX (cr_claim); one that fails that is reported in "left", not
        touched. An index that is already gone counts as done, and is counted apart ("gone"):
        the service drops the indexes it built together with the surface, so after Clean up has
        deleted it they usually all are.
        """
        cr_learn_indexes(client)
        names = ledger_live("cr", "index", pending=True)
        dropped, gone, left = 0, 0, []
        for name in names:
            try:
                info = client.execute_command("FT.INFO", name)
            except redis.ResponseError as exc:
                if not is_unknown_index(exc):
                    raise
                ledger_add("cr", "index", name, "deleted")
                gone += 1
                continue
            if not cr_claim(info):
                left.append(name)
                continue
            try:
                client.execute_command("FT.DROPINDEX", name)
                dropped += 1
            except redis.ResponseError as exc:
                if not is_unknown_index(exc):
                    raise
                gone += 1  # dropped since the FT.INFO above
            ledger_add("cr", "index", name, "deleted")
        return {"indexes": len(names), "dropped": dropped, "gone": gone, "left": left}

    def cr_confirm_gone(api, admin, surface):
        """Raise unless the service's own listing shows that `surface` is gone.

        404 to a DELETE is also what a wrong Admin API address answers to everything, and closing
        the ledger line on that would leave a live surface, holding the database password,
        unrecorded. A listing that works proves the address, and the id missing from it proves the
        surface is gone.
        """
        where = urlsplit(api).netloc
        try:
            listed = {found.get("id") for found in cr_list_surfaces(api, admin)}
        except ServiceError as exc:
            raise ServiceError(
                "cr", exc.status, "http",
                f"{surface} was not found at {where}, and its listing fails too ({exc.detail}): "
                "check the Admin API address",
            ) from None  # fmt: skip
        if surface in listed:
            raise ServiceError(
                "cr", 404, "http", f"{where} answers 404 to deleting {surface} but still lists it"
            )

    def cr_cleanup(admin, api, url):
        """Delete this run's surface, UNLINK its keys, drop the indexes the service built; the counts.

        In that order, and read from the ledger on disk now, so a fresh kernel with this run id can
        clean up too. A create whose reply never came back is found by the name we chose. Keys are
        only those the ledger names. Indexes are the ledger's and any other under this run's prefix,
        once the run has made a surface. Deleting a surface leaves its data behind, hence the keys,
        and the live service drops the indexes it built together with the surface (they count as
        "gone"); any it leaves are dropped here. The surface needs only the admin key, so it goes
        before the database is even contacted: if that fails, "stopped" says why, nothing is undone
        and the ledger keeps the rest for the next press. Anything already gone counts as done.
        """
        if RUN_TAG in ledger_live("cr", "surface", pending=True):
            cr_adopt(api, admin)
            cr_settle()
        surfaces = ledger_live("cr", "surface", pending=True)
        deleted = 0
        for surface in surfaces:
            status, _ = http_call(
                "cr", "DELETE", f"{api}/api/v1/context-surfaces/{surface}",
                headers=cr_headers(admin), timeout=T_WRITE, expect=(200, 204, 404),
            )  # fmt: skip
            if status == 404:
                cr_confirm_gone(api, admin, surface)
            ledger_add("cr", "surface", surface, "deleted")
            deleted += status != 404
        for key_id in ledger_live("cr", "agent_key", pending=True):
            ledger_add("cr", "agent_key", key_id, "deleted")  # revoked with the surface
        keys = ledger_live("cr", "key", pending=True)
        done = {
            "surfaces": len(surfaces), "deleted": deleted, "keys": len(keys), "removed": 0,
            "indexes": len(ledger_live("cr", "index", pending=True)), "dropped": 0, "gone": 0,
            "left": [], "stopped": "",
        }  # fmt: skip
        if not ledger_state("cr", "surface"):
            return done  # this run has made no surface, so the database holds nothing of its
        try:
            client = connect_redis(url.reveal())
            if keys:
                done["removed"] = client.execute_command("UNLINK", *keys)
                for key in keys:
                    ledger_add("cr", "key", key, "deleted")
            done |= cr_drop_indexes(client)
        except (ServiceError, redis.RedisError) as exc:
            done["stopped"] = str(exc)
        return done

    def cr_cleanup_report(done):
        """The counts from cr_cleanup, in a sentence, and what was left behind or not reached."""
        if not (done["surfaces"] or done["keys"] or done["indexes"] or done["stopped"]):
            return "Nothing to clean up: this run has created nothing, or it is already cleaned up."
        report = (
            f"Deleted **{done['deleted']}** of {done['surfaces']} surfaces, unlinked "
            f"**{done['removed']}** of {done['keys']} keys and dropped **{done['dropped']}** of "
            f"{done['indexes']} indexes."
        )
        if done["gone"]:
            report += (
                f" **{done['gone']}** of the indexes were already gone: the service drops the "
                "indexes it built together with the surface, so that is expected."
            )
        if done["stopped"]:
            report += (
                f" **The database steps stopped:** {done['stopped']}. Nothing was undone, and the "
                "ledger still names the keys and indexes: press Clean up again once the database "
                f"answers. The keys expire by themselves after {CR_KEY_TTL_S // 3600} h; an index "
                "does not."
            )
        if done["left"]:
            report += (
                " **Left behind:** "
                + ", ".join(f"`{name}`" for name in done["left"])
                + ". Its prefixes are not all under this run's, so it was not dropped."
            )
        return report

    def cr_test_connection():
        """verify.py --preflight: list the surfaces, reach the database, read-only. (ok, detail)."""
        admin, url = resolve_secret("CTX_ADMIN_KEY"), redis_url_secret()
        if admin is None or url is None:
            return False, "CTX_ADMIN_KEY and REDIS_URL (or REDIS_HOST) are not both set"
        conn = cr_probe(admin, url, "", "")
        facts = conn["facts"]
        return True, (
            f"{conn['listed']} surfaces visible, Redis {facts['version']} with Search and "
            f"JSON, {facts['dbsize']} keys"
        )

    # ---- column 3 · Agent Memory: helpers ----------------------------------------

    def am_memories():
        """The facts the Write cell stores: a made-up Cairn customer, Dana, ready to POST.

        Written so a question can find each one with no word in common: the presets below share
        no word with their fact. Five is few enough that a sweep shows them all.
        """
        rows = (
            ("Dana is allergic to peanuts and tree nuts, so trail snacks and camp meals must not "
             "contain them.", ["food", "allergy"]),
            ("Dana has wide feet, so she needs boots half a size bigger than her usual size.",
             ["sizing", "footwear"]),
            ("Dana is planning a winter trek across Patagonia in February and gets cold easily.",
             ["travel", "trip"]),
            ("Dana prefers gear made from recycled materials and will pay extra for it.",
             ["preferences", "sustainability"]),
            ("Dana wants parcels sent express, with no plastic packaging.",
             ["shipping", "packaging"]),
        )  # fmt: skip
        return [
            {
                "id": am_memory_id(n),
                "text": text,
                "ownerId": AM_OWNER,
                "memoryType": "semantic",
                "namespace": AM_NAMESPACE,  # the string label; SDK 0.4.1 marks it deprecated
                "topics": topics,
            }
            for n, (text, topics) in enumerate(rows, 1)
        ]

    def am_memory_id(n):
        """The id of the n-th memory this run writes: ib-<run>-m<n>."""
        return f"{AM_OWNER}-m{n}"

    def am_session_id(n):
        """The id of the n-th session this run starts: ib-<run>-s<n>."""
        return f"{AM_OWNER}-s{n}"

    def am_next_session():
        """A session id this run has never used. The ledger remembers deleted ones too."""
        used = [
            int(match[1])
            for ident in ledger_state("am", "session")
            if (match := re.fullmatch(re.escape(AM_OWNER) + r"-s(\d+)", ident))
        ]
        return am_session_id(max(used, default=0) + 1)

    def am_wow_presets():
        """The inspector's messages, each with the memory it is really after and what to notice.

        None shares a word with its memory, before or after stemming (a test says so). The lesson
        holds whatever the service ranks first: it is about how it got there.
        """
        return [
            {
                "label": "Serving food",
                "query": "What should the kitchen avoid serving me?",
                "target": am_memory_id(1),
                "lesson": "No word of the message is in the memory about peanuts, so a keyword "
                "index would find nothing. Whatever the service ranks first, it got there by "
                "meaning.",
            },
            {
                "label": "Sore toes",
                "query": "My toes feel squashed on long descents.",
                "target": am_memory_id(2),
                "lesson": "Toes and feet, squashed and wide: the same idea in different words. "
                "Look at the count of words in common, then at where the starred memory landed.",
            },
            {
                "label": "Somewhere freezing",
                "query": "Somewhere freezing to visit next year?",
                "target": am_memory_id(3),
                "lesson": "There is no travel word in common with the trek memory, and nothing "
                "says which year. A vague message is still matched by what it means.",
            },
        ]

    def am_wow_preset(label, typed):
        """The inspector message called `label`; any other label is the user's own, which is `typed`."""
        for preset in am_wow_presets():
            if preset["label"] == label:
                return preset
        return {
            "label": label,
            "query": typed.strip(),
            "target": None,
            "lesson": "Your own words, so no memory is starred: nobody said which one you were "
            "after. Compare the count of words in common with the order.",
        }

    def am_stems(text):
        """The words of a text, lower-cased, minus common ones, with -ing, -ed, -es or -s cut off.

        Crude on purpose: enough to say whether two texts share a word.
        """
        stop = (
            "a an and are as at be but by for if in into is it no not of on or such that the their "
            "then there these they this to was will with"
        ).split()
        words = [word for word in re.findall(r"[^\W_]+", text.lower()) if word not in stop]
        for i, word in enumerate(words):
            cut = next(
                (
                    s
                    for s in ("ing", "ed", "es", "s")
                    if word.endswith(s) and len(word) - len(s) >= 3
                ),
                "",
            )
            words[i] = word[: len(word) - len(cut)]
        return words

    def am_overlap(question, memory):
        """The stems a question and a memory share, sorted."""
        return sorted(set(am_stems(question)) & set(am_stems(memory)))

    def am_settings(base, store, key):
        """(connection, missing): the connection, or None and the names of the settings lacking.

        base and store are what their text boxes hold ("" falls back to the environment); key is
        the value of the SecretText field. A value that cannot be right raises ValueError without
        echoing it.
        """
        base = (base or "").strip().rstrip("/") or env("MEMORY_API_BASE_URL").rstrip("/")
        store = (store or "").strip() or env("MEMORY_STORE_ID")
        secret = resolve_secret("MEMORY_API_KEY", key)
        found = (
            ("MEMORY_API_BASE_URL", base),
            ("MEMORY_STORE_ID", store),
            ("MEMORY_API_KEY", secret),
        )
        missing = [name for name, value in found if not value]
        if missing:
            return None, missing
        if not base.startswith("https://"):
            raise ValueError("MEMORY_API_BASE_URL must start with https:// (copy the Endpoint)")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", store):
            raise ValueError("MEMORY_STORE_ID is letters, digits and - only (copy the Store ID)")
        return {"base": base, "store": store, "key": secret}, []

    def am_call(
        conn, method, path, *, body=None, params=None, timeout=T_READ, expect=(200, 201, 204)
    ):
        """One request to the store: (reply, call). call is the traced HttpCall, for the wire view."""
        headers = {"Authorization": f"Bearer {conn['key'].reveal()}", "Accept": "application/json"}
        url = f"{conn['base']}/v1/stores/{conn['store']}{path}"
        _, reply = http_call(
            "am",
            method,
            url,
            headers=headers,
            json=body,
            params=params,
            timeout=timeout,
            expect=expect,
        )
        return reply, HTTP_TRACE[-1]

    def am_filter(namespace=AM_NAMESPACE, **clauses):
        """The filter of every search this column makes: this run's owner, and a namespace.

        The owner cannot be changed, only added to (topics, memoryType ...). namespace=None
        leaves the namespace out: Clean up needs that, since a namespace `eq` never matches a
        memory that has none, and extraction gives its memories the namespace of the session's
        first event.
        """
        filt = {"ownerId": {"eq": AM_OWNER}}
        if namespace is not None:
            filt["namespace"] = {"eq": namespace}
        filt.update(clauses)
        assert filt["ownerId"] == {"eq": AM_OWNER}, (
            "a search of this notebook is always this owner's"
        )
        return filt

    def am_search(conn, text, filt, *, threshold=None, limit=10, page_token=None):
        """POST .../long-term-memory/search: {"items", "next", "call"}.

        A threshold is sent whenever it is not None: 0.0 is a real threshold (the demo's helper
        reads it as "not given" and quietly uses 0.7). text "" lists what the filter matches.
        """
        body = {"text": text, "filter": filt, "filterOp": "all", "limit": limit}
        if threshold is not None:
            body["similarityThreshold"] = threshold
        if page_token:
            body["pageToken"] = page_token
        reply, call = am_call(conn, "POST", "/long-term-memory/search", body=body)
        items = reply.get("items") if isinstance(reply, dict) else None
        if not isinstance(items, list):
            raise ServiceError("am", 200, "http", "the search reply has no list of items")
        return {"items": items, "next": reply.get("nextPageToken"), "call": call}

    def am_list(conn, filt, page=100, max_pages=50):
        """Every memory `filt` matches, as {"items", "calls"}: text "", no threshold, page by page."""
        items, calls, token = [], [], None
        for _ in range(max_pages):
            found = am_search(conn, "", filt, limit=page, page_token=token)
            items += found["items"]
            calls.append(found["call"])
            token = found["next"]
            if not token:
                return {"items": items, "calls": calls}
        raise ValueError(f"more than {page * max_pages} memories match; narrow the filter")

    def am_health(conn):
        """GET .../health, read-only: {"status", "features", "call"}."""
        reply, call = am_call(conn, "GET", "/health")
        if not (isinstance(reply, dict) and isinstance(reply.get("status"), str)):
            # A web page answers 200 too: an endpoint copied from the wrong place lands here.
            raise ServiceError(
                "am",
                call.status,
                "http",
                "the health reply is not a JSON object with a status. MEMORY_API_BASE_URL is the "
                "Endpoint in the service's General settings, not a console page",
            )
        features = reply.get("features")
        return {
            "status": reply["status"],
            "features": features if isinstance(features, dict) else {},
            "call": call,
        }

    def am_describe(facts):
        """What Test connection found, as a callout and the request behind it."""
        lines = [f"**Connected.** The store reports **{facts['status']}**."]
        for name, feature in facts["features"].items():
            state = feature.get("status") if isinstance(feature, dict) else feature
            lines.append(f"- `{name}`: {state}")
        return mo.vstack(
            [
                status_callout("ok" if facts["status"] == "healthy" else "warn", "\n".join(lines)),
                wire_view("The health check, as HTTP", facts["call"]),
            ]
        )

    def am_failed(exc):
        """service_failed with Agent Memory's own advice."""
        return service_failed(
            exc,
            {
                401: "The API key was refused. Paste `MEMORY_API_KEY` again, or make a new one on "
                "the service's API keys tab.",
                404: "Not found: check the endpoint and the store ID, or the session or memory "
                "has expired or been deleted.",
                424: "**The store could not do the work (424 Failed Dependency).** This is a "
                'failure, not "0 results". The database behind the service may be full or '
                "unreachable, or a model key or quota is at fault.",
            },
        )

    def am_write_memories(conn):
        """Bulk-create the facts in one request: {"created", "errors", "call"}.

        Every id is ledgered as an intent first, so a request that times out can still be
        cleaned up by the ids we chose.
        """
        memories = am_memories()
        for memory in memories:
            ledger_add("am", "memory", memory["id"], "intent")
        reply, call = am_call(
            conn, "POST", "/long-term-memory", body={"memories": memories}, timeout=T_WRITE
        )
        reply = reply if isinstance(reply, dict) else {}
        ours = {memory["id"] for memory in memories}
        created = [ident for ident in reply.get("created", []) if ident in ours]
        for ident in created:
            ledger_add("am", "memory", ident)
        return {"created": created, "errors": reply.get("errors") or [], "call": call}

    def am_thresholds():
        """The 11 thresholds one sweep tries: 0.0, 0.1 ... 1.0."""
        return [round(n / 10, 1) for n in range(11)]

    def am_sweep(conn, query, tick=lambda: None):
        """Search `query` at every threshold, limit 100, and derive each memory's band from the results.

        A search returns the memories that scored at least the threshold, and no score. So a
        memory's band is [t, t + 0.1) for the highest threshold t that still returned it. That
        holds only if the threshold is a hard cut on one number per memory, which `monotonic`
        tests: nothing may come back at a higher threshold that was missing at a lower one. The
        sweep stops at the first 429 and says so in `stopped`. Returns the query, the steps
        (threshold and ids), the calls, the memories (id -> record), their bands (id -> t),
        `monotonic` and `stopped`.
        """
        steps, calls, memories, stopped = [], [], {}, ""
        for threshold in am_thresholds():
            try:
                found = am_search(conn, query, am_filter(), threshold=threshold, limit=100)
            except ServiceError as exc:
                if exc.status != 429:
                    raise
                stopped = (
                    f"the service answered 429 (too many requests) after {len(steps)} searches"
                )
                break
            if not steps:
                memories = {memory["id"]: memory for memory in found["items"]}
            steps.append(
                {"threshold": threshold, "ids": [memory["id"] for memory in found["items"]]}
            )
            calls.append(found["call"])
            tick()
        seen = [set(step["ids"]) for step in steps]
        return {
            "query": query, "steps": steps, "calls": calls, "memories": memories,
            "bands": {
                ident: max(step["threshold"] for step in steps if ident in step["ids"])
                for ident in memories
            },
            "monotonic": all(later <= earlier for earlier, later in zip(seen, seen[1:], strict=False)),
            "stopped": stopped,
        }  # fmt: skip

    def am_sweep_problem(sweep):
        """A callout saying why a sweep cannot be shown as bands, or None if it can."""
        if sweep["stopped"]:
            return status_callout(
                "warn",
                f"**The sweep stopped: {sweep['stopped']}.** Nothing is derived from a partial "
                "sweep. Wait a minute and press Sweep again.",
            )
        if not sweep["memories"]:
            return status_callout("warn", "The sweep found no memories under this run's owner.")
        if not sweep["monotonic"]:
            return mo.vstack(
                [
                    status_callout(
                        "warn",
                        "**The service does not behave like a hard cut.** A memory came back at a "
                        "higher threshold that was missing at a lower one, so bands would be "
                        "made up. Here is what it returned:",
                    ),
                    plain_table(
                        [
                            {"threshold": s["threshold"], "returned": len(s["ids"])}
                            for s in sweep["steps"]
                        ]
                    ),
                ]
            )
        return None

    def am_band_rows(sweep, cut):
        """The band table at a slider position: a row per memory, most similar first.

        Works on the saved sweep and asks nothing. A memory returned at threshold t is one whose
        band starts at or above t.
        """
        cut = round(float(cut), 1)
        rows = []
        for ident, top in sorted(sweep["bands"].items(), key=lambda item: (-item[1], item[0])):
            memory = sweep["memories"][ident]
            rows.append(
                {
                    "similarity": f"{top:.1f} to {top + 0.1:.1f}" if top < 1 else "1.0",
                    "at the slider": "returned" if top >= cut else "cut",
                    "memory": memory["text"],
                    "words in common": len(am_overlap(sweep["query"], memory["text"])),
                }
            )
        return rows

    def am_recheck(conn, sweep, cut):
        """One live search at `cut`, next to what the saved sweep predicts for it."""
        cut = round(float(cut), 1)
        found = am_search(conn, sweep["query"], am_filter(), threshold=cut, limit=100)
        return {
            "threshold": cut,
            "live": [memory["id"] for memory in found["items"]],
            "predicted": [ident for ident, top in sweep["bands"].items() if top >= cut],
            "call": found["call"],
        }

    def am_nobody():
        """An owner id that cannot belong to anyone, for the Scope lens."""
        return "nobody-owns-this-id"

    def am_scope_search(conn, query):
        """The same question under three scopes: [{"scope", "filter", "count", "call"}].

        All three are read-only searches that carry an owner and a namespace. The first is this
        run's. The second asks as an owner that does not exist, in this notebook's namespace, and
        the third as this run's owner in a namespace that does not exist, so neither can return
        anyone's memories. The threshold is 0.0, so a scope finds everything it can.
        """
        cases = [
            ("this run's owner", am_filter()),
            ("an owner that does not exist",
             {"ownerId": {"eq": am_nobody()}, "namespace": {"eq": AM_NAMESPACE}}),
            ("a namespace that does not exist", am_filter(namespace="no-such-namespace")),
        ]  # fmt: skip
        found = [
            (scope, filt, am_search(conn, query, filt, threshold=0.0, limit=100))
            for scope, filt in cases
        ]
        return [
            {"scope": scope, "filter": filt, "count": len(f["items"]), "call": f["call"]}
            for scope, filt, f in found
        ]

    def am_event_body(session, text, role="USER"):
        """POST .../session-memory/events: one message. Every event carries the actor and namespace."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return {
            "sessionId": session,
            "actorId": AM_OWNER,
            "role": role,
            "content": [{"text": text}],
            "createdAt": now,
            "namespace": AM_NAMESPACE,
            "metadata": {"source": "iris-basics"},
        }

    def am_add_event(conn, session, text, role="USER"):
        """Append one event to a session of ours: (the event's id, the traced call).

        A session's owner is the actor of its first event and its namespace comes from that event
        alone, so the first event of every session must be a USER event from AM_OWNER in
        AM_NAMESPACE (every event carries both, so it always is). The session goes in the ledger
        as an intent before the request and as created after it.
        """
        first = session not in ledger_live("am", "session")
        if first and role != "USER":
            raise ValueError("the first event of a session must be a USER event")
        if first:
            ledger_add("am", "session", session, "intent")
        reply, call = am_call(
            conn, "POST", "/session-memory/events", body=am_event_body(session, text, role)
        )
        if first:
            ledger_add("am", "session", session)
        event = reply.get("event") if isinstance(reply, dict) else None
        event_id = event.get("eventId") if isinstance(event, dict) else None
        if not isinstance(event_id, str):
            raise ServiceError("am", call.status, "http", "the reply to the event has no event id")
        return event_id, call

    def am_get_session(conn, session, include_summarised=False):
        """GET .../session-memory/<id>: {"session" (the reply), "call"}."""
        params = {"includeSummarisedEvents": "true"} if include_summarised else None
        reply, call = am_call(
            conn, "GET", f"/session-memory/{quote(session, safe='')}", params=params
        )
        return {"session": reply if isinstance(reply, dict) else {}, "call": call}

    def am_take_turn(conn, chat, ask, limit):
        """One Send: add the message to the session, read the session back, search by meaning.

        `ask` is a preset (see am_wow_preset). The session is chat["session"], or a new one on
        the first Send. The search sends a threshold of 0.0, so what comes back is the top
        `limit` in rank order, and nothing is cut by score.
        """
        session = chat["session"] or am_next_session()
        chat["session"] = session
        event_id, event_call = am_add_event(conn, session, ask["query"])
        read = am_get_session(conn, session)
        found = am_search(conn, ask["query"], am_filter(), threshold=0.0, limit=limit)
        return {
            "preset": ask,
            "ask": ask["query"],
            "session": session,
            "event_id": event_id,
            "limit": limit,
            "events": read["session"].get("events") or [],
            "summary": read["session"].get("summary"),
            "recalled": found["items"],
            "calls": [event_call, read["call"], found["call"]],
        }

    def am_recall_rows(turn):
        """The recalled memories as table rows: 'recalled' reads '#1 of 3 · 0 words in common'."""
        target = turn["preset"]["target"]
        rows = []
        for rank, memory in enumerate(turn["recalled"], 1):
            shared = am_overlap(turn["ask"], memory["text"])
            words = f"{len(shared)} word{'' if len(shared) == 1 else 's'} in common"
            rows.append(
                ({"answer": "★" if memory["id"] == target else ""} if target else {})
                | {
                    "recalled": f"#{rank} of {len(turn['recalled'])} · {words}",
                    "memory": memory["text"],
                    "topics": ", ".join(memory.get("topics") or []),
                }
            )
        return rows

    def am_wow_summary(turn):
        """One line saying where the memory the message was really after came back. "" for your own."""
        target = turn["preset"]["target"]
        if not target:
            return ""
        ids = [memory["id"] for memory in turn["recalled"]]
        text = next(memory["text"] for memory in am_memories() if memory["id"] == target)
        if target in ids:
            where = f"came back #{ids.index(target) + 1} of {len(ids)}"
        else:
            where = f"did not make the top {turn['limit']}"
        return f"The memory it is really after, ★ **{text}** {where}."

    def am_event_text(event):
        """The text of a session event: its content parts, joined."""
        parts = event.get("content") if isinstance(event, dict) else None
        return " ".join(p.get("text", "") for p in parts or [] if isinstance(p, dict))

    def am_event_rows(events):
        """A session's events as table rows, oldest first, as the service returned them."""
        return [
            {
                "#": n,
                "role": event.get("role", ""),
                "actor": event.get("actorId", ""),
                "text": am_event_text(event),
            }
            for n, event in enumerate(events, 1)
        ]

    def am_context(turn, tiers):
        """The context an agent would be handed, built from the tiers that are switched on.

        tiers: {"summary", "events", "memories"} booleans. The message just sent is the question,
        so the short-term tier is the other events, the last six at most.
        """
        lines = ["Retrieved memory context:"]
        summary = turn["summary"]
        if tiers["summary"] and isinstance(summary, dict) and summary.get("text"):
            lines += ["Session summary:", summary["text"]]
        if tiers["events"]:
            earlier = [
                f"{event.get('role', '')}: {am_event_text(event)}"
                for event in turn["events"]
                if event.get("eventId") != turn["event_id"]
            ][-6:]
            lines += ["Short-term memory:"] + (earlier or ["(no earlier messages)"])
        if tiers["memories"]:
            lines += ["Long-term memory:"] + [f"- {memory['text']}" for memory in turn["recalled"]]
        if len(lines) == 1:
            return "(nothing: with every tier off the agent sees only the question)"
        return "\n".join(lines)

    def am_count(n, noun):
        """'1 event', '2 events'."""
        return f"{n} {noun}{'' if n == 1 else 's'}"

    def am_delete_memories(conn, ids):
        """DELETE .../long-term-memory, at most 100 ids per request: (deleted ids, errors).

        The choke point refuses any id that is not ledgered here or granted (approve_am_memories,
        approve_am_orphans).
        """
        deleted, errors = [], []
        for start in range(0, len(ids), 100):
            reply = am_call(
                conn, "DELETE", "/long-term-memory", body={"memoryIds": ids[start : start + 100]}
            )[0]
            reply = reply if isinstance(reply, dict) else {}
            deleted += reply.get("deleted", [])
            errors += reply.get("errors") or []
        return deleted, errors

    def am_list_sessions(conn, owner):
        """The session ids the store holds for one owner: filterOwnerId, never includeAll."""
        ids, token = [], None
        for _ in range(50):
            params = {"filterOwnerId": owner, "limit": 1000} | (
                {"pageToken": token} if token else {}
            )
            reply = am_call(conn, "GET", "/session-memory", params=params)[0]
            reply = reply if isinstance(reply, dict) else {}
            ids += reply.get("items", [])
            token = reply.get("nextPageToken")
            if not token:
                break
        return ids

    def am_orphan_sweep(conn):
        """Other runs' leftovers in this namespace: {"memories", "sessions", "rows", "total"}.

        The one search here that is not scoped to this run's owner: it lists the whole namespace,
        which only this notebook writes to. Of what it finds, only memories owned by another run
        (ib-<8 hex digits>) and older than ORPHAN_AGE_H are offered, with that owner's sessions.
        Anything else is counted and left alone.
        """
        owners = {}
        for memory in am_list(conn, {"namespace": {"eq": AM_NAMESPACE}})["items"]:
            owner = memory.get("ownerId") if isinstance(memory, dict) else None
            if isinstance(owner, str) and owner.startswith("ib-") and owner != AM_OWNER:
                owners.setdefault(owner, []).append(memory)
        offered, sessions, rows = [], [], []
        for owner, memories in sorted(owners.items()):
            run_shaped = re.fullmatch(r"ib-[0-9a-f]{8}", owner)
            old = [m for m in memories if run_shaped and is_stale(m.get("createdAt"))]
            named = re.escape(owner) + r"-s\d+"
            theirs = (
                [s for s in am_list_sessions(conn, owner) if re.fullmatch(named, s)] if old else []
            )
            offered += old
            sessions += theirs
            rows.append(
                {
                    "owner": owner,
                    "memories": len(memories),
                    "oldest": min(str(m.get("createdAt", "")) for m in memories)[:16],
                    "offered": len(old),
                    "sessions offered": len(theirs),
                }
            )
        return {
            "memories": offered,
            "sessions": sessions,
            "rows": rows,
            "total": sum(len(found) for found in owners.values()),
        }

    def am_delete_orphans(conn, orphans, confirmation):
        """Delete what am_orphan_sweep offered, once `confirmation` is typed: the counts.

        The choke point takes nothing on trust: approve_am_orphans and approve_am_session_orphans
        check each item again (another run's, old enough) before it lets one through.
        """
        approve_am_orphans(orphans["memories"], confirmation)
        approve_am_session_orphans(orphans["sessions"], confirmation)
        deleted, errors = am_delete_memories(conn, [memory["id"] for memory in orphans["memories"]])
        for session in orphans["sessions"]:
            am_call(conn, "DELETE", f"/session-memory/{session}", expect=(204, 404))
        return {
            "memories": len(deleted),
            "sessions": len(orphans["sessions"]),
            "refused": len(errors),
        }

    def am_cleanup(conn):
        """Delete this run's sessions and memories; return the counts.

        In order: delete the ledgered sessions (a 404 means gone already; one the service will
        not delete is counted and left, since it expires on its own and the memories, which last
        a year, matter more). List every memory the store holds for AM_OWNER, by owner alone,
        page by page. Check here that each one really has that owner, and stop before deleting
        any memory if one does not: the service applied the filter, and a filter is only as good
        as the request that carried it. Grant the listed ids, which extraction chose, and delete
        them and the ledgered ones, at most 100 per request. List again to see what is left, and
        mark the ledgered ids that are gone. The ledger is read from disk, so a fresh kernel with
        this run's id can clean up too.
        """
        done = {
            "ran": False,
            "sessions": 0,
            "sessions_left": 0,
            "memories": 0,
            "extracted": 0,
            "left": 0,
            "refused": 0,
        }
        if not (ledger_state("am", "session") or ledger_state("am", "memory")):
            return done  # this run wrote nothing, so there is nothing to ask the service
        done["ran"] = True
        for session in ledger_live("am", "session", pending=True):
            try:
                am_call(
                    conn, "DELETE", f"/session-memory/{quote(session, safe='')}", expect=(204, 404)
                )
            except ServiceError:
                done["sessions_left"] += 1  # still in the ledger, so the next clean-up tries again
                continue
            ledger_add("am", "session", session, "deleted")
            done["sessions"] += 1
        listed = am_list(conn, am_filter(namespace=None))["items"]
        strangers = [
            m for m in listed if not (isinstance(m, dict) and m.get("ownerId") == AM_OWNER)
        ]
        if strangers:
            raise ValueError(
                f"the service listed {len(strangers)} memories that are not owned by {AM_OWNER}, "
                "so the filter was not applied as sent. No memory was deleted."
            )
        approve_am_memories(listed)
        written = ledger_live("am", "memory", pending=True)
        ids = list(dict.fromkeys([memory["id"] for memory in listed] + written))
        deleted, errors = am_delete_memories(conn, ids)
        left = {memory["id"] for memory in am_list(conn, am_filter(namespace=None))["items"]}
        for ident in written:
            if ident not in left:
                ledger_add("am", "memory", ident, "deleted")
        done.update(
            memories=len(deleted),
            extracted=sum(ident not in written for ident in deleted),
            left=len(left),
            refused=len(errors),
        )
        return done

    def am_cleanup_report(done):
        """The counts from am_cleanup, in a sentence or two."""
        if not done["ran"]:
            return "Nothing to clean up: this run has written nothing."
        report = (
            f"Deleted **{done['sessions']}** sessions and **{done['memories']}** memories, "
            f"**{done['extracted']}** of them made by extraction."
        )
        if done["sessions_left"]:
            report += (
                f" **{done['sessions_left']}** sessions could not be deleted. They expire on their "
                "own (after an hour by default) and the next clean-up tries them again."
            )
        if done["left"] or done["refused"]:
            report += (
                f" **{done['left']}** are still listed and the service refused **{done['refused']}**"
                ": press **Clean up again** in a minute."
            )
        return report + (
            " Extraction can add memories for about 10 minutes after the last event: press "
            "**Clean up again** later to catch them."
        )

    def am_extract_conversation():
        """The short conversation the kick-off posts: what a person might say in passing."""
        return [
            "I'm training for a 50 kilometre trail race in April.",
            "I run before sunrise because I can't stand the heat.",
            "My left knee complains on steep downhills, so I always carry trekking poles.",
            "Once the race is over I want to learn to rock climb.",
        ]

    def am_extract_kickoff(conn):
        """Post the conversation to a new session of ours: {"session", "events", "call"}.

        Extraction reads a session's events in the background, minutes later, and writes memories
        under the owner and namespace of its first event.
        """
        session = am_next_session()
        calls = [am_add_event(conn, session, text)[1] for text in am_extract_conversation()]
        return {"session": session, "events": len(calls), "call": calls[0]}

    def am_extracted(conn):
        """The memories under this owner that we did not write: {"items", "calls"}.

        Their ids are the service's, not ours, which is how extraction shows itself.
        """
        written = set(ledger_state("am", "memory"))
        found = am_list(conn, am_filter())
        return {
            "items": [memory for memory in found["items"] if memory["id"] not in written],
            "calls": found["calls"],
        }

    def am_extracted_rows(items):
        """Extracted memories as table rows."""
        return [
            {
                "id": memory["id"],
                "type": memory.get("memoryType", ""),
                "topics": ", ".join(memory.get("topics") or []),
                "memory": memory["text"],
            }
            for memory in items
        ]

    def am_hood(client, conn):
        """Read-only look at the index the service keeps in the database, and at one memory in it.

        Best effort: the key layout and field names are not documented, so this reads what the
        index says and fails soft. Returns {"index", "missing", "commands", ...}; `missing` means
        the database has no such index (it is not the one behind the service).
        """
        index = f"memory:{conn['store']}:ltm"
        commands = []

        def ask(*argv):
            commands.append(redis_cli(argv))
            return client.execute_command(*argv)

        try:
            info = reply_map(decode_reply(ask("FT.INFO", index)))
        except redis.ResponseError as exc:
            if is_unknown_index(exc):
                return {"index": index, "missing": True, "commands": commands}
            raise
        attributes = (reply_map(attribute) for attribute in info.get("attributes") or [])
        vector = next(
            (attribute for attribute in attributes if attribute.get("type") == "VECTOR"), {}
        )
        hood = {
            "index": index,
            "missing": False,
            "docs": info.get("num_docs"),
            "vector": {
                name: vector.get(name)
                for name in ("algorithm", "data_type", "dim", "distance_metric")
            },
            "memory": None,
            "commands": commands,
        }
        escaped = am_memory_id(1).replace("-", "\\-")
        found = decode_reply(
            ask(
                "FT.SEARCH",
                index,
                "@id:{" + escaped + "}",
                "NOCONTENT",
                "LIMIT",
                0,
                1,
                "DIALECT",
                2,
            )
        )
        if isinstance(found, list) and len(found) > 1:
            key, fields = found[1], ("text", "owner_id", "namespace", "topics", "memory_type")
            values = decode_reply(ask("HMGET", key, *fields))
            hood["memory"] = {"key": key, **dict(zip(fields, values, strict=True))}
            hood["vector_bytes"] = ask("HSTRLEN", key, "text_vector")
        return hood

    def am_hood_view(hood):
        """What am_hood found, as a callout, a table and the commands."""
        if hood["missing"]:
            return status_callout(
                "warn",
                f"That database has no index `{hood['index']}`, so it is not the one behind this "
                "service (its *General settings* name the database). Nothing was changed.",
            )
        vector = hood["vector"]
        parts = [
            status_callout(
                "ok",
                f"`{hood['index']}` holds **{hood['docs']}** documents. Its vector field: "
                f"{vector['algorithm']} · {vector['data_type']} · `DIM {vector['dim']}` · "
                f"{vector['distance_metric']}.",
            )
        ]
        if hood["memory"]:
            size = hood["vector_bytes"]
            parts += [
                plain_table(
                    [{"field": name, "value": value} for name, value in hood["memory"].items()]
                ),
                mo.md(
                    f"`text_vector` is **{size}** bytes"
                    + (f", `{size} / 4 = {size // 4}` floats, the index's `DIM`." if size else ".")
                    + " A memory is a Redis hash and a vector in an index: Redis Insight can browse it."
                ),
            ]
        else:
            parts.append(
                status_callout(
                    "info", f"`{am_memory_id(1)}` is not in that index: write the memories first."
                )
            )
        parts.append(wire_view("The commands, as redis-cli", hood["commands"]))
        return mo.vstack(parts)

    def am_sdk_snippet(query):
        """The search above written with the official SDK, as Markdown. Text only.

        redis-agent-memory is not a dependency and nothing in this notebook imports it.
        """
        # Built line by line, not as one multi-line string: marimo strips four spaces from every
        # line of the setup block (a string's too), so a string's own indentation would differ
        # between the kernel and an import.
        return code_block(
            "\n".join(
                [
                    "# pip install redis-agent-memory==0.4.1",
                    "from redis_agent_memory import AgentMemory, models",
                    "",
                    "with AgentMemory(ENDPOINT, store_id=STORE_ID, api_key=API_KEY) as memory:",
                    "    found = memory.search_long_term_memory(",
                    "        request=models.SearchLongTermMemoryRequestContent(",
                    f"            text={query!r},",
                    "            similarity_threshold=0.3,  # one of the 11 steps of the sweep",
                    "            limit=100,",
                    "            filter_=models.LongTermMemoryFilter(",
                    f"                owner_id=models.OwnerIDFilter(eq={AM_OWNER!r}),",
                    f"                namespace=models.NamespaceFilter(eq={AM_NAMESPACE!r}),",
                    "            ),",
                    "        )",
                    "    )",
                    "# Build the filter from these models. A plain dict with a misspelled key,",
                    '# such as {"owner": {"eq": ...}}, is dropped without a word and the search',
                    "# runs unfiltered.",
                ]
            ),
            "python",
        )

    def am_test_connection():
        """verify.py --preflight: one read-only health check. Returns (ok, detail)."""
        conn, missing = am_settings("", "", None)
        if conn is None:
            return False, "missing " + ", ".join(missing)
        facts = am_health(conn)
        return facts["status"] != "unavailable", f"the store reports {facts['status']}"


@app.cell(column=0, hide_code=True)
def _():
    # Intro
    ib_notes = mo.ui.switch(label="Presenter notes (a Say / Expect line under each core cell)")

    # A red banner, because an AUTORUN notebook presses its own buttons.
    _banner = (
        mo.callout(
            mo.md(
                f"**AUTORUN is on (`{AUTORUN}`).** This notebook is pressing its own buttons "
                "and writing to whatever services are configured. Unset `IRIS_BASICS_AUTORUN` "
                "to present."
            ),
            kind="danger",
        )
        if AUTORUN
        else mo.md("")
    )

    # ContextModel needs Python < 3.14; the PEP 723 header makes uv choose 3.13, but a
    # hand-started `--no-sandbox` run can land anywhere.
    _python = (
        mo.callout(
            mo.md(
                "**This is Python 3.14 or newer.** Context Retriever's `ContextModel` breaks "
                "there. Launch with `uvx marimo@0.25.0 edit --sandbox iris_basics.py`, or use "
                "Python 3.13."
            ),
            kind="warn",
        )
        if sys.version_info >= (3, 14)
        else mo.md("")
    )

    # Style rules for marimo's own page, since a notebook cannot restyle it any other way.
    # marimo's floating outline (a column of ticks at the right edge) covers body text
    # wherever a column reaches that edge, and the table below already links each column.
    _rules = ["div.fixed.right-8.z-10000 { display: none !important; }"]
    if mo.app_meta().mode == "run":
        # `marimo run` gives every column a fixed 740 px, which puts four columns far off a
        # laptop screen. Narrower columns fit.
        _rules.append(":root { --content-width: 560px; }")
    else:
        # Edit mode, the presenting mode. It caps every output at 610 px and scrolls the rest
        # inside a box; the audience should see all of it. And a cell that did not run because
        # an ancestor stopped (a column waiting for its Test connection) shows a grey block
        # that names a cell id, so it says what to do instead.
        _rules += [
            "[data-cell-role=output] { max-height: none !important; }",
            ".marimo-cell.stopped [data-cell-role=output] > * { display: none; }",
            ".marimo-cell.stopped [data-cell-role=output]::before { "
            "content: 'Waiting for a connection or a button above.'; "
            "color: var(--slate-9, #64748b); font-size: 12px; font-style: italic; }",
        ]
    _style = mo.Html("<style>" + " ".join(_rules) + "</style>")

    _found = ", ".join(f"`{name}`" for name in env_present()) or "none"
    mo.vstack(
        [
            _banner,
            _python,
            mo.md(r"""
            # Redis Iris basics

            **Redis Iris** is Redis's context engine for AI agents: a suite of managed (or
            self-managed) services for memory, semantic caching and governed data access. Three
            of them are in preview. This notebook takes them apart, one column each, with real
            calls and the exact wire format behind every one. Column 0 starts from the Redis
            Query Engine that all of them rest on.

            | Column | You need | Core path |
            |:---|:---|:---|
            | [0 · Redis Search](#0-redis-search)<br>Keyword, vector and hybrid search over your own data | A Redis database with Search (the free tier is fine) | Test, Write, KNN or a radius, Wow |
            | [1 · LangCache](#1-langcache)<br>A semantic cache, and what its similarity threshold really trades | A LangCache service: host, cache ID and key | Test, Write, Threshold lab, Wow chat |
            | [2 · Context Retriever](#2-context-retriever)<br>A data model turned into governed MCP tools | An admin key, and a Redis database the service can reach from the internet | Change a field, Create, Wow explorer, Break it |
            | [3 · Agent Memory](#3-agent-memory)<br>Session events, background extraction, recall by meaning | The service's endpoint, store ID and key | Test, Start extraction, Write, Sweep, Wow inspector |

            The core paths take about 15 minutes for all four columns, about 25 with the deep
            dives. Each column asks only for its own settings; without them it shows a "not
            configured" note (column 2 also has a data-model cell that works offline).

            Docs: [Redis Iris context engine](https://redis.io/docs/latest/develop/ai/context-engine/)
            · [redis.io/iris](https://redis.io/iris/)
            """),
            ib_notes,
            mo.accordion(
                {
                    "Launch": mo.md(r"""
                    - **Present, code visible:** `uvx marimo@0.25.0 edit --sandbox iris_basics.py`.
                      marimo opens it with nothing run: press **Ctrl+Shift+R** (*Run all stale
                      cells*, or the yellow play button at the bottom right) and wait about 15
                      seconds for the yellow marks to clear. A cell that says *Waiting for a
                      connection or a button above* has not run yet.
                    - **Hand over, code hidden:** `uvx marimo@0.25.0 run --sandbox iris_basics.py`.
                      It runs by itself when opened.
                    - Settings come from the environment, and from the `.env` next to this file
                      (marimo loads it into the environment first) or the file
                      `IRIS_BASICS_ENV_FILE` names. Use one of the two, not both: the `.env` wins.
                    - In edit mode a column is about 1200 px wide, so one column fills the
                      screen. Zoom the browser out to 67-75%, or drag a column's border, to see
                      two.
                    """),
                    "Safety": mo.md(rf"""
                    - Everything this session writes carries the run id `{RUN}`. Redis keys start
                      `{PREFIX}`. Context Retriever surfaces are named `{RUN_TAG}` and Agent Memory
                      ids start `{AM_OWNER}-`. LangCache entries are found by id in the ledger
                      and expire in {LC_TTL_MS // 60_000} minutes; they carry the tag `{RUN_TAG}`
                      only if your cache defines a scope attribute.
                    - Nothing is written until you press a button that says what it will write and
                      where. Every key and cache entry expires on its own, and each column has a
                      Cleanup for the rest (indexes, memories, surfaces).
                    - Secrets are never prefilled into a field, and every trace and message is
                      scrubbed of them.
                    - On a shared service, never use Flush, Delete service or Generate key. This
                      notebook never does.
                    - `marimo edit` and `marimo run` write `__marimo__/`, which holds outputs and
                      tracebacks. Delete it before you hand the folder to anyone.
                    """),
                    "Settings detected": mo.md(
                        f"Names only, never values: {_found}. "
                        "See `.env.example` for what each column needs."
                    ),
                }
            ),
            _style,
        ]
    )
    return (ib_notes,)


@app.cell(column=0, hide_code=True)
def _(ib_notes):
    # Intro: shows the Say / Expect lines
    # This is the only cell that reads the Presenter notes switch (see notes()).
    ib_notes_style = mo.Html(
        f"<style>.ib-note {{ display: {'block' if ib_notes.value else 'none'}; }}</style>"
    )
    ib_notes_style
    return


@app.cell(column=0, hide_code=True)
def _():
    # 0 · Redis Search: title
    _direction = "TB" if mo.app_meta().mode == "run" else "LR"
    mo.vstack(
        [
            mo.md(r"""
            # 0 · Redis Search

            The Redis Query Engine is what the rest of Iris is built on: full-text, vector and
            hybrid search, plus aggregations, over data you already keep in Redis. You describe
            an index once with `FT.CREATE` and it watches a key prefix, so every `HSET` under
            that prefix is searchable straight away. Text is embedded on this machine by a small
            local model, and every command is shown as plain `redis-cli` you can paste into a
            terminal. In this column you run one query as keyword, vector and hybrid search and
            see where each one wins. It needs a single Redis database with Search, and the free
            30 MB tier is plenty.

            **Docs:** [Redis Search](https://redis.io/docs/latest/develop/ai/search-and-query/)
            · **Create one:** [free Redis Cloud database](https://redis.io/try-free/)
            """),
            mo.mermaid(
                f"flowchart {_direction}"
                """
                  A[App] -->|embed locally| V[Vector]
                  V -->|HSET| H[(Hash under a prefix)]
                  H -.->|index watches the prefix| I[FT index]
                  A -->|FT.SEARCH| I
                """
            ),
        ]
    )
    return


@app.cell(column=0, hide_code=True)
def _():
    # 0 · Redis Search: what to connect to
    mo.vstack(
        [
            mo.md(r"""
            ### Connect to your database

            This column needs one Redis database with Search. Paste its URL in the field below,
            or leave the field empty to use `REDIS_URL` (or `REDIS_HOST` and `REDIS_PASSWORD`)
            from the environment or `.env`. The URL is never shown, saved or prefilled.
            """),
            mo.accordion(
                {
                    "Where do I get these?": mo.md(r"""
                    - **No database yet?** Create a free one at
                      [redis.io/try-free](https://redis.io/try-free/): *Try 30 MB for free*,
                      then pick the newest Redis version on offer (8.4 or later adds
                      `FT.HYBRID`). Search is built in, so there is nothing to switch on.
                    - **The URL:** in [cloud.redis.io](https://cloud.redis.io) open *Databases*,
                      your database, *Configuration*: the *Public endpoint* is `host:port`. The
                      password is under *Security*, *Default user*, behind the eye icon (the
                      *Connect* button shows a ready-made command too). Put them together as
                      `redis://default:<password>@<host>:<port>`.
                    - The free tier has no TLS, so it is `redis://`, not `rediss://`. It allows 30
                      connections, 100 commands a second and 30 MB.
                    """)
                }
            ),
            status_callout(
                "warn",
                "**Never on a shared service.** Flush, Delete database and Reset password are "
                f"never used here. This column writes only under `{PREFIX}`, but Redis Cloud "
                "cannot give it a narrower user, so those limits are this notebook's own. "
                "Point it at a scratch database if other apps use yours.",
            ),
        ]
    )
    return


@app.cell(column=0)
def _():
    rs_url = SecretText("REDIS_URL", label="REDIS_URL")
    rs_connect = mo.ui.run_button(label="Test connection (read-only)")
    # Reset bumps this token. The connection cell reads it, so the column drops back to
    # "press Test connection" and every cell below it clears.
    rs_gen, rs_set_gen = mo.state(0)
    mo.vstack([rs_url, rs_connect])
    return rs_connect, rs_gen, rs_set_gen, rs_url


@app.cell(column=0)
def _(rs_connect, rs_gen, rs_url):
    rs_gen()  # Reset bumps this token, which re-runs the cell and drops the connection
    rs_client, rs_facts = None, {}
    try:
        _url = redis_url_secret(rs_url.value)
        if _url is None:
            _status = mo.vstack(
                [not_configured("Redis Search", ["REDIS_URL"]), recorded_live("rs")]
            )
        elif not pressed(rs_connect):
            _status = status_callout(
                "info",
                "Press **Test connection**. It only reads: `PING`, `INFO`, `FT._LIST`, `DBSIZE`, "
                "and a `SCAN` for keys that start `irisnb_`.",
            )
        else:
            rs_client = connect_redis(_url.reveal())
            rs_facts = with_spinner("Redis", lambda: rs_probe(rs_client))
            _status = rs_describe(rs_facts)
    except (ValueError, ServiceError, redis.RedisError) as _exc:
        rs_client, _status = None, status_callout("error", _exc)
    rs_has_hybrid = rs_facts.get("hybrid", False)
    rs_has_svs = rs_facts.get("svs", False)
    _status
    return rs_client, rs_facts, rs_has_hybrid, rs_has_svs


@app.cell(column=0)
def _(rs_client, rs_facts):
    mo.stop(rs_client is None, None)
    rs_orphan_confirm = mo.ui.text(placeholder=f'type "{ORPHAN_CONFIRM}"', label="Confirm")
    rs_orphan_drop = mo.ui.run_button(label="Drop those indexes", kind="danger")
    _left = rs_leftovers(rs_facts)
    _keys = sum(run["keys"] for run in _left["runs"])
    _body = []
    if _left["runs"]:
        _body += [
            mo.md(
                "Keys expire on their own an hour after they are written, so only "
                "**indexes** outlive a run. An index is offered for dropping once its run's "
                "keys have gone: until then it may be someone's live demo. Dropping one "
                "leaves any documents to expire."
            ),
            plain_table(_left["runs"]),
        ]
    if _left["indexes"]:
        _body += [
            mo.md("Indexes to drop: " + ", ".join(f"`{name}`" for name in _left["indexes"])),
            mo.hstack([rs_orphan_confirm, rs_orphan_drop], justify="start", wrap=True),
        ]
    if _left["live"]:
        _body.append(mo.md("Not offered yet: " + ", ".join(f"`{name}`" for name in _left["live"])))
    _count = len(_left["indexes"]) + len(_left["live"])
    _title = f"Leftovers from other runs ({_keys} keys, {_count} indexes)"
    mo.accordion(
        {
            _title: mo.vstack(
                _body or [mo.md("None found. Every earlier run has expired or cleaned up.")]
            )
        }
    )
    return rs_orphan_confirm, rs_orphan_drop


@app.cell(column=0)
def _(rs_client, rs_facts, rs_orphan_confirm, rs_orphan_drop):
    # A plain .value, not pressed(): deleting other people's leftovers never autoruns.
    mo.stop(not rs_orphan_drop.value, None)
    try:
        _names = rs_leftovers(rs_facts)["indexes"]
        _dropped = rs_drop_orphans(rs_client, _names, rs_orphan_confirm.value)
        _out = status_callout("ok", f"Dropped **{len(_dropped)}** of {len(_names)} indexes.")
    except (ValueError, redis.RedisError, Denied) as _exc:
        _out = status_callout("error", _exc)
    _out
    return


@app.cell(column=0)
def _(rs_client):
    # Everything below depends on this cell, so marimo skips it all while there is no connection.
    mo.stop(rs_client is None, None)
    with mo.status.spinner(title="Loading potion-base-8M", subtitle="first run: about 30 MB"):
        _name, rs_embed, _problem = rs_load_embedder()
    rs_dim = len(rs_embed(["probe"])[0])  # derived, never typed: FT.CREATE needs it
    mo.vstack(
        [
            mo.md(
                f"Embedder: **{_name}** · `DIM = {rs_dim}` · `{rs_dim * 4}` bytes per "
                "vector, computed on this machine. No API key."
            ),
            status_callout(
                "warn",
                f"**Fell back to word hashing** ({_problem}). It matches words, not meaning, "
                "so paraphrases will not be found. Fix the model download before showing "
                "this to anyone.",
            )
            if _problem
            else mo.md(""),
        ]
    )
    return rs_dim, rs_embed


@app.cell(column=0)
def _(rs_dim, rs_facts):
    rs_seed = mo.ui.run_button(label=f"Write {len(rs_articles())} articles and 1 index")
    mo.vstack(
        [
            mo.md(rf"""
            ### Load the help centre

            Pressing the button writes exactly this to `{rs_facts["where"]}`, and only under
            `{RS_PREFIX}`:

            - `FT.CREATE {rs_index()}`: an index over every hash whose key starts
              `{rs_doc_key()}`
            - **{len(rs_articles())}** hashes `{rs_doc_key()}<id>`: `title`, `body`, `category`,
              `year` and a {rs_dim}-dimension `vec`, each followed by `EXPIRE {KEY_TTL_S}`

            The database holds **{rs_facts["dbsize"]}** keys now. Every write goes in a ledger
            on disk first, so Cleanup removes these and nothing else.
            """),
            rs_seed,
        ]
    )
    return (rs_seed,)


@app.cell(column=0)
def _(rs_client, rs_dim, rs_embed, rs_seed):
    rs_ready = False
    _out = status_callout("info", "Nothing is written until you press the button above.")
    try:
        if pressed(rs_seed):
            with_spinner("Redis", lambda: rs_seed_corpus(rs_client, rs_embed, rs_dim))
        _info = rs_index_info(rs_client, rs_index())
        if _info and _info["docs"]:
            rs_ready = True
            _bad = (
                status_callout(
                    "warn",
                    f"{_info['failures']} documents failed to index. Usually a vector of the "
                    "wrong length for DIM.",
                )
                if _info["failures"]
                else mo.md("")
            )
            _out = mo.vstack(
                [
                    status_callout(
                        "ok",
                        f"**{_info['docs']}** articles are indexed and searchable. The index "
                        f"took its {rs_dim} dimensions and its `COSINE` metric from the command "
                        "below.",
                    ),
                    _bad,
                    wire_view(
                        "The FT.CREATE and one HSET, as redis-cli",
                        [
                            redis_cli(rs_create_args(rs_index(), rs_dim)),
                            redis_cli(rs_doc_args(rs_articles()[0], bytes(rs_dim * 4))),
                        ],
                    ),
                ]
            )
    except (ServiceError, redis.RedisError, Denied) as _exc:
        _out = status_callout("error", _exc)
    _out
    return (rs_ready,)


@app.cell(column=0)
def _(rs_ready):
    mo.stop(not rs_ready, None)
    rs_ft_query = mo.ui.text(value="hiking boots", label="Search", full_width=True)
    rs_ft_prefix = mo.ui.switch(label="also match prefixes (`boot*`)")
    rs_ft_fuzzy = mo.ui.switch(label="also match typos (`%boot%`)")
    mo.vstack(
        [
            mo.md(r"""
            ### 1 · Full-text search

            The oldest kind: match words. `BM25` scores an article higher for rare words and
            for words in the title, which counts double because the field is `WEIGHT 2`.
            """),
            rs_ft_query,
            mo.hstack([rs_ft_prefix, rs_ft_fuzzy], justify="start", wrap=True),
        ]
    )
    return rs_ft_fuzzy, rs_ft_prefix, rs_ft_query


@app.cell(column=0)
def _(rs_client, rs_ft_fuzzy, rs_ft_prefix, rs_ft_query):
    # Fetch. It reads only the query controls, so nothing here re-runs when a display switch flips.
    mo.stop(not rs_ft_query.value.strip(), mo.md("_Type a word or two._"))
    try:
        rs_ft_found = rs_search_text(
            rs_client, rs_ft_query.value, rs_ft_prefix.value, rs_ft_fuzzy.value
        )
    except redis.RedisError as _exc:
        mo.stop(True, rs_search_failed(_exc))
    return (rs_ft_found,)


@app.cell(column=0)
def _(rs_ft_found):
    _rows, _total = rs_ft_found["rows"], rs_ft_found["total"]
    _cut = f"; the top {len(_rows)} are shown" if _total > len(_rows) else ""
    mo.vstack(
        [
            mo.md(f"**{_total}** articles match{_cut}, best first.")
            if _rows
            else status_callout("warn", "No article contains those words."),
            plain_table(rs_hit_rows(_rows)) if _rows else mo.md(""),
            wire_view(
                "The FT.SEARCH, as redis-cli",
                redis_cli(rs_ft_found["argv"]) if rs_ft_found["argv"] else "no searchable words",
            ),
            notes(
                "This is plain keyword search. BM25 rewards rare words, and a title match "
                "counts double.",
                "Articles containing your words, best first. Try `bots` with the typo switch on.",
            ),
        ]
    )
    return


@app.cell(column=0)
def _(rs_ready):
    mo.stop(not rs_ready, None)
    rs_vec_query = mo.ui.text(value="will it fit my feet", label="Ask", full_width=True)
    rs_vec_k = mo.ui.slider(1, 10, step=1, value=3, debounce=True, show_value=True, label="k (KNN)")
    rs_vec_radius = mo.ui.slider(
        0.05, 1.0, step=0.05, value=0.5, debounce=True, show_value=True, label="radius (range)"
    )
    mo.vstack(
        [
            mo.md(r"""
            ### 2 · Vector search: KNN or a radius

            Two ways to ask the same index. `KNN` returns the k nearest articles whatever
            they are. `VECTOR_RANGE` returns everything within a distance, which may be nothing.
            """),
            rs_vec_query,
            rs_vec_k,
            rs_vec_radius,
        ]
    )
    return rs_vec_k, rs_vec_query, rs_vec_radius


@app.cell(column=0)
def _(rs_client, rs_embed, rs_vec_k, rs_vec_query, rs_vec_radius):
    mo.stop(not rs_vec_query.value.strip(), mo.md("_Type a question._"))
    try:
        rs_knn = rs_knn_search(rs_client, rs_embed, rs_vec_query.value, rs_vec_k.value)
        rs_range = rs_range_search(rs_client, rs_embed, rs_vec_query.value, rs_vec_radius.value)
    except redis.RedisError as _exc:
        mo.stop(True, rs_search_failed(_exc))
    return rs_knn, rs_range


@app.cell(column=0)
def _(rs_knn, rs_range):
    _hit = bool(rs_range["rows"])
    mo.vstack(
        [
            mo.md(
                f"**KNN**, `k = {rs_knn['k']}`: **{len(rs_knn['rows'])}** rows, as always. "
                "`dist` is cosine distance: 0 is identical, 2 is opposite."
            ),
            plain_table(rs_hit_rows(rs_knn["rows"])),
            mo.md(f"**VECTOR_RANGE**, `radius = {rs_range['radius']}`:"),
            status_callout(
                "ok" if _hit else "warn",
                f"**HIT**: {len(rs_range['rows'])} within the radius."
                if _hit
                else "**MISS**: nothing is that close. KNN could never say this.",
            ),
            plain_table(rs_hit_rows(rs_range["rows"])) if _hit else mo.md(""),
            wire_view(
                "The two FT.SEARCH commands, as redis-cli",
                [redis_cli(rs_knn["argv"]), redis_cli(rs_range["argv"])],
            ),
            notes(
                "KNN always hands back k rows, even for nonsense. A radius can hand back "
                "none, and that empty answer is a cache MISS.",
                "Ask `best pizza near me`: KNN still returns k articles, the range returns "
                "none. This is LangCache's threshold.",
            ),
        ]
    )
    return


@app.cell(column=0)
def _(rs_ready):
    mo.stop(not rs_ready, None)
    rs_pf_query = mo.ui.text(value="how long does it take", label="Ask", full_width=True)
    rs_pf_categories = mo.ui.multiselect(
        sorted({article["category"] for article in rs_articles()}),
        value=["returns", "shipping"],
        label="Category is any of",
    )
    rs_pf_years = mo.ui.range_slider(
        2022,
        2026,
        step=1,
        value=[2024, 2026],
        debounce=True,
        show_value=True,
        label="Reviewed between",
    )
    mo.vstack(
        [
            mo.md(r"""
            ### 3 · Pre-filter: narrow first, then search by meaning

            A `TAG` and a `NUMERIC` filter narrow the candidates *inside* the vector search, so
            you still get k results and every one of them qualifies.
            """),
            rs_pf_query,
            rs_pf_categories,
            rs_pf_years,
        ]
    )
    return rs_pf_categories, rs_pf_query, rs_pf_years


@app.cell(column=0)
def _(rs_client, rs_embed, rs_pf_categories, rs_pf_query, rs_pf_years):
    mo.stop(not rs_pf_query.value.strip(), mo.md("_Type a question._"))
    rs_pf_filter = rs_filter(rs_pf_categories.value, rs_pf_years.value)
    try:
        rs_pf_found = rs_knn_search(rs_client, rs_embed, rs_pf_query.value, 5, rs_pf_filter)
        rs_pf_plain = rs_knn_search(rs_client, rs_embed, rs_pf_query.value, 5)
    except redis.RedisError as _exc:
        mo.stop(True, rs_search_failed(_exc))
    return rs_pf_filter, rs_pf_found, rs_pf_plain


@app.cell(column=0)
def _(rs_pf_filter, rs_pf_found, rs_pf_plain):
    mo.vstack(
        [
            mo.md(f"**Filtered**, `{rs_pf_filter}`: **{len(rs_pf_found['rows'])}** rows"),
            plain_table(rs_hit_rows(rs_pf_found["rows"]))
            if rs_pf_found["rows"]
            else status_callout("warn", "Nothing passes that filter."),
            mo.md("**Unfiltered**, for comparison:"),
            plain_table(rs_hit_rows(rs_pf_plain["rows"])),
            wire_view("The filtered FT.SEARCH, as redis-cli", redis_cli(rs_pf_found["argv"])),
            notes(
                "The filter runs inside the vector search, so you get k results that all "
                "qualify, instead of a top k with the wrong ones thrown away.",
                "Every filtered row is in a ticked category and year range. The unfiltered "
                "list has rows from elsewhere.",
            ),
        ]
    )
    return


@app.cell(column=0)
def _(rs_ready):
    mo.stop(not rs_ready, None)
    _labels = [preset["label"] for preset in rs_wow_presets()]
    rs_wow_case = mo.ui.dropdown([*_labels, "Type your own"], value=_labels[0], label="Question")
    rs_wow_own = mo.ui.text(value="will it fit my feet", label="Your question", full_width=True)
    rs_wow_sort = mo.ui.radio(
        ["hybrid", "BM25", "vector"], value="hybrid", inline=True, label="Sort the table by"
    )
    mo.vstack(
        [
            mo.md(r"""
            ### Wow · one query, three engines

            The same question as keyword search (BM25), as vector search, and as both fused.
            One table shows the rank each engine gave every article. Pick a question, or
            choose *Type your own* and write one in the box.
            """),
            rs_wow_case,
            rs_wow_own,
            rs_wow_sort,
        ]
    )
    return rs_wow_case, rs_wow_own, rs_wow_sort


@app.cell(column=0)
def _(rs_client, rs_embed, rs_has_hybrid, rs_wow_case, rs_wow_own):
    # Fetch: three commands, only when the question changes. Sorting the table only re-renders it.
    _ask = rs_wow_preset(rs_wow_case.value, rs_wow_own.value)
    mo.stop(not rs_text_query(_ask["query"]), mo.md("_Type a word or two._"))
    try:
        rs_wow = rs_wow_search(rs_client, rs_embed, _ask, rs_has_hybrid)
    except redis.RedisError as _exc:
        mo.stop(True, rs_search_failed(_exc))
    rs_wow_trace = render_redis_trace(len(rs_wow["commands"]))
    return rs_wow, rs_wow_trace


@app.cell(column=0)
def _(rs_wow, rs_wow_sort, rs_wow_trace):
    mo.vstack(
        [
            mo.md(f"Query: **{rs_wow['preset']['query']}**"),
            plain_table(rs_rank_rows(rs_wow, rs_wow_sort.value)),
            mo.md(rs_wow_summary(rs_wow)),
            mo.md(rs_wow["preset"]["lesson"]),
            status_callout(
                "info" if rs_wow["source"] == "FT.HYBRID" else "warn",
                f"Hybrid column: **{rs_wow['source']}**. {rs_wow['note']}".strip(),
            ),
            wire_view("The three commands, as redis-cli", rs_wow["commands"]),
            mo.accordion({"Command trace": rs_wow_trace}),
            notes(
                "Same question, three engines. Keyword search matches words, vector search "
                "matches meaning, hybrid fuses the two rankings.",
                "Step through the questions: BM25 finds nothing for the paraphrase and the "
                "typo, the vector search is only guessing at the product name, and hybrid "
                "has the starred article first each time. Then type your own.",
            ),
        ]
    )
    return


@app.cell(column=0)
def _():
    rs_cleanup_btn = mo.ui.run_button(label="Clean up this run", kind="danger")
    rs_reset_btn = mo.ui.run_button(label="Reset the column")
    mo.vstack(
        [
            mo.md(rf"""
            ### Clean up · Search

            Drops the indexes and unlinks the keys that this run's ledger names, and nothing
            else: never `DD`, never a scan-and-delete. The cells above stay as they are, so a
            search after *Clean up* finds no index; *Reset* cleans up, then clears them and puts
            the column back to "press Test connection". Keys expire by themselves after
            {KEY_TTL_S // 60} minutes anyway.
            """),
            mo.hstack([rs_cleanup_btn, rs_reset_btn], justify="start", wrap=True),
        ]
    )
    return rs_cleanup_btn, rs_reset_btn


@app.cell(column=0)
def _(rs_cleanup_btn, rs_reset_btn, rs_set_gen, rs_url):
    # Cleanup depends on the config and its own buttons, and on nothing that can stop or
    # fail: it has to work when every cell above it is stuck. Reset is a plain .value, so
    # it never autoruns.
    mo.stop(not (pressed(rs_cleanup_btn, stage="cleanup") or rs_reset_btn.value), None)
    try:
        _url = redis_url_secret(rs_url.value)
        if _url is None:
            _out = not_configured("Redis Search", ["REDIS_URL"])
        else:
            _done = with_spinner("Redis", lambda: rs_cleanup(connect_redis(_url.reveal())))
            _out = status_callout("ok", rs_cleanup_report(_done))
            if rs_reset_btn.value:
                rs_set_gen(lambda n: n + 1)
    except (ValueError, ServiceError, redis.RedisError, Denied) as _exc:
        _out = status_callout("error", _exc)
    _out
    return


@app.cell(column=0)
def _(rs_ready):
    mo.stop(not rs_ready, None)
    rs_deep = mo.ui.switch(value=AUTORUN == "demo", label="Deep dive: index anatomy")
    rs_deep
    return (rs_deep,)


@app.cell(column=0)
def _(rs_deep, rs_has_svs):
    mo.stop(not rs_deep.value, None)
    rs_algo = mo.ui.dropdown(
        ["FLAT", "HNSW", "SVS-VAMANA"] if rs_has_svs else ["FLAT", "HNSW"],
        value="FLAT",
        label="Algorithm",
    )
    rs_metric = mo.ui.dropdown(["COSINE", "IP", "L2"], value="COSINE", label="Distance metric")
    rs_build = mo.ui.run_button(label=f"Build {rs_index()}2 over the same documents")
    mo.vstack(
        [
            mo.md(r"""
            #### Index anatomy

            One set of hashes can be indexed twice. `FLAT` compares against every vector,
            `HNSW` walks a graph. Build a second index over the same key prefix, then compare
            what `FT.INFO` says. It is ledgered, so Cleanup drops it.
            """),
            mo.hstack([rs_algo, rs_metric], justify="start", wrap=True),
            rs_build,
        ]
    )
    return rs_algo, rs_build, rs_metric


@app.cell(column=0)
def _(rs_algo, rs_build, rs_client, rs_dim, rs_embed, rs_metric):
    mo.stop(not pressed(rs_build), None)
    try:
        _second = with_spinner(
            "Redis", lambda: rs_build_second(rs_client, rs_dim, rs_algo.value, rs_metric.value)
        )
        _compared = rs_compare_indexes(
            rs_client,
            rs_embed,
            "will it fit my feet",
            {rs_index(): "HNSW · COSINE", _second: f"{rs_algo.value} · {rs_metric.value}"},
        )
        _out = mo.vstack(
            [
                plain_table(_compared["rows"]),
                mo.md(f"{_compared['note']} ANN parameters matter at 10⁵+ documents."),
                wire_view(
                    "The second FT.CREATE, as redis-cli",
                    redis_cli(rs_create_args(_second, rs_dim, rs_algo.value, rs_metric.value)),
                ),
            ]
        )
    except (ServiceError, redis.RedisError, Denied) as _exc:
        _out = status_callout("error", _exc)
    _out
    return


@app.cell(column=1, hide_code=True)
def _():
    # 1 · LangCache: title
    _direction = "TB" if mo.app_meta().mode == "run" else "LR"
    mo.vstack(
        [
            mo.md(r"""
            # 1 · LangCache

            LangCache is a managed semantic cache for LLM answers. It embeds each prompt, and
            when a new prompt means nearly the same as a stored one it returns the stored answer
            instead of paying for another model call. The catch is the similarity threshold: set
            it too loose and a confident wrong answer comes back for a question that only sounds
            similar. In this column you read one raw hit, score a set of labelled paraphrases and
            near-misses against a threshold you slide, and run a cache-first chat. No LLM is
            involved anywhere. You need the host, cache ID and API key of a LangCache service,
            ideally one just for this notebook, because a cache is shared by everything that
            points at it.

            **Docs:** [LangCache](https://redis.io/docs/latest/develop/ai/context-engine/langcache/)
            · **Create one:** [create a LangCache service](https://redis.io/docs/latest/operate/iris/langcache/create-service/)
            """),
            mo.mermaid(
                f"flowchart {_direction}"
                """
                  P[Prompt] --> S{LangCache search}
                  S -->|hit| A[Stored answer]
                  S -->|miss| L[LLM call]
                  L --> W[Store the answer]
                  W -.-> S
                """
            ),
        ]
    )
    return


@app.cell(column=1, hide_code=True)
def _():
    # 1 · LangCache: what to connect to
    mo.vstack(
        [
            mo.md(r"""
            ### Connect to your cache

            This column needs a LangCache service. Fill in its host and Cache ID and paste its
            API key in the fields below, or leave a field empty to use `LANGCACHE_HOST`,
            `LANGCACHE_CACHE_ID` and `LANGCACHE_API_KEY` from the environment or `.env`. The key
            is never shown, saved or prefilled. The scope attribute is optional: name one your
            cache defines, and this run's entries are tagged with it and every search filters
            on it.
            """),
            mo.accordion(
                {
                    "Where do I get these?": mo.md(r"""
                    - **No service yet?** In [cloud.redis.io](https://cloud.redis.io) open
                      *LangCache* in the left menu and create a service
                      ([steps](https://redis.io/docs/latest/operate/iris/langcache/create-service/)).
                      *Quick create* uses your free database. Make it for this notebook alone: a
                      cache is shared by everything that points at it.
                    - **Host and Cache ID:** open the service, then *Configuration*,
                      *Connectivity*. Copy a URL from the *URL List* (the closest one) and the
                      *Cache ID*. The host works with or without `https://`.
                    - **API key:** the console shows it once, in the dialog after the service is
                      created. *Generate key* makes another and kills the old one at once.
                    - **Scope attribute:** a cache can tag entries with attributes, but only ones
                      defined when it was created (*Configuration*, *Attributes*; up to five, and
                      never editable later). Leave this empty if it has none.
                    """)
                }
            ),
            status_callout(
                "warn",
                "**Never on a shared cache.** Flush, Delete service and Generate key are never "
                "used here. This column stores only entries that expire (15 minutes; 2 for a "
                "taught answer) and deletes only by id, but until they do, any app that searches "
                "the same cache can be served them. A LangCache service of your own for this "
                "notebook is the only real isolation.",
            ),
        ]
    )
    return


@app.cell(column=1)
def _():
    lc_host = mo.ui.text(
        value=env("LANGCACHE_HOST"),
        placeholder="https://<region>.langcache.redis.io",
        label="Host",
        full_width=True,
    )
    lc_cache_id = mo.ui.text(
        value=env("LANGCACHE_CACHE_ID"),
        placeholder="paste the Cache ID",
        label="Cache ID",
        full_width=True,
    )
    lc_key = SecretText("LANGCACHE_API_KEY", label="API key")
    lc_scope = mo.ui.text(
        value=env("LANGCACHE_SCOPE_ATTR"),
        placeholder="empty: the cache has no attribute for this",
        label="Scope attribute (optional)",
        full_width=True,
    )
    lc_connect = mo.ui.run_button(label="Test connection (read-only)")
    # Reset bumps this token. The connection cell reads it, so the column drops back to
    # "press Test connection" and every cell below it clears.
    lc_gen, lc_set_gen = mo.state(0)
    mo.vstack([lc_host, lc_cache_id, lc_key, lc_scope, lc_connect])
    return (
        lc_cache_id,
        lc_connect,
        lc_gen,
        lc_host,
        lc_key,
        lc_scope,
        lc_set_gen,
    )


@app.cell(column=1)
def _(lc_cache_id, lc_connect, lc_gen, lc_host, lc_key, lc_scope):
    lc_gen()  # Reset bumps this token, which re-runs the cell and drops the connection
    lc_conn = None
    try:
        _conn, _missing = lc_connection(
            lc_host.value, lc_cache_id.value, lc_key.value, lc_scope.value
        )
        if _conn is None:
            _status = mo.vstack([not_configured("LangCache", _missing), recorded_live("lc")])
        elif not pressed(lc_connect):
            _status = status_callout(
                "info",
                "Press **Test connection**. It only reads: one search for a made-up prompt.",
            )
        else:
            _facts = with_spinner("LangCache", lambda: lc_probe_cache(_conn))
            lc_conn = {**_conn, "scope": _facts["scope"]}
            _status = lc_describe(lc_conn, _facts)
    except (ValueError, ServiceError) as _exc:
        lc_conn, _status = None, lc_failed(_exc)
    _status
    return (lc_conn,)


@app.cell(column=1)
def _(lc_conn):
    mo.stop(lc_conn is None, None)
    lc_seed = mo.ui.run_button(label=f"Write {len(lc_seeds())} cache entries")
    _first = next(iter(lc_seeds().values()))
    _tagged = (
        f"Each is tagged `{lc_conn['scope']}: {RUN_TAG}`."
        if lc_conn["scope"]
        else "Nothing tags them, so any app that searches this cache can be served them."
    )
    mo.vstack(
        [
            mo.md(rf"""
            ### Store the gear-care answers

            Pressing the button writes exactly this to cache `{lc_conn["cache_id"]}`:
            **{len(lc_seeds())}** entries, one `POST .../entries` each, holding a Cairn Outfitters
            gear-care question and its answer, with `ttlMillis` {LC_TTL_MS} ({LC_TTL_MS // 60_000}
            minutes). {_tagged} LangCache cannot list what it holds, so nothing here can look for
            another run's leftovers: the expiry is the safety net, and a ledger on disk is how
            Clean up finds these by id.
            """),
            mo.md("\n".join(f"- `{key}`: {seed['prompt']}" for key, seed in lc_seeds().items())),
            wire_view(
                "The request body of the first write, as JSON",
                lc_set_body(_first["prompt"], _first["response"], LC_TTL_MS, lc_conn["scope"]),
            ),
            lc_seed,
        ]
    )
    return (lc_seed,)


@app.cell(column=1)
def _(lc_conn, lc_seed):
    lc_ready, lc_floor = False, 0.5
    _out = status_callout("info", "Nothing is written until you press the button above.")
    try:
        if pressed(lc_seed):
            with_spinner("LangCache", lambda: lc_write_seeds(lc_conn))
        # Entries from an earlier press, or an earlier kernel of this run, count too.
        if ledger_live("lc", "entry"):
            _probe = with_spinner("LangCache", lambda: lc_probe_floor(lc_conn))
            if _probe["lowest"] is not None:
                lc_ready, lc_floor = True, _probe["floor"]
                _out = status_callout("ok", lc_floor_message(_probe))
            else:
                _out = status_callout(
                    "warn",
                    "The cache returned none of this run's entries. They have expired, or the "
                    "cache has not indexed them yet: press the button again in a moment.",
                )
    except ServiceError as _exc:
        _out = lc_failed(_exc)
    _out
    return lc_floor, lc_ready


@app.cell(column=1)
def _(lc_floor, lc_ready):
    mo.stop(not lc_ready, None)
    lc_anat_query = mo.ui.text(
        value="How can I get my rain jacket to bead water again?",
        label="Ask",
        full_width=True,
    )
    lc_anat_threshold = mo.ui.slider(
        lc_floor,
        1.0,
        step=0.01,
        value=max(0.85, lc_floor),
        debounce=True,
        show_value=True,
        label="similarityThreshold",
    )
    mo.vstack(
        [
            mo.md(r"""
            ### 1 · Anatomy of a hit

            One search is one `POST`: a prompt and a threshold go up, and every stored entry at
            least that similar comes back with its score. Change the question or the threshold and
            watch the request and the answer.
            """),
            lc_anat_query,
            lc_anat_threshold,
        ]
    )
    return lc_anat_query, lc_anat_threshold


@app.cell(column=1)
def _(lc_anat_query, lc_anat_threshold, lc_conn):
    mo.stop(not lc_anat_query.value.strip(), mo.md("_Type a question._"))
    try:
        lc_anat = with_spinner(
            "LangCache",
            lambda: lc_search(lc_conn, lc_anat_query.value.strip(), lc_anat_threshold.value),
        )
    except ServiceError as _exc:
        mo.stop(True, lc_failed(_exc))
    return (lc_anat,)


@app.cell(column=1)
def _(lc_anat):
    _mine = lc_anat["mine"]
    mo.vstack(
        [
            status_callout(
                "ok",
                f"**HIT** at {lc_anat['threshold']:.2f}: the closest of this run's entries scored "
                f"**{_mine[0]['similarity']:.3f}**, so its answer would be served.",
            )
            if _mine
            else status_callout(
                "warn",
                f"**MISS** at {lc_anat['threshold']:.2f}: none of this run's entries is that "
                "similar. This is where an LLM would be called.",
            ),
            plain_table(lc_hit_rows(_mine, lc_anat["foreign"]))
            if _mine or lc_anat["foreign"]
            else mo.md(""),
            wire_view("The request and the reply, as HTTP", lc_anat["call"]),
            notes(
                "One search is one POST. The prompt, a threshold and the strategy go up, and "
                "every entry at least that similar comes back with its score.",
                "A HIT for the rain-jacket question near 0.9. Drag the threshold above that "
                "score and the same question becomes a MISS.",
            ),
        ]
    )
    return


@app.cell(column=1)
def _(lc_floor, lc_ready):
    mo.stop(not lc_ready, None)
    lc_lab_go = mo.ui.run_button(label=f"Search the {len(lc_probes())} questions once")
    lc_lab_threshold = mo.ui.slider(
        lc_floor,
        1.0,
        step=0.01,
        value=max(0.85, lc_floor),
        debounce=True,
        show_value=True,
        label="Threshold",
    )
    mo.vstack(
        [
            mo.md(rf"""
            ### 2 · Threshold lab

            {len(lc_probes())} questions, each asked **once** at the floor
            (`similarityThreshold` {lc_floor:.2f}), so the cache reports the closest of your
            entries and how close. The slider then decides, on this machine, which of them would
            have counted as a HIT. Green: the right answer was served. Red: a **false hit**, a
            wrong answer served with confidence. Amber: a paraphrase the threshold turned away.
            Grey: another app's entry.
            """),
            lc_lab_go,
            lc_lab_threshold,
        ]
    )
    return lc_lab_go, lc_lab_threshold


@app.cell(column=1)
def _(lc_conn, lc_floor, lc_lab_go):
    mo.stop(not pressed(lc_lab_go), mo.md("_Press the button to search._"))
    try:
        lc_lab = with_spinner("LangCache", lambda: lc_sweep(lc_conn, lc_probes(), lc_floor))
    except ServiceError as _exc:
        mo.stop(True, lc_failed(_exc))
    return (lc_lab,)


@app.cell(column=1)
def _(lc_floor, lc_lab, lc_lab_threshold):
    mo.vstack(
        [
            lc_lab_tiles(lc_scoreboard(lc_lab, lc_lab_threshold.value)),
            lc_lab_table(lc_lab_rows(lc_lab, lc_lab_threshold.value, lc_floor)),
            mo.md(lc_lab_summary(lc_lab)),
            wire_view("The first of the searches, as HTTP", lc_lab[0]["call"]),
            notes(
                "Similar is not the same. The cache scores meaning, so a question about a "
                "synthetic bag can sit as close to the down-bag entry as a true paraphrase does.",
                "Green rows are paraphrases served correctly, red rows are wrong answers served "
                "anyway. Slide up until the red is gone and watch recall drop with it. In a live "
                "run the worst false hit scored 0.950, and at 0.96, where none is left, only 1 of "
                "the 6 paraphrases still hit.",
            ),
        ]
    )
    return


@app.cell(column=1)
def _(lc_floor, lc_ready):
    mo.stop(not lc_ready, None)
    lc_strat_go = mo.ui.run_button(label=f"Search {len(lc_variants())} variants three ways")
    lc_strat_threshold = mo.ui.slider(
        lc_floor,
        1.0,
        step=0.01,
        value=max(0.85, lc_floor),
        debounce=True,
        show_value=True,
        label="Threshold",
    )
    mo.vstack(
        [
            mo.md(r"""
            ### 3 · searchStrategies: exact, semantic or both

            `exact` is a case-insensitive match of the whole prompt: cheap and safe. `semantic`
            compares meaning. Both together try `exact` first. Here is one stored question, asked
            with its case, punctuation and wording changed, under each setting.
            """),
            lc_strat_go,
            lc_strat_threshold,
        ]
    )
    return lc_strat_go, lc_strat_threshold


@app.cell(column=1)
def _(lc_conn, lc_floor, lc_strat_go):
    mo.stop(not pressed(lc_strat_go), mo.md("_Press the button to search._"))
    try:
        lc_strat = with_spinner(
            "LangCache", lambda: lc_strategy_sweep(lc_conn, lc_variants(), lc_floor)
        )
    except ServiceError as _exc:
        mo.stop(True, lc_failed(_exc))
    return (lc_strat,)


@app.cell(column=1)
def _(lc_strat, lc_strat_threshold):
    mo.vstack(
        [
            mo.md(
                f"Stored: **{lc_seeds()['bag']['prompt']}** · a semantic score below "
                f"{lc_strat_threshold.value:.2f} is a miss."
            ),
            plain_table(lc_strategy_rows(lc_strat, lc_strat_threshold.value)),
            wire_view("The first variant with both strategies, as HTTP", lc_strat[0]["call"]),
            notes(
                "exact is cheap and safe, semantic is the recall booster, and asking for both "
                "tries exact first.",
                "Case changes hit under exact, even shouting, which semantic missed in a live run. "
                "A dropped question mark or a new wording does not hit under exact. Only "
                "semantic catches those, and it is the one that can be wrong.",
            ),
        ]
    )
    return


@app.cell(column=1)
def _(lc_gen):
    lc_gen()  # Reset re-runs this cell, which is what empties the chat; nothing else does
    lc_history, lc_taught = [], set()  # one dict per chat turn; the entries taught this session
    return lc_history, lc_taught


@app.cell(column=1)
def _(lc_floor, lc_ready):
    mo.stop(not lc_ready, None)
    lc_chat_q = mo.ui.text(value=lc_script()["ask"], label="Ask", full_width=True)
    lc_send = mo.ui.run_button(label="Send")
    lc_chat_threshold = mo.ui.slider(
        lc_floor,
        1.0,
        step=0.01,
        value=max(0.8, lc_floor),
        debounce=True,
        show_value=True,
        label="Threshold",
    )
    lc_teach_answer = mo.ui.text_area(
        value=lc_script()["answer"], label="The answer to store", full_width=True, rows=3
    )
    lc_teach_btn = mo.ui.run_button(label=f"Teach it (kept {LC_TEACH_TTL_MS // 1000} s)")
    mo.vstack(
        [
            mo.md(r"""
            ### Wow · a chat that asks the cache first

            Every **Send** searches the cache before anything else. A **HIT** answers from the
            cache, with a badge showing how it matched. A **MISS** is where a real assistant
            would call its LLM; there is none here, so you *teach* the answer instead and ask
            again in other words. A taught answer is deleted the moment it has been served.
            """),
            lc_chat_q,
            mo.hstack([lc_send, lc_chat_threshold], justify="start", wrap=True),
        ]
    )
    return lc_chat_q, lc_chat_threshold, lc_send, lc_teach_answer, lc_teach_btn


@app.cell(column=1)
def _(
    lc_chat_q,
    lc_chat_threshold,
    lc_conn,
    lc_history,
    lc_send,
    lc_taught,
    lc_teach_answer,
    lc_teach_btn,
):
    # The history lives in a cell that only Reset re-runs, so a click adds one turn and every
    # other reason for this cell to run (the slider, the notes switch) only redraws them.
    _fail = mo.md("")
    try:
        if AUTORUN == "demo" and not lc_history:
            with_spinner(
                "LangCache",
                lambda: lc_autorun_chat(lc_conn, lc_history, lc_taught, lc_chat_threshold.value),
            )
        elif lc_send.value:
            with_spinner(
                "LangCache",
                lambda: lc_ask(lc_conn, lc_history, lc_chat_q.value, lc_chat_threshold.value),
            )
        elif lc_teach_btn.value:
            with_spinner(
                "LangCache",
                lambda: lc_teach(lc_conn, lc_history, lc_taught, lc_teach_answer.value),
            )
    except ServiceError as _exc:
        _fail = lc_failed(_exc)
    lc_last = lc_history[-1] if lc_history else None
    _asked = [turn for turn in lc_history if turn["kind"] == "ask"]
    # Teach is for the latest question, while it missed and is not taught. A refusal or a
    # blank Send after it leaves it open, so its controls stay for another try.
    _open = _asked[-1] if _asked and not (_asked[-1]["hit"] or _asked[-1].get("taught")) else None
    _turns = []
    for _turn in reversed(lc_history):
        _turns.append(lc_turn_view(_turn))
        if _turn is _open:  # the teach controls sit right under the question that missed
            _turns.append(
                mo.vstack(
                    [
                        mo.md(
                            "**Teach it.** It will be stored as "
                            f"*{lc_taught_prompt(_turn['question'])}*, and only if the "
                            "question mentions Cairn gear."
                        ),
                        status_callout("error", lc_shared_warning())
                        if not lc_conn["scope"]
                        else mo.md(""),
                        lc_teach_answer,
                        lc_teach_btn,
                    ]
                )
            )
    mo.vstack(
        [
            _fail,
            *_turns,
            wire_view("The newest search, as HTTP", _asked[-1]["call"]) if _asked else mo.md(""),
            notes(
                "A cache-first assistant asks the cache before the model. A MISS is where the "
                "LLM would run; here you teach the answer, then ask again in other words.",
                "The first question misses. After Teach, ask it in other words (try: "
                f"{lc_script()['again']}): a HIT with its badge, and the taught entry is "
                "deleted straight away.",
            ),
        ]
    )
    return (lc_last,)


@app.cell(column=1)
def _(lc_conn, lc_last, lc_taught):
    # It runs after the chat cell has drawn the HIT: a taught answer is served once, then goes.
    _hit = lc_last["hit"] if lc_last and lc_last["kind"] == "ask" else None
    mo.stop(not (_hit and _hit["id"] in lc_taught), None)
    try:
        _state = lc_delete(lc_conn, _hit["id"])
        lc_taught.discard(_hit["id"])
        _out = status_callout(
            "info",
            f"The taught entry `{_hit['id']}` is gone ({_state}): it was served once, and no "
            "other app that shares this cache will be.",
        )
    except ServiceError as _exc:
        _out = lc_failed(_exc)
    _out
    return


@app.cell(column=1)
def _():
    lc_cleanup_btn = mo.ui.run_button(label="Clean up this run", kind="danger")
    lc_reset_btn = mo.ui.run_button(label="Reset the column")
    mo.vstack(
        [
            mo.md(rf"""
            ### Clean up · LangCache

            Deletes, by id, every entry that this run's ledger names: the stored answers, any
            taught answer and the TTL test. There is no Flush and no delete by attribute, and an
            id the cache no longer knows has expired and counts as done. The cells above stay as
            they are, so a search after *Clean up* finds nothing; *Reset* cleans up, then clears
            them and puts the column back to "press Test connection". Entries expire by
            themselves after {LC_TTL_MS // 60_000} minutes anyway.
            """),
            mo.hstack([lc_cleanup_btn, lc_reset_btn], justify="start", wrap=True),
        ]
    )
    return lc_cleanup_btn, lc_reset_btn


@app.cell(column=1)
def _(
    lc_cache_id,
    lc_cleanup_btn,
    lc_host,
    lc_key,
    lc_reset_btn,
    lc_scope,
    lc_set_gen,
):
    # Cleanup depends on the config and its own buttons, and on nothing that can stop or
    # fail: it has to work when every cell above it is stuck. Reset is a plain .value, so
    # it never autoruns.
    mo.stop(not (pressed(lc_cleanup_btn, stage="cleanup") or lc_reset_btn.value), None)
    try:
        _conn, _missing = lc_connection(
            lc_host.value, lc_cache_id.value, lc_key.value, lc_scope.value
        )
        if _conn is None:
            _out = not_configured("LangCache", _missing)
        else:
            _done = with_spinner("LangCache", lambda: lc_cleanup(_conn))
            _out = status_callout("ok", lc_cleanup_report(_done))
            if lc_reset_btn.value:
                lc_set_gen(lambda n: n + 1)
    except (ValueError, ServiceError) as _exc:
        _out = lc_failed(_exc)
    _out
    return


@app.cell(column=1)
def _(lc_ready):
    mo.stop(not lc_ready, None)
    lc_deep = mo.ui.switch(value=AUTORUN == "demo", label="Deep dive: expiry (TTL)")
    lc_deep
    return (lc_deep,)


@app.cell(column=1)
def _(lc_deep):
    mo.stop(not lc_deep.value, None)
    lc_ttl_go = mo.ui.run_button(label="Store a 10-second entry")
    lc_ttl_check = mo.ui.run_button(label="Check again")
    mo.vstack(
        [
            mo.md(r"""
            #### Expiry

            Every entry here carries a `ttlMillis`, and that is what guards a shared cache if this
            notebook dies. Store one for ten seconds, search for it, then press *Check again*
            after it has expired. The first miss is followed by a delete by id: a `404` means the
            cache has already let it go.
            """),
            mo.hstack([lc_ttl_go, lc_ttl_check], justify="start", wrap=True),
        ]
    )
    return lc_ttl_check, lc_ttl_go


@app.cell(column=1)
def _(lc_conn, lc_ttl_go):
    mo.stop(not pressed(lc_ttl_go), None)
    try:
        lc_ttl_entry = with_spinner("LangCache", lambda: lc_ttl_store(lc_conn))
    except ServiceError as _exc:
        mo.stop(True, lc_failed(_exc))
    mo.md(
        f"Stored entry `{lc_ttl_entry['id']}` with `ttlMillis` 10000. Press *Check again* to "
        "search for it."
    )
    return (lc_ttl_entry,)


@app.cell(column=1)
def _(lc_conn, lc_ttl_check, lc_ttl_entry):
    _out = mo.md("_No checks yet._")
    try:
        if AUTORUN == "demo" and not lc_ttl_entry["checks"]:
            with_spinner("LangCache", lambda: lc_ttl_script(lc_conn, lc_ttl_entry))
        elif lc_ttl_check.value:
            with_spinner("LangCache", lambda: lc_ttl_look(lc_conn, lc_ttl_entry))
        if lc_ttl_entry["checks"]:
            _out = plain_table(lc_ttl_entry["checks"])
    except ServiceError as _exc:
        _out = lc_failed(_exc)
    _out
    return


@app.cell(column=2, hide_code=True)
def _():
    # 2 · Context Retriever: title
    _direction = "TB" if mo.app_meta().mode == "run" else "LR"
    mo.vstack(
        [
            mo.md(r"""
            # 2 · Context Retriever

            Context Retriever turns a data model into governed tools for agents. You describe
            your entities and which fields are searchable, the service builds a surface of MCP
            tools over your own Redis JSON data, and an agent calls those tools instead of
            writing raw queries. In this column you change the data model and watch the
            predicted tools change, create a surface for real, then call its tools yourself and
            try to break them. It has the heaviest setup of the four: an admin key from the
            console, and a Redis database the hosted service can reach, because the service, not
            this notebook, writes the data and builds the indexes.

            **Docs:** [Context Retriever](https://redis.io/docs/latest/develop/ai/context-engine/context-retriever/)
            · **Create one:** [create a Context Retriever service](https://redis.io/docs/latest/operate/iris/context-retriever/create-service)
            """),
            mo.mermaid(
                f"flowchart {_direction}"
                """
                  M[ContextModel] -->|admin API| S[Surface]
                  S --> T[MCP tools]
                  G[Agent + key] -->|MCP| T
                  T --> R[(Your Redis: JSON + indexes)]
                """
            ),
        ]
    )
    return


@app.cell(column=2, hide_code=True)
def _():
    # 2 · Context Retriever: what to connect to
    mo.vstack(
        [
            mo.md(r"""
            ### Connect to Context Retriever

            This column needs two secrets: an **admin key** for your Context Retriever service,
            and the **URL of a Redis database** with Search and JSON that the hosted service can
            reach over the internet. Paste them in the fields below, or leave a field empty to
            use `CTX_ADMIN_KEY` and `REDIS_URL` (or `REDIS_HOST` and `REDIS_PASSWORD`) from the
            environment or `.env`. Neither is ever shown, saved or prefilled.
            """),
            mo.accordion(
                {
                    "Where do I get these?": mo.md(r"""
                    - **The admin key:** in [cloud.redis.io](https://cloud.redis.io)
                      open *Context Retriever*, the *Admin keys* tab, *New admin key*, then
                      *Generate key* and copy it: it is shown once. The tab only works after you
                      have created a Context Retriever service
                      ([how](https://redis.io/docs/latest/operate/iris/context-retriever/create-service)).
                      There is a route without the console too: `ctxctl auth login`, then
                      `ctxctl --output json admin create --name notebook`.
                    - **The database:** in [cloud.redis.io](https://cloud.redis.io) open
                      *Databases*, your database, *Configuration*: the *Public endpoint* is
                      `host:port`. The password is under *Security*, *Default user*, behind the eye
                      icon. Put them together as `redis://default:<password>@<host>:<port>`
                      (`rediss://` if TLS is on). It is the same `REDIS_URL` as column 0.
                    - The hosted service connects from the internet, so `localhost` will not do.
                      The database needs Search and JSON (Redis 7.4 or later); Flex and
                      Active-Active databases are not supported. The free tier allows 30
                      connections and this column uses two.
                    """)
                }
            ),
            status_callout(
                "warn",
                "**The hosted service is given your database password.** Creating a surface "
                "sends it in the request and the service keeps it to reach your database, so "
                "use a scratch database. Clean up deletes the surface, which ends that.",
            ),
            status_callout(
                "warn",
                "**Never on a shared service.** Delete service and Generate key are never used "
                "here. The admin key can list, change and delete every surface in your "
                "account, so this column deletes only the surface it made, or, after you type a "
                "confirmation, old ones this notebook stamped. It never sends a `PUT`.",
            ),
        ]
    )
    return


@app.cell(column=2)
def _():
    cr_admin_key = SecretText("CTX_ADMIN_KEY", label="CTX_ADMIN_KEY")
    cr_url = SecretText("REDIS_URL", label="REDIS_URL")
    # Addresses are settings, not secrets, so they are prefilled. The defaults are the ones
    # cr_endpoints() falls back to when a field is emptied.
    cr_api_url = mo.ui.text(
        value=env("CTX_API_URL") or "https://cloud.redis.io/context-surfaces",
        label="Admin API",
        full_width=True,
    )
    cr_mcp_url = mo.ui.text(
        value=env("CTX_MCP_URL") or "https://gcp-us-east4.context-surfaces.redis.io/mcp",
        label="MCP endpoint",
        full_width=True,
    )
    cr_connect = mo.ui.run_button(label="Test connection (read-only)")
    # Reset bumps this token. The connection cell reads it, so the column drops back to
    # "press Test connection" and every cell below it clears.
    cr_gen, cr_set_gen = mo.state(0)
    mo.vstack(
        [
            cr_admin_key,
            cr_url,
            cr_connect,
            mo.accordion(
                {
                    "Endpoints (the defaults suit Redis Cloud)": mo.vstack(
                        [
                            mo.md(
                                "Only a self-managed install or another region needs these. The "
                                "MCP address is used only when the service names none for the "
                                "agent key."
                            ),
                            cr_api_url,
                            cr_mcp_url,
                        ]
                    ),
                }
            ),
        ]
    )
    return (
        cr_admin_key,
        cr_api_url,
        cr_connect,
        cr_gen,
        cr_mcp_url,
        cr_set_gen,
        cr_url,
    )


@app.cell(column=2)
def _(cr_admin_key, cr_api_url, cr_connect, cr_gen, cr_mcp_url, cr_url):
    cr_gen()  # Reset bumps this token, which re-runs the cell and drops the connection
    cr_conn = None
    try:
        _admin = resolve_secret("CTX_ADMIN_KEY", cr_admin_key.value)
        _url = redis_url_secret(cr_url.value)
        _missing = [n for n, v in (("CTX_ADMIN_KEY", _admin), ("REDIS_URL", _url)) if v is None]
        if _missing:
            _status = mo.vstack(
                [
                    not_configured(
                        "Context Retriever",
                        _missing,
                        offline="The data-model explorer below works without them.",
                    ),
                    recorded_live("cr"),
                ]
            )
        elif not pressed(cr_connect):
            _status = status_callout(
                "info",
                "Press **Test connection**. It only reads: a `GET` of your surfaces, and "
                "`PING`, `INFO`, `FT._LIST`, `DBSIZE` and a `JSON.GET` of a key that is not "
                "there on your database.",
            )
        else:
            cr_conn = with_spinner(
                "Context Retriever",
                lambda: cr_probe(_admin, _url, cr_api_url.value, cr_mcp_url.value),
            )
            _status = cr_describe(cr_conn)
    except (ValueError, ServiceError, redis.RedisError) as _exc:
        cr_conn, _status = None, cr_failed(_exc)
    _status
    return (cr_conn,)


@app.cell(column=2)
def _(cr_conn):
    mo.stop(cr_conn is None, None)
    cr_orphan_confirm = mo.ui.text(placeholder=f'type "{ORPHAN_CONFIRM}"', label="Confirm")
    cr_orphan_drop = mo.ui.run_button(label="Delete those surfaces", kind="danger")
    _left = cr_orphans(cr_conn["surfaces"])
    _body = []
    if _left["rows"]:
        _body += [
            mo.md(
                "A surface outlives its run and holds a connection to your database. These are "
                "stamped as this notebook's and belong to other runs. Only those older than "
                f"{ORPHAN_AGE_H} h are offered: a younger one may be a colleague's live demo. "
                "Deleting a surface does not remove the data it wrote (the docs say so), though "
                "the service drops its own indexes with it; the keys expire on their own after "
                f"{CR_KEY_TTL_S // 3600} h."
            ),
            plain_table(_left["rows"]),
        ]
    if _left["stale"]:
        _body.append(mo.hstack([cr_orphan_confirm, cr_orphan_drop], justify="start", wrap=True))
    mo.accordion(
        {
            f"Leftover surfaces from other runs ({len(_left['rows'])})": mo.vstack(
                _body or [mo.md("None found. Every earlier run has cleaned up.")]
            )
        }
    )
    return cr_orphan_confirm, cr_orphan_drop


@app.cell(column=2)
def _(cr_conn, cr_orphan_confirm, cr_orphan_drop):
    # A plain .value, not pressed(): deleting other people's leftovers never autoruns.
    mo.stop(not cr_orphan_drop.value, None)
    try:
        _stale = cr_orphans(cr_conn["surfaces"])["stale"]
        _gone = cr_delete_orphans(cr_conn, _stale, cr_orphan_confirm.value)
        _out = status_callout("ok", f"Deleted **{len(_gone)}** of {len(_stale)} surfaces.")
    except (ValueError, ServiceError) as _exc:
        _out = cr_failed(_exc)
    _out
    return


@app.cell(column=2)
def _():
    # ContextModel breaks on Python 3.14. The PEP 723 header keeps uv on 3.13, but a
    # hand-started run can land anywhere.
    mo.stop(
        sys.version_info >= (3, 14),
        status_callout(
            "error",
            "**This is Python 3.14 or newer, where `ContextModel` breaks.** Launch with "
            "`uvx marimo@0.25.0 edit --sandbox iris_basics.py`, or use Python 3.13.",
        ),
    )
    cr_field = mo.ui.dropdown(
        [field["id"] for field in cr_model_fields()], value="Order.status", label="Field"
    )
    # The changes kept so far, {"Order.status": {"index": "none", "allowed": False}, ...}.
    cr_edits, cr_set_edits = mo.state({})
    mo.vstack(
        [
            mo.md(r"""
            ### 1 · From data model to tools

            The model is three Python classes, `Customer`, `Product` and `Order`, for a
            fictional outdoor-gear shop. Each field says how it is indexed, and the service turns
            the indexes into tools: a `tag` or a `numeric` field becomes a condition that
            `filter_<entity>` accepts, `text` a full-text search tool. Pick a field and change its
            index. The model that would be sent and the tools it should produce update as you go.
            Nothing leaves this machine yet.
            """),
            cr_field,
        ]
    )
    return cr_edits, cr_field, cr_set_edits


@app.cell(column=2)
def _(cr_edits, cr_field):
    _now = cr_field_state(cr_field.value, cr_edits())
    cr_index = mo.ui.dropdown(_now["kinds"], value=_now["index"], label="Index as")
    cr_allowed = mo.ui.switch(value=_now["allowed"], label="allowed_values (a tag's fixed menu)")
    cr_keep = mo.ui.run_button(label="Keep this change")
    cr_model_reset = mo.ui.run_button(label="Reset to the default model")
    mo.vstack(
        [
            mo.hstack([cr_index, cr_allowed], justify="start", wrap=True),
            mo.hstack([cr_keep, cr_model_reset], justify="start", wrap=True),
        ]
    )
    return cr_allowed, cr_index, cr_keep, cr_model_reset


@app.cell(column=2)
def _(cr_allowed, cr_field, cr_index, cr_keep, cr_model_reset, cr_set_edits):
    # These two buttons only edit the model on this machine, and Reset never autoruns.
    if cr_model_reset.value:
        cr_set_edits({})
    elif cr_keep.value:
        _pick = {"index": cr_index.value, "allowed": cr_allowed.value}
        cr_set_edits(lambda kept: {**kept, cr_field.value: _pick})
    return


@app.cell(column=2)
def _(cr_allowed, cr_edits, cr_field, cr_index):
    # The model as it would be if the picked field were changed as shown, on top of the changes
    # kept so far. Pressing Create below sends exactly this.
    cr_picks = {
        **cr_edits(),
        cr_field.value: {"index": cr_index.value, "allowed": cr_allowed.value},
    }
    cr_model = cr_build_models(CR_PREFIX, cr_picks)
    cr_predicted = cr_predict_tools(cr_model["data_model"])
    return cr_model, cr_picks, cr_predicted


@app.cell(column=2)
def _(cr_field, cr_model, cr_picks, cr_predicted):
    _changes = cr_changes(cr_picks)
    mo.vstack(
        [
            mo.md(
                "**Changed from the default model:**\n\n" + "\n".join(f"- {c}" for c in _changes)
                if _changes
                else "This is the default model."
            ),
            mo.md(
                f"**{len(cr_predicted)}** tools, predicted with the unified naming the service "
                "served on 2026-09-30; an older service names them the classic way, such as "
                "`filter_customer_by_tier`. A prediction, not a call: the service decides the "
                "real names."
            ),
            md_table(cr_predicted, code=("tool",)),
            mo.accordion(
                {
                    f"{cr_field.value}, as export_data_model writes it": mo.md(
                        code_block(
                            json.dumps(
                                cr_field_json(cr_model["data_model"], cr_field.value), indent=2
                            ),
                            "json",
                        )
                    ),
                    "The whole exported data model": mo.json(cr_model["data_model"]),
                }
            ),
            notes(
                "You never write a query. You declare what may be searched, and the tools "
                "follow. A tag with `allowed_values` becomes a fixed menu.",
                "Set `Order.status` to `none` and `status` drops out of what `filter_order` and "
                "`count_order` accept. Set `Customer.name` to `tag`: `search_customer_by_text` "
                "shows as gone, and `filter_customer` accepts `name`.",
            ),
        ]
    )
    return


@app.cell(column=2)
def _(cr_conn, cr_model):
    mo.stop(cr_conn is None, None)
    _records = len(cr_record_keys(cr_model["data_model"]))
    _where = cr_conn["facts"]["where"]
    cr_create = mo.ui.run_button(label=f"Create the surface and load {_records} records")
    mo.vstack(
        [
            mo.md(rf"""
            ### 2 · Create the surface and load the data

            Pressing the button does exactly this, in order, for the model above:

            1. `FT._LIST` on `{_where}`, to know which indexes exist already (read-only).
            2. `POST /api/v1/context-surfaces` makes the surface `{RUN_TAG}`. **The request
               carries your database password**: the hosted service keeps it to reach
               `{_where}`, with a pool of 2 connections.
            3. An agent key that expires in {CR_KEY_TTL_S // 3600} h, and the address it talks to.
            4. `POST .../data`, once per entity: **{_records}** JSON documents under
               `{CR_PREFIX}`. The service writes them and builds the indexes; this notebook
               writes nothing itself.
            5. `EXPIRE {CR_KEY_TTL_S}` on each of those keys, by name.

            Every id goes in a ledger on disk first, so Clean up removes these and nothing else.
            Pressed twice, it picks the surface up again instead of making a second one.
            """),
            cr_create,
        ]
    )
    return (cr_create,)


@app.cell(column=2)
def _(cr_conn, cr_create, cr_model):
    cr_session = None
    _out = status_callout("info", "Nothing is created until you press the button above.")
    _live = ledger_live("cr", "surface")
    try:
        if pressed(cr_create):
            cr_session = with_spinner("Context Retriever", lambda: cr_launch(cr_conn, cr_model))
            _out = cr_launched(cr_session)
        elif _live:
            _out = status_callout(
                "warn",
                f"This run already has the surface `{_live[0]}`. Press the button to pick it "
                "up again, or **Clean up** to remove it.",
            )
    except (ValueError, ServiceError, redis.RedisError, Denied) as _exc:
        cr_session, _out = None, cr_failed(_exc)
    _out
    return (cr_session,)


@app.cell(column=2)
def _(cr_session):
    mo.stop(cr_session is None, None)
    try:
        cr_catalog, cr_listing = with_spinner(
            "Context Retriever", lambda: cr_list_tools(cr_session)
        )
    except (ValueError, ServiceError) as _exc:
        mo.stop(True, cr_failed(_exc))
    return cr_catalog, cr_listing


@app.cell(column=2)
def _(cr_catalog, cr_listing, cr_predicted, cr_session):
    _diff = cr_tool_diff(cr_predicted, [tool["name"] for tool in cr_catalog], cr_session["stale"])
    mo.vstack(
        [
            mo.md(
                "**The tools the service really made**, asked of its MCP endpoint, against the "
                "prediction:"
            ),
            plain_table(_diff["rows"]),
            mo.md(_diff["note"]),
            wire_view("The tools/list request, as JSON-RPC", cr_listing),
        ]
    )
    return


@app.cell(column=2)
def _(cr_conn, cr_session):
    mo.stop(cr_session is None, None)
    # Fetch. Two reads of your own database: the first document, and each index the service built.
    try:
        cr_seen = cr_look(cr_conn["redis"], cr_session)
    except (ValueError, redis.RedisError, Denied) as _exc:
        mo.stop(True, cr_failed(_exc))
    return (cr_seen,)


@app.cell(column=2)
def _(cr_seen):
    mo.vstack(
        [
            mo.md(
                "#### Where did my data go?\n\nThe service wrote the records into **your** "
                "database, as JSON documents, and built the indexes over them. An agent never "
                "touches Redis: it calls tools, and the service queries these. Read from your "
                "database just now:"
            ),
            mo.md(f"`JSON.GET {cr_seen['key']}` returned:"),
            mo.md(code_block(cr_seen["doc"], "json"))
            if cr_seen["doc"]
            else status_callout(
                "warn",
                f"There is no document at `{cr_seen['key']}`, so the service does not key its "
                "records the way the template says.",
            ),
            mo.md(
                "\n".join(
                    f"- `{row['index']}` over `{row['over']}`: **{row['documents']}** documents; "
                    f"{row['fields']}"
                    for row in cr_seen["indexes"]
                )
            )
            if cr_seen["indexes"]
            else mo.md("_The service built no index of its own._"),
            wire_view("What was read, as redis-cli", cr_seen["commands"]),
            notes(
                "The service reads the model, builds the tools and the indexes, and the data "
                "lands in our own Redis as JSON. Nothing is copied anywhere else.",
                "The diff should match. A name that differs is the service's version showing "
                "through: an older one serves classic `filter_<entity>_by_<field>` tools.",
            ),
        ]
    )
    return


@app.cell(column=2)
def _(cr_catalog):
    _pages = cr_pageable(cr_catalog)
    mo.stop(
        not _pages,
        status_callout(
            "info",
            "No filter tool here can be called without typing something: use the explorer below.",
        ),
    )
    cr_page_tool = mo.ui.dropdown(list(_pages), value=next(iter(_pages)), label="Filter tool")
    cr_limit = mo.ui.slider(1, 10, step=1, value=2, debounce=True, show_value=True, label="limit")
    cr_offset = mo.ui.slider(0, 10, step=1, value=0, debounce=True, show_value=True, label="offset")
    mo.vstack(
        [
            mo.md(r"""
            ### 3 · Paging and provenance

            Every list tool pages. `limit` is how many come back, `offset` where to start, and the
            reply says how many match in all (`total_count`) and whether there are more
            (`has_more`). Slide them and watch. If the service stamps its records with where
            they came from (`_retrieval`), the stamp is shown too.
            """),
            cr_page_tool,
            cr_limit,
            cr_offset,
        ]
    )
    return cr_limit, cr_offset, cr_page_tool


@app.cell(column=2)
def _(cr_catalog, cr_limit, cr_offset, cr_page_tool, cr_session):
    try:
        cr_page = cr_call_tool(
            cr_session,
            cr_page_tool.value,
            {
                **cr_pageable(cr_catalog)[cr_page_tool.value],
                "limit": cr_limit.value,
                "offset": cr_offset.value,
            },
        )
    except (ValueError, ServiceError) as _exc:
        mo.stop(True, cr_failed(_exc))
    return (cr_page,)


@app.cell(column=2)
def _(cr_page):
    mo.vstack(
        [
            cr_outcome_view(cr_page),
            notes(
                "Every list tool pages. `has_more` and `total_count` tell an agent whether to "
                "ask again, so it never has to guess.",
                "Slide `limit` below the total and `has_more` turns true. Slide `offset` past "
                "the end and the results are empty.",
            ),
        ]
    )
    return


@app.cell(column=2)
def _(cr_catalog):
    mo.stop(not cr_catalog, status_callout("warn", "The surface lists no tools."))
    _names = [tool["name"] for tool in cr_catalog]
    cr_tool = mo.ui.dropdown(
        _names,
        value=next((name for name in _names if name.startswith("get_")), _names[0]),
        searchable=True,
        label="Tool",
    )
    mo.vstack(
        [
            mo.md(r"""
            ### Wow · a tool explorer

            The form below is generated from the tool's own `inputSchema`, the same schema an
            agent reads: a fixed list becomes a dropdown, a number a number box, a list or an
            object a JSON box. A required argument starts at an example. An optional one starts
            empty and is left out of the call until you fill it in. **Run** sends a real
            `tools/call`. **Break it** sends one the schema forbids, and its result appears
            beside the last Run's.
            """),
            cr_tool,
        ]
    )
    return (cr_tool,)


@app.cell(column=2)
def _(cr_catalog, cr_tool):
    _tool = next(tool for tool in cr_catalog if tool["name"] == cr_tool.value)
    _schema = _tool.get("inputSchema") or {}
    _required = _schema.get("required") or []
    cr_form = mo.ui.dictionary(
        {
            name: cr_widget(name, prop, name in _required)
            for name, prop in (_schema.get("properties") or {}).items()
        }
    )
    cr_run = mo.ui.run_button(label="Run", kind="success")
    cr_break = mo.ui.run_button(label="Break it", kind="danger")
    cr_plan = cr_break_plan(cr_catalog, cr_tool.value)
    # The last Run and the last Break it, kept side by side. Another tool starts afresh.
    cr_results, cr_set_results = mo.state({"run": None, "break": None})
    mo.vstack(
        [
            mo.md(
                html.escape(_tool["description"])
                if _tool.get("description")
                else f"`{_tool['name']}`"
            ),
            cr_form if _schema.get("properties") else mo.md("_This tool takes no arguments._"),
            mo.hstack([cr_run, cr_break] if cr_plan else [cr_run], justify="start", wrap=True),
            mo.md(f"**Break it** will {cr_plan['how']}." if cr_plan else ""),
        ]
    )
    return cr_break, cr_form, cr_plan, cr_results, cr_run, cr_set_results


@app.cell(column=2)
def _(
    cr_break,
    cr_catalog,
    cr_form,
    cr_plan,
    cr_run,
    cr_session,
    cr_set_results,
    cr_tool,
):
    # Fetch: a tool call, only when Run or Break it is pressed. Each keeps its own result, so
    # the accepted call and the rejected one can be read together. Under AUTORUN the first
    # get_ tool is called with its example id, then Break it.
    try:
        if pressed(cr_run):
            _tool = next(tool for tool in cr_catalog if tool["name"] == cr_tool.value)
            _args = cr_arguments(_tool.get("inputSchema") or {}, cr_form.value)
            _ran = cr_call_tool(cr_session, cr_tool.value, _args)
            cr_set_results(lambda kept: {**kept, "run": _ran})
        if cr_plan and pressed(cr_break):
            _broke = cr_call_tool(cr_session, cr_plan["tool"], cr_plan["arguments"])
            cr_set_results(lambda kept: {**kept, "break": _broke})
    except (ValueError, ServiceError) as _exc:
        mo.stop(True, cr_failed(_exc))
    return


@app.cell(column=2)
def _(cr_results):
    _shown = cr_results()
    mo.vstack(
        [
            cr_outcome_view(_shown["run"]) if _shown["run"] else mo.md("_Press **Run**._"),
            mo.md("**Break it**") if _shown["break"] else mo.md(""),
            cr_outcome_view(_shown["break"], broke=True) if _shown["break"] else mo.md(""),
            notes(
                "This form was built from each tool's schema, and an agent reads the very same "
                "contract. A call outside it bounces before it reaches Redis.",
                "Run returns real records with the JSON-RPC underneath. Break it comes back as "
                "a red rejection, with the service's own code and message.",
            ),
        ]
    )
    return


@app.cell(column=2)
def _():
    cr_cleanup_btn = mo.ui.run_button(label="Clean up this run", kind="danger")
    cr_reset_btn = mo.ui.run_button(label="Reset the column")
    mo.vstack(
        [
            mo.md(rf"""
            ### Clean up · Context Retriever

            Deletes this run's surface, then unlinks its keys and drops any index the service
            built and left, because a deleted surface leaves the data behind (the service drops
            its own indexes with it): never `DD`, never a scan-and-delete, never a `PUT`. What
            goes is what the ledger names, and an index whose every prefix is under
            `{CR_PREFIX}`. The cells above stay as they are, so a tool call after *Clean up*
            fails; *Reset* cleans up, then clears them and
            puts the column back to "press Test connection". Keys and the agent key expire by
            themselves after {CR_KEY_TTL_S // 3600} h anyway.
            """),
            mo.hstack([cr_cleanup_btn, cr_reset_btn], justify="start", wrap=True),
        ]
    )
    return cr_cleanup_btn, cr_reset_btn


@app.cell(column=2)
def _(
    cr_admin_key,
    cr_api_url,
    cr_cleanup_btn,
    cr_reset_btn,
    cr_set_gen,
    cr_url,
):
    # Cleanup depends on the config and its own buttons, and on nothing that can stop or
    # fail: it has to work when every cell above it is stuck. Reset is a plain .value, so
    # it never autoruns.
    mo.stop(not (pressed(cr_cleanup_btn, stage="cleanup") or cr_reset_btn.value), None)
    try:
        _admin = resolve_secret("CTX_ADMIN_KEY", cr_admin_key.value)
        _url = redis_url_secret(cr_url.value)
        _missing = [n for n, v in (("CTX_ADMIN_KEY", _admin), ("REDIS_URL", _url)) if v is None]
        if _missing:
            _out = not_configured("Context Retriever", _missing)
        else:
            _api = cr_endpoints(cr_api_url.value)[0]
            _done = with_spinner("Context Retriever", lambda: cr_cleanup(_admin, _api, _url))
            _unfinished = _done["left"] or _done["stopped"]
            _out = status_callout("warn" if _unfinished else "ok", cr_cleanup_report(_done))
            if cr_reset_btn.value:
                cr_set_gen(lambda n: n + 1)
    except (ValueError, ServiceError, redis.RedisError, Denied) as _exc:
        _out = cr_failed(_exc)
    _out
    return


@app.cell(column=2)
def _(cr_catalog):
    mo.stop(not cr_catalog, None)
    cr_deep = mo.ui.switch(value=AUTORUN == "demo", label="Deep dive: the tool catalogue")
    cr_deep
    return (cr_deep,)


@app.cell(column=2)
def _(cr_catalog, cr_deep, cr_tool):
    mo.stop(not cr_deep.value, None)
    _tool = next(tool for tool in cr_catalog if tool["name"] == cr_tool.value)
    mo.vstack(
        [
            mo.md(r"""
            #### The tool catalogue

            Every tool the surface offers, and the arguments it takes (`*` marks the required
            ones). The form above is built from exactly this schema.
            """),
            plain_table(cr_catalogue_rows(cr_catalog)),
            mo.accordion(
                {f"inputSchema of {_tool['name']}": mo.json(_tool.get("inputSchema") or {})}
            ),
        ]
    )
    return


@app.cell(column=3, hide_code=True)
def _():
    # 3 · Agent Memory: title
    _direction = "TB" if mo.app_meta().mode == "run" else "LR"
    mo.vstack(
        [
            mo.md(r"""
            # 3 · Agent Memory

            Agent Memory gives an agent two kinds of memory. Session memory holds the events of
            the conversation in progress. Long-term memory holds facts that are recalled later by
            meaning, across sessions, and the service extracts them from your sessions in the
            background, minutes after the fact rather than instantly. In this column you write
            memories directly, search them and see which ones each similarity threshold lets
            through, prove that scoping keeps one user's memories away from another's, and chat
            with an inspector that shows what an agent would be handed. You need the endpoint,
            store ID and API key of an Agent Memory service.

            **Docs:** [Agent Memory](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/)
            · **Create one:** [create an Agent Memory service](https://redis.io/docs/latest/operate/iris/agent-memory/create-service/)
            """),
            mo.mermaid(
                f"flowchart {_direction}"
                """
                  E[Session events] -->|background extraction, minutes| M[Long-term memories]
                  M --> V[(Vector index in your Redis)]
                  Q[Search by meaning] --> V
                """
            ),
        ]
    )
    return


@app.cell(column=3, hide_code=True)
def _():
    # 3 · Agent Memory: what to connect to
    mo.vstack(
        [
            mo.md(r"""
            ### Connect to your Agent Memory service

            This column needs an Agent Memory service in Redis Cloud: its endpoint, its store ID
            and an API key. Fill the fields below, or leave one empty to use
            `MEMORY_API_BASE_URL`, `MEMORY_STORE_ID` or `MEMORY_API_KEY` from the environment
            or `.env`. The key is never shown, saved or prefilled.
            """),
            mo.md(rf"""
            **This run's scope.** Owner `{AM_OWNER}`, namespace `{AM_NAMESPACE}`. Every call
            below is filtered to both.
            """),
            mo.accordion(
                {
                    "Where do I get these?": mo.md(r"""
                    - **Endpoint and Store ID:** in [cloud.redis.io](https://cloud.redis.io) open
                      *Agent Memory* in the left menu, your service, the *Configuration* tab,
                      *General settings*. Copy the *Endpoint* (it must start with `https://`) and
                      the *Store ID*.
                    - **API key:** shown once, in the dialog that opens when the service is
                      created. If it is lost, open the service's *API keys* tab, *New API key*,
                      *Generate key*, then *Copy*.
                    - **No service yet?** Follow
                      [create an Agent Memory service](https://redis.io/docs/latest/operate/iris/agent-memory/create-service/).
                      *Quick create* builds one on a free 30 MB database and needs no LLM key:
                      the service uses Redis's own models unless you bring your own.
                    """),
                    "Why every call is filtered": mo.md(r"""
                    A store is shared by everything pointed at it (the live Iris demo, a
                    colleague), and a search without a filter returns their memories too, so
                    this notebook refuses to send one. The run id in the owner keeps two
                    presenters apart. A session's owner is the actor of its first event and its
                    namespace comes from that event alone, so every session here opens with a
                    USER event that carries both. Without them a memory extracted from it
                    could not be found again by Clean up.
                    """),
                }
            ),
            status_callout(
                "warn",
                "**Never on a shared service.** *Flush* erases every memory in the store and "
                "*Delete service* removes it (the official tutorial ends with it). This column "
                "uses neither. It deletes only ids it wrote, or listed under its own owner. "
                "Extraction takes minutes, so press *Start extraction now* below, once the "
                "connection is tested, if you want extracted memories to be there when you get "
                "to them.",
            ),
        ]
    )
    return


@app.cell(column=3)
def _():
    am_base = mo.ui.text(
        value=env("MEMORY_API_BASE_URL"),
        placeholder="https://<the Endpoint from the console>",
        label="MEMORY_API_BASE_URL",
        full_width=True,
    )
    am_store = mo.ui.text(
        value=env("MEMORY_STORE_ID"),
        placeholder="the Store ID",
        label="MEMORY_STORE_ID",
        full_width=True,
    )
    am_key = SecretText("MEMORY_API_KEY", label="MEMORY_API_KEY")
    am_connect = mo.ui.run_button(label="Test connection (read-only)")
    # Reset bumps this token. The connection cell reads it, so the column drops back to
    # "press Test connection" and every cell below it clears.
    am_gen, am_set_gen = mo.state(0)
    mo.vstack([am_base, am_store, am_key, am_connect])
    return am_base, am_connect, am_gen, am_key, am_set_gen, am_store


@app.cell(column=3)
def _(am_base, am_connect, am_gen, am_key, am_store):
    am_gen()  # Reset bumps this token, which re-runs the cell and drops the connection
    am_conn = None
    try:
        _conn, _missing = am_settings(am_base.value, am_store.value, am_key.value)
        if _conn is None:
            _status = mo.vstack([not_configured("Agent Memory", _missing), recorded_live("am")])
        elif not pressed(am_connect):
            _status = status_callout(
                "info",
                "Press **Test connection**. It makes one read-only call: "
                "`GET /v1/stores/<store>/health`.",
            )
        else:
            _facts = with_spinner("Agent Memory", lambda: am_health(_conn))
            am_conn = _conn
            _status = am_describe(_facts)
    except (ValueError, ServiceError) as _exc:
        am_conn, _status = None, am_failed(_exc)
    _status
    return (am_conn,)


@app.cell(column=3)
def _(am_conn):
    mo.stop(am_conn is None, None)
    am_orphan_confirm = mo.ui.text(placeholder=f'type "{ORPHAN_CONFIRM}"', label="Confirm")
    am_orphan_drop = mo.ui.run_button(label="Delete those orphans", kind="danger")
    am_orphans = {"memories": [], "sessions": [], "rows": [], "total": 0}
    try:
        am_orphans = with_spinner("Agent Memory", lambda: am_orphan_sweep(am_conn))
        if am_orphans["rows"]:
            _body = [
                mo.md(
                    "Long-term memories expire after a year by default and nothing here sets "
                    "an earlier date, so a run that was closed without *Clean up* leaves its "
                    f"memories in the store. These are in namespace `{AM_NAMESPACE}` and belong "
                    f"to another run. Only those over {ORPHAN_AGE_H} hours old are offered: a "
                    "younger one may be a colleague's live run. Their sessions go too."
                ),
                plain_table(am_orphans["rows"]),
            ]
            if am_orphans["memories"] or am_orphans["sessions"]:
                _body.append(
                    mo.hstack([am_orphan_confirm, am_orphan_drop], justify="start", wrap=True)
                )
        else:
            _body = [mo.md("None found. Every earlier run has cleaned up.")]
    except (ValueError, ServiceError) as _exc:
        _body = [am_failed(_exc)]
    mo.accordion({f"Leftovers from other runs ({am_orphans['total']} memories)": mo.vstack(_body)})
    return am_orphan_confirm, am_orphan_drop, am_orphans


@app.cell(column=3)
def _(am_conn, am_orphan_confirm, am_orphan_drop, am_orphans):
    # A plain .value, not pressed(): deleting other people's leftovers never autoruns.
    mo.stop(not am_orphan_drop.value, None)
    try:
        _done = am_delete_orphans(am_conn, am_orphans, am_orphan_confirm.value)
        _out = status_callout(
            "ok",
            f"Deleted **{_done['memories']}** memories and **{_done['sessions']}** sessions.",
        )
    except (ValueError, ServiceError) as _exc:
        _out = am_failed(_exc)
    _out
    return


@app.cell(column=3)
def _(am_conn):
    mo.stop(am_conn is None, None)
    am_extract_start = mo.ui.run_button(label="Start extraction now (optional, takes minutes)")
    mo.vstack(
        [
            mo.md(rf"""
            ### Start extraction now

            The service also writes long-term memories from your sessions on its own, in the
            background, a few minutes after the fact. Press this before you present the other
            columns and they are there when you get to the deep dive. It posts a short
            conversation of **{len(am_extract_conversation())}** made-up USER events to a new
            session, owner `{AM_OWNER}`, and never runs by itself.
            """),
            am_extract_start,
            notes(
                "The service also writes memories from sessions on its own, minutes later. "
                "Start extraction before the other columns, and check back at the end.",
                "Nothing at first. After the store's extraction cadence (5 minutes unless "
                "changed) the deep dive's Check lists memories with ids the service chose.",
            ),
        ]
    )
    return (am_extract_start,)


@app.cell(column=3)
def _(am_conn, am_extract_start, am_set_posted):
    # A plain .value, not pressed(): the kick-off never autoruns.
    mo.stop(not am_extract_start.value, None)
    try:
        _sent = with_spinner("Agent Memory", lambda: am_extract_kickoff(am_conn))
        _out = mo.vstack(
            [
                status_callout(
                    "ok",
                    f"Posted **{_sent['events']}** USER events to `{_sent['session']}`. "
                    "Extraction runs every few minutes while a session is active (5 by "
                    "default, 1 to 10 in the console). Press *Check for extracted memories* in "
                    "the deep dive later.",
                ),
                wire_view("The first event, as HTTP", _sent["call"]),
            ]
        )
    except (ValueError, ServiceError) as _exc:
        _out = am_failed(_exc)
    am_set_posted(lambda n: n + 1)  # a failure may have started the session all the same
    _out
    return


@app.cell(column=3)
def _(am_conn):
    mo.stop(am_conn is None, None)
    am_seed = mo.ui.run_button(label=f"Write {len(am_memories())} memories")
    mo.vstack(
        [
            mo.md(rf"""
            ### Write long-term memories

            Pressing the button sends one request, `POST /long-term-memory`, with these
            **{len(am_memories())}** facts about a made-up Cairn customer. Each has an id
            `{am_memory_id(1)[:-1]}<n>` of our choosing, owner `{AM_OWNER}`, namespace
            `{AM_NAMESPACE}` and some topics. Every id goes in a ledger on disk first, so
            Clean up deletes exactly these and nothing else. Use made-up facts only: the text
            reaches the service's embedding model.
            """),
            plain_table(
                [
                    {"id": m["id"], "memory": m["text"], "topics": ", ".join(m["topics"])}
                    for m in am_memories()
                ]
            ),
            am_seed,
        ]
    )
    return (am_seed,)


@app.cell(column=3)
def _(am_conn, am_seed):
    am_ready = False
    _out = status_callout("info", "Nothing is written until you press the button above.")
    try:
        _wrote = None
        if pressed(am_seed):
            _wrote = with_spinner("Agent Memory", lambda: am_write_memories(am_conn))
        _listed = am_list(am_conn, am_filter())
        _ids = {memory["id"] for memory in _listed["items"]}
        am_ready = {memory["id"] for memory in am_memories()} <= _ids
        _parts = []
        if am_ready:
            _parts.append(
                status_callout(
                    "ok",
                    f"**{len(am_memories())}** memories are stored for `{AM_OWNER}` and can be "
                    "searched by meaning.",
                )
            )
        elif _wrote:
            _parts.append(
                status_callout(
                    "warn",
                    "Written, but the service does not list them yet. Give it a moment and "
                    "press the button again: an id that exists is not duplicated.",
                )
            )
        if _wrote and _wrote["errors"]:
            _parts.append(status_callout("warn", f"The service refused some: `{_wrote['errors']}`"))
        if am_ready or _wrote:
            _sent = ([_wrote["call"]] if _wrote else []) + _listed["calls"][:1]
            _parts.append(wire_view("The create and the listing, as HTTP", _sent))
        _out = mo.vstack(_parts) if _parts else _out
    except (ValueError, ServiceError) as _exc:
        _out = am_failed(_exc)
    _out
    return (am_ready,)


@app.cell(column=3)
def _(am_ready):
    mo.stop(not am_ready, None)
    am_lens_query = mo.ui.text(value=am_wow_presets()[0]["query"], label="Ask", full_width=True)
    am_lens_sweep = mo.ui.run_button(label="Sweep 11 thresholds (11 searches)")
    mo.vstack(
        [
            mo.md(r"""
            ### 1 · Search by meaning: what a threshold lets through

            A search returns memories but no score, so you cannot see how close a match is.
            One sweep recovers it: the same question at 11 thresholds, 0.0 to 1.0. A memory's
            band is the highest threshold that still returned it. The button makes 11
            searches; the slider afterwards works on what was saved and makes none.
            """),
            am_lens_query,
            am_lens_sweep,
        ]
    )
    return am_lens_query, am_lens_sweep


@app.cell(column=3)
def _(am_conn, am_lens_query, am_lens_sweep):
    # Fetch. Eleven searches, only when Sweep is pressed; the slider and the notes never re-run it.
    mo.stop(
        not pressed(am_lens_sweep),
        status_callout(
            "info",
            "Press **Sweep**. It searches 11 times, once per threshold, and keeps the results.",
        ),
    )
    mo.stop(not am_lens_query.value.strip(), mo.md("_Type a question._"))
    try:
        with mo.status.progress_bar(
            total=len(am_thresholds()),
            title="Sweeping the similarity threshold",
            subtitle="one search per step",
        ) as _bar:
            am_swept = am_sweep(am_conn, am_lens_query.value, _bar.update)
    except (ValueError, ServiceError) as _exc:
        mo.stop(True, am_failed(_exc))
    _problem = am_sweep_problem(am_swept)
    mo.stop(_problem is not None, _problem)
    return (am_swept,)


@app.cell(column=3)
def _(am_swept):
    # It can sit only on the thresholds the sweep tried. It starts at 0.3, where the live service
    # kept the one on-topic memory and cut the rest; at 0.5 it returned nothing.
    am_lens_cut = mo.ui.slider(
        steps=[step["threshold"] for step in am_swept["steps"]],
        value=0.3,
        debounce=True,
        show_value=True,
        label="similarityThreshold",
    )
    am_lens_live = mo.ui.run_button(label="Re-query live at this threshold")
    mo.vstack(
        [
            mo.md("Slide the threshold. The table filters the saved sweep and asks nothing."),
            mo.hstack([am_lens_cut, am_lens_live], justify="start", wrap=True),
        ]
    )
    return am_lens_cut, am_lens_live


@app.cell(column=3)
def _(am_lens_cut, am_swept):
    _rows = am_band_rows(am_swept, am_lens_cut.value)
    _kept = sum(row["at the slider"] == "returned" for row in _rows)
    mo.vstack(
        [
            mo.md(
                f"At `similarityThreshold = {am_lens_cut.value:.1f}` the service would return "
                f"**{_kept}** of the {len(_rows)} memories."
            ),
            plain_table(_rows),
            mo.md(
                "<small>**Derived; assumes a hard cut.** The API returns no score, so a band "
                "is worked out from who came back, on the assumption that the threshold cuts "
                "one number per memory cleanly. Re-query live checks that.</small>"
            ),
            wire_view(
                "The first and last of the 11 searches, as HTTP",
                [am_swept["calls"][0], am_swept["calls"][-1]],
            ),
            mo.accordion(
                {
                    "The same call with the official SDK (text only, not installed)": mo.md(
                        am_sdk_snippet(am_swept["query"])
                    )
                }
            ),
            notes(
                "The service returns no score, so the same question is asked at eleven "
                "thresholds. A memory's band is the highest one that still returns it, and the "
                "slider only filters what was saved.",
                "It starts at 0.3. For the first question a live run gave the allergy memory the "
                "band 0.3 to 0.4 and the other four 0.0 to 0.3, so 1 of 5 is kept, and at 0.5 "
                "nothing is. Slide down and the rest come back one by one. Then Re-query live: "
                "the service should agree with the table.",
            ),
        ]
    )
    return


@app.cell(column=3)
def _(am_conn, am_lens_cut, am_lens_live, am_swept):
    mo.stop(not pressed(am_lens_live), None)
    try:
        _check = am_recheck(am_conn, am_swept, am_lens_cut.value)
        _same = set(_check["live"]) == set(_check["predicted"])
        _out = mo.vstack(
            [
                status_callout(
                    "ok" if _same else "warn",
                    f"Live at `{_check['threshold']:.1f}`: **{len(_check['live'])}** returned, "
                    f"the table predicted **{len(_check['predicted'])}**. "
                    + (
                        "They agree."
                        if _same
                        else "They differ: the threshold is not a plain hard cut, or the "
                        "store changed since the sweep (extraction adds memories)."
                    ),
                ),
                wire_view("The live search, as HTTP", _check["call"]),
            ]
        )
    except (ValueError, ServiceError) as _exc:
        _out = am_failed(_exc)
    _out
    return


@app.cell(column=3)
def _(am_conn, am_lens_query):
    # Fetch: three read-only searches, only when the question changes.
    mo.stop(not am_lens_query.value.strip(), mo.md("_Type a question._"))
    try:
        am_scoped = am_scope_search(am_conn, am_lens_query.value)
    except (ValueError, ServiceError) as _exc:
        mo.stop(True, am_failed(_exc))
    return (am_scoped,)


@app.cell(column=3)
def _(am_scoped):
    mo.vstack(
        [
            mo.md(r"""
            ### 2 · Scope: who is allowed to remember what

            The same question, three filters. Only the first is this run's; the others ask as
            an owner that does not exist and in a namespace that does not exist, and find
            nothing. Every search this column makes carries an owner and a namespace, and the
            service applies them for you.
            """),
            plain_table(
                [
                    {
                        "scope": case["scope"],
                        "filter": json.dumps(case["filter"]),
                        "memories": case["count"],
                    }
                    for case in am_scoped
                ]
            ),
            mo.md(
                "<small>A namespace `eq` never matches a memory that has no namespace, and "
                "`ne` does. That is why Clean up lists by owner alone.</small>"
            ),
            wire_view("The three searches, as HTTP", [case["call"] for case in am_scoped]),
            notes(
                "Same question, three scopes. Only the first is this run's: every search "
                "carries an owner and a namespace, so the others find nothing.",
                "N, then 0, then 0.",
            ),
        ]
    )
    return


@app.cell(column=3)
def _(am_ready):
    mo.stop(not am_ready, None)
    _labels = [preset["label"] for preset in am_wow_presets()]
    am_wow_case = mo.ui.dropdown([*_labels, "Type your own"], value=_labels[0], label="Message")
    am_wow_own = mo.ui.text(value="I want a lighter tent", label="Your message", full_width=True)
    am_wow_limit = mo.ui.slider(
        1, 5, step=1, value=3, debounce=True, show_value=True, label="limit (memories recalled)"
    )
    am_send = mo.ui.run_button(label="Send")
    am_new_session = mo.ui.run_button(label="New session")
    am_tier_summary = mo.ui.switch(value=True, label="session summary (when the service made one)")
    am_tier_events = mo.ui.switch(value=True, label="earlier messages in this session")
    am_tier_memories = mo.ui.switch(value=True, label="recalled long-term memories")
    mo.vstack(
        [
            mo.md(r"""
            ### Wow · a memory inspector

            A chat that shows what an agent would be handed. **Send** adds your message to the
            current session as a USER event, then searches long-term memory with the same
            words at a `similarityThreshold` of 0.0 (what the service does when it is left
            out is not documented): nothing is cut by score, so the memories come back in
            rank order with `limit` as the only cut. Each is marked with how many words it
            shares with your message. **New session** starts a fresh session: what was said
            is forgotten, what was learned is not. Type made-up text only: background
            extraction reads every session event, and can turn one into a long-term memory
            that lasts a year unless Clean up deletes it.
            """),
            am_wow_case,
            am_wow_own,
            am_wow_limit,
            mo.hstack([am_send, am_new_session], justify="start", wrap=True),
        ]
    )
    return (
        am_new_session,
        am_send,
        am_tier_events,
        am_tier_memories,
        am_tier_summary,
        am_wow_case,
        am_wow_limit,
        am_wow_own,
    )


@app.cell(column=3)
def _(am_gen):
    am_gen()  # Reset bumps this token, which re-runs the cell and forgets the chat
    # mo.ui.chat is broken in marimo 0.25.0, so the chat is a text box, a Send button and this
    # dict. Nothing re-runs the cell, so what the Send cell stores here stays until Reset.
    am_chat = {"session": None, "turn": None}
    # Send and the kick-off bump this when they add to a session. Only the Session memory
    # cell reads it, so moving a control above never makes that cell ask the service again.
    am_posted, am_set_posted = mo.state(0)
    return am_chat, am_posted, am_set_posted


@app.cell(column=3)
def _(
    am_chat,
    am_conn,
    am_new_session,
    am_send,
    am_set_posted,
    am_wow_case,
    am_wow_limit,
    am_wow_own,
):
    # New session is a plain .value, not pressed(): it never autoruns. Send does, with the
    # first question, so AUTORUN sends preset 1.
    if am_new_session.value:
        am_chat.update(session=None, turn=None)
    elif pressed(am_send):
        _ask = am_wow_preset(am_wow_case.value, am_wow_own.value)
        if not _ask["query"]:
            am_chat["turn"] = {"note": mo.md("_Type a message._")}
        else:
            try:
                am_chat["turn"] = with_spinner(
                    "Agent Memory",
                    lambda: am_take_turn(am_conn, am_chat, _ask, am_wow_limit.value),
                )
            except (ValueError, ServiceError) as _exc:
                am_chat["turn"] = {"note": am_failed(_exc)}
            am_set_posted(lambda n: n + 1)
    am_turn = am_chat["turn"]
    return (am_turn,)


@app.cell(column=3)
def _(am_chat, am_tier_events, am_tier_memories, am_tier_summary, am_turn):
    mo.stop(
        am_turn is None,
        mo.md(
            f"_Send a message. It goes into session `{am_chat['session'] or am_next_session()}`, "
            "and long-term memory finds the same memories whichever session asks._"
        ),
    )
    _note = am_turn.get("note")
    mo.stop(_note is not None, _note)
    _tiers = {
        "summary": am_tier_summary.value,
        "events": am_tier_events.value,
        "memories": am_tier_memories.value,
    }
    _events = am_count(len(am_turn["events"]), "event")
    mo.vstack(
        [
            mo.md(f"Session `{am_turn['session']}` · {_events} so far"),
            plain_table(am_recall_rows(am_turn)),
            mo.md(am_wow_summary(am_turn)),
            mo.md(am_turn["preset"]["lesson"]),
            mo.md("**In short-term memory** (the session's events, read back from the service):"),
            plain_table(am_event_rows(am_turn["events"])),
            mo.accordion(
                {
                    "Prompt assembly: what an agent would receive": mo.vstack(
                        [
                            mo.hstack(
                                [am_tier_summary, am_tier_events, am_tier_memories],
                                justify="start",
                                wrap=True,
                            ),
                            mo.md(code_block(am_context(am_turn, _tiers), "text")),
                            mo.md(
                                "<small>The message you sent is the question, not part of the "
                                "context. Switch a tier off to see what it contributes.</small>"
                            ),
                        ]
                    )
                }
            ),
            wire_view("The event, the read-back and the search, as HTTP", am_turn["calls"]),
            notes(
                "Each message becomes a session event, then the same words search long-term "
                "memory by meaning. Nothing in the question appears in the memory.",
                "The starred memory near the top with 0 words in common. New session, then "
                "Send again: the session's events reset and the memories do not.",
            ),
        ]
    )
    return


@app.cell(column=3)
def _():
    am_cleanup_btn = mo.ui.run_button(label="Clean up this run", kind="danger")
    am_resweep_btn = mo.ui.run_button(label="Clean up again", kind="danger")
    am_reset_btn = mo.ui.run_button(label="Reset the column")
    mo.vstack(
        [
            mo.md(rf"""
            ### Clean up · Agent Memory

            Deletes this run's sessions (from the ledger), then every memory the service holds
            for owner `{AM_OWNER}`, the ones you wrote and the ones extraction made. It deletes
            none if a memory listed turns out to have another owner. Extraction runs in the
            background and can add memories for about 10 minutes after the last event, so
            **Clean up again** repeats the clean-up to catch them, and deletes what you wrote
            too if you press it mid-demo. *Reset* cleans up, then clears the cells above and
            puts the column back to "press Test connection".
            """),
            mo.hstack([am_cleanup_btn, am_resweep_btn, am_reset_btn], justify="start", wrap=True),
        ]
    )
    return am_cleanup_btn, am_reset_btn, am_resweep_btn


@app.cell(column=3)
def _(
    am_base,
    am_cleanup_btn,
    am_key,
    am_reset_btn,
    am_resweep_btn,
    am_set_gen,
    am_store,
):
    # Cleanup depends on the config and its own buttons, and on nothing that can stop or
    # fail: it has to work when every cell above it is stuck. Reset is a plain .value, so
    # it never autoruns.
    mo.stop(
        not (
            pressed(am_cleanup_btn, stage="cleanup")
            or pressed(am_resweep_btn, stage="cleanup")
            or am_reset_btn.value
        ),
        None,
    )
    try:
        _conn, _missing = am_settings(am_base.value, am_store.value, am_key.value)
        if _conn is None:
            _out = not_configured("Agent Memory", _missing)
        else:
            _done = with_spinner("Agent Memory", lambda: am_cleanup(_conn))
            _out = status_callout(
                "warn" if _done["left"] or _done["sessions_left"] else "ok",
                am_cleanup_report(_done),
            )
            if am_reset_btn.value:
                am_set_gen(lambda n: n + 1)
    except (ValueError, ServiceError) as _exc:
        _out = am_failed(_exc)
    _out
    return


@app.cell(column=3)
def _(am_conn):
    mo.stop(am_conn is None, None)
    am_deep = mo.ui.switch(
        value=AUTORUN == "demo", label="Deep dive: session memory, extraction, under the hood"
    )
    am_deep
    return (am_deep,)


@app.cell(column=3)
def _(am_chat, am_deep, am_posted):
    am_posted()  # Send and the kick-off bump this token, which re-runs the cell
    mo.stop(not am_deep.value, None)
    _sessions = ledger_live("am", "session")
    mo.stop(
        not _sessions,
        mo.md(
            "_Send a message in the inspector above, or post the conversation below: either "
            "starts a session to read here._"
        ),
    )
    _latest = am_chat["session"]
    am_dd_session = mo.ui.dropdown(
        _sessions, value=_latest if _latest in _sessions else _sessions[-1], label="Session"
    )
    am_dd_old = mo.ui.switch(label="include summarised events")
    mo.vstack(
        [
            mo.md(r"""
            #### Session memory

            Short-term memory is the ordered events of one session, and nothing else. The
            service can also summarise a long session and drop the events it summarised; the
            summary appears here once it has, and the switch asks for the dropped events too.
            Whether it summarises, and after how many messages, is a console setting.
            """),
            mo.hstack([am_dd_session, am_dd_old], justify="start", wrap=True),
        ]
    )
    return am_dd_old, am_dd_session


@app.cell(column=3)
def _(am_conn, am_dd_old, am_dd_session):
    try:
        am_dd_read = am_get_session(am_conn, am_dd_session.value, am_dd_old.value)
    except (ValueError, ServiceError) as _exc:
        mo.stop(True, am_failed(_exc))
    return (am_dd_read,)


@app.cell(column=3)
def _(am_dd_read):
    _session = am_dd_read["session"]
    _summary = _session.get("summary")
    mo.vstack(
        [
            mo.md(
                f"Owner `{_session.get('ownerId')}` (the actor of the first event) · namespace "
                f"`{_session.get('namespace')}` (from the first event alone) · "
                f"**{am_count(len(_session.get('events', [])), 'event')}**"
            ),
            plain_table(am_event_rows(_session.get("events", []))),
            mo.md(f"**Summary:** {_summary['text']}")
            if isinstance(_summary, dict)
            else status_callout(
                "info",
                "No summary yet. The service writes one only when summarisation is on for "
                "the store and the session has passed its threshold.",
            ),
            wire_view("The read, as HTTP", am_dd_read["call"]),
        ]
    )
    return


@app.cell(column=3)
def _(am_deep):
    mo.stop(not am_deep.value, None)
    am_extract_check = mo.ui.run_button(label="Check for extracted memories")
    mo.vstack(
        [
            mo.md(r"""
            #### Auto-extraction

            You never asked for the memories below to be written. Every session with events is
            read in the background, and the service writes the long-term memories it finds in
            it, minutes later. *Start extraction now*, above, posts a short conversation to a
            new session; this button lists the memories under this owner whose ids the service
            chose. It never runs by itself.
            """),
            am_extract_check,
        ]
    )
    return (am_extract_check,)


@app.cell(column=3)
def _(am_conn, am_extract_check):
    # A plain .value, not pressed(): nothing about extraction runs by itself.
    mo.stop(not am_extract_check.value, None)
    try:
        _found = with_spinner("Agent Memory", lambda: am_extracted(am_conn))
        _out = mo.vstack(
            [
                status_callout(
                    "ok" if _found["items"] else "info",
                    f"**{len(_found['items'])}** memories with ids the service chose."
                    if _found["items"]
                    else "None yet. That is normal for the first few minutes, and it is "
                    "also what a store with extraction switched off looks like.",
                ),
                plain_table(am_extracted_rows(_found["items"])) if _found["items"] else mo.md(""),
                wire_view("The listing, as HTTP", _found["calls"][0]),
            ]
        )
    except (ValueError, ServiceError) as _exc:
        _out = am_failed(_exc)
    _out
    return


@app.cell(column=3)
def _(am_deep):
    mo.stop(not am_deep.value, None)
    am_hood_url = SecretText("REDIS_URL", label="REDIS_URL (the database behind the service)")
    am_hood_btn = mo.ui.run_button(label="Look inside the database (read-only)")
    mo.vstack(
        [
            mo.md(r"""
            #### Under the hood

            The service keeps its memories in the Redis database you chose when you created
            it, as hashes under `memory:<store>:ltm:` with a vector index of the same name. If
            this notebook can reach that database it can look, with reads only: `FT.INFO` on
            the index, then the fields of the first memory you wrote. It never writes there,
            and never drops or recreates the index, which the service would not rebuild.
            """),
            am_hood_url,
            mo.accordion(
                {
                    "Where do I get REDIS_URL?": mo.md(r"""
                    - **Which database:** in [cloud.redis.io](https://cloud.redis.io) open *Agent
                      Memory*, your service, *Configuration*: its *General settings* name the
                      database behind it.
                    - **The URL:** in [cloud.redis.io](https://cloud.redis.io) open *Databases*,
                      that database, *Configuration*: the *Public endpoint* is `host:port`. The
                      password is under *Security*, *Default user*, behind the eye icon. Put
                      them together as `redis://default:<password>@<host>:<port>`.
                    """)
                }
            ),
            am_hood_btn,
        ]
    )
    return am_hood_btn, am_hood_url


@app.cell(column=3)
def _(am_conn, am_hood_btn, am_hood_url):
    # A plain .value, not pressed(): looking inside a database is not a demo step, and under
    # AUTORUN it would connect to whatever REDIS_URL names, which is rarely this one.
    mo.stop(not am_hood_btn.value, None)
    try:
        _url = redis_url_secret(am_hood_url.value)
        if _url is None:
            _out = status_callout(
                "info",
                "No database URL. Paste `REDIS_URL` above, or set it, to look inside the "
                "database that backs the service. Everything else works without it.",
            )
        else:
            _hood = with_spinner("Redis", lambda: am_hood(connect_redis(_url.reveal()), am_conn))
            _out = am_hood_view(_hood)
    except (ValueError, ServiceError, redis.RedisError, Denied) as _exc:
        _out = am_failed(_exc)
    _out
    return


if __name__ == "__main__":
    app.run()
