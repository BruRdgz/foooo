"""Bounded, globally aligned metric grid with a north-west origin."""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real

from pyproj import CRS
from pyproj.exceptions import CRSError

MAX_ENVIRONMENT_CELLS = 2_000_000


def _finite_number(value, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


@dataclass(frozen=True, slots=True)
class RasterGrid:
    """Cell rows run southwards; left/top are inclusive, right/bottom exclusive."""

    origin_x_m: float
    origin_y_m: float
    resolution_m: float
    rows: int
    cols: int
    metric_crs: str

    def __post_init__(self):
        for name in ("origin_x_m", "origin_y_m", "resolution_m"):
            object.__setattr__(self, name, _finite_number(getattr(self, name), name))
        if self.resolution_m <= 0:
            raise ValueError("resolution_m must be positive")
        if any(
            type(value) is not int or value <= 0 for value in (self.rows, self.cols)
        ):
            raise ValueError("Raster rows and cols must be positive integers")
        if self.cell_count > MAX_ENVIRONMENT_CELLS:
            raise ValueError(f"Raster exceeds the {MAX_ENVIRONMENT_CELLS} cell budget")
        if not isinstance(self.metric_crs, str) or not self.metric_crs.strip():
            raise ValueError("metric_crs must be a CRS string")
        try:
            crs = CRS.from_user_input(self.metric_crs)
        except CRSError as exc:
            raise ValueError("metric_crs must be valid") from exc
        if (
            not crs.is_projected
            or len(crs.axis_info) != 2
            or any(axis.unit_conversion_factor != 1.0 for axis in crs.axis_info)
        ):
            raise ValueError("metric_crs must be projected with metre units")
        if not all(math.isfinite(value) for value in self.bounds):
            raise ValueError("Raster extent must be finite")
        left, bottom, right, top = self.bounds
        if right <= left or bottom >= top:
            raise ValueError("Raster cells must have representable positive dimensions")

    @classmethod
    def from_bounds(
        cls, bounds, resolution_m, metric_crs, max_cells=MAX_ENVIRONMENT_CELLS
    ):
        """Enclose metric bounds on the global resolution lattice, before allocation."""
        if type(max_cells) is not int or not 0 < max_cells <= MAX_ENVIRONMENT_CELLS:
            raise ValueError(
                f"max_cells must be an integer in [1, {MAX_ENVIRONMENT_CELLS}]"
            )
        try:
            values = tuple(bounds)
        except TypeError as exc:
            raise ValueError("bounds must contain left, bottom, right, top") from exc
        if len(values) != 4:
            raise ValueError("bounds must contain left, bottom, right, top")
        left, bottom, right, top = (_finite_number(value, "bounds") for value in values)
        resolution = _finite_number(resolution_m, "resolution_m")
        if resolution <= 0 or right <= left or top <= bottom:
            raise ValueError("Resolution and bounds dimensions must be positive")
        scaled = tuple(value / resolution for value in (left, bottom, right, top))
        if not all(math.isfinite(value) for value in scaled):
            raise ValueError("Raster bounds/resolution exceed supported dimensions")
        left_index, bottom_index = map(math.floor, scaled[:2])
        right_index, top_index = map(math.ceil, scaled[2:])
        rows, cols = top_index - bottom_index, right_index - left_index
        if rows * cols > max_cells:
            raise ValueError(f"Raster exceeds the {max_cells} cell budget")
        return cls(
            left_index * resolution,
            top_index * resolution,
            resolution,
            rows,
            cols,
            metric_crs,
        )

    @property
    def shape(self):
        return self.rows, self.cols

    @property
    def cell_count(self):
        return self.rows * self.cols

    @property
    def bounds(self):
        return (
            self.origin_x_m,
            self.origin_y_m - self.rows * self.resolution_m,
            self.origin_x_m + self.cols * self.resolution_m,
            self.origin_y_m,
        )

    def cell_center(self, row, col):
        if (
            type(row) is not int
            or type(col) is not int
            or not 0 <= row < self.rows
            or not 0 <= col < self.cols
        ):
            raise IndexError("Raster row/column is outside the grid")
        return (
            self.origin_x_m + (col + 0.5) * self.resolution_m,
            self.origin_y_m - (row + 0.5) * self.resolution_m,
        )

    def index(self, x, y):
        x, y = _finite_number(x, "x"), _finite_number(y, "y")
        left, bottom, right, top = self.bounds
        if not left <= x < right or not bottom < y <= top:
            raise IndexError("Metric point is outside the raster grid")
        row = min(math.floor((top - y) / self.resolution_m), self.rows - 1)
        col = min(math.floor((x - left) / self.resolution_m), self.cols - 1)
        return row, col
