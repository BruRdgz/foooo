"""Canonical rich roads and runtime spatial/routing indexes."""

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely import from_wkb, to_wkb
from shapely.geometry import Point
from shapely.ops import transform
from shapely.strtree import STRtree

from ..exceptions import NoRoadNearbyError, WorldDataMissingError
from ..spatial import lon_lat
from .routing import Router


@dataclass(frozen=True, slots=True)
class RoadNode:
    id: int
    lon: float
    lat: float
    x_m: float
    y_m: float


@dataclass(frozen=True, slots=True)
class RoadEdge:
    id: int
    u: int
    v: int
    length_m: float
    base_travel_time_s: float
    highway: str | None = None
    ref: str | None = None
    name: str | None = None
    maxspeed_kmh: float | None = None
    lanes: int | None = None
    surface: str | None = None
    bridge: bool = False
    tunnel: bool = False
    oneway: bool = False
    municipality_codes: tuple[str, ...] = ()
    source_id: int | None = None
    source: str = "osm"
    source_date: str | None = None
    source_file: str | None = None
    speed_kmh: float | None = None


@dataclass(frozen=True, slots=True)
class RoadSnap:
    edge_id: int
    node_id: int
    distance_m: float
    lon: float
    lat: float


EDGE_SCHEMA = pa.schema(
    [
        *(pa.field(name, pa.int64()) for name in ("id", "u", "v")),
        *(pa.field(name, pa.float64()) for name in ("length_m", "base_travel_time_s")),
        *(pa.field(name, pa.string()) for name in ("highway", "ref", "name")),
        pa.field("maxspeed_kmh", pa.float64()),
        pa.field("lanes", pa.int64()),
        pa.field("surface", pa.string()),
        *(pa.field(name, pa.bool_()) for name in ("bridge", "tunnel", "oneway")),
        pa.field("municipality_codes", pa.list_(pa.string())),
        pa.field("source_id", pa.int64()),
        *(
            pa.field(name, pa.string())
            for name in ("source", "source_date", "source_file")
        ),
        pa.field("speed_kmh", pa.float64()),
        pa.field("geometry", pa.binary()),
    ]
)
NODE_SCHEMA = pa.schema(
    [
        pa.field("id", pa.int64()),
        *(pa.field(name, pa.float64()) for name in ("lon", "lat", "x_m", "y_m")),
        pa.field("geometry", pa.binary()),
    ]
)


