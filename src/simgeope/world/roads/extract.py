"""Filtered, topology-preserving extraction from a pinned local PBF."""

import math
from dataclasses import dataclass

from pyproj import Transformer
from shapely.geometry import LineString, box
from shapely.ops import transform

from ..exceptions import WorldBuildError
from ..sources.osm import dated_pbf

DEFAULT_CLASSES = (
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
)


@dataclass(frozen=True, slots=True)
class RoadWay:
    id: int
    node_ids: tuple[int, ...]
    coordinates: tuple[tuple[float, float], ...]
    tags: dict[str, str]


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    ways: tuple[RoadWay, ...]
    warnings: tuple[str, ...]
    source_date: str
    source_file: str
    dropped_invalid_geometry_count: int = 0


def road_class_allowed(tags, highway_classes=DEFAULT_CLASSES):
    highway = tags.get("highway", "")
    base_class = highway.removesuffix("_link")
    if highway not in highway_classes and base_class not in highway_classes:
        return False
    # More-specific vehicle access overrides generic access. Private roads are
    # excluded from the public logistics graph; destination-only roads remain.
    access = tags.get("motor_vehicle", tags.get("vehicle", tags.get("access", "yes")))
    return access not in {"no", "private"}


def extract_roads(
    pbf_path, extent_wgs84, padding_m, metric_crs, highway_classes=DEFAULT_CLASSES
):
    path, source_date = dated_pbf(pbf_path)
    try:
        import osmium
    except ImportError as exc:
        raise WorldBuildError(
            "PBF extraction requires the build dependency pyosmium (osmium)"
        ) from exc
    if not math.isfinite(padding_m) or padding_m < 0:
        raise ValueError("Road extraction padding must be finite and nonnegative")
    if not hasattr(extent_wgs84, "geom_type"):
        extent_wgs84 = box(*extent_wgs84)
    project = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True).transform
    # Buffer the actual extent, including disconnected island polygons. Retain
    # intersecting whole segments so clipping never invents unconnected nodes.
    extent = transform(project, extent_wgs84).buffer(padding_m)
    bounds = extent.bounds
    ways, invalid = [], 0

    class Handler(osmium.SimpleHandler):
        def way(self, way):
            nonlocal invalid
            tags = dict(way.tags)
            if not road_class_allowed(tags, highway_classes):
                return
            try:
                coordinates = tuple((node.lon, node.lat) for node in way.nodes)
                node_ids = tuple(node.ref for node in way.nodes)
            except (osmium.InvalidLocationError, RuntimeError):
                invalid += 1
                return
            if len(coordinates) < 2:
                return
            projected = tuple(project(*coordinate) for coordinate in coordinates)
            runs, start = [], None
            for index in range(len(projected) - 1):
                segment = LineString(projected[index : index + 2])
                sb = segment.bounds
                hit = (
                    sb[0] <= bounds[2]
                    and sb[2] >= bounds[0]
                    and sb[1] <= bounds[3]
                    and sb[3] >= bounds[1]
                    and extent.intersects(segment)
                )
                if hit and start is None:
                    start = index
                elif not hit and start is not None:
                    runs.append((start, index + 1))
                    start = None
            if start is not None:
                runs.append((start, len(projected)))
            for begin, end in runs:
                ways.append(
                    RoadWay(
                        int(way.id), node_ids[begin:end], coordinates[begin:end], tags
                    )
                )

    handler = Handler()
    # Sparse disk-backed cache avoids materializing all Nordeste nodes in RAM.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="simgeope-osm-") as temporary:
        handler.apply_file(
            str(path),
            locations=True,
            idx=f"sparse_file_array,{Path(temporary) / 'nodes.idx'}",
        )
    warnings = (
        (f"Dropped {invalid} road ways with missing OSM node locations",)
        if invalid
        else ()
    )
    return ExtractionResult(
        tuple(sorted(ways, key=lambda way: (way.id, way.node_ids))),
        warnings,
        source_date,
        path.name,
        invalid,
    )
