# Phase 5 implementation report

SPEC_SIMGEOPE.md remains authoritative. Phase 5 is complete and validated. The
continuation covers the minimal environmental raster; facilities import, viewer,
Mesa and final milestone quality work remain outside this step. By completed
phase count, Phases 0–5 represent 6/10 (60%) of the roadmap, not 60% of its effort.

## Implemented

- A configurable metric, north-up, globally aligned grid enclosing the full state,
  including Fernando de Noronha, with a 2000000-cell allocation guard.
- Four minimal bands: `state_mask` (bool), `municipality_code` (uint32),
  `water_distance_m` (float32) and `wetland_fraction` (float32).
- Vectorized, bounded-batch preprocessing from already normalized administrative
  and hydro layers. No new production dependencies or raw PBF rescan is required
  when extending a matching Phase 4 world.
- Deterministic `environment_grid.json` and compressed `environment_arrays.npz`,
  strict metadata/band/shape/dtype/nodata validation, read-only arrays loaded once.
- `world.environment.at(lon, lat)` / `.at((lon, lat))` and
  `world.environment_at(point)` with coordinate/facility ID/object overloads.
- Transactional build/CLI/facade integration, manifest counts/caveats/hashes,
  verified Phase 4 reuse, matching cache reuse and Phase 3/4 compatibility.

## Semantics and decisions

- `environment_enabled` requires `hydro_enabled`; disabled defaults are omitted
  from canonical config JSON to preserve older world configuration hashes.
- The mask and municipal label describe the cell centre against canonical WGS84
  polygons. Shared municipality boundaries use the existing lowest-code tie rule.
  Labels do not replace exact `municipality_at()` at arbitrary points.
- Water distance is the exact metric centre-to-feature distance for waterways,
  waterbodies and wetlands. Coastline/basins are excluded consistently with hydro.
  Arbitrary in-cell sampling has a maximum half-cell diagonal displacement;
  at 1km this is about 707.1m, plus float32 rounding. Empty mapped water is unknown
  (`NaN` storage / `None` API), never a fabricated zero distance.
- Wetland fraction is union coverage of mapped wetland polygons clipped to the
  state divided by the full cell area. Overlaps are counted once, holes remain,
  and zero means no mapped coverage rather than proven physical absence.
- Outside-centre cells use label0 and NaN floats. Queries outside the grid or
  centre-defined state mask raise `PointOutsideWorldError`; narrow coastal/sliver
  points may differ from the exact vector domain at coarse resolution.
- The first real build exposed 71 state-covered centres outside all municipal
  polygons, including two Noronha centres. Canonical state minus municipality
  union covers 64.974km², confirming a source-boundary disagreement. Keep those
  cells, use explicit unknown municipality labels (0 / `None`), count and warn.
  No nearest municipality is invented; raw IBGE sources/geometries are preserved.
- A single 621618-cell grid fits 7.71MiB of raw bands; speculative tiling or
  Rasterio is unnecessary for this version. Urban/rural fractions and terrain are
  deferred because they lack a required rule/cheap validated input in this scope.
- Base reuse copies verified admin/roads/hydro into new private staging, matching
  sources and reused-layer settings; raster resolution/output/environment flags
  may differ. Original files remain unchanged. No future-phase module was added.

Full history: [WORLD_DECISIONS.md](WORLD_DECISIONS.md).

## Files changed

New:

| Files | Purpose |
| --- | --- |
| `src/simgeope/world/raster/__init__.py`, `grid.py`, `store.py`, `rasterize.py` | Grid, bands, persistence and runtime sampling |
| `tests/test_raster_grid.py`, `test_raster_store.py`, `test_rasterize.py`, `test_environment_integration.py` | Unit and transactional/facade tests |
| `config/world-environment-local.json` | Pinned Phase 5 output configuration |
| `docs/PHASE_5_REPORT.md` | This report |

Modified:

| Files | Purpose |
| --- | --- |
| `src/simgeope/world/config.py`, `build.py`, `model.py`, `src/simgeope/cli.py` | Optional raster configuration/build, Phase 4 reuse, facade and CLI |
| `tests/test_config.py`, `tests/test_data.py` | Configuration and real raster acceptance |
| `examples/world_queries.py`, `README.md`, `docs/WORLD_DECISIONS.md` | Public usage, measurements and maintained decisions |

