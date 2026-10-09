"""Canonical hydrology records, persistence and index-backed runtime queries."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import CRS
from shapely import from_wkb, get_coordinates, to_wkb
from shapely.errors import GEOSException
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from ..exceptions import WorldBuildError, WorldDataMissingError, WorldFormatError
from ..spatial import SpatialReference, lon_lat

LAYER_KINDS = {
    "waterways": ("river", "stream_major", "canal"),
    "waterbodies": ("lake", "reservoir", "pond", "lagoon", "water"),
    "wetlands": ("wetland",),
    "coastline": ("coastline",),
    "basins": ("basin",),
}
WATERWAY_KINDS = LAYER_KINDS["waterways"]
WATERBODY_KINDS = LAYER_KINDS["waterbodies"]
HYDRO_FILES = tuple(f"{name}.parquet" for name in LAYER_KINDS) + (
    "road_hydro_crossings.parquet",
)
LINE_TYPES = ("LineString", "MultiLineString")
POLYGON_TYPES = ("Polygon", "MultiPolygon")
FEATURE_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string(), nullable=False),
        pa.field("kind", pa.string(), nullable=False),
        pa.field("name", pa.string()),
        pa.field("source", pa.string(), nullable=False),
        *(
            pa.field(name, pa.string())
            for name in ("source_id", "source_date", "source_file")
        ),
        pa.field("municipality_codes", pa.list_(pa.string()), nullable=False),
        pa.field("tags", pa.string(), nullable=False),
        pa.field("geometry", pa.binary(), nullable=False),
    ]
)
CROSSING_SCHEMA = pa.schema(
    [
        pa.field("road_edge_id", pa.int64(), nullable=False),
        pa.field("hydro_feature_id", pa.string(), nullable=False),
        pa.field("hydro_feature_kind", pa.string(), nullable=False),
        pa.field("bridge_tag", pa.bool_(), nullable=False),
        pa.field("intersection_lon", pa.float64(), nullable=False),
        pa.field("intersection_lat", pa.float64(), nullable=False),
    ]
)


@dataclass(frozen=True, slots=True)
class HydroFeature:
    id: str
    kind: str
    geometry_wgs84: BaseGeometry
    name: str | None = None
    source: str = "osm"
    source_id: str | None = None
    source_date: str | None = None
    source_file: str | None = None
    municipality_codes: tuple[str, ...] = ()
    tags: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RoadHydroCrossing:
    road_edge_id: int
    hydro_feature_id: str
    hydro_feature_kind: str
    bridge_tag: bool
    intersection_lon: float
    intersection_lat: float


def _validate_feature(feature):
    if not isinstance(feature, HydroFeature):
        raise WorldBuildError("Hydrology features must be HydroFeature records")
    if not isinstance(feature.id, str) or not feature.id.strip():
        raise WorldBuildError("Hydrology feature IDs must be nonempty strings")
    if not isinstance(feature.kind, str) or feature.kind not in {
        kind for kinds in LAYER_KINDS.values() for kind in kinds
    }:
        raise WorldBuildError(f"Unknown hydrology kind: {feature.kind}")
    for name in ("name", "source_id", "source_date", "source_file"):
        value = getattr(feature, name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise WorldBuildError(f"Hydrology {name} must be a nonempty string or null")
    if not isinstance(feature.source, str) or not feature.source.strip():
        raise WorldBuildError("Hydrology source provenance must be a nonempty string")
    if (
        not isinstance(feature.municipality_codes, tuple)
        or any(
            not isinstance(code, str)
            or len(code) != 7
            or not code.isdigit()
            or not code.startswith("26")
            for code in feature.municipality_codes
        )
        or len(set(feature.municipality_codes)) != len(feature.municipality_codes)
    ):
        raise WorldBuildError(
            "Hydrology municipality codes must be unique Pernambuco codes"
        )
    if not isinstance(feature.tags, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in feature.tags.items()
    ):
        raise WorldBuildError("Hydrology tags must be a string-to-string dictionary")
    geometry = feature.geometry_wgs84
    types = (
        LINE_TYPES
        if feature.kind in (*LAYER_KINDS["waterways"], "coastline")
        else POLYGON_TYPES
    )
    if (
        not isinstance(geometry, BaseGeometry)
        or geometry.is_empty
        or geometry.geom_type not in types
        or not geometry.is_valid
    ):
        raise WorldBuildError(
            f"Hydrology {feature.id} requires valid nonempty {types} geometry"
        )
    coordinates = get_coordinates(geometry, include_z=geometry.has_z)
    if (
        geometry.has_z
        or not np.isfinite(coordinates).all()
        or (np.abs(coordinates[:, 0]) > 180).any()
        or (np.abs(coordinates[:, 1]) > 90).any()
    ):
        raise WorldBuildError(
            f"Hydrology {feature.id} requires finite 2D WGS84 coordinates"
        )


def _crossing_key(crossing):
    return (
        crossing.road_edge_id,
        crossing.hydro_feature_id,
        crossing.intersection_lon,
        crossing.intersection_lat,
    )


def _geo_metadata(types):
    return {
        b"geo": json.dumps(
            {
                "version": "1.0.0",
                "primary_column": "geometry",
                "columns": {
                    "geometry": {
                        "encoding": "WKB",
                        "geometry_types": list(types),
                        "crs": CRS.from_epsg(4326).to_json_dict(),
                    }
                },
            },
            sort_keys=True,
        ).encode()
    }


class HydroLayer:
    """Hydrology loaded once; all point queries use in-memory spatial indexes."""

    def __init__(self, features, metric_crs, crossings=()):
        metric = CRS.from_user_input(metric_crs)
        if not metric.is_projected or any(
            axis.unit_conversion_factor != 1.0 for axis in metric.axis_info
        ):
            raise WorldBuildError(
                "Hydrology metric CRS must be projected with metre units"
            )
        records = tuple(features)
        for feature in records:
            _validate_feature(feature)
        self.features = tuple(sorted(records, key=lambda feature: feature.id))
        self._features = {feature.id: feature for feature in self.features}
        if len(self._features) != len(self.features):
            raise WorldBuildError("Duplicate hydrology feature IDs")
        self.metric_crs = metric_crs
        self.spatial = SpatialReference(metric_crs)
        for name, kinds in LAYER_KINDS.items():
            setattr(
                self,
                name,
                tuple(feature for feature in self.features if feature.kind in kinds),
            )
        self._metric_geometries = {
            feature.id: self.spatial.geometry_to_metric(feature.geometry_wgs84)
            for feature in self.features
        }
        if any(
            not np.isfinite(get_coordinates(geometry)).all()
            for geometry in self._metric_geometries.values()
        ):
            raise WorldBuildError(
                "Hydrology geometry cannot be projected into the metric CRS"
            )
        self._query_features = {
            "waterways": self.waterways,
            "waterbodies": self.waterbodies,
            "water": tuple(
                feature
                for feature in self.features
                if feature.kind not in ("coastline", "basin")
            ),
        }
        self._metric_trees = {
            name: STRtree([self._metric_geometries[feature.id] for feature in records])
            for name, records in self._query_features.items()
        }
        self._basin_tree = STRtree([feature.geometry_wgs84 for feature in self.basins])
        self.crossings = tuple(crossings)
        for crossing in self.crossings:
            if (
                not isinstance(crossing, RoadHydroCrossing)
                or type(crossing.road_edge_id) is not int
                or crossing.road_edge_id < 0
                or type(crossing.bridge_tag) is not bool
                or not isinstance(crossing.hydro_feature_id, str)
                or not isinstance(crossing.hydro_feature_kind, str)
            ):
                raise WorldBuildError("Invalid road/hydrology crossing record")
            feature = self._features.get(crossing.hydro_feature_id)
            if (
                feature is None
                or feature.kind != crossing.hydro_feature_kind
                or feature.kind in ("coastline", "basin")
            ):
                raise WorldBuildError(
                    "Road/hydrology crossing references an unknown feature or kind"
                )
            try:
                lon_lat((crossing.intersection_lon, crossing.intersection_lat))
            except (TypeError, ValueError) as exc:
                raise WorldBuildError(
                    "Invalid road/hydrology crossing coordinates"
                ) from exc
            point = Point(
                self.spatial.project(
                    (crossing.intersection_lon, crossing.intersection_lat)
                )
            )
            # Source-linear and projected-linear intersections differ slightly
            # after projection; either legitimate construction must roundtrip.
            on_source = (
                feature.geometry_wgs84.distance(
                    Point(crossing.intersection_lon, crossing.intersection_lat)
                )
                <= 1e-9
            )
            if (
                not on_source
                and not self._metric_geometries[feature.id].distance(point) <= 1e-4
            ):
                raise WorldBuildError(
                    "Road/hydrology crossing point lies outside its hydro feature"
                )
        self.crossings = tuple(sorted(self.crossings, key=_crossing_key))
        if len({_crossing_key(crossing) for crossing in self.crossings}) != len(
            self.crossings
        ):
            raise WorldBuildError("Duplicate road/hydrology crossing")
        grouped = {}
        for crossing in self.crossings:
            grouped.setdefault(crossing.road_edge_id, []).append(crossing)
        self._crossings_by_road = {
            edge: tuple(records) for edge, records in grouped.items()
        }
        self.warnings = ()
        self.dropped_invalid_geometry_count = 0

    def _nearest(self, point, name):
        query = Point(self.spatial.project(point))
        if not math.isfinite(query.x) or not math.isfinite(query.y):
            raise ValueError("Query point cannot be projected into the metric CRS")
        records = self._query_features[name]
        if not records:
            return None, None
        indices, distances = self._metric_trees[name].query_nearest(
            query, all_matches=True, return_distance=True
        )
        index = min(range(len(indices)), key=lambda i: records[int(indices[i])].id)
        return records[int(indices[index])], float(distances[index])

    def distance_to_water(self, point) -> float | None:
        return self._nearest(point, "water")[1]

    def nearest_waterbody(self, point) -> HydroFeature | None:
        return self._nearest(point, "waterbodies")[0]

    def nearest_waterway(self, point) -> HydroFeature | None:
        return self._nearest(point, "waterways")[0]

    def basin_at(self, point) -> HydroFeature | None:
        indices = sorted(
            int(i)
            for i in self._basin_tree.query(
                Point(lon_lat(point)), predicate="intersects"
            )
        )
        return self.basins[indices[0]] if indices else None

    def intersections_with_road(self, edge_id) -> tuple[RoadHydroCrossing, ...]:
        return self._crossings_by_road.get(edge_id, ())

    def save(self, path):
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        for name in LAYER_KINDS:
            types = LINE_TYPES if name in ("waterways", "coastline") else POLYGON_TYPES
            rows = [
                {
                    "id": feature.id,
                    "kind": feature.kind,
                    "name": feature.name,
                    "source": feature.source,
                    "source_id": feature.source_id,
                    "source_date": feature.source_date,
                    "source_file": feature.source_file,
                    "municipality_codes": sorted(feature.municipality_codes),
                    "tags": json.dumps(
                        feature.tags,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "geometry": to_wkb(feature.geometry_wgs84),
                }
                for feature in getattr(self, name)
            ]
            pq.write_table(
                pa.Table.from_pylist(
                    rows, schema=FEATURE_SCHEMA.with_metadata(_geo_metadata(types))
                ),
                root / f"{name}.parquet",
                compression="zstd",
            )
        rows = [
            {name: getattr(crossing, name) for name in CROSSING_SCHEMA.names}
            for crossing in self.crossings
        ]
        pq.write_table(
            pa.Table.from_pylist(rows, schema=CROSSING_SCHEMA),
            root / "road_hydro_crossings.parquet",
            compression="zstd",
        )

    @classmethod
    def load(cls, path, metric_crs):
        root = Path(path)
        names = (*LAYER_KINDS, "road_hydro_crossings")
        for name in names:
            if not (root / f"{name}.parquet").is_file():
                raise WorldDataMissingError(
                    f"Hydrology output missing: {root / f'{name}.parquet'}"
                )
        try:
            features = []
            for name, kinds in LAYER_KINDS.items():
                table = pq.read_table(root / f"{name}.parquet")
                _validate_schema(table, FEATURE_SCHEMA)
                geo = json.loads((table.schema.metadata or {})[b"geo"])
                column = geo["columns"][geo["primary_column"]]
                types = (
                    LINE_TYPES if name in ("waterways", "coastline") else POLYGON_TYPES
                )
                if (
                    geo["primary_column"] != "geometry"
                    or column["encoding"] != "WKB"
                    or not CRS.from_user_input(column["crs"]).equals(
                        CRS.from_epsg(4326)
                    )
                    or set(column["geometry_types"]) != set(types)
                ):
                    raise WorldFormatError(
                        "Hydrology vectors must store typed WKB in EPSG:4326"
                    )
                for row in table.to_pylist():
                    if row["kind"] not in kinds:
                        raise WorldFormatError(f"Unexpected hydrology kind in {name}")
                    geometry = from_wkb(row.pop("geometry"))
                    row["municipality_codes"] = tuple(row["municipality_codes"])
                    row["tags"] = json.loads(row["tags"])
                    features.append(HydroFeature(geometry_wgs84=geometry, **row))
            table = pq.read_table(root / "road_hydro_crossings.parquet")
            _validate_schema(table, CROSSING_SCHEMA)
            crossings = [RoadHydroCrossing(**row) for row in table.to_pylist()]
            return cls(features, metric_crs, crossings)
        except (
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            WorldBuildError,
            GEOSException,
            pa.ArrowException,
        ) as exc:
            raise WorldFormatError("Invalid canonical hydrology tables") from exc


def _validate_schema(table, schema):
    if not table.schema.equals(schema, check_metadata=False):
        raise WorldFormatError("Unexpected canonical hydrology table schema")
    for column in schema:
        if not column.nullable and table[column.name].null_count:
            raise WorldFormatError(
                f"Null values in required hydrology column: {column.name}"
            )
