"""Offline calibration for notebook.py -- no Redis required.

Redis COSINE distance on unit vectors is 1 - dot(a, b). So every threshold in
the notebook can be checked here, before anyone points it at a database.

Run:  .venv/Scripts/python.exe calibrate.py
"""

import hashlib
import re

import numpy as np


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def hash_embed(text, dim=256):
    v = np.zeros(dim, dtype=np.float32)
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
        v[h % dim] += 1.0
    return unit(v)


def load():
    try:
        from model2vec import StaticModel

        m = StaticModel.from_pretrained("minishlab/potion-base-8M", force_download=False)
        return (lambda t: unit(m.encode([t])[0])), "potion-base-8M"
    except Exception as exc:
        print(f"!! potion unavailable ({str(exc)[:120]}) -- using hashing fallback")
        return hash_embed, "hashing-fallback"


def dist(a, b):
    """Redis COSINE distance."""
    return float(1.0 - np.dot(a, b))


MEMORIES = [
    "allergic to shellfish",
    "prefers window seats",
    "no dairy after 8pm",
    "travels with a service dog",
    "vegetarian on weekdays",
]
CACHED_PROMPT = "What are your fixed deposit rates?"
ROUTES = {
    "banking": [
        "what is my account balance",
        "transfer money to savings",
        "open a fixed deposit",
        "my card was declined",
    ],
    "off_topic": [
        "what is the weather tomorrow",
        "recommend a good restaurant",
        "who won the football match",
    ],
}
THRESH = {"banking": 0.7, "off_topic": 0.5}

ASK = "does the customer have any seafood restrictions?"
PROBE = "how much do fixed deposits pay?"
PROBE_MISS = "what time does the branch close?"
ROUTE_QUERIES = [
    ("can you recommend somewhere for dinner", "off_topic"),
    ("i need to move funds into my savings account", "banking"),
    ("why was my payment rejected", "banking"),
    ("tell me about black holes", None),
]

embed, name = load()
DIM = len(embed("probe"))
fails = []
print(f"embedder = {name}   DIM = {DIM}   bytes/vector = {DIM * 4}\n")

# ---- Act 1: KNN recall -----------------------------------------------------
print(f"[Act 1] KNN recall for: {ASK!r}")
qv = embed(ASK)
ranked = sorted(((dist(qv, embed(m)), m) for m in MEMORIES))
for d, m in ranked[:3]:
    print(f"   {d:.3f}  {m}")
if ranked[0][1] != "allergic to shellfish":
    fails.append(f"Act 1 top-1 is {ranked[0][1]!r}, expected the shellfish memory")
print(f"   -> top-1 correct: {ranked[0][1] == 'allergic to shellfish'}")
print(f"   -> margin over #2: {ranked[1][0] - ranked[0][0]:.3f}\n")

# ---- Act 2: VECTOR_RANGE radius -------------------------------------------
cv = embed(CACHED_PROMPT)
d_hit = dist(embed(PROBE), cv)
d_miss = dist(embed(PROBE_MISS), cv)
print(f"[Act 2] cache entry: {CACHED_PROMPT!r}")
print(f"   paraphrase {PROBE!r:45} dist {d_hit:.3f}")
print(f"   unrelated  {PROBE_MISS!r:45} dist {d_miss:.3f}")
lo, hi = d_hit, d_miss
if lo >= hi:
    fails.append("cache paraphrase is not closer than the unrelated probe")
else:
    mid = round((lo + hi) / 2, 2)
    print(f"   -> any radius in ({lo:.3f}, {hi:.3f}) separates them; notebook default 0.35")
    print(f"   -> suggested default radius: {mid}")
    if not (lo < 0.35 < hi):
        fails.append(f"default radius 0.35 does NOT separate hit/miss; use {mid}")
print()

# ---- Act 2: router ---------------------------------------------------------
print("[Act 2] router (REDUCE MIN, radius = max threshold = "
      f"{max(THRESH.values())})")
for q, expected in ROUTE_QUERIES:
    per_route = {
        r: min(dist(embed(q), embed(ref)) for ref in refs) for r, refs in ROUTES.items()
    }
    inside = {r: d for r, d in per_route.items() if d < THRESH[r]}
    got = min(inside, key=inside.get) if inside else None
    ok = got == expected
    flag = "ok " if ok else "FAIL"
    detail = "  ".join(f"{r}={d:.3f}" for r, d in sorted(per_route.items()))
    print(f"   [{flag}] {q!r}\n          {detail}  -> {got}  (expected {expected})")
    if not ok:
        fails.append(f"router: {q!r} -> {got}, expected {expected}")

# AVG vs MIN, the point of the dropdown
print("\n[Act 2] MIN vs AVG on the dinner query")
q = "can you recommend somewhere for dinner"
for label, fn in (("MIN", min), ("AVG", lambda xs: sum(xs) / len(xs))):
    agg = {r: fn([dist(embed(q), embed(ref)) for ref in refs]) for r, refs in ROUTES.items()}
    inside = {r: d for r, d in agg.items() if d < THRESH[r]}
    print(f"   {label}: " + "  ".join(f"{r}={d:.3f}" for r, d in sorted(agg.items()))
          + f"  -> {min(inside, key=inside.get) if inside else None}")

print("\n" + "=" * 68)
if fails:
    print(f"{len(fails)} CALIBRATION FAILURE(S):")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("All calibration checks passed.")
