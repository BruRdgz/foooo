# SimGeoPE implementation decisions

Scope: SPEC_SIMGEOPE.md is authoritative. Phases 0–5 are validated; this continuation implements Phases 6–9.

## 2026-10-05 — Initial architecture

- Create an independent `src/simgeope` Python package at the workspace root. Preserve the legacy `pibex_vfinal` simulation and all existing user files. The root `.git` is an empty protected placeholder, not an operational Git repository; the legacy repository has untracked source files at baseline.
- Use three concurrent, bounded workstreams: legacy audit; core/admin/CRS; roads/persistence/routing. The coordinator owns integration, build entrypoint and final verification. No recursive fan-out.
- Portable canonical vector geometries use EPSG:4326. Metric operations use the configurable local transverse Mercator specified in Section 5; EPSG:5880 is not used.
- Runtime dependencies are NumPy, SciPy, Shapely, PyProj and Arrow. GeoPandas, pyosmium and OSMnx are optional build dependencies. No Mesa, HTTP server, hydrology, environmental raster or viewer implementation in this run.
- Use the existing pinned local OSM PBF `pibex_vfinal/data/raw/nordeste-260930.osm.pbf` and cached IBGE state/municipality GeoJSON as build inputs. No automatic downloads or source refresh. Record checksums and expose unknown source metadata rather than inventing it.
- Noronha remains in the administrative dataset. Disconnected road components remain valid; routing failures are explicit.
- Canonical road nodes and rich directed edges are persisted separately from the derived compact router. Static geography is never modified by a dynamic road-state overlay.

Additional decisions and validation evidence are appended during implementation.

## 2026-10-06 — Integrated Phases 0–3

- Extract roads with pyosmium from a dated local PBF. Select intersections with the buffered state geometry, including disconnected island polygons; preserve whole selected OSM segments rather than inventing clip-boundary nodes. Include the six default logistics classes and their `_link` variants. Residential/service require explicit configuration.
- Simplify within individual OSM ways, splitting at shared/repeated nodes and way endpoints. OSMnx is optional in Section 7.3; direct deterministic simplification preserves source-way attributes without ambiguous cross-way merges or largest-component pruning. Build dependencies therefore contain pyosmium only. No NetworkX graph exists in runtime.
- Keep numeric OSM speed/directional speed when parseable, including mph conversion. Otherwise infer km/h defaults: motorway100, trunk80, primary70, secondary60, tertiary50, unclassified40, residential30, service20. Directed oneway, reverse oneway, motorway and roundabout behavior are represented; reversible/alternating ways are excluded with warnings. Turn restrictions and conditional access/speed remain explicit limitations.
- Retain parallel directed edges in canonical GeoParquet. The compact baseline CSR selects the cheapest edge for each `(u,v)`; overlays reselect surviving parallel edges before routing. Unknown edge IDs and invalid effective costs fail explicitly. `Route.distance_m` is length along the time-optimal route. Same-node routes cost zero. A physical two-way closure requires both directed edge IDs.
- Validate the NPZ derivative against canonical topology/costs at load. Baseline reconstruction/verification happens once per world load; no per-query NetworkX or file access. Cost overlays rebuild a temporary CSR from canonical edges in O(E); optimization of repeated large overlays is deferred pending measurement.
- Administrative source codes/names are normalized and checked against all 185 locally pinned divisions, including Noronha. SIRGAS2000 input is explicitly transformed to WGS84. Index point/topology queries against canonical WGS84 boundaries to preserve boundary semantics; keep projected geometries/index for metric work. Invalid polygons are repaired with warnings or rejected, never silently removed.
- Facility snapping is a Phase 3 primitive, not the Phase 6 bulk importer. Snap to the globally nearest road node, record true node distance, incident edge and administrative assignment. A provided administrative lookup overrides stale facility municipality codes, including clearing them outside the layer. Nearest-road inspection separately returns the projected point on the closest edge. First/last-mile costs and within-edge routing are deferred.
- Core exceptions and public exports load lazily so CLI help, source-date validation and `RoadRuntimeState` are usable without GIS tools. Runtime world queries need only the declared runtime dependencies.
- Builds stage privately, fingerprint outputs, then publish. Cache reuse requires matching raw source hashes, complete required outputs and config hash. A mismatch requires a new destination; no `--force` overwrite option. Paths, including output_dir, participate in the config hash.
- Manifest timestamps record actual UTC build time, or honor `SOURCE_DATE_EPOCH` for reproducibility. Record known IBGE/OSM URLs, filename-derived OSM date and raw SHA256; leave unknown IBGE vintage null and warn. Invalid/unresolved geometry drops have explicit counts. Reproducibility assumes the same software environment; dependency versions are not yet locked because resolution was unavailable.
- Phase 4 interfaces remain pending; `hydro` and `environment` are absent (`None`), environmental facade queries fail explicitly. No hydrology/raster/Mesa/viewer code was added.

