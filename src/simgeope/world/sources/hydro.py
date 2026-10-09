"""Build-time hydrology providers, with cached local OSM normalization.

OSM streams are excluded by default. The optional named-stream selection is
only a mapping heuristic, never an authoritative measure of stream importance.
"""

from __future__ import annotations

import math
import tempfile
from collections import Counter
from numbers import Real
from pathlib import Path
from typing import Protocol

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError
from shapely import from_wkb, make_valid
from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import transform
from shapely.prepared import prep

from ..exceptions import WorldBuildError, WorldDataMissingError
from .osm import source_date_from_filename


class HydroSource(Protocol):
    def load_waterways(self): ...

    def load_waterbodies(self): ...

    def load_wetlands(self): ...

    def load_coastline(self): ...

    def load_basins(self): ...


def hydro_kind(tags, *, include_named_streams=False):
    """Translate OSM tags without inferring hydrological importance or basins."""
    if tags.get("natural") == "coastline":
        return "coastline"
    if tags.get("natural") == "wetland":
        return "wetland"
    if tags.get("landuse") == "reservoir":
        return "reservoir"
    # Explicit surface-water tags take precedence over a river/canal tag on
    # multipolygons and closed ways. riverbank is OSM's legacy surface tag.
    if tags.get("natural") == "water" or "water" in tags:
        water = tags.get("water")
        return water if water in {"lake", "reservoir", "pond", "lagoon"} else "water"
    if tags.get("waterway") == "riverbank":
        return "water"
    waterway = tags.get("waterway")
    if waterway in {"river", "canal"}:
        return waterway
    if waterway == "stream":
        return (
            "stream_major"
            if include_named_streams and tags.get("name", "").strip()
            else None
        )
    return None


def _layer(kind):
    if kind in {"river", "canal", "stream_major"}:
        return "waterways"
    if kind == "wetland":
        return "wetlands"
    if kind == "coastline":
        return "coastline"
    return "waterbodies"


def _polygonal(geometry):
    """Keep only polygonal components of make_valid's possible collections."""
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    polygons = []
    for part in getattr(geometry, "geoms", ()):
        result = _polygonal(part)
        if not result.is_empty:
            polygons.extend(
                result.geoms if result.geom_type == "MultiPolygon" else [result]
            )
    return MultiPolygon(polygons) if polygons else Polygon()


