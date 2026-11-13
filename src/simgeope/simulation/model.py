"""H5N1 agent-based model on top of a pre-built PernambucoWorld."""

from __future__ import annotations

import csv
import math
import warnings
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import astuple, dataclass, fields
from itertools import takewhile
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import Point

from simgeope.mesa_adapter import WorldModel
from simgeope.world.exceptions import NoRoadNearbyError, WorldDataMissingError
from simgeope.world.facilities import Facility, FacilityIndex, snap_facility

from .agents import (
    FARM_STATUSES,
    ZONE_RINGS,
    BirdAgent,
    FarmAgent,
    HubAgent,
    Transmission,
    TruckAgent,
    TruckMovement,
    bird_introduction_prob,
    decayed_fomite_load,
    local_spread_prob,
)
from .params import FleetSpec, ModelParams

if TYPE_CHECKING:
    from simgeope.world.model import PernambucoWorld


def sample_point_in(polygon_metric, uniform, *, attempts: int = 200) -> Point | None:
    """Uniform-by-area point in a metric (multi)polygon, or None after `attempts`.

    Picks a part by area, then rejection-samples within that part's bounds, so
    a far-off island (Fernando de Noronha) does not inflate the sampling box.
    ``uniform(low, high)`` is the caller's seeded generator.
    """
    parts = [part for part in getattr(polygon_metric, "geoms", [polygon_metric])]
    total = sum(part.area for part in parts)
    if total <= 0:
        return None
    for _ in range(attempts):
        pick = uniform(0.0, total)
        part = parts[-1]
        for candidate in parts:
            pick -= candidate.area
            if pick <= 0:
                part = candidate
                break
        minx, miny, maxx, maxy = part.bounds
        point = Point(uniform(minx, maxx), uniform(miny, maxy))
        if part.contains(point):
            return point
    return None


# Standard deviation of landing positions around a flock's arrival site.
FLOCK_SPREAD_M = 300.0
# Width given to mapped water lines (rivers, canals, coastline) when weighing
# them against water polygons as bird arrival sites.
NOMINAL_WATERLINE_WIDTH_M = 50.0
# Farm introduction pathways counted per day in the history rows.
INTRODUCTION_EVENTS = (
    "INTRODUCAO_FOMITE",
    "INTRODUCAO_VIZINHANCA",
    "INTRODUCAO_AVES_MIGRATORIAS",
)


class Event(NamedTuple):
    """One log entry; `farm_id` is set for farm-scoped events, else None."""

    day: int
    kind: str
    details: str
    farm_id: str | None


class FlockArrival(NamedTuple):
    """A migratory flock landing at (lon, lat); infected flocks carry one I bird."""

    day: int
    lon: float
    lat: float
    size: int
    infected: bool


@dataclass(slots=True)
class Focus:
    """A confirmed focus of the official protocol and its emergency area.

    The area is active from `confirmed_day` until the day before
    `release_day`, which stays None until it is known: when the farm is
    culled, or when its infection ends if it is never depopulated.
    """

    farm_id: str
    confirmed_day: int
    depopulated_day: int | None
    release_day: int | None

    def active_on(self, day: int) -> bool:
        return self.confirmed_day <= day and (
            self.release_day is None or day < self.release_day
        )

    def standstill_on(self, day: int, standstill_days: int) -> bool:
        """Whether, at the end of `day`, trucks are barred from the area.

        The model applies it to the next day's trips, so a standstill of S
        days stops truck stops on days confirmed_day + 1 .. confirmed_day + S.
        """
        return self.active_on(day) and day < self.confirmed_day + standstill_days


def ring_index(distances_m: np.ndarray, params: ModelParams) -> np.ndarray:
    """Index into ``ZONE_RINGS`` of each distance from a focus.

    Boundaries belong to the inner ring; ``len(ZONE_RINGS)`` means beyond the
    protection radius. Radii are measured from the focus, not ring widths.
    """
    radii_m = 1000.0 * np.array(
        [
            params.perifocus_radius_km,
            params.surveillance_radius_km,
            params.protection_radius_km,
        ]
    )
    return np.searchsorted(radii_m, distances_m, side="left")


