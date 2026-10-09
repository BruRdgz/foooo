"""IBGE adapter for pinned local/cache GeoJSON and municipality metadata.

No network access occurs during a build or world load. Downloaded official IBGE
files can be pinned in the cache and selected explicitly with WorldConfig.
"""

from __future__ import annotations

import json
from pathlib import Path

from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import shape
from shapely.ops import transform, unary_union

from ..administrative import AdministrativeLayer, Municipality
from ..exceptions import WorldBuildError, WorldDataMissingError
from ..manifest import SourceRecord


def _property(properties, *names):
    normalized = {str(k).casefold(): value for k, value in properties.items()}
    return next(
        (
            normalized[name.casefold()]
            for name in names
            if normalized.get(name.casefold()) is not None
        ),
        None,
    )


def _code(value):
    if value is None:
        raise WorldBuildError("IBGE feature is missing its administrative code")
    return str(value).removesuffix(".0")


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorldBuildError(f"Cannot read IBGE JSON: {path}") from exc


class IBGESource:
    def __init__(
        self,
        state_path,
        municipalities_path,
        names_path=None,
        *,
        cache_dir=None,
        year=None,
        expected_count=185,
    ):
        self.cache_dir = Path(cache_dir or "data/raw/ibge")
        self.state_path = (
            Path(state_path) if state_path else self.cache_dir / "ibge_uf26.geojson"
        )
        self.municipalities_path = (
            Path(municipalities_path)
            if municipalities_path
            else self.cache_dir / "ibge_uf26_municipios.geojson"
        )
        self.names_path = Path(names_path) if names_path else None
        self.year = year
        if type(expected_count) is not int or expected_count < 1:
            raise ValueError("expected_count must be a positive integer")
        self.expected_count = expected_count
        self.metric_crs = None
        self.warnings = []
        for path in (self.state_path, self.municipalities_path):
            if not path.is_file():
                raise WorldDataMissingError(
                    f"Pinned IBGE source missing: {path}; configure local source paths or populate the cache"
                )
        if self.names_path is not None and not self.names_path.is_file():
            raise WorldDataMissingError(
                f"IBGE municipality names missing: {self.names_path}"
            )

    @classmethod
    def from_config(cls, config):
        source = cls(
            config.ibge_state,
            config.ibge_municipalities,
            config.ibge_names,
            cache_dir=config.cache_dir,
            year=config.ibge_year,
        )
        source.metric_crs = config.metric_crs
        return source

    def provenance(self):
        records = [
            SourceRecord.from_path(
                self.state_path,
                url="https://servicodados.ibge.gov.br/api/v3/malhas/estados/26?formato=application/vnd.geo+json&qualidade=maxima",
                dataset_version=str(self.year) if self.year else None,
            ),
            SourceRecord.from_path(
                self.municipalities_path,
                url="https://servicodados.ibge.gov.br/api/v3/malhas/estados/26?formato=application/vnd.geo+json&qualidade=maxima&intrarregiao=municipio",
                dataset_version=str(self.year) if self.year else None,
            ),
        ]
        if self.names_path:
            records.append(
                SourceRecord.from_path(
                    self.names_path,
                    url="https://servicodados.ibge.gov.br/api/v1/localidades/estados/26/municipios",
                )
            )
        return tuple(records)

    def _geometry(self, feature, code, source_crs):
        try:
            geometry = shape(feature["geometry"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WorldBuildError(f"Missing/invalid IBGE geometry: {code}") from exc
        if not source_crs.equals(CRS.from_epsg(4326), ignore_axis_order=True):
            transformer = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
            geometry = transform(transformer.transform, geometry)
        if not geometry.is_valid:
            geometry = make_valid(geometry)
            self.warnings.append(f"Repaired invalid IBGE geometry for {code}")
        if geometry.geom_type == "GeometryCollection":
            polygons = [
                part
                for part in geometry.geoms
                if part.geom_type in ("Polygon", "MultiPolygon")
            ]
            geometry = unary_union(polygons)
        if (
            geometry.is_empty
            or geometry.geom_type not in ("Polygon", "MultiPolygon")
            or not geometry.is_valid
        ):
            raise WorldBuildError(
                f"IBGE administrative geometry is not polygonal: {code}"
            )
        min_x, min_y, max_x, max_y = geometry.bounds
        if not (-42 <= min_x <= max_x <= -31 and -10 <= min_y <= max_y <= -3):
            raise WorldBuildError(
                f"IBGE geometry outside Pernambuco's WGS84 extent: {code}"
            )
        return geometry

    def _features(self, path):
        data = _read_json(path)
        if (
            not isinstance(data, dict)
            or data.get("type") != "FeatureCollection"
            or not isinstance(data.get("features"), list)
        ):
            raise WorldBuildError(
                f"IBGE input must be a GeoJSON FeatureCollection: {path}"
            )
        try:
            crs = data.get("crs")
            source_crs = CRS.from_user_input(
                crs["properties"]["name"] if crs else "EPSG:4326"
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise WorldBuildError(f"Invalid IBGE source CRS: {path}") from exc
        if not any(
            source_crs.equals(CRS.from_epsg(epsg), ignore_axis_order=True)
            for epsg in (4326, 4674)
        ):
            raise WorldBuildError(
                "IBGE GeoJSON must use WGS84 or SIRGAS 2000 geographic coordinates"
            )
        if any(
            not isinstance(feature, dict)
            or not isinstance(feature.get("properties"), dict)
            for feature in data["features"]
        ):
            raise WorldBuildError(f"IBGE features must have object properties: {path}")
        return data["features"], source_crs

    def load(self, metric_crs=None):
        metric_crs = metric_crs or self.metric_crs
        if metric_crs is None:
            raise ValueError("Supply metric_crs or use IBGESource.from_config")
        self.warnings = []
        names = {}
        if self.names_path:
            records = _read_json(self.names_path)
            if not isinstance(records, list):
                raise WorldBuildError("IBGE locality metadata must be a JSON array")
            for record in records:
                if not isinstance(record, dict):
                    raise WorldBuildError("IBGE locality records must be JSON objects")
                code = _code(_property(record, "id", "code", "CD_MUN"))
                name = _property(record, "nome", "name", "NM_MUN")
                if code in names:
                    raise WorldBuildError(f"Duplicate IBGE locality metadata: {code}")
                names[code] = name
        municipalities = []
        municipal_features, municipal_crs = self._features(self.municipalities_path)
        for feature in municipal_features:
            properties = feature.get("properties") or {}
            code = _code(_property(properties, "codarea", "CD_MUN", "code", "id"))
            name = _property(properties, "NM_MUN", "nome", "name") or names.get(code)
            if not code.startswith("26") or len(code) != 7:
                raise WorldBuildError(
                    f"IBGE input contains non-Pernambuco municipality: {code}"
                )
            if not name:
                raise WorldBuildError(
                    f"Municipality {code} has no name; provide pinned IBGE locality metadata"
                )
            municipalities.append(
                Municipality(
                    code, str(name), self._geometry(feature, code, municipal_crs)
                )
            )
        codes = {m.code for m in municipalities}
        if "2605459" not in codes:
            raise WorldBuildError(
                "IBGE Pernambuco input is incomplete: Fernando de Noronha (2605459) is required"
            )
        if len(codes) != self.expected_count:
            raise WorldBuildError(
                f"IBGE Pernambuco input is incomplete: expected {self.expected_count} divisions, got {len(codes)}"
            )
        if names and codes != set(names):
            raise WorldBuildError(
                "IBGE municipality geometries and locality metadata have different codes"
            )
        state_features, state_crs = self._features(self.state_path)
        if len(state_features) != 1:
            raise WorldBuildError(
                "IBGE state input must contain exactly one Pernambuco feature"
            )
        state = state_features[0]
        state_code = _code(
            _property(state.get("properties") or {}, "codarea", "CD_UF", "code", "id")
        )
        if state_code != "26":
            raise WorldBuildError(
                f"Expected Pernambuco state code 26, got {state_code}"
            )
        layer = AdministrativeLayer(
            municipalities, self._geometry(state, "26", state_crs), metric_crs
        )
        layer.warnings = tuple(self.warnings)
        return layer
