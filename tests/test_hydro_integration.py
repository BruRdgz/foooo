"""Phase 4 transactional builds, verified reuse and runtime-only facade loading."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import LineString

from simgeope import PernambucoWorld
from simgeope.world.build import OUTPUT_FILES, build_world
from simgeope.world.exceptions import WorldBuildError, WorldFormatError
from simgeope.world.hydro import HYDRO_FILES, HydroFeature, HydroLayer
from simgeope.world.manifest import SourceRecord, WorldManifest


@pytest.fixture
def hydro_inputs(tmp_path, monkeypatch, tiny_world):
    raw = tmp_path / "admin.geojson"
    raw.write_bytes(b"pinned administrative input")
    pbf = tmp_path / "nordeste-260930.osm.pbf"
    pbf.write_bytes(b"mocked source bytes")
    config = replace(
        tiny_world.config, osm_pbf=str(pbf), output_dir=str(tmp_path / "phase3")
    )
    source = SimpleNamespace(
        year=None,
        load=lambda: tiny_world.administrative,
        provenance=lambda: (SourceRecord.from_path(raw),),
    )
    monkeypatch.setattr(
        "simgeope.world.build.IBGESource.from_config", lambda config: source
    )

    def roads(configuration, administrative):
        tiny_world.roads.save(configuration.output_dir)
        return tiny_world.roads

    monkeypatch.setattr("simgeope.world.roads.build.build_from_config", roads)
    feature = HydroFeature(
        "osm:way:42",
        "river",
        LineString([(-34.995, -8.02), (-34.995, -7.99)]),
        source_id="way/42",
        source_date="2026-09-30",
        source_file=pbf.name,
    )

    class MockHydroSource:
        warnings = ("fixture hydrology warning",)
        dropped_invalid_geometry_count = 0

        def __init__(self, *args, **kwargs):
            pass

        def load_waterways(self):
            return (feature,)

        def load_waterbodies(self):
            return ()

        load_wetlands = load_waterbodies
        load_coastline = load_waterbodies
        load_basins = load_waterbodies

    monkeypatch.setattr("simgeope.world.sources.hydro.OSMHydroSource", MockHydroSource)
    return config, tiny_world, raw


def test_phase4_build_reuses_verified_roads_and_keeps_phase3(hydro_inputs, monkeypatch):
    config, _, _ = hydro_inputs
    base = build_world(config)
    before = {file.name: file.read_bytes() for file in base.iterdir() if file.is_file()}

    def unexpected(*args):
        raise AssertionError("Road extraction repeated during verified reuse")

    monkeypatch.setattr("simgeope.world.roads.build.build_from_config", unexpected)
    config = replace(config, hydro_enabled=True, output_dir=str(base.parent / "phase4"))
    path = build_world(config, base_world=base)
    assert {
        file.name: file.read_bytes() for file in base.iterdir() if file.is_file()
    } == before
    assert all(
        (path / filename).is_file() for filename in (*OUTPUT_FILES, *HYDRO_FILES)
    )
    manifest = WorldManifest.load(path / "manifest.json")
    assert manifest.feature_counts["waterways"] == 1
    assert manifest.feature_counts["basins"] == 0
    assert manifest.feature_counts["road_hydro_crossings"] > 0
    assert "fixture hydrology warning" in manifest.warnings
    world = PernambucoWorld.load(path)
    assert world.hydro.nearest_waterway((-34.995, -8)).id == "osm:way:42"
    assert world.hydro.distance_to_water((-34.995, -8)) < 1
    assert world.hydro.intersections_with_road(1)
    assert world.hydro.basin_at((-34.995, -8)) is None
    assert world.route(10, 30).travel_time_s == 20
    assert build_world(config) == path
    assert PernambucoWorld.load(base).hydro is None
    script = """
import importlib.abc, sys
class Forbid(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'osmium', 'geopandas', 'osmnx', 'networkx'}:
            raise ImportError(fullname)
