# Phase 4 implementation report

SPEC_SIMGEOPE.md remains authoritative. Phase 4 is complete; Phases 5–9 remain
outside this continuation. The accepted Phase 3 world and legacy simulation are
preserved.

## Implemented

- Interchangeable `HydroSource` protocol and a cached, local pyosmium OSM adapter.
- Rivers/canals, configurable named-stream heuristic, lake/reservoir/pond/lagoon
  and uncertain water polygons, wetlands and coastline. Multipolygon relations
  retain holes; duplicate member polygons are removed. No basins are fabricated.
- Five canonical WGS84 GeoParquet layers with source IDs/date/file, raw tags,
  municipality codes and typed empty schemas, plus road/hydro crossing Parquet.
- Indexed metric nearest-waterway/waterbody and water-distance queries, optional
  basin lookup, and cached crossing lookup by directed road edge.
- Geometric crossing preprocessing in bounded Arrow batches, preserving recorded
  bridge flags and every directed edge ID. Disconnected overlaps produce separate
  representative points, with deterministic midpoint selection for line overlaps.
- Transactional Phase 4 build/configuration, manifest counts/warnings/checksums,
  backward-compatible Phase 3 loading and explicit optional reuse of verified
  Phase 3 road/admin outputs. Reuse checks source hashes, relevant config, file
  fingerprints and canonical row counts; mismatches/failures preserve the base.

## Files changed in this continuation

New:

| Files | Purpose |
| --- | --- |
| `src/simgeope/world/hydro/__init__.py`, `model.py`, `crossings.py` | Runtime records, persistence/indexes and crossing preprocessing |
| `src/simgeope/world/sources/hydro.py` | Source protocol and OSM normalization/extraction |
| `config/world-hydro-local.json` | Pinned Phase 4 build into a separate directory |
| `tests/test_hydro.py`, `test_hydro_source.py`, `test_hydro_crossings.py`, `test_hydro_integration.py` | Hydrology unit and transactional integration tests |
| `docs/PHASE_4_REPORT.md` | This report |

Modified:

| Files | Purpose |
| --- | --- |
| `src/simgeope/world/config.py`, `build.py`, `model.py` | Validated hydro settings, build/reuse, manifest-backed facade loading |
| `src/simgeope/world/sources/__init__.py`, `src/simgeope/cli.py` | Lazy source exports and CLI hydro/base-world flags |
| `tests/test_config.py`, `tests/test_data.py` | Legacy hash compatibility and real hydro acceptance |
| `examples/world_queries.py` | Public hydrology query/crossing example |
| `README.md`, `docs/WORLD_DECISIONS.md` | Usage and maintained implementation decisions |

Generated world files under `data/` remain ignored. No legacy simulation files
were modified. The workspace root has no operational Git metadata, so this
inventory records implementation ownership rather than a Git diff.

## Decisions

- Hydrology is opt-in (`hydro_enabled=true`). Default-valued additive config
  fields are omitted from canonical JSON, preserving Phase 3 config hashes.
- Major river/canal OSM tags are accepted; streams are excluded by default.
  Named streams are an explicit, nonauthoritative heuristic. Minimum waterbody
  area defaults to 10000 square metres and is configurable, including zero.
- Water distance includes waterways, waterbodies and wetlands; coastline is
  separately persisted and does not substitute for an ocean polygon. Empty water
  queries and unavailable basins return `None`.
- Retain full intersecting geometries within the actual buffered state footprint,
  including Noronha, rather than selecting a mainland/island bounding rectangle.
- Crossings are geometric observations. A midpoint inside a water polygon does
  not establish a physical bridge, flood hazard or hydrological connectivity.
  Recorded bridge flags are preserved without inference.
- OSM repair/drop diagnostics are source-wide before state filtering: invalid or
  unassembled geometries cannot be reliably attributed solely to Pernambuco.
- `--base-world` supports verified Phase 3 inputs only, copying into private
  staging. Accepted originals are never overwritten. Cache/reuse row-count checks
  were added after an independent review found inconsistent manifest counts could
  otherwise be propagated into a new world.

Full decision history: [WORLD_DECISIONS.md](WORLD_DECISIONS.md).

## Validation and real outputs

Reproducible commands from the repository root:

