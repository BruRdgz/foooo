import pytest

from simgeope.world.config import WorldConfig
from simgeope.world.spatial import SpatialReference, lon_lat


def test_config_roundtrip_and_stable_hash(tmp_path):
    config = WorldConfig(
        road_classes=["primary", "secondary"],
        ibge_state=tmp_path / "state.geojson",
        osm_url="https://download.geofabrik.de/south-america/brazil/nordeste-260930.osm.pbf",
    )
    path = tmp_path / "config.json"
    config.save(path)
    assert WorldConfig.load(path) == config
    assert (
        WorldConfig.from_dict(
            dict(reversed(list(config.to_dict().items())))
        ).configuration_hash
        == config.configuration_hash
    )
    assert (
        WorldConfig(max_snap_distance_m=1).configuration_hash
        != config.configuration_hash
    )


@pytest.mark.parametrize(
    "values",
    [
        {"metric_crs": "EPSG:4326"},
        {"storage_crs": "EPSG:5880"},
        {"metric_crs": "EPSG:2263"},
        {"raster_resolution_m": 0},
        {"border_padding_m": -1},
        {"max_snap_distance_m": float("nan")},
        {"road_classes": []},
        {"road_classes": ["primary", "primary"]},
        {"allow_excessive_snap": "false"},
        {"unexpected": 1},
        {"road_classes": "primary"},
        {"ibge_year": "2024"},
        {"ibge_year": 2024.5},
        {"raster_resolution_m": True},
        {"border_padding_m": "1000"},
        {"metric_crs": "invalid"},
        {"osm_url": ""},
        {"osm_url": "   "},
        {"osm_url": 123},
        {"output_dir": None},
        {"cache_dir": ""},
        {"ibge_state": 123},
    ],
)
def test_invalid_configuration_rejected(values):
    with pytest.raises(ValueError):
        WorldConfig.from_dict(values)


def test_crs_conversion_uses_configured_projection():
    point = (-34.88, -8.05)
    default = SpatialReference(WorldConfig().metric_crs)
    alternative = SpatialReference("EPSG:31985")
    assert default.unproject(default.project(point)) == pytest.approx(point)
    assert default.project(point) != pytest.approx(alternative.project(point))
    first = default.project(point)
    second = default.project((-34.87, -8.05))
    assert (
        1000 < ((second[0] - first[0]) ** 2 + (second[1] - first[1]) ** 2) ** 0.5 < 1200
    )


@pytest.mark.parametrize(
    "point", [(181, 0), (0, 91), (float("nan"), 0), (1,), (1, 2, 3)]
)
def test_invalid_query_coordinates(point):
    with pytest.raises(ValueError):
        lon_lat(point)


def test_hydrology_defaults_preserve_phase3_hash():
    assert (
        WorldConfig().configuration_hash
        == "f7be7577db744e985b38cddc3dc5110414c42f8bafdc9459ae43a2faee72eecb"
    )
    enabled = WorldConfig(hydro_enabled=True, hydro_min_waterbody_area_m2=0)
    assert WorldConfig.from_dict(enabled.to_dict()) == enabled
    assert enabled.configuration_hash != WorldConfig().configuration_hash


@pytest.mark.parametrize(
    "values",
    [
        {"hydro_enabled": "true"},
        {"hydro_include_named_streams": 1},
        {"hydro_min_waterbody_area_m2": -1},
        {"hydro_min_waterbody_area_m2": float("nan")},
        {"hydro_min_waterbody_area_m2": True},
    ],
)
def test_invalid_hydrology_configuration(values):
    with pytest.raises(ValueError):
        WorldConfig.from_dict(values)


def test_environment_configuration_requires_hydrology():
    with pytest.raises(ValueError, match="requires hydro_enabled"):
        WorldConfig(environment_enabled=True)
    with pytest.raises(ValueError, match="must be boolean"):
        WorldConfig(environment_enabled="true", hydro_enabled=True)
    config = WorldConfig(environment_enabled=True, hydro_enabled=True)
    assert WorldConfig.from_dict(config.to_dict()) == config
