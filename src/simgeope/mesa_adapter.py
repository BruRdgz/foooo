"""Optional Mesa bridge sharing an already-built geographic world.

Import this module explicitly after installing ``simgeope[simulation]``.
Agent activation and dynamic road overlays belong to the consuming simulation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

try:
    import mesa
except ModuleNotFoundError as exc:
    if exc.name != "mesa":
        raise
    raise ImportError(
        "The Mesa adapter requires the simulation extra: "
        "install simgeope[simulation] or run uv with --extra simulation"
    ) from exc

if TYPE_CHECKING:
    from .world.model import PernambucoWorld


class WorldModel(mesa.Model):
    """Mesa model referencing shared geography without copying or updating it."""

    def __init__(
        self,
        world: PernambucoWorld,
        *,
        seed: int | None = None,
        rng: np.random.Generator | None = None,
    ):
        # Mesa >=3.2 uses `rng=`; keep `seed=` as a convenience for callers.
        if rng is None:
            rng = np.random.default_rng(seed)
        super().__init__(rng=rng)
        self.world = world

    def route(self, origin, destination, *, state=None):
        return self.world.route(origin, destination, state=state)

    def travel_time(self, origin, destination, *, state=None):
        return self.world.travel_time(origin, destination, state=state)

    def travel_times(self, origin, destinations, *, state=None):
        return self.world.travel_times(origin, destinations, state=state)

    def environment_at(self, location):
        return self.world.environment_at(location)

    def step(self):
        """Advance Mesa's step/time bookkeeping; consumers choose activation."""
        super().step()
