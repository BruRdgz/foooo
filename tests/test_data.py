"""Pinned-input smoke invariants; full road tests use an explicitly built world."""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from simgeope import PernambucoWorld, WorldConfig
from simgeope.world.exceptions import NoRoadNearbyError, RouteNotFoundError
from simgeope.world.roads import RoadRuntimeState
from simgeope.world.sources.ibge import IBGESource

ROOT = Path(__file__).parents[1]
RECIFE = (-34.88, -8.05)
CARUARU = (-35.97, -8.28)
PETROLINA = (-40.50, -9.39)
NORONHA = (-32.42, -3.85)
pytestmark = pytest.mark.data


def local_config():
    config = WorldConfig.load(ROOT / "config/world-local.json")
    for field in ("ibge_state", "ibge_municipalities", "ibge_names", "osm_pbf"):
        path = ROOT / getattr(config, field)
        if not path.is_file():
            pytest.skip(f"Pinned source unavailable: {path}")
        config = replace(config, **{field: str(path)})
    return config


def test_pinned_ibge_queries_and_validity():
    administrative = IBGESource.from_config(local_config()).load()
    assert len(administrative.municipalities) == 185
    for point in (RECIFE, CARUARU, PETROLINA):
        assert administrative.municipality_at(point).code.startswith("26")
    assert administrative.municipality_at(NORONHA).code == "2605459"
    assert administrative.state_geometry_wgs84.is_valid
    assert all(m.geometry_wgs84.is_valid for m in administrative.municipalities)


@pytest.fixture(scope="module")
def real_world():
    path = Path(os.environ.get("SIMGEOPE_WORLD", ROOT / "data/world/pe_v1"))
    if not (path / "manifest.json").is_file():
        pytest.skip(
            "Build pinned roads first, then set SIMGEOPE_WORLD or use data/world/pe_v1"
        )
    return PernambucoWorld.load(path)


def test_real_mainland_routes_and_overlay(real_world):
    world = real_world
    assert len(world.roads.nodes) > 100
    assert len(world.roads.edges) > 100
    for origin, destination in (
        (RECIFE, CARUARU),
        (CARUARU, PETROLINA),
    ):
        route = world.route(origin, destination)
        assert route.travel_time_s > 0
        assert route.distance_m > 0
        assert route.edge_ids
        assert all(world.roads.edge(edge).length_m > 0 for edge in route.edge_ids)
    route = world.route(RECIFE, CARUARU)
    state = RoadRuntimeState(closed_edges={route.edge_ids[len(route.edge_ids) // 2]})
    try:
        alternative = world.route(RECIFE, CARUARU, state=state)
    except RouteNotFoundError:
        pass
    else:
        assert not state.closed_edges.intersection(alternative.edge_ids)
        assert alternative.edge_ids != route.edge_ids
    assert world.route(RECIFE, CARUARU) == route
    assert all(world.roads.geometry(edge.id).is_valid for edge in world.roads.edges)


def test_real_island_never_snaps_to_mainland(real_world):
    assert real_world.municipality_at(NORONHA).code == "2605459"
    try:
        node = real_world.roads.nearest_node(
            NORONHA, max_distance_m=real_world.config.max_snap_distance_m
        )
    except NoRoadNearbyError:
        return  # No selected logistics roads on island is an explicit, valid absence.
    with pytest.raises(RouteNotFoundError):
        real_world.route(real_world.roads.nearest_node(RECIFE), node)


def test_real_hydrology_provenance_queries_and_crossings(real_world):
    from shapely.geometry import Point

    hydro = real_world.hydro
    if hydro is None:
        pytest.skip("Set SIMGEOPE_WORLD to a built Phase 4 world")
    assert hydro.waterways and hydro.waterbodies and hydro.coastline
    assert any(
        "capibaribe" in (feature.name or "").casefold() for feature in hydro.waterways
    )
    assert 0 <= hydro.distance_to_water(RECIFE) < 20_000
    assert hydro.nearest_waterway(RECIFE).kind in {"river", "canal"}
    assert hydro.nearest_waterbody(RECIFE) is not None
    assert hydro.basin_at(RECIFE) is None
    assert not hydro.basins
    assert any(
        feature.geometry_wgs84.distance(Point(NORONHA)) < 0.2
        for feature in hydro.coastline
    )
    for feature in hydro.features:
        assert feature.geometry_wgs84.is_valid
        assert feature.source == "osm"
        assert feature.source_id.startswith(("way/", "relation/"))
        assert feature.source_file == "nordeste-260930.osm.pbf"
        assert feature.source_date == "2026-09-30"
        assert feature.kind != "stream_major"
    assert hydro.crossings
    for crossing in hydro.crossings[:100]:
        point = Point(
            real_world.spatial.project(
                (crossing.intersection_lon, crossing.intersection_lat)
            )
        )
        road = real_world.roads.edge(crossing.road_edge_id)
        assert crossing.bridge_tag == road.bridge
        assert (
            real_world.spatial.geometry_to_metric(
                real_world.roads.geometry(road.id)
            ).distance(point)
            < 1e-4
        )
        assert crossing in hydro.intersections_with_road(road.id)


def test_real_environment_sampling_and_centre_invariants(real_world):
    import numpy as np
    from shapely.geometry import Point

    from simgeope.world.exceptions import PointOutsideWorldError

    store = real_world.environment
    if store is None:
        pytest.skip("Set SIMGEOPE_WORLD to a built Phase 5 world")
    assert store.grid.resolution_m == real_world.config.raster_resolution_m
    assert store.feature_counts["environment_state_cells"] > 90_000
    assert store.feature_counts == {
        name: real_world.manifest.feature_counts[name] for name in store.feature_counts
    }
    for point in (RECIFE, CARUARU, PETROLINA, NORONHA):
        sample = real_world.environment_at(point)
        assert sample["state_mask"]
        assert sample["municipality_code"] == real_world.municipality_at(point).code
        assert sample["water_distance_m"] >= 0
        assert 0 <= sample["wetland_fraction"] <= 1
        row, col = store.grid.index(*real_world.spatial.project(point))
        centre = real_world.spatial.unproject(store.grid.cell_center(row, col))
        assert sample["water_distance_m"] == pytest.approx(
            real_world.hydro.distance_to_water(centre), rel=1e-6, abs=0.05
        )
        assert sample["municipality_code"] == real_world.municipality_at(centre).code
        assert (
            abs(sample["water_distance_m"] - real_world.hydro.distance_to_water(point))
            <= store.grid.resolution_m / 2**0.5 + 0.1
        )
    unknown = np.argwhere(store.state_mask & (store.municipality_code == 0))
    assert (
        len(unknown)
        == store.feature_counts["environment_unassigned_municipality_cells"]
    )
    assert (
        len(unknown) > 0
    )  # Pinned IBGE state/municipal boundary mismatch remains explicit.
    centre = real_world.spatial.unproject(store.grid.cell_center(*map(int, unknown[0])))
    assert real_world.administrative.state_geometry_wgs84.covers(Point(centre))
    assert real_world.municipality_at(centre) is None
    assert store.at(centre)["municipality_code"] is None
    assert all(
        not array.flags.writeable
        for array in (
            store.state_mask,
            store.municipality_code,
            store.water_distance_m,
            store.wetland_fraction,
        )
    )
    with pytest.raises(PointOutsideWorldError):
        real_world.environment_at((-30, -5))
