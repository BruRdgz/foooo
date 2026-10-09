# SimGeoPE

Pernambuco's static administrative, road, hydrology and environmental world/query engine. `SPEC_SIMGEOPE.md`
is authoritative. This implementation covers Phases 0–9; the existing
`pibex_vfinal` reference is preserved.

Implemented: configurable metric CRS, versioned manifests, cached local IBGE
normalization including Fernando de Noronha, directed rich OSM roads, canonical
GeoParquet, compact SciPy routing, indexed nearest-road queries, facility snapping
primitives, immutable dynamic road costs/closures, and indexed OSM hydrology with
precomputed road/water intersections, a compact environmental raster, static
facility import, a local inspection viewer, and a thin optional Mesa adapter.

## Setup and pinned build

Python 3.12 or newer, `uv`, and access to the required package wheels are needed.
The `build` extra installs pyosmium. Runtime does not require GeoPandas, OSMnx,
NetworkX or pyosmium. No raw source is downloaded automatically.

From the workspace root:

```bash
uv sync --extra build --extra dev
uv run simgeope world build --config config/world-local.json
uv run simgeope world build --config config/world-hydro-local.json --base-world data/world/pe_v1
uv run simgeope world build --config config/world-environment-local.json --base-world data/world/pe_hydro_v1
uv run python examples/world_queries.py data/world/pe_environment_v1
```

Optional feature groups are installed separately: `spreadsheet` enables XLSX
facility input, `viewer` enables the local inspection server, and `simulation`
enables Mesa integration. The geographic runtime itself does not import them.

The sample config uses the existing local Geofabrik
`pibex_vfinal/data/raw/nordeste-260930.osm.pbf`, state/municipal IBGE GeoJSON and
name metadata. OSM date and URL are pinned. IBGE vintage is unknown, recorded as
such; local source checksums pin its bytes. Paths resolve relative to the working
directory. Use `--config` or the CLI source flags to select other local inputs.

A successful build writes `manifest.json`, `config.json`,
`municipalities.parquet`, `state_boundary.parquet`, `roads_nodes.parquet`,
`roads_edges.parquet` and `roads_routing.npz` under `data/world/pe_v1`.

Builds stage privately before publication. A matching, integrity-checked world
is reused. Different source bytes or configuration require a new output directory;
existing worlds are never deliberately overwritten. The configuration hash
includes paths, including `output_dir`. `SOURCE_DATE_EPOCH` can pin the manifest
build timestamp for byte-reproducible builds; otherwise it records actual UTC
build time. Byte reproducibility assumes the same software environment.

## Python API

```python
from simgeope import PernambucoWorld
from simgeope.world.roads import RoadRuntimeState

world = PernambucoWorld.load("data/world/pe_v1")
recife = (-34.88, -8.05)
caruaru = (-35.97, -8.28)
municipality = world.municipality_at(recife)
road = world.nearest_road(recife)
route = world.route(recife, caruaru)
seconds = world.travel_time(recife, caruaru)
metres = world.distance(recife, caruaru)

state = RoadRuntimeState(closed_edges={route.edge_ids[0]})
alternative = world.route(recife, caruaru, state=state)
# May raise RouteNotFoundError when the overlay disconnects the endpoints.
```

Distances/radii use metres, travel times seconds, input coordinates WGS84
`(lon, lat)`. `distance()` returns distance along the fastest-time route, rather
than independently optimizing length. Integer route endpoints refer to road node
IDs. Facility IDs/objects are supported when a facility index is attached.
Outside administrative boundaries, `municipality_at()` returns `None`. No valid
nearby road produces `NoRoadNearbyError`; disconnected routes produce
`RouteNotFoundError`.

`RoadNetwork.nearest_road()` returns edge projection information and its nearest
endpoint. Route coordinate inputs and `snap_facility()` instead use the global
nearest-node index and check node snap distance. Snaps are static; the facility
primitive persists node, incident-edge, municipality and distance in Parquet.
CSV/XLSX/Parquet facilities can be added during a build with
`--facilities-file path/to/facilities.csv`; the importer assigns municipality
from the coordinate, snaps to the nearest road node and writes
`facilities.parquet`. Required columns are `id,kind,lon,lat`; `name` and
`municipality_code` are optional. Legacy creator/hub sheets are accepted, but
only static identity and location are retained. Install `spreadsheet` for XLSX.
Canonical road geometry/metadata are
accessible through `world.roads.edge(edge_id)` and `.geometry(edge_id)`.

Each directed edge has a distinct ID. Close both directional IDs to close a
bidirectional physical segment. Parallel edges remain available under overlays.
Dynamic state never rewrites canonical files.

## Hydrology

`config/world-hydro-local.json` enables Phase 4 and publishes a separate
`data/world/pe_hydro_v1`. Omit `--base-world` for a fresh administration/road/hydro
build. The optional base must be a verified Phase 3 world with identical source
hashes and administration/road settings; its files are copied into private
staging. Base files are preserved. Existing Phase 3 worlds remain loadable with
`world.hydro is None`.

```python
world = PernambucoWorld.load("data/world/pe_hydro_v1")
metres = world.hydro.distance_to_water(recife)
river = world.hydro.nearest_waterway(recife)
waterbody = world.hydro.nearest_waterbody(recife)
crossings = world.hydro.intersections_with_road(road.edge_id)
basin = world.hydro.basin_at(recife)  # None without an authoritative basin source
```

Hydro features expose `id`, `kind`, `name`, `geometry_wgs84`, provenance, raw
OSM tags and municipality codes. Source adapters implement `HydroSource` in
`simgeope.world.sources.hydro`. OSM extraction is cached across layer methods and
assembles multipolygon relations, including holes. Full intersecting geometries
are retained against the buffered state footprint, including Noronha.

