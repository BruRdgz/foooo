import pytest

from simgeope.world.exceptions import WorldBuildError, WorldDataMissingError
from simgeope.world.sources.osm import dated_pbf, source_date_from_filename


@pytest.mark.parametrize(
    "filename",
    [
        "nordeste-260930.osm.pbf",
        "nordeste-20260930.osm.pbf",
        "nordeste-2026-09-30.osm.pbf",
        "nordeste-2026_09_30.osm.pbf",
    ],
)
def test_pinned_date_formats(filename):
    assert source_date_from_filename(filename) == "2026-09-30"


@pytest.mark.parametrize(
    "filename",
    [
        "nordeste-latest.osm.pbf",
        "nordeste-20260230.osm.pbf",
        "nordeste-20990101.osm.pbf",
    ],
)
def test_invalid_missing_or_future_dates_are_explicit(filename):
    with pytest.raises(WorldBuildError):
        source_date_from_filename(filename)


def test_local_pbf_requires_existing_file_and_correct_extension(tmp_path):
    path = tmp_path / "nordeste-260930.osm.pbf"
    with pytest.raises(WorldDataMissingError):
        dated_pbf(path)
    path.write_bytes(b"fixture")
    assert dated_pbf(path) == (path, "2026-09-30")
    wrong = tmp_path / "nordeste-260930.osm"
    wrong.write_bytes(b"fixture")
    with pytest.raises(WorldBuildError, match=".osm.pbf"):
        dated_pbf(wrong)
