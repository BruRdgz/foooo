"""H5N1 agent-based model on top of a pre-built PernambucoWorld."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import TYPE_CHECKING

from shapely.geometry import Point

from simgeope.mesa_adapter import WorldModel

from .agents import BirdAgent, FarmAgent, TruckAgent
from .params import ModelParams

if TYPE_CHECKING:
    from simgeope.world.facilities import Facility
    from simgeope.world.model import PernambucoWorld


class AgentModel(WorldModel):
    """Daily-tick ABM sharing the world's geography, hydrology and routing."""

    def __init__(
        self,
        world: PernambucoWorld,
        *,
        params: ModelParams | None = None,
        seed: int | None = None,
        log_path: str | Path | None = None,
    ):
        super().__init__(world, seed=seed)
        self.params: ModelParams = params or ModelParams()

        self.agents_by_id: dict[str, object] = {}
        self.farms: list[FarmAgent] = []
        self.birds: list[BirdAgent] = []
        self.trucks: list[TruckAgent] = []
        self._farms_by_facility_id: dict[str, FarmAgent] = {}

        self.day_count: int = 0
        self.total_infections: float = 0.0
        self.total_deaths: float = 0.0
        self.total_culled: float = 0.0
        self.cumulative_cost: float = 0.0
        self.current_rt: float = 0.0
        self.exposed_history: list[float] = []

        self.log_path = Path(log_path) if log_path is not None else None
        if self.log_path is not None:
            if self.log_path.exists():
                self.log_path.unlink()
            with self.log_path.open("w", newline="") as fh:
                csv.writer(fh).writerow(["dia", "tipo_evento", "detalhes"])

    # ========================================================= registries
    def _ensure_available_id(self, agent_id: str) -> None:
        if agent_id in self.agents_by_id:
            raise ValueError(f"Duplicate agent ID: {agent_id!r}")

    def remove_agent(self, agent) -> None:
        """Remove agent from local registries (Mesa 3 also removes from model.agents)."""
        if isinstance(agent, BirdAgent):
            if agent in self.birds:
                self.birds.remove(agent)
            self.agents_by_id.pop(agent.bird_id, None)
        elif isinstance(agent, TruckAgent):
            if agent in self.trucks:
                self.trucks.remove(agent)
            self.agents_by_id.pop(agent.truck_id, None)
        elif isinstance(agent, FarmAgent):
            if agent in self.farms:
                self.farms.remove(agent)
            self._farms_by_facility_id.pop(agent.farm_id, None)
            self.agents_by_id.pop(agent.farm_id, None)
        else:
            return
        # Mesa 3: Agent.remove() deregisters from model.agents
        remove = getattr(agent, "remove", None)
        if callable(remove):
            try:
                remove()
            except Exception:
                pass

    # ========================================================= factories
    def add_farm(self, facility: Facility, *, pop_total: int = 0) -> FarmAgent:
        farm = FarmAgent(self, facility, pop_total=pop_total)
        self.farms.append(farm)
        self._farms_by_facility_id[farm.farm_id] = farm
        return farm

    def add_bird(self, bird_id: str, location) -> BirdAgent:
        bird = BirdAgent(self, bird_id, location)
        self.birds.append(bird)
        return bird

    def add_truck(self, truck_id: str, at_farm: FarmAgent) -> TruckAgent:
        truck = TruckAgent(self, truck_id, at_farm)
        self.trucks.append(truck)
        return truck

    # ===================================================== scenario setup
    def add_farms_from_world(self, *, pop_total: int = 5_000) -> int:
        if self.world.facilities is None:
            raise RuntimeError("World has no FacilityIndex loaded")
        for facility in self.world.facilities.facilities:
            self.add_farm(facility, pop_total=pop_total)
        return len(self.farms)

    def add_birds_uniform(self, n: int) -> int:
        """Place `n` birds uniformly inside the PE state polygon.

        Sampling happens in the world's metric CRS for uniform area
        coverage, then unprojects to WGS84.
        """
        if n <= 0:
            return 0

        admin = self.world.administrative
        state_wgs84 = admin.state_geometry_wgs84
        state_metric = admin.spatial.geometry_to_metric(state_wgs84)
        if state_metric.is_empty:
            raise RuntimeError("State boundary polygon is empty")

        sp = admin.spatial
        minx, miny, maxx, maxy = state_metric.bounds
        p = self.params
        added = 0
        attempts = 0
        max_attempts = n * 200
        while added < n and attempts < max_attempts:
            attempts += 1
            mx = self.random.uniform(minx, maxx)
            my = self.random.uniform(miny, maxy)
            if not state_metric.contains(Point(mx, my)):
                continue
            lon, lat = sp.unproject((mx, my))
            bird = self.add_bird(f"bird-{added + 1}", (lon, lat))
            if self.random.random() * 100 < p.initial_bird_infection_pct:
                bird.infect()
            added += 1
        if added < n:
            self.log_event("AVISO_AVES", f"Só foram criadas {added} de {n} aves")
        return added

    def add_trucks_random(self, n: int) -> int:
        if n <= 0 or not self.farms:
            return 0
        for i in range(n):
            farm = self.random.choice(self.farms)
            self.add_truck(f"truck-{i + 1}", farm)
        return n

    def seed_initial_outbreak(self) -> None:
        if not self.farms:
            return
        farm = self.random.choice(self.farms)
        farm.seed_from_external(self.params.initial_infected, "INTRODUCAO_INICIAL")

    # ===================================================== spatial queries
    def farms_near(self, farm: FarmAgent, radius_km: float) -> list[FarmAgent]:
        """Farms within `radius_km` (facility KD-tree in metric CRS)."""
        facilities = self.world.facilities
        if facilities is None:
            return []
        nearby = facilities.nearby((farm.lon, farm.lat), radius_km * 1000.0)
        out = []
        for facility in nearby:
            if facility.id == farm.farm_id:
                continue
            other = self._farms_by_facility_id.get(facility.id)
            if other is not None and not other.culled:
                out.append(other)
        return out

    def distance_m(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        sp = self.world.spatial
        ax, ay = sp.project(a)
        bx, by = sp.project(b)
        return math.hypot(bx - ax, by - ay)

    def nearest_water_point(
        self, origin: tuple[float, float]
    ) -> tuple[float, float] | None:
        hydro = self.world.hydro
        if hydro is None:
            return None
        feature = hydro.nearest_waterway(origin) or hydro.nearest_waterbody(origin)
        if feature is None:
            return None
        sp = hydro.spatial
        px, py = sp.project(origin)
        geom = sp.geometry_to_metric(feature.geometry_wgs84)
        if geom.is_empty:
            return None
        closest = geom.interpolate(geom.project(Point(px, py)))
        return sp.unproject((closest.x, closest.y))

    def farm_near_water(self, farm: FarmAgent) -> bool:
        hydro = self.world.hydro
        if hydro is None:
            return False
        d = hydro.distance_to_water(farm.location)
        return d is not None and d <= self.params.farm_water_proximity_km * 1000.0

    def move_towards(
        self,
        origin: tuple[float, float],
        target: tuple[float, float],
        step_m: float,
    ) -> tuple[float, float]:
        sp = self.world.spatial
        x, y = sp.project(origin)
        tx, ty = sp.project(target)
        dx, dy = tx - x, ty - y
        n = math.hypot(dx, dy)
        if n == 0:
            return origin
        return sp.unproject((x + step_m * dx / n, y + step_m * dy / n))

    # ============================================================ daily step
    def step(self) -> None:
        self.day_count += 1
        for f in self.farms:
            f.new_exposed_today = 0.0

        for f in self.farms:
            f.step_intra_farm()

        for b in list(self.birds):
            b.step()

        self._bird_to_farm()

        for t in self.trucks:
            t.step()

        for f in self.farms:
            f.try_notify()
        for f in self.farms:
            f.try_quarantine()

        self._update_economic_cost()
        self._update_rt()

        super().step()  # Mesa bookkeeping

    def run(self, days: int) -> None:
        for _ in range(days):
            self.step()

    # -------------------------------------------------------- subsystems
    def _bird_to_farm(self) -> None:
        p = self.params
        radius_m = p.bird_farm_proximity_km * 1000.0
        for farm in self.farms:
            if farm.culled or farm.quarantined or farm.susceptible <= 0:
                continue
            here = farm.location
            if not any(
                b.bird_state == "I" and self.distance_m(here, b.location) <= radius_m
                for b in self.birds
            ):
                continue
            prob = p.infection_prob_bird_to_farm
            if self.farm_near_water(farm):
                prob *= 1.5
            if self.random.random() * 100 < min(prob, 100.0):
                farm.seed_from_external(
                    p.seed_size_birds, "INTRODUCAO_AVES_MIGRATORIAS"
                )

    def _update_economic_cost(self) -> None:
        p = self.params
        active = sum(1 for f in self.farms if f.quarantined and not f.culled)
        self.cumulative_cost += active * p.quarantine_cost_per_day

    def _update_rt(self) -> None:
        p = self.params
        now = sum(f.new_exposed_today for f in self.farms)
        self.exposed_history.append(now)
        gi = p.generation_interval_days
        if len(self.exposed_history) > gi:
            past = self.exposed_history[-1 - gi]
            self.current_rt = now / past if past > 0 else 0.0
        else:
            self.current_rt = 0.0

    # ============================================================ reports
    @property
    def healthy_farms(self) -> int:
        return sum(
            1 for f in self.farms if not f.culled and f.infected == 0 and f.exposed == 0
        )

    @property
    def notified_farms(self) -> int:
        return sum(
            1 for f in self.farms if f.notified and (f.infected > 0 or f.exposed > 0)
        )

    @property
    def hidden_farms(self) -> int:
        return sum(
            1
            for f in self.farms
            if not f.notified and (f.infected > 0 or f.exposed > 0)
        )

    # ============================================================ logging
    def log_event(self, tipo: str, detalhes: str) -> None:
        if self.log_path is None:
            return
        with self.log_path.open("a", newline="") as fh:
            csv.writer(fh).writerow([self.day_count, tipo, detalhes])


def install_synthetic_facilities(
    world,
    n: int = 50,
    *,
    kind: str = "farm",
    seed: int | None = 42,
):
    """Attach a synthetic FacilityIndex to a world that lacks one.

    Samples `n` points uniformly by area inside the PE state polygon, snaps
    each to the road network when available, and assigns the result to
    ``world.facilities``. Intended for tests and demos.
    """
    import numpy as np
    from shapely.geometry import Point

    from simgeope.world.facilities import Facility, FacilityIndex, snap_facility

    admin = world.administrative
    sp = admin.spatial
    state_metric = sp.geometry_to_metric(admin.state_geometry_wgs84)
    minx, miny, maxx, maxy = state_metric.bounds
    rng = np.random.default_rng(seed)

    facilities: list[Facility] = []
    attempts = 0
    max_attempts = n * 200
    while len(facilities) < n and attempts < max_attempts:
        attempts += 1
        mx = rng.uniform(minx, maxx)
        my = rng.uniform(miny, maxy)
        if not state_metric.contains(Point(mx, my)):
            continue
        lon, lat = sp.unproject((mx, my))
        fac = Facility(
            id=f"f{len(facilities) + 1:04d}",
            kind=kind,
            lon=lon,
            lat=lat,
        )
        if world.roads is not None:
            try:
                fac = snap_facility(
                    fac,
                    world.roads,
                    admin,
                    max_distance_m=world.config.max_snap_distance_m,
                    allow_excessive=True,
                )
            except Exception:
                # No road within max_snap_distance_m: skip this sample.
                continue
        facilities.append(fac)

    if not facilities:
        raise RuntimeError(
            "Could not place any synthetic facility inside the state polygon"
        )

    world.facilities = FacilityIndex(
        facilities, metric_crs=world.config.metric_crs
    )
    return world.facilities