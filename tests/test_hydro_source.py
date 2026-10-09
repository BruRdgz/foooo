"""Small dated OSM XML sources exercise the same pyosmium path as PBF."""

from html import escape

import pytest
from shapely.geometry import Point, box
from shapely.ops import unary_union

from simgeope.world.administrative import AdministrativeLayer, Municipality
from simgeope.world.config import WorldConfig
from simgeope.world.exceptions import WorldBuildError, WorldDataMissingError
from simgeope.world.sources.hydro import OSMHydroSource, hydro_kind


@pytest.fixture
def administrative():
    mainland = box(-35.1, -8.2, -34.8, -7.9)
    island = box(-32.45, -3.9, -32.35, -3.8)
    return AdministrativeLayer(
        [
            Municipality("2611606", "Recife", mainland),
            Municipality("2605459", "Noronha", island),
        ],
        unary_union([mainland, island]),
        WorldConfig().metric_crs,
    )


def write_osm(tmp_path, ways=(), relations=()):
    records = ['<?xml version="1.0"?><osm version="0.6" generator="test">']
    node_id = 1
    for way_id, coordinates, tags in ways:
        refs = []
        for index, (lon, lat) in enumerate(coordinates):
            if index and (lon, lat) == coordinates[0]:
                refs.append(refs[0])
                continue
            records.append(
                f'<node id="{node_id}" lon="{lon}" lat="{lat}" version="1"/>'
            )
            refs.append(node_id)
            node_id += 1
        records.append(f'<way id="{way_id}" version="1">')
        records.extend(f'<nd ref="{ref}"/>' for ref in refs)
        records.extend(
            f'<tag k="{escape(key)}" v="{escape(value)}"/>'
            for key, value in tags.items()
        )
        records.append("</way>")
    for relation_id, members, tags in relations:
        records.append(f'<relation id="{relation_id}" version="1">')
        records.extend(
            f'<member type="way" ref="{way_id}" role="{role}"/>'
            for way_id, role in members
        )
        records.extend(
            f'<tag k="{escape(key)}" v="{escape(value)}"/>'
            for key, value in tags.items()
        )
        records.append("</relation>")
    records.append("</osm>")
    path = tmp_path / "hydro-2026-01-02.osm"
    path.write_text("\n".join(records))
    return path


def ring(lon=-35.0, lat=-8.1, size=0.02):
    return [
        (lon, lat),
        (lon + size, lat),
        (lon + size, lat + size),
        (lon, lat + size),
        (lon, lat),
    ]


def source(path, administrative, **options):
    return OSMHydroSource(path, administrative, WorldConfig().metric_crs, **options)


@pytest.mark.parametrize(
    "tags,kind",
    [
        ({"waterway": "river"}, "river"),
        ({"waterway": "canal"}, "canal"),
        ({"waterway": "stream", "name": "Stream"}, None),
        ({"waterway": "ditch"}, None),
        ({"natural": "water", "water": "lake"}, "lake"),
        ({"natural": "water", "water": "reservoir"}, "reservoir"),
        ({"natural": "water", "water": "pond"}, "pond"),
        ({"natural": "water", "water": "lagoon"}, "lagoon"),
        ({"landuse": "reservoir"}, "reservoir"),
        ({"natural": "water"}, "water"),
        ({"natural": "water", "water": "river"}, "water"),
        ({"waterway": "riverbank"}, "water"),
        ({"waterway": "river", "natural": "water"}, "water"),
        ({"waterway": "canal", "natural": "water", "water": "reservoir"}, "reservoir"),
        ({"natural": "wetland"}, "wetland"),
        ({"natural": "coastline"}, "coastline"),
        ({"landuse": "basin"}, None),
    ],
)
def test_normalization(tags, kind):
    assert hydro_kind(tags) == kind


