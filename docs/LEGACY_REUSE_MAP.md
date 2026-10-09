# Legacy geography reuse map (Phase 0)

The reference is `pibex_vfinal/simbio_pe/geo`; no legacy source is modified.
Reuse is conceptual and function-sized, not a wholesale architecture port.

| Legacy location | Useful idea | SimGeoPE treatment |
| --- | --- | --- |
| `sources.py:download`, `osm_pbf` | Dated local input and persistent raw cache | Require explicit local files; retain checksums, filenames and known URLs/dates in manifest. No runtime download. |
| `sources.py:municipalities` | IBGE `codarea` geometry joined to names metadata by municipality code | Normalize to string codes/name/WGS84 geometry; retain all 185 municipal/district features including Noronha. No regional or application-specific fields required. |
| `sources.py:bbox_lonlat` | Expand geographic extent in metres before OSM extraction | Use configurable local projected CRS and state-part padded extent, avoiding an oversized mainland-to-island rectangular world. |
| `roads.py:extract_osm_xml` | pyosmium way filtering from local PBF, retaining referenced nodes | Extract roads locally, preserve OSM source IDs/tags, include configured classes and corresponding link roads, retain complete connected segments within selection extent. |
| `roads.py:build_graph` | Directed topology, baseline speed inference, SciPy CSR runtime | Preserve rich directed edges and geometry first; derive compact routing arrays. Keep every disconnected component and parallel edge. |
| `roads.py:RoadGraph.save/load` | NumPy persistent compact runtime data | Keep derived `roads_routing.npz` beside canonical node/edge Parquet; validate consistency at load. |
| `roads.py:roads_dir`, `build.py:config_hash` | Hash-keyed builds and manifest | Use full canonical config/source/output hashes; avoid filename-only or rounded-bounds cache keys. |
| `store.py` | Load geographic arrays once and expose queries | Indexed, immutable world facade; no per-query file reads. |
| `rasterize.py`, `grid.py`, `osm_extract.py` | GIS extraction/raster helper concepts | Defer to Phase 4/5; not ported in this run. |

## Problems verified in source

- `sources.py:_drop_east` and municipality filters explicitly delete Noronha.
- Legacy projected CRS EPSG:5880 is inappropriate as the primary Pernambuco metric CRS.
- `roads.py:build_graph` calls OSMnx with `retain_all=False`, discarding disconnected components.
- `RoadGraph` retains only projected node coordinates and sparse travel times, losing IDs, lengths, geometries and edge attributes.
- Parallel directed edges are collapsed to minimum time in the legacy cache; dynamic edge-specific closures require canonical edges to remain available.
- Several cached IBGE GeoJSON inputs have no verified dataset vintage. Their current local bytes can be pinned by SHA256; do not invent the source year from filesystem modification time.

## Local input inventory

- `pibex_vfinal/data/raw/nordeste-260930.osm.pbf`: 442,148,553 bytes; filename pins 2026-09-30. SHA256 `2ff3169bc71d55acc70237374d830b03d00ad87fe665eae67995c04aa8e99afc`.
- `ibge_uf26.geojson`: state boundary, including island components.
- `ibge_uf26_municipios.geojson`: 185 features; Noronha code `2605459`.
- `ibge_uf26_municipios.json`: municipality name/code metadata for the geometry join.
- `ibge_br_ufs.geojson`: available but unnecessary for Phases 0–3.

The geographic audit was started by the dedicated worker; the coordinator completed the bounded source review after that worker could not complete its turn.
