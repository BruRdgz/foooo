"""Build-time geometric road/water intersections from the canonical edge table."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import CRS
from pyproj.exceptions import CRSError, ProjError
from shapely import from_wkb, get_coordinates, normalize
from shapely.errors import GEOSException
from shapely.strtree import STRtree

from ..exceptions import WorldBuildError
from ..spatial import SpatialReference, lon_lat
from .model import LAYER_KINDS, RoadHydroCrossing, _validate_feature


def _intersection_points(geometry):
    """Yield actual overlap points, retaining every disconnected component.

    Point crossings are retained directly. A line overlap (including a road
    inside a water polygon) contributes its metric length midpoint, with a
    normalized orientation so reversed directed roads choose the same point.
    These are geometric intersections; they do not imply a physical bridge.
    """
    if geometry.is_empty:
        return
    if geometry.geom_type == "Point":
        yield geometry
    elif geometry.geom_type in ("LineString", "LinearRing"):
        yield normalize(geometry).interpolate(0.5, normalized=True)
    elif geometry.geom_type == "Polygon":
        yield geometry.representative_point()
    elif hasattr(geometry, "geoms"):
        for component in geometry.geoms:
            yield from _intersection_points(component)
    else:
        raise WorldBuildError(
            f"Unsupported road/water intersection: {geometry.geom_type}"
        )


def _road_geometry(wkb, edge_id):
    if wkb is None:
        raise WorldBuildError(f"Road edge {edge_id} has missing geometry")
    geometry = from_wkb(wkb)
    if (
        geometry is None
        or geometry.is_empty
        or not geometry.is_valid
        or geometry.geom_type != "LineString"
        or geometry.has_z
    ):
        raise WorldBuildError(
            f"Road edge {edge_id} requires valid nonempty 2D LineString geometry"
        )
    coordinates = get_coordinates(geometry)
    if (
        not np.isfinite(coordinates).all()
        or (np.abs(coordinates[:, 0]) > 180).any()
        or (np.abs(coordinates[:, 1]) > 90).any()
    ):
        raise WorldBuildError(f"Road edge {edge_id} requires finite WGS84 coordinates")
    return geometry


def _crossing_key(crossing):
    return (
        crossing.road_edge_id,
        crossing.hydro_feature_id,
        crossing.hydro_feature_kind,
        crossing.bridge_tag,
        crossing.intersection_lon,
        crossing.intersection_lat,
    )


def build_crossings(
    road_edges_path, features, metric_crs
) -> tuple[RoadHydroCrossing, ...]:
    """Scan only edge IDs, bridge tags and WKB in bounded Arrow batches.

    Each canonical directed road ID receives its own records. Waterways,
    waterbodies and wetlands participate; coastline and basins do not. All
    intersections are computed in the configured metric CRS and transformed
    back to WGS84. No bridge status is inferred from geometry.
    """
    try:
        return _build_crossings(Path(road_edges_path), features, metric_crs)
    except WorldBuildError:
        raise
    except (
        OSError,
        pa.ArrowException,
        CRSError,
        ProjError,
        GEOSException,
        ValueError,
    ) as exc:
        raise WorldBuildError(f"Cannot derive road/hydrology crossings: {exc}") from exc


def _build_crossings(road_edges_path, features, metric_crs):
    metric = CRS.from_user_input(metric_crs)
    if not metric.is_projected or any(
        axis.unit_conversion_factor != 1.0 for axis in metric.axis_info
    ):
        raise WorldBuildError("Crossings require a projected CRS with metre units")
    spatial = SpatialReference(metric_crs)
    records = tuple(features)
    feature_ids = set()
    eligible = []
    water_kinds = (
        *LAYER_KINDS["waterways"],
        *LAYER_KINDS["waterbodies"],
        *LAYER_KINDS["wetlands"],
    )
    for feature in records:
        _validate_feature(feature)
        if feature.id in feature_ids:
            raise WorldBuildError(f"Duplicate hydrology feature ID: {feature.id}")
        feature_ids.add(feature.id)
        if feature.kind in water_kinds:
            eligible.append(feature)
    metric_geometries = tuple(
        spatial.geometry_to_metric(feature.geometry_wgs84) for feature in eligible
    )
    if any(
        not geometry.is_valid or not np.isfinite(get_coordinates(geometry)).all()
        for geometry in metric_geometries
    ):
        raise WorldBuildError(
            "Hydrology geometry cannot be projected into the metric CRS"
        )
    tree = STRtree(metric_geometries)

    parquet = pq.ParquetFile(road_edges_path)
    schema = parquet.schema_arrow
    required = ("id", "bridge", "geometry")
    if any(name not in schema.names for name in required):
        raise WorldBuildError(
            "Road edge table requires id, bridge and geometry columns"
        )
    if not pa.types.is_integer(schema.field("id").type) or not pa.types.is_boolean(
        schema.field("bridge").type
    ):
        raise WorldBuildError(
            "Road edge IDs must be integers and bridge tags must be boolean"
        )
    if not (
        pa.types.is_binary(schema.field("geometry").type)
        or pa.types.is_large_binary(schema.field("geometry").type)
    ):
        raise WorldBuildError("Road edge geometry must contain WKB binary values")

    edge_ids = set()
    crossings = set()
    for batch in parquet.iter_batches(batch_size=8192, columns=list(required)):
        ids, bridges, geometries = (batch.column(name).to_pylist() for name in required)
        for edge_id, bridge, wkb in zip(ids, bridges, geometries, strict=True):
            if type(edge_id) is not int or edge_id < 0:
                raise WorldBuildError("Road edge IDs must be nonnegative integers")
            if edge_id in edge_ids:
                raise WorldBuildError(f"Duplicate road edge ID: {edge_id}")
            edge_ids.add(edge_id)
            if type(bridge) is not bool:
                raise WorldBuildError(
                    f"Road edge {edge_id} requires a boolean bridge tag"
                )
            road = spatial.geometry_to_metric(_road_geometry(wkb, edge_id))
            if not road.is_valid or not np.isfinite(get_coordinates(road)).all():
                raise WorldBuildError(
                    f"Road edge {edge_id} cannot be projected into the metric CRS"
                )
            for index in tree.query(road, predicate="intersects"):
                feature = eligible[int(index)]
                intersection = road.intersection(metric_geometries[int(index)])
                for point in _intersection_points(intersection):
                    lon, lat = lon_lat(spatial.unproject((point.x, point.y)))
                    crossings.add(
                        RoadHydroCrossing(
                            edge_id, feature.id, feature.kind, bridge, lon, lat
                        )
                    )
    return tuple(sorted(crossings, key=_crossing_key))