def dominant_source(hazards: Sequence[float]) -> tuple[int, float]:
    """Index of the largest hazard (the first on ties) and its share of the total.

    Hazards are -log(1 - p) of independent sources. Certain sources (p = 1,
    infinite hazard) split the attribution equally among themselves.
    """
    top = max(range(len(hazards)), key=hazards.__getitem__)
    if math.isinf(hazards[top]):
        return top, 1.0 / sum(math.isinf(h) for h in hazards)
    return top, hazards[top] / math.fsum(hazards)


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
        self.hubs: list[HubAgent] = []
        self.trucks: list[TruckAgent] = []
        self.movements: list[TruckMovement] = []
        # Provenance of every farm introduction, in event order.
        self.transmissions: list[Transmission] = []
        self.flock_arrivals: list[FlockArrival] = []
        # Official protocol: confirmed foci in confirmation order, and notified
        # farms awaiting confirmation as (due day, farm) in notification order.
        self.foci: list[Focus] = []
        self._pending_foci: list[tuple[int, FarmAgent]] = []
        self._farms_by_facility_id: dict[str, FarmAgent] = {}
        # Origin road node -> travel times to every stop node (see below).
        self._stop_node_index: dict[int, int] | None = None
        self._travel_cache: dict[int, np.ndarray] = {}

        self.day_count: int = 0
        self.total_infections: float = 0.0
        self.total_deaths: float = 0.0
        self.total_culled: float = 0.0
        self.cumulative_cost: float = 0.0
        self.current_rt: float = 0.0
        self.exposed_history: list[float] = []
        self.wild_bird_infections: int = 0
        self.wild_bird_deaths: int = 0
        self.birds_arrived: int = 0
        self.flocks_arrived: int = 0
        # One summary row per day from day 0, for plots.
        self.history: list[dict[str, float | dict[str, int]]] = []
        self._bird_serial = 0
        self._state_metric = None
        self._water_cache: tuple[list, np.ndarray] | None = None
        # Projected farm coordinates and KD-tree, rebuilt when farms change.
        self._farm_xy: np.ndarray | None = None
        self._farm_tree: cKDTree | None = None

        # Mirrored to the CSV log (dia, tipo_evento, detalhes, granja) when
        # configured.
        self.events: list[Event] = []
        self.log_path = Path(log_path) if log_path is not None else None
        if self.log_path is not None:
            if self.log_path.exists():
                self.log_path.unlink()
            with self.log_path.open("w", newline="") as fh:
                csv.writer(fh).writerow(["dia", "tipo_evento", "detalhes", "granja"])

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
            self._invalidate_travel_cache()
            self._farm_tree = None
        elif isinstance(agent, HubAgent):
            if agent in self.hubs:
                self.hubs.remove(agent)
            self.agents_by_id.pop(agent.hub_id, None)
            self._invalidate_travel_cache()
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
    def _snapped(self, facility: Facility) -> Facility:
        """Snap to the road network unless the facility has a valid node.

        Raises NoRoadNearbyError before any registration, keeping it atomic.
        """
        if not isinstance(facility, Facility):
            raise TypeError("Expected a Facility")
        roads = self.world.roads
        if roads is None:
            return facility
        if facility.road_node is not None:
            try:
                roads.node(facility.road_node)
                return facility
            except KeyError:
                pass  # Stale node from another network: resnap below.
        return snap_facility(
            facility,
            roads,
            self.world.administrative,
            max_distance_m=self.world.config.max_snap_distance_m,
        )

    def add_farm(self, facility: Facility, *, pop_total: int = 0) -> FarmAgent:
        farm = FarmAgent(self, self._snapped(facility), pop_total=pop_total)
        self.farms.append(farm)
        self._farms_by_facility_id[farm.farm_id] = farm
        self._invalidate_travel_cache()
        self._farm_tree = None
        return farm

    def add_hub(self, facility: Facility) -> HubAgent:
        hub = HubAgent(self, self._snapped(facility))
        self.hubs.append(hub)
        self._invalidate_travel_cache()
        return hub

    def add_bird(self, bird_id: str, location, *, roost=None) -> BirdAgent:
        bird = BirdAgent(self, bird_id, location, roost=roost)
        self.birds.append(bird)
        return bird

    def add_truck(
        self,
        truck_id: str,
        base: HubAgent,
        clients=(),
        *,
        max_stops_per_day: int = 1,
    ) -> TruckAgent:
        truck = TruckAgent(
            self, truck_id, base, clients, max_stops_per_day=max_stops_per_day
        )
        self.trucks.append(truck)
        return truck

    # ===================================================== scenario setup
    def add_farms_from_world(self, *, pop_total: int = 5_000) -> int:
        if self.world.facilities is None:
            raise RuntimeError("World has no FacilityIndex loaded")
        added = 0
        for facility in self.world.facilities.facilities:
            if facility.kind in self.params.farm_kinds:
                self.add_farm(facility, pop_total=pop_total)
                added += 1
        return added

    def add_hubs_from_world(self) -> int:
        """Register every world facility whose kind is a configured fleet base."""
        if self.world.facilities is None:
            raise RuntimeError("World has no FacilityIndex loaded")
        hub_kinds = {spec.hub_kind for spec in self.params.fleets}
        added = 0
        for facility in self.world.facilities.facilities:
            if facility.kind in hub_kinds:
                self.add_hub(facility)
                added += 1
        return added

    def _next_bird_id(self) -> str:
        self._bird_serial += 1
        return f"bird-{self._bird_serial}"

    def _random_state_point(self) -> tuple[float, float] | None:
        """Uniform-by-area point inside the state polygon (metric sampling)."""
        sp = self.world.administrative.spatial
        if self._state_metric is None:
            self._state_metric = sp.geometry_to_metric(
                self.world.administrative.state_geometry_wgs84
            )
            if self._state_metric.is_empty:
                raise RuntimeError("State boundary polygon is empty")
        point = sample_point_in(self._state_metric, self.random.uniform)
        return None if point is None else sp.unproject((point.x, point.y))

    def _water_sites(self) -> tuple[list, np.ndarray]:
        """Metric water geometries with cumulative size weights (cached).

        Polygons weigh by area and lines by length times
        ``NOMINAL_WATERLINE_WIDTH_M``, so big reservoirs, wetlands and the
        coast attract more arrivals than small ponds, as wintering flocks do.
        """
        if self._water_cache is None:
            hydro = self.world.hydro
            geometries = [
                hydro.spatial.geometry_to_metric(feature.geometry_wgs84)
                for feature in hydro.features
            ]
            weights = np.array(
                [
                    g.area if g.area > 0 else g.length * NOMINAL_WATERLINE_WIDTH_M
                    for g in geometries
                ]
            )
            self._water_cache = (geometries, np.cumsum(weights))
        return self._water_cache

    def _random_water_point(self) -> tuple[float, float] | None:
        """A point on a size-weighted random mapped water feature in the state."""
        hydro = self.world.hydro
        if hydro is None or not hydro.features:
            return None
        geometries, cumulative = self._water_sites()
        if cumulative[-1] <= 0:
            return None
        state = self.world.administrative.state_geometry_wgs84
        for _ in range(20):
            index = int(
                np.searchsorted(cumulative, self.random.random() * cumulative[-1])
            )
            geometry = geometries[min(index, len(geometries) - 1)]
            if geometry.is_empty:
                continue
            if geometry.area > 0:
                point = sample_point_in(geometry, self.random.uniform, attempts=20)
                point = point or geometry.representative_point()
            else:
                point = geometry.interpolate(self.random.random() * geometry.length)
            location = hydro.spatial.unproject((point.x, point.y))
            if state.covers(Point(location)):
                return location
        return None

    def add_birds_uniform(self, n: int) -> int:
        """Place `n` resident birds uniformly inside the PE state polygon.

        A ``bird_water_pull_prob`` share roosts at the nearest mapped water.
        """
        p = self.params
        added = 0
        while added < n:
            location = self._random_state_point()
            if location is None:
                break
            roost = None
            if self.random.random() < p.bird_water_pull_prob:
                roost = self.nearest_water_point(location)
            bird = self.add_bird(self._next_bird_id(), location, roost=roost)
            if self.random.random() * 100 < p.initial_bird_infection_pct:
                bird.infect()
            added += 1
        if added < n:
            self.log_event("AVISO_AVES", f"Só foram criadas {added} de {n} aves")
        return added

    def setup_fleets(self) -> int:
        """Create the trucks of every configured fleet from registered hubs.

        Each farm is served, per fleet, by the hub with the shortest feasible
        round trip (ties by hub ID). A hub's clients are split among its trucks
        by bearing sectors around the hub (sweep heuristic), so each truck
        serves one geographic region, visited outwards by travel time.
        """
        if self.world.roads is None:
            raise WorldDataMissingError("Truck fleets require a road layer")
        added = 0
        for spec in self.params.fleets:
            hubs = sorted(
                (h for h in self.hubs if h.kind == spec.hub_kind),
                key=lambda h: h.hub_id,
            )
            if not hubs:
                self.log_event("AVISO_FROTA", f"Nenhuma base do tipo {spec.hub_kind}")
                continue
            for hub, clients in self._assign_clients(spec, hubs).items():
                added += self._add_hub_trucks(spec, hub, clients)
        return added

    def _assign_clients(
        self, spec: FleetSpec, hubs: list[HubAgent]
    ) -> Mapping[HubAgent, list[FarmAgent]]:
        p = self.params
        shift_s = p.truck_shift_hours * 3600.0
        service_s = p.truck_service_minutes * 60.0
        assignment: dict[HubAgent, list[FarmAgent]] = {hub: [] for hub in hubs}
        unserved = 0
        for farm in sorted(self.farms, key=lambda f: f.farm_id):
            feasible = []
            for hub in hubs:
                round_trip_s = (
                    self.stop_travel_time_s(hub, farm)
                    + service_s
                    + self.stop_travel_time_s(farm, hub)
                )
                if round_trip_s <= shift_s:
                    feasible.append((round_trip_s, hub.hub_id, hub))
            if feasible:
                assignment[min(feasible)[2]].append(farm)
            else:
                unserved += 1
        if unserved:
            self.log_event(
                "AVISO_FROTA",
                f"{unserved} granjas fora do alcance de um turno da frota "
                f"{spec.hub_kind}",
            )
        return assignment

    def _add_hub_trucks(
        self, spec: FleetSpec, hub: HubAgent, clients: list[FarmAgent]
    ) -> int:
        if not clients:
            self.log_event(
                "AVISO_FROTA", f"Base {hub.hub_id} sem granjas alcançáveis em um turno"
            )
            return 0
        sp = self.world.spatial
        hx, hy = sp.project(hub.location)

        def bearing(farm: FarmAgent) -> tuple[float, str]:
            fx, fy = sp.project(farm.location)
            return math.atan2(fy - hy, fx - hx), farm.farm_id

        ordered = sorted(clients, key=bearing)
        trucks = min(spec.trucks_per_hub, len(ordered))
        for i in range(trucks):
            sector = sorted(
                ordered[i * len(ordered) // trucks : (i + 1) * len(ordered) // trucks],
                key=lambda farm: (self.stop_travel_time_s(hub, farm), farm.farm_id),
            )
            self.add_truck(
                f"{hub.hub_id}-truck-{i + 1}",
                hub,
                sector,
                max_stops_per_day=spec.max_stops_per_day,
            )
        return trucks

    def seed_initial_outbreak(self) -> None:
        """Expose one random farm and record the day-0 history row.

        This is the last setup step, so like NetLogo's ``reset-ticks`` it plots
        the initial state and charts start at day 0.
        """
        if self.farms:
            farm = self.random.choice(self.farms)
            farm.seed_from_external(self.params.initial_infected, "INTRODUCAO_INICIAL")
        self._record_history()

    def farm_by_id(self, farm_id: str) -> FarmAgent | None:
        return self._farms_by_facility_id.get(farm_id)

    # ===================================================== spatial queries
    def _farm_index(self) -> tuple[np.ndarray, cKDTree | None]:
        if self._farm_tree is None or self._farm_xy is None:
            sp = self.world.spatial
            self._farm_xy = np.array(
                [sp.project(farm.location) for farm in self.farms], dtype=float
            ).reshape(-1, 2)
            self._farm_tree = cKDTree(self._farm_xy) if self.farms else None
        return self._farm_xy, self._farm_tree

    def farms_near(self, farm: FarmAgent, radius_km: float) -> list[FarmAgent]:
        """Other non-culled model farms within `radius_km`, in metric CRS."""
        xy, tree = self._farm_index()
        if tree is None:
            return []
        centre = self.world.spatial.project(farm.location)
        return [
            self.farms[i]
            for i in sorted(tree.query_ball_point(centre, radius_km * 1000.0))
            if self.farms[i] is not farm and not self.farms[i].culled
        ]

    def _invalidate_travel_cache(self) -> None:
        self._stop_node_index = None
        self._travel_cache.clear()

    def stop_travel_time_s(
        self, origin: FarmAgent | HubAgent, destination: FarmAgent | HubAgent
    ) -> float:
        """Road travel time between registered stops; ``inf`` if unreachable.

        One Dijkstra per origin node computes times to all farm/hub nodes, so
        the cache is bounded by (stops x stops) floats and filled lazily.
        """
        if self._stop_node_index is None:
            nodes = sorted({stop.road_node for stop in (*self.farms, *self.hubs)})
            self._stop_node_index = {node: i for i, node in enumerate(nodes)}
        times = self._travel_cache.get(origin.road_node)
        if times is None:
            times = self.world.travel_times(
                origin.road_node, list(self._stop_node_index)
            )
            self._travel_cache[origin.road_node] = times
        return float(times[self._stop_node_index[destination.road_node]])

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

    # ============================================================ daily step
    def step(self) -> None:
        self.day_count += 1
        for f in self.farms:
            f.new_exposed_today = 0.0

        for f in self.farms:
            f.step_intra_farm()

        for b in list(self.birds):
            b.step()
        self._bird_arrivals()
        self._bird_transmission()

        self._bird_to_farm()
        self._local_spread()

        for h in self.hubs:
            h.load = decayed_fomite_load(h.load, self.params.fomite_survival_days)
            if h.load == 0:
                h.load_source = None
        for t in self.trucks:
            t.step()

        protocol = bool(self.params.protocol_enabled)
        if protocol:
            self._active_surveillance()
            was_notified = [f.notified for f in self.farms]
        for f in self.farms:
            f.try_notify()
        if protocol:
            self._update_protocol(
                [
                    f
                    for f, before in zip(self.farms, was_notified, strict=True)
                    if f.notified and not before
                ]
            )
        for f in self.farms:
            f.try_quarantine()
            f.try_release_quarantine()

        self._update_economic_cost()
        self._update_rt()
        self._record_history()

        super().step()  # Mesa bookkeeping

    def run(self, days: int) -> None:
        for _ in range(days):
            self.step()

    # -------------------------------------------------------- subsystems
    def _bird_to_farm(self) -> None:
        p = self.params
        radius_m = p.bird_farm_proximity_km * 1000.0
        infected = [b for b in self.birds if b.bird_state == "I"]
        for farm in self.farms:
            if farm.culled or farm.quarantined or farm.susceptible <= 0:
                continue
            here = farm.location
            # The nearest infected bird (first in list order on ties) is the
            # attributed source.
            distance, nearest = min(
                (
                    (self.distance_m(here, bird.location), k)
                    for k, bird in enumerate(infected)
                ),
                default=(math.inf, -1),
            )
            if distance > radius_m:
                continue
            prob = bird_introduction_prob(p, near_water=self.farm_near_water(farm))
            if self.random.random() < prob:
                farm.seed_from_external(
                    p.seed_size_birds,
                    "INTRODUCAO_AVES_MIGRATORIAS",
                    bird_id=infected[nearest].bird_id,
                )

    def _bird_arrivals(self) -> None:
        """Migratory flocks land at water (anywhere in the state without hydro)."""
        p = self.params
        sp = self.world.spatial
        state = self.world.administrative.state_geometry_wgs84
        for _ in range(int(self.rng.poisson(p.migratory_flocks_per_day))):
            site = self._random_water_point() or self._random_state_point()
            if site is None:
                self.log_event("AVISO_AVES", "Sem ponto para chegada migratória")
                return
            self.flocks_arrived += 1
            infected = self.random.random() * 100 < p.migratory_infected_pct
            size = 1 + int(self.rng.poisson(max(p.flock_size_mean - 1.0, 0.0)))
            self.flock_arrivals.append(
                FlockArrival(self.day_count, site[0], site[1], size, infected)
            )
            x, y = sp.project(site)
            for k in range(size):
                location = sp.unproject(
                    (
                        x + self.random.gauss(0.0, FLOCK_SPREAD_M),
                        y + self.random.gauss(0.0, FLOCK_SPREAD_M),
                    )
                )
                if not state.covers(Point(location)):
                    location = site
                bird = self.add_bird(self._next_bird_id(), location, roost=location)
                self.birds_arrived += 1
                if infected and k == 0:
                    bird.infect()
            if infected:
                self.log_event(
                    "CHEGADA_BANDO_INFECTADO",
                    f"Bando migratório de {size} aves chegou com ave infectada",
                )

    def _bird_transmission(self) -> None:
        """Infect susceptible wild birds from infected birds and farms nearby.

        Bird-to-bird contact happens at the roost (distance between roosts);
        spillback from farms uses today's foraging position.
        """
        p = self.params
        susceptible = [b for b in self.birds if b.bird_state == "S"]
        if not susceptible:
            return
        sp = self.world.spatial
        roosts = cKDTree([sp.project(b.roost) for b in susceptible])
        foraging = cKDTree([sp.project(b.location) for b in susceptible])
        escape = np.ones(len(susceptible))
        bird_prob = p.bird_to_bird_prob / 100.0
        for bird in self.birds:
            if bird.bird_state == "I":
                for k in roosts.query_ball_point(
                    sp.project(bird.roost), p.bird_contact_km * 1000.0
                ):
                    escape[k] *= 1.0 - bird_prob
        for farm in self.farms:
            shedding = 0.0 if farm.culled else farm.shedding()
            if shedding > 0:
                for k in foraging.query_ball_point(
                    sp.project(farm.location), p.bird_farm_proximity_km * 1000.0
                ):
                    escape[k] *= 1.0 - p.farm_to_bird_prob / 100.0 * shedding
        infected = 0
        for k, bird in enumerate(susceptible):
            if escape[k] < 1.0 and self.random.random() < 1.0 - escape[k]:
                bird.infect()
                infected += 1
        if infected:
            self.wild_bird_infections += infected
            self.log_event(
                "INFECCAO_AVES_SILVESTRES", f"{infected} aves silvestres infectadas"
            )

    def _local_spread(self) -> None:
        """Neighbourhood transmission from shedding farms (see local_spread_prob)."""
        p = self.params
        xy, tree = self._farm_index()
        if tree is None:
            return
        escape: dict[int, float] = {}
        # Each target's sources (farm order) and their hazards, for attribution.
        sources: dict[int, list[int]] = {}
        hazards: dict[int, list[float]] = {}
        for i, source in enumerate(self.farms):
            shedding = 0.0 if source.culled else source.shedding()
            if shedding <= 0:
                continue
            for j in tree.query_ball_point(xy[i], p.local_spread_radius_km * 1000.0):
                target = self.farms[j]
                if (
                    j == i
                    or target.culled
                    or target.quarantined
                    or target.susceptible <= 0
                ):
                    continue
                prob = local_spread_prob(
                    p,
                    shedding=shedding,
                    distance_m=float(np.hypot(*(xy[j] - xy[i]))),
                    biosecurity=target.biosecurity,
                )
                escape[j] = escape.get(j, 1.0) * (1.0 - prob)
                sources.setdefault(j, []).append(i)
                hazards.setdefault(j, []).append(
                    -math.log1p(-prob) if prob < 1.0 else math.inf
                )
        for j in sorted(escape):
            if self.random.random() < 1.0 - escape[j]:
                top, share = dominant_source(hazards[j])
                self.farms[j].seed_from_external(
                    p.seed_size_local,
                    "INTRODUCAO_VIZINHANCA",
                    source_farm_id=self.farms[sources[j][top]].farm_id,
                    share=share,
                )

    # --------------------------------------------- official protocol (POP)
    def _active_surveillance(self) -> None:
        """Bring forward the detection of infections in perifoco/vigilância.

        A surveillance visit finds an infected farm at most the ring's
        ``*_detection_days`` after it entered the ring or got infected,
        whichever is later (an infection that starts after the farm entered
        is found within that many days of starting, not retroactively).
        The day drawn at infection is kept when it is earlier.
        """
        p = self.params
        deadlines = {
            "perifoco": p.perifocus_detection_days,
            "vigilancia": p.surveillance_detection_days,
        }
        today = self.day_count
        for farm in self.farms:
            days = deadlines.get(farm.zone)
            if days is None or farm.culled or farm.notified:
                continue
            if farm.first_infection_day < 0:
                continue
            visit = max(farm.zone_since, farm.first_infection_day) + days
            visit = max(visit, today)
            if visit < farm.detection_day:
                farm.detection_day = visit
                farm.surveillance_detection = True

    def _update_protocol(self, newly_notified: Sequence[FarmAgent]) -> None:
        """End-of-day protocol: confirm foci, then rebuild the emergency areas.

        The areas set here govern the next day's truck trips and surveillance.
        """
        p = self.params
        today = self.day_count
        for farm in newly_notified:
            if farm.surveillance_detection:
                self.log_event(
                    "VIGILANCIA_ATIVA",
                    f"Granja {farm.farm_id}: infecção encontrada pela vigilância "
                    f"ativa ({farm.zone})",
                    farm_id=farm.farm_id,
                )
            # Kept even if the infection ends first: the samples were taken.
            self._pending_foci.append((today + p.confirmation_days, farm))
        due = [farm for day, farm in self._pending_foci if day <= today]
        self._pending_foci = [(d, f) for d, f in self._pending_foci if d > today]
        for farm in due:
            self._confirm_focus(farm)
        for focus in self.foci:
            if focus.release_day is None:
                self._settle_release(focus)
        self._update_zones()

    def _confirm_focus(self, farm: FarmAgent) -> None:
        p = self.params
        today = self.day_count
        self.log_event(
            "FOCO_CONFIRMADO",
            f"Granja {farm.farm_id}: foco confirmado",
            farm_id=farm.farm_id,
        )
        if p.auto_depopulation and not farm.culled:
            farm.depopulate()
        focus = Focus(farm.farm_id, today, None, None)
        self.foci.append(focus)
        self._settle_release(focus)
        if focus.standstill_on(today, p.standstill_days):
            self.log_event(
                "INTERDICAO_TRANSITO",
                f"Foco {farm.farm_id}: trânsito interrompido por "
                f"{p.standstill_days} dias até {p.protection_radius_km:g} km",
                farm_id=farm.farm_id,
            )

    def _settle_release(self, focus: Focus) -> None:
        """Set the release day once the focus is culled or its infection ends."""
        farm = self.farm_by_id(focus.farm_id)
        release_days = self.params.zone_release_days
        if farm.culled:
            focus.depopulated_day = farm.cull_day
            focus.release_day = farm.cull_day + release_days
        elif farm.exposed + farm.infected <= 0:
            focus.release_day = farm.infection_end_day + release_days

    def _update_zones(self) -> None:
        """Each farm's and hub's ring, the most restrictive over active foci.

        Distances are straight lines in the metric CRS. Farms inside the
        protection radius of a focus in its standstill get ``standstill``.
        """
        p = self.params
        today = self.day_count
        active = [focus for focus in self.foci if focus.active_on(today)]
        rows = {farm.farm_id: i for i, farm in enumerate(self.farms)}
        farm_xy, _ = self._farm_index()
        sp = self.world.spatial
        hub_xy = np.array(
            [sp.project(hub.location) for hub in self.hubs], dtype=float
        ).reshape(-1, 2)
        outside = len(ZONE_RINGS)
        farm_rings = np.full(len(self.farms), outside)
        hub_rings = np.full(len(self.hubs), outside)
        standstill = np.zeros(len(self.farms), dtype=bool)
        for focus in active:
            centre = farm_xy[rows[focus.farm_id]]
            rings = ring_index(np.hypot(*(farm_xy - centre).T), p)
            farm_rings = np.minimum(farm_rings, rings)
            hub_rings = np.minimum(
                hub_rings, ring_index(np.hypot(*(hub_xy - centre).T), p)
            )
            if focus.standstill_on(today, p.standstill_days):
                standstill |= rings < outside
        focus_ids = {focus.farm_id for focus in active}
        for farm, ring, barred in zip(self.farms, farm_rings, standstill, strict=True):
            zone = ZONE_RINGS[ring] if ring < outside else None
            if zone != farm.zone:
                farm.zone, farm.zone_since = zone, today
            farm.focus = farm.farm_id in focus_ids
            farm.standstill = bool(barred)
        for hub, ring in zip(self.hubs, hub_rings, strict=True):
            hub.zone = ZONE_RINGS[ring] if ring < outside else None

    def _record_history(self) -> None:
        today = self.day_count
        statuses = Counter(farm.status for farm in self.farms)
        # Movements and events are appended in day order, so today's records
        # are the tail of each log.
        trips = sum(
            1 for _ in takewhile(lambda m: m.day == today, reversed(self.movements))
        )
        todays_kinds = Counter(
            event.kind
            for event in takewhile(lambda e: e.day == today, reversed(self.events))
        )
        self.history.append(
            {
                "day": today,
                **{status: statuses[status] for status in FARM_STATUSES},
                "birds": len(self.birds),
                "infected_birds": sum(1 for b in self.birds if b.bird_state == "I"),
                "rt": self.current_rt,
                "cost": self.cumulative_cost,
                "new_exposed": sum(f.new_exposed_today for f in self.farms),
                "deaths": self.total_deaths,
                "culled_birds": self.total_culled,
                "trips": trips,
                "introductions": {
                    kind: todays_kinds[kind] for kind in INTRODUCTION_EVENTS
                },
            }
        )

    def _update_economic_cost(self) -> None:
        p = self.params
        self.cumulative_cost += self.active_quarantines * p.quarantine_cost_per_day

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
    def active_quarantines(self) -> int:
        """Quarantined farms still standing, which cost quarantine per day."""
        return sum(1 for f in self.farms if f.quarantined and not f.culled)

    @property
    def hidden_farms(self) -> int:
        return sum(
            1
            for f in self.farms
            if not f.notified and (f.infected > 0 or f.exposed > 0)
        )

    # ============================================================ logging
    def log_event(
        self, tipo: str, detalhes: str, *, farm_id: str | None = None
    ) -> None:
        event = Event(self.day_count, tipo, detalhes, farm_id)
        self.events.append(event)
        if self.log_path is None:
            return
        with self.log_path.open("a", newline="") as fh:
            csv.writer(fh).writerow(event)  # None is written as an empty field

    def save_movements(self, path: str | Path) -> None:
        """Write every truck arrival as a CSV contact-network edge list."""
        with Path(path).open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([field.name for field in fields(TruckMovement)])
            writer.writerows(astuple(movement) for movement in self.movements)


# Illustrative poultry poles in the Agreste/Zona da Mata used by the demo
# scenario. They make farms cluster plausibly; they are not production data.
DEMO_POULTRY_POLES = (
    "São Bento do Una",
    "Lajedo",
    "Bezerros",
    "Gravatá",
    "Vitória de Santo Antão",
    "Glória do Goitá",
)
# Standard deviation of synthetic facility positions around a pole centre.
POLE_SIGMA_KM = 4.0


def install_synthetic_facilities(
    world,
    n: int = 50,
    *,
    kind: str = "farm",
    hubs: Mapping[str, int] | None = None,
    poles: Sequence[str] = (),
    pole_fraction: float = 0.7,
    seed: int | None = 42,
):
    """Attach a synthetic FacilityIndex to a world that lacks one.

    Samples `n` farms plus ``hubs`` (kind -> count, e.g. feed mills) inside the
    PE state polygon. With ``poles`` (municipality names), ``pole_fraction`` of
    the farms and every hub are drawn around those municipalities (Gaussian,
    ``POLE_SIGMA_KM``); the rest are uniform by area. With roads, each sample
    is snapped and rejected unless its node is in the largest strongly
    connected road component, so all facilities can reach each other. The
    result is assigned to ``world.facilities``. Intended for tests and demos.
    """
    if not 0 <= pole_fraction <= 1:
        raise ValueError("pole_fraction must be within [0, 1]")
    admin = world.administrative
    sp = admin.spatial
    state_metric = sp.geometry_to_metric(admin.state_geometry_wgs84)
    rng = np.random.default_rng(seed)

    by_name = {m.name: m for m in admin.municipalities}
    unknown = [name for name in poles if name not in by_name]
    if unknown:
        raise ValueError(f"Unknown pole municipalities: {', '.join(unknown)}")
    centres = [
        sp.geometry_to_metric(by_name[name].geometry_wgs84).representative_point()
        for name in poles
    ]
    clustered = round(n * pole_fraction) if centres else 0
    wanted = [(f"f{i + 1:04d}", kind, i < clustered) for i in range(n)]
    for hub_kind, count in sorted((hubs or {}).items()):
        wanted += [
            (f"{hub_kind}-{i + 1:02d}", hub_kind, bool(centres)) for i in range(count)
        ]
    connected = (
        world.roads.largest_component_nodes() if world.roads is not None else None
    )

    facilities: list[Facility] = []
    attempts = 0
    max_attempts = len(wanted) * 200
    while len(facilities) < len(wanted) and attempts < max_attempts:
        attempts += 1
        facility_id, facility_kind, at_pole = wanted[len(facilities)]
        if at_pole:
            centre = centres[int(rng.integers(len(centres)))]
            sigma = POLE_SIGMA_KM * 1000.0
            mx, my = centre.x + rng.normal(0, sigma), centre.y + rng.normal(0, sigma)
        else:
            point = sample_point_in(state_metric, rng.uniform, attempts=1)
            if point is None:
                continue
            mx, my = point.x, point.y
        if not state_metric.contains(Point(mx, my)):
            continue
        lon, lat = sp.unproject((mx, my))
        fac = Facility(id=facility_id, kind=facility_kind, lon=lon, lat=lat)
        if world.roads is not None:
            try:
                fac = snap_facility(
                    fac,
                    world.roads,
                    admin,
                    max_distance_m=world.config.max_snap_distance_m,
                )
            except NoRoadNearbyError:
                continue  # Too far from any road for a plausible farm or hub.
            if fac.road_node not in connected:
                continue  # Isolated road fragment: trucks could not serve it.
        facilities.append(fac)

    if not facilities:
        raise RuntimeError(
            "Could not place any synthetic facility inside the state polygon"
        )
    if len(facilities) < len(wanted):
        missing = ", ".join(entry[0] for entry in wanted[len(facilities) :])
        warnings.warn(f"Synthetic facilities not placed: {missing}", stacklevel=2)

    world.facilities = FacilityIndex(facilities, metric_crs=world.config.metric_crs)
    return world.facilities
