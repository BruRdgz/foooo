"""Bounded, read-only HTTP views over an injected, already-loaded world."""

import json
import math
import re
from dataclasses import fields, is_dataclass
from pathlib import Path

from shapely import get_num_coordinates
from shapely.geometry import Point, box, mapping
from shapely.strtree import STRtree

from simgeope.world.exceptions import (
    NoRoadNearbyError,
    PointOutsideWorldError,
    RouteNotFoundError,
    WorldDataMissingError,
)
from simgeope.world.model import PernambucoWorld
from simgeope.world.roads import RoadRuntimeState
from simgeope.world.spatial import lon_lat

MAX_FEATURES = 5000
MAX_CELLS = 2000
MAX_COORDINATES = 100_000
MAX_OVERLAY_EDGES = 1000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _properties(record):
    result = (
        {
            field.name: getattr(record, field.name)
            for field in fields(record)
            if field.name != "geometry_wgs84"
        }
        if is_dataclass(record)
        else dict(record)
    )
    for name in ("source_file", "filename"):
        if result.get(name):
            # Preserve provenance filenames without server filesystem locations.
            result[name] = str(result[name]).replace("\\", "/").rsplit("/", 1)[-1]
    return result


def _feature(geometry, properties):
    return {"type": "Feature", "geometry": mapping(geometry), "properties": properties}


def _collection(records, viewport=None, limit=MAX_FEATURES):
    features, vertices, byte_count, truncated = [], 0, 0, False
    tolerance = (
        max(
            viewport.bounds[2] - viewport.bounds[0],
            viewport.bounds[3] - viewport.bounds[1],
        )
        / 2000
        if viewport is not None
        else 0.0002
    )
    for geometry, properties in records:
        if len(features) >= limit:
            truncated = True
            break
        if viewport is not None:
            geometry = geometry.intersection(viewport)
        if geometry.is_empty:
            continue
        geometry = geometry.simplify(tolerance, preserve_topology=True)
        count = int(get_num_coordinates(geometry))
        if vertices + count > MAX_COORDINATES:
            truncated = True
            break
        feature = _feature(geometry, properties)
        size = len(json.dumps(feature, ensure_ascii=True).encode("utf-8"))
        if byte_count + size > MAX_RESPONSE_BYTES:
            truncated = True
            break
        vertices += count
        byte_count += size
        features.append(feature)
    return {
        "type": "FeatureCollection",
        "features": features,
        "truncated": truncated,
        "limit": limit,
    }


