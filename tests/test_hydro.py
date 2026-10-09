import json
from dataclasses import replace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyproj import CRS
from shapely import to_wkb
from shapely.geometry import LineString, Point, Polygon, box

from simgeope.world.config import WorldConfig
from simgeope.world.exceptions import (
    WorldBuildError,
    WorldDataMissingError,
    WorldFormatError,
)
from simgeope.world.hydro import (
    HYDRO_FILES,
    HydroFeature,
    HydroLayer,
    RoadHydroCrossing,
)
from simgeope.world.hydro.model import CROSSING_SCHEMA, FEATURE_SCHEMA
from simgeope.world.spatial import SpatialReference

METRIC_CRS = WorldConfig().metric_crs


def feature(id="river-a", kind="river", geometry=None, **kwargs):
    if geometry is None:
        geometry = LineString([(-35, -8.2), (-35, -8)])
    return HydroFeature(id, kind, geometry, **kwargs)


def test_empty_runtime_layer_and_typed_empty_persistence(tmp_path):
    layer = HydroLayer([], METRIC_CRS)
    assert layer.distance_to_water((-35, -8)) is None
    assert layer.nearest_waterbody(Point(-35, -8)) is None
    assert layer.nearest_waterway((-35, -8)) is None
    assert layer.basin_at((-35, -8)) is None
    assert layer.intersections_with_road(4) == ()
    layer.save(tmp_path)
    assert {path.name for path in tmp_path.iterdir()} == set(HYDRO_FILES)
    for filename in HYDRO_FILES:
        table = pq.read_table(tmp_path / filename)
        expected = (
            CROSSING_SCHEMA
            if filename == "road_hydro_crossings.parquet"
            else FEATURE_SCHEMA
        )
        assert table.schema.equals(expected, check_metadata=False)
        assert table.num_rows == 0
    assert HydroLayer.load(tmp_path, METRIC_CRS).features == ()


def test_metric_nearest_distance_water_interior_and_coastline_exclusion():
    spatial = SpatialReference(METRIC_CRS)
    x, y = spatial.project((-35, -8.1))
    river = feature(
        geometry=spatial.geometry_to_wgs84(
            LineString([(x + 1000, y - 100), (x + 1000, y + 100)])
        )
    )
    lake = feature(
        "lake-a",
        "lake",
        spatial.geometry_to_wgs84(box(x + 2000, y - 100, x + 2200, y + 100)),
    )
    coast = feature(
        "coast-a",
        "coastline",
        spatial.geometry_to_wgs84(LineString([(x + 1, y - 100), (x + 1, y + 100)])),
    )
    layer = HydroLayer([coast, lake, river], METRIC_CRS)
    assert layer.distance_to_water((-35, -8.1)) == pytest.approx(1000, abs=1e-5)
    assert layer.nearest_waterway((-35, -8.1)) is river
    assert layer.nearest_waterbody((-35, -8.1)) is lake
    assert layer.distance_to_water(spatial.unproject((x + 2100, y))) == 0
    assert HydroLayer([coast], METRIC_CRS).distance_to_water((-35, -8.1)) is None


def test_metric_nearest_ties_are_deterministic():
    geometry = LineString([(-35, -8.2), (-35, -8)])
    a, z = feature("a", geometry=geometry), feature("z", geometry=geometry)
    assert HydroLayer([z, a], METRIC_CRS).nearest_waterway((-34.9, -8.1)).id == "a"


def test_wetlands_are_water_but_basins_are_not_and_holes_are_respected():
    polygon = Polygon(
        [(-35.2, -8.3), (-34.8, -8.3), (-34.8, -7.9), (-35.2, -7.9)],
        holes=[[(-35.1, -8.2), (-34.9, -8.2), (-34.9, -8), (-35.1, -8)]],
    )
    wetland = feature("wetland", "wetland", polygon)
    basin = feature("basin", "basin", polygon)
    layer = HydroLayer([wetland, basin], METRIC_CRS)
    assert layer.distance_to_water((-35, -8.1)) > 1000
    assert layer.distance_to_water((-35.15, -8.1)) == 0
    assert layer.basin_at((-35, -8.1)) is None
    assert layer.basin_at((-35.15, -8.1)) is basin
    assert layer.basin_at((-35.2, -8.3)) is basin
    assert HydroLayer([basin], METRIC_CRS).distance_to_water((-35.15, -8.1)) is None