class RoadNetwork:
    def __init__(self, nodes, edges, geometries, *, metric_crs):
        self.nodes = tuple(sorted(nodes, key=lambda node: node.id))
        self.edges = tuple(sorted(edges, key=lambda edge: edge.id))
        if len(set(node.id for node in self.nodes)) != len(self.nodes):
            raise ValueError("Duplicate road node IDs")
        if len(set(edge.id for edge in self.edges)) != len(self.edges):
            raise ValueError("Duplicate road edge IDs")
        for node in self.nodes:
            lon_lat((node.lon, node.lat))
            if not math.isfinite(node.x_m) or not math.isfinite(node.y_m):
                raise ValueError("Road metric node coordinates must be finite")
        self.metric_crs = metric_crs
        self._nodes = {node.id: node for node in self.nodes}
        self._edges = {edge.id: edge for edge in self.edges}
        incident = {node.id: [] for node in self.nodes}
        for edge in self.edges:
            for node_id in {edge.u, edge.v}:
                if node_id in incident:
                    incident[node_id].append(edge)
        self._incident_edges = {
            node: tuple(values) for node, values in incident.items()
        }
        self._geometries = {edge.id: geometries[edge.id] for edge in self.edges}
        for edge in self.edges:
            if edge.u not in self._nodes or edge.v not in self._nodes:
                raise ValueError(f"Edge {edge.id} references missing nodes")
            if not math.isfinite(edge.length_m) or edge.length_m <= 0:
                raise ValueError(f"Edge {edge.id} has invalid length")
            if (
                not math.isfinite(edge.base_travel_time_s)
                or edge.base_travel_time_s <= 0
            ):
                raise ValueError(f"Edge {edge.id} has invalid travel time")
            geometry = self._geometries[edge.id]
            if (
                geometry.is_empty
                or not geometry.is_valid
                or geometry.geom_type != "LineString"
            ):
                raise ValueError(f"Edge {edge.id} has invalid geometry")
            for coordinate, node_id in (
                (geometry.coords[0], edge.u),
                (geometry.coords[-1], edge.v),
            ):
                node = self._nodes[node_id]
                if not np.allclose(coordinate, (node.lon, node.lat), rtol=0, atol=1e-9):
                    raise ValueError(
                        f"Edge {edge.id} endpoints disagree with its road nodes"
                    )
        self._to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
        self._to_wgs84 = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)
        self._node_tree = (
            cKDTree([(node.x_m, node.y_m) for node in self.nodes])
            if self.nodes
            else None
        )
        self._metric_geometries = tuple(
            transform(self._to_metric.transform, self._geometries[edge.id])
            for edge in self.edges
        )
        self._edge_tree = STRtree(self._metric_geometries)
        self.router = Router([node.id for node in self.nodes], self.edges)

    @classmethod
    def from_records(cls, nodes, edges, *, metric_crs):
        node_objects = [
            node if isinstance(node, RoadNode) else RoadNode(**node) for node in nodes
        ]
        edge_objects, geometries = [], {}
        for item in edges:
            record = dict(item)
            geometries[record["id"]] = record.pop("geometry")
            if "municipality_codes" in record:
                record["municipality_codes"] = tuple(record["municipality_codes"])
            edge_objects.append(RoadEdge(**record))
        return cls(node_objects, edge_objects, geometries, metric_crs=metric_crs)

    def edge(self, edge_id):
        return self._edges[edge_id]

    def node(self, node_id):
        return self._nodes[node_id]

    def incident_edges(self, node_id):
        """Canonical incoming/outgoing edges touching a road node, sorted by ID."""
        return self._incident_edges[node_id]

    def geometry(self, edge_id):
        return self._geometries[edge_id]

    @staticmethod
    def _distance_limit(value):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("Maximum road distance must be finite and nonnegative")

    def nearest_node(self, point, *, max_distance_m=None):
        point = lon_lat(point)
        self._distance_limit(max_distance_m)
        if self._node_tree is None:
            raise NoRoadNearbyError("The road network has no nodes")
        distance, index = self._node_tree.query(self._to_metric.transform(*point))
        if max_distance_m is not None and distance > max_distance_m:
            raise NoRoadNearbyError(f"Nearest road node is {distance:.1f} m away")
        return self.nodes[int(index)].id

    def nearest_road(self, point, *, max_distance_m=None):
        point = lon_lat(point)
        self._distance_limit(max_distance_m)
        if not self.edges:
            raise NoRoadNearbyError("The road network has no edges")
        query = Point(*self._to_metric.transform(*point))
        candidates = self._edge_tree.query_nearest(query, all_matches=True)
        index = min(map(int, candidates), key=lambda item: self.edges[item].id)
        edge, geometry = self.edges[index], self._metric_geometries[index]
        projected = geometry.interpolate(geometry.project(query))
        distance = query.distance(projected)
        if max_distance_m is not None and distance > max_distance_m:
            raise NoRoadNearbyError(f"Nearest road edge is {distance:.1f} m away")
        # Road-node snapping preserves the directed graph. The edge projection is inspection-only.
        node_id = min(
            (edge.u, edge.v),
            key=lambda node: (
                query.distance(Point(self._nodes[node].x_m, self._nodes[node].y_m)),
                node,
            ),
        )
        lon, lat = self._to_wgs84.transform(projected.x, projected.y)
        return RoadSnap(edge.id, node_id, float(distance), float(lon), float(lat))

    def roads_within(self, geometry):
        metric_geometry = transform(self._to_metric.transform, geometry)
        return [
            self.edges[int(index)]
            for index in sorted(
                self._edge_tree.query(metric_geometry, predicate="intersects")
            )
        ]

    def route(self, origin, destination, *, state=None):
        return self.router.route(origin, destination, state=state)

    def travel_time(self, origin, destination, *, state=None):
        return self.router.travel_time(origin, destination, state=state)

    def travel_times(self, origin, destinations, *, state=None):
        return self.router.travel_times(origin, destinations, state=state)

    def largest_component_nodes(self):
        return self.router.largest_component_nodes()

    def distance(self, origin, destination, *, state=None):
        return self.router.distance(origin, destination, state=state)

    def save(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        geo_metadata = {
            b"geo": json.dumps(
                {
                    "version": "1.0.0",
                    "primary_column": "geometry",
                    "columns": {"geometry": {"encoding": "WKB", "crs": None}},
                }
            ).encode()
        }
        # Explicit CRS avoids treating longitude/latitude as an unspecified coordinate system.
        from pyproj import CRS

        geo = json.loads(geo_metadata[b"geo"])
        geo["columns"]["geometry"]["crs"] = CRS.from_epsg(4326).to_json_dict()
        geo_metadata[b"geo"] = json.dumps(geo).encode()
        node_rows = [
            dict(asdict(node), geometry=to_wkb(Point(node.lon, node.lat)))
            for node in self.nodes
        ]
        edge_rows = [
            dict(asdict(edge), geometry=to_wkb(self.geometry(edge.id)))
            for edge in self.edges
        ]
        node_geo = json.loads(geo_metadata[b"geo"])
        node_geo["columns"]["geometry"]["geometry_types"] = ["Point"]
        edge_geo = json.loads(geo_metadata[b"geo"])
        edge_geo["columns"]["geometry"]["geometry_types"] = ["LineString"]
        pq.write_table(
            pa.Table.from_pylist(
                node_rows,
                schema=NODE_SCHEMA.with_metadata(
                    {b"geo": json.dumps(node_geo).encode()}
                ),
            ),
            directory / "roads_nodes.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist(
                edge_rows,
                schema=EDGE_SCHEMA.with_metadata(
                    {b"geo": json.dumps(edge_geo).encode()}
                ),
            ),
            directory / "roads_edges.parquet",
        )
        graph = self.router._base_graph
        np.savez_compressed(
            directory / "roads_routing.npz",
            node_ids=np.array([node.id for node in self.nodes], dtype=np.int64),
            edge_ids=np.array([edge.id for edge in self.edges], dtype=np.int64),
            data=graph.data,
            indices=graph.indices,
            indptr=graph.indptr,
            shape=np.array(graph.shape, dtype=np.int64),
        )

    @classmethod
    def load(cls, directory, *, metric_crs):
        directory = Path(directory)
        try:
            nodes = pq.read_table(directory / "roads_nodes.parquet").to_pylist()
            edges = pq.read_table(directory / "roads_edges.parquet").to_pylist()
            with np.load(
                directory / "roads_routing.npz", allow_pickle=False
            ) as derived:
                if derived["node_ids"].tolist() != [row["id"] for row in nodes]:
                    raise ValueError("Routing node IDs disagree with canonical roads")
                if derived["edge_ids"].tolist() != [row["id"] for row in edges]:
                    raise ValueError("Routing edge IDs disagree with canonical roads")
        except FileNotFoundError as exc:
            raise WorldDataMissingError(f"Missing road output: {exc.filename}") from exc
        for row in nodes:
            row.pop("geometry")
        for row in edges:
            row["geometry"] = from_wkb(row["geometry"])
        network = cls.from_records(nodes, edges, metric_crs=metric_crs)
        # Load the persisted compact baseline after validating against canonical topology/costs.
        with np.load(directory / "roads_routing.npz", allow_pickle=False) as derived:
            baseline = network.router._base_graph
            if not (
                np.array_equal(derived["indices"], baseline.indices)
                and np.array_equal(derived["indptr"], baseline.indptr)
                and np.array_equal(derived["data"], baseline.data)
                and np.array_equal(derived["shape"], baseline.shape)
            ):
                raise ValueError("Derived routing graph disagrees with canonical roads")
        return network
