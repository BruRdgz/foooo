import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely import to_wkb
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    Point,
    Polygon,
    box,
)

from simgeope.world.exceptions import WorldBuildError
from simgeope.world.hydro.crossings import _intersection_points, build_crossings
from simgeope.world.hydro.model import LAYER_KINDS, HydroFeature, HydroLayer
from simgeope.world.spatial import SpatialReference

CRS = "EPSG:3857"
ROAD_SCHEMA = pa.schema(
    [("id", pa.int64()), ("bridge", pa.bool_()), ("geometry", pa.binary())]
)


def write_roads(tmp_path, records, schema=ROAD_SCHEMA):
    path = tmp_path / "roads_edges.parquet"
    rows = [
        {
            "id": edge_id,
            "bridge": bridge,
            "geometry": to_wkb(geometry) if geometry is not None else None,
        }
        for edge_id, bridge, geometry in records
    ]
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return path


def test_point_intersection_uses_actual_location_and_bridge_tag(tmp_path):
    road = LineString([(-0.002, 0), (0.002, 0)])
    feature = HydroFeature(
        "river", "river", LineString([(0.001, -0.001), (0.001, 0.001)])
    )
    result = build_crossings(write_roads(tmp_path, [(5, True, road)]), [feature], CRS)
    assert len(result) == 1
    crossing = result[0]
    assert crossing.road_edge_id == 5
    assert crossing.hydro_feature_id == "river"
    assert crossing.hydro_feature_kind == "river"
    assert crossing.bridge_tag is True
    assert (crossing.intersection_lon, crossing.intersection_lat) == pytest.approx(
        (0.001, 0), abs=1e-12
    )
    assert crossing.intersection_lon != pytest.approx(
        road.interpolate(0.5, normalized=True).x
    )
    layer = HydroLayer([feature], CRS, result)
    assert layer.intersections_with_road(5) == result


def test_multipoint_intersections_keep_each_point(tmp_path):
    road = LineString([(-0.003, 0), (0.003, 0)])
    river = MultiLineString(
        [
            [(-0.002, -0.001), (-0.002, 0.001)],
            [(0.001, -0.001), (0.001, 0.001)],
        ]
    )
    result = build_crossings(
        write_roads(tmp_path, [(0, False, road)]),
        [HydroFeature("river", "river", river)],
        CRS,
    )
    assert len(result) == 2
    assert [row.intersection_lon for row in result] == pytest.approx([-0.002, 0.001])
    assert all(
        row.intersection_lat == pytest.approx(0) and not row.bridge_tag
        for row in result
    )


def test_coincident_overlap_preserves_directed_ids_and_deduplicates_components(
    tmp_path,
):
    road = LineString([(-0.003, 0), (0.003, 0)])
    overlap = [(-0.002, 0), (0, 0)]
    feature = HydroFeature("canal", "canal", MultiLineString([overlap, overlap]))
    path = write_roads(
        tmp_path, [(9, False, road), (4, True, LineString(list(road.coords)[::-1]))]
    )
    result = build_crossings(path, [feature], CRS)
    assert [row.road_edge_id for row in result] == [4, 9]
    assert [row.bridge_tag for row in result] == [True, False]
    assert [row.intersection_lon for row in result] == pytest.approx([-0.001, -0.001])
    assert result[0].intersection_lon == result[1].intersection_lon
    assert result[0].intersection_lat == result[1].intersection_lat


def test_polygon_hole_keeps_disconnected_overlap_midpoints(tmp_path):
    polygon = Polygon(
        [(-0.003, -0.003), (0.003, -0.003), (0.003, 0.003), (-0.003, 0.003)],
        holes=[[(-0.001, -0.001), (0.001, -0.001), (0.001, 0.001), (-0.001, 0.001)]],
    )
    road = LineString([(-0.004, 0), (0.004, 0)])
    result = build_crossings(
        write_roads(tmp_path, [(1, False, road)]),
        [HydroFeature("lake", "lake", polygon)],
        CRS,
    )
    assert len(result) == 2
    assert [row.intersection_lon for row in result] == pytest.approx([-0.002, 0.002])
    assert all(
        polygon.covers(Point(row.intersection_lon, row.intersection_lat))
        for row in result
    )
    inside_hole = LineString([(-0.0005, 0), (0.0005, 0)])
    assert (
        build_crossings(
            write_roads(tmp_path, [(1, False, inside_hole)]),
            [HydroFeature("lake", "lake", polygon)],
            CRS,
        )
        == ()
    )


def test_geometry_collection_recurses_and_preserves_disconnected_components():
    geometry = GeometryCollection(
        [
            Point(3, 4),
            GeometryCollection(
                [MultiPoint([(5, 6), (7, 8)]), LineString([(10, 0), (12, 0)])]
            ),
        ]
    )
    assert [(point.x, point.y) for point in _intersection_points(geometry)] == [
        (3, 4),
        (5, 6),
        (7, 8),
        (11, 0),
    ]
    assert list(_intersection_points(GeometryCollection())) == []


def test_mixed_line_and_point_intersection(tmp_path):
    road = LineString([(-0.003, 0), (0.003, 0)])
    feature = HydroFeature(
        "river",
        "river",
        MultiLineString(
            [
                [(-0.002, 0), (-0.001, 0)],
                [(0.001, -0.001), (0.001, 0.001)],
            ]
        ),
    )
    result = build_crossings(write_roads(tmp_path, [(0, False, road)]), [feature], CRS)
    assert len(result) == 2
    assert [row.intersection_lon for row in result] == pytest.approx([-0.0015, 0.001])


