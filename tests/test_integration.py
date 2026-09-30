"""All four columns in one notebook, on one Redis database, in the real `marimo export`.

Each column's own tests run its cells against a stand-in for its service, and judge only that
column's cells. Nothing else runs the whole notebook the way a presenter does: columns 0 and 2
on one REDIS_URL, every column's demo pressed, then every column's cleanup. So this runs
`marimo export` twice in a child process, with IRIS_BASICS_AUTORUN=demo and then cleanup, and
all four stand-ins behind it: one Redis (column 0's search server and column 2's hosted
service share its keys and indexes), and LangCache, Context Retriever and Agent Memory on
their own hosts.

What it holds the notebook to: no error output anywhere in the demo; no red callout that is not
by design, judged the way `verify.py --live` judges a live export; every Redis write under this
run's prefix; nothing left in any stand-in, or on any ledger, after cleanup; and no secret of any
column in a session file. The stand-ins answer as the live services did on 2026-09-30 (Redis
8.6's wording of a missing index, LangCache's `404 /errors/resource-not-found`, the unified
Context Retriever tool surface), so what that run found fails here first.
"""

import importlib.util
import json
import os
import pickle
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fake_am import BASE as AM_BASE
from fake_am import KEY as AM_KEY
from fake_am import STORE as AM_STORE
from fake_am import FakeMemory
from fake_cr import ADMIN_KEY
from fake_lc import FakeLangCache, score_plan
from fake_search import FakeSearch

pytestmark = pytest.mark.skipif(
    os.environ.get("IRIS_NB_MODULE", "iris_basics") != "iris_basics",
    reason="the whole notebook only exists in the assembled file",
)

RUN = "0123abcd"
PREFIX = f"irisnb_{RUN}_"
REDIS_URL = "redis://default:fake-pw-9f3a@db.example:6379"
LC_HOST, LC_CACHE, LC_KEY = "https://lc.example.com", "cache-abc123", "fake-lc-key-9f3a"
ENV = {
    "REDIS_URL": REDIS_URL,
    "LANGCACHE_HOST": LC_HOST, "LANGCACHE_CACHE_ID": LC_CACHE, "LANGCACHE_API_KEY": LC_KEY,
    "CTX_ADMIN_KEY": ADMIN_KEY,
    "MEMORY_API_BASE_URL": AM_BASE, "MEMORY_STORE_ID": AM_STORE, "MEMORY_API_KEY": AM_KEY,
}  # fmt: skip
SECRETS = ["fake-pw-9f3a", LC_KEY, ADMIN_KEY, AM_KEY]

# A child process cannot share the parent's fakes, so it builds its own from the last export's
# state. Every command and request goes through one dispatcher, as it would to one network.
RUNNER = '''
import json
import os
import pickle
import sys
from pathlib import Path

sys.path.insert(0, {tests!r})
import httpx
import redis
from fake_am import FakeMemory
from fake_cr import FakeWorld
from fake_lc import FakeLangCache
from fake_search import FakeSearch, txt

state = Path({state!r})
saved = pickle.loads(state.read_bytes()) if state.exists() else None
search, world = FakeSearch(), FakeWorld(os.environ["FAKE_STYLE"])
lc, am = FakeLangCache({cache!r}, {key!r}), FakeMemory()
lc.scores = {{(q, p): s for q, p, s in json.loads(Path({scores!r}).read_text("utf-8"))}}
if saved:
    search.hashes, search.ttls, search.indexes = saved["search"]
    world = saved["world"]
    lc.restore(saved["lc"])
    am.load(saved["am"])
log = []


def save():
    state.write_bytes(pickle.dumps({{
        "search": (search.hashes, search.ttls, search.indexes), "world": world,
        "lc": lc.snapshot(), "am": am.state(), "commands": saved["commands"] + log if saved else log,
    }}))


def handle(self, *args, **kwargs):
    """One Redis: column 0's keys and indexes, and the ones the hosted service made."""
    log.append([txt(a) for a in args[:3]])
    name = txt(args[0]).upper()
    target = txt(args[1]) if len(args) > 1 else ""
    try:
        if name in ("PING", "INFO"):
            return search.handle(args)
        if name == "DBSIZE":
            return len(search.hashes) + len(world.docs)
        if name == "SCAN":
            return 0, search.handle(args)[1] + world.handle(args)[1]
        if name == "FT._LIST":
            return search.handle(args) + world.handle(args)
        if name in ("FT.INFO", "FT.DROPINDEX"):
            return (world if target in world.indexes else search).handle(args)
        if name.startswith("JSON.") or "_cr:" in target:
            return world.handle(args)
        return search.handle(args)
    finally:
        save()


def send(self, request, **kwargs):
    host = request.url.host
    if host == "lc.example.com":
        response = lc.handle(request)
    elif host in ("cloud.redis.io", "gcp-us-east4.context-surfaces.redis.io"):
        response = world.http(request)
    elif host == "am.example.test":
        response = am.handle(request)
    else:
        raise httpx.ConnectError("no stand-in for " + host)
    response.request = request
    save()
    return response


redis.Redis.execute_command = handle  # marimo spawns its kernel, which re-imports this file
httpx.Client.send = send

if __name__ == "__main__":
    from marimo._cli.cli import main

    args = ["-y", "export", "html", "--no-sandbox", {notebook!r}, "-o", "out.html", "-f"]
    main(args, standalone_mode=False)
'''

