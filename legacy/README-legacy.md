# Legacy notebook (superseded 2026-09-29)

`notebook.py` here is the earlier 3-act, raw-redis-py design (Agent Memory, LangCache and
Semantic Routing rebuilt from Redis primitives, one credential). It was never run against
a live Redis. Kept for reference only: the guard/tracer, `_cli` renderer and embedder were
harvested into `../iris_basics.py`. `README.md` is its original README.

Known defects found in research (not fixed here): `rediss://` placeholder fails on the free
tier (no TLS); stale "enable Search at creation" claim; redis-py 8.1 defaults to RESP3, so its
list-shaped FT.SEARCH parsing breaks unless `protocol=2`; guard gaps (FT.* index names,
multi-key DEL); pipelines bypass the guard.