## Validation gate

- Passed: 18 focused stdlib-only pytest cases (CLI, pinned dates, immutable dynamic state); isolated Ruff E4/E7/E9/F/I; Python compileall; CLI help. Raw IBGE inspection verified 1 state, 185 geometries, 185 matching names and Noronha.
- Blocked: the full suite stopped with 10 GIS test-module collection errors (missing numerical/geospatial packages). The real build exited with a missing `pyproj` dependency before creating outputs. pip and uv both failed PyPI DNS resolution; local caches did not contain the required GIS packages. The available VELA environment supplied pytest for the 18 runnable cases, not GIS.
- No real world artifacts, statewide route results or performance measurements were produced. Phase 4 acceptance remains gated on installing declared dependencies, passing the full suite, building from pinned inputs and passing the real-data smoke tests. This is a validation blocker, not permission to implement future layers.

## 2026-10-06 — Dependency blocker resolved

- With network access enabled, `uv sync --extra build --extra dev` successfully installed the declared runtime/build/test dependencies into the workspace virtualenv. No DNS or system configuration was changed. `uv.lock` now pins the resolved dependency versions.
- Core/admin tests: 47 passed; roads/extraction/routing/snapping tests: 53 passed; coordinator integration+real-IBGE tests: 6 passed. Combined non-data suite: 123 passed,3 deselected in 1.88 s. The earlier collection failures are superseded.
- Real IBGE load verified all 185 valid divisions and a valid state geometry; Recife, Caruaru, Petrolina and Noronha lookups passed. One invalid input geometry was repaired with its warning preserved.
- Clean real PBF build is running. Final route acceptance and the Phase 4 gate will be updated after publication and smoke tests.

## 2026-10-06 — Real-data acceptance completed

- Published `data/world/pe_v1` from the pinned local inputs: 185 administrative divisions, 190699 road nodes, 446562 directed edges. Fingerprinted canonical/derived outputs total 107025239 bytes. Clean build took 8m06.16s; peak RSS 3057560 KiB (2.92GiB). Dataset outputs remain outside Git.
- Marked real-data suite: 3 passed, 123 deselected in 58.26 s. Recife–Caruaru and Caruaru–Petrolina routing, edge-closure invariants, valid canonical geometry and island/mainland separation passed. Together with 123 non-data tests, 126 cases passed. No code/test fixes or weakened assertions were needed after installing dependencies.
- Cache build reused the published world without preprocessing in 1.99 s. The public example ran successfully:Recife 2611606; nearest road 113.50 m; Recife–Caruaru 133480.51 m and 4932.22 s (352 directed edges).
- Fresh-process example including world load/queries took 55.08 s and peaked at 2287428 KiB (2.18GiB). Runtime startup/index construction is expensive on this real graph. Keep world loading outside agent ticks and reuse the loaded world; improving cold-load cost remains a performance target, not an added Phase 4 implementation in this run.
- The previous dependency/real-data acceptance blockers are resolved. Phase 4 has tested admin/road APIs and a built world to consume. Full raw-source rebuild byte determinism was not exercised a second time; synthetic reproducibility/integrity and real cache reuse passed.

## 2026-10-06 — Phase 4 implementation

- Hydrology is an optional static layer enabled by `hydro_enabled`; old Phase 3 worlds still load with `hydro=None`. Additive configuration defaults are omitted from canonical JSON to preserve existing hashes. Phase 4 uses a new output directory, never overwrites the accepted Phase 3 world.
- Three concurrent bounded workers own OSM hydrology normalization, runtime/persistence, and road crossings. The coordinator owns configuration, facade/build integration, acceptance and documentation. No repeated repository-wide audit or recursive fan-out.
- OSM is the initial interchangeable HydroSource. Rivers/canals are included; streams are excluded by default. Optional named-stream inclusion is a documented heuristic, not authoritative hydrology. Waterbody polygons below 10000 square metres are excluded by default; the threshold is configurable, including zero. Wetlands/coastline remain separate. No basin boundaries are invented.
- Water distance measures the union of waterways, waterbodies and wetlands in the metric CRS. Coastline is stored and indexed separately and does not stand in for an ocean water polygon. Missing water or basins returns None, rather than a fabricated distance or basin.
- `--base-world` explicitly reuses fingerprint-verified canonical administration/road outputs only when raw source hashes and every non-hydrology/non-output configuration field match. It copies into private staging and publishes a new manifest/config; no original file is mutated and no PBF road extraction is repeated.

