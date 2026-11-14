"""Wild-bird SIR flow, neighbourhood spread, outbreak end and demo scenario."""

import csv
import importlib.util
import math

import pytest

if importlib.util.find_spec("mesa") is None:
    pytest.skip("Requires the optional simulation extra", allow_module_level=True)

from shapely.geometry import LineString, Point, box

from simgeope.simulation import AgentModel, FleetSpec, ModelParams
from simgeope.simulation.agents import (
    BROILER_FARM_KIND,
    EGG_HUB_KIND,
    LAYER_FARM_KIND,
    local_spread_prob,
)
from simgeope.simulation.model import (
    ADAGRO_REGISTERED_FARMS,
    FLOCK_SPREAD_M,
    MIGRATORY_ARRIVAL_PROFILE,
    PPM_BROILER_PROXY_2024,
    REFERENCE_TABLE,
    MigratorySite,
    dominant_source,
    install_official_facilities,
    install_synthetic_facilities,
    load_migratory_sites,
    reference_farm_weights,
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


RECIFE, NORONHA = "2611606", "2605459"
# Layers only in Recife, broilers only in Noronha, so every site reveals
# which kind's weights drew it.
SPLIT_WEIGHTS = {
    LAYER_FARM_KIND: {RECIFE: 5, NORONHA: 0},
    BROILER_FARM_KIND: {RECIFE: 0, NORONHA: 2},
}
OFFICIAL_FLEETS = (
    FleetSpec("feed_mill", 1, 1),
    FleetSpec("slaughterhouse", 1, 1, client_kinds=(BROILER_FARM_KIND, "farm")),
    FleetSpec(EGG_HUB_KIND, 1, 1, client_kinds=(LAYER_FARM_KIND,)),
)


def test_official_placement_draws_sites_by_kind_and_served_production(tiny_world):
    # Without roads nothing is snapped, so Noronha is a valid site too.
    tiny_world.roads = None
    facilities = install_official_facilities(
        tiny_world,
        OFFICIAL_FLEETS,
        farms={LAYER_FARM_KIND: 4, BROILER_FARM_KIND: 3},
        hubs_per_fleet=2,
        weights=SPLIT_WEIGHTS,
        seed=1,
    ).facilities
    sites = {(f.id, f.municipality_code) for f in facilities}
    assert {(f"P{i:04d}", RECIFE) for i in range(1, 5)} <= sites
    assert {(f"C{i:04d}", NORONHA) for i in range(1, 4)} <= sites
    # Hubs follow the production their fleet serves; a new hub kind too.
    assert {(f"{EGG_HUB_KIND}-{i:02d}", RECIFE) for i in (1, 2)} <= sites
    assert {(f"slaughterhouse-{i:02d}", NORONHA) for i in (1, 2)} <= sites
    assert sum(f.kind == "feed_mill" for f in facilities) == 2
    assert len(facilities) == 13


def test_official_placement_uses_connected_roads(two_way_world):
    facilities = install_official_facilities(
        two_way_world,
        OFFICIAL_FLEETS,
        farms={LAYER_FARM_KIND: 3, BROILER_FARM_KIND: 3},
        weights={LAYER_FARM_KIND: {RECIFE: 1}, BROILER_FARM_KIND: {RECIFE: 1}},
        seed=2,
    ).facilities
    assert len(facilities) == 12
    assert {f.road_node for f in facilities} <= {10, 20, 30, 40}


@pytest.mark.parametrize(
    "fleets,farms,weights,message",
    [
        # The default reference table names municipalities the tiny world lacks.
        (OFFICIAL_FLEETS, None, None, "World lacks"),
        (
            (FleetSpec("slaughterhouse", 1, 1, client_kinds=("farm",)),),
            None,
            SPLIT_WEIGHTS,
            "serve none",
        ),
        (OFFICIAL_FLEETS, {"farm": 1}, SPLIT_WEIGHTS, "No municipal weights"),
        (
            OFFICIAL_FLEETS,
            None,
            {LAYER_FARM_KIND: {RECIFE: 0}, BROILER_FARM_KIND: {RECIFE: 1}},
            "positive weight",
        ),
    ],
)
def test_official_placement_rejects_data_that_does_not_fit(
    tiny_world, fleets, farms, weights, message
):
    with pytest.raises(ValueError, match=message):
        install_official_facilities(
            tiny_world,
            fleets,
            farms=farms or {LAYER_FARM_KIND: 1, BROILER_FARM_KIND: 1},
            weights=weights,
        )
    assert {f.id for f in tiny_world.facilities.facilities} == {"a", "b"}


def test_reference_table_matches_cited_totals():
    weights = reference_farm_weights()
    with REFERENCE_TABLE.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    codes = [row["codigo_ibge"] for row in rows]
    assert len(set(codes)) == len(codes) == 185
    assert all(len(code) == 7 and code.startswith("26") for code in codes)
    assert sum(weights[BROILER_FARM_KIND].values()) == PPM_BROILER_PROXY_2024
    assert sum(int(row["granjas_adagro_2022"]) for row in rows) == (
        ADAGRO_REGISTERED_FARMS
    )
    for row in rows:
        assert int(row["galinaceos_2024"]) == int(row["galinhas_2024"]) + int(
            row["corte_proxy_2024"]
        )


@pytest.mark.parametrize(
    "pop_total,expected",
    [(7, 7), ({"producer": 9, "farm_layer": 1}, 9)],
)
def test_farm_population_per_kind(tiny_world, pop_total, expected):
    model = _model(tiny_world)
    assert model.add_farms_from_world(pop_total=pop_total) == 1
    assert model.farm_by_id("b").pop_total == expected


def test_farm_population_missing_kind_adds_no_farm(tiny_world):
    model = _model(tiny_world)
    with pytest.raises(ValueError, match="producer"):
        model.add_farms_from_world(pop_total={"farm": 9})
    assert model.farms == []


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
    "lon,infected_day,seed,drawn_delay,detected_day,by_surveillance",
    [
        (-34.996, 1, 10, 20, 4, True),  # perifoco: visits on days 4, 7, 10...
        (-34.996, 10, 10, 20, 13, True),  # infected after entering: next visit
        # Only 0.5 infectious birds at the day-4 visit (the rest incubating):
        # found at the next visit, with 1.3.
        (-34.996, 3, 2, 20, 7, True),
        (-34.9925, 1, 10, 20, 8, True),  # vigilância: every 7 days
        (-34.996, 1, 10, 2, 3, False),  # the drawn day comes first
        (-34.99, 1, 10, 6, 7, False),  # proteção: no active surveillance
    ],
)
def test_periodic_active_surveillance_in_perifoco_and_vigilancia(
    tiny_world, lon, infected_day, seed, drawn_delay, detected_day, by_surveillance
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
    farm.seed_from_external(seed, "INTRODUCAO_INICIAL")
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


@pytest.mark.parametrize(
    "notification,pct,window,housed,notified_day",
    [
        # Deaths per day: 90, 60, 40, 26.7... (300 infectious, none exposed).
        (1, 10.0, 3, 1000, 2),  # 90 + 60 >= 100
        (1, 5.0, 1, 1000, 1),
        (1, 10.0, 1, 1000, 5),  # no single day reaches 100: drawn day
        (1, 19.0, 2, 1000, 5),  # 190 dead by day 3, but never within 2 days
        (1, 10.0, 3, 500, 1),  # the threshold is a share of the housed flock
        (0, 5.0, 1, 1000, 5),  # rule off
    ],
)
def test_mortality_over_window_notifies_before_drawn_day(
    tiny_world, notification, pct, window, housed, notified_day
):
    model = _model(
        tiny_world,
        mortality_notification=notification,
        mortality_alert_pct=pct,
        mortality_alert_window_days=window,
        mortality_rate=90.0,
        infectious_period_days=3.0,
    )
    farm = model.add_farm(Facility("farm", "farm", -35, -8), pop_total=1000)
    farm.biosecurity = 100.0  # no new exposures
    farm.housed = housed
    farm.susceptible, farm.infected = housed - 300.0, 300.0
    farm.first_infection_day, farm.detection_day = 0, 5

    while not farm.notified:
        model.step()

    assert model.day_count == farm.detection_day == notified_day
    (detection,) = [e for e in model.events if e.kind == "DETECCAO"]
    assert ("mortalidade" in detection.details) is (notified_day < 5)


@pytest.mark.parametrize(
    "protocol,statuses,housed",
    [
        # Culled on day 1; 2-day fallow, then 20% for 3 days, then full.
        (
            1,
            ["culled"] * 2 + ["restocked"] * 3 + ["healthy"] * 2,
            [100, 100, 20, 20, 20, 100, 100],
        ),
        (0, ["culled"] * 7, [100] * 7),  # without the protocol a cull is final
    ],
)
def test_culled_farm_is_restocked_after_fallow_then_filled(
    tiny_world, protocol, statuses, housed
):
    model = _model(
        tiny_world,
        **PROTOCOL | {"protocol_enabled": protocol},
        fallow_days=2,
        restock_pct=20.0,
        restock_monitoring_days=3,
    )
    farm = model.add_farm(Facility("farm", "farm", -35, -8), pop_total=100)
    farm.susceptible, farm.recovered = 90.0, 10.0

    seen = []
    for day in range(1, 8):
        model.step()
        if day == 1:
            farm.apply_stamping_out()
        seen.append((farm.status, farm.housed, farm.susceptible))

    assert model.total_culled == 100.0  # recovered birds are culled too
    assert [s for s, _, _ in seen] == statuses
    assert [h for _, h, _ in seen] == housed
    assert all(s == (0 if st == "culled" else h) for st, h, s in seen)
    # The new flock's eggs are counted from its housing, not from before the cull.
    assert farm.last_egg_collection_day == (farm.restock_day if protocol else 0)
    lifecycle = [
        (e.day, e.kind)
        for e in model.events
        if e.kind
        in {
            "SACRIFICIO_SANITARIO",
            "VAZIO_SANITARIO",
            "REPOVOAMENTO",
            "REPOVOAMENTO_TOTAL",
        }
    ]
    assert lifecycle == [(1, "SACRIFICIO_SANITARIO")] + (
        [(1, "VAZIO_SANITARIO"), (3, "REPOVOAMENTO"), (6, "REPOVOAMENTO_TOTAL")]
        if protocol
        else []
    )


def test_restocked_focus_is_inspected_and_becomes_a_focus_again(tiny_world):
    model = _model(
        tiny_world,
        **PROTOCOL,
        confirmation_days=0,
        zone_release_days=0,  # no rings: only the restocking inspections
        fallow_days=1,
        restock_pct=50.0,
        restock_monitoring_days=30,
        restock_inspection_days=3,
    )
    farm = model.add_farm(Facility("farm", "farm", -35, -8), pop_total=1000)
    farm.susceptible, farm.exposed = 900.0, 100.0
    farm.first_infection_day, farm.detection_day = 0, 1

    model.step()  # notified, confirmed and depopulated
    model.step()  # restocked with 500 birds, a new outbreak clock
    assert (farm.status, farm.housed, farm.notified) == ("restocked", 500, False)
    farm.seed_from_external(10, "INTRODUCAO_INICIAL")
    farm.detection_day = 50
    for _ in range(3):
        model.step()

    kinds = {
        "DETECCAO",
        "VIGILANCIA_ATIVA",
        "FOCO_CONFIRMADO",
        "DESPOVOAMENTO",
        "VAZIO_SANITARIO",
        "REPOVOAMENTO",
    }
    events = [(e.day, e.kind) for e in model.events if e.kind in kinds]
    again = ["DETECCAO", "FOCO_CONFIRMADO", "DESPOVOAMENTO", "VAZIO_SANITARIO"]
    assert events == [
        *((1, kind) for kind in again),
        (2, "REPOVOAMENTO"),
        # First inspection, 3 days after restocking.
        (5, "DETECCAO"),
        (5, "VIGILANCIA_ATIVA"),
        *((5, kind) for kind in again[1:]),
    ]
    (found,) = [e for e in model.events if e.kind == "VIGILANCIA_ATIVA"]
    assert "repovoamento" in found.details
    assert [f.farm_id for f in model.foci] == ["farm", "farm"]
    assert (farm.status, farm.cull_day) == ("culled", 5)
