"""Mesa agents for the H5N1 farm/bird/truck model.

Identity and initial positioning mirror the original `agents.py`. Behavior
is ported from the NetLogo model: intra-farm SEIR, bird migration toward
water, fomite-carrying trucks, detection and intervention state machines.

Mesa 3.x: `self.random` is a `numpy.random.Generator` (integers, not randrange).
"""

from __future__ import annotations

import math
import random

from mesa import Agent

from simgeope.world.facilities import Facility
from simgeope.world.spatial import lon_lat

# --------------------------------------------------------------- helpers


def _agent_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Agent ID must be a nonempty string")
    return value


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = a
    lon2, lat2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


# ==================================================================== FARM


class FarmAgent(Agent):
    """A fixed farm backed by one spatially resolved facility."""

    def __init__(self, model, facility: Facility, *, pop_total: int = 0):
        if not isinstance(facility, Facility):
            raise TypeError("Farm facility must be a Facility")
        farm_id = _agent_id(facility.id)
        if type(pop_total) is not int or pop_total < 0:
            raise ValueError("Farm population must be a nonnegative integer")
        model._ensure_available_id(farm_id)
        super().__init__(model)

        self.farm_id = farm_id
        self.facility = facility
        self.pop_total = pop_total

        # SEIR
        self.susceptible: float = float(pop_total)
        self.exposed: float = 0.0
        self.infected: float = 0.0
        self.recovered: float = 0.0
        self.dead: float = 0.0
        self.new_exposed_today: float = 0.0

        # surveillance / intervention
        self.biosecurity: float = float(model.params.base_biosecurity)
        self.notified: bool = False
        self.first_infection_day: int = -1
        self.detection_day: int = -1
        self.reaction_day: int = -1
        self.quarantined: bool = False
        self.culled: bool = False
        self.under_biosec_boost: bool = False

        model.agents_by_id[farm_id] = self

    # ---------------------------------------------------------- geometry
    @property
    def location(self) -> tuple[float, float]:
        return self.facility.lon, self.facility.lat

    @property
    def lon(self) -> float:
        return self.facility.lon

    @property
    def lat(self) -> float:
        return self.facility.lat

    @property
    def road_node(self) -> int | None:
        return self.facility.road_node

    # ---------------------------------------------------------- SEIR step
    def step_intra_farm(self) -> None:
        if self.culled or self.pop_total <= 0:
            return

        p = self.model.params
        n = self.pop_total
        beta_eff = p.beta * (1.0 - self.biosecurity / 100.0)
        sigma = 1.0 / p.incubation_days
        gamma = 1.0 / p.infectious_period_days

        new_exp = min(self.susceptible, self.susceptible * beta_eff * self.infected / n)
        new_inf = min(self.exposed, self.exposed * sigma)
        leaving = min(self.infected, self.infected * gamma)
        new_dead = leaving * (p.mortality_rate / 100.0)
        new_rec = leaving - new_dead

        self.susceptible -= new_exp
        self.exposed += new_exp - new_inf
        self.infected += new_inf - leaving
        self.recovered += new_rec
        self.dead += new_dead

        self.new_exposed_today += new_exp
        self.model.total_infections += new_exp
        self.model.total_deaths += new_dead

        if new_exp > 0.5:
            self.model.log_event(
                "INFECCAO",
                f"Granja {self.farm_id}: +{new_exp:.1f} aves expostas",
            )

        if new_exp > 0 and self.first_infection_day == -1:
            self._start_infection_clock()

    # ------------------------------------------------ external infection
    def seed_from_external(self, seed_size: float, source: str) -> bool:
        if self.culled or self.quarantined or self.susceptible <= 0:
            return False

        seed = min(self.susceptible, float(seed_size))
        if seed <= 0:
            return False

        self.susceptible -= seed
        self.exposed += seed
        self.new_exposed_today += seed
        self.model.total_infections += seed

        self.model.log_event(
            source,
            f"Granja {self.farm_id}: exposição via {source} (+{seed:.0f})",
        )
        if self.first_infection_day == -1:
            self._start_infection_clock()
        return True

    def _start_infection_clock(self) -> None:
        p = self.model.params
        self.first_infection_day = self.model.day_count
        # Mesa 3 / numpy Generator: integers(low, high) returns [low, high)
        self.detection_day = self.model.day_count + int(
            self.random.integers(0, p.detection_time_max + 1)
        )

    # --------------------------------------------------- vigilância
    def try_notify(self) -> None:
        if self.notified or self.first_infection_day < 0:
            return
        if self.infected <= 0 and self.exposed <= 0:
            return
        if self.model.day_count >= self.detection_day:
            self.notified = True
            self.reaction_day = self.model.day_count + int(
                self.random.integers(0, self.model.params.reaction_time_max + 1)
            )
            self.model.log_event(
                "DETECCAO",
                f"Granja {self.farm_id} notificada às autoridades",
            )

    def try_quarantine(self) -> None:
        if self.notified and not self.quarantined and not self.culled:
            if self.model.day_count >= self.reaction_day:
                self.quarantined = True
                self.model.log_event(
                    "REACAO_AUTOMATICA",
                    f"Granja {self.farm_id}: bloqueio automático aplicado",
                )

    # ---------------------------------------------------- interventions
    def apply_stamping_out(self) -> None:
        p = self.model.params
        cull_count = self.susceptible + self.exposed + self.infected
        self.model.cumulative_cost += cull_count * p.cull_cost_per_bird
        self.model.total_culled += cull_count

        self.susceptible = 0.0
        self.exposed = 0.0
        self.infected = 0.0
        self.culled = True
        self.quarantined = True

        self.model.log_event(
            "SACRIFICIO_SANITARIO",
            f"Granja {self.farm_id}: {cull_count:.0f} aves sacrificadas",
        )

        for other in self.model.farms_near(self, p.stamping_out_radius_km):
            if other is self or other.culled:
                continue
            other.quarantined = True
            self.model.log_event(
                "QUARENTENA_PERIFERICA",
                f"Granja {other.farm_id}: quarentena por raio de sacrifício",
            )

    def apply_movement_block(self) -> None:
        self.quarantined = True
        self.model.log_event(
            "BLOQUEIO_MOVIMENTACAO",
            f"Granja {self.farm_id}: movimentação suspensa manualmente",
        )

    def apply_biosecurity_boost(self) -> None:
        p = self.model.params
        self.biosecurity = min(100.0, self.biosecurity + p.biosecurity_boost_amount)
        self.model.cumulative_cost += p.biosecurity_investment_cost
        self.under_biosec_boost = True
        self.model.log_event(
            "MELHORIA_BIOSSEGURANCA",
            f"Granja {self.farm_id}: biosseguridade -> {self.biosecurity:.0f}%",
        )


