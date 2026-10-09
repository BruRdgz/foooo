import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from simgeope.world.administrative import AdministrativeLayer, Municipality
from simgeope.world.config import WorldConfig
from simgeope.world.exceptions import WorldBuildError, WorldDataMissingError
from simgeope.world.hydro import HydroFeature, HydroLayer
from simgeope.world.raster.rasterize import build_environment
from simgeope.world.spatial import SpatialReference

CRS = "EPSG:3857"


def layers(state=None, features=(), *, metric_crs=CRS, municipalities=None):
    spatial = SpatialReference(metric_crs)
    if state is None:
        state = box(10, 10, 390, 190)
    if municipalities is None:
        municipalities = [("2611606", state)]
    administrative = AdministrativeLayer(
        [
            Municipality(code, code, spatial.geometry_to_wgs84(geometry))
            for code, geometry in municipalities
        ],
        spatial.geometry_to_wgs84(state),
        metric_crs,
    )
    hydro = HydroLayer(
        [
            HydroFeature(id, kind, spatial.geometry_to_wgs84(geometry))
            for id, kind, geometry in features
        ],
        metric_crs,
    )
    return administrative, hydro


def build(administrative, hydro, *, resolution=100, **kwargs):
    return build_environment(
        administrative,
        hydro,
        metric_crs=administrative.spatial.metric_crs,
        resolution_m=resolution,
        **kwargs,
    )


def test_centre_water_distance_excludes_coast_and_basins():
    admin, hydro = layers(
        features=[
            ("river", "river", LineString([(225, 10), (225, 190)])),
            ("coast", "coastline", LineString([(50, 10), (50, 190)])),
            ("basin", "basin", box(10, 10, 390, 190)),
        ]
    )
    store = build(admin, hydro)
    for x, expected in [(50, 175), (150, 75), (250, 25), (350, 125)]:
        row, col = store.grid.index(x, 50)
        assert store.water_distance_m[row, col] == pytest.approx(expected, abs=1e-4)
        assert store.municipality_code[row, col] == 2611606
        assert store.wetland_fraction[row, col] == 0
    assert store.water_distance_m.dtype == np.float32
    assert store.wetland_fraction.dtype == np.float32
    assert store.state_mask.dtype == np.bool_
    assert store.municipality_code.dtype == np.uint32


def test_empty_hydro_means_unknown_distance_and_zero_mapped_coverage():
    admin, hydro = layers()
    store = build(admin, hydro)
    assert store.state_mask.any()
    assert np.isnan(store.water_distance_m).all()
    assert np.all(store.wetland_fraction[store.state_mask] == 0)


def test_waterbody_interior_distance_zero():
    admin, hydro = layers(features=[("lake", "lake", box(20, 20, 80, 80))])
    store = build(admin, hydro)
    row, col = store.grid.index(50, 50)
    assert store.water_distance_m[row, col] == 0


def test_fraction_unions_overlaps_preserves_holes_and_uses_full_cell_area():
    state = box(10, 10, 90, 90)
    left = box(10, 10, 65, 90)
    right = box(35, 10, 90, 90)
    admin, hydro = layers(
        state,
        [("left", "wetland", left), ("right", "wetland", right)],
    )
    store = build(admin, hydro)
    assert store.grid.shape == (1, 1)
    assert store.wetland_fraction[0, 0] == pytest.approx(0.64)
    assert store.water_distance_m[0, 0] == 0
    with_hole = Polygon(state.exterior.coords, [box(40, 40, 60, 60).exterior.coords])
    admin, hydro = layers(state, [("hole", "wetland", with_hole)])
    store = build(admin, hydro)
    assert store.wetland_fraction[0, 0] == pytest.approx(0.60)
    assert store.water_distance_m[0, 0] == pytest.approx(10, abs=1e-4)


def test_coverage_clips_to_state_before_cell_area():
    admin, hydro = layers(
        box(10, 10, 80, 80), [("wetland", "wetland", box(-50, -50, 150, 150))]
    )
    store = build(admin, hydro)
    assert store.grid.shape == (1, 1)
    assert store.wetland_fraction[0, 0] == pytest.approx(0.49)


def test_state_holes_and_islands_preserved_and_outside_bands_nodata():
    mainland = Polygon(
        box(10, 10, 290, 190).exterior.coords,
        [box(120, 20, 180, 80).exterior.coords],
    )
    island = box(410, 10, 590, 190)
    admin, hydro = layers(
        unary_union([mainland, island]),
        municipalities=[("2611606", mainland), ("2605459", island)],
    )
    store = build(admin, hydro)
    row, col = store.grid.index(450, 50)
    assert store.state_mask[row, col]
    assert store.municipality_code[row, col] == 2605459
    for x in (150, 350):
        row, col = store.grid.index(x, 50)
        assert not store.state_mask[row, col]
    outside = ~store.state_mask
    assert (store.municipality_code[outside] == 0).all()
    assert np.isnan(store.water_distance_m[outside]).all()
    assert np.isnan(store.wetland_fraction[outside]).all()