class OSMHydroSource:
    """Extract once from a pinned local PBF (or dated OSM XML fixture).

    Geometry is retained whole when it intersects the projected state footprint
    and optional metre buffer. Disconnected administrative islands remain part
    of that footprint. pyosmium performs its required relation assembly prepass
    within this one extraction; loading different layers never repeats it.

    Repair/drop diagnostics describe selected hydro tags across the source,
    before state filtering. Missing-node or unassembled geometries cannot be
    located reliably, so their counts cannot be attributed solely to Pernambuco.
    """

    def __init__(
        self,
        path,
        administrative,
        metric_crs,
        *,
        min_waterbody_area_m2=10_000,
        include_named_streams=False,
        border_padding_m=0,
    ):
        self.path = Path(path)
        if not self.path.is_file():
            raise WorldDataMissingError(
                f"Local OSM hydrology source does not exist: {self.path}"
            )
        if not self.path.name.endswith((".osm.pbf", ".osm", ".osm.xml")):
            raise WorldBuildError(
                "OSM hydrology input must be a local .osm.pbf or .osm XML file"
            )
        self.source_date = source_date_from_filename(self.path)
        self.source_file = self.path.name
        for label, value in (
            ("Waterbody minimum area", min_waterbody_area_m2),
            ("Hydrology border padding", border_padding_m),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{label} must be finite and nonnegative")
        if not isinstance(include_named_streams, bool):
            raise ValueError("include_named_streams must be a boolean")
        try:
            projected_crs = CRS.from_user_input(metric_crs)
        except CRSError as exc:
            raise ValueError(
                "metric_crs must be a valid projected CRS with metre units"
            ) from exc
        if not projected_crs.is_projected or any(
            axis.unit_conversion_factor != 1.0 for axis in projected_crs.axis_info
        ):
            raise ValueError("metric_crs must be a projected CRS with metre units")
        self.administrative = administrative
        self.min_waterbody_area_m2 = min_waterbody_area_m2
        self.include_named_streams = include_named_streams
        self._project = Transformer.from_crs(
            "EPSG:4326", projected_crs, always_xy=True
        ).transform
        self._extent = transform(
            self._project, administrative.state_geometry_wgs84
        ).buffer(border_padding_m)
        self._prepared_extent = prep(self._extent)
        self._layers = None
        self._warnings = ()
        self._dropped = 0

    @property
    def warnings(self):
        self._ensure_loaded()
        return self._warnings

    @property
    def dropped_invalid_geometry_count(self):
        self._ensure_loaded()
        return self._dropped

    def load_waterways(self):
        return self._load("waterways")

    def load_waterbodies(self):
        return self._load("waterbodies")

    def load_wetlands(self):
        return self._load("wetlands")

    def load_coastline(self):
        return self._load("coastline")

    def load_basins(self):
        # OSM surface-water polygons do not establish hydrographic basins.
        return ()

    def _load(self, layer):
        self._ensure_loaded()
        return self._layers[layer]

    def _ensure_loaded(self):
        if self._layers is not None:
            return
        try:
            import osmium
        except ImportError as exc:
            raise WorldBuildError(
                "OSM hydrology extraction requires pyosmium (osmium)"
            ) from exc
        from ..hydro.model import HydroFeature

        candidates = {}
        relation_members = {}
        expected_relations, assembled_relations = set(), set()
        counts = Counter()
        factory = osmium.geom.WKBFactory()
        source = self

        def add(source_type, source_id, kind, geometry, tags):
            if geometry.is_empty:
                counts["invalid"] += 1
                return
            if _layer(kind) in {"waterbodies", "wetlands"}:
                if not geometry.is_valid:
                    geometry = _polygonal(make_valid(geometry))
                    counts["repaired"] += 1
                if geometry.is_empty or not geometry.is_valid:
                    counts["invalid"] += 1
                    return
            elif not geometry.is_valid or geometry.length == 0:
                counts["invalid"] += 1
                return
            projected = transform(source._project, geometry)
            if not projected.is_valid:
                counts["invalid"] += 1
                return
            if not source._prepared_extent.intersects(projected):
                return
            if (
                _layer(kind) == "waterbodies"
                and projected.area < source.min_waterbody_area_m2
            ):
                return
            key = (source_type, source_id)
            candidates[key] = HydroFeature(
                id=f"osm:{source_type}:{source_id}",
                kind=kind,
                geometry_wgs84=geometry,
                name=tags.get("name", "").strip() or None,
                source="osm",
                source_id=f"{source_type}/{source_id}",
                source_date=source.source_date,
                source_file=source.source_file,
                municipality_codes=source.administrative.municipality_codes_for_geometry(
                    geometry
                ),
                tags=tags,
            )

        class Handler(osmium.SimpleHandler):
            def way(self, way):
                tags = dict(way.tags)
                kind = hydro_kind(
                    tags, include_named_streams=source.include_named_streams
                )
                if kind is None:
                    return
                try:
                    coordinates = [(node.lon, node.lat) for node in way.nodes]
                    if len(coordinates) < 2 or not all(
                        math.isfinite(value) for pair in coordinates for value in pair
                    ):
                        counts["invalid"] += 1
                        return
                    if _layer(kind) in {"waterbodies", "wetlands"}:
                        if len(coordinates) < 4 or coordinates[0] != coordinates[-1]:
                            counts["invalid"] += 1
                            return
                        geometry = Polygon(coordinates)
                    else:
                        geometry = LineString(coordinates)
                    add("way", int(way.id), kind, geometry, tags)
                except (
                    osmium.InvalidLocationError,
                    RuntimeError,
                    ValueError,
                    GEOSException,
                ):
                    counts["invalid"] += 1

            def relation(self, relation):
                tags = dict(relation.tags)
                kind = hydro_kind(
                    tags, include_named_streams=source.include_named_streams
                )
                if (
                    tags.get("type") != "multipolygon"
                    or kind is None
                    or _layer(kind) not in {"waterbodies", "wetlands"}
                ):
                    return
                relation_id = int(relation.id)
                expected_relations.add(relation_id)
                relation_members[relation_id] = {
                    int(member.ref) for member in relation.members if member.type == "w"
                }

            def area(self, area):
                if area.from_way():
                    return  # Closed ways were normalized by way(), including repair.
                tags = dict(area.tags)
                kind = hydro_kind(
                    tags, include_named_streams=source.include_named_streams
                )
                if kind is None or _layer(kind) not in {"waterbodies", "wetlands"}:
                    return
                relation_id = int(area.orig_id())
                assembled_relations.add(relation_id)
                try:
                    geometry = from_wkb(factory.create_multipolygon(area))
                    add("relation", relation_id, kind, geometry, tags)
                except (
                    osmium.InvalidLocationError,
                    RuntimeError,
                    ValueError,
                    GEOSException,
                ):
                    counts["invalid"] += 1

        try:
            with tempfile.TemporaryDirectory(prefix="simgeope-hydro-") as temporary:
                Handler().apply_file(
                    str(self.path),
                    locations=True,
                    idx=f"sparse_file_array,{Path(temporary) / 'nodes.idx'}",
                )
        except (OSError, RuntimeError) as exc:
            raise WorldBuildError(
                f"Cannot extract local OSM hydrology source: {self.path}"
            ) from exc

        counts["invalid"] += len(expected_relations - assembled_relations)
        # Tagged member ways are redundant only when their parent polygon was
        # actually retained. An unrelated river member remains a linear feature.
        for relation_id, members in relation_members.items():
            relation = candidates.get(("relation", relation_id))
            if relation is not None:
                for member in members:
                    way = candidates.get(("way", member))
                    if way is not None and _layer(way.kind) == _layer(relation.kind):
                        del candidates[("way", member)]
        layers = {
            name: [] for name in ("waterways", "waterbodies", "wetlands", "coastline")
        }
        for feature in sorted(
            candidates.values(), key=lambda feature: (feature.kind, feature.id)
        ):
            layers[_layer(feature.kind)].append(feature)
        warnings = [
            "No authoritative hydrographic basin source is available; basins are empty"
        ]
        if self.include_named_streams:
            warnings.append(
                "Named OSM streams use a nonauthoritative stream_major heuristic; mapping density does not measure physical stream density"
            )
        if counts["repaired"]:
            warnings.append(
                f"Repaired {counts['repaired']} invalid OSM hydro polygon geometries (source-wide, before state filtering)"
            )
        if counts["invalid"]:
            warnings.append(
                f"Dropped {counts['invalid']} invalid or unassembled OSM hydro geometries (source-wide, before state filtering)"
            )
        if any(feature.kind == "water" for feature in candidates.values()):
            warnings.append(
                "Generic OSM water polygons have uncertain waterbody taxonomy"
            )
        self._warnings = tuple(warnings)
        self._dropped = counts["invalid"]
        self._layers = {name: tuple(features) for name, features in layers.items()}
