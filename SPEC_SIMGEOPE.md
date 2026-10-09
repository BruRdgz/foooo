# SPEC — SimGeoPE: Pernambuco World Model for Mesa

## 0. Purpose

Build **SimGeoPE**, a reusable, deterministic and performant geospatial substrate for Mesa-based simulations of Pernambuco.

The deliverable is **not** a disease model and not a generic GIS for its own sake. SimGeoPE is a **world/query engine** for Pernambuco, with a lightweight graphical viewer used to inspect, validate and debug the world. The viewer is a consumer of the same public world API used by Mesa; it must never become a dependency of simulation logic.

The core design principle is:

> **Preprocess heavy GIS once; keep the simulation hot loop light.**

The world layer must remain independent from any specific epidemiological, control, or scenario logic.

---

## 1. Scope

### 1.1 Must support

The world model must represent:

1. Pernambuco state boundary and municipalities.
2. A real road network suitable for intermunicipal logistics and routing.
3. Hydrology relevant to spatial/environmental queries:
   - rivers;
   - major streams;
   - canals;
   - lakes/reservoirs/ponds/lagoons;
   - wetlands;
   - coastline;
   - hydrographic basins when a reliable source is available.
4. Facilities/points of interest used by the simulation:
   - farms/producers;
   - processing facilities;
   - distribution hubs;
   - offices/service points;
   - other user-provided simulation facilities.
5. A lightweight environmental raster for spatial sampling.
6. Spatial indexes for fast point, radius, nearest-feature and intersection queries.
7. A routing representation fast enough to be called repeatedly from Mesa.
8. A clean adapter/API that Mesa agents can consume without importing GeoPandas/OSMnx/pyosmium in the simulation loop.
9. A lightweight graphical world viewer for inspection/debug of municipalities, roads, hydrology, facilities, raster overlays and routes.

### 1.2 Must NOT implement in this milestone

Do not implement:

- SEIR or any other transmission model;
- control measures;
- scenario-specific hazard formulas;
- Monte Carlo orchestration beyond what is needed for tests;
- a full simulation dashboard or production GIS editor;
- satellite-based water permanence;
- detailed flood simulation;
- 30 m terrain/DEM processing unless required by a testable rule;
- every residential street in the state;
- every first-order stream in OSM;
- hexagonal circulation zones as part of the physical world;
- dynamic disease or policy state inside geographic objects.

These can be added later as consumers of the world API.

---

## 2. Legacy assets to reuse conceptually

There is an existing SimBio-PE codebase. Reuse its proven ideas where useful, but do **not** blindly port the full architecture.

Useful legacy concepts:

- IBGE administrative boundaries.
- Reproducible, dated Geofabrik/OSM PBF input.
- `pyosmium` extraction instead of repeatedly querying Overpass.
- A preprocessing/build command with persistent cache.
- Rasterization helpers for presence/coverage/distance/labels.
- Road extraction from local OSM data.
- OSMnx only as a build-time helper.
- Conversion of the road graph to SciPy sparse structures for runtime routing.
- Derived outputs stored outside Git.
- Deterministic manifests/hashes for rebuilds.

Important legacy problems to fix:

- Do **not** use EPSG:5880 as the main metric CRS for Pernambuco.
- Do **not** reduce the road network to only `(x, y, travel_time_matrix)` and discard edge attributes.
- Do **not** make OSM `waterway=stream` the authoritative hydrology layer.
- Do **not** delete Fernando de Noronha from the administrative world model.
- Do **not** mix hazard/seasonality formulas into the geographic base.

---

## 3. Architectural model

The world is hybrid:

```text
PernambucoWorld
│
├── administrative
│   ├── state boundary
│   └── municipalities
│
├── network
│   └── roads
│
├── hydro
│   ├── waterways
│   ├── water bodies
│   ├── wetlands
│   ├── coastline
│   └── basins (optional source-dependent enrichment)
│
├── environment
│   └── lightweight raster fields
│
├── facilities
│   └── farms / hubs / other POIs
│
└── spatial indexes
```

Mesa sits **above** this layer:

```text
Mesa Model
│
├── Agent state
├── Scenario state
├── Dynamic road disruption state
└── Processes
      │
      ▼
PernambucoWorld  (mostly immutable)
```

### 3.1 Static world vs dynamic simulation state

Keep static geography separate from mutable simulation state.

