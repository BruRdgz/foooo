"""Live simulation endpoints served next to the world inspector."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("mesa")
from fastapi.testclient import TestClient

from simgeope.api import create_app
from simgeope.api.simulation import SimulationSession
from simgeope.simulation import FleetSpec, ModelParams


@pytest.fixture
def session(two_way_world):
    # Facility "a" (kind hub, node 10) serves producer "b" (node 30) by road.
    params = ModelParams(fleets=(FleetSpec("hub", 1, 1),))
    return SimulationSession(two_way_world, params=params, seed=7, birds=0)


@pytest.fixture
def client(two_way_world, session):
    return TestClient(create_app(two_way_world, simulation=session))


def test_metadata_flags_simulation(two_way_world, client):
    assert client.get("/api/world/metadata").json()["simulation"] is True
    plain = TestClient(create_app(two_way_world))
    assert plain.get("/api/world/metadata").json()["simulation"] is False
    assert plain.get("/api/sim/state").status_code == 404


def test_step_shows_seeded_farm_and_todays_road_trips(client, session):
    state = client.get("/api/sim/state").json()
    assert (state["day"], state["seed"], state["farms"], state["trucks"]) == (
        0,
        7,
        1,
        1,
    )
    assert state["synthetic_facilities"] is False
    assert state["farms_by_kind"] == {"producer": 1}
    (farm,) = client.get("/api/sim/farms").json()["features"]
    assert farm["properties"]["id"] == "b"
    assert farm["properties"]["production"] is None  # unknown production type
    assert farm["properties"]["status"] == "hidden"  # exposed, not yet notified

    assert client.post("/api/sim/step", json={"days": 1}).json()["day"] == 1
    trips = client.get("/api/sim/movements").json()["features"]
    assert [
        (t["properties"]["origin_id"], t["properties"]["destination_id"]) for t in trips
    ] == [
        ("a", "b"),
        ("b", "a"),
    ]
    # Trips follow the road (a -> b passes node 20); the API simplifies
    # collinear vertices, so only the endpoints survive here.
    assert session.route_coordinates(10, 30) == [(-35, -8), (-34.99, -8), (-34.98, -8)]
    assert trips[0]["geometry"]["coordinates"] == [[-35, -8], [-34.98, -8]]
    events = client.get("/api/sim/events").json()
    assert events["events"][0]["type"] == "INTRODUCAO_INICIAL"
    assert (
        client.get("/api/sim/events", params={"since": events["next"]}).json()["events"]
        == []
    )


def test_session_without_facilities_needs_the_reference_municipalities(tiny_world):
    # No silent fallback to illustrative poles on a world the data misses.
    tiny_world.facilities = None
    with pytest.raises(ValueError, match="World lacks"):
        SimulationSession(tiny_world, birds=0)


def test_manual_stamping_out_draws_zone_and_reset_restores(client):
    assert client.get("/api/sim/interventions").json()["features"] == []
    response = client.post(
        "/api/sim/intervene", json={"farm_id": "b", "action": "stamping_out"}
    )
    assert response.status_code == 200
    assert response.json()["total_culled"] > 0
    (farm,) = client.get("/api/sim/farms").json()["features"]
    assert farm["properties"]["status"] == "culled"
    (zone,) = client.get("/api/sim/interventions").json()["features"]
    assert zone["properties"] == {"farm_id": "b", "radius_km": 5.0}
    assert zone["geometry"]["type"] == "Polygon"
    again = client.post(
        "/api/sim/intervene", json={"farm_id": "b", "action": "stamping_out"}
    )
    assert again.status_code == 409

    state = client.post("/api/sim/reset", json={"seed": 3}).json()
    assert (state["day"], state["seed"], state["total_culled"]) == (0, 3, 0)
    assert client.get("/api/sim/interventions").json()["features"] == []


def test_risk_without_environment_is_reported_unavailable(client):
    assert client.get("/api/sim/state").json()["risk_available"] is False
    assert client.get("/api/sim/risk").json() == {
        "type": "FeatureCollection",
        "features": [],
        "truncated": False,
        "limit": 5000,
        "available": False,
    }


@pytest.mark.parametrize(
    "path,payload,code",
    [
        ("/api/sim/step", {"days": 0}, 422),
        ("/api/sim/step", {"days": 61}, 422),
        ("/api/sim/step", {"days": "1"}, 422),
        ("/api/sim/step", {"weeks": 1}, 422),
        ("/api/sim/reset", {"seed": -1}, 422),
        ("/api/sim/intervene", {"farm_id": "b", "action": "vaccinate"}, 422),
        ("/api/sim/intervene", {"farm_id": 1, "action": "stamping_out"}, 422),
        ("/api/sim/intervene", {"farm_id": "zz", "action": "stamping_out"}, 404),
    ],
)
def test_invalid_requests_leave_model_unchanged(client, path, payload, code):
    before = client.get("/api/sim/state").json()
    assert client.post(path, json=payload).status_code == code
    assert client.get("/api/sim/state").json() == before


def test_malformed_and_oversized_bodies_are_rejected(client):
    assert client.post("/api/sim/step", content=b"{").status_code == 422
    assert client.post("/api/sim/step", content=b"[1]").status_code == 422
    assert client.post("/api/sim/step", content=b" " * 5000).status_code == 413
    # Resets carry parameter overrides and get a larger body limit.
    assert client.post("/api/sim/reset", content=b" " * 5000).status_code == 422
    assert client.post("/api/sim/reset", content=b" " * 17000).status_code == 413


def test_param_schema_covers_every_scalar_model_parameter(client):
    from dataclasses import fields
    from typing import get_type_hints

    body = client.get("/api/sim/params").json()
    hints = get_type_hints(ModelParams)
    scalar = {f.name: hints[f.name].__name__ for f in fields(ModelParams)}
    scalar = {name: kind for name, kind in scalar.items() if kind in ("int", "float")}
    schema = {spec["name"]: spec for spec in body["schema"]}
    assert {name: spec["type"] for name, spec in schema.items()} == scalar
    assert body["defaults"] == {name: getattr(ModelParams(), name) for name in scalar}
    # "Restore defaults" must always pass the reset validation.
    assert all(
        spec["min"] <= body["defaults"][name] <= spec["max"]
        for name, spec in schema.items()
    )
    groups = list(dict.fromkeys(spec["group"] for spec in body["schema"]))
    assert groups == [
        "Calendário",
        "SEIR intra-granja",
        "Vigilância e reação",
        "Aves silvestres",
        "Transmissão local",
        "Fômites (caminhões)",
        "Camada de risco",
        "Intervenções e economia",
        "Protocolo oficial (MAPA POP 2023)",
    ]
    # Every entry says whether its value is an assumption and where it comes
    # from; protocol values are cited or flagged as assumptions.
    for spec in body["schema"]:
        assert type(spec["assumption"]) is bool
        if spec["group"] != groups[-1]:
            assert (spec["assumption"], spec["source"]) == (False, None)
        elif spec["assumption"]:
            assert spec["source"].startswith("suposição: ")
        else:
            assert spec["source"] and "POP" in spec["source"]
    protocol = {s["name"]: s for s in body["schema"] if s["group"] == groups[-1]}
    assert not protocol["perifocus_radius_km"]["assumption"]
    assert protocol["surveillance_radius_km"]["assumption"]


def test_reset_param_overrides_persist_across_resets(client, session):
    overrides = {"beta": 2, "detection_time_max": 10}
    assert client.post("/api/sim/reset", json={"params": overrides}).status_code == 200
    client.post("/api/sim/reset", json={"seed": 9})
    values = client.get("/api/sim/params").json()["values"]
    assert (values["beta"], values["detection_time_max"]) == (2.0, 10)
    assert type(values["beta"]) is float
    assert (session.model.params.beta, session.seed) == (2.0, 9)
    # Overrides merge into the session parameters; fleets are kept.
    assert session.model.params.fleets == (FleetSpec("hub", 1, 1),)


@pytest.mark.parametrize(
    "content",
    [
        b'{"params": {"betta": 3}}',
        b'{"params": {"detection_time_max": 7.5}}',
        b'{"params": {"beta": true}}',
        b'{"params": {"beta": 9}}',
        b'{"params": {"beta": NaN}}',
        b'{"params": {"beta": 2, "fleets": []}}',
        b'{"params": {"risk_yellow_threshold": 0.5, "risk_red_threshold": 0.4}}',
        b'{"params": {"surveillance_radius_km": 3}}',  # empty vigilancia ring
        b'{"params": {"protocol_enabled": 2}}',
        b'{"params": [["beta", 2]]}',
    ],
)
def test_invalid_param_reset_leaves_model_unchanged(client, content):
    client.post("/api/sim/step", json={"days": 1})
    before = client.get("/api/sim/state").json()
    values = client.get("/api/sim/params").json()["values"]
    response = client.post(
        "/api/sim/reset", content=content, headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert client.get("/api/sim/state").json() == before
    assert client.get("/api/sim/params").json()["values"] == values


REPLAYABLE = (
    "/api/sim/farms",
    "/api/sim/hubs",
    "/api/sim/birds",
    "/api/sim/movements",
    "/api/sim/interventions",
)


def test_past_days_replay_after_stamping_out(client):
    client.post("/api/sim/step", json={"days": 1})
    client.post("/api/sim/intervene", json={"farm_id": "b", "action": "stamping_out"})
    state = client.get("/api/sim/state").json()
    assert state["snapshot_days"] == [0, 1]
    assert state["active_quarantines"] == 0  # the only farm was culled

    for path in REPLAYABLE:
        today = client.get(path, params={"day": 1}).json()
        assert today == client.get(path).json()
        for missing in (2, -1):
            assert client.get(path, params={"day": missing}).status_code == 404

    (farm,) = client.get("/api/sim/farms", params={"day": 0}).json()["features"]
    assert farm["properties"]["status"] == "hidden"
    assert farm["properties"]["municipality"] == "Recife"
    assert (
        client.get("/api/sim/interventions", params={"day": 0}).json()["features"] == []
    )
    assert client.get("/api/sim/movements", params={"day": 0}).json()["features"] == []
    (hub,) = client.get("/api/sim/hubs").json()["features"]
    assert (hub["properties"]["id"], hub["properties"]["trucks"]) == ("a", 1)


def test_snapshots_older_than_retention_are_dropped(client, monkeypatch):
    from simgeope.api import simulation

    monkeypatch.setattr(simulation, "MAX_SNAPSHOT_DAYS", 2)
    client.post("/api/sim/step", json={"days": 3})
    assert client.get("/api/sim/state").json()["snapshot_days"] == [2, 3]
    assert client.get("/api/sim/farms", params={"day": 1}).status_code == 404
    assert client.get("/api/sim/farms", params={"day": 2}).status_code == 200


def test_farm_detail_combines_history_events_and_visits(client):
    client.post("/api/sim/step", json={"days": 1})
    client.post("/api/sim/intervene", json={"farm_id": "b", "action": "stamping_out"})
    detail = client.get("/api/sim/farms/b").json()

    assert (detail["status"], detail["culled"], detail["kind"]) == (
        "culled",
        True,
        "producer",
    )
    assert detail["nearest_hub"] == {"id": "a", "kind": "hub"}
    assert [(h["day"], h["status"]) for h in detail["history"]] == [
        (0, "hidden"),
        (1, "culled"),
    ]
    kinds = [e["type"] for e in detail["events"]]
    assert kinds[0] == "INTRODUCAO_INICIAL"
    assert kinds[-1] == "SACRIFICIO_SANITARIO"
    assert {e["farm_id"] for e in detail["events"]} == {"b"}
    assert [(v["origin_id"], v["destination_id"]) for v in detail["visits"]] == [
        ("a", "b"),
        ("b", "a"),
    ]
    assert client.get("/api/sim/farms/zz").status_code == 404


def test_events_filter_by_farm_and_type_keeps_log_cursor(client):
    client.post("/api/sim/step", json={"days": 2})
    full = client.get("/api/sim/events").json()
    assert all(e["farm_id"] == "b" for e in full["events"])  # no fleet warnings

    for query, expected in [
        ({"farm_id": "b"}, full["events"]),
        ({"farm_id": "zz"}, []),
        (
            {"type": "INTRODUCAO_INICIAL"},
            [e for e in full["events"] if e["type"] == "INTRODUCAO_INICIAL"],
        ),
    ]:
        filtered = client.get("/api/sim/events", params=query).json()
        assert filtered == {"next": full["next"], "skipped": 0, "events": expected}


def test_history_starts_at_day_zero_with_daily_flows(client):
    client.post("/api/sim/step", json={"days": 1})
    day0, day1 = client.get("/api/sim/history").json()
    no_introductions = {
        "INTRODUCAO_FOMITE": 0,
        "INTRODUCAO_VIZINHANCA": 0,
        "INTRODUCAO_AVES_MIGRATORIAS": 0,
    }
    # Day 0 holds the initial seeding (ModelParams.initial_infected birds).
    assert (day0["day"], day0["hidden"], day0["new_exposed"], day0["trips"]) == (
        0,
        1,
        50.0,
        0,
    )
    assert day0["introductions"] == no_introductions
    assert (day1["day"], day1["trips"]) == (1, 2)
    assert set(day1["introductions"]) == set(no_introductions)
    state = client.get("/api/sim/state").json()
    assert (day1["deaths"], day1["culled_birds"]) == (
        state["total_deaths"],
        state["total_culled"],
    )


def test_risk_cells_report_fomite_exposure_at_visited_farm(two_way_world):
    import numpy as np

    from simgeope.world.raster import EnvironmentStore, RasterGrid

    x, y = two_way_world.spatial.project((-35, -8))
    grid = RasterGrid(x - 500, y + 500, 1000, 1, 5, two_way_world.config.metric_crs)
    mask = np.ones((1, 5), dtype=bool)
    two_way_world.environment = EnvironmentStore(
        grid,
        state_mask=mask,
        municipality_code=np.full((1, 5), 2611606, dtype=np.uint32),
        water_distance_m=np.full((1, 5), 9000, dtype=np.float32),
        wetland_fraction=np.zeros((1, 5), dtype=np.float32),
    )
    params = ModelParams(fleets=(FleetSpec("hub", 1, 1),), truck_disinfection_prob=0)
    session = SimulationSession(two_way_world, params=params, seed=7, birds=0)
    client = TestClient(create_app(two_way_world, simulation=session))

    # Day 1 the truck loads at infected farm b; day 2 it arrives there loaded.
    client.post("/api/sim/step", json={"days": 2})
    risk = client.get("/api/sim/risk").json()

    assert risk["available"] is True
    cells = {f["properties"]["col"]: f["properties"] for f in risk["features"]}
    assert all(f["geometry"]["type"] == "Polygon" for f in risk["features"])
    # Only the visited farm's cell (col 2) has fomite exposure.
    assert [c for c in cells if cells[c]["fomite"] > 0] == [2]
    # Neighbourhood risk around infected farm b decays with distance.
    local = [cells[c]["local"] for c in range(5)]
    assert local[2] > local[1] > local[0] > 0
    assert local[2] > local[3] > local[4] > 0
    assert all(c["bird"] == 0 for c in cells.values())
    assert cells[2]["total"] == pytest.approx(
        1 - (1 - cells[2]["fomite"]) * (1 - cells[2]["local"]), rel=1e-6
    )

    # Culling the only shedding farm removes its neighbourhood risk the same day.
    client.post("/api/sim/intervene", json={"farm_id": "b", "action": "stamping_out"})
    after = client.get("/api/sim/risk").json()["features"]
    assert all(f["properties"]["local"] == 0 for f in after)


@pytest.fixture
def chain(two_way_world):
    """Hubs a and z, producers b and c (1.1 km apart) and one resident bird."""
    from simgeope.world.facilities import Facility, FacilityIndex

    two_way_world.facilities = FacilityIndex(
        [
            Facility("a", "hub", -35.0, -8.0),
            Facility("z", "hub", -34.99, -8.01),
            Facility("b", "producer", -34.98, -8.0),
            Facility("c", "producer", -34.99, -8.0),
        ],
        metric_crs=two_way_world.config.metric_crs,
    )
    params = ModelParams(fleets=(FleetSpec("hub", 1, 1),))
    session = SimulationSession(two_way_world, params=params, seed=7, birds=1)
    return session, TestClient(create_app(two_way_world, simulation=session))


def test_transmissions_window_paths_and_hub_ranking(chain):
    from simgeope.simulation.agents import Transmission

    session, client = chain
    client.post("/api/sim/step", json={"days": 3})
    model = session.model
    (bird_id,) = session.snapshots[0].bird_ids
    seen = session.snapshots[0].bird_locations[0].tolist()
    a, z, b, c = (
        list(model.agents_by_id[key].location) for key in ("a", "z", "b", "c")
    )
    # Controlled provenance: the endpoint only serialises the model's records.
    model.transmissions = [
        Transmission(0, "b", "initial", None, None, None, None, None),
        Transmission(0, "c", "bird", None, None, None, bird_id, None),
        Transmission(1, "c", "fomite", "b", "a", "a-truck-1", None, 1.0),
        Transmission(2, "b", "fomite", "c", "z", "z-truck-1", None, 1.0),
        Transmission(2, "c", "fomite", "b", None, "a-truck-1", None, 1.0),
        Transmission(3, "c", "local", "b", None, None, None, 0.75),
        Transmission(3, "b", "fomite", "c", "a", "a-truck-1", None, 1.0),
        Transmission(3, "b", "bird", None, None, None, "gone", None),
    ]

    whole = client.get("/api/sim/transmissions").json()
    assert [t["path"] for t in whole["transmissions"]] == [
        [b],
        [seen, c],
        [b, a, c],
        [c, z, b],
        [b, c],  # carried straight between farms: no hub
        [b, c],
        [c, a, b],
        [b, b],  # bird not in that day's snapshot: drawn at the target
    ]
    assert whole["transmissions"][2] == {
        "day": 1,
        "farm_id": "c",
        "pathway": "fomite",
        "source_farm_id": "b",
        "hub_id": "a",
        "truck_id": "a-truck-1",
        "bird_id": None,
        "share": 1.0,
        "path": [b, a, c],
    }
    hub_a = whole["hubs"][0]
    assert hub_a == {
        "id": "a",
        "name": None,
        "kind": "hub",
        "lon": -35.0,
        "lat": -8.0,
        "introductions": 2,
        "source_farms": 2,
    }

    for query, days, ranking in [
        ({}, [0, 0, 1, 2, 2, 3, 3, 3], [("a", 2, 2), ("z", 1, 1)]),
        ({"from_day": 2, "to_day": 2}, [2, 2], [("z", 1, 1), ("a", 0, 0)]),
        ({"from_day": 3}, [3, 3, 3], [("a", 1, 1), ("z", 0, 0)]),
        ({"to_day": 0}, [0, 0], [("a", 0, 0), ("z", 0, 0)]),
        ({"from_day": 9, "to_day": 20}, [], [("a", 0, 0), ("z", 0, 0)]),
    ]:
        body = client.get("/api/sim/transmissions", params=query).json()
        assert [t["day"] for t in body["transmissions"]] == days
        assert [
            (h["id"], h["introductions"], h["source_farms"]) for h in body["hubs"]
        ] == ranking
    bad = client.get("/api/sim/transmissions", params={"from_day": 3, "to_day": 2})
    assert bad.status_code == 422


def test_heat_counts_introductions_infected_days_and_exposures(client, session):
    client.post("/api/sim/step", json={"days": 2})
    exposed = [row["new_exposed"] for row in client.get("/api/sim/history").json()]
    infected = [
        session.snapshots[day].farms[0].status in ("hidden", "notified")
        for day in (0, 1, 2)
    ]
    introductions = [t.day for t in session.model.transmissions]
    assert introductions[0] == 0 and exposed[0] == 50.0

    for query, window in [
        ({}, [0, 2]),
        ({"from_day": 1}, [1, 2]),
        ({"from_day": -5, "to_day": 99}, [0, 2]),
        ({"to_day": 0}, [0, 0]),
    ]:
        body = client.get("/api/sim/heat", params=query).json()
        assert body["window"] == window
        low, high = window
        (farm,) = body["features"]
        assert farm["properties"] == {
            "id": "b",
            "introductions": sum(low <= day <= high for day in introductions),
            "infected_days": sum(infected[low : high + 1]),
            "exposed": pytest.approx(sum(exposed[low : high + 1])),
        }
    empty = client.get("/api/sim/heat", params={"from_day": 5}).json()
    assert (empty["window"], empty["features"]) == (None, [])
    bad = client.get("/api/sim/heat", params={"from_day": 2, "to_day": 1})
    assert bad.status_code == 422


def test_heat_omits_farms_without_any_value(chain):
    session, client = chain
    (seeded,) = [t.farm_id for t in session.model.transmissions]
    (farm,) = client.get("/api/sim/heat").json()["features"]
    assert farm["properties"] == {
        "id": seeded,
        "introductions": 1,
        "infected_days": 1,
        "exposed": 50.0,
    }


def test_flocks_filter_arrivals_by_day(client, session):
    from simgeope.simulation.model import FlockArrival

    client.post("/api/sim/step", json={"days": 3})
    session.model.flock_arrivals = [
        FlockArrival(0, -35.0, -8.0, 4, False),
        FlockArrival(1, -34.99, -8.0, 7, True),
        FlockArrival(1, -34.98, -8.0, 2, False),
        FlockArrival(3, -35.0, -8.01, 9, True),
    ]

    for query, expected in [
        ({}, [(3, 9, True)]),  # today only
        ({"from_day": 1, "to_day": 1}, [(1, 7, True), (1, 2, False)]),
        ({"from_day": 1}, [(1, 7, True), (1, 2, False), (3, 9, True)]),
        ({"to_day": 0}, [(0, 4, False)]),
        ({"from_day": 4, "to_day": 9}, []),
    ]:
        features = client.get("/api/sim/flocks", params=query).json()["features"]
        assert [
            (
                f["properties"]["day"],
                f["properties"]["size"],
                f["properties"]["infected"],
            )
            for f in features
        ] == expected
    (flock,) = client.get("/api/sim/flocks").json()["features"]
    assert flock["geometry"] == {"type": "Point", "coordinates": [-35.0, -8.01]}
    bad = client.get("/api/sim/flocks", params={"from_day": 2, "to_day": 1})
    assert bad.status_code == 422


def test_presets_are_valid_overrides_applied_by_reset(client, session):
    from simgeope.simulation.params import override_params

    presets = client.get("/api/sim/params").json()["presets"]
    ids = [preset["id"] for preset in presets]
    assert ids[0] == "padrao" and presets[0]["values"] == {}
    assert "surto_sustentado" in ids
    by_id = {preset["id"]: preset for preset in presets}
    official = by_id["protocolo_mapa"]
    assert official["label"] == "Protocolo oficial (MAPA)"
    assert "ADAGRO" in official["description"]
    # The sustained outbreak plus the protocol table's values.
    assert official["values"] == by_id["surto_sustentado"]["values"] | {
        "protocol_enabled": 1,
        "confirmation_days": 2,
        "auto_depopulation": 1,
        "perifocus_radius_km": 3.0,
        "surveillance_radius_km": 10.0,
        "protection_radius_km": 25.0,
        "standstill_days": 7,
        "zone_disinfection_prob": 0.95,
        "zone_release_days": 28,
        "perifocus_detection_days": 3,
        "surveillance_detection_days": 7,
    }
    for preset in presets:
        assert preset["label"] and preset["description"]
        override_params(ModelParams(), preset["values"])  # within schema bounds
        response = client.post(
            "/api/sim/reset", json={"seed": 1, "params": preset["values"]}
        )
        assert response.status_code == 200
        for name, value in preset["values"].items():
            assert getattr(session.model.params, name) == value


@pytest.fixture
def protocol_chain(two_way_world):
    """Hub a and producers b and c 1.1 km apart, the official protocol on.

    The seeded farm becomes the only focus (nothing else spreads) and is not
    depopulated, so the other farm stays in its vigilância ring.
    """
    from simgeope.world.facilities import Facility, FacilityIndex

    two_way_world.facilities = FacilityIndex(
        [
            Facility("a", "hub", -35.0, -8.0),
            Facility("b", "producer", -34.98, -8.0),
            Facility("c", "producer", -34.99, -8.0),
        ],
        metric_crs=two_way_world.config.metric_crs,
    )
    params = ModelParams(
        fleets=(FleetSpec("hub", 1, 1),),
        protocol_enabled=1,
        perifocus_radius_km=0.5,
        surveillance_radius_km=1.5,
        protection_radius_km=2.5,
        detection_time_max=1,
        confirmation_days=1,
        auto_depopulation=0,
        standstill_days=1,
        zone_release_days=10,
        local_spread_prob=0.0,
        infection_prob_truck_to_farm=0.0,
    )
    session = SimulationSession(two_way_world, params=params, seed=7, birds=0)
    return session, TestClient(create_app(two_way_world, simulation=session))


def test_zones_draw_rings_per_focus_and_replay_past_days(protocol_chain):
    from shapely.geometry import Point, shape

    session, client = protocol_chain
    while not session.model.foci:
        client.post("/api/sim/step", json={"days": 1})
    (focus,) = session.model.foci
    confirmed, focus_id = focus.confirmed_day, focus.farm_id
    client.post("/api/sim/step", json={"days": 1})

    spatial = session.world.spatial
    centre = spatial.project(session.model.farm_by_id(focus_id).location)
    features = client.get("/api/sim/zones", params={"day": confirmed}).json()[
        "features"
    ]
    assert [f["properties"] for f in features] == [
        {
            "focus_id": focus_id,
            "ring": ring,
            "radius_km": radius,
            "confirmed_day": confirmed,
            "standstill": True,
            "release_day": None,
        }
        for ring, radius in (("perifoco", 0.5), ("vigilancia", 1.5), ("protecao", 2.5))
    ]
    rings = [spatial.geometry_to_metric(shape(f["geometry"])) for f in features]
    # A disk, then annuli: each holds points between its inner and outer radii.
    assert [len(ring.interiors) for ring in rings] == [0, 1, 1]
    for ring, inner, outer in zip(rings, (0, 500, 1500), (500, 1500, 2500)):
        # GeoJSON output is simplified (~20 m), noticeable on these tiny rings.
        assert ring.area == pytest.approx(3.14159 * (outer**2 - inner**2), rel=0.05)
        mid = Point(centre[0] + (inner + outer) / 2, centre[1])
        assert [r.contains(mid) for r in rings] == [r is ring for r in rings]

    today = client.get("/api/sim/zones").json()["features"]
    assert [f["properties"]["standstill"] for f in today] == [False] * 3
    assert client.get("/api/sim/zones", params={"day": 0}).json()["features"] == []
    for missing in (99, -1):
        response = client.get("/api/sim/zones", params={"day": missing})
        assert response.status_code == 404

    # Farm features carry their zone, in replayed days too.
    def zones(day):
        farms = client.get("/api/sim/farms", params={"day": day}).json()
        return {
            f["properties"]["id"]: (f["properties"]["zone"], f["properties"]["focus"])
            for f in farms["features"]
        }

    other = ({"b", "c"} - {focus_id}).pop()
    assert zones(0) == {"b": (None, False), "c": (None, False)}
    assert zones(confirmed) == {
        focus_id: ("perifoco", True),
        other: ("vigilancia", False),
    }
    detail = client.get(f"/api/sim/farms/{focus_id}").json()
    assert (detail["zone"], detail["focus"]) == ("perifoco", True)
    kinds = {e["type"] for e in client.get("/api/sim/events").json()["events"]}
    assert {"FOCO_CONFIRMADO", "INTERDICAO_TRANSITO"} <= kinds


def test_state_reports_protocol_counts(protocol_chain, client):
    off = client.get("/api/sim/state").json()["protocol"]
    assert off == {
        "enabled": False,
        "active_foci": 0,
        "standstill_foci": 0,
        "farms_in_zones": {"perifoco": 0, "vigilancia": 0, "protecao": 0},
    }
    assert client.get("/api/sim/zones").json()["features"] == []

    session, chain_client = protocol_chain

    def protocol():
        return chain_client.get("/api/sim/state").json()["protocol"]

    while not session.model.foci:
        assert protocol()["active_foci"] == 0
        chain_client.post("/api/sim/step", json={"days": 1})
    in_zones = {"perifoco": 1, "vigilancia": 1, "protecao": 0}
    assert protocol() == {
        "enabled": True,
        "active_foci": 1,
        "standstill_foci": 1,
        "farms_in_zones": in_zones,
    }
    chain_client.post("/api/sim/step", json={"days": 1})
    assert protocol()["standstill_foci"] == 0
    # Culled farms are no longer counted as under restriction.
    focus_id = session.model.foci[0].farm_id
    chain_client.post(
        "/api/sim/intervene", json={"farm_id": focus_id, "action": "stamping_out"}
    )
    assert protocol()["farms_in_zones"] == in_zones | {"perifoco": 0}
