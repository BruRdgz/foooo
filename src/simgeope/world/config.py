"""Validated, reproducible configuration for preprocessing and runtime queries."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from pyproj import CRS
from pyproj.exceptions import CRSError

DEFAULT_METRIC_CRS = (
    "+proj=tmerc +lat_0=0 +lon_0=-37.5 +k=1 +x_0=500000 "
    "+y_0=10000000 +ellps=GRS80 +units=m +no_defs"
)


@dataclass(frozen=True, slots=True)
class WorldConfig:
    metric_crs: str = DEFAULT_METRIC_CRS
    storage_crs: str = "EPSG:4326"
    raster_resolution_m: float = 1000.0
    road_classes: tuple[str, ...] = (
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "unclassified",
    )
    border_padding_m: float = 10000.0
    max_snap_distance_m: float = 20000.0
    allow_excessive_snap: bool = False
    environment_enabled: bool = False
    hydro_enabled: bool = False
    hydro_min_waterbody_area_m2: float = 10000.0
    hydro_include_named_streams: bool = False
    ibge_year: int | None = None
    ibge_municipalities: str | None = None
    ibge_state: str | None = None
    ibge_names: str | None = None
    osm_pbf: str | None = None
    osm_url: str | None = None
    facilities_file: str | None = None
    output_dir: str = "data/world/pe_v1"
    cache_dir: str = "data/raw/ibge"

    def __post_init__(self) -> None:
        try:
            metric = CRS.from_user_input(self.metric_crs)
            storage = CRS.from_user_input(self.storage_crs)
        except CRSError as exc:
            raise ValueError("World CRS definitions must be valid") from exc
        if not metric.is_projected or any(
            axis.unit_conversion_factor != 1.0 for axis in metric.axis_info
        ):
            raise ValueError("metric_crs must be a projected CRS with metre units")
        if not storage.equals(CRS.from_epsg(4326)):
            raise ValueError("Portable vector storage must use EPSG:4326")
        for name in ("raster_resolution_m", "max_snap_distance_m"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be finite and positive")
        if (
            isinstance(self.border_padding_m, bool)
            or not isinstance(self.border_padding_m, (int, float))
            or not math.isfinite(self.border_padding_m)
            or self.border_padding_m < 0
        ):
            raise ValueError("border_padding_m must be finite and nonnegative")
        for name in (
            "environment_enabled",
            "hydro_enabled",
            "hydro_include_named_streams",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        if self.environment_enabled and not self.hydro_enabled:
            raise ValueError("environment_enabled requires hydro_enabled")
        area = self.hydro_min_waterbody_area_m2
        if (
            isinstance(area, bool)
            or not isinstance(area, (int, float))
            or not math.isfinite(area)
            or area < 0
        ):
            raise ValueError(
                "hydro_min_waterbody_area_m2 must be finite and nonnegative"
            )
        if not isinstance(self.allow_excessive_snap, bool):
            raise ValueError("allow_excessive_snap must be boolean")
        if not isinstance(self.road_classes, (list, tuple)):
            raise ValueError("road_classes must be a sequence of road class names")
        classes = tuple(self.road_classes)
        if not classes or any(not isinstance(c, str) or not c for c in classes):
            raise ValueError("road_classes must contain nonempty road class names")
        if len(set(classes)) != len(classes):
            raise ValueError("road_classes cannot contain duplicates")
        object.__setattr__(self, "road_classes", classes)
        if self.osm_url is not None and (
            not isinstance(self.osm_url, str) or not self.osm_url.strip()
        ):
            raise ValueError("osm_url must be a nonempty string or null")
        for name in (
            "ibge_municipalities",
            "ibge_state",
            "ibge_names",
            "osm_pbf",
            "facilities_file",
            "output_dir",
            "cache_dir",
        ):
            value = getattr(self, name)
            if value is None and name not in ("output_dir", "cache_dir"):
                continue
            if not isinstance(value, (str, Path)) or not str(value).strip():
                raise ValueError(f"{name} must be a nonempty path")
            object.__setattr__(self, name, str(value))
        if self.ibge_year is not None and (
            type(self.ibge_year) is not int or self.ibge_year < 2000
        ):
            raise ValueError("ibge_year must be a dataset year >= 2000")

    def to_dict(self) -> dict:
        result = asdict(self)
        result["road_classes"] = list(self.road_classes)
        # Omit additive defaults so existing Phase 3 configuration hashes remain valid.
        for name, default in (
            ("facilities_file", None),
            ("environment_enabled", False),
            ("hydro_enabled", False),
            ("hydro_min_waterbody_area_m2", 10000.0),
            ("hydro_include_named_streams", False),
        ):
            if result[name] == default:
                result.pop(name)
        return result

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    @property
    def configuration_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.canonical_json() + "\n", encoding="utf-8")

    @classmethod
    def from_dict(cls, values: dict) -> WorldConfig:
        if not isinstance(values, dict):
            raise ValueError("World configuration must be a JSON object")
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown world configuration keys: {sorted(unknown)}")
        return cls(**values)

    @classmethod
    def load(cls, path: str | Path) -> WorldConfig:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
