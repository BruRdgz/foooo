"""End-to-end facade/runtime contracts across administrative and road layers."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from shapely.geometry import box

from simgeope.world.exceptions import (
    NoRoadNearbyError,
    RouteNotFoundError,
    WorldDataMissingError,
)
from simgeope.world.roads import RoadRuntimeState


def test_world_facade_persisted_snaps_and_overlay(tiny_world):
    world = tiny_world
    assert world.municipality_at("a").code == "2611606"
    assert world.municipality_at((-32.4, -3.85)).code == "2605459"
    assert world.nearest_road("a").distance_m < 1
    route = world.route("a", "b")
    assert route.travel_time_s == 20
    assert route.distance_m > 0
    alternative = world.route(
        world.facilities.get("a"),
        (-34.98, -8.0),
        state=RoadRuntimeState(closed_edges={1}),
    )
    assert alternative.edge_ids != route.edge_ids
    assert alternative.travel_time_s > route.travel_time_s
    assert world.route("a", "b") == route
    assert world.travel_time("a", "b") == route.travel_time_s
    assert world.distance("a", "b") == route.distance_m
    assert len(world.nearby_facilities("a", radius_m=5_000)) == 2
    assert len(world.facilities_within(box(-35.1, -8.1, -34.9, -7.9))) == 2
    assert world.roads_within(box(-35.1, -8.1, -34.9, -7.9))
    with pytest.raises(RouteNotFoundError):
        world.route(10, 50)
    with pytest.raises(RouteNotFoundError):
        world.route("a", "b", state=RoadRuntimeState(closed_edges={1, 3}))
    with pytest.raises(NoRoadNearbyError):
        world.nearest_road((-40, -9))
    with pytest.raises(WorldDataMissingError):
        world.environment_at((-35, -8))


def test_loaded_world_needs_no_build_libraries(tiny_world):
    script = """
import importlib.abc
import sys
class ForbidBuildImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"geopandas", "osmnx", "osmium", "networkx"}:
            raise ImportError("Build dependency imported at runtime: " + fullname)
sys.meta_path.insert(0, ForbidBuildImports())
from simgeope import PernambucoWorld
world = PernambucoWorld.load(sys.argv[1])
assert world.travel_time("a", "b") > 0
assert world.municipality_at((-32.4, -3.85)).code == "2605459"
"""
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src"))
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tiny_world.path)],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
