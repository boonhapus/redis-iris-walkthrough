"""Hermetic test setup for the Iris basics notebook.

The suite imports the module named by IRIS_NB_MODULE (default `iris_basics`) from
IRIS_NB_PATH (default: the folder above tests/), so the same tests run against the
assembled notebook and against a single-column fragment.

Nothing here may reach a real service. Before the notebook is imported (its setup block
reads IRIS_BASICS_RUN and IRIS_BASICS_AUTORUN at import time) every setting the notebook
knows about is removed from the environment; the settings file is pointed at an empty
one; and every test gets an httpx transport and a socket connect that refuse real hosts.
"""

import importlib
import importlib.util
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

NB_MODULE = os.environ.get("IRIS_NB_MODULE", "iris_basics")
NB_DIR = Path(os.environ.get("IRIS_NB_PATH", Path(__file__).resolve().parent.parent)).resolve()
NB_FILE = NB_DIR / f"{NB_MODULE}.py"

# Whole families are removed, not just the allowlist, so a new setting cannot slip through.
_SETTING_PREFIXES = (
    "REDIS_", "LANGCACHE_", "CTX_", "MEMORY_", "MCP_", "OPENAI_", "DEMO_", "IRIS_BASICS_",
)  # fmt: skip
for _name in [n for n in os.environ if n.startswith(_SETTING_PREFIXES)]:
    del os.environ[_name]

_SCRATCH = Path(tempfile.mkdtemp(prefix="iris_basics_tests_"))
(_SCRATCH / "empty.env").write_text("", encoding="utf-8")
os.environ["IRIS_BASICS_ENV_FILE"] = str(_SCRATCH / "empty.env")
os.environ["IRIS_BASICS_NO_DOTENV"] = "1"
os.environ["IRIS_BASICS_LEDGER_DIR"] = str(_SCRATCH / "ledger")
sys.path.insert(0, str(NB_DIR))


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SCRATCH, ignore_errors=True)


def pytest_configure(config):
    # The Context Retriever SDK still uses Pydantic v1-style config; not ours to fix.
    config.addinivalue_line(
        "filterwarnings", "ignore::pydantic.warnings.PydanticDeprecatedSince20"
    )


def pytest_collection_modifyitems(config, items):
    """Leave out a column's tests when the notebook under test does not contain that column.

    A column's test module declares COLUMN_PREFIX (e.g. "lc_"). Column phases verify base.py
    plus one fragment, so the other columns' tests have nothing to test and are deselected (not
    skipped: a skip means a check that should have run). For the real notebook verify.py sets
    IRIS_NB_REQUIRE_ALL=1, which makes a missing column an error: one must never vanish quietly.
    """
    names = dir(importlib.import_module(NB_MODULE))
    missing = {
        prefix
        for item in items
        if (prefix := getattr(item.module, "COLUMN_PREFIX", None))
        and not any(n.startswith(prefix) for n in names)
    }
    if not missing:
        return
    if os.environ.get("IRIS_NB_REQUIRE_ALL") == "1":
        raise pytest.UsageError(f"{NB_FILE.name} has no names for column(s) {sorted(missing)}")
    kept = [i for i in items if getattr(i.module, "COLUMN_PREFIX", None) not in missing]
    config.hook.pytest_deselected(items=[i for i in items if i not in kept])
    items[:] = kept


@pytest.fixture(scope="session")
def nb():
    """The notebook module, imported once (which runs its setup block)."""
    return importlib.import_module(NB_MODULE)


@pytest.fixture(scope="session")
def nb_file():
    return NB_FILE


