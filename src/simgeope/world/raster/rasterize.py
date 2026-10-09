"""Build aligned environmental bands from canonical administrative/hydro layers.

Labels and the state mask describe the canonical WGS84 geometry at each cell
centre. Distances are exact metric centre-to-feature distances (including water
polygon interiors and excluding coastline/basins). A lookup elsewhere in a cell
therefore approximates distance with at most a half-cell diagonal of spatial
displacement. Wetland coverage is mapped union area inside the state divided by
the full metric cell area; it is not a hazard or water-permanence estimate.
"""

from __future__ import annotations

import numpy as np
import shapely
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.errors import GEOSException
from shapely.strtree import STRtree

from ..administrative import AdministrativeLayer
from ..exceptions import WorldBuildError, WorldDataMissingError
from ..hydro.model import HydroLayer
from ..spatial import SpatialReference
from .grid import MAX_ENVIRONMENT_CELLS, RasterGrid
from .store import EnvironmentStore

_BATCH_CELLS = 32_768


def _projected_geometry(spatial, geometry, label):
    projected = spatial.geometry_to_metric(geometry)
    if (
        projected.is_empty
        or not projected.is_valid
        or not np.isfinite(shapely.get_coordinates(projected)).all()
    ):
        raise WorldBuildError(f"{label} cannot be projected into the metric CRS")
    return projected


def _wetland_tree(wetlands, state):
    if not wetlands:
        return STRtree([])
    # Union BEFORE measuring coverage: overlapping mapped polygons must never
    # count the same area twice. GEOS preserves holes in the resulting polygons.
    mapped = shapely.union_all(wetlands)
    clipped = shapely.intersection(mapped, state)
    parts = shapely.get_parts(clipped)
    return STRtree(parts[shapely.area(parts) > 0])


def build_environment(
    administrative,
    hydro,
    *,
    metric_crs,
    resolution_m,
    max_cells=MAX_ENVIRONMENT_CELLS,
) -> EnvironmentStore:
    """Precompute minimal environmental fields; no input loading or road work.

    The grid encloses the entire state, including disconnected islands. Outside
    cells have label zero and NaN floating bands. State centres unmatched by the
    canonical municipal source retain label zero (unknown), because independent
    boundary datasets can have gaps. A valid empty hydro layer has unknown water
    distance (NaN) and zero mapped wetland coverage inside cells.
    """
    if administrative is None or hydro is None:
        raise WorldDataMissingError(
            "Environmental raster requires administrative and hydrology layers"
        )
    if not isinstance(administrative, AdministrativeLayer) or not isinstance(
        hydro, HydroLayer
    ):
        raise WorldBuildError("Environmental raster requires canonical world layers")
    try:
        crs = CRS.from_user_input(metric_crs)
        if not crs.equals(
            CRS.from_user_input(administrative.spatial.metric_crs)
        ) or not crs.equals(CRS.from_user_input(hydro.metric_crs)):
            raise WorldBuildError(
                "Environmental metric CRS must match administrative and hydrology sources"
            )
        spatial = SpatialReference(metric_crs)
        state = _projected_geometry(
            spatial, administrative.state_geometry_wgs84, "State boundary"
        )
        grid = RasterGrid.from_bounds(
            state.bounds, resolution_m, metric_crs, max_cells=max_cells
        )
        return _build_bands(administrative, hydro, spatial, state, grid)
    except (CRSError, ValueError, GEOSException, OverflowError) as exc:
        raise WorldBuildError(f"Cannot build environmental raster: {exc}") from exc


def _build_bands(administrative, hydro, spatial, state, grid):
    state_mask = np.zeros(grid.shape, dtype=np.bool_)
    municipality_code = np.zeros(grid.shape, dtype=np.uint32)
    water_distance_m = np.full(grid.shape, np.nan, dtype=np.float32)
    wetland_fraction = np.full(grid.shape, np.nan, dtype=np.float32)
    mask, labels, distances, fractions = (
        array.ravel()
        for array in (state_mask, municipality_code, water_distance_m, wetland_fraction)
    )
    source_state = administrative.state_geometry_wgs84
    shapely.prepare(source_state)
    municipalities = administrative.municipalities
    municipality_tree = STRtree([m.geometry_wgs84 for m in municipalities])
    codes = np.array([int(m.code) for m in municipalities], dtype=np.uint32)
    water_geometries, wetland_geometries = [], []
    for feature in hydro.features:
        if feature.kind in ("coastline", "basin"):
            continue
        geometry = _projected_geometry(spatial, feature.geometry_wgs84, feature.id)
        water_geometries.append(geometry)
        if feature.kind == "wetland":
            wetland_geometries.append(geometry)
    water_tree = STRtree(water_geometries)
    wetlands = _wetland_tree(wetland_geometries, state)
    half_cell = grid.resolution_m / 2
    cell_area = grid.resolution_m**2
    for start in range(0, grid.cell_count, _BATCH_CELLS):
        end = min(start + _BATCH_CELLS, grid.cell_count)
        flat = np.arange(start, end)
        rows, cols = np.divmod(flat, grid.cols)
        x = grid.origin_x_m + (cols + 0.5) * grid.resolution_m
        y = grid.origin_y_m - (rows + 0.5) * grid.resolution_m
        lon, lat = spatial.unproject((x, y))
        source_points = shapely.points(lon, lat)
        inside = shapely.covers(source_state, source_points)
        mask[start:end] = inside
        indices = flat[inside]
        if not indices.size:
            continue
        pairs = municipality_tree.query(source_points[inside], predicate="intersects")
        selected = np.full(indices.size, np.iinfo(np.uint32).max, dtype=np.uint32)
        np.minimum.at(selected, pairs[0], codes[pairs[1]])
        # Pinned IBGE state/municipal sources have coastal and border slivers
        # between them. Preserve state coverage and report an unknown label;
        # assigning the nearest municipality would invent source geography.
        selected[selected == np.iinfo(np.uint32).max] = 0
        labels[indices] = selected
        x, y = x[inside], y[inside]
        if len(water_tree):
            pairs, nearest_distances = water_tree.query_nearest(
                shapely.points(x, y), return_distance=True, all_matches=False
            )
            distances[indices[pairs[0]]] = nearest_distances
        fractions[indices] = 0
        if len(wetlands):
            cells = shapely.box(
                x - half_cell, y - half_cell, x + half_cell, y + half_cell
            )
            pairs = wetlands.query(cells, predicate="intersects")
            areas = np.zeros(indices.size, dtype=np.float64)
            np.add.at(
                areas,
                pairs[0],
                shapely.area(
                    shapely.intersection(cells[pairs[0]], wetlands.geometries[pairs[1]])
                ),
            )
            fractions[indices] = np.clip(areas / cell_area, 0, 1)
    return EnvironmentStore(
        grid,
        state_mask=state_mask,
        municipality_code=municipality_code,
        water_distance_m=water_distance_m,
        wetland_fraction=wetland_fraction,
    )
