"""Predicted infection risk layer on the world's environmental raster grid.

Each state cell holds the probability that a hypothetical susceptible farm at
the cell centre, with the base biosecurity, has infection introduced today. It
applies the model's own introduction rules to the current model state:

- birds: infected birds within ``bird_farm_proximity_km`` of the centre, with
  the near-water multiplier taken from the raster's water distance band (cell
  centre sampling, so it can differ from the exact farm-point distance);
- fomites: today's truck arrivals at farms inside the cell, each with its load
  on arrival. Cells without visited farms have no fomite exposure;
- neighbourhood: shedding farms within ``local_spread_radius_km`` of the
  centre, with the local spread distance kernel.

The pathways are combined as independent events. Cells outside the state mask
are NaN, distinct from zero risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from scipy.spatial import cKDTree

from simgeope.world.exceptions import WorldDataMissingError
from simgeope.world.raster import RasterGrid
from simgeope.world.spatial import SpatialReference

from .agents import bird_introduction_prob, fomite_introduction_prob, local_spread_prob

if TYPE_CHECKING:
    from .model import AgentModel

# Risk level codes; OUTSIDE marks cells outside the state mask.
OUTSIDE, GREEN, YELLOW, RED = -1, 0, 1, 2


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    day: int
    grid: RasterGrid
    bird: np.ndarray
    fomite: np.ndarray
    local: np.ndarray
    total: np.ndarray

    def levels(self, yellow_threshold: float, red_threshold: float) -> np.ndarray:
        """Classify ``total`` as GREEN/YELLOW/RED; OUTSIDE where it is NaN."""
        if not 0 <= yellow_threshold <= red_threshold <= 1:
            raise ValueError("Risk thresholds must satisfy 0 <= yellow <= red <= 1")
        levels = np.full(self.total.shape, OUTSIDE, dtype=np.int8)
        inside = ~np.isnan(self.total)
        levels[inside] = GREEN
        levels[inside & (self.total >= yellow_threshold)] = YELLOW
        levels[inside & (self.total >= red_threshold)] = RED
        return levels

    def save(self, path: str | Path) -> None:
        """Write the bands and their grid as one self-describing NPZ file."""
        g = self.grid
        np.savez_compressed(
            path,
            day=self.day,
            bird=self.bird,
            fomite=self.fomite,
            local=self.local,
            total=self.total,
            origin_x_m=g.origin_x_m,
            origin_y_m=g.origin_y_m,
            resolution_m=g.resolution_m,
            metric_crs=g.metric_crs,
        )


def infection_risk(model: AgentModel) -> RiskSnapshot:
    environment = model.world.environment
    if environment is None:
        raise WorldDataMissingError("The risk layer requires an environmental raster")
    p = model.params
    grid = environment.grid
    mask = environment.state_mask
    reference = SpatialReference(grid.metric_crs)

    # State cells are handled as a flat vector, then scattered back to the grid.
    rows, cols = np.nonzero(mask)
    centres_x = grid.origin_x_m + (cols + 0.5) * grid.resolution_m
    centres_y = grid.origin_y_m - (rows + 0.5) * grid.resolution_m

    centres = np.column_stack([centres_x, centres_y])
    tree = cKDTree(centres) if rows.size else None

    bird = np.zeros(rows.size)
    infected = [b.location for b in model.birds if b.bird_state == "I"]
    if infected and tree is not None:
        near_birds = np.zeros(rows.size, dtype=bool)
        points = [reference.project(location) for location in infected]
        for cells in tree.query_ball_point(points, p.bird_farm_proximity_km * 1000.0):
            near_birds[cells] = True
        # NaN (unknown water distance) compares False: not treated as near water.
        near_water = (
            environment.water_distance_m[rows, cols]
            <= p.farm_water_proximity_km * 1000.0
        )
        bird[near_birds & ~near_water] = bird_introduction_prob(p, near_water=False)
        bird[near_birds & near_water] = bird_introduction_prob(p, near_water=True)

    cell_of = np.full(grid.shape, -1, dtype=np.int64)
    cell_of[rows, cols] = np.arange(rows.size)
    escape = np.ones(rows.size)
    for movement in reversed(model.movements):
        if movement.day != model.day_count:
            break  # Movements are appended in day order.
        farm = model.farm_by_id(movement.destination_id)
        if farm is None:
            continue  # Arrival at a hub.
        try:
            cell = cell_of[grid.index(*reference.project(farm.location))]
        except IndexError:
            continue  # Farm outside the raster grid.
        if cell >= 0:
            escape[cell] *= 1.0 - fomite_introduction_prob(
                p, load=movement.load_on_arrival, biosecurity=p.base_biosecurity
            )
    fomite = 1.0 - escape

    escape = np.ones(rows.size)
    for farm in model.farms:
        shedding = 0.0 if farm.culled else farm.shedding()
        if shedding <= 0 or tree is None:
            continue
        source = np.asarray(reference.project(farm.location))
        cells = tree.query_ball_point(source, p.local_spread_radius_km * 1000.0)
        for cell, distance in zip(
            cells, np.hypot(*(centres[cells] - source).T), strict=True
        ):
            escape[cell] *= 1.0 - local_spread_prob(
                p,
                shedding=shedding,
                distance_m=float(distance),
                biosecurity=p.base_biosecurity,
            )
    local = 1.0 - escape

    def to_grid(values: np.ndarray) -> np.ndarray:
        band = np.full(grid.shape, np.nan, dtype=np.float32)
        band[rows, cols] = values
        return band

    total = 1.0 - (1.0 - bird) * (1.0 - fomite) * (1.0 - local)
    return RiskSnapshot(
        day=model.day_count,
        grid=grid,
        bird=to_grid(bird),
        fomite=to_grid(fomite),
        local=to_grid(local),
        total=to_grid(total),
    )
