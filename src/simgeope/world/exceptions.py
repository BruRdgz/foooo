"""Explicit failures exposed by the geographic world API."""


class WorldBuildError(Exception):
    """An input cannot be normalized into a valid world."""


class WorldDataMissingError(Exception):
    """A required source or built layer is missing."""


class WorldFormatError(Exception):
    """A persisted world is incompatible or fails integrity validation."""


class PointOutsideWorldError(Exception):
    """A query requires a location inside the world."""


class NoRoadNearbyError(Exception):
    """No valid road snap exists within the allowed distance."""


class RouteNotFoundError(Exception):
    """The requested road endpoints have no connecting path."""
