"""Public world facade shared by simulations and inspection tools."""

from __future__ import annotations

from numbers import Integral
from pathlib import Path

from .administrative import AdministrativeLayer
from .config import WorldConfig
from .exceptions import WorldDataMissingError, WorldFormatError
from .manifest import WorldManifest
from .spatial import SpatialReference


class PernambucoWorld:
    def __init__(
        self,
        *,
        config,
        administrative,
        roads=None,
        facilities=None,
        hydro=None,
        environment=None,
        manifest=None,
        path=None,
    ):
        self.config = config
        self.administrative = administrative
        self.roads = roads
        self.facilities = facilities
        self.manifest = manifest
        self.path = Path(path) if path is not None else None
        self.spatial = SpatialReference(config.metric_crs)
        self.hydro = hydro
        self.environment = environment

    @classmethod
    def load(cls, path):
        root = Path(path)
        manifest = WorldManifest.load(root / "manifest.json")
        config = WorldConfig.load(root / "config.json")
        if any(
            filename not in manifest.outputs
            for filename in (
                "config.json",
                "municipalities.parquet",
                "state_boundary.parquet",
            )
        ):
            raise WorldFormatError(
                "Administrative outputs are not recorded in the world manifest"
            )
        administrative = AdministrativeLayer.load(root, config.metric_crs)
        if (
            manifest.feature_counts.get("municipalities")
            != len(administrative.municipalities)
            or manifest.feature_counts.get("state_boundaries") != 1
        ):
            raise WorldFormatError(
                "Administrative counts do not match the world manifest"
            )
        roads = None
        road_files = ("roads_nodes.parquet", "roads_edges.parquet", "roads_routing.npz")
        if any((root / filename).exists() for filename in road_files):
            missing = [
                filename for filename in road_files if not (root / filename).is_file()
            ]
            if missing:
                raise WorldDataMissingError(f"Incomplete road layer: {missing}")
            if any(filename not in manifest.outputs for filename in road_files):
                raise WorldFormatError(
                    "Road outputs are not recorded in the world manifest"
                )
            from .roads import RoadNetwork

            roads = RoadNetwork.load(root, metric_crs=config.metric_crs)
            if manifest.feature_counts.get("road_nodes") != len(
                roads.nodes
            ) or manifest.feature_counts.get("road_edges") != len(roads.edges):
                raise WorldFormatError("Road counts do not match the world manifest")
        hydro = None
        from .hydro import HYDRO_FILES

        if config.hydro_enabled or any(
            (root / filename).exists() for filename in HYDRO_FILES
        ):
            if not config.hydro_enabled:
                raise WorldFormatError(
                    "Hydrology outputs require hydro_enabled configuration"
                )
            missing = [
                filename for filename in HYDRO_FILES if not (root / filename).is_file()
            ]
            if missing:
                raise WorldDataMissingError(f"Incomplete hydrology layer: {missing}")
            if any(filename not in manifest.outputs for filename in HYDRO_FILES):
                raise WorldFormatError(
                    "Hydrology outputs are not recorded in the world manifest"
                )
            from .hydro import HydroLayer

            hydro = HydroLayer.load(root, metric_crs=config.metric_crs)
            for name in ("waterways", "waterbodies", "wetlands", "coastline", "basins"):
                if manifest.feature_counts.get(name) != len(getattr(hydro, name)):
                    raise WorldFormatError(
                        f"Hydrology count does not match manifest: {name}"
                    )
            if manifest.feature_counts.get("road_hydro_crossings") != len(
                hydro.crossings
            ):
                raise WorldFormatError("Crossing count does not match manifest")
            if hydro.crossings:
                if roads is None:
                    raise WorldFormatError("Hydrology crossings require a road layer")
                road_ids = {edge.id for edge in roads.edges}
                if any(
                    crossing.road_edge_id not in road_ids
                    for crossing in hydro.crossings
                ):
                    raise WorldFormatError(
                        "Hydrology crossing references an unknown road edge"
                    )
        environment = None
        from .raster import ENVIRONMENT_FILES

        if config.environment_enabled or any(
            (root / filename).exists() for filename in ENVIRONMENT_FILES
        ):
            if not config.environment_enabled:
                raise WorldFormatError(
                    "Environmental outputs require environment_enabled configuration"
                )
            if any(filename not in manifest.outputs for filename in ENVIRONMENT_FILES):
                raise WorldFormatError(
                    "Environmental outputs are not recorded in the world manifest"
                )
            from .raster import EnvironmentStore

            environment = EnvironmentStore.load(
                root,
                expected_metric_crs=config.metric_crs,
                expected_resolution_m=config.raster_resolution_m,
            )
            if any(
                manifest.feature_counts.get(name) != count
                for name, count in environment.feature_counts.items()
            ):
                raise WorldFormatError(
                    "Environmental counts do not match the world manifest"
                )
            import numpy as np

            codes = {
                int(code)
                for code in np.unique(
                    environment.municipality_code[environment.state_mask]
                )
            }
            if not (codes - {0}).issubset(
                {
                    int(municipality.code)
                    for municipality in administrative.municipalities
                }
            ):
                raise WorldFormatError(
                    "Environmental labels reference an unknown municipality"
                )
        facilities = None
        if (root / "facilities.parquet").is_file():
            if "facilities.parquet" not in manifest.outputs:
                raise WorldFormatError(
                    "Facilities output is not recorded in the world manifest"
                )
            from .facilities import FacilityIndex

            facilities = FacilityIndex.load(root, metric_crs=config.metric_crs)
        return cls(
            config=config,
            administrative=administrative,
            roads=roads,
            facilities=facilities,
            hydro=hydro,
            environment=environment,
            manifest=manifest,
            path=root,
        )

    def _point(self, value):
        if isinstance(value, str):
            if self.facilities is None:
                raise WorldDataMissingError("This world has no facilities layer")
            facility = self.facilities.get(value)
            if facility is None:
                raise KeyError(f"Unknown facility: {value}")
            return facility.lon, facility.lat
        if hasattr(value, "lon") and hasattr(value, "lat"):
            return value.lon, value.lat
        return value

    def municipality_at(self, point):
        return self.administrative.municipality_at(self._point(point))

    def _roads(self):
        if self.roads is None:
            raise WorldDataMissingError("This world has no road layer")
        return self.roads

    def nearest_road(self, point):
        return self._roads().nearest_road(
            self._point(point), max_distance_m=self.config.max_snap_distance_m
        )

    def _node(self, endpoint):
        if isinstance(endpoint, Integral):
            return int(endpoint)
        if isinstance(endpoint, str):
            if self.facilities is None:
                raise WorldDataMissingError("This world has no facilities layer")
            endpoint = self.facilities.get(endpoint)
            if endpoint is None:
                raise KeyError("Unknown facility endpoint")
        if hasattr(endpoint, "road_node") and endpoint.road_node is not None:
            return endpoint.road_node
        return self._roads().nearest_node(
            self._point(endpoint), max_distance_m=self.config.max_snap_distance_m
        )

    def route(self, origin, destination, *, state=None):
        return self._roads().route(
            self._node(origin), self._node(destination), state=state
        )

    def travel_time(self, origin, destination, *, state=None):
        return self.route(origin, destination, state=state).travel_time_s

    def distance(self, origin, destination, *, state=None):
        return self.route(origin, destination, state=state).distance_m

    def nearby_facilities(self, point, radius_m):
        if self.facilities is None:
            raise WorldDataMissingError("This world has no facilities layer")
        return self.facilities.nearby(self._point(point), radius_m)

    def facilities_within(self, geometry):
        if self.facilities is None:
            raise WorldDataMissingError("This world has no facilities layer")
        return self.facilities.within(geometry)

    def roads_within(self, geometry):
        return self._roads().roads_within(geometry)

    def environment_at(self, point):
        if self.environment is None:
            raise WorldDataMissingError("This world has no environmental raster")
        return self.environment.at(self._point(point))
