import pytest


def _tiny_world(tmp_path, extra_edges=()):
    from shapely.geometry import LineString, box
    from shapely.ops import unary_union

    from simgeope import PernambucoWorld, WorldConfig
    from simgeope.world.administrative import AdministrativeLayer, Municipality
    from simgeope.world.facilities import Facility, FacilityIndex, snap_facility
    from simgeope.world.manifest import WorldManifest
    from simgeope.world.roads import RoadNetwork
    from simgeope.world.spatial import SpatialReference

    config = WorldConfig(max_snap_distance_m=5_000)
    mainland = box(-35.01, -8.02, -34.97, -7.99)
    island = box(-32.45, -3.9, -32.35, -3.8)
    admin = AdministrativeLayer(
        [
            Municipality("2611606", "Recife", mainland),
            Municipality("2605459", "Fernando de Noronha", island),
        ],
        unary_union([mainland, island]),
        config.metric_crs,
    )
    reference = SpatialReference(config.metric_crs)
    positions = {
        10: (-35.0, -8.0),
        20: (-34.99, -8.0),
        30: (-34.98, -8.0),
        40: (-34.99, -8.01),
        50: (-32.4, -3.85),
    }
    nodes = []
    for node_id, point in positions.items():
        x, y = reference.project(point)
        nodes.append(dict(id=node_id, lon=point[0], lat=point[1], x_m=x, y_m=y))
    edges = []
    for edge_id, u, v, seconds in [
        (1, 10, 20, 10),
        (2, 20, 30, 10),
        (3, 10, 40, 30),
        (4, 40, 30, 30),
        *extra_edges,
    ]:
        geometry = LineString([positions[u], positions[v]])
        edges.append(
            dict(
                id=edge_id,
                u=u,
                v=v,
                geometry=geometry,
                length_m=reference.geometry_to_metric(geometry).length,
                base_travel_time_s=seconds,
                highway="primary",
                ref="BR-test",
                source_id=100 + edge_id,
                municipality_codes=("2611606",),
            )
        )
    roads = RoadNetwork.from_records(nodes, edges, metric_crs=config.metric_crs)
    facilities = FacilityIndex(
        [
            snap_facility(Facility("a", "hub", *positions[10]), roads, admin),
            snap_facility(Facility("b", "producer", *positions[30]), roads, admin),
        ],
        metric_crs=config.metric_crs,
    )
    config.save(tmp_path / "config.json")
    admin.save(tmp_path)
    roads.save(tmp_path)
    facilities.save(tmp_path)
    outputs = [file.name for file in tmp_path.iterdir() if file.is_file()]
    manifest = WorldManifest.create(
        config,
        sources=(),
        output_dir=tmp_path,
        output_files=outputs,
        feature_counts={
            "municipalities": 2,
            "state_boundaries": 1,
            "road_nodes": 5,
            "road_edges": 4 + len(extra_edges),
            "facilities": 2,
        },
    )
    manifest.save(tmp_path / "manifest.json")
    return PernambucoWorld.load(tmp_path)


@pytest.fixture
def tiny_world(tmp_path):
    return _tiny_world(tmp_path)


@pytest.fixture
def two_way_world(tmp_path):
    """Tiny world whose 10-20-30 corridor is also drivable back towards 10."""
    return _tiny_world(tmp_path, extra_edges=[(5, 20, 10, 10), (6, 30, 20, 10)])
