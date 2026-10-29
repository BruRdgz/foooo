"""Mesa agents for the H5N1 farm/bird/truck model.

Identity and initial positioning mirror the original `agents.py`. Behavior
is ported from the NetLogo model: intra-farm SEIR, bird migration toward
water, detection and intervention state machines. Fomite trucks extend the
NetLogo random farm-to-farm hops into hub-based fleets on the road network.

Mesa 3.x: `self.random` is a stdlib `random.Random`; `model.rng` is NumPy's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

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


# A full fomite load decays to this level after `fomite_survival_days` and is
# cleared on the next decay, matching NetLogo's contamination day counter.
NEGLIGIBLE_FOMITE_LOAD = 0.1
# Keeps the boundary day independent of floating-point rounding.
_DECAY_TOLERANCE = 1e-9


def decayed_fomite_load(load: float, survival_days: int) -> float:
    load *= NEGLIGIBLE_FOMITE_LOAD ** (1.0 / survival_days)
    return load if load >= NEGLIGIBLE_FOMITE_LOAD * (1 - _DECAY_TOLERANCE) else 0.0


# NetLogo `bird-to-farm-transmission`: farms near water get 1.5x bird pressure.
WATER_BIRD_PRESSURE_MULTIPLIER = 1.5


def bird_introduction_prob(params, *, near_water: bool) -> float:
    """Daily chance that infected birds nearby introduce infection into a farm."""
    prob = params.infection_prob_bird_to_farm / 100.0
    if near_water:
        prob *= WATER_BIRD_PRESSURE_MULTIPLIER
    return min(prob, 1.0)


def local_spread_prob(
    params, *, shedding: float, distance_m: float, biosecurity: float
) -> float:
    """Daily chance that an infected neighbour farm infects this one.

    Linear distance kernel up to ``local_spread_radius_km`` (airborne/dust,
    shared equipment, rodents), scaled by the source's shedding and the
    receiving farm's biosecurity.
    """
    radius_m = params.local_spread_radius_km * 1000.0
    if distance_m >= radius_m:
        return 0.0
    return (
        params.local_spread_prob
        / 100.0
        * shedding
        * (1.0 - distance_m / radius_m)
        * (1.0 - biosecurity / 100.0)
    )


# The continuous SEIR tail never reaches zero; below one bird incubating or
# infectious the outbreak inside the farm is over.
EXTINCTION_THRESHOLD_BIRDS = 1.0


def fomite_introduction_prob(params, *, load: float, biosecurity: float) -> float:
    """Chance that a truck visit with this load introduces infection into a farm.

    Farm biosecurity (vehicle disinfection at the gate) scales the risk.
    """
    return (
        params.infection_prob_truck_to_farm / 100.0 * load * (1.0 - biosecurity / 100.0)
    )


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
        self.quarantine_day: int = -1
        self.infection_end_day: int = -1
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

    @property
    def status(self) -> str:
        """NetLogo `update-visuals` classification of the farm."""
        if self.culled:
            return "culled"
        if self.infected > 0 or self.exposed > 0:
            return "notified" if self.notified else "hidden"
        return "quarantined" if self.quarantined else "healthy"

    def shedding(self) -> float:
        """Virus shedding in [0, 1], saturating at ``fomite_full_load_prevalence``.

        It is the load a visiting truck picks up and the strength of this farm
        as a source of local or wild-bird spread.
        """
        if self.pop_total <= 0 or self.infected <= 0:
            return 0.0
        prevalence = self.infected / self.pop_total
        return min(1.0, prevalence / self.model.params.fomite_full_load_prevalence)

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

        if 0 < self.exposed + self.infected < EXTINCTION_THRESHOLD_BIRDS:
            self._end_infection()

    def _end_infection(self) -> None:
        self.recovered += self.exposed + self.infected
        self.exposed = self.infected = 0.0
        self.infection_end_day = self.model.day_count
        # A later reintroduction is a new outbreak with its own detection.
        self.first_infection_day = self.detection_day = self.reaction_day = -1
        self.notified = False
        self.model.log_event(
            "FIM_INFECCAO", f"Granja {self.farm_id}: sem aves infectadas"
        )

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
        # randint is inclusive, like NetLogo's `random (n + 1)`.
        self.detection_day = self.model.day_count + self.random.randint(
            0, p.detection_time_max
        )

    # --------------------------------------------------- vigilância
    def try_notify(self) -> None:
        if self.notified or self.first_infection_day < 0:
            return
        if self.infected <= 0 and self.exposed <= 0:
            return
        if self.model.day_count >= self.detection_day:
            self.notified = True
            self.reaction_day = self.model.day_count + self.random.randint(
                0, self.model.params.reaction_time_max
            )
            self.model.log_event(
                "DETECCAO",
                f"Granja {self.farm_id} notificada às autoridades",
            )

    def start_quarantine(self) -> None:
        if not self.quarantined:
            self.quarantined = True
            self.quarantine_day = self.model.day_count

    def try_quarantine(self) -> None:
        if self.notified and not self.quarantined and not self.culled:
            if self.model.day_count >= self.reaction_day:
                self.start_quarantine()
                self.model.log_event(
                    "REACAO_AUTOMATICA",
                    f"Granja {self.farm_id}: bloqueio automático aplicado",
                )

    def try_release_quarantine(self) -> None:
        if not self.quarantined or self.culled or self.exposed + self.infected > 0:
            return
        free_since = max(self.quarantine_day, self.infection_end_day)
        if (
            self.model.day_count
            >= free_since + self.model.params.quarantine_release_days
        ):
            self.quarantined = False
            self.model.log_event(
                "LIBERACAO_QUARENTENA",
                f"Granja {self.farm_id}: quarentena encerrada",
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
        self.start_quarantine()

        self.model.log_event(
            "SACRIFICIO_SANITARIO",
            f"Granja {self.farm_id}: {cull_count:.0f} aves sacrificadas",
        )

        for other in self.model.farms_near(self, p.stamping_out_radius_km):
            if other is self or other.culled:
                continue
            other.start_quarantine()
            self.model.log_event(
                "QUARENTENA_PERIFERICA",
                f"Granja {other.farm_id}: quarentena por raio de sacrifício",
            )

    def apply_movement_block(self) -> None:
        self.start_quarantine()
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
    """A wild bird at an arbitrary WGS84 point, independent of the road graph.

    It roosts at a fixed site (shared by its flock) and forages each day at a
    Gaussian offset of ``bird_step_m`` around it. Carries an S/I/R
    state: after ``bird_infection_duration`` days infected it dies with
    ``bird_mortality_pct`` chance, otherwise recovers immune. Every day it may
    migrate onwards and leave Pernambuco (mean stay ``bird_mean_stay_days``).
    """

    def __init__(self, model, bird_id: str, location, *, roost=None):
        bird_id = _agent_id(bird_id)
        location = lon_lat(location)
        roost = location if roost is None else lon_lat(roost)
        model._ensure_available_id(bird_id)
        super().__init__(model)

        self.bird_id = bird_id
        self.location: tuple[float, float] = location
        self.roost: tuple[float, float] = roost
        self.bird_state: str = "S"  # "S" | "I" | "R"
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

        if self.random.random() < 1.0 / p.bird_mean_stay_days:
            self.model.remove_agent(self)
            return

        if self.bird_state == "I":
            self.days_infected += 1
            if self.days_infected > p.bird_infection_duration:
                if self.random.random() * 100 < p.bird_mortality_pct:
                    self.model.wild_bird_deaths += 1
                    self.model.remove_agent(self)
                    return
                self.bird_state = "R"

        sp = self.model.world.spatial
        x, y = sp.project(self.roost)
        try:
            self.location = lon_lat(
                sp.unproject(
                    (
                        x + self.random.gauss(0.0, p.bird_step_m),
                        y + self.random.gauss(0.0, p.bird_step_m),
                    )
                )
            )
        except ValueError:
            self.location = self.roost  # Offset left valid WGS84: stay at roost.

    def infect(self) -> None:
        if self.bird_state == "S":
            self.bird_state = "I"
            self.days_infected = 0


# ===================================================================== HUB


class HubAgent(Agent):
    """A fixed fleet base (feed mill, slaughterhouse, market...).

    Its environment accumulates fomite load deposited by returning trucks,
    which can then contaminate other trucks of the same base.
    """

    def __init__(self, model, facility: Facility):
        if not isinstance(facility, Facility):
            raise TypeError("Hub facility must be a Facility")
        hub_id = _agent_id(facility.id)
        model._ensure_available_id(hub_id)
        super().__init__(model)

        self.hub_id = hub_id
        self.facility = facility
        self.load: float = 0.0

        model.agents_by_id[hub_id] = self

    @property
    def kind(self) -> str:
        return self.facility.kind

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


# =================================================================== TRUCK


@dataclass(frozen=True, slots=True)
class TruckMovement:
    """One truck arrival, the edge list of the farm contact network."""

    day: int
    truck_id: str
    origin_id: str
    destination_id: str
    travel_time_s: float
    load_on_arrival: float
    introduced_infection: bool


class TruckAgent(Agent):
    """A truck based at a hub that serves a fixed client list in rotation.

    Each step is one working day: leave the base, visit the next clients that
    fit in the shift (travel, service and the drive back), then return. The
    fomite load is continuous in [0, 1]: picked up at infected farms, exchanged
    with the base hub, decayed daily and cleared by disinfection at the base.
    """

    def __init__(
        self,
        model,
        truck_id: str,
        base: HubAgent,
        clients=(),
        *,
        max_stops_per_day: int = 1,
    ):
        truck_id = _agent_id(truck_id)
        clients = tuple(clients)
        for agent, kind in ((base, HubAgent), *((c, FarmAgent) for c in clients)):
            if (
                not isinstance(agent, kind)
                or agent.model is not model
                or model.agents_by_id.get(agent.facility.id) is not agent
            ):
                raise ValueError(
                    "Truck base and clients must be registered in the model"
                )
        if type(max_stops_per_day) is not int or max_stops_per_day < 1:
            raise ValueError("Truck stops per day must be a positive integer")
        model._ensure_available_id(truck_id)
        super().__init__(model)

        self.truck_id = truck_id
        self.base = base
        self.clients: tuple[FarmAgent, ...] = clients
        self.max_stops_per_day = max_stops_per_day
        self.next_client: int = 0
        self.position: HubAgent | FarmAgent = base
        self.load: float = 0.0

        model.agents_by_id[truck_id] = self

    # ------------------------------------------------------- geometry
    @property
    def location(self) -> tuple[float, float]:
        return self.position.location

    @property
    def lon(self) -> float:
        return self.location[0]

    @property
    def lat(self) -> float:
        return self.location[1]

    # ------------------------------------------------------- behavior
    def step(self) -> None:
        p = self.model.params
        self.load = decayed_fomite_load(self.load, p.fomite_survival_days)

        shift_s = p.truck_shift_hours * 3600.0
        service_s = p.truck_service_minutes * 60.0
        elapsed_s = 0.0
        stops = 0
        for _ in range(len(self.clients)):
            if stops >= self.max_stops_per_day:
                break
            farm = self.clients[self.next_client]
            if farm.culled or farm.quarantined:
                self._advance_rotation()
                continue
            go_s = self.model.stop_travel_time_s(self.position, farm)
            back_s = self.model.stop_travel_time_s(farm, self.base)
            # Leave it as tomorrow's first stop; assignment guarantees a
            # round trip from the base fits in one shift.
            if elapsed_s + go_s + service_s + back_s > shift_s:
                break
            elapsed_s += go_s + service_s
            self._arrive(farm, go_s)
            self._advance_rotation()
            stops += 1

        if self.position is not self.base:
            self._arrive(
                self.base, self.model.stop_travel_time_s(self.position, self.base)
            )

    def _advance_rotation(self) -> None:
        self.next_client = (self.next_client + 1) % len(self.clients)

    def _arrive(self, stop: HubAgent | FarmAgent, travel_time_s: float) -> None:
        load_on_arrival = self.load
        introduced = (
            self._exchange_at_farm(stop)
            if isinstance(stop, FarmAgent)
            else self._exchange_at_base()
        )
        self.model.movements.append(
            TruckMovement(
                day=self.model.day_count,
                truck_id=self.truck_id,
                origin_id=self.position.facility.id,
                destination_id=stop.facility.id,
                travel_time_s=travel_time_s,
                load_on_arrival=load_on_arrival,
                introduced_infection=introduced,
            )
        )
        self.position = stop

    def _exchange_at_farm(self, farm: FarmAgent) -> bool:
        p = self.model.params
        introduced = False
        if self.load > 0 and farm.susceptible > 0:
            prob = fomite_introduction_prob(
                p, load=self.load, biosecurity=farm.biosecurity
            )
            if self.random.random() < prob:
                introduced = farm.seed_from_external(
                    p.seed_size_fomite, "INTRODUCAO_FOMITE"
                )
        self.load = max(self.load, farm.shedding())
        return introduced

    def _exchange_at_base(self) -> bool:
        p = self.model.params
        hub = self.base
        hub.load = max(hub.load, p.hub_transfer_fraction * self.load)
        if self.random.random() < p.truck_disinfection_prob:
            self.load = 0.0
        else:
            self.load = max(self.load, p.hub_transfer_fraction * hub.load)
        return False