# ==================================================================== BIRD


class BirdAgent(Agent):
    """A bird at an arbitrary WGS84 point, independent of the road graph.

    Moves toward the nearest hydrology feature with probability
    ``bird_water_pull_prob``; otherwise takes a random walk. Carries an
    S/I state and dies after ``bird_infection_duration`` days infected.
    """

    def __init__(self, model, bird_id: str, location):
        bird_id = _agent_id(bird_id)
        location = lon_lat(location)
        model._ensure_available_id(bird_id)
        super().__init__(model)

        self.bird_id = bird_id
        self.location: tuple[float, float] = location
        self.bird_state: str = "S"  # "S" | "I"
        self.days_infected: int = 0

        model.agents_by_id[bird_id] = self

    @property
    def lon(self) -> float:
        return self.location[0]

    @property
    def lat(self) -> float:
        return self.location[1]

    # ------------------------------------------------------- behavior
    def step(self) -> None:
        p = self.model.params

        if self.bird_state == "I":
            self.days_infected += 1
            if self.days_infected > p.bird_infection_duration:
                self.model.remove_agent(self)
                return

        if self.random.random() < p.bird_water_pull_prob:
            target = self.model.nearest_water_point(self.location)
            if target is not None:
                self.location = self.model.move_towards(
                    self.location, target, p.bird_step_m
                )
                return

        # random walk in metric space
        theta = self.random.random() * 2 * math.pi
        sp = self.model.world.spatial
        x, y = sp.project(self.location)
        nx = x + p.bird_step_m * math.cos(theta)
        ny = y + p.bird_step_m * math.sin(theta)
        try:
            self.location = sp.unproject((nx, ny))
        except Exception:
            pass

    def infect(self) -> None:
        if self.bird_state != "I":
            self.bird_state = "I"
            self.days_infected = 0