- GeoParquet preserves canonical string IDs (`osm:way:<id>`/`osm:relation:<id>`), taxonomy, names, source/date/file, municipality codes, and sorted raw-tag JSON. Typed empty tables preserve all five layer schemas and the crossing schema. Runtime indexes are constructed once; queries perform no source scans or disk I/O.
- Polygon areas assemble OSM multipolygon relations with holes; retained parent polygons remove redundant tagged member polygons. Legacy riverbank/surface-water tags retain uncertain `water` classification. Polygon repair/drop warnings are bounded and source-wide before state filtering; missing-node geometries cannot be attributed solely to Pernambuco.
- Crossings scan only road IDs, bridge flags and WKB in bounded Arrow batches, without loading the full runtime router. Points are retained; each connected line overlap uses a normalized metric midpoint (including road portions inside polygons). Coastline/basins are excluded. Preserve bridge flags exactly; geometric intersection alone does not assert a bridge or flooding risk.
- Reuse/cache publication validates counts against Parquet metadata as well as output SHA256. A targeted independent review found that modified manifest counts could otherwise be propagated; regression tests now reject that case before publication.

## 2026-10-06 — Phase 4 real-data acceptance

- Published `data/world/pe_hydro_v1`: 3266 waterways, 3295 waterbodies, 788 wetlands, 237 coastline features, 0 basins and 5907 directed road/hydro crossing records. Existing admin/road output hashes are byte-identical to `pe_v1`; the original world remains valid. Total fingerprinted outputs: 117264331 bytes.
- Incremental pinned-PBF hydro build with verified Phase 3 reuse: 3m26.87s, peak RSS 1524408 KiB (1.45 GiB). Cache reuse: 1.91s. Manifest preserves all caveats and records four source-wide hydro repairs and 74 invalid/unassembled hydro drops.
- Final full suite against the new real world: 264 passed, zero skips, in 64.51s; isolated Ruff, changed-file formatting and compileall passed. Runtime-only subprocess queries and generated real PBF/XML fixtures passed. Real Capibaribe, Noronha coastline, source provenance, crossing/bridge geometry and previous road acceptance passed.
- Phase 4 is complete. Phase 5 can consume the validated water-distance and wetland/admin geometry APIs. Environmental grid/arrays, viewer, bulk import and Mesa remain deferred. No authoritative hydrographic basin dataset is available; this is an explicit optional-source limitation, not a blocker to the minimal Phase 5 bands.

- Updated public example passed: Recife–Caruaru remains 133480.51m / 4932.22s with 352 directed edges; 18 geometric hydro crossing records lie along the route. Fresh-process example took 57.51s, peak RSS 2275236 KiB (2.17 GiB), including cold world load.

## 2026-10-06 — Phase 5 implementation

- Use two bounded concurrent workers for raster grid/runtime persistence and vector-to-band preprocessing; the coordinator integrates config/build/facade, tests and docs. No broad legacy/repository rescan, recursive fan-out or future-phase implementation.
- Environmental output is opt-in, requires hydrology, and uses the configured metre CRS/resolution (default 1km). Omit the new disabled default from canonical configuration to preserve Phase 3/4 hashes.
- A single north-up, globally resolution-aligned grid covers the whole state, including Noronha. About 622000 cells at 1km fit roughly 8MB of uncompressed bands; a 2000000-cell hard limit rejects excessive allocation. No Rasterio/GeoTIFF dependency or speculative tile layer is needed for this compact lookup surface.
- State mask and municipality labels describe cell centres using canonical WGS84 polygons; shared-boundary labels follow the existing lowest-code tie rule. Outside-state centres use explicit no-data. The sampler raises PointOutsideWorldError outside the raster's valid centre-sampled domain; exact municipality queries remain available independently.
- Water distance is computed at the cell centre against metric waterways/waterbodies/wetlands, excluding coastline/basins consistently with hydrology. Empty mapped water returns None. Wetland fraction measures the union of mapped wetland polygons clipped to the state, divided by full cell area; overlaps are not counted twice and holes are preserved.
- Persist two files: environment_grid.json and compressed environment_arrays.npz. Load NumPy bands once as read-only arrays; queries perform no raster disk I/O. Urban/rural fractions and elevation are deferred because no concrete rule or cheap validated source requires them.
- Extend --base-world to accept a verified Phase 4 source when building Phase 5. Match all reused-layer settings and source hashes; only output/environment settings and grid resolution can differ. Copy existing admin/roads/hydro files into new private staging and preserve all original files and caveats.