Rivers/canals are included by default. Minor streams are excluded;
`hydro_include_named_streams=true` enables a named-stream heuristic with a warning.
`hydro_min_waterbody_area_m2` defaults to 10000 and may be set to zero. Taxonomy
includes lake, reservoir, pond, lagoon and uncertain generic `water`, plus
wetlands/coastline. No hydrographic basins are fabricated. Empty nearest/water
queries return `None`. Distance includes waterways, waterbodies and wetlands;
coastline is separate and does not substitute for ocean polygons.

Phase 4 adds six fingerprinted files: `waterways.parquet`, `waterbodies.parquet`,
`wetlands.parquet`, `coastline.parquet`, `basins.parquet` (typed empty for OSM), and
`road_hydro_crossings.parquet`. All vector files use WGS84 GeoParquet; queries and
crossings use the configured metric CRS. Crossings preserve directed road IDs and
recorded bridge flags. Each disconnected linear overlap contributes its midpoint;
this describes geometric overlap and does not establish a physical bridge.

## Environmental raster

`config/world-environment-local.json` enables Phase 5 with hydrology and publishes
`data/world/pe_environment_v1`. A verified Phase 4 `--base-world` reuses admin,
roads and hydro without re-extraction. Omit the base for a fresh full build.
`--environment` enables both optional layers; `--raster-resolution-m` selects the
cell size. New disabled defaults preserve Phase 3/4 configuration hashes.

```python
world = PernambucoWorld.load("data/world/pe_environment_v1")
cell = world.environment.at(-34.88, -8.05)
cell = world.environment_at((-34.88, -8.05))
# {'state_mask': True, 'municipality_code': '2611606',
#  'water_distance_m': 731.72..., 'wetland_fraction': 0.0}
```

Four read-only NumPy bands are loaded once from compressed NPZ; grid metadata is
stored in JSON. No Rasterio dependency or per-query raster file reads are needed.
The default aligned 1km grid covers the mainland and Noronha. A 2000000-cell
allocation limit rejects excessive requested extents/resolutions.

Mask/municipal labels and water distance describe each cell centre. Water distance
includes mapped waterways/waterbodies/wetlands; in-cell samples can differ from
exact vector-point distance by roughly half the cell diagonal (707m at 1km).
Wetland fraction uses mapped union area clipped to the state divided by full cell
area, retaining holes and avoiding duplicate overlap area. It is not flood risk.

Queries outside the grid or centre-defined state mask raise
`PointOutsideWorldError`; exact vector-domain membership remains available via
`municipality_at()`. Empty mapped water returns `None`. The pinned IBGE sources
have 71 state-centre cells without municipal coverage, explicitly represented as
`municipality_code=None` and counted/warned in the manifest. No municipality is
inferred from proximity. Urban/rural fractions, terrain and hazards are deferred.

## Local viewer and Mesa adapter

Serve an already built world on loopback with `uv run --extra viewer simgeope
serve --world data/world/pe_environment_v1`. The viewer exposes bounded, read-only
layer queries, point inspection and A/B routing with temporary edge closure/cost
overlays. It requires browser access to the MapLibre CDN and basemap tiles; the
world APIs themselves work offline. The viewer package is an inspection client,
not a scenario editor.

`uv sync --extra simulation` enables `simgeope.mesa_adapter.WorldModel`, which
holds the same loaded `PernambucoWorld` object and delegates route/environment
queries. See `examples/mesa_world_model.py`; no domain agents or hazard rules are
part of the adapter.

## Validation

```bash
uv run pytest -m 'not data'
uv run pytest -m data
# For a world in a nondefault location:
SIMGEOPE_WORLD=/path/to/world uv run pytest -m data
```

Real IBGE tests use pinned local sources. Real route smoke tests require a built
world and otherwise skip explicitly. Synthetic tests cover directed/parallel
routing, islands, closures, cost multipliers, rich persistence, generated actual
PBF extraction, snapping, facade loading without build libraries, and build
publication/cache/failure behavior.

The dependency blocker was resolved with `uv sync --extra build --extra dev`;
`uv.lock` pins the environment. All 381 tests passed against the Phase 5 world,
including five real-data smoke tests. Phase 5 generated its 621618-cell grid in
12.99s by reusing verified canonical inputs, adding about 382KiB of compressed
environmental output; cache reuse took 1.27s. The original road/hydro counts and
hashes remain unchanged. See the [Phase 5 report](docs/PHASE_5_REPORT.md),
[Phase 4 report](docs/PHASE_4_REPORT.md) and
[Phases 0–3 report](docs/PHASE_0_3_REPORT.md) for the staged acceptance history.

Hydrology is static OSM geography, with uneven coverage and uncertain generic
water taxonomy. Invalid/unassembled hydro counts describe the source-wide scan
before filtering because unlocatable features cannot be assigned to Pernambuco.
OSM turn restrictions/conditional tags are not modeled; reversible/alternating
ways are excluded with warnings. Speeds are inferred by road class when no
numeric speed is supplied. Node snapping excludes first/last-mile access costs
and within-edge progress, so very nearby coordinates can snap to the same node
and produce a zero-length route. The Phase 5 fresh-process public example took 58.76 seconds
and peaked at 2.19 GiB, including world loading and routing; startup performance
remains a target for improvement. Statewide connectivity beyond the selected
smoke journeys and a second fresh raw-data rebuild are not yet exhaustively
validated.

[Decisions](docs/WORLD_DECISIONS.md) · [Legacy reuse map](docs/LEGACY_REUSE_MAP.md)
