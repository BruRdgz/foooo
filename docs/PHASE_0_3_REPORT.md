# Phases 0–3 implementation report

Phases 0–3 are implemented and validated against the locally pinned inputs.
The initial dependency/DNS blocker was resolved when network access was enabled.
No hydrology, raster, Mesa integration or viewer implementation was added.

## Delivered behavior

- Phase 0: bounded legacy geography audit and function-level reuse map; legacy
  source/data preserved.
- Phase 1: validated configuration and configurable metric CRS, explicit errors,
  versioned/fingerprinted provenance manifest, lazy facade and reproducible CLI.
- Phase 2: cached/local IBGE adapter, WGS84 normalization and GeoParquet,
  completeness/name/code checks, indexed queries, Fernando de Noronha retained.
- Phase 3: local pyosmium extraction with metric padding and class/access
  filtering, attribute-preserving simplification, directed rich canonical
  node/edge GeoParquet and derived compact CSR NPZ, spatial indexes, routing,
  immutable closures/multipliers and facility snapping/index/persistence primitives.
- Coordinator: transactional build/cache/publication, integration tests, runtime
  isolation checks, example and documentation. Three initial concurrent workstreams
  were used; two bounded workers resumed core/road validation after the interruption.

## Decisions

See `WORLD_DECISIONS.md` and `LEGACY_REUSE_MAP.md`. Key choices: configurable local
metric CRS and WGS84 interchange; all components retained; rich canonical edges
as authority; deterministic per-way simplification with OSMnx optional; derived
CSR routing; immutable overlays; nearest-node snapping with limits; source-aware
cache; private staging and no overwrite; explicit unknown IBGE vintage.
`uv.lock` now pins the resolved dependencies.

## Validation results

| Check | Result |
| --- | --- |
| Core/config/CRS/admin/manifest tests | 47 passed |
| Road/extraction/routing/snapping tests | 53 passed |
| Coordinator integration plus real IBGE test | 6 passed |
| Combined non-data suite | 123 passed, 3 data cases deselected in 1.88 s |
| Marked real-data suite | 3 passed, 123 deselected in 58.26 s |
| Lint E4/E7/E9/F/I and Python compileall | Passed |
| CLI help without GIS imports | Passed |
| Clean pinned world build | Passed,8m06.16s,peak 2.92 GiB |
| Matching cache reuse | Passed,1.99s |
| Public facade example | Passed,55.08s,peak 2.18 GiB including world load and queries |
| Runtime without GeoPandas/OSMnx/NetworkX/pyosmium | Passed in isolated subprocess |

The targeted rows overlap the combined suites; the total is **126 passed cases**,
not their sum. No test assertions were weakened and no substitutes were used for
GIS dependencies. All earlier collection failures are superseded.

Real-data checks cover all 185 valid administrative divisions, repaired geometry
warnings, Recife/Caruaru/Petrolina/Noronha lookup, plausible road counts,
Recife–Caruaru and Caruaru–Petrolina routes, canonical geometry validity, closure
invariants and explicit disconnected/absent island-road behavior.

Logs for this session: `/tmp/simgeope-pytest-unit.log`,
`/tmp/simgeope-pytest-data.log`, `/tmp/simgeope-build.log`,
`/tmp/simgeope-cache.log`, `/tmp/simgeope-example.log`.

## Built world

`data/world/pe_v1` contains 185 divisions, 190699 road nodes and 446562 directed
edges. Fingerprinted outputs total 107025239 bytes (102.1MiB):config, state and
municipalities, rich road nodes/edges and derived routing arrays; manifest stores
raw/output checksums, source date/URLs, configuration and warnings.

IBGE code 2607208 had invalid source geometry; repair was preserved and warned.
No invalid/unresolved road geometries were dropped in this pinned build.

The public example resolved Recife 2611606, found a road113.50 m from the query,
and routed Recife–Caruaru over 133480.51 m with 4932.22 s baseline cost (352 edges).
These are observed results for the pinned data, not fragile test expectations.

## Limitations and readiness for Phase 4

- Cold startup is costly:the public example takes 55 seconds and peaks at 2.18 GiB
  including loading and a route. Metric geometry/index construction and baseline
  validation happen during load, not per tick. Reuse one loaded world; startup
  cost remains a performance improvement target.
- OSM turn restrictions and conditional access/speed are not modeled.
  Reversible/alternating ways are excluded with warnings; unknown speeds use
  road-class defaults. Mapping completeness affects routing quality.
- Node snapping excludes first/last-mile costs and partial-edge movement;
  nearby coordinates can snap to the same node and return zero cost.
- Overlay routing constructs a temporary CSR in O(E). More aggressive runtime
  optimization and full statewide connectivity audits remain future quality work.
- IBGE vintage is unknown; raw SHA256 pins the local source bytes.
- A second clean raw-data rebuild was not performed. Synthetic reproducibility,
  canonical consistency/integrity and matching real cache reuse passed. Pin
  SOURCE_DATE_EPOCH when byte-identical manifests are needed.
- Bulk facility import is Phase 6. Hydrology/raster/Mesa/viewer remain deferred.
- Root Git metadata is an empty protected placeholder. No commit/diff against a
  root Git baseline is available; legacy Git status remained unchanged.

Phase 4 prerequisites are now met:tested admin/road APIs, actual built world,
passing unit/integration/data suites and pinned dependencies. Hydrology can be
implemented as the next isolated workstream without mixing it into this run.
The full milestone still includes later phases; this is not a claim that they
are complete.

```bash
uv sync --locked --extra build --extra dev
uv run pytest -m 'not data'
uv run simgeope world build --config config/world-local.json
uv run pytest -m data
uv run python examples/world_queries.py data/world/pe_v1
```

## Files created

- `.gitignore`
- `README.md`
- `config/world-local.json`
- `docs/LEGACY_REUSE_MAP.md`
- `docs/PHASE_0_3_REPORT.md`
- `docs/WORLD_DECISIONS.md`
- `examples/world_queries.py`
- `pyproject.toml`
- `src/simgeope/__init__.py`
- `src/simgeope/__main__.py`
- `src/simgeope/cli.py`
- `src/simgeope/world/__init__.py`
- `src/simgeope/world/administrative.py`
- `src/simgeope/world/build.py`
- `src/simgeope/world/config.py`
- `src/simgeope/world/exceptions.py`
- `src/simgeope/world/facilities.py`
- `src/simgeope/world/manifest.py`
- `src/simgeope/world/model.py`
- `src/simgeope/world/roads/__init__.py`
- `src/simgeope/world/roads/build.py`
- `src/simgeope/world/roads/extract.py`
- `src/simgeope/world/roads/model.py`
- `src/simgeope/world/roads/routing.py`
- `src/simgeope/world/roads/state.py`
- `src/simgeope/world/sources/__init__.py`
- `src/simgeope/world/sources/ibge.py`
- `src/simgeope/world/sources/osm.py`
- `src/simgeope/world/spatial.py`
- `tests/conftest.py`
- `tests/test_administrative.py`
- `tests/test_build.py`
- `tests/test_cli.py`
- `tests/test_config.py`
- `tests/test_data.py`
- `tests/test_facility_snap.py`
- `tests/test_manifest.py`
- `tests/test_osm_source.py`
- `tests/test_road_extract.py`
- `tests/test_roads.py`
- `tests/test_routing.py`
- `tests/test_state.py`
- `tests/test_world.py`
- `uv.lock`
