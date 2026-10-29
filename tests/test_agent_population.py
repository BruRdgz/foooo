"""Generic Mesa agents keep geography queries delegated to the shared world."""

import importlib.util

import pytest

if importlib.util.find_spec("mesa") is None:
    pytest.skip("Requires the optional simulation extra", allow_module_level=True)

from simgeope.simulation import (
    AgentModel,
    BirdAgent,
    FarmAgent,
    FleetSpec,
    HubAgent,
    ModelParams,
    TruckAgent,
)
from simgeope.simulation.agents import decayed_fomite_load
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


def test_truck_starts_at_registered_hub_and_rejects_foreign_stops(tiny_world):
    model = AgentModel(tiny_world)
    hub = model.add_hub(Facility("mill", "feed_mill", -35.0, -8.0))
    farm = model.add_farm(Facility("farm", "producer", -34.98, -8.0))
    truck = model.add_truck("truck", hub, [farm])
    other_model = AgentModel(tiny_world)
    foreign_hub = other_model.add_hub(Facility("foreign", "feed_mill", -35.0, -8.0))

    assert isinstance(hub, HubAgent)
    assert isinstance(truck, TruckAgent)
    assert hub.road_node == 10
    assert truck.position is hub
    assert (truck.lon, truck.lat) == hub.location
    assert truck in model.agents_by_type[TruckAgent]

    before = (_ids(model), dict(model.agents_by_id))
    for base, clients in ((foreign_hub, []), (farm, []), (hub, [hub]), (object(), [])):
        with pytest.raises((TypeError, ValueError)):
            model.add_truck("bad-truck", base, clients)
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
    model.add_farm(Facility("shared", "producer", -35.0, -8.0))
    hub = model.add_hub(Facility("hub", "feed_mill", -35.0, -8.0))
    before = (_ids(model), dict(model.agents_by_id), tuple(model.agents))

    with pytest.raises((TypeError, ValueError)):
        model.add_bird("shared", (-32.4, -3.85))
    with pytest.raises((TypeError, ValueError)):
        model.add_truck("shared", hub)
    with pytest.raises(NoRoadNearbyError):
        model.add_farm(Facility("island-farm", "producer", -32.35, -3.8))

    assert (_ids(model), dict(model.agents_by_id), tuple(model.agents)) == before
    assert set(model.agents_by_id) == {"shared", "hub"}


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


def _fleet_model(world, *, max_stops, shift_s, **params):
    """One feed mill at node 10 serving farms at nodes 20 and 30 (10s apart)."""
    model = AgentModel(
        world,
        params=ModelParams(
            fleets=(
                FleetSpec("feed_mill", trucks_per_hub=1, max_stops_per_day=max_stops),
            ),
            truck_shift_hours=shift_s / 3600,
            truck_service_minutes=0,
            **params,
        ),
        seed=1,
    )
    model.add_hub(Facility("mill", "feed_mill", -35.0, -8.0))
    model.add_farm(Facility("f1", "farm", -34.99, -8.0), pop_total=100)
    model.add_farm(Facility("f2", "farm", -34.98, -8.0), pop_total=100)
    assert model.setup_fleets() == 1
    return model


@pytest.mark.parametrize(
    "max_stops,shift_s,quarantined,expected_days",
    [
        # Both fit in one shift: f1 (10s), f2 (10s), back to the mill (20s).
        (2, 3600, (), [[("f1", 10), ("f2", 10), ("mill", 20)]] * 2),
        # One stop per day rotates through the client list.
        (1, 3600, (), [[("f1", 10), ("mill", 10)], [("f2", 20), ("mill", 20)]]),
        # The f2 round trip (40s) exceeds a 35s shift: f2 is never assigned.
        (2, 35, (), [[("f1", 10), ("mill", 10)]] * 2),
        # Quarantined farms are skipped without spending shift time.
        (2, 3600, ("f1",), [[("f2", 20), ("mill", 20)]] * 2),
    ],
)
def test_truck_tours_follow_rotation_shift_and_quarantine(
    two_way_world, max_stops, shift_s, quarantined, expected_days
):
    model = _fleet_model(two_way_world, max_stops=max_stops, shift_s=shift_s)
    for farm in model.farms:
        farm.quarantined = farm.farm_id in quarantined
    (truck,) = model.trucks

    days = []
    for _ in expected_days:
        start = len(model.movements)
        truck.step()
        days.append(
            [(m.destination_id, m.travel_time_s) for m in model.movements[start:]]
        )

    assert days == expected_days
    assert truck.position is truck.base


@pytest.mark.parametrize(
    "biosecurity,disinfection_prob,introduced,truck_load_after",
    [
        (0, 0.0, True, 1.0),
        (0, 1.0, True, 0.0),  # disinfection at the base clears the truck only
        (100, 0.0, False, 1.0),  # full farm biosecurity blocks introduction
    ],
)
def test_fomite_carried_from_infected_farm_to_next_farm_and_base(
    two_way_world, biosecurity, disinfection_prob, introduced, truck_load_after
):
    model = _fleet_model(
        two_way_world,
        max_stops=2,
        shift_s=3600,
        infection_prob_truck_to_farm=100.0,
        truck_disinfection_prob=disinfection_prob,
        hub_transfer_fraction=1.0,
    )
    source, target = model.farms
    source.susceptible, source.infected = 0.0, 100.0
    target.biosecurity = biosecurity
    (truck,) = model.trucks

    truck.step()

    arrivals = {m.destination_id: m for m in model.movements}
    assert arrivals["f1"].load_on_arrival == 0.0
    assert arrivals["f2"].load_on_arrival == 1.0
    assert arrivals["f2"].introduced_infection is introduced
    assert (target.exposed > 0) is introduced
    assert truck.base.load == 1.0
    assert truck.load == truck_load_after


@pytest.mark.parametrize("survival_days", [1, 5, 14])
def test_full_fomite_load_survives_exactly_survival_days(survival_days):
    load = 1.0
    for _ in range(survival_days):
        load = decayed_fomite_load(load, survival_days)
        assert load > 0
    assert decayed_fomite_load(load, survival_days) == 0.0


def test_fleet_setup_assigns_nearest_hub_and_reports_unreachable_farms(
    two_way_world,
):
    model = AgentModel(
        two_way_world,
        params=ModelParams(
            fleets=(
                FleetSpec("slaughterhouse", trucks_per_hub=2, max_stops_per_day=1),
            ),
            truck_shift_hours=30 / 3600,
            truck_service_minutes=0,
        ),
    )
    west = model.add_hub(Facility("west", "slaughterhouse", -35.0, -8.0))
    east = model.add_hub(Facility("east", "slaughterhouse", -34.98, -8.0))
    model.add_farm(Facility("f1", "farm", -34.99, -8.0))
    # Node 40 is an 80s round trip from either hub: beyond the 30s shift.
    model.add_farm(Facility("f4", "farm", -34.99, -8.01))
    model.add_hub(Facility("unused", "market", -35.0, -8.0))

    # f1 is 20s round trip from both hubs; the tie goes to the lower hub ID.
    assert model.setup_fleets() == 1
    (truck,) = model.trucks
    assert truck.base is east
    assert [farm.farm_id for farm in truck.clients] == ["f1"]
    assert west.hub_id not in {t.base.hub_id for t in model.trucks}