@pytest.fixture
def load_fresh(monkeypatch):
    """Import a new copy of the notebook under the given environment.

    RUN and AUTORUN are fixed when the setup block runs, so testing how they are derived
    needs a fresh execution of it.
    """
    counter = iter(range(1_000_000))

    def load(**env):
        for name in [n for n in os.environ if n.startswith("IRIS_BASICS_")]:
            if name not in (
                "IRIS_BASICS_ENV_FILE",
                "IRIS_BASICS_NO_DOTENV",
                "IRIS_BASICS_LEDGER_DIR",
            ):
                monkeypatch.delenv(name)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        spec = importlib.util.spec_from_file_location(f"_nb_fresh_{next(counter)}", NB_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return load


@pytest.fixture(autouse=True)
def hermetic(nb, monkeypatch, tmp_path):
    """Per-test isolation: no network, a private ledger, and clean module-level state."""
    monkeypatch.setenv("IRIS_BASICS_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.chdir(tmp_path)  # the notebook looks for a .env in the working directory

    blocked = []

    def refuse(request):
        blocked.append(str(request.url))
        raise httpx.ConnectError("network is disabled in tests", request=request)

    monkeypatch.setattr(nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(refuse)))

    real_connect = socket.socket.connect

    def loopback_only(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if str(host) not in ("127.0.0.1", "::1", "localhost"):
            blocked.append(str(host))
            raise OSError(f"network is disabled in tests ({host})")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)

    for state in (nb.SECRETS, nb.OWNED_INDEXES, nb.HTTP_TRACE, nb.REDIS_TRACE, nb.HTTP_RULES):
        state.clear()
    for grants in (*nb.APPROVED.values(), *nb.ORPHANS.values()):
        grants.clear()
    monkeypatch.setitem(nb.LC_SCOPE, "attr", "")
    monkeypatch.setattr(nb, "WRITE_RATE", 1_000_000)  # pacing is tested on its own
    nb._parse_env_file.cache_clear()

    yield
    assert not blocked, f"a test tried to reach the network: {blocked}"


@pytest.fixture
def mock_http(nb, monkeypatch):
    """Route http_call to a canned reply; returns the list of requests it received.

    seen = mock_http(status=201, json={"ok": True})
    seen = mock_http(handler=lambda request: httpx.Response(...))
    """

    def install(handler=None, *, status=200, json=None, text=None):
        seen = []

        def respond(request):
            seen.append(request)
            if handler:
                return handler(request)
            if text is not None:
                return httpx.Response(status, text=text)
            return httpx.Response(status, json=json) if json is not None else httpx.Response(status)

        monkeypatch.setattr(nb, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(respond)))
        return seen

    return install


@pytest.fixture
def rec(nb, monkeypatch):
    """A GuardedTracedRedis wired to a recorder instead of a server.

    `rec.sent` lists the argv of every command that got past the guard; `rec.replies`
    maps a command name to the reply it should return (default b"OK"; an exception is
    raised instead). FT.INFO answers like a server that built each FT.CREATE as written,
    unless a reply is set for it.
    """
    sent, replies, built = [], {}, {}

    def prefixes_of(tokens):
        """What the last PREFIX clause before SCHEMA covers, as RediSearch reads it."""
        upper = [t.upper() for t in tokens]
        upper = upper[: upper.index("SCHEMA")] if "SCHEMA" in upper else upper
        if "PREFIX" not in upper:
            return []
        i = len(upper) - 1 - upper[::-1].index("PREFIX")
        return tokens[i + 2 : i + 2 + int(tokens[i + 1])]

    def fake_execute(self, *args, **options):
        sent.append(args)
        name = str(nb._txt(args[0])).upper()
        if name == "FT.CREATE":
            built[str(nb._txt(args[1]))] = prefixes_of([str(nb._txt(a)) for a in args[2:]])
        elif name == "FT.INFO" and name not in replies and str(nb._txt(args[1])) in built:
            index = str(nb._txt(args[1]))
            definition = [b"key_type", b"HASH", b"prefixes", [p.encode() for p in built[index]]]
            return [b"index_name", index.encode(), b"index_definition", definition]
        reply = replies.get(name, b"OK")
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr(nb.redis.Redis, "execute_command", fake_execute)

    class NoServer:
        """The connection pool of a client that must never connect.

        redis-py's module clients (ft, json, ts) read the pool when they are built, and a
        Pipeline made from it would connect on execute(); this pool fails the test instead.
        """

        connection_kwargs = {"protocol": 2}

        def get_connection(self, *args, **kwargs):
            raise AssertionError("a test tried to open a Redis connection")

    class Recorder(nb.GuardedTracedRedis):
        def __init__(self):  # not Redis.__init__: no real pool, so nothing can talk to a server
            self.connection_pool = NoServer()
            self.response_callbacks = {}

    client = Recorder()
    client.sent, client.replies = sent, replies
    return client
