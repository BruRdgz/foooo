"""Immutable environmental arrays, loaded once for constant-time cell lookup."""

from __future__ import annotations

import json
import math
from dataclasses import KW_ONLY, asdict, dataclass, field, fields
from pathlib import Path
from zipfile import BadZipFile

import numpy as np
from pyproj import CRS

from ..exceptions import (
    PointOutsideWorldError,
    WorldDataMissingError,
    WorldFormatError,
)
from ..spatial import SpatialReference, lon_lat
from .grid import RasterGrid

ENVIRONMENT_FILES = ("environment_grid.json", "environment_arrays.npz")
ENVIRONMENT_FORMAT_VERSION = 1
_BAND_DTYPES = {
    "state_mask": np.dtype("bool"),
    "municipality_code": np.dtype("uint32"),
    "water_distance_m": np.dtype("float32"),
    "wetland_fraction": np.dtype("float32"),
}


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate environmental metadata key: {key}")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class EnvironmentStore:
    """Shareable raster bands with a cell-centre-defined sampling domain.

    A query is accepted when its cell centre was covered by the state boundary
    during preprocessing. Coastal/sliver points can therefore differ from an
    exact vector-boundary lookup. No geometry operations or file reads occur in
    sampling. Passed NumPy arrays are frozen in place rather than copied.
    State-covered centres lacking a canonical municipality retain label zero;
    lookup exposes their municipality as None rather than inventing a label.
    """

    grid: RasterGrid
    _: KW_ONLY
    state_mask: np.ndarray = field(repr=False, compare=False)
    municipality_code: np.ndarray = field(repr=False, compare=False)
    water_distance_m: np.ndarray = field(repr=False, compare=False)
    wetland_fraction: np.ndarray = field(repr=False, compare=False)
    _spatial: SpatialReference = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if not isinstance(self.grid, RasterGrid):
            raise TypeError("EnvironmentStore requires a RasterGrid")
        for name, dtype in _BAND_DTYPES.items():
            array = getattr(self, name)
            if not isinstance(array, np.ndarray):
                raise TypeError(f"Environmental band {name} must be a NumPy array")
            if array.shape != self.grid.shape or array.dtype != dtype:
                raise ValueError(
                    f"Environmental band {name} requires shape {self.grid.shape} and dtype {dtype}"
                )
        mask = self.state_mask
        labels = self.municipality_code
        distance = self.water_distance_m
        fraction = self.wetland_fraction
        if np.any(labels[~mask] != 0) or np.any(
            (labels[mask] != 0)
            & ((labels[mask] < 2_600_000) | (labels[mask] > 2_699_999))
        ):
            raise ValueError(
                "Municipality cells require Pernambuco codes or unknown zero inside and zero outside"
            )
        if not np.isnan(distance[~mask]).all() or not np.isnan(fraction[~mask]).all():
            raise ValueError(
                "Environmental float bands must be NaN outside the state mask"
            )
        water = distance[mask]
        if np.any(np.isinf(water)) or np.any(water < 0):
            raise ValueError(
                "Water distance must be finite and nonnegative, or NaN for unknown"
            )
        wetland = fraction[mask]
        if not np.isfinite(wetland).all() or np.any((wetland < 0) | (wetland > 1)):
            raise ValueError("Wetland fraction must be finite and within [0, 1]")
        object.__setattr__(self, "_spatial", SpatialReference(self.grid.metric_crs))
        for name in _BAND_DTYPES:
            getattr(self, name).setflags(write=False)

    @property
    def feature_counts(self):
        return {
            "environment_cells": self.grid.cell_count,
            "environment_state_cells": int(np.count_nonzero(self.state_mask)),
            "environment_unassigned_municipality_cells": int(
                np.count_nonzero(self.state_mask & (self.municipality_code == 0))
            ),
        }

    def at(self, lon, lat=None):
        coordinates = lon_lat(lon if lat is None else (lon, lat))
        x, y = self._spatial.project(coordinates)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError(
                "Coordinates cannot be projected into the environmental CRS"
            )
        try:
            row, col = self.grid.index(x, y)
        except IndexError as exc:
            raise PointOutsideWorldError(
                "Point is outside the environmental grid"
            ) from exc
        if not self.state_mask[row, col]:
            raise PointOutsideWorldError(
                "Point is outside the environmental cell-centre state mask"
            )
        water = float(self.water_distance_m[row, col])
        municipality = int(self.municipality_code[row, col])
        return {
            "state_mask": True,
            "municipality_code": str(municipality) if municipality else None,
            "water_distance_m": None if math.isnan(water) else water,
            "wetland_fraction": float(self.wetland_fraction[row, col]),
        }

    def save(self, root):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        metadata = {
            "format_version": ENVIRONMENT_FORMAT_VERSION,
            "grid": asdict(self.grid),
        }
        (root / ENVIRONMENT_FILES[0]).write_text(
            json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        np.savez_compressed(
            root / ENVIRONMENT_FILES[1],
            **{name: getattr(self, name) for name in _BAND_DTYPES},
        )

    @classmethod
    def load(cls, root, expected_metric_crs=None, expected_resolution_m=None):
        root = Path(root)
        for name in ENVIRONMENT_FILES:
            if not (root / name).is_file():
                raise WorldDataMissingError(
                    f"Missing environmental file: {root / name}"
                )
        try:
            metadata = json.loads(
                (root / ENVIRONMENT_FILES[0]).read_text(encoding="utf-8"),
                object_pairs_hook=_json_object,
            )
            if not isinstance(metadata, dict) or set(metadata) != {
                "format_version",
                "grid",
            }:
                raise ValueError(
                    "Environmental metadata must contain exactly format_version and grid"
                )
            if (
                type(metadata["format_version"]) is not int
                or metadata["format_version"] != ENVIRONMENT_FORMAT_VERSION
            ):
                raise ValueError("Unsupported environmental format version")
            grid_data = metadata["grid"]
            if not isinstance(grid_data, dict) or set(grid_data) != {
                item.name for item in fields(RasterGrid)
            }:
                raise ValueError("Invalid environmental grid metadata keys")
            grid = RasterGrid(**grid_data)
            if expected_metric_crs is not None and not CRS.from_user_input(
                expected_metric_crs
            ).equals(CRS.from_user_input(grid.metric_crs)):
                raise ValueError("Environmental CRS differs from world configuration")
            if (
                expected_resolution_m is not None
                and grid.resolution_m != expected_resolution_m
            ):
                raise ValueError(
                    "Environmental resolution differs from world configuration"
                )
            with np.load(root / ENVIRONMENT_FILES[1], allow_pickle=False) as arrays:
                if len(arrays.files) != len(_BAND_DTYPES) or set(arrays.files) != set(
                    _BAND_DTYPES
                ):
                    raise ValueError(
                        "Environmental archive must contain exactly the four required bands"
                    )
                loaded = {name: arrays[name] for name in _BAND_DTYPES}
            return cls(grid, **loaded)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            BadZipFile,
            EOFError,
        ) as exc:
            raise WorldFormatError(
                f"Invalid environmental data in {root}: {exc}"
            ) from exc
