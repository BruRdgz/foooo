"""Generic Mesa agents keep geography queries delegated to the shared world."""

import importlib.util

import pytest

if importlib.util.find_spec("mesa") is None:
    pytest.skip("Requires the optional simulation extra", allow_module_level=True)

from simgeope.simulation import AgentModel, BirdAgent, FarmAgent, TruckAgent
from simgeope.world.exceptions import NoRoadNearbyError
from simgeope.world.facilities import Facility


def _ids(model):
    return {agent.unique_id for agent in model.agents}


def test_farm_agents_share_world_and_keep_snapped_facility_local(tiny_world):
    original_facilities = tiny_world.facilities.facilities
    original_a = tiny_world.facilities.get("a")
    model = AgentModel(tiny_world, seed=42)

    farm = model.add_farm(Facility("farm-1", "producer", -35.0, -8.0), pop_total=12)

    assert isinstance(farm, FarmAgent)
    assert isinstance(model, AgentModel)
    assert model.world is tiny_world
    assert farm.model is model
    assert farm.facility.road_node == 10
    assert farm.road_node == 10
    assert (farm.lon, farm.lat) == (-35.0, -8.0)
    assert farm.pop_total == 12
    assert tiny_world.municipality_at(farm).code == "2611606"
    assert tiny_world.route(farm, "b").travel_time_s == 20
    assert tiny_world.facilities.facilities is original_facilities
    assert tiny_world.facilities.get("a") is original_a
    assert "farm-1" not in {
        facility.id for facility in tiny_world.facilities.facilities
    }
    assert farm in model.agents
    assert farm in model.agents_by_type[FarmAgent]
    assert model.agents_by_id == {"farm-1": farm}


def test_farm_resnaps_stale_road_node_and_does_not_activate_on_step(tiny_world):
    model = AgentModel(tiny_world)
    stale = Facility("farm", "producer", -34.98, -8.0, road_node=999)
    farm = model.add_farm(stale, pop_total=7)
    before = (farm.location, farm.pop_total)

    assert farm.road_node == 30
    assert farm.location == (-34.98, -8.0)
    model.step()
    assert model.steps == 1
    assert (farm.location, farm.pop_total) == before


def test_bird_keeps_offroad_island_location_without_snapping(tiny_world):
    model = AgentModel(tiny_world)
    bird = model.add_bird("bird-1", (-32.4, -3.85))

    assert isinstance(bird, BirdAgent)
    assert bird.location == (-32.4, -3.85)
    assert (bird.lon, bird.lat) == bird.location
    assert not hasattr(bird, "road_node") or bird.road_node is None
    assert tiny_world.municipality_at(bird).code == "2605459"
    assert bird in model.agents_by_type[BirdAgent]


def test_truck_location_follows_registered_farm_and_foreign_farms_are_rejected(
    tiny_world,
):
    model = AgentModel(tiny_world)
    farm = model.add_farm(Facility("farm", "producer", -35.0, -8.0))
    truck = model.add_truck("truck", at_farm=farm)
    other_model = AgentModel(tiny_world)
    foreign_farm = other_model.add_farm(Facility("foreign", "producer", -34.98, -8.0))

    assert isinstance(truck, TruckAgent)
    assert truck.at_farm is farm
    assert truck.location == farm.location
    assert (truck.lon, truck.lat) == (farm.lon, farm.lat)
    assert truck in model.agents_by_type[TruckAgent]

    before = (_ids(model), dict(model.agents_by_id))
    for invalid_farm in (foreign_farm, object()):
        with pytest.raises((TypeError, ValueError)):
            model.add_truck("bad-truck", at_farm=invalid_farm)
        assert (_ids(model), dict(model.agents_by_id)) == before


@pytest.mark.parametrize(
    "factory,args",
    [
        ("add_bird", ("", (0, 0))),
        ("add_bird", ("bad", (0, 91))),
    ],
)
def test_invalid_inputs_leave_model_catalog_and_mesa_registry_unchanged(
    tiny_world, factory, args
):
    model = AgentModel(tiny_world)
    before = (_ids(model), dict(model.agents_by_id), tuple(model.agents))

    with pytest.raises((TypeError, ValueError)):
        getattr(model, factory)(*args)

    assert (_ids(model), dict(model.agents_by_id), tuple(model.agents)) == before


def test_duplicate_ids_across_types_and_no_road_failure_are_atomic(tiny_world):
    model = AgentModel(tiny_world)
    farm = model.add_farm(Facility("shared", "producer", -35.0, -8.0))
    before = (_ids(model), dict(model.agents_by_id), tuple(model.agents))

    with pytest.raises((TypeError, ValueError)):
        model.add_bird("shared", (-32.4, -3.85))
    with pytest.raises((TypeError, ValueError)):
        model.add_truck("shared", at_farm=farm)
    with pytest.raises(NoRoadNearbyError):
        model.add_farm(Facility("island-farm", "producer", -32.35, -3.8))

    assert (_ids(model), dict(model.agents_by_id), tuple(model.agents)) == before
    assert set(model.agents_by_id) == {"shared"}


@pytest.mark.parametrize(
    "factory,args,kwargs",
    [
        ("add_farm", (Facility("farm", "producer", -35, -8),), {"pop_total": -1}),
        ("add_bird", ("bird", (181, 0)), {}),
        ("add_bird", ("bird", (0, float("nan"))), {}),
    ],
)
def test_invalid_factory_arguments_do_not_register_agents(
    tiny_world, factory, args, kwargs
):
    model = AgentModel(tiny_world)
    before = (_ids(model), dict(model.agents_by_id), tuple(model.agents))
    with pytest.raises((TypeError, ValueError)):
        getattr(model, factory)(*args, **kwargs)
    assert (_ids(model), dict(model.agents_by_id), tuple(model.agents)) == before


def test_invalid_farm_population_does_not_register_agent(tiny_world):
    model = AgentModel(tiny_world)
    before = (_ids(model), dict(model.agents_by_id), tuple(model.agents))
    with pytest.raises((TypeError, ValueError)):
        model.add_farm(Facility("farm", "producer", -35, -8), pop_total=-1)
    assert (_ids(model), dict(model.agents_by_id), tuple(model.agents)) == before


def test_step_is_bookkeeping_only_for_all_agent_types(tiny_world):
    model = AgentModel(tiny_world)
    farm = model.add_farm(Facility("farm", "producer", -35, -8), pop_total=9)
    bird = model.add_bird("bird", (-32.4, -3.85))
    truck = model.add_truck("truck", at_farm=farm)
    before = {
        "farm": (farm.location, farm.pop_total),
        "bird": bird.location,
        "truck": (truck.location, truck.at_farm),
    }

    model.step()

    assert model.steps == 1
    assert (farm.location, farm.pop_total) == before["farm"]
    assert bird.location == before["bird"]
    assert (truck.location, truck.at_farm) == before["truck"]