def test_layers_provenance_threshold_and_stream_caveat(tmp_path, administrative):
    coordinates = [(-35.02, -8.08), (-34.98, -8.08)]
    path = write_osm(
        tmp_path,
        [
            (1, coordinates, {"waterway": "river", "name": "River"}),
            (2, coordinates, {"waterway": "canal"}),
            (3, coordinates, {"waterway": "stream", "name": "Mapped stream"}),
            (4, coordinates, {"waterway": "stream"}),
            (5, ring(), {"natural": "water", "water": "lake"}),
            (6, ring(size=0.00001), {"natural": "water", "water": "pond"}),
            (7, ring(), {"natural": "wetland", "wetland": "marsh"}),
            (8, coordinates, {"natural": "coastline"}),
            (9, ring(), {"natural": "water"}),
        ],
    )
    hydro = source(path, administrative)
    assert {feature.kind for feature in hydro.load_waterways()} == {"river", "canal"}
    assert {feature.kind for feature in hydro.load_waterbodies()} == {"lake", "water"}
    assert len(hydro.load_wetlands()) == len(hydro.load_coastline()) == 1
    assert hydro.load_basins() == ()
    river = next(
        feature for feature in hydro.load_waterways() if feature.kind == "river"
    )
    assert (river.id, river.source_id, river.source, river.name) == (
        "osm:way:1",
        "way/1",
        "osm",
        "River",
    )
    assert river.source_date == "2026-01-02"
    assert river.source_file == path.name
    assert river.tags == {"waterway": "river", "name": "River"}
    assert river.municipality_codes == ("2611606",)
    assert any("uncertain" in warning for warning in hydro.warnings)
    optional = source(path, administrative, include_named_streams=True)
    assert [
        feature.id
        for feature in optional.load_waterways()
        if feature.kind == "stream_major"
    ] == ["osm:way:3"]
    assert any("nonauthoritative" in warning for warning in optional.warnings)


def test_multipolygon_holes_and_member_dedup(tmp_path, administrative):
    outer = ring(size=0.08)
    inner = ring(-34.98, -8.08, size=0.02)
    path = write_osm(
        tmp_path,
        [
            (10, outer, {"natural": "water", "water": "lake"}),
            (11, inner, {}),
        ],
        [
            (
                20,
                [(10, "outer"), (11, "inner")],
                {
                    "type": "multipolygon",
                    "natural": "water",
                    "water": "lake",
                    "name": "Hole lake",
                },
            )
        ],
    )
    features = source(path, administrative).load_waterbodies()
    assert len(features) == 1
    lake = features[0]
    assert lake.id == "osm:relation:20"
    assert lake.source_id == "relation/20"
    assert lake.geometry_wgs84.is_valid
    assert not lake.geometry_wgs84.covers(Point(-34.97, -8.07))
    assert lake.geometry_wgs84.covers(Point(-34.99, -8.09))


def test_relation_assembles_open_member_ways(tmp_path, administrative):
    path = write_osm(
        tmp_path,
        [
            (10, [(-35, -8.1), (-34.98, -8.1), (-34.98, -8.08)], {}),
            (11, [(-34.98, -8.08), (-35, -8.08), (-35, -8.1)], {}),
        ],
        [
            (
                20,
                [(10, "outer"), (11, "outer")],
                {"type": "multipolygon", "natural": "water", "water": "reservoir"},
            )
        ],
    )
    # Different nodes at shared coordinates are not a connected OSM ring. Make
    # member endpoints refer to the same OSM nodes, as actual OSM requires.
    contents = (
        path.read_text()
        .replace('<nd ref="4"/>', '<nd ref="3"/>')
        .replace('<nd ref="6"/>', '<nd ref="1"/>')
    )
    path.write_text(contents)
    hydro = source(path, administrative)
    assert len(hydro.load_waterbodies()) == 1
    assert hydro.load_waterbodies()[0].kind == "reservoir"
    assert hydro.dropped_invalid_geometry_count == 0


