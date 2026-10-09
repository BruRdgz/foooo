import json

import pytest

from simgeope.world.config import WorldConfig
from simgeope.world.exceptions import WorldDataMissingError, WorldFormatError
from simgeope.world.manifest import SourceRecord, WorldManifest


def build_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1735689600")
    config = WorldConfig()
    config.save(tmp_path / "config.json")
    (tmp_path / "municipalities.parquet").write_bytes(b"example")
    source_path = tmp_path / "source.geojson"
    source_path.write_text("pinned source")
    manifest = WorldManifest.create(
        config,
        sources=[SourceRecord.from_path(source_path)],
        feature_counts={"municipalities": 2},
        output_dir=tmp_path,
        output_files=["config.json", "municipalities.parquet"],
    )
    manifest.save(tmp_path / "manifest.json")
    return manifest


def test_manifest_reproducible_and_integrity_checked(tmp_path, monkeypatch):
    manifest = build_manifest(tmp_path, monkeypatch)
    assert manifest.build_timestamp == "2025-01-01T00:00:00Z"
    assert WorldManifest.load(tmp_path / "manifest.json") == manifest
    first = (tmp_path / "manifest.json").read_bytes()
    assert build_manifest(tmp_path, monkeypatch) == manifest
    assert (tmp_path / "manifest.json").read_bytes() == first
    (tmp_path / "municipalities.parquet").write_bytes(b"changed")
    with pytest.raises(WorldFormatError, match="checksum"):
        WorldManifest.load(tmp_path / "manifest.json")


def test_incompatible_version_refused(tmp_path, monkeypatch):
    build_manifest(tmp_path, monkeypatch)
    path = tmp_path / "manifest.json"
    data = json.loads(path.read_text())
    data["world_format_version"] = "999.0"
    path.write_text(json.dumps(data))
    with pytest.raises(WorldFormatError, match="Unsupported"):
        WorldManifest.load(path)


def test_missing_output_and_config_mismatch(tmp_path, monkeypatch):
    build_manifest(tmp_path, monkeypatch)
    WorldConfig(max_snap_distance_m=123).save(tmp_path / "config.json")
    with pytest.raises(WorldFormatError, match="configuration"):
        WorldManifest.load(tmp_path / "manifest.json")
    build_manifest(tmp_path, monkeypatch)
    (tmp_path / "municipalities.parquet").unlink()
    with pytest.raises(WorldDataMissingError):
        WorldManifest.load(tmp_path / "manifest.json")


def test_path_escape_rejected(tmp_path, monkeypatch):
    manifest = build_manifest(tmp_path, monkeypatch)
    data = manifest.to_dict()
    data["outputs"] = {"../outside": {"sha256": "x", "size_bytes": 1}}
    (tmp_path / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(WorldFormatError, match="Unsafe"):
        WorldManifest.load(tmp_path / "manifest.json")


@pytest.mark.parametrize(
    "replacement",
    [
        [],
        {"feature_counts": {"municipalities": -1}},
        {"outputs": {"municipalities.parquet": []}},
        {"raster_resolution_m": -1},
    ],
)
def test_malformed_manifest_uses_explicit_error(tmp_path, monkeypatch, replacement):
    build_manifest(tmp_path, monkeypatch)
    path = tmp_path / "manifest.json"
    data = json.loads(path.read_text())
    if isinstance(replacement, dict):
        data.update(replacement)
    else:
        data = replacement
    path.write_text(json.dumps(data))
    with pytest.raises(WorldFormatError):
        WorldManifest.load(path)
