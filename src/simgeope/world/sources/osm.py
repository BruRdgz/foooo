"""Local dated OSM inputs. No download or Overpass fallback."""

import re
from datetime import date, datetime
from pathlib import Path

from ..exceptions import WorldBuildError, WorldDataMissingError


def dated_pbf(path):
    path = Path(path)
    if not path.is_file():
        raise WorldDataMissingError(f"Local OSM PBF does not exist: {path}")
    if not path.name.endswith(".osm.pbf"):
        raise WorldBuildError("OSM input must be a local .osm.pbf file")
    return path, source_date_from_filename(path)


def source_date_from_filename(path):
    """Read a pinned OSM date without requiring the source to exist."""
    match = re.search(
        r"(?<!\d)(\d{4}[-_]\d{2}[-_]\d{2}|\d{8}|\d{6})(?!\d)", Path(path).name
    )
    if match is None:
        raise WorldBuildError(
            "Pin the OSM source date in its filename (YYYY-MM-DD, YYYYMMDD or YYMMDD)"
        )
    token = match.group(1).replace("_", "-")
    fmt = "%Y-%m-%d" if "-" in token else "%Y%m%d" if len(token) == 8 else "%y%m%d"
    try:
        source_date = datetime.strptime(token, fmt).date()
    except ValueError as exc:
        raise WorldBuildError(f"Invalid OSM source date: {token}") from exc
    if source_date > date.today():
        raise WorldBuildError(f"OSM source date is in the future: {source_date}")
    return source_date.isoformat()