Example:

```text
RoadEdge
    geometry          -> world/static
    length            -> world/static
    highway class     -> world/static
    municipality      -> world/static

RoadRuntimeState
    closed            -> simulation/dynamic
    congestion        -> simulation/dynamic
    cost multiplier   -> simulation/dynamic
```

Likewise:

```text
Facility
    coordinates       -> world/static
    municipality      -> world/static
    snapped road node -> world/static

FacilityRuntimeState
    inventory         -> simulation/dynamic
    status            -> simulation/dynamic
    restrictions      -> simulation/dynamic
```

The world should be shareable across replicas without copying heavy GIS objects.

---

## 4. Package layout

The new project/package name is **SimGeoPE**. Prefer a top-level Python package named `simgeope`. If the repository uses `src/`, place it under `src/simgeope/`.

```text
simgeope/
  world/
    __init__.py
    model.py
    config.py
    build.py
    manifest.py

    administrative.py
    facilities.py
    spatial.py

    roads/
      __init__.py
      extract.py
      build.py
      model.py
      routing.py
      state.py

    hydro/
      __init__.py
      source.py
      osm.py
      build.py
      model.py

    raster/
      __init__.py
      grid.py
      rasterize.py
      store.py

    sources/
      ibge.py
      osm.py

    mesa_adapter.py

  api/
    __init__.py
    app.py
    routes_world.py
    routes_query.py

  web/
    index.html
    app.js
    styles.css
```

Do not restructure unrelated code just to match this layout. The API/web layer depends on the world facade; the world package must not depend on the API/web layer.

---

## 5. Coordinate reference systems

### 5.1 Storage CRS

Persist portable vector source geometries in:

- EPSG:4326 (WGS84) for interchange/export.

### 5.2 Metric working CRS

Use a configurable local projected CRS for distance/area operations over Pernambuco.

Default candidate:

```text
+proj=tmerc
+lat_0=0
+lon_0=-37.5
+k=1
+x_0=500000
+y_0=10000000
+ellps=GRS80
+units=m
+no_defs
```

Do not hardcode the CRS in implementation logic. Put it in `WorldConfig`.

All distance/radius APIs must operate in metres.

---

## 6. Core data models

Prefer lightweight dataclasses / NumPy arrays / Arrow/Parquet tables at runtime. Do not keep large GeoDataFrames or NetworkX graphs alive inside every Mesa model.

### 6.1 Municipality

```python
@dataclass(frozen=True, slots=True)
class Municipality:
    code: str
    name: str
    geometry_wgs84: object
```

Runtime queries should use a prebuilt spatial index rather than scanning geometries.

### 6.2 Facility

Generic geographic facility; scenario-specific properties live elsewhere.

```python
@dataclass(frozen=True, slots=True)
class Facility:
    id: str
    kind: str
    lon: float
    lat: float
    municipality_code: str | None
    road_node: int | None
```

Required facility kinds are user-extensible strings. Do not encode disease behavior here.

### 6.3 RoadNode

```python
@dataclass(frozen=True, slots=True)
class RoadNode:
    id: int
    lon: float
    lat: float
    x_m: float
    y_m: float
```

### 6.4 RoadEdge

The persistent rich edge table must retain enough information to support later network disruption and analysis.

At minimum:

```python
@dataclass(frozen=True, slots=True)
class RoadEdge:
    id: int
    u: int
    v: int

    length_m: float
    base_travel_time_s: float

    highway: str | None
    ref: str | None
    name: str | None
    maxspeed_kmh: float | None
    lanes: int | None
    surface: str | None

    bridge: bool
    tunnel: bool
    oneway: bool

    municipality_codes: tuple[str, ...]
```

Geometry should be stored in the vector edge table, but does not need to be duplicated in every in-memory edge object.

### 6.5 Hydro features

At minimum distinguish:

```text
Waterway
  river
  stream_major
  canal

WaterBody
  lake
  reservoir
  pond
  lagoon
  basin_polygon / generic water polygon when source taxonomy is uncertain

Wetland
Coastline
HydroBasin (optional initial source-dependent layer)
```

Hydrology source adapters must normalize provider-specific tags into this common taxonomy.

---

## 7. Road network requirements

### 7.1 Data source

Initial source:

- local, dated Geofabrik Nordeste `.osm.pbf`;
- no Overpass dependency at runtime;
- extract only the geographic extent needed for Pernambuco plus configurable border padding.

