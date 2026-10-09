"""Environmental sampling, immutable bands and strict persistence integrity."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest

from simgeope.world.exceptions import (
    PointOutsideWorldError,
    WorldDataMissingError,
    WorldFormatError,
)
from simgeope.world.raster import ENVIRONMENT_FILES, EnvironmentStore, RasterGrid
from simgeope.world.spatial import SpatialReference


def bands():
    return {
        "state_mask": np.array(
            [[True, False, False], [False, False, True]], dtype=bool
        ),
        "municipality_code": np.array(
            [[2611606, 0, 0], [0, 0, 2605459]], dtype=np.uint32
        ),
        "water_distance_m": np.array(
            [[12.5, np.nan, np.nan], [np.nan, np.nan, np.nan]], dtype=np.float32
        ),
        "wetland_fraction": np.array(
            [[0.25, np.nan, np.nan], [np.nan, np.nan, 0]], dtype=np.float32
        ),
    }


@pytest.fixture
def store():
    return EnvironmentStore(RasterGrid(0, 2000, 1000, 2, 3, "EPSG:3857"), **bands())


def wgs_cell(store, row, col):
    return SpatialReference(store.grid.metric_crs).unproject(
        store.grid.cell_center(row, col)
    )


def test_samples_tuple_and_two_numbers_with_unknown_water(store):
    point = wgs_cell(store, 0, 0)
    assert (
        store.at(point)
        == store.at(*point)
        == {
            "state_mask": True,
            "municipality_code": "2611606",
            "water_distance_m": 12.5,
            "wetland_fraction": 0.25,
        }
    )
    assert store.at(wgs_cell(store, 1, 2)) == {
        "state_mask": True,
        "municipality_code": "2605459",
        "water_distance_m": None,
        "wetland_fraction": 0.0,
    }
    assert store.feature_counts == {
        "environment_cells": 6,
        "environment_state_cells": 2,
        "environment_unassigned_municipality_cells": 0,
    }


def test_mask_holes_and_outside_extent_raise_explicitly(store):
    with pytest.raises(PointOutsideWorldError, match="mask"):
        store.at(wgs_cell(store, 0, 1))
    with pytest.raises(PointOutsideWorldError, match="grid"):
        store.at(1, 1)


def test_state_covered_unknown_municipality_remains_explicit(store, tmp_path):
    values = bands()
    values["municipality_code"][0, 0] = 0
    unknown = EnvironmentStore(store.grid, **values)
    assert unknown.at(wgs_cell(unknown, 0, 0))["municipality_code"] is None
    assert unknown.feature_counts["environment_unassigned_municipality_cells"] == 1
    unknown.save(tmp_path)
    loaded = EnvironmentStore.load(tmp_path)
    assert loaded.at(wgs_cell(loaded, 0, 0))["municipality_code"] is None
    assert loaded.feature_counts == unknown.feature_counts


@pytest.mark.parametrize(
    "point", [(181, 0), (0, 91), (0, -91), (float("nan"), 0), (0,), "0,0"]
)
def test_invalid_wgs_coordinates(store, point):
    with pytest.raises(ValueError):
        store.at(point)


def test_read_only_no_heavy_copy():
    inputs = bands()
    store = EnvironmentStore(RasterGrid(0, 2000, 1000, 2, 3, "EPSG:3857"), **inputs)
    for name, original in inputs.items():
        assert getattr(store, name) is original
        assert not original.flags.writeable
        with pytest.raises(ValueError):
            original[0, 0] = 0
    with pytest.raises(FrozenInstanceError):
        store.grid = RasterGrid(0, 1, 1, 1, 1, "EPSG:3857")


def test_sampling_performs_no_file_io_after_loading(store, tmp_path, monkeypatch):
    store.save(tmp_path)
    loaded = EnvironmentStore.load(tmp_path)
    point = wgs_cell(loaded, 0, 0)

    def forbid_io(*args, **kwargs):
        raise AssertionError("Per-query file I/O")

    monkeypatch.setattr(np, "load", forbid_io)
    monkeypatch.setattr(Path, "read_text", forbid_io)
    monkeypatch.setattr("builtins.open", forbid_io)
    for _ in range(10):
        assert loaded.at(point)["water_distance_m"] == 12.5


def test_save_load_roundtrip_and_byte_determinism(store, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    store.save(first)
    store.save(second)
    assert {path.name for path in first.iterdir()} == set(ENVIRONMENT_FILES)
    for name in ENVIRONMENT_FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    loaded = EnvironmentStore.load(
        first, expected_metric_crs="EPSG:3857", expected_resolution_m=1000
    )
    assert loaded.grid == store.grid
    for name in bands():
        np.testing.assert_array_equal(getattr(loaded, name), getattr(store, name))
        assert not getattr(loaded, name).flags.writeable
    assert loaded.at(wgs_cell(store, 0, 0)) == store.at(wgs_cell(store, 0, 0))


@pytest.mark.parametrize("name", ENVIRONMENT_FILES)
def test_missing_file_explicit(store, tmp_path, name):
    store.save(tmp_path)
    (tmp_path / name).unlink()
    with pytest.raises(WorldDataMissingError, match=name):
        EnvironmentStore.load(tmp_path)


@pytest.mark.parametrize("name", ENVIRONMENT_FILES)
def test_corrupt_file_explicit(store, tmp_path, name):
    store.save(tmp_path)
    (tmp_path / name).write_bytes(b"broken content")
    with pytest.raises(WorldFormatError):
        EnvironmentStore.load(tmp_path)


@pytest.mark.parametrize(
    "expected", [{"expected_resolution_m": 999}, {"expected_metric_crs": "EPSG:32624"}]
)
def test_configuration_mismatch(store, tmp_path, expected):
    store.save(tmp_path)
    with pytest.raises(WorldFormatError, match="configuration"):
        EnvironmentStore.load(tmp_path, **expected)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(extra=1),
        lambda data: data.update(format_version=True),
        lambda data: data.update(format_version=99),
        lambda data: data["grid"].update(extra=1),
        lambda data: data["grid"].update(rows=2_000_001),
        lambda data: data["grid"].update(rows=True),
        lambda data: data["grid"].update(resolution_m=0),
        lambda data: data["grid"].update(origin_x_m=float("inf")),
        lambda data: data["grid"].update(metric_crs="EPSG:4326"),
    ],
)
def test_metadata_validation(store, tmp_path, mutation):
    store.save(tmp_path)
    path = tmp_path / ENVIRONMENT_FILES[0]
    data = json.loads(path.read_text())
    mutation(data)
    path.write_text(json.dumps(data))
    with pytest.raises(WorldFormatError):
        EnvironmentStore.load(tmp_path)


def test_duplicate_metadata_keys_rejected(store, tmp_path):
    store.save(tmp_path)
    path = tmp_path / ENVIRONMENT_FILES[0]
    path.write_text(
        path.read_text().replace(
            '"format_version":1', '"format_version":1,"format_version":1'
        )
    )
    with pytest.raises(WorldFormatError, match="Duplicate"):
        EnvironmentStore.load(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda values: values.update(extra=np.zeros((2, 3))),
        lambda values: values.pop("water_distance_m"),
        lambda values: values.update(state_mask=np.ones((1, 6), dtype=bool)),
        lambda values: values.update(
            municipality_code=values["municipality_code"].astype(np.int64)
        ),
        lambda values: values.update(
            water_distance_m=values["water_distance_m"].astype(np.float64)
        ),
        lambda values: values["municipality_code"].__setitem__((0, 0), 3511606),
        lambda values: values["municipality_code"].__setitem__((0, 1), 2611606),
        lambda values: values["water_distance_m"].__setitem__((0, 0), -1),
        lambda values: values["water_distance_m"].__setitem__((0, 0), np.inf),
        lambda values: values["water_distance_m"].__setitem__((0, 1), 0),
        lambda values: values["wetland_fraction"].__setitem__((0, 0), np.nan),
        lambda values: values["wetland_fraction"].__setitem__((0, 0), 1.1),
        lambda values: values["wetland_fraction"].__setitem__((0, 1), 0),
        lambda values: values.update(state_mask=np.array([[object()]], dtype=object)),
    ],
)
def test_array_archive_validation(store, tmp_path, mutation):
    store.save(tmp_path)
    values = bands()
    mutation(values)
    np.savez_compressed(tmp_path / ENVIRONMENT_FILES[1], **values)
    with pytest.raises(WorldFormatError):
        EnvironmentStore.load(tmp_path)


def test_constructor_validates_arrays_before_freezing(store):
    values = bands()
    values["wetland_fraction"][0, 0] = -1
    with pytest.raises(ValueError):
        EnvironmentStore(store.grid, **values)
    assert all(value.flags.writeable for value in values.values())
