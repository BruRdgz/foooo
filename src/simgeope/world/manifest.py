"""World format, deterministic fingerprints, and source/output provenance."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import WorldConfig
from .exceptions import WorldDataMissingError, WorldFormatError

WORLD_FORMAT_VERSION = "1.0"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class SourceRecord:
    filename: str
    sha256: str
    url: str | None = None
    date: str | None = None
    dataset_version: str | None = None

    @classmethod
    def from_path(cls, path, *, url=None, date=None, dataset_version=None):
        path = Path(path)
        if not path.is_file():
            raise WorldDataMissingError(f"Source file does not exist: {path}")
        return cls(path.name, sha256_file(path), url, date, dataset_version)


def _timestamp() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    timestamp = (
        datetime.fromtimestamp(int(epoch), UTC)
        if epoch is not None
        else datetime.now(UTC)
    )
    return timestamp.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class WorldManifest:
    world_format_version: str
    build_timestamp: str
    sources: tuple[SourceRecord, ...]
    configuration_hash: str
    projected_crs: str
    raster_resolution_m: float
    feature_counts: dict[str, int]
    outputs: dict[str, dict]
    warnings: tuple[str, ...] = ()
    dropped_invalid_geometry_count: int = 0

    @classmethod
    def create(
        cls,
        config: WorldConfig,
        *,
        sources,
        feature_counts,
        output_dir,
        output_files,
        warnings=(),
        dropped_invalid_geometry_count=0,
        build_timestamp=None,
    ):
        root = Path(output_dir)
        outputs = {}
        for filename in sorted(output_files):
            relative = Path(filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(
                    "Manifest output names must remain inside the world directory"
                )
            path = root / relative
            if not path.is_file():
                raise WorldDataMissingError(f"Missing built output: {path}")
            outputs[str(relative)] = {
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        if any(
            type(count) is not int or count < 0 for count in feature_counts.values()
        ):
            raise ValueError("Feature counts must be nonnegative integers")
        if (
            type(dropped_invalid_geometry_count) is not int
            or dropped_invalid_geometry_count < 0
        ):
            raise ValueError("Dropped geometry count must be a nonnegative integer")
        return cls(
            WORLD_FORMAT_VERSION,
            build_timestamp or _timestamp(),
            tuple(sorted(sources, key=lambda s: (s.filename, s.sha256))),
            config.configuration_hash,
            config.metric_crs,
            config.raster_resolution_m,
            dict(sorted(feature_counts.items())),
            outputs,
            tuple(warnings),
            dropped_invalid_geometry_count,
        )

    def to_dict(self):
        return asdict(self)

    def save(self, path):
        Path(path).write_text(
            json.dumps(
                self.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path, *, validate_files=True):
        path = Path(path)
        if not path.is_file():
            raise WorldDataMissingError(f"World manifest does not exist: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise WorldFormatError("World manifest must be a JSON object")
            if data.get("world_format_version") != WORLD_FORMAT_VERSION:
                raise WorldFormatError(
                    f"Unsupported world format: {data.get('world_format_version')!r}; expected {WORLD_FORMAT_VERSION}"
                )
            data["sources"] = tuple(
                SourceRecord(**record) for record in data["sources"]
            )
            data["warnings"] = tuple(data.get("warnings", ()))
            manifest = cls(**data)
            if (
                not isinstance(manifest.feature_counts, dict)
                or any(
                    type(count) is not int or count < 0
                    for count in manifest.feature_counts.values()
                )
                or type(manifest.dropped_invalid_geometry_count) is not int
                or manifest.dropped_invalid_geometry_count < 0
            ):
                raise WorldFormatError("Invalid manifest feature/geometry counts")
            if (
                not isinstance(manifest.raster_resolution_m, (int, float))
                or not math.isfinite(manifest.raster_resolution_m)
                or manifest.raster_resolution_m <= 0
            ):
                raise WorldFormatError("Invalid manifest raster resolution")
        except (ValueError, KeyError, TypeError) as exc:
            raise WorldFormatError(f"Malformed world manifest: {path}") from exc
        if validate_files:
            manifest.validate_outputs(path.parent)
        return manifest

    def validate_outputs(self, root):
        root = Path(root)
        config_path = root / "config.json"
        if not config_path.is_file():
            raise WorldDataMissingError(f"World configuration missing: {config_path}")
        try:
            config = WorldConfig.load(config_path)
        except (ValueError, TypeError) as exc:
            raise WorldFormatError("Invalid persisted world configuration") from exc
        if (
            config.configuration_hash != self.configuration_hash
            or config.metric_crs != self.projected_crs
            or config.raster_resolution_m != self.raster_resolution_m
        ):
            raise WorldFormatError("World configuration does not match its manifest")
        if not isinstance(self.outputs, dict) or not self.outputs:
            raise WorldFormatError("World manifest has no output fingerprints")
        for filename, metadata in self.outputs.items():
            if not isinstance(filename, str):
                raise WorldFormatError("Manifest output names must be strings")
            relative = Path(filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise WorldFormatError(f"Unsafe manifest output name: {filename}")
            path = root / relative
            if not path.is_file():
                raise WorldDataMissingError(f"World output missing: {path}")
            try:
                if (
                    not isinstance(metadata, dict)
                    or type(metadata["size_bytes"]) is not int
                    or metadata["size_bytes"] < 0
                    or not isinstance(metadata["sha256"], str)
                ):
                    raise WorldFormatError(f"Invalid output fingerprint: {filename}")
                valid = (
                    path.stat().st_size == metadata["size_bytes"]
                    and sha256_file(path) == metadata["sha256"]
                )
            except (KeyError, TypeError) as exc:
                raise WorldFormatError(
                    f"Invalid output fingerprint: {filename}"
                ) from exc
            if not valid:
                raise WorldFormatError(f"World output checksum mismatch: {filename}")