def test_labels_follow_canonical_source_and_lowest_code_overlap_tie():
    # Overlapping/shared administrative boundaries have the same deterministic
    # code tie rule as AdministrativeLayer.municipality_at.
    state = box(10, 10, 390, 190)
    admin, hydro = layers(
        state,
        municipalities=[("2611606", state), ("2605459", box(10, 10, 190, 190))],
    )
    store = build(admin, hydro)
    for row, col in zip(*np.nonzero(store.state_mask), strict=True):
        point = admin.spatial.unproject(store.grid.cell_center(int(row), int(col)))
        expected = admin.municipality_at(point)
        assert store.municipality_code[row, col] == int(expected.code)
    row, col = store.grid.index(50, 50)
    assert store.municipality_code[row, col] == 2605459


def test_alternative_metric_crs_has_exact_centre_distances():
    metric_crs = WorldConfig().metric_crs
    state = box(550010, 9100010, 550390, 9100190)
    river = LineString([(550225, 9100010), (550225, 9100190)])
    admin, hydro = layers(state, [("river", "river", river)], metric_crs=metric_crs)
    store = build(admin, hydro)
    for row, col in zip(*np.nonzero(store.state_mask), strict=True):
        point = admin.spatial.unproject(store.grid.cell_center(int(row), int(col)))
        assert store.water_distance_m[row, col] == pytest.approx(
            hydro.distance_to_water(point), abs=1e-4
        )


def test_shared_municipality_boundary_uses_lowest_code():
    admin, hydro = layers(
        municipalities=[
            ("2611606", box(10, 10, 150, 190)),
            ("2605459", box(150, 10, 390, 190)),
        ]
    )
    store = build(admin, hydro)
    row, col = store.grid.index(150, 50)
    assert store.municipality_code[row, col] == 2605459


def test_state_mask_uses_source_geometry_when_projected_edges_differ():
    spatial = SpatialReference(WorldConfig().metric_crs)
    state = box(-42, -9, -33, -2)
    admin = AdministrativeLayer(
        [Municipality("2611606", "Synthetic state", state)],
        state,
        spatial.metric_crs,
    )
    hydro = HydroLayer([], spatial.metric_crs)
    store = build(admin, hydro, resolution=5_000)
    rows, cols = np.indices(store.grid.shape)
    x = store.grid.origin_x_m + (cols + 0.5) * store.grid.resolution_m
    y = store.grid.origin_y_m - (rows + 0.5) * store.grid.resolution_m
    lon, lat = spatial.unproject((x, y))
    expected = shapely.covers(state, shapely.points(lon, lat))
    projected = shapely.covers(spatial.geometry_to_metric(state), shapely.points(x, y))
    assert np.any(expected != projected)  # Exercise the projection-boundary regression.
    np.testing.assert_array_equal(store.state_mask, expected)


def test_determinism_across_source_order_and_batch_size(monkeypatch):
    features = [
        ("right", "wetland", box(130, 10, 370, 180)),
        ("left", "wetland", box(20, 20, 260, 170)),
        ("river", "river", LineString([(225, 10), (225, 190)])),
    ]
    admin, hydro = layers(features=features)
    first = build(admin, hydro)
    monkeypatch.setattr("simgeope.world.raster.rasterize._BATCH_CELLS", 3)
    admin, hydro = layers(features=list(reversed(features)))
    second = build(admin, hydro)
    assert first.grid == second.grid
    for name in (
        "state_mask",
        "municipality_code",
        "water_distance_m",
        "wetland_fraction",
    ):
        np.testing.assert_array_equal(getattr(first, name), getattr(second, name))


@pytest.mark.parametrize("resolution", [0, -1, float("nan"), True, "100"])
def test_invalid_resolution_rejected(resolution):
    admin, hydro = layers()
    with pytest.raises(WorldBuildError):
        build(admin, hydro, resolution=resolution)


@pytest.mark.parametrize("metric_crs", ["not-a-crs", "EPSG:4326", "EPSG:2263"])
def test_invalid_or_inconsistent_metric_crs_rejected(metric_crs):
    admin, hydro = layers()
    with pytest.raises(WorldBuildError):
        build_environment(admin, hydro, metric_crs=metric_crs, resolution_m=100)


def test_missing_hydro_and_source_inconsistency_rejected():
    admin, hydro = layers()
    with pytest.raises(WorldDataMissingError, match="hydrology"):
        build(admin, None)
    other_crs = HydroLayer([], WorldConfig().metric_crs)
    with pytest.raises(WorldBuildError, match="must match"):
        build(admin, other_crs)


def test_state_centres_in_municipal_source_gaps_preserved_as_unknown():
    # Real pinned IBGE state/municipality boundaries disagree in coastal and
    # border slivers. The state is authoritative for the mask, and absent
    # municipal coverage remains unknown rather than receiving a nearest label.
    admin, hydro = layers(municipalities=[("2611606", box(10, 10, 90, 90))])
    store = build(admin, hydro)
    assert store.state_mask.all()
    assert np.count_nonzero(store.municipality_code == 0) == 7
    point = admin.spatial.unproject((150, 50))
    assert store.at(point)["municipality_code"] is None
    assert store.at(point)["wetland_fraction"] == 0
    assert store.feature_counts["environment_unassigned_municipality_cells"] == 7


def test_allocation_budget_rejected_before_bands():
    admin, hydro = layers()
    with pytest.raises(WorldBuildError, match="cell budget"):
        build(admin, hydro, max_cells=1)