# =================================================================== TRUCK


class TruckAgent(Agent):
    """A truck that shuttles between farms and can carry fomites.

    If `params.truck_uses_roads` is True, movements respect the real road
    network: the truck enters a transit state and arrives after
    `travel_time_s / 86400` ticks (rounded up). Otherwise it teleports.
    """

    def __init__(self, model, truck_id: str, at_farm: FarmAgent):
        truck_id = _agent_id(truck_id)
        if (
            not isinstance(at_farm, FarmAgent)
            or at_farm.model is not model
            or model.agents_by_id.get(at_farm.farm_id) is not at_farm
        ):
            raise ValueError("Truck farm must be registered in the same model")
        model._ensure_available_id(truck_id)
        super().__init__(model)

        self.truck_id = truck_id
        self.at_farm: FarmAgent | None = at_farm
        self.destination: FarmAgent | None = None
        self.days_to_arrival: int = 0
        self.contaminated: bool = False
        self.days_contaminated: int = 0

        model.agents_by_id[truck_id] = self

    # ------------------------------------------------------- geometry
    @property
    def location(self) -> tuple[float, float]:
        if self.at_farm is not None:
            return self.at_farm.location
        if self.destination is not None:
            return self.destination.location
        return (0.0, 0.0)

    @property
    def lon(self) -> float:
        return self.location[0]

    @property
    def lat(self) -> float:
        return self.location[1]

    @property
    def in_transit(self) -> bool:
        return self.days_to_arrival > 0

    # ------------------------------------------------------- behavior
    def step(self) -> None:
        p = self.model.params

        # 1) fomite decay
        if self.contaminated:
            self.days_contaminated += 1
            if self.days_contaminated > p.fomite_survival_days:
                self.contaminated = False
                self.days_contaminated = 0

        # 2) arriving?
        if self.in_transit:
            self.days_to_arrival -= 1
            if self.days_to_arrival == 0:
                self.at_farm, self.destination = self.destination, None
                if self.at_farm is not None:
                    self._visit(self.at_farm)
            return

        # 3) currently at a farm: visit, then pick the next
        if self.at_farm is not None:
            self._visit(self.at_farm)
        candidates = [f for f in self.model.farms if not f.culled]
        if not candidates:
            return
        next_farm = self.random.choice(candidates)

        # 4) move
        if p.truck_uses_roads and self.at_farm is not None:
            self._start_transit(next_farm)
        else:
            self.at_farm = next_farm

    def _start_transit(self, destination: FarmAgent) -> None:
        p = self.model.params
        try:
            route = self.model.world.route(self.location, destination.location)
            days = max(1, math.ceil(route.travel_time_s / 86_400.0))
        except Exception:
            days = 1
        days = min(days, p.truck_max_transit_days)
        self.destination = destination
        self.at_farm = None
        self.days_to_arrival = days

    def _visit(self, farm: FarmAgent) -> None:
        if farm.quarantined:
            return
        p = self.model.params

        if self.contaminated and farm.susceptible > 0:
            if self.random.random() * 100 < p.infection_prob_truck_to_farm:
                farm.seed_from_external(p.seed_size_fomite, "INTRODUCAO_FOMITE")

        if farm.infected > 0:
            self.contaminated = True
            self.days_contaminated = 0
