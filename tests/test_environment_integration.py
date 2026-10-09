"""Transactional raster extension and manifest-backed public sampling."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_hydro_integration import hydro_inputs as hydro_inputs

from simgeope import PernambucoWorld
from simgeope.world.build import build_world
from simgeope.world.exceptions import (
    WorldBuildError,
    WorldDataMissingError,
    WorldFormatError,
)
from simgeope.world.manifest import WorldManifest
from simgeope.world.raster import ENVIRONMENT_FILES, EnvironmentStore


def test_phase5_reuses_verified_hydro_and_preserves_base(hydro_inputs, monkeypatch):
    config, _, _ = hydro_inputs
    base_config = replace(config, hydro_enabled=True)
    base = build_world(base_config)
    before = {file.name: file.read_bytes() for file in base.iterdir() if file.is_file()}

    def unexpected(*args, **kwargs):
        raise AssertionError("Repeated extraction of verified base layers")

    monkeypatch.setattr("simgeope.world.roads.build.build_from_config", unexpected)
    monkeypatch.setattr("simgeope.world.sources.hydro.OSMHydroSource", unexpected)
    config = replace(
        base_config,
        environment_enabled=True,
        raster_resolution_m=750,
        output_dir=str(base.parent / "phase5"),
    )
    root = build_world(config, base_world=base)
    assert {
        file.name: file.read_bytes() for file in base.iterdir() if file.is_file()
    } == before
    manifest = WorldManifest.load(root / "manifest.json")
    assert manifest.feature_counts["environment_state_cells"] > 0
    assert manifest.feature_counts["environment_unassigned_municipality_cells"] == 0
    world = PernambucoWorld.load(root)
    sample = world.environment_at((-35, -8))
    assert sample == world.environment.at(-35, -8)
    assert sample["municipality_code"] == "2611606"
    assert sample["water_distance_m"] >= 0
    assert sample["wetland_fraction"] == 0
    assert world.route(10, 30).travel_time_s == 20
    assert world.hydro.intersections_with_road(1)
    assert world.environment.grid.resolution_m == 750
    assert build_world(config) == root
    assert PernambucoWorld.load(base).environment is None
    script = """
import importlib.abc, sys
class Forbid(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'osmium', 'geopandas', 'osmnx', 'networkx', 'rasterio'}:
            raise ImportError(fullname)
sys.meta_path.insert(0, Forbid())
from simgeope import PernambucoWorld
world = PernambucoWorld.load(sys.argv[1])
assert world.environment_at((-35,-8))['municipality_code'] == '2611606'
assert world.hydro.intersections_with_road(1)
assert world.route(10,30).travel_time_s == 20
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src")),
    )
    assert result.returncode == 0, result.stderr


def test_facilities_can_extend_verified_phase5_world(
    hydro_inputs, monkeypatch, tmp_path
):
    config, _, _ = hydro_inputs
    base = build_world(replace(config, hydro_enabled=True, environment_enabled=True))

    def unexpected(*args, **kwargs):
        raise AssertionError("Verified Phase 5 layers must be reused")

    monkeypatch.setattr("simgeope.world.roads.build.build_from_config", unexpected)
    monkeypatch.setattr("simgeope.world.sources.hydro.OSMHydroSource", unexpected)
    facility_source = tmp_path / "facilities.csv"
    facility_source.write_text("id,kind,lon,lat\nclinic,health,-35,-8\n")
    output = base.parent / "phase5-with-facilities"
    extended = build_world(
        replace(
            config,
            hydro_enabled=True,
            environment_enabled=True,
            facilities_file=str(facility_source),
            output_dir=str(output),
        ),
        base_world=base,
    )
    world = PernambucoWorld.load(extended)
    assert world.facilities.get("clinic").road_node == 10
    assert world.environment_at((-35, -8)) == PernambucoWorld.load(base).environment_at(
        (-35, -8)
    )
    assert all((extended / name).is_file() for name in ENVIRONMENT_FILES)


def test_fresh_full_phase5_build(hydro_inputs):
    config, _, _ = hydro_inputs
    root = build_world(replace(config, hydro_enabled=True, environment_enabled=True))
    assert (
        PernambucoWorld.load(root).environment_at((-35, -8))["municipality_code"]
        == "2611606"
    )


