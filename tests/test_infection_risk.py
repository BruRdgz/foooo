"""The risk layer applies the model's introduction rules to raster cells."""

import importlib.util

import numpy as np
import pytest

if importlib.util.find_spec("mesa") is None:
    pytest.skip("Requires the optional simulation extra", allow_module_level=True)

from simgeope.simulation import AgentModel, ModelParams, infection_risk
from simgeope.simulation.agents import TruckMovement
from simgeope.simulation.risk import GREEN, OUTSIDE, RED, YELLOW
from simgeope.world.exceptions import WorldDataMissingError
from simgeope.world.facilities import Facility
from simgeope.world.raster import EnvironmentStore, RasterGrid

NAN = float("nan")


@pytest.fixture
def risk_world(tiny_world):
    """One row of five 1km cells; column 0 is centred on (-35, -8)."""
    x, y = tiny_world.spatial.project((-35, -8))
    grid = RasterGrid(x - 500, y + 500, 1000, 1, 5, tiny_world.config.metric_crs)
    mask = np.array([[True, True, True, True, False]])
    tiny_world.environment = EnvironmentStore(
        grid,
        state_mask=mask,
        municipality_code=np.where(mask, 2611606, 0).astype(np.uint32),
        water_distance_m=np.array([[100, 5000, NAN, 5000, NAN]], dtype=np.float32),
        wetland_fraction=np.where(mask, 0.0, NAN).astype(np.float32),
    )
    return tiny_world


def _movement(day, destination, load):
    return TruckMovement(day, "truck", "mill", destination, 10.0, load, False)


def test_bird_and_fomite_pathways_per_cell(risk_world):
    params = ModelParams(
        infection_prob_bird_to_farm=15.0,
        bird_farm_proximity_km=1.2,
        farm_water_proximity_km=3.0,
        infection_prob_truck_to_farm=20.0,
        base_biosecurity=50.0,
    )
    model = AgentModel(risk_world, params=params)
    model.add_bird("sick", (-35, -8)).infect()
    model.add_bird("healthy", (-34.97, -8))  # susceptible birds carry no risk
    # Farm at -34.99 lies in column 1; f2 at -34.98 lies in column 2.
    model.add_farm(Facility("f1", "farm", -34.99, -8.0))
    model.add_farm(Facility("f2", "farm", -34.98, -8.0))
    model.movements += [
        _movement(-1, "f2", 1.0),  # yesterday: ignored
        _movement(0, "f1", 1.0),
        _movement(0, "f1", 1.0),
        _movement(0, "mill", 1.0),  # hub arrival: ignored
    ]

    risk = infection_risk(model)

    # Column 0: infected bird, near water (x1.5). Column 1: bird in range (1km),
    # far from water, plus two full-load truck visits at 10% each.
    np.testing.assert_allclose(risk.bird[0], [0.225, 0.15, 0, 0, NAN])
    np.testing.assert_allclose(risk.fomite[0], [0, 0.19, 0, 0, NAN])
    np.testing.assert_allclose(
        risk.total[0], [0.225, 1 - 0.85 * 0.81, 0, 0, NAN], rtol=1e-6
    )
    assert risk.levels(0.05, 0.25)[0].tolist() == [YELLOW, RED, GREEN, GREEN, OUTSIDE]
    with pytest.raises(ValueError):
        risk.levels(0.3, 0.2)


def test_risk_layer_requires_environment(tiny_world):
    with pytest.raises(WorldDataMissingError, match="environmental raster"):
        infection_risk(AgentModel(tiny_world))
