import pytest
from shapely.geometry import box

from simgeope.world.config import DEFAULT_METRIC_CRS
from simgeope.world.roads.build import build_roads
from simgeope.world.roads.extract import extract_roads, road_class_allowed


@pytest.mark.parametrize(
    "tags,allowed",
    [
        ({"highway": "primary"}, True),
        ({"highway": "primary_link"}, True),
        ({"highway": "motorway_link"}, True),
        ({"highway": "residential"}, False),
        ({"highway": "service"}, False),
        ({"highway": "footway"}, False),
        ({"highway": "primary", "access": "private"}, False),
        ({"highway": "primary", "motor_vehicle": "no"}, False),
        ({"highway": "primary", "access": "private", "motor_vehicle": "yes"}, True),
        ({"highway": "primary", "access": "destination"}, True),
    ],
)
def test_logistics_class_and_access_filter(tags, allowed):
    assert road_class_allowed(tags) is allowed


def test_explicit_local_street_classes():
    assert road_class_allowed({"highway": "residential"}, ("residential",))
    assert not road_class_allowed({"highway": "primary"}, ("residential",))


@pytest.fixture
def small_pbf(tmp_path):
    osmium = pytest.importorskip(
        "osmium", reason="Self-contained PBF extraction test needs pyosmium build extra"
    )
    path = tmp_path / "tiny-250101.osm.pbf"
    writer = osmium.SimpleWriter(str(path))
    try:
        for node_id, coordinate in {
            1: (-35.02, -8),
            2: (-35, -8),
            3: (-34.98, -8),
            4: (-35, -7.99),
            5: (-35, -8.01),
            6: (-34, -8),
            7: (-33.99, -8),
        }.items():
            writer.add_node(osmium.osm.mutable.Node(id=node_id, location=coordinate))
        for source_id, node_ids, tags in [
            (
                10,
                [1, 2, 3],
                {
                    "highway": "primary",
                    "ref": "BR-232",
                    "name": "Fixture road",
                    "surface": "asphalt",
                },
            ),
            (11, [2, 4], {"highway": "primary_link", "oneway": "-1", "bridge": "yes"}),
            (12, [6, 7], {"highway": "primary"}),
            (13, [2, 5], {"highway": "residential"}),
            (14, [2, 5], {"highway": "primary", "access": "private"}),
            (15, [2, 999], {"highway": "primary"}),
        ]:
            writer.add_way(
                osmium.osm.mutable.Way(id=source_id, nodes=node_ids, tags=tags)
            )
    finally:
        writer.close()
    return path


def test_actual_pbf_filters_extent_classes_and_keeps_source_tags(small_pbf, tmp_path):
    extracted = extract_roads(
        small_pbf, box(-35.001, -8.001, -34.999, -7.999), 0, DEFAULT_METRIC_CRS
    )
    assert [way.id for way in extracted.ways] == [10, 11]
    assert extracted.ways[0].node_ids == (1, 2, 3)
    assert extracted.ways[0].tags["ref"] == "BR-232"
    assert extracted.source_date == "2025-01-01"
    assert extracted.source_file == small_pbf.name
    assert extracted.warnings == (
        "Dropped 1 road ways with missing OSM node locations",
    )
    assert extracted.dropped_invalid_geometry_count == 1
    roads = build_roads(
        extracted.ways,
        tmp_path / "roads",
        DEFAULT_METRIC_CRS,
        source_date=extracted.source_date,
        source_file=extracted.source_file,
    )
    assert {edge.highway for edge in roads.edges} == {"primary", "primary_link"}
    assert {node.id for node in roads.nodes} == {1, 2, 3, 4}
    assert roads.route(4, 1).travel_time_s > 0
    assert all(edge.source_date == "2025-01-01" for edge in roads.edges)
    assert all(edge.source_file == small_pbf.name for edge in roads.edges)


def test_actual_pbf_explicit_residential_and_padding(small_pbf):
    extracted = extract_roads(
        small_pbf,
        box(-35.001, -8.001, -34.999, -7.999),
        0,
        DEFAULT_METRIC_CRS,
        ("residential",),
    )
    assert [way.id for way in extracted.ways] == [13]
    # No road intersects this distant region until the requested metric buffer expands it.
    extent = box(-35.05, -8.001, -35.049, -7.999)
    assert not extract_roads(small_pbf, extent, 0, DEFAULT_METRIC_CRS).ways
    assert extract_roads(small_pbf, extent, 4000, DEFAULT_METRIC_CRS).ways


@pytest.mark.parametrize("padding", [-1, float("inf"), float("nan")])
def test_invalid_extraction_padding(small_pbf, padding):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        extract_roads(
            small_pbf, (-35.001, -8.001, -34.999, -7.999), padding, DEFAULT_METRIC_CRS
        )
