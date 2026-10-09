"""Offline inspection and immutable debug-route contracts."""

import builtins
import json
import socket
from dataclasses import replace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient
from shapely.geometry import LineString

from simgeope.api import create_app
from simgeope.world.hydro import HydroFeature, HydroLayer, RoadHydroCrossing
from simgeope.world.raster import build_environment

BBOX = "-35.01,-8.02,-34.97,-7.99"
ROUTE = {"origin": [-35, -8], "destination": [-34.98, -8]}


@pytest.fixture
def client(tiny_world):
    return TestClient(create_app(tiny_world))


def test_optional_fastapi_import_is_actionable(tiny_world, monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "fastapi":
            raise ImportError("deliberately absent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(RuntimeError, match="simgeope\\[viewer\\]"):
        create_app(tiny_world)


def test_static_metadata_and_admin(client, tiny_world):
    assert "World inspector" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/../../config.json").status_code == 404
    metadata = client.get("/api/world/metadata").json()
    assert metadata["crs"] == "EPSG:4326"
    assert metadata["layers"]["hydro"] is False
    assert str(tiny_world.path) not in json.dumps(metadata)
    municipalities = client.get("/api/world/municipalities").json()
    assert municipalities["type"] == "FeatureCollection"
    assert {f["properties"]["code"] for f in municipalities["features"]} == {
        "2611606",
        "2605459",
    }
    assert municipalities["features"][0]["geometry"]["type"] == "Polygon"


def test_phase3_layers_optional_and_canonical(client, tiny_world):
    response = client.get("/api/world/roads", params={"bbox": BBOX}).json()
    assert len(response["features"]) == 4
    assert response["features"][0]["properties"]["base_travel_time_s"] == 10
    assert response["features"][0]["properties"]["source_id"] == 101
    assert response["features"][0]["properties"]["highway"] == "primary"
    for name in ("hydro", "environment"):
        assert (
            client.get(f"/api/world/{name}", params={"bbox": BBOX}).json()["features"]
            == []
        )
    facilities = client.get("/api/world/facilities", params={"bbox": BBOX}).json()
    assert facilities["features"][0]["properties"]["road_node"] == 10
    tiny_world.facilities = None
    assert (
        client.get("/api/world/facilities", params={"bbox": BBOX}).json()["features"]
        == []
    )


@pytest.mark.parametrize("layer", ["roads", "hydro", "facilities", "environment"])
@pytest.mark.parametrize(
    "bbox,code",
    [
        (None, 422),
        ("bad", 422),
        ("-35,-8,-36,-7", 422),
        ("nan,-8,-34,-7", 422),
        ("-181,-8,-34,-7", 422),
        ("-50,-10,-30,0", 422),
        ("0,0,1,1", 404),
    ],
)
def test_viewports_are_required_and_validated(client, layer, bbox, code):
    params = {"bbox": bbox} if bbox is not None else {}
    assert client.get(f"/api/world/{layer}", params=params).status_code == code


@pytest.mark.parametrize("limit", [0, -1, 5001, "nan"])
def test_feature_count_limit(client, limit):
    assert (
        client.get(
            "/api/world/roads", params={"bbox": BBOX, "limit": limit}
        ).status_code
        == 422
    )


def test_capped_payload(client):
    result = client.get("/api/world/roads", params={"bbox": BBOX, "limit": 1}).json()
    assert len(result["features"]) == 1
    assert result["truncated"] is True


def test_world_queries_no_network(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Runtime query attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    query = client.get("/api/query/point", params={"lon": -35, "lat": -8}).json()
    assert query["municipality"]["code"] == "2611606"
    assert query["nearest_road"]["edge_id"] == 1
    assert query["hydro"] is None and query["environment"] is None
    assert client.post("/api/route", json=ROUTE).status_code == 200
    assert (
        client.get("/api/query/point", params={"lon": 0, "lat": 0}).status_code == 404
    )
    assert (
        client.get("/api/query/point", params={"lon": "nan", "lat": -8}).status_code
        == 422
    )
    # Inside the disconnected island: no fabricated snap/route.
    query = client.get("/api/query/point", params={"lon": -32.4, "lat": -3.85}).json()
    assert query["nearest_road"] is None
    assert (
        client.post(
            "/api/route", json=dict(ROUTE, destination=[-32.4, -3.85])
        ).status_code
        == 404
    )


def test_phase5_hydro_environment_and_facility_values(tiny_world):
    river = HydroFeature(
        "river",
        "river",
        LineString([(-34.995, -8.015), (-34.995, -7.995)]),
        source_id="123",
        source_date="2026-09-30",
        source_file="/private/source.osm.pbf",
        municipality_codes=("2611606",),
    )
    crossing = RoadHydroCrossing(1, "river", "river", False, -34.995, -8)
    tiny_world.hydro = HydroLayer([river], tiny_world.config.metric_crs, [crossing])
    tiny_world.environment = build_environment(
        tiny_world.administrative,
        tiny_world.hydro,
        metric_crs=tiny_world.config.metric_crs,
        resolution_m=500,
    )
    tiny_world.manifest = replace(
        tiny_world.manifest,
        sources=(),
        warnings=("Incomplete hydrology source /private/hydro.osm.pbf",),
    )
    client = TestClient(create_app(tiny_world))
    hydro = client.get("/api/world/hydro", params={"bbox": BBOX}).json()["features"]
    assert len(hydro) == 2
    assert hydro[0]["properties"]["source_file"] == "source.osm.pbf"
    assert hydro[0]["properties"]["source_id"] == "123"
    assert hydro[1]["properties"]["layer"] == "crossings"
    environment = client.get(
        "/api/world/environment", params={"bbox": BBOX, "limit": 10}
    ).json()
    assert 0 < len(environment["features"]) <= 10
    assert environment["sample_stride"] > 1
    for feature in environment["features"]:
        props = feature["properties"]
        assert props["water_distance_m"] == float(
            tiny_world.environment.water_distance_m[props["row"], props["col"]]
        )
        assert props["municipality_code"] == "2611606"
        assert feature["geometry"]["type"] == "Polygon"
    query = client.get("/api/query/point", params={"lon": -35, "lat": -8}).json()
    assert query["environment"] == tiny_world.environment_at((-35, -8))
    assert query["hydro"]["nearest_waterway"]["id"] == "river"
    assert "/private" not in json.dumps(query)
    metadata = client.get("/api/world/metadata").json()
    assert metadata["warnings"] == ["Incomplete hydrology source hydro.osm.pbf"]


def test_route_overlays_never_change_canonical_network(client, tiny_world):
    before = tiny_world.route((-35, -8), (-34.98, -8))
    baseline = client.post("/api/route", json=ROUTE).json()
    assert baseline["geometry"]["type"] == "LineString"
    assert baseline["properties"]["travel_time_s"] == 20
    assert baseline["geometry"]["coordinates"][0] == [-35, -8]
    for overlay in ({"closed_edges": [1]}, {"cost_multipliers": {"1": 10}}):
        altered = client.post("/api/route", json=dict(ROUTE, **overlay)).json()
        assert altered["properties"]["edge_ids"] == [3, 4]
        assert altered["properties"]["travel_time_s"] == 60
    assert tiny_world.route((-35, -8), (-34.98, -8)) == before
    assert (
        client.post("/api/route", json=dict(ROUTE, closed_edges=[1, 3])).status_code
        == 404
    )
    assert (
        client.post("/api/route", json=dict(ROUTE, destination=ROUTE["origin"])).json()[
            "geometry"
        ]["type"]
        == "Point"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"origin": [0, 0]},
        {"origin": [True, -8]},
        {"origin": [1e200, -8]},
        {"origin": ["-35", "-8"]},
        {"closed_edges": [999]},
        {"closed_edges": [True]},
        {"closed_edges": [-1]},
        {"closed_edges": list(range(1001))},
        {"cost_multipliers": {"1": 0}},
        {"cost_multipliers": {"1": True}},
        {"cost_multipliers": {"bad": 2}},
        {"cost_multipliers": {"1": 1e200}},
        {"unexpected": 5},
    ],
)
def test_route_bad_inputs(client, change):
    assert client.post("/api/route", json=dict(ROUTE, **change)).status_code == (
        404 if change.get("origin") == [0, 0] else 422
    )


def test_route_body_limits(client):
    assert client.post("/api/route", content='{"origin":').status_code == 422
    assert client.post("/api/route", json=[]).status_code == 422
    assert client.post("/api/route", content=b" " * 32769).status_code == 413
