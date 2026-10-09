"""Simulation-owned road costs; the canonical network remains immutable."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class RoadRuntimeState:
    closed_edges: frozenset[int] = field(default_factory=frozenset)
    cost_multiplier: Mapping[int, float] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "closed_edges", frozenset(self.closed_edges))
        values = dict(self.cost_multiplier)
        if any(not isfinite(value) or value <= 0 for value in values.values()):
            raise ValueError(
                "Road cost multipliers must be finite and strictly positive"
            )
        object.__setattr__(self, "cost_multiplier", MappingProxyType(values))