@pytest.mark.parametrize(
    "bad",
    [
        feature(kind="stream"),
        feature(id=""),
        feature(geometry=Point(-35, -8)),
        feature("bad", "water", LineString([(-35, -8), (-34, -8)])),
        feature(geometry=LineString([(181, 0), (182, 0)])),
        feature(geometry=LineString([(-35, -8, 2), (-35, -7, 2)])),
        feature(geometry=LineString()),
        feature("bad", "lake", Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])),
        feature(source=""),
        feature(source_id=1),
        feature(municipality_codes=("3511606",)),
        feature(tags={"width": 12}),
    ],
)
def test_invalid_features_rejected(bad):
    with pytest.raises(WorldBuildError):
        HydroLayer([bad], METRIC_CRS)


def test_duplicate_ids_and_nonmetric_crs_rejected():
    with pytest.raises(WorldBuildError, match="Duplicate"):
        HydroLayer([feature(), feature()], METRIC_CRS)
    for crs in ("EPSG:4326", "EPSG:2263"):
        with pytest.raises(WorldBuildError, match="metre"):
            HydroLayer([], crs)


@pytest.mark.parametrize("point", [(float("nan"), -8), (-35, 91), "invalid", Point()])
def test_invalid_query_coordinates_rejected_even_for_empty_layers(point):
    layer = HydroLayer([], METRIC_CRS)
    with pytest.raises(ValueError):
        layer.distance_to_water(point)
    with pytest.raises(ValueError):
        layer.basin_at(point)


def test_roundtrip_provenance_geometry_crossings_and_deterministic_files(tmp_path):
    river = feature(
        name="Rio",
        source_id="way/44",
        source_date="2026-10-01",
        source_file="pinned.osm.pbf",
        municipality_codes=("2611606", "2604106"),
        tags={"waterway": "river", "name": "Rio"},
    )
    crossing = RoadHydroCrossing(3, river.id, river.kind, True, -35, -8.1)
    lake = feature("lake", "reservoir", box(-34.9, -8.2, -34.8, -8))
    layer = HydroLayer([river, lake], METRIC_CRS, [crossing])
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    layer.save(root_a)
    HydroLayer(
        [lake, replace(river, tags=dict(reversed(list(river.tags.items()))))],
        METRIC_CRS,
        [crossing],
    ).save(root_b)
    for name in HYDRO_FILES:
        assert (root_a / name).read_bytes() == (root_b / name).read_bytes()
    restored = HydroLayer.load(root_a, METRIC_CRS)
    restored_river = restored.nearest_waterway((-35, -8.1))
    assert restored_river.geometry_wgs84.equals(river.geometry_wgs84)
    assert restored_river.source_id == "way/44"
    assert restored_river.source_date == "2026-10-01"
    assert restored_river.source_file == "pinned.osm.pbf"
    assert restored_river.municipality_codes == ("2604106", "2611606")
    assert restored_river.tags == river.tags
    assert restored.intersections_with_road(3) == (crossing,)
    assert restored.intersections_with_road(999) == ()
    for name in HYDRO_FILES[:-1]:
        geo = json.loads(pq.read_schema(root_a / name).metadata[b"geo"])
        assert CRS.from_user_input(geo["columns"]["geometry"]["crs"]).equals(
            CRS.from_epsg(4326)
        )