def create_app(world: PernambucoWorld):
    """Create the optional viewer without loading worlds or build-time tools."""
    try:
        from fastapi import FastAPI, HTTPException, Query, Request
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
        from starlette.concurrency import run_in_threadpool
    except ImportError as exc:
        raise RuntimeError(
            "The viewer requires optional dependencies. Install with "
            "`pip install 'simgeope[viewer]'` or run `uv run --extra viewer simgeope serve`."
        ) from exc

    app = FastAPI(title="SimGeoPE world inspector", docs_url=None, redoc_url=None)
    web = Path(__file__).resolve().parent.parent / "web"
    app.mount("/static", StaticFiles(directory=web), name="static")
    state_geometry = world.administrative.state_geometry_wgs84
    hydro_features = world.hydro.features if world.hydro is not None else ()
    hydro_tree = STRtree([feature.geometry_wgs84 for feature in hydro_features])
    crossings = world.hydro.crossings if world.hydro is not None else ()
    crossing_points = [Point(c.intersection_lon, c.intersection_lat) for c in crossings]
    crossing_tree = STRtree(crossing_points)

    def viewport(value):
        try:
            bounds = tuple(float(item) for item in value.split(","))
            if len(bounds) != 4:
                raise ValueError()
            west, south = lon_lat(bounds[:2])
            east, north = lon_lat(bounds[2:])
            if west >= east or south >= north or east - west > 12 or north - south > 12:
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(
                422,
                "bbox must be minLon,minLat,maxLon,maxLat, ordered finite WGS84 coordinates, with spans at most 12 degrees",
            ) from None
        geometry = box(west, south, east, north)
        if not geometry.intersects(state_geometry):
            raise HTTPException(404, "Viewport is outside this world")
        return geometry

    def point(value):
        try:
            if (
                not isinstance(value, (list, tuple))
                or len(value) != 2
                or any(
                    isinstance(item, bool) or not isinstance(item, (int, float))
                    for item in value
                )
            ):
                raise ValueError()
            result = lon_lat(value)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(
                422, "Expected a finite WGS84 [longitude, latitude] pair"
            ) from None
        if not state_geometry.covers(Point(result)):
            raise HTTPException(404, "Point is outside this world")
        return result

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    @app.get("/api/world/metadata")
    def metadata():
        manifest = world.manifest
        return {
            "bounds": state_geometry.bounds,
            "crs": "EPSG:4326",
            "metric_crs": world.config.metric_crs,
            "layers": {
                name: getattr(world, name) is not None
                for name in ("roads", "hydro", "facilities", "environment")
            },
            "limits": {
                "features": MAX_FEATURES,
                "environment_cells": MAX_CELLS,
                "bbox_span_degrees": 12,
            },
            "build_timestamp": manifest.build_timestamp if manifest else None,
            "world_format_version": manifest.world_format_version if manifest else None,
            "feature_counts": manifest.feature_counts if manifest else {},
            "warnings": [
                re.sub(
                    r"(?<![:\w/])(?:[A-Za-z]:[\\/]|/)[^\s]+",
                    lambda match: match.group(0).replace("\\", "/").rsplit("/", 1)[-1],
                    warning[:2000],
                )
                for warning in manifest.warnings[:100]
            ]
            if manifest
            else [],
            "sources": [_properties(source) for source in manifest.sources]
            if manifest
            else [],
            "limitations": [
                "Environmental display samples actual grid cells; gaps at overview zoom are intentional.",
                "MapLibre CDN and OpenStreetMap basemap require browser internet.",
            ],
        }

    @app.get("/api/world/municipalities")
    def municipalities():
        return _collection(
            (m.geometry_wgs84, {"code": m.code, "name": m.name})
            for m in world.administrative.municipalities
        )

    @app.get("/api/world/roads")
    def roads(bbox: str, limit: int = Query(MAX_FEATURES, ge=1, le=MAX_FEATURES)):
        view = viewport(bbox)
        records = world.roads_within(view) if world.roads is not None else ()
        return _collection(
            ((world.roads.geometry(edge.id), _properties(edge)) for edge in records),
            view,
            limit,
        )

    @app.get("/api/world/hydro")
    def hydro(bbox: str, limit: int = Query(MAX_FEATURES, ge=1, le=MAX_FEATURES)):
        view = viewport(bbox)

        def records():
            for index in sorted(hydro_tree.query(view, predicate="intersects")):
                feature = hydro_features[int(index)]
                yield feature.geometry_wgs84, dict(_properties(feature), layer="hydro")
            for index in sorted(crossing_tree.query(view, predicate="intersects")):
                yield (
                    crossing_points[int(index)],
                    dict(_properties(crossings[int(index)]), layer="crossings"),
                )

        return _collection(records(), view, limit)

    @app.get("/api/world/facilities")
    def facilities(bbox: str, limit: int = Query(MAX_FEATURES, ge=1, le=MAX_FEATURES)):
        view = viewport(bbox)
        records = world.facilities_within(view) if world.facilities is not None else ()
        return _collection(
            ((Point(f.lon, f.lat), _properties(f)) for f in records), view, limit
        )

    @app.get("/api/world/environment")
    def environment(bbox: str, limit: int = Query(MAX_CELLS, ge=1, le=MAX_CELLS)):
        view = viewport(bbox)
        store = world.environment
        if store is None:
            return _collection((), view, limit)
        grid = store.grid
        metric = world.spatial.geometry_to_metric(
            view.segmentize(
                max(view.bounds[2] - view.bounds[0], view.bounds[3] - view.bounds[1])
                / 16
            )
        )
        left, bottom, right, top = metric.bounds
        resolution = grid.resolution_m
        col0 = max(0, min(grid.cols, math.floor((left - grid.origin_x_m) / resolution)))
        col1 = max(0, min(grid.cols, math.ceil((right - grid.origin_x_m) / resolution)))
        row0 = max(0, min(grid.rows, math.floor((grid.origin_y_m - top) / resolution)))
        row1 = max(
            0, min(grid.rows, math.ceil((grid.origin_y_m - bottom) / resolution))
        )
        stride = max(
            1, math.ceil(math.sqrt(max(0, (row1 - row0) * (col1 - col0)) / limit))
        )
        while (
            math.ceil((row1 - row0) / stride) * math.ceil((col1 - col0) / stride)
            > limit
        ):
            stride += 1

        def records():
            for row in range(row0, row1, stride):
                for col in range(col0, col1, stride):
                    if not store.state_mask[row, col]:
                        continue
                    x, y = grid.cell_center(row, col)
                    geometry = world.spatial.geometry_to_wgs84(
                        box(
                            x - resolution / 2,
                            y - resolution / 2,
                            x + resolution / 2,
                            y + resolution / 2,
                        )
                    )
                    if not geometry.intersects(view):
                        continue
                    water = float(store.water_distance_m[row, col])
                    code = int(store.municipality_code[row, col])
                    yield (
                        geometry,
                        {
                            "row": row,
                            "col": col,
                            "municipality_code": str(code) if code else None,
                            "water_distance_m": water if math.isfinite(water) else None,
                            "wetland_fraction": float(store.wetland_fraction[row, col]),
                        },
                    )

        result = _collection(records(), view, limit)
        result.update(sample_stride=stride, resolution_m=resolution, sampled=stride > 1)
        return result

    @app.get("/api/query/point")
    def query_point(lon: float, lat: float):
        location = point((lon, lat))
        municipality = world.municipality_at(location)
        result = {
            "lon": lon,
            "lat": lat,
            "municipality": {"code": municipality.code, "name": municipality.name}
            if municipality
            else None,
        }
        try:
            result["nearest_road"] = _properties(world.nearest_road(location))
        except (NoRoadNearbyError, WorldDataMissingError):
            result["nearest_road"] = None
        result["facilities"] = (
            [_properties(f) for f in world.nearby_facilities(location, 10_000)[:100]]
            if world.facilities is not None
            else []
        )
        result["hydro"] = None
        if world.hydro is not None:
            nearest = world.hydro.nearest_waterway(location)
            result["hydro"] = {
                "distance_to_water_m": world.hydro.distance_to_water(location),
                "nearest_waterway": _properties(nearest) if nearest else None,
            }
        try:
            result["environment"] = world.environment_at(location)
        except (WorldDataMissingError, PointOutsideWorldError):
            result["environment"] = None
        return result

    @app.post("/api/route")
    async def route(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 32_768:
                raise HTTPException(413, "Route request exceeds 32 KiB")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise HTTPException(422, "Expected a JSON route request") from None
        if not isinstance(payload, dict) or set(payload) - {
            "origin",
            "destination",
            "closed_edges",
            "cost_multipliers",
        }:
            raise HTTPException(
                422,
                "Expected origin, destination and optional closed_edges/cost_multipliers",
            )
        origin = point(payload.get("origin"))
        destination = point(payload.get("destination"))
        closed = payload.get("closed_edges", [])
        costs = payload.get("cost_multipliers", {})
        if (
            not isinstance(closed, list)
            or not isinstance(costs, dict)
            or len(closed) + len(costs) > MAX_OVERLAY_EDGES
        ):
            raise HTTPException(422, "Overlay must contain at most 1000 edge entries")
        if any(type(edge) is not int or not 0 <= edge <= 2**63 - 1 for edge in closed):
            raise HTTPException(422, "Closed edge IDs must be nonnegative integers")
        converted = {}
        for edge, cost in costs.items():
            if (
                not isinstance(edge, str)
                or not edge.isascii()
                or not edge.isdigit()
                or len(edge) > 19
                or int(edge) > 2**63 - 1
                or isinstance(cost, bool)
                or not isinstance(cost, (int, float))
                or cost <= 0
                or cost > 1e12
                or not math.isfinite(cost)
            ):
                raise HTTPException(
                    422,
                    "Cost multipliers require integer edge keys and positive finite numbers at most 1e12",
                )
            converted[int(edge)] = cost
        try:
            overlay = RoadRuntimeState(frozenset(closed), converted)
            result = await run_in_threadpool(
                world.route,
                origin,
                destination,
                state=overlay if closed or costs else None,
            )
        except (NoRoadNearbyError, RouteNotFoundError):
            raise HTTPException(
                404,
                "No road route connects these points within the configured snap distance",
            ) from None
        except WorldDataMissingError:
            raise HTTPException(404, "This world has no road layer") from None
        except ValueError:
            raise HTTPException(
                422, "Overlay references unknown edges or invalid effective costs"
            ) from None
        if len(result.edge_ids) > 20_000:
            raise HTTPException(413, "Route exceeds the viewer response budget")
        coordinates = []
        for edge in result.edge_ids:
            segment = list(world.roads.geometry(edge).coords)
            coordinates.extend(segment if not coordinates else segment[1:])
            if len(coordinates) > MAX_COORDINATES:
                raise HTTPException(
                    413, "Route geometry exceeds the viewer response budget"
                )
        if coordinates:
            geometry = {"type": "LineString", "coordinates": coordinates}
        else:
            node = world.roads.node(result.node_ids[0])
            geometry = {"type": "Point", "coordinates": [node.lon, node.lat]}
        return {
            "type": "Feature",
            "geometry": geometry,
            "properties": _properties(result),
        }

    return app