@pytest.mark.parametrize("change", ["hydro_settings", "source", "checksum"])
def test_phase5_rejects_incompatible_base(hydro_inputs, change):
    config, _, raw = hydro_inputs
    base_config = replace(config, hydro_enabled=True)
    base = build_world(base_config)
    config = replace(
        base_config, environment_enabled=True, output_dir=str(base.parent / "phase5")
    )
    if change == "hydro_settings":
        config = replace(config, hydro_min_waterbody_area_m2=0)
    elif change == "source":
        raw.write_text("changed source")
    else:
        (base / "waterways.parquet").write_bytes(b"changed hydro output")
    with pytest.raises((WorldBuildError, WorldFormatError)):
        build_world(config, base_world=base)
    assert not Path(config.output_dir).exists()


def test_failed_raster_keeps_base_and_cleans_staging(hydro_inputs, monkeypatch):
    config, _, _ = hydro_inputs
    base_config = replace(config, hydro_enabled=True)
    base = build_world(base_config)
    before = (base / "manifest.json").read_bytes()

    def fail(*args, **kwargs):
        raise RuntimeError("deliberate raster failure")

    monkeypatch.setattr("simgeope.world.raster.rasterize.build_environment", fail)
    newer = replace(
        base_config, environment_enabled=True, output_dir=str(base.parent / "phase5")
    )
    with pytest.raises(WorldBuildError, match="deliberate raster failure"):
        build_world(newer, base_world=base)
    assert (base / "manifest.json").read_bytes() == before
    assert not Path(newer.output_dir).exists()
    assert not list(base.parent.glob(".phase5-build-*"))


def test_environment_facade_overloads_for_facilities(tiny_world):
    from simgeope.world.facilities import Facility
    from simgeope.world.hydro import HydroLayer
    from simgeope.world.raster import build_environment

    tiny_world.environment = build_environment(
        tiny_world.administrative,
        HydroLayer((), tiny_world.config.metric_crs),
        metric_crs=tiny_world.config.metric_crs,
        resolution_m=1000,
    )
    assert tiny_world.environment_at("a") == tiny_world.environment_at(
        Facility("a", "hub", -35, -8)
    )
    assert tiny_world.environment_at("a")["water_distance_m"] is None


@pytest.mark.parametrize("fault", ["counts", "crs", "labels", "partial", "disabled"])
def test_environment_facade_rejects_inconsistency(hydro_inputs, fault):
    config, _, _ = hydro_inputs
    config = replace(config, hydro_enabled=True, environment_enabled=True)
    root = build_world(config)
    manifest = WorldManifest.load(root / "manifest.json")
    if fault == "counts":
        values = manifest.to_dict()
        values["feature_counts"]["environment_state_cells"] = 999
        (root / "manifest.json").write_text(json.dumps(values))
        with pytest.raises(WorldFormatError, match="Environmental counts"):
            PernambucoWorld.load(root)
        with pytest.raises(WorldFormatError, match="Manifest count"):
            build_world(config)
        return
    if fault == "partial":
        (root / ENVIRONMENT_FILES[1]).unlink()
        with pytest.raises(WorldDataMissingError):
            PernambucoWorld.load(root)
        return
    if fault == "crs":
        file = root / ENVIRONMENT_FILES[0]
        values = json.loads(file.read_text())
        values["grid"]["metric_crs"] = "EPSG:31985"
        file.write_text(json.dumps(values))
    elif fault == "labels":
        store = EnvironmentStore.load(root)
        codes = store.municipality_code.copy()
        codes[store.state_mask] = 2611111
        EnvironmentStore(
            store.grid,
            state_mask=store.state_mask,
            municipality_code=codes,
            water_distance_m=store.water_distance_m,
            wetland_fraction=store.wetland_fraction,
        ).save(root)
    else:
        config = replace(config, environment_enabled=False)
        config.save(root / "config.json")
    updated = WorldManifest.create(
        config,
        sources=manifest.sources,
        output_dir=root,
        output_files=manifest.outputs,
        feature_counts=manifest.feature_counts,
    )
    updated.save(root / "manifest.json")
    with pytest.raises(WorldFormatError):
        PernambucoWorld.load(root)


def test_cache_rejects_unrecorded_environment_files(hydro_inputs):
    config, _, _ = hydro_inputs
    config = replace(config, hydro_enabled=True)
    root = build_world(config)
    (root / ENVIRONMENT_FILES[0]).write_text("{}")
    with pytest.raises(WorldFormatError, match="environmental outputs"):
        build_world(config)
