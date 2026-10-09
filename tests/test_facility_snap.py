import pytest
from shapely.geometry import box

from simgeope.world.config import DEFAULT_METRIC_CRS
from simgeope.world.exceptions import NoRoadNearbyError
from simgeope.world.facilities import Facility, FacilityIndex, snap_facility
from simgeope.world.roads.build import build_roads
from simgeope.world.roads.extract import RoadWay


@pytest.fixture
def roads():
    # The closest edge has endpoints kilometers away. A nearby other way has
    # a valid node much closer to the facility than either of those endpoints.
    ways = [
        RoadWay(1, (1, 2), ((-35.1, -8), (-34.9, -8)), {"highway": "primary"}),
        RoadWay(2, (3, 4), ((-35, -7.999), (-35, -7.99)), {"highway": "secondary"}),
    ]
    return build_roads(ways, None, DEFAULT_METRIC_CRS)


def test_nearest_node_snap_uses_global_index_and_limit(roads):
    facility = Facility("farm", "producer", -35, -8)
    assert roads.nearest_road((facility.lon, facility.lat)).node_id in {1, 2}
    snapped = snap_facility(facility, roads, max_distance_m=200)
    assert snapped.road_node == 3
    assert snapped.road_edge in {edge.id for edge in roads.incident_edges(3)}
    assert 100 < snapped.snap_distance_m < 120
    assert facility.road_node is None
    with pytest.raises(NoRoadNearbyError, match="snapped road node"):
        snap_facility(facility, roads, max_distance_m=10)
    with pytest.warns(UserWarning, match="snapped road node"):
        excessive = snap_facility(
            facility, roads, max_distance_m=10, allow_excessive=True
        )
    assert excessive.road_node == 3


def test_municipality_assignment_and_two_nearby_facility_route(roads):
    class Municipality:
        code = "2611606"

    class Admin:
        def municipality_at(self, point):
            return Municipality()

    a = snap_facility(
        Facility("a", "hub", -35, -7.999), roads, Admin(), max_distance_m=1
    )
    b = snap_facility(
        Facility("b", "farm", -35, -7.99), roads, Admin(), max_distance_m=1
    )
    assert a.municipality_code == b.municipality_code == "2611606"
    assert roads.route(a.road_node, b.road_node).travel_time_s > 0


def test_facility_spatial_index_and_snap_persistence(roads, tmp_path):
    a = snap_facility(Facility("a", "farm", -35, -7.999), roads, max_distance_m=1)
    b = snap_facility(Facility("b", "hub", -35, -7.99), roads, max_distance_m=1)
    index = FacilityIndex([b, a], metric_crs=DEFAULT_METRIC_CRS)
    assert index.get("a") == a
    assert index.nearby((-35, -7.999), 100) == [a]
    assert index.nearby((-35, -7.999), 2000) == [a, b]
    assert index.within(box(-35.001, -8, -34.999, -7.998)) == [a]
    index.save(tmp_path)
    restored = FacilityIndex.load(tmp_path, metric_crs=DEFAULT_METRIC_CRS)
    assert restored.facilities == index.facilities
    assert restored.get("a").road_node == 3
    assert restored.nearby((-35, -7.999), 100) == [a]


def test_empty_index_and_invalid_facilities():
    empty = FacilityIndex(metric_crs=DEFAULT_METRIC_CRS)
    assert empty.nearby((-35, -8), 1) == []
    assert empty.within(box(-36, -9, -34, -7)) == []
    with pytest.raises(KeyError):
        empty.get("missing")
    with pytest.raises(ValueError, match="Duplicate"):
        FacilityIndex(
            [Facility("a", "farm", -35, -8)] * 2, metric_crs=DEFAULT_METRIC_CRS
        )
    for invalid in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            empty.nearby((-35, -8), invalid)
        with pytest.raises(ValueError):
            Facility("a", "farm", -35, -8, snap_distance_m=invalid)
    with pytest.raises(ValueError):
        Facility("", "farm", -35, -8)
    with pytest.raises(ValueError):
        Facility("a", "farm", -350, -8)


def test_administrative_lookup_clears_stale_assignment_outside_world(roads):
    class Admin:
        def municipality_at(self, point):
            return None

    facility = Facility("a", "hub", -35, -7.999, municipality_code="2611606")
    snapped = snap_facility(facility, roads, Admin(), max_distance_m=1)
    assert snapped.municipality_code is None
