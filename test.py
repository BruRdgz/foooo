"""Smoke test for the H5N1 ABM against the built world.

If the world was built without facilities.parquet, this test injects a
synthetic FacilityIndex (farms plus truck hubs) at runtime (Option B) so the
simulation can run. Truck arrivals are written as a contact-network CSV and
the final infection risk layer as an NPZ raster.
"""

from simgeope.simulation.model import (
    DEMO_POULTRY_POLES,
    AgentModel,
    install_synthetic_facilities,
)
from simgeope.simulation.params import ModelParams
from simgeope.simulation.risk import GREEN, RED, YELLOW, infection_risk
from simgeope.world.model import PernambucoWorld


def main() -> None:
    world = PernambucoWorld.load("data/world/pe_environment_v1")

    print("has facilities:", world.facilities is not None)
    print("has roads:     ", world.roads is not None)
    print("has hydro:     ", world.hydro is not None)
    print("has admin:     ", world.administrative is not None)

    # ---- Option B quick fix ------------------------------------------------
    if world.facilities is None:
        print("→ world has no facilities.parquet; installing synthetic poultry poles")
        install_synthetic_facilities(
            world,
            n=120,
            hubs={"feed_mill": 2, "slaughterhouse": 2},
            poles=DEMO_POULTRY_POLES,
            seed=42,
        )
        print(
            f"→ installed {len(world.facilities.facilities)} facilities "
            f"(snapped to roads: {world.roads is not None})"
        )
    # ------------------------------------------------------------------------

    model = AgentModel(
        world,
        params=ModelParams(beta=3.2, base_biosecurity=60),
        seed=42,
        log_path="h5n1_log.csv",
    )

    model.add_farms_from_world(pop_total=5_000)
    model.add_birds_uniform(80)  # inside PE state polygon
    model.add_hubs_from_world()
    print(f"→ {model.setup_fleets()} trucks across {len(model.hubs)} hubs")
    model.seed_initial_outbreak()
    model.run(180)
    model.save_movements("h5n1_movements.csv")

    risk = infection_risk(model)
    risk.save("h5n1_risk.npz")
    levels = risk.levels(
        model.params.risk_yellow_threshold, model.params.risk_red_threshold
    )
    print(
        "Risco (células de 1km):",
        f"verde={(levels == GREEN).sum()}",
        f"| amarelo={(levels == YELLOW).sum()}",
        f"| vermelho={(levels == RED).sum()}",
    )

    print(
        f"Dia {model.day_count}",
        f"| saudáveis={model.healthy_farms}",
        f"| notificadas={model.notified_farms}",
        f"| ocultas={model.hidden_farms}",
        f"| Rt={model.current_rt:.2f}",
        f"| custo=R$ {model.cumulative_cost:,.0f}",
        f"| mortas={model.total_deaths:.0f}",
        f"| sacrificadas={model.total_culled:.0f}",
    )
    print(
        f"Aves silvestres: {len(model.birds)} presentes",
        f"| chegaram={model.birds_arrived}",
        f"| infectadas no total={model.wild_bird_infections}",
        f"| mortas={model.wild_bird_deaths}",
    )


if __name__ == "__main__":
    main()
