"""Explicit local preprocessing commands; no imports of GIS tools for --help."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="simgeope")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Serve the local world inspector")
    serve.add_argument("--world", required=True, help="Built world directory")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--simulate",
        action="store_true",
        help="Also serve a live H5N1 simulation (requires the simulation extra)",
    )
    serve.add_argument("--seed", type=int, default=42, help="Initial simulation seed")
    world = commands.add_parser("world", help="Build the static Pernambuco world")
    operations = world.add_subparsers(dest="operation", required=True)
    build = operations.add_parser(
        "build", help="Build/cache Phases 0–5 from pinned local inputs"
    )
    build.add_argument(
        "--config", help="WorldConfig JSON; paths resolve relative to working directory"
    )
    build.add_argument("--ibge-state")
    build.add_argument("--ibge-municipalities")
    build.add_argument("--ibge-names")
    build.add_argument("--osm-pbf")
    build.add_argument(
        "--facilities-file", help="Static CSV/XLSX/Parquet facility source"
    )
    build.add_argument("--output-dir")
    build.add_argument(
        "--hydro", action="store_true", help="Build the Phase 4 hydrology layer"
    )
    build.add_argument(
        "--environment",
        action="store_true",
        help="Build the Phase 5 environmental raster (includes hydro)",
    )
    build.add_argument("--raster-resolution-m", type=float)
    build.add_argument(
        "--base-world",
        help="Reuse verified administration/roads/hydrology from a matching world",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            if not 1 <= args.port <= 65535:
                parser.error("--port must be between 1 and 65535")
            from .api.app import create_app
            from .world.model import PernambucoWorld

            try:
                import uvicorn
            except ImportError as exc:
                raise ImportError("uvicorn (install simgeope[viewer])") from exc
            loaded = PernambucoWorld.load(args.world)
            simulation = None
            if args.simulate:
                from .api.simulation import SimulationSession

                simulation = SimulationSession(loaded, seed=args.seed)
            uvicorn.run(
                create_app(loaded, simulation=simulation),
                host="127.0.0.1",
                port=args.port,
            )
            return 0
        from .world.build import build_world
        from .world.config import WorldConfig

        config = WorldConfig.load(args.config) if args.config else WorldConfig()
        overrides = {
            key: getattr(args, key)
            for key in (
                "ibge_state",
                "ibge_municipalities",
                "ibge_names",
                "osm_pbf",
                "facilities_file",
                "output_dir",
                "raster_resolution_m",
            )
            if getattr(args, key) is not None
        }
        if args.hydro:
            overrides["hydro_enabled"] = True
        if args.environment:
            overrides.update(environment_enabled=True, hydro_enabled=True)
        config = replace(config, **overrides)
        destination = build_world(config, base_world=args.base_world)
        print(
            json.dumps(
                {
                    "world": str(destination),
                    "status": "ready",
                    "phases": list(
                        range(
                            7
                            if config.facilities_file
                            else 6
                            if config.environment_enabled
                            else 5
                            if config.hydro_enabled
                            else 4
                        )
                    ),
                }
            )
        )
        return 0
    except ImportError as exc:
        print(
            f"Missing Python dependency: {exc}. Install simgeope[build].",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        from .world.exceptions import (
            WorldBuildError,
            WorldDataMissingError,
            WorldFormatError,
        )

        if not isinstance(
            exc,
            (
                WorldBuildError,
                WorldDataMissingError,
                WorldFormatError,
                ValueError,
                OSError,
            ),
        ):
            raise
        print(f"World build failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