```bash
uv sync --extra build --extra dev
uv run simgeope world build --config config/world-hydro-local.json --base-world data/world/pe_v1
SIMGEOPE_WORLD=data/world/pe_hydro_v1 uv run pytest -q
uv run simgeope world build --config config/world-hydro-local.json
uv run python examples/world_queries.py data/world/pe_hydro_v1
```

- Source tests: 56 passed; runtime/persistence: 40 passed; crossings: 24 passed;
  Phase 4 integration: 11 passed. Configuration includes six additional hydro
  compatibility/validation cases.
- Final full suite against the Phase 4 real world: **264 passed**, no skips,
  in 64.51 seconds (260 non-data cases, 4 pinned-data smoke cases).
- Isolated Ruff E4/E7/E9/F/I, changed-Python-file formatting and compileall passed.
- Synthetic checks include actual generated PBF/XML equivalence, polygon holes,
  source caching, invalid/missing geometry, repair, metric thresholds/buffer,
  island filtering, uncertain taxonomy, empty layers, deterministic persistence,
  corrupted schemas/CRS/references, directed crossings and overlap components,
  build publication/cache/failure cleanup and runtime imports without build tools.
- Real tests validate all administrative/road acceptance from Phases 0–3,
  Capibaribe/nearest-water queries, source/date provenance, island coastline,
  unavailable basins and crossing point/road/bridge invariants.

Published `data/world/pe_hydro_v1` from the same SHA256-pinned OSM/IBGE bytes:

| Feature | Count |
| --- | ---: |
| Administrative divisions | 185 |
| Road nodes | 190699 |
| Directed road edges | 446562 |
| Waterways | 3266 |
| Waterbodies | 3295 |
| Wetlands | 788 |
| Coastline features | 237 |
| Basins | 0 |
| Road/hydro crossing records | 5907 |

- Phase 4 build with verified Phase 3 reuse: **3m26.87s**, peak RSS 1524408 KiB
  (1.45 GiB). This is an incremental build measurement, not a fresh full-road
  rebuild. Synthetic fresh full-layer builds also passed.
- All reused canonical administration/road output hashes remain byte-identical
  to the accepted Phase 3 world. Total fingerprinted outputs: 117264331 bytes
  (111.83 MiB), roughly 9.76 MiB additional hydro data/configuration.
- Matching Phase 4 cache reuse: **1.91s**, with no preprocessing or output writes.
- Manifest records 4 repaired hydro polygons and 74 invalid/unassembled drops,
  both source-wide; the earlier IBGE repair warning is retained. IBGE vintage,
  uncertain water taxonomy and unavailable authoritative basins remain explicit.
- Recife `(-34.88, -8.05)` returns water distance **600.637766m**; nearest waterway
  `osm:way:367789900`, Capibaribe (river); nearest waterbody `osm:way:573277852`,
  Rio Capibaribe (generic water); basin `None`.

- Public example succeeded in **57.51s**, peak RSS 2275236 KiB (2.17 GiB),
  including full-world cold loading, nearest road, Recife–Caruaru routing, hydro
  distance/nearest queries and 18 geometric crossing records along that route.

## Limitations and Phase 5 readiness

OSM coverage and stream importance are not authoritative. No ANA/IBGE hydrographic
basin source is present. Shorelines remain lines, without inferred ocean polygons.
Small waterbodies and minor streams are excluded by default. Crossing locations
summarize geometric components, rather than entry/exit pairs or flood-risk rules.
Source-wide drop counts do not represent Pernambuco-only data quality.

Full raw road+hydro real-data rebuild byte determinism was not re-exercised;
existing pinned-road acceptance, unchanged road hashes, synthetic deterministic
hydro persistence and real cache verification passed. The runtime retains the
Phase 3 cold-load cost: final suite including one real full-world load peaked at
2306876 KiB (2.20 GiB). Load once and reuse outside simulation ticks.

Phase 5 has validated administrative masks/labels and indexed water distance/
wetland geometry inputs. There is no remaining Phase 4 implementation blocker.
The next phase still needs its minimal configurable grid, selected coverage-band
semantics and compact runtime array storage; no terrain/elevation source is
required by the specification's initial bands. Raster, bulk facility import,
viewer and Mesa remain unimplemented.