### 7.2 Road classes

Default network should focus on intermunicipal/logistics movement.

Include by default:

```text
motorway
trunk
primary
secondary
tertiary
unclassified
```

Consider including `residential` and `service` only:

- near facilities;
- when necessary to connect facilities to the main network;
- via an explicit configuration option.

The builder must avoid producing an unusably dense statewide urban street graph by default.

### 7.3 Build-time representation

Use pyosmium for PBF extraction.

OSMnx may be used at build time to:

- construct a directed graph;
- simplify topology;
- infer speeds when missing;
- derive baseline travel times.

Do not make OSMnx/NetworkX a runtime dependency of every simulation model.

### 7.4 Persistent road outputs

Produce both:

```text
roads_nodes.parquet
roads_edges.parquet
roads_routing.npz
```

Optionally also produce a GeoPackage for inspection/export:

```text
roads.gpkg
```

`roads_edges.parquet` is the canonical rich road dataset.

`roads_routing.npz` is a derived optimized representation.

### 7.5 Runtime routing representation

Use SciPy CSR/CSC sparse matrices or an equivalent compact structure.

Runtime router must support:

```python
route(origin, destination)
travel_time(origin, destination)
distance(origin, destination)
```

and must accept a dynamic overlay:

```python
route(
    origin,
    destination,
    state=RoadRuntimeState(...),
)
```

where `RoadRuntimeState` can provide:

```python
closed_edges: set[int]
cost_multiplier: Mapping[int, float]
```

No mutation of the canonical world graph is required.

### 7.6 Facility snapping

Every facility should be snapped once during preprocessing/import to the nearest valid road node or edge.

Persist the snap result.

Do not run nearest-road GeoPandas operations every simulation day.

### 7.7 Routing acceptance tests

At least test:

- Recife -> Caruaru;
- Caruaru -> Petrolina;
- two nearby facilities in the same municipality;
- disconnected island/mainland behavior;
- a route with one closed edge that forces rerouting;
- a case where no path exists.

Tests should validate invariants, not hardcode fragile exact route node lists.

---

## 8. Hydrology requirements

### 8.1 Initial role

Hydrology is part of the static world and must support spatial/environmental queries.

It is **not** an agent population.

### 8.2 Initial source strategy

Allow OSM as the initial source for:

- major rivers;
- canals;
- large mapped water bodies;
- wetlands;
- coastline.

However, the architecture must explicitly allow replacement/enrichment with a more authoritative hydrographic dataset later.

Create an interface similar to:

```python
class HydroSource(Protocol):
    def load_waterways(...): ...
    def load_waterbodies(...): ...
    def load_wetlands(...): ...
    def load_coastline(...): ...
    def load_basins(...): ...
```

### 8.3 OSM stream caveat

Do not assume OSM stream density represents physical stream density.

The legacy dataset already showed strong mapping-effort bias for small streams.

Therefore:

- keep `river` and `canal`;
- include only configurable subsets of `stream`;
- mark source/provenance for every feature;
- do not make model rules depend heavily on minor OSM streams by default.

### 8.4 Hydro query API

Required:

```python
world.hydro.distance_to_water(point)
world.hydro.nearest_waterbody(point)
world.hydro.nearest_waterway(point)
world.hydro.intersections_with_road(edge_id)
world.hydro.basin_at(point)  # may return None until basin source exists
```

### 8.5 Derived road-hydro crossings

Precompute road/water intersections where practical.

Produce a table like:

```text
road_hydro_crossings.parquet
```

Suggested fields:

```text
road_edge_id
hydro_feature_id
hydro_feature_kind
bridge_tag
intersection_lon
intersection_lat
```

This dataset will support later infrastructure-impact/disruption models without repeated expensive spatial joins.

---

## 9. Administrative geography

### 9.1 Sources

Use IBGE for:

- Pernambuco state boundary;
- municipal boundaries;
- municipality codes and names.

### 9.2 Fernando de Noronha

Do not delete it from the administrative layer.

It may remain disconnected from the continental road graph.

Queries must handle this normally:

- `municipality_at()` works;
- mainland-to-island road routing can return `NoPath`;
- future air/sea transport can be added separately.

### 9.3 Municipality as interoperability key

Assign municipality code wherever practical:

- facilities;
- road edges;
- environmental cells;
- hydro features when cheap to compute.