sys.meta_path.insert(0, Forbid())
from simgeope import PernambucoWorld
world = PernambucoWorld.load(sys.argv[1])
assert world.hydro.nearest_waterway((-34.995, -8)).id == 'osm:way:42'
assert world.hydro.intersections_with_road(1)
assert world.route(10, 30).travel_time_s == 20
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src")),
    )
    assert result.returncode == 0, result.stderr


def test_phase4_fresh_build(hydro_inputs):
    config, _, _ = hydro_inputs
    path = build_world(replace(config, hydro_enabled=True))
    assert PernambucoWorld.load(path).hydro.intersections_with_road(1)


@pytest.mark.parametrize("change", ["sources", "config", "checksum", "counts"])
def test_base_mismatch_rejected_before_publication(hydro_inputs, change):
    config, _, raw = hydro_inputs
    base = build_world(config)
    newer = replace(config, hydro_enabled=True, output_dir=str(base.parent / "new"))
    if change == "sources":
        raw.write_bytes(b"changed source")
    elif change == "config":
        newer = replace(newer, border_padding_m=123)
    elif change == "checksum":
        (base / "roads_routing.npz").write_bytes(b"tampered cache")
    else:
        file = base / "manifest.json"
        values = json.loads(file.read_text())
        values["feature_counts"]["road_edges"] = 999
        file.write_text(json.dumps(values))
    with pytest.raises((WorldBuildError, WorldFormatError)):
        build_world(newer, base_world=base)
    assert not Path(newer.output_dir).exists()
    assert not list(base.parent.glob(".new-build-*"))


def test_hydro_failure_keeps_base_and_cleans_staging(hydro_inputs, monkeypatch):
    config, _, _ = hydro_inputs
    base = build_world(config)
    before = (base / "manifest.json").read_bytes()
    newer = replace(config, hydro_enabled=True, output_dir=str(base.parent / "new"))

    def fail(*args, **kwargs):
        raise RuntimeError("hydro extraction failed")

    monkeypatch.setattr("simgeope.world.sources.hydro.OSMHydroSource", fail)
    with pytest.raises(WorldBuildError, match="hydro extraction failed"):
        build_world(newer, base_world=base)
    assert not Path(newer.output_dir).exists()
    assert not list(base.parent.glob(".new-build-*"))
    assert (base / "manifest.json").read_bytes() == before


def test_facade_rejects_unmanifested_hydrology(tiny_world):
    HydroLayer((), tiny_world.config.metric_crs).save(tiny_world.path)
    with pytest.raises(WorldFormatError, match="hydro_enabled"):
        PernambucoWorld.load(tiny_world.path)


def test_facade_rejects_wrong_hydro_counts(hydro_inputs):
    config, _, _ = hydro_inputs
    path = build_world(replace(config, hydro_enabled=True))
    file = path / "manifest.json"
    values = json.loads(file.read_text())
    values["feature_counts"]["waterways"] = 999
    file.write_text(json.dumps(values))
    with pytest.raises(WorldFormatError, match="Hydrology count"):
        PernambucoWorld.load(path)


def test_cache_rejects_incorrect_hydrology_counts(hydro_inputs):
    config, _, _ = hydro_inputs
    config = replace(config, hydro_enabled=True)
    path = build_world(config)
    file = path / "manifest.json"
    values = json.loads(file.read_text())
    values["feature_counts"]["waterways"] = 999
    file.write_text(json.dumps(values))
    with pytest.raises(WorldFormatError, match="Manifest count"):
        build_world(config)


def test_facade_rejects_crossing_with_unknown_road(hydro_inputs):
    config, _, _ = hydro_inputs
    config = replace(config, hydro_enabled=True)
    path = build_world(config)
    world = PernambucoWorld.load(path)
    crossings = (replace(world.hydro.crossings[0], road_edge_id=999),)
    HydroLayer(world.hydro.features, config.metric_crs, crossings).save(path)
    manifest = WorldManifest.create(
        config,
        sources=world.manifest.sources,
        output_dir=path,
        output_files=world.manifest.outputs,
        feature_counts={**world.manifest.feature_counts, "road_hydro_crossings": 1},
    )
    manifest.save(path / "manifest.json")
    with pytest.raises(WorldFormatError, match="unknown road edge"):
        PernambucoWorld.load(path)
