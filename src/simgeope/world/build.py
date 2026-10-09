"""Single transactional preprocessing entrypoint for Phases 0–5."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from .config import WorldConfig
from .exceptions import WorldBuildError, WorldDataMissingError, WorldFormatError
from .manifest import SourceRecord, WorldManifest
from .sources.ibge import IBGESource

OUTPUT_FILES = (
    "config.json",
    "municipalities.parquet",
    "state_boundary.parquet",
    "roads_nodes.parquet",
    "roads_edges.parquet",
    "roads_routing.npz",
)

FACILITY_FILES = ("facilities.parquet",)


def _validate_counts(root, manifest):
    """Check manifest counts without constructing the complete runtime router."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    files = {
        "municipalities": "municipalities.parquet",
        "state_boundaries": "state_boundary.parquet",
        "road_nodes": "roads_nodes.parquet",
        "road_edges": "roads_edges.parquet",
        **{
            name: f"{name}.parquet"
            for name in (
                "waterways",
                "waterbodies",
                "wetlands",
                "coastline",
                "basins",
                "road_hydro_crossings",
                "facilities",
            )
        },
    }
    for name, filename in files.items():
        if filename in manifest.outputs:
            try:
                count = pq.ParquetFile(Path(root) / filename).metadata.num_rows
            except (OSError, pa.ArrowException) as exc:
                raise WorldFormatError(f"Invalid canonical table: {filename}") from exc
            if manifest.feature_counts.get(name) != count:
                raise WorldFormatError(
                    f"Manifest count does not match canonical table: {name}"
                )

    from .raster import ENVIRONMENT_FILES, EnvironmentStore

    config = WorldConfig.load(Path(root) / "config.json")
    if config.environment_enabled or any(
        filename in manifest.outputs or (Path(root) / filename).exists()
        for filename in ENVIRONMENT_FILES
    ):
        if not config.environment_enabled or not all(
            filename in manifest.outputs for filename in ENVIRONMENT_FILES
        ):
            raise WorldFormatError("Incomplete or disabled environmental outputs")
        environment = EnvironmentStore.load(
            root,
            expected_metric_crs=config.metric_crs,
            expected_resolution_m=config.raster_resolution_m,
        )
        if any(
            manifest.feature_counts.get(name) != count
            for name, count in environment.feature_counts.items()
        ):
            raise WorldFormatError("Manifest count does not match environmental arrays")
        labels = set(
            int(value)
            for value in environment.municipality_code[environment.state_mask]
        ) - {0}
        try:
            known = {
                int(value)
                for value in pq.read_table(
                    Path(root) / "municipalities.parquet", columns=["code"]
                )["code"].to_pylist()
            }
        except (ValueError, TypeError, OSError, pa.ArrowException) as exc:
            raise WorldFormatError("Invalid canonical municipality codes") from exc
        if not labels.issubset(known):
            raise WorldFormatError(
                "Environmental labels reference an unknown municipality"
            )


