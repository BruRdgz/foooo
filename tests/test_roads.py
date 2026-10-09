import json

import numpy as np
import pyarrow.parquet as pq
import pytest
from shapely.geometry import LineString, box

from simgeope.world.config import DEFAULT_METRIC_CRS
from simgeope.world.exceptions import (
    NoRoadNearbyError,
    RouteNotFoundError,
    WorldBuildError,
    WorldDataMissingError,
)
from simgeope.world.roads import RoadNetwork, RoadRuntimeState
from simgeope.world.roads.build import build_roads, parse_maxspeed
from simgeope.world.roads.extract import RoadWay


def way(source_id, node_ids, coords, **tags):
    return RoadWay(
        source_id, tuple(node_ids), tuple(coords), {"highway": "primary", **tags}
    )


@pytest.fixture
def network():
    return build_roads(
        [
            way(
                10,
                [1, 2, 3],
                [(-35, -8), (-34.99, -8), (-34.98, -8)],
                ref="BR-232",
                name="Main road",
                maxspeed="50",
                lanes="2",
                surface="asphalt",
                bridge="yes",
            ),
            way(
                11, [2, 4], [(-34.99, -8), (-34.99, -7.99)], oneway="yes", tunnel="yes"
            ),
            way(12, [8, 9], [(-32.42, -3.85), (-32.41, -3.85)], highway="secondary"),
        ],
        None,
        DEFAULT_METRIC_CRS,
        source_date="2025-01-01",
        source_file="nordeste-250101.osm.pbf",
    )


def test_way_interior_simplifies_but_keeps_geometry_and_metadata():
    class Admin:
        def municipality_codes_for_geometry(self, geometry):
            return ("2611606", "2604106")

    roads = build_roads(
        [
            way(
                15,
                [1, 2, 3],
                [(-35, -8), (-34.99, -7.999), (-34.98, -8)],
                highway="primary_link",
                ref="BR-232",
                name="Road",
                maxspeed="40 mph",
                lanes="2",
                surface="asphalt",
                bridge="yes",
                tunnel="no",
            )
        ],
        None,
        DEFAULT_METRIC_CRS,
        Admin(),
        "2025-01-01",
        "test-250101.osm.pbf",
    )
    assert {node.id for node in roads.nodes} == {1, 3}
    assert len(roads.edges) == 2
    forward, reverse = roads.edges
    assert len(roads.geometry(forward.id).coords) == 3
    assert tuple(roads.geometry(reverse.id).coords) == tuple(
        reversed(roads.geometry(forward.id).coords)
    )
    assert forward.highway == "primary_link"
    assert forward.source_id == 15
    assert forward.source_date == "2025-01-01"
    assert forward.source_file == "test-250101.osm.pbf"
    assert forward.ref == "BR-232" and forward.name == "Road"
    assert forward.maxspeed_kmh == pytest.approx(64.37376)
    assert forward.lanes == 2 and forward.surface == "asphalt"
    assert forward.bridge and not forward.tunnel and not forward.oneway
    assert forward.municipality_codes == ("2611606", "2604106")
    assert forward.base_travel_time_s == pytest.approx(
        forward.length_m / (forward.maxspeed_kmh / 3.6)
    )


def test_junctions_and_island_components_are_preserved(network):
    assert {node.id for node in network.nodes} == {1, 2, 3, 4, 8, 9}
    assert len(network.edges) == 7
    route = network.route(1, 4)
    assert route.travel_time_s > 0
    assert all(network.edge(edge_id).length_m > 0 for edge_id in route.edge_ids)
    with pytest.raises(RouteNotFoundError):
        network.route(4, 1)
    with pytest.raises(RouteNotFoundError):
        network.route(1, 8)
    assert network.route(8, 9).distance_m > 0


def test_closed_ring_retains_shape_and_junction():
    coords = [(-35, -8), (-34.99, -8), (-34.99, -7.99), (-35, -8)]
    ring = way(10, [1, 2, 3, 1], coords, junction="roundabout")
    isolated = build_roads([ring], None, DEFAULT_METRIC_CRS)
    assert len(isolated.edges) == 1
    assert isolated.edges[0].u == isolated.edges[0].v == 1
    assert isolated.edges[0].oneway
    assert len(isolated.geometry(0).coords) == 4
    joined = build_roads(
        [ring, way(11, [2, 4], [coords[1], (-34.98, -8)])], None, DEFAULT_METRIC_CRS
    )
    assert len([edge for edge in joined.edges if edge.source_id == 10]) == 2
    assert joined.route(1, 4).travel_time_s > 0


