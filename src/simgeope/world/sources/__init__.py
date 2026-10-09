"""Build-time pinned source adapters, imported only when requested."""

from importlib import import_module

_EXPORTS = {"IBGESource": ".ibge", "HydroSource": ".hydro", "OSMHydroSource": ".hydro"}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value
