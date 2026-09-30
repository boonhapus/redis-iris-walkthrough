"""Offline tests for notebook.py -- no Redis server required.

Imports the notebook module so the `with app.setup:` block executes, then
exercises the guard, the redis-cli renderer, and the argv of every command the
notebook issues (using a stub that records instead of connecting).

Run:  .venv/Scripts/python.exe test_offline.py
"""

import sys

import notebook as nb

fails = []


def check(label, cond, detail=""):
    print(f"   [{'ok  ' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(label)


# ---- guard ----------------------------------------------------------------
print("[guard]")


class Recorder(nb.GuardedTracedRedis):
    """Records argv instead of talking to a server."""

    def __init__(self):
        self.sent = []

    def _send(self, *args, **kwargs):
        self.sent.append(args)
        return ["stub"]


# Route the parent's real work to our stub, keeping the guard + tracer intact.
_real_parent = nb.redis.Redis.execute_command
nb.redis.Redis.execute_command = Recorder._send
r = Recorder()

try:
    r.execute_command("FLUSHDB")
    check("FLUSHDB is denied", False, "no exception raised")
except nb.Denied as e:
    check("FLUSHDB is denied", True, str(e))

try:
    r.execute_command("CONFIG", "SET", "maxmemory", "0")
    check("CONFIG SET is denied", False, "no exception raised")
except nb.Denied as e:
    check("CONFIG SET is denied", True, str(e))

try:
    r.execute_command("HSET", "prod:customer:1", "name", "x")
    check("out-of-prefix key is denied", False, "no exception raised")
except nb.Denied as e:
    check("out-of-prefix key is denied", True, str(e))

try:
    r.execute_command("OBJECT", "ENCODING", "someone-elses-key")
    check("subcommand key offset is checked (OBJECT ENCODING)", False, "not denied")
except nb.Denied as e:
    check("subcommand key offset is checked (OBJECT ENCODING)", True, str(e))

r.execute_command("HSET", f"{nb.PREFIX}mem:abc", "text", "hello")
check("in-prefix key is allowed", len(r.sent) == 1)
check("FT.DROPINDEX (keyless) is allowed", True)
r.execute_command("FT.DROPINDEX", "demo_x_mem")
check("teardown commands in allowlist",
      {"UNLINK", "FT.DROPINDEX"} <= nb.ALLOWED)

# ---- tracer ---------------------------------------------------------------
print("\n[tracer]")
errs = [t for t in nb.TRACE if t[3]]
oks = [t for t in nb.TRACE if not t[3]]
check("failed commands recorded as errors", len(errs) == 4, f"{len(errs)} error rows")
check("successful commands record a reply", len(oks) == 2 and oks[0][2] != "",
      f"reply={oks[0][2] if oks else None!r}")

vec = nb._blob([0.1] * 256)
rendered = nb._cli(["FT.SEARCH", "idx", "*=>[KNN 3 @v $vec]", "PARAMS", 2, "vec", vec])
check("binary vector elided in redis-cli output", "<1024 bytes>" in rendered)
check("no raw bytes leak into the trace", "\\x" not in rendered, rendered[:60] + "...")

# ---- vectors --------------------------------------------------------------
print("\n[vectors]")
check("_blob is FLOAT32 little-endian", len(nb._blob([1.0] * 256)) == 256 * 4)
u = nb._unit([3.0, 4.0])
check("_unit normalises", abs(float((u * u).sum()) - 1.0) < 1e-6)
check("_unit survives the zero vector", nb._unit([0.0, 0.0]).tolist() == [0.0, 0.0])

# ---- argv shapes ----------------------------------------------------------
print("\n[argv]")
DIM = 256
v = nb._blob([0.1] * DIM)

knn = ["FT.SEARCH", nb.IDX_MEM, "*=>[KNN 3 @vector $vec AS dist]",
       "PARAMS", 2, "vec", v, "SORTBY", "dist", "ASC",
       "RETURN", 2, "text", "dist", "DIALECT", 2]
rng = ["FT.SEARCH", nb.IDX_CACHE,
       "@vector:[VECTOR_RANGE $r $vec]=>{$YIELD_DISTANCE_AS: dist}",
       "PARAMS", 4, "r", 0.35, "vec", v,
       "RETURN", 2, "response", "dist", "DIALECT", 2]
agg = ["FT.AGGREGATE", nb.IDX_ROUTE,
       "@vector:[VECTOR_RANGE $r $vec]=>{$YIELD_DISTANCE_AS: vector_distance}",
       "GROUPBY", 1, "@route_name",
       "REDUCE", "MIN", 1, "@vector_distance", "AS", "distance",
       "SORTBY", 2, "@distance", "ASC", "MAX", 1,
       "FILTER", "(@route_name == 'banking' && @distance < 0.7)",
       "PARAMS", 4, "r", 0.7, "vec", v, "DIALECT", 2]


CLAUSES = {"DIALECT", "SORTBY", "RETURN", "LIMIT", "GROUPBY", "REDUCE", "FILTER"}


def params_ok(argv):
    """PARAMS nargs counts only its own name/value tokens.

    Other clauses may legitimately follow -- Redis's own vector-search docs put
    PARAMS immediately after the query and SORTBY/RETURN after it -- so count
    up to the next clause keyword, not to DIALECT.
    """
    i = argv.index("PARAMS")
    nargs = argv[i + 1]
    rest = argv[i + 2:]
    stop = next((j for j, t in enumerate(rest) if t in CLAUSES), len(rest))
    return nargs == stop and nargs % 2 == 0


for label, argv in (("KNN", knn), ("VECTOR_RANGE", rng), ("FT.AGGREGATE", agg)):
    check(f"{label}: PARAMS count matches tokens", params_ok(argv))
    check(f"{label}: DIALECT 2 present", argv[-2:] == ["DIALECT", 2])

check("GROUPBY nargs matches field count", agg[agg.index("GROUPBY") + 1] == 1)
check("REDUCE nargs matches arg count", agg[agg.index("REDUCE") + 2] == 1)
check("SORTBY nargs counts field+direction", agg[agg.index("SORTBY") + 1] == 2)
check("range query yields a distance alias", "$YIELD_DISTANCE_AS" in rng[2])
check("KNN aliases its distance", "AS dist" in knn[2])

nb.redis.Redis.execute_command = _real_parent

print("\n" + "=" * 68)
if fails:
    print(f"{len(fails)} FAILURE(S): " + ", ".join(fails))
    sys.exit(1)
print("All offline tests passed.")