def test_footprint_preserves_island_and_full_intersecting_features(
    tmp_path, administrative
):
    crossing = [(-35.2, -8.1), (-34.9, -8.1)]
    gap = [(-34.0, -6.0), (-33.9, -6.0)]  # Inside state bbox, outside both polygons.
    island = [(-32.44, -3.85), (-32.4, -3.85)]
    path = write_osm(
        tmp_path,
        [
            (1, crossing, {"waterway": "river"}),
            (2, gap, {"waterway": "river"}),
            (3, island, {"waterway": "river"}),
        ],
    )
    features = source(path, administrative).load_waterways()
    assert [feature.id for feature in features] == ["osm:way:1", "osm:way:3"]
    assert list(features[0].geometry_wgs84.coords) == crossing
    assert features[1].municipality_codes == ("2605459",)


def test_buffer_in_metres(tmp_path, administrative):
    path = write_osm(
        tmp_path, [(1, [(-35.1001, -8.1), (-35.1001, -8.09)], {"waterway": "river"})]
    )
    assert source(path, administrative).load_waterways() == ()
    padded = source(path, administrative, border_padding_m=100).load_waterways()
    assert len(padded) == 1
    assert padded[0].municipality_codes == ()


def test_empty_source_and_one_cached_extraction(tmp_path, administrative, monkeypatch):
    import osmium

    path = write_osm(tmp_path)
    calls = []
    apply_file = osmium.SimpleHandler.apply_file

    def count(self, *args, **kwargs):
        calls.append(args[0])
        return apply_file(self, *args, **kwargs)

    monkeypatch.setattr(osmium.SimpleHandler, "apply_file", count)
    hydro = source(path, administrative)
    assert (
        hydro.load_waterways()
        == hydro.load_waterbodies()
        == hydro.load_wetlands()
        == hydro.load_coastline()
        == hydro.load_basins()
        == ()
    )
    assert len(hydro.warnings) == 1
    assert "basin source" in hydro.warnings[0]
    assert hydro.dropped_invalid_geometry_count == 0
    assert len(calls) == 1


def test_local_pbf_matches_xml_normalization(tmp_path, administrative):
    import osmium

    path = write_osm(
        tmp_path,
        [
            (1, [(-35, -8.1), (-34.98, -8.08)], {"waterway": "river"}),
            (2, ring(), {"natural": "water", "water": "reservoir"}),
        ],
    )
    pbf = tmp_path / "hydro-2026-01-02.osm.pbf"
    with osmium.SimpleWriter(str(pbf)) as writer:

        class Copy(osmium.SimpleHandler):
            def node(self, node):
                writer.add_node(node)

            def way(self, way):
                writer.add_way(way)

        Copy().apply_file(str(path))
    xml_source = source(path, administrative)
    pbf_source = source(pbf, administrative)
    for xml_features, pbf_features in (
        (xml_source.load_waterways(), pbf_source.load_waterways()),
        (xml_source.load_waterbodies(), pbf_source.load_waterbodies()),
    ):
        assert len(xml_features) == len(pbf_features) == 1
        assert xml_features[0].id == pbf_features[0].id
        assert xml_features[0].kind == pbf_features[0].kind
        assert xml_features[0].geometry_wgs84.equals(pbf_features[0].geometry_wgs84)
        assert pbf_features[0].source_file == pbf.name


def test_missing_way_node_is_dropped_and_warned(tmp_path, administrative):
    path = write_osm(
        tmp_path, [(1, [(-35, -8.1), (-34.98, -8.08)], {"waterway": "river"})]
    )
    path.write_text(path.read_text().replace('<nd ref="2"/>', '<nd ref="999"/>'))
    hydro = source(path, administrative)
    assert hydro.load_waterways() == ()
    assert hydro.dropped_invalid_geometry_count == 1
    assert len(hydro.warnings) == 2
    assert any("source-wide" in warning for warning in hydro.warnings)