@pytest.mark.parametrize(
    "change",
    [
        {"hydro_feature_id": "missing"},
        {"hydro_feature_id": []},
        {"hydro_feature_kind": "canal"},
        {"road_edge_id": -1},
        {"bridge_tag": "yes"},
        {"intersection_lon": -34.5},
        {"intersection_lat": float("nan")},
    ],
)
def test_invalid_crossings_rejected(change):
    crossing = RoadHydroCrossing(3, "river-a", "river", True, -35, -8.1)
    with pytest.raises(WorldBuildError):
        HydroLayer([feature()], METRIC_CRS, [replace(crossing, **change)])


def test_duplicate_crossings_rejected():
    crossing = RoadHydroCrossing(3, "river-a", "river", True, -35, -8.1)
    with pytest.raises(WorldBuildError, match="Duplicate"):
        HydroLayer([feature()], METRIC_CRS, [crossing, crossing])


def test_projected_crossing_roundtrips_and_coastline_and_basins_cannot_cross(tmp_path):
    river = feature()
    spatial = SpatialReference(METRIC_CRS)
    projected = spatial.geometry_to_metric(river.geometry_wgs84).interpolate(
        0.5, normalized=True
    )
    lon, lat = spatial.unproject((projected.x, projected.y))
    crossing = RoadHydroCrossing(3, river.id, river.kind, False, lon, lat)
    HydroLayer([river], METRIC_CRS, [crossing]).save(tmp_path)
    assert HydroLayer.load(tmp_path, METRIC_CRS).crossings == (crossing,)
    for kind, geometry in [
        ("coastline", river.geometry_wgs84),
        ("basin", box(-36, -9, -34, -7)),
    ]:
        hydro = feature("excluded", kind, geometry)
        excluded_crossing = RoadHydroCrossing(3, hydro.id, kind, False, -35, -8.1)
        with pytest.raises(WorldBuildError):
            HydroLayer([hydro], METRIC_CRS, [excluded_crossing])


def test_missing_or_partial_layer_is_explicit(tmp_path):
    with pytest.raises(WorldDataMissingError):
        HydroLayer.load(tmp_path, METRIC_CRS)
    HydroLayer([], METRIC_CRS).save(tmp_path)
    (tmp_path / "basins.parquet").unlink()
    with pytest.raises(WorldDataMissingError, match="basins"):
        HydroLayer.load(tmp_path, METRIC_CRS)


@pytest.mark.parametrize(
    "corruption",
    [
        "schema",
        "crs",
        "null_id",
        "kind",
        "geometry",
        "tags",
        "provenance",
        "crossing_reference",
    ],
)
def test_corrupt_persistence_rejected(tmp_path, corruption):
    crossing = RoadHydroCrossing(3, "river-a", "river", True, -35, -8.1)
    HydroLayer([feature()], METRIC_CRS, [crossing]).save(tmp_path)
    path = tmp_path / (
        "road_hydro_crossings.parquet"
        if corruption == "crossing_reference"
        else "waterways.parquet"
    )
    table = pq.read_table(path)
    if corruption == "schema":
        table = table.drop(["source"])
    elif corruption == "crs":
        metadata = dict(table.schema.metadata)
        geo = json.loads(metadata[b"geo"])
        geo["columns"]["geometry"]["crs"] = CRS.from_epsg(3857).to_json_dict()
        metadata[b"geo"] = json.dumps(geo).encode()
        table = table.replace_schema_metadata(metadata)
    else:
        rows = table.to_pylist()
        changes = {
            "null_id": ("id", None),
            "kind": ("kind", "lake"),
            "geometry": ("geometry", to_wkb(Point(-35, -8))),
            "tags": ("tags", "[]"),
            "provenance": ("source", ""),
            "crossing_reference": ("hydro_feature_id", "missing"),
        }
        key, value = changes[corruption]
        rows[0][key] = value
        schema = table.schema
        if corruption == "null_id":
            schema = schema.set(
                schema.get_field_index("id"), pa.field("id", pa.string())
            )
        table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path)
    with pytest.raises(WorldFormatError):
        HydroLayer.load(tmp_path, METRIC_CRS)