Municipality code is expected to be the main join key for future external datasets.

---

## 10. Environmental raster

### 10.1 Purpose

The raster is a compact environmental lookup surface, not the primary movement space.

Do not model each raster cell as a Mesa agent.

### 10.2 Resolution

Start with a configurable resolution around 1 km.

Do not increase resolution unless a model rule requires it.

### 10.3 Initial bands

Keep the first version minimal.

Required or useful initial bands:

```text
state_mask
municipality_code
water_distance_m
wetland_fraction
urban_fraction (optional if already cheap)
rural_fraction (optional if already cheap)
```

Do not add terrain/elevation yet unless used by a concrete rule.

### 10.4 API

```python
world.environment.at(lon, lat)
```

Return a compact object or dict, e.g.:

```python
{
    "municipality_code": "2611606",
    "water_distance_m": 812.0,
    "wetland_fraction": 0.02,
}
```

No GeoTIFF reads should occur per agent per tick. Load/mmap required arrays once.

---

## 11. Spatial index requirements

Use Shapely `STRtree`, GeoPandas `sindex`, SciPy KD-tree, or equivalent as appropriate.

Required fast queries:

```python
world.municipality_at(point)
world.nearest_road(point)
world.nearby_facilities(point, radius_m)
world.facilities_within(geometry)
world.hydro.distance_to_water(point)
world.hydro.nearest_waterway(point)
world.roads_within(geometry)
```

Where possible, convert query inputs to primitive `(lon, lat)` or `(x_m, y_m)` tuples rather than requiring callers to construct GeoPandas objects.

---

## 12. Public world API

Target public facade:

```python
world = PernambucoWorld.load(path)
```

Expected fields:

```python
world.administrative
world.roads
world.hydro
world.environment
world.facilities
world.spatial
```

Expected core queries:

```python
world.municipality_at((lon, lat))
world.nearest_road((lon, lat))
world.nearby_facilities((lon, lat), radius_m=10_000)
world.environment_at((lon, lat))

world.route(facility_a, facility_b)
world.travel_time(facility_a, facility_b)

world.hydro.distance_to_water((lon, lat))
world.hydro.nearest_waterway((lon, lat))
world.hydro.intersections_with_road(edge_id)
```

Prefer convenience overloads that accept either:

- `(lon, lat)`;
- facility ID;
- `Facility`.

Do not expose build-tool internals through the public API.

---

## 13. Graphical world viewer

### 13.1 Purpose

SimGeoPE must provide a lightweight browser-based viewer for **inspection, validation and debugging** of the built world. It is not the simulation engine and must not contain model rules.

The viewer should make spatial errors obvious early: bad clipping, broken road topology, incorrect facility snapping, missing hydrology, impossible routes, wrong administrative assignment, and similar issues.

### 13.2 Technology

Prefer the proven lightweight stack:

- FastAPI for local read/query endpoints;
- MapLibre GL JS for the map;
- plain HTML/CSS/JavaScript is acceptable for the first version;
- no Node/bundler is required unless the repository already uses one.

Default local command:

```bash
simgeope serve --port 8765
```

Bind to `127.0.0.1` by default. Do not add authentication or deployment infrastructure in this milestone.

### 13.3 Initial layers

The viewer must be able to toggle, as they become available:

```text
municipal boundaries
road network
hydrology / water bodies
facilities
environmental raster
road-hydro crossings
```

Do not require every statewide road edge to be sent to the browser in one giant GeoJSON response if it is impractical. Use bounding-box filtering, simplification, vector tiling, or another straightforward approach if needed. Keep the implementation proportional to the dataset.

### 13.4 Inspection interactions

Minimum useful interactions:

- click a municipality and inspect code/name;
- click a road edge and inspect canonical attributes;
- click a hydro feature and inspect type/provenance;
- click a facility and inspect municipality + road snap;
- click an arbitrary map point and query the world at that coordinate;
- choose A/B points or facilities and draw the computed road route.

A road edge inspection should expose useful attributes when present, including:

```text
edge id
OSM/source id
road class
ref/name
length
base travel time
maxspeed
lanes
surface
bridge/tunnel/oneway
municipality
```

### 13.5 Debug route overlay

The viewer should support a debug-only road state overlay that can temporarily close an edge or apply a cost multiplier and recalculate the A/B route. This is a developer inspection tool for `RoadRuntimeState`; it must not mutate canonical world files.