WRITES = {"HSET", "EXPIRE", "UNLINK", "DEL", "JSON.SET", "FT.CREATE", "FT.DROPINDEX"}


def load_verify(nb_file):
    """verify.py, which sits beside the notebook, as a module."""
    spec = importlib.util.spec_from_file_location("iris_basics_verify", nb_file.parent / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Everything:
    """What the four stand-ins hold after an export, and the commands that reached Redis."""

    def __init__(self, saved, session):
        self.session = session
        self.search = FakeSearch()
        self.search.hashes, self.search.ttls, self.search.indexes = saved["search"]
        self.world = saved["world"]
        self.lc = FakeLangCache(LC_CACHE, LC_KEY)
        self.lc.restore(saved["lc"])
        self.am = FakeMemory()
        self.am.load(saved["am"])
        self.commands = saved["commands"]

    @property
    def text(self):
        return json.dumps(self.session)

    def errors(self):
        """The kinds of error output in any cell, whichever column it is in."""
        return {
            o["ename"]
            for cell in self.session["cells"]
            for o in cell.get("outputs") or []
            if o["type"] == "error"
        }

    def held(self):
        """Everything of this run that is still stored somewhere, by stand-in."""
        mine = f"ib-{RUN}-"
        return {
            "redis keys": list(self.search.hashes) + list(self.world.docs),
            "redis indexes": list(self.search.indexes) + list(self.world.indexes),
            "surfaces": list(self.world.surfaces),
            "cache entries": list(self.lc.entries),
            "memories": [i for i in self.am.memories if i.startswith(mine)],
            "sessions": [i for i in self.am.sessions if i.startswith(mine)],
        }


def ledger_live(folder):
    """{(column, kind, ident)} the ledger still shows as created or intended, not deleted."""
    path = Path(folder) / f"{RUN}.jsonl"
    latest = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        latest[entry["column"], entry["kind"], entry["ident"]] = entry["state"]
    return {key for key, state in latest.items() if state != "deleted"}


class TestAllFourColumns:
    def export(self, nb, nb_file, tmp_path, stage, style="unified"):
        work = tmp_path / "all"
        work.mkdir(exist_ok=True)
        shutil.copy2(nb_file, work / nb_file.name)
        state = tmp_path / "everything.pkl"
        plan = score_plan(nb.lc_seeds(), nb.lc_probes(), nb.lc_variants(), nb.lc_script())
        scores = tmp_path / "scores.json"
        scores.write_text(json.dumps([[q, p, s] for (q, p), s in plan.items()]), encoding="utf-8")
        (work / "runner.py").write_text(
            RUNNER.format(
                tests=str(Path(__file__).parent),
                state=str(state),
                cache=LC_CACHE,
                key=LC_KEY,
                scores=str(scores),
                notebook=nb_file.name,
            ),
            encoding="utf-8",
        )
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        env.update(
            PYTHONIOENCODING="utf-8", PYTHONUTF8="1", HF_HUB_OFFLINE="1", FAKE_STYLE=style,
            IRIS_BASICS_RUN=RUN, IRIS_BASICS_AUTORUN=stage, IRIS_BASICS_HEADLESS="1", **ENV,
        )  # fmt: skip
        done = subprocess.run(
            [sys.executable, "runner.py"], cwd=work, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
        )  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
        session = work / "__marimo__" / "session" / f"{nb_file.name}.json"
        saved = pickle.loads(state.read_bytes())
        return Everything(saved, json.loads(session.read_text(encoding="utf-8")))

    def test_every_columns_demo_then_every_columns_cleanup_on_one_database(
        self, nb, nb_file, tmp_path
    ):
        demo = self.export(nb, nb_file, tmp_path, "demo")

        # 1. Nothing failed, in any column.
        assert demo.errors() == set()
        for shown in (
            "articles are indexed",  # column 0
            "Threshold lab",  # column 1
            "The tools the service really made",  # column 2
            "memories are stored",  # column 3
        ):
            assert shown in demo.text, shown

        # 1b. The judge that reads a live export finds no red callout that is not by design, and
        #     no cleanup that says it could not finish.
        verify = load_verify(nb_file)
        cells = verify.notebook_cells(nb_file.read_text(encoding="utf-8"))
        judged = verify.judge_session(demo.session, cells, nb.scrub, "demo")
        assert judged["mapped"] and judged["banner"]
        assert {k: g["unexpected"] for k, g in judged["groups"].items()} == {
            k: [] for k in judged["groups"]
        }
        assert {k: g["errors"] for k, g in judged["groups"].items()} == {
            k: [] for k in judged["groups"]
        }

        # 2. Every write is this run's. Column 2's service is the one thing that names an index
        #    of its own, and Clean up may drop those by name.
        writes = [c for c in demo.commands if c[0].upper() in WRITES]
        assert writes, "the demo wrote nothing"
        assert all(c[1].startswith(PREFIX) for c in writes), [
            c for c in writes if not c[1].startswith(PREFIX)
        ]
        assert {c[0].upper() for c in demo.commands} & {"FLUSHALL", "FLUSHDB", "CONFIG"} == set()

        # 3. The two columns that share the database each found their own, and neither took the
        #    other's for somebody else's: the demo user is `default`, and the database holds
        #    nothing but this notebook's keys and indexes.
        assert len(demo.search.hashes) == 36 and len(demo.world.docs) == 20
        assert "that are not this notebook" not in demo.text
        assert all(k.startswith(PREFIX) for k in [*demo.search.hashes, *demo.world.docs])
        held = demo.held()
        assert all(
            held[kind]
            for kind in (
                "redis keys",
                "redis indexes",
                "surfaces",
                "cache entries",
                "memories",
                "sessions",
            )
        )
        assert ledger_live(tmp_path / "ledger")

        # 4. Cleanup, from the ledger on disk, empties all four.
        clean = self.export(nb, nb_file, tmp_path, "cleanup")
        assert clean.errors() <= {"ancestor-stopped"}  # Test connection did not run; cleanup did
        finished = verify.judge_session(clean.session, cells, nb.scrub, "cleanup")
        assert {k: g["unfinished"] for k, g in finished["groups"].items()} == {
            k: [] for k in finished["groups"]
        }
        assert {k: g["unexpected"] for k, g in finished["groups"].items()} == {
            k: [] for k in finished["groups"]
        }
        assert clean.held() == {kind: [] for kind in clean.held()}, clean.held()
        assert ledger_live(tmp_path / "ledger") == set()
        drops = [c for c in clean.commands if c[0].upper() == "FT.DROPINDEX"]
        assert drops and "DD" not in {arg.upper() for c in clean.commands for arg in c}
        made_by_the_service = set(demo.world.indexes)
        for command in (c for c in clean.commands if c[0].upper() in WRITES):
            assert command[1].startswith(PREFIX) or (
                command[0].upper() == "FT.DROPINDEX" and command[1] in made_by_the_service
            ), command

        # 5. No credential of any column is in either session file.
        agent_keys = list(demo.world.agent_keys)
        for run in (demo, clean):
            for secret in [*SECRETS, *agent_keys]:
                assert secret not in run.text, secret
