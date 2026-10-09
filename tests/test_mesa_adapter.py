"""Thin optional Mesa bridge and explicit consumer-owned activation/state."""

import importlib.util
import os
import runpy
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

if importlib.util.find_spec("mesa") is None:
    pytest.skip("Requires the optional simulation extra", allow_module_level=True)

from mesa import Agent, Model

from simgeope.mesa_adapter import WorldModel
from simgeope.world.exceptions import RouteNotFoundError, WorldDataMissingError
from simgeope.world.raster import EnvironmentStore, RasterGrid
from simgeope.world.roads import RoadRuntimeState

EXAMPLE = Path(__file__).parents[1] / "examples" / "mesa_world_model.py"


@pytest.fixture
def environment_world(tiny_world):
    """Attach one real, immutable synthetic raster cell around facility a."""
    x, y = tiny_world.spatial.project((-35, -8))
    grid = RasterGrid(x - 500, y + 500, 1000, 1, 1, tiny_world.config.metric_crs)
    tiny_world.environment = EnvironmentStore(
        grid,
        state_mask=np.array([[True]], dtype=bool),
        municipality_code=np.array([[2611606]], dtype=np.uint32),
        water_distance_m=np.array([[125]], dtype=np.float32),
        wetland_fraction=np.array([[0.25]], dtype=np.float32),
    )
    return tiny_world


def test_shared_world_and_one_mesa_step(tiny_world):
    class CounterAgent(Agent):
        def __init__(self, model):
            super().__init__(model)
            self.calls = 0

        def step(self):
            self.calls += 1

    first = WorldModel(tiny_world, seed=42)
    second = WorldModel(tiny_world, seed=42)
    agent = CounterAgent(first)
    assert isinstance(first, Model)
    assert first.world is second.world is tiny_world
    assert first.world.roads is tiny_world.roads
    assert first.random.random() == second.random.random()
    assert first.steps == second.steps == 0
    first.step()
    assert first.steps == 1
    assert second.steps == 0
    assert agent.calls == 0
    first.agents.do("step")
    assert agent.calls == 1


def test_delegated_routing_and_simulation_owned_overlay(tiny_world):
    model = WorldModel(tiny_world)
    other = WorldModel(tiny_world)
    baseline = tiny_world.route("a", "b")
    state = RoadRuntimeState(closed_edges={1}, cost_multiplier={3: 2})
    changed = model.route("a", "b", state=state)
    assert changed.edge_ids == (3, 4)
    assert changed.travel_time_s == 90
    assert model.travel_time("a", "b", state=state) == 90
    assert model.route("a", "b") == other.route("a", "b") == baseline
    model.step()
    assert model.route("a", "b") == baseline
    assert state.closed_edges == frozenset({1})
    assert dict(state.cost_multiplier) == {3: 2}
    with pytest.raises(RouteNotFoundError):
        model.route("a", "b", state=RoadRuntimeState(closed_edges={1, 3}))


def test_environment_delegation(environment_world):
    model = WorldModel(environment_world)
    sample = model.environment_at("a")
    assert sample == environment_world.environment_at("a")
    assert sample["municipality_code"] == "2611606"
    assert sample["water_distance_m"] == 125
    assert sample["wetland_fraction"] == 0.25
    assert model.world.environment is environment_world.environment
    assert not environment_world.environment.state_mask.flags.writeable


def test_phase3_missing_environment_is_explicit(tiny_world):
    model = WorldModel(tiny_world)
    with pytest.raises(WorldDataMissingError, match="no environmental raster"):
        model.environment_at("a")
    assert model.route("a", "b").travel_time_s == 20


def test_example_agent_queries_and_handles_missing_route(environment_world):
    agent_class = runpy.run_path(str(EXAMPLE))["LocationAgent"]
    model = WorldModel(environment_world)
    connected = agent_class(model, "a", destination="b")
    disconnected = agent_class(model, (-35, -8), destination=50)
    model.step()
    model.agents.do("step")
    assert model.steps == 1
    assert connected.municipality.code == "2611606"
    assert connected.environment["water_distance_m"] == 125
    assert connected.route.travel_time_s == 20
    assert connected.route_error is None
    assert disconnected.route is None
    assert "No directed road route" in disconnected.route_error


def test_example_reports_phase3_missing_environment(tiny_world):
    completed = subprocess.run(
        [
            sys.executable,
            str(EXAMPLE),
            str(tiny_world.path),
            "--facility",
            "a",
            "--destination",
            "b",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Mesa steps: 1" in completed.stdout
    assert "no environmental raster" in completed.stdout
    assert "travel_time_s=20" in completed.stdout


def test_core_runtime_does_not_require_mesa(tiny_world):
    script = """
import importlib.abc
import sys
class ForbidMesa(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] == 'mesa':
            raise ModuleNotFoundError('Mesa is unavailable', name='mesa')
sys.meta_path.insert(0, ForbidMesa())
from simgeope import PernambucoWorld
world = PernambucoWorld.load(sys.argv[1])
assert world.travel_time('a', 'b') == 20
assert 'mesa' not in sys.modules
try:
    from simgeope.mesa_adapter import WorldModel
except ImportError as exc:
    assert 'simulation extra' in str(exc)
else:
    raise AssertionError('Adapter must require the optional Mesa installation')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tiny_world.path)],
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src")),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