### 13.6 Minimal HTTP API

Target endpoints may look like:

```text
GET /api/world/metadata
GET /api/world/municipalities
GET /api/world/roads?bbox=...
GET /api/world/hydro?bbox=...
GET /api/world/facilities?bbox=...
GET /api/world/environment?...
GET /api/query/point?lon=...&lat=...
GET /api/route?from_lon=...&from_lat=...&to_lon=...&to_lat=...
```

Endpoint naming may change to fit repository conventions, but the browser must consume the same `PernambucoWorld` facade used by Python callers rather than reimplementing GIS logic in HTTP handlers.

### 13.7 Future simulation viewer

A time-aware Mesa simulation viewer is explicitly **out of scope for this milestone**. Design the static world viewer so a future endpoint such as `GET /api/simulation/{run}/state/{tick}` can be added without contaminating the world model.

---

## 14. Mesa integration

Create a thin adapter only.

```python
class WorldModel(mesa.Model):
    def __init__(self, world: PernambucoWorld, *, seed=None):
        super().__init__(seed=seed)
        self.world = world
```

Do not require `MultiGrid` as the main representation.

### 14.1 Movement

Road-constrained moving agents should track network state, e.g.:

```python
@dataclass
class NetworkPosition:
    edge_id: int
    progress: float  # 0..1
```

or a simpler node/route-index representation for the first version.

### 14.2 Static facilities

Facilities do not need to subclass `Agent` unless they have dynamic behavior.

A static farm or hub may simply be a world entity referenced by an agent/state object.

### 14.3 Environmental access

Agents should query environmental data through the facade:

```python
cell = model.world.environment_at(agent.location)
```

Never read raster files directly from agent code.

---

## 15. Build pipeline

Provide a single reproducible build entry point.

Example CLI:

```bash
simgeope world build
```

or:

```bash
python -m simgeope.world.build
```

Pipeline:

```text
IBGE --------------------┐
                         │
OSM PBF -----------------┼--> normalize --> clip --> validate
                         │
Hydro source ------------┘
                                  │
                 ┌────────────────┼─────────────────┐
                 │                │                 │
             admin            roads             hydro
                 │                │                 │
                 └────────────── derived joins ─────┘
                                  │
                          environmental raster
                                  │
                            runtime indexes
                                  │
                               manifest
```

### 15.1 Cache layout

Suggested:

```text
data/
  raw/
    ibge/
    osm/
    hydro/

  world/
    pe_v1/
      manifest.json
      config.json

      municipalities.parquet
      state_boundary.parquet

      roads_nodes.parquet
      roads_edges.parquet
      roads_routing.npz

      waterways.parquet
      waterbodies.parquet
      wetlands.parquet
      coastline.parquet
      basins.parquet              # optional
      road_hydro_crossings.parquet

      facilities.parquet

      environment.tif             # or a compact alternative
      environment_arrays.npz      # optional runtime format
```

Build outputs should be reproducible from raw data + config.

---

## 16. Manifest and provenance

Every world build must produce `manifest.json` containing at least:

```text
world format version
build timestamp
source file names
source URLs where applicable
source dates / dataset versions
source checksums if practical
configuration hash
projected CRS
raster resolution
feature counts
road node/edge counts
hydro feature counts
warnings / dropped invalid geometry counts
```

The world loader should refuse obviously incompatible format versions.

---

## 17. Performance requirements

Design target, not premature micro-optimization:

1. Heavy GIS work occurs only during build/import.
2. Loading an already-built world should take seconds, not minutes.
3. `municipality_at`, nearest-road and nearby-facility queries should be index-backed.
4. Routing must not reconstruct NetworkX graphs per call.
5. Mesa replicas should be able to share read-only world data where multiprocessing semantics allow it.
6. Runtime API should not import or instantiate OSMnx.
7. No per-tick file I/O for geographic data.

Use memory mapping where helpful, but keep the first implementation maintainable.

---

## 18. Error handling

Create explicit exceptions such as:

```python
class WorldBuildError(Exception): ...


class WorldDataMissingError(Exception): ...


class PointOutsideWorldError(Exception): ...


class NoRoadNearbyError(Exception): ...


class RouteNotFoundError(Exception): ...
```

Do not silently invent routes or municipalities when data is missing.