- The first pinned-data raster build found 71 of 98175 state-covered centres outside every canonical municipal polygon, including two Noronha centres. The canonical state-minus-municipal-union area is 64.974km², proving source-boundary mismatch rather than a raster query defect. Preserve the state mask and explicit unknown municipality labels (zero storage sentinel, public None), count/warn in the manifest; never assign a nearest municipality or rewrite IBGE geometry. This supersedes the initial completeness assumption for inside-state labels.

## 2026-10-06 — Phase 5 real-data acceptance

- Published `data/world/pe_environment_v1`: 626×993 grid (621618 cells), 98175 state-centre cells, 71 explicitly unknown municipal labels. Environmental NPZ/JSON occupy 391441 bytes; arrays use 8081034 bytes uncompressed. All inherited admin/road/hydro hashes match the original Phase 4 world.
- Incremental real build completed in 12.99s, peak RSS 371468 KiB (0.354GiB). Verified cache reuse: 1.27s. Standalone store load: 15.1ms; 10000 loaded-array queries: 40.6ms (~4.06µs/query), measured locally without claims about complete-world cold start or multiprocessing.
- Final full suite against the new world: 381 passed, zero skips, 67.73s; isolated Ruff, 15 changed-file formatting checks and compileall passed. Real raster smoke verifies Recife/Caruaru/Petrolina/Noronha, centre distance invariants, readonly bands and explicit IBGE source-gap labels; prior road/hydro real-data acceptance remains green.
- Phase 5 is complete. Phase count is now 6/10 (60%); this is not an effort-weighted percentage. Bulk facility import, viewer, Mesa and final milestone quality remain pending. No terrain, hazard rules or simulation logic was introduced.

- Updated public example passed in 58.76s, peak RSS 2295792 KiB (2.19GiB), including cold world load and raster sampling. Recife–Caruaru/nearest-road/hydrology results remain unchanged; the environmental Recife cell reports 731.72m centre-sampled water distance versus 600.64m at the exact input point.

## 2026-10-06 — Phases 6–8 implementation

- Facilities are an optional build input (`facilities_file`) omitted from canonical config when unset. A new build writes `facilities.parquet`; the raw source is fingerprinted in the manifest and participates in cache identity. A verified base world may supply admin/roads/hydro/environment; only facilities are imported and snapped into the new output. Facility rows require valid IDs, kind and WGS84 coordinates. Invalid rows fail atomically; no implicit row drops.
- Facility import supports CSV, Parquet and optional XLSX, including the bounded legacy creator/hub workbook schemas. It retains static identity/name/location only, derives municipality from geometry, and snaps to nearest road node. Scenario/disease fields are discarded with a warning. Names are additive in the canonical schema and older facility tables remain loadable.
- The viewer is an optional FastAPI consumer of an already-loaded `PernambucoWorld`. API responses cap feature/cell/coordinate/body sizes, clip and simplify viewport output, and strip filesystem paths from provenance. It binds only to 127.0.0.1. MapLibre/tiles are browser CDN resources; inspection APIs remain local and read-only. Runtime road overlays are immutable and do not alter canonical files.
- The Mesa adapter is isolated behind the `simulation` extra, holds a reference to the shared world and provides a tiny query example. `networkx` is included in that optional extra because the installed Mesa package imports it; the geographic core still does not use NetworkX.

## 2026-10-06 — Phase 9 acceptance

- Full acceptance and the latest focused build-reuse tests are recorded in `PHASE_6_9_REPORT.md`. The report separates pinned real-world smoke results from synthetic facility/viewer fixtures and includes basic query timings.
- Preserve external browser rendering as an environment-dependent manual check: the viewer's MapLibre library and basemap tiles come from public CDN/tile services. HTTP behavior and static resource packaging are covered locally.
