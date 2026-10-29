"""Tunable parameters mirroring the NetLogo interface sliders."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FleetSpec:
    """Trucks based at every hub facility of one kind, serving nearby farms.

    Examples: ``feed_mill`` (ração: fábrica → várias granjas → fábrica),
    ``slaughterhouse`` (aves vivas: abatedouro → granja → abatedouro), or any
    other hub kind present in the facility table, such as ``market``.
    """

    hub_kind: str
    trucks_per_hub: int
    max_stops_per_day: int


@dataclass(slots=True)
class ModelParams:
    # --- SEIR intra-granja ------------------------------------------------
    beta: float = 3.2
    incubation_days: float = 4.0
    infectious_period_days: float = 3.0
    mortality_rate: float = 90.0  # %
    base_biosecurity: float = 50.0  # 0-100
    initial_infected: int = 50

    # --- vigilância / reação ---------------------------------------------
    detection_time_max: int = 7
    reaction_time_max: int = 3
    generation_interval_days: int = 5

    # --- aves silvestres (SIR com chegada e partida migratória) ----------
    # Illustrative defaults for a demonstrative scenario, not field estimates.
    bird_infection_duration: int = 5
    bird_mortality_pct: float = 30.0  # infectadas que morrem; o resto se recupera
    infection_prob_bird_to_farm: float = 15.0  # %
    seed_size_birds: int = 10
    initial_bird_infection_pct: float = 10.0  # %
    bird_step_m: float = 2000.0  # raio típico de forrageio em torno do poleiro
    bird_water_pull_prob: float = 0.60  # residentes com poleiro na água
    farm_water_proximity_km: float = 3.0
    bird_farm_proximity_km: float = 3.0
    # Per infected roost-mate per day, for roosts within bird_contact_km.
    bird_to_bird_prob: float = 8.0  # %
    bird_contact_km: float = 1.0
    # Spillback: wild birds near an infected farm (within bird_farm_proximity_km).
    farm_to_bird_prob: float = 5.0  # %
    # Migratory flow: flocks land together at size-weighted water sites
    # (Poisson events, 1 + Poisson(flock_size_mean - 1) birds) and each bird
    # stays a geometric time, so the population settles near
    # flocks x size x stay. An infected flock carries one infected index bird.
    migratory_flocks_per_day: float = 0.3
    flock_size_mean: float = 8.0
    migratory_infected_pct: float = 15.0  # % dos bandos
    bird_mean_stay_days: float = 30.0

    # --- transmissão local entre granjas vizinhas ------------------------
    # Daily chance from a fully shedding neighbour at distance zero; it falls
    # linearly to zero at local_spread_radius_km and scales with biosecurity.
    local_spread_prob: float = 10.0  # %
    local_spread_radius_km: float = 3.0
    seed_size_local: int = 10

    # --- fim de surto -----------------------------------------------------
    # Quarantine of a non-culled farm is lifted this long after infection ends.
    quarantine_release_days: int = 21

    # --- fômites (caminhões) ---------------------------------------------
    # Facility kinds that become farms; every other kind may be a fleet hub.
    farm_kinds: tuple[str, ...] = ("farm", "producer")
    # Illustrative defaults, to be calibrated against real fleet data.
    fleets: tuple[FleetSpec, ...] = (
        FleetSpec("feed_mill", trucks_per_hub=3, max_stops_per_day=4),
        FleetSpec("slaughterhouse", trucks_per_hub=2, max_stops_per_day=1),
    )
    truck_shift_hours: float = 10.0
    truck_service_minutes: float = 45.0  # carga/descarga por parada
    # Max chance per visit for a fully loaded truck at a farm without biosecurity.
    infection_prob_truck_to_farm: float = 20.0  # %
    seed_size_fomite: int = 15
    # A full load decays to negligible after this many days (NetLogo slider).
    fomite_survival_days: int = 5
    # Farm prevalence (I/N) at which a visiting truck leaves fully loaded.
    fomite_full_load_prevalence: float = 0.01
    # Fraction of load exchanged between a truck and its base hub's environment.
    hub_transfer_fraction: float = 0.5
    truck_disinfection_prob: float = 0.5  # lavagem ao voltar à base

    # --- camada de risco (probabilidade diária de introdução) -------------
    # Initial cut-offs at default parameters: a fully loaded truck visit to a
    # base-biosecurity farm (10%) is yellow; infected birds nearby (15%) red.
    risk_yellow_threshold: float = 0.05
    risk_red_threshold: float = 0.15

    # --- intervenções / economia -----------------------------------------
    stamping_out_radius_km: float = 5.0
    cull_cost_per_bird: float = 8.0
    quarantine_cost_per_day: float = 500.0
    biosecurity_boost_amount: float = 20.0
    biosecurity_investment_cost: float = 5000.0