def test_all_normalized_kinds_and_deterministic_feature_order(tmp_path):
    line = LineString([(0, -0.001), (0, 0.001)])
    area = box(-0.001, -0.001, 0.001, 0.001)
    features = [
        HydroFeature(kind, kind, line if layer in ("waterways", "coastline") else area)
        for layer, kinds in LAYER_KINDS.items()
        for kind in kinds
    ]
    path = write_roads(tmp_path, [(0, False, LineString([(-0.002, 0), (0.002, 0)]))])
    result = build_crossings(path, features, CRS)
    assert {row.hydro_feature_kind for row in result} == {
        feature.kind
        for feature in features
        if feature.kind not in ("coastline", "basin")
    }
    assert len(result) == 9
    assert build_crossings(path, reversed(features), CRS) == result


def test_empty_layers_and_empty_roads(tmp_path):
    path = write_roads(tmp_path, [])
    assert build_crossings(path, [], CRS) == ()
    assert (
        build_crossings(
            path, [HydroFeature("river", "river", LineString([(0, -1), (0, 1)]))], CRS
        )
        == ()
    )
    path = write_roads(tmp_path, [(0, False, LineString([(-1, 0), (1, 0)]))])
    assert build_crossings(path, [], CRS) == ()


def test_only_required_road_columns_are_scanned_in_batches(tmp_path, monkeypatch):
    path = write_roads(tmp_path, [(0, False, LineString([(-1, 0), (1, 0)]))])
    original = pq.ParquetFile
    calls = []

    class TrackedFile:
        def __init__(self, path):
            self.file = original(path)
            self.schema_arrow = self.file.schema_arrow

        def iter_batches(self, *, batch_size, columns):
            calls.append((batch_size, columns))
            return self.file.iter_batches(batch_size=batch_size, columns=columns)

    monkeypatch.setattr(pq, "ParquetFile", TrackedFile)
    assert build_crossings(path, [], CRS) == ()
    assert calls == [(8192, ["id", "bridge", "geometry"])]


@pytest.mark.parametrize(
    "records,match",
    [
        ([(0, False, None)], "missing geometry"),
        ([(0, None, LineString([(-1, 0), (1, 0)]))], "boolean bridge"),
        ([(None, False, LineString([(-1, 0), (1, 0)]))], "nonnegative integers"),
        ([(-1, False, LineString([(-1, 0), (1, 0)]))], "nonnegative integers"),
        ([(0, False, Point(0, 0))], "LineString"),
        ([(0, False, LineString([(190, 0), (191, 0)]))], "WGS84"),
        ([(0, False, LineString())], "LineString"),
        ([(0, False, LineString([(0, 0, 1), (1, 0, 1)]))], "2D"),
    ],
)
def test_invalid_road_records_fail_explicitly(tmp_path, records, match):
    with pytest.raises(WorldBuildError, match=match):
        build_crossings(write_roads(tmp_path, records), [], CRS)


def test_duplicate_road_and_hydro_ids_fail(tmp_path):
    line = LineString([(-1, 0), (1, 0)])
    with pytest.raises(WorldBuildError, match="Duplicate road edge"):
        build_crossings(
            write_roads(tmp_path, [(1, False, line), (1, False, line)]), [], CRS
        )
    feature = HydroFeature("river", "river", line)
    with pytest.raises(WorldBuildError, match="Duplicate hydrology"):
        build_crossings(write_roads(tmp_path, []), [feature, feature], CRS)


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:2263", "not-a-crs"])
def test_invalid_metric_crs_fails(tmp_path, crs):
    with pytest.raises(WorldBuildError):
        build_crossings(write_roads(tmp_path, []), [], crs)


def test_missing_columns_bad_types_wkb_and_file_fail(tmp_path):
    path = tmp_path / "roads.parquet"
    pq.write_table(pa.table({"id": [1]}), path)
    with pytest.raises(WorldBuildError, match="requires id, bridge and geometry"):
        build_crossings(path, [], CRS)
    pq.write_table(
        pa.table({"id": ["1"], "bridge": [False], "geometry": [b"bad"]}), path
    )
    with pytest.raises(WorldBuildError, match="IDs must be integers"):
        build_crossings(path, [], CRS)
    pq.write_table(
        pa.Table.from_pylist(
            [{"id": 1, "bridge": False, "geometry": b"bad"}], schema=ROAD_SCHEMA
        ),
        path,
    )
    with pytest.raises(WorldBuildError, match="Cannot derive"):
        build_crossings(path, [], CRS)
    with pytest.raises(WorldBuildError, match="Cannot derive"):
        build_crossings(tmp_path / "missing.parquet", [], CRS)


def test_projection_failure_is_explicit(tmp_path):
    path = write_roads(tmp_path, [(0, False, LineString([(0, 0), (1, 0)]))])
    with pytest.raises(WorldBuildError, match="cannot be projected"):
        build_crossings(
            path, [], "+proj=tmerc +lat_0=0 +lon_0=90 +k=1 +ellps=WGS84 +units=m"
        )


def test_projected_points_remain_valid_for_long_wgs84_segments(tmp_path):
    # Projection makes a straight metric segment differ from the WGS84 chord.
    feature = HydroFeature("river", "river", LineString([(-37, -9), (-35, -7)]))
    road = LineString([(-37, -7), (-35, -9)])
    path = write_roads(tmp_path, [(0, False, road)])
    result = build_crossings(path, [feature], CRS)
    assert len(result) == 1
    projected = SpatialReference(CRS).geometry_to_metric(feature.geometry_wgs84)
    point = Point(
        SpatialReference(CRS).project(
            (result[0].intersection_lon, result[0].intersection_lat)
        )
    )
    assert projected.distance(point) < 1e-4
    assert HydroLayer([feature], CRS, result).crossings == result
