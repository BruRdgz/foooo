"""Immutable administrative records and index-backed municipality queries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import CRS
from shapely import from_wkb, to_wkb
from shapely.errors import GEOSException
from shapely.geometry import Point
from shapely.strtree import STRtree

from .exceptions import WorldBuildError, WorldDataMissingError, WorldFormatError
from .spatial import SpatialReference, lon_lat


@dataclass(frozen=True, slots=True)
class Municipality:
    code: str
    name: str
    geometry_wgs84: object


def _validate_polygon(geometry, label):
    if (
        geometry is None
        or geometry.is_empty
        or geometry.geom_type not in ("Polygon", "MultiPolygon")
        or not geometry.is_valid
    ):
        raise WorldBuildError(f"{label} must have valid, nonempty polygonal geometry")


def _write_geoparquet(path, rows):
    table = pa.Table.from_pylist(rows)
    metadata = dict(table.schema.metadata or {})
    metadata[b"geo"] = json.dumps(
        {
            "version": "1.0.0",
            "primary_column": "geometry",
            "columns": {
                "geometry": {
                    "encoding": "WKB",
                    "geometry_types": ["Polygon", "MultiPolygon"],
                    "crs": CRS.from_epsg(4326).to_json_dict(),
                }
            },
        },
        sort_keys=True,
    ).encode()
    pq.write_table(table.replace_schema_metadata(metadata), path, compression="zstd")


class AdministrativeLayer:
    def __init__(self, municipalities, state_geometry_wgs84, metric_crs):
        self.municipalities = tuple(sorted(municipalities, key=lambda m: m.code))
        if not self.municipalities:
            raise WorldBuildError("The administrative layer has no municipalities")
        codes = [m.code for m in self.municipalities]
        if len(set(codes)) != len(codes):
            raise WorldBuildError("Duplicate municipality code")
        for municipality in self.municipalities:
            if (
                not isinstance(municipality.code, str)
                or len(municipality.code) != 7
                or not municipality.code.startswith("26")
                or not municipality.code.isdigit()
            ):
                raise WorldBuildError(
                    f"Non-Pernambuco municipality code: {municipality.code}"
                )
            if not isinstance(municipality.name, str) or not municipality.name.strip():
                raise WorldBuildError(f"Missing municipality name: {municipality.code}")
            _validate_polygon(municipality.geometry_wgs84, municipality.code)
        _validate_polygon(state_geometry_wgs84, "Pernambuco state boundary")
        self.state_geometry_wgs84 = state_geometry_wgs84
        self.spatial = SpatialReference(metric_crs)
        self._metric_geometries = tuple(
            self.spatial.geometry_to_metric(m.geometry_wgs84)
            for m in self.municipalities
        )
        self._metric_tree = STRtree(self._metric_geometries)
        # Topological lookups use canonical geometries: projection of polygon
        # vertices alone can move a point off a straight source boundary.
        self._tree = STRtree([m.geometry_wgs84 for m in self.municipalities])
        self.warnings = ()
        self.dropped_invalid_geometry_count = 0

    def municipality_at(self, point) -> Municipality | None:
        query = Point(lon_lat(point))
        # Sorted codes make shared-boundary ties independent of source row order.
        indices = sorted(
            int(i) for i in self._tree.query(query, predicate="intersects")
        )
        return self.municipalities[indices[0]] if indices else None

    def municipality_codes_for_geometry(self, geometry_wgs84) -> tuple[str, ...]:
        return tuple(
            self.municipalities[i].code
            for i in sorted(
                int(i) for i in self._tree.query(geometry_wgs84, predicate="intersects")
            )
        )

    def save(self, path):
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        _write_geoparquet(
            root / "municipalities.parquet",
            [
                {"code": m.code, "name": m.name, "geometry": to_wkb(m.geometry_wgs84)}
                for m in self.municipalities
            ],
        )
        _write_geoparquet(
            root / "state_boundary.parquet",
            [
                {
                    "code": "26",
                    "name": "Pernambuco",
                    "geometry": to_wkb(self.state_geometry_wgs84),
                }
            ],
        )

    @classmethod
    def load(cls, path, metric_crs):
        root = Path(path)
        for name in ("municipalities.parquet", "state_boundary.parquet"):
            if not (root / name).is_file():
                raise WorldDataMissingError(
                    f"Administrative output missing: {root / name}"
                )
        try:
            tables = [
                pq.read_table(root / name)
                for name in ("municipalities.parquet", "state_boundary.parquet")
            ]
            for table in tables:
                geo = json.loads((table.schema.metadata or {})[b"geo"])
                geometry = geo["columns"][geo["primary_column"]]
                if (
                    geo["primary_column"] != "geometry"
                    or geometry["encoding"] != "WKB"
                    or not CRS.from_user_input(geometry["crs"]).equals(
                        CRS.from_epsg(4326)
                    )
                ):
                    raise WorldFormatError(
                        "Administrative vectors must store WKB in EPSG:4326"
                    )
            municipalities, states = (table.to_pylist() for table in tables)
            if len(states) != 1 or states[0]["code"] != "26":
                raise WorldFormatError("Expected one Pernambuco state boundary")
            return cls(
                [
                    Municipality(row["code"], row["name"], from_wkb(row["geometry"]))
                    for row in municipalities
                ],
                from_wkb(states[0]["geometry"]),
                metric_crs,
            )
        except (
            KeyError,
            TypeError,
            WorldBuildError,
            ValueError,
            RuntimeError,
            GEOSException,
            pa.ArrowException,
        ) as exc:
            raise WorldFormatError("Invalid administrative world tables") from exc
