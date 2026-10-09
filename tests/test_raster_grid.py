"""Metric grid alignment, bounds, indexing and allocation safeguards."""

from dataclasses import FrozenInstanceError

import pytest

from simgeope.world.raster import RasterGrid


def test_global_alignment_negative_bounds_and_centres():
    grid = RasterGrid.from_bounds((-1.1, -21, 20.1, 1.1), 10, "EPSG:3857")
    assert grid.bounds == (-10, -30, 30, 10)
    assert grid.shape == (4, 4)
    assert grid.cell_count == 16
    assert grid.cell_center(0, 0) == (-5, 5)
    assert grid.cell_center(3, 3) == (25, -25)
    for row in range(grid.rows):
        for col in range(grid.cols):
            assert grid.index(*grid.cell_center(row, col)) == (row, col)
    with pytest.raises(FrozenInstanceError):
        grid.rows = 3


def test_already_aligned_extent_and_half_open_edges():
    grid = RasterGrid.from_bounds((0, 0, 20, 20), 10, "EPSG:3857")
    assert grid.shape == (2, 2)
    assert grid.index(0, 20) == (0, 0)
    assert grid.index(10, 10) == (1, 1)
    assert grid.index(19.99, 0.01) == (1, 1)
    for point in ((20, 10), (10, 0), (-0.01, 10), (10, 20.01)):
        with pytest.raises(IndexError):
            grid.index(*point)


@pytest.mark.parametrize("value", [float("inf"), float("nan"), True, "1", None])
def test_invalid_metric_coordinates(value):
    grid = RasterGrid(0, 20, 10, 2, 2, "EPSG:3857")
    with pytest.raises(ValueError):
        grid.index(value, 10)
    with pytest.raises(ValueError):
        grid.index(10, value)


@pytest.mark.parametrize(
    "row,col", [(-1, 0), (2, 0), (0, 2), (0, -1), (True, 0), (0, 1.0)]
)
def test_invalid_cell_index(row, col):
    with pytest.raises(IndexError):
        RasterGrid(0, 20, 10, 2, 2, "EPSG:3857").cell_center(row, col)


@pytest.mark.parametrize(
    "bounds,resolution",
    [
        ((0, 0, 0, 1), 1),
        ((0, 1, 2, 0), 1),
        ((0, 0, 1), 1),
        ((0, 0, float("inf"), 1), 1),
        ((0, 0, 1, 1), 0),
        ((0, 0, 1, 1), -1),
        ((0, 0, 1, 1), True),
        ((0, 0, 1e300, 1), 1e-300),
    ],
)
def test_invalid_bounds_and_resolution(bounds, resolution):
    with pytest.raises(ValueError):
        RasterGrid.from_bounds(bounds, resolution, "EPSG:3857")


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:2263", "not-a-crs", None])
def test_requires_projected_metre_crs(crs):
    with pytest.raises(ValueError):
        RasterGrid(0, 20, 10, 2, 2, crs)


@pytest.mark.parametrize("dimension", [0, -1, True, 2.0, 2_000_001])
def test_invalid_dimensions_and_default_budget(dimension):
    with pytest.raises(ValueError):
        RasterGrid(0, 20, 10, dimension, 1, "EPSG:3857")


def test_budget_checked_before_allocating():
    with pytest.raises(ValueError, match="budget"):
        RasterGrid.from_bounds((0, 0, 10_000, 10_000), 1, "EPSG:3857")
    with pytest.raises(ValueError, match="budget"):
        RasterGrid.from_bounds((0, 0, 30, 30), 10, "EPSG:3857", max_cells=8)
    assert (
        RasterGrid.from_bounds((0, 0, 30, 30), 10, "EPSG:3857", max_cells=9).cell_count
        == 9
    )


@pytest.mark.parametrize("max_cells", [0, -1, True, 1.0, 2_000_001])
def test_invalid_budget(max_cells):
    with pytest.raises(ValueError, match="max_cells"):
        RasterGrid.from_bounds((0, 0, 10, 10), 10, "EPSG:3857", max_cells=max_cells)


def test_unrepresentable_extent_rejected():
    with pytest.raises(ValueError):
        RasterGrid(1e300, 1e300, 1e-300, 1, 1, "EPSG:3857")
