"""Wild-bird SIR flow, neighbourhood spread, outbreak end and demo scenario."""

import importlib.util

import pytest

if importlib.util.find_spec("mesa") is None:
    pytest.skip("Requires the optional simulation extra", allow_module_level=True)

from simgeope.simulation import AgentModel, ModelParams
from simgeope.simulation.agents import local_spread_prob
from simgeope.simulation.model import install_synthetic_facilities
from simgeope.world.facilities import Facility

# Birds stay put and never leave, and nobody arrives, unless a test says so.
STILL = dict(bird_step_m=0.0, bird_mean_stay_days=1e9, migratory_flocks_per_day=0)


def _model(world, **params):
    return AgentModel(world, params=ModelParams(**(STILL | params)), seed=3)


@pytest.mark.parametrize(
    "mortality,survives,state",
    [(100.0, False, None), (0.0, True, "R")],
)
def test_infected_bird_dies_or_recovers_after_infection(
    tiny_world, mortality, survives, state
):
    model = _model(tiny_world, bird_infection_duration=1, bird_mortality_pct=mortality)
    bird = model.add_bird("bird", (-35, -8))
    bird.infect()
    model.step()
    assert bird.bird_state == "I"
    model.step()
    assert (bird in model.birds) is survives
    assert model.wild_bird_deaths == (0 if survives else 1)
    if survives:
        assert bird.bird_state == state
        bird.infect()  # recovered birds are immune
        assert bird.bird_state == "R"


def test_birds_infect_neighbours_and_infected_farms_spill_back(tiny_world):
    model = _model(
        tiny_world, bird_to_bird_prob=100.0, bird_contact_km=1.0, farm_to_bird_prob=0.0
    )
    model.add_bird("source", (-34.995, -8)).infect()
    near = model.add_bird("near", (-35, -8))  # ~550 m away
    far = model.add_bird("far", (-34.98, -8.01))  # ~2 km away
    model.step()
    assert (near.bird_state, far.bird_state) == ("I", "S")

    spill = _model(tiny_world, bird_to_bird_prob=0.0, farm_to_bird_prob=100.0)
    farm = spill.add_farm(Facility("farm", "farm", -35, -8), pop_total=100)
    farm.susceptible, farm.infected = 0.0, 100.0
    bird = spill.add_bird("bird", (-34.99, -8))
    spill.step()
    assert bird.bird_state == "I"
    assert spill.wild_bird_infections == 1


def test_migratory_flocks_arrive_inside_state_and_depart(tiny_world):
    model = _model(
        tiny_world,
        migratory_flocks_per_day=5,
        flock_size_mean=4,
        migratory_infected_pct=100.0,
        bird_mean_stay_days=1.0,  # every bird leaves on its next step
    )
    model.step()
    arrived = list(model.birds)
    assert model.birds_arrived == len(arrived) > model.flocks_arrived > 0
    # Each infected flock carries exactly one infected index bird.
    assert sum(b.bird_state == "I" for b in arrived) == model.flocks_arrived
    assert all(tiny_world.municipality_at(b.location) is not None for b in arrived)
    assert len({b.bird_id for b in arrived}) == len(arrived)
    model.step()
    assert not set(arrived) & set(model.birds)


@pytest.mark.parametrize(
    "distance_m,biosecurity,expected",
    [
        (0, 0, 0.10),
        (1500, 0, 0.05),  # half way to the 3 km radius
        (0, 50, 0.05),
        (3000, 0, 0.0),
        (5000, 0, 0.0),
    ],
)
def test_local_spread_kernel(distance_m, biosecurity, expected):
    prob = local_spread_prob(
        ModelParams(local_spread_prob=10.0, local_spread_radius_km=3.0),
        shedding=1.0,
        distance_m=distance_m,
        biosecurity=biosecurity,
    )
    assert prob == pytest.approx(expected)


def test_shedding_farm_infects_neighbour_but_not_quarantined_one(tiny_world):
    model = _model(
        tiny_world,
        local_spread_prob=100.0,
        local_spread_radius_km=1000.0,
        base_biosecurity=0.0,
        infection_prob_bird_to_farm=0.0,
    )
    source = model.add_farm(Facility("source", "farm", -35, -8), pop_total=100)
    open_farm = model.add_farm(Facility("open", "farm", -34.99, -8), pop_total=100)
    closed = model.add_farm(Facility("closed", "farm", -34.98, -8), pop_total=100)
    source.susceptible, source.infected = 0.0, 100.0
    closed.start_quarantine()
    model.step()
    assert open_farm.exposed > 0
    assert closed.exposed == 0
    assert "INTRODUCAO_VIZINHANCA" in {kind for _, kind, _ in model.events}


def test_outbreak_tail_ends_and_quarantine_is_released(tiny_world):
    model = _model(tiny_world, quarantine_release_days=2)
    farm = model.add_farm(Facility("farm", "farm", -35, -8), pop_total=100)
    farm.biosecurity = 100.0  # no new exposures
    farm.susceptible, farm.exposed, farm.infected = 99.4, 0.3, 0.3
    farm.notified, farm.first_infection_day = True, 0
    farm.start_quarantine()

    model.step()
    assert (farm.exposed, farm.infected, farm.notified) == (0, 0, False)
    # The 0.6 birds in the tail either died this step or recovered.
    assert farm.recovered + farm.dead == pytest.approx(0.6)
    assert farm.status == "quarantined"
    model.step()
    assert farm.quarantined  # free since day 1: released on day 3
    model.step()
    assert farm.status == "healthy"
    kinds = [kind for _, kind, _ in model.events]
    assert kinds == ["FIM_INFECCAO", "LIBERACAO_QUARENTENA"]


def test_synthetic_poles_cluster_on_connected_roads(two_way_world):
    facilities = install_synthetic_facilities(
        two_way_world, n=20, hubs={"feed_mill": 1}, poles=("Recife",), seed=5
    ).facilities
    assert len(facilities) == 21
    # Node 50 (Noronha) is an isolated component and must never be used.
    assert {f.road_node for f in facilities} <= {10, 20, 30, 40}
    with pytest.raises(ValueError, match="Unknown pole"):
        install_synthetic_facilities(two_way_world, poles=("Atlântida",))
