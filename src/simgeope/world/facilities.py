"""Static facility entities, snapping primitives and cached spatial queries."""

import math
import warnings
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from scipy.spatial import cKDTree
from shapely.geometry import Point
from shapely.strtree import STRtree

from .exceptions import NoRoadNearbyError, WorldDataMissingError, WorldFormatError
from .spatial import SpatialReference, lon_lat


@dataclass(frozen=True, slots=True)
class Facility:
    id: str
    kind: str
    lon: float
    lat: float
    municipality_code: str | None = None
    road_node: int | None = None
    road_edge: int | None = None
    snap_distance_m: float | None = None
    name: str | None = None

    def __post_init__(self):
        lon_lat((self.lon, self.lat))
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.id, self.kind)
        ):
            raise ValueError("Facility ID and kind must be nonempty")
        for label, value in (
            ("name", self.name),
            ("municipality_code", self.municipality_code),
        ):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"Facility {label} must be text or None")
        for label, value in (
            ("road_node", self.road_node),
            ("road_edge", self.road_edge),
        ):
            if value is not None and type(value) is not int:
                raise ValueError(f"Facility {label} must be an integer or None")
        if self.snap_distance_m is not None and (
            not math.isfinite(self.snap_distance_m) or self.snap_distance_m < 0
        ):
            raise ValueError("Invalid facility snap distance")


def snap_facility(
    facility,
    roads,
    administrative=None,
    *,
    max_distance_m=20_000,
    allow_excessive=False,
):
    if not math.isfinite(max_distance_m) or max_distance_m < 0:
        raise ValueError(
            "Facility maximum snap distance must be finite and nonnegative"
        )
    point = (facility.lon, facility.lat)
    node_id = roads.nearest_node(point)
    node = roads.node(node_id)
    x, y = SpatialReference(roads.metric_crs).project(point)
    distance = math.hypot(node.x_m - x, node.y_m - y)
    if distance > max_distance_m:
        message = (
            f"Facility {facility.id!r} is {distance:.1f} m from its snapped road node"
        )
        if not allow_excessive:
            raise NoRoadNearbyError(message)
        warnings.warn(message, UserWarning, stacklevel=2)
    municipality = (
        administrative.municipality_at(point) if administrative is not None else None
    )
    incident = roads.incident_edges(node_id)
    edge_id = min((edge.id for edge in incident), default=None)
    return replace(
        facility,
        road_node=node_id,
        road_edge=edge_id,
        snap_distance_m=distance,
        municipality_code=(municipality.code if municipality else None)
        if administrative is not None
        else facility.municipality_code,
    )


class FacilityIndex:
    def __init__(self, facilities=(), *, metric_crs):
        self.facilities = tuple(sorted(facilities, key=lambda facility: facility.id))
        self._by_id = {facility.id: facility for facility in self.facilities}
        if len(self._by_id) != len(self.facilities):
            raise ValueError("Duplicate facility IDs")
        self.sources = ()
        self.warnings = ()
        self.dropped_row_count = 0
        self.dropped_invalid_geometry_count = 0
        self._reference = SpatialReference(metric_crs)
        self._points = tuple(
            Point(*self._reference.project((facility.lon, facility.lat)))
            for facility in self.facilities
        )
        self._tree = STRtree(self._points)
        self._kd_tree = (
            cKDTree([(point.x, point.y) for point in self._points])
            if self._points
            else None
        )

    def get(self, facility_id):
        return self._by_id[facility_id]

    def nearby(self, point, radius_m):
        if not math.isfinite(radius_m) or radius_m < 0:
            raise ValueError("Facility radius must be finite and nonnegative")
        projected = self._reference.project(point)
        if self._kd_tree is None:
            return []
        indices = sorted(self._kd_tree.query_ball_point(projected, radius_m))
        return [self.facilities[index] for index in indices]

    def within(self, geometry):
        projected = self._reference.geometry_to_metric(geometry)
        return [
            self.facilities[int(index)]
            for index in sorted(self._tree.query(projected, predicate="covers"))
        ]

    def save(self, directory):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("kind", pa.string()),
                pa.field("lon", pa.float64()),
                pa.field("lat", pa.float64()),
                pa.field("municipality_code", pa.string()),
                pa.field("road_node", pa.int64()),
                pa.field("road_edge", pa.int64()),
                pa.field("snap_distance_m", pa.float64()),
                pa.field("name", pa.string()),
            ]
        )
        pq.write_table(
            pa.Table.from_pylist(
                [asdict(facility) for facility in self.facilities], schema=schema
            ),
            path / "facilities.parquet",
        )

    @classmethod
    def load(cls, directory, *, metric_crs):
        path = Path(directory) / "facilities.parquet"
        if not path.is_file():
            raise WorldDataMissingError(f"Missing facility output: {path}")
        try:
            table = pq.read_table(path)
            required = {
                "id",
                "kind",
                "lon",
                "lat",
                "municipality_code",
                "road_node",
                "road_edge",
                "snap_distance_m",
            }
            if not required.issubset(table.column_names) or set(table.column_names) - (
                required | {"name"}
            ):
                raise WorldFormatError(f"Invalid canonical facility columns: {path}")
            if len(set(table.column_names)) != len(table.column_names):
                raise WorldFormatError(f"Duplicate canonical facility columns: {path}")
            for field in table.schema:
                if pa.types.is_null(field.type):
                    continue  # Older writers may infer null-only optional columns.
                if field.name in {"id", "kind", "name", "municipality_code"}:
                    valid = pa.types.is_string(field.type)
                elif field.name in {"road_node", "road_edge"}:
                    valid = pa.types.is_integer(field.type)
                else:
                    valid = pa.types.is_integer(field.type) or pa.types.is_floating(
                        field.type
                    )
                if not valid:
                    raise WorldFormatError(
                        f"Invalid canonical facility type for {field.name}: {path}"
                    )
            return cls(
                [Facility(**row) for row in table.to_pylist()],
                metric_crs=metric_crs,
            )
        except (ValueError, TypeError, OSError, pa.ArrowException) as exc:
            raise WorldFormatError(f"Invalid canonical facility table: {path}") from exc
