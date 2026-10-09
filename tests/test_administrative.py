import json

import pytest
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from simgeope.world.administrative import AdministrativeLayer, Municipality
from simgeope.world.config import WorldConfig
from simgeope.world.exceptions import WorldBuildError, WorldDataMissingError
from simgeope.world.sources.ibge import IBGESource


def make_layer():
    recife = box(-35, -8.2, -34.8, -8)
    noronha = box(-32.45, -3.9, -32.35, -3.8)
    return AdministrativeLayer(
        [
            Municipality("2611606", "Recife", recife),
            Municipality("2605459", "Fernando de Noronha", noronha),
        ],
        unary_union([recife, noronha]),
        WorldConfig().metric_crs,
    )


def test_index_lookup_preserves_island_and_boundary():
    layer = make_layer()
    assert layer.municipality_at((-34.9, -8.1)).name == "Recife"
    assert layer.municipality_at((-32.4, -3.85)).code == "2605459"
    assert layer.municipality_at((-36, -8)) is None
    assert layer.municipality_at((-35, -8.2)).code == "2611606"
    assert layer.municipality_at((-34.9, -8.2)).code == "2611606"
    assert layer.municipality_codes_for_geometry(box(-35.1, -8.3, -34.9, -8.1)) == (
        "2611606",
    )


def test_admin_parquet_roundtrip(tmp_path):
    layer = make_layer()
    layer.save(tmp_path)
    restored = AdministrativeLayer.load(tmp_path, WorldConfig().metric_crs)
    assert restored.municipality_at((-32.4, -3.85)).name == "Fernando de Noronha"
    assert restored.state_geometry_wgs84.equals(layer.state_geometry_wgs84)


def test_missing_admin_outputs(tmp_path):
    with pytest.raises(WorldDataMissingError):
        AdministrativeLayer.load(tmp_path, WorldConfig().metric_crs)


def test_duplicate_or_wrong_municipalities_rejected():
    polygon = box(-35, -8.2, -34.8, -8)
    with pytest.raises(WorldBuildError, match="Duplicate"):
        AdministrativeLayer(
            [Municipality("2611606", "A", polygon)] * 2,
            polygon,
            WorldConfig().metric_crs,
        )
    with pytest.raises(WorldBuildError, match="Non-Pernambuco"):
        AdministrativeLayer(
            [Municipality("3511606", "A", polygon)], polygon, WorldConfig().metric_crs
        )


def test_ibge_codarea_and_metadata_names(tmp_path):
    layer = make_layer()
    municipalities = [
        {
            "type": "Feature",
            "properties": {"codarea": m.code},
            "geometry": mapping(m.geometry_wgs84),
        }
        for m in layer.municipalities
    ]
    state = [
        {
            "type": "Feature",
            "properties": {"codarea": "26"},
            "geometry": mapping(layer.state_geometry_wgs84),
        }
    ]
    names = [{"id": int(m.code), "nome": m.name} for m in layer.municipalities]
    municipal_path = tmp_path / "municipalities.geojson"
    state_path = tmp_path / "state.geojson"
    names_path = tmp_path / "names.json"
    for path, features in [(municipal_path, municipalities), (state_path, state)]:
        path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    names_path.write_text(json.dumps(names))
    source = IBGESource(state_path, municipal_path, names_path, expected_count=2)
    assert (
        source.load(WorldConfig().metric_crs).municipality_at((-32.4, -3.85)).name
        == "Fernando de Noronha"
    )
    assert len(source.provenance()) == 3
    assert source.provenance()[0].date is None
    municipalities.pop(0)  # Sorted municipality records start with Noronha.
    municipal_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": municipalities})
    )
    with pytest.raises(WorldBuildError, match="Fernando de Noronha"):
        source.load(WorldConfig().metric_crs)


def write_ibge_fixture(tmp_path, *, crs=None, invalid=False):
    layer = make_layer()
    features = [
        {
            "type": "Feature",
            "properties": {"codarea": m.code, "name": m.name},
            "geometry": mapping(m.geometry_wgs84),
        }
        for m in layer.municipalities
    ]
    if invalid:
        features[1]["geometry"] = {
            "type": "Polygon",
            "coordinates": [
                [[-35, -8.2], [-34.8, -8], [-35, -8], [-34.8, -8.2], [-35, -8.2]]
            ],
        }
    states = [
        {
            "type": "Feature",
            "properties": {"codarea": "26"},
            "geometry": mapping(layer.state_geometry_wgs84),
        }
    ]
    paths = [tmp_path / "state.geojson", tmp_path / "municipalities.geojson"]
    for path, rows in zip(paths, (states, features), strict=True):
        data = {"type": "FeatureCollection", "features": rows}
        if crs is not None:
            data["crs"] = {"type": "name", "properties": {"name": crs}}
        path.write_text(json.dumps(data))
    return paths


def test_ibge_completeness_is_explicit(tmp_path):
    state, municipalities = write_ibge_fixture(tmp_path)
    with pytest.raises(WorldBuildError, match="expected 185"):
        IBGESource(state, municipalities).load(WorldConfig().metric_crs)
    assert (
        len(
            IBGESource(state, municipalities, expected_count=2)
            .load(WorldConfig().metric_crs)
            .municipalities
        )
        == 2
    )


def test_ibge_sirgas_and_invalid_geometry_normalization(tmp_path):
    state, municipalities = write_ibge_fixture(tmp_path, crs="EPSG:4674", invalid=True)
    source = IBGESource(state, municipalities, expected_count=2)
    layer = source.load(WorldConfig().metric_crs)
    assert len(layer.warnings) == 1
    assert "Repaired" in layer.warnings[0]
    assert all(m.geometry_wgs84.is_valid for m in layer.municipalities)
    assert layer.municipality_at((-32.4, -3.85)).code == "2605459"
    layer.save(tmp_path)
    assert AdministrativeLayer.load(
        tmp_path, WorldConfig().metric_crs
    ).state_geometry_wgs84.is_valid


@pytest.mark.parametrize("crs", ["EPSG:5880", "not-a-crs"])
def test_ibge_wrong_source_crs_rejected(tmp_path, crs):
    state, municipalities = write_ibge_fixture(tmp_path, crs=crs)
    with pytest.raises(WorldBuildError, match="CRS|coordinates"):
        IBGESource(state, municipalities, expected_count=2).load(
            WorldConfig().metric_crs
        )


def test_facade_rejects_unrecorded_administrative_layer(tmp_path):
    from simgeope.world.exceptions import WorldFormatError
    from simgeope.world.manifest import WorldManifest
    from simgeope.world.model import PernambucoWorld

    config = WorldConfig()
    config.save(tmp_path / "config.json")
    make_layer().save(tmp_path)
    manifest = WorldManifest.create(
        config,
        sources=[],
        feature_counts={"municipalities": 2, "state_boundaries": 1},
        output_dir=tmp_path,
        output_files=[
            "config.json",
            "municipalities.parquet",
            "state_boundary.parquet",
        ],
    )
    manifest.save(tmp_path / "manifest.json")
    world = PernambucoWorld.load(tmp_path)
    assert world.municipality_at((-32.4, -3.85)).code == "2605459"
    with pytest.raises(WorldDataMissingError, match="road layer"):
        world.nearest_road((-34.9, -8.1))
    del manifest.outputs["municipalities.parquet"]
    manifest.save(tmp_path / "manifest.json")
    with pytest.raises(WorldFormatError, match="not recorded"):
        PernambucoWorld.load(tmp_path)