Facility snapping should record excessive snap distances as warnings or validation failures depending on config.

---

## 19. Tests

### 19.1 Unit tests

Cover:

- config parsing;
- CRS conversion;
- municipality point lookup;
- facility snapping;
- raster lookup;
- hydro normalization;
- road runtime overlay;
- routing on a tiny synthetic graph;
- invalid/missing data behavior.

### 19.2 Data integration tests

Mark real-data tests separately, e.g. `@pytest.mark.data`.

They should verify invariants such as:

- Pernambuco polygon loads;
- expected municipalities exist;
- Recife resolves to a Pernambuco municipality;
- Caruaru resolves to a Pernambuco municipality;
- road graph has a plausible nonzero node/edge count;
- Recife and Caruaru are road-connected;
- mainland and Fernando de Noronha are not assumed road-connected;
- major hydro features load;
- no invalid geometries remain in canonical outputs;
- every routed facility has a valid road snap.

Avoid tests that depend on exact feature counts from an evolving external dataset unless the raw source is pinned.

### 19.3 Acceptance smoke test

The following should work after a clean build:

```python
world = PernambucoWorld.load("data/world/pe_v1")

recife = world.municipality_at((-34.88, -8.05))
assert recife is not None

road = world.nearest_road((-34.88, -8.05))
assert road is not None

water_d = world.hydro.distance_to_water((-34.88, -8.05))
assert water_d >= 0

route = world.route("facility-a", "facility-b")
assert route.travel_time_s > 0

nearby = world.nearby_facilities((-35.0, -8.2), radius_m=10_000)
assert isinstance(nearby, list)
```

Also run the same route under a `RoadRuntimeState` with one route edge closed and verify either:

- a different route is returned; or
- `RouteNotFoundError` is raised if the graph is disconnected.

---

## 20. Implementation order

Codex should implement in this order and keep each stage independently testable.

### Phase 0 — Audit

- Inspect the existing repo and legacy SimBio-PE geography code.
- Identify reusable functions rather than copying whole modules.
- Record decisions in `docs/WORLD_DECISIONS.md`.
- Do not modify unrelated simulation behavior.

### Phase 1 — World core

- `WorldConfig`
- manifest/versioning
- package skeleton
- `PernambucoWorld` facade
- exception types

### Phase 2 — Administrative layer

- IBGE source/cache
- state polygon
- municipalities
- projected working geometries/index
- `municipality_at`

### Phase 3 — Roads

- PBF extraction
- road class filtering
- OSMnx build-time graph
- canonical node/edge tables
- compact sparse routing graph
- nearest-road index
- facility snapping
- dynamic runtime overlay

### Phase 4 — Hydrology

- `HydroSource` interface
- initial OSM adapter
- canonical normalized tables
- spatial index
- water-distance and nearest-water queries
- road/hydro crossing derivation

### Phase 5 — Environmental raster

- minimal grid
- state mask
- municipality labels
- water distance
- selected coverage bands
- runtime array store

### Phase 6 — Facilities

- generic facility schema
- CSV/XLSX/Parquet import if existing project conventions support it
- municipality assignment
- road snapping
- spatial index

### Phase 7 — World viewer

- FastAPI local inspection API
- MapLibre world viewer
- layer toggles
- click-to-inspect for municipalities/roads/hydro/facilities
- arbitrary point query
- A/B route visualization
- debug road-edge closure/cost overlay
- viewer remains a pure consumer of `PernambucoWorld`

### Phase 8 — Mesa adapter

- thin adapter
- tiny example agent using route/environment queries
- no scenario-specific logic

### Phase 9 — Quality pass

- real-data integration tests
- benchmark basic queries
- manifest/provenance audit
- README usage example
- remove dead build artifacts

---

## 21. Definition of Done

The milestone is done when:

1. A clean `world build` produces a versioned Pernambuco world dataset.
2. The build is deterministic for the same pinned inputs/config.
3. `PernambucoWorld.load(...)` does not need OSMnx/pyosmium to answer runtime queries.
4. Municipality lookup works.
5. Facilities can be imported and snapped to roads.
6. Road routing works over the real network.
7. Runtime road closures/cost multipliers can affect routing without rebuilding the world.
8. Hydrology supports nearest/distance queries.
9. Road/hydro crossings are precomputed.
10. A minimal raster can be sampled by coordinate.
11. `simgeope serve` opens a local graphical viewer that can inspect the built world and draw an A/B route.
12. The viewer can exercise a temporary road closure/cost overlay without mutating canonical world data.
13. Mesa can hold a reference to the world and agents can call the query API.
14. No simulation-specific hazard or disease logic is embedded in the world package.
15. Tests document and enforce the above behavior.

