# Phases 6–9 implementation report

## Delivered

- Phase 6: reproducible CSV/Parquet/XLSX static facility import, including the repository's creator and hub workbook formats. The importer validates rows, derives municipality from location, snaps to the road network, and records source checksums. The generic facility table keeps only identity, type, optional name, coordinates, municipality, and snap data; scenario-specific fields are discarded with a warning. `simgeope world build --facilities-file ...` writes `facilities.parquet`; a verified Phase 5 base can be extended without re-extracting roads/hydrology or re-rasterizing.
- Phase 7: optional loopback-only FastAPI viewer with bounded GeoJSON layer queries, point inspection, route queries, and temporary edge closure/cost overlays. The MapLibre UI has layer toggles, feature inspection, arbitrary point query, A/B route display and overlay controls. Static assets are included in built distributions.
- Phase 8: optional Mesa adapter and a small example agent that shares the loaded world and calls its environmental and routing APIs. No scenario or disease logic was added.
- Phase 9: README usage, implementation decisions, manifest/source audit, package build verification, real-world benchmark and complete regression suite.

## Changed files

- `src/simgeope/world/config.py`, `src/simgeope/world/build.py`, `src/simgeope/world/facilities.py`, `src/simgeope/world/sources/facilities.py`, `src/simgeope/cli.py`, `pyproject.toml`, `uv.lock`
- `src/simgeope/api/__init__.py`, `src/simgeope/api/app.py`, `src/simgeope/web/index.html`, `src/simgeope/web/app.js`, `src/simgeope/web/styles.css`
- `src/simgeope/mesa_adapter.py`, `examples/mesa_world_model.py`
- `tests/test_build.py`, `tests/test_facility_import.py`, `tests/test_viewer.py`, `tests/test_mesa_adapter.py`, `tests/test_environment_integration.py`
- `README.md`, `docs/WORLD_DECISIONS.md`, this report

## Decisions and limits

- Facilities are an optional, checksum-tracked build source and are published into a new immutable world directory. Invalid input rows fail atomically. Legacy Excel biological/dynamic fields do not enter the static geography.
- Spreadsheet, viewer and Mesa dependencies are optional extras. NetworkX is included only in the simulation extra because Mesa currently imports it. Phase 3–5 worlds remain loadable without facility output or optional extras.
- The viewer binds to `127.0.0.1`; its web map loads MapLibre and basemap tiles from public browser services. Its server/API and layer query behavior are tested, but a graphical browser rendering pass was not run in this headless validation.
- No suitable pinned static facility dataset was available for a real facility snapping smoke run. Facility import/snap/build/reuse are verified with synthetic records and both actual legacy workbook schemas. Real-world routing, hydro, environment, provenance and prior Phase 5 acceptance continue to use the locally pinned world.
- Mesa 3.5.1 emits an upstream deprecation warning for its `seed` argument, which the adapter retains to match the specification. Starlette currently emits a deprecation warning for the HTTPX-backed TestClient.

## Validation

- Full suite with `SIMGEOPE_WORLD=data/world/pe_environment_v1` and build/spreadsheet/viewer/simulation extras: **478 passed**, including pinned real-world smoke tests. No test skips. The suite includes the real-data journey tests for administration, roads, hydrology, environment, routing and cache integrity.
- Focused facilities/viewer/Mesa integration and layer tests: **139 passed** before the final base-world extension test; focused Phase 4/5/facility build integration: **29 passed** after the final integration changes.
- Ruff formatting and import sorting passed for changed code; JavaScript `node --check` and Python `compileall` passed. The complete repository Ruff rule set is not configured as a project check and flags pre-existing warnings in untouched code; no broad cleanup was applied.
- `uv build --out-dir /tmp/...` produced an sdist and wheel; inspection confirmed all three `simgeope/web` assets are in the wheel. Temporary packaging output was removed.
- Fresh-process Phase 5 world benchmark on this host: load **57.98 s**, peak RSS **2,317,836 KiB**. Median of three queries: municipality **52 μs**, nearest road **106 μs**, environment **11 μs**, route **26.1 ms**. Recife–Caruaru remained **133,480.5 m / 4,932.2 s**. These are local single-process measurements, not performance guarantees.

Phases 0–9 are implemented and the full milestone is ready for use. Browser CDN access and availability of a real static facility source remain environment/data limitations, not code acceptance failures.