@pytest.mark.parametrize(
    "tags,start,end,reverse",
    [
        ({"oneway": "-1"}, 2, 1, False),
        ({"highway": "motorway"}, 1, 2, False),
        ({"highway": "motorway", "oneway": "no"}, 1, 2, True),
    ],
)
def test_osm_direction_rules(tags, start, end, reverse):
    roads = build_roads(
        [way(10, [1, 2], [(-35, -8), (-34.99, -8)], **tags)], None, DEFAULT_METRIC_CRS
    )
    assert roads.route(start, end).travel_time_s > 0
    if reverse:
        assert roads.route(end, start).travel_time_s > 0
    else:
        with pytest.raises(RouteNotFoundError):
            roads.route(end, start)


def test_directional_speeds_and_reversible_warning():
    roads = build_roads(
        [
            way(
                10,
                [1, 2],
                [(-35, -8), (-34.99, -8)],
                **{"maxspeed:forward": "60", "maxspeed:backward": "30"},
            ),
            way(11, [2, 3], [(-34.99, -8), (-34.98, -8)], oneway="reversible"),
        ],
        None,
        DEFAULT_METRIC_CRS,
    )
    assert roads.travel_time(2, 1) == pytest.approx(2 * roads.travel_time(1, 2))
    assert any("reversible" in warning for warning in roads.build_warnings)
    assert any("turn restrictions" in warning for warning in roads.build_warnings)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("30;50", 30),
        ("50 mph", 80.4672),
        ("70 km/h", 70),
        ("signals", None),
        ("0", None),
        (None, None),
    ],
)
def test_speed_parsing(value, expected):
    assert parse_maxspeed(value) == expected


def test_nearest_indexes_and_distance_limits(network):
    snap = network.nearest_road((-34.995, -8))
    assert snap.edge_id == 0  # Directed copies of the same shape tie by stable ID.
    assert snap.distance_m < 2
    assert snap.lon == pytest.approx(-34.995, abs=1e-5)
    assert network.nearest_node((-34.99, -8), max_distance_m=1) == 2
    assert {edge.id for edge in network.incident_edges(2)} == {0, 1, 2, 3, 4}
    assert network.node(2).lon == -34.99
    selected = network.roads_within(box(-35.001, -8.001, -34.999, -7.999))
    assert {edge.id for edge in selected} == {0, 1}
    with pytest.raises(NoRoadNearbyError):
        network.nearest_road((-36, -9), max_distance_m=100)
    with pytest.raises(NoRoadNearbyError):
        network.nearest_node((-36, -9), max_distance_m=100)
    for bad in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            network.nearest_road((-35, -8), max_distance_m=bad)


def test_persistence_keeps_rich_edges_geometry_and_route(network, tmp_path):
    network.save(tmp_path)
    loaded = RoadNetwork.load(tmp_path, metric_crs=DEFAULT_METRIC_CRS)
    assert loaded.nodes == network.nodes
    assert loaded.edges == network.edges
    assert all(
        loaded.geometry(edge.id).equals_exact(network.geometry(edge.id), 0)
        for edge in network.edges
    )
    assert loaded.route(1, 3) == network.route(1, 3)
    state = RoadRuntimeState(closed_edges={0})
    with pytest.raises(RouteNotFoundError):
        loaded.route(1, 3, state=state)
    metadata = json.loads(
        pq.read_schema(tmp_path / "roads_edges.parquet").metadata[b"geo"]
    )
    assert metadata["columns"]["geometry"]["geometry_types"] == ["LineString"]
    assert metadata["columns"]["geometry"]["crs"]["id"]["code"] == 4326


def test_persistence_rejects_corrupt_derived_costs(network, tmp_path):
    network.save(tmp_path)
    path = tmp_path / "roads_routing.npz"
    with np.load(path, allow_pickle=False) as persisted:
        values = {key: persisted[key].copy() for key in persisted.files}
    values["data"][0] *= 2
    np.savez_compressed(path, **values)
    with pytest.raises(ValueError, match="disagrees"):
        RoadNetwork.load(tmp_path, metric_crs=DEFAULT_METRIC_CRS)


def test_missing_or_invalid_road_data(network, tmp_path):
    with pytest.raises(WorldDataMissingError):
        RoadNetwork.load(tmp_path, metric_crs=DEFAULT_METRIC_CRS)
    with pytest.raises(WorldBuildError, match="No usable roads"):
        build_roads([], None, DEFAULT_METRIC_CRS)
    with pytest.raises(WorldBuildError, match="inconsistent"):
        build_roads([way(1, [1], [(-35, -8)])], None, DEFAULT_METRIC_CRS)
    with pytest.raises(WorldBuildError, match="Conflicting coordinates"):
        build_roads(
            [
                way(1, [1, 2], [(-35, -8), (-34.99, -8)]),
                way(2, [1, 3], [(-35.001, -8), (-34.98, -8)]),
            ],
            None,
            DEFAULT_METRIC_CRS,
        )
    with pytest.raises(ValueError, match="endpoints"):
        RoadNetwork(
            network.nodes,
            network.edges,
            {edge.id: LineString([(-1, -1), (0, 0)]) for edge in network.edges},
            metric_crs=DEFAULT_METRIC_CRS,
        )
