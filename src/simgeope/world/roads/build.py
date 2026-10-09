"""Build roads without discarding source attributes or disconnected components.

Simplification collapses the interior nodes of each OSM way between junctions.
Way boundaries are retained so attributes/provenance never need ambiguous merges.
"""

import math
import re
from collections import Counter

from pyproj import Transformer
from shapely.geometry import LineString
from shapely.ops import transform

from ..exceptions import WorldBuildError
from .extract import extract_roads
from .model import RoadNetwork

DEFAULT_SPEEDS_KMH = {
    "motorway": 100,
    "trunk": 80,
    "primary": 70,
    "secondary": 60,
    "tertiary": 50,
    "unclassified": 40,
    "residential": 30,
    "service": 20,
}


def parse_maxspeed(value):
    if value is None:
        return None
    speeds = []
    for token in value.split(";"):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?\s*", token)
        if match:
            speed = float(match.group(1)) * (1.609344 if match.group(2) == "mph" else 1)
            if speed > 0 and math.isfinite(speed):
                speeds.append(speed)
    return min(speeds) if speeds else None


def _positive_int(value):
    try:
        result = int(value)
        return result if result > 0 else None
    except (ValueError, TypeError):
        return None


def _tag_bool(value):
    return value is not None and value not in {"no", "false", "0"}


def build_roads(
    ways,
    output_dir,
    metric_crs,
    administrative=None,
    source_date=None,
    source_file=None,
):
    ways = sorted(ways, key=lambda way: (way.id, way.node_ids))
    for way in ways:
        if len(way.node_ids) < 2 or len(way.node_ids) != len(way.coordinates):
            raise WorldBuildError(
                f"Road way {way.id} has inconsistent nodes/coordinates"
            )
    # Occurrences, including repeated nodes in a way, identify real junctions.
    occurrence = Counter(node for way in ways for node in way.node_ids)
    endpoints = {node for way in ways for node in (way.node_ids[0], way.node_ids[-1])}
    break_nodes = endpoints | {node for node, count in occurrence.items() if count > 1}
    project = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True).transform
    nodes, edges, dropped, reversible = {}, [], 0, 0
    for way in ways:
        highway = way.tags.get("highway")
        base_class = highway.removesuffix("_link") if highway else ""
        oneway_tag = way.tags.get("oneway", "").lower()
        if oneway_tag in {"reversible", "alternating"}:
            reversible += 1
            continue  # A static snapshot cannot assume a permitted direction.
        if oneway_tag in {"yes", "true", "1"}:
            directions = (False,)
        elif oneway_tag == "-1":
            directions = (True,)
        elif oneway_tag in {"no", "false", "0"}:
            directions = (False, True)
        elif way.tags.get("junction") == "roundabout" or base_class == "motorway":
            directions = (False,)
        else:
            directions = (False, True)
        begin = 0
        for end in range(1, len(way.node_ids)):
            if way.node_ids[end] not in break_nodes:
                continue
            coords = way.coordinates[begin : end + 1]
            geometry = LineString(coords)
            length = float(transform(project, geometry).length)
            u, v = way.node_ids[begin], way.node_ids[end]
            begin = end
            if not math.isfinite(length) or length <= 0 or not geometry.is_valid:
                dropped += 1
                continue
            for node, coordinate in ((u, coords[0]), (v, coords[-1])):
                x, y = project(*coordinate)
                if (
                    node in nodes
                    and (nodes[node]["lon"], nodes[node]["lat"]) != coordinate
                ):
                    raise WorldBuildError(
                        f"Conflicting coordinates for OSM road node {node}"
                    )
                nodes[node] = dict(
                    id=node, lon=coordinate[0], lat=coordinate[1], x_m=x, y_m=y
                )
            municipality_codes = (
                administrative.municipality_codes_for_geometry(geometry)
                if administrative is not None
                else ()
            )
            for reverse in directions:
                speed_tag = way.tags.get(
                    "maxspeed:backward" if reverse else "maxspeed:forward",
                    way.tags.get("maxspeed"),
                )
                maxspeed = parse_maxspeed(speed_tag)
                speed = maxspeed or DEFAULT_SPEEDS_KMH.get(base_class, 30)
                edges.append(
                    dict(
                        id=len(edges),
                        u=v if reverse else u,
                        v=u if reverse else v,
                        length_m=length,
                        base_travel_time_s=length / (speed / 3.6),
                        highway=highway,
                        ref=way.tags.get("ref"),
                        name=way.tags.get("name"),
                        maxspeed_kmh=maxspeed,
                        speed_kmh=float(speed),
                        lanes=_positive_int(way.tags.get("lanes")),
                        surface=way.tags.get("surface"),
                        bridge=_tag_bool(way.tags.get("bridge")),
                        tunnel=_tag_bool(way.tags.get("tunnel")),
                        oneway=len(directions) == 1,
                        municipality_codes=municipality_codes,
                        source="osm",
                        source_id=way.id,
                        source_date=source_date,
                        source_file=source_file,
                        geometry=LineString(tuple(reversed(coords)))
                        if reverse
                        else geometry,
                    )
                )
    if not edges:
        raise WorldBuildError(
            "No usable roads matched the configured source, extent and classes"
        )
    network = RoadNetwork.from_records(
        list(nodes.values()), edges, metric_crs=metric_crs
    )
    network.dropped_invalid_geometry_count = dropped
    network.build_warnings = tuple(
        message
        for message in (
            f"Dropped {dropped} zero-length/invalid road segments" if dropped else None,
            f"Excluded {reversible} reversible/alternating ways without a static travel direction"
            if reversible
            else None,
            "OSM turn restrictions and conditional access/speed tags are not modeled; routing uses directed edges",
        )
        if message
    )
    if output_dir is not None:
        network.save(output_dir)
    return network


def build_from_config(config, administrative):
    extracted = extract_roads(
        config.osm_pbf,
        administrative.state_geometry_wgs84,
        config.border_padding_m,
        config.metric_crs,
        config.road_classes,
    )
    network = build_roads(
        extracted.ways,
        config.output_dir,
        config.metric_crs,
        administrative,
        extracted.source_date,
        extracted.source_file,
    )
    network.build_warnings = extracted.warnings + network.build_warnings
    network.dropped_invalid_geometry_count += extracted.dropped_invalid_geometry_count
    return network
