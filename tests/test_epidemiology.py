"""Wild-bird SIR flow, neighbourhood spread, outbreak end and demo scenario."""

import importlib.util
import math

import pytest

if importlib.util.find_spec("mesa") is None:
    pytest.skip("Requires the optional simulation extra", allow_module_level=True)

from shapely.geometry import LineString, Point, box

from simgeope.simulation import AgentModel, ModelParams
from simgeope.simulation.agents import local_spread_prob
from simgeope.simulation.model import (
    FLOCK_SPREAD_M,
    MIGRATORY_ARRIVAL_PROFILE,
    MigratorySite,
    dominant_source,
    install_synthetic_facilities,
    load_migratory_sites,
)
from simgeope.world.facilities import Facility
from simgeope.world.hydro import HydroFeature, HydroLayer

# Birds stay put and never leave, and nobody arrives, unless a test says so.
STILL = dict(bird_step_m=0.0, bird_mean_stay_days=1e9, migratory_flocks_per_day=0)
RECIFE = "2611606"  # the tiny world's mainland municipality


def _model(world, **params):
    return AgentModel(world, params=ModelParams(**(STILL | params)), seed=3)


def _add_water(world):
    """A north-south river at lon -34.995 and a pond in the east of Recife."""
    world.hydro = HydroLayer(
        [
            HydroFeature(
                "river",
                "river",
                LineString([(-34.995, -8.015), (-34.995, -7.995)]),
                municipality_codes=(RECIFE,),
            ),
            HydroFeature(
                "pond",
                "pond",
                box(-34.985, -8.012, -34.975, -8.004),
                municipality_codes=(RECIFE,),
            ),
        ],
        world.config.metric_crs,
    )
    return world


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
        start_month=10,  # peak month: the full daily rate
        bird_to_bird_prob=0.0,  # only index birds are infected
    )
    model.step()
    arrived = list(model.birds)
    assert model.birds_arrived == len(arrived) > model.flocks_arrived > 0
    # Each infected flock carries exactly one infected index bird.
    assert sum(b.bird_state == "I" for b in arrived) == model.flocks_arrived
    assert all(tiny_world.municipality_at(b.location) is not None for b in arrived)
    assert len({b.bird_id for b in arrived}) == len(arrived)
    flocks = model.flock_arrivals
    assert len(flocks) == model.flocks_arrived
    assert sum(f.size for f in flocks) == model.birds_arrived
    assert all(f.day == 1 and f.infected for f in flocks)
    model.step()
    assert not set(arrived) & set(model.birds)


@pytest.mark.parametrize(
    "start_month,days,arrives",
    [
        # Day d is start_month's day d + 1: each run covers the rest of the month.
        (5, 30, False),
        (6, 29, False),
        (7, 30, False),
        (9, 1, True),
        (10, 1, True),
    ],
)
def test_migratory_arrivals_follow_the_monthly_profile(
    tiny_world, start_month, days, arrives
):
    # The slider is the peak-month rate.
    assert len(MIGRATORY_ARRIVAL_PROFILE) == 12
    assert max(MIGRATORY_ARRIVAL_PROFILE) == 1.0
    model = _model(tiny_world, migratory_flocks_per_day=20, start_month=start_month)
    model.run(days)
    assert model.current_date.month == start_month
    assert (model.flocks_arrived > 0) is arrives


def test_documented_sites_land_flocks_on_nearby_water_in_the_state(tiny_world):
    model = _model(
        _add_water(tiny_world),
        migratory_flocks_per_day=40,
        start_month=10,
        migratory_site_fraction=1.0,
    )
    model.migratory_sites = (
        MigratorySite("Lagoa", RECIFE, (-34.98, -8.008), 1.0),  # inside the pond
        MigratorySite("Margem", RECIFE, (-34.993, -8.0), 1.0),  # ~220 m off the river
        # ~3.9 km east of the pond, outside the state: lands at Recife's water.
        MigratorySite("Longe", RECIFE, (-34.94, -8.0), 1.0),
        MigratorySite("Sem coordenada", RECIFE, None, 1.0),
        MigratorySite("Fora do mundo", "2600054", None, 1e6),
    )
    model.step()
    by_site = {}
    for flock in model.flock_arrivals:
        by_site.setdefault(flock.site, []).append((flock.lon, flock.lat))
    assert set(by_site) == {"Lagoa", "Margem", "Longe", "Sem coordenada"}
    for site, anchor in [("Lagoa", (-34.98, -8.008)), ("Margem", (-34.995, -8.0))]:
        for lon, lat in by_site[site]:
            assert (lon, lat) == pytest.approx(anchor, abs=1e-6)
    for lon, lat in by_site["Longe"] + by_site["Sem coordenada"]:
        assert tiny_world.hydro.distance_to_water((lon, lat)) < 1.0
        assert tiny_world.municipality_at((lon, lat)).code == RECIFE
    state = tiny_world.administrative.state_geometry_wgs84
    landings = [(f.lon, f.lat) for f in model.flock_arrivals]
    assert model.birds
    for bird in model.birds:
        assert state.covers(Point(bird.location))
        # Flock members scatter FLOCK_SPREAD_M (1 sigma) around their landing.
        assert min(model.distance_m(bird.location, s) for s in landings) < (
            5 * FLOCK_SPREAD_M
        )
    warnings = " ".join(e.details for e in model.events if e.kind == "AVISO_AVES")
    assert "Fora do mundo" in warnings and "Longe" in warnings


