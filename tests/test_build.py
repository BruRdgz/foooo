"""Transactional publication and source-aware cache integration."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from simgeope.world.build import OUTPUT_FILES, build_world
from simgeope.world.exceptions import WorldBuildError, WorldDataMissingError
from simgeope.world.manifest import SourceRecord, WorldManifest


@pytest.fixture
def build_inputs(tmp_path, monkeypatch, tiny_world):
    raw = tmp_path / "input.geojson"
    raw.write_text("pinned admin bytes")
    pbf = tmp_path / "nordeste-260930.osm.pbf"
    pbf.write_bytes(b"pinned osm bytes")
    config = replace(
        tiny_world.config, osm_pbf=str(pbf), output_dir=str(tmp_path / "published")
    )
    source = SimpleNamespace(
        year=None,
        load=lambda: tiny_world.administrative,
        provenance=lambda: (SourceRecord.from_path(raw),),
    )
    monkeypatch.setattr(
        "simgeope.world.build.IBGESource.from_config", lambda config: source
    )
    calls = []

    def build_roads(configuration, administrative):
        assert administrative is tiny_world.administrative
        assert configuration.output_dir != config.output_dir
        assert not (tmp_path / "published").exists()
        calls.append(configuration)
        tiny_world.roads.save(configuration.output_dir)
        return tiny_world.roads

    monkeypatch.setattr("simgeope.world.roads.build.build_from_config", build_roads)
    return config, raw, calls


def test_clean_build_publishes_and_reuses_only_matching_cache(
    build_inputs, monkeypatch
):
    config, raw, calls = build_inputs
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790726400")
    destination = build_world(config)
    assert len(calls) == 1
    assert set(OUTPUT_FILES).issubset(file.name for file in destination.iterdir())
    manifest = WorldManifest.load(destination / "manifest.json")
    assert manifest.configuration_hash == config.configuration_hash
    assert manifest.feature_counts["road_edges"] > 0
    assert any("vintage is unknown" in warning for warning in manifest.warnings)
    before = (destination / "manifest.json").read_bytes()
    assert build_world(config) == destination
    assert len(calls) == 1
    raw.write_text("changed raw data with same filename")
    with pytest.raises(WorldBuildError, match="different inputs"):
        build_world(config)
    assert (destination / "manifest.json").read_bytes() == before
    assert not list(destination.parent.glob(".published-build-*"))


def test_failed_build_removes_only_private_staging(build_inputs, monkeypatch):
    config, raw, _ = build_inputs

    def fail(*args):
        raise RuntimeError("deliberate extraction failure")

    monkeypatch.setattr("simgeope.world.roads.build.build_from_config", fail)
    with pytest.raises(WorldBuildError, match="deliberate extraction failure"):
        build_world(config)
    assert raw.read_text() == "pinned admin bytes"
    from pathlib import Path

    destination = Path(config.output_dir)
    assert not destination.exists()
    assert not list(destination.parent.glob(".published-build-*"))


def test_build_missing_pbf_is_explicit(build_inputs):
    config, _, _ = build_inputs
    with pytest.raises(WorldDataMissingError, match="osm_pbf"):
        build_world(replace(config, osm_pbf=None))


def test_build_imports_and_records_static_facilities(build_inputs, tiny_world):
    config, _, _ = build_inputs
    source = config.output_dir.rsplit("/", 1)[0] + "/facilities.csv"
    from pathlib import Path

    Path(source).write_text("id,kind,lon,lat,name\nclinic,health,-35,-8,Clinic\n")
    destination = build_world(replace(config, facilities_file=source))
    from simgeope.world.manifest import WorldManifest
    from simgeope.world.model import PernambucoWorld

    world = PernambucoWorld.load(destination)
    assert world.facilities.get("clinic").name == "Clinic"
    manifest = WorldManifest.load(destination / "manifest.json")
    assert manifest.feature_counts["facilities"] == 1
    assert "facilities.parquet" in manifest.outputs
    assert any(item.filename == "facilities.csv" for item in manifest.sources)
