"""Shared primitive coordinate operations; no build-time GIS dependencies."""

from __future__ import annotations

import math

from pyproj import Transformer
from shapely.geometry import Point
from shapely.ops import transform


def lon_lat(point) -> tuple[float, float]:
    if isinstance(point, Point):
        if point.is_empty:
            raise ValueError("Expected a nonempty point")
        values = point.x, point.y
    else:
        if isinstance(point, (str, bytes)):
            raise ValueError("Expected a (longitude, latitude) pair")
        try:
            values = tuple(point)
        except TypeError as exc:
            raise ValueError("Expected a (longitude, latitude) pair") from exc
    if len(values) != 2:
        raise ValueError("Expected a (longitude, latitude) pair")
    lon, lat = map(float, values)
    if (
        not math.isfinite(lon)
        or not math.isfinite(lat)
        or not -180 <= lon <= 180
        or not -90 <= lat <= 90
    ):
        raise ValueError("Coordinates must be finite WGS84 longitude/latitude")
    return lon, lat


class SpatialReference:
    """Cached projections for a world's configurable metric coordinate system."""

    def __init__(self, metric_crs: str):
        self.metric_crs = metric_crs
        self._to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
        self._to_wgs84 = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)

    def project(self, point) -> tuple[float, float]:
        return self._to_metric.transform(*lon_lat(point))

    def unproject(self, point) -> tuple[float, float]:
        return self._to_wgs84.transform(*point)

    def geometry_to_metric(self, geometry):
        return transform(self._to_metric.transform, geometry)

    def geometry_to_wgs84(self, geometry):
        return transform(self._to_wgs84.transform, geometry)
