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
    (farm,) = client.get("/api/sim/farms").json()["features"]
    assert farm["properties"]["id"] == "b"
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
