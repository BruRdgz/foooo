"""SimGeoPE: reusable geospatial world and query engine for Pernambuco."""

from importlib import import_module

__version__ = "0.1.0"
__all__ = ["PernambucoWorld", "WorldConfig"]


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".world", __name__), name)
    globals()[name] = value
    return value