Generated world data stays ignored. Legacy simulation sources were not modified.
There is no operational Git repository at the workspace root; file ownership and
review provide the change inventory rather than a Git diff.

## Validation

Commands from the repository root:

```bash
uv sync --extra build --extra dev
uv run simgeope world build --config config/world-environment-local.json --base-world data/world/pe_hydro_v1
SIMGEOPE_WORLD=data/world/pe_environment_v1 uv run pytest -q
uv run simgeope world build --config config/world-environment-local.json
uv run python examples/world_queries.py data/world/pe_environment_v1
```

- Final full suite: **381 passed**, no skips, in **67.73s** (376 non-data cases,
  5 pinned-data smoke cases). All previous road/hydro acceptance remains green.
- Isolated Ruff E4/E7/E9/F/I, formatting of 15 changed Python files and compileall
  passed. Runtime subprocess queries block pyosmium/GeoPandas/OSMnx/NetworkX and
  Rasterio imports. Store tests verify lookup without per-query file reads.
- Tests cover grid alignment/half-open edges/resource caps, invalid CRS/metadata,
  exact band schemas/nodata, read-only arrays, persistence determinism/corruption,
  centre distances, canonical boundary semantics/ties, island/state holes,
  wetland unions/overlap/holes/partial-border area, explicit unknown municipality
  labels, missing water, transactional failure cleanup, config/source mismatch,
  unrecorded outputs, unknown municipal references and cache count verification.
- Synthetic fresh full-layer builds passed. The real build intentionally reuses
  the accepted SHA256-verified Phase 4 canonical outputs rather than repeating
  the full raw road/hydro extraction.

## Real outputs and measurements

Published `data/world/pe_environment_v1` from the same pinned sources/configured
1km metric grid:

| Item | Result |
| --- | ---: |
| Grid rows × columns | 626 × 993 |
| Total grid cells | 621618 |
| State-centre cells | 98175 |
| Explicit unknown municipal labels | 71 |
| Raw band bytes | 8081034 (7.71MiB) |
| Compressed NPZ bytes | 391212 |
| Grid JSON bytes | 229 |
| Total fingerprinted world outputs | 117655805 bytes |

- Incremental Phase 5 build: **12.99s**, peak RSS **371468KiB** (0.354GiB).
- Matching, fully verified cache: **1.27s**, no preprocessing/output writes.
- All inherited administration/road/hydro output hashes are byte-identical to the
  accepted Phase 4 world. Config/manifest and environmental files are new.
- Isolated environmental store load: **15.1ms**; 10000 repeated samples:
  **40.6ms**, approximately **4.06µs per query** on this machine. This measures
  the already-loaded sampler, not complete-world startup or multiprocess scaling.
- Updated public example passed in **58.76s**, peak RSS **2295792KiB**
  (2.19GiB), including full cold world load, road/hydro queries and the
  environmental sample. Recife–Caruaru and its 18 hydro crossings remain unchanged.
- Full-suite peak RSS including real road world load: **2256660KiB** (2.15GiB).
  Raster loading is lightweight; the inherited road cold-start remains expensive.

Sampled municipality codes are correct for Recife, Caruaru, Petrolina and
Noronha. Their cell-centre water distances are respectively 731.72m, 455.19m,
1005.77m and 265.64m. Recife's exact vector-point water distance is 600.64m;
this difference follows the documented centre-sampling semantics. All sampled
wetland fractions at these four points are zero mapped coverage.

## Limitations and next phase

The grid is a compact lookup surface, not the movement space or a hazard model.
Boundary/municipality aliasing follows its chosen resolution. Small islands or
slivers with no covered cell centre can be absent from the raster mask while
remaining in the canonical administrative layer; Noronha's main island is
validated. Municipal unknowns and uneven OSM coverage remain explicit.

The 2000000-cell guard can require a coarser resolution for large extents; this
implementation does not introduce tiled fine-resolution products. Runtime uses
ordinary read-only arrays rather than memory mapping because the bands are small.
A second real fresh raw road+hydro rebuild was not exercised; prior acceptance,
byte-identical canonical reuse, synthetic reproducibility and real cache checks
passed.

Phase 5 has no remaining implementation blocker. Phase 6 can build on the existing
generic facility schema, road snapping and municipality/spatial APIs to add bulk
CSV/XLSX/Parquet import. Viewer, Mesa adapter and final milestone quality pass
remain pending.