---

## 22. Guardrails for Codex

- Prefer clear, maintainable Python over clever abstractions.
- Avoid introducing services/databases unless clearly necessary.
- Avoid runtime HTTP dependencies.
- Prefer local, pinned source files and explicit rebuild commands.
- Keep the required viewer narrow: inspection/debug only. Do not broaden it into a full simulation dashboard, disease modeling UI, scenario editor, or generic GIS tooling.
- Do not make every geographic feature a Mesa agent.
- Do not model movement on a 2D cellular grid when a road graph already represents the relevant topology.
- Keep geospatial preprocessing out of the simulation hot loop.
- Preserve provenance for every source-derived layer.
- If a dataset is incomplete or unreliable, expose that limitation rather than silently fabricating values.
- If a design choice conflicts with an existing repository constraint, document the conflict and choose the smallest compatible change.

---

## 23. Codex / Work execution policy

Subagents are a **default requirement** for substantive work on SimGeoPE. The goal is useful parallelism without wasting the user's usage budget.

### 23.1 Mandatory decomposition

When there are at least two independent workstreams, the coordinator must use subagents rather than doing all substantive work serially in the main agent.

Prefer concurrent execution for independent tasks. Use dependency-aware waves when workstreams depend on earlier outputs.

### 23.2 Concurrency budget

Default to **2–4 useful subagents in parallel**. Do not create a large fan-out merely because parallel execution is available.

Good decomposition examples:

```text
A. audit/reuse of legacy geography code
B. world API + administrative/CRS design
C. road extraction/routing/persistence
D. focused test/review pass (only if warranted)
```

### 23.3 Usage discipline

- Do not create subagents for trivial/local edits.
- Do not have multiple agents independently inspect the entire repository unless an independent review is explicitly valuable.
- Do not repeat broad file scans already completed by another agent; reuse its findings.
- Avoid recursive subagent spawning unless a clearly separate blocker requires it.
- Stop a research/investigation branch once there is enough evidence for a decision.
- Group tightly related work when that reduces duplicate context loading.
- Prefer targeted reads/searches over repeatedly traversing the whole repository.
- The coordinator integrates and resolves conflicts; do not make every subagent redo integration work.

### 23.4 Expected subagent result

Each subagent should report compactly:

```text
findings
decisions
files inspected/changed
tests run
risks/blockers
recommended next action
```

### 23.5 Coordinator responsibilities

The main agent must:

1. read the spec and identify independent workstreams;
2. dispatch only the necessary subagents;
3. run them concurrently where safe;
4. reuse their findings rather than repeat them;
5. integrate changes coherently;
6. run or coordinate the final validation pass;
7. launch another wave only when substantive work remains.

Principle:

> **Maximize useful parallelism, not agent count.**

---

## 24. Recommended first Codex task

Start with:

> Use subagents according to Section 23. For this task, begin with approximately three independent workstreams in parallel: (A) legacy geography audit/reuse map, (B) World core + administrative/CRS API, and (C) road extraction/routing/persistence. The coordinator owns integration and final tests. Do not duplicate repository-wide inspection across agents.
>
> Audit the existing geography code and implement **Phases 0–3 only**. Do not start hydrology/raster/Mesa integration until the administrative and road layers have stable public APIs and tests. Produce `docs/WORLD_DECISIONS.md`, the package skeleton, municipality lookup, the canonical rich road tables, the compact runtime router, facility snapping primitives, and integration tests using the pinned local OSM/IBGE data. Preserve road edge metadata; do not collapse the canonical network into only a travel-time matrix.

This first task should end with a runnable example equivalent to:

```python
world = PernambucoWorld.load("data/world/pe_v1")
print(world.municipality_at((-34.88, -8.05)))
print(world.nearest_road((-34.88, -8.05)))
print(world.travel_time((-34.88, -8.05), (-35.97, -8.28)))
```

Hydrology should be the next isolated task after the road API is stable. The graphical viewer is required for the milestone, but it should be implemented only after the core road/hydro/facility query APIs are stable enough to inspect meaningfully.
