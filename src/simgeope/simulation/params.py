"""Tunable parameters mirroring the NetLogo interface sliders."""

from __future__ import annotations

from dataclasses import dataclass


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

    # --- aves ------------------------------------------------------------
    bird_infection_duration: int = 5
    infection_prob_bird_to_farm: float = 15.0  # %
    seed_size_birds: int = 10
    initial_bird_infection_pct: float = 10.0  # %
    bird_step_m: float = 500.0
    bird_water_pull_prob: float = 0.60
    farm_water_proximity_km: float = 3.0
    bird_farm_proximity_km: float = 3.0

    # --- fômites (caminhões) ---------------------------------------------
    infection_prob_truck_to_farm: float = 20.0  # %
    seed_size_fomite: int = 15
    fomite_survival_days: int = 5
    truck_uses_roads: bool = True  # False = teleporte
    truck_max_transit_days: int = 30  # segurança

    # --- intervenções / economia -----------------------------------------
    stamping_out_radius_km: float = 5.0
    cull_cost_per_bird: float = 8.0
    quarantine_cost_per_day: float = 500.0
    biosecurity_boost_amount: float = 20.0
    biosecurity_investment_cost: float = 5000.0