def test_zero_site_fraction_keeps_size_weighted_arrivals(tiny_world):
    """Arrivals recorded before documented sites existed (same seed and world)."""
    params = ModelParams(
        **STILL
        | {
            "migratory_flocks_per_day": 4.0,
            "flock_size_mean": 3.0,
            "migratory_infected_pct": 50.0,
            "start_month": 10,  # profile 1: the pre-profile rate
            "migratory_site_fraction": 0.0,
        }
    )
    model = AgentModel(_add_water(tiny_world), params=params, seed=11)
    model.run(2)
    assert [tuple(f) for f in model.flock_arrivals] == [
        pytest.approx(expected)
        for expected in [
            (1, -34.97980580875277, -8.010421238901023, 3, True, None),
            (1, -34.97950647972904, -8.0042038289688, 1, True, None),
            (2, -34.98069996470407, -8.005669848537533, 5, True, None),
            (2, -34.97822757089576, -8.011496985177255, 3, True, None),
            (2, -34.97934421821516, -8.009454371660697, 3, False, None),
            (2, -34.99500001404442, -8.012935325742644, 4, True, None),
            (2, -34.98383508657752, -8.006434237442956, 2, False, None),
            (2, -34.97515566466907, -8.004681189362433, 1, True, None),
        ]
    ]