def build_world(config: WorldConfig, *, base_world: str | Path | None = None) -> Path:
    """Build once, or validate and reuse matching cached outputs.

    Existing worlds are never overwritten. Use a new output directory when
    sources/configuration change. Failed builds remove only their private staging
    directory, leaving published worlds and raw sources untouched.
    """
    from .roads.build import build_from_config
    from .sources.osm import source_date_from_filename

    source = IBGESource.from_config(config)
    if not config.osm_pbf or not Path(config.osm_pbf).is_file():
        raise WorldDataMissingError("Set osm_pbf to an existing, dated local OSM PBF")
    sources = (
        *source.provenance(),
        SourceRecord.from_path(
            config.osm_pbf,
            date=source_date_from_filename(config.osm_pbf),
            url=config.osm_url,
        ),
    )
    if config.facilities_file:
        sources += (SourceRecord.from_path(config.facilities_file),)
    output_files = OUTPUT_FILES
    if config.facilities_file:
        output_files += FACILITY_FILES
    if config.hydro_enabled:
        from .hydro import HYDRO_FILES

        output_files += HYDRO_FILES
    if config.environment_enabled:
        from .raster import ENVIRONMENT_FILES

        output_files += ENVIRONMENT_FILES
    base = None
    base_manifest = None
    reuse_hydro = False
    reuse_environment = False
    base_files = OUTPUT_FILES
    if base_world is not None:
        if not config.hydro_enabled:
            raise WorldBuildError("base_world reuse requires hydro_enabled")
        base = Path(base_world)
        base_manifest = WorldManifest.load(base / "manifest.json")
        _validate_counts(base, base_manifest)
        base_config = WorldConfig.load(base / "config.json")
        ignored = {
            "output_dir",
            "environment_enabled",
            "facilities_file",
        }

        if base_config.environment_enabled:
            if (
                not config.environment_enabled
                or config.raster_resolution_m != base_config.raster_resolution_m
            ):
                raise WorldBuildError(
                    "An environmental base requires matching enabled raster settings"
                )
            from .hydro import HYDRO_FILES
            from .raster import ENVIRONMENT_FILES

            base_files += HYDRO_FILES + ENVIRONMENT_FILES
            reuse_hydro = True
            reuse_environment = True
        elif config.environment_enabled:
            ignored.add("raster_resolution_m")

        if not base_config.hydro_enabled:
            ignored.update(
                (
                    "hydro_enabled",
                    "hydro_min_waterbody_area_m2",
                    "hydro_include_named_streams",
                )
            )
        elif not reuse_hydro:
            from .hydro import HYDRO_FILES

            base_files += HYDRO_FILES
            reuse_hydro = True

        def comparable(value):
            return {
                key: item for key, item in value.to_dict().items() if key not in ignored
            }

        facility_source_name = (
            Path(config.facilities_file).name if config.facilities_file else None
        )
        base_sources = tuple(
            record
            for record in base_manifest.sources
            if record.filename != facility_source_name
        )
        current_sources = tuple(
            record for record in sources if record.filename != facility_source_name
        )

        if (
            (base_config.hydro_enabled and not config.environment_enabled)
            or not set(base_files).issubset(base_manifest.outputs)
            or comparable(base_config) != comparable(config)
            or base_sources
            != tuple(
                sorted(current_sources, key=lambda item: (item.filename, item.sha256))
            )
        ):
            raise WorldBuildError(
                "Base world has different sources/reused-layer configuration, incompatible phase, or incomplete outputs"
            )
    destination = Path(config.output_dir)
    if destination.exists():
        manifest = WorldManifest.load(destination / "manifest.json")
        _validate_counts(destination, manifest)
        if not set(output_files).issubset(manifest.outputs):
            raise WorldFormatError("Cached world is missing required outputs")
        if (
            manifest.configuration_hash == config.configuration_hash
            and manifest.sources
            == tuple(sorted(sources, key=lambda item: (item.filename, item.sha256)))
        ):
            return destination
        raise WorldBuildError(
            f"Existing world {destination} has different inputs/configuration; choose a new output_dir"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-build-", dir=destination.parent)
    )
    try:
        if base is None:
            administrative = source.load()
            administrative.save(staging)
            roads = build_from_config(
                replace(config, output_dir=str(staging)), administrative
            )
            if not roads.nodes or not roads.edges:
                raise WorldBuildError("The selected real road network is empty")
            counts = {
                "municipalities": len(administrative.municipalities),
                "state_boundaries": 1,
                "road_nodes": len(roads.nodes),
                "road_edges": len(roads.edges),
            }
            warnings = (*administrative.warnings, *getattr(roads, "build_warnings", ()))
            dropped = administrative.dropped_invalid_geometry_count + getattr(
                roads, "dropped_invalid_geometry_count", 0
            )
            if source.year is None:
                warnings += (
                    "IBGE dataset vintage is unknown; local source bytes are pinned by SHA256",
                )
        else:
            from .administrative import AdministrativeLayer

            for filename in base_files:
                if filename != "config.json":
                    shutil.copyfile(base / filename, staging / filename)
            administrative = AdministrativeLayer.load(staging, config.metric_crs)
            counts = {
                name: base_manifest.feature_counts[name]
                for name in (
                    "municipalities",
                    "state_boundaries",
                    "road_nodes",
                    "road_edges",
                )
            }
            warnings = base_manifest.warnings
            dropped = base_manifest.dropped_invalid_geometry_count
        config.save(staging / "config.json")
        if config.hydro_enabled:
            from .hydro import HydroLayer
            from .hydro.crossings import build_crossings
            from .sources.hydro import OSMHydroSource

            if reuse_hydro:
                hydro = HydroLayer.load(staging, metric_crs=config.metric_crs)
            else:
                hydro_source = OSMHydroSource(
                    config.osm_pbf,
                    administrative,
                    config.metric_crs,
                    min_waterbody_area_m2=config.hydro_min_waterbody_area_m2,
                    include_named_streams=config.hydro_include_named_streams,
                    border_padding_m=config.border_padding_m,
                )
                features = tuple(
                    feature
                    for load in (
                        hydro_source.load_waterways,
                        hydro_source.load_waterbodies,
                        hydro_source.load_wetlands,
                        hydro_source.load_coastline,
                        hydro_source.load_basins,
                    )
                    for feature in load()
                )
                crossings = build_crossings(
                    staging / "roads_edges.parquet", features, config.metric_crs
                )
                hydro = HydroLayer(
                    features, metric_crs=config.metric_crs, crossings=crossings
                )
                hydro.save(staging)
                warnings += tuple(hydro_source.warnings)
                dropped += hydro_source.dropped_invalid_geometry_count
            counts.update(
                {
                    name: len(getattr(hydro, name))
                    for name in (
                        "waterways",
                        "waterbodies",
                        "wetlands",
                        "coastline",
                        "basins",
                    )
                }
            )
            counts["road_hydro_crossings"] = len(hydro.crossings)
        if config.environment_enabled:
            if reuse_environment:
                from .raster import EnvironmentStore

                environment = EnvironmentStore.load(
                    staging,
                    expected_metric_crs=config.metric_crs,
                    expected_resolution_m=config.raster_resolution_m,
                )
            else:
                from .raster.rasterize import build_environment

                environment = build_environment(
                    administrative,
                    hydro,
                    metric_crs=config.metric_crs,
                    resolution_m=config.raster_resolution_m,
                )
                environment.save(staging)
            counts.update(environment.feature_counts)
            unassigned = environment.feature_counts[
                "environment_unassigned_municipality_cells"
            ]
            if unassigned:
                warnings += (
                    f"{unassigned} state-covered environmental cell centres have no municipality in the pinned IBGE polygons; labels remain unknown",
                )
            warnings += (
                "Environmental bands sample cell centres; boundaries and water distances have grid sampling precision",
                "Wetland fractions describe union coverage of mapped OSM wetlands clipped to the state, not physical completeness",
            )
        if config.facilities_file:
            from .roads.model import RoadNetwork
            from .sources.facilities import load_facilities

            if base is not None:
                roads = RoadNetwork.load(staging, metric_crs=config.metric_crs)
            facility_index = load_facilities(
                config.facilities_file,
                roads,
                administrative,
                max_snap_distance_m=config.max_snap_distance_m,
                allow_excessive_snap=config.allow_excessive_snap,
            )
            facility_index.save(staging)
            counts["facilities"] = len(facility_index.facilities)
            warnings += tuple(facility_index.warnings)
        manifest = WorldManifest.create(
            config,
            sources=sources,
            output_dir=staging,
            output_files=output_files,
            feature_counts=counts,
            warnings=warnings,
            dropped_invalid_geometry_count=dropped,
        )
        manifest.save(staging / "manifest.json")
        manifest.validate_outputs(staging)
        _validate_counts(staging, manifest)
        # Rename only onto a destination that remains absent; concurrent publication
        # is an error rather than permission to replace somebody else's world.
        if destination.exists():
            raise WorldBuildError(f"World output appeared during build: {destination}")
        staging.rename(destination)
        return destination
    except (WorldBuildError, WorldDataMissingError):
        raise
    except Exception as exc:
        raise WorldBuildError(f"World build could not complete: {exc}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main(argv=None):
    import sys

    from simgeope.cli import main as cli_main

    return cli_main(["world", "build", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
