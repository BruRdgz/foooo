"""Geographic world API independent from simulation and viewer logic."""

from importlib import import_module

_EXPORTS = {
    "PernambucoWorld": ".model",
    "WorldConfig": ".config",
    "Municipality": ".administrative",
    "AdministrativeLayer": ".administrative",
    "WorldBuildError": ".exceptions",
    "WorldDataMissingError": ".exceptions",
    "WorldFormatError": ".exceptions",
    "PointOutsideWorldError": ".exceptions",
    "NoRoadNearbyError": ".exceptions",
    "RouteNotFoundError": ".exceptions",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value