def test_repair_invalid_polygon_and_report_dropped_geometry(tmp_path, administrative):
    path = write_osm(
        tmp_path,
        [
            (
                1,
                [
                    (-35, -8.1),
                    (-34.98, -8.08),
                    (-35, -8.08),
                    (-34.98, -8.1),
                    (-35, -8.1),
                ],
                {"natural": "water"},
            ),
            (2, [(-35, -8.1), (-34.98, -8.08)], {"natural": "water"}),
            (3, [(-35, -8.1), (-35, -8.1)], {"waterway": "river"}),
        ],
        [(20, [(999, "outer")], {"type": "multipolygon", "natural": "water"})],
    )
    hydro = source(path, administrative)
    features = hydro.load_waterbodies()
    assert len(features) == 1
    assert features[0].geometry_wgs84.is_valid
    assert features[0].geometry_wgs84.geom_type == "MultiPolygon"
    assert hydro.dropped_invalid_geometry_count == 3
    assert any("Repaired 1" in warning for warning in hydro.warnings)
    assert any("Dropped 3" in warning for warning in hydro.warnings)
    assert len(hydro.warnings) < 5


def test_missing_invalid_and_undated_sources(tmp_path, administrative):
    with pytest.raises(WorldDataMissingError):
        source(tmp_path / "missing.osm", administrative)
    path = tmp_path / "source.osm"
    path.write_text("<osm/>")
    with pytest.raises(WorldBuildError, match="Pin"):
        source(path, administrative)
    path = tmp_path / "source-2026-01-02.txt"
    path.write_text("bad")
    with pytest.raises(WorldBuildError, match="input"):
        source(path, administrative)
    path = tmp_path / "source-2026-01-02.osm"
    path.write_text("not osm")
    with pytest.raises(WorldBuildError, match="Cannot extract"):
        source(path, administrative).load_waterways()


@pytest.mark.parametrize("option", ["min_waterbody_area_m2", "border_padding_m"])
@pytest.mark.parametrize(
    "value", [-1, float("nan"), float("inf"), True, False, "1000", None, 1j]
)
def test_invalid_metric_options(tmp_path, administrative, option, value):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        source(write_osm(tmp_path), administrative, **{option: value})


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_stream_option_requires_boolean(tmp_path, administrative, value):
    with pytest.raises(ValueError, match="boolean"):
        source(write_osm(tmp_path), administrative, include_named_streams=value)


@pytest.mark.parametrize("metric_crs", ["EPSG:4326", "EPSG:2277", "invalid"])
def test_source_requires_projected_metre_crs(tmp_path, administrative, metric_crs):
    with pytest.raises(ValueError, match="projected CRS with metre units"):
        OSMHydroSource(write_osm(tmp_path), administrative, metric_crs)


@pytest.mark.parametrize(
    "name,expected", [("", None), ("   ", None), (" River ", "River")]
)
def test_normalized_name_preserves_raw_tag(tmp_path, administrative, name, expected):
    path = write_osm(
        tmp_path,
        [(1, [(-35, -8.1), (-34.98, -8.08)], {"waterway": "river", "name": name})],
    )
    feature = source(path, administrative).load_waterways()[0]
    assert feature.name == expected
    assert feature.tags["name"] == name


@pytest.mark.parametrize(
    "tags",
    [
        {"natural": "water", "waterway": "river"},
        {"natural": "water", "waterway": "canal"},
        {"waterway": "riverbank"},
    ],
)
def test_multipolygon_surface_tags_override_waterway_kind(
    tmp_path, administrative, tags
):
    path = write_osm(
        tmp_path,
        [(10, ring(), {})],
        [(20, [(10, "outer")], {"type": "multipolygon", **tags})],
    )
    hydro = source(path, administrative)
    assert hydro.load_waterways() == ()
    assert len(hydro.load_waterbodies()) == 1
    assert hydro.load_waterbodies()[0].kind == "water"
    assert hydro.dropped_invalid_geometry_count == 0