@pytest.mark.parametrize(
    "row,message",
    [
        ("Sítio,2611606,-34.9,,1", "together"),
        ("Sítio,2911606,,,1", "municipality code"),
        ("Sítio,2611606,,,0", "positive"),
        ("Sítio,2611606,-200,-8,1", "WGS84"),
    ],
)
def test_migratory_site_table_rejects_invalid_rows(tmp_path, row, message):
    assert load_migratory_sites()  # the shipped table is valid
    path = tmp_path / "sitios.csv"
    path.write_text(f"nome,codigo_ibge,lon,lat,peso\n{row}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=f"line 2: .*{message}"):
        load_migratory_sites(path)


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
    assert "INTRODUCAO_VIZINHANCA" in {kind for _, kind, _, _ in model.events}


@pytest.mark.parametrize(
    "hazards,expected",
    [
        ([0.1, 0.3], (1, 0.75)),
        ([0.2, 0.2], (0, 0.5)),  # ties go to the first source in farm order
        ([0.0, 0.4], (1, 1.0)),
        ([math.inf, 0.5, math.inf], (0, 0.5)),  # certain sources split the share
    ],
)
def test_dominant_source_and_share(hazards, expected):
    top, share = dominant_source(hazards)
    assert (top, share) == (expected[0], pytest.approx(expected[1]))


def test_local_introduction_attributed_to_dominant_neighbour(tiny_world):
    model = _model(
        tiny_world,
        local_spread_prob=100.0,
        local_spread_radius_km=3.0,
        seed_size_local=1,
    )
    far = model.add_farm(Facility("far", "farm", -34.98, -8), pop_total=100)
    near = model.add_farm(Facility("near", "farm", -34.995, -8), pop_total=100)
    target = model.add_farm(Facility("target", "farm", -35, -8), pop_total=100)
    for source in (far, near):
        source.susceptible, source.infected = 0.0, 100.0
    target.biosecurity = 0.0
    hazards = [
        -math.log1p(
            -local_spread_prob(
                model.params,
                shedding=1.0,
                distance_m=model.distance_m(source.location, target.location),
                biosecurity=0.0,
            )
        )
        for source in (far, near)
    ]
    # About 87% daily chance: allow a few days (the sources keep shedding fully).
    for _ in range(5):
        model.step()
        if model.transmissions:
            break

    (transmission,) = model.transmissions
    assert (transmission.farm_id, transmission.pathway) == ("target", "local")
    assert transmission.source_farm_id == "near"
    assert transmission.share == pytest.approx(hazards[1] / sum(hazards))
    assert 0.5 < transmission.share < 1


def test_bird_introduction_attributed_to_nearest_infected_bird(tiny_world):
    model = _model(tiny_world, infection_prob_bird_to_farm=100.0)
    farm = model.add_farm(Facility("farm", "farm", -35, -8), pop_total=100)
    model.add_bird("far", (-34.98, -8)).infect()  # listed first, ~2.2 km away
    model.add_bird("near", (-34.995, -8)).infect()  # ~550 m away
    model.add_bird("healthy", (-35, -8))
    model.step()

    (transmission,) = model.transmissions
    assert (transmission.farm_id, transmission.pathway) == (farm.farm_id, "bird")
    assert (transmission.bird_id, transmission.source_farm_id) == ("near", None)
    assert transmission.share is None


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
    kinds = [kind for _, kind, _, _ in model.events]
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


# Official protocol on a scale that fits the tiny world (farms ~1.1 km apart).
PROTOCOL = {
    "protocol_enabled": 1,
    "perifocus_radius_km": 0.5,
    "surveillance_radius_km": 1.0,
    "protection_radius_km": 1.5,
    "local_spread_prob": 0.0,
    "infection_prob_bird_to_farm": 0.0,
}


@pytest.mark.parametrize(
    "distances_m,expected",
    [
        ([0, 3000, 3000.5], [0, 0, 1]),  # boundaries belong to the inner ring
        ([9999, 10000, 10000.5], [1, 1, 2]),
        ([25000, 25000.5, 1e6], [2, 3, 3]),  # 3: beyond the protection radius
    ],
)
def test_ring_index_by_distance_from_focus(distances_m, expected):
    import numpy as np

    from simgeope.simulation.model import ring_index

    assert ring_index(np.array(distances_m), ModelParams()).tolist() == expected


@pytest.mark.parametrize(
    "confirmation_days,auto_depopulation,confirmed,depopulated",
    [
        (2, 1, 3, 3),  # notified on day 1
        (0, 1, 1, 1),
        (2, 0, 3, None),
    ],
)
def test_notified_farm_becomes_focus_after_confirmation_delay(
    tiny_world, confirmation_days, auto_depopulation, confirmed, depopulated
):
    from simgeope.simulation.model import Focus

    model = _model(
        tiny_world,
        **PROTOCOL,
        confirmation_days=confirmation_days,
        auto_depopulation=auto_depopulation,
        zone_release_days=28,
    )
    farm = model.add_farm(Facility("farm", "farm", -35, -8), pop_total=1000)
    # 1.1 km away: inside the 5 km manual stamping-out radius.
    neighbour = model.add_farm(Facility("near", "farm", -34.99, -8), pop_total=1000)
    farm.susceptible, farm.exposed = 900.0, 100.0
    farm.first_infection_day, farm.detection_day = 0, 1

    for day in range(1, confirmed + 2):
        model.step()
        assert bool(model.foci) is (day >= confirmed)

    release = None if depopulated is None else depopulated + 28
    assert model.foci == [Focus("farm", confirmed, depopulated, release)]
    assert farm.culled is (depopulated is not None)
    assert (farm.focus, farm.zone, neighbour.zone) == (True, "perifoco", "protecao")
    assert not neighbour.quarantined
    protocol_events = [
        (e.day, e.kind, e.farm_id)
        for e in model.events
        if e.kind
        in {
            "FOCO_CONFIRMADO",
            "DESPOVOAMENTO",
            "INTERDICAO_TRANSITO",
            "SACRIFICIO_SANITARIO",
            "QUARENTENA_PERIFERICA",
        }
    ]
    assert protocol_events == [
        (confirmed, "FOCO_CONFIRMADO", "farm"),
        *([(confirmed, "DESPOVOAMENTO", "farm")] if depopulated else []),
        (confirmed, "INTERDICAO_TRANSITO", "farm"),
    ]
    if depopulated:
        assert model.total_culled == pytest.approx(1000.0, rel=0.05)
        assert model.cumulative_cost >= model.total_culled * 8.0


def test_farm_zone_is_most_restrictive_ring_over_active_foci(tiny_world):
    from simgeope.simulation.model import Focus

    model = _model(tiny_world, **PROTOCOL)
    # Foci 2.2 km apart; distances to (a, b) in km in the comments.
    for focus_id, lon in (("a", -35.0), ("b", -34.98)):
        model.add_farm(Facility(focus_id, "farm", lon, -8), pop_total=100).culled = True
    expected = {
        (-34.99, -8.0): "protecao",  # 1.10 / 1.10
        (-34.9855, -8.0): "vigilancia",  # 1.60 (outside) / 0.61
        (-34.9925, -8.0): "vigilancia",  # 0.83 / 1.38 (protecao)
        (-34.996, -8.0): "perifoco",  # 0.44 / 1.76
        (-34.983, -8.0): "perifoco",  # 1.87 / 0.33
        (-35.0, -8.016): None,  # 1.77 / 2.81
    }
    targets = {
        location: model.add_farm(Facility(f"t{k}", "farm", *location), pop_total=100)
        for k, location in enumerate(expected)
    }
    released = Focus("b", 0, 0, 1)  # its area closed at the end of day 0
    model.foci = [Focus("a", 0, 0, 99), released]
    model.step()
    assert {loc: farm.zone for loc, farm in targets.items()} == {
        (-34.99, -8.0): "protecao",
        (-34.9855, -8.0): None,
        (-34.9925, -8.0): "vigilancia",
        (-34.996, -8.0): "perifoco",
        (-34.983, -8.0): None,
        (-35.0, -8.016): None,
    }
    released.release_day = 99
    model.step()
    assert {loc: farm.zone for loc, farm in targets.items()} == expected
    assert [f.farm_id for f in model.farms if f.focus] == ["a", "b"]


@pytest.mark.parametrize(
    "lon,infected_day,drawn_delay,detected_day,by_surveillance",
    [
        (-34.996, 1, 20, 4, True),  # perifoco: 3 days after entering
        (-34.996, 10, 20, 13, True),  # infected after entering: 3 days later
        (-34.9925, 1, 20, 8, True),  # vigilância: 7 days
        (-34.996, 1, 2, 3, False),  # the drawn day comes first
        (-34.99, 1, 6, 7, False),  # proteção: no active surveillance
    ],
)
def test_active_surveillance_caps_detection_in_perifoco_and_vigilancia(
    tiny_world, lon, infected_day, drawn_delay, detected_day, by_surveillance
):
    from simgeope.simulation.model import Focus

    model = _model(
        tiny_world,
        **PROTOCOL,
        perifocus_detection_days=3,
        surveillance_detection_days=7,
    )
    model.add_farm(Facility("focus", "farm", -35, -8), pop_total=100).culled = True
    farm = model.add_farm(Facility("farm", "farm", lon, -8), pop_total=1000)
    model.foci = [Focus("focus", 0, 0, 99)]
    model.step()  # the farm enters its ring at the end of day 1
    assert farm.zone_since == 1
    while model.day_count < infected_day:
        model.step()
    farm.seed_from_external(10, "INTRODUCAO_INICIAL")
    farm.detection_day = infected_day + drawn_delay

    while not farm.notified:
        model.step()

    assert model.day_count == detected_day
    surveillance = [
        (e.day, e.farm_id) for e in model.events if e.kind == "VIGILANCIA_ATIVA"
    ]
    assert surveillance == ([(detected_day, "farm")] if by_surveillance else [])


@pytest.mark.parametrize(
    "auto_depopulation,manual_cull_day,depopulated,release",
    [
        (1, None, 1, 3),  # zone_release_days after depopulation
        (0, None, None, 6),  # after the infection ends on day 4
        (0, 2, 2, 4),  # after a manual cull of the focus
    ],
)
def test_emergency_area_is_released_after_zone_release_days(
    tiny_world, auto_depopulation, manual_cull_day, depopulated, release
):
    model = _model(
        tiny_world,
        **PROTOCOL,
        confirmation_days=0,
        auto_depopulation=auto_depopulation,
        zone_release_days=2,
    )
    farm = model.add_farm(Facility("farm", "farm", -35, -8), pop_total=100)
    neighbour = model.add_farm(Facility("near", "farm", -34.99, -8), pop_total=100)
    # 5 infected birds and no susceptibles: under one bird left on day 4.
    farm.susceptible, farm.infected = 0.0, 5.0
    farm.first_infection_day, farm.detection_day = 0, 1

    zones = []
    for day in range(1, 8):
        model.step()
        if day == manual_cull_day:
            farm.apply_stamping_out()
        zones.append(neighbour.zone)

    (focus,) = model.foci
    assert (focus.confirmed_day, focus.depopulated_day) == (1, depopulated)
    assert focus.release_day == release
    # Active on days 1 .. release - 1, by the zones set at the end of each day.
    assert zones == ["protecao"] * (release - 1) + [None] * (8 - release)
